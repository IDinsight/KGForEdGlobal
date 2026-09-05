"""Deterministic admissibility, identity, ranking, and budgets for LP candidates.

The pair filter assesses only explicitly supplied endpoints. The population builder
then evaluates the fixed named evidence features, keeps bounded per-SFI shortlists,
deduplicates their union, applies final incident-pair and run budgets, and can write
the candidate artifacts. Candidate selection is permission for later adjudication,
never an accepted relationship. Its non-embedding recall and semantic quality are
unmeasured.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json

from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

# Package Library
from kgfeg.kgs.lp_admissibility import (
    LPCandidateFilter,
    build_lp_pair_filter,
    build_lp_pair_id,
)
from kgfeg.kgs.lp_evidence import LPEvidenceExtractor, build_lp_evidence_extractor
from kgfeg.kgs.lp_selection import (
    LPSelectionReport,
    build_lp_selection,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPCandidatePair,
    LPCandidateSummary,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import CreateKGConfig
from kgfeg.utils.general import make_dir, write_to_json

_CANDIDATE_LIMITATIONS = (
    "Candidate recall is unmeasured; deterministic non-embedding nomination may miss plausible pairs.",
    "Candidate structural and process counts do not establish pedagogical correctness.",
)
_EVIDENCE_RANKING_PRECEDENCE = (
    "shared_learning_components",
    "hierarchy_context",
    "lc_text_token_overlap",
    "lc_tag_token_overlap",
    "sfi_text_token_overlap",
    "sfi_text_trigram_overlap",
    "source_code_prefix",
    "local_rank_proximity",
    "source_page_proximity",
)


@dataclass(frozen=True, slots=True)
class _LPCandidateBounds:
    """Canonical eligible population and its configured pair ceilings."""

    candidate_pair_bound: int
    candidate_pair_evaluation_bound: int
    eligible_sfi_uuids: tuple[UUID, ...]
    max_candidates_per_sfi: int
    max_total_candidates: int
    total_unordered_pairs: int


@dataclass(frozen=True, slots=True)
class _LPCandidateBudgetResult:
    """Deterministic union and final candidate budget results."""

    candidate_union_count: int
    per_sfi_survivor_count: int
    retained: tuple[LPCandidatePair, ...]
    shortlist_entry_count: int


@dataclass(frozen=True, slots=True)
class _LPCandidateEvaluation:
    """Bounded evidence evaluation counts and per-SFI shortlists."""

    shortlists: dict[UUID, list[LPCandidatePair]]
    total_admissible_pairs: int
    total_nominated_pairs: int


@dataclass(frozen=True, slots=True)
class _LPCandidateRowAudit:
    """Values derived independently from serialized candidate rows."""

    candidate_payload: list[dict[str, Any]]
    candidate_warning_counts: Counter[str]
    evidence_type_counts: Counter[str]
    incidence: Counter[UUID]
    pair_ids: tuple[str, ...]
    total_candidate_pairs_with_warnings: int


@dataclass(frozen=True, slots=True)
class LPCandidatePopulation:
    """One validated, bounded candidate sequence and its reconciled summary.

    Attributes
    ----------
    candidates
        Candidate records in deterministic ranking order after both budgets.
    summary
        Material hashes, population counts, union/deduplication counts, budgets, and
        accepted limitations for the exact candidate sequence.
    """

    candidates: tuple[LPCandidatePair, ...]
    summary: LPCandidateSummary


def _apply_candidate_budgets(
    *,
    bounds: _LPCandidateBounds,
    ranked_union: tuple[LPCandidatePair, ...],
    shortlist_entry_count: int,
) -> _LPCandidateBudgetResult:
    """Apply endpoint-incidence and run-wide budgets to the ranked union.

    Candidates are considered in deterministic ranking order. A candidate is discarded
    when either endpoint has already reached its incidence limit. Survivors of that
    constraint are then truncated to the configured run-wide maximum.

    Parameters
    ----------
    bounds
        Configured per-endpoint and run-wide candidate limits.
    ranked_union
        Deduplicated candidates in deterministic ranking order.
    shortlist_entry_count
        Number of per-endpoint shortlist entries before union deduplication.

    Returns
    -------
    _LPCandidateBudgetResult
        Retained candidates and counts describing shortlist entries, the unique union,
        and survivors of the endpoint-incidence budget.
    """

    incident_counts: Counter[UUID] = Counter()
    per_sfi_budget_survivors: list[LPCandidatePair] = []

    for candidate in ranked_union:
        if (
            incident_counts[candidate.first_sfi_uuid] >= bounds.max_candidates_per_sfi
            or incident_counts[candidate.second_sfi_uuid]
            >= bounds.max_candidates_per_sfi
        ):
            continue

        per_sfi_budget_survivors.append(candidate)
        incident_counts[candidate.first_sfi_uuid] += 1
        incident_counts[candidate.second_sfi_uuid] += 1

    return _LPCandidateBudgetResult(
        candidate_union_count=len(ranked_union),
        per_sfi_survivor_count=len(per_sfi_budget_survivors),
        retained=tuple(per_sfi_budget_survivors[: bounds.max_total_candidates]),
        shortlist_entry_count=shortlist_entry_count,
    )


def _bounded_pair_endpoints(
    *, eligible_sfi_uuids: tuple[UUID, ...], pair_evaluation_bound: int
) -> tuple[tuple[UUID, UUID], ...]:
    """Select a balanced deterministic endpoint neighborhood for evidence evaluation.

    Canonical endpoints are visited by increasing cyclic distance. This gives every
    endpoint bounded early representation instead of exhausting all partners for the
    first endpoint. Duplicate unordered pairs from the reverse half of the cycle are
    removed without constructing the complete pair matrix.

    Parameters
    ----------
    eligible_sfi_uuids
        Eligible endpoint UUIDs in canonical order.
    pair_evaluation_bound
        Maximum number of distinct unordered pairs to return.

    Returns
    -------
    tuple[tuple[UUID, UUID], ...]
        At most the requested number of canonical unordered endpoint pairs.

    Raises
    ------
    ValueError
        If the endpoint order is not canonical and unique or the bound is negative.
    """

    if pair_evaluation_bound < 0:
        raise ValueError("LP pair evaluation bound cannot be negative.")

    canonical_uuids = tuple(sorted(set(eligible_sfi_uuids), key=str))

    if canonical_uuids != eligible_sfi_uuids:
        raise ValueError("Eligible SFI UUIDs must be unique and canonically ordered.")

    if pair_evaluation_bound == 0 or len(canonical_uuids) < 2:
        return ()

    endpoints: list[tuple[UUID, UUID]] = []
    seen: set[tuple[UUID, UUID]] = set()

    for distance in range(1, len(canonical_uuids)):
        for first_index, first_sfi_uuid in enumerate(canonical_uuids):
            second_sfi_uuid = canonical_uuids[
                (first_index + distance) % len(canonical_uuids)
            ]
            first_endpoint, second_endpoint = sorted(
                (first_sfi_uuid, second_sfi_uuid), key=str
            )
            pair = (first_endpoint, second_endpoint)

            if pair in seen:
                continue

            seen.add(pair)
            endpoints.append(pair)

            if len(endpoints) == pair_evaluation_bound:
                return tuple(endpoints)

    return tuple(endpoints)


def _candidate_bounds(
    *,
    eligible_sfi_uuids: tuple[UUID, ...],
    max_candidates_per_sfi: int,
    max_total_candidates: int,
) -> _LPCandidateBounds:
    """Calculate retention and evaluation bounds for one eligible population.

    The retention bound is limited by the run-wide budget, the number of available
    unordered pairs, and the maximum pair count permitted by the per-endpoint incidence
    budget. The evaluation bound permits one bounded proposal contribution from each
    fixed evidence family, capped by the complete unordered-pair population.

    Parameters
    ----------
    eligible_sfi_uuids
        Eligible endpoint UUIDs in canonical order.
    max_candidates_per_sfi
        Maximum number of retained candidates incident to one endpoint.
    max_total_candidates
        Maximum number of candidates retained for the complete run.

    Returns
    -------
    _LPCandidateBounds
        Eligible population, configured budgets, unordered-pair count, and derived
        retention and evaluation ceilings.
    """

    total_eligible_sfis = len(eligible_sfi_uuids)
    total_unordered_pairs = total_eligible_sfis * (total_eligible_sfis - 1) // 2
    candidate_pair_bound = min(
        max_total_candidates,
        total_unordered_pairs,
        total_eligible_sfis * max_candidates_per_sfi // 2,
    )
    return _LPCandidateBounds(
        candidate_pair_bound=candidate_pair_bound,
        candidate_pair_evaluation_bound=min(
            total_unordered_pairs,
            candidate_pair_bound * len(_EVIDENCE_RANKING_PRECEDENCE),
        ),
        eligible_sfi_uuids=eligible_sfi_uuids,
        max_candidates_per_sfi=max_candidates_per_sfi,
        max_total_candidates=max_total_candidates,
        total_unordered_pairs=total_unordered_pairs,
    )


def _candidate_from_pair_evidence(pair_evidence: Any) -> LPCandidatePair:
    """Convert one filtered evidence result into a candidate record.

    Pair identity, canonical endpoints, warnings, admissible decisions, and positive
    evidence are copied into the intrinsic candidate schema. Nested decision and
    evidence models are deep-copied so the candidate does not share mutable state with
    the extractor result.

    Parameters
    ----------
    pair_evidence
        Filtered evidence result for one admissible endpoint pair.

    Returns
    -------
    LPCandidatePair
        Independently owned candidate record for the evaluated pair.
    """

    admissibility = pair_evidence.admissibility
    return LPCandidatePair(
        admissible_decisions=[
            option.model_copy(deep=True)
            for option in admissibility.admissible_decisions
        ],
        evidence=[
            evidence.model_copy(deep=True) for evidence in pair_evidence.evidence
        ],
        first_sfi_uuid=admissibility.first_sfi_uuid,
        pair_id=admissibility.pair_id,
        second_sfi_uuid=admissibility.second_sfi_uuid,
        warnings=list(admissibility.warnings),
    )


def _candidate_ranking_key(candidate: LPCandidatePair) -> tuple[int | str, ...]:
    """Rank signal breadth before fixed signal presence and pair identity.

    Parameters
    ----------
    candidate
        One evidence-bearing, intrinsically admissible logical pair.

    Returns
    -------
    tuple[int | str, ...]
        Ascending sort key with broader evidence first, then the code-owned signal
        precedence, and finally the unique pair ID as a total tie-breaker.

    Raises
    ------
    ValueError
        If a candidate contains a signal outside the fixed built-in policy.
    """

    evidence_types = frozenset(item.evidence_type for item in candidate.evidence)
    unknown_types = evidence_types.difference(_EVIDENCE_RANKING_PRECEDENCE)

    if unknown_types:
        raise ValueError(
            f"LP candidate contains evidence outside the built-in policy: "
            f"{sorted(unknown_types)}."
        )

    return (
        -len(evidence_types),
        *(
            0 if evidence_type in evidence_types else 1
            for evidence_type in _EVIDENCE_RANKING_PRECEDENCE
        ),
        candidate.pair_id,
    )


def _candidate_row_audit(
    candidates: tuple[LPCandidatePair, ...],
) -> _LPCandidateRowAudit:
    """Derive population facts directly from candidate rows.

    The rows are serialized in artifact order, and their pair identifiers, endpoint
    incidence, evidence occurrences, warning occurrences, and number of warning-bearing
    rows are counted independently of the supplied summary.

    Parameters
    ----------
    candidates
        Candidate rows to serialize and audit.

    Returns
    -------
    _LPCandidateRowAudit
        Row-derived material and counts used for summary reconciliation.
    """

    return _LPCandidateRowAudit(
        candidate_payload=[
            candidate.model_dump(mode="json") for candidate in candidates
        ],
        candidate_warning_counts=Counter(
            warning for candidate in candidates for warning in candidate.warnings
        ),
        evidence_type_counts=Counter(
            evidence.evidence_type
            for candidate in candidates
            for evidence in candidate.evidence
        ),
        incidence=Counter(
            endpoint
            for candidate in candidates
            for endpoint in (candidate.first_sfi_uuid, candidate.second_sfi_uuid)
        ),
        pair_ids=tuple(candidate.pair_id for candidate in candidates),
        total_candidate_pairs_with_warnings=sum(
            bool(candidate.warnings) for candidate in candidates
        ),
    )


def _candidate_shortlist_union(
    *, bounds: _LPCandidateBounds, evaluation: _LPCandidateEvaluation
) -> tuple[tuple[LPCandidatePair, ...], int]:
    """Build a deterministic union of the per-endpoint shortlists.

    Shortlist entries are traversed in canonical endpoint order and deduplicated by
    logical pair identifier. Repeated identifiers must refer to identical candidate
    records. The unique union is returned in deterministic ranking order together with
    the original number of shortlist entries.

    Parameters
    ----------
    bounds
        Eligible endpoint population whose order controls shortlist traversal.
    evaluation
        Per-endpoint shortlists produced by bounded evidence evaluation.

    Returns
    -------
    tuple[tuple[LPCandidatePair, ...], int]
        Ranked unique candidate union and total shortlist-entry count before
        deduplication.

    Raises
    ------
    ValueError
        If different candidate records share the same logical pair identifier.
    """

    shortlist_entry_count = sum(
        len(shortlist) for shortlist in evaluation.shortlists.values()
    )
    candidate_union_by_pair_id: dict[str, LPCandidatePair] = {}

    for sfi_uuid in bounds.eligible_sfi_uuids:
        for candidate in evaluation.shortlists[sfi_uuid]:
            existing = candidate_union_by_pair_id.get(candidate.pair_id)

            if existing is not None and existing != candidate:
                raise ValueError(
                    f"Conflicting LP candidate records share pair ID {candidate.pair_id}."
                )

            candidate_union_by_pair_id[candidate.pair_id] = candidate

    return (
        tuple(sorted(candidate_union_by_pair_id.values(), key=_candidate_ranking_key)),
        shortlist_entry_count,
    )


def _candidate_summary(
    *,
    bounds: _LPCandidateBounds,
    budget_result: _LPCandidateBudgetResult,
    evaluation: _LPCandidateEvaluation,
    pair_evaluation_count: int,
    selection: LPSelectionReport,
) -> LPCandidateSummary:
    """Build the material summary for one retained candidate population.

    Candidate hashes, endpoint incidence, evidence counts, and warning counts are
    derived directly from the retained rows. Population bounds, input hashes,
    evaluation outcomes, shortlist deduplication, budget removals, and fixed
    limitations are copied from the corresponding completed build phases.

    Parameters
    ----------
    bounds
        Eligible population, configured budgets, and derived candidate ceilings.
    budget_result
        Candidate population and counts after deduplication and budget enforcement.
    evaluation
        Counts produced while filtering and evaluating nominated pairs.
    pair_evaluation_count
        Actual number of endpoint pairs submitted to the evidence extractor.
    selection
        Current framework identity, eligible population, and material input hashes.

    Returns
    -------
    LPCandidateSummary
        Validated summary describing the exact retained candidate population.
    """

    retained = budget_result.retained
    audit = _candidate_row_audit(retained)
    return LPCandidateSummary(
        candidate_pair_bound=bounds.candidate_pair_bound,
        candidate_pair_evaluation_bound=bounds.candidate_pair_evaluation_bound,
        candidate_pairs_content_hash=_content_hash(audit.candidate_payload),
        candidate_pairs_per_sfi={
            sfi_uuid: audit.incidence[sfi_uuid]
            for sfi_uuid in bounds.eligible_sfi_uuids
        },
        candidate_warning_counts=dict(sorted(audit.candidate_warning_counts.items())),
        config_content_hash=selection.config_content_hash,
        eligible_sfis_content_hash=selection.eligible_sfis_content_hash,
        evidence_type_counts=dict(sorted(audit.evidence_type_counts.items())),
        framework_uuid=selection.framework_uuid,
        limitations=list(_CANDIDATE_LIMITATIONS),
        max_candidates_per_sfi=bounds.max_candidates_per_sfi,
        max_total_candidates=bounds.max_total_candidates,
        total_admissible_pairs=evaluation.total_admissible_pairs,
        total_candidate_pairs=len(retained),
        total_candidate_pairs_dropped_by_per_sfi_budget=(
            evaluation.total_nominated_pairs - budget_result.per_sfi_survivor_count
        ),
        total_candidate_pairs_dropped_by_total_budget=(
            budget_result.per_sfi_survivor_count - len(retained)
        ),
        total_candidate_pairs_with_warnings=(audit.total_candidate_pairs_with_warnings),
        total_candidate_shortlist_entries=budget_result.shortlist_entry_count,
        total_candidate_union_pairs=budget_result.candidate_union_count,
        total_duplicate_shortlist_entries=(
            budget_result.shortlist_entry_count - budget_result.candidate_union_count
        ),
        total_eligible_sfis=len(bounds.eligible_sfi_uuids),
        total_nominated_pairs=evaluation.total_nominated_pairs,
        total_pair_evaluations=pair_evaluation_count,
        total_pairs_without_evidence=(
            evaluation.total_admissible_pairs - evaluation.total_nominated_pairs
        ),
        total_policy_disallowed_pairs=(
            pair_evaluation_count - evaluation.total_admissible_pairs
        ),
        total_unordered_pairs_considered=bounds.total_unordered_pairs,
        upstream_content_hash=selection.upstream_content_hash,
    )


def _canonical_json(value: Any) -> str:
    """Serialize candidate material deterministically for hashing and artifacts.

    Parameters
    ----------
    value
        JSON-compatible candidate or summary material.

    Returns
    -------
    str
        Stable Unicode JSON with sorted mapping keys and no non-finite numbers.
    """

    return json.dumps(
        allow_nan=False,
        ensure_ascii=False,
        obj=value,
        separators=(",", ":"),
        sort_keys=True,
    )


def _content_hash(value: Any) -> str:
    """Hash actual candidate material rather than a policy version marker.

    Parameters
    ----------
    value
        JSON-compatible material to identify.

    Returns
    -------
    str
        SHA-256 hexadecimal digest of canonical UTF-8 JSON.
    """

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _evaluate_candidate_pairs(
    *,
    bounds: _LPCandidateBounds,
    extractor: Any,
    pair_endpoints: tuple[tuple[UUID, UUID], ...],
) -> _LPCandidateEvaluation:
    """Evaluate bounded endpoint pairs and maintain per-endpoint shortlists.

    Each supplied pair is passed through the evidence extractor. Disallowed pairs are
    omitted, admissible pairs are counted, and admissible pairs without positive
    evidence are not nominated. Every evidence-bearing candidate enters the shortlist
    for both endpoints, where deterministic ranking retains only the configured number
    of candidates per endpoint.

    Parameters
    ----------
    bounds
        Eligible endpoint population and per-endpoint shortlist limit.
    extractor
        Evidence extractor used to filter and evaluate each endpoint pair.
    pair_endpoints
        Bounded endpoint pairs selected for evaluation.

    Returns
    -------
    _LPCandidateEvaluation
        Per-endpoint shortlists and counts of admissible and evidence-bearing pairs.
    """

    shortlists: dict[UUID, list[LPCandidatePair]] = {
        sfi_uuid: [] for sfi_uuid in bounds.eligible_sfi_uuids
    }
    total_admissible_pairs = 0
    total_nominated_pairs = 0

    for first_sfi_uuid, second_sfi_uuid in pair_endpoints:
        pair_evidence = extractor.extract_pair(
            first_sfi_uuid=first_sfi_uuid, second_sfi_uuid=second_sfi_uuid
        )

        if pair_evidence is None:
            continue

        total_admissible_pairs += 1

        if not pair_evidence.evidence:
            continue

        total_nominated_pairs += 1
        candidate = _candidate_from_pair_evidence(pair_evidence)

        for sfi_uuid in (candidate.first_sfi_uuid, candidate.second_sfi_uuid):
            shortlist = shortlists[sfi_uuid]
            shortlist.append(candidate)
            shortlist.sort(key=_candidate_ranking_key)

            if len(shortlist) > bounds.max_candidates_per_sfi:
                shortlist.pop()

    return _LPCandidateEvaluation(
        shortlists=shortlists,
        total_admissible_pairs=total_admissible_pairs,
        total_nominated_pairs=total_nominated_pairs,
    )


def _nominate_candidate_pair_endpoints(
    *, bounds: _LPCandidateBounds, extractor: Any
) -> tuple[tuple[UUID, UUID], ...]:
    """Select a bounded endpoint population for evidence evaluation.

    Production extractors must expose the fixed evidence families in their required
    ranking order before evidence-indexed nomination is used. Compatible injected test
    doubles use the deterministic bounded endpoint selector so tests retain the same
    evaluation ceiling without requiring the concrete production extractor.

    Parameters
    ----------
    bounds
        Eligible endpoint population and derived nomination ceilings.
    extractor
        Production evidence extractor or compatible injected test double.

    Returns
    -------
    tuple[tuple[UUID, UUID], ...]
        Deterministically ordered endpoint pairs bounded by the evaluation ceiling.

    Raises
    ------
    ValueError
        If a production extractor's evidence families do not match the fixed ranking
        policy.
    """

    if not isinstance(extractor, LPEvidenceExtractor):
        return _bounded_pair_endpoints(
            eligible_sfi_uuids=bounds.eligible_sfi_uuids,
            pair_evaluation_bound=bounds.candidate_pair_evaluation_bound,
        )

    if extractor.evidence_types != _EVIDENCE_RANKING_PRECEDENCE:
        raise ValueError("LP nomination and ranking evidence policies do not match.")

    return extractor.nominate_pair_endpoints(bounds.candidate_pair_bound)


def _reconcile_candidate_population(
    *,
    candidates: tuple[LPCandidatePair, ...],
    doc_key: str,
    kg_config: CreateKGConfig,
    pair_filter: LPCandidateFilter,
    selection: LPSelectionReport,
    summary: LPCandidateSummary,
) -> None:
    """Reconcile actual candidate rows with every retained-population summary field.

    Parameters
    ----------
    candidates
        Round-trip-validated candidate rows in artifact order.
    doc_key
        Current document identity used to independently verify logical pair IDs.
    kg_config
        Current validated configuration whose budgets must match the summary.
    pair_filter
        Independently rebuilt hard filter for current pair decisions and warnings.
    selection
        Independently rebuilt eligible-SFI population and material input hashes.
    summary
        Round-trip-validated summary proposed for those exact rows.

    Raises
    ------
    ValueError
        If identities, counts, incidence, evidence, warnings, hashes, or budgets do not
        describe the supplied candidate rows exactly.
    """

    budgets = kg_config.learning_progressions.candidate_policy.budgets
    bounds = _candidate_bounds(
        eligible_sfi_uuids=tuple(
            record.sfi.case_identifier_uuid for record in selection.eligible_sfis
        ),
        max_candidates_per_sfi=budgets.max_candidates_per_sfi,
        max_total_candidates=budgets.max_total_candidates,
    )
    audit = _candidate_row_audit(candidates)
    _validate_candidate_summary_material(
        bounds=bounds, selection=selection, summary=summary
    )
    _validate_candidate_rows(
        audit=audit,
        candidates=candidates,
        doc_key=doc_key,
        pair_filter=pair_filter,
    )
    _validate_candidate_row_summary_identity(
        audit=audit, bounds=bounds, candidates=candidates, summary=summary
    )
    _validate_candidate_row_summary_counts(audit=audit, summary=summary)


def _validate_candidate_row_summary_counts(
    *, audit: _LPCandidateRowAudit, summary: LPCandidateSummary
) -> None:
    """Reconcile row-derived incidence, evidence, and warning counts.

    Endpoint incidence is calculated for every endpoint represented by the summary.
    Evidence occurrences, warning occurrences, and the number of warning-bearing rows
    must exactly match their corresponding summary fields.

    Parameters
    ----------
    audit
        Counts derived independently from the candidate rows.
    summary
        Candidate summary whose count fields are being reconciled.

    Raises
    ------
    ValueError
        If endpoint incidence, evidence counts, warning counts, or the number of
        warning-bearing rows differs from the candidate population.
    """

    expected_incidence = {
        sfi_uuid: audit.incidence[sfi_uuid]
        for sfi_uuid in summary.candidate_pairs_per_sfi
    }

    if expected_incidence != summary.candidate_pairs_per_sfi:
        raise ValueError(
            "Candidate artifact endpoint incidence does not match candidate_pairs_per_sfi."
        )

    if dict(sorted(audit.evidence_type_counts.items())) != summary.evidence_type_counts:
        raise ValueError(
            "Candidate artifact evidence counts do not match evidence_type_counts."
        )

    if (
        dict(sorted(audit.candidate_warning_counts.items()))
        != summary.candidate_warning_counts
    ):
        raise ValueError(
            "Candidate artifact warning counts do not match candidate_warning_counts."
        )

    if (
        audit.total_candidate_pairs_with_warnings
        != summary.total_candidate_pairs_with_warnings
    ):
        raise ValueError(
            "Candidate artifact warning-bearing row count does not match its summary."
        )


def _validate_candidate_row_summary_identity(
    *,
    audit: _LPCandidateRowAudit,
    bounds: _LPCandidateBounds,
    candidates: tuple[LPCandidatePair, ...],
    summary: LPCandidateSummary,
) -> None:
    """Validate candidate row identity against the supplied summary.

    The row count must match the summary and remain within the retention bound. Rows
    must follow deterministic ranking order, hash to the recorded candidate content
    hash, and contain only endpoints from the current eligible population.

    Parameters
    ----------
    audit
        Serialized row material and identifiers derived from the candidates.
    bounds
        Current eligible population and candidate ceilings.
    candidates
        Candidate rows in artifact order.
    summary
        Candidate summary to reconcile with the rows.

    Raises
    ------
    ValueError
        If the count, bound, order, content hash, or endpoint containment check fails.
    """

    if len(candidates) != summary.total_candidate_pairs:
        raise ValueError(
            "Candidate artifact row count does not match total_candidate_pairs."
        )

    if len(candidates) > summary.candidate_pair_bound:
        raise ValueError("Candidate artifact row count exceeds candidate_pair_bound.")

    if tuple(sorted(candidates, key=_candidate_ranking_key)) != candidates:
        raise ValueError("Candidate artifact rows do not follow deterministic ranking.")

    if _content_hash(audit.candidate_payload) != summary.candidate_pairs_content_hash:
        raise ValueError(
            "Candidate artifact content does not match its recorded content hash."
        )

    eligible_sfi_uuids = set(bounds.eligible_sfi_uuids)

    if any(
        endpoint not in eligible_sfi_uuids
        for candidate in candidates
        for endpoint in (candidate.first_sfi_uuid, candidate.second_sfi_uuid)
    ):
        raise ValueError("Candidate artifact contains an ineligible SFI endpoint.")


def _validate_candidate_rows(
    *,
    audit: _LPCandidateRowAudit,
    candidates: tuple[LPCandidatePair, ...],
    doc_key: str,
    pair_filter: LPCandidateFilter,
) -> None:
    """Validate candidate identities, permissions, and warning context.

    Logical pair identifiers must be unique and reproducible from the current document
    key and endpoint UUIDs. Each pair is reevaluated through the current pair filter,
    and its recorded decisions and warnings must exactly match the resulting
    permissions and endpoint context.

    Parameters
    ----------
    audit
        Facts derived independently from the candidate rows.
    candidates
        Candidate rows to validate.
    doc_key
        Current document identity used to reconstruct logical pair identifiers.
    pair_filter
        Current pair filter used to recompute permissions and warnings.

    Raises
    ------
    ValueError
        If a pair identifier is duplicated or invalid, a pair is disallowed, or its
        decisions or warnings differ from the current filter result.
    """

    if len(audit.pair_ids) != len(set(audit.pair_ids)):
        raise ValueError("Candidate artifact contains duplicate logical pair IDs.")

    if any(
        candidate.pair_id
        != build_lp_pair_id(
            doc_key=doc_key,
            first_sfi_uuid=candidate.first_sfi_uuid,
            second_sfi_uuid=candidate.second_sfi_uuid,
        )
        for candidate in candidates
    ):
        raise ValueError("Candidate artifact contains an invalid logical pair ID.")

    for candidate in candidates:
        admissibility = pair_filter.filter_pair(
            first_sfi_uuid=candidate.first_sfi_uuid,
            second_sfi_uuid=candidate.second_sfi_uuid,
        )

        if admissibility is None:
            raise ValueError(
                "Candidate artifact contains a policy-disallowed endpoint pair."
            )

        if candidate.admissible_decisions != list(admissibility.admissible_decisions):
            raise ValueError(
                "Candidate artifact decisions do not match current pair permissions."
            )

        if candidate.warnings != list(admissibility.warnings):
            raise ValueError(
                "Candidate artifact warnings do not match current endpoint context."
            )


def _validate_candidate_summary_material(
    *,
    bounds: _LPCandidateBounds,
    selection: LPSelectionReport,
    summary: LPCandidateSummary,
) -> None:
    """Validate summary material against the current inputs and budgets.

    The summary must identify the current framework, configuration, eligible
    population, and upstream material. Its endpoint population, configured budgets,
    unordered-pair total, and derived retention and evaluation bounds must also match
    independently calculated values.

    Parameters
    ----------
    bounds
        Independently calculated population and candidate ceilings.
    selection
        Current eligible population and material input hashes.
    summary
        Candidate summary to validate.

    Raises
    ------
    ValueError
        If any identity, hash, endpoint population, budget, or derived bound does not
        match the current inputs.
    """

    if (
        summary.config_content_hash != selection.config_content_hash
        or summary.eligible_sfis_content_hash != selection.eligible_sfis_content_hash
        or summary.upstream_content_hash != selection.upstream_content_hash
    ):
        raise ValueError(
            "Candidate summary material hashes do not match the current inputs."
        )

    if summary.framework_uuid != selection.framework_uuid:
        raise ValueError("Candidate summary framework does not match current inputs.")

    if set(summary.candidate_pairs_per_sfi) != set(bounds.eligible_sfi_uuids):
        raise ValueError(
            "Candidate summary SFI population does not match current eligibility."
        )

    if (
        summary.candidate_pair_bound != bounds.candidate_pair_bound
        or summary.candidate_pair_evaluation_bound
        != bounds.candidate_pair_evaluation_bound
        or summary.max_candidates_per_sfi != bounds.max_candidates_per_sfi
        or summary.max_total_candidates != bounds.max_total_candidates
        or summary.total_eligible_sfis != len(bounds.eligible_sfi_uuids)
        or summary.total_unordered_pairs_considered != bounds.total_unordered_pairs
    ):
        raise ValueError(
            "Candidate summary bounds or population totals do not match current inputs."
        )


def build_lp_candidates(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
) -> LPCandidatePopulation:
    """Build the complete ranked candidate population without file or LLM access.

    A budget-derived deterministic endpoint neighborhood is assessed through the hard
    filter and named evidence extractor without constructing the complete pair matrix.
    Each endpoint retains only its highest-ranked configured number of evidence-bearing
    nominations, which bounds the in-memory union before it is deduplicated. The union
    is ranked again, constrained to the same final incident-pair budget at both
    endpoints, and only then truncated to the configured run total.

    Parameters
    ----------
    as_lc_bundle
        Final passed, error-free AS+LC bundle for the current run.
    doc_key
        Source document key matching authoritative upstream framework identity.
    kg_config
        Validated curriculum configuration containing the explicit candidate budgets.

    Returns
    -------
    LPCandidatePopulation
        Retained candidates in stable ranking order and a reconciled material summary.

    Raises
    ------
    ValueError
        If upstream identity, graph, coordinates, evidence, or summary reconciliation
        fails.
    """

    bundle = as_lc_bundle.model_copy(deep=True)
    selection = build_lp_selection(as_lc_bundle=bundle, kg_config=kg_config)
    extractor = build_lp_evidence_extractor(
        as_lc_bundle=bundle, doc_key=doc_key, kg_config=kg_config
    )
    budgets = kg_config.learning_progressions.candidate_policy.budgets
    bounds = _candidate_bounds(
        eligible_sfi_uuids=tuple(
            sorted(
                (record.sfi.case_identifier_uuid for record in selection.eligible_sfis),
                key=str,
            )
        ),
        max_candidates_per_sfi=budgets.max_candidates_per_sfi,
        max_total_candidates=budgets.max_total_candidates,
    )
    pair_endpoints = _nominate_candidate_pair_endpoints(
        bounds=bounds, extractor=extractor
    )
    evaluation = _evaluate_candidate_pairs(
        bounds=bounds, extractor=extractor, pair_endpoints=pair_endpoints
    )
    ranked_union, shortlist_entry_count = _candidate_shortlist_union(
        bounds=bounds, evaluation=evaluation
    )
    budget_result = _apply_candidate_budgets(
        bounds=bounds,
        ranked_union=ranked_union,
        shortlist_entry_count=shortlist_entry_count,
    )
    return LPCandidatePopulation(
        candidates=budget_result.retained,
        summary=_candidate_summary(
            bounds=bounds,
            budget_result=budget_result,
            evaluation=evaluation,
            pair_evaluation_count=len(pair_endpoints),
            selection=selection,
        ),
    )


def write_lp_candidate_artifacts(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
) -> LPCandidatePopulation:
    """Build, validate, and write the two deterministic candidate artifacts.

    Both complete payloads are canonicalized and round-trip validated before either
    destination is changed. Existing files are replaced from current material inputs;
    this function does not perform stale-artifact reuse or any LLM work.

    Parameters
    ----------
    as_lc_bundle
        Final validated AS+LC bundle supplying all candidate inputs.
    doc_key
        Source document key matching upstream framework identity.
    kg_config
        Validated curriculum configuration supplying policy and budgets.
    kg_dirs
        Run directory receiving candidate JSONL and summary JSON artifacts.

    Returns
    -------
    LPCandidatePopulation
        The exact round-trip-validated population written to disk.

    Raises
    ------
    OSError
        If the artifact directory or either output cannot be written.
    ValueError
        If candidate generation, serialization, or artifact reconciliation fails.
    """

    population = build_lp_candidates(
        as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=kg_config
    )
    candidate_payload = json.loads(
        _canonical_json(
            [candidate.model_dump(mode="json") for candidate in population.candidates]
        )
    )
    summary_payload = json.loads(
        _canonical_json(population.summary.model_dump(mode="json"))
    )
    candidates = tuple(
        LPCandidatePair.model_validate(candidate) for candidate in candidate_payload
    )
    summary = LPCandidateSummary.model_validate(summary_payload)
    pair_filter = build_lp_pair_filter(
        as_lc_bundle=as_lc_bundle, doc_key=doc_key, kg_config=kg_config
    )
    selection = build_lp_selection(as_lc_bundle=as_lc_bundle, kg_config=kg_config)
    _reconcile_candidate_population(
        candidates=candidates,
        doc_key=doc_key,
        kg_config=kg_config,
        pair_filter=pair_filter,
        selection=selection,
        summary=summary,
    )

    make_dir(kg_dirs.root)
    write_to_json(
        fp=kg_dirs.root / "lp_candidate_pairs.jsonl", json_info=candidate_payload
    )
    write_to_json(
        fp=kg_dirs.root / "lp_candidate_summary.json", json_info=summary_payload
    )
    return LPCandidatePopulation(candidates=candidates, summary=summary)
