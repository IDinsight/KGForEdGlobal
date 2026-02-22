"""Translate skeleton match results into SegmentDecision objects.

This is the core schema alignment layer.  The matching engine's output
(``MatchedSegment``) must be converted into valid ``SegmentDecision`` objects
that pass all existing validators and can be consumed unchanged by
``compile_canonical_ir()``.
"""

# Future Library
from __future__ import annotations

# Standard Library
import re

from dataclasses import dataclass
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    CurriculumColumnMapping,
    CurriculumEmitPolicy,
    CurriculumSkeletonNode,
    GroupingDecision,
    LeafDecision,
    RowDecision,
    SegmentDecision,
)
from skg.canonical_ir.skeleton_engine import MatchedSegment
from skg.canonical_ir.utils import MatchableSegment
from skg.document_ir.schemas import TableSegment
from skg.page_ir_extraction.schemas import TextUnit
from skg.utils.constants import (
    DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER,
    BlockType,
    NodeRole,
    SegmentDecisionType,
    StatementRole,
)

# ── Context groupings ────────────────────────────────────────────────────────────


def build_context_groupings(
    *,
    ancestry: list[CurriculumSkeletonNode],
    matched_node: CurriculumSkeletonNode,
    role_order: list[NodeRole] | None = None,
) -> list[GroupingDecision]:
    """Build context_groupings from the skeleton ancestry chain.

    Includes ancestors that:

    * Have a ``grouping_role`` (not None, not FRAMEWORK).
    * Are NOT the matched node itself (that goes in ``groupings``).
    * Are ``EMIT_GROUPING``/``EMIT_GROUPING_AND_LEAF``/``EMIT_TABLE_ROWS``
      (visible in document), OR are ``CONTAINER_ONLY`` with ``implicit=True``.

    The result is sorted by role precedence (outer → inner).

    Parameters
    ----------
    ancestry
        Full ancestry chain from root to matched node (inclusive).
    matched_node
        The node that was matched (excluded from context).
    role_order
        Custom role precedence order.  Falls back to
        ``DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER``.

    Returns
    -------
    list[GroupingDecision]
        Sorted context groupings for the SegmentDecision.
    """

    precedence = {
        role: i
        for i, role in enumerate(role_order or DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER)
    }
    context: list[GroupingDecision] = []

    for node in ancestry:
        # Stop before the matched node itself.
        if node.id == matched_node.id:
            break

        # Skip nodes without grouping roles.
        if node.grouping_role is None or node.grouping_role == NodeRole.FRAMEWORK:
            continue

        # CONTAINER_ONLY nodes are invisible UNLESS implicit=True.
        if node.emit == CurriculumEmitPolicy.CONTAINER_ONLY and not node.implicit:
            continue

        context.append(
            GroupingDecision(
                role=node.grouping_role,
                title=node.canonical_name.primary,
                source_label=node.source_label,
                local_code=node.local_code,
            )
        )

    context.sort(key=lambda g: precedence.get(g.role, 999))
    return context


# ── Translation by emit policy ───────────────────────────────────────────────────


