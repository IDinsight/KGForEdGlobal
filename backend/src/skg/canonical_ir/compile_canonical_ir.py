"""This module contains utility functions for compiling the canonical Intermediate
Representation.
"""

# Standard Library
import hashlib
import json
import re
import unicodedata
import uuid

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, TypeVar

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    CanonicalEdge,
    CanonicalIR,
    CanonicalNode,
    GroupingDecision,
    LeafDecision,
    RowDecision,
    SegmentDecision,
    SegmentDecisionSet,
    UnresolvedItem,
)
from skg.canonical_ir.utils import extract_block_segment_text
from skg.config import Settings
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.page_ir_extraction.schemas import TextUnit
from skg.regexes import DASH_RE, WS_RE
from skg.schemas import BBox
from skg.utils.constants import NodeRole, SegmentDecisionType, UnresolvedReason
from skg.utils.general import QUOTES_TRANSLATION, write_to_json

T = TypeVar("T")


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

    # Audit-only warning: segment.section_path suggests strong curriculum structure,
    # but we were provided an empty `context_groupings` snapshot. This does NOT
    # materialize any hierarchy; it only flags likely missed context.
    leaf_count = len(decision.leaves) + sum(len(row.leaves) for row in decision.rows)

    # Checks for the scenario: "Did we emit real curriculum leaves, but without any
    # context about where they belong in the hierarchy, even though the document's own
    # heading trail suggests there IS structural context available?". If the
    # if-statement triggers, then this means we are emitting orphaned leaves that will
    # land directly under whatever the current context stack tip is (possibly the
    # framework root) but the document's own headings suggest this content sits inside
    # some other structure. In other words, the curriculum skeleton probably missed
    # assigning `context_groupings` for this match.
    if (
        decision.decision_type
        not in (SegmentDecisionType.IGNORE, SegmentDecisionType.UNRESOLVED)
        and leaf_count > 0
        and not decision.context_groupings
        and section_path_text
    ):
        path_str = " / ".join([p for p in section_path_text if p])

        if path_str:
            pages_str = ",".join(str(p) for p in page_indices) if page_indices else "-"
            msg = (
                f"context_evidence_present_but_context_groupings_empty:"
                f"segment_id={segment_id} decision_id={decision.decision_id} "
                f"kind={segment_kind} pages={pages_str} section_path={path_str!r}"
            )
            logger.warning(msg)
            warnings.append(msg)

    if decision.confidence >= structural_leaf_warn_threshold:
        return

    if leaf_count == 0:
        return

    pages_str = "-" if not page_indices else ",".join(str(p) for p in page_indices)
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
        f"segment_id={segment_id} "
        f"decision_id={decision.decision_id} "
        f"kind={segment_kind} "
        f"conf={decision.confidence:.3f} "
        f"leaf_count={leaf_count} "
        f"threshold={structural_leaf_warn_threshold:.3f} "
        f"row_range={row_range_str} "
        f"pages={pages_str} "
        f"section_path={path_str}"
    )
    logger.warning(msg)
    warnings.append(msg)


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
    decision_id = d.decision_id
    assert decision_id, (
        f"SegmentDecision must have a decision_id before sorting. "
        f"Found empty decision_id for segment_id={d.segment_id!r} "
        f"row_range=({d.row_range_start},{d.row_range_end}) "
        f"decision_type={d.decision_type}"
    )

    return start, end, decision_id


