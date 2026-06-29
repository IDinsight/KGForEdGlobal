"""This module contains functionalities for resolving finalized Academic Standards SFI
hasChild relationships.

This module consumes finalized SFI records, recovers source context, builds bounded
source-grounded parent candidate sets, asks the LLM to choose direct hasChild parents
from those bounded sets, validates the resulting graph, and persists
relationship-resolution artifacts.
"""

# Standard Library
import hashlib
import json
import re
import unicodedata
import uuid

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import BlockSegment, DocumentIR, TableSegment
from skg.kgs.llm import KGUsageTracker, resolve_sfi_has_child_parent_request
from skg.kgs.schemas import (
    ExtractionWindow,
    SFIFinalRecord,
    SFIFinalSummary,
    SFIHasChildCandidateParentSet,
    SFIHasChildEdge,
    SFIHasChildFinalContext,
    SFIHasChildParentCandidate,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
    SFIHasChildResolutionSummary,
)
from skg.kgs.utils import KGDirs, normalize_code, unique_nonempty
from skg.kgs.validators import verify_sfi_has_child_resolution_quality
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, open_json_type, write_to_json

_ROOT_EVIDENCE_REASON = "root_fallback"


@dataclass
class _ParentEvidence:
    """Mutable parent-candidate evidence before schema conversion."""

    candidate: SFIHasChildParentCandidate
    evidence_reasons: set[str] = field(init=False)
    evidence_summary: list[str] = field(init=False)

    def __post_init__(self) -> None:
        """Assign mutable evidence fields from the candidate's immutable fields."""

        self.evidence_reasons = set(self.candidate.evidence_reasons)
        self.evidence_summary = list(self.candidate.evidence_summary)


def _add_parent_evidence(
    *,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    evidence_reason: str,
    evidence_summary: str,
    parent_context: SFIHasChildFinalContext,
) -> None:
    """Add or update one non-root SFI parent candidate evidence record.

    Examples
    --------

    1. A parent candidate is created the first time evidence is added for its endpoint.

    Suppose `topic_context` represents the finalized SFI:

    final_sfi_uuid=<topic-uuid>
    description="1.1 CONVERSATION"
    normalized_statement_type="Standard Grouping"
    statement_type="Topic"
    normalized_statement_code="1.1"
    statement_code="1.1"

    And the evidence accumulator is initially empty:

    evidence_by_endpoint_id = {}

    Calling:

    _add_parent_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        evidence_reason="same_table_context",
        evidence_summary="Child and parent share cited table row/header context.",
        parent_context=topic_context,
    )

    creates one `_ParentEvidence` entry keyed by the parent endpoint UUID. The stored
    candidate has:

    endpoint_id=str(topic_context.final_sfi_uuid)
    endpoint_kind="StandardsFrameworkItem"
    description="1.1 CONVERSATION"
    evidence_reasons=["same_table_context"]
    evidence_summary=[
        "Child and parent share cited table row/header context."
    ]

    Adding more evidence for the same parent updates the existing entry instead of
    creating a duplicate parent candidate.

    For example, a later evidence channel may call:

    _add_parent_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        evidence_reason="code_parent_hint",
        evidence_summary=(
            "Configured code-parent hint maps child code '1.1.1' "
            "to parent code '1.1'."
        ),
        parent_context=topic_context,
    )

    The accumulator still contains only one entry for `topic_context`, but that entry
    now has both evidence reasons:

    evidence_reasons={
        "same_table_context",
        "code_parent_hint",
    }

    and both evidence summaries:

    evidence_summary=[
        "Child and parent share cited table row/header context.",
        "Configured code-parent hint maps child code '1.1.1' to parent code '1.1'.",
    ]

    This lets multiple retrieval signals support the same parent candidate without
    duplicating the candidate in the bounded parent-candidate set. Final schema
    conversion later sorts the evidence reasons and preserves unique evidence summaries.

    Parameters
    ----------
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    evidence_reason
        Machine-readable evidence channel reason.
    evidence_summary
        Human-readable evidence summary.
    parent_context
        Final SFI context for the candidate parent.
    """

    endpoint_id = str(parent_context.final_sfi_uuid)

    if endpoint_id not in evidence_by_endpoint_id:
        evidence_by_endpoint_id[endpoint_id] = _ParentEvidence(
            SFIHasChildParentCandidate(
                description=parent_context.description,
                endpoint_id=endpoint_id,
                endpoint_kind="StandardsFrameworkItem",
                evidence_reasons=[evidence_reason],
                evidence_summary=[evidence_summary],
                final_sfi_uuid=parent_context.final_sfi_uuid,
                is_root=False,
                normalized_statement_code=parent_context.normalized_statement_code,
                normalized_statement_type=parent_context.normalized_statement_type,
                source_context_keys=parent_context.source_context_keys,
                source_page_indexes=parent_context.source_page_indexes,
                source_segment_ids=parent_context.source_segment_ids,
                source_window_indexes=parent_context.source_window_indexes,
                statement_code=parent_context.statement_code,
                statement_type=parent_context.statement_type,
            )
        )
        return

    evidence = evidence_by_endpoint_id[endpoint_id]
    evidence.evidence_reasons.add(evidence_reason)

    if evidence_summary and evidence_summary not in evidence.evidence_summary:
        evidence.evidence_summary.append(evidence_summary)


def _assert_model_payload_equal(
    *, actual: BaseModel, artifact_label: str, expected: BaseModel
) -> None:
    """Validate that two Pydantic model payloads are exactly equivalent.

    Parameters
    ----------
    actual
        Model loaded from an existing artifact.
    artifact_label
        Human-readable artifact label for error messages.
    expected
        Model payload computed from the current inputs.

    Raises
    ------
    ValueError
        If the actual artifact payload differs from the expected current payload.
    """

    if _model_dump_key(actual) != _model_dump_key(expected):
        raise ValueError(
            f"{artifact_label} does not match the current planned SFI hasChild "
            f"resolution payload."
        )