def translate_matched_segment(
    matched: MatchedSegment,
    *,
    doc_key: str,
    role_order: list[NodeRole] | None = None,
) -> SegmentDecision:
    """Convert a MatchedSegment into a SegmentDecision.

    Parameters
    ----------
    matched
        The matched segment from the engine.
    doc_key
        Document key for decision ID generation.
    role_order
        Custom role precedence for context_groupings sorting.

    Returns
    -------
    SegmentDecision
        A valid SegmentDecision ready for ``compile_canonical_ir()``.
    """

    node = matched.node
    seg = matched.segment
    context = build_context_groupings(
        ancestry=matched.ancestry,
        matched_node=node,
        role_order=role_order,
    )
    decision_id = f"skeleton:{doc_key}:{seg.segment_id}"
    block_type = BlockType(seg.block_type) if seg.block_type else None

    # Combine text from bilingual pairs.
    all_texts = [seg.text or ""]
    for extra in matched.additional_segments:
        if extra.text:
            all_texts.append(extra.text)
    combined_text = "\n\n".join(t for t in all_texts if t.strip())

    # ── IGNORE ──
    if node.emit == CurriculumEmitPolicy.IGNORE:
        return SegmentDecision(
            decision_id=decision_id,
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
            block_type=block_type,
            decision_type=SegmentDecisionType.IGNORE,
            confidence=1.0,
            rationale=f"Skeleton IGNORE: '{node.id}'.",
            context_groupings=[],
            groupings=[],
            leaves=[],
            rows=[],
        )

    # ── EMIT_GROUPING ──
    if node.emit == CurriculumEmitPolicy.EMIT_GROUPING:
        return SegmentDecision(
            decision_id=decision_id,
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
            block_type=block_type,
            decision_type=SegmentDecisionType.EMIT_GROUPINGS_ONLY,
            confidence=1.0,
            rationale=(
                f"Skeleton EMIT_GROUPING: '{node.id}' " f"→ {node.grouping_role.value}."
            ),
            context_groupings=context,
            groupings=[
                GroupingDecision(
                    role=node.grouping_role,
                    title=node.canonical_name.primary,
                    source_label=node.source_label,
                    local_code=node.local_code,
                )
            ],
            leaves=[],
            rows=[],
        )

    # ── EMIT_LEAF ──
    if node.emit == CurriculumEmitPolicy.EMIT_LEAF:
        return SegmentDecision(
            decision_id=decision_id,
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
            block_type=block_type,
            decision_type=SegmentDecisionType.EMIT_LEAVES_ONLY,
            confidence=1.0,
            rationale=(f"Skeleton EMIT_LEAF: '{node.id}' → {node.leaf_role.value}."),
            context_groupings=context,
            groupings=[],
            leaves=[
                LeafDecision(
                    role=node.leaf_role,
                    body=combined_text,
                    source_label=node.source_label,
                )
            ],
            rows=[],
        )

    # ── EMIT_GROUPING_AND_LEAF ──
    if node.emit == CurriculumEmitPolicy.EMIT_GROUPING_AND_LEAF:
        return SegmentDecision(
            decision_id=decision_id,
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
            block_type=block_type,
            decision_type=SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES,
            confidence=1.0,
            rationale=f"Skeleton EMIT_GROUPING_AND_LEAF: '{node.id}'.",
            context_groupings=context,
            groupings=[
                GroupingDecision(
                    role=node.grouping_role,
                    title=node.canonical_name.primary,
                    source_label=node.source_label,
                    local_code=node.local_code,
                )
            ],
            leaves=[
                LeafDecision(
                    role=node.leaf_role,
                    body=combined_text,
                    source_label=node.source_label,
                )
            ],
            rows=[],
        )

    # ── EMIT_TABLE_ROWS ──
    if node.emit == CurriculumEmitPolicy.EMIT_TABLE_ROWS:
        return _translate_table_rows(
            context=context,
            decision_id=decision_id,
            matched=matched,
            node=node,
            seg=seg,
        )

    raise ValueError(f"Unhandled emit policy: {node.emit}")


# ── Unmatched segments ───────────────────────────────────────────────────────────


def translate_unmatched(
    seg: MatchableSegment,
    *,
    doc_key: str,
) -> SegmentDecision:
    """Convert an unmatched segment into an IGNORE SegmentDecision.

    Parameters
    ----------
    seg
        The unmatched MatchableSegment.
    doc_key
        Document key for decision ID generation.

    Returns
    -------
    SegmentDecision
        An IGNORE decision.
    """

    return SegmentDecision(
        decision_id=f"skeleton:{doc_key}:{seg.segment_id}:unmatched",
        segment_id=seg.segment_id,
        segment_kind=seg.segment_kind,
        block_type=BlockType(seg.block_type) if seg.block_type else None,
        decision_type=SegmentDecisionType.IGNORE,
        confidence=1.0,
        rationale="No skeleton node matched this segment.",
        context_groupings=[],
        groupings=[],
        leaves=[],
        rows=[],
    )


# ── Table row extraction ─────────────────────────────────────────────────────────


