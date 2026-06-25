"""This module contains functionalities for merging SFI groups with bounded LLM dedup
review.

This module consumes the SFI candidate registry, constructs small review sets from
duplicate buckets, warning groups, and source-provenance overlap, asks the dedup LLM to
classify each bounded set, validates every response, and emits merge groups for every
registry candidate.
"""

# Standard Library
import hashlib
import itertools
import json

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.llm import KGUsageTracker, review_sfi_dedup_set
from skg.kgs.schemas import (
    SFIDedupDecision,
    SFIDedupReviewCandidate,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIMergeDecision,
    SFIMergeGroup,
    SFIMergeReport,
    SFIMergeSummary,
    SFIRegistryArtifact,
    SFIRegistryCandidate,
)
from skg.kgs.utils import KGDirs
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, open_json_type, write_to_json

_MAX_DEDUP_REVIEW_SET_CANDIDATES = 12


@dataclass(frozen=True)
class _ReviewComponent:
    """Internal connected review component before LLM review."""

    candidate_ids: tuple[str, ...]
    needs_review_without_llm: bool
    review_reasons: tuple[str, ...]


def _build_initial_review_edges(
    sfi_candidate_registry: SFIRegistryArtifact,
) -> list[tuple[set[str], set[str]]]:
    """Build initial review edges from buckets, warnings, and source overlap.

    Parameters
    ----------
    sfi_candidate_registry
        SFI candidate registry artifact.

    Returns
    -------
    list[tuple[set[str], set[str]]]
        Candidate-ID sets paired with deterministic review reasons.
    """

    edges: list[tuple[set[str], set[str]]] = []

    for bucket in sfi_candidate_registry.duplicate_buckets:
        candidate_ids = set(bucket.registry_candidate_ids)

        if len(candidate_ids) < 2:
            continue

        edges.append(
            (
                candidate_ids,
                {
                    f"duplicate_bucket:{bucket.bucket_type}:{bucket.bucket_id}:"
                    f"{bucket.evidence_strength}"
                },
            )
        )

    for warning in sfi_candidate_registry.warnings:
        candidate_ids = set(warning.registry_candidate_ids)

        if len(candidate_ids) < 2:
            continue

        edges.append(
            (
                candidate_ids,
                {f"registry_warning:{warning.warning_type}:{warning.warning_id}"},
            )
        )

    provenance_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)

    for candidate in sfi_candidate_registry.candidates:
        for source_segment_id in candidate.source_segment_ids:
            for label, index in itertools.chain(
                (("same_source_table_row", idx) for idx in candidate.table_row_indexes),
                (
                    ("same_source_table_header", idx)
                    for idx in candidate.table_header_indexes
                ),
            ):
                provenance_groups[
                    (
                        label,
                        candidate.statement_type,
                        source_segment_id,
                        index,
                    )
                ].append(candidate.registry_candidate_id)

    for provenance_key, candidate_ids_raw in sorted(provenance_groups.items()):
        candidate_ids = set(candidate_ids_raw)

        if len(candidate_ids) < 2:
            continue

        edges.append(
            (
                candidate_ids,
                {"provenance_overlap:" + ":".join(str(v) for v in provenance_key)},
            )
        )

    return edges


