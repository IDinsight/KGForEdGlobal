"""This module contains functionalities for merging SFI groups with bounded LLM dedup
review.

This module consumes the SFI candidate registry, constructs small review sets from
controlled-value repeats, duplicate buckets, warning groups, and source-provenance
overlap, runs an SFI dedup producer and independent checker for each bounded set, and
emits merge groups for every registry candidate.
"""

# Standard Library
import hashlib
import itertools

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from skg.kgs.llm import KGUsageTracker, review_sfi_dedup_set
from skg.kgs.schemas import (
    SFICodeResolutionMethod,
    SFIDedupContextWindow,
    SFIDedupDecision,
    SFIDedupReviewCandidate,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIDedupReviewSignal,
    SFIMergeDecision,
    SFIMergeGroup,
    SFIMergeReport,
    SFIMergeSummary,
    SFIRegistryArtifact,
    SFIRegistryCandidate,
)
from skg.kgs.utils import (
    KGDirs,
    append_jsonl_model,
    assert_model_sequences_equal,
    model_dump_key,
    normalize_text,
    reset_output_files,
    unique_nonempty,
)
from skg.kgs.validators import verify_sfi_dedup_review_integrity
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, open_json_type, write_to_json

_SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG = "same_code_different_content"


@dataclass(frozen=True)
class _CodeResolution:
    """Resolved canonical-code details for one SFI merge group.

    Attributes
    ----------
    canonical_code_source_candidate_id
        Registry candidate selected by review when multiple distinct source codes merge.
    canonical_code_type
        Configured code type resolved for the canonical source-backed code.
    canonical_normalized_statement_code
        Resolved normalized code for the logical merged item, when coded.
    canonical_statement_code
        Resolved source-backed code surface for the logical merged item, when coded.
    code_resolution_method
        Deterministic method describing how the canonical code was resolved.
    code_resolution_reason
        Source-grounded or deterministic explanation for the resolution.
    """

    canonical_code_source_candidate_id: str | None
    canonical_code_type: str | None
    canonical_normalized_statement_code: str | None
    canonical_statement_code: str | None
    code_resolution_method: SFICodeResolutionMethod
    code_resolution_reason: str


@dataclass(frozen=True)
class _ReviewComponent:
    """Internal connected review component before LLM review."""

    candidate_ids: tuple[str, ...]
    needs_review_without_llm: bool
    review_reasons: tuple[str, ...]


@dataclass(frozen=True)
class _TypeResolution:
    """Resolved canonical statement-type details for one SFI merge group.

    Attributes
    ----------
    canonical_normalized_statement_type
        Resolved normalized statement type for a mintable group.
    canonical_statement_type
        Resolved source-facing statement type for a mintable group.
    canonical_type_selection_reason
        Source-grounded reason for a mixed-type merge selection, when required.
    canonical_type_source_candidate_id
        Candidate selected as the canonical type source, when required.
    """

    canonical_normalized_statement_type: str | None
    canonical_statement_type: str | None
    canonical_type_selection_reason: str | None
    canonical_type_source_candidate_id: str | None


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
        # If these are truthy, they are non-empty strings.
        if not (
            merge_group.merge_decision in {"merged", "singleton"}
            and merge_group.canonical_code_type
            and merge_group.canonical_normalized_statement_code
        ):
            continue

        groups_by_code_key[
            (
                merge_group.code_scope_key or "",
                merge_group.canonical_code_type,
                merge_group.canonical_normalized_statement_code,
            )
        ].append(merge_group)

    annotated_by_id = {group.merge_group_id: group for group in merge_groups}

    for (
        code_scope_key,
        canonical_code_type,
        normalized_statement_code,
    ), groups in groups_by_code_key.items():
        if len(groups) < 2:
            continue

        # Build normalized content evidence to detect same-code divergences.
        fingerprints = set()

        for group in groups:
            values = [*group.candidate_descriptions, *group.candidate_source_texts]
            fingerprints.add(
                tuple(
                    sorted(
                        {
                            normalized_value
                            for value in values
                            if (normalized_value := normalize_text(value))
                        }
                    )
                )
            )

        if len(fingerprints) <= 1:
            continue

        group_ids = [group.merge_group_id for group in groups]
        audit_note = (
            f"Shares code_scope_key={code_scope_key or None!r}, "
            f"canonical_code_type={canonical_code_type!r}, "
            f"and normalized_statement_code={normalized_statement_code!r} "
            f"with another mintable merge group, but the source-visible "
            f"descriptions/source_text differ. SFI finalization should mint separate "
            f"deterministic final SFIs with source/text/provenance disambiguators "
            f"and preserve this same-code/different-content evidence for manual review."
        )

        for group in groups:
            peer_group_ids = [gid for gid in group_ids if gid != group.merge_group_id]
            current = annotated_by_id[group.merge_group_id]

            annotated_by_id[group.merge_group_id] = current.model_copy(
                update={
                    "audit_flags": unique_nonempty(
                        [*current.audit_flags, _SAME_CODE_DIFFERENT_CONTENT_AUDIT_FLAG]
                    ),
                    "audit_notes": unique_nonempty([*current.audit_notes, audit_note]),
                    "audit_peer_merge_group_ids": unique_nonempty(
                        [*current.audit_peer_merge_group_ids, *peer_group_ids]
                    ),
                }
            )

    return [annotated_by_id[group.merge_group_id] for group in merge_groups]


def _assert_model_payload_equal(
    *, actual: BaseModel, artifact_label: str, expected: BaseModel
) -> None:
    """Validate that two Pydantic-style payloads are exactly equivalent.

    Parameters
    ----------
    actual
        Model loaded from an artifact.
    artifact_label
        Human-readable artifact label for error messages.
    expected
        Expected model computed during the current run.

    Raises
    ------
    ValueError
        If the model payloads differ.
    """

    if model_dump_key(actual) != model_dump_key(expected):
        raise ValueError(
            f"{artifact_label} does not match the current planned artifact payload."
        )


def _build_component_review_signals(
    *, component_candidate_ids: set[str], sfi_candidate_registry: SFIRegistryArtifact
) -> list[SFIDedupReviewSignal]:
    """Build explicit candidate-subset signals for one connected review component.

    Internal hashes, bucket keys, warning IDs, segment UUIDs, and source-context keys
    remain implementation details. The prompt receives only human-readable signal
    categories, applicable candidate subsets, and optional source-facing values.

    Parameters
    ----------
    component_candidate_ids
        Candidate IDs in the bounded connected component.
    sfi_candidate_registry
        Registry artifact containing candidates, buckets, and warnings.

    Returns
    -------
    list[SFIDedupReviewSignal]
        Deterministically ordered review signals applying to at least two candidates.
    """

    candidates = [
        candidate
        for candidate in sfi_candidate_registry.candidates
        if candidate.registry_candidate_id in component_candidate_ids
    ]

    signals = [
        *_canonical_value_signals(candidates),
        *_normalized_source_text_signals(candidates),
        *_duplicate_bucket_signals(
            component_candidate_ids=component_candidate_ids,
            sfi_candidate_registry=sfi_candidate_registry,
        ),
        *_registry_warning_signals(
            component_candidate_ids=component_candidate_ids,
            sfi_candidate_registry=sfi_candidate_registry,
        ),
        *_shared_provenance_signals(candidates),
    ]
    signals_by_key: dict[tuple[object, ...], SFIDedupReviewSignal] = {}

    for signal in signals:
        key = (
            tuple(signal.candidate_ids),
            signal.signal_type,
            signal.summary,
            signal.value,
        )
        signals_by_key[key] = signal

    return [signals_by_key[key] for key in sorted(signals_by_key)]