def _detect_semantic_collision(
    *, existing_node: CanonicalNode, node: CanonicalNode, warnings: list[str]
) -> bool:
    """Check for semantic conflicts between an existing node and a new node.

    NB:

    1. Node IDs are derived from *normalized* semantics (role + normalized title/body
        + ancestor fingerprint + optional local_code). Therefore, we only treat this as
        a true collision when the normalized semantics differ.
    2. Formatting-only differences (casing, punctuation, whitespace) should *not*
        trigger collision handling because that creates duplicate canonical nodes.

    Examples
    --------
    1. Without normalization, these would be treated as collisions (causing duplicates):
        - "Recognize letters." vs "Recognize letters"
        - "Add and subtract" vs "add and subtract"
        - Bullet formatting differences
        - Small punctuation/whitespace changes
    2. With normalization, only true semantic differences trigger collision handling:
        - Normalized meaning differs (casefold + whitespace + dash normalize differs)

    Parameters
    ----------
    existing_node
        The existing CanonicalNode.
    node
        The new CanonicalNode.
    warnings
        The list of warnings to append to.

    Returns
    -------
    bool
        True if a collision is detected (and appends details to warnings), otherwise
        False.
    """

    collision = False

    # Role mismatch should never happen if node IDs are derived correctly, but we keep
    # it as a hard collision to avoid merging incompatible nodes.
    if existing_node.role != node.role:
        msg = (
            f"node_semantic_conflict_same_id_different_role:"
            f"node_id={node.node_id} existing_role={existing_node.role} new_role={node.role} "
            f"existing_src_segments={existing_node.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return True

    # NB: Text here should be normalized already before this function is called!
    existing_norm = existing_node.normalized_text or ""
    new_norm = node.normalized_text or ""

    # True semantic mismatch (same deterministic ID but different normalized meaning).
    if existing_norm and new_norm and existing_norm != new_norm:
        msg = (
            f"node_semantic_conflict_same_id_different_normalized_text:"
            f"node_id={node.node_id} existing_norm={existing_norm!r} new_norm={new_norm!r} "
            f"existing_src_segments={existing_node.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)
        collision = True

    # Formatting-only mismatches: warn, but do NOT mark as collision.
    existing_title = (
        existing_node.title.text if existing_node.title is not None else None
    )
    new_title = node.title.text if node.title is not None else None

    if (
        existing_title
        and new_title
        and existing_title.strip() != new_title.strip()
        and existing_norm
        and new_norm
        and existing_norm == new_norm
    ):
        msg = (
            f"node_formatting_diff_same_id_title_semantics_equal:"
            f"node_id={node.node_id} existing_title={existing_title!r} new_title={new_title!r} "
            f"existing_src_segments={existing_node.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    existing_body = existing_node.body.text if existing_node.body is not None else None
    new_body = node.body.text if node.body is not None else None

    if (
        existing_body
        and new_body
        and existing_body.strip() != new_body.strip()
        and existing_norm
        and new_norm
        and existing_norm == new_norm
    ):
        msg = (
            f"node_formatting_diff_same_id_body_semantics_equal:"
            f"node_id={node.node_id} existing_body={existing_body!r} new_body={new_body!r} "
            f"existing_src_segments={existing_node.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    # Local code mismatch is treated as provenance/metadata drift, not a true collision.
    if (
        existing_node.local_code
        and node.local_code
        and existing_node.local_code.strip() != node.local_code.strip()
    ):
        msg = (
            f"node_local_code_diff_same_id_semantics_equal:"
            f"node_id={node.node_id} existing_local_code={existing_node.local_code!r} "
            f"new_local_code={node.local_code!r} "
            f"existing_src_segments={existing_node.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    return collision


def _emit_edge(
    *,
    child_id: str,
    child_to_parent: dict[str, str],
    decision_id: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    parent_id: str,
    segment_id: str,
    warnings: list[str],
) -> None:
    """Emit edge and assign order index by encounter order.

    NB:

    1. Keep-first-parent tree enforcement.
    2. Drop and warn on parent conflicts.
    3. Assign order index by first encounter order per parent.
    4. Merge provenance if the exact same edge is encountered again.

    High-level Overview
    -------------------
    This function is basically saying:
        1. “Can this child legally live under this parent?"
        2. If no, drop it.
        3. If yes, have we already recorded this exact link?
        4. If yes, merge provenance.
        5. If no, create it with the next sibling slot.

    In other words, this is the function that actually turns “this grouping should now
    exist under that parent” into a tree edge, while defending against:
        - Duplicate links
        - Conflicting parents
        - Unstable sibling order

    That is why this function sits in the middle of almost every materialization path.

    Examples
    --------
    1. Normal new edge
        Suppose the compiler has already created a section node and now wants to attach
        a week node under it.

        Inputs:

        * parent_id = section_A
        * child_id = week_1
        * child_to_parent = {}
        * edges_by_key = {}
        * next_order_index[section_A] = 0

        What happens:

        * child has no parent yet
        * record child_to_parent[week_1] = section_A
        * edge key is new
        * assign order_index = 0
        * create edge (section_A)-[:hasChild {order_index: 0}] -> (week_1)

        Afterward:

        next_order_index[section_A] = 1

        So the first child under section_A gets order 0.

    2. Same exact edge encountered again
        Now later another decision or segment points to the same parent and child:

        * parent_id = section_A
        * child_id = week_1
        * decision_id = dec_99
        * segment_id = seg_99

        What happens:

        * existing_parent is section_A, so no parent conflict
        * edge key (section_A, week_1, "hasChild") already exists
        * do not create a second edge
        * just append seg_99 and dec_99 to the existing edge provenance

        So we still have one edge, but now it knows it was supported by multiple
        decisions/segments. This is useful because grouping nodes often get revisited
        by later decisions.

    3. Illegal second parent
        Suppose week_1 is already attached under section_A, and later some buggy path
        tries to attach it under section_B.

        Inputs:

        * child_to_parent["week_1"] = section_A
        * incoming parent_id = section_B

        What happens:

        * existing_parent is section_A
        * incoming parent is section_B
        * mismatch triggers tree_parent_conflict_dropped
        * function returns immediately
        * no edge is emitted under section_B

        This prevents the canonical hierarchy from turning into a DAG or multi-parent
        structure. This is especially important because
        `_materialize_decision_structure()` and `reconcile_context_stack()` may revisit
        nodes from many segments, and the compiler wants one clean containment tree.

    4. Sibling ordering
        Suppose under the same parent strand_X, the compiler emits children in this
        order:

        * week_1
        * week_2
        * week_3

        Then _emit_edge() assigns:

        * week_1 → order 0
        * week_2 → order 1
        * week_3 → order 2

        If week_2 is encountered again later, _emit_edge() does not change its order
        index. It only merges provenance into the existing edge. So first-seen order
        wins.

    Parameters
    ----------
    child_id
        The child node ID.
    child_to_parent
        The mapping of child_id to parent_id. This contains information regarding what
        parent a child already belongs to. Without this dict, we could still
        accidentally attach the same child to two different parents.
    decision_id
        The SegmentDecision ID.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
        This contains information regarding whether or not we have already emitted a
        given parent -> child edge. Without this dict, we could still emit duplicate
        copies of the same edge.
    next_order_index
        The mapping of parent_id to next order_index. This contains information
        regarding what sibling index should the next child under a parent get.
    parent_id
        The parent node ID.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.
    """

    # Keep-first-parent tree enforcement.
    existing_parent = child_to_parent.get(child_id)

    # This is the tree-enforcement rule. The canonical IR is supposed to be a tree of
    # `hasChild` edges, not a general graph with multiple containment parents. So once
    # a child is attached to one parent, any later attempt to attach it somewhere else
    # must be dropped.
    if existing_parent is not None and existing_parent != parent_id:
        msg = (
            f"tree_parent_conflict_dropped:"
            f"child={child_id} existing_parent={existing_parent} new_parent={parent_id}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return

    # Record first valid parent assignment. This happens before edge dedupe so that
    # parent ownership is established at the child level, not only at the edge-object
    # level.
    if existing_parent is None:
        child_to_parent[child_id] = parent_id

    # Edge key includes `rel` to match CanonicalEdge identity semantics. This tuple is
    # the identity of the edge. If the same parent -> child relation comes thru again,
    # it is treated as the same edge rather than a new one.
    key = (parent_id, child_id, "hasChild")

    # If edge already exists, merge provenance into the first-emitted edge instead of
    # creating a new one.
    existing = edges_by_key.get(key)

    # Repeated evidence for the same parent -> child link does not create duplicate
    # edges. It just adds onto the provenance on the first edge. An edge tells us:
    # "What containment relationship exists?". Multiple segments/decisions may
    # contribute evidence for the same containment relationship, so the edge keeps its
    # own `source_segment_ids` and `source_decision_ids` lists to track all supporting
    # evidence.
    if existing is not None:
        existing.source_segment_ids = _stable_extend_unique(
            base=existing.source_segment_ids, extra=[segment_id]
        )
        existing.source_decision_ids = _stable_extend_unique(
            base=existing.source_decision_ids, extra=[decision_id]
        )
        return

    # Create a brand-new edge with the next sibling index.
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


def _extract_table_headers(
    *, segment: Segment, warnings: Optional[list[str]] = None
) -> list[str]:
    """Best-effort extraction of header cell strings for unresolved table items.

    Returns an empty list when the segment is not a table or when the table has no
    header rows. Missing headers are warning-worthy for debugging, but should never
    crash canonical compilation.

    Parameters
    ----------
    segment
        The Segment to extract headers from.
    warnings
        Optional list to append warning messages to when header rows are missing.

    Returns
    -------
    list[str]
        The extracted header cell strings.
    """

    if segment.kind != "table":
        return []

    header_rows = segment.header_rows

    if not header_rows:
        msg = f"table_missing_header_rows:{segment.segment_id}"
        logger.warning(msg)

        if warnings is not None:
            warnings.append(msg)

        return []

    # Use the last header row as "most specific".
    last_header_row = header_rows[-1]
    output: list[str] = []

    for cell in last_header_row.cells:
        tu = cell.text

        if tu and tu.text.strip():
            output.append(tu.text.strip())

    return output


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
    segment_decisions: SegmentDecisionSet,
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
        assert isinstance(d.segment_id, str) and d.segment_id, (
            f"SegmentDecision.segment_id must be populated before canonical compilation. "
            f"Found missing segment_id for decision_id={d.decision_id!r} "
            f"row_range=({d.row_range_start},{d.row_range_end}) decision_type={d.decision_type}"
        )
        assert isinstance(d.decision_id, str) and d.decision_id, (
            f"SegmentDecision.decision_id must be populated before canonical compilation. "
            f"Found missing decision_id for segment_id={d.segment_id!r} "
            f"row_range=({d.row_range_start},{d.row_range_end}) decision_type={d.decision_type}"
        )
        decisions_by_segment[d.segment_id].append(d)

    return decisions_by_segment


def _make_unmatched_segment_sample(
    *, max_len: int = 280, segment: Segment
) -> str | None:
    """Sample string for segments that have no SegmentDecision.

    For blocks: uses best-effort extracted text.
    For tables: includes header row preview + first body-row preview when available.

    Parameters
    ----------
    max_len
        The maximum length of the sample string.
    segment
        The Segment to create a sample for.

    Returns
    -------
    str | None
        A short sample string for debugging, or None if no useful info could be
        extracted.
    """

    if isinstance(segment, BlockSegment):
        text = extract_block_segment_text(segment)
        return text[:max_len] if text else None

    if isinstance(segment, TableSegment):
        parts: list[str] = []
        headers = _extract_table_headers(segment=segment)

        if headers:
            parts.append("headers=" + " | ".join(headers[:8]))

        row_preview = _table_first_body_row_preview(segment=segment)

        if row_preview:
            parts.append("row0=" + row_preview)

        s = " | ".join(parts).strip()
        return s[:max_len] if s else None

    return None


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
    rationale = decision.rationale
    assert rationale, (
        f"Unresolved decisions should always have a rationale. "
        f"Found empty rationale for decision_id={decision.decision_id} "
        f"segment_id={decision.segment_id}"
    )
    parts.append(f"rationale={rationale}")

    if isinstance(segment, BlockSegment):
        segment_text = extract_block_segment_text(segment)
    else:
        # Fallback: best-effort (mostly useful for some segment variants).
        text_or_none = getattr(segment, "text", None)
        segment_text = text_or_none.text if isinstance(text_or_none, TextUnit) else None

    if segment_text and segment_text.strip():
        parts.append(segment_text.strip())
    # For tables, we often don't have a clean "text" field; header preview can help
    # debugging.
    elif isinstance(segment, TableSegment):
        headers = _extract_table_headers(segment=segment)

        if headers:
            parts.append("headers=" + " | ".join(headers[:8]))

    s = " | ".join(parts).strip()
    return s[:max_len]


def _materialize_decision_structure(
    *,
    active_context_stack: list[ContextFrame],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    root_id: str,
    section_path_text: list[str],
    segment: Segment,
    warnings: list[str],
) -> list[ContextFrame]:
    """Reconcile context stack, create grouping nodes, and materialize leaves/rows.

    NB: For tables, a table decision may contain both decision.leaves[] (segment-level
    statements) OR decision.rows[] (row-wise statements). In these cases, emitting
    leaves first makes them appear first in deterministic order_index order under that
    parent. If we wanted to emit rows first, then we can just switch the if-statements.

    High-level Overview
    -------------------

    1. `reconcile_context_stack()` only restores the compiler to the exact context
        snapshot represented by `decision.context_groupings`. That is “where are we?”
    2. This main for-loop then materializes `decision.groupings`, which are structural
        nodes emitted by the decision itself. That is “what new containers does this
        decision add before its leaves/rows?”
    3. So the sequence is:
        3a. Restore correct structural location
        3b. Emit any grouping containers for this decision
        3c. Emit leaves or table rows under the final parent
    4. That is why `_materialize_leaves()` and `_materialize_table_rows()` are called
        after the main for-loop, with the updated `parent_id` and `ancestor_keys`.

    Examples
    --------
    1. One grouping emitted under existing context
        Suppose reconciliation returned:

        * current context branch = Section: Planification
        * parent_id = planification_node
        * ancestor_keys = [section:planification]

        and the decision has:

        * decision.groupings = [GroupingDecision(role="week", title="Semaine 4")]

        The loop will:

        * canonicalize Semaine 4
        * compute a node ID for week/Semaine 4 under the Planification ancestor path
        * build the week node
        * ensure/reuse it
        * emit Planification -> Semaine 4
        * update parent_id to the week node
        * append the week grouping key to ancestor_keys

        Then when `_materialize_leaves()` runs afterward, the leaves attach under
        Semaine 4, not under Planification.

    2. Two emitted groupings become nested, not siblings
        Suppose reconciliation returned:

        * current parent = strand = Communication orale

        and the decision has:

        *  decision.groupings = [substage="Palier 1", topic="Salutations"]

        Iteration 1:

        * create/reuse Palier 1
        * emit Communication orale -> Palier 1
        * update parent to Palier 1

        Iteration 2:

        * compute the topic node ID using ancestor keys that now include Palier 1
        * create/reuse Salutations
        * emit Palier 1 -> Salutations
        * update parent to Salutations

        Resulting structure:

        * Communication orale
            * Palier 1
                * Salutations

        not:

        * Communication orale
            * Palier 1
            * Salutations

        That parent/ancestor update at the bottom of the loop is what causes the
        nesting.

    3. Same grouping encountered again later
        Suppose a later decision arrives with the same reconciled context and again
        emits:

        * week = "Semaine 4"

        The loop will build the same deterministic node ID, `ensure_node()` will reuse
        the existing node, and `_emit_edge()` will reuse the existing edge while
        merging provenance so that we do not get duplicate week nodes.

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
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
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
        desired_context_groupings=decision.context_groupings,
        doc_key=doc_key,
        edges_by_key=edges_by_key,
        edges=edges,
        next_order_index=next_order_index,
        nodes_by_id=nodes_by_id,
        page_indices=page_indices,
        root_id=root_id,
        segment=segment,
        warnings=warnings,
    )

    # Apply decision.groupings[] under the context stack tip. This for-loop takes each
    # GroupingDecision in decision.groupings and turns it into an actual canonical
    # grouping node and a `hasChild` edge, one-by-one, under whatever parent
    # `reconcile_context_stack()` just returned. Then, it updates the local parent/path
    # state so the **next** grouping in the list nests underneath the previous one,
    # creating a chain of groupings if there are multiple.
    #
    # NB:
    #
    # 1. decision.context_groupings is where a decision already lives.
    # 2. decision.groupings is the new structure that a decision itself should emit.
    #
    # Thus, after `reconcile_context_stack()` restores the correct context snapshot,
    # this loop **extends that** branch with any grouping containers produced by the
    # decision.
    for g in decision.groupings:
        g_title = canonical_grouping_title(role=g.role, title=g.title)
        node_id = canonical_grouping_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, grouping=g
        )

        # NB: This is a grouping/container node and not a leaf statement node (`title`
        # is set buty `body` is None).
        node = CanonicalNode(
            bbox=_segment_first_bbox(segment),
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
            title=TextUnit(language="und", text=canonical_storage_text(g_title)),
        )

        effective_node_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=effective_node_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=segment.segment_id,
            warnings=warnings,
        )

        # Update variables so that the newly created (or reused) grouping becomes the
        # parent for the next iteration, and its semantic key becomes part of the
        # ancestor path used for the next node ID. This is what makes the for-loop nest
        # the groupings instead of attaching them all as siblings.
        parent_id = effective_node_id
        ancestor_keys.append(_grouping_key(g))

    # Dispatch based on segment kind.
    if segment.kind == "block":
        _materialize_leaves(
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
            segment_bbox=_segment_first_bbox(segment),
            segment_id=segment.segment_id,
            source_type="block",
            warnings=warnings,
        )
    elif segment.kind == "table":
        assert isinstance(segment, TableSegment), (
            f"Segment {segment.segment_id} has kind='table' but is not a "
            f"TableSegment instance ({type(segment).__name__})."
        )

        # NB: decision.groupings (segment-level grouping containers) have already been
        # materialized above; only warn when there are truly no leaves, rows, OR
        # groupings to emit.
        if not decision.leaves and not decision.rows and not decision.groupings:
            msg = f"table_decision_emits_nothing:{segment.segment_id}:{decision.decision_id}"
            logger.warning(msg)
            warnings.append(msg)

        # Table decisions may emit leaves directly.
        #
        # NB: decision.leaves and decision.rows are mutually exclusive (enforced by
        # SegmentDecision._check_table_rows_vs_leaves).
        if decision.leaves:
            _materialize_leaves(
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
                segment_bbox=_segment_first_bbox(segment),
                segment_id=segment.segment_id,
                source_type="table",
                warnings=warnings,
            )
        # Preferred: row-level decisions.
        elif decision.rows:
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
                table_segment=segment,
                warnings=warnings,
            )
    else:
        msg = f"unknown_segment_kind:{segment.kind}:{segment.segment_id}"
        logger.warning(msg)
        warnings.append(msg)

    return active_context_stack


def _materialize_leaves(
    *,
    ancestor_keys: list[str],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    parent_id: str,
    section_path_text: list[str],
    segment_bbox: Optional[BBox],
    segment_id: str,
    source_type: str,
    warnings: list[str],
) -> None:
    """Materialize leaf nodes from decision.leaves[] under the current parent.

    Used for both block and table segments (segment-level leaves). The `source_type`
    parameter preserves the correct provenance origin.

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
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
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
    segment_bbox
        The segment bounding box.
    segment_id
        The segment ID.
    source_type
        The provenance source type ("block" or "table").
    warnings
        The list of warnings to append to.
    """

    for leaf in decision.leaves:
        leaf_id = canonical_leaf_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, leaf=leaf
        )
        node = CanonicalNode(
            bbox=segment_bbox,
            body=TextUnit(language="und", text=canonical_storage_text(leaf.body)),
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
            source_type=source_type,
            title=None,
        )
        effective_leaf_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=effective_leaf_id,
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_id,
            segment_id=segment_id,
            warnings=warnings,
        )


def _materialize_row_groupings(
    *,
    child_to_parent: dict[str, str],
    decision_id: str,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
    groupings: list[Any],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    row_ancestor_keys: list[str],
    row_bbox: BBox,
    row_parent_id: str,
    section_path_text: list[str],
    segment_id: str,
    warnings: list[str],
) -> str:
    """Process groupings for a table row and emit corresponding nodes and edges.

    Parameters
    ----------
    child_to_parent
        The mapping of child_id to parent_id.
    decision_id
        The ID of the decision being materialized.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
    groupings
        The list of groupings for the row.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    row_ancestor_keys
        The current ancestor keys for the row (mutated in place).
    row_bbox
        The bounding box for the row.
    row_parent_id
        The parent node ID for the row.
    section_path_text
        The section path text for the segment.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.

    Returns
    -------
    str
        The effective parent ID after processing all groupings.
    """

    for g in groupings:
        g_title = canonical_grouping_title(role=g.role, title=g.title)
        node_id = canonical_grouping_node_id(
            ancestor_grouping_keys=row_ancestor_keys, doc_key=doc_key, grouping=g
        )
        node = CanonicalNode(
            bbox=row_bbox,
            body=None,
            list_marker=None,
            local_code=g.local_code,
            node_id=node_id,
            normalized_text=_normalize_text(text=g_title),
            page_indices=page_indices,
            role=g.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision_id],
            source_label=g.source_label,
            source_segment_ids=[segment_id],
            source_type="table",
            title=TextUnit(language="und", text=canonical_storage_text(g_title)),
        )
        effective_node_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=effective_node_id,
            child_to_parent=child_to_parent,
            decision_id=decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=row_parent_id,
            segment_id=segment_id,
            warnings=warnings,
        )
        row_parent_id = effective_node_id
        row_ancestor_keys.append(_grouping_key(g))

    return row_parent_id


def _materialize_row_leaves(
    *,
    child_to_parent: dict[str, str],
    decision_id: str,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    row: RowDecision,
    row_ancestor_keys: list[str],
    row_bbox: BBox,
    row_parent_id: str,
    section_path_text: list[str],
    segment_id: str,
    warnings: list[str],
) -> None:
    """Process and materialize leaves for a table row, handling expectation anchoring.

    Parameters
    ----------
    child_to_parent
        The mapping of child_id to parent_id.
    decision_id
        The ID of the decision being materialized.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append to.
    edges_by_key
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
    next_order_index
        The mapping of parent_id to next order_index.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The list of page indices for the segment.
    row
        The RowDecision containing the leaves to materialize.
    row_ancestor_keys
        The current ancestor keys for the row.
    row_bbox
        The bounding box for the row.
    row_parent_id
        The parent node ID for the row.
    section_path_text
        The section path text for the segment.
    segment_id
        The segment ID.
    warnings
        The list of warnings to append to.
    """

    row_disambiguator = (
        f"table_row:{segment_id}:{row.row_index}:"
        f"{row.col_index if row.col_index is not None else '-'}"
    )
    expectation_leaves = [
        leaf for leaf in row.leaves if _role_value(role=leaf.role) == "expectation"
    ]
    column_scope_key = (
        f"table_col:{segment_id}:{row.col_index}" if row.col_index is not None else None
    )
    legacy_leaf_ancestor_keys = list(row_ancestor_keys)

    if column_scope_key is not None:
        legacy_leaf_ancestor_keys.append(column_scope_key)

    legacy_leaf_ancestor_keys.append(row_disambiguator)
    anchored_expectation_id: Optional[str] = None
    expectation_anchor_key: Optional[str] = None

    if len(expectation_leaves) == 1:
        expectation = expectation_leaves[0]
        expectation_ancestor_keys = list(row_ancestor_keys)

        if column_scope_key is not None:
            expectation_ancestor_keys.append(column_scope_key)

        anchored_leaf_id = canonical_leaf_node_id(
            ancestor_grouping_keys=expectation_ancestor_keys,
            doc_key=doc_key,
            leaf=expectation,
        )
        expectation_node = CanonicalNode(
            bbox=row_bbox,
            body=TextUnit(
                language="und", text=canonical_storage_text(expectation.body)
            ),
            list_marker=expectation.list_marker,
            local_code=expectation.local_code,
            node_id=anchored_leaf_id,
            normalized_text=_normalize_text(text=expectation.body),
            page_indices=page_indices,
            role=expectation.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision_id],
            source_label=expectation.source_label,
            source_segment_ids=[segment_id],
            source_type="table",
            title=None,
        )
        anchored_expectation_id = ensure_node(
            node=expectation_node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=anchored_expectation_id,
            child_to_parent=child_to_parent,
            decision_id=decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=row_parent_id,
            segment_id=segment_id,
            warnings=warnings,
        )
        expectation_anchor_key = f"expectation_anchor:{anchored_leaf_id}"
    elif len(expectation_leaves) > 1:
        aux_leaves = [
            leaf
            for leaf in row.leaves
            if _role_value(role=leaf.role) in {"descriptor", "guidance"}
        ]

        if aux_leaves:
            msg = (
                f"table_row_multiple_expectations_unanchored_aux:"
                f"segment={segment_id} row={row.row_index} "
                f"expectations={len(expectation_leaves)} "
                f"aux_leaves={len(aux_leaves)}"
            )
            logger.warning(msg)
            warnings.append(msg)

    for leaf in row.leaves:
        role_value = _role_value(role=leaf.role)

        if anchored_expectation_id is not None and role_value == "expectation":
            continue

        is_aux = role_value in {"descriptor", "guidance"}
        parent_for_leaf = (
            anchored_expectation_id
            if anchored_expectation_id is not None and is_aux
            else row_parent_id
        )

        if (
            parent_for_leaf == anchored_expectation_id
            and expectation_anchor_key is not None
        ):
            leaf_ancestor_keys = list(row_ancestor_keys)

            if column_scope_key is not None:
                leaf_ancestor_keys.append(column_scope_key)

            leaf_ancestor_keys.append(expectation_anchor_key)
            leaf_ancestor_keys.append(row_disambiguator)
        else:
            leaf_ancestor_keys = legacy_leaf_ancestor_keys

        leaf_id = canonical_leaf_node_id(
            ancestor_grouping_keys=leaf_ancestor_keys, doc_key=doc_key, leaf=leaf
        )
        node = CanonicalNode(
            bbox=row_bbox,
            body=TextUnit(language="und", text=canonical_storage_text(leaf.body)),
            list_marker=leaf.list_marker,
            local_code=leaf.local_code,
            node_id=leaf_id,
            normalized_text=_normalize_text(text=leaf.body),
            page_indices=page_indices,
            role=leaf.role,
            section_path_text=section_path_text,
            source_decision_ids=[decision_id],
            source_label=leaf.source_label,
            source_segment_ids=[segment_id],
            source_type="table",
            title=None,
        )
        effective_leaf_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )
        _emit_edge(
            child_id=effective_leaf_id,
            child_to_parent=child_to_parent,
            decision_id=decision_id,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            parent_id=parent_for_leaf,
            segment_id=segment_id,
            warnings=warnings,
        )


def _materialize_table_rows(
    *,
    ancestor_keys: list[str],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    parent_id: str,
    section_path_text: list[str],
    segment_id: str,
    table_segment: TableSegment,
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
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
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
    table_segment
        The TableSegment to materialize rows for.
    warnings
        The list of warnings to append to.
    """

    # Sort by row index, then col index, and treat None as max int for col index. This
    # matters because edge `order_index` is assigned by encounter order downstream via
    # `_emit_edge()`.
    for row in sorted(
        decision.rows,
        key=lambda r: (
            r.row_index,
            r.col_index if r.col_index is not None else 2**31 - 1,
        ),
    ):
        row_ancestor_keys = list(ancestor_keys)
        row_bbox = None

        if table_segment.row_provenance and 0 <= row.row_index < len(
            table_segment.row_provenance
        ):
            rp = table_segment.row_provenance[row.row_index]
            row_bbox = rp.row_bbox or rp.bbox

        row_parent_id = _materialize_row_groupings(
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            doc_key=doc_key,
            edges=edges,
            edges_by_key=edges_by_key,
            groupings=row.groupings,
            next_order_index=next_order_index,
            nodes_by_id=nodes_by_id,
            page_indices=page_indices,
            row_ancestor_keys=row_ancestor_keys,
            row_bbox=row_bbox,
            row_parent_id=parent_id,
            section_path_text=section_path_text,
            segment_id=segment_id,
            warnings=warnings,
        )
        _materialize_row_leaves(
            child_to_parent=child_to_parent,
            decision_id=decision.decision_id,
            doc_key=doc_key,
            edges=edges,
            edges_by_key=edges_by_key,
            next_order_index=next_order_index,
            nodes_by_id=nodes_by_id,
            page_indices=page_indices,
            row=row,
            row_ancestor_keys=row_ancestor_keys,
            row_bbox=row_bbox,
            row_parent_id=row_parent_id,
            section_path_text=section_path_text,
            segment_id=segment_id,
            warnings=warnings,
        )


def _normalize_text(text: Optional[str]) -> str:
    """Deterministic normalization for hashing/comparisons.

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
    text = text.translate(QUOTES_TRANSLATION)
    text = DASH_RE.sub("-", text)
    text = WS_RE.sub(" ", text).strip()

    # Normalize colon spacing ONLY when a non-space follows the colon.
    text = re.sub(r":\s*(?=\S)", ": ", text)
    text = WS_RE.sub(" ", text).strip()

    return text.casefold()


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


def _resolve_collision(
    *, node: CanonicalNode, nodes_by_id: dict[str, CanonicalNode], warnings: list[str]
) -> str:
    """Disambiguate a node ID using provenance data and insert it as a new node. Used
    when a semantic collision is detected. Updates `node.node_id` in place.

    Parameters
    ----------
    node
        The CanonicalNode with a colliding node_id.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    warnings
        The list of warnings to append to.

    Returns
    -------
    str
        The new unique node_id.
    """

    parts: list[str] = []

    # Build a stable disambiguator string from provenance.
    if node.source_segment_ids:
        parts.append(f"seg={node.source_segment_ids[0]}")

    if node.source_decision_ids:
        parts.append(f"dec={node.source_decision_ids[0]}")

    if node.page_indices:
        parts.append(f"page={node.page_indices[0]}")

    if node.list_marker:
        parts.append(f"list={node.list_marker}")

    if node.local_code:
        parts.append(f"code={node.local_code}")

    disambiguator = "|".join(parts) if parts else "no_provenance"
    base_key = f"{node.node_id}|collision|{disambiguator}"

    # Generate deterministic new ID.
    new_id = uuidv5_from_key(base_key)

    # Extremely defensive: ensure uniqueness deterministically.
    i = 1

    while new_id in nodes_by_id:
        new_id = uuidv5_from_key(f"{base_key}|{i}")
        i += 1

    msg = (
        f"node_id_collision_resolved:"
        f"old_node_id={node.node_id} new_node_id={new_id} disambiguator={disambiguator}"
    )
    logger.warning(msg)
    warnings.append(msg)

    # Apply changes.
    node.node_id = new_id
    nodes_by_id[new_id] = node

    return new_id


def _role_value(role: Any) -> str:
    """Return a normalized (case-folded) string value for a role.

    Parameters
    ----------
    role
        The role value (enum or string).

    Returns
    -------
    str
        The normalized role string.
    """

    value = getattr(role, "value", role)

    return str(value).casefold()


def _segment_first_bbox(segment: Segment) -> BBox:
    """Best-effort bbox for a segment.

    NB: Segments can span pages; bboxes are page-local, so we only take the first
    provenance bbox (deterministic + meaningful for debugging).

    Parameters
    ----------
    segment
        The Segment to extract the bbox from.

    Returns
    -------
    BBox
        The BBox for the segment.
    """

    return segment.segment_provenance[0].bbox


def _stable_extend_unique(*, base: list[T], extra: list[T]) -> list[T]:
    """Deterministic "stable" union for string lists:

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


def _table_first_body_row_preview(
    *, max_cell_len: int = 40, max_cells: int = 8, segment: TableSegment
) -> str | None:
    """Create a compact preview of the first non-header row in a table.

    Preference order for source rows:

    1. rows_filldown (if present)
    2. rows_grid (if present)
    3. rows (raw stitched visual rows)

    Parameters
    ----------
    max_cell_len
        The maximum length of text to include for each cell before truncating.
    max_cells
        The maximum number of cells to include in the preview before truncating.
    segment
        The TableSegment to extract the row preview from.

    Returns
    -------
    str | None
        A string preview of the first non-header row, or None if no rows are available.
    """

    rows = segment.rows_filldown or segment.rows_grid or segment.rows

    if not rows:
        return None

    hrc = segment.header_row_count or 0

    if hrc >= len(rows):
        return None

    row = rows[hrc]
    cells_out: list[str] = []
    any_non_empty = False

    for cell in (row.cells or [])[:max_cells]:
        tu = cell.text
        raw_text = tu.text if isinstance(tu, TextUnit) else ""
        text = " ".join(raw_text.split()).strip()

        if text:
            any_non_empty = True

            if len(text) > max_cell_len:
                text = text[: max_cell_len - 1] + "…"

            cells_out.append(text)
        else:
            cells_out.append("∅")

    if not any_non_empty:
        return None

    return " | ".join(cells_out)


def _validate_and_handle_unresolved(
    *,
    decision: SegmentDecision,
    page_indices: list[int],
    section_path_text: list[str],
    segment: Segment,
    segment_decision_conf_threshold: float,
    unresolved: list[UnresolvedItem],
    warnings: list[str],
) -> bool:
    """Validate decision, update unresolved/warnings, return True if materializable.

    Parameters
    ----------
    decision
        The SegmentDecision to validate.
    page_indices
        The list of page indices for the segment.
    section_path_text
        The section path text for the segment.
    segment
        The Segment to validate.
    segment_decision_conf_threshold
        The low confidence threshold for segment decisions.
    unresolved
        The list of UnresolvedItems to append to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    bool
        True if the decision is materializable, False otherwise. When False, the caller
        skips `_materialize_decision_structure`, which means `active_context_stack` is
        **not** updated for this decision.
    """

    if decision.decision_type == SegmentDecisionType.IGNORE:
        return False

    # SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED is a "review" decision. It must be
    # persisted to the audit trail, but MUST NOT be materialized into CanonicalIR
    # nodes/edges.
    if decision.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED:
        msg = (
            f"flagged_unresolved_decision_not_materialized:"
            f"segment_id={segment.segment_id} "
            f"decision_id={decision.decision_id} "
            f"kind={segment.kind} "
            f"conf={decision.confidence:.3f}"
        )
        logger.warning(msg)
        warnings.append(msg)
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=_extract_table_headers(segment=segment, warnings=warnings),
                kind=segment.kind,
                local_code=segment.local_code,
                page_indices=page_indices,
                reason=UnresolvedReason.FLAGGED_UNRESOLVED,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    if decision.decision_type == SegmentDecisionType.UNRESOLVED:
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=_extract_table_headers(segment=segment, warnings=warnings),
                local_code=segment.local_code,
                kind=segment.kind,
                page_indices=page_indices,
                reason=UnresolvedReason.DECISION_UNRESOLVED,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    # Confidence gating. NB: Currently, all confidences are deterministically set to
    # either 0.0 or 1.0. We keep this check here in case confidence is more
    # fine-grained in the future (e.g., set by LLMs).
    if decision.confidence < segment_decision_conf_threshold:
        msg = (
            f"low_confidence_decision_not_materialized:"
            f"segment_id={segment.segment_id} "
            f"decision_id={decision.decision_id} "
            f"kind={segment.kind} "
            f"conf={decision.confidence:.3f} "
            f"threshold={segment_decision_conf_threshold:.3f}"
        )
        logger.warning(msg)
        warnings.append(msg)
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=_extract_table_headers(segment=segment, warnings=warnings),
                kind=segment.kind,
                local_code=segment.local_code,
                page_indices=page_indices,
                reason=UnresolvedReason.LOW_CONFIDENCE_DECISION_NOT_MATERIALIZED,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    return True


def apply_table_signatures(
    *, decisions: list[SegmentDecision], document_ir: Any
) -> list[SegmentDecision]:
    """Iterate through segment decisions and update table segments with column
    signatures found in the source DocumentIR.

    Parameters
    ----------
    decisions
        The list of SegmentDecisions to update.
    document_ir
        The source DocumentIR (dict or object form).

    Returns
    -------
    list[SegmentDecision]
        The updated list of SegmentDecisions.
    """

    segments = document_ir.segments
    signature_map = {}

    # Build the map for segment_id -> columns_signature.
    for segment in segments:
        seg_id = segment.segment_id
        col_sig = getattr(segment, "columns_signature", None)

        if seg_id and col_sig is not None:
            signature_map[seg_id] = col_sig

    # Update decisions.
    updated_decisions = []

    for d in decisions:
        # If it is a table and we have a signature for it, update the field.
        if d.segment_kind == "table" and d.segment_id in signature_map:
            d = d.model_copy(update={"columns_signature": signature_map[d.segment_id]})

        updated_decisions.append(d)

    return updated_decisions


def canonical_grade_level_title(title: str) -> str:
    """Normalize common grade label variants into a consistent display form.

    Examples
    --------
    1. "GRADE 1-3"     -> "GRADES 1–3"
    2. "GRADES 1 – 3"  -> "GRADES 1–3"
    3. "Grade 2"       -> "GRADE 2"

    NB:

    1. This only fires when patterns match confidently.
    2. Otherwise, we return the original string unchanged.

    Parameters
    ----------
    title
        The original title string to canonicalize.

    Returns
    -------
    str
        The canonicalized title string if patterns matched, otherwise the original
        title.
    """

    if not title:
        return title

    # Normalize unicode + whitespace + dash variants for matching.
    t = unicodedata.normalize("NFKC", title).strip()
    t = WS_RE.sub(" ", t)
    t = DASH_RE.sub("-", t)  # Unify various dash chars
    t = re.sub(r"\s*-\s*", "-", t)  # Remove spaces around hyphen
    t = WS_RE.sub(" ", t).strip()

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

    NB: The node ID is not based only on the grouping text. It depends on:
        1. Document key
        2. Current ancestor path fingerprint
        3. Grouping role
        4. Normalized local code
        5. Normalized title hash

    So the same visible title can produce different IDs in different branches.

    For example:

    week = "Semaine 1" under strand = Lecture
    week = "Semaine 1" under strand = Récitation

    Those are not treated as the same grouping node, because their ancestor_keys differ.

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
    code = normalize_local_code(code=grouping.local_code or "-")
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
    code = normalize_local_code(code=leaf.local_code or "-")
    text_hash = _normalized_text_hash(text=leaf.body)
    key = canonical_key(
        doc_key=doc_key,
        local_code_or_dash=code,
        normalized_text_hash_hex=text_hash,
        path_fp=path_fp,
        role=leaf.role.value,
    )
    return uuidv5_from_key(key)


def canonical_storage_text(text: Optional[str]) -> str:
    """Canonicalize text for storage in CanonicalNode.{title,body}.text. The goal here
    is to remove meaningless formatting noise while preserving original casing. This
    reduces formatting-diff warnings and makes merges stable.

    Parameters
    ----------
    text
        The text to canonicalize.

    Returns
    -------
    str
        The canonicalized text.
    """

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = DASH_RE.sub("-", text)
    text = WS_RE.sub(" ", text).strip()
    return text


def compile_and_save_canonical_ir(
    *,
    canonical_ir_fp: Path,
    doc_key: str,
    document_ir: DocumentIR,
    segment_decision_conf_threshold: float = 0.8,
    segment_decisions: SegmentDecisionSet,
    structural_leaf_warn_threshold: float = 0.8,
) -> None:
    """Compile a CanonicalIR from DocumentIR and SegmentDecisionSet and write results
    to file.

    The process is as follows:

    1. Initialize state containers.
    2. Index decisions by segment ID.
    3. Create Framework Root node.
    4. Main traversal loop:
        a. For each segment in DocumentIR:
            i.   Prepare segment-level data.
            ii.  If no segment decisions, log warning + add to unresolved.
            iii. For each segment decision:
                1. Validate the segment decision; update unresolved/warnings; skip if
                    not materializable.
                2. Check for structural warnings; update warnings.
                3. Materialize canonical IR nodes; update state containers.
    5. Post-pass hygiene: merge duplicate nodes, dedupe edges, prune empty groupings,
        prune unreachable nodes, reindex sibling order_indices, sanity check tree
        invariants, and apply table column signatures.
    6. Serialize final CanonicalIR to JSON.

    Parameters
    ----------
    canonical_ir_fp
        The file path to write the compiled CanonicalIR JSON to.
    doc_key
        The document key.
    document_ir
        The DocumentIR to process.
    segment_decision_conf_threshold
        The low confidence threshold for segment decisions. Decisions with confidence
        below this are demoted to unresolved. Defaults to 0.8. NB: curriculum skeleton
        generated decisions always have confidence=1.0, so this only applies to
        LLM-generated decision sets.
    segment_decisions
        The SegmentDecisionSet to apply.
    structural_leaf_warn_threshold
        The confidence threshold below which structural leaves will emit warnings.
        Defaults to 0.8. NB: curriculum skeleton generated decisions always have
        confidence=1.0, so this only applies to LLM-generated decision sets.
    """

    logger.info("Compiling CanonicalIR from SegmentDecisions...")

    # 1.
    active_context_stack: list[ContextFrame] = []
    child_to_parent: dict[str, str] = {}
    edges: list[CanonicalEdge] = []
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge] = {}
    next_order_index: dict[str, int] = defaultdict(int)
    nodes_by_id: dict[str, CanonicalNode] = {}
    unresolved: list[UnresolvedItem] = []
    warnings: list[str] = []

    # 2.
    decisions_by_segment = _index_decisions_by_segment(segment_decisions)

    # 3.
    framework_title = segment_decisions.pdf_name
    root_id = uuidv5_from_key(f"lc:canonical:{doc_key}:framework")
    framework_node = CanonicalNode(
        bbox=None,
        body=None,
        list_marker=None,
        local_code=None,
        node_id=root_id,
        normalized_text=_normalize_text(framework_title),
        page_indices=[],
        role=NodeRole.FRAMEWORK,
        section_path_text=[],
        source_decision_ids=[],
        source_label=None,
        source_segment_ids=[],
        source_type=None,
        title=TextUnit(language="und", text=canonical_storage_text(framework_title)),
    )
    effective_root_id = ensure_node(
        node=framework_node, nodes_by_id=nodes_by_id, warnings=warnings
    )

    # 4.
    for segment in document_ir.segments:
        seg_id = segment.segment_id
        seg_decisions = decisions_by_segment.get(seg_id, [])

        # Prepare segment-level data (needed even when unresolved).
        page_indices = sorted({p.page_index for p in segment.segment_provenance})
        section_path_text = [h.text for h in (segment.section_path or [])]

        if not seg_decisions:
            reason = (
                UnresolvedReason.UNMATCHED_TABLE
                if segment.kind == "table"
                else UnresolvedReason.UNMATCHED_BLOCK
            )
            msg = f"no_decision_for_segment:{seg_id}"
            logger.warning(msg)
            warnings.append(msg)
            unresolved.append(
                UnresolvedItem(
                    caption_text=None,
                    headers=_extract_table_headers(segment=segment, warnings=warnings),
                    kind=segment.kind,
                    local_code=segment.local_code,
                    page_indices=page_indices,
                    reason=reason,
                    sample=_make_unmatched_segment_sample(segment=segment),
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
                page_indices=page_indices,
                section_path_text=section_path_text,
                segment=segment,
                segment_decision_conf_threshold=segment_decision_conf_threshold,
                unresolved=unresolved,
                warnings=warnings,
            )

            if not should_continue:
                continue

            # Check for structural warnings.
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
                root_id=effective_root_id,
                section_path_text=section_path_text,
                segment=segment,
                warnings=warnings,
            )

    # 5.
    canonical_ir = CanonicalIR(
        decision_set_id=segment_decisions.decision_set_id,
        doc_key=doc_key,
        edges=edges,
        nodes=list(nodes_by_id.values()),
        pdf_name=segment_decisions.pdf_name,
        root_id=effective_root_id,
        segment_decisions=segment_decisions.decisions,
        unresolved=unresolved,
        warnings=warnings,
    )
    canonical_ir = perform_postpass_hygiene(
        canonical_ir=canonical_ir, document_ir=document_ir
    )

    logger.info(
        f"Compiled CanonicalIR:\n"
        f"nodes={len(canonical_ir.nodes)}\n"
        f"edges={len(canonical_ir.edges)}\n"
        f"unresolved={len(canonical_ir.unresolved)}\n"
        f"warnings={len(canonical_ir.warnings)}"
    )

    save_canonical_ir(canonical_ir=canonical_ir, canonical_ir_fp=canonical_ir_fp)

    logger.success(f"CanonicalIR compiled and saved to: {canonical_ir_fp}")


def ensure_node(
    *, node: CanonicalNode, nodes_by_id: dict[str, CanonicalNode], warnings: list[str]
) -> str:
    """Ensure a CanonicalNode is present in nodes_by_id.

    This function is the single insertion point for all CanonicalNode objects into the
    `nodes_by_id` registry. It handles three cases:

    Case A: New node (ID not seen before): Insert directly, `return node_id`.
    Case B: ID exists, semantics match: Merge provenance (page indices, segment IDs,
        decision IDs, section paths) into the existing node. Scalar fields use
        keep-first/fill-if-missing. Return the existing `node_id`.
    Case C: ID exists, semantics differ (collision): Deterministically disambiguate the
        new node's ID using provenance-derived salt, insert as a separate node, return
        the new ID.

    The reason this matters is that node IDs are deterministic hashes of (doc_key,
    role, ancestor_path_fingerprint, local_code, normalized_text_hash). Two different
    pieces of content can produce the same ID if their normalized text and ancestor
    context happen to collide. Case C handles that.

    Merge policy (when semantics match):

    1. Preserve first-seen ordering for all provenance lists.
    2. Merge: page_indices, source_segment_ids, source_decision_ids, section_path_text.
    3. Keep-first for core semantic fields; fill if missing.

    Examples
    --------
    1. Case B (merge, most common)
        Two different table rows in the same table both reference the grouping
        strand: "Communication orale" under the same ancestor path. The first row
        creates the node; the second row hits `ensure_node()` with an identical ID
        and identical normalized text. Result: provenance from the second row's
        segment/decision is merged into the existing node. No new node is created.

    2. Case B (fill-if-missing)
        A grouping node is first created from a `context_groupings` snapshot where
        `source_label` was None. A later decision creates the same grouping node
        but this time with `source_label="Sous-domaine"`. Since the existing node's
        `source_label` is None, the scalar fill-if-missing logic populates it. The
        node ID stays the same.

    3. Case C (collision)
        Suppose two genuinely different leaf statements happen to produce the same
        normalized text hash under the same ancestor path (extremely rare, but
        possible with short/generic text like "Lire" appearing in two different
        structural contexts that collapse to the same path fingerprint).
        `_detect_semantic_collision` finds that normalized_text differs
        (pre-normalization content is semantically different).
        `_resolve_collision` generates a new deterministic ID by hashing the
        original ID + provenance salt (segment ID, decision ID, page index), and
        inserts as a distinct node. The caller receives the new ID so edges point
        to the right place.

    4. Case B with formatting-only difference (no collision)
        Two decisions produce nodes with titles "Recognize letters." vs.
        "Recognize letters". After normalization (casefold + whitespace + dash +
        colon normalization), both produce the same normalized_text.
        `_detect_semantic_collision` returns False (no collision), a formatting
        warning is logged, and provenance is merged. This prevents duplicate
        canonical nodes from trivial OCR/extraction formatting drift.

    Parameters
    ----------
    node
        The CanonicalNode to ensure.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    warnings
        The list of warnings to append to.

    Returns
    -------
    str
        The effective node_id.
    """

    node = node.model_copy(deep=True)

    # NB: Callers already wrap text in canonical_storage_text() when constructing the
    # node. This second pass is intentionally redundant (idempotent) as a defensive
    # guarantee: ensure_node is the single chokepoint for all node insertion, so it
    # must normalize even if a future caller forgets to.
    if node.title is not None:
        node.title.text = canonical_storage_text(node.title.text)

    if node.body is not None:
        node.body.text = canonical_storage_text(node.body.text)

    if node.node_id not in nodes_by_id:
        nodes_by_id[node.node_id] = node
        return node.node_id

    existing_node = nodes_by_id[node.node_id]

    # If we detected a collision, deterministically disambiguate the node ID and insert.
    # NB: Return the effective `node_id` so callers emit edges to the correct node.
    if _detect_semantic_collision(
        existing_node=existing_node, node=node, warnings=warnings
    ):
        return _resolve_collision(node=node, nodes_by_id=nodes_by_id, warnings=warnings)

    # No collision: Semantics match -> merge provenance (preserve first-seen order).
    list_fields = (
        "page_indices",
        "source_segment_ids",
        "source_decision_ids",
        "section_path_text",
    )

    for field_ in list_fields:
        # Update existing in-place.
        setattr(
            existing_node,
            field_,
            _stable_extend_unique(
                base=getattr(existing_node, field_), extra=getattr(node, field_)
            ),
        )

    # page_indices must stay sorted (other list fields preserve first-seen order).
    existing_node.page_indices = sorted(set(existing_node.page_indices))

    # Keep-first semantics, fill missing values if present.
    scalar_fields = (
        "normalized_text",
        "source_label",
        "source_type",
        "title",
        "body",
        "local_code",
        "list_marker",
        "bbox",
    )

    for field_ in scalar_fields:
        # Only overwrite if existing is None. If `node.field` is also None, this is a
        # harmless no-op.
        if getattr(existing_node, field_) is None:
            setattr(existing_node, field_, getattr(node, field_))

    return existing_node.node_id


def normalize_local_code(code: str) -> str:
    """Normalize a local code string deterministically.

    Parameters
    ----------
    code
        The local code to normalize.

    Returns
    -------
    str
        The normalized local code.
    """

    if code == "-":
        return code

    code = unicodedata.normalize("NFKC", code)
    code = DASH_RE.sub("-", code)
    code = WS_RE.sub(" ", code).strip()
    return code


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

    keys = list(grouping_keys)

    if not keys:
        return "-"

    # JSON encoding avoids delimiter ambiguity (e.g., ['a>b','c'] vs. ['a','b>c']).
    payload = json.dumps(keys, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode(encoding)).hexdigest()[:32]


def perform_postpass_hygiene(
    *, canonical_ir: CanonicalIR, document_ir: DocumentIR
) -> CanonicalIR:
    """Perform post-pass hygiene on a CanonicalIR.

    The process is as follows:

    1. Prune empty grouping containers
    2. Prune nodes/edges not reachable from root
    3. Reindex `order_index` under each parent (remove gaps after pruning)
    4. Perform sanity checks
    5. Add `columns_signature` from the document IR to `segment_decisions` for audit
        and debugging purposes.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to process.
    document_ir
        The DocumentIR (for columns_signature lookup).

    Returns
    -------
    CanonicalIR
        An updated CanonicalIR.
    """

    warnings = list(canonical_ir.warnings)

    # 1.
    nodes_pruned_empty, edges_pruned_empty = prune_empty_groupings(
        edges=canonical_ir.edges,
        nodes=canonical_ir.nodes,
        # prune_roles={NodeRole.PROSE, NodeRole.SECTION},
        prune_roles=None,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    # 2.
    nodes_pruned_reachable, edges_pruned_reachable = prune_unreachable_nodes(
        edges=edges_pruned_empty,
        nodes=nodes_pruned_empty,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    # 3.
    edges_reindexed = reindex_order_indices_postpass(
        edges=edges_pruned_reachable, warnings=warnings
    )

    # 4.
    sanity_checks_postpass(
        edges=edges_reindexed,
        nodes=nodes_pruned_reachable,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    # 5.
    updated_decisions = apply_table_signatures(
        decisions=canonical_ir.segment_decisions, document_ir=document_ir
    )

    return canonical_ir.model_copy(
        update={
            "nodes": nodes_pruned_reachable,
            "edges": edges_reindexed,
            "segment_decisions": updated_decisions,
            "warnings": warnings,
        }
    )


def prune_empty_groupings(
    *,
    edges: list[CanonicalEdge],
    nodes: list[CanonicalNode],
    prune_roles: set[NodeRole] | None = None,
    root_id: str,
    warnings: list[str],
) -> tuple[list[CanonicalNode], list[CanonicalEdge]]:
    """Remove grouping nodes (NodeRole) that have *zero* outgoing children.

    This is an iterative bottom-up prune:

    1. If a grouping has no children, drop it.
    2. After dropping, its parent may become empty -> repeat until stable.
    3. Root is never pruned.

    This **should** be safe because:

    1. It only removes empty containers (no standards/leaves underneath).
    2. It makes the CanonicalIR cleaner for export (Step 5).

    Parameters
    ----------
    edges
        The list of CanonicalEdges.
    nodes
        The list of CanonicalNodes.
    prune_roles
        The set of NodeRoles to prune. If None, all grouping roles are pruned.
    root_id
        The root node ID.
    warnings
        The list of warning messages to append to.

    Returns
    -------
    tuple[list[CanonicalNode], list[CanonicalEdge]]
        The (pruned_nodes, pruned_edges).
    """

    # Build lookup: node_id -> CanonicalNode.
    nodes_by_id = {n.node_id: n for n in nodes}

    removed_total = 0

    while True:
        # Compute out-degree (children count) for each parent.
        out_degree: dict[str, int] = {nid: 0 for nid in nodes_by_id.keys()}

        for e in edges:
            # CanonicalEdge.rel is always "hasChild", but keep this check just in case.
            if e.rel == "hasChild" and e.parent_id in out_degree:
                out_degree[e.parent_id] += 1

        # Identify empty grouping nodes (0 children), excluding root.
        prunable_ids: set[str] = set()

        for nid, node in nodes_by_id.items():
            if nid == root_id:
                continue

            if (
                isinstance(node.role, NodeRole)
                and (prune_roles is None or node.role in prune_roles)
                and out_degree.get(nid, 0) == 0
            ):
                prunable_ids.add(nid)

        if not prunable_ids:
            break

        removed_total += len(prunable_ids)

        # Remove nodes.
        for nid in prunable_ids:
            nodes_by_id.pop(nid, None)

        # Remove any edges touching removed nodes (either as parent or child).
        edges = [
            e
            for e in edges
            if e.parent_id not in prunable_ids and e.child_id not in prunable_ids
        ]

    if removed_total > 0:
        msg = f"empty_groupings_pruned:count={removed_total}"
        logger.warning(msg)
        warnings.append(msg)

    return list(nodes_by_id.values()), edges


def prune_unreachable_nodes(
    *,
    edges: list[CanonicalEdge],
    nodes: list[CanonicalNode],
    root_id: str,
    warnings: list[str],
) -> tuple[list[CanonicalNode], list[CanonicalEdge]]:
    """Drop any nodes/edges that are not reachable from the CanonicalIR root.

    This removes orphan "islands" created by:

    1. Inconsistent context snapshots
    2. Dropped parent edges from keep-first-parent enforcement
    3. Partial materialization/unresolved segments

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

    Returns
    -------
    tuple[list[CanonicalNode], list[CanonicalEdge]]
        The (pruned_nodes, pruned_edges).
    """

    nodes_by_id = {n.node_id: n for n in nodes}

    # Build adjacency list: parent -> children.
    children_by_parent: dict[str, list[str]] = {}

    for e in edges:
        if e.rel != "hasChild":
            continue

        children_by_parent.setdefault(e.parent_id, []).append(e.child_id)

    # Traverse from root and mark reachable nodes.
    reachable: set[str] = set()
    stack: list[str] = [root_id]

    while stack:
        nid = stack.pop()

        if nid in reachable:
            continue

        reachable.add(nid)

        for child_id in children_by_parent.get(nid, []):
            # Only traverse into nodes that actually exist.
            if child_id in nodes_by_id and child_id not in reachable:
                stack.append(child_id)

    # Filter nodes + edges to reachable set.
    nodes_pruned = [n for n in nodes if n.node_id in reachable]
    edges_pruned = [
        e for e in edges if e.parent_id in reachable and e.child_id in reachable
    ]

    removed_nodes = len(nodes) - len(nodes_pruned)
    removed_edges = len(edges) - len(edges_pruned)

    if removed_nodes > 0 or removed_edges > 0:
        msg = f"unreachable_pruned:nodes={removed_nodes},edges={removed_edges}"
        logger.warning(msg)
        warnings.append(msg)

    return nodes_pruned, edges_pruned


def reconcile_context_stack(
    *,
    active_stack: list[ContextFrame],
    child_to_parent: dict[str, str],
    decision: SegmentDecision,
    desired_context_groupings: list[GroupingDecision],
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
    next_order_index: dict[str, int],
    nodes_by_id: dict[str, CanonicalNode],
    page_indices: list[int],
    root_id: str,
    segment: Segment,
    warnings: list[str],
) -> tuple[str, list[str], list[ContextFrame]]:
    """Reconcile the current active context stack to match the desired context by
    enforcing context snapshot exactly per decision and reusing nodes via deterministic
    IDs.

    This function makes the compiler's current hierarchical position match the
    SegmentDecision's desired context grouping exactly, before any new
    `decision.groupings`, leaves, or table rows are emitted.
    `_materialize_decision_structure()` calls it first, then uses the returned
    `parent_id` and `ancestor_keys` as the base location for whatever that decision
    emits next.

    NB:

    1. `active_stack` excludes the framework root.
    2. `ContextFrame` only stores two things:
        1. grouping_key: A stable semantic key for the grouping
        2. node_id: The actual canonical node already representing that grouping in the
            graph.
    3. This function only handles `decision.context_groupings`. It does not handle
        `decision.groupings`. Those are applied afterward in
        `_materialize_decision_structure()` under the returned `parent_id`.
        `context_groupings` = where this decision lives. `groupings` = new nodes this
        decision itself emits.

    Examples
    --------
    1. Staying on the same branch
        Suppose the current active stack is:

        section = Planification
        strand = Lecture
        week = Semaine 3

        and the next decision has the exact same context_groupings.

        Then:

        * desired_keys equals the current stack keys
        * lcp = 3
        * new_stack = active_stack[:3] so nothing is popped
        * there are no missing frames to push
        * return current week node as parent_id

        Effect: no structural graph changes happen inside reconciliation. It just
        confirms “we are already in the right place.”

    2. Moving to a sibling week under the same strand
        Current stack:

        section = Planification
        strand = Lecture
        week = Semaine 3

        Desired context:

        section = Planification
        strand = Lecture
        week = Semaine 4

        Then:

        * longest common prefix is [Planification, Lecture]
        * lcp = 2
        * pop Semaine 3
        * current parent becomes the Lecture node
        * push Semaine 4
        * return the Semaine 4 node as parent_id

        Effect: the compiler cleanly branches from one week sibling to the next without
        carrying stale week context forward.

    3. Moving across strands in the same section
        Current stack:

        section = Planification
        strand = Lecture
        week = Semaine 4

        Desired context:

        section = Planification
        strand = Récitation
        week = Semaine 1

        Then:

        * longest common prefix is just [Planification]
        * pop Lecture and Semaine 4
        * current parent becomes Planification
        * push Récitation
        * push Semaine 1
        * return Semaine 1

        Effect: it does not “move sideways” from under Lecture; it first backs out to
        the shared ancestor, then rebuilds the target branch correctly.

    4. Dropping all the way back to root
        Current stack:

        section = Strand Oral
        substage = Palier 2

        Desired context:

        empty list

        Then:

        * desired_keys = []
        * lcp = 0
        * new_stack = []
        * parent_id = root_id
        * nothing is pushed

        Effect: the next materialized content will attach directly under the framework
        root unless `_materialize_decision_structure()` then adds `decision.groupings`.
        This is how the compiler avoids stale structural carry-over when a decision
        intentionally has no desired context groupings.

    5. Adding an implicit container that is not currently on the stack
        Suppose translation emitted context groupings:

        section = Communication écrite
        substage = Palier 1

        but the current stack is only:

        section = Communication écrite

        Then:

        * lcp = 1
        * nothing gets popped
        * it pushes the missing Palier 1 grouping
        * returns that as the current parent

        Effect: This is one way implicit structural nodes from the translated decision
        snapshot become actual canonical grouping nodes during compilation.

    Parameters
    ----------
    active_stack
        The current active context stack.
    child_to_parent
        The mapping of child_id to parent_id for emitted edges.
    decision
        The SegmentDecision being processed.
    desired_context_groupings
        The desired context groupings snapshot from the SegmentDecision.
    doc_key
        The document key.
    edges
        The list of CanonicalEdges to append new edges to.
    edges_by_key
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
    next_order_index
        The mapping of parent_id to next order index for child edges.
    nodes_by_id
        The mapping of node_id to CanonicalNode.
    page_indices
        The sorted, deduplicated page indices for the current segment (precomputed by
        the caller to avoid redundant derivation from segment.segment_provenance).
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

    # 1. Convert each desired context grouping into a stable normalized key before
    # comparing anything. `_grouping_key()` is role-aware and based on grouping role,
    # canonicalized grouping title, and normalized local code (if present). So the
    # comparison is semantic, not pointer-based.
    desired_keys = [_grouping_key(g) for g in desired_context_groupings]

    section_path_text = [h.text for h in segment.section_path]
    seg_id = segment.segment_id
    seg_kind = segment.kind

    # 2. Longest common prefix between active_stack keys and desired_keys. This is the
    # main reconciliation step. It walks `active_stack` and `desired_keys` from the
    # front until they diverge, storing the length in `lcp`. For example, if the
    # current stack is [Grade 1, Math, Numbers] and the desired stack is
    # [Grade 1, Math, Geometry], then the longest common prefix is [Grade 1, Math] and
    # lcp = 2. This is how the function decides what can be reused and where it needs
    # to branch.
    lcp = 0

    while lcp < len(active_stack) and lcp < len(desired_keys):
        if active_stack[lcp].grouping_key != desired_keys[lcp]:
            break

        lcp += 1

    # 3. Pop extra frames (enforce desired context grouping exactly). Basically, this
    # just says "for this decision, forget any deeper context beyond the shared prefix."
    # If `new_stack` is empty after the pop, then `parent_id = root_id` and
    # `ancestor_keys = []`. Otherwise, we derive the new parent and ancestor keys from
    # the truncated stack. So after the pop, the compiler knows which node is now the
    # current parent and which ancestor semantic path should be used when hashing any
    # new grouping nodes.
    new_stack = active_stack[:lcp]

    # Determine current parent and ancestor keys after pop.
    if not new_stack:
        ancestor_keys: list[str] = []
        parent_id = root_id
    else:
        ancestor_keys = [f.grouping_key for f in new_stack]
        parent_id = new_stack[-1].node_id

    # 4. Push missing frames by looping over the desired context grouping entries that
    # were not already in the longest common prefix.
    for g in desired_context_groupings[lcp:]:
        # `node_id` depends on the document and the path leading to that grouping. This
        # is what allows reuse of the right grouping node and avoids conflating
        # same-named groupings in different branches.
        node_id = canonical_grouping_node_id(
            ancestor_grouping_keys=ancestor_keys, doc_key=doc_key, grouping=g
        )
        g_title = canonical_grouping_title(role=g.role, title=g.title)

        # Build the CanonicalNode object using the canonicalized node ID and grouping
        # title.
        node = CanonicalNode(
            bbox=_segment_first_bbox(segment),
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
            title=TextUnit(language="und", text=canonical_storage_text(g_title)),
        )

        # `ensure_node()` will ensure that if this grouping node already exists under
        # the same deterministic identity, it will get reused/merged. Otherwise, it
        # gets inserted. So pushing a context frame is not necessarily creating a new
        # graph node every time. Often, it just resolves to an existing one.
        effective_node_id = ensure_node(
            node=node, nodes_by_id=nodes_by_id, warnings=warnings
        )

        # Now, we emit a `hasChild` edge from the current `parent_id` to the effective
        # grouping node. `_emit_edge()` will enforce a keep-first-parent tree policy
        # and dedupe edges.
        _emit_edge(
            child_id=effective_node_id,
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
        next_grouping_key = _grouping_key(g)
        new_stack.append(
            ContextFrame(grouping_key=next_grouping_key, node_id=effective_node_id)
        )
        parent_id = effective_node_id
        ancestor_keys.append(next_grouping_key)

    # After reconciliation, new_stack should EXACTLY match desired_context snapshot.
    return parent_id, ancestor_keys, new_stack


def reindex_order_indices_postpass(
    *, edges: list[CanonicalEdge], warnings: list[str]
) -> list[CanonicalEdge]:
    """Reindex sibling `order_index` values so they are contiguous 0...N-1 under each
    parent.

    The process is as follows:

    1. For each parent, sort children by (existing order_index, child_id).
    2. Rewrite order_index based on that deterministic order.

    Parameters
    ----------
    edges
        The list of CanonicalEdges.
    warnings
        The list of warning messages to append to.

    Returns
    -------
    list[CanonicalEdge]
        The updated list of CanonicalEdges with reindexed order_index values.
    """

    # Group edges by parent.
    edges_by_parent: dict[str, list["CanonicalEdge"]] = {}

    for e in edges:
        if e.rel != "hasChild":
            continue

        edges_by_parent.setdefault(e.parent_id, []).append(e)

    num_reindexed = 0

    for parent_edges in edges_by_parent.values():
        # Sort deterministically:
        #   - Primary: existing order_index (None treated as large number)
        #   - Secondary: child_id to break ties deterministically
        parent_edges_sorted = sorted(
            parent_edges,
            key=lambda e: (
                e.order_index if e.order_index is not None else 10**9,
                e.child_id,
            ),
        )

        # Rewrite order_index to be contiguous.
        for new_idx, e in enumerate(parent_edges_sorted):
            if e.order_index != new_idx:
                num_reindexed += 1
                e.order_index = new_idx

    if num_reindexed > 0:
        msg = f"order_index_reindexed:edges_updated={num_reindexed}"
        logger.warning(msg)
        warnings.append(msg)

    return edges


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
