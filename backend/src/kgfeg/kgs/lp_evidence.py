"""Named, non-embedding evidence for explicitly supplied LP endpoint pairs.

Evidence is weak nomination support, never a semantic judgment or a published edge. The
extractor applies the authoritative pair filter before inspecting any features. It does
not enumerate, rank, budget, persist, or adjudicate a candidate population. Weakly
overlapping concepts can be missed; candidate recall is unmeasured and these lexical
features have not been validated for multilingual progression judgments.
"""

# Future Library
from __future__ import annotations

# Standard Library
import re
import unicodedata

from collections import deque
from dataclasses import dataclass
from uuid import UUID

# Third Party Library
from pydantic import JsonValue

# Package Library
from kgfeg.kgs.lp_candidates import (
    LPCandidateFilter,
    LPPairAdmissibility,
    build_lp_pair_filter,
)
from kgfeg.kgs.lp_index import LPGraphIndex, build_lp_graph_index
from kgfeg.kgs.lp_selection import LPSFIEligibility
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LearningComponent,
    LPCandidateEvidence,
    LPRelationshipType,
)
from kgfeg.schemas import CreateKGConfig


@dataclass(frozen=True, slots=True)
class LPEvidenceExtractor:
    """Run-scoped snapshot for filtered, on-demand pair evidence.

    Construct with ``build_lp_evidence_extractor``. Private graph and policy snapshots
    isolate feature evaluation from later input mutation. Returned records are fresh
    per call, including the filter's complete endpoint warning and provenance context.
    """

    _graph_index: LPGraphIndex
    _pair_filter: LPCandidateFilter

    def extract_pair(
        self, *, first_sfi_uuid: UUID | str, second_sfi_uuid: UUID | str
    ) -> LPPairEvidence | None:
        """Evaluate every built-in feature for one admissible logical pair.

        Parameters
        ----------
        first_sfi_uuid
            One final SFI CASE UUID in this run, in any valid UUID spelling.
        second_sfi_uuid
            The other final SFI CASE UUID; encounter order has no semantic meaning.

        Returns
        -------
        LPPairEvidence | None
            Canonical permission and named matches, possibly with no matching feature.
            Self-pairs and policy-disallowed pairs return ``None``.

        Raises
        ------
        ValueError
            If either endpoint is malformed or outside this run's final SFIs.
        """

        pair = self._pair_filter.filter_pair(
            first_sfi_uuid=first_sfi_uuid, second_sfi_uuid=second_sfi_uuid
        )

        if pair is None:
            return None

        evidence = [
            *_code_evidence(pair),
            *_hierarchy_evidence(graph_index=self._graph_index, pair=pair),
            *_lc_evidence(graph_index=self._graph_index, pair=pair),
            *_rank_evidence(pair),
            *_source_evidence(pair),
            *_text_evidence(pair),
        ]
        return LPPairEvidence(
            admissibility=pair,
            evidence=tuple(sorted(evidence, key=lambda item: item.evidence_type)),
        )


@dataclass(frozen=True, slots=True)
class LPPairEvidence:
    """Filtered pair context and feature matches without population selection.

    Attributes
    ----------
    admissibility
        Unchanged canonical pair identity, allowed outcomes, complete endpoint records,
        warnings, unresolved status, fallback audit IDs, and upstream provenance.
    evidence
        Named positive matches in name order, not relevance order. An empty tuple means
        the pair is permitted but no feature matched. Audit flags and missing values
        are retained in endpoint context; they never become positive feature matches.
    """

    admissibility: LPPairAdmissibility
    evidence: tuple[LPCandidateEvidence, ...]


def _ancestor_distances(
    *, graph_index: LPGraphIndex, sfi_uuid: UUID
) -> dict[UUID, int]:
    """Visit all positive DAG branches without enumerating their path combinations.

    Parameters
    ----------
    graph_index
        Validated graph whose SFI adjacency excludes every framework attachment.
    sfi_uuid
        Endpoint whose ancestors are sought.

    Returns
    -------
    dict[UUID, int]
        Every reachable SFI ancestor with its minimum positive edge distance. Minimum
        distance summarizes proximity without selecting one canonical ancestry path.
    """

    distances: dict[UUID, int] = {}
    pending = deque(
        (parent, 1) for parent in graph_index.parent_sfi_uuids_by_sfi_uuid[sfi_uuid]
    )

    while pending:
        ancestor, distance = pending.popleft()

        if ancestor in distances:
            continue

        distances[ancestor] = distance
        pending.extend(
            (parent, distance + 1)
            for parent in graph_index.parent_sfi_uuids_by_sfi_uuid[ancestor]
        )

    return distances