def _build_current_merge_groups(
    *,
    review_responses: Sequence[SFIDedupReviewResponse],
    sfi_candidate_registry: SFIRegistryArtifact,
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
    unresolved_components: Sequence[_ReviewComponent],
) -> tuple[list[SFIMergeGroup], int]:
    """Rebuild complete merge groups from current registry inputs.

    This function is the single deterministic path for converting validated dedup
    responses, unresolved components, and unreviewed registry candidates into final
    SFI merge groups. Reusing this path for both fresh runs and complete-artifact
    validation prevents an old merge report from being accepted merely because its
    candidate IDs still cover the current registry.

    Parameters
    ----------
    review_responses
        Validated LLM dedup review responses to convert into merge groups.
    sfi_candidate_registry
        Current SFI candidate registry that must be covered exactly once.
    sfi_candidates_by_id
        Current registry candidates keyed by temporary registry candidate ID.
    unresolved_components
        Bounded review components that were intentionally not sent to the LLM and must
        become needs-review merge groups.

    Returns
    -------
    tuple[list[SFIMergeGroup], int]
        The complete sorted merge groups and the number of deterministic singleton
        groups created for candidates with no review evidence.
    """

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

    return merge_groups, len(singleton_groups)


def _build_initial_review_edges(
    sfi_candidate_registry: SFIRegistryArtifact,
) -> list[tuple[set[str], set[str]]]:
    """Build initial review edges from controlled values, buckets, warnings, exact
    source-text repeats, and source overlap. This function answers "which candidates
    have enough evidence to be reviewed together?". It does not answer
    "which candidates are duplicates?".

    Examples
    --------

    1. A configured controlled-value repeat creates one review edge containing every
    candidate with the same statement type and canonical controlled value. This is not
    a hard scope or merge decision; it only lets the LLM compare repeated labels and
    alias/punctuation variants using source evidence and runtime instructions.

    2. A code duplicate bucket creates one review edge containing every candidate in
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
    edges.extend(_build_same_canonical_statement_value_edges(sfi_candidate_registry))
    edges.extend(_build_same_normalized_source_text_edges(sfi_candidate_registry))

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
    canonical_code_selection_reason: str | None = None,
    canonical_code_source_candidate_id: str | None = None,
    canonical_type_selection_reason: str | None = None,
    canonical_type_source_candidate_id: str | None = None,
    llm_decision: SFIDedupDecision | None,
    llm_review_set_id: str | None,
    merge_decision: SFIMergeDecision,
    merge_reason: str,
    representative_candidate_id: str | None,
) -> SFIMergeGroup:
    """Build one source-backed SFI merge group from registry candidates.

    The caller supplies the dedup outcome. This constructor deterministically resolves
    canonical type, code type, code value, and compatible scope while preserving all
    observed candidate evidence for audit and finalization.

    Parameters
    ----------
    candidates
        Registry candidates included in this merge group.
    canonical_code_selection_reason
        Source-grounded reason for a mixed-code merge selection, when required.
    canonical_code_source_candidate_id
        Candidate selected as the canonical code source, when required.
    canonical_type_selection_reason
        Source-grounded reason for a mixed-type merge selection, when required.
    canonical_type_source_candidate_id
        Candidate selected as the canonical type source, when required.
    llm_decision
        Original LLM decision for reviewed groups, if any.
    llm_review_set_id
        Review-set ID for LLM-reviewed groups, if any.
    merge_decision
        Final merge decision carried into later stages.
    merge_reason
        Source-grounded or deterministic merge reason.
    representative_candidate_id
        Candidate supplying final source-facing description and language, when mintable.

    Returns
    -------
    SFIMergeGroup
        Merge group preserving source evidence and resolved canonical fields.

    Raises
    ------
    ValueError
        If canonical selections, code types, or configured source scopes are
        inconsistent with the supplied candidates and merge decision.
    """

    sorted_candidates = sorted(
        candidates, key=lambda candidate: candidate.registry_candidate_id
    )
    registry_candidate_ids = [
        candidate.registry_candidate_id for candidate in sorted_candidates
    ]
    confidence_values = [candidate.confidence for candidate in sorted_candidates]
    canonical_statement_value_keys = unique_nonempty(
        candidate.canonical_statement_value_key for candidate in sorted_candidates
    )
    canonical_statement_values = unique_nonempty(
        candidate.canonical_statement_value for candidate in sorted_candidates
    )
    normalized_statement_codes = unique_nonempty(
        candidate.normalized_statement_code for candidate in sorted_candidates
    )
    normalized_statement_types = unique_nonempty(
        candidate.normalized_statement_type for candidate in sorted_candidates
    )
    statement_codes = unique_nonempty(
        candidate.statement_code for candidate in sorted_candidates
    )
    statement_types = unique_nonempty(
        candidate.statement_type for candidate in sorted_candidates
    )
    code_resolution = _resolve_code_resolution(
        candidates=sorted_candidates,
        canonical_code_selection_reason=canonical_code_selection_reason,
        canonical_code_source_candidate_id=canonical_code_source_candidate_id,
        merge_decision=merge_decision,
    )
    type_resolution = _resolve_type_resolution(
        candidates=sorted_candidates,
        canonical_type_selection_reason=canonical_type_selection_reason,
        canonical_type_source_candidate_id=canonical_type_source_candidate_id,
        merge_decision=merge_decision,
    )
    code_scope_key, code_scope_keys, code_scope_values = (
        _resolve_merge_group_code_scope(
            candidates=sorted_candidates,
            code_resolution=code_resolution,
            merge_decision=merge_decision,
        )
    )
    identity_scope_key, identity_scope_keys, identity_scope_values = (
        _resolve_merge_group_identity_scope(
            candidates=sorted_candidates, merge_decision=merge_decision
        )
    )
    digest = hashlib.sha256(
        "|".join(
            str(value)
            for value in [
                merge_decision,
                llm_review_set_id or "",
                code_resolution.canonical_code_source_candidate_id or "",
                type_resolution.canonical_type_source_candidate_id or "",
                representative_candidate_id or "",
                *registry_candidate_ids,
            ]
        ).encode("utf-8")
    ).hexdigest()

    return SFIMergeGroup(
        candidate_descriptions=unique_nonempty(
            candidate.description for candidate in sorted_candidates
        ),
        candidate_source_refs=[
            {
                "applicable_code_type": candidate.applicable_code_type,
                "canonical_statement_value": candidate.canonical_statement_value,
                "canonical_statement_value_key": (
                    candidate.canonical_statement_value_key
                ),
                "code_scope_key": candidate.code_scope_key,
                "code_scope_values": candidate.code_scope_values,
                "identity_scope_key": candidate.identity_scope_key,
                "identity_scope_values": candidate.identity_scope_values,
                "normalized_statement_code": candidate.normalized_statement_code,
                "normalized_statement_type": candidate.normalized_statement_type,
                "registry_candidate_id": candidate.registry_candidate_id,
                "resolved_code_type": candidate.resolved_code_type,
                "source_context_key": candidate.source_context_key,
                "source_context_labels": candidate.source_context_labels,
                "source_segment_ids": candidate.source_segment_ids,
                "statement_code": candidate.statement_code,
                "statement_type": candidate.statement_type,
                "table_header_indexes": candidate.table_header_indexes,
                "table_row_indexes": candidate.table_row_indexes,
                "window_id": candidate.window_id,
                "window_index": candidate.window_index,
            }
            for candidate in sorted_candidates
        ],
        candidate_source_texts=unique_nonempty(
            candidate.source_text for candidate in sorted_candidates
        ),
        canonical_code_source_candidate_id=(
            code_resolution.canonical_code_source_candidate_id
        ),
        canonical_code_type=code_resolution.canonical_code_type,
        canonical_normalized_statement_code=(
            code_resolution.canonical_normalized_statement_code
        ),
        canonical_normalized_statement_type=(
            type_resolution.canonical_normalized_statement_type
        ),
        canonical_statement_code=code_resolution.canonical_statement_code,
        canonical_statement_type=type_resolution.canonical_statement_type,
        canonical_statement_value=(
            canonical_statement_values[0]
            if len(canonical_statement_values) == 1
            else None
        ),
        canonical_statement_value_key=(
            canonical_statement_value_keys[0]
            if len(canonical_statement_value_keys) == 1
            else None
        ),
        canonical_statement_value_keys=canonical_statement_value_keys,
        canonical_statement_values=canonical_statement_values,
        canonical_type_selection_reason=(
            type_resolution.canonical_type_selection_reason
        ),
        canonical_type_source_candidate_id=(
            type_resolution.canonical_type_source_candidate_id
        ),
        code_resolution_method=code_resolution.code_resolution_method,
        code_resolution_reason=code_resolution.code_resolution_reason,
        code_scope_key=code_scope_key,
        code_scope_keys=code_scope_keys,
        code_scope_values=code_scope_values,
        confidence_max=max(confidence_values),
        confidence_min=min(confidence_values),
        identity_scope_key=identity_scope_key,
        identity_scope_keys=identity_scope_keys,
        identity_scope_values=identity_scope_values,
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
        representative_candidate_id=representative_candidate_id,
        source_segment_ids=unique_nonempty(
            source_segment_id
            for candidate in sorted_candidates
            for source_segment_id in candidate.source_segment_ids
        ),
        source_window_ids=unique_nonempty(
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
    merge_reason="Review component was too large or unsafe for bounded LLM
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
                        canonical_code_selection_reason=(
                            decision_group.canonical_code_selection_reason
                        ),
                        canonical_code_source_candidate_id=(
                            decision_group.canonical_code_source_candidate_id
                        ),
                        canonical_type_selection_reason=(
                            decision_group.canonical_type_selection_reason
                        ),
                        canonical_type_source_candidate_id=(
                            decision_group.canonical_type_source_candidate_id
                        ),
                        llm_decision=decision_group.decision,
                        llm_review_set_id=review_response.review_set_id,
                        merge_decision="merged",
                        merge_reason=decision_group.reason,
                        representative_candidate_id=(
                            decision_group.representative_candidate_id
                        ),
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
                            representative_candidate_id=(
                                group_candidate.registry_candidate_id
                            ),
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
                    representative_candidate_id=None,
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
                    "Review component was too large or unsafe for bounded LLM "
                    "dedup review: " + "; ".join(component.review_reasons[:5])
                ),
                representative_candidate_id=None,
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
    sfi_candidate_registry: SFIRegistryArtifact,
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> tuple[list[SFIDedupReviewRequest], list[_ReviewComponent]]:
    """Build compact producer/checker requests and carry unresolved components forward.

    Each request contains compact candidate-local fields, configured code-scope values,
    explicit candidate-subset retrieval signals, and one de-duplicated request-level
    pool of nearby context windows. Internal normalized text, source UUIDs, duplicate
    bucket keys, full provenance, and repeated candidate neighborhoods are not exposed
    to the LLM.

    Parameters
    ----------
    components
        Bounded connected review components.
    kg_config
        Runtime KG configuration carrying dedup instructions and context radius.
    sfi_candidate_registry
        Registry artifact containing shared context windows and review evidence.
    sfi_candidates_by_id
        Registry candidates keyed by temporary candidate ID.

    Returns
    -------
    tuple[list[SFIDedupReviewRequest], list[_ReviewComponent]]
        LLM review requests and components preserved as unresolved without an LLM call.
    """

    review_requests: list[SFIDedupReviewRequest] = []
    unresolved_components: list[_ReviewComponent] = []
    context_window_radius = kg_config.academic_standards.sfi_dedup_context_window_radius

    for component in components:
        if component.needs_review_without_llm:
            unresolved_components.append(component)
            continue

        component_candidates = [
            sfi_candidates_by_id[candidate_id]
            for candidate_id in component.candidate_ids
        ]
        candidate_ids = sorted(
            candidate.registry_candidate_id for candidate in component_candidates
        )
        review_set_id = f"dedupe_review_{hashlib.sha256(
            '|'.join(candidate_ids).encode('utf-8')
        ).hexdigest()[:16]}"
        candidate_context_window_indexes, context_windows = (
            _select_request_context_windows(
                component_candidates=component_candidates,
                context_window_radius=context_window_radius,
                dedup_context_windows=sfi_candidate_registry.dedup_context_windows,
            )
        )
        review_signals = _build_component_review_signals(
            component_candidate_ids=set(candidate_ids),
            sfi_candidate_registry=sfi_candidate_registry,
        )

        if not review_signals:
            review_signals = [
                SFIDedupReviewSignal(
                    candidate_ids=candidate_ids,
                    signal_type="transitive_component_subset",
                    summary=(
                        "These candidates remain in one bounded subset of a connected "
                        "review component. Their retrieval relationship may be "
                        "transitive rather than pairwise."
                    ),
                    value=None,
                )
            ]

        review_requests.append(
            SFIDedupReviewRequest(
                bilingual_pair_policy=(
                    kg_config.academic_standards.bilingual_pair_policy
                ),
                candidates=[
                    SFIDedupReviewCandidate(
                        applicable_code_type=candidate.applicable_code_type,
                        canonical_statement_value=(candidate.canonical_statement_value),
                        code_scope_key=candidate.code_scope_key,
                        code_scope_values=candidate.code_scope_values,
                        context_window_indexes=(
                            candidate_context_window_indexes[
                                candidate.registry_candidate_id
                            ]
                        ),
                        description=candidate.description,
                        identity_scope_key=candidate.identity_scope_key,
                        identity_scope_values=candidate.identity_scope_values,
                        language=candidate.language,
                        normalized_statement_code=(candidate.normalized_statement_code),
                        normalized_statement_type=(candidate.normalized_statement_type),
                        registry_candidate_id=candidate.registry_candidate_id,
                        resolved_code_type=candidate.resolved_code_type,
                        source_text=candidate.source_text,
                        statement_code=candidate.statement_code,
                        statement_type=candidate.statement_type,
                        table_header_indexes=candidate.table_header_indexes,
                        table_row_indexes=candidate.table_row_indexes,
                        window_index=candidate.window_index,
                    )
                    for candidate in component_candidates
                ],
                context_windows=context_windows,
                review_set_id=review_set_id,
                review_signals=review_signals,
                sfi_dedup_instructions=(
                    kg_config.academic_standards.sfi_dedup_instructions
                ),
            )
        )

    return review_requests, unresolved_components


def _build_same_canonical_statement_value_edges(
    sfi_candidate_registry: SFIRegistryArtifact,
) -> list[tuple[set[str], set[str]]]:
    """Build review edges for repeated configured controlled values.

    Controlled-value equality is a retrieval signal only. It helps place punctuation
    variants, alias variants, and repeated visible organizers into bounded LLM review
    neighborhoods without first assigning them an inferred canonical hierarchy scope.
    The LLM must still decide whether the candidates are duplicates, distinct
    same-label organizers, conflicts, or need review using visible source evidence and
    runtime instructions.

    Parameters
    ----------
    sfi_candidate_registry
        SFI candidate registry artifact.

    Returns
    -------
    list[tuple[set[str], set[str]]]
        Candidate-ID sets paired with deterministic controlled-value review reasons.
    """

    edges: list[tuple[set[str], set[str]]] = []
    candidate_ids_by_value_key: dict[tuple[str, str, str, str], list[str]] = (
        defaultdict(list)
    )

    for candidate in sfi_candidate_registry.candidates:
        if not candidate.canonical_statement_value_key:
            continue

        candidate_ids_by_value_key[
            (
                candidate.identity_scope_key or "",
                candidate.normalized_statement_type,
                candidate.statement_type,
                candidate.canonical_statement_value_key,
            )
        ].append(candidate.registry_candidate_id)

    for value_key, candidate_ids_raw in sorted(candidate_ids_by_value_key.items()):
        candidate_ids = set(candidate_ids_raw)

        if len(candidate_ids) < 2:
            continue

        (
            identity_scope_key,
            normalized_statement_type,
            statement_type,
            canonical_value_key,
        ) = value_key
        digest = hashlib.sha256(
            "|".join(
                [
                    identity_scope_key,
                    normalized_statement_type,
                    statement_type,
                    canonical_value_key,
                ]
            ).encode("utf-8")
        ).hexdigest()[:16]

        edges.append(
            (
                candidate_ids,
                {
                    "same_canonical_statement_value:"
                    f"{identity_scope_key}:{statement_type}:"
                    f"{normalized_statement_type}:{digest}"
                },
            )
        )

    return edges


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
    partitioned by configured code scope, source-facing statement type, and normalized
    statement type before an edge is emitted. This prevents unrelated source roles or
    distinct configured code scopes from being sent together solely because their
    visible text matches.

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
    candidate_ids_by_text_key: dict[tuple[str, str, str, str, str], list[str]] = (
        defaultdict(list)
    )

    for candidate in sfi_candidate_registry.candidates:
        normalized_source_text = candidate.normalized_source_text.strip()

        if not normalized_source_text:
            continue

        candidate_ids_by_text_key[
            (
                candidate.code_scope_key or "",
                candidate.identity_scope_key or "",
                candidate.normalized_statement_type,
                candidate.statement_type,
                normalized_source_text,
            )
        ].append(candidate.registry_candidate_id)

    for text_key, candidate_ids_raw in sorted(candidate_ids_by_text_key.items()):
        candidate_ids = set(candidate_ids_raw)

        if len(candidate_ids) < 2:
            continue

        (
            code_scope_key,
            identity_scope_key,
            normalized_statement_type,
            statement_type,
            normalized_source_text,
        ) = text_key
        digest = hashlib.sha256(
            "|".join(
                [
                    code_scope_key,
                    identity_scope_key,
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
                    f"{code_scope_key}:{identity_scope_key}:{statement_type}:"
                    f"{normalized_statement_type}:{digest}"
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
                representative_candidate_id=candidate_id,
            )
        )

    return singleton_groups


def _canonical_value_signals(
    candidates: Sequence[SFIRegistryCandidate],
) -> list[SFIDedupReviewSignal]:
    """Build same-canonical-statement-value signals for the component candidates.

    Parameters
    ----------
    candidates
        Registry candidates belonging to the connected review component.

    Returns
    -------
    list[SFIDedupReviewSignal]
        One signal per canonical value key shared by at least two candidates.
    """

    canonical_groups: dict[tuple[str, str, str, str], list[SFIRegistryCandidate]] = (
        defaultdict(list)
    )

    for candidate in candidates:
        if candidate.canonical_statement_value_key:
            canonical_groups[
                (
                    candidate.identity_scope_key or "",
                    candidate.normalized_statement_type,
                    candidate.statement_type,
                    candidate.canonical_statement_value_key,
                )
            ].append(candidate)

    signals: list[SFIDedupReviewSignal] = []

    for grouped_candidates in canonical_groups.values():
        if len(grouped_candidates) < 2:
            continue

        candidate_ids = sorted(
            candidate.registry_candidate_id for candidate in grouped_candidates
        )
        canonical_values = unique_nonempty(
            candidate.canonical_statement_value for candidate in grouped_candidates
        )
        statement_types = unique_nonempty(
            candidate.statement_type for candidate in grouped_candidates
        )
        value = canonical_values[0] if len(canonical_values) == 1 else None
        statement_type = statement_types[0] if len(statement_types) == 1 else "item"
        signals.append(
            SFIDedupReviewSignal(
                candidate_ids=candidate_ids,
                signal_type="same_canonical_statement_value",
                summary=(
                    f"These {statement_type} candidates map to the same configured "
                    f"canonical statement value."
                ),
                value=value,
            )
        )

    return signals


def _duplicate_bucket_signals(
    *, component_candidate_ids: set[str], sfi_candidate_registry: SFIRegistryArtifact
) -> list[SFIDedupReviewSignal]:
    """Build duplicate-bucket signals restricted to the component candidates.

    Parameters
    ----------
    component_candidate_ids
        Candidate IDs in the bounded connected component.
    sfi_candidate_registry
        Registry artifact containing the duplicate buckets.

    Returns
    -------
    list[SFIDedupReviewSignal]
        One signal per duplicate bucket covering at least two component candidates.
    """

    bucket_signal_types = {
        "code": "same_code_bucket",
        "description_text": "same_description_text_bucket",
        "source_text": "same_source_text_bucket",
    }
    signals: list[SFIDedupReviewSignal] = []

    for bucket in sfi_candidate_registry.duplicate_buckets:
        candidate_ids = sorted(
            component_candidate_ids.intersection(bucket.registry_candidate_ids)
        )

        if len(candidate_ids) < 2:
            continue

        signals.append(
            SFIDedupReviewSignal(
                candidate_ids=candidate_ids,
                signal_type=bucket_signal_types[bucket.bucket_type],
                summary=(
                    f"The registry placed these candidates in the same "
                    f"{bucket.bucket_type.replace('_', ' ')} review bucket "
                    f"({bucket.evidence_strength.replace('_', ' ')})."
                ),
                value=None,
            )
        )

    return signals


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
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
    unresolved_components: Sequence[_ReviewComponent],
) -> SFIMergeReport | None:
    """Load a complete existing merge report, or return None for resume.

    A report is reusable only when the full report, companion JSON artifacts, and
    review JSONL artifacts are all present, parseable, aligned to the current planned
    review requests, rebuilt from the current registry candidate payloads, and cover
    every current registry candidate exactly once.

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
    sfi_candidates_by_id
        Current registry candidates keyed by temporary registry candidate ID.
    unresolved_components
        Current unresolved review components that must become needs-review groups.

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
            sfi_candidates_by_id=sfi_candidates_by_id,
            unresolved_components=unresolved_components,
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


