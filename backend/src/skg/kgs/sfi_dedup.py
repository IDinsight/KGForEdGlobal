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
    """Build initial review edges from buckets, warnings, and source overlap. This
    function answers "which candidates have enough evidence to be reviewed together?".
    It does not answer "which candidates are duplicates?".

    Examples
    --------

    1. A code duplicate bucket creates one review edge containing every candidate in
    that bucket. For example, if the registry has a duplicate bucket for
    `content standard|b4.1.1.3` with two candidates, this function emits an edge
    like:

    (
        {"w0143:sfi_11:39af9680", "w0144:sfi_1:ddee825d"},
        {"duplicate_bucket:code:bucket_code_abcd1234:strong_signal",},
    )

    This edge does not mean the candidates should automatically merge. It only means
    they should be reviewed together because the registry found a strong same-code
    signal.

    2. A repeated-text warning creates another review edge. For example, if a no-code
    curriculum repeats the same normalized objective text across windows, the registry
    warning can produce an edge like:

    (
        {"w0052:sfi_7:aaaa1111", "w0054:sfi_3:bbbb2222", "w0056:sfi_2:cccc3333"},
        {"registry_warning:same_text_repeated_across_windows:warning_0027",},
    )

    This lets the later component-building step place all candidates from the warning
    into the same bounded dedup review set, unless the component must be split for size
    or safety.

    3. Source-provenance overlap creates review edges even when there is no duplicate
    bucket. For example, if two candidates with the same `statement_type` were
    extracted from the same table row in the same source segment, this function groups
    them under a provenance reason like:

    (
        {"w0012:sfi_4:c1736bd1", "w0012:sfi_8:a4fde5d4"},
        {
            "provenance_overlap:same_source_table_row:Compétence de base:"
            "6aaf44b7-3925-52d2-99bb-6feb16798606:1",
        },
    )

    This is useful for catching duplicate extractions from repeated rows, table
    overlap, or layout artifacts. It is only a retrieval signal: same row or same
    header does not by itself prove that candidates are duplicates.

    4. Different edge sources can overlap. For example, candidate `A` and candidate `B`
    might share a duplicate bucket, while candidate `B` and candidate `C` share a
    source-row overlap. This function returns two separate edges, and
    `_merge_edges_to_components()` later combines them into one connected component
    `{A, B, C}` with both review reasons.

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

    This function is a small constructor that turns one or more SFIRegistryCandidates
    into one SFIMergeGroup.

    It is used in three situations:

    1. The LLM says several candidates should be merged.
    2. The LLM says candidates should stay separate, so each candidate gets its own
        singleton group.
    3. The system creates deterministic singleton/conflict/needs-review groups without
        final SFI IDs.

    The function does not decide whether candidates should merge. Its caller already
    decided that through an LLM decision or deterministic fallback. This function just
    packages the candidates and evidence into a consistent merge-group record.

    Examples
    --------

    1. A merged group with two duplicate candidates might be built from two registry
    candidates that share the same statement type and normalized statement code:

    _build_merge_group(
        candidates=[candidate_a, candidate_b],
        llm_decision="merge",
        llm_review_set_id="dedupe_review_abc123",
        merge_decision="merged",
        merge_reason="Same statement type, same official code, and compatible text.",
    )

    The resulting `SFIMergeGroup` has one stable group ID, both registry candidate IDs,
    the original candidate descriptions and source texts, source references for each
    candidate, and the LLM decision metadata. If both candidates share exactly one
    normalized statement code, the group also stores that value in the singular
    `normalized_statement_code` field.

    2. A keep-separate decision is represented by calling this function once per
    candidate. For example, if the LLM reviewed `candidate_a` and `candidate_b`
    together but decided they are distinct, the caller creates two singleton groups:

    _build_merge_group(
        candidates=[candidate_a],
        llm_decision="keep_separate",
        llm_review_set_id="dedupe_review_abc123",
        merge_decision="singleton",
        merge_reason="Same text, but different visible curriculum contexts.",
    )
    _build_merge_group(
        candidates=[candidate_b],
        llm_decision="keep_separate",
        llm_review_set_id="dedupe_review_abc123",
        merge_decision="singleton",
        merge_reason="Same text, but different visible curriculum contexts.",
    )

    3. A deterministic singleton outside all review sets is also built with one
    candidate, but has no LLM decision metadata:

    _build_merge_group(
        candidates=[candidate_c],
        llm_decision=None,
        llm_review_set_id=None,
        merge_decision="singleton",
        merge_reason="Candidate was not included in any SFI merge review set.",
    )

    4. A conflict or needs-review group can contain multiple candidates. In those
    cases, if the candidates have different statement codes or statement types, the
    singular fields such as `statement_code` or `statement_type` are set to `None`,
    while the plural evidence fields retain all observed values.

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

    At this point:
        - `_build_review_requests()` has sent safe bounded components to the LLM.
        - `_run_dedup_reviews()` has returned validated SFIDedupReviewResponse objects.
        - Some components may have skipped the LLM because
            1needs_review_without_llm=True1.

    This function combines both paths:
        1. LLM-reviewed candidates -> use each LLM decision_group.
        2. Unresolved components -> emit deterministic needs_review merge groups
            without an LLM decision.

    The key behavior is based on the LLM decision:

    LLM decision	Output merge group behavior

    merge	        One multi-candidate group with merge_decision="merged"
    keep_separate	One singleton group per candidate with merge_decision="singleton"
    conflict	    One group with merge_decision="conflict"
    needs_review	One group with merge_decision="needs_review"

    For unresolved components that were not sent to the LLM, it emits one
    needs_review group with llm_decision=None and llm_review_set_id=None.

    This function only converts decisions into merge-group records. It does not create
    singleton groups for candidates outside all review components; that is handled
    later by `_build_singleton_merge_groups()`.

    Examples
    --------

    1. An LLM `merge` decision becomes one merged group containing all candidates in
    that decision group. For example, if a review response contains:

    SFIDedupDecisionGroup(
        candidate_ids=("candidate_a", "candidate_b"),
        decision="merge",
        reason="Same statement type, same official code, and compatible text.",
    )

    this function emits one `SFIMergeGroup` like:

    SFIMergeGroup(
        registry_candidate_ids=("candidate_a", "candidate_b"),
        llm_decision="merge",
        llm_review_set_id="dedupe_review_abc123",
        merge_decision="merged",
        merge_reason="Same statement type, same official code, and compatible text.",
        ...
    )

    2. An LLM `keep_separate` decision becomes one singleton merge group per candidate.
    For example, if the LLM returns:

    SFIDedupDecisionGroup(
        candidate_ids=("candidate_a", "candidate_b"),
        decision="keep_separate",
        reason="Same text, but candidates belong to different source contexts.",
    )

    this function emits two singleton groups, each carrying the same LLM reason:

    SFIMergeGroup(
        registry_candidate_ids=("candidate_a",),
        llm_decision="keep_separate",
        llm_review_set_id="dedupe_review_abc123",
        merge_decision="singleton",
        merge_reason="Same text, but candidates belong to different source contexts.",
        ...
    )
    SFIMergeGroup(
        registry_candidate_ids=("candidate_b",),
        llm_decision="keep_separate",
        llm_review_set_id="dedupe_review_abc123",
        merge_decision="singleton",
        merge_reason="Same text, but candidates belong to different source contexts.",
        ...
    )

    3. An LLM `conflict` decision becomes one conflict group. For example, if the LLM
    sees the same official code attached to materially different descriptions:

    SFIDedupDecisionGroup(
        candidate_ids=("candidate_a", "candidate_b"),
        decision="conflict",
        reason="Same code appears with incompatible descriptions.",
    )

    this function emits one group with `merge_decision="conflict"`. The group is
    preserved for later inspection rather than being merged or split automatically.

    4. An LLM `needs_review` decision becomes one needs-review group. For example, if
    the bounded payload does not contain enough visible source context to decide safely:

    SFIDedupDecisionGroup(
        candidate_ids=("candidate_a", "candidate_b", "candidate_c"),
        decision="needs_review",
        reason="The candidates may be related, but the bounded payload is not
        sufficient to determine whether they are duplicates.",
    )

    this function emits one group with `merge_decision="needs_review"`.

    5. A component that was not sent to the LLM because it was too large or unsafe is
    also emitted as a needs-review group. For example:

    _ReviewComponent(
        candidate_ids=("candidate_z",),
        needs_review_without_llm=True,
        review_reasons=(
            "oversized_component_singleton_split_residue_needs_review:"
            "('Objectif spécifique', '', ('segment_9',), 22)",
        ),
    )

    becomes an `SFIMergeGroup` with:

    llm_decision=None
    llm_review_set_id=None
    merge_decision="needs_review"
    merge_reason="Review component was too large or unsafe for bounded v0 LLM
    dedup review: ..."

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

    This function converts bounded `_ReviewComponents` into the actual payloads that
    will be sent to the dedup LLM. At this point, the pipeline has already decided
    which candidates should be reviewed together and whether the set is small/safe
    enough for LLM review. This function does not create new review evidence and does
    not make merge decisions. It simply separates components into two groups:

    1. LLM-reviewable components
        needs_review_without_llm=False -> converted into SFIDedupReviewRequest.

    2. Unresolved components
        needs_review_without_llm=True -> carried forward unchanged in
        unresolved_components, so later code can emit needs_review merge groups without
        calling the LLM.

    The candidate payload sent to the LLM is intentionally compact. It includes
    registry identity, statement type, code fields, description/source text, normalized
    text keys, source segment IDs, window references, and table row/header indexes. It
    does not include the full registry, full extraction window, DocumentIR, final SFI
    IDs, hierarchy, or canonical KG text.

    Examples
    --------

    1. A reviewable component becomes one LLM review request. For example, an input
    component like:

    _ReviewComponent(
        candidate_ids=("candidate_a", "candidate_b"),
        needs_review_without_llm=False,
        review_reasons=("duplicate_bucket:code:bucket_123:strong_signal",),
    )

    is converted into one `SFIDedupReviewRequest`. The request contains a deterministic
    `review_set_id` derived from the sorted candidate IDs, the deduplication
    instructions from the KG config, the bilingual pair policy, the review reasons, and
    compact candidate records copied from the registry:

    SFIDedupReviewRequest(
        bilingual_pair_policy=kg_config.academic_standards.bilingual_pair_policy,
        candidates=[
            SFIDedupReviewCandidate(... registry_candidate_id="candidate_a" ...),
            SFIDedupReviewCandidate(... registry_candidate_id="candidate_b" ...),
        ],
        review_reasons=("duplicate_bucket:code:bucket_123:strong_signal",),
        review_set_id="dedupe_review_<stable_hash>",
        sfi_deduplication_instructions=(
            kg_config.academic_standards.sfi_deduplication_instructions
        ),
    )

    The request is only a bounded review payload. It does not tell the LLM to merge the
    candidates; it asks the LLM to classify the candidates into merge, keep-separate,
    conflict, or needs-review groups.

    2. A component marked `needs_review_without_llm=True` is not converted into an LLM
    request. For example:

    _ReviewComponent(
        candidate_ids=("candidate_z",),
        needs_review_without_llm=True,
        review_reasons=(
            "duplicate_bucket:source_text:bucket_xyz:weak_signal",
            "oversized_component_singleton_split_residue_needs_review:"
            "('Objectif spécifique', '', ('segment_9',), 22)",
        ),
    )

    is appended to `unresolved_components` and returned separately. Later,
    `_build_merge_groups_from_responses()` converts it into a `needs_review` merge
    group without making an LLM call.

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

    By the time this function runs, the pipeline has already created merge groups for:

        - LLM-reviewed candidates from _build_merge_groups_from_responses()
        - unresolved oversized/split residues marked needs_review
        - candidates the LLM explicitly kept separate

    But many candidates may never have appeared in any duplicate bucket, warning, or
    provenance-overlap component. Those candidates were never sent to the LLM because
    there was no review evidence connecting them to another candidate. Thus,
    `_build_singleton_merge_groups()` turns each of those remaining candidates into an
    ordinary singleton merge group.

    It does this by computing: `set(sfi_candidates_by_id) - covered_candidate_ids`

    Then for each remaining candidate ID, it calls `_build_merge_group()` with:

        candidates=[candidate]
        llm_decision=None
        llm_review_set_id=None
        merge_decision="singleton"
        merge_reason="Candidate was not included in any SFI merge review set."

    So this function does not mean “the LLM decided this is separate.” It means the
    deterministic review-signal builder found no reason to compare this candidate with
    anything else. In other words, it handles candidates with no dedup evidence at all.

    This function is a final coverage step. It ensures every registry candidate appears
    in exactly one merge group before `_validate_merge_group_coverage()` checks the
    completed output.

    Examples
    --------

    1. This function creates singleton merge groups for registry candidates that were
    not covered by any reviewed, unresolved, conflict, merged, or keep-separate
    group.

    For example, suppose the registry contains five candidates:

    {
        "candidate_a",
        "candidate_b",
        "candidate_c",
        "candidate_d",
        "candidate_e",
    }

    and earlier merge-group construction already covered three of them:

    covered_candidate_ids = {
        "candidate_a",  # included in an LLM-reviewed merged group
        "candidate_b",  # included in the same LLM-reviewed merged group
        "candidate_c",  # included in a needs-review group
    }

    This function computes the remaining candidate IDs:

    {"candidate_d", "candidate_e"}

    and emits one singleton merge group for each remaining candidate:

    SFIMergeGroup(
        registry_candidate_ids=("candidate_d",),
        llm_decision=None,
        llm_review_set_id=None,
        merge_decision="singleton",
        merge_reason="Candidate was not included in any SFI merge review set.",
        ...
    )
    SFIMergeGroup(
        registry_candidate_ids=("candidate_e",),
        llm_decision=None,
        llm_review_set_id=None,
        merge_decision="singleton",
        merge_reason="Candidate was not included in any SFI merge review set.",
        ...
    )

    These singleton groups are different from singleton groups created after an LLM
    `keep_separate` decision. A keep-separate singleton means the candidate was
    reviewed with one or more related candidates and the LLM decided it should stand
    alone. A singleton from this function means the candidate was never included in any
    review component because no duplicate bucket, warning, or source-provenance overlap
    connected it to another candidate.

    Candidates marked `needs_review_without_llm=True` should already be included in
    `covered_candidate_ids` after `_build_merge_groups_from_responses()`. They
    therefore do not become ordinary unreviewed singletons here.

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
    """Merge overlapping review edges into connected components. This function turns
    overlapping links into review neighborhoods.

    If the output of `_build_initial_review_edges()` is something like:

    [
        ({"A", "B"}, {"reason_1"}),
        ({"B", "C"}, {"reason_2"}),
    ]

    then this function turns it into:

    _ReviewComponent(
        candidate_ids=("A", "B", "C"),
        needs_review_without_llm=False,
        review_reasons=("reason_1", "reason_2"),
    )

    In other words, this function does not decide what to merge either. It just
    combines candidates whose review signals overlap into one candidate cluster before
    bounding/splitting occurs. It uses a small union-find structure: every edge unions
    all candidate IDs in that edge, then reasons are collected across all candidates in
    the connected component. Components with fewer than 2 candidates are omitted.

    Examples
    --------

    1. A single edge becomes one review component. For example, an initial edge like:

    (
        {"candidate_a", "candidate_b"},
        {"duplicate_bucket:code:bucket_123:strong_signal"},
    )

    produces a component like:

    _ReviewComponent(
        candidate_ids=("candidate_a", "candidate_b"),
        needs_review_without_llm=False,
        review_reasons=("duplicate_bucket:code:bucket_123:strong_signal",),
    )

    2. Overlapping edges are merged into one connected component. For example, if one
    edge connects `candidate_a` to `candidate_b` and another edge connects
    `candidate_b` to `candidate_c`:

    [
        (
            {"candidate_a", "candidate_b"},
            {"duplicate_bucket:description_text:bucket_abc:medium_signal"},
        ),
        (
            {"candidate_b", "candidate_c"},
            {"registry_warning:same_text_repeated_across_windows:warning_0007"},
        ),
    ]

    the output is one component containing all three candidates:

    _ReviewComponent(
        candidate_ids=("candidate_a", "candidate_b", "candidate_c"),
        needs_review_without_llm=False,
        review_reasons=(
            "duplicate_bucket:description_text:bucket_abc:medium_signal",
            "registry_warning:same_text_repeated_across_windows:warning_0007",
        ),
    )

    The component does not mean all candidates should merge. It only means the
    candidate IDs are connected by review evidence and should be considered together
    before the bounded review step.

    3. Disjoint edges remain separate components. For example:

    [
        ({"candidate_a", "candidate_b"}, {"reason_1"}),
        ({"candidate_c", "candidate_d"}, {"reason_2"}),
    ]

    produces two independent components:

    _ReviewComponent(
        candidate_ids=("candidate_a", "candidate_b"),
        needs_review_without_llm=False,
        review_reasons=("reason_1",),
    )
    _ReviewComponent(
        candidate_ids=("candidate_c", "candidate_d"),
        needs_review_without_llm=False,
        review_reasons=("reason_2",),
    )

    4. Edges with fewer than two candidate IDs are ignored by construction because
    there is no deduplication comparison to perform.

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

    After `_merge_edges_to_components()`, a component can become large because
    duplicate buckets, warnings, and provenance edges can chain together. This function
    keeps small components as-is, but splits oversized components into smaller, safer
    chunks before they become LLM review requests. The current max is
    `_MAX_DEDUP_REVIEW_SET_CANDIDATES = 12`. Components with 12 or fewer candidates
    pass through unchanged.

    Oversized components are split by a conservative source-derived key:
    statement_type, normalized/code bucket identity, exact source segment IDs, and a
    coarse window_index // 3 band. If a split group or chunk has only one candidate, it
    is still carried forward as needs_review_without_llm=True so candidates with review
    evidence do not silently fall through as ordinary singletons.

    The flow is:

    1. If a connected component has 12 or fewer candidates, it passes through with
    needs_review_without_llm=False

    2. If a connected component has more than 12 candidates, the code splits it by:

    (
        candidate.statement_type,
        candidate.normalized_statement_code or candidate.code_bucket_key or "",
        tuple(candidate.source_segment_ids),
        candidate.window_index // 3,
    )

    During that split, needs_review_without_llm=True is assigned when the resulting
    group cannot become a meaningful LLM comparison set because it has only one
    candidate.

    There are two specific cases:

    Case 1: the split group itself has exactly one candidate (before chunking)

    split_key_A -> 8 candidates
    split_key_B -> 11 candidates
    split_key_C -> 1 candidate  # Case 1

    split_key_C has no peer left after safe splitting, so it cannot form an LLM dedup
    comparison. But because it came from an oversized connected component, the code
    preserves it as needs_review rather than letting it become an ordinary singleton.

    Case 2: a split group has at least 2 candidates, but it is still larger than the
    max review size, so the code chunks it into groups of 12; if the final chunk has
    only one candidate, then `needs_review_without_llm=True`.

    split_key_A -> 25 candidates

    Chunked with max size 12:

    chunk 1 -> 12 candidates
    chunk 2 -> 12 candidates
    chunk 3 -> 1 candidate

    That last candidate had peers under the same split key, but chunking separated it
    into a one-candidate tail. Since a one-candidate LLM dedup request is not useful,
    it is marked needs_review.

    Practical difference:

    Case 1 means: “This candidate became isolated after safe-context splitting.”
    Case 2 means: “This candidate belonged to a valid multi-candidate safe-context
        group, but was left alone by max-size chunking.”

    Both are handled the same downstream: no LLM call; emit a needs_review merge group.

    Examples
    --------

    1. A small component is passed through unchanged. For example, if the input
    component has 3 candidates and the maximum review-set size is 12:

    _ReviewComponent(
        candidate_ids=("candidate_a", "candidate_b", "candidate_c"),
        needs_review_without_llm=False,
        review_reasons=("duplicate_bucket:code:bucket_123:strong_signal",),
    )

    the same component is returned. It is small enough to become one bounded LLM dedup
    review request.

    2. An oversized component is split by conservative source-derived context. For
    example, suppose a 20-candidate component contains candidates from two source
    segments and two nearby window bands. Candidates are grouped by:

    (
        candidate.statement_type,
        candidate.normalized_statement_code or candidate.code_bucket_key or "",
        tuple(candidate.source_segment_ids),
        candidate.window_index // 3,
    )

    This can turn one broad connected component into smaller review components such as:

    _ReviewComponent(
        candidate_ids=("candidate_a", "candidate_b", "candidate_c"),
        needs_review_without_llm=False,
        review_reasons=(
            "duplicate_bucket:description_text:bucket_abc:medium_signal",
            "oversized_component_split_by_safe_source_context:"
            "('Objectif spécifique', '', ('segment_1',), 17)",
        ),
    )
    _ReviewComponent(
        candidate_ids=("candidate_d", "candidate_e"),
        needs_review_without_llm=False,
        review_reasons=(
            "duplicate_bucket:description_text:bucket_abc:medium_signal",
            "oversized_component_split_by_safe_source_context:"
            "('Objectif spécifique', '', ('segment_2',), 18)",
        ),
    )

    The split does not decide that candidates are duplicates. It only creates smaller,
    safer candidate sets for the LLM to review.

    If an oversized split group contains more than the maximum number of candidates, it
    is chunked into review-sized groups. For example, a 25-candidate split group with a
    maximum size of 12 becomes two 12-candidate review components and one 1-candidate
    residue.

    3. Singleton split residues are not treated as ordinary unreviewed singletons. They
    are carried forward as needs-review components because they came from an oversized
    component with real review evidence. For example:

    _ReviewComponent(
        candidate_ids=("candidate_z",),
        needs_review_without_llm=True,
        review_reasons=(
            "duplicate_bucket:source_text:bucket_xyz:weak_signal",
            "oversized_component_singleton_split_residue_needs_review:"
            "('Objectif spécifique', '', ('segment_9',), 22)",
        ),
    )

    Likewise, a one-candidate tail chunk from a larger split group is carried forward
    as needs-review rather than silently dropped:

    _ReviewComponent(
        candidate_ids=("candidate_y",),
        needs_review_without_llm=True,
        review_reasons=(
            "registry_warning:same_text_repeated_across_windows:warning_0042",
            "oversized_component_chunk_residue_needs_review:"
            "('Indicator', 'b4.1.1.3.2', ('segment_4',), 48)",
        ),
    )

    This guarantees that every candidate connected by review evidence remains
    represented either in an LLM-reviewable component or in an explicit needs-review
    component.

    Parameters
    ----------
    components
        Connected components built from review edges.
    sfi_candidates_by_id
        Lookup of registry candidates by temporary registry candidate ID.

    Returns
    -------
    list[_ReviewComponent]
        Bounded review components. Oversized unsafe components and split residues are
        marked for needs-review without an LLM call.
    """

    bounded_components: list[_ReviewComponent] = []

    for component in components:
        if len(component.candidate_ids) <= _MAX_DEDUP_REVIEW_SET_CANDIDATES:
            bounded_components.append(component)
            continue

        split_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)

        for candidate_id in component.candidate_ids:
            candidate = sfi_candidates_by_id[candidate_id]
            split_groups[
                (
                    candidate.statement_type,
                    candidate.normalized_statement_code
                    or candidate.code_bucket_key
                    or "",
                    tuple(candidate.source_segment_ids),
                    candidate.window_index // 3,
                )
            ].append(candidate_id)

        for split_key, split_candidate_ids in sorted(split_groups.items()):
            split_candidate_ids_sorted = sorted(split_candidate_ids)
            split_reason = "oversized_component_split_by_safe_source_context:" + repr(
                split_key
            )

            if len(split_candidate_ids_sorted) == 1:
                bounded_components.append(
                    _ReviewComponent(
                        candidate_ids=tuple(split_candidate_ids_sorted),
                        needs_review_without_llm=True,
                        review_reasons=tuple(
                            sorted(
                                set(component.review_reasons)
                                | {
                                    "oversized_component_singleton_split_residue_needs_review:"
                                    + repr(split_key)
                                }
                            )
                        ),
                    )
                )
                continue

            for start_index in range(
                0, len(split_candidate_ids_sorted), _MAX_DEDUP_REVIEW_SET_CANDIDATES
            ):
                chunk_candidate_ids = split_candidate_ids_sorted[
                    start_index : start_index + _MAX_DEDUP_REVIEW_SET_CANDIDATES
                ]

                if len(chunk_candidate_ids) < 2:
                    bounded_components.append(
                        _ReviewComponent(
                            candidate_ids=tuple(chunk_candidate_ids),
                            needs_review_without_llm=True,
                            review_reasons=tuple(
                                sorted(
                                    set(component.review_reasons)
                                    | {
                                        "oversized_component_chunk_residue_needs_review:"
                                        + repr(split_key)
                                    }
                                )
                            ),
                        )
                    )
                    continue

                bounded_components.append(
                    _ReviewComponent(
                        candidate_ids=tuple(chunk_candidate_ids),
                        needs_review_without_llm=False,
                        review_reasons=tuple(
                            sorted(set(component.review_reasons) | {split_reason})
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
