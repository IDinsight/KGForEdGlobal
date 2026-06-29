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
from pathlib import Path
from typing import Any, Iterable, Sequence

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import DocumentIR
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
from skg.kgs.utils import KGDirs
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, open_json_type, write_to_json

_ROOT_EVIDENCE_REASON = "root_fallback"


class _ParentEvidence:
    """Mutable parent-candidate evidence before schema conversion."""

    def __init__(self, candidate: SFIHasChildParentCandidate) -> None:
        """Initialize mutable evidence for one parent candidate.

        Parameters
        ----------
        candidate
            Parent candidate schema payload to enrich with evidence.
        """

        self.candidate = candidate
        self.evidence_reasons = set(candidate.evidence_reasons)
        self.evidence_summary = list(candidate.evidence_summary)


def _add_parent_evidence(
    *,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    evidence_reason: str,
    evidence_summary: str,
    parent_context: SFIHasChildFinalContext,
) -> None:
    """Add or update one non-root SFI parent candidate evidence record.

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


def _bound_parent_candidates(
    *,
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    non_root_candidates: Sequence[SFIHasChildParentCandidate],
) -> tuple[list[SFIHasChildParentCandidate], list[str], bool]:
    """Rerank and truncate parent candidates while preserving root fallback.

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

    max_parent_candidates = kg_config.academic_standards.max_has_child_parent_candidates
    root_candidate = _root_parent_candidate(
        framework_uuid=framework_uuid,
        framework_title=kg_config.metadata.framework_title,
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

    code_parent_pairs = _extract_code_parent_pairs(extraction_windows)
    max_parent_candidates = kg_config.academic_standards.max_has_child_parent_candidates
    parent_sets: list[SFIHasChildCandidateParentSet] = []
    table_keys_by_uuid = {
        context.final_sfi_uuid: _table_context_keys(context) for context in contexts
    }

    for child_context in contexts:
        evidence_by_endpoint_id: dict[str, _ParentEvidence] = {}
        child_code = _normalize_code(child_context.normalized_statement_code)
        child_table_keys = table_keys_by_uuid[child_context.final_sfi_uuid]

        for parent_context in contexts:
            if parent_context.final_sfi_uuid == child_context.final_sfi_uuid:
                continue

            parent_code = _normalize_code(parent_context.normalized_statement_code)
            parent_table_keys = table_keys_by_uuid[parent_context.final_sfi_uuid]

            if (
                child_code
                and parent_code
                and (child_code, parent_code) in code_parent_pairs
            ):
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason="code_parent_hint",
                    evidence_summary=(
                        f"Configured code-parent hint maps child code {child_code!r} "
                        f"to parent code {parent_code!r}."
                    ),
                    parent_context=parent_context,
                )

            if set(child_context.source_context_keys) & set(
                parent_context.source_context_keys
            ):
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason="same_source_context_key",
                    evidence_summary="Child and parent share a registry source-context key.",
                    parent_context=parent_context,
                )

            if set(child_context.source_segment_ids) & set(
                parent_context.source_segment_ids
            ):
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason="same_source_segment",
                    evidence_summary="Child and parent share a DocumentIR source segment.",
                    parent_context=parent_context,
                )

            if set(child_context.source_window_ids) & set(
                parent_context.source_window_ids
            ):
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason="same_source_window",
                    evidence_summary="Child and parent share an extraction window.",
                    parent_context=parent_context,
                )

            if child_table_keys & parent_table_keys:
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason="same_table_context",
                    evidence_summary="Child and parent share cited table row/header context.",
                    parent_context=parent_context,
                )

            if _context_matches_section_path(
                child_context=child_context, parent_context=parent_context
            ):
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason="matched_section_path_label",
                    evidence_summary="Parent description matches recovered child section-path evidence.",
                    parent_context=parent_context,
                )

            if _is_preceding_grouping(
                child_context=child_context, parent_context=parent_context
            ):
                distance = child_context.source_order - parent_context.source_order

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

            if _is_nearby_source_window(
                child_context=child_context, parent_context=parent_context
            ):
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason="nearby_source_context_key",
                    evidence_summary="Parent appears in a nearby preceding source window.",
                    parent_context=parent_context,
                )

        non_root_candidates = [
            evidence.candidate.model_copy(
                update={
                    "evidence_reasons": sorted(evidence.evidence_reasons),
                    "evidence_summary": _unique_nonempty(evidence.evidence_summary),
                }
            )
            for evidence in evidence_by_endpoint_id.values()
        ]
        parent_candidates, truncation_notes, was_truncated = _bound_parent_candidates(
            framework_uuid=framework_uuid,
            kg_config=kg_config,
            non_root_candidates=non_root_candidates,
        )
        parent_sets.append(
            SFIHasChildCandidateParentSet(
                candidate_count_after_truncation=len(parent_candidates),
                candidate_count_before_truncation=len(non_root_candidates) + 1,
                child_context=child_context,
                max_parent_candidates=max_parent_candidates,
                parent_candidates=parent_candidates,
                truncation_notes=truncation_notes,
                was_truncated=was_truncated,
            )
        )

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
                root_candidate = _root_parent_candidate(
                    framework_uuid=framework_uuid,
                    framework_title="StandardsFramework root fallback",
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

    segment_order_by_id = {
        segment.segment_id: index for index, segment in enumerate(document_ir.segments)
    }
    segments_by_id = {segment.segment_id: segment for segment in document_ir.segments}
    contexts: list[SFIHasChildFinalContext] = []

    for record in sfi_final_records:
        source_order = min(
            [
                segment_order_by_id[source_segment_id]
                for source_segment_id in record.source_segment_ids
                if source_segment_id in segment_order_by_id
            ]
            or [0]
        )
        section_path_labels = _recover_section_path_labels(
            record=record, segments_by_id=segments_by_id
        )
        table_header_indexes = _source_ref_int_values(
            key="table_header_indexes", record=record
        )
        table_row_indexes = _source_ref_int_values(
            key="table_row_indexes", record=record
        )
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
                source_page_indexes=record.source_page_indexes,
                source_registry_candidate_ids=record.source_registry_candidate_ids,
                source_segment_ids=record.source_segment_ids,
                source_order=source_order,
                source_window_ids=record.source_window_ids,
                source_window_indexes=record.source_window_indexes,
                statement_code=record.statement_code,
                statement_type=record.statement_type,
                table_header_indexes=table_header_indexes,
                table_row_indexes=table_row_indexes,
            )
        )

    contexts.sort(
        key=lambda context: (context.source_order, str(context.final_sfi_uuid))
    )
    return contexts