def _normalized_source_text_signals(
    candidates: Sequence[SFIRegistryCandidate],
) -> list[SFIDedupReviewSignal]:
    """Build same-normalized-source-text signals for the component candidates.

    Parameters
    ----------
    candidates
        Registry candidates belonging to the connected review component.

    Returns
    -------
    list[SFIDedupReviewSignal]
        One signal per normalized source text shared by at least two candidates.
    """

    normalized_source_groups: dict[
        tuple[str, str, str, str, str], list[SFIRegistryCandidate]
    ] = defaultdict(list)

    for candidate in candidates:
        if candidate.normalized_source_text:
            normalized_source_groups[
                (
                    candidate.code_scope_key or "",
                    candidate.identity_scope_key or "",
                    candidate.normalized_statement_type,
                    candidate.statement_type,
                    candidate.normalized_source_text,
                )
            ].append(candidate)

    signals: list[SFIDedupReviewSignal] = []

    for grouped_candidates in normalized_source_groups.values():
        if len(grouped_candidates) < 2:
            continue

        signals.append(
            SFIDedupReviewSignal(
                candidate_ids=sorted(
                    candidate.registry_candidate_id for candidate in grouped_candidates
                ),
                signal_type="same_normalized_source_text",
                summary=(
                    "These candidates have identical internally normalized source "
                    "text. The original source_text fields remain authoritative."
                ),
                value=None,
            )
        )

    return signals


