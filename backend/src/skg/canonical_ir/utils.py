"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import hashlib
import json
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
from pydantic import TypeAdapter

# Package Library
from skg.canonical_ir.schemas import (
    CanonicalEdge,
    CanonicalIR,
    CanonicalNode,
    CaptionBinding,
    CurriculumSkeleton,
    GroupingDecision,
    LeafDecision,
    SegmentDecision,
    SegmentDecisionSet,
    UnresolvedItem,
    compute_decision_set_id,
)
from skg.canonical_ir.skeleton_engine import (
    MatchableSegment,
    match,
    CursorJump,
    MatchResult,
    dfs_all,
    dfs_matchable,
)
from skg.canonical_ir.skeleton_translator import (
    translate_matched_segment,
    translate_unmatched,
)
from skg.config import Settings
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.page_ir_extraction.schemas import TextUnit
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import BBox, CreateCanonicalConfig, RunCtx
from skg.utils.constants import (
    BlockType,
    CaptionFigurePrefixes,
    CaptionKind,
    CaptionTablePrefixes,
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
_DASH_RE = re.compile(r"[‐-‒–—−]+")  # Common unicode dash characters
_STRUCTURAL_CONTEXT_CUE_RE = re.compile(
    r"\b("
    r"grade|class|primary|standard|std\.?|stage|theme|sub[-\s]?theme|strand|subject|"
    r"learning\s+area|unit|week|term|chapter|module|p\s*[1-9]|std\s*[ivx]+"
    r"|palier|jéego|j[ée]ego|semaine|étape|activit[ée]s|niveau|comp[ée]tence"
    r")\b",
    flags=re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path
    canonical_ir: Path
    caption_binding: Path
    segment_decisions: Path


@dataclass(frozen=True)
class ContextFrame:
    """One frame in the active context stack (excluding the framework root)."""

    grouping_key: str
    node_id: str


@dataclass
class MatchReport:
    """Structured diagnostics from a skeleton matching run.

    Attributes
    ----------
    total_segments
        Total number of matchable segments in the document.
    matched_segments
        Number of segments that matched a skeleton node.
    unmatched_segments
        Number of segments that did NOT match any node.
    total_skeleton_nodes
        Total nodes in the skeleton (all types).
    total_matchable_nodes
        Nodes that participate in matching (not CONTAINER_ONLY).
    matched_nodes
        Number of distinct matchable nodes that received at least one match.
    container_only_nodes
        Number of CONTAINER_ONLY nodes (structural-only, never matched).
    cursor_jumps
        List of large cursor jumps (potential document/skeleton ordering issues).
    unmatched_segment_ids
        IDs of document segments that found no match.
    unexpected_skipped_node_ids
        IDs of matchable nodes that expected a match but received none.
        CONTAINER_ONLY and IGNORE nodes are excluded from this list.
    """

    total_segments: int = 0
    matched_segments: int = 0
    unmatched_segments: int = 0
    total_skeleton_nodes: int = 0
    total_matchable_nodes: int = 0
    matched_nodes: int = 0
    container_only_nodes: int = 0
    cursor_jumps: list[CursorJump] = field(default_factory=list)
    unmatched_segment_ids: list[str] = field(default_factory=list)
    unexpected_skipped_node_ids: list[str] = field(default_factory=list)

    @property
    def segment_coverage(self) -> float:
        """Fraction of document segments that matched a skeleton node."""
        if self.total_segments == 0:
            return 0.0
        return self.matched_segments / self.total_segments

    @property
    def node_coverage(self) -> float:
        """Fraction of matchable skeleton nodes that received a match."""
        if self.total_matchable_nodes == 0:
            return 0.0
        return self.matched_nodes / self.total_matchable_nodes

    @property
    def is_healthy(self) -> bool:
        """A match is healthy when >90% of matchable nodes matched and there
        are no large cursor jumps (which indicate ordering misalignment)."""
        return self.node_coverage > 0.9 and len(self.cursor_jumps) == 0

    def summary(self) -> str:
        """Return a human-readable summary string."""
        lines = [
            f"{'═' * 60}",
            "  Skeleton Match Report",
            f"{'═' * 60}",
            f"  Segments:  {self.matched_segments}/{self.total_segments} matched "
            f"({self.segment_coverage:.1%})",
            f"  Nodes:     {self.matched_nodes}/{self.total_matchable_nodes} matched "
            f"({self.node_coverage:.1%})",
            f"  Container: {self.container_only_nodes} (structural-only)",
            f"  Jumps:     {len(self.cursor_jumps)}",
            f"  Healthy:   {'YES' if self.is_healthy else 'NO'}",
        ]

        if self.unmatched_segment_ids:
            lines.append(f"\n  Unmatched segments ({len(self.unmatched_segment_ids)}):")
            for sid in self.unmatched_segment_ids[:10]:
                lines.append(f"    - {sid}")
            if len(self.unmatched_segment_ids) > 10:
                lines.append(f"    ... and {len(self.unmatched_segment_ids) - 10} more")

        if self.unexpected_skipped_node_ids:
            lines.append(
                f"\n  Unexpected skipped nodes "
                f"({len(self.unexpected_skipped_node_ids)}):"
            )
            for nid in self.unexpected_skipped_node_ids[:10]:
                lines.append(f"    - {nid}")
            if len(self.unexpected_skipped_node_ids) > 10:
                lines.append(
                    f"    ... and {len(self.unexpected_skipped_node_ids) - 10} more"
                )

        if self.cursor_jumps:
            lines.append(f"\n  Cursor jumps ({len(self.cursor_jumps)}):")
            for jump in self.cursor_jumps[:5]:
                lines.append(
                    f"    - {jump.from_node_id} → {jump.to_node_id} "
                    f"(skipped {jump.skipped_count}, seg={jump.segment_id})"
                )

        lines.append(f"{'═' * 60}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict for persistence."""
        return {
            "total_segments": self.total_segments,
            "matched_segments": self.matched_segments,
            "unmatched_segments": self.unmatched_segments,
            "total_skeleton_nodes": self.total_skeleton_nodes,
            "total_matchable_nodes": self.total_matchable_nodes,
            "matched_nodes": self.matched_nodes,
            "container_only_nodes": self.container_only_nodes,
            "segment_coverage": round(self.segment_coverage, 4),
            "node_coverage": round(self.node_coverage, 4),
            "is_healthy": self.is_healthy,
            "cursor_jumps": [
                {
                    "from_node_id": j.from_node_id,
                    "to_node_id": j.to_node_id,
                    "skipped_count": j.skipped_count,
                    "segment_id": j.segment_id,
                }
                for j in self.cursor_jumps
            ],
            "unmatched_segment_ids": self.unmatched_segment_ids,
            "unexpected_skipped_node_ids": self.unexpected_skipped_node_ids,
        }


def _gkey_tuple(
    *,
    role: NodeRole,
    title: str,
    local_code: Optional[str],
    source_label: Optional[str],
) -> tuple[str, str, str, str]:
    """Create a grouping key tuple.

    NB: All string fields are stripped to ensure lookup parity with
    `collect_unique_grouping_keys`, which strips titles before building dedupe keys and
    persisting `GroupingCanonicalizationKey` objects. Without stripping here, a
    `GroupingDecision` whose title carries leading/trailing whitespace would silently
    miss the mapping index and default to KEEP.

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

    return (
        role.value,
        (title or "").strip(),
        (local_code or "").strip(),
        (source_label or "").strip(),
    )


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

    return "unknown"


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
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
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
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
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

    # Edge key: includes rel for consistency with dedupe_edges_postpass.
    key = (parent_id, child_id, "hasChild")

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


def _iter_all_grouping_decisions(
    *,
    decision_set: SegmentDecisionSet,
    row_grouping_roles: frozenset[NodeRole] | None = None,
) -> Iterable[GroupingDecision]:
    """Yield GroupingDecision objects from a SegmentDecisionSet in stable order.

    This iterator is used to construct the global grouping canonicalization inventory.
    It always yields:

    1. SegmentDecision.context_groupings
    2. SegmentDecision.groupings

    It optionally yields row-level groupings:

    3. RowDecision.groupings (only when ``row_grouping_roles`` is provided and
        non-empty).

    When row-level groupings are enabled, only groupings whose role is in
    `row_grouping_roles` are yielded. This supports generalized PDFs where important
    curriculum structure can appear at the row level (e.g., week/term/palier), without
    pulling every row-local token into global canonicalization.

    Parameters
    ----------
    decision_set
        The SegmentDecisionSet to iterate over.
    row_grouping_roles
        Optional set of NodeRoles that are allowed to be yielded from row-level
        groupings. If None or empty, row-level groupings are not yielded.

    Yields
    ------
    GroupingDecision
        The next GroupingDecision in deterministic traversal order.
    """

    include_rows = bool(row_grouping_roles)

    for d in decision_set.decisions:
        if d.context_groupings:
            yield from d.context_groupings

        if d.groupings:
            yield from d.groupings

        if include_rows and d.rows:
            for r in d.rows:
                if not r.groupings:
                    continue

                for g in r.groupings:
                    if g.role in (row_grouping_roles or frozenset()):
                        yield g


def _make_unmatched_segment_sample(
    *, max_len: int = 280, segment: Segment
) -> str | None:
    """Sample string for segments that have *no* SegmentDecision.

    For blocks: uses best-effort extracted text.
    For tables: includes header preview + first body-row preview when available.

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
        text = _extract_block_segment_text(segment)
        return text[:max_len] if text else None

    if isinstance(segment, TableSegment):
        parts: list[str] = []
        headers = _extract_table_headers(segment)

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
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
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
        # NB: decision.groupings (segment-level grouping containers) have already been
        # materialized above; only warn when there are truly no leaves, rows, OR
        # groupings to emit.
        if not decision.leaves and not decision.rows and not decision.groupings:
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
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
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
    text = text.translate(QUOTES_TRANSLATION)
    text = _DASH_RE.sub("-", text)
    text = _WS_RE.sub(" ", text).strip()

    # Normalize colon spacing ONLY when a non-space follows the colon.
    text = re.sub(r":\s*(?=\S)", ": ", text)
    text = _WS_RE.sub(" ", text).strip()

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
    *,
    context_groupings_role_dict: dict[NodeRole, int],
    groupings: list[GroupingDecision],
) -> list[GroupingDecision]:
    """Sort groupings by the global precedence order used for context_groupings.
    Unknown roles fall to the end deterministically.

    Parameters
    ----------
    context_groupings_role_dict
        The mapping of NodeRole to precedence order.
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

        return context_groupings_role_dict.get(g.role, 10_000), g.title

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
    Optional[str]
        A string preview of the first non-header row, or None if no rows are available.
    """

    rows = segment.rows_filldown or segment.rows_grid or segment.rows

    if not rows:
        return None

    start_idx = segment.header_row_count if segment.header_row_count < len(rows) else 0
    row = rows[start_idx]

    cells_out: list[str] = []
    any_non_empty = False

    for cell in (row.cells or [])[:max_cells]:
        tu = getattr(cell, "text", None)
        raw = tu.text if isinstance(tu, TextUnit) else ""
        txt = " ".join(raw.split()).strip()

        if txt:
            any_non_empty = True

            if len(txt) > max_cell_len:
                txt = txt[: max_cell_len - 1] + "…"

            cells_out.append(txt)
        else:
            cells_out.append("∅")

    if not any_non_empty:
        return None

    return " | ".join(cells_out)


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
        True if the decision is materializable, False otherwise. When False, the caller
        skips _materialize_decision_structure, which means active_context_stack is not
        updated for this decision.
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
                local_code=getattr(segment, "local_code", None),
                page_indices=page_indices,
                reason=UnresolvedReason.FLAGGED_UNRESOLVED,
                sample=_make_unresolved_sample(decision=decision, segment=segment),
                section_path_text=section_path_text,
                segment_id=segment.segment_id,
            )
        )
        return False

    if decision.decision_type == SegmentDecisionType.UNRESOLVED:
        reason = UnresolvedReason.DECISION_UNRESOLVED
        unresolved.append(
            UnresolvedItem(
                caption_text=decision.caption_text,
                headers=(
                    _extract_table_headers(segment) if segment.kind == "table" else []
                ),
                local_code=getattr(segment, "local_code", None),
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


def _validate_chunk_sequence(
    *,
    expected_end: int,
    expected_start: int,
    intervals: list[tuple[int, int]],
    segment: Segment,
) -> None:
    """Helper to validate that sorted intervals cover the range
    [expected_start, expected_end) contiguously without overlaps or gaps.

    Parameters
    ----------
    expected_end
        The expected exclusive end of the covered range.
    expected_start
        The expected inclusive start of the covered range.
    intervals
        The list of (start, end) intervals to validate.
    segment
        The Segment being validated.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    cursor = expected_start

    for start, end in intervals:
        if start >= end:
            msg = (
                f"Invalid chunk interval (start must be < end).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  interval: [{start}, {end})"
            )
            logger.error(msg)
            raise QualityError(msg)

        if start < expected_start:
            msg = (
                f"Chunk interval begins before the table body rows (likely includes header rows).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  header_row_count: {segment.header_row_count}\n"
                f"  body_row_range: [{expected_start}, {expected_end})\n"
                f"  interval: [{start}, {end})"
            )
            logger.error(msg)
            raise QualityError(msg)

        if end > expected_end:
            msg = (
                f"Chunk interval ends past the end of the table rows.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  table_row_count: {expected_end}\n"
                f"  interval: [{start}, {end})"
            )
            logger.error(msg)
            raise QualityError(msg)

        if start < cursor:
            msg = (
                f"Overlapping chunk intervals detected.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  overlap_at: row_index={start}\n"
                f"  previous_end: {cursor}\n"
                f"  interval: [{start}, {end})"
            )
            logger.error(msg)
            raise QualityError(msg)

        if start > cursor:
            msg = (
                f"Gap between chunk intervals detected (missing coverage).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  missing_row_range: [{cursor}, {start})\n"
                f"  next_interval: [{start}, {end})"
            )
            logger.error(msg)
            raise QualityError(msg)

        cursor = end

    if cursor != expected_end:
        msg = (
            f"Chunk intervals do not fully cover the table body rows.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  covered_end: {cursor}\n"
            f"  expected_end: {expected_end}\n"
            f"  body_row_range: [{expected_start}, {expected_end})"
        )
        logger.error(msg)
        raise QualityError(msg)


def _validate_decision_types(
    *, all_decisions: list[Any], has_chunks: bool, segment_id: str
) -> None:
    """Ensure no mixing of chunked/unchunked decisions and no malformed ranges.

    Parameters
    ----------
    all_decisions
        The list of all SegmentDecisions for the segment.
    has_chunks
        Whether any of the decisions are chunked (have non-None row_range_start/end).
    segment_id
        The ID of the segment being validated (used for error messages).

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # If we have chunks, we cannot have any "unchunked" (both None) decisions.
    has_unchunked = any(
        (d.row_range_start is None and d.row_range_end is None) for d in all_decisions
    )

    if has_chunks and has_unchunked:
        chunk_count = sum(
            1
            for d in all_decisions
            if d.row_range_start is not None and d.row_range_end is not None
        )
        msg = (
            f"Chunked + unchunked SegmentDecisions detected for the same table segment. "
            f"This can happen if you generated chunked decisions with one config and later "
            f"generated an unchunked decision (or vice-versa).\n"
            f"  segment_id: {segment_id}\n"
            f"  chunk_decision_count: {chunk_count}"
        )
        logger.error(msg)
        raise QualityError(msg)

    # Half-Chunked (one none, one not none).
    has_half_chunked = any(
        (d.row_range_start is None) != (d.row_range_end is None) for d in all_decisions
    )

    if has_half_chunked:
        msg = (
            f"Half-chunked SegmentDecision detected "
            f"(one of row_range_start/end is None, the other is not).\n"
            f"  segment_id: {segment_id}"
        )
        logger.error(msg)
        raise QualityError(msg)


def _validate_interval_uniqueness(
    *, chunk_decisions: list[Any], segment_id: str
) -> None:
    """Ensure no two decisions claim the exact same row interval.

    Parameters
    ----------
    chunk_decisions
        The list of chunked SegmentDecisions for the segment.
    segment_id
        The ID of the segment being validated.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    interval_to_ids: dict[tuple[int, int], list[str]] = {}

    for d in chunk_decisions:
        interval = (int(d.row_range_start), int(d.row_range_end))
        interval_to_ids.setdefault(interval, []).append(d.decision_id)

    duplicate_intervals = {k: v for k, v in interval_to_ids.items() if len(v) > 1}

    if duplicate_intervals:
        interval_sample = list(duplicate_intervals.items())[:5]
        msg = (
            f"Duplicate chunk intervals detected for the same table segment.\n"
            f"  segment_id: {segment_id}\n"
            f"  duplicates(sample): {interval_sample}"
        )
        logger.error(msg)
        raise QualityError(msg)


def _validate_single_table_segment(*, decisions: list[Any], segment: Any) -> None:
    """Perform comprehensive validation of SegmentDecisions for a single TABLE segment,
    ensuring consistent chunking, valid intervals, and contiguous coverage of the table
    body rows.

    Parameters
    ----------
    decisions
        The list of SegmentDecisions for the segment.
    segment
        The Segment being validated.
    """

    # Filter for explicit chunk decisions.
    chunk_decisions = [
        d
        for d in decisions
        if d.row_range_start is not None and d.row_range_end is not None
    ]

    # If the table is not chunked (no chunk decisions found), we skip validation.
    if not chunk_decisions:
        return

    _validate_decision_types(
        all_decisions=decisions, has_chunks=True, segment_id=segment.segment_id
    )
    _validate_interval_uniqueness(
        chunk_decisions=chunk_decisions, segment_id=segment.segment_id
    )
    _validate_chunk_sequence(
        expected_end=len(segment.rows),
        expected_start=segment.header_row_count,
        intervals=sorted(
            ((int(d.row_range_start), int(d.row_range_end)) for d in chunk_decisions),
            key=lambda t: t,
        ),
        segment=segment,
    )


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


def compile_canonical_ir(
    *,
    canonical_ir_fp: Path,
    doc_key: str,
    document_ir: DocumentIR,
    segment_decision_conf_threshold: float,
    segment_decisions: SegmentDecisionSet,
    structural_leaf_warn_threshold: float,
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
            ii.  If no decisions, log warning + add to unresolved.
            iii. For each decision for the segment:
                1. Validate decision; update unresolved/warnings; skip if not
                    materializable.
                2. Check for structural warnings; update warnings.
                3. Materialize nodes; update state containers.
    5. Final compilation of CanonicalIR.

    Parameters
    ----------
    canonical_ir_fp
        The file path to write the compiled CanonicalIR JSON to.
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
    """

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
    decisions_by_segment = _index_decisions_by_segment(
        segment_decisions=segment_decisions
    )

    # 3.
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

    # 4.
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
                    local_code=getattr(segment, "local_code", None),
                    page_indices=page_indices,
                    reason=(
                        UnresolvedReason.UNMATCHED_TABLE
                        if segment.kind == "table"
                        else UnresolvedReason.UNMATCHED_BLOCK
                    ),
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


def create_canonical_ir_dirs(*, output_dir: Path) -> CanonicalIRDirs:
    """Create canonical IR directories for a given creation run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    CanonicalIRDirs
        The created canonical IR directories.
    """

    root = output_dir
    canonical_ir = root / "canonical_ir"
    caption_binding = root / "caption_binding"
    segment_decisions = root / "segment_decisions"

    for p in [root, canonical_ir, caption_binding, segment_decisions]:
        make_dir(p)

    return CanonicalIRDirs(
        root=root,
        canonical_ir=canonical_ir,
        caption_binding=caption_binding,
        segment_decisions=segment_decisions,
    )


def create_canonical_ir_from_curriculum_skeleton(
    *,
    caption_bindings: dict[str, CaptionBinding],
    curriculum_match_report_fp: Path,
    curriculum_skeleton: CurriculumSkeleton,
    canonical_ir_fp: Path,
    doc_key: str,
    document_ir: DocumentIR,
    max_skip_distance: int = 20,
    segment_decisions_fp: Path,
) -> MatchReport:
    """Create a CanonicalIR from a CurriculumSkeleton by matching against DocumentIR
    segments.

    Parameters
    ----------
    caption_bindings
        Mapping from table segment_id to CaptionBinding.
    curriculum_match_report_fp
        Path to persist the MatchReport JSON.
    curriculum_skeleton
        A validated CurriculumSkeleton for this document.
    canonical_ir_fp
        Output path for the compiled CanonicalIR JSON.
    doc_key
        The expected document key for all page IRs.
    document_ir
        The loaded DocumentIR JSON from stitching.
    max_skip_distance
        Maximum skeleton nodes to skip before flagging a cursor jump.
    segment_decisions_fp
        Optional path to persist the generated SegmentDecisionSet JSON.
        Useful for auditing and debugging.

    Returns
    -------
    MatchReport
        Curriculum matching diagnostics from the matching run.
    """

    # 1. Adapter layer
    logger.info("Preparing matchable segments from DocumentIR...")
    matchable_segments = prepare_matchable_segments(
        document_ir=document_ir,
        caption_bindings=caption_bindings,
    )
    logger.info(f"Prepared {len(matchable_segments)} matchable segments.")

    # 2. Matching engine
    logger.info(
        f"Running skeleton matching engine (skeleton={curriculum_skeleton.skeleton_id})..."
    )
    match_result = match(
        segments=matchable_segments,
        curriculum_skeleton=curriculum_skeleton,
        max_skip_distance=max_skip_distance,
    )
    logger.info(
        f"Match complete: {len(match_result.matched)} matched, "
        f"{len(match_result.unmatched)} unmatched."
    )

    # 3. Translation
    logger.info("Translating match results to SegmentDecisions...")
    role_order = curriculum_skeleton.metadata.context_groupings_role_order

    decisions: list[SegmentDecision] = []

    for matched_seg in match_result.matched:
        decision = translate_matched_segment(
            matched_seg,
            doc_key=doc_key,
            role_order=role_order,
        )
        decisions.append(decision)

    for unmatched_seg in match_result.unmatched:
        decision = translate_unmatched(
            unmatched_seg,
            doc_key=doc_key,
        )
        decisions.append(decision)

    # Sort by document order for consistency.
    seg_order: dict[str, int] = {
        s.segment_id: s.document_order for s in matchable_segments
    }
    decisions.sort(key=lambda d: seg_order.get(d.segment_id or "", 999_999))

    logger.info(f"Translated {len(decisions)} total SegmentDecisions.")

    # 4. Build SegmentDecisionSet.
    decision_set = SegmentDecisionSet.model_validate(
        {
            "decision_set_id": compute_decision_set_id(decisions=decisions),
            "decisions": [d.model_dump(mode="json") for d in decisions],
            "doc_key": doc_key,
            "generator": f"curriculum_skeleton:{curriculum_skeleton.skeleton_id}",
            "pdf_name": document_ir.pdf_name,
        }
    )

    # Save the segment decisions.
    write_to_json(fp=segment_decisions_fp, json_info=decision_set)
    logger.info(f"Saved segment decisions to: {segment_decisions_fp}")

    # 6. Compile CanonicalIR.
    logger.info("Compiling CanonicalIR from SegmentDecisions...")
    compile_canonical_ir(
        canonical_ir_fp=canonical_ir_fp,
        doc_key=doc_key,
        document_ir=document_ir,
        segment_decisions=decision_set,
    )
    logger.success(f"CanonicalIR compiled and saved to: {canonical_ir_fp}")

    # 7. Diagnostics ──
    report = generate_match_report(
        match_result=match_result,
        curriculum_skeleton=curriculum_skeleton,
        total_segments=len(matchable_segments),
    )

    logger.info(f"\n{report.summary()}")

    # Save the match report.
    if curriculum_match_report_fp:
        write_to_json(fp=curriculum_match_report_fp, json_info=report.to_dict())
        logger.info(f"Saved curriculum match report to: {curriculum_match_report_fp}")

    return report


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


def generate_match_report(
    *,
    match_result: MatchResult,
    curriculum_skeleton: CurriculumSkeleton,
    total_segments: int,
) -> MatchReport:
    """Build a MatchReport from the engine's MatchResult.

    Parameters
    ----------
    match_result
        Output from ``skeleton_engine.match()``.
    curriculum_skeleton
        The CurriculumSkeleton used for matching.
    total_segments
        Total number of document segments passed to the engine.

    Returns
    -------
    MatchReport
        Structured diagnostics.
    """

    all_nodes = dfs_all(curriculum_skeleton.root)
    matchable_nodes = dfs_matchable(curriculum_skeleton.root)
    matched_node_ids = {m.node.id for m in match_result.matched}

    container_only = [
        n for n in all_nodes if n.emit == CurriculumEmitPolicy.CONTAINER_ONLY
    ]

    # "Unexpected" skipped = matchable nodes that didn't match AND aren't IGNORE.
    unexpected_skipped: list[str] = []
    for node in matchable_nodes:
        if node.id not in matched_node_ids and node.emit != CurriculumEmitPolicy.IGNORE:
            unexpected_skipped.append(node.id)

    report = MatchReport(
        total_segments=total_segments,
        matched_segments=len(match_result.matched),
        unmatched_segments=len(match_result.unmatched),
        total_skeleton_nodes=len(all_nodes),
        total_matchable_nodes=len(matchable_nodes),
        matched_nodes=len(matched_node_ids),
        container_only_nodes=len(container_only),
        cursor_jumps=match_result.cursor_jumps,
        unmatched_segment_ids=[s.segment_id for s in match_result.unmatched],
        unexpected_skipped_node_ids=unexpected_skipped,
    )

    return report


def load_curriculum_skeleton(curriculum_skeleton_fp: Path) -> CurriculumSkeleton:
    """Load and validate a CurriculumSkeleton from a JSON file.

    Parameters
    ----------
    curriculum_skeleton_fp
        Path to the skeleton JSON file.

    Returns
    -------
    CurriculumSkeleton
        A validated skeleton.
    """

    data = open_json_type(curriculum_skeleton_fp)
    curriculum_skeleton = CurriculumSkeleton.model_validate(data)

    logger.info(
        f"Loaded curriculum skeleton '{curriculum_skeleton.skeleton_id}' "
        f"({curriculum_skeleton.metadata.country}/{curriculum_skeleton.metadata.academic_subject})."
    )

    return curriculum_skeleton


def load_or_build_caption_bindings(
    *,
    bind_unknown_caption: bool = True,
    creation_dirs: CanonicalIRDirs,
    document_ir: DocumentIR,
    max_gap_segments: int = 2,
    max_page_distance: int = 1,
    overwrite: bool,
) -> dict[str, CaptionBinding]:
    """Load existing caption-to-table bindings or build deterministic caption-to-table
    bindings *before* LLM interpretation.

    Many curriculum PDFs place a short caption/label block immediately before a table.
    That caption is usually not curriculum content itself, but it often contains
    critical context (grade, subject, theme/unit, table meaning) needed to interpret
    the table.

    This function:

    1. Scans DocumentIR.segments[] in order and one-shot binds each CAPTION block to
        the *next* table segment (within configured gap/page limits).
    2. Produces a stable mapping: table_segment_id -> CaptionBinding(...).
    3. Emits warnings for captions that cannot be bound (e.g., dangling captions).

    We call this function before calling the LLM so that we can:

    1. Improve the LLM accuracy by injecting caption context into table payloads,
        helping it choose correct context_groupings[] and statement roles.
    2. Avoid asking the LLM to infer cross-segment relationships, keeping behavior
        deterministic and replayable.
    3. Enforce the policy that captions are provenance-only: captions provide evidence
        but never become canonical nodes.
    4. Stabilize chunked-table processing by ensuring all chunks of a table receive the
        same caption metadata.

    The resulting bindings are applied when constructing LLM inputs for table segments
    and are stored as provenance/audit context (or attached to unresolved items).

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
    overwrite
        Whether to overwrite existing caption bindings.

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings_fp = creation_dirs.caption_binding / "caption_bindings.json"
    warnings_fp = creation_dirs.caption_binding / "caption_binding_warnings.json"

    if not overwrite and caption_bindings_fp.exists() and warnings_fp.exists():
        logger.warning(
            f"Caption bindings already exists at: {caption_bindings_fp}. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        adapter = TypeAdapter(dict[str, CaptionBinding])
        caption_bindings_json = open_json_type(caption_bindings_fp)
        return adapter.validate_python(caption_bindings_json)

    caption_bindings: dict[str, CaptionBinding] = {}
    warnings: list[str] = []

    # (caption_segment, caption_text, caption_kind, caption_page, caption_index)
    pending_caption: tuple[BlockSegment, str, CaptionKind, int, int] | None = None

    for index, segment in enumerate(document_ir.segments):
        assert (
            segment.slices
        ), f"Segment {segment.segment_id} has no slices; cannot determine page index."
        page_index = segment.slices[0].page_index
        assert isinstance(page_index, int) and page_index >= 0

        # Explicit caption candidate.
        if segment.kind == "block":
            caption_text = _extract_block_segment_text(segment)

            # Only explicit captions bind to tables; headings provide context via
            # section_path/heading_levels instead.
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
                    f"Dangling caption dropped:\n"
                    f"caption={cap_seg.segment_id}\n"
                    f"gap={gap}\n"
                    f"page_index={page_index}\n"
                    f"segment_index={index}"
                )
                logger.warning(msg)
                warnings.append(msg)

            pending_caption = None

            continue

        # Expire pending caption if too far. NB: pending_caption[4] is cap_index.
        if (
            pending_caption is not None
            and max(0, index - pending_caption[4] - 1) > max_gap_segments
        ):
            cap_seg, _, _, _, cap_index = pending_caption
            msg = (
                f"Dangling caption dropped:\n"
                f"caption={cap_seg.segment_id} gap_exceeded={max(0, index - cap_index - 1)}\n"
                f"page_index={page_index}\n"
                f"segment_index={index}"
            )
            logger.warning(msg)
            warnings.append(msg)
            pending_caption = None

    if pending_caption is not None:
        cap_seg, *_ = pending_caption
        msg = f"Dangling caption dropped: caption={cap_seg.segment_id} end_of_document"
        logger.warning(msg)
        warnings.append(msg)

    write_to_json(
        fp=caption_bindings_fp,
        json_info={k: v.model_dump() for k, v in caption_bindings.items()},
    )
    write_to_json(fp=warnings_fp, json_info={"warnings": warnings})

    logger.success(f"Saved caption bindings to: {caption_bindings_fp}")
    logger.success(f"Saved caption binding warnings to: {warnings_fp}")

    return caption_bindings


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

    keys = list(grouping_keys)

    if not keys:
        return "-"

    # JSON encoding avoids delimiter ambiguity (e.g., ['a>b','c'] vs ['a','b>c']).
    payload = json.dumps(keys, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode(encoding)).hexdigest()[:32]


def perform_postpass_hygiene(
    *, canonical_ir: CanonicalIR, document_ir: DocumentIR
) -> CanonicalIR:
    """Perform post-pass hygiene on a CanonicalIR:

    The process is as follows:

    1. Merge nodes
    2. Dedupe edges
    3. Prune empty grouping containers
    4. Prune nodes/edges not reachable from root
    5. Reindex order_index under each parent (remove gaps after pruning)
    6. Perform sanity checks
    7. Add `columns_signature` from the document IR to the canonical IR.

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

    # 1. Merge duplicate nodes by node_id.
    #
    # NB: Currently a no-op because compile_canonical_ir() stores nodes in a dict keyed
    # by node_id, so duplicates are impossible at construction time. Retained as a
    # defensive postpass for future entry points (e.g., deserialization, IR merging).
    nodes_merged = merge_nodes_postpass(nodes=canonical_ir.nodes, warnings=warnings)
    node_ids = {n.node_id for n in nodes_merged}

    # 2. Dedupe edges and drop dangling references.
    #
    # NB: Currently a no-op because _emit_edge() already deduplicates via edges_by_key
    # and emits edges only for valid (possibly collision-resolved) node_ids. Retained as
    # a defensive postpass — the dangling-edge check guards against future changes to
    # collision handling or multi-source IR assembly.
    edges_merged = dedupe_edges_postpass(
        edges=canonical_ir.edges, node_ids=node_ids, warnings=warnings
    )

    # 3.
    nodes_pruned_empty, edges_pruned_empty = prune_empty_groupings(
        edges=edges_merged,
        nodes=nodes_merged,
        # prune_roles={NodeRole.PROSE, NodeRole.SECTION},
        prune_roles=None,
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

    # 7.
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
    exclude_keys = {"overwrite"}
    creation_run = RunCtx(
        extra={
            k: v
            for k, v in config.model_dump(mode="json").items()
            if k not in exclude_keys
        },
        models=[],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "creation_run.json", json_info=creation_run)
    logger.info(f"Saving canonical IR creation results to: {creation_dirs}")

    return creation_dirs, creation_run


def prepare_matchable_segments(
    *,
    document_ir: DocumentIR,
    caption_bindings: dict[str, CaptionBinding],
) -> list[MatchableSegment]:
    """Convert DocumentIR segments into MatchableSegments.

    This is the ONLY place that touches DocumentIR internals.  Downstream code
    (matching engine, translator, diagnostics) works exclusively with the
    ``MatchableSegment`` interface.

    Parameters
    ----------
    document_ir
        The loaded DocumentIR containing ordered segments.
    caption_bindings
        Mapping from table segment_id to CaptionBinding (produced by
        ``load_or_build_caption_bindings``).

    Returns
    -------
    list[MatchableSegment]
        Segments in document order, ready for the matching engine.
    """

    result: list[MatchableSegment] = []

    for idx, segment in enumerate(document_ir.segments):
        if not segment.slices:
            logger.warning(
                f"Segment {segment.segment_id} has no slices; skipping adapter."
            )
            continue

        page_index = segment.slices[0].page_index

        if segment.kind == "block":
            assert isinstance(segment, BlockSegment)
            text = _extract_block_segment_text(segment) or ""
            block_type_val = segment.block_type.value if segment.block_type else None

            result.append(
                MatchableSegment(
                    segment_id=segment.segment_id,
                    segment_kind="block",
                    block_type=block_type_val,
                    text=text,
                    page_index=page_index,
                    document_order=idx,
                    raw_segment=segment,
                )
            )

        elif segment.kind == "table":
            assert isinstance(segment, TableSegment)
            binding = caption_bindings.get(segment.segment_id)

            # Convert header_rows_canonical to immutable tuples for frozen dataclass.
            hrc: tuple[tuple[str, ...], ...] = tuple(
                tuple(row) for row in (segment.header_rows_canonical or [])
            )

            result.append(
                MatchableSegment(
                    segment_id=segment.segment_id,
                    segment_kind="table",
                    block_type=None,
                    text=None,
                    page_index=page_index,
                    document_order=idx,
                    raw_segment=segment,
                    caption_text=binding.caption_text if binding else None,
                    caption_kind=binding.caption_kind if binding else None,
                    caption_segment_id=(
                        binding.caption_segment_id if binding else None
                    ),
                    caption_page_index=(
                        binding.caption_page_index if binding else None
                    ),
                    caption_gap_segments=(binding.gap_segments if binding else None),
                    columns_signature=segment.columns_signature,
                    header_rows_canonical=hrc,
                )
            )

        else:
            logger.warning(
                f"Segment {segment.segment_id} has unknown kind "
                f"{segment.kind!r}; skipping."
            )

    return result


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
            # CanonicalEdge.rel is always "hasChild", but keep this chec, just in case.
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
    desired_context: list[GroupingDecision],
    doc_key: str,
    edges: list[CanonicalEdge],
    edges_by_key: dict[tuple[str, str, str], CanonicalEdge],
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
        The mapping of (parent_id, child_id, rel) to CanonicalEdge for deduplication.
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


def validate_table_chunk_coverage_and_overlap(
    *, document_ir: DocumentIR, segment_decisions: SegmentDecisionSet
) -> None:
    """Validate that chunked-table SegmentDecisions cover the whole table body with no
    overlaps.

    NB: This is a **decision-set-level** validator intended to run *after* all chunk
    decisions for a table segment have been produced, but *before* compilation.

    It checks:

    1. No mixing of chunked and unchunked decisions for the same table segment.
    2. Chunk intervals are within the table row bounds and do not include header rows.
    3. Chunk intervals do not overlap.
    4. Chunk intervals fully cover the table body rows: [header_row_count, len(rows)).

    Parameters
    ----------
    document_ir
        The DocumentIR containing table segments.
    segment_decisions
        The SegmentDecisionSet containing all SegmentDecisions.

    Raises
    ------
    QualityError
        If any chunk coverage/overlap checks fail.
    """

    decisions_by_segment_id: dict[str, list[Any]] = {}

    for d in segment_decisions.decisions:
        assert isinstance(d.segment_id, str), f"Decision missing segment_id: {d}"
        decisions_by_segment_id.setdefault(d.segment_id, []).append(d)

    for segment in document_ir.segments:
        if segment.kind != "table":
            continue

        decisions = decisions_by_segment_id.get(segment.segment_id, [])
        _validate_single_table_segment(decisions=decisions, segment=segment)
