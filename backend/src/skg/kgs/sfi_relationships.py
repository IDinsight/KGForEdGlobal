"""This module contains functionalities for resolving finalized Academic Standards SFI
hasChild relationships.

This module consumes finalized SFI records, recovers source context, builds bounded
source-grounded parent candidate sets, runs independent producer/checker LLM parent
resolution, validates universal response and graph contracts, and persists complete
relationship-resolution artifacts.
"""

# Standard Library
import hashlib
import re
import uuid

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence, TypeVar

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from skg.config import Settings
from skg.document_ir.schemas import DocumentIR
from skg.kgs.llm import KGUsageTracker, resolve_sfi_has_child_parent_request
from skg.kgs.schemas import (
    ExtractionWindow,
    SFIFinalContext,
    SFIFinalRecord,
    SFIFinalSummary,
    SFIHasChildCandidateParentSet,
    SFIHasChildEdge,
    SFIHasChildParentCandidate,
    SFIHasChildParentRequirement,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
    SFIHasChildResolutionSummary,
    SFIHasChildScopeComparison,
    SFIHasChildSourceRelation,
    SFIHasChildValidationVerdict,
)
from skg.kgs.sfi_source_anchors import SFISourceUnit, build_sfi_source_unit_map
from skg.kgs.utils import (
    KGDirs,
    append_jsonl_model,
    assert_model_sequences_equal,
    build_standards_framework_uuid,
    model_dump_key,
    normalize_code,
    normalize_text,
    reset_output_files,
    unique_nonempty,
)
from skg.kgs.validators import (
    ACTIVE_OUTLINE_STACK_PARENT_REASON,
    CARRY_FORWARD_PARENT_REASONS,
    CODE_PARENT_HINT_REASON,
    DECISIVE_DIRECT_PARENT_REASONS,
    IDENTITY_SCOPE_ANCESTOR_CONFLICT_REASON,
    IDENTITY_SCOPE_ANCESTOR_MATCH_REASON,
    IDENTITY_SCOPE_COMPLETE_PARENT_MATCH_REASON,
    IDENTITY_SCOPE_DIRECT_PARENT_CONFLICT_REASON,
    IDENTITY_SCOPE_DIRECT_PARENT_MATCH_REASON,
    LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON,
    MATCHED_SECTION_PATH_LABEL_REASON,
    NEARBY_SOURCE_CONTEXT_KEY_REASON,
    NEAREST_PRECEDING_GROUPING_REASON,
    PARENT_CELL_APPLIES_TO_CHILD_ROW_REASON,
    ROOT_EVIDENCE_REASON,
    SAME_RAW_TABLE_ROW_REASON,
    SAME_SOURCE_CONTEXT_KEY_REASON,
    SAME_SOURCE_SEGMENT_REASON,
    SAME_SOURCE_UNIT_REASON,
    SAME_SOURCE_WINDOW_REASON,
    SAME_TABLE_CONTEXT_REASON,
    SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_CONFLICT_REASON,
    SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_REASON,
    SOURCE_SCOPE_GROUPING_REASON,
    SOURCE_VISIBLE_DIRECT_PARENT_REASON,
    STATEMENT_TYPE_COMPATIBLE_REASON,
    STRONG_LOCAL_RANKING_PARENT_REASONS,
    verify_sfi_has_child_resolution_integrity,
    verify_sfi_has_child_validation_integrity,
)
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig, normalize_controlled_value_key
from skg.utils.general import make_dir, open_json_type, write_to_json

ModelT = TypeVar("ModelT", bound=BaseModel)
_TABLE_BODY_SOURCE_UNIT_PATTERN = re.compile(
    r"^(?P<source_segment_id>[^|]+)\|table_body_cell\|row=(?P<row_index>\d+)"
    r"\|columns=(?P<column_start_index>\d+):"
    r"(?P<column_end_index_exclusive>\d+)$"
)


@dataclass(frozen=True)
class _ControlledValueMatchPolicy:
    """Normalized controlled-value aliases for one configured statement type."""

    alias_to_value_key: dict[str, str]


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


@dataclass(frozen=True)
class _ResolutionProgress:
    """Reusable producer/checker progress for hasChild resolution."""

    draft_responses: list[SFIHasChildResolutionResponse]
    final_responses: list[SFIHasChildResolutionResponse]
    validation_verdicts: list[SFIHasChildValidationVerdict]


@dataclass(frozen=True)
class _TableOccurrenceRef:
    """Candidate-level table occurrence with paired source provenance.

    Attributes
    ----------
    source_segment_id
        DocumentIR table segment containing the occurrence.
    table_header_indexes
        Raw header-row indexes cited by the candidate in this occurrence.
    table_row_indexes
        Raw body-row indexes cited by the candidate in this occurrence.
    window_id
        Stable extraction-window identifier preserving occurrence identity.
    window_index
        Zero-based extraction-window index used for bounded locality checks.
    """

    source_segment_id: str
    table_header_indexes: tuple[int, ...]
    table_row_indexes: tuple[int, ...]
    window_id: str
    window_index: int


@dataclass(frozen=True)
class _TableSourceUnitRef:
    """Parsed table-body source unit used for child-relative source relations."""

    column_end_index_exclusive: int
    column_start_index: int
    row_index: int
    source_segment_id: str
    source_text: str
    source_unit_id: str


def _add_identity_scope_match_evidence(
    *,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIFinalContext,
    scope_comparison: SFIHasChildScopeComparison,
) -> None:
    """Add finalized identity-scope match evidence for one parent candidate.

    A complete structured-scope match and a single-value direct-parent match are
    mutually exclusive positive channels, so at most one is recorded. Matching ancestor
    scope dimensions are recorded independently because they can co-occur with either
    channel.

    Parameters
    ----------
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    parent_context
        Candidate parent final SFI context.
    scope_comparison
        Exact structured-scope comparison between the child and the candidate parent.
    """

    if scope_comparison.complete_match:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=IDENTITY_SCOPE_COMPLETE_PARENT_MATCH_REASON,
            evidence_summary=(
                "Candidate's canonical parent value and every available ancestor "
                "scope dimension match the child's finalized identity scope."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )
    elif scope_comparison.direct_parent_value_match is True:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=IDENTITY_SCOPE_DIRECT_PARENT_MATCH_REASON,
            evidence_summary=(
                "Candidate's canonical value matches the child's finalized scope "
                "value for this direct parent statement type."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )

    if scope_comparison.matching_ancestor_statement_types:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=IDENTITY_SCOPE_ANCESTOR_MATCH_REASON,
            evidence_summary=(
                f"Candidate and child have matching finalized ancestor scope "
                f"dimensions: {scope_comparison.matching_ancestor_statement_types}."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )


def _add_local_active_outline_direct_parent_evidence(
    *,
    child_context: SFIFinalContext,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIFinalContext,
    source_relations: Sequence[SFIHasChildSourceRelation],
) -> None:
    """Add strong local ranking evidence for active-outline parent candidates.

    Active outline evidence by itself is only a carry-forward retrieval signal. It
    becomes stronger local ranking evidence only when the active parent and child share
    an exact source-context key or a deterministic table relation. Broad same-segment
    and same-window overlap are intentionally insufficient.

    Parameters
    ----------
    child_context
        Final SFI context for the child.
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    parent_context
        Active-outline parent final SFI context.
    source_relations
        Exact child-relative table relations for the active parent candidate.
    """

    if not (
        bool(
            set(child_context.source_context_keys)
            & set(parent_context.source_context_keys)
        )
        or bool(source_relations)
    ):
        return

    _add_parent_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        evidence_reason=LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON,
        evidence_summary=(
            "Parent is the active configured direct parent in source order and shares "
            "an exact source-context key or deterministic table relation with the child."
        ),
        parent_context=parent_context,
        source_relations=source_relations,
    )


def _add_parent_conflict_evidence(
    *,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIFinalContext,
    scope_comparison: SFIHasChildScopeComparison,
    source_local_parent_scope_value_keys_by_type: dict[str, set[str]],
) -> None:
    """Attach structured-scope and typed source-label conflicts to a parent candidate.

    Conflicts are recorded only for candidates that already carry at least one positive
    retrieval signal, so a candidate is never introduced by conflict evidence alone.
    Conflicts lower retrieval rank but never remove a candidate that has other positive
    evidence.

    Parameters
    ----------
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    parent_context
        Candidate parent final SFI context.
    scope_comparison
        Exact structured-scope comparison between the child and the candidate parent.
    source_local_parent_scope_value_keys_by_type
        All controlled parent values recognized in typed local source labels.
    """

    endpoint_id = str(parent_context.final_sfi_uuid)

    if endpoint_id not in evidence_by_endpoint_id:
        return

    if scope_comparison.direct_parent_value_match is False:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=IDENTITY_SCOPE_DIRECT_PARENT_CONFLICT_REASON,
            evidence_summary=(
                "Candidate's canonical value conflicts with the child's finalized "
                "scope value for this direct parent statement type."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )

    if scope_comparison.conflicting_ancestor_statement_types:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=IDENTITY_SCOPE_ANCESTOR_CONFLICT_REASON,
            evidence_summary=(
                f"Candidate and child have conflicting finalized ancestor scope "
                f"dimensions: {scope_comparison.conflicting_ancestor_statement_types}."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )

    expected_local_value_keys = source_local_parent_scope_value_keys_by_type.get(
        parent_context.statement_type, set()
    )

    parent_value_key = _normalize_controlled_value_key(
        parent_context.canonical_statement_value_key
        or parent_context.canonical_statement_value
        or parent_context.description
    )

    if (
        expected_local_value_keys
        and parent_value_key
        and parent_value_key not in expected_local_value_keys
    ):
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_CONFLICT_REASON,
            evidence_summary=(
                "Candidate canonical value does not match any controlled value "
                "recognized in the child's typed bounded source-local labels. These "
                "labels may be cumulative or stale and require semantic review."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )


def _add_parent_evidence(
    *,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    evidence_reason: str,
    evidence_summary: str,
    parent_context: SFIFinalContext,
    scope_comparison: SFIHasChildScopeComparison | None = None,
    source_relations: Sequence[SFIHasChildSourceRelation] = (),
) -> None:
    """Add or update one non-root parent candidate evidence record.

    Parameters
    ----------
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    evidence_reason
        Machine-readable deterministic evidence channel.
    evidence_summary
        Human-readable evidence summary.
    parent_context
        Finalized context for the candidate parent.
    scope_comparison
        Optional exact structured-scope comparison with the current child.
    source_relations
        Deterministic child-relative source relations to preserve on the candidate.
    """

    endpoint_id = str(parent_context.final_sfi_uuid)

    if endpoint_id not in evidence_by_endpoint_id:
        evidence_by_endpoint_id[endpoint_id] = _ParentEvidence(
            SFIHasChildParentCandidate(
                canonical_statement_value=parent_context.canonical_statement_value,
                canonical_statement_value_key=(
                    parent_context.canonical_statement_value_key
                ),
                description=parent_context.description,
                endpoint_id=endpoint_id,
                endpoint_kind="StandardsFrameworkItem",
                evidence_reasons=[evidence_reason],
                evidence_summary=[evidence_summary],
                final_sfi_uuid=parent_context.final_sfi_uuid,
                identity_scope_key=parent_context.identity_scope_key,
                identity_scope_values=parent_context.identity_scope_values,
                is_root=False,
                normalized_statement_code=parent_context.normalized_statement_code,
                normalized_statement_type=parent_context.normalized_statement_type,
                scope_comparison=(scope_comparison or SFIHasChildScopeComparison()),
                source_context_keys=parent_context.source_context_keys,
                source_order=parent_context.source_order,
                source_page_indexes=parent_context.source_page_indexes,
                source_relations=list(source_relations),
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

    if scope_comparison is not None:
        evidence.candidate = evidence.candidate.model_copy(
            update={"scope_comparison": scope_comparison}
        )

    if source_relations:
        relations_by_key = {
            model_dump_key(relation): relation
            for relation in evidence.candidate.source_relations
        }

        for source_relation in source_relations:
            relations_by_key.setdefault(
                model_dump_key(source_relation), source_relation
            )

        evidence.candidate = evidence.candidate.model_copy(
            update={
                "source_relations": [
                    relations_by_key[key] for key in sorted(relations_by_key)
                ]
            }
        )


def _add_preceding_grouping_evidence(
    *,
    child_context: SFIFinalContext,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIFinalContext,
    scope_comparison: SFIHasChildScopeComparison,
) -> None:
    """Add preceding Standard Grouping evidence bounded by source-order distance.

    A nearer preceding grouping earns stronger nearest-grouping evidence, and a
    slightly more distant preceding grouping still earns compatible-statement-type
    evidence. The function is a no-op unless the candidate is a Standard Grouping that
    precedes the child in source order.

    Parameters
    ----------
    child_context
        Final SFI context for the child.
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    parent_context
        Candidate parent final SFI context.
    scope_comparison
        Exact structured-scope comparison between the child and the candidate parent.
    """

    if not (
        parent_context.normalized_statement_type == "Standard Grouping"
        and parent_context.source_order < child_context.source_order
    ):
        return

    distance = child_context.source_order - parent_context.source_order

    if distance <= 8:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=NEAREST_PRECEDING_GROUPING_REASON,
            evidence_summary=(
                f"Parent is a preceding Standard Grouping within {distance} "
                f"source-order units."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )

    if distance <= 12:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=STATEMENT_TYPE_COMPATIBLE_REASON,
            evidence_summary=(
                "Parent is a preceding Standard Grouping of an allowed direct "
                "parent statement type."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )


def _add_source_relation_evidence(
    *,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIFinalContext,
    scope_comparison: SFIHasChildScopeComparison,
    source_relations: Sequence[SFIHasChildSourceRelation],
) -> None:
    """Attach deterministic table relations to one parent candidate.

    Parameters
    ----------
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    parent_context
        Candidate parent final SFI context.
    scope_comparison
        Exact structured-scope comparison between child and candidate parent.
    source_relations
        Child-relative source relations recovered from anchors and table grids.
    """

    for source_relation in source_relations:
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=_source_relation_evidence_reason(source_relation),
            evidence_summary=_source_relation_evidence_summary(source_relation),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
            source_relations=[source_relation],
        )


def _add_source_visible_direct_parent_evidence(
    *,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIFinalContext,
    source_scope_grouping: bool,
) -> None:
    """Add explicit source-visible grouping evidence for one parent candidate.

    Typed controlled-value recognition is recorded separately because cumulative or
    stale hierarchy labels do not independently prove direct parentage. This stronger
    evidence label is reserved for an explicit source-scope grouping/header relation.

    Parameters
    ----------
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    parent_context
        Candidate parent final SFI context.
    source_scope_grouping
        Whether source structure explicitly presents the parent as a grouping/header
        for row-derived child content.
    """

    if not source_scope_grouping:
        return

    endpoint_id = str(parent_context.final_sfi_uuid)

    if endpoint_id not in evidence_by_endpoint_id:
        return

    _add_parent_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        evidence_reason=SOURCE_VISIBLE_DIRECT_PARENT_REASON,
        evidence_summary=(
            "Parent has explicit source-visible grouping/header evidence for the "
            "child's local source structure."
        ),
        parent_context=parent_context,
    )