def _registry_warning_signals(
    *, component_candidate_ids: set[str], sfi_candidate_registry: SFIRegistryArtifact
) -> list[SFIDedupReviewSignal]:
    """Build registry-warning signals restricted to the component candidates.

    Parameters
    ----------
    component_candidate_ids
        Candidate IDs in the bounded connected component.
    sfi_candidate_registry
        Registry artifact containing the warnings.

    Returns
    -------
    list[SFIDedupReviewSignal]
        One signal per warning covering at least two component candidates.
    """

    signals: list[SFIDedupReviewSignal] = []

    for warning in sfi_candidate_registry.warnings:
        candidate_ids = sorted(
            component_candidate_ids.intersection(warning.registry_candidate_ids)
        )

        if len(candidate_ids) < 2:
            continue

        signals.append(
            SFIDedupReviewSignal(
                candidate_ids=candidate_ids,
                signal_type="registry_warning",
                summary=warning.message,
                value=warning.warning_type,
            )
        )

    return signals


def _resolve_code_resolution(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    canonical_code_selection_reason: str | None,
    canonical_code_source_candidate_id: str | None,
    merge_decision: SFIMergeDecision,
) -> _CodeResolution:
    """Resolve one merge group's canonical statement code deterministically.

    Zero-code and single-code groups resolve without LLM selection. A merged group with
    multiple distinct normalized source codes must identify one coded source candidate
    selected by the reviewed dedup decision. Conflict and needs-review groups preserve
    multiple codes without selecting a canonical one.

    Parameters
    ----------
    candidates
        Registry candidates included in the merge group.
    canonical_code_selection_reason
        Source-grounded LLM reason for a mixed-code merge selection, when required.
    canonical_code_source_candidate_id
        Candidate selected by the LLM as the canonical code source, when required.
    merge_decision
        Final merge decision for the group.

    Returns
    -------
    _CodeResolution
        Canonical-code resolution details for the merge group.

    Raises
    ------
    ValueError
        If source code fields are internally inconsistent or a mixed-code merged group
        lacks a valid coded source-candidate selection.
    """

    sorted_candidates = sorted(
        candidates, key=lambda candidate: candidate.registry_candidate_id
    )
    normalized_codes = unique_nonempty(
        candidate.normalized_statement_code for candidate in sorted_candidates
    )
    statement_codes = unique_nonempty(
        candidate.statement_code for candidate in sorted_candidates
    )

    if not normalized_codes:
        return _resolve_no_source_code(
            canonical_code_selection_reason=canonical_code_selection_reason,
            canonical_code_source_candidate_id=canonical_code_source_candidate_id,
            statement_codes=statement_codes,
        )

    if len(normalized_codes) == 1:
        return _resolve_single_source_code(
            canonical_code_selection_reason=canonical_code_selection_reason,
            canonical_code_source_candidate_id=canonical_code_source_candidate_id,
            normalized_code=normalized_codes[0],
            sorted_candidates=sorted_candidates,
        )

    if merge_decision != "merged":
        return _resolve_unresolved_multiple_source_codes(
            canonical_code_selection_reason=canonical_code_selection_reason,
            canonical_code_source_candidate_id=canonical_code_source_candidate_id,
        )

    return _resolve_review_selected_source_code(
        canonical_code_selection_reason=canonical_code_selection_reason,
        canonical_code_source_candidate_id=canonical_code_source_candidate_id,
        normalized_codes=normalized_codes,
        sorted_candidates=sorted_candidates,
    )