def _build_merge_group(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    llm_decision: SFIDedupDecision | None,
    llm_review_set_id: str | None,
    merge_decision: SFIMergeDecision,
    merge_reason: str,
) -> SFIMergeGroup:
    """Build one SFI merge-group record from registry candidates.

    Parameters
    ----------
    candidates
        Registry candidates included in this merge group.
    llm_decision
        Original LLM decision for reviewed groups, if any.
    llm_review_set_id
        Review-set ID for LLM-reviewed groups, if any.
    merge_decision
        SFI merge decision carried forward to later stages.
    merge_reason
        Short deterministic or LLM-sourced reason.

    Returns
    -------
    SFIMergeGroup
        Merge group preserving source and candidate evidence.
    """

    sorted_candidates = sorted(
        candidates, key=lambda candidate: candidate.registry_candidate_id
    )
    registry_candidate_ids = [
        candidate.registry_candidate_id for candidate in sorted_candidates
    ]
    confidence_values = [candidate.confidence for candidate in sorted_candidates]
    normalized_statement_codes = _unique_nonempty(
        candidate.normalized_statement_code for candidate in sorted_candidates
    )
    normalized_statement_types = _unique_nonempty(
        candidate.normalized_statement_type for candidate in sorted_candidates
    )
    statement_codes = _unique_nonempty(
        candidate.statement_code for candidate in sorted_candidates
    )
    statement_types = _unique_nonempty(
        candidate.statement_type for candidate in sorted_candidates
    )
    digest = hashlib.sha256(
        "|".join(
            str(value)
            for value in [
                merge_decision,
                llm_review_set_id or "",
                *registry_candidate_ids,
            ]
        ).encode("utf-8")
    ).hexdigest()
    return SFIMergeGroup(
        candidate_descriptions=_unique_nonempty(
            candidate.description for candidate in sorted_candidates
        ),
        candidate_source_refs=[
            {
                "registry_candidate_id": candidate.registry_candidate_id,
                "source_segment_ids": candidate.source_segment_ids,
                "table_header_indexes": candidate.table_header_indexes,
                "table_row_indexes": candidate.table_row_indexes,
                "window_id": candidate.window_id,
                "window_index": candidate.window_index,
            }
            for candidate in sorted_candidates
        ],
        candidate_source_texts=_unique_nonempty(
            candidate.source_text for candidate in sorted_candidates
        ),
        confidence_max=max(confidence_values),
        confidence_min=min(confidence_values),
        llm_decision=llm_decision,
        llm_review_set_id=llm_review_set_id,
        merge_decision=merge_decision,
        merge_group_id=f"merge_group_{merge_decision}_{digest[:16]}",
        merge_reason=merge_reason,
        normalized_statement_code=(
            normalized_statement_codes[0]
            if len(normalized_statement_codes) == 1
            else None
        ),
        normalized_statement_codes=normalized_statement_codes,
        normalized_statement_type=(
            normalized_statement_types[0]
            if len(normalized_statement_types) == 1
            else None
        ),
        normalized_statement_types=normalized_statement_types,
        registry_candidate_ids=registry_candidate_ids,
        source_segment_ids=_unique_nonempty(
            source_segment_id
            for candidate in sorted_candidates
            for source_segment_id in candidate.source_segment_ids
        ),
        source_window_ids=_unique_nonempty(
            candidate.window_id for candidate in sorted_candidates
        ),
        source_window_indexes=sorted(
            {candidate.window_index for candidate in sorted_candidates}
        ),
        statement_code=statement_codes[0] if len(statement_codes) == 1 else None,
        statement_codes=statement_codes,
        statement_type=statement_types[0] if len(statement_types) == 1 else None,
        statement_types=statement_types,
    )


def _build_merge_groups_from_responses(
    *,
    review_responses: Sequence[SFIDedupReviewResponse],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
    unresolved_components: Sequence[_ReviewComponent],
) -> list[SFIMergeGroup]:
    """Convert reviewed and unresolved components into merge groups.

    Parameters
    ----------
    review_responses
        Validated LLM dedup review responses.
    sfi_candidates_by_id
        Lookup of registry candidates by temporary registry candidate ID.
    unresolved_components
        Bounded-review components marked as needs_review without an LLM call.

    Returns
    -------
    list[SFIMergeGroup]
        Merge groups created from reviewed and unresolved candidates.
    """

    merge_groups: list[SFIMergeGroup] = []

    for review_response in review_responses:
        for decision_group in review_response.decision_groups:
            group_candidates = [
                sfi_candidates_by_id[candidate_id]
                for candidate_id in decision_group.candidate_ids
            ]

            if decision_group.decision == "merge":
                merge_groups.append(
                    _build_merge_group(
                        candidates=group_candidates,
                        llm_decision=decision_group.decision,
                        llm_review_set_id=review_response.review_set_id,
                        merge_decision="merged",
                        merge_reason=decision_group.reason,
                    )
                )
                continue

            if decision_group.decision == "keep_separate":
                for group_candidate in group_candidates:
                    merge_groups.append(
                        _build_merge_group(
                            candidates=[group_candidate],
                            llm_decision=decision_group.decision,
                            llm_review_set_id=review_response.review_set_id,
                            merge_decision="singleton",
                            merge_reason=decision_group.reason,
                        )
                    )
                continue

            merge_groups.append(
                _build_merge_group(
                    candidates=group_candidates,
                    llm_decision=decision_group.decision,
                    llm_review_set_id=review_response.review_set_id,
                    merge_decision=decision_group.decision,
                    merge_reason=decision_group.reason,
                )
            )

    for component in unresolved_components:
        merge_groups.append(
            _build_merge_group(
                candidates=[
                    sfi_candidates_by_id[candidate_id]
                    for candidate_id in component.candidate_ids
                ],
                llm_decision=None,
                llm_review_set_id=None,
                merge_decision="needs_review",
                merge_reason=(
                    "Review component was too large or unsafe for bounded v0 LLM "
                    "dedup review: " + "; ".join(component.review_reasons[:5])
                ),
            )
        )

    return merge_groups


