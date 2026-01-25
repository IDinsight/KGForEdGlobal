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
from graveyard.for_upload.schemas import RowDecision
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    CanonicalEdge,
    CanonicalIR,
    CanonicalNode,
    GroupingCanonicalizationKey,
    GroupingCanonicalizationMap,
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
from skg.schemas import BBox, CreateCanonicalConfig, RunCtx
from skg.utils.constants import (
    CONTEXT_GROUPINGS_ROLE_PRECEDENCE,
    GroupingCanonicalizationAction,
    NodeRole,
    SegmentDecisionType,
    UnresolvedReason,
)
from skg.utils.general import (
    QUOTES_TRANSLATION,
    make_dir,
    open_json_type,
    write_to_json,
)

T = TypeVar("T")

# Compiled regexes.
_DASH_RE = re.compile(r"[‐-‒–—−]+")
_STRUCTURAL_CONTEXT_CUE_RE = re.compile(
    r"\b("
    r"grade|class|primary|standard|std\.?|stage|theme|sub[-\s]?theme|strand|subject|"
    r"learning\s+area|unit|week|term|chapter|module|p\s*[1-9]|std\s*[ivx]+"
    r")\b",
    flags=re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path


@dataclass(frozen=True)
class ContextFrame:
    """One frame in the active context stack (excluding the framework root)."""

    grouping_key: str
    node_id: str


def _gkey_tuple(
    *,
    role: NodeRole,
    title: str,
    local_code: Optional[str],
    source_label: Optional[str],
) -> tuple[str, str, str, str]:
    """Create a grouping key tuple.

    Parameters
    ----------
    role
        The NodeRole of the grouping.
    title
        The title of the grouping.
    local_code
        The local code of the grouping.
    source_label
        The source label of the grouping.

    Returns
    -------
    tuple[str, str, str, str]
        The grouping key tuple.
    """

    return role.value, title, local_code or "", source_label or ""


def _build_mapping_index(
    *, canonical_grouping_min_confidence: float, mapping: GroupingCanonicalizationMap
) -> dict[
    tuple[str, str, str, str],
    GroupingCanonicalizationKey | list[GroupingCanonicalizationKey] | None,
]:
    """Build a fast lookup index for grouping canonicalization mapping.

    Parameters
    ----------
    canonical_grouping_min_confidence
        The minimum confidence threshold for applying mapping actions.
    mapping
        The GroupingCanonicalizationMap to index.

    Returns
    -------
    dict[
        tuple[str, str, str, str],
        GroupingCanonicalizationKey | list[GroupingCanonicalizationKey] | None,
    ]
        An index as follows:
          - None means DROP
          - GroupingKey means REPLACE (1)
          - list[GroupingKey] means SPLIT (2+)
          - Missing entry means KEEP
    """

    index: dict[
        tuple[str, str, str, str],
        GroupingCanonicalizationKey | list[GroupingCanonicalizationKey] | None,
    ] = {}

    for item in mapping.items:
        if item.confidence < canonical_grouping_min_confidence:
            continue  # Treat as KEEP

        inp = item.input
        k = _gkey_tuple(
            local_code=inp.local_code,
            role=inp.role,
            source_label=inp.source_label,
            title=inp.title,
        )

        if item.action == GroupingCanonicalizationAction.DROP:
            index[k] = None
        elif item.action == GroupingCanonicalizationAction.KEEP:
            # not stored -> interpreted as KEEP
            continue
        elif item.action == GroupingCanonicalizationAction.REPLACE:
            index[k] = item.output[0]
        elif item.action == GroupingCanonicalizationAction.SPLIT:
            index[k] = item.output
        else:
            continue

    return index


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
    # but the LLM provided an empty context_groupings snapshot. This does NOT
    # materialize any hierarchy; it only flags likely missed context.
    leaf_count = _count_decision_leaves(decision)

    if (
        decision.decision_type
        not in (SegmentDecisionType.IGNORE, SegmentDecisionType.UNRESOLVED)
        and leaf_count > 0
        and not decision.context_groupings
        and section_path_text
    ):
        path_str = " / ".join([p for p in section_path_text if p])
        if path_str and _STRUCTURAL_CONTEXT_CUE_RE.search(path_str):
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


def _clean_decision_rows(rows: list[RowDecision]) -> list[RowDecision]:
    """Clean a list of decision rows.

    Parameters
    ----------
    rows
        The list of SegmentDecisionRow to clean.

    Returns
    -------
    list[RowDecision]
        The cleaned list of decision rows.
    """

    new_rows = []

    for r in rows:
        row_updates = {}

        if r.groupings:
            row_updates["groupings"] = [_clean_grouping(g) for g in r.groupings]

        if r.leaves:
            row_updates["leaves"] = [_clean_leaf(leaf) for leaf in r.leaves]

        if row_updates:
            new_rows.append(r.model_copy(update=row_updates))
        else:
            new_rows.append(r)

    return new_rows


def _clean_grouping(g: GroupingDecision) -> GroupingDecision:
    """Apply cleaning to GroupingDecision fields.

    Parameters
    ----------
    g
        The GroupingDecision to clean.

    Returns
    -------
    GroupingDecision
        The cleaned GroupingDecision.
    """

    title = _clean_text(g.title) or g.title.strip()

    # NB: Do NOT apply casing transformations here. SegmentDecision text should be
    # stored as close to verbatim as possible.
    return g.model_copy(
        update={
            "local_code": _clean_text(g.local_code),
            "source_label": _clean_text(g.source_label),
            "title": title,
        }
    )


def _clean_leaf(leaf: LeafDecision) -> LeafDecision:
    """Apply deterministic hygiene to LeafDecision.

    Parameters
    ----------
    leaf
        The LeafDecision to clean.

    Returns
    -------
    LeafDecision
        The cleaned LeafDecision.
    """

    updates = {}

    if getattr(leaf, "body", None):
        updates["body"] = _normalize_leaf_body(leaf.body)

    # Optional: normalize list_marker whitespace if it exists.
    if getattr(leaf, "list_marker", None):
        updates["list_marker"] = re.sub(r"\s+", " ", leaf.list_marker).strip()

    return leaf.model_copy(update=updates) if updates else leaf


def _clean_text(text: Optional[str]) -> Optional[str]:
    """Clean text as follows:

    1. NFKC unicode normalization
    2. Normalize curly quotes to ASCII
    3. Unify dash variants to '-'
    4. Collapse whitespace
    5. Strip

    NB: Do not include curriculum-specific heuristics here.

    Parameters
    ----------
    text
        The text to clean.

    Returns
    -------
    Optional[str]
        The cleaned text, or None if input was None or normalized to empty.
    """

    if text is None:
        return None

    t = unicodedata.normalize("NFKC", text)
    t = t.translate(QUOTES_TRANSLATION)
    t = _DASH_RE.sub("-", t)
    t = _WS_RE.sub(" ", t).strip()

    # Preserve None for optional fields when they normalize to empty.
    return t or None


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


def _dedupe_preserve_order(groupings: list[GroupingDecision]) -> list[GroupingDecision]:
    """Deduplicate GroupingDecisions while preserving order.

    Parameters
    ----------
    groupings
        The list of GroupingDecisions to deduplicate.

    Returns
    -------
    list[GroupingDecision]
        The deduplicated list of GroupingDecisions.
    """

    output: list[GroupingDecision] = []
    seen: set[tuple[str, str, str, str]] = set()

    for g in groupings:
        key = _gkey_tuple(
            local_code=g.local_code,
            role=g.role,
            source_label=g.source_label,
            title=g.title,
        )

        if key in seen:
            continue

        output.append(g)
        seen.add(key)

    return output


def _detect_semantic_collision(
    *, existing: CanonicalNode, node: CanonicalNode, warnings: list[str]
) -> bool:
    """Check for semantic conflicts between an existing node and a new node.

    NB:

    1. Node IDs are derived from *normalized* semantics (role + normalized title/body
        + ancestor fingerprint + optional local_code). Therefore we only treat this as
        a true collision when the normalized semantics differ.
    2. Formatting-only differences (casing, punctuation, whitespace) should *not*
        trigger collision handling because that creates duplicate canonical nodes.

    Examples:

    1. Without normalization, these would be treated as collisions (causing
        duplicates):
        - "Recognize letters." vs "Recognize letters"
        - "Add and subtract" vs "add and subtract"
        - bullet formatting differences
        - small punctuation/whitespace changes
    2. With normalization, only true semantic differences trigger collision handling:
        - Normalized meaning differs (casefold + whitespace + dash normalize differs)

    Parameters
    ----------
    existing
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
    if existing.role != node.role:
        msg = (
            f"node_semantic_conflict_same_id_different_role:"
            f"node_id={node.node_id} existing_role={existing.role} new_role={node.role} "
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return True

    def _semantic_norm_text(n: CanonicalNode) -> str:
        """Return the normalized semantic text that should define this node.

        Parameters
        ----------
        n
            The CanonicalNode to extract normalized semantic text from.

        Returns
        -------
        str
            The normalized semantic text.
        """

        # Prefer stored normalized_text (debug snapshot).
        if n.normalized_text:
            return n.normalized_text

        # Otherwise derive it deterministically from the available text.
        if n.title is not None and n.title.text:
            return _normalize_text(n.title.text)

        if n.body is not None and n.body.text:
            return _normalize_text(n.body.text)

        return ""

    existing_norm = _semantic_norm_text(existing)
    new_norm = _semantic_norm_text(node)

    # True semantic mismatch (same deterministic ID but different normalized meaning).
    if existing_norm and new_norm and existing_norm != new_norm:
        msg = (
            f"node_semantic_conflict_same_id_different_normalized_text:"
            f"node_id={node.node_id} existing_norm={existing_norm!r} new_norm={new_norm!r} "
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)
        collision = True

    # Formatting-only mismatches: warn, but do NOT mark as collision.
    existing_title = existing.title.text if existing.title is not None else None
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
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    existing_body = existing.body.text if existing.body is not None else None
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
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    # Local code mismatch is treated as provenance/metadata drift, not a true collision.
    if (
        existing.local_code
        and node.local_code
        and existing.local_code.strip() != node.local_code.strip()
    ):
        msg = (
            f"node_local_code_diff_same_id_semantics_equal:"
            f"node_id={node.node_id} existing_local_code={existing.local_code!r} "
            f"new_local_code={node.local_code!r} "
            f"existing_src_segments={existing.source_segment_ids} new_src_segments={node.source_segment_ids}"
        )
        logger.warning(msg)
        warnings.append(msg)

    return collision


def _drop_duplicate_roles_keep_first(
    groupings: list[GroupingDecision],
) -> list[GroupingDecision]:
    """Drop duplicate roles, keeping the first occurrence.

    Parameters
    ----------
    groupings
        The list of GroupingDecisions to process.

    Returns
    -------
    list[GroupingDecision]
        The filtered list of GroupingDecisions.
    """

    output: list[GroupingDecision] = []
    seen_roles: set[str] = set()

    for g in groupings:
        role_value = g.role.value

        if role_value in seen_roles:
            continue

        output.append(g)
        seen_roles.add(role_value)

    return output


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


def _iter_all_grouping_decisions(
    decision_set: SegmentDecisionSet,
) -> Iterable[GroupingDecision]:
    """Yield every GroupingDecision present anywhere in the decision set:

    1. context_groupings
    2. segment-level groupings
    3. row-level groupings

    Parameters
    ----------
    decision_set
        The SegmentDecisionSet to iterate over.

    Yields
    -------
    GroupingDecision
        The next GroupingDecision.
    """

    for d in decision_set.decisions:
        if d.context_groupings:
            yield from d.context_groupings

        if d.groupings:
            yield from d.groupings

        if d.rows:
            for r in d.rows:
                if r.groupings:
                    yield from r.groupings


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

    if isinstance(segment, BlockSegment):
        segment_text = _extract_block_segment_text(segment)
    else:
        # Fallback: best-effort (mostly useful for some segment variants)
        text_or_none = getattr(segment, "text", None)
        segment_text = text_or_none.text if isinstance(text_or_none, TextUnit) else None

    if segment_text and segment_text.strip():
        parts.append(segment_text.strip())
    # For tables, we often don't have a clean "text" field; header preview can help
    # debugging.
    elif isinstance(segment, TableSegment):
        headers = _extract_table_headers(segment)
        if headers:
            parts.append("headers=" + " | ".join(headers[:8]))

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
    segment_bbox: Optional[BBox],
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
    segment_bbox
        The segment bounding box.
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
            source_type="block",
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

    NB: For tables, a table decision may contain both decision.leaves[] (segment-level
    statements) OR decision.rows[] (row-wise statements). In these cases, emitting
    leaves first makes them appear first in deterministic order_index order under that
    parent. If we wanted to emit rows first, then we can just switch the if-statements.

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

        parent_id = effective_node_id
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
            segment_bbox=_segment_first_bbox(segment),
            segment_id=segment.segment_id,
            warnings=warnings,
        )
    elif segment.kind == "table":
        if not decision.leaves and not decision.rows:
            msg = f"table_decision_emits_nothing:{segment.segment_id}:{decision.decision_id}"
            logger.warning(msg)
            warnings.append(msg)

        # Table decisions may emit leaves directly.
        if decision.leaves:
            _materialize_table_leaves(
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
                warnings=warnings,
            )

        # Preferred: row-level decisions.
        if decision.rows:
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


def _materialize_table_leaves(
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
    segment_bbox: Optional[BBox],
    segment_id: str,
    warnings: list[str],
) -> None:
    """Materialize table-level leaves (SegmentDecision.leaves) under the current
    parent. This is used for TABLE segments where the LLM emits leaves directly
    (fallback mode) instead of emitting RowDecision entries in SegmentDecision.rows[].

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
    segment_bbox
        The segment bounding box.
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
            source_type="table",  # IMPORTANT: keep provenance correct
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
    table_segment
        The TableSegment to materialize rows for.
    warnings
        The list of warnings to append to.
    """

    for row in sorted(decision.rows, key=lambda r: r.row_index):
        row_ancestor_keys = list(ancestor_keys)
        row_bbox = _table_row_bbox(row_index=row.row_index, table_segment=table_segment)
        row_parent_id = parent_id

        # Row groupings.
        for g in row.groupings:
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
                source_decision_ids=[decision.decision_id],
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
                decision_id=decision.decision_id,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                parent_id=row_parent_id,
                segment_id=segment_id,
                warnings=warnings,
            )

            row_parent_id = effective_node_id
            row_ancestor_keys.append(_grouping_key(g))

        # Row leaves.
        for leaf in row.leaves:
            leaf_id = canonical_leaf_node_id(
                ancestor_grouping_keys=row_ancestor_keys, doc_key=doc_key, leaf=leaf
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
                source_decision_ids=[decision.decision_id],
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
                decision_id=decision.decision_id,
                edges=edges,
                edges_by_key=edges_by_key,
                next_order_index=next_order_index,
                parent_id=row_parent_id,
                segment_id=segment_id,
                warnings=warnings,
            )


def _normalize_leaf_body(body: str) -> str:
    """Normalize statement text. The goal here is to remove purely formatting-based
    diffs that shouldn't create merge warnings.

    The following are considered safe operations:

    1. Unicode normalize (NFKC)
    2. Normalize quotes/dashes
    3. Collapse whitespace
    4. Normalize spacing after ":" (no capitalization changes)

    Parameters
    ----------
    body
        The leaf body text to normalize.

    Returns
    -------
    str
        The normalized leaf body text.
    """

    s = unicodedata.normalize("NFKC", body or "")
    s = s.translate(QUOTES_TRANSLATION)
    s = _DASH_RE.sub("-", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Normalize colon spacing ONLY when a non-space follows the colon.
    # Examples:
    #   "Skills:use blends"   -> "Skills: use blends"
    #   "Skills:  use blends" -> "Skills: use blends"
    s = re.sub(r":\s*(?=\S)", ": ", s)
    s = re.sub(r"\s+", " ", s).strip()

    return s


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


def _resolve_collision(
    *, node: CanonicalNode, nodes_by_id: dict[str, CanonicalNode], warnings: list[str]
) -> str:
    """Disambiguate a node ID using provenance data and inserts it as a new node. Used
    when a semantic collision is detected. Updates node.node_id in place.

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


def _rewrite_grouping_list(
    *,
    enforce_unique_roles: bool,
    groupings: list[GroupingDecision] | None,
    mapping_index: dict[
        tuple[str, str, str, str],
        GroupingCanonicalizationKey | list[GroupingCanonicalizationKey] | None,
    ],
    sort_by_precedence: bool,
) -> list[GroupingDecision] | None:
    """Rewrite a list of GroupingDecisions using a mapping index.

    Parameters
    ----------
    enforce_unique_roles
        Whether to enforce unique roles in the output list.
    groupings
        The list of GroupingDecisions to rewrite.
    mapping_index
        The mapping index for rewriting groupings.
    sort_by_precedence
        Whether to sort the output list by context precedence.

    Returns
    -------
    list[GroupingDecision] | None
        The rewritten list of GroupingDecisions.
    """

    if not groupings:
        return groupings

    rewritten: list[GroupingDecision] = []

    for g in groupings:
        key = _gkey_tuple(
            local_code=g.local_code,
            role=g.role,
            source_label=g.source_label,
            title=g.title,
        )

        if key not in mapping_index:
            rewritten.append(g)
            continue

        repl = mapping_index[key]

        if repl is None:  # Drop
            continue

        if isinstance(repl, list):  # Split
            rewritten.extend(
                [
                    GroupingDecision(
                        local_code=k.local_code,
                        role=k.role,
                        source_label=k.source_label,
                        title=k.title,
                    )
                    for k in repl
                ]
            )
        else:  # Replace
            rewritten.append(
                GroupingDecision(
                    local_code=repl.local_code,
                    role=repl.role,
                    source_label=repl.source_label,
                    title=repl.title,
                )
            )

    # Deduplicate while preserving order.
    rewritten = _dedupe_preserve_order(rewritten)

    if enforce_unique_roles:
        rewritten = _drop_duplicate_roles_keep_first(rewritten)

    if sort_by_precedence:
        rewritten = _sort_by_context_precedence(rewritten)

    return rewritten


def _segment_first_bbox(segment: Segment) -> Optional[BBox]:
    """Best-effort bbox for a segment.

    NB: Segments can span pages; bboxes are page-local, so we only take the first
    provenance bbox (deterministic + meaningful for debugging).

    Parameters
    ----------
    segment
        The Segment to extract the bbox from.

    Returns
    -------
    Optional[BBox]
        The first BBox if available, else None.
    """

    return segment.segment_provenance[0].bbox if segment.segment_provenance else None


def _sort_by_context_precedence(
    groupings: list[GroupingDecision],
) -> list[GroupingDecision]:
    """Sort groupings by the global precedence order used for context_groupings.
    Unknown roles fall to the end deterministically.

    Parameters
    ----------
    groupings
        The list of GroupingDecisions to sort.

    Returns
    -------
    list[GroupingDecision]
        The sorted list of GroupingDecisions.
    """

    def key_fn(g: GroupingDecision) -> tuple[int, str]:
        """Key function for sorting groupings by precedence and title.

        Parameters
        ----------
        g
            The GroupingDecision to generate a sort key for.

        Returns
        -------
        tuple[int, str]
            The sort key as (precedence, title).
        """

        return CONTEXT_GROUPINGS_ROLE_PRECEDENCE.get(g.role, 10_000), g.title

    return sorted(groupings, key=key_fn)


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


def _table_row_bbox(*, row_index: int, table_segment: TableSegment) -> Optional[BBox]:
    """Best-effort bbox for a stitched table row.

    Parameters
    ----------
    row_index
        The row index to extract the bbox for.
    table_segment
        The TableSegment to extract the row bbox from.

    Returns
    -------
    Optional[BBox]
        The BBox for the row if available, else None.
    """

    if table_segment.row_provenance and 0 <= row_index < len(
        table_segment.row_provenance
    ):
        rp = table_segment.row_provenance[row_index]

        return rp.row_bbox or rp.bbox

    return None


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
        True if the decision is materializable, False otherwise.
    """

    if decision.decision_type == SegmentDecisionType.IGNORE:
        return False

    # SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED is a "review" decision. It must be
    # persisted to the audit trail, but MUST NOT be materialized into CanonicalIR
    # nodes/edges.
    if decision.decision_type == SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED:
        msg = (
            f"flagged_unresolved_decision_not_materialized:"
            f"segment_id={segment.segment_id} decision_id={decision.decision_id} "
            f"kind={segment.kind} conf={decision.confidence:.3f}"
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
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    # Confidence gating.
    if decision.confidence < segment_decision_conf_threshold:
        msg = (
            f"low_confidence_decision_not_materialized:"
            f"segment_id={segment.segment_id} decision_id={decision.decision_id} "
            f"kind={segment.kind} conf={decision.confidence:.3f} "
            f"threshold={segment_decision_conf_threshold:.3f}"
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


def apply_grouping_canonicalization_map(
    *,
    canonical_grouping_min_confidence: float,
    creation_dirs: CanonicalIRDirs,
    mapping: GroupingCanonicalizationMap,
    overwrite: bool,
    segment_decisions: SegmentDecisionSet,
) -> SegmentDecisionSet:
    """Deterministically apply a GroupingCanonicalizationMap to all grouping lists in a
    decision set.

    Applies to:

    1. SegmentDecision.context_groupings
    2. SegmentDecision.groupings
    3. RowDecision.groupings

    The mapping is as follows:

    1. KEEP: no-op
    2. DROP: remove the grouping
    3. REPLACE: replace with 1 canonical grouping (same role)
    4. SPLIT: replace with 2+ canonical groupings (may change roles)

    The process is as follows:

    1. Dedupe exact duplicates (stable)
    2. Enforce unique roles for context_groupings + row groupings (keep-first)
    3. Sort context_groupings by global outer→inner precedence for stability

    Parameters
    ----------
    canonical_grouping_min_confidence
        The minimum confidence threshold for applying canonical grouping.
    creation_dirs
        The canonical IR creation directories.
    mapping
        The GroupingCanonicalizationMap to apply.
    overwrite
        Whether to overwrite existing normalized decisions.
    segment_decisions
        The SegmentDecisionSet to update.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    normalized_segment_decisions_fp = (
        creation_dirs.root / "segment_decisions_normalized.json"
    )

    if not overwrite and normalized_segment_decisions_fp.exists():
        logger.warning(
            f"Normalized segment decisions JSON already exists at {normalized_segment_decisions_fp}. "
            f"Reusing existing normalized segment decisions. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return SegmentDecisionSet.model_validate(
            open_json_type(normalized_segment_decisions_fp)
        )

    mapping_index = _build_mapping_index(
        canonical_grouping_min_confidence=canonical_grouping_min_confidence,
        mapping=mapping,
    )
    new_decisions: list[SegmentDecision] = []

    for decision in segment_decisions.decisions:
        updates = {}

        # Context groupings: enforce precedence + no duplicate roles.
        if decision.context_groupings:
            updates["context_groupings"] = _rewrite_grouping_list(
                enforce_unique_roles=True,
                groupings=decision.context_groupings,
                mapping_index=mapping_index,
                sort_by_precedence=True,
            )

        # Segment-level groupings: keep order by default.
        if decision.groupings:
            updates["groupings"] = _rewrite_grouping_list(
                enforce_unique_roles=False,
                groupings=decision.groupings,
                mapping_index=mapping_index,
                sort_by_precedence=False,
            )

        # Row-level groupings: enforce unique roles.
        if decision.rows:
            new_rows = []

            for row in decision.rows:
                if row.groupings:
                    new_groupings = _rewrite_grouping_list(
                        enforce_unique_roles=True,
                        groupings=row.groupings,
                        mapping_index=mapping_index,
                        sort_by_precedence=False,
                    )
                    new_rows.append(row.model_copy(update={"groupings": new_groupings}))
                else:
                    new_rows.append(row)

            updates["rows"] = new_rows

        new_decisions.append(decision.model_copy(update=updates))

    # NB: Recompute decision set ID since decisions have changed.
    new_id = compute_decision_set_id(decisions=new_decisions)

    # Save updated decision set.
    normalized_segment_decisions = segment_decisions.model_copy(
        update={"decision_set_id": new_id, "decisions": new_decisions}
    )
    write_to_json(
        fp=normalized_segment_decisions_fp, json_info=normalized_segment_decisions
    )

    logger.success(
        f"Saved normalized segment decisions to: {normalized_segment_decisions_fp}"
    )

    return normalized_segment_decisions


def build_context_hint_from_decision(d: SegmentDecision) -> list[dict[str, Any]]:
    """Build context hint from a SegmentDecision.

    This is used as `prior_context_groupings` when deciding later chunks of the same
    table segment (and can also be used as a general context hint between segments).

    Preference in order:

    1. `context_groupings[]` (explicit outer-context snapshot)
    2. `groupings[]`        (segment-level groupings emitted by the decision)
    3. `rows[].groupings[]` (row-level groupings; useful when the table repeats context
        per row)

    Only coarse "carry" roles are included to avoid noisy/unstable context.

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
    seen: set[str] = set()  # One entry per role

    def _maybe_add(g: GroupingDecision) -> None:
        """Helper to add grouping if eligible.

        Parameters
        ----------
        g
            The GroupingDecision to consider.

        """

        if g.role not in carry_roles:
            return

        key = str(g.role)

        if key in seen:
            return

        seen.add(key)
        hint.append(g.model_dump(mode="json"))

    for g in d.context_groupings or []:
        _maybe_add(g)

    for g in d.groupings or []:
        _maybe_add(g)

    # Row-level groupings matter when the table repeats grade/subject/week/etc per-row
    # (e.g., Uganda thematic curriculum tables).
    for rd in getattr(d, "rows", None) or []:
        for g in rd.groupings or []:
            _maybe_add(g)

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
    text = _DASH_RE.sub("-", text)
    text = _WS_RE.sub(" ", text).strip()

    return text


def clean_up_segment_decisions(
    *,
    creation_dirs: CanonicalIRDirs,
    overwrite: bool,
    segment_decisions: SegmentDecisionSet,
) -> SegmentDecisionSet:
    """Apply universal (non-heuristic) hygiene to all grouping fields in a decision
    set. This is intended to run BEFORE the LLM-based global grouping canonicalization
    step so that the mapping operates on stable strings (whitespace/quote/dash noise
    removed).

    Parameters
    ----------
    creation_dirs
        The canonical IR creation directories.
    overwrite
        Whether to overwrite existing cleaned decisions.
    segment_decisions
        The SegmentDecisionSet to clean.

    Returns
    -------
    SegmentDecisionSet
        The cleaned SegmentDecisionSet.
    """

    segment_decisions_cleaned_fp = creation_dirs.root / "segment_decisions_cleaned.json"

    if not overwrite and segment_decisions_cleaned_fp.exists():
        logger.warning(
            f"Cleaned segment decisions JSON already exists at {segment_decisions_cleaned_fp}. "
            f"Reusing existing cleaned segment decisions. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return SegmentDecisionSet.model_validate(
            open_json_type(segment_decisions_cleaned_fp)
        )

    new_segment_decisions = []

    for d in segment_decisions.decisions:
        updates = {}

        if d.context_groupings:
            updates["context_groupings"] = [
                _clean_grouping(g) for g in d.context_groupings
            ]

        if d.groupings:
            updates["groupings"] = [_clean_grouping(g) for g in d.groupings]

        if d.leaves:
            updates["leaves"] = [_clean_leaf(leaf) for leaf in d.leaves]

        if d.rows:
            updates["rows"] = _clean_decision_rows(d.rows)

        new_segment_decisions.append(d.model_copy(update=updates))

    # NB: Recompute decision_set_id since decision content changed.
    new_id = compute_decision_set_id(decisions=new_segment_decisions)

    # Save cleaned decisions.
    segment_decisions_cleaned = segment_decisions.model_copy(
        update={"decision_set_id": new_id, "decisions": new_segment_decisions}
    )
    write_to_json(fp=segment_decisions_cleaned_fp, json_info=segment_decisions_cleaned)

    logger.success(
        f"Saved cleaned segment decisions to: {segment_decisions_cleaned_fp}"
    )

    return segment_decisions_cleaned


def collect_unique_grouping_keys(
    *,
    creation_dirs: CanonicalIRDirs,
    overwrite: bool,
    segment_decisions: SegmentDecisionSet,
) -> list[GroupingCanonicalizationKey]:
    """Collect the set of unique grouping candidates
    (role/title/local_code/source_label) from a SegmentDecisionSet to feed into the
    LLM-based global grouping canonicalizer.

    The process ensures:

    1. Input traversal is stable
    2. Dedupe uses exact tuple matching

    Parameters
    ----------
    creation_dirs
        The canonical IR creation directories.
    overwrite
        Whether to overwrite existing unique grouping keys.
    segment_decisions
        The SegmentDecisionSet to extract grouping keys from.

    Returns
    -------
    list[GroupingCanonicalizationKey]
        The list of unique grouping keys.
    """

    grouping_keys_unique_fp = creation_dirs.root / "grouping_keys_unique.json"

    if not overwrite and grouping_keys_unique_fp.exists():
        logger.warning(
            f"Unique grouping keys JSON already exists at {grouping_keys_unique_fp}. "
            f"Reusing existing unique grouping keys. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        return [
            GroupingCanonicalizationKey.model_validate(item)
            for item in open_json_type(grouping_keys_unique_fp)
        ]

    grouping_keys: list[GroupingCanonicalizationKey] = []
    seen: set[tuple[str, str, str, str]] = set()

    for g in _iter_all_grouping_decisions(segment_decisions):
        role = g.role
        title = (g.title or "").strip()
        assert title, f"GroupingDecision with empty title found: {g}"

        local_code = (g.local_code or "").strip()
        source_label = (g.source_label or "").strip()

        dedupe_key = (role.value, title, local_code, source_label)

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        grouping_keys.append(
            GroupingCanonicalizationKey(
                local_code=(local_code or None),
                role=role,
                source_label=(source_label or None),
                title=title,
            )
        )

    grouping_keys.sort(
        key=lambda x: (x.role.value, x.title, x.local_code or "", x.source_label or "")
    )
    write_to_json(fp=grouping_keys_unique_fp, json_info=grouping_keys)

    logger.success(f"Saved unique grouping keys to: {creation_dirs.root}")

    return grouping_keys


def compile_canonical_ir(
    *,
    doc_key: str,
    document_ir: DocumentIR,
    segment_decision_conf_threshold: float,
    segment_decisions: SegmentDecisionSet,
    structural_leaf_warn_threshold: float,
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
    segment_decision_conf_threshold
        The low confidence threshold for segment decisions (this ties into the segment
        decision system prompt).
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
        title=TextUnit(language="und", text=canonical_storage_text(framework_title)),
    )
    effective_root_id = ensure_node(
        node=framework_node, nodes_by_id=nodes_by_id, warnings=warnings
    )

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
                page_indices=page_indices,
                section_path_text=section_path_text,
                segment=segment,
                segment_decision_conf_threshold=segment_decision_conf_threshold,
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
                root_id=effective_root_id,
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
        root_id=effective_root_id,
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

    for p in [root]:
        make_dir(p)

    return CanonicalIRDirs(root=root)


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
) -> str:
    """Ensure a CanonicalNode is present in nodes_by_id.

    Returns the effective node_id. If the incoming node's node_id already exists but
    the semantics do not match, we deterministically disambiguate the ID using stable
    provenance-derived salt and insert as a distinct node.

    Merge policy (when semantics match):

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

    Returns
    -------
    str
        The effective node_id.
    """

    node = node.model_copy(deep=True)

    if node.title is not None:
        node.title.text = canonical_storage_text(node.title.text)

    if node.body is not None:
        node.body.text = canonical_storage_text(node.body.text)

    if node.node_id not in nodes_by_id:
        nodes_by_id[node.node_id] = node
        return node.node_id

    existing = nodes_by_id[node.node_id]

    # If we detected a collision, deterministically disambiguate the node_id and insert.
    # NB: Return the effective node_id so callers emit edges to the correct node.
    if _detect_semantic_collision(existing=existing, node=node, warnings=warnings):
        return _resolve_collision(node=node, nodes_by_id=nodes_by_id, warnings=warnings)

    # No collision: Semantics match -> merge provenance (preserve first-seen order).
    list_fields = (
        "page_indices",
        "source_segment_ids",
        "source_decision_ids",
        "section_path_text",
    )
    for field in list_fields:
        base = getattr(existing, field)
        extra = getattr(node, field)

        # Update existing in-place.
        setattr(existing, field, _stable_extend_unique(base=base, extra=extra))

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
    for field in scalar_fields:
        # Only overwrite if existing is None. If node.field is also None, this is a
        # harmless no-op.
        if getattr(existing, field) is None:
            setattr(existing, field, getattr(node, field))

    return existing.node_id


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

    return hashlib.sha256(joined.encode(encoding)).hexdigest()[:32]


def perform_postpass_hygiene(canonical_ir: CanonicalIR) -> CanonicalIR:
    """Perform post-pass hygiene on a CanonicalIR:

    The process is as follows:

    1. Merge nodes
    2. Dedupe edges
    3. Prune empty grouping containers
    4. Prune nodes/edges not reachable from root
    5. Reindex order_index under each parent (remove gaps after pruning)
    6. Perform sanity checks

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

    # 1.
    nodes_merged = merge_nodes_postpass(nodes=canonical_ir.nodes, warnings=warnings)
    node_ids = {n.node_id for n in nodes_merged}

    # 2.
    edges_merged = dedupe_edges_postpass(
        edges=canonical_ir.edges, node_ids=node_ids, warnings=warnings
    )

    # 3.
    nodes_pruned_empty, edges_pruned_empty = prune_empty_groupings(
        edges=edges_merged,
        nodes=nodes_merged,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    # 4.
    nodes_pruned_reachable, edges_pruned_reachable = prune_unreachable_nodes(
        edges=edges_pruned_empty,
        nodes=nodes_pruned_empty,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    # 5.
    edges_reindexed = reindex_order_indices_postpass(
        edges=edges_pruned_reachable, warnings=warnings
    )

    # 6.
    sanity_checks_postpass(
        edges=edges_reindexed,
        nodes=nodes_pruned_reachable,
        root_id=canonical_ir.root_id,
        warnings=warnings,
    )

    return canonical_ir.model_copy(
        update={
            "nodes": nodes_pruned_reachable,
            "edges": edges_reindexed,
            "warnings": warnings,
        }
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
    exclude_keys = {"model", "overwrite"}
    creation_run = RunCtx(
        extra={k: v for k, v in config.model_dump().items() if k not in exclude_keys},
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "creation_run.json", json_info=creation_run)
    logger.info(f"Saving canonical IR creation results to: {creation_dirs}")

    return creation_dirs, creation_run


def prune_empty_groupings(
    *,
    edges: list[CanonicalEdge],
    nodes: list[CanonicalNode],
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
            # CanonicalEdge.rel is always "hasChild", but keep this chec, just in case.
            if e.rel == "hasChild" and e.parent_id in out_degree:
                out_degree[e.parent_id] += 1

        # Identify empty grouping nodes (0 children), excluding root.
        prunable_ids: set[str] = set()

        for nid, node in nodes_by_id.items():
            if nid == root_id:
                continue

            if isinstance(node.role, NodeRole) and out_degree.get(nid, 0) == 0:
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
            segment_id=seg_id,
            warnings=warnings,
        )

        # Advance stack.
        gk = _grouping_key(g)
        new_stack.append(ContextFrame(grouping_key=gk, node_id=effective_node_id))
        parent_id = effective_node_id
        ancestor_keys.append(gk)

    # After reconciliation, new_stack should EXACTLY matches desired_context snapshot.
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


def save_canonical_ir(
    *,
    canonical_ir: CanonicalIR,
    canonical_ir_fp: Path,
    segment_decision_conf_threshold: float,
    structural_leaf_warn_threshold: float,
) -> None:
    """Export the canonical IR to a JSON file.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to serialize.
    canonical_ir_fp
        The output file path for the CanonicalIR JSON.
    segment_decision_conf_threshold
        The low confidence threshold used for segment decisions.
    structural_leaf_warn_threshold
        The structural leaf warning threshold used during canonicalization.
    """

    write_to_json(fp=canonical_ir_fp, json_info=canonical_ir)
    logger.success(f"Saved canonical IR to: {canonical_ir_fp}")

    # Write structural leaf warnings sidecar file.
    structural_leaf_warnings = [
        w for w in canonical_ir.warnings if w.startswith("structural_leaf_review:")
    ]
    structural_leaf_warnings_fp = canonical_ir_fp.with_name(
        canonical_ir_fp.stem + "_structural_leaf_warnings.json"
    )
    write_to_json(
        fp=structural_leaf_warnings_fp,
        json_info={
            "count": len(structural_leaf_warnings),
            "decision_set_id": canonical_ir.decision_set_id,
            "doc_key": canonical_ir.doc_key,
            "segment_decision_conf_threshold": segment_decision_conf_threshold,
            "structural_leaf_warn_threshold": structural_leaf_warn_threshold,
            "warnings": structural_leaf_warnings,
        },
    )
    logger.success(f"Saved structural leaf warnings to: {structural_leaf_warnings_fp}")


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
