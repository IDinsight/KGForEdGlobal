"""This module contains LC duplicate-skill grouping for KG creation.

Exact duplicates group by normalized text within the configured dedup scope;
semantic duplicates are nominated by deterministic blocking (a
language-independent core, an optional per-language booster pack, and
similarity-blind neighborhood review sets) and adjudicated by a bounded,
resumable LLM pair judge — the sfi_dedup pattern applied to generated
skills. Minting collapses each group into one LearningComponent keyed on the
group's canonical text.

Sibling LC modules mirror the sfi_* layout: lc_selection.py (LC-source
selection), lc_generation.py (requests + LLM decomposition),
lc_finalization.py (mint nodes, supports edges, validate/summarize),
lc_export.py (AS+LC bundle merge).
"""

# Standard Library
import hashlib
import re

from itertools import combinations
from pathlib import Path
from typing import Optional, Sequence
from uuid import UUID

# Third Party Library
from loguru import logger
from pydantic import ValidationError

# Package Library
from kgfeg.kgs.llm import KGUsageTracker, adjudicate_lc_dedup_request
from kgfeg.kgs.schemas import (
    LCDedupConflict,
    LCDedupGroup,
    LCDedupGroups,
    LCDedupPair,
    LCDedupPairVerdict,
    LCDedupRequest,
    LCDedupResponse,
    LCGenerationRequest,
    LCGenerationResponse,
    LCRequestSFI,
)
from kgfeg.kgs.utils import KGDirs, append_jsonl_model, make_dir, reset_output_files
from kgfeg.kgs.validators import verify_lc_dedup_quality
from kgfeg.page_ir_extraction.validators import QualityError
from kgfeg.schemas import (
    _CreateKGLCDedupBlockingConfig,
    _CreateKGLCDedupLanguagePackConfig,
    _CreateKGLearningComponentsConfig,
)
from kgfeg.utils.general import write_to_json


class _ScopeFeatures:
    """Precomputed similarity features for one scope's unique texts."""

    def __init__(self) -> None:
        """Initialize empty feature maps."""

        self.core_tokens: dict[str, frozenset[str]] = {}
        self.pack_tokens: dict[str, frozenset[str]] = {}
        self.small_neighborhoods: set[Optional[UUID]] = set()
        self.tag_bags: dict[str, frozenset[str]] = {}
        self.trigrams: dict[str, frozenset[str]] = {}


class _SkillUnit:
    """One unique normalized skill text within a dedup scope.

    Accumulates every claim of the text: claiming SFIs, raw surface forms,
    tags, direct-parent UUIDs, and source-facing statement types.
    """

    def __init__(self) -> None:
        """Initialize an empty skill unit."""

        self.claim_count = 0
        self.parent_uuids: set[Optional[UUID]] = set()
        self.sfi_uuids: set[UUID] = set()
        self.statement_types: set[str] = set()
        self.surface_forms: list[str] = []
        self.tag_texts: set[str] = set()


def _adjudicate_candidate_pairs(
    *,
    lc_dedup_instructions: Optional[str],
    lc_dedup_requests: Sequence[LCDedupRequest],
    overwrite: bool,
    usage_tracker: KGUsageTracker,
    verdicts_fp: Path,
) -> tuple[dict[int, LCDedupPairVerdict], int]:
    """Adjudicate candidate pairs sequentially with request_id-keyed resume.

    Parameters
    ----------
    lc_dedup_instructions
        Optional curriculum-specific adjudication policy for the judge, or
        None for the generic rubric alone.
    lc_dedup_requests
        Deterministic adjudication requests, in request order.
    overwrite
        When True, discard saved verdicts and re-adjudicate from scratch.
    usage_tracker
        Tracker to accumulate LLM token usage.
    verdicts_fp
        Path to the verdicts JSONL artifact (resume source).

    Returns
    -------
    tuple[dict[int, LCDedupPairVerdict], int]
        Verdicts keyed by pair ID, and the count of resumed responses.
    """

    if overwrite:
        logger.info(
            "Starting LC dedup adjudication from scratch because overwrite=True."
        )
        reset_output_files(output_fps=[verdicts_fp])
        completed: dict[str, LCDedupResponse] = {}
    else:
        completed = _load_resumable_lc_dedup_responses(
            lc_dedup_requests=lc_dedup_requests, verdicts_fp=verdicts_fp
        )
        reset_output_files(output_fps=[verdicts_fp])
        for response in completed.values():
            append_jsonl_model(fp=verdicts_fp, model=response)

    verdicts: dict[int, LCDedupPairVerdict] = {}
    for request in lc_dedup_requests:
        response = completed.get(request.request_id)
        if response is None:
            response = adjudicate_lc_dedup_request(
                lc_dedup_instructions=lc_dedup_instructions,
                lc_dedup_request=request,
                usage_tracker=usage_tracker,
            )
            append_jsonl_model(fp=verdicts_fp, model=response)
        for verdict in response.verdicts:
            verdicts[verdict.pair_id] = verdict
    return verdicts, len(completed)