def _build_merge_report(
    *,
    merge_groups: Sequence[SFIMergeGroup],
    review_requests: Sequence[SFIDedupReviewRequest],
    review_responses: Sequence[SFIDedupReviewResponse],
    sfi_candidate_registry: SFIRegistryArtifact,
    unreviewed_singleton_count: int,
) -> SFIMergeReport:
    """Build the aggregate SFI merge report.

    Parameters
    ----------
    merge_groups
        Complete merge groups for all registry candidates.
    review_requests
        LLM review requests.
    review_responses
        Validated LLM review responses.
    sfi_candidate_registry
        SFI candidate registry artifact.
    unreviewed_singleton_count
        Count of singleton groups created without LLM review.

    Returns
    -------
    SFIMergeReport
        Complete merge/dedup report.
    """

    conflict_groups = [
        group for group in merge_groups if group.merge_decision == "conflict"
    ]
    needs_review_groups = [
        group for group in merge_groups if group.merge_decision == "needs_review"
    ]
    reviewed_candidate_ids = {
        candidate_id
        for response in review_responses
        for group in response.decision_groups
        for candidate_id in group.candidate_ids
    }
    summary = SFIMergeSummary(
        candidate_count=len(sfi_candidate_registry.candidates),
        conflict_group_count=len(conflict_groups),
        dedup_review_request_count=len(review_requests),
        dedup_review_response_count=len(review_responses),
        merge_group_count=len(merge_groups),
        merged_group_count=sum(
            1 for group in merge_groups if group.merge_decision == "merged"
        ),
        needs_review_group_count=len(needs_review_groups),
        reviewed_candidate_count=len(reviewed_candidate_ids),
        singleton_group_count=sum(
            1 for group in merge_groups if group.merge_decision == "singleton"
        ),
        unreviewed_singleton_count=unreviewed_singleton_count,
    )
    return SFIMergeReport(
        conflict_groups=conflict_groups,
        merge_groups=list(merge_groups),
        needs_review_groups=needs_review_groups,
        review_requests=list(review_requests),
        review_responses=list(review_responses),
        summary=summary,
    )


def _build_review_requests(
    *,
    components: Sequence[_ReviewComponent],
    kg_config: CreateKGConfig,
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> tuple[list[SFIDedupReviewRequest], list[_ReviewComponent]]:
    """Build LLM review requests and carry unresolved components forward.

    Parameters
    ----------
    components
        Bounded connected review components.
    kg_config
        Runtime KG configuration carrying dedup instructions.
    sfi_candidates_by_id
        Lookup of registry candidates by temporary registry candidate ID.

    Returns
    -------
    tuple[list[SFIDedupReviewRequest], list[_ReviewComponent]]
        Review requests to send to the LLM and unresolved components to mark
        needs_review without an LLM call.
    """

    review_requests: list[SFIDedupReviewRequest] = []
    unresolved_components: list[_ReviewComponent] = []

    for component in components:
        if component.needs_review_without_llm:
            unresolved_components.append(component)
            continue

        # Resolve candidates for this component.
        component_candidates = [
            sfi_candidates_by_id[candidate_id]
            for candidate_id in component.candidate_ids
        ]

        # Generate deterministic review set ID.
        candidate_ids = sorted(
            candidate.registry_candidate_id for candidate in component_candidates
        )
        review_set_id = f"dedupe_review_{hashlib.sha256(
            "|".join(str(value) for value in candidate_ids).encode("utf-8")
        ).hexdigest()[:16]}"

        review_requests.append(
            SFIDedupReviewRequest(
                bilingual_pair_policy=kg_config.academic_standards.bilingual_pair_policy,
                candidates=[
                    SFIDedupReviewCandidate(
                        code_bucket_key=candidate.code_bucket_key,
                        description=candidate.description,
                        language=candidate.language,
                        normalized_description=candidate.normalized_description,
                        normalized_source_text=candidate.normalized_source_text,
                        normalized_statement_code=candidate.normalized_statement_code,
                        normalized_statement_type=candidate.normalized_statement_type,
                        registry_candidate_id=candidate.registry_candidate_id,
                        source_segment_ids=candidate.source_segment_ids,
                        source_text=candidate.source_text,
                        source_text_bucket_key=candidate.source_text_bucket_key,
                        statement_code=candidate.statement_code,
                        statement_type=candidate.statement_type,
                        table_header_indexes=candidate.table_header_indexes,
                        table_row_indexes=candidate.table_row_indexes,
                        text_bucket_key=candidate.text_bucket_key,
                        window_id=candidate.window_id,
                        window_index=candidate.window_index,
                    )
                    for candidate in component_candidates
                ],
                review_reasons=sorted(set(component.review_reasons)),
                review_set_id=review_set_id,
                sfi_deduplication_instructions=(
                    kg_config.academic_standards.sfi_deduplication_instructions
                ),
            )
        )

    return review_requests, unresolved_components


