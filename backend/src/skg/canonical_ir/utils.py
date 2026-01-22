"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import hashlib
import re
import unicodedata
import uuid

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, TypeVar

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.llm import generate_segment_decision
from skg.canonical_ir.schemas import (
    CanonicalEdge,
    CanonicalIR,
    CanonicalNode,
    GroupingDecision,
    LeafDecision,
    SegmentDecision,
    SegmentDecisionSet,
    UnresolvedItem,
    compute_decision_set_id,
)
from skg.config import Settings
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import CreateCanonicalConfig, RunCtx
from skg.utils.constants import (
    BlockType,
    CaptionFigurePrefixes,
    CaptionKind,
    CaptionTablePrefixes,
    NodeRole,
    SegmentDecisionType,
    UnresolvedReason,
)
from skg.utils.general import make_dir, open_json_type, write_to_json

T = TypeVar("T")

# Compiled regexes.
_DASH_RE = re.compile(r"[‐-‒–—−]+")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path
    canonical_ir: Path
    segment_decisions: Path


@dataclass(frozen=True)
class CaptionBinding:
    """Dataclass for caption-to-table bindings."""

    caption_kind: CaptionKind
    caption_page_index: int | None
    caption_segment_id: str
    caption_text: str
    gap_segments: int
    table_page_index: int | None
    table_segment_id: str


@dataclass(frozen=True)
class ContextFrame:
    """One frame in the active context stack (excluding the framework root)."""

    grouping_key: str
    node_id: str


def _check_cycles(*, child_to_parent: dict[str, str], warnings: list[str]) -> None:
    """Detect cycles by following parent pointers.

    Parameters
    ----------
    child_to_parent
        The mapping of child_id to parent_id.
    warnings
        The list of warnings to append to.
    """

    for child in child_to_parent:
        seen: set[str] = set()
        cur = child

        while cur in child_to_parent:
            if cur in seen:
                msg = f"cycle_detected_at:{cur}"
                logger.warning(msg)
                warnings.append(msg)
                break

            seen.add(cur)
            cur = child_to_parent[cur]


def _check_multiple_parents(
    *, edges: list[CanonicalEdge], warnings: list[str]
) -> dict[str, str]:
    """Ensure no child has more than 1 parent and return the child->parent map.

    Parameters
    ----------
    edges
        The list of CanonicalEdges to check.
    warnings
        The list of warnings to append to.

    Returns
    -------
    dict[str, str]
        The mapping of child_id to parent_id.
    """

    child_to_parent: dict[str, str] = {}

    for e in edges:
        existing = child_to_parent.get(e.child_id)

        if existing is None:
            child_to_parent[e.child_id] = e.parent_id
        elif existing != e.parent_id:
            msg = (
                f"tree_invariant_violation_multiple_parents:"
                f"child={e.child_id} p1={existing} p2={e.parent_id}"
            )
            logger.warning(msg)
            warnings.append(msg)

    return child_to_parent


def _check_order_indices(*, edges: list[CanonicalEdge], warnings: list[str]) -> None:
    """Validate order indices: check for duplicates, zero-start, and gaps.

    Parameters
    ----------
    edges
        The list of CanonicalEdges to check.
    warnings
        The list of warnings to append to.
    """

    # Group orders by parent.
    parent_to_orders: dict[str, list[int]] = {}
    for e in edges:
        parent_to_orders.setdefault(e.parent_id, []).append(e.order_index)

    # Validate each parent's group.
    for parent, orders in parent_to_orders.items():
        # Check duplicates.
        if len(orders) != len(set(orders)):
            msg = f"order_index_duplicate_under_parent:{parent}"
            logger.warning(msg)
            warnings.append(msg)

        sorted_orders = sorted(set(orders))

        # Check strict contiguous ordering starting at 0.
        if sorted_orders and sorted_orders[0] != 0:
            msg = f"order_index_not_starting_at_zero:{parent} min={sorted_orders[0]}"
            logger.warning(msg)
            warnings.append(msg)

        # Check contiguity ignoring dropped edges
        for i in range(1, len(sorted_orders)):
            if sorted_orders[i] != sorted_orders[i - 1] + 1:
                msg = f"order_index_gap_under_parent:{parent} orders={sorted_orders}"
                logger.warning(msg)
                warnings.append(msg)
                break


def _check_root_as_child(
    *, edges: list[CanonicalEdge], root_id: str, warnings: list[str]
) -> None:
    """Ensure the root does not appear as a child (tree invariant).

    Parameters
    ----------
    edges
        The list of CanonicalEdges to check.
    root_id
        The root node ID.
    warnings
        The list of warnings to append to.
    """

    for e in edges:
        if e.child_id == root_id:
            msg = f"root_has_parent_edge:{root_id} parent={e.parent_id}"
            logger.warning(msg)
            warnings.append(msg)


def _check_structural_warnings(
    *,
    decision: SegmentDecision,
    page_indices: list[int],
    section_path_text: list[str],
    segment_id: str,
    segment_kind: str,
    structural_leaf_warn_threshold: float,
    warnings: list[str],
) -> None:
    """Check decision confidence against structural threshold and emit warnings.

    Parameters
    ----------
    decision
        The SegmentDecision to check.
    page_indices
        The list of page indices for the segment.
    section_path_text
        The section path text for the segment.
    segment_id
        The segment ID.
    segment_kind
        The segment kind.
    structural_leaf_warn_threshold
        The structural leaf warning confidence threshold.
    warnings
        The list of warnings to append to.
    """

    if decision.confidence >= structural_leaf_warn_threshold:
        return

    leaf_count = _count_decision_leaves(decision)

    if leaf_count <= 0:
        return

    pages_str = _format_page_indices(page_indices)
    path_str = _format_section_path(section_path_text=section_path_text)

    if decision.row_range_start is not None or decision.row_range_end is not None:
        start = (
            decision.row_range_start if decision.row_range_start is not None else "-"
        )
        end = decision.row_range_end if decision.row_range_end is not None else "-"
        row_range_str = f"[{start},{end})"
    else:
        row_range_str = "-"

    msg = (
        f"structural_leaf_review:"
        f"segment_id={segment_id} decision_id={decision.decision_id} "
        f"kind={segment_kind} conf={decision.confidence:.3f} "
        f"leaf_count={leaf_count} threshold={structural_leaf_warn_threshold:.3f} "
        f"row_range={row_range_str} "
        f"pages={pages_str} "
        f"section_path={path_str}"
    )
    logger.warning(msg)
    warnings.append(msg)


def _classify_caption_kind(text: str) -> CaptionKind:
    """Classify caption kind based on text prefixes.

    Parameters
    ----------
    text
        The caption text to classify.

    Returns
    -------
    CaptionKind
        The classified caption kind.
    """

    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)

    for p in CaptionTablePrefixes:
        if t.startswith(p):
            return "table"

    for p in CaptionFigurePrefixes:
        if t.startswith(p):
            return "figure"

    # Regex fallback for common patterns.
    if re.match(r"^(table|tab\.?|tbl\.?|jedwali|tableau)\s*\d+", t):
        return "table"

    if re.match(r"^(figure|fig\.?)\s*\d+", t):
        return "figure"

    return "unknown"


def _groupings_to_payload_dicts(gs: list[Any] | None) -> list[dict[str, Any]]:
    """Convert a list of GroupingDecision to list of dicts for payload.

    Parameters
    ----------
    gs
        The list of GroupingDecision to convert.

    Returns
    -------
    list[dict[str, Any]]
        The converted list of dicts.
    """

    return [g.model_dump(mode="json") for g in (gs or [])]


def _count_decision_leaves(d: SegmentDecision) -> int:
    """Count statement leaves emitted by this decision (block leaves + table row
    leaves).

    Parameters
    ----------
    d
        The SegmentDecision to count leaves for.

    Returns
    -------
    int
        The total number of leaves in the decision.
    """

    n = 0
    n += len(d.leaves or [])

    for r in d.rows or []:
        n += len(r.leaves or [])

    return n


def _decision_sort_key(d: SegmentDecision) -> tuple[int, int, str]:
    """Sort key for SegmentDecision ordering.

    Parameters
    ----------
    d
        The SegmentDecision to compute the sort key for.

    Returns
    -------
    tuple[int, int, str]
        The sort key as (row_range_start, row_range_end, decision_id).
    """

    start = d.row_range_start if d.row_range_start is not None else -1
    end = d.row_range_end if d.row_range_end is not None else 2**31 - 1
    decision_id = getattr(d, "decision_id", "") or ""

    return start, end, decision_id