def _build_dedup_groups(
    *,
    candidate_pairs: Sequence[LCDedupPair],
    units_by_scope: dict[str, dict[str, "_SkillUnit"]],
    verdicts: dict[int, LCDedupPairVerdict],
) -> tuple[list[LCDedupGroup], list[LCDedupConflict]]:
    """Assemble verdicts into multi-claim duplicate groups per scope.

    Parameters
    ----------
    candidate_pairs
        Nominated pairs (scope keys and texts for verdict mapping).
    units_by_scope
        Skill units keyed by scope key, then normalized text.
    verdicts
        Adjudication verdicts keyed by pair ID.

    Returns
    -------
    tuple[list[LCDedupGroup], list[LCDedupConflict]]
        Multi-claim groups and chaining-guard conflicts.
    """

    pairs_by_id = {pair.pair_id: pair for pair in candidate_pairs}
    same_links_by_scope: dict[str, list[tuple[str, str]]] = {}
    distinct_links_by_scope: dict[str, set[frozenset[str]]] = {}
    for pair_id, verdict in verdicts.items():
        pair = pairs_by_id[pair_id]
        if verdict.same_skill:
            same_links_by_scope.setdefault(pair.scope_key, []).append(
                (pair.text_a, pair.text_b)
            )
        else:
            distinct_links_by_scope.setdefault(pair.scope_key, set()).add(
                frozenset((pair.text_a, pair.text_b))
            )

    groups: list[LCDedupGroup] = []
    conflicts: list[LCDedupConflict] = []
    for scope_key in sorted(units_by_scope):
        units = units_by_scope[scope_key]
        clusters, scope_conflicts = _union_find_clusters(
            distinct_links=distinct_links_by_scope.get(scope_key, set()),
            same_links=same_links_by_scope.get(scope_key, []),
            texts=sorted(units),
        )
        conflicts.extend(scope_conflicts)
        for members in clusters.values():
            if len(members) == 1 and units[members[0]].claim_count == 1:
                continue
            groups.append(
                LCDedupGroup(
                    canonical_text=_elect_canonical_text(members=members, units=units),
                    member_texts=sorted(members),
                    scope_key=scope_key,
                    sfi_uuids=sorted(
                        {
                            sfi_uuid
                            for member in members
                            for sfi_uuid in units[member].sfi_uuids
                        },
                        key=str,
                    ),
                )
            )
    return groups, conflicts


def _build_dedup_request_id(pairs: Sequence[LCDedupPair]) -> str:
    """Build the deterministic request ID for one batch of candidate pairs.

    Parameters
    ----------
    pairs
        Candidate pairs in the batch, in batch order.

    Returns
    -------
    str
        The deterministic request ID.
    """

    joined = "||".join(f"{pair.text_a}|{pair.text_b}" for pair in pairs)
    return "lc_dedup_request_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _build_scope_features(
    *,
    blocking: _CreateKGLCDedupBlockingConfig,
    pack: Optional[_CreateKGLCDedupLanguagePackConfig],
    units: dict[str, _SkillUnit],
) -> _ScopeFeatures:
    """Precompute similarity features for one scope's unique texts.

    Parameters
    ----------
    blocking
        Nomination thresholds (corpus stopword DF, neighborhood cap).
    pack
        Language pack for booster token sets, or None for core-only.
    units
        Skill units of this scope, keyed by normalized text.

    Returns
    -------
    _ScopeFeatures
        Token sets, trigram sets, tag bags, and small neighborhoods.
    """

    texts = sorted(units)
    raw_tokens = {text: frozenset(_tokenize(text)) for text in texts}
    stopwords = _corpus_stopword_set(
        df_threshold=blocking.corpus_stopword_df,
        token_lists=[raw_tokens[text] for text in texts],
    )

    features = _ScopeFeatures()
    features.core_tokens = {
        text: frozenset(raw_tokens[text] - stopwords) for text in texts
    }
    features.trigrams = {text: _char_trigrams(text) for text in texts}
    features.tag_bags = {
        text: frozenset(
            _fold_token(pack=pack, token=token) if pack else token
            for tag in units[text].tag_texts
            for token in _tokenize(tag)
        )
        for text in texts
    }
    if pack is not None:
        pack_stopwords = frozenset(pack.stopwords)
        features.pack_tokens = {
            text: frozenset(
                _fold_token(pack=pack, token=token)
                for token in raw_tokens[text]
                if token not in pack_stopwords
            )
            for text in texts
        }

    neighborhood_texts: dict[Optional[UUID], set[str]] = {}
    for text in texts:
        for parent_uuid in units[text].parent_uuids:
            neighborhood_texts.setdefault(parent_uuid, set()).add(text)
    features.small_neighborhoods = {
        parent_uuid
        for parent_uuid, members in neighborhood_texts.items()
        if 0 < len(members) <= blocking.neighborhood_all_pairs_max_size
    }
    return features