def _build_singleton_merge_groups(
    *,
    covered_candidate_ids: set[str],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> list[SFIMergeGroup]:
    """Build singleton merge groups for unreviewed registry candidates.

    Parameters
    ----------
    covered_candidate_ids
        Candidate IDs already represented in reviewed or unresolved groups.
    sfi_candidates_by_id
        Lookup of registry candidates by temporary registry candidate ID.

    Returns
    -------
    list[SFIMergeGroup]
        Singleton merge groups for candidates outside any review set.
    """

    singleton_groups: list[SFIMergeGroup] = []

    for candidate_id in sorted(set(sfi_candidates_by_id) - covered_candidate_ids):
        singleton_groups.append(
            _build_merge_group(
                candidates=[sfi_candidates_by_id[candidate_id]],
                llm_decision=None,
                llm_review_set_id=None,
                merge_decision="singleton",
                merge_reason="Candidate was not included in any SFI merge review set.",
            )
        )

    return singleton_groups


def _load_existing_merge_report(
    *, merge_report_fp: Path, sfi_candidate_registry: SFIRegistryArtifact
) -> SFIMergeReport:
    """Load and validate an existing SFI merge report artifact.

    Parameters
    ----------
    sfi_candidate_registry
        Current SFI candidate registry used to validate existing merge-group coverage.
    merge_report_fp
        Path to an existing `sfi_merge_report.json` artifact.

    Returns
    -------
    SFIMergeReport
        Validated existing merge report.
    """

    merge_report = SFIMergeReport.model_validate(open_json_type(merge_report_fp))
    _validate_merge_group_coverage(
        merge_groups=merge_report.merge_groups,
        sfi_candidate_registry=sfi_candidate_registry,
    )
    return merge_report


def _merge_edges_to_components(
    edges: Sequence[tuple[set[str], set[str]]],
) -> list[_ReviewComponent]:
    """Merge overlapping review edges into connected components.

    Parameters
    ----------
    edges
        Initial candidate review edges and reasons.

    Returns
    -------
    list[_ReviewComponent]
        Connected review components before bounding.
    """

    parent: dict[str, str] = {}
    reasons_by_candidate: dict[str, set[str]] = defaultdict(set)

    def _find(candidate_id: str) -> str:
        """Find the union-find root for one candidate ID.

        Parameters
        ----------
        candidate_id
            Candidate ID.

        Returns
        -------
        str
            Root candidate ID for the connected component.
        """

        parent.setdefault(candidate_id, candidate_id)

        if parent[candidate_id] != candidate_id:
            parent[candidate_id] = _find(parent[candidate_id])

        return parent[candidate_id]

    def _union(*, first_candidate_id: str, second_candidate_id: str) -> None:
        """Union two candidate IDs in the connected-component index.

        Parameters
        ----------
        first_candidate_id
            First candidate ID.
        second_candidate_id
            Second candidate ID.
        """

        first_root = _find(first_candidate_id)
        second_root = _find(second_candidate_id)

        if first_root != second_root:
            parent[second_root] = first_root

    for candidate_ids, review_reasons in edges:
        candidate_ids_sorted = sorted(candidate_ids)

        if len(candidate_ids_sorted) < 2:
            continue

        first_candidate_id = candidate_ids_sorted[0]

        for candidate_id in candidate_ids_sorted[1:]:
            _union(
                first_candidate_id=first_candidate_id, second_candidate_id=candidate_id
            )

        for candidate_id in candidate_ids_sorted:
            reasons_by_candidate[candidate_id].update(review_reasons)

    component_ids_by_root: dict[str, set[str]] = defaultdict(set)
    component_reasons_by_root: dict[str, set[str]] = defaultdict(set)

    for candidate_id in sorted(parent):
        root = _find(candidate_id)
        component_ids_by_root[root].add(candidate_id)
        component_reasons_by_root[root].update(reasons_by_candidate[candidate_id])

    return [
        _ReviewComponent(
            candidate_ids=tuple(sorted(candidate_ids)),
            needs_review_without_llm=False,
            review_reasons=tuple(sorted(component_reasons_by_root[root])),
        )
        for root, candidate_ids in sorted(component_ids_by_root.items())
        if len(candidate_ids) >= 2
    ]


def _prepare_output_files(output_fps: Sequence[Path]) -> None:
    """Remove stale output artifacts and create empty JSONL artifacts.

    Parameters
    ----------
    output_fps
        Output file paths to reset.
    """

    for output_fp in output_fps:
        make_dir(output_fp.parent)

        if output_fp.exists():
            output_fp.unlink()

        if output_fp.suffix == ".jsonl":
            output_fp.write_text("", encoding="utf-8")


def _run_dedup_reviews(
    *,
    review_requests: Sequence[SFIDedupReviewRequest],
    review_requests_fp: Path,
    review_responses_fp: Path,
    usage_tracker: KGUsageTracker,
) -> list[SFIDedupReviewResponse]:
    """Run LLM dedup reviews and persist requests/responses incrementally.

    Parameters
    ----------
    review_requests
        Review requests to send to the dedup LLM.
    review_requests_fp
        JSONL path for persisted review requests.
    review_responses_fp
        JSONL path for persisted review responses.
    usage_tracker
        Usage tracker to accumulate LLM token counts.

    Returns
    -------
    list[SFIDedupReviewResponse]
        Validated LLM dedup review responses in request order.
    """

    review_responses: list[SFIDedupReviewResponse] = []

    if review_requests:
        make_dir(review_requests_fp.parent)
        make_dir(review_responses_fp.parent)

    for current_request_number, review_request in enumerate(review_requests, start=1):
        logger.info(
            f"Running SFI dedup review {current_request_number}/"
            f"{len(review_requests)}: review_set_id={review_request.review_set_id}; "
            f"SFI candidates={len(review_request.candidates)}."
        )

        with review_requests_fp.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(review_request.model_dump(mode="json"), ensure_ascii=False)
                + "\n"
            )

        review_response = review_sfi_dedup_set(
            review_request=review_request, usage_tracker=usage_tracker
        )

        with review_responses_fp.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(review_response.model_dump(mode="json"), ensure_ascii=False)
                + "\n"
            )

        review_responses.append(review_response)

    return review_responses


