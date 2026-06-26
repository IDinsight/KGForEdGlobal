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

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# Third Party Library
from loguru import logger
from pydantic import BaseModel

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
from skg.kgs.validators import verify_sfi_dedup_review_quality
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, open_json_type, write_to_json

_MAX_DEDUP_REVIEW_SET_CANDIDATES = 12
_SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG = "same_code_different_content"


@dataclass(frozen=True)
class _ReviewComponent:
    """Internal connected review component before LLM review."""

    candidate_ids: tuple[str, ...]
    needs_review_without_llm: bool
    review_reasons: tuple[str, ...]


def _annotate_same_code_different_content_audit_flags(
    merge_groups: Sequence[SFIMergeGroup],
) -> list[SFIMergeGroup]:
    """Attach audit flags to mintable same-code groups with divergent content.

    This does not change merge decisions. It records that the next step must mint
    separate, disambiguated final SFI IDs and must not resolve hierarchy by code alone
    for these groups.

    Parameters
    ----------
    merge_groups
        Merge groups produced by LLM decisions and deterministic singleton fallback.

    Returns
    -------
    list[SFIMergeGroup]
        Merge groups with same-code/different-content audit annotations added.
    """

    groups_by_code_key: dict[tuple[str, str, str], list[SFIMergeGroup]] = defaultdict(
        list
    )

    for merge_group in merge_groups:
        if not (
            merge_group.merge_decision in {"merged", "singleton"}
            and bool(merge_group.normalized_statement_code)
            and bool(merge_group.normalized_statement_type)
            and bool(merge_group.statement_type)
        ):
            continue

        groups_by_code_key[
            (
                merge_group.statement_type or "",
                merge_group.normalized_statement_type or "",
                merge_group.normalized_statement_code or "",
            )
        ].append(merge_group)

    annotated_by_id = {
        merge_group.merge_group_id: merge_group for merge_group in merge_groups
    }

    for (
        statement_type,
        normalized_statement_type,
        normalized_statement_code,
    ), groups in groups_by_code_key.items():
        if len(groups) < 2:
            continue

        fingerprints = {
            group.merge_group_id: _audit_content_fingerprint(group) for group in groups
        }

        if len(set(fingerprints.values())) <= 1:
            continue

        group_ids = [group.merge_group_id for group in groups]
        audit_note = (
            f"Shares statement_type="
            f"{statement_type!r}, normalized_statement_type="
            f"{normalized_statement_type!r}, and normalized_statement_code="
            f"{normalized_statement_code!r} with another mintable merge group, but "
            f"the source-visible descriptions/source_text differ. Step 8 should mint "
            f"separate deterministic final SFIs with source/text/provenance "
            f"disambiguators and preserve this same-code/different-content evidence "
            f"for manual review."
        )

        for group in groups:
            peer_group_ids = [
                group_id for group_id in group_ids if group_id != group.merge_group_id
            ]
            annotated_by_id[group.merge_group_id] = _append_merge_group_audit(
                audit_flag=_SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG,
                audit_note=audit_note,
                audit_peer_merge_group_ids=peer_group_ids,
                merge_group=annotated_by_id[group.merge_group_id],
            )

    return [annotated_by_id[merge_group.merge_group_id] for merge_group in merge_groups]