def _resolve_max_dedup_review_set_candidates(
    *, candidate_count: int, kg_config: CreateKGConfig
) -> int:
    """Resolve the maximum LLM dedup review-set size for one component.

    When `kg_config.academic_standards.max_dedup_review_set_candidates` is None, the
    effective maximum is the full length of the current connected candidate set. This
    disables size-based chunking for that set while preserving all other dedup
    safeguards.

    Parameters
    ----------
    candidate_count
        Number of candidates in the connected component being bounded.
    kg_config
        Runtime KG configuration carrying Academic Standards dedup settings.

    Returns
    -------
    int
        Effective maximum number of candidates allowed in one LLM review request.

    Raises
    ------
    ValueError
        If `candidate_count` is negative or the configured maximum is less than 2.
    """

    if candidate_count < 0:
        raise ValueError(
            f"Candidate count for SFI dedup review cannot be negative: "
            f"{candidate_count}."
        )

    configured_max = kg_config.academic_standards.max_dedup_review_set_candidates

    if configured_max is None:
        return candidate_count

    if configured_max < 2:
        raise ValueError(
            f"Academic Standards max_dedup_review_set_candidates must be null or "
            f"at least 2; got {configured_max}."
        )

    return configured_max


def _resolve_merge_group_code_scope(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    code_resolution: _CodeResolution,
    merge_decision: SFIMergeDecision,
) -> tuple[str | None, list[str], dict[str, str]]:
    """Resolve aggregate source-backed code scope for one merge group.

    Mintable coded groups require every source candidate to carry the canonical
    applicable code type and the same resolved scope. This permits coded and uncoded
    duplicate occurrences to merge when the uncoded occurrence independently resolves
    the same configured scope, while rejecting contradictory or unresolved scope.

    Parameters
    ----------
    candidates
        Registry candidates included in the merge group.
    code_resolution
        Canonical code resolution for the merge group.
    merge_decision
        Final merge decision for the group.

    Returns
    -------
    tuple[str | None, list[str], dict[str, str]]
        Resolved common scope key, all non-empty observed scope keys, and the common
        canonical scope-value mapping.

    Raises
    ------
    ValueError
        If a mintable coded group contains incompatible code types, contradictory
        scopes, or unresolved source scope on any candidate.
    """

    code_scope_keys = unique_nonempty(
        candidate.code_scope_key for candidate in candidates
    )
    source_scope_signatures = {
        candidate.code_scope_key or "" for candidate in candidates
    }
    is_mintable_coded = bool(
        merge_decision in {"merged", "singleton"}
        and code_resolution.canonical_normalized_statement_code
    )

    if is_mintable_coded:
        canonical_code_type = code_resolution.canonical_code_type

        if canonical_code_type is None:
            raise ValueError(
                "A mintable coded merge group requires a canonical code type."
            )

        applicable_code_types = {
            candidate.applicable_code_type for candidate in candidates
        }

        if applicable_code_types != {canonical_code_type}:
            raise ValueError(
                "Every candidate in a mintable coded merge group must have the same "
                "applicable code type as the canonical source code."
            )

        if len(source_scope_signatures) != 1:
            raise ValueError(
                "Every candidate in a mintable coded merge group must preserve one "
                "common configured code scope. Unresolved or contradictory scope "
                "requires needs_review or conflict."
            )

    code_scope_key = (
        next(iter(source_scope_signatures)) or None
        if len(source_scope_signatures) == 1
        else None
    )

    if code_scope_key is None:
        return None, code_scope_keys, {}

    matching_scope_values = {
        tuple(candidate.code_scope_values.items())
        for candidate in candidates
        if candidate.code_scope_key == code_scope_key
    }

    if len(matching_scope_values) != 1:
        raise ValueError(
            "Candidates sharing one code_scope_key must preserve identical "
            "code_scope_values."
        )

    return code_scope_key, code_scope_keys, dict(next(iter(matching_scope_values)))


def _resolve_merge_group_identity_scope(
    *, candidates: Sequence[SFIRegistryCandidate], merge_decision: SFIMergeDecision
) -> tuple[str | None, list[str], dict[str, str]]:
    """Resolve one compatible semantic identity scope for a merge group.

    Parameters
    ----------
    candidates
        Registry candidates included in the merge group.
    merge_decision
        Final merge decision for the group.

    Returns
    -------
    tuple[str | None, list[str], dict[str, str]]
        Shared identity-scope key, all non-empty observed keys, and shared canonical
        identity-scope values.

    Raises
    ------
    ValueError
        If a mintable group combines different identity scopes or one scope key is
        associated with contradictory source-backed values.
    """

    identity_scope_keys = unique_nonempty(
        candidate.identity_scope_key for candidate in candidates
    )
    source_scope_signatures = {
        candidate.identity_scope_key or "" for candidate in candidates
    }

    if merge_decision in {"merged", "singleton"} and len(source_scope_signatures) != 1:
        raise ValueError(
            "Every candidate in a mintable merge group must preserve one common "
            "configured semantic identity scope."
        )

    identity_scope_key = (
        next(iter(source_scope_signatures)) or None
        if len(source_scope_signatures) == 1
        else None
    )

    if identity_scope_key is None:
        return None, identity_scope_keys, {}

    matching_scope_values = {
        tuple(candidate.identity_scope_values.items())
        for candidate in candidates
        if candidate.identity_scope_key == identity_scope_key
    }

    if len(matching_scope_values) != 1:
        raise ValueError(
            "Candidates sharing one identity_scope_key must preserve identical "
            "identity_scope_values."
        )

    return (
        identity_scope_key,
        identity_scope_keys,
        dict(next(iter(matching_scope_values))),
    )