def _split_and_bound_components(
    *,
    components: Sequence[_ReviewComponent],
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> list[_ReviewComponent]:
    """Split connected components into bounded review components.

    Parameters
    ----------
    components
        Connected components built from review edges.
    sfi_candidates_by_id
        Lookup of registry candidates by temporary registry candidate ID.

    Returns
    -------
    list[_ReviewComponent]
        Bounded review components. Oversized unsafe components are marked for
        needs_review without an LLM call.
    """

    bounded_components: list[_ReviewComponent] = []

    for component in components:
        if len(component.candidate_ids) <= _MAX_DEDUP_REVIEW_SET_CANDIDATES:
            bounded_components.append(component)
            continue

        split_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)

        for candidate_id in component.candidate_ids:
            candidate = sfi_candidates_by_id[candidate_id]
            split_key = (
                candidate.statement_type,
                candidate.normalized_statement_code or candidate.code_bucket_key or "",
                tuple(candidate.source_segment_ids),
                candidate.window_index // 3,
            )
            split_groups[split_key].append(candidate_id)

        added_split_group = False

        for split_key, split_candidate_ids in sorted(split_groups.items()):
            split_candidate_ids_sorted = sorted(split_candidate_ids)

            if len(split_candidate_ids_sorted) < 2:
                continue

            for start_index in range(
                0, len(split_candidate_ids_sorted), _MAX_DEDUP_REVIEW_SET_CANDIDATES
            ):
                chunk_candidate_ids = split_candidate_ids_sorted[
                    start_index : start_index + _MAX_DEDUP_REVIEW_SET_CANDIDATES
                ]

                if len(chunk_candidate_ids) < 2:
                    continue

                bounded_components.append(
                    _ReviewComponent(
                        candidate_ids=tuple(chunk_candidate_ids),
                        needs_review_without_llm=False,
                        review_reasons=tuple(
                            sorted(
                                set(component.review_reasons)
                                | {
                                    "oversized_component_split_by_safe_source_context:"
                                    + repr(split_key)
                                }
                            )
                        ),
                    )
                )
                added_split_group = True

        if not added_split_group:
            bounded_components.append(
                _ReviewComponent(
                    candidate_ids=component.candidate_ids,
                    needs_review_without_llm=True,
                    review_reasons=tuple(
                        sorted(
                            set(component.review_reasons)
                            | {"oversized_component_unbounded_needs_review"}
                        )
                    ),
                )
            )

    return bounded_components


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
    """Return unique non-empty string values preserving order.

    Parameters
    ----------
    values
        Raw values to normalize as strings.

    Returns
    -------
    list[str]
        Unique non-empty values.
    """

    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        if value is None:
            continue

        value_clean = str(value).strip()

        if not value_clean or value_clean in seen:
            continue

        output.append(value_clean)
        seen.add(value_clean)

    return output