def _char_trigrams(text: str) -> frozenset[str]:
    """Build the character-trigram set of a normalized text.

    Parameters
    ----------
    text
        Normalized skill text.

    Returns
    -------
    frozenset[str]
        Character trigrams (whole text when shorter than three characters).
    """

    squashed = re.sub(r"\W+", " ", text, flags=re.UNICODE).strip()
    if len(squashed) < 3:
        return frozenset({squashed})
    return frozenset(squashed[i : i + 3] for i in range(len(squashed) - 2))


def _collect_skill_units(
    *,
    lc_dedup_scope: str,
    lc_generation_requests: Sequence[LCGenerationRequest],
    lc_generation_responses: Sequence[LCGenerationResponse],
) -> dict[str, dict[str, _SkillUnit]]:
    """Group every generated skill claim into per-scope unique-text units.

    Parameters
    ----------
    lc_dedup_scope
        Configured merge scope (framework | top_ancestor | parent | none).
    lc_generation_requests
        LC generation requests (ancestor paths for scope keys and
        neighborhoods).
    lc_generation_responses
        LC generation responses carrying the generated skills.

    Returns
    -------
    dict[str, dict[str, _SkillUnit]]
        Units keyed by scope key, then by normalized skill text.

    Raises
    ------
    ValueError
        If a response claims an SFI absent from the requests.
    """

    request_sfis: dict[UUID, LCRequestSFI] = {
        request_sfi.final_sfi_uuid: request_sfi
        for request in lc_generation_requests
        for request_sfi in request.sfis
    }

    units: dict[str, dict[str, _SkillUnit]] = {}
    for response in lc_generation_responses:
        for item in response.items:
            request_sfi = request_sfis.get(item.sfi_uuid)
            if request_sfi is None:
                raise ValueError(
                    f"LC dedup: response for request {response.request_id} claims "
                    f"SFI {item.sfi_uuid}, which is absent from the LC "
                    f"generation requests."
                )
            scope_key = _scope_key_for(
                lc_dedup_scope=lc_dedup_scope, request_sfi=request_sfi
            )
            parent_uuids: set[Optional[UUID]] = set(request_sfi.parent_uuids) or {None}
            for skill in item.skills:
                normalized = _normalize_skill_text(skill.description)
                unit = units.setdefault(scope_key, {}).setdefault(
                    normalized, _SkillUnit()
                )
                unit.claim_count += 1
                unit.parent_uuids.update(parent_uuids)
                unit.sfi_uuids.add(item.sfi_uuid)
                unit.statement_types.add(request_sfi.statement_type)
                unit.surface_forms.append(skill.description.strip())
                unit.tag_texts.update(tag.lower().strip() for tag in skill.tags)
    return units


def _corpus_stopword_set(
    *, df_threshold: float, token_lists: Sequence[frozenset[str]]
) -> frozenset[str]:
    """Derive corpus stopwords from token document frequency.

    Parameters
    ----------
    df_threshold
        Fraction of texts above which a token counts as a stopword.
    token_lists
        Raw token sets, one per unique text.

    Returns
    -------
    frozenset[str]
        Tokens appearing in more than ``df_threshold`` of the texts.
    """

    if not token_lists:
        return frozenset()
    counts: dict[str, int] = {}
    for tokens in token_lists:
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
    return frozenset(
        token
        for token, count in counts.items()
        if count / len(token_lists) > df_threshold
    )