def _code_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Compare source-authoritative codes as generic leading alphanumeric segments.

    Parameters
    ----------
    pair
        Filtered pair retaining the original codes and all AS code/merge audit context.

    Returns
    -------
    list[LPCandidateEvidence]
        A weak prefix match, or no match. Normalized-code metadata and invented local
        code grammars are not consulted; warnings do not certify a code as correct.
    """

    first_code = pair.first_sfi.sfi.statement_code
    second_code = pair.second_sfi.sfi.statement_code

    if not first_code or not second_code:
        return []

    first_parts = _word_tokens(first_code)
    second_parts = _word_tokens(second_code)
    common: list[JsonValue] = []

    for first, second in zip(first_parts, second_parts):
        if first != second:
            break

        common.append(first)

    return (
        []
        if not common
        else [
            _nomination(
                evidence_type="source_code_prefix",
                pair=pair,
                references=_sfi_references(pair),
                triggering_values={
                    "common_segments": common,
                    "first_code": first_code,
                    "second_code": second_code,
                    "strength": "weak",
                },
            )
        ]
    )


def _hierarchy_evidence(
    *, graph_index: LPGraphIndex, pair: LPPairAdmissibility
) -> list[LPCandidateEvidence]:
    """Capture shared context and ancestry across every positive SFI branch.

    Parameters
    ----------
    graph_index
        Validated DAG index with fallback/root placement outside positive adjacency.
    pair
        Canonical pair whose complete direct-parent lists remain in endpoint context.

    Returns
    -------
    list[LPCandidateEvidence]
        Shared ancestor and/or endpoint ancestry values. A shared framework alone,
        including unresolved fallback placement, never produces a match.
    """

    first = _ancestor_distances(graph_index=graph_index, sfi_uuid=pair.first_sfi_uuid)
    second = _ancestor_distances(graph_index=graph_index, sfi_uuid=pair.second_sfi_uuid)
    common = sorted(first.keys() & second.keys(), key=str)
    values: dict[str, JsonValue] = {}

    if common:
        values["shared_ancestors"] = [
            {
                "first_distance": first[ancestor],
                "second_distance": second[ancestor],
                "sfi_uuid": str(ancestor),
                "statement_type": graph_index.sfi_by_uuid[ancestor].statement_type,
            }
            for ancestor in common
        ]

    if pair.first_sfi_uuid in second:
        values["first_is_ancestor_of_second_distance"] = second[pair.first_sfi_uuid]

    if pair.second_sfi_uuid in first:
        values["second_is_ancestor_of_first_distance"] = first[pair.second_sfi_uuid]

    return (
        []
        if not values
        else [
            _nomination(
                evidence_type="hierarchy_context",
                pair=pair,
                references=[
                    *_sfi_references(pair),
                    *(f"sfi:{ancestor}" for ancestor in common),
                ],
                triggering_values=values,
            )
        ]
    )


def _lc_evidence(
    *, graph_index: LPGraphIndex, pair: LPPairAdmissibility
) -> list[LPCandidateEvidence]:
    """Compare exact supporting LC identities and their text/tag token bags.

    Parameters
    ----------
    graph_index
        Validated support adjacency; LCs are evidence only, never pair endpoints.
    pair
        Canonical pair to inspect.

    Returns
    -------
    list[LPCandidateEvidence]
        Independent exact, description-token, and tag-token overlap matches. Repeated
        generic LCs may match but cannot establish pedagogical correctness or an edge.
    """

    first = graph_index.learning_components_by_sfi_uuid[pair.first_sfi_uuid]
    second = graph_index.learning_components_by_sfi_uuid[pair.second_sfi_uuid]
    first_ids = frozenset(str(component.identifier) for component in first)
    second_ids = frozenset(str(component.identifier) for component in second)
    references = [f"lc:{identifier}" for identifier in sorted(first_ids | second_ids)]
    return [
        *_overlap_evidence(
            evidence_type="shared_learning_components",
            first=first_ids,
            pair=pair,
            references=[
                f"lc:{identifier}" for identifier in sorted(first_ids & second_ids)
            ],
            second=second_ids,
        ),
        *_overlap_evidence(
            evidence_type="lc_text_token_overlap",
            first=frozenset(
                token
                for component in first
                for token in _word_tokens(component.description)
            ),
            pair=pair,
            references=references,
            second=frozenset(
                token
                for component in second
                for token in _word_tokens(component.description)
            ),
        ),
        *_overlap_evidence(
            evidence_type="lc_tag_token_overlap",
            first=frozenset(
                token for component in first for token in _tag_tokens(component)
            ),
            pair=pair,
            references=references,
            second=frozenset(
                token for component in second for token in _tag_tokens(component)
            ),
        ),
    ]


def _nomination(
    *,
    evidence_type: str,
    pair: LPPairAdmissibility,
    references: list[str],
    triggering_values: dict[str, JsonValue],
) -> LPCandidateEvidence:
    """Attach feature values only to relationship types allowed by the hard filter.

    Parameters
    ----------
    evidence_type
        Fixed code-owned name for this feature.
    pair
        Filtered canonical pair supplying authoritative admissible outcomes.
    references
        Source SFI or LC identifiers supporting the concrete values.
    triggering_values
        Nonempty JSON values explaining the feature match.

    Returns
    -------
    LPCandidateEvidence
        Intrinsically validated evidence without a chosen relation or direction.
    """

    relationships: list[LPRelationshipType] = []

    for option in pair.admissible_decisions:
        if (
            option.decision in ("buildsTowards", "relatesTo")
            and option.decision not in relationships
        ):
            relationships.append(option.decision)

    return LPCandidateEvidence(
        evidence_type=evidence_type,
        nominated_relationships=relationships,
        references=sorted(set(references)),
        triggering_values=triggering_values,
    )


def _overlap_evidence(
    *,
    evidence_type: str,
    first: frozenset[str],
    pair: LPPairAdmissibility,
    references: list[str],
    second: frozenset[str],
) -> list[LPCandidateEvidence]:
    """Record a nonempty set intersection and its exact Jaccard inputs.

    Parameters
    ----------
    evidence_type
        Stable name of the concrete feature family.
    first
        Lower-UUID endpoint's tokens, character trigrams, or LC identifiers.
    pair
        Filtered pair supplying relation permissions.
    references
        Identifiers for the source records used to build the compared sets.
    second
        Higher-UUID endpoint's corresponding values.

    Returns
    -------
    list[LPCandidateEvidence]
        A single explainable match when at least one value is shared, otherwise empty.
        Jaccard is descriptive evidence, not confidence or an acceptance threshold.
    """

    shared = first & second

    if not shared:
        return []

    union_count = len(first | second)
    return [
        _nomination(
            evidence_type=evidence_type,
            pair=pair,
            references=references,
            triggering_values={
                "first_count": len(first),
                "jaccard": len(shared) / union_count,
                "second_count": len(second),
                "shared_count": len(shared),
                "shared_values": list(sorted(shared)),
                "union_count": union_count,
            },
        )
    ]


def _rank_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Record same or adjacent configured local ranks as weak proximity evidence.

    Parameters
    ----------
    pair
        Filtered pair whose coordinates were resolved from canonical identity scope.

    Returns
    -------
    list[LPCandidateEvidence]
        A proximity match when both ranks exist and differ by at most one. Larger gaps
        remain admissible to other signals; a missing coordinate never becomes a rank.
    """

    first = pair.first_sfi.coordinate
    second = pair.second_sfi.coordinate

    if first.rank is None or second.rank is None:
        return []

    gap = abs(first.rank - second.rank)
    return (
        []
        if gap > 1
        else [
            _nomination(
                evidence_type="local_rank_proximity",
                pair=pair,
                references=_sfi_references(pair),
                triggering_values={
                    "coordinate_statement_type": first.statement_type,
                    "first_rank": first.rank,
                    "first_value": first.canonical_value,
                    "rank_gap": gap,
                    "second_rank": second.rank,
                    "second_value": second.canonical_value,
                    "strength": "weak",
                },
            )
        ]
    )


