"""This module contains utility functions for the curriculum skeleton pipeline."""

# Standard Library
import re
import unicodedata

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    CaptionBinding,
    CurriculumColumnMapping,
    CurriculumSkeleton,
    CurriculumSkeletonNode,
    GroupingDecision,
    LeafDecision,
    RowDecision,
    SegmentDecision,
)
from skg.canonical_ir.utils import _WS_RE, CanonicalIRDirs, extract_block_segment_text
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.page_ir_extraction.schemas import TableRow, TextUnit
from skg.utils.constants import (
    DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER,
    BlockType,
    CaptionFigurePrefixes,
    CaptionKind,
    CaptionTablePrefixes,
    CurriculumEmitPolicy,
    NodeRole,
    SegmentDecisionType,
    StatementRole,
)
from skg.utils.general import open_json_type, write_to_json


@dataclass
class CurriculumCursorJump:
    """Represents a large jump in the curriculum skeleton matching cursor, which may
    indicate a significant ordering misalignment between the document and skeleton.
    """

    from_node_id: str
    segment_id: str
    skipped_count: int
    to_node_id: str


@dataclass(frozen=True)
class CurriculumMatchableSegment:
    """Normalized view of a DocumentIR segment for the matching engine.

    All fields the matching engine needs are pre-extracted here so the engine never
    touches DocumentIR internals. The `raw_segment` reference is kept for downstream
    translation (table row extraction needs the full TableSegment).
    """

    block_type: Optional[str]  # BlockType.value or None for tables
    document_order: int  # Index in document_ir.segments
    page_index: int  # From first slice
    raw_segment: Segment  # Reference back to full segment
    segment_id: str
    segment_kind: str  # "block" or "table"
    text: Optional[str]  # Combined text for blocks; None for tables

    # True when this block is a caption bound to a table via caption_bindings. Bound
    # captions have text="" so they cannot accidentally match structural skeleton
    # rules; they fall through to IGNORE as expected.
    is_bound_caption: bool = False

    # Table-specific (populated from caption_bindings + TableSegment).
    caption_gap_segments: Optional[int] = None
    caption_kind: Optional[str] = None
    caption_page_index: Optional[int] = None
    caption_segment_id: Optional[str] = None
    caption_text: Optional[str] = None
    columns_signature: Optional[str] = None
    header_rows_canonical: tuple[tuple[str, ...], ...] = ()  # Immutable for frozen