def _assert_model_sequences_equal(
    *, actual: Sequence[BaseModel], artifact_label: str, expected: Sequence[BaseModel]
) -> None:
    """Validate that two ordered Pydantic model sequences are exactly equivalent.

    Parameters
    ----------
    actual
        Models loaded from an existing SFI hasChild resolution artifact.
    artifact_label
        Human-readable artifact label for error messages.
    expected
        Model sequence computed from the current SFI hasChild resolution inputs.

    Raises
    ------
    ValueError
        If the sequence lengths differ or any payload differs at the same position.
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
                f"planned SFI hasChild resolution payload."
            )


def _bound_parent_candidates(
    *,
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    non_root_candidates: Sequence[SFIHasChildParentCandidate],
) -> tuple[list[SFIHasChildParentCandidate], list[str], bool]:
    """Rerank and truncate (non-root) parent candidates while preserving one slot for
    the StandardsFramework root fallback.

    Examples
    --------

    1. Suppose the runtime config allows at most four parent candidates:

    kg_config.academic_standards.max_has_child_parent_candidates = 4

    And a child has five non-root parent candidates before bounding:

    topic:
        endpoint_id="<topic-uuid>"
        evidence_reasons=["code_parent_hint", "same_table_context"]

    strand:
        endpoint_id="<strand-uuid>"
        evidence_reasons=["matched_section_path_label"]

    grade:
        endpoint_id="<grade-uuid>"
        evidence_reasons=["nearest_preceding_grouping"]

    nearby_group:
        endpoint_id="<nearby-group-uuid>"
        evidence_reasons=["nearby_source_context_key"]

    weak_group:
        endpoint_id="<weak-group-uuid>"
        evidence_reasons=["statement_type_compatible"]

    Calling:

    parent_candidates, truncation_notes, was_truncated = _bound_parent_candidates(
        framework_uuid=<framework-uuid>,
        kg_config=kg_config,
        non_root_candidates=[
            topic,
            strand,
            grade,
            nearby_group,
            weak_group,
        ],
    )

    first ranks the non-root candidates by evidence strength. Candidates with
    `code_parent_hint` or `matched_section_path_label` are treated as high-signal and
    are preserved first. Because one slot is reserved for the StandardsFramework root,
    only three non-root candidates can be returned.

    A possible returned list is:

    [
        topic,          # high-signal: code_parent_hint
        strand,         # high-signal: matched_section_path_label
        grade,          # next strongest remaining candidate
        root_candidate, # always included
    ]

    The function also reports that truncation occurred:

    was_truncated == True
    truncation_notes == [
        "Truncated parent candidates from 6 to 4, preserving StandardsFramework root fallback."
    ]

    2. The root candidate is always appended to the returned list, even when no
        non-root candidate exists. For example, if `non_root_candidates=[]`, the result
        is:

    parent_candidates == [root_candidate]
    was_truncated == False
    truncation_notes == []

    This guarantees that every child has at least one selectable parent endpoint, while
    still letting the LLM prefer a source-supported SFI parent when one is present.

    Parameters
    ----------
    framework_uuid
        Deterministic StandardsFramework root UUID.
    kg_config
        Runtime KG configuration containing parent-candidate set maximum.
    non_root_candidates
        Non-root parent candidates before bounding.

    Returns
    -------
    tuple[list[SFIHasChildParentCandidate], list[str], bool]
        Bounded parent candidates, truncation notes, and whether truncation occurred.
    """

    root_candidate = SFIHasChildParentCandidate(
        description=kg_config.metadata.framework_title or "StandardsFramework root",
        endpoint_id=str(framework_uuid),
        endpoint_kind="StandardsFramework",
        evidence_reasons=[_ROOT_EVIDENCE_REASON],
        evidence_summary=["StandardsFramework root fallback is always available."],
        final_sfi_uuid=None,
        is_root=True,
        normalized_statement_code=None,
        normalized_statement_type=None,
        source_context_keys=[],
        source_page_indexes=[],
        source_segment_ids=[],
        source_window_indexes=[],
        statement_code=None,
        statement_type=None,
    )
    sorted_candidates = sorted(non_root_candidates, key=_parent_candidate_rank)
    high_signal_reasons = {"code_parent_hint", "matched_section_path_label"}
    high_signal_candidates = [
        candidate
        for candidate in sorted_candidates
        if set(candidate.evidence_reasons) & high_signal_reasons
    ]
    other_candidates = [
        candidate
        for candidate in sorted_candidates
        if candidate not in high_signal_candidates
    ]
    max_parent_candidates = kg_config.academic_standards.max_has_child_parent_candidates
    slots_for_non_root = max_parent_candidates - 1
    selected_non_root = high_signal_candidates[:slots_for_non_root]
    selected_ids = {candidate.endpoint_id for candidate in selected_non_root}

    for candidate in other_candidates:
        if len(selected_non_root) >= slots_for_non_root:
            break

        if candidate.endpoint_id in selected_ids:
            continue

        selected_non_root.append(candidate)
        selected_ids.add(candidate.endpoint_id)

    was_truncated = len(non_root_candidates) + 1 > max_parent_candidates
    truncation_notes = []

    if was_truncated:
        truncation_notes.append(
            f"Truncated parent candidates from {len(non_root_candidates) + 1} to "
            f"{max_parent_candidates}, preserving StandardsFramework root fallback."
        )

    return [*selected_non_root, root_candidate], truncation_notes, was_truncated


def _build_candidate_parent_sets(
    *,
    contexts: Sequence[SFIHasChildFinalContext],
    extraction_windows: Sequence[ExtractionWindow],
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
) -> list[SFIHasChildCandidateParentSet]:
    """Build bounded candidate parent sets for finalized SFIs.

    Examples
    --------

    1. Suppose there are three finalized SFI contexts in source order:

    grade_4:
        description="Grade 4"
        normalized_statement_type="Standard Grouping"
        source_order=1
        source_window_indexes=[3]

    listening:
        description="Listening and Speaking"
        normalized_statement_type="Standard Grouping"
        source_order=2
        source_window_indexes=[4]

    conversation:
        description="1.1 CONVERSATION"
        normalized_statement_type="Standard Grouping"
        source_order=3
        source_window_indexes=[5]
        section_path_labels=["Grade 4", "Listening and Speaking"]

    When `conversation` is evaluated as the child, this function compares it with both
    `grade_4` and `listening` as possible parents. Because both are preceding Standard
    Grouping records, both may receive preceding-grouping evidence. Because
    "Listening and Speaking" also appears in `conversation.section_path_labels`, it may
    additionally receive `matched_section_path_label` evidence.

    The returned parent set for `conversation` contains a bounded list of selectable
    parent candidates, for example:

    [
        parent candidate: listening
            evidence_reasons=[
                "matched_section_path_label",
                "nearby_source_context_key",
                "nearest_preceding_grouping",
                "statement_type_compatible",
            ]

        parent candidate: grade_4
            evidence_reasons=[
                "matched_section_path_label",
                "statement_type_compatible",
            ]

        parent candidate: StandardsFramework root
            evidence_reasons=["root_fallback"]
    ]

    The LLM later chooses the direct parent from this menu. This function only
    retrieves and bounds plausible parent candidates.

    Parameters
    ----------
    contexts
        Final SFI source contexts.
    extraction_windows
        Persisted extraction windows, used for code-parent hints.
    framework_uuid
        Deterministic StandardsFramework root UUID.
    kg_config
        Runtime KG configuration.

    Returns
    -------
    list[SFIHasChildCandidateParentSet]
        Bounded parent candidate sets, one per finalized SFI.
    """

    parent_sets: list[SFIHasChildCandidateParentSet] = []
    table_keys_by_uuid = {
        context.final_sfi_uuid: _table_context_keys(context) for context in contexts
    }

    # Extract normalized child-code to parent-code hints.
    code_parent_pairs = {
        (child, parent)
        for window in extraction_windows
        for hint in window.code_parent_hints
        if (child := normalize_code(hint.child_code))
        and (parent := normalize_code(hint.parent_code))
    }

    for child_context in contexts:
        child_code = normalize_code(child_context.normalized_statement_code)
        child_table_keys = table_keys_by_uuid[child_context.final_sfi_uuid]
        evidence_by_endpoint_id: dict[str, _ParentEvidence] = {}

        for parent_context in contexts:
            if parent_context.final_sfi_uuid == child_context.final_sfi_uuid:
                continue

            _evaluate_parent_child_relationship(
                child_code=child_code,
                child_context=child_context,
                child_table_keys=child_table_keys,
                code_parent_pairs=code_parent_pairs,
                evidence_by_endpoint_id=evidence_by_endpoint_id,
                parent_context=parent_context,
                parent_table_keys=table_keys_by_uuid[parent_context.final_sfi_uuid],
            )

        parent_set = _finalize_candidate_parent_set(
            child_context=child_context,
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            framework_uuid=framework_uuid,
            kg_config=kg_config,
        )
        parent_sets.append(parent_set)

    return parent_sets


def _build_edge(
    *,
    child_context: SFIHasChildFinalContext,
    confidence: float,
    document_ir: DocumentIR,
    framework_uuid: uuid.UUID,
    llm_reason: str,
    parent_candidate: SFIHasChildParentCandidate,
    unresolved_root_fallback: bool,
) -> SFIHasChildEdge:
    """Build one deterministic final hasChild edge.

    Parameters
    ----------
    child_context
        Child final SFI context.
    confidence
        LLM confidence for the child parent decision.
    document_ir
        Source DocumentIR used to scope relationship IDs.
    framework_uuid
        StandardsFramework root UUID.
    llm_reason
        LLM parent-selection reason.
    parent_candidate
        Selected parent candidate endpoint.
    unresolved_root_fallback
        Whether this root edge was added because the child was unresolved.

    Returns
    -------
    SFIHasChildEdge
        Final hasChild edge artifact.

    Raises
    ------
    ValueError
        If a non-root candidate has no UUID.
    """

    source_entity_uuid = (
        framework_uuid if parent_candidate.is_root else parent_candidate.final_sfi_uuid
    )

    if source_entity_uuid is None:
        raise ValueError(
            f"Non-root parent candidate {parent_candidate.endpoint_id!r} has no UUID."
        )

    relationship_key = (
        f"lc:curriculum:{document_ir.doc_key}:relationship:hasChild:"
        f"{source_entity_uuid}:{child_context.final_sfi_uuid}"
    )
    relationship_id = uuid.uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, relationship_key)
    return SFIHasChildEdge(
        child_final_sfi_uuid=child_context.final_sfi_uuid,
        confidence=confidence,
        evidence_reasons=parent_candidate.evidence_reasons,
        is_root_edge=parent_candidate.is_root,
        llm_reason=llm_reason,
        metadata={
            "doc_key": document_ir.doc_key,
            "parent_evidence_summary": parent_candidate.evidence_summary,
            "relationship_identity_key": relationship_key,
        },
        parent_endpoint_id=parent_candidate.endpoint_id,
        parent_final_sfi_uuid=parent_candidate.final_sfi_uuid,
        relationship_id=relationship_id,
        relationship_type="hasChild",
        source_entity=(
            "StandardsFramework"
            if parent_candidate.is_root
            else "StandardsFrameworkItem"
        ),
        source_entity_uuid=source_entity_uuid,
        target_entity="StandardsFrameworkItem",
        target_sfi_uuid=child_context.final_sfi_uuid,
        unresolved_root_fallback=unresolved_root_fallback,
    )


def _build_edges_from_responses(
    *,
    document_ir: DocumentIR,
    framework_uuid: uuid.UUID,
    parent_sets: Sequence[SFIHasChildCandidateParentSet],
    responses: Sequence[SFIHasChildResolutionResponse],
) -> list[SFIHasChildEdge]:
    """Convert validated LLM responses into deterministic hasChild edges.

    Parameters
    ----------
    document_ir
        Source DocumentIR used to scope relationship IDs.
    framework_uuid
        Deterministic StandardsFramework root UUID.
    parent_sets
        Bounded parent candidate sets supplied to the LLM.
    responses
        Validated hasChild resolution responses.

    Returns
    -------
    list[SFIHasChildEdge]
        Final hasChild edge records.
    """

    child_context_by_id = {
        str(parent_set.child_context.final_sfi_uuid): parent_set.child_context
        for parent_set in parent_sets
    }
    parent_candidates_by_child_id = {
        str(parent_set.child_context.final_sfi_uuid): {
            candidate.endpoint_id: candidate
            for candidate in parent_set.parent_candidates
        }
        for parent_set in parent_sets
    }
    edges: list[SFIHasChildEdge] = []

    for response in responses:
        for child_resolution in response.child_resolutions:
            child_id = str(child_resolution.child_final_sfi_uuid)
            child_context = child_context_by_id[child_id]
            parent_candidates = parent_candidates_by_child_id[child_id]

            if child_resolution.unresolved:
                root_candidate = SFIHasChildParentCandidate(
                    description="StandardsFramework root fallback",
                    endpoint_id=str(framework_uuid),
                    endpoint_kind="StandardsFramework",
                    evidence_reasons=[_ROOT_EVIDENCE_REASON],
                    evidence_summary=[
                        "StandardsFramework root fallback is always available."
                    ],
                    final_sfi_uuid=None,
                    is_root=True,
                    normalized_statement_code=None,
                    normalized_statement_type=None,
                    source_context_keys=[],
                    source_page_indexes=[],
                    source_segment_ids=[],
                    source_window_indexes=[],
                    statement_code=None,
                    statement_type=None,
                )
                edges.append(
                    _build_edge(
                        child_context=child_context,
                        confidence=child_resolution.confidence,
                        document_ir=document_ir,
                        framework_uuid=framework_uuid,
                        llm_reason=child_resolution.reason,
                        parent_candidate=root_candidate,
                        unresolved_root_fallback=True,
                    )
                )
                continue

            for parent_endpoint_id in child_resolution.selected_parent_endpoint_ids:
                edges.append(
                    _build_edge(
                        child_context=child_context,
                        confidence=child_resolution.confidence,
                        document_ir=document_ir,
                        framework_uuid=framework_uuid,
                        llm_reason=child_resolution.reason,
                        parent_candidate=parent_candidates[parent_endpoint_id],
                        unresolved_root_fallback=False,
                    )
                )

    edges.sort(
        key=lambda edge: (str(edge.target_sfi_uuid), str(edge.source_entity_uuid))
    )
    return edges


def _build_final_contexts(
    *, document_ir: DocumentIR, sfi_final_records: Sequence[SFIFinalRecord]
) -> list[SFIHasChildFinalContext]:
    """Recover source context for finalized SFIs.

    Examples
    --------

    1. Suppose the DocumentIR segment order is:

    segment_001 -> source-order 0, section_path=["Grade 4"]
    segment_010 -> source-order 1, section_path=["Grade 4", "Listening and Speaking"]
    segment_011 -> source-order 2, section_path=["Grade 4", "Reading"]

    And suppose one final SFI record has:

    final_sfi_uuid=<uuid-a>
    description="1.1 CONVERSATION"
    normalized_statement_type="Standard Grouping"
    statement_type="Topic"
    source_segment_ids=["segment_010"]
    source_window_indexes=[5]
    source_context_keys=["ctx_topic_1"]
    candidate_source_refs=[
        {
            "table_header_indexes": [],
            "table_row_indexes": [2],
            "source_segment_ids": ["segment_010"],
            "window_index": 5,
        }
    ]

    Then `_build_final_contexts(...)` creates a `SFIHasChildFinalContext` whose key
    relationship-resolution fields are:

    final_sfi_uuid=<uuid-a>
    description="1.1 CONVERSATION"
    source_order=1
    section_path_labels=["Grade 4", "Listening and Speaking"]
    source_segment_ids=["segment_010"]
    source_window_indexes=[5]
    source_context_keys=["ctx_topic_1"]
    table_header_indexes=[]
    table_row_indexes=[2]
    normalized_statement_type="Standard Grouping"
    statement_type="Topic"

    The returned context does not decide any parent-child relationship. It only
    packages source order, section-path, table, code, and provenance evidence so later
    code can build bounded parent-candidate sets.

    Parameters
    ----------
    document_ir
        Source DocumentIR used to recover section-path and source-order evidence.
    sfi_final_records
        Finalized SFI records.

    Returns
    -------
    list[SFIHasChildFinalContext]
        Final SFI source contexts sorted by source order and UUID.
    """

    contexts: list[SFIHasChildFinalContext] = []
    segment_order_by_id = {
        segment.segment_id: index for index, segment in enumerate(document_ir.segments)
    }
    segments_by_id = {segment.segment_id: segment for segment in document_ir.segments}

    for record in sfi_final_records:
        # Use earliest source segment index among the final record's
        # `source_segment_ids`.
        source_order = min(
            [
                segment_order_by_id[source_segment_id]
                for source_segment_id in record.source_segment_ids
                if source_segment_id in segment_order_by_id
            ]
            or [0]
        )

        # Look up the `section_path` for each source segment in the DocumentIR.
        section_path_labels = _recover_section_path_labels(
            record=record, segments_by_id=segments_by_id
        )

        # Collect table provenance indexes.
        table_header_indexes = _source_ref_int_values(
            key="table_header_indexes", record=record
        )
        table_row_indexes = _source_ref_int_values(
            key="table_row_indexes", record=record
        )

        # Build the context for the finalized SFI.
        contexts.append(
            SFIHasChildFinalContext(
                audit_flags=record.audit_flags,
                candidate_source_texts=record.candidate_source_texts,
                description=record.description,
                final_sfi_uuid=record.final_sfi_uuid,
                normalized_statement_code=record.normalized_statement_code,
                normalized_statement_type=record.normalized_statement_type,
                section_path_labels=section_path_labels,
                source_context_keys=record.source_context_keys,
                source_order=source_order,
                source_page_indexes=record.source_page_indexes,
                source_registry_candidate_ids=record.source_registry_candidate_ids,
                source_segment_ids=record.source_segment_ids,
                source_window_ids=record.source_window_ids,
                source_window_indexes=record.source_window_indexes,
                statement_code=record.statement_code,
                statement_type=record.statement_type,
                table_header_indexes=table_header_indexes,
                table_row_indexes=table_row_indexes,
            )
        )

    # Sort by source order, then UUID.
    contexts.sort(
        key=lambda context: (context.source_order, str(context.final_sfi_uuid))
    )

    return contexts


def _build_resolution_summary(
    *,
    edges: Sequence[SFIHasChildEdge],
    parent_sets: Sequence[SFIHasChildCandidateParentSet],
    requests: Sequence[SFIHasChildResolutionRequest],
    responses: Sequence[SFIHasChildResolutionResponse],
) -> SFIHasChildResolutionSummary:
    """Build the aggregate hasChild resolution summary.

    Parameters
    ----------
    edges
        Final hasChild edge records.
    parent_sets
        Planned bounded parent-candidate sets.
    requests
        Planned LLM resolution requests.
    responses
        Completed LLM resolution responses.

    Returns
    -------
    SFIHasChildResolutionSummary
        Aggregate summary payload.
    """

    return SFIHasChildResolutionSummary(
        candidate_parent_set_count=len(parent_sets),
        edge_count=len(edges),
        final_sfi_count=len(parent_sets),
        llm_request_count=len(requests),
        llm_response_count=len(responses),
        root_edge_count=sum(1 for edge in edges if edge.is_root_edge),
        sfi_to_sfi_edge_count=sum(1 for edge in edges if not edge.is_root_edge),
        truncated_candidate_parent_set_count=sum(
            1 for parent_set in parent_sets if parent_set.was_truncated
        ),
        unresolved_child_count=sum(
            1 for edge in edges if edge.unresolved_root_fallback
        ),
    )


def _context_matches_section_path(
    *, child_context: SFIHasChildFinalContext, parent_context: SFIHasChildFinalContext
) -> bool:
    """Check whether a parent label appears in child section-path evidence.

    Parameters
    ----------
    child_context
        Child final SFI context.
    parent_context
        Candidate parent final SFI context.

    Returns
    -------
    bool
        True when parent text matches a recovered section-path label.
    """

    parent_texts = unique_nonempty(
        _normalize_text(value)
        for value in [
            parent_context.description,
            *parent_context.candidate_source_texts,
        ]
    )
    section_texts = unique_nonempty(
        _normalize_text(value) for value in child_context.section_path_labels
    )

    if not parent_texts or not section_texts:
        return False

    return any(
        parent_text == section_text or parent_text in section_text
        for parent_text in parent_texts
        for section_text in section_texts
    )


def _detect_sfi_cycles(edges: Sequence[SFIHasChildEdge]) -> list[list[str]]:
    """Detect directed cycles among SFI-to-SFI hasChild edges.

    Parameters
    ----------
    edges
        Final hasChild edges.

    Returns
    -------
    list[list[str]]
        Detected cycles represented as UUID strings.
    """

    graph: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        if edge.is_root_edge:
            continue

        graph[str(edge.source_entity_uuid)].append(str(edge.target_sfi_uuid))

    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def _visit(node_id: str) -> None:
        """Visit one graph node during cycle detection.

        Parameters
        ----------
        node_id
            Node UUID string to inspect.
        """

        if node_id in visiting:
            cycle_start = stack.index(node_id) if node_id in stack else 0
            cycles.append(stack[cycle_start:] + [node_id])
            return

        if node_id in visited:
            return

        visiting.add(node_id)
        stack.append(node_id)

        for child_id in graph.get(node_id, []):
            _visit(child_id)

        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(graph):
        _visit(node_id)

    return cycles


def _evaluate_parent_child_relationship(
    *,
    child_code: str | None,
    child_context: SFIHasChildFinalContext,
    child_table_keys: set[str],
    code_parent_pairs: set[tuple[str, str]],
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIHasChildFinalContext,
    parent_table_keys: set[str],
) -> None:
    """Evaluate relationship between a child and parent context to record evidence.

    Examples
    -=------

    1. Code-parent hints from extraction windows become high-signal parent evidence.

    Suppose an extraction window contains this code-parent hint:

    child_code="1.1.1"
    parent_code="1.1"

    And the finalized SFI contexts include:

    parent:
        description="1.1 CONVERSATION"
        normalized_statement_code="1.1"
        normalized_statement_type="Standard Grouping"

    child:
        description="1.1.1 Use appropriate expressions in conversation"
        normalized_statement_code="1.1.1"
        normalized_statement_type="Standard"

    During `_build_candidate_parent_sets(...)`, code values are normalized and the
    pair:

    ("1.1.1", "1.1")

    is added to `code_parent_pairs`.

    When this function is compares the child to the parent, it finds that the child's
    normalized code maps to the parent's normalized code. The parent candidate receives
    evidence like:

    evidence_reasons=["code_parent_hint"]
    evidence_summary=[
        "Configured code-parent hint maps child code '1.1.1' to parent code '1.1'."
    ]

    Because `code_parent_hint` is a high-signal reason, `_bound_parent_candidates()`
    tries to preserve this parent candidate even when the candidate list must be
    truncated.

    Parameters
    ----------
    child_code
        Normalized code of the child context.
    child_context
        Final SFI source context for the child.
    child_table_keys
        Table context keys for the child.
    code_parent_pairs
        Set of valid code-parent relationships extracted from windows.
    evidence_by_endpoint_id
        Dictionary of accumulated parent evidence to update.
    parent_context
        Final SFI source context for the potential parent.
    parent_table_keys
        Table context keys for the potential parent.
    """

    parent_code = normalize_code(parent_context.normalized_statement_code)

    # Simple, independent evidence channels expressed as (predicate, reason, summary).
    # Each predicate is evaluated against this child/parent pair, and every channel
    # whose predicate holds contributes its evidence.
    simple_rules: tuple[tuple[bool, str, str], ...] = (
        (
            bool(child_code)
            and bool(parent_code)
            and (child_code, parent_code) in code_parent_pairs,
            "code_parent_hint",
            f"Configured code-parent hint maps child code {child_code!r} "
            f"to parent code {parent_code!r}.",
        ),
        (
            bool(
                set(child_context.source_context_keys)
                & set(parent_context.source_context_keys)
            ),
            "same_source_context_key",
            "Child and parent share a registry source-context key.",
        ),
        (
            bool(
                set(child_context.source_segment_ids)
                & set(parent_context.source_segment_ids)
            ),
            "same_source_segment",
            "Child and parent share a DocumentIR source segment.",
        ),
        (
            bool(
                set(child_context.source_window_ids)
                & set(parent_context.source_window_ids)
            ),
            "same_source_window",
            "Child and parent share an extraction window.",
        ),
        (
            bool(child_table_keys & parent_table_keys),
            "same_table_context",
            "Child and parent share cited table row/header context.",
        ),
        (
            _context_matches_section_path(
                child_context=child_context, parent_context=parent_context
            ),
            "matched_section_path_label",
            "Parent description matches recovered child section-path evidence.",
        ),
        (
            _is_nearby_source_window(
                child_context=child_context, parent_context=parent_context
            ),
            "nearby_source_context_key",
            "Parent appears in a nearby preceding source window.",
        ),
    )

    for matched, reason, summary in simple_rules:
        if matched:
            _add_parent_evidence(
                evidence_by_endpoint_id=evidence_by_endpoint_id,
                evidence_reason=reason,
                evidence_summary=summary,
                parent_context=parent_context,
            )

    # Evaluate distance-banded preceding-grouping evidence.
    if (
        parent_context.normalized_statement_type == "Standard Grouping"
        and parent_context.source_order < child_context.source_order
    ):
        distance = child_context.source_order - parent_context.source_order

        # Distance thresholds, evaluated ordered widest-last.
        if distance <= 8:
            _add_parent_evidence(
                evidence_by_endpoint_id=evidence_by_endpoint_id,
                evidence_reason="nearest_preceding_grouping",
                evidence_summary=(
                    f"Parent is a preceding Standard Grouping within {distance} "
                    f"source-order units."
                ),
                parent_context=parent_context,
            )

        if distance <= 12:
            _add_parent_evidence(
                evidence_by_endpoint_id=evidence_by_endpoint_id,
                evidence_reason="statement_type_compatible",
                evidence_summary=(
                    "Parent is a preceding Standard Grouping compatible with "
                    "hasChild hierarchy instructions."
                ),
                parent_context=parent_context,
            )


def _finalize_candidate_parent_set(
    *,
    child_context: SFIHasChildFinalContext,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
) -> SFIHasChildCandidateParentSet:
    """Process collected evidence and build the bounded candidate parent set.

    Examples
    --------

    1. Suppose `child_context` represents the finalized SFI:

    final_sfi_uuid=<child-uuid>
    description="1.1.1 Use appropriate expressions in conversation"
    normalized_statement_type="Standard"
    statement_type="Specific Competence"
    normalized_statement_code="1.1.1"
    statement_code="1.1.1"

    And parent evidence has already been accumulated by `_add_parent_evidence(...)`:

    evidence_by_endpoint_id = {
        "<topic-uuid>": _ParentEvidence(
            candidate=SFIHasChildParentCandidate(
                endpoint_id="<topic-uuid>",
                endpoint_kind="StandardsFrameworkItem",
                description="1.1 CONVERSATION",
                evidence_reasons=["code_parent_hint"],
                evidence_summary=[
                    "Configured code-parent hint maps child code '1.1.1' "
                    "to parent code '1.1'."
                ],
                final_sfi_uuid=<topic-uuid>,
                is_root=False,
                normalized_statement_code="1.1",
                normalized_statement_type="Standard Grouping",
                statement_code="1.1",
                statement_type="Topic",
                source_context_keys=["ctx_topic"],
                source_segment_ids=["segment_010"],
                source_window_indexes=[5],
                source_page_indexes=[12],
            )
        )
    }

    Calling:

    parent_set = _finalize_candidate_parent_set(
        child_context=child_context,
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        framework_uuid=<framework-uuid>,
        kg_config=kg_config,
    )

    returns an `SFIHasChildCandidateParentSet` whose `child_context` is the supplied
    child and whose `parent_candidates` contain the accumulated topic parent plus the
    StandardsFramework root fallback:

    parent_set.child_context == child_context
    parent_set.candidate_count_before_truncation == 2
    parent_set.parent_candidates == [
        <topic parent candidate>,
        <StandardsFramework root fallback candidate>,
    ]

    Before returning, the function converts each `_ParentEvidence` object back into an
    immutable schema model, sorts the evidence reasons, removes duplicate evidence
    summaries, and delegates final ranking/truncation/root-fallback behavior to
    `_bound_parent_candidates(...)`.

    The returned parent set is only a bounded menu of selectable parents. It does not
    mean any parent has been chosen yet; the LLM later chooses direct hasChild parents
    from this set.

    Parameters
    ----------
    child_context
        Final SFI source context for the child.
    evidence_by_endpoint_id
        Dictionary of accumulated parent evidence.
    framework_uuid
        Deterministic StandardsFramework root UUID.
    kg_config
        Runtime KG configuration.

    Returns
    -------
    SFIHasChildCandidateParentSet
        The bounded parent candidate set for the child SFI.
    """

    non_root_candidates = [
        evidence.candidate.model_copy(
            update={
                "evidence_reasons": sorted(evidence.evidence_reasons),
                "evidence_summary": unique_nonempty(evidence.evidence_summary),
            }
        )
        for evidence in evidence_by_endpoint_id.values()
    ]

    parent_candidates, truncation_notes, was_truncated = _bound_parent_candidates(
        framework_uuid=framework_uuid,
        kg_config=kg_config,
        non_root_candidates=non_root_candidates,
    )
    return SFIHasChildCandidateParentSet(
        candidate_count_after_truncation=len(parent_candidates),
        candidate_count_before_truncation=len(non_root_candidates) + 1,
        child_context=child_context,
        max_parent_candidates=kg_config.academic_standards.max_has_child_parent_candidates,
        parent_candidates=parent_candidates,
        truncation_notes=truncation_notes,
        was_truncated=was_truncated,
    )


def _is_nearby_source_window(
    *, child_context: SFIHasChildFinalContext, parent_context: SFIHasChildFinalContext
) -> bool:
    """Check whether a parent is in a nearby preceding source window.

    Parameters
    ----------
    child_context
        Child final SFI context.
    parent_context
        Candidate parent final SFI context.

    Returns
    -------
    bool
        True when parent is a nearby preceding grouping candidate.
    """

    if parent_context.normalized_statement_type != "Standard Grouping":
        return False

    if parent_context.source_order >= child_context.source_order:
        return False

    return any(
        0 <= child_window_index - parent_window_index <= 2
        for child_window_index in child_context.source_window_indexes
        for parent_window_index in parent_context.source_window_indexes
    )


def _load_complete_existing_relationship_artifacts(
    *,
    contexts: Sequence[SFIHasChildFinalContext],
    contexts_fp: Path,
    document_ir: DocumentIR,
    edges_fp: Path,
    framework_uuid: uuid.UUID,
    parent_sets: Sequence[SFIHasChildCandidateParentSet],
    parent_sets_fp: Path,
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    responses_fp: Path,
    sfi_final_records: Sequence[SFIFinalRecord],
    summary_fp: Path,
    unresolved_edges_fp: Path,
) -> list[SFIHasChildEdge] | None:
    """Load complete current artifacts, or return None to resume.

    Existing artifacts are reusable only when every persisted artifact is present,
    parseable, exactly aligned with the current deterministic context, parent-set, and
    request payloads, and rebuilds to the same validated graph.

    Parameters
    ----------
    contexts
        Current recovered final SFI contexts.
    contexts_fp
        JSON path for persisted final SFI contexts.
    document_ir
        Source DocumentIR used to scope relationship IDs.
    edges_fp
        JSON path for final hasChild edges.
    framework_uuid
        Deterministic StandardsFramework UUID.
    parent_sets
        Current bounded parent-candidate sets.
    parent_sets_fp
        JSONL path for persisted parent-candidate sets.
    requests
        Current hasChild resolution requests.
    requests_fp
        JSONL path for persisted hasChild resolution requests.
    responses_fp
        JSONL path for persisted hasChild resolution responses.
    sfi_final_records
        Final SFI records that must be covered by the graph.
    summary_fp
        JSON path for persisted hasChild resolution summary.
    unresolved_edges_fp
        JSON path for unresolved root-fallback edges.

    Returns
    -------
    list[SFIHasChildEdge] | None
        Reusable final hasChild edges, or None when artifacts are incomplete or stale.
    """

    if not edges_fp.exists():
        return None

    try:
        edges = _validate_complete_relationship_artifacts(
            contexts=contexts,
            contexts_fp=contexts_fp,
            document_ir=document_ir,
            edges_fp=edges_fp,
            framework_uuid=framework_uuid,
            parent_sets=parent_sets,
            parent_sets_fp=parent_sets_fp,
            requests=requests,
            requests_fp=requests_fp,
            responses_fp=responses_fp,
            sfi_final_records=sfi_final_records,
            summary_fp=summary_fp,
            unresolved_edges_fp=unresolved_edges_fp,
        )
    except Exception as e:  # pylint: disable=W0718
        logger.warning(
            f"Existing hasChild artifacts are incomplete or stale; resuming SFI "
            f"hasChild resolution: {e}"
        )
        return None

    logger.info(
        f"Loading complete existing hasChild artifacts because overwrite=False: "
        f"{edges_fp}"
    )
    return edges


def _load_extraction_windows(kg_dirs: KGDirs) -> list[ExtractionWindow]:
    """Load persisted extraction windows from the KG directory.

    Parameters
    ----------
    kg_dirs
        KG artifact directory wrapper.

    Returns
    -------
    list[ExtractionWindow]
        Parsed extraction windows in file order.

    Raises
    ------
    ValueError
        If there is an error when parsing the extraction windows JSONL file.
    """

    extraction_windows: list[ExtractionWindow] = []
    extraction_windows_fp = kg_dirs.root / "extraction_windows.jsonl"

    with extraction_windows_fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            try:
                extraction_windows.append(
                    ExtractionWindow.model_validate_json(line_clean)
                )
            except Exception as e:  # pylint: disable=W0718
                raise ValueError(
                    f"Could not parse extraction window JSONL line {line_number} "
                    f"from {extraction_windows_fp}."
                ) from e

    return extraction_windows


def _load_json_model_sequence(*, fp: Path, model_type: BaseModel) -> list[BaseModel]:
    """Load a JSON list artifact into a typed Pydantic model sequence.

    Parameters
    ----------
    fp
        JSON artifact path containing a list payload.
    model_type
        Pydantic model class used to validate each item.

    Returns
    -------
    list[BaseModel]
        Parsed and validated model sequence.

    Raises
    ------
    ValueError
        If the file is missing, not a JSON list, or contains invalid records.
    """

    if not fp.exists():
        raise ValueError(f"Missing SFI hasChild resolution JSON artifact: {fp}")

    data = open_json_type(fp)

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON list in SFI hasChild resolution artifact: {fp}"
        )

    return [model_type.model_validate(item) for item in data]


def _load_jsonl_models(
    *, allow_partial_prefix: bool, fp: Path, model_type: BaseModel
) -> list[BaseModel]:
    """Load a JSONL artifact into a typed Pydantic model sequence.

    Parameters
    ----------
    allow_partial_prefix
        Whether to return the valid prefix when a later line is invalid or truncated.
    fp
        JSONL artifact path.
    model_type
        Pydantic model class used to validate each JSONL record.

    Returns
    -------
    list[BaseModel]
        Parsed and validated model sequence.

    Raises
    ------
    ValueError
        If the file is missing or invalid and partial-prefix loading is disabled.
    """

    if not fp.exists():
        if allow_partial_prefix:
            return []

        raise ValueError(f"Missing SFI hasChild resolution JSONL artifact: {fp}")

    models: list[BaseModel] = []

    with fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            if not line_clean:
                continue

            try:
                models.append(model_type.model_validate_json(line_clean))
            except Exception as e:
                if allow_partial_prefix:
                    logger.warning(
                        f"Ignoring invalid trailing SFI hasChild resolution JSONL "
                        f"record in {fp} at line {line_number}; valid prefix length is "
                        f"{len(models)}: {e}"
                    )
                    return models

                raise ValueError(
                    f"Invalid SFI hasChild resolution JSONL record in {fp} at line "
                    f"{line_number}: {e}"
                ) from e

    return models


def _load_resumable_resolution_progress(
    *,
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    responses_fp: Path,
) -> list[SFIHasChildResolutionResponse]:
    """Load a valid completed hasChild response prefix for resuming SFI hasChild
    resolution.

    A completed response prefix is reusable only when each response validates against
    the current planned request at the same position and the saved request payloads for
    that completed prefix exactly match the current request payloads.

    Parameters
    ----------
    requests
        Current deterministic hasChild resolution requests.
    requests_fp
        JSONL path for persisted hasChild resolution requests.
    responses_fp
        JSONL path for persisted hasChild resolution responses.

    Returns
    -------
    list[SFIHasChildResolutionResponse]
        Valid completed response prefix, or an empty list when no reusable progress
        exists.
    """

    try:
        responses = _load_jsonl_models(
            allow_partial_prefix=True,
            fp=responses_fp,
            model_type=SFIHasChildResolutionResponse,
        )
        _validate_resolution_response_prefix(requests=requests, responses=responses)

        if responses:
            saved_requests = _load_jsonl_models(
                allow_partial_prefix=False,
                fp=requests_fp,
                model_type=SFIHasChildResolutionRequest,
            )
            _validate_resolution_request_prefix(
                requests=requests,
                saved_requests=saved_requests,
                trusted_prefix_length=len(responses),
            )
            logger.info(
                f"Resuming hasChild resolution from {len(responses)} completed "
                f"responses in {responses_fp}."
            )
            return responses
    except Exception as e:  # pylint: disable=W0718
        logger.warning(
            f"Ignoring existing hasChild JSONL progress because the saved "
            f"request/response prefix does not match the current plan: {e}"
        )

    return []


def _load_sfi_final_summary(kg_dirs: KGDirs) -> SFIFinalSummary | None:
    """Load SFI final summary if present.

    Parameters
    ----------
    kg_dirs
        KG artifact directory wrapper.

    Returns
    -------
    SFIFinalSummary | None
        Parsed final summary, or None when absent.
    """

    summary_fp = kg_dirs.root / "sfi_final_summary.json"

    if not summary_fp.exists():
        return None

    return SFIFinalSummary.model_validate(open_json_type(summary_fp))


def _model_dump_key(value: BaseModel) -> str:
    """Build a stable JSON comparison key for a Pydantic model.

    Parameters
    ----------
    value
        Pydantic model to serialize.

    Returns
    -------
    str
        Stable JSON representation suitable for exact artifact comparison.
    """

    return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _normalize_text(value: Any) -> str:
    """Normalize text for matching and hashing.

    Parameters
    ----------
    value
        Raw text value.

    Returns
    -------
    str
        Normalized text.
    """

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _parent_candidate_rank(candidate: SFIHasChildParentCandidate) -> tuple[int, str]:
    """Build deterministic sorting key for parent candidates.

    Parameters
    ----------
    candidate
        Parent candidate to rank.

    Returns
    -------
    tuple[int, str]
        Sort key where lower values rank earlier.
    """

    weights = {
        "code_parent_hint": 100,
        "matched_section_path_label": 90,
        "same_table_context": 80,
        "same_source_context_key": 75,
        "same_source_segment": 65,
        "same_source_window": 60,
        "nearby_source_context_key": 40,
        "nearest_preceding_grouping": 35,
        "statement_type_compatible": 15,
        _ROOT_EVIDENCE_REASON: 0,
    }
    score = sum(weights.get(reason, 0) for reason in candidate.evidence_reasons)
    return (-score, candidate.endpoint_id)


def _prepare_output_files(output_fps: Sequence[Path]) -> None:
    """Remove stale artifacts and initialize JSONL outputs.

    Parameters
    ----------
    output_fps
        Artifact paths to reset.
    """

    for output_fp in output_fps:
        make_dir(output_fp.parent)

        if output_fp.exists():
            output_fp.unlink()

        if output_fp.suffix == ".jsonl":
            output_fp.write_text("", encoding="utf-8")


def _reachable_sfi_ids(
    *, edges: Sequence[SFIHasChildEdge], framework_uuid: uuid.UUID
) -> set[str]:
    """Compute final SFIs reachable from StandardsFramework root.

    Parameters
    ----------
    edges
        Final hasChild edges.
    framework_uuid
        StandardsFramework root UUID.

    Returns
    -------
    set[str]
        Reachable final SFI UUID strings.
    """

    graph: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        graph[str(edge.source_entity_uuid)].append(str(edge.target_sfi_uuid))

    reachable: set[str] = set()
    stack = [str(framework_uuid)]

    while stack:
        node_id = stack.pop()

        for child_id in graph.get(node_id, []):
            if child_id in reachable:
                continue

            reachable.add(child_id)
            stack.append(child_id)

    return reachable


def _recover_section_path_labels(
    *, record: SFIFinalRecord, segments_by_id: dict[str, BlockSegment | TableSegment]
) -> list[str]:
    """Recover section-path labels from DocumentIR source segments.

    Examples
    --------

    1. Suppose `record.source_segment_ids` is:

    ["segment_010", "segment_011"]

    And the corresponding DocumentIR segments have section paths:

    segment_010.section_path = ["Grade 4", "Listening and Speaking"]
    segment_011.section_path = ["Grade 4", "Reading"]

    Then `_recover_section_path_labels(record=record, segments_by_id=segments_by_id)`
    returns:

    ["Grade 4", "Listening and Speaking", "Reading"]

    The duplicate "Grade 4" label is kept only once, and labels remain in first-seen
    source order. These labels later provide evidence for candidate parents whose
    descriptions match the child SFI's recovered section-path context.

    Parameters
    ----------
    record
        Final SFI record.
    segments_by_id
        DocumentIR segments keyed by segment ID.

    Returns
    -------
    list[str]
        Unique section-path labels in source order.
    """

    section_ref_labels: list[str] = []

    for source_segment_id in record.source_segment_ids:
        segment = segments_by_id.get(source_segment_id)
        assert isinstance(
            segment, (BlockSegment, TableSegment)
        ), f"{source_segment_id = }"

        for section_ref in segment.section_path:
            section_ref_label = section_ref.text.strip()
            assert section_ref_label, f"{source_segment_id = }"
            section_ref_labels.append(section_ref_label)

    return unique_nonempty(section_ref_labels)


def _rewrite_resolution_progress_files(
    *,
    completed_requests: Sequence[SFIHasChildResolutionRequest],
    completed_responses: Sequence[SFIHasChildResolutionResponse],
    requests_fp: Path,
    responses_fp: Path,
) -> None:
    """Rewrite hasChild request/response JSONL artifacts to a clean prefix.

    Parameters
    ----------
    completed_requests
        Planned requests corresponding to completed responses.
    completed_responses
        Completed responses to preserve for resume.
    requests_fp
        JSONL path for hasChild resolution requests.
    responses_fp
        JSONL path for hasChild resolution responses.
    """

    make_dir(requests_fp.parent)
    make_dir(responses_fp.parent)
    requests_fp.write_text("", encoding="utf-8")
    responses_fp.write_text("", encoding="utf-8")

    for request in completed_requests:
        _write_jsonl_model(fp=requests_fp, model=request)

    for response in completed_responses:
        _write_jsonl_model(fp=responses_fp, model=response)


def _run_resolution_requests(
    *,
    completed_responses: Sequence[SFIHasChildResolutionResponse],
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    responses_fp: Path,
    usage_tracker: KGUsageTracker,
) -> list[SFIHasChildResolutionResponse]:
    """Run remaining LLM hasChild requests and persist progress incrementally.

    Parameters
    ----------
    completed_responses
        Valid response prefix already completed in a previous partial run.
    requests
        Full planned hasChild resolution request sequence.
    requests_fp
        JSONL path for persisted requests.
    responses_fp
        JSONL path for persisted responses.
    usage_tracker
        LLM usage tracker.

    Returns
    -------
    list[SFIHasChildResolutionResponse]
        Completed responses in request order, including the resumed prefix.

    Raises
    ------
    ValueError
        If the completed response prefix is longer than the planned requests.
    """

    responses = list(completed_responses)

    if len(responses) > len(requests):
        raise ValueError(
            f"Completed hasChild response prefix has {len(responses)} records, but "
            f"only {len(requests)} requests are planned."
        )

    if requests:
        make_dir(requests_fp.parent)
        make_dir(responses_fp.parent)

    for request_index in range(len(responses), len(requests)):
        current_request_number = request_index + 1
        request = requests[request_index]

        logger.info(
            f"Running hasChild resolution {current_request_number}/{len(requests)}: "
            f"request_id={request.request_id}."
        )

        _write_jsonl_model(fp=requests_fp, model=request)
        response = resolve_sfi_has_child_parent_request(
            resolution_request=request, usage_tracker=usage_tracker
        )
        _write_jsonl_model(fp=responses_fp, model=response)
        responses.append(response)

    return responses


def _source_ref_int_values(*, key: str, record: SFIFinalRecord) -> list[int]:
    """Collect integer values from final-record candidate source refs.

    Examples
    --------

    1. Suppose `record.candidate_source_refs` contains:

    [
        {"table_row_indexes": [2, "3"], "table_header_indexes": []},
        {"table_row_indexes": [3, 4], "table_header_indexes": ["0"]},
        {"table_row_indexes": ["not-an-int"], "table_header_indexes": []},
    ]

    Then:

    _source_ref_int_values(key="table_row_indexes", record=record)

    returns [2, 3, 4]

    And:

    _source_ref_int_values(key="table_header_indexes", record=record)

    returns [0]

    Values are converted to integers when possible, invalid values are ignored,
    duplicates are removed, and the result is sorted. The returned indexes are later
    used to build table-context evidence such as "same_table_context" for possible
    hasChild parents.

    Parameters
    ----------
    key
        Candidate source-ref key to collect.
    record
        Final SFI record.

    Returns
    -------
    list[int]
        Sorted unique integer values.
    """

    values: set[int] = set()

    for source_ref in record.candidate_source_refs:
        if not isinstance(source_ref, dict):
            continue

        for value in source_ref.get(key) or []:
            try:
                values.add(int(value))
            except Exception:  # pylint: disable=W0718
                continue

    return sorted(values)


def _table_context_keys(context: SFIHasChildFinalContext) -> set[str]:
    """Build table-local context keys for one final SFI context.

    Examples
    --------

    1. Table row and header provenance can create same-table-context evidence.

    Suppose two final SFI contexts cite the same source table segment:

    topic:
        description="1.1 CONVERSATION"
        source_segment_ids=["segment_010"]
        table_row_indexes=[2]

    specific_competence:
        description="1.1.1 Use appropriate expressions in conversation"
        source_segment_ids=["segment_010"]
        table_row_indexes=[2]

    For each context, this function creates a key like:

    "segment:segment_010:row:2"

    When `_evaluate_parent_child_relationship(...)` compares `specific_competence` as
    the child and `topic` as a possible parent, the table-context key sets intersect.
    The parent candidate receives:

    evidence_reasons=["same_table_context"]
    evidence_summary=["Child and parent share cited table row/header context."]

    This evidence does not automatically make `topic` the direct parent. It only places
    `topic` in the bounded parent-candidate set so the LLM can decide whether the
    source supports a direct hasChild relationship.

    Parameters
    ----------
    context
        Final SFI context.

    Returns
    -------
    set[str]
        Table-local row/header context keys.
    """

    keys: set[str] = set()

    for source_segment_id in context.source_segment_ids:
        for row_index in context.table_row_indexes:
            keys.add(f"segment:{source_segment_id}:row:{row_index}")

        for header_index in context.table_header_indexes:
            keys.add(f"segment:{source_segment_id}:header:{header_index}")

    return keys


def _validate_complete_relationship_artifacts(
    *,
    contexts: Sequence[SFIHasChildFinalContext],
    contexts_fp: Path,
    document_ir: DocumentIR,
    edges_fp: Path,
    framework_uuid: uuid.UUID,
    parent_sets: Sequence[SFIHasChildCandidateParentSet],
    parent_sets_fp: Path,
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    responses_fp: Path,
    sfi_final_records: Sequence[SFIFinalRecord],
    summary_fp: Path,
    unresolved_edges_fp: Path,
) -> list[SFIHasChildEdge]:
    """Validate that persisted artifacts are complete and current.

    Parameters
    ----------
    contexts
        Current recovered final SFI contexts.
    contexts_fp
        JSON path for persisted final SFI contexts.
    document_ir
        Source DocumentIR used to scope relationship IDs.
    edges_fp
        JSON path for final hasChild edges.
    framework_uuid
        Deterministic StandardsFramework UUID.
    parent_sets
        Current bounded parent-candidate sets.
    parent_sets_fp
        JSONL path for persisted parent-candidate sets.
    requests
        Current hasChild resolution requests.
    requests_fp
        JSONL path for persisted hasChild resolution requests.
    responses_fp
        JSONL path for persisted hasChild resolution responses.
    sfi_final_records
        Final SFI records that must be covered by the graph.
    summary_fp
        JSON path for persisted hasChild resolution summary.
    unresolved_edges_fp
        JSON path for unresolved root-fallback edges.

    Returns
    -------
    list[SFIHasChildEdge]
        Existing final hasChild edges when all artifacts validate.

    Raises
    ------
    ValueError
        If any artifact is missing, stale, incomplete, or internally inconsistent.
    """

    loaded_contexts = _load_json_model_sequence(
        fp=contexts_fp, model_type=SFIHasChildFinalContext
    )
    _assert_model_sequences_equal(
        actual=loaded_contexts,
        artifact_label="sfi_final_contexts.json",
        expected=contexts,
    )

    loaded_parent_sets = _load_jsonl_models(
        allow_partial_prefix=False,
        fp=parent_sets_fp,
        model_type=SFIHasChildCandidateParentSet,
    )
    _assert_model_sequences_equal(
        actual=loaded_parent_sets,
        artifact_label="has_child_candidate_parent_sets.jsonl",
        expected=parent_sets,
    )

    loaded_requests = _load_jsonl_models(
        allow_partial_prefix=False,
        fp=requests_fp,
        model_type=SFIHasChildResolutionRequest,
    )
    _assert_model_sequences_equal(
        actual=loaded_requests,
        artifact_label="has_child_resolution_requests.jsonl",
        expected=requests,
    )

    loaded_responses = _load_jsonl_models(
        allow_partial_prefix=False,
        fp=responses_fp,
        model_type=SFIHasChildResolutionResponse,
    )
    _validate_resolution_response_prefix(requests=requests, responses=loaded_responses)

    if len(loaded_responses) != len(requests):
        raise ValueError(
            f"has_child_resolution_responses.jsonl has {len(loaded_responses)} "
            f"records, but expected {len(requests)} records."
        )

    expected_edges = _build_edges_from_responses(
        document_ir=document_ir,
        framework_uuid=framework_uuid,
        parent_sets=parent_sets,
        responses=loaded_responses,
    )
    _validate_graph(
        edges=expected_edges,
        framework_uuid=framework_uuid,
        sfi_final_records=sfi_final_records,
    )
    expected_summary = _build_resolution_summary(
        edges=expected_edges,
        parent_sets=parent_sets,
        requests=requests,
        responses=loaded_responses,
    )
    expected_unresolved_edges = [
        edge for edge in expected_edges if edge.unresolved_root_fallback
    ]

    loaded_edges = _load_json_model_sequence(fp=edges_fp, model_type=SFIHasChildEdge)
    _assert_model_sequences_equal(
        actual=loaded_edges,
        artifact_label="has_child_edges_final.json",
        expected=expected_edges,
    )
    loaded_unresolved_edges = _load_json_model_sequence(
        fp=unresolved_edges_fp, model_type=SFIHasChildEdge
    )
    _assert_model_sequences_equal(
        actual=loaded_unresolved_edges,
        artifact_label="has_child_unresolved_edges.json",
        expected=expected_unresolved_edges,
    )
    loaded_summary = SFIHasChildResolutionSummary.model_validate(
        open_json_type(summary_fp)
    )
    _assert_model_payload_equal(
        actual=loaded_summary,
        artifact_label="has_child_resolution_summary.json",
        expected=expected_summary,
    )

    return loaded_edges


def _validate_graph(
    *,
    edges: Sequence[SFIHasChildEdge],
    framework_uuid: uuid.UUID,
    sfi_final_records: Sequence[SFIFinalRecord],
) -> None:
    """Validate final hasChild graph constraints.

    Parameters
    ----------
    edges
        Final hasChild edges.
    framework_uuid
        StandardsFramework root UUID.
    sfi_final_records
        Final SFI records that must be represented and reachable.

    Raises
    ------
    ValueError
        If endpoint, cycle, duplicate, coverage, or reachability checks fail.
    """

    final_sfi_ids = {str(record.final_sfi_uuid) for record in sfi_final_records}
    valid_source_ids = set(final_sfi_ids) | {str(framework_uuid)}
    edge_pairs = [
        (str(edge.source_entity_uuid), str(edge.target_sfi_uuid)) for edge in edges
    ]
    duplicate_pairs = sorted(
        {pair for pair in edge_pairs if edge_pairs.count(pair) > 1}
    )

    if duplicate_pairs:
        raise ValueError(f"Duplicate hasChild edge pairs detected: {duplicate_pairs}.")

    for edge in edges:
        source_id = str(edge.source_entity_uuid)
        target_id = str(edge.target_sfi_uuid)

        if source_id not in valid_source_ids:
            raise ValueError(f"hasChild source endpoint does not exist: {source_id}.")

        if target_id not in final_sfi_ids:
            raise ValueError(f"hasChild target SFI does not exist: {target_id}.")

        if source_id == target_id:
            raise ValueError(f"hasChild self-loop detected for SFI {target_id}.")

    represented_child_ids = {str(edge.target_sfi_uuid) for edge in edges}
    missing_child_ids = sorted(final_sfi_ids - represented_child_ids)

    if missing_child_ids:
        raise ValueError(
            f"Final SFIs missing incoming hasChild edges: {missing_child_ids}."
        )

    cycles = _detect_sfi_cycles(edges)

    if cycles:
        raise ValueError(f"SFI-to-SFI hasChild cycles detected: {cycles[:5]}.")

    reachable = _reachable_sfi_ids(edges=edges, framework_uuid=framework_uuid)
    unreachable = sorted(final_sfi_ids - reachable)

    if unreachable:
        raise ValueError(
            f"Final SFIs are not reachable from StandardsFramework root: {unreachable}."
        )

    relationship_ids = [str(edge.relationship_id) for edge in edges]
    duplicate_relationship_ids = sorted(
        {value for value in relationship_ids if relationship_ids.count(value) > 1}
    )

    if duplicate_relationship_ids:
        raise ValueError(
            f"Duplicate hasChild relationship IDs detected: {duplicate_relationship_ids}."
        )


def _validate_resolution_request_prefix(
    *,
    requests: Sequence[SFIHasChildResolutionRequest],
    saved_requests: Sequence[SFIHasChildResolutionRequest],
    trusted_prefix_length: int,
) -> None:
    """Validate saved hasChild requests for a completed-response prefix.

    Parameters
    ----------
    requests
        Current deterministic hasChild resolution requests.
    saved_requests
        Persisted request records from a previous run.
    trusted_prefix_length
        Number of saved request records required to match the current plan.

    Raises
    ------
    ValueError
        If the saved request prefix cannot safely support response reuse.
    """

    if trusted_prefix_length < 0:
        raise ValueError(
            f"Trusted hasChild request prefix length cannot be negative: "
            f"{trusted_prefix_length}."
        )

    if trusted_prefix_length > len(requests):
        raise ValueError(
            f"Trusted hasChild request prefix length {trusted_prefix_length} exceeds "
            f"the current request count {len(requests)}."
        )

    if len(saved_requests) < trusted_prefix_length:
        raise ValueError(
            f"Saved hasChild requests contain {len(saved_requests)} records, but "
            f"{trusted_prefix_length} completed responses require matching saved "
            f"request payloads."
        )

    _assert_model_sequences_equal(
        actual=saved_requests[:trusted_prefix_length],
        artifact_label="saved hasChild request completed-response prefix",
        expected=requests[:trusted_prefix_length],
    )


def _validate_resolution_response_prefix(
    *,
    requests: Sequence[SFIHasChildResolutionRequest],
    responses: Sequence[SFIHasChildResolutionResponse],
) -> None:
    """Validate completed hasChild responses against the current request prefix.

    Parameters
    ----------
    requests
        Current deterministic hasChild resolution requests.
    responses
        Saved or newly produced hasChild resolution responses.

    Raises
    ------
    ValueError
        If any response is stale, malformed relative to its request, or too long.
    """

    if len(responses) > len(requests):
        raise ValueError(
            f"Found {len(responses)} hasChild responses, but only "
            f"{len(requests)} requests are planned."
        )

    for response_index, response in enumerate(responses):
        request = requests[response_index]

        try:
            verify_sfi_has_child_resolution_quality(
                resolution_request=request, resolution_response=response
            )
        except Exception as e:
            raise ValueError(
                f"Saved hasChild response {response_index + 1} does not match the "
                f"current planned request: {e}"
            ) from e


def _validate_sfi_final_records_and_summary(
    *, kg_dirs: KGDirs, sfi_final_records: Sequence[SFIFinalRecord]
) -> None:
    """Validate that final SFI universe is complete enough for hasChild resolution.

    Parameters
    ----------
    kg_dirs
        KG artifact directory wrapper.

    Raises
    ------
    ValueError
        If there is not at least one final SFI record.
        If SFI final summary contains excluded conflict or needs-review merge groups.
    """

    if not sfi_final_records:
        raise ValueError(
            "SFI hasChild resolution requires at least one final SFI record."
        )

    sfi_final_summary = _load_sfi_final_summary(kg_dirs)

    if sfi_final_summary is None:
        return

    if sfi_final_summary.excluded_conflict_group_count:
        raise ValueError(
            f"SFI hasChild resolution requires a complete finalized SFI universe, but "
            f"SFI final summary excluded "
            f"{sfi_final_summary.excluded_conflict_group_count} conflict groups."
        )

    if sfi_final_summary.excluded_needs_review_group_count:
        raise ValueError(
            f"SFI hasChild resolution requires a complete finalized SFI universe, but "
            f"SFI final summary excluded "
            f"{sfi_final_summary.excluded_needs_review_group_count} needs-review groups."
        )


def _write_context_and_parent_set_artifacts(
    *,
    contexts: Sequence[SFIHasChildFinalContext],
    contexts_fp: Path,
    parent_sets: Sequence[SFIHasChildCandidateParentSet],
    parent_sets_fp: Path,
) -> None:
    """Write deterministic context and parent-set artifacts.

    Parameters
    ----------
    contexts
        Current recovered final SFI contexts.
    contexts_fp
        JSON path for persisted final SFI contexts.
    parent_sets
        Current bounded parent-candidate sets.
    parent_sets_fp
        JSONL path for persisted parent-candidate sets.
    """

    write_to_json(
        fp=contexts_fp,
        json_info=[context.model_dump(mode="json") for context in contexts],
    )
    parent_sets_fp.write_text("", encoding="utf-8")

    for parent_set in parent_sets:
        _write_jsonl_model(fp=parent_sets_fp, model=parent_set)


def _write_jsonl_model(*, fp: Path, model: Any) -> None:
    """Append one Pydantic model to a JSONL file.

    Parameters
    ----------
    fp
        JSONL path.
    model
        Pydantic-style model with ``model_dump``.
    """

    make_dir(fp.parent)

    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(model.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _write_resolution_artifacts(
    *,
    edges: Sequence[SFIHasChildEdge],
    edges_fp: Path,
    summary: SFIHasChildResolutionSummary,
    summary_fp: Path,
    unresolved_edges_fp: Path,
) -> None:
    """Write final edge, unresolved-edge, and summary artifacts.

    Parameters
    ----------
    edges
        Final hasChild edges.
    edges_fp
        JSON path for final hasChild edge artifact.
    summary
        Aggregate hasChild resolution summary.
    summary_fp
        JSON path for hasChild resolution summary.
    unresolved_edges_fp
        JSON path for unresolved root-fallback edge artifact.
    """

    write_to_json(
        fp=edges_fp, json_info=[edge.model_dump(mode="json") for edge in edges]
    )
    write_to_json(
        fp=unresolved_edges_fp,
        json_info=[
            edge.model_dump(mode="json")
            for edge in edges
            if edge.unresolved_root_fallback
        ],
    )
    write_to_json(fp=summary_fp, json_info=summary.model_dump(mode="json"))


def resolve_has_child_edges(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool,
    sfi_final_records: Sequence[SFIFinalRecord],
    usage_tracker: KGUsageTracker,
) -> list[SFIHasChildEdge]:
    """Resolve final hasChild edges for finalized SFI records.

    When `overwrite` is true, all artifacts are regenerated from scratch. When
    `overwrite`` is false, complete current artifacts are reused, and incomplete
    artifacts resume from the longest validated request/response JSONL prefix.

    Parameters
    ----------
    document_ir
        Source DocumentIR.
    kg_config
        Runtime KG configuration containing hasChild instructions and candidate-set
        bounds.
    kg_dirs
        KG artifact directory wrapper.
    overwrite
        Whether to discard existing artifacts and restart from the first hasChild
        resolution request.
    sfi_final_records
        Finalized SFI records.
    usage_tracker
        LLM usage tracker.

    Returns
    -------
    list[SFIHasChildEdge]
        Final hasChild edge records.
    """

    _validate_sfi_final_records_and_summary(
        kg_dirs=kg_dirs, sfi_final_records=sfi_final_records
    )

    make_dir(kg_dirs.root)
    contexts_fp = kg_dirs.root / "sfi_final_contexts.json"
    edges_fp = kg_dirs.root / "has_child_edges_final.json"
    parent_sets_fp = kg_dirs.root / "has_child_candidate_parent_sets.jsonl"
    requests_fp = kg_dirs.root / "has_child_resolution_requests.jsonl"
    responses_fp = kg_dirs.root / "has_child_resolution_responses.jsonl"
    summary_fp = kg_dirs.root / "has_child_resolution_summary.json"
    unresolved_edges_fp = kg_dirs.root / "has_child_unresolved_edges.json"

    # Create the framework UUID.
    identity_key = f"lc:curriculum:{document_ir.doc_key}:standards_framework"
    framework_uuid = uuid.uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, identity_key)

    # Load extraction windows and build SFI final contexts and parent sets for hasChild
    # resolution requests.
    extraction_windows = _load_extraction_windows(kg_dirs)
    contexts = _build_final_contexts(
        document_ir=document_ir, sfi_final_records=sfi_final_records
    )
    parent_sets = _build_candidate_parent_sets(
        contexts=contexts,
        extraction_windows=extraction_windows,
        framework_uuid=framework_uuid,
        kg_config=kg_config,
    )
    requests = [
        SFIHasChildResolutionRequest(
            child_parent_sets=[parent_set],
            request_id=(
                f"has_child_request_"
                f"{hashlib.sha256(_normalize_text(str(parent_set.child_context.final_sfi_uuid)).encode('utf-8')).hexdigest()[:16]}"
            ),
            sfi_has_child_instructions=kg_config.academic_standards.sfi_has_child_instructions,
        )
        for parent_set in parent_sets
    ]

    if overwrite:
        logger.info(
            "Starting SFI hasChild resolution from scratch because overwrite=True."
        )

        _prepare_output_files(
            [
                contexts_fp,
                edges_fp,
                parent_sets_fp,
                requests_fp,
                responses_fp,
                summary_fp,
                unresolved_edges_fp,
            ]
        )
        completed_responses: list[SFIHasChildResolutionResponse] = []
    else:
        existing_edges = _load_complete_existing_relationship_artifacts(
            contexts=contexts,
            contexts_fp=contexts_fp,
            document_ir=document_ir,
            edges_fp=edges_fp,
            framework_uuid=framework_uuid,
            parent_sets=parent_sets,
            parent_sets_fp=parent_sets_fp,
            requests=requests,
            requests_fp=requests_fp,
            responses_fp=responses_fp,
            sfi_final_records=sfi_final_records,
            summary_fp=summary_fp,
            unresolved_edges_fp=unresolved_edges_fp,
        )

        if existing_edges is not None:
            return existing_edges

        completed_responses = _load_resumable_resolution_progress(
            requests=requests, requests_fp=requests_fp, responses_fp=responses_fp
        )
        _prepare_output_files(
            [contexts_fp, edges_fp, parent_sets_fp, summary_fp, unresolved_edges_fp]
        )
        _rewrite_resolution_progress_files(
            completed_requests=requests[: len(completed_responses)],
            completed_responses=completed_responses,
            requests_fp=requests_fp,
            responses_fp=responses_fp,
        )

    _write_context_and_parent_set_artifacts(
        contexts=contexts,
        contexts_fp=contexts_fp,
        parent_sets=parent_sets,
        parent_sets_fp=parent_sets_fp,
    )
    responses = _run_resolution_requests(
        completed_responses=completed_responses,
        requests=requests,
        requests_fp=requests_fp,
        responses_fp=responses_fp,
        usage_tracker=usage_tracker,
    )
    edges = _build_edges_from_responses(
        document_ir=document_ir,
        framework_uuid=framework_uuid,
        parent_sets=parent_sets,
        responses=responses,
    )
    _validate_graph(
        edges=edges, framework_uuid=framework_uuid, sfi_final_records=sfi_final_records
    )

    summary = _build_resolution_summary(
        edges=edges, parent_sets=parent_sets, requests=requests, responses=responses
    )
    _write_resolution_artifacts(
        edges=edges,
        edges_fp=edges_fp,
        summary=summary,
        summary_fp=summary_fp,
        unresolved_edges_fp=unresolved_edges_fp,
    )

    logger.success(
        f"Resolved final hasChild edges: "
        f"edges={len(edges)}; "
        f"root_edges={summary.root_edge_count}; "
        f"unresolved={summary.unresolved_child_count}."
    )

    return edges