def _resolve_no_source_code(
    *,
    canonical_code_selection_reason: str | None,
    canonical_code_source_candidate_id: str | None,
    statement_codes: Sequence[str],
) -> _CodeResolution:
    """Resolve a merge group whose candidates expose no normalized source code.

    Parameters
    ----------
    canonical_code_selection_reason
        Source-grounded LLM reason for a mixed-code merge selection; must be absent.
    canonical_code_source_candidate_id
        Candidate selected as the canonical code source; must be absent.
    statement_codes
        Distinct non-empty statement codes preserved across the group.

    Returns
    -------
    _CodeResolution
        No-source-code resolution for the merge group.

    Raises
    ------
    ValueError
        If statement codes exist without normalized codes, or canonical-code selection
        fields are set.
    """

    if statement_codes:
        raise ValueError(
            "SFI candidates expose statement_code values without corresponding "
            "normalized_statement_code values."
        )

    if (
        canonical_code_selection_reason is not None
        or canonical_code_source_candidate_id is not None
    ):
        raise ValueError(
            "No-code merge groups must not define canonical-code selection fields."
        )

    return _CodeResolution(
        canonical_code_source_candidate_id=None,
        canonical_code_type=None,
        canonical_normalized_statement_code=None,
        canonical_statement_code=None,
        code_resolution_method="no_source_code",
        code_resolution_reason=(
            "No source candidate in the merge group has a normalized statement code."
        ),
    )


def _resolve_review_selected_source_code(
    *,
    canonical_code_selection_reason: str | None,
    canonical_code_source_candidate_id: str | None,
    normalized_codes: Sequence[str],
    sorted_candidates: Sequence[SFIRegistryCandidate],
) -> _CodeResolution:
    """Resolve a merged group with multiple distinct normalized source codes.

    Such a group must identify one coded source candidate selected by the reviewed
    dedup decision, backed by a source-grounded reason.

    Parameters
    ----------
    canonical_code_selection_reason
        Source-grounded LLM reason for the mixed-code merge selection.
    canonical_code_source_candidate_id
        Candidate selected by the LLM as the canonical code source.
    normalized_codes
        Distinct non-empty normalized statement codes preserved across the group.
    sorted_candidates
        Registry candidates in the merge group, ordered by registry candidate ID.

    Returns
    -------
    _CodeResolution
        Review-selected-source-code resolution for the merge group.

    Raises
    ------
    ValueError
        If the required selection fields are missing, the selected candidate is not in
        the group or has no source-backed code, or its normalized code is not preserved
        among the group's source codes.
    """

    if canonical_code_source_candidate_id is None:
        raise ValueError(
            "A merged group with multiple distinct normalized source codes requires "
            "canonical_code_source_candidate_id."
        )

    if canonical_code_selection_reason is None:
        raise ValueError(
            "A merged group with multiple distinct normalized source codes requires "
            "canonical_code_selection_reason."
        )

    candidates_by_id = {
        candidate.registry_candidate_id: candidate for candidate in sorted_candidates
    }
    canonical_candidate = candidates_by_id.get(canonical_code_source_candidate_id)

    if canonical_candidate is None:
        raise ValueError(
            f"Canonical code source candidate "
            f"{canonical_code_source_candidate_id!r} is not in the merge group."
        )

    if (
        canonical_candidate.statement_code is None
        or canonical_candidate.normalized_statement_code is None
        or canonical_candidate.resolved_code_type is None
    ):
        raise ValueError(
            f"Canonical code source candidate "
            f"{canonical_code_source_candidate_id!r} has no fully resolved "
            f"source-backed code."
        )

    if canonical_candidate.normalized_statement_code not in normalized_codes:
        raise ValueError(
            "Selected canonical normalized statement code is not preserved among the "
            "merge group's source codes."
        )

    return _CodeResolution(
        canonical_code_source_candidate_id=canonical_code_source_candidate_id,
        canonical_code_type=canonical_candidate.resolved_code_type,
        canonical_normalized_statement_code=(
            canonical_candidate.normalized_statement_code
        ),
        canonical_statement_code=canonical_candidate.statement_code,
        code_resolution_method="review_selected_source_code",
        code_resolution_reason=canonical_code_selection_reason,
    )


def _resolve_single_source_code(
    *,
    canonical_code_selection_reason: str | None,
    canonical_code_source_candidate_id: str | None,
    normalized_code: str,
    sorted_candidates: Sequence[SFIRegistryCandidate],
) -> _CodeResolution:
    """Resolve a merge group that shares a single normalized source code.

    Parameters
    ----------
    canonical_code_selection_reason
        Source-grounded LLM reason for a mixed-code merge selection; must be absent.
    canonical_code_source_candidate_id
        Candidate selected as the canonical code source; must be absent.
    normalized_code
        The single distinct normalized statement code shared by the group.
    sorted_candidates
        Registry candidates in the merge group, ordered by registry candidate ID.

    Returns
    -------
    _CodeResolution
        Single-source-code resolution for the merge group.

    Raises
    ------
    ValueError
        If canonical-code selection fields are set, or the normalized code lacks a
        source-backed statement-code candidate.
    """

    if (
        canonical_code_selection_reason is not None
        or canonical_code_source_candidate_id is not None
    ):
        raise ValueError(
            "Single-code merge groups must not define canonical-code selection fields."
        )

    canonical_candidate = next(
        (
            candidate
            for candidate in sorted_candidates
            if candidate.normalized_statement_code == normalized_code
            and candidate.statement_code
        ),
        None,
    )

    if canonical_candidate is None:
        raise ValueError(
            "A normalized source code exists without a source-backed statement "
            "code candidate."
        )

    if canonical_candidate.resolved_code_type is None:
        raise ValueError(
            "A source-backed normalized code exists without a resolved code type."
        )

    return _CodeResolution(
        canonical_code_source_candidate_id=None,
        canonical_code_type=canonical_candidate.resolved_code_type,
        canonical_normalized_statement_code=(
            canonical_candidate.normalized_statement_code
        ),
        canonical_statement_code=canonical_candidate.statement_code,
        code_resolution_method="single_source_code",
        code_resolution_reason=(
            "All coded source candidates in the merge group resolve to one "
            "normalized statement code."
        ),
    )