def _bound_parent_candidates(
    *,
    child_context: SFIFinalContext,
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    non_root_candidates: Sequence[SFIHasChildParentCandidate],
) -> tuple[list[SFIHasChildParentCandidate], list[str], bool]:
    """Rank and bound non-root candidates while preserving a root fallback slot.

    Candidate bounding is deterministic retrieval, not semantic parent selection.
    Candidates with exact complete structured-scope matches, locally compatible
    configured code-parent hints, or explicit source-scope grouping evidence are never
    silently truncated. When those indispensable retrieval candidates alone exceed the
    configured bound, the function fails so the runtime bound can be increased instead
    of hiding evidence from the producer/checker LLMs.

    Parameters
    ----------
    child_context
        Finalized child context.
    framework_uuid
        Deterministic StandardsFramework root UUID.
    kg_config
        Runtime configuration containing the candidate-set maximum.
    non_root_candidates
        Retrieved non-root parent candidates before bounding.

    Returns
    -------
    tuple[list[SFIHasChildParentCandidate], list[str], bool]
        Bounded candidates including root fallback, truncation notes, and truncation
        status.

    Raises
    ------
    ValueError
        If indispensable retrieval candidates exceed the available non-root slots.
    """

    root_candidate = SFIHasChildParentCandidate(
        canonical_statement_value=None,
        canonical_statement_value_key=None,
        description=kg_config.metadata.framework_title or "StandardsFramework root",
        endpoint_id=str(framework_uuid),
        endpoint_kind="StandardsFramework",
        evidence_reasons=[ROOT_EVIDENCE_REASON],
        evidence_summary=["StandardsFramework root fallback is always available."],
        final_sfi_uuid=None,
        identity_scope_key=None,
        identity_scope_values={},
        is_root=True,
        normalized_statement_code=None,
        normalized_statement_type=None,
        scope_comparison=SFIHasChildScopeComparison(),
        source_context_keys=[],
        source_order=None,
        source_page_indexes=[],
        source_relations=[],
        source_segment_ids=[],
        source_window_indexes=[],
        statement_code=None,
        statement_type=None,
    )
    max_parent_candidates = kg_config.academic_standards.max_has_child_parent_candidates
    slots_for_non_root = max_parent_candidates - 1
    sorted_candidates = sorted(non_root_candidates, key=_parent_candidate_rank)
    indispensable_reasons = {
        CODE_PARENT_HINT_REASON,
        IDENTITY_SCOPE_COMPLETE_PARENT_MATCH_REASON,
        PARENT_CELL_APPLIES_TO_CHILD_ROW_REASON,
        SAME_RAW_TABLE_ROW_REASON,
        SAME_SOURCE_UNIT_REASON,
        SOURCE_SCOPE_GROUPING_REASON,
    }
    indispensable_candidates = [
        candidate
        for candidate in sorted_candidates
        if set(candidate.evidence_reasons) & indispensable_reasons
    ]

    if len(indispensable_candidates) > slots_for_non_root:
        raise ValueError(
            f"Child {child_context.final_sfi_uuid} has "
            f"{len(indispensable_candidates)} indispensable non-root parent "
            f"candidates, but max_has_child_parent_candidates="
            f"{max_parent_candidates} leaves only {slots_for_non_root} non-root "
            f"slots. Increase the runtime bound rather than truncating exact "
            f"structured-scope, source-relation, code-parent, or source-grouping "
            f"evidence."
        )

    selected_non_root = list(indispensable_candidates)
    selected_ids = {candidate.endpoint_id for candidate in selected_non_root}

    for candidate in sorted_candidates:
        if len(selected_non_root) >= slots_for_non_root:
            break

        if candidate.endpoint_id in selected_ids:
            continue

        selected_non_root.append(candidate)
        selected_ids.add(candidate.endpoint_id)

    was_truncated = len(non_root_candidates) > len(selected_non_root)
    truncation_notes: list[str] = []

    if was_truncated:
        truncation_notes.append(
            f"Truncated parent candidates from {len(non_root_candidates) + 1} to "
            f"{max_parent_candidates} for child {child_context.final_sfi_uuid} "
            f"({child_context.statement_type}), preserving indispensable retrieval "
            f"evidence and the StandardsFramework root fallback."
        )

    return [*selected_non_root, root_candidate], truncation_notes, was_truncated


def _build_active_outline_parent_map(
    *,
    contexts: Sequence[SFIFinalContext],
    kg_config: CreateKGConfig,
    parent_statement_types_by_child_type: dict[str, set[str]],
) -> dict[uuid.UUID, SFIFinalContext]:
    """Build active-outline parent candidates from finalized SFIs in source order.

    The map is generated from direct parent-type policy rather than only from a linear
    hierarchy. This supports branching cases where multiple child statement types share
    the same direct parent type, such as Topic directly parenting both Performance
    Objective and Content. The function is only a candidate-generation heuristic; final
    selection remains with the LLM and graph validation enforces the same type policy.

    Parameters
    ----------
    contexts
        Final SFI contexts to scan in source order.
    kg_config
        Runtime KG configuration containing hierarchy order for active-stack ranking.
    parent_statement_types_by_child_type
        Allowed direct parent statement types keyed by child statement_type.

    Returns
    -------
    dict[uuid.UUID, SFIFinalContext]
        Mapping of child final SFI UUID to one active direct parent context.
    """

    hierarchy = _get_hierarchy_statement_types(kg_config)

    if len(hierarchy) < 2:
        return {}

    rank_by_statement_type = {
        statement_type: rank for rank, statement_type in enumerate(hierarchy)
    }
    active_by_statement_type: dict[str, SFIFinalContext] = {}
    parent_by_child_uuid: dict[uuid.UUID, SFIFinalContext] = {}

    for context in sorted(
        contexts, key=lambda item: _context_source_position_key(context=item)
    ):
        allowed_parent_types = parent_statement_types_by_child_type.get(
            context.statement_type, set()
        )
        active_parent_options = [
            parent_context
            for parent_type, parent_context in active_by_statement_type.items()
            if parent_type in allowed_parent_types
        ]

        if active_parent_options:
            parent_by_child_uuid[context.final_sfi_uuid] = sorted(
                active_parent_options,
                key=lambda item: rank_by_statement_type.get(item.statement_type, -1),
                reverse=True,
            )[0]

        active_by_statement_type[context.statement_type] = context
        current_rank = rank_by_statement_type.get(context.statement_type)

        if current_rank is None:
            continue

        for stale_statement_type in list(active_by_statement_type):
            stale_rank = rank_by_statement_type.get(stale_statement_type)

            if stale_rank is not None and stale_rank > current_rank:
                del active_by_statement_type[stale_statement_type]

    return parent_by_child_uuid


def _build_candidate_parent_sets(
    *,
    contexts: Sequence[SFIFinalContext],
    extraction_windows: Sequence[ExtractionWindow],
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    sfi_final_records: Sequence[SFIFinalRecord],
    table_context_keys_by_uuid: dict[uuid.UUID, set[str]],
    table_occurrences_by_uuid: dict[uuid.UUID, tuple[_TableOccurrenceRef, ...]],
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
                _MATCHED_SECTION_PATH_LABEL_REASON,
                _NEARBY_SOURCE_CONTEXT_KEY_REASON,
                _NEAREST_PRECEDING_GROUPING_REASON,
                _STATEMENT_TYPE_COMPATIBLE_REASON,
            ]

        parent candidate: grade_4
            evidence_reasons=[
                _MATCHED_SECTION_PATH_LABEL_REASON,
                _STATEMENT_TYPE_COMPATIBLE_REASON,
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
    sfi_final_records
        Finalized SFI records containing exact candidate source anchors.
    table_context_keys_by_uuid
        Paired table row/header context keys keyed by final SFI UUID. These keys are
        recovered from candidate-level source refs rather than flattened final-record
        segment/index aggregates, so table evidence does not cross-combine unrelated
        source segments and row/header indexes.
    table_occurrences_by_uuid
        Candidate-level table occurrences keyed by final SFI UUID. Each occurrence
        preserves the segment, window, and row/header indexes from one source ref.

    Returns
    -------
    list[SFIHasChildCandidateParentSet]
        Bounded parent candidate sets, one per finalized SFI.
    """

    parent_sets: list[SFIHasChildCandidateParentSet] = []
    parent_requirements_by_child_type = _build_parent_requirements_by_child_type(
        kg_config
    )
    parent_statement_types_by_child_type = _build_direct_parent_statement_types(
        kg_config
    )
    records_by_uuid = {record.final_sfi_uuid: record for record in sfi_final_records}
    source_row_by_grid_position = _build_table_grid_source_row_index(extraction_windows)
    source_units_by_id = _build_source_unit_map_from_windows(extraction_windows)
    table_source_units_by_uuid = _build_table_source_unit_refs_by_uuid(
        sfi_final_records=sfi_final_records, source_units_by_id=source_units_by_id
    )
    value_match_policies = _build_controlled_value_match_policies(kg_config)
    outline_parent_by_child_uuid = _build_active_outline_parent_map(
        contexts=contexts,
        kg_config=kg_config,
        parent_statement_types_by_child_type=parent_statement_types_by_child_type,
    )
    table_keys_by_uuid = table_context_keys_by_uuid

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
        child_source_units = table_source_units_by_uuid[child_context.final_sfi_uuid]
        child_table_keys = table_keys_by_uuid[child_context.final_sfi_uuid]
        child_table_occurrences = table_occurrences_by_uuid[
            child_context.final_sfi_uuid
        ]
        evidence_by_endpoint_id: dict[str, _ParentEvidence] = {}
        source_local_parent_scope_value_keys_by_type = (
            _collect_source_local_parent_scope_value_keys(
                child_context=child_context,
                kg_config=kg_config,
                parent_statement_types=parent_statement_types_by_child_type.get(
                    child_context.statement_type, set()
                ),
                value_match_policies=value_match_policies,
            )
        )

        # The active outline stack is a source-order retrieval heuristic. A typed
        # source-local conflict is exposed later as evidence and never removes this
        # candidate before producer/checker review.
        if outline_parent_context := outline_parent_by_child_uuid.get(
            child_context.final_sfi_uuid
        ):
            outline_source_relations = _build_source_relations(
                child_source_units=child_source_units,
                parent_source_units=table_source_units_by_uuid[
                    outline_parent_context.final_sfi_uuid
                ],
                source_row_by_grid_position=source_row_by_grid_position,
            )
            outline_parent_record = records_by_uuid[
                outline_parent_context.final_sfi_uuid
            ]
            shares_exact_source_context = bool(
                set(child_context.source_context_keys)
                & set(outline_parent_context.source_context_keys)
            )

            if (
                not _record_has_multiple_source_occurrences(outline_parent_record)
                or shares_exact_source_context
                or outline_source_relations
            ):
                _add_parent_evidence(
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    evidence_reason=ACTIVE_OUTLINE_STACK_PARENT_REASON,
                    evidence_summary=(
                        "Parent is the active preceding finalized SFI of the "
                        "configured immediate parent statement type in source order. "
                        "This is retrieval evidence only; the producer/checker must "
                        "confirm the direct hasChild parent."
                    ),
                    parent_context=outline_parent_context,
                    scope_comparison=_build_parent_scope_comparison(
                        child_context=child_context,
                        parent_context=outline_parent_context,
                    ),
                    source_relations=outline_source_relations,
                )
                _add_local_active_outline_direct_parent_evidence(
                    child_context=child_context,
                    evidence_by_endpoint_id=evidence_by_endpoint_id,
                    parent_context=outline_parent_context,
                    source_relations=outline_source_relations,
                )

        for parent_context in contexts:
            if parent_context.final_sfi_uuid == child_context.final_sfi_uuid:
                continue

            if parent_context.statement_type not in (
                parent_statement_types_by_child_type.get(
                    child_context.statement_type, set()
                )
            ):
                continue

            source_relations = _build_source_relations(
                child_source_units=child_source_units,
                parent_source_units=table_source_units_by_uuid[
                    parent_context.final_sfi_uuid
                ],
                source_row_by_grid_position=source_row_by_grid_position,
            )

            _evaluate_parent_child_relationship(
                child_code=child_code,
                child_context=child_context,
                child_table_keys=child_table_keys,
                child_table_occurrences=child_table_occurrences,
                code_parent_pairs=code_parent_pairs,
                evidence_by_endpoint_id=evidence_by_endpoint_id,
                parent_context=parent_context,
                parent_statement_types_by_child_type=parent_statement_types_by_child_type,
                parent_table_keys=table_keys_by_uuid[parent_context.final_sfi_uuid],
                parent_table_occurrences=table_occurrences_by_uuid[
                    parent_context.final_sfi_uuid
                ],
                source_local_parent_scope_value_keys_by_type=(
                    source_local_parent_scope_value_keys_by_type
                ),
                source_relations=source_relations,
            )

        _suppress_ambiguous_active_outline_evidence(evidence_by_endpoint_id)

        parent_set = _finalize_candidate_parent_set(
            child_context=child_context,
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            framework_uuid=framework_uuid,
            kg_config=kg_config,
            parent_requirements=parent_requirements_by_child_type.get(
                child_context.statement_type, []
            ),
        )
        parent_sets.append(parent_set)

    return parent_sets


def _build_controlled_value_match_policies(
    kg_config: CreateKGConfig,
) -> dict[str, _ControlledValueMatchPolicy]:
    """Build normalized controlled-value match policies from runtime config.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing statement-type controlled values.

    Returns
    -------
    dict[str, _ControlledValueMatchPolicy]
        Match policies keyed by source-facing statement_type. Statement types without
        configured controlled values are omitted.
    """

    policies: dict[str, _ControlledValueMatchPolicy] = {}

    for statement_type_policy in kg_config.academic_standards.statement_type_policy:
        alias_to_value_key: dict[str, str] = {}

        for controlled_value in statement_type_policy.controlled_values:
            canonical_value_key = _normalize_controlled_value_key(
                controlled_value.canonical_value
            )

            if not canonical_value_key:
                continue

            aliases = [controlled_value.canonical_value, *controlled_value.aliases]

            for alias in aliases:
                alias_key = _normalize_controlled_value_key(alias)

                if alias_key:
                    alias_to_value_key[alias_key] = canonical_value_key

        if alias_to_value_key:
            policies[statement_type_policy.statement_type] = (
                _ControlledValueMatchPolicy(alias_to_value_key=alias_to_value_key)
            )

    return policies


def _build_direct_parent_statement_types(
    kg_config: CreateKGConfig,
) -> dict[str, set[str]]:
    """Build allowed direct parent statement types from the unified policy.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing the complete hasChild parent policy.

    Returns
    -------
    dict[str, set[str]]
        Allowed direct parent statement types keyed by child statement_type.
    """

    return {
        child_type: {entry.parent_statement_type for entry in parent_policy_entries}
        for child_type, parent_policy_entries in (
            kg_config.academic_standards.sfi_has_child_parent_policy.items()
        )
    }


def _build_edge(
    *,
    checker_passed: bool,
    checker_rationale: str,
    child_context: SFIFinalContext,
    confidence: float,
    document_ir: DocumentIR,
    framework_uuid: uuid.UUID,
    llm_reason: str,
    parent_candidate: SFIHasChildParentCandidate,
    resolution_origin: str,
    resolution_request_id: str,
    unresolved_root_fallback: bool,
) -> SFIHasChildEdge:
    """Build one deterministic final hasChild edge with checker provenance.

    Parameters
    ----------
    checker_passed
        Whether the independent checker accepted the producer response unchanged.
    checker_rationale
        Checker assessment for the complete request.
    child_context
        Finalized child context.
    confidence
        Confidence from the accepted producer or checker-corrected response.
    document_ir
        Source DocumentIR used to scope relationship identity.
    framework_uuid
        StandardsFramework root UUID.
    llm_reason
        Source-grounded reason from the accepted response.
    parent_candidate
        Selected bounded parent candidate.
    resolution_origin
        `producer_accepted` or `checker_corrected`.
    resolution_request_id
        Deterministic request ID.
    unresolved_root_fallback
        Whether the edge is an unresolved root fallback.

    Returns
    -------
    SFIHasChildEdge
        Deterministic hasChild edge artifact.
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
            "checker_passed": checker_passed,
            "checker_rationale": checker_rationale,
            "doc_key": document_ir.doc_key,
            "parent_evidence_summary": parent_candidate.evidence_summary,
            "parent_scope_comparison": parent_candidate.scope_comparison.model_dump(
                mode="json"
            ),
            "parent_source_relations": [
                source_relation.model_dump(mode="json")
                for source_relation in parent_candidate.source_relations
            ],
            "relationship_identity_key": relationship_key,
            "resolution_origin": resolution_origin,
            "resolution_request_id": resolution_request_id,
            "source_hierarchy_audit": _relationship_code_anomaly_metadata(
                child_context=child_context,
                parent_candidate=parent_candidate,
            ),
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
    requests: Sequence[SFIHasChildResolutionRequest],
    responses: Sequence[SFIHasChildResolutionResponse],
    validation_verdicts: Sequence[SFIHasChildValidationVerdict],
) -> list[SFIHasChildEdge]:
    """Convert accepted producer/checker responses into deterministic edges.

    Parameters
    ----------
    document_ir
        Source DocumentIR used for relationship identity.
    framework_uuid
        StandardsFramework root UUID.
    parent_sets
        Bounded parent candidate sets.
    requests
        Original deterministic producer/checker requests.
    responses
        Final accepted or checker-corrected responses.
    validation_verdicts
        Independent checker verdicts aligned to requests and responses.

    Returns
    -------
    list[SFIHasChildEdge]
        Final deterministic hasChild edges.

    Raises
    ------
    ValueError
        If request, response, and verdict counts are not aligned.
    """

    if not len(requests) == len(responses) == len(validation_verdicts):
        raise ValueError(
            f"hasChild request, final response, and checker verdict counts must align: "
            f"requests={len(requests)}, responses={len(responses)}, "
            f"verdicts={len(validation_verdicts)}."
        )

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

    for request, response, validation_verdict in zip(
        requests, responses, validation_verdicts, strict=True
    ):
        checker_passed = validation_verdict.passed
        resolution_origin = (
            "producer_accepted" if checker_passed else "checker_corrected"
        )

        for child_resolution in response.child_resolutions:
            child_id = str(child_resolution.child_final_sfi_uuid)
            child_context = child_context_by_id[child_id]
            parent_candidates = parent_candidates_by_child_id[child_id]

            if child_resolution.unresolved:
                root_candidate = SFIHasChildParentCandidate(
                    canonical_statement_value=None,
                    canonical_statement_value_key=None,
                    description="StandardsFramework root fallback",
                    endpoint_id=str(framework_uuid),
                    endpoint_kind="StandardsFramework",
                    evidence_reasons=[ROOT_EVIDENCE_REASON],
                    evidence_summary=[
                        "StandardsFramework root fallback is always available."
                    ],
                    final_sfi_uuid=None,
                    identity_scope_key=None,
                    identity_scope_values={},
                    is_root=True,
                    normalized_statement_code=None,
                    normalized_statement_type=None,
                    scope_comparison=SFIHasChildScopeComparison(),
                    source_context_keys=[],
                    source_order=None,
                    source_page_indexes=[],
                    source_segment_ids=[],
                    source_window_indexes=[],
                    statement_code=None,
                    statement_type=None,
                )
                edges.append(
                    _build_edge(
                        checker_passed=checker_passed,
                        checker_rationale=validation_verdict.rationale,
                        child_context=child_context,
                        confidence=child_resolution.confidence,
                        document_ir=document_ir,
                        framework_uuid=framework_uuid,
                        llm_reason=child_resolution.reason,
                        parent_candidate=root_candidate,
                        resolution_origin=resolution_origin,
                        resolution_request_id=request.request_id,
                        unresolved_root_fallback=True,
                    )
                )
                continue

            for parent_endpoint_id in child_resolution.selected_parent_endpoint_ids:
                parent_candidate = parent_candidates[parent_endpoint_id]
                edges.append(
                    _build_edge(
                        checker_passed=checker_passed,
                        checker_rationale=validation_verdict.rationale,
                        child_context=child_context,
                        confidence=child_resolution.confidence,
                        document_ir=document_ir,
                        framework_uuid=framework_uuid,
                        llm_reason=child_resolution.reason,
                        parent_candidate=parent_candidate,
                        resolution_origin=resolution_origin,
                        resolution_request_id=request.request_id,
                        unresolved_root_fallback=False,
                    )
                )

    edges.sort(
        key=lambda edge: (str(edge.target_sfi_uuid), str(edge.source_entity_uuid))
    )
    return edges