def _build_resolution_requests(
    *, kg_config: CreateKGConfig, parent_sets: Sequence[SFIHasChildCandidateParentSet]
) -> list[SFIHasChildResolutionRequest]:
    """Build one-child LLM resolution requests from parent candidate sets.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing hasChild instructions.
    parent_sets
        Bounded parent candidate sets.

    Returns
    -------
    list[SFIHasChildResolutionRequest]
        LLM request payloads in deterministic child order.
    """

    requests: list[SFIHasChildResolutionRequest] = []

    for parent_set in parent_sets:
        request_id = "has_child_request_" + _hash_text(
            n_hex=16, value=str(parent_set.child_context.final_sfi_uuid)
        )
        requests.append(
            SFIHasChildResolutionRequest(
                child_parent_sets=[parent_set],
                request_id=request_id,
                sfi_has_child_instructions=(
                    kg_config.academic_standards.sfi_has_child_instructions
                ),
            )
        )

    return requests


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

    parent_texts = _normalized_text_candidates(
        [parent_context.description, *parent_context.candidate_source_texts]
    )
    section_texts = _normalized_text_candidates(child_context.section_path_labels)

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


def _extract_code_parent_pairs(
    extraction_windows: Sequence[ExtractionWindow],
) -> set[tuple[str, str]]:
    """Extract normalized child-code to parent-code hints from extraction windows.

    Parameters
    ----------
    extraction_windows
        Persisted extraction windows containing deterministic code-parent hints.

    Returns
    -------
    set[tuple[str, str]]
        Normalized ``(child_code, parent_code)`` pairs.
    """

    pairs: set[tuple[str, str]] = set()

    for extraction_window in extraction_windows:
        for hint in extraction_window.code_parent_hints:
            child_code = _normalize_code(hint.child_code)
            parent_code = _normalize_code(hint.parent_code)

            if child_code and parent_code:
                pairs.add((child_code, parent_code))

    return pairs