def _emit_edge(
    *,
    child_id: str,
    child_to_parent: dict[str, str],
    decision_id: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    parent_id: str,
    segment_id: str,
    warnings: list[str],
) -> None:
    """Emit edge and assign order_index by encounter order.

    NB:
    1. Keep-first-parent tree enforcement.
    2. Drop and warn on parent conflicts.
    3. Assign order_index by first encounter order per parent.
    4. Merge provenance if the exact same edge is encountered again.

    Parameters
    ----------
    child_id
        The child node ID.
    child_to_parent
        The mapping of child_id to parent_id.
    decision_id
        The SegmentDecision ID.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge.
    next_order_index
        The mapping of parent_id to next order_index.
    parent_id
        The parent node ID.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.
    """

    # Keep-first-parent tree enforcement.
    existing_parent = child_to_parent.get(child_id)
    if existing_parent is not None and existing_parent != parent_id:
        msg = (
            f"tree_parent_conflict_dropped:"
            f"child={child_id} existing_parent={existing_parent} new_parent={parent_id}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return

    # Record first valid parent assignment.
    if existing_parent is None:
        child_to_parent[child_id] = parent_id

    # Edge key.
    key = (parent_id, child_id)

    # If edge already exists, merge provenance into the first-emitted edge.
    existing = edges_by_key.get(key)
    if existing is not None:
        existing.source_segment_ids = _stable_extend_unique(
            base=existing.source_segment_ids, extra=[segment_id]
        )
        existing.source_decision_ids = _stable_extend_unique(
            base=existing.source_decision_ids, extra=[decision_id]
        )
        return

    # Deterministic sibling ordering (per parent).
    order = next_order_index[parent_id]
    next_order_index[parent_id] += 1

    edge = CanonicalEdge(
        child_id=child_id,
        order_index=order,
        parent_id=parent_id,
        source_decision_ids=[decision_id],
        source_segment_ids=[segment_id],
    )
    edges.append(edge)
    edges_by_key[key] = edge


def _extract_block_segment_text(segment: BlockSegment) -> str | None:
    """Extract text from a BlockSegment.

    Parameters
    ----------
    segment
        The BlockSegment to extract text from.

    Returns
    -------
    str | None
        The extracted text, or None if not found.
    """

    if segment.combined_text and segment.combined_text.strip():
        return segment.combined_text.strip()

    if isinstance(segment.text, TextUnit) and segment.text.text.strip():
        return segment.text.text.strip()

    if segment.list_items:
        parts: list[str] = []

        for list_item in segment.list_items:
            text_unit = list_item.text

            if text_unit.text.strip():
                parts.append(text_unit.text.strip())

        if parts:
            return "\n".join(parts)

    return None


def _extract_table_headers(segment: Segment) -> list[str]:
    """Best-effort extraction of header cell strings for unresolved table items.

    Parameters
    ----------
    segment
        The Segment to extract headers from.

    Returns
    -------
    list[str]
        The extracted header cell strings.
    """

    if segment.kind != "table":
        return []

    header_rows = getattr(segment, "header_rows", [])

    if not header_rows:
        return []

    # Use the last header row as "most specific".
    last = header_rows[-1]
    output: list[str] = []

    for cell in getattr(last, "cells", []) or []:
        tu = getattr(cell, "text", None)
        if tu and getattr(tu, "text", "").strip():
            output.append(tu.text.strip())

    return output


def _format_page_indices(page_indices: list[int]) -> str:
    """Format page indices as a comma-separated string.

    Parameters
    ----------
    page_indices
        The list of page indices to format.

    Returns
    -------
    str
        The formatted page indices string.
    """

    return "-" if not page_indices else ",".join(str(p) for p in page_indices)


def _format_section_path(*, max_items: int = 6, section_path_text: list[str]) -> str:
    """Compact section path for warnings. Keep the last N headings to avoid huge
    strings.

    Parameters
    ----------
    max_items
        The maximum number of section path items to keep.
    section_path_text
        The full section path text list.

    Returns
    -------
    str
        The compacted section path string.
    """

    if not section_path_text:
        return "-"

    tail = section_path_text[-max_items:]

    return " > ".join(tail)


def _grouping_key(g: GroupingDecision) -> str:
    """Create a stable string for path_fingerprint().

    NB: This key must NOT depend on canonical node_id (circular dependency).

    Parameters
    ----------
    g
        The GroupingDecision to compute the key for.

    Returns
    -------
    str
        The grouping key.
    """

    code = g.local_code or "-"
    title = canonical_grouping_title(role=g.role, title=g.title)

    return f"{g.role.value}:{_normalize_text(text=title)}:{_normalize_text(text=code)}"


def _index_decisions_by_segment(
    *, segment_decisions: SegmentDecisionSet
) -> dict[str, list[SegmentDecision]]:
    """Index all decisions by their segment ID.

    Parameters
    ----------
    segment_decisions
        The SegmentDecisionSet to index.

    Returns
    -------
    dict[str, list[SegmentDecision]]
        The mapping of segment_id to list of SegmentDecisions.
    """

    decisions_by_segment: dict[str, list[SegmentDecision]] = defaultdict(list)

    for d in segment_decisions.decisions:
        assert isinstance(d.segment_id, str) and d.segment_id
        decisions_by_segment[d.segment_id].append(d)

    return decisions_by_segment


def _make_unresolved_sample(
    *, decision: SegmentDecision, max_len: int = 280, segment: Segment
) -> str:
    """Create a short, human-debuggable preview string for UnresolvedItem.sample.

    Parameters
    ----------
    decision
        The SegmentDecision to create a sample for.
    max_len
        The maximum length of the sample string.
    segment
        The Segment to create a sample for.

    Returns
    -------
    str
        The generated sample string.
    """

    parts: list[str] = []
    parts.append(f"type={decision.decision_type.value} conf={decision.confidence:.2f}")

    rationale = getattr(decision, "rationale", None)

    if rationale:
        parts.append(f"rationale={rationale}")

    # Use segment text for blocks when available.
    text_or_none = getattr(segment, "text", None)
    segment_text = text_or_none.text if isinstance(text_or_none, TextUnit) else None

    if segment_text:
        parts.append(segment_text)

    s = " | ".join(parts).strip()

    return s[:max_len]


def _materialize_block_leaves(
    *,
    ancestor_keys: list[str],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    parent_id: str,
    section_path_text: list[str],
    segment_id: str,
    warnings: list[str],
) -> None:
    """Materialize leaf nodes for a block segment.

    Parameters
    ----------
    ancestor_keys
        The list of ancestor grouping keys.
    child_to_parent
        The mapping of child_id to parent_id.
    decision
        The SegmentDecision to materialize.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    parent_id
        The parent node ID.
    section_path_text
        The section path text for the segment.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.
    """

    for leaf in decision.leaves:
        leaf_id = canonical_leaf_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, leaf=leaf
        )

        node = CanonicalNode(
            bbox=None,
            body=TextUnit(language="und", text=leaf.body),
            list_marker=leaf.list_marker,
            local_code=leaf.local_code,
            node_id=leaf_id,
            normalized_text=_normalize_text(text=leaf.body),
            page_indices=page_indices,
            role=leaf.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision.decision_id],
            source_label=leaf.source_label,
            source_segment_ids=[segment_id],
            source_type="block",
            title=None,
        )

        ensure_node(node=node, nodes_by_id=nodes_by_id, warnings=warnings)
        _emit_edge(
            child_id=leaf_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=segment_id,
            warnings=warnings,
        )


def _materialize_decision_structure(
    *,
    active_context_stack: list[ContextFrame],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    root_id: str,
    section_path_text: list[str],
    segment: Segment,
    warnings: list[str],
) -> list[ContextFrame]:
    """Reconcile context stack, create grouping nodes, and materialize leaves/rows.

    Parameters
    ----------
    active_context_stack
        The current active context stack.
    child_to_parent
        The mapping of child_id to parent_id.
    decision
        The SegmentDecision to materialize.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    root_id
        The root node ID.
    section_path_text
        The section path text for the segment.
    segment
        The Segment to materialize.
    warnings
        The list of warnings to append to.

    Returns
    -------
    list[ContextFrame]
        The updated active_context_stack.
    """

    # Context stack reconciliation.
    parent_id, ancestor_keys, active_context_stack = reconcile_context_stack(
        active_stack=active_context_stack,
        child_to_parent=child_to_parent,
        decision=decision,
        desired_context=decision.context_groupings,
        doc_key=doc_key,
        edges_by_key=edges_by_key,
        edges=edges,
        next_order_index=next_order_index,
        nodes_by_id=nodes_by_id,
        root_id=root_id,
        segment=segment,
        warnings=warnings,
    )

    # Apply decision.groupings[] under the context stack tip.
    for g in decision.groupings:
        g_title = canonical_grouping_title(role=g.role, title=g.title)
        node_id = canonical_grouping_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, grouping=g
        )

        node = CanonicalNode(
            bbox=None,
            body=None,
            list_marker=None,
            local_code=g.local_code,
            node_id=node_id,
            normalized_text=_normalize_text(text=g_title),
            page_indices=page_indices,
            role=g.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision.decision_id],
            source_label=g.source_label,
            source_segment_ids=[segment.segment_id],
            source_type=segment.kind,
            title=TextUnit(language="und", text=g_title),
        )

        ensure_node(node=node, nodes_by_id=nodes_by_id, warnings=warnings)
        _emit_edge(
            child_id=node_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=segment.segment_id,
            warnings=warnings,
        )

        parent_id = node_id
        ancestor_keys.append(_grouping_key(g))

    # Dispatch based on segment kind.
    if segment.kind == "block":
        _materialize_block_leaves(
            ancestor_keys=ancestor_keys,
            child_to_parent=child_to_parent,
            decision=decision,
            doc_key=doc_key,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            nodes_by_id=nodes_by_id,
            page_indices=page_indices,
            parent_id=parent_id,
            section_path_text=section_path_text,
            segment_id=segment.segment_id,
            warnings=warnings,
        )
    elif segment.kind == "table":
        _materialize_table_rows(
            ancestor_keys=ancestor_keys,
            child_to_parent=child_to_parent,
            decision=decision,
            doc_key=doc_key,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            nodes_by_id=nodes_by_id,
            page_indices=page_indices,
            parent_id=parent_id,
            section_path_text=section_path_text,
            segment_id=segment.segment_id,
            warnings=warnings,
        )
    else:
        msg = f"unknown_segment_kind:{segment.kind}:{segment.segment_id}"
        logger.warning(msg)
        warnings.append(msg)

    return active_context_stack