def _append_jsonl_model(*, fp: Path, model: BaseModel) -> None:
    """Append one Pydantic model payload to a JSONL artifact.

    Parameters
    ----------
    fp
        JSONL file path.
    model
        Pydantic model instance to append.
    """

    make_dir(fp.parent)

    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(model.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _append_merge_group_audit(
    *,
    audit_flag: str,
    audit_note: str,
    audit_peer_merge_group_ids: Sequence[str],
    merge_group: SFIMergeGroup,
) -> SFIMergeGroup:
    """Return a merge group with one deterministic audit annotation appended.

    Parameters
    ----------
    audit_flag
        Machine-readable audit flag to attach.
    audit_note
        Human-readable note explaining why the flag was attached.
    audit_peer_merge_group_ids
        Related merge-group IDs that share the same audit concern.
    merge_group
        Existing merge group to annotate.

    Returns
    -------
    SFIMergeGroup
        Copy of the merge group with updated audit fields.
    """

    return merge_group.model_copy(
        update={
            "audit_flags": _unique_nonempty([*merge_group.audit_flags, audit_flag]),
            "audit_notes": _unique_nonempty([*merge_group.audit_notes, audit_note]),
            "audit_peer_merge_group_ids": _unique_nonempty(
                [
                    *merge_group.audit_peer_merge_group_ids,
                    *audit_peer_merge_group_ids,
                ]
            ),
        }
    )


def _assert_model_sequences_equal(
    *, actual: Sequence[Any], artifact_label: str, expected: Sequence[Any]
) -> None:
    """Validate that two persisted model sequences are exactly equivalent.

    Parameters
    ----------
    actual
        Models loaded from an artifact.
    artifact_label
        Human-readable artifact label for error messages.
    expected
        Expected models computed during the current run.

    Raises
    ------
    ValueError
        If the sequences differ in length or model payload.
    """

    if len(actual) != len(expected):
        raise ValueError(
            f"{artifact_label} has {len(actual)} records, but expected "
            f"{len(expected)} records."
        )

    for index, (actual_model, expected_model) in enumerate(
        zip(actual, expected, strict=True), start=1
    ):
        if _model_dump_key(actual_model) != _model_dump_key(expected_model):
            raise ValueError(
                f"{artifact_label} record {index} does not match the current "
                f"planned artifact payload."
            )


def _audit_content_fingerprint(merge_group: SFIMergeGroup) -> tuple[str, ...]:
    """Build normalized content evidence used to detect same-code divergences.

    Parameters
    ----------
    merge_group
        Merge group whose source-visible text and descriptions should be summarized.

    Returns
    -------
    tuple[str, ...]
        Stable normalized content fragments for audit comparison.
    """

    values = [
        *merge_group.candidate_descriptions,
        *merge_group.candidate_source_texts,
    ]

    return tuple(
        sorted(
            {
                normalized
                for value in values
                if (normalized := " ".join(str(value or "").casefold().split()))
            }
        )
    )


def _build_initial_review_edges(
    sfi_candidate_registry: SFIRegistryArtifact,
) -> list[tuple[set[str], set[str]]]:
    """Build initial review edges from buckets, warnings, exact source-text repeats,
    and source overlap. This function answers "which candidates have enough evidence
    to be reviewed together?".
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
    edges.extend(
        _build_same_normalized_source_text_edges(
            sfi_candidate_registry=sfi_candidate_registry
        )
    )

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
                "source_context_key": candidate.source_context_key,
                "source_context_labels": candidate.source_context_labels,
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
    audit_flag_counts = Counter(
        audit_flag for group in merge_groups for audit_flag in group.audit_flags
    )
    summary = SFIMergeSummary(
        audit_flag_count_by_type=dict(sorted(audit_flag_counts.items())),
        candidate_count=len(sfi_candidate_registry.candidates),
        conflict_group_count=len(conflict_groups),
        dedup_review_request_count=len(review_requests),
        dedup_review_response_count=len(review_responses),
        merge_group_audit_flag_count=sum(
            1 for group in merge_groups if group.audit_flags
        ),
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
                        source_context_key=candidate.source_context_key,
                        source_context_labels=candidate.source_context_labels,
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
                review_focus=(
                    "same_normalized_source_text"
                    if any(
                        reason.startswith("same_normalized_source_text:")
                        for reason in component.review_reasons
                    )
                    else "general"
                ),
                review_reasons=sorted(set(component.review_reasons)),
                review_set_id=review_set_id,
                sfi_deduplication_instructions=(
                    kg_config.academic_standards.sfi_deduplication_instructions
                ),
            )
        )

    return review_requests, unresolved_components


def _build_same_normalized_source_text_edges(
    sfi_candidate_registry: SFIRegistryArtifact,
) -> list[tuple[set[str], set[str]]]:
    """Build review edges for exact normalized source-text repeats.

    Candidates with the same normalized source text are not automatically duplicates: a
    label such as "Data", "Grade 4", or "Strand 1" may be reused in different
    curriculum scopes. This function only creates retrieval edges so the dedup LLM can
    decide whether each repeated text instance represents the same logical source item
    or separate items with reused wording.

    To keep the comparison general and safe across jurisdictions, groups are
    partitioned by both source-facing statement type and normalized statement type
    before an edge is emitted. This prevents unrelated source roles from being sent
    together solely because their visible text matches.

    Parameters
    ----------
    sfi_candidate_registry
        SFI candidate registry artifact.

    Returns
    -------
    list[tuple[set[str], set[str]]]
        Candidate-ID sets paired with deterministic same-source-text review reasons.
    """

    edges: list[tuple[set[str], set[str]]] = []
    candidate_ids_by_text_key: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for candidate in sfi_candidate_registry.candidates:
        normalized_source_text = candidate.normalized_source_text.strip()

        if not normalized_source_text:
            continue

        candidate_ids_by_text_key[
            (
                candidate.normalized_statement_type,
                candidate.statement_type,
                normalized_source_text,
            )
        ].append(candidate.registry_candidate_id)

    for text_key, candidate_ids_raw in sorted(candidate_ids_by_text_key.items()):
        candidate_ids = set(candidate_ids_raw)

        if len(candidate_ids) < 2:
            continue

        normalized_statement_type, statement_type, normalized_source_text = text_key
        digest = hashlib.sha256(
            "|".join(
                [
                    normalized_statement_type,
                    statement_type,
                    normalized_source_text,
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]

        edges.append(
            (
                candidate_ids,
                {
                    "same_normalized_source_text:"
                    f"{statement_type}:{normalized_statement_type}:{digest}"
                },
            )
        )

    return edges


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


def _load_complete_existing_merge_report(
    *,
    conflicts_fp: Path,
    merge_groups_fp: Path,
    merge_report_fp: Path,
    needs_review_fp: Path,
    planned_review_requests: Sequence[SFIDedupReviewRequest],
    review_requests_fp: Path,
    review_responses_fp: Path,
    sfi_candidate_registry: SFIRegistryArtifact,
) -> SFIMergeReport | None:
    """Load a complete existing merge report, or return None for resume.

    A report is reusable only when the full report, companion JSON artifacts, and
    review JSONL artifacts are all present, parseable, aligned to the current planned
    review requests, and cover every current registry candidate exactly once.

    Parameters
    ----------
    conflicts_fp
        JSON path for conflict groups.
    merge_groups_fp
        JSON path for all merge groups.
    merge_report_fp
        JSON path for the full merge report.
    needs_review_fp
        JSON path for needs-review groups.
    planned_review_requests
        Deterministic review requests computed from the current registry.
    review_requests_fp
        JSONL path for persisted review requests.
    review_responses_fp
        JSONL path for persisted review responses.
    sfi_candidate_registry
        Current SFI candidate registry.

    Returns
    -------
    SFIMergeReport | None
        Valid existing merge report, or None when this step should resume/rebuild.
    """

    if not merge_report_fp.exists():
        return None

    try:
        merge_report = SFIMergeReport.model_validate(open_json_type(merge_report_fp))
        _validate_complete_merge_artifacts(
            conflicts_fp=conflicts_fp,
            merge_groups_fp=merge_groups_fp,
            merge_report=merge_report,
            merge_report_fp=merge_report_fp,
            needs_review_fp=needs_review_fp,
            planned_review_requests=planned_review_requests,
            review_requests_fp=review_requests_fp,
            review_responses_fp=review_responses_fp,
            sfi_candidate_registry=sfi_candidate_registry,
        )
    except Exception as e:  # pylint: disable=W0718
        logger.warning(
            f"Existing SFI merge artifacts are incomplete or stale; resuming dedup step: "
            f"{e}"
        )
        return None

    logger.info(
        f"Loading complete existing SFI merge report because overwrite=False: "
        f"{merge_report_fp}"
    )

    return merge_report


def _load_jsonl_review_requests(
    review_requests_fp: Path,
) -> list[SFIDedupReviewRequest]:
    """Load persisted SFI dedup review requests from JSONL.

    Parameters
    ----------
    review_requests_fp
        JSONL path for persisted review requests.

    Returns
    -------
    list[SFIDedupReviewRequest]
        Parsed review requests in file order.

    Raises
    ------
    ValueError
        If the JSONL file is missing or contains invalid request records.
    """

    if not review_requests_fp.exists():
        raise ValueError(f"Missing review request JSONL artifact: {review_requests_fp}")

    review_requests: list[SFIDedupReviewRequest] = []

    with review_requests_fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            if not line_clean:
                continue

            try:
                review_requests.append(
                    SFIDedupReviewRequest.model_validate_json(line_clean)
                )
            except Exception as e:
                raise ValueError(
                    f"Invalid SFI dedup review request JSONL record at line "
                    f"{line_number}: {e}"
                ) from e

    return review_requests


def _load_jsonl_review_responses(
    *, allow_partial_prefix: bool, review_responses_fp: Path
) -> list[SFIDedupReviewResponse]:
    """Load persisted SFI dedup review responses from JSONL.

    Parameters
    ----------
    allow_partial_prefix
        If true, return the valid prefix when a later line is invalid or truncated. If
        false, invalid records raise an error.
    review_responses_fp
        JSONL path for persisted review responses.

    Returns
    -------
    list[SFIDedupReviewResponse]
        Parsed review responses in file order.

    Raises
    ------
    ValueError
        If the JSONL file is missing or invalid and partial prefixes are not allowed.
    """

    if not review_responses_fp.exists():
        if allow_partial_prefix:
            return []

        raise ValueError(
            f"Missing review response JSONL artifact: {review_responses_fp}"
        )

    review_responses: list[SFIDedupReviewResponse] = []

    with review_responses_fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            if not line_clean:
                continue

            try:
                review_responses.append(
                    SFIDedupReviewResponse.model_validate_json(line_clean)
                )
            except Exception as e:
                if allow_partial_prefix:
                    logger.warning(
                        f"Ignoring invalid trailing SFI dedup review response JSONL "
                        f"record at line {line_number}; valid prefix length is "
                        f"{len(review_responses)}: {e}"
                    )
                    return review_responses

                raise ValueError(
                    f"Invalid SFI dedup review response JSONL record at line "
                    f"{line_number}: {e}"
                ) from e

    return review_responses


def _load_merge_groups_file(fp: Path) -> list[SFIMergeGroup]:
    """Load a JSON artifact containing a list of SFI merge groups.

    Parameters
    ----------
    fp
        JSON file path to load.

    Returns
    -------
    list[SFIMergeGroup]
        Parsed merge groups.

    Raises
    ------
    ValueError
        If the file is missing or does not contain a valid merge-group list.
    """

    if not fp.exists():
        raise ValueError(f"Missing SFI merge group artifact: {fp}")

    data = open_json_type(fp)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in SFI merge group artifact: {fp}")

    return [SFIMergeGroup.model_validate(item) for item in data]


def _load_resumable_review_progress(
    *,
    merge_report_fp: Path,
    planned_review_requests: Sequence[SFIDedupReviewRequest],
    review_requests_fp: Path,
    review_responses_fp: Path,
) -> list[SFIDedupReviewResponse]:
    """Load a valid completed review-response prefix for an incomplete dedup run.

    The response JSONL is preferred because it is written incrementally after each LLM
    review. A saved response prefix is reusable only when both the saved response
    prefix and the saved request payload prefix match the current deterministic review
    plan. This prevents stale LLM decisions from being reused when candidate IDs remain
    stable but candidate text, source context, or dedup instructions have changed. If
    JSONL progress is unavailable or stale, a valid response/request prefix embedded in
    an existing merge report can be used as a fallback.

    Parameters
    ----------
    merge_report_fp
        JSON path for the full merge report, used as a fallback response source.
    planned_review_requests
        Deterministic review requests computed from the current registry.
    review_requests_fp
        JSONL path for persisted review requests.
    review_responses_fp
        JSONL path for persisted review responses.

    Returns
    -------
    list[SFIDedupReviewResponse]
        Valid completed review responses whose saved request payloads match the current
        planned request prefix.
    """

    try:
        review_responses = _load_jsonl_review_responses(
            allow_partial_prefix=True, review_responses_fp=review_responses_fp
        )
        _validate_review_response_prefix(
            planned_review_requests=planned_review_requests,
            saved_review_responses=review_responses,
        )

        if review_responses:
            review_requests = _load_jsonl_review_requests(review_requests_fp)
            _validate_review_request_prefix(
                planned_review_requests=planned_review_requests,
                saved_review_requests=review_requests,
                trusted_prefix_length=len(review_responses),
            )

            logger.info(
                f"Resuming SFI dedup from {len(review_responses)} completed "
                f"review responses in {review_responses_fp}."
            )

            return review_responses
    except Exception as e:  # pylint: disable=W0718
        logger.warning(
            f"Ignoring existing SFI dedup JSONL progress because the saved "
            f"request/response prefix does not match the current plan: {e}"
        )

    if not merge_report_fp.exists():
        return []

    try:
        merge_report = SFIMergeReport.model_validate(open_json_type(merge_report_fp))
        _validate_review_response_prefix(
            planned_review_requests=planned_review_requests,
            saved_review_responses=merge_report.review_responses,
        )

        if merge_report.review_responses:
            _validate_review_request_prefix(
                planned_review_requests=planned_review_requests,
                saved_review_requests=merge_report.review_requests,
                trusted_prefix_length=len(merge_report.review_responses),
            )
    except Exception as e:  # pylint: disable=W0718
        logger.warning(
            f"Ignoring existing SFI merge report response progress because its saved "
            f"request/response prefix does not match the current plan: {e}"
        )

        return []

    if merge_report.review_responses:
        logger.info(
            f"Resuming SFI dedup from {len(merge_report.review_responses)} "
            f"validated review responses embedded in {merge_report_fp}."
        )

    return list(merge_report.review_responses)


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


def _model_dump_key(value: Any) -> str:
    """Build a stable comparison key for a Pydantic-style model.

    Parameters
    ----------
    value
        Model-like value with a `model_dump` method.

    Returns
    -------
    str
        Stable JSON representation for exact artifact comparison.
    """

    return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


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


def _rewrite_review_progress_files(
    *,
    completed_review_requests: Sequence[SFIDedupReviewRequest],
    completed_review_responses: Sequence[SFIDedupReviewResponse],
    review_requests_fp: Path,
    review_responses_fp: Path,
) -> None:
    """Rewrite review JSONL artifacts to a clean valid completed prefix.

    Parameters
    ----------
    completed_review_requests
        Planned review requests corresponding to completed responses.
    completed_review_responses
        Completed review responses to preserve.
    review_requests_fp
        JSONL path for persisted review requests.
    review_responses_fp
        JSONL path for persisted review responses.
    """

    make_dir(review_requests_fp.parent)
    make_dir(review_responses_fp.parent)

    review_requests_fp.write_text("", encoding="utf-8")
    review_responses_fp.write_text("", encoding="utf-8")

    for review_request in completed_review_requests:
        _append_jsonl_model(fp=review_requests_fp, model=review_request)

    for review_response in completed_review_responses:
        _append_jsonl_model(fp=review_responses_fp, model=review_response)


def _run_dedup_reviews(
    *,
    completed_review_responses: Sequence[SFIDedupReviewResponse],
    review_requests: Sequence[SFIDedupReviewRequest],
    review_requests_fp: Path,
    review_responses_fp: Path,
    usage_tracker: KGUsageTracker,
) -> list[SFIDedupReviewResponse]:
    """Run remaining LLM dedup reviews and persist progress incrementally.

    Parameters
    ----------
    completed_review_responses
        Valid response prefix already completed in a previous partial run.
    review_requests
        Full planned review request sequence.
    review_requests_fp
        JSONL path for persisted review requests.
    review_responses_fp
        JSONL path for persisted review responses.
    usage_tracker
        Usage tracker to accumulate LLM token counts.

    Returns
    -------
    list[SFIDedupReviewResponse]
        Completed review responses in request order, including the resumed prefix.

    Raises
    ------
    ValueError
        If the completed response prefix is longer than the planned request sequence.
    """

    review_responses = list(completed_review_responses)

    if len(review_responses) > len(review_requests):
        raise ValueError(
            f"Completed review response prefix has {len(review_responses)} records, "
            f"but only {len(review_requests)} review requests are planned."
        )

    if review_requests:
        make_dir(review_requests_fp.parent)
        make_dir(review_responses_fp.parent)

    for request_index in range(len(review_responses), len(review_requests)):
        current_request_number = request_index + 1
        review_request = review_requests[request_index]

        logger.info(
            f"Running SFI dedup review {current_request_number}/"
            f"{len(review_requests)}: review_set_id={review_request.review_set_id}; "
            f"SFI candidates={len(review_request.candidates)}."
        )

        _append_jsonl_model(fp=review_requests_fp, model=review_request)

        review_response = review_sfi_dedup_set(
            review_request=review_request, usage_tracker=usage_tracker
        )

        _append_jsonl_model(fp=review_responses_fp, model=review_response)

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
    statement_type, normalized/code/context bucket identity, exact source segment IDs,
    and a coarse window_index // 3 band. If a split group or chunk has only one
    candidate, it is still carried forward as needs_review_without_llm=True so
    candidates with review evidence do not silently fall through as ordinary singletons.

    The flow is:

    1. If a connected component has 12 or fewer candidates, it passes through with
    needs_review_without_llm=False

    2. If a connected component has more than 12 candidates, the code splits it by:

    (
        candidate.statement_type,
        candidate.normalized_statement_code
        or candidate.code_bucket_key
        or candidate.source_context_key,
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
        candidate.normalized_statement_code
        or candidate.code_bucket_key
        or candidate.source_context_key,
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
                    or candidate.source_context_key,
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


def _validate_complete_merge_artifacts(
    *,
    conflicts_fp: Path,
    merge_groups_fp: Path,
    merge_report: SFIMergeReport,
    merge_report_fp: Path,
    needs_review_fp: Path,
    planned_review_requests: Sequence[SFIDedupReviewRequest],
    review_requests_fp: Path,
    review_responses_fp: Path,
    sfi_candidate_registry: SFIRegistryArtifact,
) -> None:
    """Validate that all persisted dedup artifacts are complete and current.

    Parameters
    ----------
    conflicts_fp
        JSON path for conflict groups.
    merge_groups_fp
        JSON path for all merge groups.
    merge_report
        Parsed full merge report.
    merge_report_fp
        JSON path for the full merge report.
    needs_review_fp
        JSON path for needs-review groups.
    planned_review_requests
        Deterministic review requests computed from the current registry.
    review_requests_fp
        JSONL path for persisted review requests.
    review_responses_fp
        JSONL path for persisted review responses.
    sfi_candidate_registry
        Current SFI candidate registry.

    Raises
    ------
    ValueError
        If any artifact is missing, stale, incomplete, or internally inconsistent.
    """

    if not merge_report_fp.exists():
        raise ValueError(f"Missing SFI merge report artifact: {merge_report_fp}")

    _validate_merge_group_coverage(
        merge_groups=merge_report.merge_groups,
        sfi_candidate_registry=sfi_candidate_registry,
    )
    _assert_model_sequences_equal(
        actual=merge_report.review_requests,
        artifact_label="sfi_merge_report.review_requests",
        expected=planned_review_requests,
    )
    _validate_review_response_prefix(
        planned_review_requests=planned_review_requests,
        saved_review_responses=merge_report.review_responses,
    )

    if len(merge_report.review_responses) != len(planned_review_requests):
        raise ValueError(
            f"sfi_merge_report.review_responses has "
            f"{len(merge_report.review_responses)} records, but expected "
            f"{len(planned_review_requests)} records."
        )

    _assert_model_sequences_equal(
        actual=_load_merge_groups_file(merge_groups_fp),
        artifact_label="sfi_merge_groups.json",
        expected=merge_report.merge_groups,
    )
    _assert_model_sequences_equal(
        actual=_load_merge_groups_file(conflicts_fp),
        artifact_label="sfi_merge_conflicts.json",
        expected=merge_report.conflict_groups,
    )
    _assert_model_sequences_equal(
        actual=_load_merge_groups_file(needs_review_fp),
        artifact_label="sfi_merge_needs_review.json",
        expected=merge_report.needs_review_groups,
    )
    _assert_model_sequences_equal(
        actual=_load_jsonl_review_requests(review_requests_fp),
        artifact_label="sfi_dedup_review_requests.jsonl",
        expected=planned_review_requests,
    )
    _assert_model_sequences_equal(
        actual=_load_jsonl_review_responses(
            allow_partial_prefix=False, review_responses_fp=review_responses_fp
        ),
        artifact_label="sfi_dedup_review_responses.jsonl",
        expected=merge_report.review_responses,
    )


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


def _validate_review_request_prefix(
    *,
    planned_review_requests: Sequence[SFIDedupReviewRequest],
    saved_review_requests: Sequence[SFIDedupReviewRequest],
    trusted_prefix_length: int,
) -> None:
    """Validate saved review requests for a trusted completed-response prefix.

    A completed response can be reused only when the saved request payload that
    produced it exactly matches the current deterministic request payload at the same
    position. Candidate IDs alone are not sufficient because candidate descriptions,
    source context, source references, or dedup instructions may change while temporary
    registry IDs remain stable.

    Parameters
    ----------
    planned_review_requests
        Deterministic review requests computed from the current registry.
    saved_review_requests
        Persisted review requests from JSONL or an existing merge report.
    trusted_prefix_length
        Number of saved request records required to match the current planned prefix.

    Raises
    ------
    ValueError
        If the trusted prefix length is invalid, the saved request artifact is shorter
        than the completed-response prefix, or any saved request payload differs from
        the current planned request payload at the same position.
    """

    if trusted_prefix_length < 0:
        raise ValueError(
            f"Trusted SFI dedup review request prefix length cannot be negative: "
            f"{trusted_prefix_length}."
        )

    if trusted_prefix_length > len(planned_review_requests):
        raise ValueError(
            f"Trusted SFI dedup review request prefix length "
            f"{trusted_prefix_length} exceeds the current planned request count "
            f"{len(planned_review_requests)}."
        )

    if len(saved_review_requests) < trusted_prefix_length:
        raise ValueError(
            f"Saved SFI dedup review requests contain {len(saved_review_requests)} "
            f"records, but {trusted_prefix_length} completed responses require the "
            f"same number of matching saved request payloads."
        )

    _assert_model_sequences_equal(
        actual=saved_review_requests[:trusted_prefix_length],
        artifact_label=("saved SFI dedup review request completed-response prefix"),
        expected=planned_review_requests[:trusted_prefix_length],
    )


def _validate_review_response_prefix(
    *,
    planned_review_requests: Sequence[SFIDedupReviewRequest],
    saved_review_responses: Sequence[SFIDedupReviewResponse],
) -> None:
    """Validate completed review responses against the current request prefix.

    Parameters
    ----------
    planned_review_requests
        Deterministic review requests computed from the current registry.
    saved_review_responses
        Persisted review responses to validate as a completed prefix.

    Raises
    ------
    ValueError
        If the responses do not match the current planned request prefix.
    """

    if len(saved_review_responses) > len(planned_review_requests):
        raise ValueError(
            f"Found {len(saved_review_responses)} saved review responses, but only "
            f"{len(planned_review_requests)} review requests are planned."
        )

    for response_index, review_response in enumerate(saved_review_responses):
        review_request = planned_review_requests[response_index]

        try:
            verify_sfi_dedup_review_quality(
                review_request=review_request, review_response=review_response
            )
        except QualityError as e:
            raise ValueError(
                f"Saved SFI dedup response {response_index + 1} does not match the "
                f"current planned review request: {e}"
            ) from e


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
        Complete dedup merge report.
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
        If true, reset all dedupe artifacts and restart dedup from scratch. If false,
        reuse only complete current artifacts; otherwise resume from completed review
        responses and finish the dedup artifacts.
    sfi_candidate_registry
        Global SFI candidate registry artifact.
    usage_tracker
        Usage tracker to accumulate LLM token counts.

    Returns
    -------
    SFIMergeReport
        Complete merge/dedup report.
    """

    conflicts_fp = kg_dirs.root / "sfi_merge_conflicts.json"
    merge_groups_fp = kg_dirs.root / "sfi_merge_groups.json"
    merge_report_fp = kg_dirs.root / "sfi_merge_report.json"
    needs_review_fp = kg_dirs.root / "sfi_merge_needs_review.json"
    review_requests_fp = kg_dirs.root / "sfi_dedup_review_requests.jsonl"
    review_responses_fp = kg_dirs.root / "sfi_dedup_review_responses.jsonl"

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

    if overwrite:
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
        completed_review_responses: list[SFIDedupReviewResponse] = []
    else:
        existing_merge_report = _load_complete_existing_merge_report(
            conflicts_fp=conflicts_fp,
            merge_groups_fp=merge_groups_fp,
            merge_report_fp=merge_report_fp,
            needs_review_fp=needs_review_fp,
            planned_review_requests=review_requests,
            review_requests_fp=review_requests_fp,
            review_responses_fp=review_responses_fp,
            sfi_candidate_registry=sfi_candidate_registry,
        )

        if existing_merge_report is not None:
            return existing_merge_report

        completed_review_responses = _load_resumable_review_progress(
            merge_report_fp=merge_report_fp,
            planned_review_requests=review_requests,
            review_requests_fp=review_requests_fp,
            review_responses_fp=review_responses_fp,
        )
        _prepare_output_files(
            [conflicts_fp, merge_groups_fp, merge_report_fp, needs_review_fp]
        )
        _rewrite_review_progress_files(
            completed_review_requests=review_requests[
                : len(completed_review_responses)
            ],
            completed_review_responses=completed_review_responses,
            review_requests_fp=review_requests_fp,
            review_responses_fp=review_responses_fp,
        )

    review_responses = _run_dedup_reviews(
        completed_review_responses=completed_review_responses,
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
    merge_groups = _annotate_same_code_different_content_audit_flags(merge_groups)
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