def _hash_text(*, n_hex: int, value: str) -> str:
    """Hash normalized text with SHA-256.

    Parameters
    ----------
    n_hex
        Number of hexadecimal digest characters to return.
    value
        Raw text to hash.

    Returns
    -------
    str
        Truncated digest.
    """

    normalized = _normalize_text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:n_hex]


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


def _is_preceding_grouping(
    *, child_context: SFIHasChildFinalContext, parent_context: SFIHasChildFinalContext
) -> bool:
    """Check whether a parent is a preceding grouping SFI.

    Parameters
    ----------
    child_context
        Child final SFI context.
    parent_context
        Candidate parent final SFI context.

    Returns
    -------
    bool
        True when parent is a preceding Standard Grouping.
    """

    return (
        parent_context.normalized_statement_type == "Standard Grouping"
        and parent_context.source_order < child_context.source_order
    )


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
    """

    extraction_windows_fp = kg_dirs.root / "extraction_windows.jsonl"

    if not extraction_windows_fp.exists():
        return []

    extraction_windows: list[ExtractionWindow] = []

    with extraction_windows_fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            if not line_clean:
                continue

            try:
                extraction_windows.append(
                    ExtractionWindow.model_validate_json(line_clean)
                )
            except Exception as e:
                raise ValueError(
                    f"Could not parse extraction window JSONL line {line_number} "
                    f"from {extraction_windows_fp}."
                ) from e

    return extraction_windows


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


def _normalize_code(value: Any) -> str | None:
    """Normalize a source code for hint matching.

    Parameters
    ----------
    value
        Raw code-like value.

    Returns
    -------
    str | None
        Normalized code or None.
    """

    if value is None:
        return None

    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    normalized = re.sub(r"\s+", "", normalized).strip(" .:;-)–—")
    return normalized or None


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


def _normalized_text_candidates(values: Iterable[Any]) -> list[str]:
    """Normalize and deduplicate text candidates.

    Parameters
    ----------
    values
        Raw text values.

    Returns
    -------
    list[str]
        Unique normalized non-empty text values.
    """

    return _unique_nonempty(_normalize_text(value) for value in values)


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
    *, record: SFIFinalRecord, segments_by_id: dict[str, Any]
) -> list[str]:
    """Recover section-path labels from DocumentIR source segments.

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

    labels: list[str] = []

    for source_segment_id in record.source_segment_ids:
        segment = segments_by_id.get(source_segment_id)

        if segment is None:
            continue

        for section_ref in getattr(segment, "section_path", []) or []:
            if isinstance(section_ref, dict):
                label = str(section_ref.get("text") or "").strip()
            else:
                label = str(getattr(section_ref, "text", "") or "").strip()

            if label:
                labels.append(label)

    return _unique_nonempty(labels)