def _materialize_table_rows(
    *,
    ancestor_keys: list[str],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    parent_id: str,
    section_path_text: list[str],
    segment_id: str,
    warnings: list[str],
) -> None:
    """Materialize rows and leaves for a table segment.

    Parameters
    ----------
    ancestor_keys
        The list of ancestor grouping keys.
    child_to_parent
        The mapping of child_id to parent_id.
    decision
        The SegmentDecision to materialize.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    parent_id
        The parent node ID.
    section_path_text
        The section path text for the segment.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.
    """

    for row in sorted(decision.rows, key=lambda r: r.row_index):
        row_parent_id = parent_id
        row_ancestor_keys = list(ancestor_keys)

        # Row groupings.
        for g in row.groupings:
            g_title = canonical_grouping_title(role=g.role, title=g.title)
            node_id = canonical_grouping_node_id(
                ancestor_grouping_keys=row_ancestor_keys, doc_key=doc_key, grouping=g
            )

            node = CanonicalNode(
                bbox=None,
                body=None,
                list_marker=None,
                local_code=g.local_code,
                node_id=node_id,
                normalized_text=_normalize_text(text=g_title),
                page_indices=page_indices,
                role=g.role,
                section_path_text=section_path_text,
                source_decision_ids=[decision.decision_id],
                source_label=g.source_label,
                source_segment_ids=[segment_id],
                source_type="table",
                title=TextUnit(language="und", text=g_title),
            )

            ensure_node(node=node, nodes_by_id=nodes_by_id, warnings=warnings)
            _emit_edge(
                child_id=node_id,
                child_to_parent=child_to_parent,
                decision_id=decision.decision_id,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                parent_id=row_parent_id,
                segment_id=segment_id,
                warnings=warnings,
            )

            row_parent_id = node_id
            row_ancestor_keys.append(_grouping_key(g))

        # Row leaves.
        for leaf in row.leaves:
            leaf_id = canonical_leaf_node_id(
                ancestor_grouping_keys=row_ancestor_keys, doc_key=doc_key, leaf=leaf
            )

            node = CanonicalNode(
                bbox=None,
                body=TextUnit(language="und", text=leaf.body),
                list_marker=leaf.list_marker,
                local_code=leaf.local_code,
                node_id=leaf_id,
                normalized_text=_normalize_text(text=leaf.body),
                page_indices=page_indices,
                role=leaf.role,
                section_path_text=section_path_text,
                source_decision_ids=[decision.decision_id],
                source_label=leaf.source_label,
                source_segment_ids=[segment_id],
                source_type="table",
                title=None,
            )

            ensure_node(node=node, nodes_by_id=nodes_by_id, warnings=warnings)
            _emit_edge(
                child_id=leaf_id,
                child_to_parent=child_to_parent,
                decision_id=decision.decision_id,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                parent_id=row_parent_id,
                segment_id=segment_id,
                warnings=warnings,
            )


def _normalize_text(text: Optional[str]) -> str:
    """Deterministic normalization for hashing/comparisons:

    Parameters
    ----------
    text
        The text to normalize.

    Returns
    -------
    str
        The normalized text.
    """

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = _DASH_RE.sub("-", text)

    return _WS_RE.sub(" ", text).strip().casefold()


def _normalized_text_hash(*, encoding: str = "utf-8", text: str) -> str:
    """Compute a stable SHA-256 hash of normalized text.

    Parameters
    ----------
    encoding
        The text encoding to use.
    text
        The text to hash.

    Returns
    -------
    str
        The SHA-256 hash of the normalized text as a hex string.
    """

    norm = _normalize_text(text)

    return hashlib.sha256(norm.encode(encoding)).hexdigest()