def _build_parent_requirements_by_child_type(
    kg_config: CreateKGConfig,
) -> dict[str, list[SFIHasChildParentRequirement]]:
    """Build request-facing parent requirements from the runtime policy.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing parent cardinalities.

    Returns
    -------
    dict[str, list[SFIHasChildParentRequirement]]
        Parent requirements keyed by child statement_type in configured order.
    """

    return {
        child_type: [
            SFIHasChildParentRequirement(
                max_count=entry.max_count,
                min_count=entry.min_count,
                parent_statement_type=entry.parent_statement_type,
            )
            for entry in parent_policy_entries
        ]
        for child_type, parent_policy_entries in (
            kg_config.academic_standards.sfi_has_child_parent_policy.items()
        )
    }


def _build_parent_scope_comparison(
    *, child_context: SFIFinalContext, parent_context: SFIFinalContext
) -> SFIHasChildScopeComparison:
    """Compare finalized child scope with one candidate parent's value and scope.

    The comparison is deterministic and curriculum-neutral. It records exact structured
    matches, conflicts, and missing dimensions without deciding whether the candidate
    is the semantic direct parent.

    Parameters
    ----------
    child_context
        Finalized child context.
    parent_context
        Candidate parent context.

    Returns
    -------
    SFIHasChildScopeComparison
        Structured comparison exposed to the producer and checker LLMs.
    """

    child_parent_value = _scope_value_for_statement_type(
        scope_values=child_context.identity_scope_values,
        statement_type=parent_context.statement_type,
    )
    parent_value_key = (
        parent_context.canonical_statement_value_key
        or _normalize_controlled_value_key(
            parent_context.canonical_statement_value or parent_context.description
        )
    )
    child_parent_value_key = _normalize_controlled_value_key(child_parent_value)
    direct_parent_value_match: bool | None = None

    if child_parent_value_key and parent_value_key:
        direct_parent_value_match = child_parent_value_key == parent_value_key

    conflicting_ancestor_statement_types: list[str] = []
    matching_ancestor_statement_types: list[str] = []
    missing_child_ancestor_statement_types: list[str] = []

    for ancestor_statement_type, parent_scope_value in sorted(
        parent_context.identity_scope_values.items(),
        key=lambda item: item[0].casefold(),
    ):
        child_scope_value = _scope_value_for_statement_type(
            scope_values=child_context.identity_scope_values,
            statement_type=ancestor_statement_type,
        )

        if child_scope_value is None:
            missing_child_ancestor_statement_types.append(ancestor_statement_type)
            continue

        child_scope_value_key = _normalize_controlled_value_key(child_scope_value)
        parent_scope_value_key = _normalize_controlled_value_key(parent_scope_value)

        if child_scope_value_key and child_scope_value_key == parent_scope_value_key:
            matching_ancestor_statement_types.append(ancestor_statement_type)
        else:
            conflicting_ancestor_statement_types.append(ancestor_statement_type)

    complete_match = bool(
        direct_parent_value_match is True
        and not conflicting_ancestor_statement_types
        and not missing_child_ancestor_statement_types
    )
    return SFIHasChildScopeComparison(
        complete_match=complete_match,
        conflicting_ancestor_statement_types=conflicting_ancestor_statement_types,
        direct_parent_statement_type=parent_context.statement_type,
        direct_parent_value_match=direct_parent_value_match,
        matching_ancestor_statement_types=matching_ancestor_statement_types,
        missing_child_ancestor_statement_types=missing_child_ancestor_statement_types,
    )


def _build_source_relations(
    *,
    child_source_units: Sequence[_TableSourceUnitRef],
    parent_source_units: Sequence[_TableSourceUnitRef],
    source_row_by_grid_position: dict[tuple[str, int, int], int],
) -> list[SFIHasChildSourceRelation]:
    """Build exact child-relative table relations for one parent candidate.

    Parameters
    ----------
    child_source_units
        Exact table-body units cited by the child.
    parent_source_units
        Exact table-body units cited by the candidate parent.
    source_row_by_grid_position
        Table-grid origin-row index derived from persisted grid_sources metadata.

    Returns
    -------
    list[SFIHasChildSourceRelation]
        Unique deterministic source relations sorted by serialized content.
    """

    relations_by_key: dict[str, SFIHasChildSourceRelation] = {}

    for child_source_unit in child_source_units:
        for parent_source_unit in parent_source_units:
            if (
                child_source_unit.source_segment_id
                != parent_source_unit.source_segment_id
            ):
                continue

            relation_kind: str | None = None

            if child_source_unit.source_unit_id == parent_source_unit.source_unit_id:
                relation_kind = "same_source_unit"
            elif child_source_unit.row_index == parent_source_unit.row_index:
                relation_kind = "same_raw_table_row"
            elif parent_source_unit.row_index < child_source_unit.row_index:
                parent_column_indexes = range(
                    parent_source_unit.column_start_index,
                    parent_source_unit.column_end_index_exclusive,
                )
                parent_cell_applies = all(
                    source_row_by_grid_position.get(
                        (
                            child_source_unit.source_segment_id,
                            child_source_unit.row_index,
                            column_index,
                        )
                    )
                    == parent_source_unit.row_index
                    for column_index in parent_column_indexes
                )

                if parent_cell_applies:
                    relation_kind = "parent_cell_applies_to_child_row"

            if relation_kind is None:
                continue

            relation = SFIHasChildSourceRelation(
                child_row_index=child_source_unit.row_index,
                child_source_text=child_source_unit.source_text,
                child_source_unit_id=child_source_unit.source_unit_id,
                parent_column_end_index_exclusive=(
                    parent_source_unit.column_end_index_exclusive
                ),
                parent_column_start_index=parent_source_unit.column_start_index,
                parent_origin_row_index=parent_source_unit.row_index,
                parent_source_text=parent_source_unit.source_text,
                parent_source_unit_id=parent_source_unit.source_unit_id,
                relation_kind=relation_kind,
                source_segment_id=child_source_unit.source_segment_id,
            )
            relations_by_key.setdefault(model_dump_key(relation), relation)

    return [relations_by_key[key] for key in sorted(relations_by_key)]


def _build_source_unit_map_from_windows(
    extraction_windows: Sequence[ExtractionWindow],
) -> dict[str, SFISourceUnit]:
    """Build a run-wide exact source-unit map from persisted extraction windows.

    Overlapping windows may repeat the same stable source unit. Repeated definitions
    must be identical so relationship evidence cannot depend on which window copy was
    encountered first.

    Parameters
    ----------
    extraction_windows
        Persisted source-faithful extraction windows.

    Returns
    -------
    dict[str, SFISourceUnit]
        Stable source units keyed by source_unit_id.

    Raises
    ------
    ValueError
        If overlapping windows define one source_unit_id inconsistently.
    """

    source_units_by_id: dict[str, SFISourceUnit] = {}

    for extraction_window in extraction_windows:
        for source_unit_id, source_unit in build_sfi_source_unit_map(
            extraction_window
        ).items():
            existing_source_unit = source_units_by_id.get(source_unit_id)

            if existing_source_unit is not None and existing_source_unit != source_unit:
                raise ValueError(
                    f"Extraction windows define source unit {source_unit_id!r} "
                    f"inconsistently."
                )

            source_units_by_id[source_unit_id] = source_unit

    return source_units_by_id


def _build_table_grid_source_row_index(
    extraction_windows: Sequence[ExtractionWindow],
) -> dict[tuple[str, int, int], int]:
    """Index table-grid origin rows by segment, selected row, and column.

    Parameters
    ----------
    extraction_windows
        Persisted extraction windows containing aligned grid_sources metadata.

    Returns
    -------
    dict[tuple[str, int, int], int]
        Mapping from `(segment_id, child_row_index, column_index)` to the raw row that
        originated the visible grid value.

    Raises
    ------
    ValueError
        If table payload alignment is invalid or overlapping windows disagree.
    """

    source_row_by_grid_position: dict[tuple[str, int, int], int] = {}

    for extraction_window in extraction_windows:
        table = extraction_window.table

        if table is None or table.grid_sources is None:
            continue

        if len(extraction_window.source_segment_ids) != 1:
            raise ValueError(
                "Table extraction windows must reference exactly one source segment."
            )

        if len(table.grid_sources) != len(table.row_indexes):
            raise ValueError(
                f"Table window {extraction_window.window_id!r} has misaligned "
                f"grid_sources and row_indexes."
            )

        source_segment_id = extraction_window.source_segment_ids[0]

        for grid_sources, row_index in zip(table.grid_sources, table.row_indexes):
            if len(grid_sources) != table.n_cols:
                raise ValueError(
                    f"Table window {extraction_window.window_id!r} row {row_index} "
                    f"has grid_sources that do not match n_cols."
                )

            for column_index, source_info in enumerate(grid_sources):
                source_row = source_info.get("source_row")

                if not isinstance(source_row, int):
                    continue

                grid_key = (source_segment_id, row_index, column_index)
                existing_source_row = source_row_by_grid_position.get(grid_key)

                if (
                    existing_source_row is not None
                    and existing_source_row != source_row
                ):
                    raise ValueError(
                        f"Overlapping extraction windows disagree on grid origin "
                        f"for segment {source_segment_id!r}, row {row_index}, "
                        f"column {column_index}."
                    )

                source_row_by_grid_position[grid_key] = source_row

    return source_row_by_grid_position


def _build_table_source_unit_refs_by_uuid(
    *,
    sfi_final_records: Sequence[SFIFinalRecord],
    source_units_by_id: dict[str, SFISourceUnit],
) -> dict[uuid.UUID, list[_TableSourceUnitRef]]:
    """Recover exact table-body source units for every finalized SFI.

    Parameters
    ----------
    sfi_final_records
        Finalized SFIs whose candidate anchors preserve source_unit_id values.
    source_units_by_id
        Run-wide exact source-unit map built from persisted extraction windows.

    Returns
    -------
    dict[uuid.UUID, list[_TableSourceUnitRef]]
        Sorted table-body source-unit references keyed by final SFI UUID.

    Raises
    ------
    ValueError
        If a table-body source anchor cannot be resolved to a persisted source unit.
    """

    refs_by_uuid: dict[uuid.UUID, list[_TableSourceUnitRef]] = {}

    for record in sfi_final_records:
        table_refs: list[_TableSourceUnitRef] = []

        for source_unit_id in _source_unit_ids_from_record(record):
            source_unit = source_units_by_id.get(source_unit_id)

            if source_unit is None:
                if "|table_body_cell|" in source_unit_id:
                    raise ValueError(
                        f"Final SFI {record.final_sfi_uuid} references table source "
                        f"unit {source_unit_id!r}, which is absent from persisted "
                        f"extraction windows."
                    )

                continue

            table_ref = _parse_table_source_unit(
                source_unit=source_unit, source_unit_id=source_unit_id
            )

            if table_ref is not None:
                table_refs.append(table_ref)

        refs_by_uuid[record.final_sfi_uuid] = sorted(
            table_refs,
            key=lambda item: (
                item.source_segment_id,
                item.row_index,
                item.column_start_index,
                item.column_end_index_exclusive,
                item.source_unit_id,
            ),
        )

    return refs_by_uuid


def _collect_source_local_parent_scope_value_keys(
    *,
    child_context: SFIFinalContext,
    kg_config: CreateKGConfig,
    parent_statement_types: set[str],
    value_match_policies: dict[str, _ControlledValueMatchPolicy],
) -> dict[str, set[str]]:
    """Collect controlled parent values recognized in typed local source labels.

    The function preserves every recognized value from bounded hierarchy-bearing
    labels. It does not select the active value or treat nearest-first label order as
    an asserted hierarchy stack. The producer/checker LLMs receive matches and
    conflicts as evidence and make the semantic parent decision.

    Parameters
    ----------
    child_context
        Finalized child context whose typed local labels are inspected.
    kg_config
        Runtime configuration containing statement-type aliases.
    parent_statement_types
        Allowed direct parent statement types for the child.
    value_match_policies
        Controlled-value recognition policies keyed by statement type.

    Returns
    -------
    dict[str, set[str]]
        All recognized controlled value keys keyed by parent statement type.
    """

    collected_value_keys_by_type: dict[str, set[str]] = {}
    local_texts_by_parent_type = _typed_source_local_parent_texts_by_type(
        child_context=child_context,
        kg_config=kg_config,
        parent_statement_types=parent_statement_types,
    )

    for parent_statement_type in sorted(parent_statement_types):
        policy = value_match_policies.get(parent_statement_type)

        if policy is None:
            continue

        value_keys: set[str] = set()

        for local_text in local_texts_by_parent_type.get(parent_statement_type, []):
            value_keys.update(
                _controlled_value_keys_in_text(policy=policy, text=local_text)
            )

        if value_keys:
            collected_value_keys_by_type[parent_statement_type] = value_keys

    return collected_value_keys_by_type


def _context_matches_section_path(
    *, child_context: SFIFinalContext, parent_context: SFIFinalContext
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
        normalize_text(value)
        for value in [
            parent_context.description,
            *parent_context.candidate_source_texts,
        ]
    )
    section_texts = unique_nonempty(
        normalize_text(value) for value in child_context.section_path_labels
    )

    if not parent_texts or not section_texts:
        return False

    return any(
        parent_text == section_text or parent_text in section_text
        for parent_text in parent_texts
        for section_text in section_texts
    )


def _context_source_position_key(
    context: SFIFinalContext,
) -> tuple[int, int, int, int, str]:
    """Build a deterministic source-order key for active outline scanning.

    Parameters
    ----------
    context
        Final SFI context to order.

    Returns
    -------
    tuple[int, int, int, int, str]
        Sort key using DocumentIR source order, earliest extraction window index,
        earliest cited table row/header index, parsed within-window candidate order,
        and final UUID.
    """

    source_window_index = min(context.source_window_indexes, default=1_000_000)
    table_position = min(
        [*context.table_header_indexes, *context.table_row_indexes], default=1_000_000
    )

    # Determine the earliest within-window registry candidate position.
    candidate_positions: list[int] = []
    for candidate_id in context.source_registry_candidate_ids:
        parts = str(candidate_id).split(":")

        # Registry candidate IDs are shaped like `w0143:sfi_2:...`.
        if len(parts) >= 2 and parts[1].startswith("sfi_"):
            try:
                candidate_positions.append(int(parts[1].removeprefix("sfi_")))
            except ValueError:
                continue

    return (
        context.source_order,
        source_window_index,
        table_position,
        min(candidate_positions, default=1_000_000),
        str(context.final_sfi_uuid),
    )