@dataclass
class CurriculumMatchReport:
    """Structured diagnostics from a skeleton matching run.

    Attributes
    ----------
    caption_blocks_ignored
        Number of bound caption blocks that correctly fell through to IGNORE. These are
        block segments whose text was transferred to their table's `caption_text`
        field, so they do not participate in matching.
    container_only_nodes
        Number of CONTAINER_ONLY nodes (structural-only, never matched).
    cursor_jumps
        List of large cursor jumps (potential document/skeleton ordering issues).
    matched_nodes
        Number of distinct matchable nodes that received at least one match.
    matched_segments
        Number of segments that matched a skeleton node.
    total_matchable_nodes
        Nodes that participate in matching (not CONTAINER_ONLY).
    total_segments
        Total number of matchable segments in the document.
    total_skeleton_nodes
        Total nodes in the skeleton (all types).
    unexpected_skipped_node_ids
        IDs of matchable nodes that expected a match but received none. CONTAINER_ONLY
        and IGNORE nodes are excluded from this list.
    unmatched_segment_ids
        IDs of document segments that found no match.
    unmatched_segments
        Number of segments that did NOT match any node.
    """

    caption_blocks_ignored: int = 0
    container_only_nodes: int = 0
    cursor_jumps: list[CurriculumCursorJump] = field(default_factory=list)
    matched_nodes: int = 0
    matched_segments: int = 0
    total_matchable_nodes: int = 0
    total_segments: int = 0
    total_skeleton_nodes: int = 0
    unexpected_skipped_node_ids: list[str] = field(default_factory=list)
    unmatched_segment_ids: list[str] = field(default_factory=list)
    unmatched_segments: int = 0

    @property
    def has_ordering_warnings(self) -> bool:
        """True when cursor jumps were recorded, indicating the document and skeleton
        ordering diverged at one or more points.

        Ordering warnings are informational--they often indicate cross-strand phrase
        collisions in the skeleton (ambiguous match phrases that match nodes in a
        different strand) rather than algorithm failures. Review the `cursor_jumps`
        list for details.

        Returns
        -------
        bool
            True if any cursor jumps were recorded.
        """

        return len(self.cursor_jumps) > 0

    @property
    def is_healthy(self) -> bool:
        """A match is healthy when > 90% of matchable nodes received at least one
        segment match AND no matchable (non-IGNORE) nodes were unexpectedly skipped.

        Cursor jumps are tracked separately via `has_ordering_warnings` and do NOT
        affect the health signal. Jumps typically indicate cross-strand phrase
        ambiguity in the skeleton rather than a fundamental matching failure.

        Returns
        -------
        bool
            True if the match is healthy, False otherwise.
        """

        return self.node_coverage > 0.9 and len(self.unexpected_skipped_node_ids) == 0

    @property
    def node_coverage(self) -> float:
        """Fraction of matchable curriculum skeleton nodes that received a match.

        Returns
        -------
        float
            The node coverage as a float between 0.0 and 1.0. Returns 0.0 if there are
            no matchable nodes.
        """

        if self.total_matchable_nodes == 0:
            return 0.0
        return self.matched_nodes / self.total_matchable_nodes

    @property
    def segment_coverage(self) -> float:
        """Fraction of document segments that matched a skeleton node.

        Returns
        -------
        float
            The segment coverage as a float between 0.0 and 1.0. Returns 0.0 if there
            are no segments.
        """

        if self.total_segments == 0:
            return 0.0
        return self.matched_segments / self.total_segments

    def summary(self) -> str:
        """Return a human-readable summary string for the curriculum match report.

        Returns
        -------
        str
            A formatted multi-line string summarizing the match report, including
            coverage metrics, counts of matched/unmatched segments and nodes, and
            details on unmatched segments and cursor jumps.
        """

        lines = [
            f"{'═' * 60}",
            "  Skeleton Match Report",
            f"{'═' * 60}",
            f"  Segments:  {self.matched_segments}/{self.total_segments} matched "
            f"({self.segment_coverage:.1%})",
            f"  Nodes:     {self.matched_nodes}/{self.total_matchable_nodes} matched "
            f"({self.node_coverage:.1%})",
            f"  Container: {self.container_only_nodes} (structural-only)",
            f"  Captions:  {self.caption_blocks_ignored} (bound -> IGNORE)",
            f"  Jumps:     {len(self.cursor_jumps)}",
            f"  Healthy:   {'YES' if self.is_healthy else 'NO'}",
            f"  Ordering:  {'WARNINGS' if self.has_ordering_warnings else 'OK'}",
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

    def to_dict(self) -> dict[str, Any]:
        """Serialize the curriculum match report to a JSON-compatible dict for
        persistence.

        Returns
        -------
        dict[str, Any]
            A dictionary representation of the match report, including all metrics,
            lists of unmatched segments and cursor jumps, and the overall health status.
        """

        return {
            "total_segments": self.total_segments,
            "matched_segments": self.matched_segments,
            "unmatched_segments": self.unmatched_segments,
            "total_skeleton_nodes": self.total_skeleton_nodes,
            "total_matchable_nodes": self.total_matchable_nodes,
            "matched_nodes": self.matched_nodes,
            "container_only_nodes": self.container_only_nodes,
            "caption_blocks_ignored": self.caption_blocks_ignored,
            "segment_coverage": round(self.segment_coverage, 4),
            "node_coverage": round(self.node_coverage, 4),
            "is_healthy": self.is_healthy,
            "has_ordering_warnings": self.has_ordering_warnings,
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


@dataclass
class CurriculumMatchedSegment:
    """A document segment successfully matched to a curriculum skeleton node."""

    ancestry: list[CurriculumSkeletonNode]  # Root -> matched node (inclusive)
    node: CurriculumSkeletonNode
    segment: CurriculumMatchableSegment

    # For allow_multiple_segments (bilingual pairs).
    additional_segments: list[CurriculumMatchableSegment] = field(default_factory=list)


@dataclass
class CurriculumMatchResult:
    """Complete output of the curriculum matching engine."""

    cursor_jumps: list[CurriculumCursorJump]
    matched: list[CurriculumMatchedSegment]
    skipped_node_ids: set[str]
    unmatched: list[CurriculumMatchableSegment]


@dataclass
class CurriculumResolvedColumnRole:
    """Resolved role for a single table column."""

    col_index: int
    kind: str  # "grouping", "leaf", or "skip"
    role_value: str  # e.g., "strand", "expectation"
    source_label: str  # Column header text (for source_label)


def _cell_to_text(cell: TextUnit | dict[str, Any]) -> str:
    """Extract plain text from a table cell.

    Handles both `TextUnit` objects (with `.text` attribute) and dicts.

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

        return inner.strip() if isinstance(inner, str) else str(inner or "")

    # Dict fallback.
    if isinstance(cell, dict):
        t = cell.get("text", "")
        return (
            (t.get("text", "") or "").strip()
            if isinstance(t, dict)
            else str(t or "").strip()
        )

    return str(cell or "").strip()


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


def _count_skipped_between(
    *,
    consumed_node_ids: set[str],
    end: int,
    matchable_nodes: list[CurriculumSkeletonNode],
    start: int,
) -> int:
    """Count un-consumed nodes in [start, end) for jump detection.

    Parameters
    ----------
    consumed_node_ids
        Set of node IDs that have already been matched and consumed.
    end
        The end index (exclusive) for the count window.
    matchable_nodes
        The full list of matchable skeleton nodes in DFS order.
    start
        The start index (inclusive) for the count window.

    Returns
    -------
    int
        The number of skipped (un-consumed) nodes within the specified window.
    """

    return sum(
        1 for i in range(start, end) if matchable_nodes[i].id not in consumed_node_ids
    )


def _create_decision_from_role(
    *, cell_text: str, col_role: CurriculumResolvedColumnRole
) -> GroupingDecision | LeafDecision | None:
    """Create a GroupingDecision or LeafDecision based on the column role.

    Parameters
    ----------
    cell_text
        The extracted and stripped text from the cell.
    col_role
        The resolved column role definition.

    Returns
    -------
    GroupingDecision | LeafDecision | None
        The appropriate decision object, or None if the role is invalid.
    """

    try:
        if col_role.kind == "grouping":
            return GroupingDecision(
                role=NodeRole(col_role.role_value),
                source_label=col_role.source_label,
                title=cell_text,
            )
        if col_role.kind == "leaf":
            return LeafDecision(
                body=cell_text,
                role=StatementRole(col_role.role_value),
                source_label=col_role.source_label,
            )
    except ValueError:
        logger.warning(
            f"Invalid role '{col_role.role_value}' for kind '{col_role.kind}'; "
            f"skipping column {col_role.col_index}."
        )

    return None


def _drain_cursor(
    *,
    consumed_node_ids: set[str],
    cursor: int,
    matchable_nodes: list[CurriculumSkeletonNode],
    pinned_node_id: str | None,
) -> int:
    """Advance the cursor past consecutive consumed nodes at the head of the list.

    A pinned node blocks draining so that it stays within the probe window for
    subsequent multi-segment matches.

    Parameters
    ----------
    consumed_node_ids
        Set of node IDs that have already been matched.
    cursor
        The current cursor position index.
    matchable_nodes
        The full list of matchable skeleton nodes in DFS order.
    pinned_node_id
        The ID of the node currently pinned, or None if no node is pinned.

    Returns
    -------
    int
        The updated cursor position after draining.
    """

    while cursor < len(matchable_nodes):
        nid = matchable_nodes[cursor].id

        if nid in consumed_node_ids and nid != pinned_node_id:
            cursor += 1
        else:
            break

    return cursor


def _extract_fallback_content(
    *, cells: list[Any], default_leaf_role: StatementRole | None
) -> tuple[list[GroupingDecision], list[LeafDecision]]:
    """Extract fallback content when no column mapping is provided.

    Parameters
    ----------
    cells
        The cells from the table row.
    default_leaf_role
        Fallback leaf role to use for the extracted text.

    Returns
    -------
    tuple[list[GroupingDecision], list[LeafDecision]]
        Empty groupings and a list of fallback leaves, if applicable.
    """

    if not default_leaf_role:
        return [], []

    parts: list[str] = []

    for cell in cells:
        text = _cell_to_text(cell)

        if text and (stripped := text.strip()):
            parts.append(stripped)

    if not parts:
        return [], []

    return [], [LeafDecision(body="\n\n".join(parts), role=default_leaf_role)]


def _extract_row_content(
    *,
    col_map: list[CurriculumResolvedColumnRole],
    default_leaf_role: StatementRole | None,
    row: TableRow,
) -> tuple[list[GroupingDecision], list[LeafDecision]]:
    """Extract groupings and leaves from a single table row using column map.

    Parameters
    ----------
    col_map
        Resolved column-to-role mapping.
    default_leaf_role
        Fallback leaf role when no column_mappings matched any column.
    row
        A TableRow object from DocumentIR.

    Returns
    -------
    tuple[list[GroupingDecision], list[LeafDecision]]
        Row-level groupings and leaves.
    """

    cells = getattr(row, "cells", [])

    if not col_map:
        return _extract_fallback_content(
            cells=cells, default_leaf_role=default_leaf_role
        )

    groupings: list[GroupingDecision] = []
    leaves: list[LeafDecision] = []

    for col_role in col_map:
        if col_role.col_index >= len(cells) or col_role.kind == "skip":
            continue

        cell_text = _cell_to_text(cells[col_role.col_index])

        if not cell_text or not (cell_text_stripped := cell_text.strip()):
            continue

        decision = _create_decision_from_role(
            cell_text=cell_text_stripped, col_role=col_role
        )

        if isinstance(decision, GroupingDecision):
            groupings.append(decision)
        elif isinstance(decision, LeafDecision):
            leaves.append(decision)

    return groupings, leaves


def _normalize_match_text(text: str) -> str:
    """Normalize text for phrase matching.

    Applies: NFKD unicode decomposition, accent/diacritical mark stripping,
    casefolding, and whitespace collapsing. This ensures matching is robust to
    vision-model extraction errors that drop or alter accents (e.g., `é` extracted as
    `e`).

    Parameters
    ----------
    text
        The raw text to normalize.

    Returns
    -------
    str
        The normalized, accent-free text ready for substring matching.
    """

    # NFKD decomposes accented characters into base char + combining mark.
    text = unicodedata.normalize("NFKD", text)

    # Strip combining diacritical marks (category "Mn").
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")

    text = text.casefold()
    text = _WS_RE.sub(" ", text).strip()

    return text


def _probe_nodes(
    *,
    consumed_node_ids: set[str],
    end: int,
    matchable_nodes: list[CurriculumSkeletonNode],
    pinned_node_id: str | None,
    segment: CurriculumMatchableSegment,
    start: int,
) -> tuple[int, CurriculumSkeletonNode] | None:
    """Find the first matching un-consumed node in [start, end).

    Already-consumed nodes are skipped except the currently pinned node, which must
    remain matchable for multi-segment continuations.

    Parameters
    ----------
    consumed_node_ids
        Set of node IDs that have already been matched.
    end
        The end index (exclusive) for the probe window.
    matchable_nodes
        The full list of matchable skeleton nodes in DFS order.
    pinned_node_id
        The ID of the node currently pinned, or None if no node is pinned.
    segment
        The document segment being matched.
    start
        The start index (inclusive) for the probe window.

    Returns
    -------
    tuple[int, CurriculumSkeletonNode] | None
        A tuple of (matched_index, matched_node) if a match is found, else None.
    """

    for idx in range(start, min(end, len(matchable_nodes))):
        node = matchable_nodes[idx]

        if node.id in consumed_node_ids and node.id != pinned_node_id:
            continue

        if segment_matches_node(node=node, segment=segment):
            return idx, node

    return None


def _record_match(
    *,
    ancestry_map: dict[str, Any],
    consumed_node_ids: set[str],
    cursor: int,
    cursor_jumps: list[CurriculumCursorJump],
    matchable_nodes: list[CurriculumSkeletonNode],
    node: CurriculumSkeletonNode,
    pinned_node_id: str | None,
    probe_idx: int,
    results: list[CurriculumMatchedSegment],
    segment: CurriculumMatchableSegment,
) -> tuple[int, str | None]:
    """Handle a successful match: record the result, manage the pin, and drain the
    cursor.

    Parameters
    ----------
    ancestry_map
        Mapping of node IDs to their ancestry.
    consumed_node_ids
        Set of node IDs that have already been matched (modified in place).
    cursor
        The current cursor position index.
    cursor_jumps
        List tracking node jumps (modified in place).
    matchable_nodes
        The full list of matchable skeleton nodes in DFS order.
    node
        The successfully matched curriculum skeleton node.
    pinned_node_id
        The ID of the node currently pinned.
    probe_idx
        The index where the match was found.
    results
        List tracking successfully matched segments (modified in place).
    segment
        The document segment that matched the node.

    Returns
    -------
    tuple[int, str | None]
        The updated cursor position and the newly pinned node ID (or None).
    """

    ancestry = ancestry_map[node.id]

    # Multi-segment continuation: same node as the last result.
    if node.allow_multiple_segments and results and results[-1].node.id == node.id:
        results[-1].additional_segments.append(segment)

        # Pin stays; consumed_node_ids unchanged; cursor stays.
        return cursor, pinned_node_id

    # Record a cursor jump when > 1 un-consumed node was skipped.
    skipped_count = _count_skipped_between(
        consumed_node_ids=consumed_node_ids,
        end=probe_idx,
        matchable_nodes=matchable_nodes,
        start=cursor,
    )

    if skipped_count > 1 and cursor < len(matchable_nodes):
        cursor_jumps.append(
            CurriculumCursorJump(
                from_node_id=matchable_nodes[cursor].id,
                segment_id=segment.segment_id,
                skipped_count=skipped_count,
                to_node_id=node.id,
            )
        )

    results.append(
        CurriculumMatchedSegment(ancestry=ancestry, node=node, segment=segment)
    )
    consumed_node_ids.add(node.id)

    # Update pin.
    if node.allow_multiple_segments:
        pinned_node_id = node.id
    else:
        # Release any previous pin.
        pinned_node_id = None

    cursor = _drain_cursor(
        consumed_node_ids=consumed_node_ids,
        cursor=cursor,
        matchable_nodes=matchable_nodes,
        pinned_node_id=pinned_node_id,
    )

    return cursor, pinned_node_id


def _resolve_column_mappings(
    *,
    column_mappings: list[CurriculumColumnMapping],
    header_rows_canonical: list[list[str]],
) -> list[CurriculumResolvedColumnRole]:
    """Match skeleton column_mappings against actual table headers.

    Builds per-column header signatures by joining all header rows for each column
    using `HEADER_SIGNATURE_SEPARATOR`. Each column is tested against every mapping in
    order; first match wins. Unmatched columns default to `skip`.

    Note: `column_mappings` patterns use inline `(?i)` flags (self-contained), so this
    function compiles with `re.UNICODE` only, not `re.IGNORECASE`.

    Parameters
    ----------
    column_mappings
        Column-to-role mappings from the skeleton node.
    header_rows_canonical
        Canonical header rows as `list[list[str]]` from the table segment.

    Returns
    -------
    list[CurriculumResolvedColumnRole]
        One entry per column in the (widest) header row.
    """

    if not header_rows_canonical:
        return []

    # Determine column count from the widest header row.
    n_cols = max(len(row) for row in header_rows_canonical)

    if n_cols == 0:
        return []

    # Build per-column header signature by joining all header rows.
    col_headers = [
        " / ".join(row[i] for row in header_rows_canonical if i < len(row) and row[i])
        for i in range(n_cols)
    ]

    resolved: list[CurriculumResolvedColumnRole] = []

    for col_idx, header_text in enumerate(col_headers):
        matches = [
            m
            for m in column_mappings
            if re.search(m.header_pattern, header_text, re.UNICODE)
        ]

        if len(matches) > 1:
            logger.warning(
                f"Column {col_idx} header {header_text!r} matched "
                f"{len(matches)} mappings; using first match."
            )

        matched_mapping = matches[0] if matches else None

        # Build the resolved role.
        if not matched_mapping or matched_mapping.role == "skip":
            resolved.append(
                CurriculumResolvedColumnRole(
                    col_index=col_idx,
                    kind="skip",
                    role_value="",
                    source_label=header_text,
                )
            )
        else:
            kind, role_value = matched_mapping.role.split(":", 1)
            resolved.append(
                CurriculumResolvedColumnRole(
                    col_index=col_idx,
                    kind=kind,
                    role_value=role_value,
                    source_label=(matched_mapping.source_label_override or header_text),
                )
            )

    any_column_matched = any(r.kind != "skip" for r in resolved)
    has_leaf_column = any(r.kind == "leaf" for r in resolved)

    if not any_column_matched:
        logger.warning(
            f"No column matched any mapping (table may be incorrectly targeted). "
            f"Headers: {col_headers}"
        )
    elif not has_leaf_column:
        logger.warning(
            f"No leaf column resolved (table will produce groupings-only). "
            f"Headers: {col_headers}"
        )

    return resolved


def _translate_table_rows(
    *,
    context: list[GroupingDecision],
    decision_id: str,
    node: CurriculumSkeletonNode,
    seg: CurriculumMatchableSegment,
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

    # Build column index -> role mapping from skeleton + table headers.
    col_map = _resolve_column_mappings(
        column_mappings=node.column_mappings,
        header_rows_canonical=list(list(row) for row in seg.header_rows_canonical),
    )

    # Row source fallback chain (rows_filldown -> rows_grid -> rows).
    if table_seg.rows_filldown is not None:
        rows_source = table_seg.rows_filldown
    elif table_seg.rows_grid is not None:
        rows_source = table_seg.rows_grid
    else:
        logger.warning(
            f"Table {seg.segment_id}: falling back to raw rows "
            f"(rows_filldown and rows_grid are both None). "
            f"Column-index alignment may be broken due to col_span > 1 cells."
        )
        rows_source = table_seg.rows

    header_n = table_seg.header_row_count or 0
    row_decisions: list[RowDecision] = []

    for abs_i, row in enumerate(rows_source):
        if abs_i < header_n:
            continue  # Skip header rows

        row_groupings, row_leaves = _extract_row_content(
            col_map=col_map, default_leaf_role=node.leaf_role, row=row
        )

        if not row_groupings and not row_leaves:
            continue

        row_decisions.append(
            RowDecision(groupings=row_groupings, leaves=row_leaves, row_index=abs_i)
        )

    # Build segment-level groupings from node metadata.
    segment_groupings: list[GroupingDecision] = []

    if node.grouping_role:
        segment_groupings.append(
            GroupingDecision(
                local_code=node.local_code,
                role=node.grouping_role,
                source_label=node.source_label,
                title=node.canonical_name.primary,
            )
        )

    # Determine decision type from actual row outputs (not node metadata).
    has_any_groupings = bool(segment_groupings) or any(
        r.groupings for r in row_decisions
    )
    has_any_leaves = any(r.leaves for r in row_decisions)

    if has_any_groupings and has_any_leaves:
        dt = SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES
    elif has_any_leaves:
        dt = SegmentDecisionType.EMIT_LEAVES_ONLY
    elif has_any_groupings:
        dt = SegmentDecisionType.EMIT_GROUPINGS_ONLY
    else:
        logger.warning(
            f"Table {seg.segment_id}: no groupings or leaves produced from "
            f"{len(rows_source) - header_n} data rows "
            f"(bad column mapping or empty table body). "
            f"Marking as UNRESOLVED (skeleton node '{node.id}' matched but "
            f"extraction failed)."
        )
        dt = SegmentDecisionType.UNRESOLVED

    # UNRESOLVED decisions must have all output arrays empty (schema invariant).
    is_noop = dt == SegmentDecisionType.UNRESOLVED

    return SegmentDecision(
        block_type=None,
        caption_gap_segments=seg.caption_gap_segments,
        caption_kind=seg.caption_kind,
        caption_page_index=seg.caption_page_index,
        caption_segment_id=seg.caption_segment_id,
        caption_text=seg.caption_text,
        columns_signature=seg.columns_signature,
        confidence=1.0,
        context_groupings=context if not is_noop else [],
        decision_id=decision_id,
        decision_type=dt,
        groupings=segment_groupings if not is_noop else [],
        leaves=[],
        rationale=(
            f"Skeleton TABLE: '{node.id}' → {len(row_decisions)} data rows."
            if not is_noop
            else (
                f"Skeleton TABLE UNRESOLVED: '{node.id}' matched but column "
                f"extraction produced 0 groupings and 0 leaves from "
                f"{len(rows_source) - header_n} data rows."
            )
        ),
        rows=row_decisions if not is_noop else [],
        segment_id=seg.segment_id,
        segment_kind="table",
    )


def build_ancestry_map(
    root: CurriculumSkeletonNode,
) -> dict[str, list[CurriculumSkeletonNode]]:
    """Build node_id -> full ancestry chain (root -> node, inclusive).

    Parameters
    ----------
    root
        The root SkeletonNode.

    Returns
    -------
    dict[str, list[CurriculumSkeletonNode]]
        Mapping from each node ID to its full ancestry chain.
    """

    result: dict[str, list[CurriculumSkeletonNode]] = {}

    def _walk(
        *, ancestors: list[CurriculumSkeletonNode], node: CurriculumSkeletonNode
    ) -> None:
        """DFS walk to build ancestry map.

        Parameters
        ----------
        ancestors
            Ancestry chain from root to parent of current node.
        node
            Current SkeletonNode being visited.
        """

        chain = ancestors + [node]
        result[node.id] = chain

        for child in node.children:
            _walk(ancestors=chain, node=child)

    _walk(ancestors=[], node=root)
    return result


def build_context_groupings(
    *,
    ancestry: list[CurriculumSkeletonNode],
    matched_node: CurriculumSkeletonNode,
    role_order: list[NodeRole] | None = None,
) -> list[GroupingDecision]:
    """Build context_groupings from the skeleton ancestry chain.

    Includes ancestors that:

    1. Have a `grouping_role` (not None, not FRAMEWORK).
    2. Are NOT the matched node itself (that goes in `groupings`).
    3. Are `EMIT_GROUPING`/`EMIT_GROUPING_AND_LEAF`/`EMIT_TABLE_ROWS` (visible in
        document), OR are `CONTAINER_ONLY` with `implicit=True`.

    The result is sorted by role precedence (outer → inner).

    Parameters
    ----------
    ancestry
        Full ancestry chain from root to matched node (inclusive).
    matched_node
        The node that was matched (excluded from context).
    role_order
        Custom role precedence order. Falls back to
        `DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER`.

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
                local_code=node.local_code,
                role=node.grouping_role,
                source_label=node.source_label,
                title=node.canonical_name.primary,
            )
        )

    context.sort(key=lambda g: precedence.get(g.role, 999))
    return context