def _process_block_segment(
    *,
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Helper to process block segments.

    Parameters
    ----------
    config
        The CreateCanonicalConfig to use.
    context_hint
        The context hint to apply.
    decision_set
        The SegmentDecisionSet to update.
    doc_key
        The document key.
    existing_keys
        The set of existing decision keys.
    segment
        The Segment to process.
    segment_decisions_fp
        The file path to save segment decisions to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    key: tuple[str, int | None, int | None] = (segment.segment_id, None, None)

    if key in existing_keys:
        msg = f"Skipping block segment {segment.segment_id}: decision already exists."
        logger.warning(msg)
        warnings.append(msg)
        return decision_set

    # NB: Never apply caption bindings to block segments.
    segment_payload = segment.model_dump(mode="json")
    segment_payload["prior_context_groupings"] = [dict(x) for x in (context_hint or [])]

    segment_decision = generate_segment_decision(
        always_double_check_first_attempt=config.always_double_check_first_attempt,
        doc_key=doc_key,
        model=config.model,
        segment=segment,
        segment_payload=segment_payload,
    )

    decision_set.decisions.append(segment_decision)
    existing_keys.add(key)

    return save_segment_decision_set(
        decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
    )


def _process_table_segment(
    *,
    caption_bindings: dict[str, CaptionBinding | None],
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Helper to process table segments, handling both chunked and unchunked logic.

    Parameters
    ----------
    caption_bindings
        The mapping of segment_id to CaptionBinding.
    config
        The CreateCanonicalConfig to use.
    context_hint
        The context hint to apply.
    decision_set
        The SegmentDecisionSet to update.
    doc_key
        The document key.
    existing_keys
        The set of existing decision keys.
    segment
        The Segment to process.
    segment_decisions_fp
        The file path to save segment decisions to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    # Caption bindings dict is keyed by TABLE segment_id, and many tables have NO
    # caption -> Use .get().
    binding: CaptionBinding | None = caption_bindings.get(segment.segment_id)

    # Table segments: chunk only if needed. If an unchunked table decision already
    # exists, do NOT mix chunked + unchunked.
    unchunked_key = (segment.segment_id, None, None)

    if unchunked_key in existing_keys:
        msg = f"Skipping table segment {segment.segment_id}: unchunked decision already exists."
        logger.warning(msg)
        warnings.append(msg)
        return decision_set

    # Determine table chunks.
    chunks = table_chunks_for_segment(
        max_body_rows=config.max_table_rows_per_decision, segment=segment
    )

    # Unchunked table == 1 decision.
    if len(chunks) == 1 and chunks[0] == (None, None):
        # Do NOT create an unchunked decision if ANY chunked decisions already exist
        # for this segment (else we would mix chunked + unchunked representations).
        existing_chunked_for_segment = any(
            sid == segment.segment_id and row_start is not None
            for (sid, row_start, _row_end) in existing_keys
        )
        if existing_chunked_for_segment:
            msg = (
                f"Skipping unchunked decision for table segment {segment.segment_id} "
                f"because chunked decisions already exist (avoid mixing chunked + unchunked)."
            )
            logger.warning(msg)
            warnings.append(msg)
            return decision_set

        if unchunked_key not in existing_keys:
            # Apply caption binding and pass the payload even for UNCHUNKED tables so
            # the LLM sees caption_text/caption_kind etc.
            table_payload = make_table_full_payload(segment=segment)
            table_payload = apply_caption_binding_to_table_payload(
                caption_binding=binding, table_payload=table_payload
            )
            table_payload["prior_context_groupings"] = [
                dict(x) for x in (context_hint or [])
            ]

            segment_decision = generate_segment_decision(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                doc_key=doc_key,
                model=config.model,
                segment=segment,
                segment_payload=table_payload,
            )
            segment_decision = attach_caption_binding_to_segment_decision(
                caption_binding=binding, segment_decision=segment_decision
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(unchunked_key)

            return save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )

        return decision_set

    # Chunked table == N decisions.
    #
    # For chunked tables we want a stable "prior_context_groupings" across ALL chunks
    # of the SAME table segment. The best anchor is the context_groupings decided for
    # the FIRST chunk of that table.
    #
    # This avoids drift where chunk 1 gets a rich context (e.g. Learning Area + Subject)
    # but chunk 2+ gets a smaller/different context depending on what segment preceded
    # the table.
    stable_table_prior_context: list[dict[str, Any]] | None = None

    # Ensure deterministic chunk traversal order.
    chunks_sorted = sorted(
        [(s, e) for (s, e) in chunks if s is not None and e is not None],
        key=lambda x: (x[0], x[1]),
    )

    for start, end in chunks_sorted:
        key = (segment.segment_id, start, end)
        table_payload = make_table_chunk_payload(end=end, segment=segment, start=start)

        # Mark whether this is the first chunk (used by validators to enforce context
        # stability).
        table_payload.setdefault("chunking", {})
        table_payload["chunking"]["is_first_chunk"] = start == chunks_sorted[0][0]

        table_payload = apply_caption_binding_to_table_payload(
            caption_binding=binding, table_payload=table_payload
        )

        # NB: For chunked tables, chunk #2+ should see the context that was decided for
        # chunk #1 of the SAME table.
        prior = (
            stable_table_prior_context
            if stable_table_prior_context is not None
            else (context_hint or [])
        )
        table_payload["prior_context_groupings"] = [dict(x) for x in prior]

        segment_decision = generate_segment_decision(
            always_double_check_first_attempt=config.always_double_check_first_attempt,
            doc_key=doc_key,
            model=config.model,
            row_range_end=end,
            row_range_start=start,
            segment=segment,
            segment_payload=table_payload,
        )
        segment_decision = attach_caption_binding_to_segment_decision(
            caption_binding=binding, segment_decision=segment_decision
        )

        # Freeze the table prior context from the FIRST generated chunk decision, but
        # only if it produced a usable outer context.
        if stable_table_prior_context is None:
            usable = segment_decision.decision_type not in (
                "ignore",
                "unresolved",
            ) and bool(segment_decision.context_groupings)
            if usable:
                stable_table_prior_context = _groupings_to_payload_dicts(
                    segment_decision.context_groupings
                )
            else:
                stable_table_prior_context = [dict(x) for x in (context_hint or [])]
                msg = (
                    f"Chunked table first-chunk produced no usable context_groupings; "
                    f"falling back to context_hint for segment_id={segment.segment_id}, "
                    f"row_range_start={start}, row_range_end={end}."
                )
                logger.warning(msg)
                warnings.append(msg)

        decision_set.decisions.append(segment_decision)
        existing_keys.add(key)

        decision_set = save_segment_decision_set(
            decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
        )

    return decision_set


def _stable_extend_unique(*, base: list[T], extra: list[T]) -> list[T]:
    """Deterministic "stable union" for string lists:

    1. Preserve first-seen order
    2. Avoid duplicates

    Parameters
    ----------
    base
        The base list of strings.
    extra
        The extra list of strings to append uniquely.

    Returns
    -------
    list[T]
        The extended list of strings.
    """

    seen = set(base)
    out = list(base)

    for x in extra:
        if x not in seen:
            out.append(x)
            seen.add(x)

    return out


def _validate_and_handle_unresolved(
    *,
    decision: SegmentDecision,
    low_conf_threshold: float,
    page_indices: list[int],
    section_path_text: list[str],
    segment: Segment,
    unresolved: list[UnresolvedItem],
    warnings: list[str],
) -> bool:
    """Validate decision, update unresolved/warnings, return True if materializable.

    Parameters
    ----------
    decision
        The SegmentDecision to validate.
    low_conf_threshold
        The low confidence threshold.
    page_indices
        The list of page indices for the segment.
    section_path_text
        The section path text for the segment.
    segment
        The Segment to validate.
    unresolved
        The list of UnresolvedItems to append to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    bool
        True if the decision is materializable, False otherwise.
    """

    if decision.decision_type == SegmentDecisionType.IGNORE:
        return False

    if decision.decision_type == SegmentDecisionType.UNRESOLVED:
        reason = (
            UnresolvedReason.UNMATCHED_TABLE
            if segment.kind == "table"
            else UnresolvedReason.UNMATCHED_BLOCK
        )
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=(
                    _extract_table_headers(segment) if segment.kind == "table" else []
                ),
                local_code=segment.local_code,
                kind=segment.kind,
                page_indices=page_indices,
                reason=reason,
                sample=None,
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    # Confidence gating.
    if decision.confidence < low_conf_threshold:
        msg = (
            f"low_confidence_decision_not_materialized:"
            f"segment_id={segment.segment_id} decision_id={decision.decision_id} "
            f"kind={segment.kind} conf={decision.confidence:.3f} "
            f"threshold={low_conf_threshold:.3f}"
        )
        logger.warning(msg)
        warnings.append(msg)
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=(
                    _extract_table_headers(segment) if segment.kind == "table" else []
                ),
                kind=segment.kind,
                local_code=getattr(segment, "local_code", None),
                page_indices=page_indices,
                reason=UnresolvedReason.LOW_CONFIDENCE_DECISION_NOT_MATERIALIZED,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    return True


def apply_caption_binding_to_table_payload(
    *, caption_binding: CaptionBinding | None, table_payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply caption binding information to a table segment payload.

    Parameters
    ----------
    caption_binding
        The CaptionBinding to apply, or None to skip.
    table_payload
        The table segment payload to update.

    Returns
    -------
    dict[str, Any]
        The updated table segment payload.
    """

    if not caption_binding:
        return table_payload

    table_payload["caption_gap_segments"] = caption_binding.gap_segments
    table_payload["caption_kind"] = caption_binding.caption_kind
    table_payload["caption_page_index"] = caption_binding.caption_page_index
    table_payload["caption_segment_id"] = caption_binding.caption_segment_id
    table_payload["caption_text"] = caption_binding.caption_text

    return table_payload


def attach_caption_binding_to_segment_decision(
    *, caption_binding: CaptionBinding | None, segment_decision: SegmentDecision
) -> SegmentDecision:
    """Persist caption binding provenance on the SegmentDecision.

    Parameters
    ----------
    caption_binding
        The CaptionBinding to apply, or None to skip.
    segment_decision
        The SegmentDecision to update.

    Returns
    -------
    SegmentDecision
        The updated SegmentDecision.
    """

    if not caption_binding:
        return segment_decision

    segment_decision.caption_gap_segments = caption_binding.gap_segments
    segment_decision.caption_kind = caption_binding.caption_kind
    segment_decision.caption_page_index = caption_binding.caption_page_index
    segment_decision.caption_segment_id = caption_binding.caption_segment_id
    segment_decision.caption_text = caption_binding.caption_text

    return segment_decision


def build_caption_bindings(
    *,
    bind_unknown_caption: bool = True,
    creation_dirs: CanonicalIRDirs,
    document_ir: DocumentIR,
    max_gap_segments: int = 2,
    max_page_distance: int = 1,
) -> dict[str, CaptionBinding]:
    """Bind Caption block to next Table segment (within limits).

    Parameters
    ----------
    bind_unknown_caption
        Whether to bind captions of unknown kind.
    creation_dirs
        The canonical IR creation directories.
    document_ir
        The DocumentIR to process.
    max_gap_segments
        The maximum number of non-table segments allowed between caption and table.
    max_page_distance
        The maximum page distance allowed between caption and table.

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings: dict[str, CaptionBinding] = {}
    warnings: list[str] = []

    # (caption_segment, caption_text, caption_kind, caption_page, caption_index)
    pending_caption: tuple[BlockSegment, str, CaptionKind, int, int] | None = None

    for index, segment in enumerate(document_ir.segments):
        page_index = (
            segment.slices[0].page_index
            if segment.slices
            else segment.segment_provenance[0].page_index
        )
        assert isinstance(page_index, int) and page_index >= 0

        # Explicit caption candidate.
        if segment.kind == "block":
            caption_text = _extract_block_segment_text(segment)

            if segment.block_type == BlockType.CAPTION and caption_text:
                kind = _classify_caption_kind(caption_text)

                # Don't bind figure captions to tables.
                if kind == "figure" or (kind == "unknown" and not bind_unknown_caption):
                    continue

                pending_caption = (
                    segment,
                    caption_text,
                    kind,
                    page_index,
                    index,
                )

                continue

        # Bind to next table if eligible.
        if segment.kind == "table" and pending_caption is not None:
            cap_seg, cap_text, cap_kind, cap_page, cap_index = pending_caption
            gap = max(0, index - cap_index - 1)
            page_dist = abs(page_index - cap_page)

            if gap <= max_gap_segments and page_dist <= max_page_distance:
                caption_bindings[segment.segment_id] = CaptionBinding(
                    caption_kind=cap_kind,
                    caption_page_index=cap_page,
                    caption_segment_id=cap_seg.segment_id,
                    caption_text=cap_text,
                    gap_segments=gap,
                    table_page_index=page_index,
                    table_segment_id=segment.segment_id,
                )
            else:
                msg = (
                    f"Dangling caption dropped: "
                    f"caption={cap_seg.segment_id} gap={gap} page_dist={page_dist}"
                )
                logger.warning(msg)
                warnings.append(msg)

            pending_caption = None

            continue

        # Expire pending caption if too far.
        if pending_caption is not None:
            cap_seg, _cap_text, _cap_kind, _cap_page, cap_index = pending_caption
            gap = max(0, index - cap_index - 1)
            if gap > max_gap_segments:
                msg = (
                    f"Dangling caption dropped: "
                    f"caption={cap_seg.segment_id} gap_exceeded={gap}"
                )
                logger.warning(msg)
                warnings.append(msg)
                pending_caption = None

    if pending_caption is not None:
        cap_seg, *_ = pending_caption
        msg = f"Dangling caption dropped: caption={cap_seg.segment_id} end_of_document"
        logger.warning(msg)
        warnings.append(msg)

    warnings_fp = creation_dirs.root / "caption_binding_warnings.json"
    write_to_json(fp=warnings_fp, json_info={"warnings": warnings})

    return caption_bindings


def build_context_hint_from_decision(d: SegmentDecision) -> list[dict[str, Any]]:
    """Build context hint from SegmentDecision's context_groupings and groupings.

    Parameters
    ----------
    d
        The SegmentDecision to extract context hint from.

    Returns
    -------
    list[dict[str, Any]]
        The context hint as a list of dicts.
    """

    carry_roles = {
        NodeRole.GRADE_LEVEL,
        NodeRole.STAGE,
        NodeRole.LEARNING_AREA,
        NodeRole.STRAND,
        NodeRole.SUBJECT,
        NodeRole.THEME,
        NodeRole.UNIT,
        NodeRole.WEEK,
    }
    hint: list[dict[str, Any]] = []

    for g in d.context_groupings or []:
        if g.role in carry_roles:
            hint.append(g.model_dump(mode="json"))

    for g in d.groupings or []:
        if g.role in carry_roles:
            hint.append(g.model_dump(mode="json"))

    return hint


def canonical_grade_level_title(title: str) -> str:
    """Normalize common grade label variants into a consistent display form.

    Examples:
      - "GRADE 1-3"     -> "GRADES 1–3"
      - "GRADES 1 – 3"  -> "GRADES 1–3"
      - "Grade 2"       -> "GRADE 2"

    NB:

    1. This only fires when patterns match confidently.
    2. Otherwise we return the original string unchanged.
    """

    if not title:
        return title

    # Normalize unicode + whitespace + dash variants for matching.
    t = unicodedata.normalize("NFKC", title).strip()
    t = _WS_RE.sub(" ", t)
    t = _DASH_RE.sub("-", t)  # Unify various dash chars
    t = re.sub(r"\s*-\s*", "-", t)  # Remove spaces around hyphen
    t = _WS_RE.sub(" ", t).strip()

    # Numeric grade range: GRADE(S) 1 - 3,
    m = re.match(r"^(grades?|grade)\s+(\d+)-(\d+)$", t, flags=re.IGNORECASE)
    if m:
        start = int(m.group(2))
        end = int(m.group(3))
        return f"GRADES {start}–{end}"  # en dash

    # Single numeric grade: GRADE(S) 2.
    m = re.match(r"^(grades?|grade)\s+(\d+)$", t, flags=re.IGNORECASE)
    if m:
        n = int(m.group(2))
        return f"GRADE {n}"

    # Otherwise: leave unchanged (avoid accidental over-normalization).
    return title.strip()


def canonical_grouping_node_id(
    *, ancestor_grouping_keys: list[str], doc_key: str, grouping: GroupingDecision
) -> str:
    """Compute the canonical node ID for a grouping decision.

    Parameters
    ----------
    ancestor_grouping_keys
        The list of ancestor grouping keys.
    doc_key
        The document key.
    grouping
        The GroupingDecision to compute the node ID for.

    Returns
    -------
    str
        The computed canonical node ID.
    """

    path_fp = path_fingerprint(grouping_keys=ancestor_grouping_keys)
    code = grouping.local_code or "-"

    if code != "-":
        # Normalize local_code deterministically (whitespace + unicode dash).
        code = unicodedata.normalize("NFKC", code)
        code = _DASH_RE.sub("-", code)
        code = _WS_RE.sub(" ", code).strip()

    title = canonical_grouping_title(role=grouping.role, title=grouping.title)
    text_hash = _normalized_text_hash(text=title)

    key = canonical_key(
        doc_key=doc_key,
        local_code_or_dash=code,
        normalized_text_hash_hex=text_hash,
        path_fp=path_fp,
        role=grouping.role.value,
    )

    return uuidv5_from_key(key)


def canonical_grouping_title(*, role: NodeRole, title: str) -> str:
    """Canonicalize grouping titles in a role-aware way. This is intentionally
    conservative: we only normalize when we can do so deterministically and safely.

    Parameters
    ----------
    role
        The node role.
    title
        The original title.

    Returns
    -------
    str
        The canonicalized title.
    """

    if not title:
        return title

    if role == NodeRole.GRADE_LEVEL:
        return canonical_grade_level_title(title)

    return title.strip()


def canonical_key(
    *,
    doc_key: str,
    local_code_or_dash: str,
    normalized_text_hash_hex: str,
    path_fp: str,
    role: str,
) -> str:
    """Create a canonical key:

    lc:canonical:{doc_key}:{role}:{path_fingerprint}:{local_code_or_-}:{normalized_text_hash}

    Parameters
    ----------
    doc_key
        The document key.
    local_code_or_dash
        The local code or dash.
    normalized_text_hash_hex
        The normalized text hash hex.
    path_fp
        The path fingerprint.
    role
        The node role.

    Returns
    -------
    str
        The canonical key.
    """

    return (
        f"lc:canonical:{doc_key}:"
        f"{role}:"
        f"{path_fp}:"
        f"{local_code_or_dash}:"
        f"{normalized_text_hash_hex}"
    )


def canonical_leaf_node_id(
    *, ancestor_grouping_keys: list[str], doc_key: str, leaf: LeafDecision
) -> str:
    """Compute the canonical node ID for a leaf decision.

    Parameters
    ----------
    ancestor_grouping_keys
        The list of ancestor grouping keys.
    doc_key
        The document key.
    leaf
        The LeafDecision to compute the node ID for.

    Returns
    -------
    str
        The computed canonical node ID.
    """

    path_fp = path_fingerprint(grouping_keys=ancestor_grouping_keys)
    code = leaf.local_code or "-"

    if code != "-":
        # Normalize local_code deterministically (whitespace + unicode dash).
        code = unicodedata.normalize("NFKC", code)
        code = _DASH_RE.sub("-", code)
        code = _WS_RE.sub(" ", code).strip()

    text_hash = _normalized_text_hash(text=leaf.body)

    key = canonical_key(
        doc_key=doc_key,
        local_code_or_dash=code,
        normalized_text_hash_hex=text_hash,
        path_fp=path_fp,
        role=leaf.role.value,
    )

    return uuidv5_from_key(key)


def compile_canonical_ir(
    *,
    doc_key: str,
    document_ir: DocumentIR,
    low_conf_threshold: float = 0.80,
    segment_decisions: SegmentDecisionSet,
    structural_leaf_warn_threshold: float = 0.80,
) -> CanonicalIR:
    """Compile a CanonicalIR from DocumentIR and SegmentDecisionSet.

    The process is as follows:

    1. Iterate DocumentIR.segments in order.
    2. Load all decisions for the segment.
    3. Sort decisions by (row_range_start, row_range_end).
    4. Materialize nodes + edges
    5. Assign order_index by encounter order.

    Parameters
    ----------
    doc_key
        The document key.
    document_ir
        The DocumentIR to process.
    low_conf_threshold
        The low confidence threshold for warnings (this comes from the system prompt
        for the LLM).
    segment_decisions
        The SegmentDecisionSet to apply.
    structural_leaf_warn_threshold
        The confidence threshold below which structural leaves will emit warnings.

    Returns
    -------
    CanonicalIR
        The compiled CanonicalIR.
    """

    # 1. Initialize state containers.
    active_context_stack: list[ContextFrame] = []
    child_to_parent: dict[str, str] = {}
    edges: list[CanonicalEdge] = []
    edges_by_key: dict[tuple[str, str], CanonicalEdge] = {}
    next_order_index: dict[str, int] = defaultdict(int)
    nodes_by_id: dict[str, CanonicalNode] = {}
    unresolved: list[UnresolvedItem] = []
    warnings: list[str] = []

    # 2. Index decisions.
    decisions_by_segment = _index_decisions_by_segment(
        segment_decisions=segment_decisions
    )

    # 3. Create Framework Root.
    framework_title = segment_decisions.pdf_name
    root_id = uuidv5_from_key(f"lc:canonical:{doc_key}:framework")
    framework_node = CanonicalNode(
        bbox=None,
        body=None,
        list_marker=None,
        local_code=None,
        node_id=root_id,
        normalized_text=_normalize_text(text=framework_title),
        page_indices=[],
        role=NodeRole.FRAMEWORK,
        section_path_text=[],
        source_decision_ids=[],
        source_label=None,
        source_segment_ids=[],
        source_type=None,
        title=TextUnit(language="und", text=framework_title),
    )
    ensure_node(node=framework_node, nodes_by_id=nodes_by_id, warnings=warnings)

    # 4. Main traversal loop.
    for segment in document_ir.segments:
        seg_id = segment.segment_id
        seg_decisions = decisions_by_segment.get(seg_id, [])

        # Prepare segment-level data (needed even when unresolved).
        page_indices = sorted({p.page_index for p in segment.segment_provenance})
        section_path_text = [h.text for h in (segment.section_path or [])]

        if not seg_decisions:
            msg = f"no_decision_for_segment:{seg_id}"
            logger.warning(msg)
            warnings.append(msg)
            unresolved.append(
                UnresolvedItem(
                    caption_text=None,
                    headers=(
                        _extract_table_headers(segment)
                        if segment.kind == "table"
                        else []
                    ),
                    kind=segment.kind,
                    local_code=segment.local_code,
                    page_indices=page_indices,
                    reason=(
                        UnresolvedReason.UNMATCHED_TABLE
                        if segment.kind == "table"
                        else UnresolvedReason.UNMATCHED_BLOCK
                    ),
                    sample=(segment.combined_text if segment.kind == "block" else None),
                    section_path_text=section_path_text,
                    segment_id=segment.segment_id,
                )
            )
            continue

        seg_decisions_sorted = sorted(seg_decisions, key=_decision_sort_key)

        # Process each decision for the segment.
        for decision in seg_decisions_sorted:
            # Check ignore/unresolved/low confidence.
            should_continue = _validate_and_handle_unresolved(
                decision=decision,
                low_conf_threshold=low_conf_threshold,
                page_indices=page_indices,
                section_path_text=section_path_text,
                segment=segment,
                unresolved=unresolved,
                warnings=warnings,
            )

            if not should_continue:
                continue

            # Check for structural warnings
            _check_structural_warnings(
                decision=decision,
                page_indices=page_indices,
                section_path_text=section_path_text,
                segment_id=seg_id,
                segment_kind=segment.kind,
                structural_leaf_warn_threshold=structural_leaf_warn_threshold,
                warnings=warnings,
            )

            # Materialize nodes.
            active_context_stack = _materialize_decision_structure(
                active_context_stack=active_context_stack,
                child_to_parent=child_to_parent,
                decision=decision,
                doc_key=doc_key,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                nodes_by_id=nodes_by_id,
                page_indices=page_indices,
                root_id=root_id,
                section_path_text=section_path_text,
                segment=segment,
                warnings=warnings,
            )

    # 5. Final compilation.
    canonical_ir = CanonicalIR(
        decision_set_id=segment_decisions.decision_set_id,
        doc_key=doc_key,
        edges=edges,
        nodes=list(nodes_by_id.values()),
        pdf_name=segment_decisions.pdf_name,
        root_id=root_id,
        segment_decisions=segment_decisions.decisions,
        unresolved=unresolved,
        warnings=warnings,
    )
    canonical_ir = perform_postpass_hygiene(canonical_ir)

    logger.info(
        f"Compiled CanonicalIR:\n"
        f"nodes={len(canonical_ir.nodes)}\n"
        f"edges={len(canonical_ir.edges)}\n"
        f"unresolved={len(canonical_ir.unresolved)}\n"
        f"warnings={len(canonical_ir.warnings)}"
    )

    return canonical_ir


def create_canonical_ir_dirs(*, output_dir: Path) -> CanonicalIRDirs:
    """Create canonical IR directories for a given creation run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    CanonicalDocumentIRDirs
        The created canonical document IR directories.
    """

    root = output_dir
    canonical_ir = root / "canonical_ir"
    segment_decisions = root / "segment_decisions"

    for p in [root, canonical_ir, segment_decisions]:
        make_dir(p)

    return CanonicalIRDirs(
        root=root, canonical_ir=canonical_ir, segment_decisions=segment_decisions
    )


def decision_key(
    segment_decision: SegmentDecision,
) -> tuple[str, int | None, int | None]:
    """Compute a unique key for a SegmentDecision based on segment_id and row range.

    Parameters
    ----------
    segment_decision
        The SegmentDecision to compute the key for.

    Returns
    -------
    tuple[str, int | None, int | None]
        The unique key as (segment_id, row_range_start, row_range_end).
    """

    return (
        segment_decision.segment_id or "",
        segment_decision.row_range_start,
        segment_decision.row_range_end,
    )


def dedupe_edges_postpass(
    *, edges: list[CanonicalEdge], node_ids: set[str], warnings: list[str]
) -> list[CanonicalEdge]:
    """Dedupe edges in a post-pass after all edges have been emitted:

    1. Drop dangling edges (missing nodes)
    2. Dedupe edges by (parent_id, child_id, rel)
    3. Merge provenance lists deterministically
    4. keep first order_index encountered

    Parameters
    ----------
    edges
        The list of CanonicalEdges to dedupe.
    node_ids
        The set of valid node IDs.
    warnings
        The list of warnings to append to.

    Returns
    -------
    list[CanonicalEdge]
        The deduped list of CanonicalEdges.
    """

    merged: dict[tuple[str, str, str], CanonicalEdge] = {}

    for e in edges:
        # Drop edges referencing missing nodes.
        if e.parent_id not in node_ids or e.child_id not in node_ids:
            msg = f"dangling_edge_dropped:parent={e.parent_id} child={e.child_id}"
            logger.warning(msg)
            warnings.append(msg)
            continue

        key = (e.parent_id, e.child_id, e.rel)

        if key not in merged:
            merged[key] = e
            continue

        m = merged[key]

        # order_index should be stable; keep first and warn if mismatch
        if m.order_index != e.order_index:
            msg = (
                f"edge_order_index_conflict_kept_first:"
                f"parent={e.parent_id} child={e.child_id} kept={m.order_index} dropped={e.order_index}"
            )
            logger.warning(msg)
            warnings.append(msg)

        m.source_segment_ids = _stable_extend_unique(
            base=m.source_segment_ids, extra=e.source_segment_ids
        )
        m.source_decision_ids = _stable_extend_unique(
            base=m.source_decision_ids, extra=e.source_decision_ids
        )

    # Preserve deterministic order: first-seen edge key order.
    return list(merged.values())


def ensure_node(
    *, node: CanonicalNode, nodes_by_id: dict[str, CanonicalNode], warnings: list[str]
) -> None:
    """Ensure a CanonicalNode is present in nodes_by_id, merging provenance if needed.

    Merge policy:

    1. Preserve first-seen ordering for all provenance lists.
    2. Merge: page_indices, source_segment_ids, source_decision_ids, section_path_text.
    3. Keep-first for core semantic fields; fill if missing.

    Parameters
    ----------
    node
        The CanonicalNode to ensure.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    warnings
        The list of warnings to append to.
    """

    if node.node_id not in nodes_by_id:
        nodes_by_id[node.node_id] = node
        return

    existing = nodes_by_id[node.node_id]

    # Role mismatch is always a conflict.
    if existing.role != node.role:
        msg = (
            f"node_semantic_conflict_same_id_different_role:"
            f"node_id={node.node_id} existing_role={existing.role} new_role={node.role} "
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    # Grouping title mismatch.
    existing_title = existing.title.text if existing.title is not None else None
    new_title = node.title.text if node.title is not None else None
    if existing_title and new_title and existing_title.strip() != new_title.strip():
        msg = (
            f"node_semantic_conflict_same_id_different_title:"
            f"node_id={node.node_id} existing_title={existing_title!r} new_title={new_title!r} "
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    # Leaf/statement body mismatch.
    existing_body = existing.body.text if existing.body is not None else None
    new_body = node.body.text if node.body is not None else None
    if existing_body and new_body and existing_body.strip() != new_body.strip():
        msg = (
            f"node_semantic_conflict_same_id_different_body:"
            f"node_id={node.node_id} existing_body={existing_body!r} new_body={new_body!r} "
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    # Stable provenance merge (preserve first-seen order).
    existing.page_indices = _stable_extend_unique(
        base=existing.page_indices, extra=node.page_indices
    )
    existing.source_segment_ids = _stable_extend_unique(
        base=existing.source_segment_ids, extra=node.source_segment_ids
    )
    existing.source_decision_ids = _stable_extend_unique(
        base=existing.source_decision_ids, extra=node.source_decision_ids
    )
    existing.section_path_text = _stable_extend_unique(
        base=existing.section_path_text, extra=node.section_path_text
    )

    # Keep-first semantics, fill missing values if present.
    if existing.normalized_text is None and node.normalized_text is not None:
        existing.normalized_text = node.normalized_text

    if existing.source_label is None and node.source_label is not None:
        existing.source_label = node.source_label

    if existing.source_type is None and node.source_type is not None:
        existing.source_type = node.source_type

    # Preserve bbox if missing (do NOT attempt union without a known BBox shape).
    if existing.bbox is None and node.bbox is not None:
        existing.bbox = node.bbox


def load_segment_decision_set(
    *, expected_doc_key: str, pdf_name: str, segment_decisions_fp: Path
) -> SegmentDecisionSet:
    """Load SegmentDecisionSet JSON and normalize formats.

    Parameters
    ----------
    expected_doc_key
        The expected document key for the SegmentDecisionSet.
    pdf_name
        The expected PDF name for the SegmentDecisionSet.
    segment_decisions_fp
        The file path to the SegmentDecisionSet JSON.

    Returns
    -------
    SegmentDecisionSet
        The loaded SegmentDecisionSet.

    Raises
    ------
    ValueError
        If the SegmentDecisionSet.doc_key does not match expected_doc_key, or if the
        SegmentDecisionSet.pdf_name does not match pdf_name.
    """

    decision_set = SegmentDecisionSet.model_validate(
        open_json_type(segment_decisions_fp)
    )

    if decision_set.doc_key != expected_doc_key:
        raise ValueError(
            f"SegmentDecisionSet.doc_key mismatch.\n"
            f"  Expected: {expected_doc_key}\n"
            f"  Got:      {decision_set.doc_key}\n"
            f"  File:     {segment_decisions_fp}"
        )

    if decision_set.pdf_name != pdf_name:
        raise ValueError(
            f"SegmentDecisionSet.pdf_name mismatch.\n"
            f"  DocumentIR: {pdf_name}\n"
            f"  Decisions:  {decision_set.pdf_name}"
        )

    return decision_set


def make_table_chunk_payload(
    *,
    context_rows_after: int = 2,
    context_rows_before: int = 2,
    end: int,
    segment: TableSegment,
    start: int,
) -> dict[str, Any]:
    """Build a table chunk payload for the LLM as follows:

    1. Keep table metadata + headers
    2. Replace `rows` with ONLY the rows in [start,end)
    3. Adds abs_row_index to each provided row
    4. Adds a `chunking` object so prompts can instruct absolute indexing
    5. Adds `context_rows_before` containing up to N rows immediately preceding `start`
       (context-only; the LLM MUST NOT emit RowDecision for these rows).
    6. Adds `context_rows_after` containing up to M rows immediately following `end`
       (context-only; the LLM MUST NOT emit RowDecision for these rows).
    7. If `rows_filldown` exists in the segment, uses it to produce a fill-down view of
       ONLY the decision rows. The filled rows become the main `rows` payload, and the
       raw visual decision rows are preserved under `rows_original`.

    Parameters
    ----------
    context_rows_after
        The number of context rows to include after the chunk end.
    context_rows_before
        The number of context rows to include before the chunk start.
    end
        The exclusive end row index for the chunk.
    segment
        The TableSegment to chunk.
    start
        The inclusive start row index for the chunk.

    Returns
    -------
    dict[str, Any]
        The table chunk payload.
    """

    seg = segment.model_dump(mode="json")

    # NB: Chunk payload should not include full-table derived views that can leak
    # information outside the chunk. NB: We intentionally KEEP rows_filldown here (if
    # present) but slice it down to the decision-row window so it does not expose the
    # entire table.
    for k in ("rows_grid", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    full_rows_raw = seg.get("rows") or []
    full_rows_filldown: list[dict[str, Any]] | None = seg.get("rows_filldown")

    # Context windows (before/after).
    ctx_before_start = max(0, start - max(0, int(context_rows_before or 0)))
    ctx_after_end = min(len(full_rows_raw), end + max(0, int(context_rows_after or 0)))

    context_rows_before_payload: list[dict[str, Any]] = []
    context_rows_after_payload: list[dict[str, Any]] = []

    for abs_i in range(ctx_before_start, start):
        row = dict(full_rows_raw[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_before_payload.append(row)

    for abs_i in range(end, ctx_after_end):
        row = dict(full_rows_raw[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_after_payload.append(row)

    # Decision rows: raw visual + optional fill-down view.
    decision_rows_raw: list[dict[str, Any]] = []
    decision_rows_payload: list[dict[str, Any]] = []

    # Prefer fill-down view (if available) for primary `rows`, because validators
    # ground row-local groupings against visible row text.
    use_filldown = (full_rows_filldown is not None) and len(full_rows_filldown) == len(
        full_rows_raw
    )

    for abs_i in range(start, end):
        raw_row = dict(full_rows_raw[abs_i])
        raw_row["abs_row_index"] = abs_i
        raw_row["is_context_only"] = False
        decision_rows_raw.append(raw_row)

        if use_filldown:
            assert full_rows_filldown is not None
            fd_row = dict(full_rows_filldown[abs_i])
            fd_row["abs_row_index"] = abs_i
            fd_row["is_context_only"] = False
            decision_rows_payload.append(fd_row)
        else:
            decision_rows_payload.append(raw_row)

    # Primary decision rows (potentially fill-down adjusted)/
    seg["rows"] = decision_rows_payload

    # Preserve raw visual decision rows for audit/debug.
    seg["rows_original"] = decision_rows_raw

    # Preserve context rows separately (raw visual).
    seg["context_rows_before"] = context_rows_before_payload
    seg["context_rows_after"] = context_rows_after_payload

    # Keep ONLY the decision-row slice of rows_filldown for explicitness/debugging.
    if use_filldown:
        seg["rows_filldown"] = [dict(r) for r in decision_rows_payload]
    else:
        seg.pop("rows_filldown", None)

    seg["chunking"] = {
        "row_range_start": start,
        "row_range_end": end,
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
        "context_rows_before_start": ctx_before_start,
        "context_rows_before_end": start,
        "context_rows_before_count": len(context_rows_before_payload),
        "context_rows_after_start": end,
        "context_rows_after_end": ctx_after_end,
        "context_rows_after_count": len(context_rows_after_payload),
        "rows_are_filldown_view": use_filldown,
        "rows_original_preserved": True,
    }

    return seg


def make_table_full_payload(*, segment: TableSegment) -> dict[str, Any]:
    """Build a FULL (unchunked) table payload for the LLM.

    This mirrors `make_table_chunk_payload` but includes ALL rows. Critically, it:

    1. Prefers `rows_filldown` (if available) so row-level groupings are grounded
        in-row (raw visual rows are preserved under `rows_original`).
    2. Adds `abs_row_index` to every row so validators can enforce grounding
    3. Adds a lightweight `chunking` object indicating absolute indices

    Parameters
    ----------
    segment
        The TableSegment to process.

    Returns
    -------
    dict[str, Any]
        The full table payload.
    """

    seg = segment.model_dump(mode="json")

    # Prefer fill-down view if it exists.
    rows_raw = seg.get("rows") or []
    rows_filldown = seg.get("rows_filldown")

    use_filldown = (
        isinstance(rows_filldown, list)
        and len(rows_filldown) == len(rows_raw)
        and len(rows_raw) > 0
    )

    if use_filldown:
        seg["rows_original"] = rows_raw
        seg["rows"] = rows_filldown  # Store rows_filldown here before removing
        seg["rows_original_preserved"] = True
    else:
        seg["rows_original_preserved"] = False

    # NB: Remove derived structures that bloat the prompt. We intentionally keep the
    # filldown effect by swapping seg["rows"] above.
    for k in ("rows_grid", "rows_filldown", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    rows = seg.get("rows") or []

    # Add abs_row_index to every row (headers included).
    for abs_i, row in enumerate(rows):
        if isinstance(row, dict):
            row["abs_row_index"] = abs_i

    seg["rows"] = rows
    seg["chunking"] = {
        "row_range_start": 0,
        "row_range_end": len(rows),
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
    }

    return seg


def merge_nodes_postpass(
    *, nodes: list[CanonicalNode], warnings: list[str]
) -> list[CanonicalNode]:
    """Merge CanonicalNodes with the same node_id, combining provenance.

    Parameters
    ----------
    nodes
        The list of CanonicalNodes to merge.
    warnings
        A list to append warning messages to.

    Returns
    -------
    list[CanonicalNode]
        The merged list of CanonicalNodes.
    """

    merged: dict[str, CanonicalNode] = {}

    for n in nodes:
        if n.node_id not in merged:
            merged[n.node_id] = n

            continue

        m = merged[n.node_id]

        # Sanity check: role + title/body should be consistent.
        if m.role != n.role:
            msg = f"node_merge_role_conflict:{n.node_id} kept={m.role} dropped={n.role}"
            logger.warning(msg)
            warnings.append(msg)

        if (m.title is None) != (n.title is None) or (m.body is None) != (
            n.body is None
        ):
            msg = f"node_merge_title_body_shape_conflict:{n.node_id}"
            logger.warning(msg)
            warnings.append(msg)

        # Merge provenance deterministically.
        m.page_indices = _stable_extend_unique(
            base=m.page_indices, extra=n.page_indices
        )
        m.source_segment_ids = _stable_extend_unique(
            base=m.source_segment_ids, extra=n.source_segment_ids
        )
        m.source_decision_ids = _stable_extend_unique(
            base=m.source_decision_ids, extra=n.source_decision_ids
        )
        m.section_path_text = _stable_extend_unique(
            base=m.section_path_text, extra=n.section_path_text
        )

        # Merge optional fields conservatively: keep first non-null.
        for field in (
            "normalized_text",
            "source_label",
            "source_type",
            "list_marker",
            "local_code",
            "bbox",
        ):
            if getattr(m, field) is None and getattr(n, field) is not None:
                setattr(m, field, getattr(n, field))

        if m.bbox is None and n.bbox is not None:
            m.bbox = n.bbox

    # Preserve deterministic order: first-seen node_id order.
    return list(merged.values())


def path_fingerprint(*, encoding: str = "utf-8", grouping_keys: Iterable[str]) -> str:
    """Create a short stable fingerprint of the ancestor grouping key sequence.

    Parameters
    ----------
    encoding
        The string encoding to use.
    grouping_keys
        The sequence of ancestor grouping keys.

    Returns
    -------
    str
        The computed path fingerprint.
    """

    joined = ">".join(grouping_keys)

    if not joined:
        return "-"

    return hashlib.sha256(joined.encode(encoding)).hexdigest()[:16]


def perform_postpass_hygiene(canonical_ir: CanonicalIR) -> CanonicalIR:
    """Perform post-pass hygiene on a CanonicalIR:

    1. Node merge
    2. Edge dedupe
    3. Sanity checks

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to process.

    Returns
    -------
    CanonicalIR
        An updated CanonicalIR.
    """

    warnings = list(canonical_ir.warnings)

    nodes_merged = merge_nodes_postpass(nodes=canonical_ir.nodes, warnings=warnings)
    node_ids = {n.node_id for n in nodes_merged}

    edges_merged = dedupe_edges_postpass(
        edges=canonical_ir.edges, node_ids=node_ids, warnings=warnings
    )

    sanity_checks_postpass(
        edges=edges_merged,
        nodes=nodes_merged,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    return canonical_ir.model_copy(
        update={"nodes": nodes_merged, "edges": edges_merged, "warnings": warnings}
    )


def persist_canonical_run(
    *, config: CreateCanonicalConfig, output_dir: Path
) -> tuple[CanonicalIRDirs, RunCtx]:
    """Persist canonical IR creation run metadata.

    Parameters
    ----------
    config
        The canonical IR creation run configuration.
    output_dir
        The output directory for the canonical IR creation run results.

    Returns
    -------
    tuple[CanonicalIRDirs, RunCtx]
        The created canonical IR directories and persisted canonical IR creation run
        metadata.
    """

    creation_dirs = create_canonical_ir_dirs(output_dir=output_dir)
    creation_run = RunCtx(
        extra={},
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "creation_run.json", json_info=creation_run)
    logger.info(f"Saving canonical IR creation results to: {creation_dirs}")

    return creation_dirs, creation_run


def process_segment_decisions(
    *,
    caption_bindings: dict[str, CaptionBinding | None],
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None = None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Process a single segment to generate and persist decisions.

    Parameters
    ----------
    caption_bindings
        The caption bindings to apply to table segments.
    config
        The canonical IR creation run configuration.
    context_hint
        The context hint to include in the segment decision payload.
    decision_set
        The current SegmentDecisionSet to update.
    doc_key
        The expected document key for all page IRs.
    existing_keys
        The set of existing decision keys to avoid duplicates.
    segment
        The Segment to process.
    segment_decisions_fp
        The output file path for the SegmentDecisionSet JSON.
    warnings
        A list to append warning messages to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    assert segment.kind in (
        "block",
        "table",
    ), f"Unexpected segment kind: {segment.kind}"

    if segment.kind == "block":
        return _process_block_segment(
            config=config,
            context_hint=context_hint,
            decision_set=decision_set,
            doc_key=doc_key,
            existing_keys=existing_keys,
            segment=segment,
            segment_decisions_fp=segment_decisions_fp,
            warnings=warnings,
        )

    return _process_table_segment(
        caption_bindings=caption_bindings,
        config=config,
        context_hint=context_hint,
        decision_set=decision_set,
        doc_key=doc_key,
        existing_keys=existing_keys,
        segment=segment,
        segment_decisions_fp=segment_decisions_fp,
        warnings=warnings,
    )


def reconcile_context_stack(
    *,
    active_stack: list[ContextFrame],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    desired_context: list[GroupingDecision],
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    root_id: str,
    segment: Segment,
    warnings: list[str],
) -> tuple[str, list[str], list[ContextFrame]]:
    """Reconcile the current active context stack to match the desired context by
    enforcing context snapshot exactly per decision and reusing nodes via deterministic
    IDs.

    Parameters
    ----------
    active_stack
        The current active context stack.
    child_to_parent
        The mapping of child_id to parent_id for emitted edges.
    decision
        The SegmentDecision being processed.
    desired_context
        The desired context snapshot from the SegmentDecision.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append new edges to.
    edges_by_key
        The mapping of (parent_id, child_id) to CanonicalEdge for deduplication.
    next_order_index
        The mapping of parent_id to next order index for child edges.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    root_id
        The root node ID.
    segment
        The Segment being processed.
    warnings
        The list of warning messages to append to.

    Returns
    -------
    tuple[str, list[str], list[ContextFrame]]
        The (parent_id, ancestor_keys, new_stack) after reconciliation.
    """

    desired_keys = [_grouping_key(g) for g in desired_context]
    page_indices = sorted({p.page_index for p in segment.segment_provenance})
    section_path_text = [h.text for h in (segment.section_path or [])]
    seg_id = segment.segment_id
    seg_kind = segment.kind

    # 1. Longest common prefix between active_stack keys and desired_keys.
    lcp = 0
    while lcp < len(active_stack) and lcp < len(desired_keys):
        if active_stack[lcp].grouping_key != desired_keys[lcp]:
            break

        lcp += 1

    # 2. Pop extra frames (enforce snapshot exactly).
    new_stack = active_stack[:lcp]

    # Determine current parent and ancestor keys after pop.
    if not new_stack:
        ancestor_keys: list[str] = []
        parent_id = root_id
    else:
        ancestor_keys = [f.grouping_key for f in new_stack]
        parent_id = new_stack[-1].node_id

    # 3. Push missing frames.
    for g in desired_context[lcp:]:
        node_id = canonical_grouping_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, grouping=g
        )
        g_title = canonical_grouping_title(role=g.role, title=g.title)

        node = CanonicalNode(
            bbox=None,
            body=None,
            list_marker=None,
            local_code=g.local_code,
            node_id=node_id,
            normalized_text=_normalize_text(text=g_title),
            page_indices=page_indices,
            role=g.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision.decision_id],
            source_label=g.source_label,
            source_segment_ids=[seg_id],
            source_type=seg_kind,
            title=TextUnit(language="und", text=g_title),
        )

        ensure_node(node=node, nodes_by_id=nodes_by_id, warnings=warnings)
        _emit_edge(
            child_id=node_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=seg_id,
            warnings=warnings,
        )

        # Advance stack.
        gk = _grouping_key(g)
        new_stack.append(ContextFrame(grouping_key=gk, node_id=node_id))
        parent_id = node_id
        ancestor_keys.append(gk)

    # After reconciliation, new_stack should EXACTLY matches desired_context snapshot.
    return parent_id, ancestor_keys, new_stack


def sanity_checks_postpass(
    *,
    edges: list[CanonicalEdge],
    nodes: list[CanonicalNode],
    root_id: str,
    warnings: list[str],
) -> None:
    """Perform sanity checks on the canonical IR structure.

    Parameters
    ----------
    edges
        The list of CanonicalEdges.
    nodes
        The list of CanonicalNodes.
    root_id
        The root node ID.
    warnings
        The list of warning messages to append to.

    Raises
    ------
    ValueError
        If the root node is missing.
    """

    node_ids = {n.node_id for n in nodes}

    # Root existence is catastrophic if missing.
    if root_id not in node_ids:
        raise ValueError(f"canonical_root_missing:{root_id}")

    _check_root_as_child(edges=edges, root_id=root_id, warnings=warnings)
    child_to_parent = _check_multiple_parents(edges=edges, warnings=warnings)
    _check_cycles(child_to_parent=child_to_parent, warnings=warnings)
    _check_order_indices(edges=edges, warnings=warnings)


def save_canonical_ir(*, canonical_ir: CanonicalIR, canonical_ir_fp: Path) -> None:
    """Export the canonical IR to a JSON file.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to serialize.
    canonical_ir_fp
        The output file path for the CanonicalIR JSON.
    """

    write_to_json(fp=canonical_ir_fp, json_info=canonical_ir)
    logger.success(f"Saved canonical IR to: {canonical_ir_fp}")

    # Write structural leaf warnings sidecar file.
    structural_leaf_warnings = [
        w for w in canonical_ir.warnings if w.startswith("structural_leaf_review:")
    ]
    structural_leaf_warnings_fp = canonical_ir_fp.with_name(
        canonical_ir_fp.stem + ".structural_leaf_warnings.json"
    )
    write_to_json(
        fp=structural_leaf_warnings_fp,
        json_info={
            "doc_key": canonical_ir.doc_key,
            "decision_set_id": canonical_ir.decision_set_id,
            "threshold": 0.80,  # Fixed for now to align with LLM prompt
            "count": len(structural_leaf_warnings),
            "warnings": structural_leaf_warnings,
        },
    )
    logger.success(f"Saved structural leaf warnings to: {structural_leaf_warnings_fp}")


def save_segment_decision_set(
    *, decision_set: SegmentDecisionSet, segment_decisions_fp: Path
) -> SegmentDecisionSet:
    """Write a SegmentDecisionSet with an updated stable decision_set_id.

    Parameters
    ----------
    decision_set
        The SegmentDecisionSet to serialize.
    segment_decisions_fp
        The output file path for the SegmentDecisionSet JSON.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet with recomputed decision_set_id.
    """

    # Recompute stable ID every write and keep the in-memory object consistent.
    new_id = compute_decision_set_id(decisions=decision_set.decisions)
    decision_set.decision_set_id = new_id

    write_to_json(fp=segment_decisions_fp, json_info=decision_set)

    return decision_set


def table_chunks_for_segment(
    *, max_body_rows: int | None, segment: TableSegment
) -> list[tuple[int | None, int | None]]:
    """Compute table row chunks for a TableSegment based on max_body_rows.

    Parameters
    ----------
    max_body_rows
        The maximum number of body rows per chunk. If None or <= 0, no chunk splitting
        is performed.
    segment
        The TableSegment to compute chunks for.

    Returns
    -------
    list[tuple[int | None, int | None]]
        A list of (start, end) row index tuples for each chunk. If no chunk splitting
        is needed, returns [(None, None)].
    """

    if not max_body_rows or max_body_rows <= 0:
        return [(None, None)]

    header_n = segment.header_row_count or 0
    total_rows = len(segment.rows)
    body_rows = max(0, total_rows - header_n)

    if body_rows <= max_body_rows:
        return [(None, None)]

    chunks = []
    start = header_n  # Chunk only body rows (skip headers)

    while start < total_rows:
        end = min(total_rows, start + max_body_rows)
        chunks.append((start, end))
        start = end

    return chunks


def uuidv5_from_key(key: str) -> str:
    """Create a deterministic UUIDv5 from a string key.

    Parameters
    ----------
    key
        The input string key.

    Returns
    -------
    str
        The resulting UUIDv5 as a string.
    """

    return str(uuid.uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, key))