def _controlled_value_keys_in_text(
    *, policy: _ControlledValueMatchPolicy, text: str
) -> set[str]:
    """Find configured controlled-value keys mentioned in source-local text.

    Matching is generic and curriculum-agnostic: exact aliases match directly, and
    aliases may also match as complete normalized phrases inside a broader section or
    table label. This handles labels such as `Mathematics Syllabus for P3` when the
    configured controlled value is `P3` without hard-coding any grade terminology.

    Parameters
    ----------
    policy
        Controlled-value match policy for the parent statement type.
    text
        Source-local label or description to inspect.

    Returns
    -------
    set[str]
        Normalized canonical controlled-value keys found in the supplied text.
    """

    text_key = _normalize_controlled_value_key(text)

    if not text_key:
        return set()

    matched_value_keys: set[str] = set()
    exact_value_key = policy.alias_to_value_key.get(text_key)

    if exact_value_key:
        return {exact_value_key}

    padded_text_key = f" {text_key} "

    for alias_key, canonical_value_key in policy.alias_to_value_key.items():
        if alias_key.isdigit() or len(alias_key) < 2:
            continue

        if f" {alias_key} " in padded_text_key:
            matched_value_keys.add(canonical_value_key)

    return matched_value_keys


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
    child_context: SFIFinalContext,
    child_table_keys: set[str],
    child_table_occurrences: Sequence[_TableOccurrenceRef],
    code_parent_pairs: set[tuple[str, str]],
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    parent_context: SFIFinalContext,
    parent_statement_types_by_child_type: dict[str, set[str]],
    parent_table_keys: set[str],
    parent_table_occurrences: Sequence[_TableOccurrenceRef],
    source_local_parent_scope_value_keys_by_type: dict[str, set[str]],
    source_relations: Sequence[SFIHasChildSourceRelation],
) -> None:
    """Collect deterministic retrieval evidence for one possible parent.

    The function compares configured statement types, finalized structured scope,
    source locality, table context, code-parent hints, and source order. It never
    selects or rejects a semantic parent. Conflicts are attached only to candidates
    that have at least one positive retrieval signal.

    Parameters
    ----------
    child_code
        Normalized child code, when available.
    child_context
        Finalized child context.
    child_table_keys
        Paired table-local context keys for the child.
    child_table_occurrences
        Candidate-level child table occurrences preserving paired source provenance.
    code_parent_pairs
        Locally extracted normalized child-code to parent-code hints.
    evidence_by_endpoint_id
        Mutable evidence accumulator.
    parent_context
        Candidate parent context.
    parent_statement_types_by_child_type
        Allowed direct parent statement types keyed by child statement type.
    parent_table_keys
        Paired table-local context keys for the candidate parent.
    parent_table_occurrences
        Candidate-level parent table occurrences preserving paired source provenance.
    source_local_parent_scope_value_keys_by_type
        All controlled parent values recognized in typed local source labels.
    source_relations
        Exact child-relative table relations for this parent candidate.
    """

    if parent_context.statement_type not in parent_statement_types_by_child_type.get(
        child_context.statement_type, set()
    ):
        return

    scope_comparison = _build_parent_scope_comparison(
        child_context=child_context, parent_context=parent_context
    )
    parent_code = normalize_code(parent_context.normalized_statement_code)
    source_local_controlled_parent_scope_match = (
        _source_local_parent_scope_matches_parent(
            parent_context=parent_context,
            source_local_parent_scope_value_keys_by_type=(
                source_local_parent_scope_value_keys_by_type
            ),
        )
    )
    matched_section_path_label = _context_matches_section_path(
        child_context=child_context, parent_context=parent_context
    )
    nearby_source_window = _is_nearby_source_window(
        child_context=child_context, parent_context=parent_context
    )
    same_source_context_key = bool(
        set(child_context.source_context_keys) & set(parent_context.source_context_keys)
    )
    same_source_segment = bool(
        set(child_context.source_segment_ids) & set(parent_context.source_segment_ids)
    )
    same_source_window = bool(
        set(child_context.source_window_ids) & set(parent_context.source_window_ids)
    )
    same_table_context = bool(child_table_keys & parent_table_keys)
    source_scope_grouping = _is_source_scope_grouping(
        child_occurrences=child_table_occurrences,
        parent_context=parent_context,
        parent_occurrences=parent_table_occurrences,
    )

    _add_identity_scope_match_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        parent_context=parent_context,
        scope_comparison=scope_comparison,
    )
    _add_source_relation_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        parent_context=parent_context,
        scope_comparison=scope_comparison,
        source_relations=source_relations,
    )

    simple_rules: tuple[tuple[bool, str, str], ...] = (
        (
            source_local_controlled_parent_scope_match,
            SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_REASON,
            (
                "Candidate controlled value appears in at least one typed bounded "
                "source-local label for the configured direct parent type."
            ),
        ),
        (
            same_source_context_key,
            SAME_SOURCE_CONTEXT_KEY_REASON,
            "Child and parent share a registry source-context key.",
        ),
        (
            same_source_segment,
            SAME_SOURCE_SEGMENT_REASON,
            "Child and parent share a DocumentIR source segment.",
        ),
        (
            same_source_window,
            SAME_SOURCE_WINDOW_REASON,
            "Child and parent share an extraction window.",
        ),
        (
            same_table_context,
            SAME_TABLE_CONTEXT_REASON,
            "Child and parent share cited table row/header context.",
        ),
        (
            source_scope_grouping,
            SOURCE_SCOPE_GROUPING_REASON,
            (
                "Parent is an explicit source-scope grouping/header for row-derived "
                "child content in the same source segment/window."
            ),
        ),
        (
            matched_section_path_label,
            MATCHED_SECTION_PATH_LABEL_REASON,
            "Parent description matches recovered child section-path evidence.",
        ),
        (
            nearby_source_window,
            NEARBY_SOURCE_CONTEXT_KEY_REASON,
            "Parent appears in a nearby source window.",
        ),
    )

    for matched, reason, summary in simple_rules:
        if matched:
            _add_parent_evidence(
                evidence_by_endpoint_id=evidence_by_endpoint_id,
                evidence_reason=reason,
                evidence_summary=summary,
                parent_context=parent_context,
                scope_comparison=scope_comparison,
            )

    if _should_add_code_parent_hint_evidence(
        child_code=child_code,
        child_context=child_context,
        child_table_keys=child_table_keys,
        code_parent_pairs=code_parent_pairs,
        parent_code=parent_code,
        parent_context=parent_context,
        parent_table_keys=parent_table_keys,
    ):
        _add_parent_evidence(
            evidence_by_endpoint_id=evidence_by_endpoint_id,
            evidence_reason=CODE_PARENT_HINT_REASON,
            evidence_summary=(
                f"Configured code-parent hint maps child code {child_code!r} to "
                f"parent code {parent_code!r}, with compatible local source evidence."
            ),
            parent_context=parent_context,
            scope_comparison=scope_comparison,
        )

    _add_preceding_grouping_evidence(
        child_context=child_context,
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        parent_context=parent_context,
        scope_comparison=scope_comparison,
    )

    _add_source_visible_direct_parent_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        parent_context=parent_context,
        source_scope_grouping=source_scope_grouping,
    )

    _add_parent_conflict_evidence(
        evidence_by_endpoint_id=evidence_by_endpoint_id,
        parent_context=parent_context,
        scope_comparison=scope_comparison,
        source_local_parent_scope_value_keys_by_type=(
            source_local_parent_scope_value_keys_by_type
        ),
    )


def _evidence_has_decisive_direct_parent_support(evidence_reasons: set[str]) -> bool:
    """Return whether evidence contains corroborated direct-parent support.

    Decisive reasons are restricted to locally compatible code-parent hints, typed
    source-local controlled parent matches, and explicit source-scope grouping/header
    structure. Generic proximity signals are intentionally excluded.

    Parameters
    ----------
    evidence_reasons
        Evidence reasons accumulated for one parent candidate.

    Returns
    -------
    bool
        True when the reason set contains decisive direct-parent evidence.
    """

    return bool(evidence_reasons & DECISIVE_DIRECT_PARENT_REASONS)


def _evidence_has_soft_carry_forward_support(evidence_reasons: set[str]) -> bool:
    """Return whether evidence contains soft carry-forward parent support.

    Soft carry-forward support keeps plausible outline, section-path, and nearby
    source-order parents in the bounded candidate set without treating them as direct
    source-visible truth when a stronger same-type local parent is present.

    Parameters
    ----------
    evidence_reasons
        Evidence reasons accumulated for one parent candidate.

    Returns
    -------
    bool
        True when the reason set contains a conservative carry-forward pattern.
    """

    return (
        ACTIVE_OUTLINE_STACK_PARENT_REASON in evidence_reasons
        and MATCHED_SECTION_PATH_LABEL_REASON in evidence_reasons
    ) or (
        NEAREST_PRECEDING_GROUPING_REASON in evidence_reasons
        and NEARBY_SOURCE_CONTEXT_KEY_REASON in evidence_reasons
        and STATEMENT_TYPE_COMPATIBLE_REASON in evidence_reasons
    )


def _evidence_has_strong_local_ranking_support(evidence_reasons: set[str]) -> bool:
    """Return whether evidence contains strong but non-decisive local support.

    Same-table locality and a locally active outline parent should rank ahead of weak
    nearby or semantic candidates, but they cannot independently make selection of a
    parent mandatory.

    Parameters
    ----------
    evidence_reasons
        Evidence reasons accumulated for one parent candidate.

    Returns
    -------
    bool
        True when strong local retrieval/ranking evidence is present.
    """

    return bool(evidence_reasons & STRONG_LOCAL_RANKING_PARENT_REASONS)


def _finalize_candidate_parent_set(
    *,
    child_context: SFIFinalContext,
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    parent_requirements: Sequence[SFIHasChildParentRequirement],
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
                evidence_reasons=[_CODE_PARENT_HINT_REASON],
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
        parent_requirements=[...],
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
    parent_requirements
        Allowed direct-parent types and cardinalities for this child statement type.

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
        child_context=child_context,
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
        parent_requirements=list(parent_requirements),
        truncation_notes=truncation_notes,
        was_truncated=was_truncated,
    )


def _get_hierarchy_statement_types(kg_config: CreateKGConfig) -> list[str]:
    """Return the configured statement-type hierarchy in broad-to-narrow order.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing the hierarchy override and statement type
        policy.

    Returns
    -------
    list[str]
        Statement types ordered from broadest parent to narrowest child.
    """

    standards = kg_config.academic_standards

    if standards.sfi_has_child_statement_type_hierarchy:
        return list(standards.sfi_has_child_statement_type_hierarchy)

    return [item.statement_type for item in standards.statement_type_policy]


def _label_matches_statement_type(
    *,
    label: str,
    statement_type: str,
    statement_type_aliases_by_type: dict[str, set[str]],
) -> bool:
    """Return whether a normalized source label names one statement type.

    Parameters
    ----------
    label
        Source-local label text, usually the portion before a colon.
    statement_type
        Canonical statement_type whose aliases should be matched.
    statement_type_aliases_by_type
        Normalized statement-type aliases keyed by canonical statement_type.

    Returns
    -------
    bool
        True when the label is exactly an alias or begins with an alias followed by a
        number or separator, such as `Unit 10` for the configured `Unit` type.
    """

    label_key = normalize_text(label)

    if not label_key:
        return False

    for alias_key in statement_type_aliases_by_type.get(statement_type, set()):
        if not alias_key:
            continue

        if label_key == alias_key or re.match(
            rf"^{re.escape(alias_key)}(?:\s|\d|$)", label_key
        ):
            return True

    return False


def _is_nearby_source_window(
    *, child_context: SFIFinalContext, parent_context: SFIFinalContext
) -> bool:
    """Check whether a parent is in a nearby source window.

    Parameters
    ----------
    child_context
        Child final SFI context.
    parent_context
        Candidate parent final SFI context.

    Returns
    -------
    bool
        True when parent is a nearby grouping candidate. This allows slightly later
        source-order headings to be considered when PDF layout/OCR emits a grade or
        organizer after its visually co-located child heading.
    """

    if parent_context.normalized_statement_type != "Standard Grouping":
        return False

    return any(
        abs(child_window_index - parent_window_index) <= 2
        for child_window_index in child_context.source_window_indexes
        for parent_window_index in parent_context.source_window_indexes
    )


def _is_source_scope_grouping(
    *,
    child_occurrences: Sequence[_TableOccurrenceRef],
    parent_context: SFIFinalContext,
    parent_occurrences: Sequence[_TableOccurrenceRef],
) -> bool:
    """Check whether a parent has a paired header occurrence for a row child.

    This captures a common curriculum layout without hardcoding curriculum labels: a
    table grouping is expressed by one header-only parent occurrence, while the child
    is expressed by one body-row occurrence from the same table segment and the same
    or a nearby following extraction window. Candidate-level occurrences are compared
    directly so provenance from separate source refs cannot be cross-combined.

    Parameters
    ----------
    child_occurrences
        Candidate-level child table occurrences preserving segment/window/index pairing.
    parent_context
        Final SFI source context for the potential parent.
    parent_occurrences
        Candidate-level parent table occurrences preserving segment/window/index
        pairing.

    Returns
    -------
    bool
        True when one header-only parent occurrence scopes one row-derived child
        occurrence in the same source segment and same or nearby following window.
    """

    if parent_context.normalized_statement_type != "Standard Grouping":
        return False

    for parent_occurrence in parent_occurrences:
        if (
            not parent_occurrence.table_header_indexes
            or parent_occurrence.table_row_indexes
        ):
            continue

        for child_occurrence in child_occurrences:
            if (
                not child_occurrence.table_row_indexes
                or child_occurrence.source_segment_id
                != parent_occurrence.source_segment_id
            ):
                continue

            same_window = child_occurrence.window_id == parent_occurrence.window_id
            nearby_following_window = (
                0 <= child_occurrence.window_index - parent_occurrence.window_index <= 2
            )

            if same_window or nearby_following_window:
                return True

    return False


def _load_and_validate_existing_relationship_artifacts(
    *,
    contexts: Sequence[SFIFinalContext],
    contexts_fp: Path,
    document_ir: DocumentIR,
    draft_responses_fp: Path,
    edges_fp: Path,
    final_responses_fp: Path,
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    parent_sets: Sequence[SFIHasChildCandidateParentSet],
    parent_sets_fp: Path,
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    sfi_final_records: Sequence[SFIFinalRecord],
    summary_fp: Path,
    unresolved_edges_fp: Path,
    validation_verdicts_fp: Path,
) -> list[SFIHasChildEdge] | None:
    """Reuse complete current producer/checker and relationship artifacts.

    Parameters
    ----------
    contexts
        Current finalized SFI contexts.
    contexts_fp
        Persisted context artifact path.
    document_ir
        Source DocumentIR.
    draft_responses_fp
        Producer draft response JSONL path.
    edges_fp
        Final edge JSON path.
    final_responses_fp
        Accepted or corrected response JSONL path.
    framework_uuid
        StandardsFramework root UUID.
    kg_config
        Runtime configuration used for graph statement-type policy.
    parent_sets
        Current bounded parent candidate sets.
    parent_sets_fp
        Persisted parent-set JSONL path.
    requests
        Current deterministic requests.
    requests_fp
        Persisted request JSONL path.
    sfi_final_records
        Final SFI universe.
    summary_fp
        Persisted resolution summary path.
    unresolved_edges_fp
        Persisted unresolved-edge path.
    validation_verdicts_fp
        Checker verdict JSONL path.

    Returns
    -------
    list[SFIHasChildEdge] | None
        Reusable edge sequence, or `None` when artifacts are missing or stale.
    """

    try:
        loaded_contexts = _load_json_model_sequence(
            fp=contexts_fp, model_type=SFIFinalContext
        )
        assert_model_sequences_equal(
            actual=loaded_contexts,
            artifact_label="sfi_final_contexts.json",
            expected=contexts,
        )
        loaded_parent_sets = _load_jsonl_models(
            allow_partial_prefix=False,
            fp=parent_sets_fp,
            model_type=SFIHasChildCandidateParentSet,
        )
        assert_model_sequences_equal(
            actual=loaded_parent_sets,
            artifact_label="has_child_candidate_parent_sets.jsonl",
            expected=parent_sets,
        )
        loaded_requests = _load_jsonl_models(
            allow_partial_prefix=False,
            fp=requests_fp,
            model_type=SFIHasChildResolutionRequest,
        )
        assert_model_sequences_equal(
            actual=loaded_requests,
            artifact_label="has_child_resolution_requests.jsonl",
            expected=requests,
        )
        loaded_draft_responses = _load_jsonl_models(
            allow_partial_prefix=False,
            fp=draft_responses_fp,
            model_type=SFIHasChildResolutionResponse,
        )
        loaded_final_responses = _load_jsonl_models(
            allow_partial_prefix=False,
            fp=final_responses_fp,
            model_type=SFIHasChildResolutionResponse,
        )
        loaded_validation_verdicts = _load_jsonl_models(
            allow_partial_prefix=False,
            fp=validation_verdicts_fp,
            model_type=SFIHasChildValidationVerdict,
        )
        _validate_resolution_artifact_prefix(
            draft_responses=loaded_draft_responses,
            final_responses=loaded_final_responses,
            requests=requests,
            validation_verdicts=loaded_validation_verdicts,
        )

        if len(loaded_final_responses) != len(requests):
            raise ValueError(
                f"Complete hasChild artifacts contain {len(loaded_final_responses)} "
                f"responses, but {len(requests)} requests are planned."
            )

        expected_edges = _build_edges_from_responses(
            document_ir=document_ir,
            framework_uuid=framework_uuid,
            parent_sets=parent_sets,
            requests=requests,
            responses=loaded_final_responses,
            validation_verdicts=loaded_validation_verdicts,
        )
        _validate_graph(
            edges=expected_edges,
            framework_uuid=framework_uuid,
            kg_config=kg_config,
            sfi_final_records=sfi_final_records,
        )
        expected_summary = SFIHasChildResolutionSummary(
            candidate_parent_set_count=len(parent_sets),
            checker_corrected_response_count=sum(
                1 for verdict in loaded_validation_verdicts if not verdict.passed
            ),
            checker_request_count=len(requests),
            checker_verdict_count=len(loaded_validation_verdicts),
            edge_count=len(expected_edges),
            final_sfi_count=len(parent_sets),
            generator_request_count=len(requests),
            generator_response_count=len(loaded_draft_responses),
            root_edge_count=sum(1 for edge in expected_edges if edge.is_root_edge),
            sfi_to_sfi_edge_count=sum(
                1 for edge in expected_edges if not edge.is_root_edge
            ),
            truncated_candidate_parent_set_count=sum(
                1 for parent_set in parent_sets if parent_set.was_truncated
            ),
            unresolved_child_count=sum(
                1 for edge in expected_edges if edge.unresolved_root_fallback
            ),
        )
        expected_unresolved_edges = [
            edge for edge in expected_edges if edge.unresolved_root_fallback
        ]
        loaded_edges = _load_json_model_sequence(
            fp=edges_fp, model_type=SFIHasChildEdge
        )
        assert_model_sequences_equal(
            actual=loaded_edges,
            artifact_label="has_child_edges_final.json",
            expected=expected_edges,
        )
        loaded_unresolved_edges = _load_json_model_sequence(
            fp=unresolved_edges_fp, model_type=SFIHasChildEdge
        )
        assert_model_sequences_equal(
            actual=loaded_unresolved_edges,
            artifact_label="has_child_unresolved_edges.json",
            expected=expected_unresolved_edges,
        )
        loaded_summary = SFIHasChildResolutionSummary.model_validate(
            open_json_type(summary_fp)
        )

        if model_dump_key(loaded_summary) != model_dump_key(expected_summary):
            raise ValueError(
                "has_child_resolution_summary.json does not match current "
                "producer/checker output."
            )
    except Exception as exc:  # pylint: disable=W0718
        logger.warning(
            f"Existing hasChild artifacts are incomplete, missing, or stale; "
            f"resuming producer/checker resolution: {exc}"
        )
        return None

    logger.info(
        f"Loading complete existing hasChild artifacts because overwrite=False: "
        f"{edges_fp}"
    )
    return loaded_edges


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
    extraction_windows_fp = kg_dirs.root / "sfi_extraction_windows.jsonl"

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