def dfs_all(root: CurriculumSkeletonNode) -> list[CurriculumSkeletonNode]:
    """Flatten ALL skeleton nodes into DFS order (including CONTAINER_ONLY).

    Parameters
    ----------
    root
        The root SkeletonNode.

    Returns
    -------
    list[SkeletonNode]
        All nodes in DFS traversal order.
    """

    nodes: list[CurriculumSkeletonNode] = []

    def _walk(node: CurriculumSkeletonNode) -> None:
        """DFS walk to collect all nodes.

        Parameters
        ----------
        node
            Current SkeletonNode being visited.
        """

        nodes.append(node)

        for child in node.children:
            _walk(child)

    _walk(root)
    return nodes


def dfs_matchable(root: CurriculumSkeletonNode) -> list[CurriculumSkeletonNode]:
    """Flatten skeleton into DFS order, keeping only matchable nodes.

    A node is matchable if it is NOT `CONTAINER_ONLY` and has at least one
    `match_rule`. The framework root is excluded (it has no match rules).

    Parameters
    ----------
    root
        The root SkeletonNode.

    Returns
    -------
    list[SkeletonNode]
        Matchable nodes in DFS traversal order.
    """

    nodes: list[CurriculumSkeletonNode] = []

    def _walk(node: CurriculumSkeletonNode) -> None:
        """DFS walk to collect matchable nodes.

        Parameters
        ----------
        node
            Current SkeletonNode being visited.
        """

        if node.emit != CurriculumEmitPolicy.CONTAINER_ONLY and node.match_phrases:
            nodes.append(node)

        for child in node.children:
            _walk(child)

    _walk(root)
    return nodes