def _resolve_type_resolution(
    *,
    candidates: Sequence[SFIRegistryCandidate],
    canonical_type_selection_reason: str | None,
    canonical_type_source_candidate_id: str | None,
    merge_decision: SFIMergeDecision,
) -> _TypeResolution:
    """Resolve canonical statement type for one merge group.

    A single observed statement-type pair resolves deterministically. A merged group
    with multiple observed pairs must select one existing candidate as the canonical
    type source. Non-mintable groups preserve conflicting type evidence without
    asserting a canonical pair.

    Parameters
    ----------
    candidates
        Registry candidates included in the merge group.
    canonical_type_selection_reason
        Source-grounded reason for a mixed-type merge selection, when required.
    canonical_type_source_candidate_id
        Candidate selected as the canonical type source, when required.
    merge_decision
        Final merge decision for the group.

    Returns
    -------
    _TypeResolution
        Canonical statement-type resolution details.

    Raises
    ------
    ValueError
        If selection fields are unexpected, incomplete, out of group, or inconsistent
        with the selected candidate's existing type pair.
    """

    sorted_candidates = sorted(
        candidates, key=lambda candidate: candidate.registry_candidate_id
    )
    type_pairs = {
        (candidate.statement_type, candidate.normalized_statement_type)
        for candidate in sorted_candidates
    }

    if merge_decision not in {"merged", "singleton"}:
        if (
            canonical_type_selection_reason is not None
            or canonical_type_source_candidate_id is not None
        ):
            raise ValueError(
                "Conflict and needs-review groups must not select a canonical "
                "statement type."
            )

        return _TypeResolution(
            canonical_normalized_statement_type=None,
            canonical_statement_type=None,
            canonical_type_selection_reason=None,
            canonical_type_source_candidate_id=None,
        )

    if len(type_pairs) == 1:
        if (
            canonical_type_selection_reason is not None
            or canonical_type_source_candidate_id is not None
        ):
            raise ValueError(
                "Single-type merge groups must not define canonical type selection "
                "fields."
            )

        statement_type, normalized_statement_type = next(iter(type_pairs))
        return _TypeResolution(
            canonical_normalized_statement_type=normalized_statement_type,
            canonical_statement_type=statement_type,
            canonical_type_selection_reason=None,
            canonical_type_source_candidate_id=None,
        )

    if merge_decision != "merged":
        raise ValueError(
            "A singleton merge group cannot contain multiple statement-type pairs."
        )

    if canonical_type_source_candidate_id is None:
        raise ValueError(
            "A mixed-type merged group requires " "canonical_type_source_candidate_id."
        )

    if canonical_type_selection_reason is None:
        raise ValueError(
            "A mixed-type merged group requires canonical_type_selection_reason."
        )

    candidates_by_id = {
        candidate.registry_candidate_id: candidate for candidate in sorted_candidates
    }
    canonical_candidate = candidates_by_id.get(canonical_type_source_candidate_id)

    if canonical_candidate is None:
        raise ValueError(
            f"Canonical type source candidate "
            f"{canonical_type_source_candidate_id!r} is not in the merge group."
        )

    return _TypeResolution(
        canonical_normalized_statement_type=(
            canonical_candidate.normalized_statement_type
        ),
        canonical_statement_type=canonical_candidate.statement_type,
        canonical_type_selection_reason=canonical_type_selection_reason,
        canonical_type_source_candidate_id=canonical_type_source_candidate_id,
    )


def _resolve_unresolved_multiple_source_codes(
    *,
    canonical_code_selection_reason: str | None,
    canonical_code_source_candidate_id: str | None,
) -> _CodeResolution:
    """Resolve a non-merged group that preserves multiple normalized source codes.

    Conflict and needs-review groups keep every distinct code without selecting a
    canonical one.

    Parameters
    ----------
    canonical_code_selection_reason
        Source-grounded LLM reason for a mixed-code merge selection; must be absent.
    canonical_code_source_candidate_id
        Candidate selected as the canonical code source; must be absent.

    Returns
    -------
    _CodeResolution
        Unresolved-multiple-source-codes resolution for the merge group.

    Raises
    ------
    ValueError
        If canonical-code selection fields are set.
    """

    if (
        canonical_code_selection_reason is not None
        or canonical_code_source_candidate_id is not None
    ):
        raise ValueError(
            "Conflict and needs-review groups must not select a canonical code "
            "source candidate."
        )

    return _CodeResolution(
        canonical_code_source_candidate_id=None,
        canonical_code_type=None,
        canonical_normalized_statement_code=None,
        canonical_statement_code=None,
        code_resolution_method="unresolved_multiple_source_codes",
        code_resolution_reason=(
            "The group preserves multiple distinct normalized source codes and is "
            "not eligible for canonical-code selection."
        ),
    )


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
        append_jsonl_model(fp=review_requests_fp, model=review_request)

    for review_response in completed_review_responses:
        append_jsonl_model(fp=review_responses_fp, model=review_response)


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
            f"candidate_set_length={len(review_request.candidates)}."
        )

        append_jsonl_model(fp=review_requests_fp, model=review_request)

        review_response = review_sfi_dedup_set(
            review_request=review_request, usage_tracker=usage_tracker
        )

        append_jsonl_model(fp=review_responses_fp, model=review_response)

        review_responses.append(review_response)

    return review_responses


def _select_request_context_windows(
    *,
    component_candidates: Sequence[SFIRegistryCandidate],
    context_window_radius: int,
    dedup_context_windows: Sequence[SFIDedupContextWindow],
) -> tuple[dict[str, list[int]], list[SFIDedupContextWindow]]:
    """Select shared compact context windows for one dedup request.

    Parameters
    ----------
    component_candidates
        Registry candidates included in the review request.
    context_window_radius
        Number of windows before and after each candidate window to include.
    dedup_context_windows
        Shared compact registry-level source-window pool.

    Returns
    -------
    tuple[dict[str, list[int]], list[SFIDedupContextWindow]]
        Candidate-to-window references and the de-duplicated request-level windows.
    """

    windows_by_index = {
        context_window.window_index: context_window
        for context_window in dedup_context_windows
    }
    candidate_window_indexes: dict[str, list[int]] = {}
    selected_window_indexes: set[int] = set()

    for candidate in component_candidates:
        context_window_indexes = [
            window_index
            for window_index in range(
                candidate.window_index - context_window_radius,
                candidate.window_index + context_window_radius + 1,
            )
            if window_index in windows_by_index
        ]
        candidate_window_indexes[candidate.registry_candidate_id] = (
            context_window_indexes
        )
        selected_window_indexes.update(context_window_indexes)

    selected_context_windows = [
        windows_by_index[window_index]
        for window_index in sorted(selected_window_indexes)
    ]
    return candidate_window_indexes, selected_context_windows


def _shared_provenance_signals(
    candidates: Sequence[SFIRegistryCandidate],
) -> list[SFIDedupReviewSignal]:
    """Build shared source-table row/header provenance signals.

    Parameters
    ----------
    candidates
        Registry candidates belonging to the connected review component.

    Returns
    -------
    list[SFIDedupReviewSignal]
        One deterministically ordered signal per shared table row or header cited by at
        least two candidates within a single source table segment.
    """

    provenance_groups: dict[tuple[str, str, str, int], list[str]] = defaultdict(list)

    for candidate in candidates:
        for source_segment_id in candidate.source_segment_ids:
            for row_index in candidate.table_row_indexes:
                provenance_groups[
                    (
                        "shared_table_row",
                        candidate.statement_type,
                        source_segment_id,
                        row_index,
                    )
                ].append(candidate.registry_candidate_id)

            for header_index in candidate.table_header_indexes:
                provenance_groups[
                    (
                        "shared_table_header",
                        candidate.statement_type,
                        source_segment_id,
                        header_index,
                    )
                ].append(candidate.registry_candidate_id)

    signals: list[SFIDedupReviewSignal] = []

    for (
        signal_type,
        _statement_type,
        _source_segment_id,
        source_index,
    ), candidate_ids_raw in sorted(provenance_groups.items()):
        candidate_ids = sorted(set(candidate_ids_raw))

        if len(candidate_ids) < 2:
            continue

        source_label = "row" if signal_type == "shared_table_row" else "header"
        signals.append(
            SFIDedupReviewSignal(
                candidate_ids=candidate_ids,
                signal_type=signal_type,
                summary=(
                    f"These candidates cite the same source table {source_label} "
                    f"within one source table segment."
                ),
                value=f"{source_label}_index={source_index}",
            )
        )

    return signals