def _load_final_contexts(contexts_fp: Path) -> list[SFIFinalContext]:
    """Load final SFI contexts for hasChild resolution.

    Parameters
    ----------
    contexts_fp
        JSON artifact path written by SFI finalization.

    Returns
    -------
    list[SFIFinalContext]
        Parsed final SFI contexts in deterministic source order.

    Raises
    ------
    ValueError
        If the final-context artifact is missing, malformed, or not a JSON list.
    """

    if not contexts_fp.exists():
        raise ValueError(f"Missing final SFI contexts artifact: {contexts_fp}")

    data = open_json_type(contexts_fp)

    if not isinstance(data, list):
        raise ValueError(
            f"Expected a JSON list in final SFI contexts artifact: {contexts_fp}"
        )

    return [SFIFinalContext.model_validate(item) for item in data]


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
    *, allow_partial_prefix: bool, fp: Path, model_type: type[ModelT]
) -> list[ModelT]:
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
    list[ModelT]
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

    models: list[ModelT] = []

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
    draft_responses_fp: Path,
    final_responses_fp: Path,
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    validation_verdicts_fp: Path,
) -> _ResolutionProgress:
    """Load the longest aligned producer/checker prefix safe for resume.

    Parameters
    ----------
    draft_responses_fp
        JSONL path for producer draft responses.
    final_responses_fp
        JSONL path for accepted or corrected final responses.
    requests
        Current deterministic request sequence.
    requests_fp
        JSONL path for persisted requests.
    validation_verdicts_fp
        JSONL path for checker verdicts.

    Returns
    -------
    _ResolutionProgress
        Valid aligned progress prefix, or empty progress when reuse is unsafe.
    """

    empty_progress = _ResolutionProgress(
        draft_responses=[], final_responses=[], validation_verdicts=[]
    )

    try:
        draft_responses = _load_jsonl_models(
            allow_partial_prefix=True,
            fp=draft_responses_fp,
            model_type=SFIHasChildResolutionResponse,
        )
        final_responses = _load_jsonl_models(
            allow_partial_prefix=True,
            fp=final_responses_fp,
            model_type=SFIHasChildResolutionResponse,
        )
        validation_verdicts = _load_jsonl_models(
            allow_partial_prefix=True,
            fp=validation_verdicts_fp,
            model_type=SFIHasChildValidationVerdict,
        )
        trusted_prefix_length = min(
            len(draft_responses),
            len(final_responses),
            len(validation_verdicts),
        )

        if trusted_prefix_length == 0:
            return empty_progress

        draft_prefix = draft_responses[:trusted_prefix_length]
        final_prefix = final_responses[:trusted_prefix_length]
        verdict_prefix = validation_verdicts[:trusted_prefix_length]
        _validate_resolution_artifact_prefix(
            draft_responses=draft_prefix,
            final_responses=final_prefix,
            requests=requests,
            validation_verdicts=verdict_prefix,
        )
        saved_requests = _load_jsonl_models(
            allow_partial_prefix=False,
            fp=requests_fp,
            model_type=SFIHasChildResolutionRequest,
        )
        _validate_resolution_request_prefix(
            requests=requests,
            saved_requests=saved_requests,
            trusted_prefix_length=trusted_prefix_length,
        )

        logger.info(
            f"Resuming hasChild producer/checker resolution from "
            f"{trusted_prefix_length} completed requests."
        )

        return _ResolutionProgress(
            draft_responses=draft_prefix,
            final_responses=final_prefix,
            validation_verdicts=verdict_prefix,
        )
    except Exception as exc:  # pylint: disable=W0718
        logger.warning(
            f"Ignoring existing hasChild producer/checker progress because the "
            f"saved prefix does not match the current plan: {exc}"
        )

    return empty_progress


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


def _normalize_controlled_value_key(value: object) -> str:
    """Normalize controlled-value text for multilingual scope comparison.

    Parameters
    ----------
    value
        Raw controlled value, alias, source label, or description.

    Returns
    -------
    str
        Unicode-aware canonical comparison key.
    """

    return normalize_controlled_value_key(str(value or ""))


def _parent_candidate_evidence_tier(
    candidate: SFIHasChildParentCandidate,
) -> int:
    """Assign a deterministic retrieval tier for one parent candidate.

    Lower tiers are preserved first during bounding. The tier is a retrieval ordering,
    not a semantic parent decision.

    Parameters
    ----------
    candidate
        Parent candidate to rank.

    Returns
    -------
    int
        Deterministic retrieval tier.
    """

    evidence_reasons = set(candidate.evidence_reasons or [])

    if candidate.is_root or ROOT_EVIDENCE_REASON in evidence_reasons:
        return 90

    shares_local_source_container = bool(
        evidence_reasons
        & {
            PARENT_CELL_APPLIES_TO_CHILD_ROW_REASON,
            SAME_RAW_TABLE_ROW_REASON,
            SAME_SOURCE_CONTEXT_KEY_REASON,
            SAME_SOURCE_SEGMENT_REASON,
            SAME_SOURCE_UNIT_REASON,
            SAME_SOURCE_WINDOW_REASON,
        }
    )

    # Ordered retrieval tiers: the first matching predicate wins, so lower (stronger)
    # tiers must precede higher (weaker) ones.
    tier_rules: tuple[tuple[bool, int], ...] = (
        (IDENTITY_SCOPE_COMPLETE_PARENT_MATCH_REASON in evidence_reasons, 0),
        (_evidence_has_decisive_direct_parent_support(evidence_reasons), 1),
        (
            IDENTITY_SCOPE_DIRECT_PARENT_MATCH_REASON in evidence_reasons
            or SAME_RAW_TABLE_ROW_REASON in evidence_reasons
            or SAME_SOURCE_UNIT_REASON in evidence_reasons
            or SOURCE_VISIBLE_DIRECT_PARENT_REASON in evidence_reasons,
            2,
        ),
        (_evidence_has_strong_local_ranking_support(evidence_reasons), 3),
        (
            ACTIVE_OUTLINE_STACK_PARENT_REASON in evidence_reasons
            and shares_local_source_container,
            4,
        ),
        (_evidence_has_soft_carry_forward_support(evidence_reasons), 5),
        (shares_local_source_container, 6),
        (bool(evidence_reasons & CARRY_FORWARD_PARENT_REASONS), 7),
    )

    for matched, tier in tier_rules:
        if matched:
            return tier

    return 8


def _parent_candidate_rank(
    candidate: SFIHasChildParentCandidate,
) -> tuple[int, int, str]:
    """Build a deterministic retrieval sort key for parent candidates.

    Positive exact and source-local evidence improves rank. Structured-scope and typed
    source-label conflicts lower rank but never remove a candidate that has another
    positive retrieval signal.

    Parameters
    ----------
    candidate
        Parent candidate to rank.

    Returns
    -------
    tuple[int, int, str]
        Evidence tier, negative weighted score, and stable endpoint ID.
    """

    weights = {
        ACTIVE_OUTLINE_STACK_PARENT_REASON: 70,
        CODE_PARENT_HINT_REASON: 125,
        IDENTITY_SCOPE_ANCESTOR_CONFLICT_REASON: -110,
        IDENTITY_SCOPE_ANCESTOR_MATCH_REASON: 80,
        IDENTITY_SCOPE_COMPLETE_PARENT_MATCH_REASON: 180,
        IDENTITY_SCOPE_DIRECT_PARENT_CONFLICT_REASON: -140,
        IDENTITY_SCOPE_DIRECT_PARENT_MATCH_REASON: 120,
        LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON: 100,
        MATCHED_SECTION_PATH_LABEL_REASON: 60,
        NEARBY_SOURCE_CONTEXT_KEY_REASON: 35,
        NEAREST_PRECEDING_GROUPING_REASON: 30,
        PARENT_CELL_APPLIES_TO_CHILD_ROW_REASON: 170,
        ROOT_EVIDENCE_REASON: 0,
        SAME_RAW_TABLE_ROW_REASON: 135,
        SAME_SOURCE_CONTEXT_KEY_REASON: 70,
        SAME_SOURCE_SEGMENT_REASON: 60,
        SAME_SOURCE_UNIT_REASON: 145,
        SAME_SOURCE_WINDOW_REASON: 55,
        SAME_TABLE_CONTEXT_REASON: 85,
        SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_CONFLICT_REASON: -45,
        SOURCE_LOCAL_CONTROLLED_PARENT_SCOPE_REASON: 75,
        SOURCE_SCOPE_GROUPING_REASON: 120,
        SOURCE_VISIBLE_DIRECT_PARENT_REASON: 125,
        STATEMENT_TYPE_COMPATIBLE_REASON: 10,
    }
    score = sum(weights.get(reason, 0) for reason in candidate.evidence_reasons)
    return _parent_candidate_evidence_tier(candidate), -score, candidate.endpoint_id


def _parse_table_source_unit(
    *, source_unit: SFISourceUnit, source_unit_id: str
) -> _TableSourceUnitRef | None:
    """Parse one stable table-body source-unit identifier.

    Parameters
    ----------
    source_unit
        Exact source unit recovered from persisted extraction windows.
    source_unit_id
        Stable source-unit identifier to parse.

    Returns
    -------
    _TableSourceUnitRef | None
        Parsed table-body coordinates, or `None` for non-table-body units.

    Raises
    ------
    ValueError
        If parsed column bounds are invalid.
    """

    match = _TABLE_BODY_SOURCE_UNIT_PATTERN.fullmatch(source_unit_id)

    if match is None:
        return None

    column_end_index_exclusive = int(match.group("column_end_index_exclusive"))
    column_start_index = int(match.group("column_start_index"))

    if column_end_index_exclusive <= column_start_index:
        raise ValueError(
            f"Invalid table source-unit column range in {source_unit_id!r}."
        )

    return _TableSourceUnitRef(
        column_end_index_exclusive=column_end_index_exclusive,
        column_start_index=column_start_index,
        row_index=int(match.group("row_index")),
        source_segment_id=match.group("source_segment_id"),
        source_text=source_unit.source_text,
        source_unit_id=source_unit_id,
    )


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


def _record_has_multiple_source_occurrences(record: SFIFinalRecord) -> bool:
    """Return whether one finalized SFI represents multiple printed occurrences.

    Parameters
    ----------
    record
        Finalized SFI record with candidate source references.

    Returns
    -------
    bool
        True when more than one distinct source occurrence is represented.
    """

    occurrence_keys = {
        str(source_ref.get("source_occurrence_location_key") or "").strip()
        for source_ref in record.candidate_source_refs
        if str(source_ref.get("source_occurrence_location_key") or "").strip()
    }

    if occurrence_keys:
        return len(occurrence_keys) > 1

    return len(_source_unit_ids_from_record(record)) > 1


def _relationship_code_anomaly_metadata(
    *, child_context: SFIFinalContext, parent_candidate: SFIHasChildParentCandidate
) -> dict[str, Any]:
    """Build deterministic code/source-hierarchy audit metadata for one edge.

    The metadata is intentionally advisory: it does not change the edge, but it makes
    visible when a selected source-visible parent was chosen without a matching
    code-parent hint, or when a selected coded parent differs from the generic
    dot-prefix implied by the child code.

    Parameters
    ----------
    child_context
        Child final SFI context for the edge.
    parent_candidate
        Selected parent candidate used to build the edge.

    Returns
    -------
    dict[str, Any]
        Metadata fields describing source-visible parent use and detectable code
        conflicts.
    """

    child_code = normalize_code(child_context.normalized_statement_code)

    # Extract the generic immediate-parent prefix for a dotted child code.
    code_implied_parent_code = None

    if child_code and "." in child_code:
        code_implied_parent_code = child_code.rsplit(".", 1)[0].strip(".") or None

    parent_code = normalize_code(parent_candidate.normalized_statement_code)

    source_visible_parent_used = (
        SOURCE_VISIBLE_DIRECT_PARENT_REASON in parent_candidate.evidence_reasons
    )
    code_parent_hint_used = CODE_PARENT_HINT_REASON in parent_candidate.evidence_reasons

    selected_coded_parent_conflicts = bool(
        code_implied_parent_code
        and parent_code
        and parent_code != code_implied_parent_code
    )
    return {
        "code_implied_parent_code": code_implied_parent_code,
        "code_parent_hint_used": code_parent_hint_used,
        "selected_parent_code": parent_code,
        "selected_coded_parent_conflicts_with_child_code": selected_coded_parent_conflicts,
        "source_code_anomaly_visible_parent_used": bool(
            source_visible_parent_used and selected_coded_parent_conflicts
        ),
        "source_visible_parent_used": source_visible_parent_used,
        "source_visible_parent_without_code_hint": bool(
            source_visible_parent_used and child_code and not code_parent_hint_used
        ),
    }


def _rewrite_resolution_progress_files(
    *,
    completed_draft_responses: Sequence[SFIHasChildResolutionResponse],
    completed_final_responses: Sequence[SFIHasChildResolutionResponse],
    completed_requests: Sequence[SFIHasChildResolutionRequest],
    completed_validation_verdicts: Sequence[SFIHasChildValidationVerdict],
    draft_responses_fp: Path,
    final_responses_fp: Path,
    requests_fp: Path,
    validation_verdicts_fp: Path,
) -> None:
    """Rewrite producer/checker JSONL artifacts to one clean aligned prefix.

    Parameters
    ----------
    completed_draft_responses
        Producer drafts to preserve.
    completed_final_responses
        Accepted or corrected final responses to preserve.
    completed_requests
        Deterministic requests corresponding to the completed prefix.
    completed_validation_verdicts
        Checker verdicts to preserve.
    draft_responses_fp
        JSONL path for producer drafts.
    final_responses_fp
        JSONL path for final responses.
    requests_fp
        JSONL path for requests.
    validation_verdicts_fp
        JSONL path for checker verdicts.
    """

    paths = [
        draft_responses_fp,
        final_responses_fp,
        requests_fp,
        validation_verdicts_fp,
    ]

    for path in paths:
        make_dir(path.parent)
        path.write_text("", encoding="utf-8")

    for request in completed_requests:
        append_jsonl_model(fp=requests_fp, model=request)

    for draft_response in completed_draft_responses:
        append_jsonl_model(fp=draft_responses_fp, model=draft_response)

    for validation_verdict in completed_validation_verdicts:
        append_jsonl_model(fp=validation_verdicts_fp, model=validation_verdict)

    for final_response in completed_final_responses:
        append_jsonl_model(fp=final_responses_fp, model=final_response)