def _root_parent_candidate(
    *, framework_title: str, framework_uuid: uuid.UUID
) -> SFIHasChildParentCandidate:
    """Build the StandardsFramework root fallback parent candidate.

    Parameters
    ----------
    framework_title
        Human-readable StandardsFramework title.
    framework_uuid
        Deterministic StandardsFramework root UUID.

    Returns
    -------
    SFIHasChildParentCandidate
        Root parent candidate.
    """

    return SFIHasChildParentCandidate(
        description=framework_title or "StandardsFramework root",
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


def _run_resolution_requests(
    *,
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    responses_fp: Path,
    usage_tracker: KGUsageTracker,
) -> list[SFIHasChildResolutionResponse]:
    """Run LLM hasChild requests and persist request/response JSONL artifacts.

    Parameters
    ----------
    requests
        HasChild resolution requests.
    requests_fp
        JSONL path for persisted requests.
    responses_fp
        JSONL path for persisted responses.
    usage_tracker
        LLM usage tracker.

    Returns
    -------
    list[SFIHasChildResolutionResponse]
        Validated LLM responses.
    """

    make_dir(requests_fp.parent)
    requests_fp.write_text("", encoding="utf-8")
    responses_fp.write_text("", encoding="utf-8")
    responses: list[SFIHasChildResolutionResponse] = []

    for request_index, request in enumerate(requests, start=1):
        logger.info(
            f"Running hasChild resolution {request_index}/{len(requests)}: "
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


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
    """Return unique non-empty string values while preserving order.

    Parameters
    ----------
    values
        Raw values.

    Returns
    -------
    list[str]
        Unique cleaned values.
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


def _validate_sfi_final_summary(sfi_final_summary: SFIFinalSummary | None) -> None:
    """Validate that final SFI universe is complete enough for hasChild resolution.

    Parameters
    ----------
    sfi_final_summary
        SFI final summary, if available.

    Raises
    ------
    ValueError
        If SFI final summary contains excluded conflict or needs-review merge groups.
    """

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


def resolve_has_child_edges(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    sfi_final_records: Sequence[SFIFinalRecord],
    usage_tracker: KGUsageTracker,
) -> list[SFIHasChildEdge]:
    """Resolve final hasChild edges for finalized SFI records.

    Parameters
    ----------
    document_ir
        Source DocumentIR.
    kg_config
        Runtime KG configuration containing hasChild instructions and candidate-set
        bounds.
    kg_dirs
        KG artifact directory wrapper.
    sfi_final_records
        Finalized SFI records.
    usage_tracker
        LLM usage tracker.

    Returns
    -------
    list[SFIHasChildEdge]
        Final hasChild edge records.

    Raises
    ------
    ValueError
        If any of SFI records cannot be resolved.
    """

    if not sfi_final_records:
        raise ValueError(
            "SFI hasChild resolution requires at least one final SFI record."
        )

    if not kg_config.academic_standards.sfi_has_child_instructions.strip():
        raise ValueError(
            "SFI hasChild resolution requires non-empty sfi_has_child_instructions."
        )

    sfi_final_summary = _load_sfi_final_summary(kg_dirs)
    _validate_sfi_final_summary(sfi_final_summary)

    identity_key = f"lc:curriculum:{document_ir.doc_key}:standards_framework"
    framework_uuid = uuid.uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, identity_key)
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
    requests = _build_resolution_requests(kg_config=kg_config, parent_sets=parent_sets)

    contexts_fp = kg_dirs.root / "sfi_final_contexts.json"
    edges_fp = kg_dirs.root / "has_child_edges_final.json"
    parent_sets_fp = kg_dirs.root / "has_child_candidate_parent_sets.jsonl"
    requests_fp = kg_dirs.root / "has_child_resolution_requests.jsonl"
    responses_fp = kg_dirs.root / "has_child_resolution_responses.jsonl"
    summary_fp = kg_dirs.root / "has_child_resolution_summary.json"
    unresolved_edges_fp = kg_dirs.root / "has_child_unresolved_edges.json"

    make_dir(kg_dirs.root)
    write_to_json(
        fp=contexts_fp,
        json_info=[context.model_dump(mode="json") for context in contexts],
    )
    parent_sets_fp.write_text("", encoding="utf-8")

    for parent_set in parent_sets:
        _write_jsonl_model(fp=parent_sets_fp, model=parent_set)

    responses = _run_resolution_requests(
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

    summary = SFIHasChildResolutionSummary(
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

    logger.success(
        f"Resolved final hasChild edges: edges={len(edges)}; "
        f"root_edges={summary.root_edge_count}; unresolved={summary.unresolved_child_count}."
    )

    return edges