def _validate_merge_group_coverage(
    *,
    merge_groups: Sequence[SFIMergeGroup],
    sfi_candidate_registry: SFIRegistryArtifact,
) -> None:
    """Validate that merge groups cover each registry candidate exactly once.

    Parameters
    ----------
    sfi_candidate_registry
        Source candidate registry artifact.
    merge_groups
        SFI merge groups.

    Raises
    ------
    ValueError
        If candidates are omitted or assigned to more than one merge group.
    """

    expected_candidate_ids = {
        candidate.registry_candidate_id
        for candidate in sfi_candidate_registry.candidates
    }
    assigned_candidate_ids = [
        candidate_id
        for merge_group in merge_groups
        for candidate_id in merge_group.registry_candidate_ids
    ]
    assigned_candidate_id_set = set(assigned_candidate_ids)
    duplicate_candidate_ids = sorted(
        {
            candidate_id
            for candidate_id in assigned_candidate_ids
            if assigned_candidate_ids.count(candidate_id) > 1
        }
    )
    omitted_candidate_ids = sorted(expected_candidate_ids - assigned_candidate_id_set)
    unknown_candidate_ids = sorted(assigned_candidate_id_set - expected_candidate_ids)

    if duplicate_candidate_ids:
        raise ValueError(
            f"SFI merge groups assigned candidates more than once: "
            f"{duplicate_candidate_ids}"
        )

    if omitted_candidate_ids:
        raise ValueError(
            f"SFI merge groups omitted registry candidates: {omitted_candidate_ids}"
        )

    if unknown_candidate_ids:
        raise ValueError(
            f"SFI merge groups contain unknown registry candidates: "
            f"{unknown_candidate_ids}"
        )


def _write_merge_artifacts(
    *,
    conflicts_fp: Path,
    merge_groups_fp: Path,
    merge_report: SFIMergeReport,
    merge_report_fp: Path,
    needs_review_fp: Path,
) -> None:
    """Write SFI merge report artifacts.

    Parameters
    ----------
    conflicts_fp
        JSON path for conflict groups.
    merge_groups_fp
        JSON path for all merge groups.
    merge_report
        Complete Step 7 merge report.
    merge_report_fp
        JSON path for the full report.
    needs_review_fp
        JSON path for needs-review groups.
    """

    write_to_json(fp=merge_groups_fp, json_info=merge_report.merge_groups)
    write_to_json(fp=conflicts_fp, json_info=merge_report.conflict_groups)
    write_to_json(fp=needs_review_fp, json_info=merge_report.needs_review_groups)
    write_to_json(fp=merge_report_fp, json_info=merge_report)