def _run_resolution_requests(
    *,
    completed_progress: _ResolutionProgress,
    draft_responses_fp: Path,
    final_responses_fp: Path,
    requests: Sequence[SFIHasChildResolutionRequest],
    requests_fp: Path,
    usage_tracker: KGUsageTracker,
    validation_verdicts_fp: Path,
) -> _ResolutionProgress:
    """Run remaining producer/checker requests and persist each completed stage.

    Parameters
    ----------
    completed_progress
        Valid aligned prefix from a previous partial run.
    draft_responses_fp
        JSONL path for producer drafts.
    final_responses_fp
        JSONL path for accepted or checker-corrected responses.
    requests
        Full deterministic request sequence.
    requests_fp
        JSONL path for requests.
    usage_tracker
        Producer/checker usage tracker.
    validation_verdicts_fp
        JSONL path for checker verdicts.

    Returns
    -------
    _ResolutionProgress
        Complete aligned producer/checker artifacts in request order.

    Raises
    ------
    ValueERror If the completed hasChild record has an issue.
    """

    draft_responses = list(completed_progress.draft_responses)
    final_responses = list(completed_progress.final_responses)
    validation_verdicts = list(completed_progress.validation_verdicts)
    completed_count = len(final_responses)

    if not len(draft_responses) == completed_count == len(validation_verdicts):
        raise ValueError("Completed hasChild producer/checker progress is not aligned.")

    if completed_count > len(requests):
        raise ValueError(
            f"Completed hasChild prefix has {completed_count} records, but only "
            f"{len(requests)} requests are planned."
        )

    for path in [
        draft_responses_fp,
        final_responses_fp,
        requests_fp,
        validation_verdicts_fp,
    ]:
        make_dir(path.parent)

    for request_index in range(completed_count, len(requests)):
        request = requests[request_index]
        logger.info(
            f"Running hasChild producer/checker request "
            f"{request_index + 1}/{len(requests)}: request_id={request.request_id}."
        )
        append_jsonl_model(fp=requests_fp, model=request)
        resolution_run = resolve_sfi_has_child_parent_request(
            resolution_request=request, usage_tracker=usage_tracker
        )
        append_jsonl_model(fp=draft_responses_fp, model=resolution_run.draft_response)
        append_jsonl_model(
            fp=validation_verdicts_fp, model=resolution_run.validation_verdict
        )
        append_jsonl_model(fp=final_responses_fp, model=resolution_run.final_response)
        draft_responses.append(resolution_run.draft_response)
        final_responses.append(resolution_run.final_response)
        validation_verdicts.append(resolution_run.validation_verdict)

    return _ResolutionProgress(
        draft_responses=draft_responses,
        final_responses=final_responses,
        validation_verdicts=validation_verdicts,
    )


def _scope_value_for_statement_type(
    *, scope_values: dict[str, str], statement_type: str
) -> str | None:
    """Return one scope value using case-insensitive statement-type matching.

    Parameters
    ----------
    scope_values
        Finalized identity-scope values keyed by source-facing statement type.
    statement_type
        Configured statement type to retrieve.

    Returns
    -------
    str | None
        Matching scope value, or `None` when the dimension is absent.
    """

    statement_type_key = statement_type.strip().casefold()

    for scope_statement_type, scope_value in scope_values.items():
        if str(scope_statement_type).strip().casefold() == statement_type_key:
            value = str(scope_value or "").strip()
            return value or None

    return None


def _should_add_code_parent_hint_evidence(
    *,
    child_code: str | None,
    child_context: SFIFinalContext,
    child_table_keys: set[str],
    code_parent_pairs: set[tuple[str, str]],
    parent_code: str | None,
    parent_context: SFIFinalContext,
    parent_table_keys: set[str],
) -> bool:
    """Decide whether a code-parent hint should become high-signal evidence.

    Code-parent pairs are useful retrieval evidence, but they must be admitted only for
    a specific child/parent pair that is also source-compatible. This prevents reused,
    duplicated, or disambiguated codes from crowding out the true source-grounded
    parent during bounded candidate truncation.

    Parameters
    ----------
    child_code
        Normalized code for the potential child, when present.
    child_context
        Final SFI source context for the potential child.
    child_table_keys
        Table-local row/header context keys for the child.
    code_parent_pairs
        Globally extracted normalized child-code to parent-code hint pairs.
    parent_code
        Normalized code for the potential parent, when present.
    parent_context
        Final SFI source context for the potential parent.
    parent_table_keys
        Table-local row/header context keys for the parent.

    Returns
    -------
    bool
        True when the normalized code pair exists, the parent is structurally
        plausible, local source compatibility exists, and audit evidence does not
        require blocking the hint.
    """

    if not (child_code and parent_code):
        return False

    if (child_code, parent_code) not in code_parent_pairs:
        return False

    # A source-visible Standard Grouping is always structurally plausible. Other types
    # are only allowed if they do not appear after the child in source order.
    if (
        parent_context.normalized_statement_type != "Standard Grouping"
        and parent_context.source_order > child_context.source_order
    ):
        return False

    # Check for strong local source compatibility.
    has_strong_compatibility = (
        bool(
            set(child_context.source_context_keys)
            & set(parent_context.source_context_keys)
        )
        or bool(
            set(child_context.source_segment_ids)
            & set(parent_context.source_segment_ids)
        )
        or bool(
            set(child_context.source_window_ids) & set(parent_context.source_window_ids)
        )
        or bool(child_table_keys & parent_table_keys)
        or _context_matches_section_path(
            child_context=child_context, parent_context=parent_context
        )
    )

    if has_strong_compatibility:
        return True

    # Fallback: Without strong compatibility, we require weak compatibility (nearby
    # source window) AND the absence of the same-code/different-content audit flag.
    if not _is_nearby_source_window(
        child_context=child_context, parent_context=parent_context
    ):
        return False

    return not (
        "same_code_different_content" in child_context.audit_flags
        or "same_code_different_content" in parent_context.audit_flags
    )


def _source_context_label_segments(text: str) -> list[str]:
    """Split one source-context label into source-local semantic segments.

    Parameters
    ----------
    text
        Source-context label from a final SFI context.

    Returns
    -------
    list[str]
        Ordered non-empty source-local label segments with artifact prefixes removed.
    """

    cleaned_text = re.sub(
        r"^(?:section|table_columns|table_header\[[0-9]+\]):\s*",
        "",
        str(text).strip(),
        flags=re.IGNORECASE,
    )
    raw_segments = re.split(r"\s*(?:\|\|+|\|)\s*", cleaned_text)
    return unique_nonempty(segment.strip() for segment in raw_segments if segment)


def _source_label_statement_type(
    *, segment: str, statement_type_aliases_by_type: dict[str, set[str]]
) -> str | None:
    """Identify the configured statement type explicitly named by a segment label.

    Parameters
    ----------
    segment
        Source-local segment, such as `SUB-TOPIC AREA: FRACTIONS` or `Unit 4: ...`.
    statement_type_aliases_by_type
        Normalized statement-type aliases keyed by canonical statement_type.

    Returns
    -------
    str | None
        The matching statement_type, or None when the segment has no typed label.
    """

    if ":" not in segment:
        return None

    label = segment.split(":", 1)[0]

    for statement_type in sorted(statement_type_aliases_by_type):
        if _label_matches_statement_type(
            label=label,
            statement_type=statement_type,
            statement_type_aliases_by_type=statement_type_aliases_by_type,
        ):
            return statement_type

    return None


def _source_local_parent_scope_matches_parent(
    *,
    parent_context: SFIFinalContext,
    source_local_parent_scope_value_keys_by_type: dict[str, set[str]],
) -> bool:
    """Check whether a parent matches a child's local controlled scope.

    Parameters
    ----------
    parent_context
        Candidate parent final SFI context.
    source_local_parent_scope_value_keys_by_type
        Inferred local controlled parent value keys keyed by parent statement_type.

    Returns
    -------
    bool
        True when the child locally names a value for this parent type and the
        candidate parent has the same canonical value key.
    """

    expected_value_keys = source_local_parent_scope_value_keys_by_type.get(
        parent_context.statement_type, set()
    )

    parent_value_key = _normalize_controlled_value_key(
        parent_context.canonical_statement_value_key
        or parent_context.canonical_statement_value
        or parent_context.description
    )

    if not expected_value_keys or not parent_value_key:
        return False

    return parent_value_key in expected_value_keys


def _source_ref_int_list(*, key: str, source_ref: dict[str, object]) -> list[int]:
    """Collect sorted integer values from one candidate source-ref field.

    Parameters
    ----------
    key
        Source-ref field to read, such as `table_row_indexes` or `table_header_indexes`.
    source_ref
        Candidate source-ref dictionary from a final SFI record.

    Returns
    -------
    list[int]
        Sorted unique integer values. Invalid or empty values are ignored.
    """

    raw_values = source_ref.get(key)

    if raw_values is None:
        return []

    if isinstance(raw_values, (str, bytes)) or not isinstance(raw_values, Sequence):
        values_iterable: Any = [raw_values]
    else:
        values_iterable = raw_values

    values: set[int] = set()

    for value in values_iterable:
        try:
            values.add(int(value))
        except Exception:  # pylint: disable=W0718
            continue

    return sorted(values)


def _source_ref_segment_ids(source_ref: dict[str, object]) -> list[str]:
    """Collect source segment IDs from one candidate source-ref record.

    Parameters
    ----------
    source_ref
        Candidate source-ref dictionary from a final SFI record.

    Returns
    -------
    list[str]
        Unique non-empty source segment IDs in source-ref order.
    """

    raw_segment_ids = source_ref.get("source_segment_ids")

    if raw_segment_ids is None:
        return []

    if isinstance(raw_segment_ids, (str, bytes)) or not isinstance(
        raw_segment_ids, Sequence
    ):
        segment_ids_iterable: Any = [raw_segment_ids]
    else:
        segment_ids_iterable = raw_segment_ids

    return unique_nonempty(str(segment_id) for segment_id in segment_ids_iterable)


def _source_ref_text_list(*, key: str, source_ref: dict[str, object]) -> list[str]:
    """Collect source-ref string values in first-seen order.

    Parameters
    ----------
    key
        Source-ref field to read, such as `source_context_labels`.
    source_ref
        Candidate source-ref dictionary from a final SFI record.

    Returns
    -------
    list[str]
        Unique non-empty string values. Invalid or empty values are ignored.
    """

    raw_values = source_ref.get(key)

    if raw_values is None:
        return []

    if isinstance(raw_values, (str, bytes)) or not isinstance(raw_values, Sequence):
        values_iterable: Any = [raw_values]
    else:
        values_iterable = raw_values

    return unique_nonempty(str(value).strip() for value in values_iterable)


def _source_relation_evidence_reason(source_relation: SFIHasChildSourceRelation) -> str:
    """Return the evidence-reason constant for one source relation.

    Parameters
    ----------
    source_relation
        Deterministic child-relative source relation.

    Returns
    -------
    str
        Machine-readable evidence reason.
    """

    reasons_by_relation_kind = {
        "parent_cell_applies_to_child_row": (PARENT_CELL_APPLIES_TO_CHILD_ROW_REASON),
        "same_raw_table_row": SAME_RAW_TABLE_ROW_REASON,
        "same_source_unit": SAME_SOURCE_UNIT_REASON,
    }
    return reasons_by_relation_kind[source_relation.relation_kind]


def _source_relation_evidence_summary(
    source_relation: SFIHasChildSourceRelation,
) -> str:
    """Build a concise human-readable summary for one source relation.

    Parameters
    ----------
    source_relation
        Deterministic child-relative source relation.

    Returns
    -------
    str
        Source-grounded relation summary for producer/checker review.
    """

    if source_relation.relation_kind == "same_source_unit":
        return "Child and parent cite the same exact source-visible table cell."

    if source_relation.relation_kind == "same_raw_table_row":
        return (
            f"Child and parent cite source-visible table cells in the same raw row "
            f"{source_relation.child_row_index}."
        )

    return (
        f"The persisted table grid shows that the parent cell originating in raw row "
        f"{source_relation.parent_origin_row_index}, columns "
        f"[{source_relation.parent_column_start_index}, "
        f"{source_relation.parent_column_end_index_exclusive}), applies to child raw "
        f"row {source_relation.child_row_index}."
    )


def _source_unit_ids_from_record(record: SFIFinalRecord) -> list[str]:
    """Return stable source-unit IDs preserved by one finalized SFI record.

    Parameters
    ----------
    record
        Finalized SFI record containing candidate-level source references.

    Returns
    -------
    list[str]
        Sorted unique source-unit identifiers from code and description anchors.
    """

    source_unit_ids: set[str] = set()

    for source_ref in record.candidate_source_refs:
        for anchor_field in ("code_source_anchors", "description_source_anchors"):
            anchors = source_ref.get(anchor_field) or []

            for anchor in anchors:
                if not isinstance(anchor, dict):
                    continue

                source_unit_id = str(anchor.get("source_unit_id") or "").strip()

                if source_unit_id:
                    source_unit_ids.add(source_unit_id)

    return sorted(source_unit_ids)


def _split_typed_source_hierarchy_segments(
    *,
    raw_text: str,
    statement_type_aliases_by_type: dict[str, set[str]],
) -> list[tuple[str, str]]:
    """Split source text into typed hierarchy label/value segments.

    This parser only emits segments whose label explicitly names a configured statement
    type, such as `Theme: Number` or `Sub-Theme: Whole Number`. Untyped text is ignored
    so words in a Topic, Performance Objective, Content item, activity, resource, or
    evaluation prompt cannot be mistaken for a parent organizer. The matching is
    configuration-driven and does not depend on any curriculum-specific labels.

    Parameters
    ----------
    raw_text
        Source-local text to parse. Artifact prefixes such as `section:` and
        `table_header[0]:` may be present.
    statement_type_aliases_by_type
        Normalized statement-type aliases keyed by configured statement_type.

    Returns
    -------
    list[tuple[str, str]]
        Ordered `(statement_type, value_text)` pairs for typed hierarchy labels.
    """

    cleaned_text = re.sub(
        r"^(?:section|table_columns|table_header\[[0-9]+\]):\s*",
        "",
        str(raw_text).strip(),
        flags=re.IGNORECASE,
    )

    if not cleaned_text:
        return []

    alias_type_pairs = sorted(
        (
            (alias_key, statement_type)
            for statement_type, aliases in statement_type_aliases_by_type.items()
            for alias_key in aliases
            if alias_key
        ),
        key=lambda item: (-len(item[0]), item[0], item[1]),
    )

    if not alias_type_pairs:
        return []

    alias_patterns: list[str] = []

    for alias_key, _statement_type in alias_type_pairs:
        alias_parts = [re.escape(part) for part in alias_key.split() if part]

        if not alias_parts:
            continue

        alias_patterns.append(r"[\s_\-]*".join(alias_parts))

    label_pattern = "|".join(dict.fromkeys(alias_patterns))

    if not label_pattern:
        return []

    label_regex = re.compile(
        rf"(?<![A-Za-z0-9])(?P<label>{label_pattern})\s*:", flags=re.IGNORECASE
    )
    matches = list(label_regex.finditer(cleaned_text))

    if not matches:
        return []

    statement_type_by_alias = dict(alias_type_pairs)
    typed_segments: list[tuple[str, str]] = []

    for match_index, match in enumerate(matches):
        value_start = match.end()
        value_end = (
            matches[match_index + 1].start()
            if match_index + 1 < len(matches)
            else len(cleaned_text)
        )
        label_key = normalize_text(match.group("label"))
        statement_type = statement_type_by_alias.get(label_key)
        value_text = cleaned_text[value_start:value_end].strip(" |\n\t")

        if statement_type and value_text:
            typed_segments.append((statement_type, value_text))

    return typed_segments


def _statement_type_aliases_by_type(kg_config: CreateKGConfig) -> dict[str, set[str]]:
    """Build normalized statement-type aliases from runtime config.

    Parameters
    ----------
    kg_config
        Runtime KG configuration containing statement-type policy items.

    Returns
    -------
    dict[str, set[str]]
        Normalized aliases keyed by canonical statement_type.
    """

    aliases_by_type: dict[str, set[str]] = {}

    for policy_item in kg_config.academic_standards.statement_type_policy:
        aliases_by_type[policy_item.statement_type] = {
            alias_key
            for alias in [policy_item.statement_type, *policy_item.aliases]
            if (alias_key := normalize_text(alias))
        }

    return aliases_by_type