def _split_and_bound_components(
    *,
    components: Sequence[_ReviewComponent],
    kg_config: CreateKGConfig,
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
) -> list[_ReviewComponent]:
    """Split connected components into safe bounded review components.

    After `_merge_edges_to_components()`, a component can become large because
    duplicate buckets, warnings, and provenance edges can chain together. This function
    keeps small components as-is. Oversized components are split only by conservative
    source-derived context; it does not arbitrarily chunk a still-coherent split group
    into independent LLM review sets.

    The effective maximum comes from
    `kg_config.academic_standards.max_dedup_review_set_candidates`. When that value is
    null, each connected component uses its own full candidate count as the maximum, so
    size-based bounding is disabled for that component.

    Oversized components are split by this conservative retrieval key:

    (
        candidate.statement_type,
        candidate.code_bucket_key
        or candidate.normalized_statement_code
        or candidate.canonical_statement_value_key
        or candidate.source_context_key,
    )

    The key uses configured code scope when a scoped code bucket exists, while still
    avoiding inferred hierarchy and arbitrary source-order window bands. If a split
    group remains too large, it is marked needs-review rather than chunked into
    independent review sets that could under-merge duplicates across chunk boundaries.

    The split is a retrieval-safety step, not a merge decision. Each resulting split
    group is handled as follows:

    1. If it contains one candidate, it is marked `needs_review_without_llm=True` so a
        candidate with real review evidence does not silently become an ordinary
        unreviewed singleton.
    2. If it contains at least two candidates and is no larger than the effective max,
        it is sent to the LLM as one bounded review component.
    3. If it still contains more candidates than the effective max, the whole split
        group is marked `needs_review_without_llm=True`. This avoids unreconciled
        arbitrary chunking, where candidates split across independent chunks could
        require merging but would never be compared.

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
        candidate.code_bucket_key
        or candidate.normalized_statement_code
        or candidate.canonical_statement_value_key
        or candidate.source_context_key,
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

    3. A still-oversized split group is not arbitrarily chunked. For example, a
    25-candidate split group with a maximum size of 12 is preserved as one unresolved
    needs-review component:

    _ReviewComponent(
        candidate_ids=("candidate_001", "...", "candidate_025"),
        needs_review_without_llm=True,
        review_reasons=(
            "duplicate_bucket:source_text:bucket_xyz:weak_signal",
            "oversized_split_group_needs_review_without_chunking:"
            "('Objectif spécifique', '', ('segment_9',), 22)",
        ),
    )

    This is intentionally conservative: without a cross-chunk reconciliation pass,
    independent chunks could under-deduplicate candidates that should be merged across
    chunk boundaries.

    Parameters
    ----------
    components
        Connected components built from review edges.
    kg_config
        Runtime KG configuration carrying the dedup review-set size limit.
    sfi_candidates_by_id
        Lookup of registry candidates by temporary registry candidate ID.

    Returns
    -------
    list[_ReviewComponent]
        Bounded review components. Safe-size groups are sent to the LLM; singleton
        split residues and still-oversized split groups are marked for needs-review
        without an LLM call.
    """

    bounded_components: list[_ReviewComponent] = []

    for component in components:
        max_dedup_review_set_candidates = _resolve_max_dedup_review_set_candidates(
            candidate_count=len(component.candidate_ids), kg_config=kg_config
        )

        if len(component.candidate_ids) <= max_dedup_review_set_candidates:
            bounded_components.append(component)
            continue

        split_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)

        for candidate_id in component.candidate_ids:
            candidate = sfi_candidates_by_id[candidate_id]
            split_groups[
                (
                    candidate.statement_type,
                    candidate.code_bucket_key
                    or candidate.normalized_statement_code
                    or candidate.canonical_statement_value_key
                    or candidate.source_context_key,
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

            if len(split_candidate_ids_sorted) > max_dedup_review_set_candidates:
                bounded_components.append(
                    _ReviewComponent(
                        candidate_ids=tuple(split_candidate_ids_sorted),
                        needs_review_without_llm=True,
                        review_reasons=tuple(
                            sorted(
                                set(component.review_reasons)
                                | {
                                    "oversized_split_group_needs_review_without_chunking:"
                                    + repr(split_key)
                                }
                            )
                        ),
                    )
                )
                continue

            bounded_components.append(
                _ReviewComponent(
                    candidate_ids=tuple(split_candidate_ids_sorted),
                    needs_review_without_llm=False,
                    review_reasons=tuple(
                        sorted(set(component.review_reasons) | {split_reason})
                    ),
                )
            )

    return bounded_components


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
    sfi_candidates_by_id: dict[str, SFIRegistryCandidate],
    unresolved_components: Sequence[_ReviewComponent],
) -> None:
    """Validate that all persisted dedup artifacts are complete and current.

    Complete-artifact reuse is allowed only when the saved report can be rebuilt
    exactly from the current registry candidate payloads, the current unresolved
    components, and the saved validated review responses. Candidate-ID coverage alone
    is not sufficient because registry candidate IDs can remain stable while source
    text, descriptions, source references, confidence values, or audit flags change.

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
    sfi_candidates_by_id
        Current registry candidates keyed by temporary registry candidate ID.
    unresolved_components
        Current unresolved review components that must become needs-review groups.

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
    assert_model_sequences_equal(
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

    expected_merge_groups, expected_singleton_count = _build_current_merge_groups(
        review_responses=merge_report.review_responses,
        sfi_candidate_registry=sfi_candidate_registry,
        sfi_candidates_by_id=sfi_candidates_by_id,
        unresolved_components=unresolved_components,
    )
    expected_merge_report = _build_merge_report(
        merge_groups=expected_merge_groups,
        review_requests=planned_review_requests,
        review_responses=merge_report.review_responses,
        sfi_candidate_registry=sfi_candidate_registry,
        unreviewed_singleton_count=expected_singleton_count,
    )

    _assert_model_payload_equal(
        actual=merge_report,
        artifact_label="sfi_merge_report.json",
        expected=expected_merge_report,
    )
    assert_model_sequences_equal(
        actual=_load_merge_groups_file(merge_groups_fp),
        artifact_label="sfi_merge_groups.json",
        expected=expected_merge_report.merge_groups,
    )
    assert_model_sequences_equal(
        actual=_load_merge_groups_file(conflicts_fp),
        artifact_label="sfi_merge_conflicts.json",
        expected=expected_merge_report.conflict_groups,
    )
    assert_model_sequences_equal(
        actual=_load_merge_groups_file(needs_review_fp),
        artifact_label="sfi_merge_needs_review.json",
        expected=expected_merge_report.needs_review_groups,
    )
    assert_model_sequences_equal(
        actual=_load_jsonl_review_requests(review_requests_fp),
        artifact_label="sfi_dedup_review_requests.jsonl",
        expected=planned_review_requests,
    )
    assert_model_sequences_equal(
        actual=_load_jsonl_review_responses(
            allow_partial_prefix=False, review_responses_fp=review_responses_fp
        ),
        artifact_label="sfi_dedup_review_responses.jsonl",
        expected=expected_merge_report.review_responses,
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

    assert_model_sequences_equal(
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
            verify_sfi_dedup_review_integrity(
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
        components=connected_components,
        kg_config=kg_config,
        sfi_candidates_by_id=sfi_candidates_by_id,
    )
    review_requests, unresolved_components = _build_review_requests(
        components=bounded_components,
        kg_config=kg_config,
        sfi_candidate_registry=sfi_candidate_registry,
        sfi_candidates_by_id=sfi_candidates_by_id,
    )

    if overwrite:
        reset_output_files(
            output_fps=[
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
            sfi_candidates_by_id=sfi_candidates_by_id,
            unresolved_components=unresolved_components,
        )

        if existing_merge_report is not None:
            return existing_merge_report

        completed_review_responses = _load_resumable_review_progress(
            merge_report_fp=merge_report_fp,
            planned_review_requests=review_requests,
            review_requests_fp=review_requests_fp,
            review_responses_fp=review_responses_fp,
        )
        reset_output_files(
            output_fps=[
                conflicts_fp,
                merge_groups_fp,
                merge_report_fp,
                needs_review_fp,
            ]
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
    merge_groups, unreviewed_singleton_count = _build_current_merge_groups(
        review_responses=review_responses,
        sfi_candidate_registry=sfi_candidate_registry,
        sfi_candidates_by_id=sfi_candidates_by_id,
        unresolved_components=unresolved_components,
    )
    merge_report = _build_merge_report(
        merge_groups=merge_groups,
        review_requests=review_requests,
        review_responses=review_responses,
        sfi_candidate_registry=sfi_candidate_registry,
        unreviewed_singleton_count=unreviewed_singleton_count,
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