@dataclass
class ResolvedColumnRole:
    """Resolved role for a single table column."""

    col_index: int
    kind: str  # "grouping", "leaf", or "skip"
    role_value: str  # e.g., "strand", "expectation"
    source_label: str  # Column header text (for source_label)


def _translate_table_rows(
    *,
    context: list[GroupingDecision],
    decision_id: str,
    node: CurriculumSkeletonNode,
    seg: MatchableSegment,
) -> SegmentDecision:
    """Build a SegmentDecision with RowDecision[] from a table segment.

    Parameters
    ----------
    context
        Pre-built context_groupings from skeleton ancestry.
    decision_id
        The decision ID string.
    node
        The skeleton node that matched (has column_mappings).
    seg
        The MatchableSegment wrapper.

    Returns
    -------
    SegmentDecision
        A table decision with per-row RowDecisions.
    """

    assert isinstance(seg.raw_segment, TableSegment)
    table_seg: TableSegment = seg.raw_segment

    # Build column index → role mapping from skeleton + table headers.
    col_map = _resolve_column_mappings(
        column_mappings=node.column_mappings,
        header_rows_canonical=list(list(row) for row in seg.header_rows_canonical),
    )

    # Prefer rows_filldown if available (merged cells filled down).
    rows_source = table_seg.rows_filldown or table_seg.rows
    header_n = table_seg.header_row_count or 0

    row_decisions: list[RowDecision] = []

    for abs_i, row in enumerate(rows_source):
        if abs_i < header_n:
            continue  # Skip header rows.

        row_groupings, row_leaves = _extract_row_content(
            col_map=col_map,
            default_leaf_role=node.leaf_role,
            row=row,
        )

        if not row_groupings and not row_leaves:
            continue

        row_decisions.append(
            RowDecision(
                row_index=abs_i,
                groupings=row_groupings,
                leaves=row_leaves,
            )
        )

    # Determine decision type.
    has_grouping = bool(node.grouping_role)
    has_rows = bool(row_decisions)

    if has_grouping and has_rows:
        dt = SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES
    elif has_rows:
        dt = SegmentDecisionType.EMIT_LEAVES_ONLY
    elif has_grouping:
        dt = SegmentDecisionType.EMIT_GROUPINGS_ONLY
    else:
        dt = SegmentDecisionType.IGNORE

    groupings: list[GroupingDecision] = []
    if node.grouping_role:
        groupings.append(
            GroupingDecision(
                role=node.grouping_role,
                title=node.canonical_name.primary,
                source_label=node.source_label,
                local_code=node.local_code,
            )
        )

    return SegmentDecision(
        decision_id=decision_id,
        segment_id=seg.segment_id,
        segment_kind="table",
        block_type=None,
        columns_signature=seg.columns_signature,
        caption_text=seg.caption_text,
        caption_kind=seg.caption_kind,
        caption_segment_id=seg.caption_segment_id,
        caption_page_index=seg.caption_page_index,
        caption_gap_segments=seg.caption_gap_segments,
        decision_type=dt,
        confidence=1.0,
        rationale=(f"Skeleton TABLE: '{node.id}' → {len(row_decisions)} data rows."),
        context_groupings=context,
        groupings=groupings,
        leaves=[],
        rows=row_decisions,
    )


def _resolve_column_mappings(
    *,
    column_mappings: list[CurriculumColumnMapping],
    header_rows_canonical: list[list[str]],
) -> list[ResolvedColumnRole]:
    """Match skeleton column_mappings against actual table headers.

    Uses the first canonical header row for matching.  Each column is tested
    against every mapping in order; first match wins.  Unmatched columns
    default to ``skip``.

    Parameters
    ----------
    column_mappings
        Column-to-role mappings from the skeleton node.
    header_rows_canonical
        Canonical header rows as ``list[list[str]]`` from the table segment.

    Returns
    -------
    list[ResolvedColumnRole]
        One entry per column in the header row.
    """

    if not header_rows_canonical:
        return []

    # Use the first header row for matching.
    headers = header_rows_canonical[0]
    resolved: list[ResolvedColumnRole] = []

    for col_idx, header_text in enumerate(headers):
        matched_mapping: CurriculumColumnMapping | None = None

        for mapping in column_mappings:
            if re.search(
                mapping.header_pattern,
                header_text,
                re.IGNORECASE | re.UNICODE,
            ):
                matched_mapping = mapping
                break

        if matched_mapping is None or matched_mapping.role == "skip":
            resolved.append(
                ResolvedColumnRole(
                    col_index=col_idx,
                    kind="skip",
                    role_value="",
                    source_label=header_text,
                )
            )
        else:
            kind, role_value = matched_mapping.role.split(":", 1)
            resolved.append(
                ResolvedColumnRole(
                    col_index=col_idx,
                    kind=kind,
                    role_value=role_value,
                    source_label=(matched_mapping.source_label_override or header_text),
                )
            )

    return resolved