def _suppress_ambiguous_active_outline_evidence(
    evidence_by_endpoint_id: dict[str, _ParentEvidence],
) -> None:
    """Remove arbitrary active-outline advantages from equivalent row candidates.

    When multiple candidates of the same statement type have the same strongest exact
    table relation to the child, flattened source order cannot distinguish their
    semantic parentage. Structural evidence remains; only active-outline ranking
    signals are removed.

    Parameters
    ----------
    evidence_by_endpoint_id
        Mutable parent evidence records keyed by selectable endpoint ID.
    """

    relation_priority = {
        "parent_cell_applies_to_child_row": 0,
        "same_source_unit": 1,
        "same_raw_table_row": 2,
    }
    grouped_endpoint_ids: dict[tuple[str, int], list[str]] = defaultdict(list)

    for endpoint_id, evidence in evidence_by_endpoint_id.items():
        statement_type = evidence.candidate.statement_type

        if not statement_type or not evidence.candidate.source_relations:
            continue

        strongest_relation_priority = min(
            relation_priority[relation.relation_kind]
            for relation in evidence.candidate.source_relations
        )
        grouped_endpoint_ids[(statement_type, strongest_relation_priority)].append(
            endpoint_id
        )

    for endpoint_ids in grouped_endpoint_ids.values():
        if len(endpoint_ids) < 2:
            continue

        for endpoint_id in endpoint_ids:
            evidence = evidence_by_endpoint_id[endpoint_id]
            evidence.evidence_reasons.discard(ACTIVE_OUTLINE_STACK_PARENT_REASON)
            evidence.evidence_reasons.discard(LOCAL_ACTIVE_OUTLINE_DIRECT_PARENT_REASON)
            evidence.evidence_summary = [
                summary
                for summary in evidence.evidence_summary
                if "active preceding finalized SFI" not in summary
                and "active configured direct parent" not in summary
            ]


def _table_context_keys_from_occurrences(
    table_occurrences: Sequence[_TableOccurrenceRef],
) -> set[str]:
    """Build paired table-local context keys from candidate-level occurrences.

    Parameters
    ----------
    table_occurrences
        Candidate-level table occurrences preserving segment/window/index pairing.

    Returns
    -------
    set[str]
        Table-local row/header context keys derived without cross-combining provenance.
    """

    keys: set[str] = set()

    for occurrence in table_occurrences:
        for row_index in occurrence.table_row_indexes:
            keys.add(f"segment:{occurrence.source_segment_id}:row:{row_index}")

        for header_index in occurrence.table_header_indexes:
            keys.add(f"segment:{occurrence.source_segment_id}:header:{header_index}")

    return keys


def _table_occurrences_from_source_refs(
    record: SFIFinalRecord,
) -> tuple[_TableOccurrenceRef, ...]:
    """Build strict candidate-level table occurrences for one final SFI record.

    Each occurrence is constructed from one candidate source-ref entry so source
    segment, extraction window, and cited row/header indexes remain paired. Non-table
    refs are ignored. Table refs must identify exactly one source segment and a valid
    extraction window; malformed provenance fails closed rather than creating inferred
    combinations.

    Parameters
    ----------
    record
        Final SFI record whose candidate source refs should be parsed.

    Returns
    -------
    tuple[_TableOccurrenceRef, ...]
        Unique deterministic table occurrences sorted by complete provenance.

    Raises
    ------
    ValueError
        If a table source ref is not dictionary-shaped, does not identify exactly one
        source segment, or lacks a valid extraction-window identity.
    """

    occurrences: set[_TableOccurrenceRef] = set()

    for source_ref_index, source_ref in enumerate(record.candidate_source_refs):
        if not isinstance(source_ref, dict):
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} candidate_source_refs entry "
                f"{source_ref_index} is not a dictionary."
            )

        table_header_indexes = tuple(
            _source_ref_int_list(key="table_header_indexes", source_ref=source_ref)
        )
        table_row_indexes = tuple(
            _source_ref_int_list(key="table_row_indexes", source_ref=source_ref)
        )

        if not table_header_indexes and not table_row_indexes:
            continue

        source_segment_ids = _source_ref_segment_ids(source_ref)

        if len(source_segment_ids) != 1:
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} table source ref "
                f"{source_ref_index} must identify exactly one source segment; got "
                f"{source_segment_ids!r}."
            )

        window_id = str(source_ref.get("window_id") or "").strip()
        raw_window_index: Any = source_ref.get("window_index")

        if not window_id:
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} table source ref "
                f"{source_ref_index} has no window_id."
            )

        if isinstance(raw_window_index, bool):
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} table source ref "
                f"{source_ref_index} has invalid window_index {raw_window_index!r}."
            )

        try:
            window_index = int(raw_window_index)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} table source ref "
                f"{source_ref_index} has invalid window_index {raw_window_index!r}."
            ) from exc

        if window_index < 0:
            raise ValueError(
                f"Final SFI {record.final_sfi_uuid} table source ref "
                f"{source_ref_index} has negative window_index {window_index}."
            )

        occurrences.add(
            _TableOccurrenceRef(
                source_segment_id=source_segment_ids[0],
                table_header_indexes=table_header_indexes,
                table_row_indexes=table_row_indexes,
                window_id=window_id,
                window_index=window_index,
            )
        )

    return tuple(
        sorted(
            occurrences,
            key=lambda occurrence: (
                occurrence.source_segment_id,
                occurrence.window_index,
                occurrence.window_id,
                occurrence.table_header_indexes,
                occurrence.table_row_indexes,
            ),
        )
    )


def _typed_source_local_parent_texts_by_type(
    *,
    child_context: SFIFinalContext,
    kg_config: CreateKGConfig,
    parent_statement_types: set[str],
) -> dict[str, list[str]]:
    """Collect typed hierarchy-bearing local source texts for parent types.

    Only typed source hierarchy labels are eligible for source-local parent-scope
    inference. This intentionally excludes untyped table rows, Topic titles,
    Performance Objective text, Content text, activities, resources, and evaluation
    prompts. Those untyped fields may contain words that are also valid controlled
    organizer values elsewhere, but they do not prove the child's direct parent.

    Parameters
    ----------
    child_context
        Final SFI context whose hierarchy-bearing source labels should be parsed.
    kg_config
        Runtime KG configuration containing statement-type aliases.
    parent_statement_types
        Allowed direct parent statement types for the child.

    Returns
    -------
    dict[str, list[str]]
        Ordered typed source-local value texts keyed by parent statement_type.
    """

    child_statement_type = child_context.statement_type
    statement_type_aliases_by_type = _statement_type_aliases_by_type(kg_config)
    texts_by_type: dict[str, list[str]] = {
        parent_statement_type: [] for parent_statement_type in parent_statement_types
    }
    hierarchy_labels = [
        *child_context.section_path_labels,
        *child_context.source_context_labels,
    ]

    for hierarchy_label in hierarchy_labels:
        for (
            segment_statement_type,
            segment_value,
        ) in _split_typed_source_hierarchy_segments(
            raw_text=hierarchy_label,
            statement_type_aliases_by_type=statement_type_aliases_by_type,
        ):
            if segment_statement_type == child_statement_type:
                continue

            if segment_statement_type not in parent_statement_types:
                continue

            texts_by_type[segment_statement_type].append(segment_value)

    return {
        parent_statement_type: unique_nonempty(texts)
        for parent_statement_type, texts in texts_by_type.items()
    }


def _validate_child_parent_cardinality(
    *,
    child_edges: Sequence[SFIHasChildEdge],
    child_id: str,
    child_record: SFIFinalRecord,
    parent_requirements_by_child_type: dict[str, list[SFIHasChildParentRequirement]],
    records_by_id: dict[str, SFIFinalRecord],
) -> None:
    """Validate resolved edge counts against the unified parent policy.

    Children with an unresolved root-fallback edge are exempt because no semantic
    parent set was resolved. Root-level child types have an empty requirement list.

    Parameters
    ----------
    child_edges
        Incoming hasChild edges for this child.
    child_id
        String UUID of the child SFI.
    child_record
        Final SFI record for the child.
    parent_requirements_by_child_type
        Allowed parent types and cardinalities keyed by child statement type.
    records_by_id
        Final SFI records keyed by string UUID.

    Raises
    ------
    ValueError
        If a resolved child's parent count falls outside any configured range.
    """

    if any(edge.unresolved_root_fallback for edge in child_edges):
        return

    selected_parent_counts: dict[str, int] = defaultdict(int)

    for edge in child_edges:
        source_id = str(edge.source_entity_uuid)

        if edge.is_root_edge or source_id not in records_by_id:
            continue

        parent_statement_type = records_by_id[source_id].statement_type
        selected_parent_counts[parent_statement_type] += 1

    for requirement in parent_requirements_by_child_type.get(
        child_record.statement_type, []
    ):
        selected_count = selected_parent_counts.get(
            requirement.parent_statement_type, 0
        )

        if selected_count < requirement.min_count:
            raise ValueError(
                f"Resolved hasChild edges for child {child_id} with statement_type "
                f"{child_record.statement_type!r} selected {selected_count} parent(s) "
                f"of type {requirement.parent_statement_type!r}; the configured "
                f"minimum is {requirement.min_count}."
            )

        if requirement.max_count is not None and selected_count > requirement.max_count:
            raise ValueError(
                f"Resolved hasChild edges for child {child_id} with statement_type "
                f"{child_record.statement_type!r} selected {selected_count} parent(s) "
                f"of type {requirement.parent_statement_type!r}; the configured "
                f"maximum is {requirement.max_count}."
            )


def _validate_final_contexts_align_with_records(
    *, contexts: Sequence[SFIFinalContext], sfi_final_records: Sequence[SFIFinalRecord]
) -> None:
    """Validate that loaded final contexts cover the current final SFI records.

    Parameters
    ----------
    contexts
        Loaded final SFI contexts.
    sfi_final_records
        Current final SFI records supplied to relationship resolution.

    Raises
    ------
    ValueError
        If context UUIDs are duplicated, missing, unknown, or materially inconsistent
        with their corresponding final SFI records.
    """

    records_by_id = {str(record.final_sfi_uuid): record for record in sfi_final_records}
    context_ids = [str(context.final_sfi_uuid) for context in contexts]
    context_id_set = set(context_ids)
    duplicate_context_ids = sorted(
        {context_id for context_id in context_ids if context_ids.count(context_id) > 1}
    )
    missing_context_ids = sorted(set(records_by_id) - context_id_set)
    unknown_context_ids = sorted(context_id_set - set(records_by_id))

    if duplicate_context_ids:
        raise ValueError(
            f"sfi_final_contexts.json contains duplicate final_sfi_uuid values: "
            f"{duplicate_context_ids}."
        )

    if missing_context_ids:
        raise ValueError(
            f"sfi_final_contexts.json is missing contexts for final SFIs: "
            f"{missing_context_ids}."
        )

    if unknown_context_ids:
        raise ValueError(
            f"sfi_final_contexts.json contains contexts for unknown final SFIs: "
            f"{unknown_context_ids}."
        )

    for context in contexts:
        record = records_by_id[str(context.final_sfi_uuid)]
        expected_table_header_indexes = sorted(
            {
                index
                for source_ref in record.candidate_source_refs
                if isinstance(source_ref, dict)
                for index in _source_ref_int_list(
                    key="table_header_indexes", source_ref=source_ref
                )
            }
        )
        expected_table_row_indexes = sorted(
            {
                index
                for source_ref in record.candidate_source_refs
                if isinstance(source_ref, dict)
                for index in _source_ref_int_list(
                    key="table_row_indexes", source_ref=source_ref
                )
            }
        )
        expected_source_context_labels = unique_nonempty(
            label
            for source_ref in record.candidate_source_refs
            if isinstance(source_ref, dict)
            for label in _source_ref_text_list(
                key="source_context_labels", source_ref=source_ref
            )
        )
        checks = {
            "audit_flags": context.audit_flags == record.audit_flags,
            "candidate_source_texts": (
                context.candidate_source_texts == record.candidate_source_texts
            ),
            "canonical_statement_value": (
                context.canonical_statement_value == record.canonical_statement_value
            ),
            "canonical_statement_value_key": (
                context.canonical_statement_value_key
                == record.canonical_statement_value_key
            ),
            "description": context.description == record.description,
            "identity_scope_key": (
                context.identity_scope_key == record.identity_scope_key
            ),
            "identity_scope_values": (
                context.identity_scope_values == record.identity_scope_values
            ),
            "normalized_statement_code": (
                context.normalized_statement_code == record.normalized_statement_code
            ),
            "normalized_statement_type": (
                context.normalized_statement_type == record.normalized_statement_type
            ),
            "source_context_keys": (
                context.source_context_keys == record.source_context_keys
            ),
            "source_context_labels": (
                context.source_context_labels == expected_source_context_labels
            ),
            "source_page_indexes": context.source_page_indexes
            == record.source_page_indexes,
            "source_registry_candidate_ids": (
                context.source_registry_candidate_ids
                == record.source_registry_candidate_ids
            ),
            "source_segment_ids": context.source_segment_ids
            == record.source_segment_ids,
            "source_window_ids": context.source_window_ids == record.source_window_ids,
            "source_window_indexes": (
                context.source_window_indexes == record.source_window_indexes
            ),
            "statement_code": context.statement_code == record.statement_code,
            "statement_type": context.statement_type == record.statement_type,
            "table_header_indexes": (
                context.table_header_indexes == expected_table_header_indexes
            ),
            "table_row_indexes": context.table_row_indexes
            == expected_table_row_indexes,
        }
        mismatched_fields = sorted(
            field_name for field_name, matched in checks.items() if not matched
        )

        if mismatched_fields:
            raise ValueError(
                f"sfi_final_contexts.json context for final SFI "
                f"{context.final_sfi_uuid} does not align with the current final "
                f"record fields: {mismatched_fields}."
            )