def _elect_canonical_text(
    *, members: Sequence[str], units: dict[str, _SkillUnit]
) -> str:
    """Elect one canonical text for a duplicate cluster, deterministically.

    Most-claimed wins; ties break to the shortest, then lexicographically
    smallest text.

    Parameters
    ----------
    members
        Normalized member texts of the cluster.
    units
        Skill units of the cluster's scope (claim counts).

    Returns
    -------
    str
        The canonical normalized text.
    """

    return min(members, key=lambda text: (-units[text].claim_count, len(text), text))


def _fold_token(*, pack: _CreateKGLCDedupLanguagePackConfig, token: str) -> str:
    """Fold configured affixes off a token using a language pack.

    Parameters
    ----------
    pack
        Language pack declaring prefixes/suffixes to fold.
    token
        Lowercase token to fold.

    Returns
    -------
    str
        The folded token (candidacy only; identity never uses folding).
    """

    for prefix in pack.strip_prefixes:
        if token.startswith(prefix) and len(token) > len(prefix) + 1:
            token = token[len(prefix) :]
            break
    for suffix in pack.strip_suffixes:
        if len(token) >= pack.min_fold_length and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _jaccard(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    """Compute Jaccard overlap between two sets.

    Parameters
    ----------
    set_a
        First set.
    set_b
        Second set.

    Returns
    -------
    float
        Jaccard overlap (0.0 when either set is empty).
    """

    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _load_resumable_lc_dedup_responses(
    *,
    lc_dedup_requests: Sequence[LCDedupRequest],
    verdicts_fp: Path,
) -> dict[str, LCDedupResponse]:
    """Load completed LC dedup adjudications that remain valid for this run.

    Reads a valid prefix of the verdicts artifact (a truncated or invalid
    trailing line is dropped with a warning). Every parsed response must
    match a current request by ``request_id`` and pass the quality checks
    against it; any stale, duplicate, or invalid response discards ALL
    progress with a warning so the run restarts cleanly.

    Parameters
    ----------
    lc_dedup_requests
        Current deterministic adjudication requests.
    verdicts_fp
        Path to the LC dedup verdicts JSONL artifact.

    Returns
    -------
    dict[str, LCDedupResponse]
        Valid completed responses keyed by request ID.
    """

    if not verdicts_fp.exists() or verdicts_fp.stat().st_size == 0:
        return {}

    requests_by_id = {request.request_id: request for request in lc_dedup_requests}
    completed: dict[str, LCDedupResponse] = {}
    with verdicts_fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                response = LCDedupResponse.model_validate_json(stripped)
            except ValidationError:
                logger.warning(
                    f"Dropping invalid/truncated LC dedup response at "
                    f"{verdicts_fp}:{line_number}; resuming from the "
                    f"{len(completed)} valid responses before it."
                )
                break
            request = requests_by_id.get(response.request_id)
            if request is None or response.request_id in completed:
                logger.warning(
                    f"LC dedup response at {verdicts_fp}:{line_number} has a "
                    f"stale or duplicate request_id {response.request_id!r}; "
                    f"discarding all saved progress."
                )
                return {}
            try:
                verify_lc_dedup_quality(
                    lc_dedup_request=request, lc_dedup_response=response
                )
            except QualityError as e:
                logger.warning(
                    f"Saved LC dedup response for request "
                    f"{response.request_id!r} no longer passes quality checks "
                    f"({str(e)[:200]}); discarding all saved progress."
                )
                return {}
            completed[response.request_id] = response
    return completed


def _nominate_candidate_pairs(
    *,
    blocking: _CreateKGLCDedupBlockingConfig,
    pack: Optional[_CreateKGLCDedupLanguagePackConfig],
    pair_id_start: int,
    scope_key: str,
    units: dict[str, _SkillUnit],
) -> list[LCDedupPair]:
    """Nominate candidate duplicate pairs within one dedup scope.

    Union of nomination rules — language-independent core (token identity,
    token Jaccard, containment, char-trigram Jaccard, tag-bag Jaccard over
    corpus-DF-filtered Unicode tokens), booster rules from the config-declared
    language pack (curated stopwords + affix folding) when the document
    profile declares one, and similarity-blind neighborhood review sets (all
    pairs under a shared direct parent when the neighborhood is small enough).

    Parameters
    ----------
    blocking
        Nomination thresholds.
    pack
        Language pack for booster rules, or None for core-only.
    pair_id_start
        First pair ID to assign (IDs are global across scopes).
    scope_key
        The dedup scope these units share.
    units
        Skill units of this scope, keyed by normalized text.

    Returns
    -------
    list[LCDedupPair]
        Nominated pairs in deterministic order.
    """

    texts = sorted(units)
    features = _build_scope_features(blocking=blocking, pack=pack, units=units)

    pairs: list[LCDedupPair] = []
    pair_id = pair_id_start
    for text_a, text_b in combinations(texts, 2):
        rules = _pair_rule_names(
            blocking=blocking,
            features=features,
            pack=pack,
            text_a=text_a,
            text_b=text_b,
            units=units,
        )
        if rules:
            pairs.append(
                LCDedupPair(
                    nomination_rules=rules,
                    pair_id=pair_id,
                    scope_key=scope_key,
                    statement_types_a=sorted(units[text_a].statement_types),
                    statement_types_b=sorted(units[text_b].statement_types),
                    text_a=text_a,
                    text_b=text_b,
                )
            )
            pair_id += 1
    return pairs


def _normalize_skill_text(text: str) -> str:
    """Normalize one skill text for identity and grouping.

    Lowercase, whitespace-collapse, trailing-period strip — deliberately
    language-independent; no stemming, ever (identity must be universal).

    Parameters
    ----------
    text
        Raw skill description.

    Returns
    -------
    str
        The normalized text.
    """

    return re.sub(r"\s+", " ", text.lower().strip()).rstrip(".").strip()


def _pair_rule_names(
    *,
    blocking: _CreateKGLCDedupBlockingConfig,
    features: _ScopeFeatures,
    pack: Optional[_CreateKGLCDedupLanguagePackConfig],
    text_a: str,
    text_b: str,
    units: dict[str, _SkillUnit],
) -> list[str]:
    """Evaluate every nomination rule for one candidate text pair.

    Parameters
    ----------
    blocking
        Nomination thresholds.
    features
        Precomputed similarity features for the scope.
    pack
        Language pack, or None when only core rules apply.
    text_a
        First normalized text.
    text_b
        Second normalized text.
    units
        Skill units of the scope (neighborhood membership).

    Returns
    -------
    list[str]
        Sorted names of the rules that fired (empty = not nominated).
    """

    rules = _token_rule_names(
        blocking=blocking,
        suffix="",
        tokens_a=features.core_tokens[text_a],
        tokens_b=features.core_tokens[text_b],
    )
    if (
        _jaccard(features.trigrams[text_a], features.trigrams[text_b])
        >= blocking.trigram_jaccard_threshold
    ):
        rules.append("trigram_jaccard")
    if (
        _jaccard(features.tag_bags[text_a], features.tag_bags[text_b])
        >= blocking.tag_jaccard_threshold
    ):
        rules.append("tag_jaccard")
    if pack is not None:
        rules.extend(
            _token_rule_names(
                blocking=blocking,
                suffix="_pack",
                tokens_a=features.pack_tokens[text_a],
                tokens_b=features.pack_tokens[text_b],
            )
        )
    if any(
        parent_uuid in features.small_neighborhoods
        for parent_uuid in units[text_a].parent_uuids & units[text_b].parent_uuids
    ):
        rules.append("neighborhood")
    return sorted(set(rules))


def _scope_key_for(*, lc_dedup_scope: str, request_sfi: LCRequestSFI) -> str:
    """Compute the dedup scope key for one LC-source seed.

    Seeds with an empty or unresolved ancestor path fall back to their own
    UUID under scoped modes — an unreliable path must never enable merging.
    A seed or ancestor with several hasChild parents keys on the whole
    parent set, so no single branch decides the scope.

    Parameters
    ----------
    lc_dedup_scope
        Configured merge scope.
    request_sfi
        The seed's LC generation request entry (ancestor path + status).

    Returns
    -------
    str
        The scope key.
    """

    if lc_dedup_scope == "framework":
        return "framework"
    if lc_dedup_scope == "none":
        return str(request_sfi.final_sfi_uuid)
    path = request_sfi.ancestor_path
    if not path or request_sfi.ancestor_path_status != "resolved":
        return str(request_sfi.final_sfi_uuid)
    if lc_dedup_scope == "top_ancestor":
        scope_uuids = [
            ancestor.case_identifier_uuid
            for ancestor in path
            if not ancestor.parent_uuids
        ]
    else:
        scope_uuids = list(request_sfi.parent_uuids)
    return "|".join(sorted(str(scope_uuid) for scope_uuid in scope_uuids))


def _token_rule_names(
    *,
    blocking: _CreateKGLCDedupBlockingConfig,
    suffix: str,
    tokens_a: frozenset[str],
    tokens_b: frozenset[str],
) -> list[str]:
    """Evaluate the token-set nomination rules for one pair.

    Parameters
    ----------
    blocking
        Nomination thresholds.
    suffix
        Rule-name suffix ("" for the core rules, "_pack" for pack rules).
    tokens_a
        First token set.
    tokens_b
        Second token set.

    Returns
    -------
    list[str]
        Names of the token rules that fired.
    """

    rules: list[str] = []
    if not tokens_a or not tokens_b:
        return rules
    shared = len(tokens_a & tokens_b)
    if tokens_a == tokens_b:
        rules.append(f"token_identical{suffix}")
    if _jaccard(tokens_a, tokens_b) >= blocking.token_jaccard_threshold:
        rules.append(f"token_jaccard{suffix}")
    if (
        shared / min(len(tokens_a), len(tokens_b)) >= blocking.containment_threshold
        and shared >= blocking.containment_min_shared_tokens
    ):
        rules.append(f"containment{suffix}")
    return rules


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase Unicode word tokens.

    Parameters
    ----------
    text
        Text to tokenize.

    Returns
    -------
    list[str]
        Lowercase Unicode word tokens.
    """

    return re.findall(r"\w+", text.lower(), re.UNICODE)


def _union_find_clusters(
    *,
    distinct_links: set[frozenset[str]],
    same_links: Sequence[tuple[str, str]],
    texts: Sequence[str],
) -> tuple[dict[str, list[str]], list[LCDedupConflict]]:
    """Cluster texts from pairwise SAME verdicts, guarding against chaining.

    Merge links are processed in sorted order for determinism. A link whose
    merge would place an explicitly-DISTINCT pair inside one cluster is
    dropped and recorded as a conflict.

    Parameters
    ----------
    distinct_links
        Pairs the judge explicitly ruled distinct.
    same_links
        Pairs the judge ruled the same skill.
    texts
        All unique texts of the scope.

    Returns
    -------
    tuple[dict[str, list[str]], list[LCDedupConflict]]
        Cluster members keyed by leader text, and dropped-link conflicts.
    """

    parent = {text: text for text in texts}
    members = {text: {text} for text in texts}

    def find(text: str) -> str:
        """Follow (and compress) parent pointers to the cluster leader.

        Parameters
        ----------
        text
            Member text to resolve.

        Returns
        -------
        str
            The cluster's current leader text.
        """

        while parent[text] != text:
            parent[text] = parent[parent[text]]
            text = parent[text]
        return text

    conflicts: list[LCDedupConflict] = []
    for text_a, text_b in sorted(same_links):
        root_a, root_b = find(text_a), find(text_b)
        if root_a == root_b:
            continue
        if any(
            frozenset((member_a, member_b)) in distinct_links
            for member_a in members[root_a]
            for member_b in members[root_b]
        ):
            conflicts.append(
                LCDedupConflict(
                    reason=(
                        "merging would join texts the judge explicitly ruled "
                        "distinct (chaining guard)"
                    ),
                    text_a=text_a,
                    text_b=text_b,
                )
            )
            continue
        parent[root_a] = root_b
        members[root_b] |= members.pop(root_a)

    clusters: dict[str, list[str]] = {}
    for text in texts:
        clusters.setdefault(find(text), []).append(text)
    return clusters, conflicts


def group_duplicate_skills(
    *,
    kg_dirs: KGDirs,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_generation_requests: Sequence[LCGenerationRequest],
    lc_generation_responses: Sequence[LCGenerationResponse],
    overwrite: bool,
    usage_tracker: KGUsageTracker,
) -> LCDedupGroups:
    """Group the exact and semantic duplicate skills.

    Exact duplicates collapse by normalized text within `lc_dedup_scope`.
    When `lc_semantic_dedup` is enabled, deterministic blocking nominates
    candidate pairs, a bounded LLM judge adjudicates them (sequential,
    resumable by request_id, `overwrite` restarts), and union-find with a
    chaining guard assembles the verdicts into clusters with a
    deterministically elected canonical text per cluster. Adjudication
    failures propagate loudly; a resumed run continues from the cached
    verdicts.

    Parameters
    ----------
    kg_dirs
        KG artifact directories; artifacts are written under ``kg_dirs.root``.
    lc_config
        Learning Components runtime configuration.
    lc_generation_requests
        Deterministic LC generation requests.
    lc_generation_responses
        Validated LC generation responses.
    overwrite
        When True, discard saved verdicts and re-adjudicate from scratch.
    usage_tracker
        Tracker to accumulate LLM token usage (``lc_dedup`` bucket).

    Returns
    -------
    LCDedupGroups
        Multi-claim duplicate groups, conflicts, and grouping counts.

    Raises
    ------
    ValueError
        If a response claims an SFI absent from the requests.
    """

    units_by_scope = _collect_skill_units(
        lc_dedup_scope=lc_config.lc_dedup_scope,
        lc_generation_requests=lc_generation_requests,
        lc_generation_responses=lc_generation_responses,
    )
    total_claim_count = sum(
        unit.claim_count for units in units_by_scope.values() for unit in units.values()
    )
    unique_text_count = sum(len(units) for units in units_by_scope.values())

    pack = lc_config.lc_dedup_language_pack

    candidate_pairs: list[LCDedupPair] = []
    for scope_key in sorted(units_by_scope):
        candidate_pairs.extend(
            _nominate_candidate_pairs(
                blocking=lc_config.lc_dedup_blocking,
                pack=pack,
                pair_id_start=len(candidate_pairs),
                scope_key=scope_key,
                units=units_by_scope[scope_key],
            )
        )

    make_dir(kg_dirs.root)
    pairs_fp = kg_dirs.root / "lc_dedup_candidate_pairs.jsonl"
    verdicts_fp = kg_dirs.root / "lc_dedup_verdicts.jsonl"
    write_to_json(fp=pairs_fp, json_info=candidate_pairs)

    batch_size = lc_config.lc_dedup_batch_size
    lc_dedup_requests = [
        LCDedupRequest(pairs=batch, request_id=_build_dedup_request_id(batch))
        for batch in (
            candidate_pairs[start : start + batch_size]
            for start in range(0, len(candidate_pairs), batch_size)
        )
    ]

    verdicts: dict[int, LCDedupPairVerdict] = {}
    resumed = 0
    if lc_config.lc_semantic_dedup and lc_dedup_requests:
        verdicts, resumed = _adjudicate_candidate_pairs(
            lc_dedup_instructions=lc_config.lc_dedup_instructions,
            lc_dedup_requests=lc_dedup_requests,
            overwrite=overwrite,
            usage_tracker=usage_tracker,
            verdicts_fp=verdicts_fp,
        )
    elif not lc_config.lc_semantic_dedup:
        logger.warning(
            f"lc_semantic_dedup=False; LC dedup groups exact duplicates only "
            f"({len(candidate_pairs)} nominated pairs left unadjudicated)."
        )

    groups, conflicts = _build_dedup_groups(
        candidate_pairs=candidate_pairs,
        units_by_scope=units_by_scope,
        verdicts=verdicts,
    )

    dedup_groups = LCDedupGroups(
        candidate_pair_count=len(candidate_pairs),
        conflict_count=len(conflicts),
        conflicts=conflicts,
        exact_duplicate_claim_count=total_claim_count - unique_text_count,
        groups=sorted(groups, key=lambda group: group.canonical_text),
        judged_same_count=sum(1 for verdict in verdicts.values() if verdict.same_skill),
        total_claim_count=total_claim_count,
        unique_text_count=unique_text_count,
    )
    write_to_json(
        fp=kg_dirs.root / "lc_dedup_groups.json",
        json_info=dedup_groups.model_dump(mode="json"),
    )

    logger.success(
        f"Grouped duplicate skills: claims={total_claim_count}; "
        f"unique_texts={unique_text_count}; "
        f"candidate_pairs={len(candidate_pairs)}; "
        f"requests={len(lc_dedup_requests)} (resumed={resumed}); "
        f"judged_same={dedup_groups.judged_same_count}; "
        f"multi_claim_groups={len(groups)}; "
        f"conflicts={len(conflicts)}"
    )
    return dedup_groups