def _extract_row_content(
    *,
    col_map: list[ResolvedColumnRole],
    default_leaf_role: StatementRole | None,
    row: Any,  # TableRow from page_ir_extraction.schemas
) -> tuple[list[GroupingDecision], list[LeafDecision]]:
    """Extract groupings and leaves from a single table row using column map.

    Parameters
    ----------
    col_map
        Resolved column-to-role mapping.
    default_leaf_role
        Fallback leaf role when no column_mappings matched any column.
    row
        A TableRow object from DocumentIR (has ``.cells``).

    Returns
    -------
    tuple[list[GroupingDecision], list[LeafDecision]]
        Row-level groupings and leaves.
    """

    groupings: list[GroupingDecision] = []
    leaves: list[LeafDecision] = []
    cells = getattr(row, "cells", [])

    for col_role in col_map:
        if col_role.col_index >= len(cells):
            continue

        cell = cells[col_role.col_index]
        cell_text = _cell_to_text(cell)

        if not cell_text or not cell_text.strip():
            continue

        if col_role.kind == "skip":
            continue

        if col_role.kind == "grouping":
            try:
                role = NodeRole(col_role.role_value)
            except ValueError:
                logger.warning(
                    f"Invalid NodeRole '{col_role.role_value}' in column mapping; "
                    f"skipping column {col_role.col_index}."
                )
                continue

            groupings.append(
                GroupingDecision(
                    role=role,
                    title=cell_text.strip(),
                    source_label=col_role.source_label,
                )
            )

        elif col_role.kind == "leaf":
            try:
                role = StatementRole(col_role.role_value)
            except ValueError:
                logger.warning(
                    f"Invalid StatementRole '{col_role.role_value}' in column "
                    f"mapping; skipping column {col_role.col_index}."
                )
                continue

            leaves.append(
                LeafDecision(
                    role=role,
                    body=cell_text.strip(),
                    source_label=col_role.source_label,
                )
            )

    # Fallback: if no column_mappings produced leaves but a default role exists,
    # and there were no col_map entries at all, concatenate all non-empty cells.
    if not leaves and default_leaf_role and not col_map:
        parts: list[str] = []
        for cell in cells:
            text = _cell_to_text(cell)
            if text and text.strip():
                parts.append(text.strip())
        if parts:
            leaves.append(
                LeafDecision(
                    role=default_leaf_role,
                    body="\n\n".join(parts),
                )
            )

    return groupings, leaves


def _cell_to_text(cell: Any) -> str:
    """Extract plain text from a table cell.

    Handles both ``TextUnit`` objects (with ``.text`` attribute) and dicts.

    Parameters
    ----------
    cell
        A table cell object (typically TextUnit or dict).

    Returns
    -------
    str
        The extracted text, or empty string.
    """

    if cell is None:
        return ""

    # TextUnit object.
    if isinstance(cell, TextUnit):
        return (cell.text or "").strip()

    if hasattr(cell, "text"):
        inner = cell.text
        if isinstance(inner, TextUnit):
            return (inner.text or "").strip()
        if isinstance(inner, str):
            return inner.strip()
        return str(inner or "")

    # Dict fallback.
    if isinstance(cell, dict):
        t = cell.get("text", "")
        if isinstance(t, dict):
            return (t.get("text", "") or "").strip()
        return str(t or "").strip()

    return str(cell or "").strip()