def _validate_graph(
    *,
    edges: Sequence[SFIHasChildEdge],
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    sfi_final_records: Sequence[SFIFinalRecord],
) -> None:
    """Validate final hasChild graph constraints.

    Examples
    --------

    1. A valid graph has one or more incoming hasChild edges for every final SFI, all
    endpoints exist, and every SFI is reachable from the StandardsFramework root.

    Suppose the finalized SFI universe contains three records:

    grade_4_uuid
    topic_uuid
    competence_uuid

    And the StandardsFramework root UUID is:

    framework_uuid

    A valid edge set might be:

    framework_uuid -> grade_4_uuid
    grade_4_uuid  -> topic_uuid
    topic_uuid    -> competence_uuid

    Calling:

    _validate_graph(
        edges=[
            edge(framework_uuid, grade_4_uuid),
            edge(grade_4_uuid, topic_uuid),
            edge(topic_uuid, competence_uuid),
        ],
        framework_uuid=framework_uuid,
        sfi_final_records=[
            grade_4_record,
            topic_record,
            competence_record,
        ],
    )

    passes because:

    - Every source endpoint is either the framework root or a finalized SFI;
    - Every target endpoint is a finalized SFI;
    - No edge pair is duplicated;
    - No edge is a self-loop;
    - Every finalized SFI has an incoming hasChild edge;
    - There are no SFI-to-SFI cycles;
    - All finalized SFIs are reachable from the framework root;
    - Every relationship_id is unique.

    The function performs structural graph validation only. It does not decide whether
    `grade_4_uuid`, `topic_uuid`, or `competence_uuid` is the semantically best parent;
    that decision is made earlier by the bounded hasChild parent-selection step.

    2. The function rejects missing, duplicated, unknown, and self-loop edges.

    If a final SFI has no incoming edge, validation fails:

    finalized SFIs:
        grade_4_uuid
        topic_uuid

    edges:
        framework_uuid -> grade_4_uuid

    topic_uuid has no incoming edge, so validation raises:
        "Final SFIs missing incoming hasChild edges: [...]"

    If the same edge pair appears twice, validation fails:

    edges:
        framework_uuid -> grade_4_uuid
        framework_uuid -> grade_4_uuid

    Duplicate source/target pair, so validation raises:
        "Duplicate hasChild edge pairs detected: [...]"

    If an edge points to a target that is not in `sfi_final_records`, validation fails:

    edges:
        framework_uuid -> unknown_sfi_uuid

    unknown_sfi_uuid is not a finalized SFI, so validation raises:
        "hasChild target SFI does not exist: ..."

    If an SFI is its own parent, validation fails:

    edges:
        topic_uuid -> topic_uuid

    Source and target are the same SFI, so validation raises:
        "hasChild self-loop detected for SFI ..."

    3. The function also rejects cycles and unreachable graph components.

    A cycle among SFI nodes fails validation even when all endpoints exist:

    framework_uuid -> grade_4_uuid
    grade_4_uuid  -> topic_uuid
    topic_uuid    -> competence_uuid
    competence_uuid -> topic_uuid

    Here, `topic_uuid` and `competence_uuid` form an SFI-to-SFI cycle:

    topic_uuid -> competence_uuid -> topic_uuid

    So `_validate_graph(...)` raises:
        "SFI-to-SFI hasChild cycles detected: [...]"

    An unreachable component also fails validation:

    framework_uuid -> grade_4_uuid
    topic_uuid    -> competence_uuid

    All endpoints may exist, and every SFI may have an incoming edge, but `topic_uuid`
    and `competence_uuid` are not reachable from `framework_uuid`. So validation raises:
        "Final SFIs are not reachable from StandardsFramework root: [...]"

    This reachability check prevents the pipeline from accepting disconnected hasChild
    subgraphs. Every finalized SFI must be connected, directly or indirectly, under the
    StandardsFramework root.

    Parameters
    ----------
    edges
        Final hasChild edges.
    framework_uuid
        StandardsFramework root UUID.
    kg_config
        Runtime KG configuration containing direct parent type policy.
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

    _validate_has_child_statement_type_policy(
        edges=edges,
        framework_uuid=framework_uuid,
        kg_config=kg_config,
        sfi_final_records=sfi_final_records,
    )

    relationship_ids = [str(edge.relationship_id) for edge in edges]
    duplicate_relationship_ids = sorted(
        {value for value in relationship_ids if relationship_ids.count(value) > 1}
    )

    if duplicate_relationship_ids:
        raise ValueError(
            f"Duplicate hasChild relationship IDs detected: {duplicate_relationship_ids}."
        )


def _validate_has_child_edge_parent_type(
    *,
    edge: SFIHasChildEdge,
    framework_uuid: uuid.UUID,
    parent_statement_types_by_child_type: dict[str, set[str]],
    records_by_id: dict[str, SFIFinalRecord],
    root_child_statement_types: set[str],
) -> None:
    """Validate a single hasChild edge against the direct parent statement-type policy.

    Root and framework-root edges are checked against the set of statement types
    permitted at the root, while resolved SFI-to-SFI edges are checked against the
    allowed parent types configured for the child's statement type. Unresolved
    root-fallback edges are accepted at the root and rejected everywhere else.

    Parameters
    ----------
    edge
        The hasChild edge to validate.
    framework_uuid
        StandardsFramework root UUID.
    parent_statement_types_by_child_type
        Allowed parent statement types keyed by child statement type.
    records_by_id
        Final SFI records keyed by string UUID.
    root_child_statement_types
        Statement types permitted directly under the StandardsFramework root.

    Raises
    ------
    ValueError
        If the edge violates the configured direct parent statement-type policy, or an
        unresolved root-fallback edge does not terminate at the root.
    """

    child_record = records_by_id[str(edge.target_sfi_uuid)]

    if edge.is_root_edge or str(edge.source_entity_uuid) == str(framework_uuid):
        if edge.unresolved_root_fallback:
            return

        if child_record.statement_type not in root_child_statement_types:
            raise ValueError(
                f"hasChild root edge violates configured statement-type policy: "
                f"child {child_record.final_sfi_uuid} has statement_type "
                f"{child_record.statement_type!r}; root-level child types are "
                f"{sorted(root_child_statement_types)}. Use an unresolved "
                f"root-fallback edge only when no supplied SFI parent candidate "
                f"is source-supported."
            )

        return

    if edge.unresolved_root_fallback:
        raise ValueError(
            f"hasChild edge for child {child_record.final_sfi_uuid} is marked "
            f"as an unresolved root fallback, but its parent endpoint is not the "
            f"StandardsFramework root."
        )

    parent_record = records_by_id[str(edge.source_entity_uuid)]
    allowed_parent_types = parent_statement_types_by_child_type.get(
        child_record.statement_type, set()
    )

    if parent_record.statement_type not in allowed_parent_types:
        raise ValueError(
            f"hasChild SFI edge violates configured statement-type policy: "
            f"parent {parent_record.final_sfi_uuid} has statement_type "
            f"{parent_record.statement_type!r}; child "
            f"{child_record.final_sfi_uuid} has statement_type "
            f"{child_record.statement_type!r}; allowed parent types are "
            f"{sorted(allowed_parent_types)}."
        )


def _validate_has_child_statement_type_policy(
    *,
    edges: Sequence[SFIHasChildEdge],
    framework_uuid: uuid.UUID,
    kg_config: CreateKGConfig,
    sfi_final_records: Sequence[SFIFinalRecord],
) -> None:
    """Validate final edges against allowed direct-parent types and cardinalities.

    Structural graph checks can pass even when a resolved non-root child is attached to
    the StandardsFramework root or a child is attached to a broader non-direct
    grouping. This validation enforces the configured semantic parent policy after
    response conversion and before relationship artifacts are accepted.

    Unresolved root-fallback edges are intentionally allowed for any statement type.
    They are reachability-preserving audit edges created only when the bounded
    parent-selection response marks a child unresolved and selects no SFI parent.

    Parameters
    ----------
    edges
        Final hasChild edges to validate.
    framework_uuid
        StandardsFramework root UUID.
    kg_config
        Runtime KG configuration containing direct parent type policy.
    sfi_final_records
        Final SFI records whose statement_type labels are checked.

    Raises
    ------
    ValueError
        If a resolved root or SFI parent violates the configured direct parent policy,
        or a resolved child violates a configured parent cardinality.
    """

    parent_requirements_by_child_type = _build_parent_requirements_by_child_type(
        kg_config
    )
    parent_statement_types_by_child_type = _build_direct_parent_statement_types(
        kg_config
    )
    root_child_statement_types = {
        child_type
        for child_type, parent_types in parent_statement_types_by_child_type.items()
        if not parent_types
    }
    records_by_id = {str(record.final_sfi_uuid): record for record in sfi_final_records}
    incoming_edges_by_child_id: dict[str, list[SFIHasChildEdge]] = defaultdict(list)

    for edge in edges:
        child_record = records_by_id[str(edge.target_sfi_uuid)]
        incoming_edges_by_child_id[str(child_record.final_sfi_uuid)].append(edge)
        _validate_has_child_edge_parent_type(
            edge=edge,
            framework_uuid=framework_uuid,
            parent_statement_types_by_child_type=parent_statement_types_by_child_type,
            records_by_id=records_by_id,
            root_child_statement_types=root_child_statement_types,
        )

    for child_id, child_record in records_by_id.items():
        _validate_child_parent_cardinality(
            child_edges=incoming_edges_by_child_id.get(child_id, []),
            child_id=child_id,
            child_record=child_record,
            parent_requirements_by_child_type=parent_requirements_by_child_type,
            records_by_id=records_by_id,
        )


def _validate_resolution_artifact_prefix(
    *,
    draft_responses: Sequence[SFIHasChildResolutionResponse],
    final_responses: Sequence[SFIHasChildResolutionResponse],
    requests: Sequence[SFIHasChildResolutionRequest],
    validation_verdicts: Sequence[SFIHasChildValidationVerdict],
) -> None:
    """Validate an aligned producer/checker artifact prefix.

    Parameters
    ----------
    draft_responses
        Producer draft responses.
    final_responses
        Accepted or checker-corrected final responses.
    requests
        Current deterministic requests.
    validation_verdicts
        Independent checker verdicts.

    Raises
    ------
    QualityError
        If any draft, verdict, or final response violates universal integrity.
    ValueError
        If artifact counts or final-response selection are inconsistent.
    """

    prefix_length = len(final_responses)

    if not len(draft_responses) == prefix_length == len(validation_verdicts):
        raise ValueError(
            f"hasChild producer/checker artifact prefix counts must match: "
            f"drafts={len(draft_responses)}, finals={len(final_responses)}, "
            f"verdicts={len(validation_verdicts)}."
        )

    if prefix_length > len(requests):
        raise ValueError(
            f"Found {prefix_length} completed hasChild artifacts, but only "
            f"{len(requests)} requests are planned."
        )

    for response_index in range(prefix_length):
        draft_response = draft_responses[response_index]
        final_response = final_responses[response_index]
        request = requests[response_index]
        validation_verdict = validation_verdicts[response_index]

        try:
            verify_sfi_has_child_resolution_integrity(
                resolution_request=request,
                resolution_response=draft_response,
            )
            verify_sfi_has_child_validation_integrity(
                draft_response=draft_response,
                resolution_request=request,
                validation_verdict=validation_verdict,
            )
        except QualityError as exc:
            raise QualityError(
                f"Saved hasChild producer/checker artifacts at position "
                f"{response_index + 1} do not match the current request: {exc}"
            ) from exc

        expected_final_response = (
            draft_response
            if validation_verdict.passed
            else validation_verdict.corrected_response
        )

        if expected_final_response is None:
            raise ValueError(
                f"Failing checker verdict at position {response_index + 1} has no "
                f"corrected response."
            )

        if model_dump_key(final_response) != model_dump_key(expected_final_response):
            raise ValueError(
                f"Saved final hasChild response at position {response_index + 1} "
                f"does not equal the producer-accepted or checker-corrected response."
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

    assert_model_sequences_equal(
        actual=saved_requests[:trusted_prefix_length],
        artifact_label="saved hasChild request completed-response prefix",
        expected=requests[:trusted_prefix_length],
    )


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


def _write_parent_set_artifacts(
    *, parent_sets: Sequence[SFIHasChildCandidateParentSet], parent_sets_fp: Path
) -> None:
    """Write deterministic bounded parent-candidate set artifacts.

    Parameters
    ----------
    parent_sets
        Current bounded parent-candidate sets generated for hasChild resolution.
    parent_sets_fp
        JSONL path for persisting parent-candidate sets.
    """

    parent_sets_fp.write_text("", encoding="utf-8")

    for parent_set in parent_sets:
        append_jsonl_model(fp=parent_sets_fp, model=parent_set)


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
    """Resolve finalized SFI hasChild edges through a producer/checker flow.

    Deterministic Python retrieves and bounds candidates, packages exact evidence,
    validates universal response and graph contracts, and persists artifacts. The
    producer/checker LLMs own direct-parent semantics and unresolved decisions.

    Parameters
    ----------
    document_ir
        Source DocumentIR.
    kg_config
        Runtime configuration containing producer/checker hierarchy instructions.
    kg_dirs
        KG artifact directory wrapper.
    overwrite
        Whether to discard all relationship-stage artifacts and restart.
    sfi_final_records
        Finalized SFI records.
    usage_tracker
        Producer/checker usage tracker.

    Returns
    -------
    list[SFIHasChildEdge]
        Validated final hasChild edges.
    """

    _validate_sfi_final_records_and_summary(
        kg_dirs=kg_dirs, sfi_final_records=sfi_final_records
    )
    make_dir(kg_dirs.root)
    contexts_fp = kg_dirs.root / "sfi_final_contexts.json"
    draft_responses_fp = kg_dirs.root / "has_child_resolution_draft_responses.jsonl"
    edges_fp = kg_dirs.root / "has_child_edges_final.json"
    final_responses_fp = kg_dirs.root / "has_child_resolution_responses.jsonl"
    parent_sets_fp = kg_dirs.root / "has_child_candidate_parent_sets.jsonl"
    requests_fp = kg_dirs.root / "has_child_resolution_requests.jsonl"
    summary_fp = kg_dirs.root / "has_child_resolution_summary.json"
    unresolved_edges_fp = kg_dirs.root / "has_child_unresolved_edges.json"
    validation_verdicts_fp = (
        kg_dirs.root / "has_child_resolution_validation_verdicts.jsonl"
    )
    framework_uuid = build_standards_framework_uuid(document_ir.doc_key)
    extraction_windows = _load_extraction_windows(kg_dirs)
    contexts = _load_final_contexts(contexts_fp)
    _validate_final_contexts_align_with_records(
        contexts=contexts, sfi_final_records=sfi_final_records
    )
    table_occurrences_by_uuid = {
        record.final_sfi_uuid: _table_occurrences_from_source_refs(record)
        for record in sfi_final_records
    }
    table_context_keys_by_uuid = {
        final_sfi_uuid: _table_context_keys_from_occurrences(table_occurrences)
        for final_sfi_uuid, table_occurrences in table_occurrences_by_uuid.items()
    }
    parent_sets = _build_candidate_parent_sets(
        contexts=contexts,
        extraction_windows=extraction_windows,
        framework_uuid=framework_uuid,
        kg_config=kg_config,
        sfi_final_records=sfi_final_records,
        table_context_keys_by_uuid=table_context_keys_by_uuid,
        table_occurrences_by_uuid=table_occurrences_by_uuid,
    )
    requests = [
        SFIHasChildResolutionRequest(
            child_parent_sets=[parent_set],
            request_id=(
                "has_child_request_"
                + hashlib.sha256(
                    normalize_text(str(parent_set.child_context.final_sfi_uuid)).encode(
                        "utf-8"
                    )
                ).hexdigest()[:16]
            ),
            sfi_has_child_instructions=(
                kg_config.academic_standards.sfi_has_child_instructions
            ),
            sfi_has_child_validation_instructions=(
                kg_config.academic_standards.sfi_has_child_validation_instructions
            ),
        )
        for parent_set in parent_sets
    ]

    if overwrite:
        logger.info(
            "Starting SFI hasChild producer/checker resolution from scratch because "
            "overwrite=True."
        )

        reset_output_files(
            output_fps=[
                draft_responses_fp,
                edges_fp,
                final_responses_fp,
                parent_sets_fp,
                requests_fp,
                summary_fp,
                unresolved_edges_fp,
                validation_verdicts_fp,
            ]
        )
        completed_progress = _ResolutionProgress(
            draft_responses=[],
            final_responses=[],
            validation_verdicts=[],
        )
    else:
        existing_edges = _load_and_validate_existing_relationship_artifacts(
            contexts=contexts,
            contexts_fp=contexts_fp,
            document_ir=document_ir,
            draft_responses_fp=draft_responses_fp,
            edges_fp=edges_fp,
            final_responses_fp=final_responses_fp,
            framework_uuid=framework_uuid,
            kg_config=kg_config,
            parent_sets=parent_sets,
            parent_sets_fp=parent_sets_fp,
            requests=requests,
            requests_fp=requests_fp,
            sfi_final_records=sfi_final_records,
            summary_fp=summary_fp,
            unresolved_edges_fp=unresolved_edges_fp,
            validation_verdicts_fp=validation_verdicts_fp,
        )

        if existing_edges is not None:
            return existing_edges

        completed_progress = _load_resumable_resolution_progress(
            draft_responses_fp=draft_responses_fp,
            final_responses_fp=final_responses_fp,
            requests=requests,
            requests_fp=requests_fp,
            validation_verdicts_fp=validation_verdicts_fp,
        )
        reset_output_files(
            output_fps=[
                edges_fp,
                parent_sets_fp,
                summary_fp,
                unresolved_edges_fp,
            ]
        )
        completed_count = len(completed_progress.final_responses)
        _rewrite_resolution_progress_files(
            completed_draft_responses=completed_progress.draft_responses,
            completed_final_responses=completed_progress.final_responses,
            completed_requests=requests[:completed_count],
            completed_validation_verdicts=(completed_progress.validation_verdicts),
            draft_responses_fp=draft_responses_fp,
            final_responses_fp=final_responses_fp,
            requests_fp=requests_fp,
            validation_verdicts_fp=validation_verdicts_fp,
        )

    _write_parent_set_artifacts(
        parent_sets=parent_sets,
        parent_sets_fp=parent_sets_fp,
    )
    progress = _run_resolution_requests(
        completed_progress=completed_progress,
        draft_responses_fp=draft_responses_fp,
        final_responses_fp=final_responses_fp,
        requests=requests,
        requests_fp=requests_fp,
        usage_tracker=usage_tracker,
        validation_verdicts_fp=validation_verdicts_fp,
    )
    edges = _build_edges_from_responses(
        document_ir=document_ir,
        framework_uuid=framework_uuid,
        parent_sets=parent_sets,
        requests=requests,
        responses=progress.final_responses,
        validation_verdicts=progress.validation_verdicts,
    )
    _validate_graph(
        edges=edges,
        framework_uuid=framework_uuid,
        kg_config=kg_config,
        sfi_final_records=sfi_final_records,
    )
    summary = SFIHasChildResolutionSummary(
        candidate_parent_set_count=len(parent_sets),
        checker_corrected_response_count=sum(
            1 for verdict in progress.validation_verdicts if not verdict.passed
        ),
        checker_request_count=len(requests),
        checker_verdict_count=len(progress.validation_verdicts),
        edge_count=len(edges),
        final_sfi_count=len(parent_sets),
        generator_request_count=len(requests),
        generator_response_count=len(progress.draft_responses),
        root_edge_count=sum(1 for edge in edges if edge.is_root_edge),
        sfi_to_sfi_edge_count=sum(1 for edge in edges if not edge.is_root_edge),
        truncated_candidate_parent_set_count=sum(
            1 for parent_set in parent_sets if parent_set.was_truncated
        ),
        unresolved_child_count=sum(
            1 for edge in edges if edge.unresolved_root_fallback
        ),
    )
    _write_resolution_artifacts(
        edges=edges,
        edges_fp=edges_fp,
        summary=summary,
        summary_fp=summary_fp,
        unresolved_edges_fp=unresolved_edges_fp,
    )

    logger.success(
        f"Resolved final hasChild edges: edges={len(edges)}; "
        f"checker_corrections={summary.checker_corrected_response_count}; "
        f"root_edges={summary.root_edge_count}; "
        f"unresolved={summary.unresolved_child_count}."
    )

    return edges