def _sfi_references(pair: LPPairAdmissibility) -> list[str]:
    """Return canonical endpoint references for evidence and retained audit context.

    Parameters
    ----------
    pair
        Filtered canonical pair.

    Returns
    -------
    list[str]
        Endpoint SFI references in canonical UUID order.
    """

    return [f"sfi:{pair.first_sfi_uuid}", f"sfi:{pair.second_sfi_uuid}"]


def _source_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Compare preserved page positions without interpreting source order as rank.

    Parameters
    ----------
    pair
        Filtered pair with original source provenance and metadata.

    Returns
    -------
    list[LPCandidateEvidence]
        Weak same/adjacent-page evidence with the nearest page pair. Missing, invalid,
        or conflicting positions supply no feature; original audit context is retained.
    """

    first_pages = _source_pages(pair.first_sfi)
    second_pages = _source_pages(pair.second_sfi)

    if not first_pages or not second_pages:
        return []

    first_index = second_index = 0
    closest = (abs(first_pages[0] - second_pages[0]), first_pages[0], second_pages[0])

    while first_index < len(first_pages) and second_index < len(second_pages):
        first = first_pages[first_index]
        second = second_pages[second_index]
        closest = min(closest, (abs(first - second), first, second))

        if first <= second:
            first_index += 1
        else:
            second_index += 1

    gap, first_page, second_page = closest
    return (
        []
        if gap > 1
        else [
            _nomination(
                evidence_type="source_page_proximity",
                pair=pair,
                references=_sfi_references(pair),
                triggering_values={
                    "first_page_index": first_page,
                    "first_source_page_indexes": list(first_pages),
                    "page_gap": gap,
                    "second_page_index": second_page,
                    "second_source_page_indexes": list(second_pages),
                    "strength": "weak",
                },
            )
        ]
    )


def _source_pages(record: LPSFIEligibility) -> tuple[int, ...]:
    """Read valid page indexes only when populated upstream copies agree.

    Parameters
    ----------
    record
        Endpoint with unmodified exported metadata and entity provenance.

    Returns
    -------
    tuple[int, ...]
        Sorted unique nonnegative integer indexes, or empty when unavailable,
        malformed, or conflicting. Arbitrary metadata keys and source-window IDs are
        not positions.
    """

    populations: list[tuple[int, ...]] = []

    for source in (record.sfi.metadata, record.source_provenance):
        if "source_page_indexes" not in source:
            continue

        pages = source["source_page_indexes"]

        if not isinstance(pages, list) or any(
            isinstance(page, bool) or not isinstance(page, int) or page < 0
            for page in pages
        ):
            return ()

        populations.append(tuple(sorted(set(pages))))

    if not populations or any(pages != populations[0] for pages in populations):
        return ()

    return populations[0]


def _tag_tokens(component: LearningComponent) -> frozenset[str]:
    """Tokenize a well-formed optional LC tag list without stringifying metadata.

    Parameters
    ----------
    component
        Final supporting LC whose tags are optional metadata.

    Returns
    -------
    frozenset[str]
        Unicode tag tokens, or an empty set when tags are absent or malformed.
    """

    tags = component.metadata.get("tags", [])

    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        return frozenset()

    return frozenset(token for tag in tags for token in _word_tokens(tag))


def _text_evidence(pair: LPPairAdmissibility) -> list[LPCandidateEvidence]:
    """Compare SFI descriptions with independent token and character-trigram overlap.

    Parameters
    ----------
    pair
        Filtered pair containing authoritative final SFI descriptions.

    Returns
    -------
    list[LPCandidateEvidence]
        Nonempty lexical matches with concrete shared values and counts. No stemming,
        language-specific vocabulary, embeddings, or semantic interpretation is used.
    """

    first = pair.first_sfi.sfi.description
    second = pair.second_sfi.sfi.description
    references = _sfi_references(pair)
    return [
        *_overlap_evidence(
            evidence_type="sfi_text_token_overlap",
            first=frozenset(_word_tokens(first)),
            pair=pair,
            references=references,
            second=frozenset(_word_tokens(second)),
        ),
        *_overlap_evidence(
            evidence_type="sfi_text_trigram_overlap",
            first=_trigrams(first),
            pair=pair,
            references=references,
            second=_trigrams(second),
        ),
    ]


def _trigrams(text: str) -> frozenset[str]:
    """Extract character trigrams from normalized alphanumeric words.

    Parameters
    ----------
    text
        Authoritative description text.

    Returns
    -------
    frozenset[str]
        Unique trigrams without boundary whitespace; texts shorter than three
        characters supply no trigram evidence, but may still share word tokens.
    """

    normalized = " ".join(_word_tokens(text))
    return frozenset(
        normalized[index : index + 3]
        for index in range(len(normalized) - 2)
        if normalized[index : index + 3] == normalized[index : index + 3].strip()
    )


def _word_tokens(text: str) -> tuple[str, ...]:
    """Normalize Unicode compatibility forms and case into alphanumeric word tokens.

    Parameters
    ----------
    text
        Text or source code; normalization affects evidence only, never identity.

    Returns
    -------
    tuple[str, ...]
        Tokens in original text order with punctuation and underscores as separators.
    """

    return tuple(
        re.findall(
            pattern=r"[^\W_]+", string=unicodedata.normalize("NFKC", text).casefold()
        )
    )


def build_lp_evidence_extractor(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
) -> LPEvidenceExtractor:
    """Snapshot authoritative inputs and build a filtered evidence extractor.

    Parameters
    ----------
    as_lc_bundle
        Final passed, error-free AS+LC bundle with contained endpoints and provenance.
    doc_key
        Document identity checked against the authoritative framework by the filter.
    kg_config
        Validated curriculum configuration supplying participation and local order.

    Returns
    -------
    LPEvidenceExtractor
        On-demand feature evaluator with no pair-population processing or file I/O.

    Raises
    ------
    ValueError
        If upstream validation, document identity, containment, or coordinates fail.
    """

    bundle = as_lc_bundle.model_copy(deep=True)
    pair_filter = build_lp_pair_filter(
        as_lc_bundle=bundle, doc_key=doc_key, kg_config=kg_config
    )
    return LPEvidenceExtractor(
        _graph_index=build_lp_graph_index(bundle), _pair_filter=pair_filter
    )