def generate_curriculum_match_report(
    *,
    curriculum_match_report_fp: Path,
    curriculum_match_results: CurriculumMatchResult,
    curriculum_skeleton: CurriculumSkeleton,
    total_segments: int,
) -> None:
    """Build a CurriculumMatchReport from the engine's CurriculumMatchResult.

    Parameters
    ----------
    curriculum_match_report_fp
        The file path to write the CurriculumMatchReport JSON to.
    curriculum_match_results
        The raw CurriculumMatchResult from the matching engine.
    curriculum_skeleton
        The CurriculumSkeleton used for matching.
    total_segments
        Total number of document segments passed to the engine.
    """

    all_nodes = dfs_all(curriculum_skeleton.root)
    matchable_nodes = dfs_matchable(curriculum_skeleton.root)
    matched_node_ids = {m.node.id for m in curriculum_match_results.matched}

    container_only = [
        n for n in all_nodes if n.emit == CurriculumEmitPolicy.CONTAINER_ONLY
    ]

    # "Unexpected" skipped = matchable nodes that didn't match AND aren't IGNORE.
    unexpected_skipped: list[str] = []
    for node in matchable_nodes:
        if node.id not in matched_node_ids and node.emit != CurriculumEmitPolicy.IGNORE:
            unexpected_skipped.append(node.id)

    # Count bound caption blocks that correctly fell through to IGNORE.
    caption_blocks_ignored = sum(
        1 for s in curriculum_match_results.unmatched if s.is_bound_caption
    )

    report = CurriculumMatchReport(
        caption_blocks_ignored=caption_blocks_ignored,
        container_only_nodes=len(container_only),
        cursor_jumps=curriculum_match_results.cursor_jumps,
        matched_nodes=len(matched_node_ids),
        matched_segments=len(curriculum_match_results.matched),
        total_matchable_nodes=len(matchable_nodes),
        total_segments=total_segments,
        total_skeleton_nodes=len(all_nodes),
        unexpected_skipped_node_ids=unexpected_skipped,
        unmatched_segment_ids=[
            s.segment_id for s in curriculum_match_results.unmatched
        ],
        unmatched_segments=len(curriculum_match_results.unmatched),
    )

    logger.info(f"\n{report.summary()}")

    # Save the match report.
    write_to_json(fp=curriculum_match_report_fp, json_info=report.to_dict())

    logger.success(f"Saved curriculum match report to: {curriculum_match_report_fp}")

    if not report.is_healthy:
        logger.warning(
            f"Curriculum skeleton match is NOT healthy "
            f"(node coverage={report.node_coverage:.1%}, "
            f"unexpected skipped nodes={len(report.unexpected_skipped_node_ids)}). "
            f"Review the curriculum match report at: {curriculum_match_report_fp}"
        )

    if report.has_ordering_warnings:
        logger.warning(
            f"Curriculum skeleton match has ordering warnings: "
            f"{len(report.cursor_jumps)} cursor jump(s) detected. "
            f"This often indicates cross-strand phrase collisions in the skeleton "
            f"(ambiguous match_phrases that match nodes in a different strand). "
            f"Review cursor_jumps in: {curriculum_match_report_fp}"
        )


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

    logger.info(
        f"Using curriculum skeleton pipeline with curriculum skeleton: "
        f"{curriculum_skeleton_fp}"
    )

    data = open_json_type(curriculum_skeleton_fp)
    curriculum_skeleton = CurriculumSkeleton.model_validate(data)

    logger.success(
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

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings: dict[str, CaptionBinding] = {}
    caption_bindings_fp = creation_dirs.caption_binding / "caption_bindings.json"
    warnings: list[str] = []
    warnings_fp = creation_dirs.caption_binding / "caption_binding_warnings.json"

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
            caption_text = extract_block_segment_text(segment)

            # Only explicit captions bind to tables; headings provide context via
            # section_path/heading_levels instead.
            if segment.block_type == BlockType.CAPTION and caption_text:
                kind = _classify_caption_kind(caption_text)

                # Don't bind figure captions to tables.
                if kind == "figure" or (kind == "unknown" and not bind_unknown_caption):
                    continue

                # Warn if a previous caption is being overwritten before it could bind
                # to a table. This can happen when two captions appear in sequence
                # (e.g. multi-caption annotations), and means the earlier caption's
                # context is silently lost.
                if pending_caption is not None:
                    prev_seg, _, _, _, _ = pending_caption
                    msg = (
                        f"Pending caption overwritten before binding:\n"
                        f"  overwritten_caption={prev_seg.segment_id}\n"
                        f"  replaced_by={segment.segment_id}\n"
                        f"  page_index={page_index}\n"
                        f"  segment_index={index}"
                    )
                    logger.warning(msg)
                    warnings.append(msg)

                pending_caption = (segment, caption_text, kind, page_index, index)
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


def match_curriculum(
    *,
    curriculum_skeleton: CurriculumSkeleton,
    max_skip_distance: int,
    segments: list[CurriculumMatchableSegment],
) -> CurriculumMatchResult:
    """Deterministic forward-only matching of document segments to skeleton nodes.

    The engine walks document segments in order and probes up to `max_skip_distance`
    skeleton nodes ahead of the cursor for each segment. When a match is found the node
    is marked as consumed, but the cursor only advances past *consecutive* consumed
    nodes at the head of the remaining list (the cursor "drains" rather than "jumps").

    This design decouples the **lookahead distance** (how far ahead to search) from the
    **cursor advancement** (which nodes are permanently passed over). A distant match
    consumes the matched node but does NOT permanently skip the intermediate unmatched
    nodes--they remain available for later segments.

    For `allow_multiple_segments` nodes the cursor is pinned (the consumed node is not
    drained) until a different node matches or no match is found, at which point the
    pin is released and the cursor drains normally.

    Parameters
    ----------
    curriculum_skeleton
        A validated CurriculumSkeleton.
    max_skip_distance
        Maximum skeleton nodes to probe ahead from the current cursor position. The
        engine will NOT scan beyond this window (bounded probe).
    segments
        CurriculumMatchableSegment in document order.

    Returns
    -------
    CurriculumMatchResult
        Matched segments, unmatched segments, skipped node IDs, and jumps.
    """

    logger.info("Running curriculum skeleton matching engine...")

    matchable_nodes = dfs_matchable(curriculum_skeleton.root)
    ancestry_map = build_ancestry_map(curriculum_skeleton.root)

    cursor: int = 0
    consumed_node_ids: set[str] = set()
    pinned_node_id: str | None = None

    cursor_jumps: list[CurriculumCursorJump] = []
    results: list[CurriculumMatchedSegment] = []
    unmatched: list[CurriculumMatchableSegment] = []

    for segment in segments:
        probe_end = min(cursor + max_skip_distance, len(matchable_nodes))

        hit = _probe_nodes(
            consumed_node_ids=consumed_node_ids,
            end=probe_end,
            matchable_nodes=matchable_nodes,
            pinned_node_id=pinned_node_id,
            segment=segment,
            start=cursor,
        )

        if hit is not None:
            cursor, pinned_node_id = _record_match(
                ancestry_map=ancestry_map,
                consumed_node_ids=consumed_node_ids,
                cursor=cursor,
                cursor_jumps=cursor_jumps,
                matchable_nodes=matchable_nodes,
                node=hit[1],
                pinned_node_id=pinned_node_id,
                probe_idx=hit[0],
                results=results,
                segment=segment,
            )
            continue

        # No match in the primary window. If pinned, unpin and retry.
        if pinned_node_id is not None:
            pinned_node_id = None
            cursor = _drain_cursor(
                consumed_node_ids=consumed_node_ids,
                cursor=cursor,
                matchable_nodes=matchable_nodes,
                pinned_node_id=pinned_node_id,
            )

            retry_end = min(cursor + max_skip_distance, len(matchable_nodes))
            hit = _probe_nodes(
                consumed_node_ids=consumed_node_ids,
                end=retry_end,
                matchable_nodes=matchable_nodes,
                pinned_node_id=pinned_node_id,
                segment=segment,
                start=cursor,
            )

            if hit is not None:
                cursor, pinned_node_id = _record_match(
                    ancestry_map=ancestry_map,
                    consumed_node_ids=consumed_node_ids,
                    cursor=cursor,
                    cursor_jumps=cursor_jumps,
                    matchable_nodes=matchable_nodes,
                    node=hit[1],
                    pinned_node_id=pinned_node_id,
                    probe_idx=hit[0],
                    results=results,
                    segment=segment,
                )
                continue

        unmatched.append(segment)

    all_ids: set[str] = {n.id for n in dfs_all(curriculum_skeleton.root)}
    skipped = all_ids - consumed_node_ids

    curriculum_matches = CurriculumMatchResult(
        cursor_jumps=cursor_jumps,
        matched=results,
        skipped_node_ids=skipped,
        unmatched=unmatched,
    )

    logger.info(
        f"Curriculum matching complete: {len(curriculum_matches.matched)} matched, "
        f"{len(curriculum_matches.unmatched)} unmatched."
    )

    return curriculum_matches


def prepare_matchable_segments(
    *, caption_bindings: dict[str, CaptionBinding], document_ir: DocumentIR
) -> list[CurriculumMatchableSegment]:
    """Convert DocumentIR segments into MatchableSegments.

    This is the ONLY place that touches DocumentIR internals. Downstream code (matching
    engine, translator, diagnostics) works exclusively with the
    `CurriculumMatchableSegment` interface.

    Parameters
    ----------
    caption_bindings
        Mapping from table segment_id to CaptionBinding (produced by
        `load_or_build_caption_bindings`).
    document_ir
        The loaded DocumentIR containing ordered segments.

    Returns
    -------
    list[CurriculumMatchableSegment]
        Segments in document order, ready for the matching engine.
    """

    logger.info("Preparing matchable segments from DocumentIR...")

    result: list[CurriculumMatchableSegment] = []

    # Build the set of block segment IDs that are bound captions. These blocks have
    # their text transferred to the table's caption_text field and must not participate
    # in pattern matching (text="").
    bound_caption_sids: set[str] = {
        binding.caption_segment_id
        for binding in caption_bindings.values()
        if binding.caption_segment_id
    }

    for idx, segment in enumerate(document_ir.segments):
        if not segment.slices:
            logger.warning(
                f"Segment {segment.segment_id} has no slices; skipping adapter."
            )
            continue

        page_index = segment.slices[0].page_index

        if segment.kind == "block":
            assert isinstance(segment, BlockSegment)
            block_type_val = segment.block_type.value if segment.block_type else None

            # Bound caption blocks have their content transferred to the table
            # CurriculumMatchableSegment's caption_text field. Neutralize the block's
            # text so it cannot accidentally match structural skeleton rules (e.g.
            # "palier\s+1" inside a caption like "Tableau 4 — Apprentissages ponctuels:
            # Palier 1, ...").
            is_bound = segment.segment_id in bound_caption_sids
            text = "" if is_bound else (extract_block_segment_text(segment) or "")

            if is_bound:
                logger.debug(
                    f"bound caption block {segment.segment_id}: "
                    f"text neutralized (will fall through to IGNORE)."
                )

            result.append(
                CurriculumMatchableSegment(
                    block_type=block_type_val,
                    document_order=idx,
                    is_bound_caption=is_bound,
                    page_index=page_index,
                    raw_segment=segment,
                    segment_id=segment.segment_id,
                    segment_kind="block",
                    text=text,
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
                CurriculumMatchableSegment(
                    block_type=None,
                    caption_gap_segments=(binding.gap_segments if binding else None),
                    caption_kind=binding.caption_kind if binding else None,
                    caption_page_index=(
                        binding.caption_page_index if binding else None
                    ),
                    caption_segment_id=(
                        binding.caption_segment_id if binding else None
                    ),
                    caption_text=binding.caption_text if binding else None,
                    columns_signature=segment.columns_signature,
                    document_order=idx,
                    header_rows_canonical=hrc,
                    page_index=page_index,
                    raw_segment=segment,
                    segment_id=segment.segment_id,
                    segment_kind="table",
                    text="",  # Tables matched via CAPTION/require_segment_kind
                )
            )

        else:
            logger.warning(
                f"Segment {segment.segment_id} has unknown kind "
                f"{segment.kind!r}; skipping."
            )

    logger.success(f"Prepared {len(result)} matchable segments.")

    return result


def segment_matches_node(
    *, node: CurriculumSkeletonNode, segment: CurriculumMatchableSegment
) -> bool:
    """Test whether a document segment matches a skeleton node using normalized
    phrase containment. Any match_phrase matching is sufficient (OR logic).

    Parameters
    ----------
    node
        The SkeletonNode to test against.
    segment
        The MatchableSegment to test.

    Returns
    -------
    bool
        True if any of the node's match_phrases match the segment.
    """

    if node.emit == CurriculumEmitPolicy.CONTAINER_ONLY:
        return False

    if not node.match_phrases:
        return False

    # Determine the segment text to match against based on match_target.
    if node.match_target == "caption":
        target_text = segment.caption_text
    else:
        target_text = segment.text

    if not target_text:
        return False

    normalized_target = _normalize_match_text(target_text)

    return any(
        _normalize_match_text(phrase) in normalized_target
        for phrase in node.match_phrases
    )


def translate_matched_segment(
    *,
    doc_key: str,
    matched: CurriculumMatchedSegment,
    role_order: list[NodeRole] | None = None,
) -> SegmentDecision:
    """Convert a CurriculumMatchedSegment into a SegmentDecision.

    Parameters
    ----------
    doc_key
        Document key for decision ID generation.
    matched
        The matched curriculum segment from the engine.
    role_order
        Custom role precedence for context_groupings sorting.

    Returns
    -------
    SegmentDecision
        A valid SegmentDecision ready for canonical IR compilation.

    Raises
    ------
    ValueError
        If the matched node has an unhandled emit policy.
    """

    node = matched.node
    seg = matched.segment
    context = build_context_groupings(
        ancestry=matched.ancestry, matched_node=node, role_order=role_order
    )
    decision_id = f"skeleton:{doc_key}:{seg.segment_id}"
    block_type = BlockType(seg.block_type) if seg.block_type else None

    # Combine text from bilingual pairs.
    all_texts = [seg.text or ""]

    for extra in matched.additional_segments:
        if extra.text:
            all_texts.append(extra.text)

    combined_text = "\n\n".join(t for t in all_texts if t.strip())

    # Ignore.
    if node.emit == CurriculumEmitPolicy.IGNORE:
        return SegmentDecision(
            block_type=block_type,
            confidence=1.0,
            context_groupings=[],
            decision_id=decision_id,
            decision_type=SegmentDecisionType.IGNORE,
            groupings=[],
            leaves=[],
            rationale=f"Skeleton IGNORE: '{node.id}'.",
            rows=[],
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
        )

    # EMIT_GROUPING.
    if node.emit == CurriculumEmitPolicy.EMIT_GROUPING:
        return SegmentDecision(
            block_type=block_type,
            confidence=1.0,
            context_groupings=context,
            decision_id=decision_id,
            decision_type=SegmentDecisionType.EMIT_GROUPINGS_ONLY,
            groupings=[
                GroupingDecision(
                    role=node.grouping_role,
                    title=node.canonical_name.primary,
                    source_label=node.source_label,
                    local_code=node.local_code,
                )
            ],
            leaves=[],
            rationale=(
                f"Skeleton EMIT_GROUPING: '{node.id}' " f"→ {node.grouping_role.value}."
            ),
            rows=[],
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
        )

    # EMIT_LEAF.
    if node.emit == CurriculumEmitPolicy.EMIT_LEAF:
        return SegmentDecision(
            block_type=block_type,
            confidence=1.0,
            context_groupings=context,
            decision_id=decision_id,
            decision_type=SegmentDecisionType.EMIT_LEAVES_ONLY,
            groupings=[],
            leaves=[
                LeafDecision(
                    role=node.leaf_role,
                    body=combined_text,
                    source_label=node.source_label,
                )
            ],
            rationale=(f"Skeleton EMIT_LEAF: '{node.id}' → {node.leaf_role.value}."),
            rows=[],
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
        )

    # EMIT_GROUPING_AND_LEAF.
    if node.emit == CurriculumEmitPolicy.EMIT_GROUPING_AND_LEAF:
        return SegmentDecision(
            block_type=block_type,
            confidence=1.0,
            context_groupings=context,
            decision_id=decision_id,
            decision_type=SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES,
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
            rationale=f"Skeleton EMIT_GROUPING_AND_LEAF: '{node.id}'.",
            rows=[],
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
        )

    # EMIT_TABLE_ROWS.
    if node.emit == CurriculumEmitPolicy.EMIT_TABLE_ROWS:
        return _translate_table_rows(
            context=context, decision_id=decision_id, node=node, seg=seg
        )

    raise ValueError(f"Unhandled emit policy: {node.emit}")


def translate_segments(
    *,
    curriculum_match_results: CurriculumMatchResult,
    doc_key: str,
    matchable_segments: list[CurriculumMatchableSegment],
    role_order: list[NodeRole] | None,
) -> list[SegmentDecision]:
    """Translate CurriculumMatchResult into a list of SegmentDecisions.

    Parameters
    ----------
    curriculum_match_results
        The raw CurriculumMatchResult from the matching engine.
    doc_key
        Document key for decision ID generation.
    matchable_segments
        The original list of matchable segments, used to determine document order and
        to translate unmatched segments.
    role_order
        Custom role precedence for context_groupings sorting.

    Returns
    -------
    list[SegmentDecision]
        A list of SegmentDecisions corresponding to the matched and unmatched segments.
    """

    logger.info("Translating curriculum match results to SegmentDecisions...")

    decisions: list[SegmentDecision] = []

    for matched_seg in curriculum_match_results.matched:
        decision = translate_matched_segment(
            doc_key=doc_key, matched=matched_seg, role_order=role_order
        )
        decisions.append(decision)

    for unmatched_seg in curriculum_match_results.unmatched:
        decision = translate_unmatched(doc_key=doc_key, seg=unmatched_seg)
        decisions.append(decision)

    # Sort by document order for consistency.
    seg_order: dict[str, int] = {
        s.segment_id: s.document_order for s in matchable_segments
    }
    decisions.sort(key=lambda d: seg_order.get(d.segment_id or "", 999_999))

    logger.success(f"Translated {len(decisions)} total SegmentDecisions.")

    return decisions


def translate_unmatched(
    *, doc_key: str, seg: CurriculumMatchableSegment
) -> SegmentDecision:
    """Convert an unmatched segment into an IGNORE SegmentDecision.

    Parameters
    ----------
    doc_key
        Document key for decision ID generation.
    seg
        The unmatched MatchableSegment.

    Returns
    -------
    SegmentDecision
        An IGNORE decision.
    """

    return SegmentDecision(
        block_type=BlockType(seg.block_type) if seg.block_type else None,
        confidence=1.0,
        context_groupings=[],
        decision_id=f"skeleton:{doc_key}:{seg.segment_id}:unmatched",
        decision_type=SegmentDecisionType.IGNORE,
        groupings=[],
        leaves=[],
        rationale="No skeleton node matched this segment.",
        rows=[],
        segment_id=seg.segment_id,
        segment_kind=seg.segment_kind,
    )