def merge_sfi_candidates(
    *,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool,
    sfi_candidate_registry: SFIRegistryArtifact,
    usage_tracker: KGUsageTracker,
) -> SFIMergeReport:
    """Merge SFI registry candidates into merge groups.

    Parameters
    ----------
    kg_config
        Runtime KG creation config with dedup instructions.
    kg_dirs
        Runtime KG directory structure for artifacts.
    overwrite
        If false and a merge report already exists, load and return it. Otherwise
        reset and regenerate all Step 7 artifacts.
    sfi_candidate_registry
        Global SFI candidate registry artifact.
    usage_tracker
        Usage tracker to accumulate LLM token counts.

    Returns
    -------
    SFIMergeReport
        Complete Step 7 merge/dedup report.
    """

    conflicts_fp = kg_dirs.root / "sfi_merge_conflicts.json"
    merge_groups_fp = kg_dirs.root / "sfi_merge_groups.json"
    merge_report_fp = kg_dirs.root / "sfi_merge_report.json"
    needs_review_fp = kg_dirs.root / "sfi_merge_needs_review.json"
    review_requests_fp = kg_dirs.root / "sfi_dedup_review_requests.jsonl"
    review_responses_fp = kg_dirs.root / "sfi_dedup_review_responses.jsonl"

    if not overwrite and merge_report_fp.exists():
        logger.info(
            f"Loading existing SFI merge report because overwrite=False: "
            f"{merge_report_fp}"
        )

        return _load_existing_merge_report(
            merge_report_fp=merge_report_fp,
            sfi_candidate_registry=sfi_candidate_registry,
        )

    _prepare_output_files(
        [
            conflicts_fp,
            merge_groups_fp,
            merge_report_fp,
            needs_review_fp,
            review_requests_fp,
            review_responses_fp,
        ]
    )

    sfi_candidates_by_id = {
        candidate.registry_candidate_id: candidate
        for candidate in sfi_candidate_registry.candidates
    }
    initial_edges = _build_initial_review_edges(sfi_candidate_registry)
    connected_components = _merge_edges_to_components(initial_edges)
    bounded_components = _split_and_bound_components(
        components=connected_components, sfi_candidates_by_id=sfi_candidates_by_id
    )
    review_requests, unresolved_components = _build_review_requests(
        components=bounded_components,
        kg_config=kg_config,
        sfi_candidates_by_id=sfi_candidates_by_id,
    )
    review_responses = _run_dedup_reviews(
        review_requests=review_requests,
        review_requests_fp=review_requests_fp,
        review_responses_fp=review_responses_fp,
        usage_tracker=usage_tracker,
    )
    merge_groups = _build_merge_groups_from_responses(
        review_responses=review_responses,
        sfi_candidates_by_id=sfi_candidates_by_id,
        unresolved_components=unresolved_components,
    )
    covered_candidate_ids = {
        candidate_id
        for merge_group in merge_groups
        for candidate_id in merge_group.registry_candidate_ids
    }
    singleton_groups = _build_singleton_merge_groups(
        covered_candidate_ids=covered_candidate_ids,
        sfi_candidates_by_id=sfi_candidates_by_id,
    )
    merge_groups.extend(singleton_groups)
    merge_groups.sort(key=lambda group: (group.merge_decision, group.merge_group_id))

    _validate_merge_group_coverage(
        merge_groups=merge_groups, sfi_candidate_registry=sfi_candidate_registry
    )
    merge_report = _build_merge_report(
        merge_groups=merge_groups,
        review_requests=review_requests,
        review_responses=review_responses,
        sfi_candidate_registry=sfi_candidate_registry,
        unreviewed_singleton_count=len(singleton_groups),
    )
    _write_merge_artifacts(
        conflicts_fp=conflicts_fp,
        merge_groups_fp=merge_groups_fp,
        merge_report=merge_report,
        merge_report_fp=merge_report_fp,
        needs_review_fp=needs_review_fp,
    )

    logger.success(
        f"Finished SFI merge/dedup: "
        f"SFI candidates={len(sfi_candidate_registry.candidates)}; "
        f"review_requests={len(review_requests)}; "
        f"merge_groups={len(merge_groups)}; "
        f"report={merge_report_fp}."
    )

    return merge_report
