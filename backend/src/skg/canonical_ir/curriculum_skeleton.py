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
    CurriculumGroupingRoleOverride,
    CurriculumSkeleton,
    CurriculumSkeletonNode,
    GroupingDecision,
    LeafDecision,
    RowDecision,
    SegmentDecision,
    SegmentDecisionSet,
    compute_decision_set_id,
)
from skg.canonical_ir.utils import CanonicalIRDirs, extract_block_segment_text
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.page_ir_extraction.schemas import TableRow, TextUnit
from skg.regexes import WS_RE
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

# (caption_segment, caption_text, caption_kind, caption_page_index, segment_index).
PendingCaption = tuple[BlockSegment, str, CaptionKind, int, int]


@dataclass
class CurriculumCursorJump:
    """Represents a large jump in the curriculum skeleton matching cursor, which may
    indicate a significant ordering misalignment between the document and curriculum
    skeleton.
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
    # captions have text="" so they cannot accidentally match structural curriculum
    # skeleton rules; they fall through to IGNORE as expected.
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
    """Structured diagnostics from a curriculum skeleton matching run.

    Attributes
    ----------
    caption_blocks_ignored
        Number of bound caption blocks that correctly fell through to IGNORE. These are
        block segments whose text was transferred to their table's `caption_text`
        field, so they do not participate in matching.
    container_only_nodes
        Number of CONTAINER_ONLY nodes (structural-only, never matched).
    cursor_jumps
        List of large cursor jumps (potential document/curriculum skeleton ordering issues).
    matched_nodes
        Number of distinct matchable nodes that received at least one match.
    matched_segments
        Number of source segments consumed by successful curriculum skeleton matches,
        including any `additional_segments` attached via multi-segment matching.
    total_curriculum_skeleton_nodes
        Total nodes in the curriculum skeleton (all types).
    total_matchable_nodes
        Nodes that participate in matching (not CONTAINER_ONLY).
    total_segments
        Total number of diagnostic segments considered for coverage in the document,
        excluding bound caption blocks that were intentionally neutralized and ignored.

    unexpected_skipped_node_ids
        IDs of matchable nodes that expected a match but received none. CONTAINER_ONLY
        and IGNORE nodes are excluded from this list.
    unmatched_segment_ids
        IDs of genuinely unmatched document segments, excluding bound caption blocks
        that were intentionally transferred to table captions and ignored.
    unmatched_segments
        Number of genuinely unmatched segments (same exclusion rule as above).
    """

    caption_blocks_ignored: int = 0
    container_only_nodes: int = 0
    cursor_jumps: list[CurriculumCursorJump] = field(default_factory=list)
    matched_nodes: int = 0
    matched_segments: int = 0
    total_curriculum_skeleton_nodes: int = 0
    total_matchable_nodes: int = 0
    total_segments: int = 0
    unexpected_skipped_node_ids: list[str] = field(default_factory=list)
    unmatched_segment_ids: list[str] = field(default_factory=list)
    unmatched_segments: int = 0

    @property
    def has_ordering_warnings(self) -> bool:
        """True when cursor jumps were recorded, indicating the document and curriculum
        skeleton ordering diverged at one or more points.

        Ordering warnings are informational--they often indicate cross-strand phrase
        collisions in the curriculum skeleton (ambiguous match phrases that match nodes
        in a different strand) rather than algorithm failures. Review the `cursor_jumps`
        list for details.

        Returns
        -------
        bool
            True if any cursor jumps were recorded.
        """

        return len(self.cursor_jumps) > 0

    @property
    def is_healthy(self) -> bool:
        """A curriculum skeleton match is healthy when > 90% of matchable nodes
        received at least one segment match AND no matchable (non-IGNORE) nodes were
        unexpectedly skipped.

        Cursor jumps are tracked separately via `has_ordering_warnings` and do NOT
        affect the health signal. Jumps typically indicate cross-strand phrase
        ambiguity in the curriculum skeleton rather than a fundamental matching failure.

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
        """Fraction of diagnostic segments that matched a curriculum skeleton node.

        Bound caption blocks intentionally transferred to table captions and ignored
        are excluded from the denominator.

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
            "  Curriculum Skeleton Match Report",
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
            "total_curriculum_skeleton_nodes": self.total_curriculum_skeleton_nodes,
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
    unmatched: list[CurriculumMatchableSegment]


@dataclass
class CurriculumResolvedColumnRole:
    """Resolved role for a single table column."""

    col_index: int
    kind: str  # "grouping", "leaf", or "skip"
    role_value: str  # e.g., "strand", "expectation"
    source_label: str  # Column header text (for source_label)

    grouping_role_overrides: list[CurriculumGroupingRoleOverride] = field(
        default_factory=list
    )


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

    # TextUnit (or any object with .text).
    if hasattr(cell, "text"):
        raw = cell.text
    elif isinstance(cell, dict):
        raw = cell.get("text", "")
    else:
        return str(cell).strip()

    # Raw may itself be a TextUnit or dict (nested wrapper).
    if hasattr(raw, "text"):
        raw = raw.text
    elif isinstance(raw, dict):
        raw = raw.get("text", "")

    return str(raw or "").strip()


def _classify_caption_kind(text: str) -> CaptionKind:
    """Classify a caption as table, figure, or unknown using normalized prefixes.

    The classifier is intentionally lightweight: it trims the text, lowercases it,
    collapses repeated whitespace, and then checks whether the caption begins with any
    known table or figure prefix from constants. The first matching family wins; if no
    prefix matches, the caption is classified as "unknown".

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

    Draining only removes already-consumed nodes that sit at the *front* of the
    remaining DFS-ordered node list. A pinned node blocks draining even if it has
    already been consumed, so that it remains probeable for `allow_multiple_segments`
    continuations.

    Examples
    --------
    1. Drain across consecutive consumed nodes
        Suppose:

            matchable_nodes = [n0, n1, n2, n3]
            cursor = 0
            consumed_node_ids = {"n0", "n1"}
            pinned_node_id = None

        The function sees that `n0` and `n1` are both already consumed, so it advances
        twice and stops at `n2`.

        Result:
            Returns `2`

    2. Stop at the first unconsumed node
        Suppose:

            matchable_nodes = [n0, n1, n2, n3]
            cursor = 1
            consumed_node_ids = {"n0", "n1"}
            pinned_node_id = None

        The node at index 1 (`n1`) is consumed, so the cursor advances to index 2.
        The node at index 2 (`n2`) is not consumed, so draining stops there.

        Result:
            Returns `2`

    3. Pinned node blocks draining
        Suppose:

            matchable_nodes = [n0, n1, n2]
            cursor = 1
            consumed_node_ids = {"n0", "n1"}
            pinned_node_id = "n1"

        Even though `n1` is already in `consumed_node_ids`, it is pinned, so draining
        must stop at index 1.

        Result:
            Returns `1`

    4. Drain after pin release
        Suppose a node had been pinned earlier, but the caller has just released the
        pin and calls:

            matchable_nodes = [n0, n1, n2]
            cursor = 1
            consumed_node_ids = {"n0", "n1"}
            pinned_node_id = None

        Now `n1` no longer blocks draining, so the cursor advances to the next
        unconsumed node.

        Result:
            Returns `2`

    5. Cursor already at an active node
        Suppose:

            matchable_nodes = [n0, n1, n2]
            cursor = 2
            consumed_node_ids = {"n0", "n1"}
            pinned_node_id = None

        The node at index 2 is not consumed, so no draining happens.

        Result:
            Returns `2`

    6. Cursor at end of list
        Suppose:

            matchable_nodes = [n0, n1]
            cursor = 2
            consumed_node_ids = {"n0", "n1"}
            pinned_node_id = None

        Since `cursor == len(matchable_nodes)`, the loop does not run.

        Result:
            Returns `2`

    Parameters
    ----------
    consumed_node_ids
        Set of node IDs that have already been matched.
    cursor
        The current cursor position index.
    matchable_nodes
        The full list of matchable curriculum skeleton nodes in DFS order.
    pinned_node_id
        The ID of the node currently pinned, or None if no node is pinned.

    Returns
    -------
    int
        The updated cursor position after draining.
    """

    while cursor < len(matchable_nodes):
        node_id = matchable_nodes[cursor].id

        if node_id in consumed_node_ids and node_id != pinned_node_id:
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

    cells = row.cells

    if not col_map:
        return _extract_fallback_content(
            cells=cells, default_leaf_role=default_leaf_role
        )

    groupings: list[GroupingDecision] = []
    leaves: list[LeafDecision] = []

    for col_role in col_map:
        if col_role.col_index >= len(cells) or col_role.kind == "skip":
            continue

        if not (cell_text := (_cell_to_text(cells[col_role.col_index]) or "").strip()):
            continue

        effective_col_role = _resolve_effective_role(
            cell_text=cell_text, col_role=col_role
        )

        if effective_col_role.kind == "skip":
            continue

        decision = _create_decision_from_role(
            cell_text=cell_text, col_role=effective_col_role
        )

        if isinstance(decision, GroupingDecision):
            groupings.append(decision)
        elif isinstance(decision, LeafDecision):
            leaves.append(decision)

    return groupings, leaves


def _handle_pending_caption_binding(
    *,
    caption_bindings: dict[str, CaptionBinding],
    current_index: int,
    current_page_index: int,
    max_gap_segments: int,
    max_page_distance: int,
    pending_caption: PendingCaption,
    segment: Segment,
    warnings: list[str],
) -> tuple[PendingCaption | None, bool]:
    """Process a pending caption by either binding it to a table or expiring it.

    The function is evaluated against *every* subsequent segment after a caption is
    seen. A pending caption is kept only while both of these remain true:

    1. The number of intervening non-table segments is within `max_gap_segments`.
    2. The forward page distance is within `max_page_distance`.

    When the current segment is a table and both limits still hold, the caption is
    bound to that table. Otherwise, the caption is dropped with a diagnostic warning.

    Parameters
    ----------
    caption_bindings
        The dictionary of existing table-to-caption bindings to update.
    current_index
        The current iteration index in the document segments.
    current_page_index
        The page index of the current segment.
    max_gap_segments
        The maximum number of non-table segments allowed between caption and table.
    max_page_distance
        The maximum forward page distance allowed between caption and table.
    pending_caption
        The metadata of the caption currently awaiting a table.
    segment
        The current document segment being evaluated.
    warnings
        The list of warning messages to append to.

    Returns
    -------
    tuple[PendingCaption | None, bool]
        A tuple containing:
            1. The updated pending_caption (`None` if bound or expired).
            2. A boolean `should_continue` indicating whether the main loop should
               skip further processing for this segment because it was consumed as the
               table target.
    """

    cap_seg, cap_text, cap_kind, cap_page, cap_index = pending_caption
    gap = max(0, current_index - cap_index - 1)
    page_dist = current_page_index - cap_page

    if page_dist > max_page_distance:
        msg = (
            f"Dangling caption dropped:\n"
            f"caption={cap_seg.segment_id} page_distance_exceeded={page_dist}\n"
            f"page_index={current_page_index}\n"
            f"segment_index={current_index}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return None, False

    # Attempt to bind to the next eligible table.
    if segment.kind == "table":
        if gap <= max_gap_segments:
            caption_bindings[segment.segment_id] = CaptionBinding(
                caption_kind=cap_kind,
                caption_page_index=cap_page,
                caption_segment_id=cap_seg.segment_id,
                caption_text=cap_text,
                gap_segments=gap,
                table_page_index=current_page_index,
                table_segment_id=segment.segment_id,
            )
        else:
            msg = (
                f"Dangling caption dropped:\n"
                f"caption={cap_seg.segment_id} gap_exceeded={gap}\n"
                f"page_index={current_page_index}\n"
                f"segment_index={current_index}"
            )
            logger.warning(msg)
            warnings.append(msg)

        # Whether bound or dropped for gap, the caption is no longer pending once a
        # candidate table is reached.
        return None, True

    if gap > max_gap_segments:
        msg = (
            f"Dangling caption dropped:\n"
            f"caption={cap_seg.segment_id} gap_exceeded={gap}\n"
            f"page_index={current_page_index}\n"
            f"segment_index={current_index}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return None, False

    return pending_caption, False


def _normalize_match_text(text: str) -> str:
    """Normalize match phrase text for phrase matching.

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

    # NFKD decomposes accented characters into base character + combining mark.
    text = unicodedata.normalize("NFKD", text)

    # Strip combining diacritical marks (category "Mn").
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")

    text = text.casefold()
    text = WS_RE.sub(" ", text).strip()
    return text


def _probe_nodes(
    *,
    consumed_node_ids: set[str],
    end: int,
    matchable_nodes: list[CurriculumSkeletonNode],
    normalized_match_phrases: dict[str, list[str]] | None = None,
    pinned_node_id: str | None,
    segment: CurriculumMatchableSegment,
    start: int,
) -> tuple[int, CurriculumSkeletonNode] | None:
    """Find the first matching **unconsumed** node in the half-open window [start, end).

    Already-consumed nodes are skipped except for the currently pinned node, which must
    remain matchable for multi-segment continuations. Matching is deterministic and
    first-hit-wins: the first node in DFS-probe order whose phrases match the segment
    is returned immediately.

    Parameters
    ----------
    consumed_node_ids
        Set of node IDs that have already been matched.
    end
        The end index (exclusive) for the probe window.
    matchable_nodes
        The full list of matchable curriculum skeleton nodes in DFS order.
    normalized_match_phrases
        Optional pre-computed mapping from node.id to normalized match phrases.
    pinned_node_id
        The ID of the node currently pinned, or None if no node is pinned.
    segment
        The document segment being matched.
    start
        The start index (inclusive) for the probe window.

    Returns
    -------
    tuple[int, CurriculumSkeletonNode] | None
        A tuple of `(matched_index, matched_node)` if a match is found, else None.
    """

    # Cache normalized texts to avoid redundant normalizations inside the loop.
    normalized_segment_text = None
    normalized_segment_caption = None

    # Test whether a segment matches a curriculum skeleton node via the normalized
    # match phrases. Any matching phrase is sufficient (OR logic). The target text
    # comes from `segment.text` for ordinary text-target nodes, or
    # `segment.caption_text` for caption-target nodes. Nodes with no phrases,
    # container-only nodes, and segments whose relevant target text is empty are
    # non-matches.
    for idx in range(start, min(end, len(matchable_nodes))):
        node = matchable_nodes[idx]

        if (
            (node.id in consumed_node_ids and node.id != pinned_node_id)
            or node.emit == CurriculumEmitPolicy.CONTAINER_ONLY
            or not node.match_phrases
        ):
            continue

        # Determine the segment text to match against based on match_target.
        if node.match_target == "caption":
            target_text = segment.caption_text

            if not target_text:
                continue

            normalized_segment_caption = (
                normalized_segment_caption or _normalize_match_text(target_text)
            )
            normalized_target = normalized_segment_caption
        else:
            target_text = segment.text

            if not target_text:
                continue

            normalized_segment_text = normalized_segment_text or _normalize_match_text(
                target_text
            )
            normalized_target = normalized_segment_text

        # Use pre-computed normalized phrases when available; fall back to on-the-fly
        # normalization for callers that don't supply the cache.
        if normalized_match_phrases is not None and node.id in normalized_match_phrases:
            is_match = any(
                phrase in normalized_target
                for phrase in normalized_match_phrases[node.id]
            )
        else:
            is_match = any(
                _normalize_match_text(phrase) in normalized_target
                for phrase in node.match_phrases
            )

        if is_match:
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

    Examples
    --------
    1. Normal single-segment match
        Suppose the current state is:

            cursor = 3
            consumed_node_ids = {"n0", "n1", "n2"}
            pinned_node_id = None

        and the current segment matches node `n3`, with `probe_idx=3` and
        `node.allow_multiple_segments=False`.

        Result:
            - A new `CurriculumMatchedSegment` is appended to `results`
            - "n3" is added to `consumed_node_ids`
            - `pinned_node_id` stays `None`
            - `_drain_cursor()` advances the cursor past `n3` if it is now the next
                consecutive consumed node

    2. Multi-segment continuation on the same node
        Suppose the previous result already matched node `tableau-1.1.1`, and that node
        has `allow_multiple_segments=True`. A new segment also matches the same node.

        Before:
            results[-1].node.id == "tableau-1.1.1"
            node.id == "tableau-1.1.1"
            pinned_node_id == "tableau-1.1.1"

        Result:
            - The new segment is appended to `results[-1].additional_segments`
            - No new top-level result is created
            - `consumed_node_ids` is unchanged
            - `cursor` is unchanged
            - The pin stays active

    3. Matching a later node records a cursor jump
        Suppose:

            cursor = 5
            consumed_node_ids = {"n0", "n1", "n2", "n3", "n4"}
            probe_idx = 8

        and nodes `n5`, `n6`, and `n7` are still unconsumed. If the segment matches
        node `n8`, then `_count_skipped_between()` returns 3 for the window `[5, 8)`.

        Result:
            - A `CurriculumCursorJump` is appended, because more than 1 unconsumed
                node was skipped
            - The match for `n8` is still recorded normally
            - "n8" is added to `consumed_node_ids`

    4. Matching a node that allows multiple segments creates a new pin
        Suppose the segment matches node `tableau-1.2.1`, and that node has
        `allow_multiple_segments=True`.

        Result:
            - A new `CurriculumMatchedSegment` is appended
            - "tableau-1.2.1" is added to `consumed_node_ids`
            - `pinned_node_id` becomes `"tableau-1.2.1"`
            - `_drain_cursor()` does NOT drain past that pinned node, even though it is
                already consumed

    5. Matching a non-pinned node releases any previous pin
        Suppose the previous pin was "tableau-1.1.1", but the current segment now
        matches "tableau-1.1.2", and "tableau-1.1.2" does not allow multiple segments.

        Result:
            - The new node is recorded as a normal match
            - The previous pin is released
            - `pinned_node_id` becomes `None`
            - `_drain_cursor()` can now advance past any consecutive consumed nodes at
                the head of the list

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
        The full list of matchable curriculum skeleton nodes in DFS order.
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

        # Pin stays, consumed_node_ids unchanged, and cursor stays.
        return cursor, pinned_node_id

    # Record a cursor jump when more than one unconsumed node was skipped.
    skipped_count = sum(
        1
        for i in range(cursor, probe_idx)
        if matchable_nodes[i].id not in consumed_node_ids
    )

    if skipped_count > 1 and cursor < len(matchable_nodes):
        logger.warning(
            f"Cursor jump: skipped {skipped_count} nodes to match segment "
            f"{segment.segment_id} to node {node.id}."
        )
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

    if node.allow_multiple_segments:
        pinned_node_id = node.id  # Update pin to the newly matched node
    else:
        pinned_node_id = None  # Release pin since node doesn't allow multiple segments

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
    """Match curriculum skeleton `column_mappings` against actual table headers.

    Builds per-column header signatures by joining all non-empty header-row cells for
    each column using the literal separator " / ". Each column is tested against every
    mapping in order; first match wins. Unmatched columns default to `skip`.

    This function is basically making a schema for a table:

    [
        "column 0 = week",
        "column 1 = expectation",
        "column 2 = guidance",
        "column 3 = descriptor",
    ]

    It does not emit any curriculum content itself. It just prepares the lookup table
    that row parsing will use next.

    NB: `column_mappings` patterns use inline `(?i)` flags (self-contained), so this
    function compiles with `re.UNICODE` only, not `re.IGNORECASE`.

    Examples
    --------
    1. Simple one-row header mapping
        Suppose the table header is:

            [
                ["Semaine", "Vocabulaire", "Grammaire", "Production"]
            ]

        and the curriculum skeleton provides:

            [
                {"header_pattern": "(?i)semaine", "role": "grouping:week"},
                {"header_pattern": "(?i)vocabulaire", "role": "leaf:expectation"},
                {"header_pattern": "(?i)grammaire", "role": "leaf:expectation"},
                {"header_pattern": "(?i)production", "role": "leaf:expectation"},
            ]

        Then the function returns:

            [
                CurriculumResolvedColumnRole(
                    col_index=0,
                    kind="grouping",
                    role_value="week",
                    source_label="Semaine",
                ),
                CurriculumResolvedColumnRole(
                    col_index=1,
                    kind="leaf",
                    role_value="expectation",
                    source_label="Vocabulaire",
                ),
                CurriculumResolvedColumnRole(
                    col_index=2,
                    kind="leaf",
                    role_value="expectation",
                    source_label="Grammaire",
                ),
                CurriculumResolvedColumnRole(
                    col_index=3,
                    kind="leaf",
                    role_value="expectation",
                    source_label="Production",
                ),
            ]

        This means later row parsing will treat the first column as a grouping and the
        other three as emitted leaves.

    2. Multi-row header collapse
        Suppose the table has two header rows:

            [
                ["Activités", "Expression orale", "Expression orale", "Lecture"],
                ["Semaines", "Objectifs", "Contenus", "Objectifs"],
            ]

        The function builds one header signature per column by joining the non-empty
        header cells for that column with " / ":

            [
                "Activités / Semaines",
                "Expression orale / Objectifs",
                "Expression orale / Contenus",
                "Lecture / Objectifs",
            ]

        Matching is performed against those joined signatures, not against each header
        row separately.

    3. Unmatched column becomes skip
        Suppose the header is:

            [
                ["Semaine", "Notes de bas de page", "Production"]
            ]

        and no curriculum skeleton mapping matches "Notes de bas de page".

        Then that column resolves to:

            CurriculumResolvedColumnRole(
                col_index=1,
                kind="skip",
                role_value="",
                source_label="Notes de bas de page",
            )

        This means later row parsing will ignore that column completely.

    4. First match wins when multiple patterns match
        Suppose the header string is:

            "Objectif spécifique"

        and two mappings both match it:

            [
                {"header_pattern": "(?i)objectif", "role": "leaf:guidance"},
                {"header_pattern": "(?i)objectif spécifique", "role": "leaf:expectation"},
            ]

        The function logs a warning and uses the first match only. So this column would
        resolve to `leaf:guidance`, even though the second mapping is more specific.

        This means the order of `column_mappings` in the curriculum skeleton matters.

    5. source_label_override replaces the actual header text
        Suppose the header is:

            "nisaru jukki"

        and the matching curriculum skeleton rule is:

            {
                "header_pattern": "(?i)objectifs? sp[ée]cifiques?|nisaru jukki",
                "role": "leaf:expectation",
                "source_label_override": "Objectif spécifique",
            }

        Then the resolved role uses:

            source_label="Objectif spécifique"

        rather than the raw extracted header text "nisaru jukki".

        This is useful when bilingual or noisy headers should normalize to a cleaner
        source label.

    6. No headers or zero-width header rows
        If `header_rows_canonical` is empty, or all header rows are empty, the function
        returns an empty list.

        In that case, later row parsing falls back to default row handling rather than
        column-aware extraction.

    Parameters
    ----------
    column_mappings
        Column-to-role mappings from the curriculum skeleton node.
    header_rows_canonical
        Canonical header rows as `list[list[str]]` from the table segment.

    Returns
    -------
    list[CurriculumResolvedColumnRole]
        One entry per column in the (widest) header row.
    """

    # Determine column count from the widest header row.
    n_cols = max(len(row) for row in header_rows_canonical)

    if not header_rows_canonical or n_cols == 0:
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
                    grouping_role_overrides=matched_mapping.grouping_role_overrides,
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


def _resolve_effective_role(
    *, cell_text: str, col_role: CurriculumResolvedColumnRole
) -> CurriculumResolvedColumnRole:
    """Resolve the effective semantic role for one table cell.

    This function applies per-cell overrides to a column's already-resolved role.

    It is used after `_resolve_column_mappings()`. At that earlier stage, the pipeline
    has already decided what each column *usually* means based on the table headers.
    However, some grouping columns contain mixed content across rows. For example, a
    column may usually represent `week`, but certain row values such as "Palier 2" or
    "Compétences de base" should be treated differently.

    This function handles those cases by checking the cell's text against the column's
    `grouping_role_overrides` in order. The first matching override wins.

    What this function does
    -----------------------

    1. If the resolved column role is not a grouping role, return it unchanged.
    2. If the grouping column has no overrides, return it unchanged.
    3. Otherwise, test each override's `cell_pattern` against the current cell text.
    4. On the first match:
        - If the override role is `skip`, return a skip role for this cell
        - Otherwise return a new `CurriculumResolvedColumnRole` using the override role
    5. If no override matches, return the original column role unchanged.

    NB:

    1. Overrides are evaluated in order; first match wins.
    2. This function only applies overrides to grouping columns.
    3. It does not emit any decisions itself. It only chooses the effective role that
        `_create_decision_from_role()` will use next.
    4. The returned object keeps the original `col_index` and `source_label`.
    5. Without `_resolve_effective_role()` (or something similar), we would have to
        split one physical column into multiple separate column mappings based on row
        type, which is awkward and often impossible. This function lets us say: “This
        column is usually weeks, except when a particular row text tells us it is
        really a substage marker or noise we should drop.”

    Examples
    --------
    1. No overrides -> keep original role
        Suppose the resolved column role is:

            CurriculumResolvedColumnRole(
                col_index=0,
                kind="grouping",
                role_value="week",
                source_label="Semaine",
                grouping_role_overrides=[],
            )

        and the current cell text is:

            "Semaine 3"

        Since there are no overrides, the function returns the original role unchanged:

            kind="grouping", role_value="week"

    2. Override grouping:week -> grouping:substage
        Suppose the resolved column role is:

            CurriculumResolvedColumnRole(
                col_index=0,
                kind="grouping",
                role_value="week",
                source_label="Activités",
                grouping_role_overrides=[
                    CurriculumGroupingRoleOverride(
                        cell_pattern="(?i)^\\s*(j[ée]ego|palier|semaines)\\b",
                        role="grouping:substage",
                    )
                ],
            )

        and the cell text is:

            "Palier 2"

        The override matches, so the function returns a new effective role:

            CurriculumResolvedColumnRole(
                col_index=0,
                kind="grouping",
                role_value="substage",
                source_label="Activités",
            )

        This means the cell will be treated as a `substage` grouping rather than a
        `week` grouping.

    3. Override to skip
        Suppose the same grouping column has an override:

            CurriculumGroupingRoleOverride(
                cell_pattern="(?i)^\\s*(manoore|comp[ée]tences?\\s+de\\s+base)\\b",
                role="skip",
            )

        and the cell text is:

            "Compétences de base"

        Then the function returns:

            CurriculumResolvedColumnRole(
                col_index=0,
                kind="skip",
                role_value="",
                source_label="Activités",
            )

        This means the cell is ignored and produces no grouping or leaf.

    4. No override matches -> keep original grouping role
        Suppose the base column role is still `grouping:week`, and the cell text is:

            "Semaine 12"

        If none of the overrides match that text, the function returns the original role:

            kind="grouping", role_value="week"

        So the cell is treated normally as a week grouping.

    5. Leaf columns are never overridden here
        Suppose the resolved column role is:

            CurriculumResolvedColumnRole(
                col_index=2,
                kind="leaf",
                role_value="expectation",
                source_label="Objectif spécifique",
            )

        and the cell text is:

            "Lire un texte court"

        Even if overrides were present on the object, this function would return the
        role unchanged because only grouping columns are eligible for per-cell
        overrides.

    Parameters
    ----------
    cell_text
        The extracted and stripped text from the current table cell.
    col_role
        The resolved column role for this column, possibly including
        `grouping_role_overrides`.

    Returns
    -------
    CurriculumResolvedColumnRole
        The effective role to use for this specific cell.
    """

    if col_role.kind != "grouping" or not col_role.grouping_role_overrides:
        return col_role

    for override in col_role.grouping_role_overrides:
        if not re.search(override.cell_pattern, cell_text, re.UNICODE):
            continue

        if override.role == "skip":
            return CurriculumResolvedColumnRole(
                col_index=col_role.col_index,
                kind="skip",
                role_value="",
                source_label=col_role.source_label,
            )

        override_kind, override_value = override.role.split(":", 1)
        return CurriculumResolvedColumnRole(
            col_index=col_role.col_index,
            kind=override_kind,
            role_value=override_value,
            source_label=col_role.source_label,
        )

    return col_role


def _translate_table_rows(
    *,
    context: list[GroupingDecision],
    decision_id: str,
    node: CurriculumSkeletonNode,
    seg: CurriculumMatchableSegment,
) -> SegmentDecision:
    """Build a SegmentDecision with RowDecision[] from a matched table segment.

    This function is used when a curriculum skeleton node has `emit=EMIT_TABLE_ROWS`.
    Instead of treating the whole table as one big leaf, it parses the table row by row
    and turns each usable row into a `RowDecision`.

    What this function does
    -----------------------

    1. Confirm that the matched segment is really a `TableSegment`.
    2. Resolve the curriculum skeleton node's `column_mappings` against the table's
        actual header rows, producing a column-by-column semantic role map.
    3. Choose the best available row source in this order:
        `rows_filldown` -> `rows_grid` -> raw `rows`.
    4. Skip the table's header rows.
    5. For each remaining row:
        - Extract row-level groupings and leaves using the resolved column map
        - Skip rows that produce no usable content
        - Emit a `RowDecision` for rows that do produce content
    6. Optionally add one segment-level grouping from `node.grouping_role`. This is
        useful when the whole table itself represents a container such as a unit, week
        band, or substage.
    7. Set the final `decision_type` from the actual outputs:
        - Groupings + leaves -> `EMIT_GROUPINGS_AND_LEAVES`
        - Leaves only -> `EMIT_LEAVES_ONLY`
        - Groupings only -> `EMIT_GROUPINGS_ONLY`
        - Nothing at all -> `UNRESOLVED`

    NB:

    1. `context_groupings` are outer ancestors that were already built before this
        function is called.
    2. `groupings` on the returned `SegmentDecision` are segment-level outputs for the
      matched table as a whole.
    3. `rows` contain the row-by-row curriculum content extracted from the table body.
    4. Even though `context_groupings` is passed in, they do not determine the decision
        type. The decision type is computed only from the segment-level grouping plus
        the row outputs. That matches the SegmentDecision validators, which do not
        allow a pretend emit decision whose only content is context.

    Examples
    --------
    1. Weekly plan table with one grouping column and several expectation columns
        Suppose the table looks like this:

            | Semaine | Vocabulaire | Grammaire | Production |
            |---------|-------------|-----------|------------|
            | S1      | ...         | ...       | ...        |
            | S2      | ...         | ...       | ...        |

        and the curriculum skeleton maps:
            - "Semaine" -> grouping:week
            - "Vocabulaire" -> leaf:expectation
            - "Grammaire" -> leaf:expectation
            - "Production" -> leaf:expectation

        Then each body row becomes something like:

            RowDecision(
                row_index=1,
                groupings=[GroupingDecision(role="week", title="S1")],
                leaves=[
                    LeafDecision(role="expectation", body="..."),
                    LeafDecision(role="expectation", body="..."),
                    LeafDecision(role="expectation", body="..."),
                ],
            )

        Because the rows produce both groupings and leaves, the returned
        `SegmentDecision.decision_type` becomes `EMIT_GROUPINGS_AND_LEAVES`.

    2. Table with only leaf columns
        Suppose the table looks like this:

            | Objectif spécifique | Contenu |
            |---------------------|---------|
            | Lire des mots ...   | ...     |
            | Identifier ...      | ...     |

        and both columns map to leaf roles.

        Then each row produces leaves but no row-level groupings, for example:

            RowDecision(
                row_index=1,
                groupings=[],
                leaves=[
                    LeafDecision(role="expectation", body="Lire des mots ..."),
                    LeafDecision(role="guidance", body="..."),
                ],
            )

        If the node also has no `grouping_role`, the overall decision type becomes
        `EMIT_LEAVES_ONLY`.

    3. Table with only grouping output
        Suppose the table body is acting like a structural index rather than carrying
        actual statement text, for example:

            | Palier |
            |--------|
            | Palier 1 |
            | Palier 2 |

        and the curriculum skeleton maps the only column to `grouping:substage`.

        Then rows may produce only row-level groupings:

            RowDecision(
                row_index=1,
                groupings=[GroupingDecision(role="substage", title="Palier 1")],
                leaves=[],
            )

        In that case the overall decision type becomes `EMIT_GROUPINGS_ONLY`.

    4. Segment-level grouping plus row-level leaves
        Suppose the matched table node itself has `grouping_role="unit"` and
        `canonical_name.primary="Communication écrite"`, and the rows produce only leaf
        expectations.

        Then the returned decision contains:
            - One top-level `GroupingDecision(role="unit", title="Communication écrite")`
            - Many row-level leaves inside `rows`

        Even if rows have no row-level groupings, the presence of the segment-level
        grouping plus row-level leaves means the decision type becomes
        `EMIT_GROUPINGS_AND_LEAVES`.

    5. Fallback when no column mappings match
        If `_resolve_column_mappings()` cannot match any headers, row extraction falls
        back to `_extract_fallback_content()`. In that fallback mode, the function joins
        all non-empty cells in a row into one leaf body using the node's default
        `leaf_role`.

        So a row like:

            | S1 | Les voyelles | Lecture orale |

        may become:

            RowDecision(
                row_index=1,
                groupings=[],
                leaves=[
                    LeafDecision(
                        role=node.leaf_role,
                        body="S1\\n\\nLes voyelles\\n\\nLecture orale",
                    )
                ],
            )

        This is a recovery path for badly mapped tables, not the preferred behavior.

    6. Empty or badly mapped table -> UNRESOLVED
        If no body row produces any groupings or leaves at all, the function does not
        return an empty emit decision. Instead it marks the table as `UNRESOLVED` and
        clears `context_groupings`, `groupings`, and `rows` so the result satisfies the
        `SegmentDecision` schema.

        This usually means:
            - The wrong table was matched
            - The header patterns did not match the real headers
            - The table body was empty
            - Or the table structure was too broken for row extraction

    Parameters
    ----------
    context
        Pre-built context_groupings from curriculum skeleton ancestry.
    decision_id
        Stable decision ID for this table decision.
    node
        The matched curriculum skeleton node. Must provide `column_mappings`, and may
        optionally provide a segment-level `grouping_role`.
    seg
        The matched table segment wrapper.

    Returns
    -------
    SegmentDecision
        A table-backed decision whose main payload is usually stored in `rows[]`.

    Raises
    ------
    TypeError
        If `seg.raw_segment` is not a `TableSegment`.
    """

    if not isinstance(seg.raw_segment, TableSegment):
        raise TypeError(
            f"EMIT_TABLE_ROWS matched a non-table segment: {seg.segment_id}. "
            f"raw_segment type: {type(seg.raw_segment).__name__}"
        )

    table_seg: TableSegment = seg.raw_segment

    # Build column index -> role mapping from curriculum skeleton + table headers.
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

    hrc = table_seg.header_row_count or 0
    row_decisions: list[RowDecision] = []

    # Skip header rows.
    for abs_i, row in enumerate(rows_source[hrc:], start=hrc):
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
            f"{len(rows_source) - hrc} data rows "
            f"(bad column mapping or empty table body). "
            f"Marking as UNRESOLVED (curriculum skeleton node '{node.id}' matched but "
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
            f"Curriculum skeleton TABLE: '{node.id}' → {len(row_decisions)} data rows."
            if not is_noop
            else (
                f"Curriculum skeleton TABLE UNRESOLVED: '{node.id}' matched but column "
                f"extraction produced 0 groupings and 0 leaves from "
                f"{len(rows_source) - hrc} data rows."
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

    The reason this function exists is that later, when `match_curriculum()` finds that
    a segment matched some node, `_record_match()` grabs
    `ancestry = ancestry_map[node.id]` and stores it on the `CurriculumMatchedSegment`.
    That ancestry is then used downstream to build context groupings and keep the
    matched segment attached to the right structural path.

    Parameters
    ----------
    root
        The root `CurriculumSkeletonNode`.

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
        assert (
            node.id not in result
        ), f"Duplicate node ID detected in curriculum skeleton: {result}"
        result[node.id] = chain

        for child in node.children:
            _walk(ancestors=chain, node=child)

    _walk(ancestors=[], node=root)
    return result


def build_caption_bindings(
    *,
    bind_unknown_caption: bool = True,
    creation_dirs: CanonicalIRDirs,
    document_ir: DocumentIR,
    max_gap_segments: int,
    max_page_distance: int,
) -> dict[str, CaptionBinding]:
    """Build deterministic caption-to-table bindings before curriculum skeleton
    matching.

    Many curriculum PDFs place a short caption or label block immediately before a
    table. That caption is usually not curriculum content itself, but it often carries
    critical context (grade, subject, theme, table meaning) needed to interpret the
    table.

    This function scans `document_ir.segments` in document order and maintains at most
    one pending caption at a time. A pending caption is bound to the first later table
    segment that falls within the configured gap/page limits. If another bindable
    caption appears first, the older pending caption is intentionally overwritten and a
    warning is emitted. Figure captions are ignored, and unknown captions are bound
    only when `bind_unknown_caption=True`.

    The resulting bindings are persisted to disk and later injected into
    `CurriculumMatchableSegment` table payloads as provenance/context.

    Examples
    --------
    1. Immediate same-page binding
        If a caption block is followed directly by a table, the caption binds to that
        table with `gap_segments=0`.

        Segment order:
            0. caption("Tableau 1.2.1")
            1. table("tbl-001")

        Result:
            `caption_bindings["tbl-001"]` points to the caption from segment 0.

    2. Binding across intervening non-table segments
        A caption may still bind when a small number of non-table segments appear
        between the caption and the table, as long as the gap is within
        `max_gap_segments`.

        Segment order:
            0. caption("Tableau 3")
            1. paragraph("Intro text")
            2. table("tbl-002")

        Result:
            With `max_gap_segments >= 1`, the caption binds to `tbl-002` with
            `gap_segments=1`.

    3. Cross-page binding
        A caption on one page may bind to a table on the next page when the forward
        page distance is within `max_page_distance`.

        Segment order:
            10. caption("Table 4") on page 5
            11. paragraph(...) on page 5
            12. table("tbl-003") on page 6

        Result:
            With `max_page_distance >= 1` and an allowed gap, the caption binds to
            `tbl-003`.

    4. Figure captions are ignored
        Captions classified as figures do not become pending captions and are never
        bound to tables.

        Segment order:
            0. caption("Figure 2: ...")
            1. table("tbl-004")

        Result:
            No caption binding is created for `tbl-004`.

    5. Unknown captions are optional
        Captions whose kind cannot be classified are only considered bindable when
        `bind_unknown_caption=True`.

        Segment order:
            0. caption("Bilingual competency overview")
            1. table("tbl-005")

        Result:
            - If `bind_unknown_caption=True`, the caption may bind to `tbl-005`.
            - If `bind_unknown_caption=False`, no binding is created.

    6. Caption dropped when the gap is too large
        A pending caption is dropped once the next candidate table is too far away in
        non-table segments.

        Segment order:
            0. caption("Table 7")
            1. paragraph(...)
            2. paragraph(...)
            3. table("tbl-006")

        Result:
            With `max_gap_segments=1`, the caption is dropped and a warning is
            recorded. No binding is created for `tbl-006`.

    7. Caption dropped when the page distance is too large
        A pending caption is dropped once the current segment is beyond the allowed
        forward page distance, even if a matching table appears later.

        Segment order:
            0. caption("Table 8") on page 2
            ...
            8. table("tbl-007") on page 5

        Result:
            With `max_page_distance=1`, the caption is dropped before reaching
            `tbl-007`, and a warning is recorded.

    8. New caption overwrites an older pending caption
        If a second bindable caption appears before the first pending caption reaches a
        table, the older pending caption is replaced. This is intentional: only one
        pending caption is tracked at a time.

        Segment order:
            0. caption("Tableau A")
            1. caption("Tableau B")
            2. table("tbl-008")

        Result:
            `tbl-008` binds to "Tableau B". The older pending caption ("Tableau A") is
            overwritten and a warning is recorded.

    9. End-of-document cleanup
        If the document ends while a caption is still pending, the caption is dropped
        and a warning is written.

        Segment order:
            0. caption("Table 9")
            1. paragraph("Closing remarks")

        Result:
            No binding is created, and the pending caption is reported as dangling at
            end of document.

    Parameters
    ----------
    bind_unknown_caption
        Whether to bind captions whose kind cannot be confidently classified.
    creation_dirs
        The canonical IR creation directories.
    document_ir
        The DocumentIR to process.
    max_gap_segments
        The maximum number of non-table segments allowed between caption and table.
    max_page_distance
        The maximum forward page distance allowed between caption and table.

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings_fp = creation_dirs.caption_binding / "caption_bindings.json"
    warnings_fp = creation_dirs.caption_binding / "caption_binding_warnings.json"

    caption_bindings: dict[str, CaptionBinding] = {}
    warnings: list[str] = []
    pending_caption: PendingCaption | None = None

    for index, segment in enumerate(document_ir.segments):
        assert segment.slices, f"Segment {segment.segment_id} has no slices."
        page_index = segment.slices[0].page_index
        assert (
            isinstance(page_index, int) and page_index >= 0
        ), f"Segment {segment.segment_id} has invalid page index: {page_index!r}"

        assert (
            segment.kind == "block"
            and isinstance(segment, BlockSegment)
            or segment.kind == "table"
            and isinstance(segment, TableSegment)
        ), (
            f"Segment {segment.segment_id} has invalid kind/type combination: "
            f"declared kind={segment.kind}, actual type={type(segment).__name__}"
        )

        # First, give any existing pending caption a chance to bind or expire against
        # the current segment. Doing this before processing a new caption block keeps
        # overwrite behavior explicit and ensures page/gap expiry is checked against
        # every subsequent segment, including ignored figure captions.
        if pending_caption is not None:
            pending_caption, should_continue = _handle_pending_caption_binding(
                caption_bindings=caption_bindings,
                current_index=index,
                current_page_index=page_index,
                max_gap_segments=max_gap_segments,
                max_page_distance=max_page_distance,
                pending_caption=pending_caption,
                segment=segment,
                warnings=warnings,
            )

            if should_continue:
                continue

        if segment.kind != "block":
            continue

        caption_text = extract_block_segment_text(segment)

        if segment.block_type != BlockType.CAPTION or not caption_text:
            continue

        kind = _classify_caption_kind(caption_text)

        if kind == "figure" or (kind == "unknown" and not bind_unknown_caption):
            continue

        if pending_caption is not None:
            prev_seg = pending_caption[0]
            msg = (
                f"Pending caption overwritten: {prev_seg.segment_id} -> "
                f"{segment.segment_id}"
            )
            logger.warning(msg)
            warnings.append(msg)

        pending_caption = (segment, caption_text, kind, page_index, index)

    # Cleanup dangling caption at end of document.
    if pending_caption is not None:
        cap_seg = pending_caption[0]
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


def build_context_groupings(
    *,
    ancestry: list[CurriculumSkeletonNode],
    context_groupings_role_order: list[NodeRole] | None = None,
    matched_node: CurriculumSkeletonNode,
) -> list[GroupingDecision]:
    """Build context groupings from the curriculum skeleton ancestry chain.

    We include ancestors that:

    1. Have a `grouping_role` (i.e., not None, FRAMEWORK, etc.).
    2. Are NOT the matched node itself (that goes in `groupings`).
    3. Are `EMIT_GROUPING`/`EMIT_GROUPING_AND_LEAF`/`EMIT_TABLE_ROWS` (i.e., visible in
        document), OR are `CONTAINER_ONLY` with `implicit=True`.

    The result is sorted by role precedence (outer -> inner).

    Parameters
    ----------
    ancestry
        Full ancestry chain from root to matched node (inclusive).
    context_groupings_role_order
        Custom role precedence order. Falls back to
        `DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER` if not provided.
    matched_node
        The node that was matched (excluded from context).

    Returns
    -------
    list[GroupingDecision]
        Sorted context groupings for the SegmentDecision.
    """

    precedence = {
        role: i
        for i, role in enumerate(
            context_groupings_role_order or DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER
        )
    }
    context: list[GroupingDecision] = []

    visible_grouping_emits = {
        CurriculumEmitPolicy.EMIT_GROUPING,
        CurriculumEmitPolicy.EMIT_GROUPING_AND_LEAF,
        CurriculumEmitPolicy.EMIT_TABLE_ROWS,
    }

    for node in ancestry:
        # Stop before the matched node itself.
        if node.id == matched_node.id:
            break

        # Skip nodes without grouping roles.
        if node.grouping_role in (None, NodeRole.FRAMEWORK):
            continue

        # CONTAINER_ONLY nodes are invisible UNLESS implicit=True. Other ancestor node
        # types only contribute context when they are grouping-bearing visible emits.
        # This keeps IGNORE/EMIT_LEAF ancestors from leaking into context_groupings
        # when a curriculum skeleton accidentally sets grouping_role.
        is_implicit_container = (
            node.emit == CurriculumEmitPolicy.CONTAINER_ONLY and node.implicit
        )
        is_visible_emit = node.emit in visible_grouping_emits

        if not (is_implicit_container or is_visible_emit):
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
    """Flatten **all** curriculum skeleton nodes into DFS order (including CONTAINER_ONLY).

    Parameters
    ----------
    root
        The root CurriculumSkeletonNode.

    Returns
    -------
    list[CurriculumSkeletonNode]
        All nodes in DFS traversal order.
    """

    nodes: list[CurriculumSkeletonNode] = []

    def _walk(node: CurriculumSkeletonNode) -> None:
        """DFS walk to collect all nodes.

        Parameters
        ----------
        node
            Current curriculum skeleton node being visited.
        """

        nodes.append(node)

        for child in node.children:
            _walk(child)

    _walk(root)
    return nodes


def dfs_matchable(root: CurriculumSkeletonNode) -> list[CurriculumSkeletonNode]:
    """Flatten the curriculum skeleton into DFS order, keeping only matchable nodes.

    A node is matchable if it is not `CONTAINER_ONLY` and has at least one
    `match_phrase`. This includes `IGNORE` nodes, because they still need to consume
    matching document segments deterministically.

    Parameters
    ----------
    root
        The root `CurriculumSkeletonNode`.

    Returns
    -------
    list[CurriculumSkeletonNode]
        Matchable nodes in DFS traversal order.
    """

    nodes: list[CurriculumSkeletonNode] = []

    def _walk(node: CurriculumSkeletonNode) -> None:
        """DFS walk to collect matchable nodes.

        Parameters
        ----------
        node
            Current curriculum skeleton node being visited.
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
        Total number of document segments passed to the engine. This count may include
        bound caption blocks, which are excluded from the effective denominator below.
    """

    all_nodes = dfs_all(curriculum_skeleton.root)
    matchable_nodes = dfs_matchable(curriculum_skeleton.root)
    matched_node_ids = {m.node.id for m in curriculum_match_results.matched}
    container_only_nodes = [
        n for n in all_nodes if n.emit == CurriculumEmitPolicy.CONTAINER_ONLY
    ]
    unexpected_skipped: list[str] = []

    # "Unexpected" skipped = matchable nodes that didn't match AND aren't IGNORE.
    for node in matchable_nodes:
        if node.id not in matched_node_ids and node.emit != CurriculumEmitPolicy.IGNORE:
            unexpected_skipped.append(node.id)

    # Bound caption blocks are intentionally neutralized and later translated to IGNORE
    # decisions, so they should not count as genuinely unmatched content in the
    # diagnostics.
    ignored_bound_captions = [
        s for s in curriculum_match_results.unmatched if s.is_bound_caption
    ]
    genuine_unmatched = [
        s for s in curriculum_match_results.unmatched if not s.is_bound_caption
    ]
    caption_blocks_ignored = len(ignored_bound_captions)

    # Exclude intentionally ignored bound captions from segment coverage/noise counts.
    effective_total_segments = max(0, total_segments - caption_blocks_ignored)

    # Count all source segments consumed by successful matches, including any
    # additional segments attached via allow_multiple_segments (e.g., bilingual
    # heading/body pairs). Counting only the primary matched objects understates
    # segment coverage and can make healthy runs look incomplete.
    matched_segment_count = sum(
        1 + len(m.additional_segments) for m in curriculum_match_results.matched
    )

    report = CurriculumMatchReport(
        caption_blocks_ignored=caption_blocks_ignored,
        container_only_nodes=len(container_only_nodes),
        cursor_jumps=curriculum_match_results.cursor_jumps,
        matched_nodes=len(matched_node_ids),
        matched_segments=matched_segment_count,
        total_curriculum_skeleton_nodes=len(all_nodes),
        total_matchable_nodes=len(matchable_nodes),
        total_segments=effective_total_segments,
        unexpected_skipped_node_ids=unexpected_skipped,
        unmatched_segment_ids=[s.segment_id for s in genuine_unmatched],
        unmatched_segments=len(genuine_unmatched),
    )
    write_to_json(fp=curriculum_match_report_fp, json_info=report.to_dict())

    logger.info(f"\n{report.summary()}")
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
            f"This often indicates cross-strand phrase collisions in the curriculum "
            f"skeleton (ambiguous match_phrases that match nodes in a different strand). "
            f"Review cursor_jumps in: {curriculum_match_report_fp}"
        )


def load_curriculum_skeleton(curriculum_skeleton_fp: Path) -> CurriculumSkeleton:
    """Load and validate a curriculum skeleton from JSON.

    Parameters
    ----------
    curriculum_skeleton_fp
        Path to the curriculum skeleton JSON file.

    Returns
    -------
    CurriculumSkeleton
        A validated curriculum skeleton.
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


def match_curriculum(
    *,
    curriculum_skeleton: CurriculumSkeleton,
    max_skip_distance: int,
    segments: list[CurriculumMatchableSegment],
) -> CurriculumMatchResult:
    """Deterministically match document segments to curriculum skeleton nodes.

    The function walks `segments` in document order and maintains a cursor into the
    DFS-ordered list of matchable curriculum skeleton nodes. For each segment it probes
    a bounded, half-open window `[cursor, cursor + max_skip_distance)`. Because the end
    of the window is exclusive, this means the function checks **at most
    `max_skip_distance` nodes total, including the node currently at the cursor**.

    When a match is found, the node is marked as consumed, but the cursor does not jump
    straight to the matched index. Instead, it only drains past *consecutive* consumed
    nodes at the head of the remaining list. This keeps skipped-but-unconsumed nodes
    available for later segments.

    For `allow_multiple_segments` nodes the matched node is pinned. A pinned node is
    not drained even after being consumed, which allows later segments to continue
    matching the same node. If a later segment finds no hit while a pin is active, the
    pin is released, the cursor drains, and this function retries that same segment
    once against the newly exposed window.

    Matching is deterministic and first-hit-wins within each probe window. That means
    an earlier ambiguous node can shadow a later, more specific node if both phrases
    match the same segment (see examples below).

    Examples
    --------

    1. Straightforward hit in the current window

    Suppose the matchable curriculum skeleton nodes are:

    * index 0: `schema-integrateur`
    * index 1: `tableau-1.1.1`
    * index 2: `tableau-1.1.2`

    And the current state is:

    * `cursor = 0`
    * `consumed_node_ids = {}`
    * `pinned_node_id = None`
    * `max_skip_distance = 3`

    Now the current segment is a table whose bound caption text is:

    * `"Tableau 1.1.1 : Compétence de cycle"`

    Because this is a caption-target table node, `segment_matches_node()` checks
    `segment.caption_text`, not `segment.text`. It normalizes the target text and
    checks whether any normalized `match_phrases` are contained in it.

    So the loop does this:

    * Probes nodes `[0, 3)` -> indices 0, 1, 2
    * `_probe_nodes()` finds that index 1 matches
    * `_record_match()` adds a `CurriculumMatchedSegment`
    * Node `tableau-1.1.1` is added to `consumed_node_ids`
    * `_drain_cursor()` advances past any consumed nodes at the head of the list, but
        only consecutive ones from the current cursor position

    So after this iteration, we have one recorded match, and the cursor may or may not
    move much depending on whether earlier nodes were already consumed.

    2. First-hit-wins inside the probe window

    Suppose the current probe window contains these nodes:

    * index 5: `strand-oral`
    * index 6: `oral-palier-1`
    * index 7: `tableau-1.3.1`

    And suppose the current segment text is something broad like:

    * `"Palier 1 — Communication orale"`

    If both index 5 and index 6 have phrases that happen to match this segment,
    `_probe_nodes()` returns the **first** one it encounters in DFS/probe order. It
    does not score them or pick the “best” one. It just returns the first matching node.

    So the loop behavior is:

    * Probe window opened
    * First matching node found
    * Stop scanning
    * `_record_match()` is called on that node
    * Later possible matches in the same window are ignored for this segment

    This is why broad earlier phrases can shadow later more specific ones.

    3. Multi-segment continuation on a pinned node (subtle case)

    Suppose node `tableau-1.1.1` has `allow_multiple_segments=True`, and the first
    segment that matched it has already been recorded. That means `_record_match()`
    pinned that node by setting `pinned_node_id = node.id`.

    Now the next segment comes in and also matches the same node.

    What happens?

    * `_probe_nodes()` is allowed to consider the pinned node even if it is already
        consumed
    * It finds the same node again
    * `_record_match()` notices:
      * `node.allow_multiple_segments` is true
      * There is already a previous result
      * The previous result’s node is the same node
    * Instead of creating a new top-level match, it appends this segment to
        `results[-1].additional_segments`
    * The cursor does not move
    * The pin stays active

    So conceptually:

    * First segment = “Start match for this node”
    * Second segment = “Same node continues, attach it as additional content”

    That is how bilingual pairs or split-across-segments content get merged into one
    logical match.

    4. Miss in the first pass, then success after unpinning

    Suppose the currently pinned node is `tableau-1.1.1`, because the previous segment
    matched it as part of a multi-segment sequence.

    Now the next segment is actually the start of a different node, say `tableau-1.1.2`.

    First pass:

    * `_probe_nodes()` scans with the pin still active
    * Maybe the pinned node blocks draining and keeps the cursor earlier than it
        otherwise would be
    * No hit is found in that primary window

    At that point the loop does this:

    * If `pinned_node_id is not None`, it clears the pin
    * Calls `_drain_cursor(...)`
    * Recomputes the probe window
    * Retries `_probe_nodes(...)` once more

    If the second probe now hits `tableau-1.1.2`, then `_record_match()` records it and
    the loop continues normally.

    So this pattern means:

    * “Maybe the current segment was still part of the pinned node”
    * “If not, release the pin and try again as a new match”

    That retry is the bridge between “continuation mode” and “move on to the next node.”

    5. Complete miss

    Suppose the segment is just unrelated front-matter prose, or some curriculum
    content the curriculum skeleton does not know how to match.

    Then:

    * `_probe_nodes()` finds no hit
    * Either there is no pin, or the unpin-and-retry also finds no hit
    * The loop executes `unmatched.append(segment)`

    Later, in translation:

    * Unmatched bound caption blocks become `IGNORE`
    * Other unmatched segments become `UNRESOLVED`

    So the main loop itself does not decide ignore vs. unresolved. It just says: “This
    segment found no curriculum skeleton node.”

    6. Cursor jump warning

    Suppose:

    * `cursor` currently points at node index 10
    * The segment does not match nodes 10 or 11
    * It does match node 13
    * Nodes 10–12 were not already consumed

    Then `_record_match()` calls `_count_skipped_between(...)` for the range
    `[cursor, probe_idx)`. If that skipped count is greater than 1, it records a
    `CurriculumCursorJump`.

    This does **not** stop the match. It is just diagnostics saying:

    * “We matched something noticeably later than where we expected to be”

    That often points to ambiguous phrases or ordering drift.

    Parameters
    ----------
    curriculum_skeleton
        A validated `CurriculumSkeleton`.
    max_skip_distance
        Maximum number of curriculum skeleton nodes to inspect per probe window.
    segments
        `CurriculumMatchableSegment` objects in document order.

    Returns
    -------
    CurriculumMatchResult
        Matched segments, unmatched segments, and recorded cursor jumps.

    Raises
    ------
    ValueError
        If `max_skip_distance` is less than 1.
    """

    logger.info("Running curriculum skeleton matching engine...")

    if max_skip_distance < 1:
        raise ValueError(f"max_skip_distance must be >= 1. Got: {max_skip_distance}.")

    matchable_nodes = dfs_matchable(curriculum_skeleton.root)
    assert matchable_nodes, "Curriculum skeleton contains 0 matchable nodes."

    # Pre-compute normalized match phrases for all matchable nodes once, avoiding
    # redundant `_normalize_match_text()` calls during the O(segments × window) probe
    # loop.
    normalized_match_phrases: dict[str, list[str]] = {
        node.id: [_normalize_match_text(phrase) for phrase in node.match_phrases]
        for node in matchable_nodes
        if node.match_phrases
    }

    ancestry_map = build_ancestry_map(curriculum_skeleton.root)

    # Where in the curriculum skeleton we currently are.
    cursor: int = 0

    # Nodes already matched.
    consumed_node_ids: set[str] = set()

    # Keep this node available because it may absorb multiple segments.
    pinned_node_id: str | None = None

    # Diagnostics about non-sequential matches.
    cursor_jumps: list[CurriculumCursorJump] = []

    # Matches we found, and segments we couldn't match at all.
    results: list[CurriculumMatchedSegment] = []
    unmatched: list[CurriculumMatchableSegment] = []

    # For each segment, this loop is basically doing something like: "Look a little bit
    # ahead from where we currently are in the curriculum skeleton, and see what is the
    # first node this segment can belong to.". If we find a match, we record it. If it
    # was in continuation mode, we give ourselves an extra chance after releasing the
    # pin. If we still find nothing, the segment is left unmatched.
    for seg_idx, segment in enumerate(segments):
        # If the cursor has advanced past all matchable nodes and there's no active
        # pin, the remaining segments cannot possibly match anything, so we can
        # short-circuit and mark them all as unmatched immediately.
        if cursor >= len(matchable_nodes) and pinned_node_id is None:
            unmatched.extend(segments[seg_idx:])
            break

        # Probe a bounded half-open window [cursor, probe_end). Because probe_end is
        # exclusive, a max_skip_distance of N checks at most N nodes total, including
        # the node currently at the cursor.
        probe_end = min(cursor + max_skip_distance, len(matchable_nodes))

        # Scan the current probe window and return the first matching node.
        hit = _probe_nodes(
            consumed_node_ids=consumed_node_ids,
            end=probe_end,
            matchable_nodes=matchable_nodes,
            normalized_match_phrases=normalized_match_phrases,
            pinned_node_id=pinned_node_id,
            segment=segment,
            start=cursor,
        )

        # Save the hit, maybe pin it, maybe drain the cursor forward.
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
                normalized_match_phrases=normalized_match_phrases,
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

        # At this point, we have definitively ruled out a match for this segment. Mark
        # it unmatched and move on.
        unmatched.append(segment)

    curriculum_match_results = CurriculumMatchResult(
        cursor_jumps=cursor_jumps, matched=results, unmatched=unmatched
    )

    logger.info(
        f"Curriculum matching complete: {len(curriculum_match_results.matched)} matched, "
        f"{len(curriculum_match_results.unmatched)} unmatched."
    )

    return curriculum_match_results


def prepare_matchable_segments(
    *, caption_bindings: dict[str, CaptionBinding], document_ir: DocumentIR
) -> list[CurriculumMatchableSegment]:
    """Adapt `DocumentIR.segments` into `CurriculumMatchableSegment` wrappers.

    This should be the only function in the canonical IR pipeline that reads raw
    `DocumentIR` structure directly (aside from building caption bindings). Downstream
    code (matching engine, translator, diagnostics, etc.) works only with the
    normalized `CurriculumMatchableSegment` interface.

    This function basically turns raw `DocumentIR` segments into uniform objects that
    the matching engine knows how to work with. It is an adapter between the extraction
    output shape (DocumentIR with blocks, tables slices, provenance, etc.) and the
    matcher's simpler input shape (CurriculumMatchableSegment).

    Why do we need this step? Because the matching engine does not need to know every
    detail of DocumentIR. It just needs a stream of items in document order that say
    things like:
        - "I am a block"
        - "My text is X"
        - "I am a table"
        - "My caption text is Y"
        - ...

    So, this function is the step that precomputes those fields once so that the
    matcher translator (downstream) can stay simple.

    Behavior
    --------
    1. Block segments keep their extracted text, except bound caption blocks.
    2. Bound caption blocks are retained as block segments but their `text` is
       neutralized to `""` and `is_bound_caption=True` so they cannot accidentally
       match structural curriculum skeleton phrases.
    3. Table segments receive any bound caption provenance (`caption_text`,
       `caption_segment_id`, `caption_page_index`, `caption_gap_segments`) and set
       `text=None` because table matching is driven by caption text and/or segment kind
       rather than block prose.
    4. `header_rows_canonical` is converted to immutable tuples so the frozen dataclass
        remains hash-safe and stable across reruns.

    Examples
    --------
    1. Bound caption block
        If segment `cap-1` is the caption bound to table `tbl-1`, the returned block
        wrapper keeps `segment_kind="block"` but sets `text=""` and
        `is_bound_caption=True`.

    2. Table with bound caption provenance
        If `caption_bindings["tbl-1"]` exists, the returned table wrapper for `tbl-1`
        includes `caption_text` and related caption provenance fields, while `text` is
        `None`.

    3. Unbound prose block
        A regular heading/paragraph/list block keeps its extracted text and can still
        participate in phrase matching normally.

    Parameters
    ----------
    caption_bindings
        Mapping from table `segment_id` to `CaptionBinding`, typically produced by
        `build_caption_bindings()`.
    document_ir
        The loaded `DocumentIR` containing ordered stitched segments.

    Returns
    -------
    list[CurriculumMatchableSegment]
        Segments in document order, ready for the matching engine.

    Raises
    ------
    TypeError
        If a segment's declared `kind` does not match its actual runtime type (for
        example, `kind="table"` on a non-`TableSegment`).
    ValueError
        If a segment has no slices or its first-slice page index is missing/invalid.
        These are structural invariants required for deterministic page provenance.
    """

    logger.info("Preparing matchable segments from DocumentIR...")

    result: list[CurriculumMatchableSegment] = []

    # Build the set of block segment IDs that are bound captions. These blocks have
    # their text transferred to the table's caption_text field and must not participate
    # in pattern matching. Once a caption has been bound to a table, we generally want
    # the **table** to carry that caption context, not the caption block to
    # independently compete in matching.
    bound_caption_sids: set[str] = {
        binding.caption_segment_id
        for binding in caption_bindings.values()
        if binding.caption_segment_id
    }

    # Create exactly one CurriculumMatchableSegment per DocumentIR segment, in document
    # order, so that the matcher later sees an ordered list of simplified segments, one
    # per original segment.
    for idx, segment in enumerate(document_ir.segments):
        if not segment.slices:
            raise ValueError(
                f"Segment {segment.segment_id} has no slices and cannot be adapted "
                f"into a matchable segment."
            )

        page_index = segment.slices[0].page_index

        if not isinstance(page_index, int) or page_index < 0:
            raise ValueError(
                f"Segment {segment.segment_id} has invalid first-slice page index: "
                f"{page_index!r}"
            )

        # For block segments, extract text for matching.
        if segment.kind == "block":
            if not isinstance(segment, BlockSegment):
                raise TypeError(
                    f"Segment {segment.segment_id} has kind='block' but is "
                    f"{type(segment).__name__}, not BlockSegment."
                )

            # NB: Bound caption blocks have their content transferred to the table
            # CurriculumMatchableSegment's caption_text field. Neutralize the block's
            # text so it cannot accidentally match structural curriculum skeleton
            # rules. In other words, we do this because otherwise a caption like
            # "Tableau 1.3.1 -- Expression Orale -- Palier 1" might accidentally match
            # a structural curriculum skeleton node just because it contains words like
            # "Palier 1" or "Expression orale". And we do **not** want the caption
            # block itself to become curriculum content. Instead, we want the **table**
            # to carry the caption as context. Thus, the caption block stays in the
            # pipeline for provenance and ordering, but its text is intentionally
            # blanked out so it will later fall through to IGNORE.
            is_bound = segment.segment_id in bound_caption_sids
            extracted_text = extract_block_segment_text(segment) or ""
            text = "" if is_bound else extracted_text

            if is_bound:
                logger.debug(
                    f"bound caption block {segment.segment_id}: "
                    f"text neutralized to empty string (will fall through to IGNORE)."
                )

            result.append(
                CurriculumMatchableSegment(
                    block_type=segment.block_type.value if segment.block_type else None,
                    document_order=idx,
                    is_bound_caption=is_bound,
                    page_index=page_index,
                    raw_segment=segment,
                    segment_id=segment.segment_id,
                    segment_kind="block",
                    text=text,
                )
            )
        # For tables, the matcher does not match against table body text. Instead,
        # table nodes in the curriculum skeleton are expected to match via **bound
        # caption text**. In other words, table segments match by their attached
        # caption text (and header rows).
        elif segment.kind == "table":
            if not isinstance(segment, TableSegment):
                raise TypeError(
                    f"Segment {segment.segment_id} has kind='table' but is "
                    f"{type(segment).__name__}, not TableSegment."
                )

            # Convert header_rows_canonical to immutable tuples for the frozen
            # CurriculumMatchableSegment dataclass.
            hrc: tuple[tuple[str, ...], ...] = tuple(
                tuple(row) for row in (segment.header_rows_canonical or [])
            )

            caption_binding = caption_bindings.get(segment.segment_id)
            result.append(
                CurriculumMatchableSegment(
                    block_type=None,
                    caption_gap_segments=(
                        caption_binding.gap_segments if caption_binding else None
                    ),
                    caption_kind=(
                        caption_binding.caption_kind if caption_binding else None
                    ),
                    caption_page_index=(
                        caption_binding.caption_page_index if caption_binding else None
                    ),
                    caption_segment_id=(
                        caption_binding.caption_segment_id if caption_binding else None
                    ),
                    caption_text=(
                        caption_binding.caption_text if caption_binding else None
                    ),
                    columns_signature=segment.columns_signature,
                    document_order=idx,
                    header_rows_canonical=hrc,
                    page_index=page_index,
                    raw_segment=segment,
                    segment_id=segment.segment_id,
                    segment_kind="table",
                    text=None,  # Tables are matched via caption_text/segment_kind
                )
            )
        else:
            logger.warning(
                f"Segment {segment.segment_id} has unknown kind "
                f"{segment.kind!r}; skipping."
            )

    logger.success(f"Prepared {len(result)} matchable segments.")

    return result


def translate_matched_segment(
    *,
    context_groupings_role_order: list[NodeRole] | None = None,
    doc_key: str,
    matched: CurriculumMatchedSegment,
) -> SegmentDecision:
    """Convert one successful curriculum skeleton match into a SegmentDecision.

    This function is the main bridge between the matching stage and the canonical IR
    decision stage. Given a `CurriculumMatchedSegment`, it looks at the matched
    curriculum skeleton node and turns that match into the appropriate
    `SegmentDecision` shape. This function answers the question: "Now that this
    document segment matched this curriculum skeleton node, what should we emit for the
    canonical IR?"

    High-level behavior
    -------------------
    1. Build `context_groupings` from the matched node's ancestry in the curriculum
        skeleton. These are outer structural containers such as grade, section, strand,
        or substage that provide context for the matched content.
    2. Create a deterministic `decision_id` using the document key and the primary
        matched segment ID.
    3. Merge text from the primary matched segment plus any
        `matched.additional_segments`. This is mainly used for cases like bilingual
        pairs or multi-segment headings/body content that should become one leaf
        statement.
    4. Dispatch based on the matched node's `emit` policy:
        - `IGNORE`: Return an IGNORE decision with no emitted content.
        - `EMIT_GROUPING`: Emit one top-level `GroupingDecision`.
        - `EMIT_LEAF`: Emit one top-level `LeafDecision`.
        - `EMIT_GROUPING_AND_LEAF`: Emit both one grouping and one leaf.
        - `EMIT_TABLE_ROWS`: Delegate table translation to `_translate_table_rows()`,
            which produces row-level decisions from the matched table segment.

    NB:

    1. The returned decision is anchored to the **primary** matched segment
        (`matched.segment.segment_id`), even when `additional_segments` were merged into
        the text.
    2. `context_groupings` come from ancestor curriculum skeleton nodes, while
        `groupings` and `leaves` describe what this matched node itself emits.
    3. For table-emitting nodes, this function does not parse rows directly; it passes
        the work to `_translate_table_rows()`.
    4. The practical difference between EMIT_GROUPING and EMIT_GROUPING_AND_LEAF is:
        Suppose the matched heading is: Palier 1 — Communication orale. If we use
        EMIT_GROUPING, then we are saying: “This heading is only structural. Make a
        substage node for Palier 1, but do not treat the heading text itself as a
        standard.”. If we use EMIT_GROUPING_AND_LEAF, then we are saying: “This heading
        is structural, but it also carries a real curriculum expectation that we want
        preserved as a leaf.”

    Examples
    --------
    1. IGNORE
        This is used when the matched segment should be consumed so it does not remain
        unmatched, but it should not create any canonical content. The function returns
        decision_type=IGNORE with empty context_groupings, groupings, leaves, and rows.

        * Source heading: APPRENTISSAGES PONCTUELS
        * Why ignore it: It is just a repeated document heading, not a curriculum node
            or standard.
        * Output shape:
            SegmentDecision(
                decision_type=IGNORE,
                context_groupings=[],
                groupings=[],
                leaves=[],
                rows=[],
            )

    2. EMIT_GROUPING
        This is used when the matched segment is a structural container like a section,
        strand, or unit, and we want it to become a node in the canonical IR hierarchy,
        but we do not want the matched text itself to become a standard/expectation
        leaf. The function emits one top-level GroupingDecision and no leaves.

        * Source heading: 1.2 Tableau de planification des apprentissages
        * Intended meaning: Create a section node called “Planification des
            apprentissages du CE1”
        * Output shape:
            SegmentDecision(
                decision_type=EMIT_GROUPINGS_ONLY,
                context_groupings=[...],
                groupings=[
                    GroupingDecision(
                        role="section",
                        title="Planification des apprentissages du CE1",
                    )
                ],
                leaves=[],
                rows=[],
            )

        This is the right choice when the heading is just a container and the real
        curricular content lives underneath it in child tables or child statements. For
        example, in the Senegal reading curriculum skeleton, nodes like
        schema-integrateur and planification-oral-lecture are set up this way.

    3. EMIT_LEAF
        This is used when the matched segment is a statement only. It should produce a
        LeafDecision, but it should not create a new grouping node. The function emits
        decision_type=EMIT_LEAVES_ONLY, no top-level groupings, and one
        LeafDecision(body=combined_text).

        * Source paragraph: The learner identifies vowels and consonants in simple
            words.
        * Intended meaning: This is directly a standard/expectation statement, not a
            new container.
        * Output shape:
            SegmentDecision(
                decision_type=EMIT_LEAVES_ONLY,
                context_groupings=[...],   # e.g. Grade 1 > Literacy > Phonics
                groupings=[],
                leaves=[
                    LeafDecision(
                        role="expectation",
                        body="The learner identifies vowels and consonants in simple words.",
                    )
                ],
                rows=[],
            )

    4. EMIT_GROUPING_AND_LEAF1
        This is used when the matched segment is doing two jobs at once: it names a
        structural grouping and the heading itself carries a meaningful curriculum
        statement. The function emits one GroupingDecision plus one
        LeafDecision(body=combined_text).

        * Source heading: Palier 1 — Production d’écrits : production de textes
            narratifs
        * Intended meaning: Create a substage grouping for Palier 1 and also preserve
            the actual expectation text carried by that heading
        * Output shape:
            SegmentDecision(
                decision_type=EMIT_GROUPINGS_AND_LEAVES,
                context_groupings=[...],
                groupings=[
                    GroupingDecision(
                        role="substage",
                        title="Palier 1 — Production d'écrits",
                    )
                ],
                leaves=[
                    LeafDecision(
                        role="expectation",
                        body="Palier 1 ... production de textes narratifs",
                    )
                ],
                rows=[],
            )

    5. EMIT_TABLE_ROWS
        This is used when the matched segment is a table and the content should be
        interpreted row by row rather than as one big leaf.
        `translate_matched_segment()` does not parse the table itself in this case; it
        hands off to `_translate_table_rows()`, which uses the curriculum skeleton’s
        `column_mappings` to build RowDecision[]. Those row decisions may contain
        row-level groupings and leaves, and the overall decision type is chosen based
        on what the rows actually produced.

        * Source table caption: Tableau 1.6.1 — Outils de langue, Palier 1
            Table columns:
                Outils de langue -> grouping subtopic
                Objectif spécifique -> leaf expectation
                Contenus -> leaf guidance
                Durée -> leaf descriptor
        * Output shape:
            SegmentDecision(
                decision_type=EMIT_GROUPINGS_AND_LEAVES,   # Assuming rows produce both
                context_groupings=[...],
                groupings=[],   # Maybe a segment-level grouping, maybe not
                leaves=[],
                rows=[
                    RowDecision(
                        row_index=0,
                        groupings=[GroupingDecision(role="subtopic", title="Vocabulaire")],
                        leaves=[
                            LeafDecision(role="expectation", body="..."),
                            LeafDecision(role="guidance", body="..."),
                            LeafDecision(role="descriptor", body="30 min"),
                        ],
                    ),
                    ...
                ],
            )

    Parameters
    ----------
    context_groupings_role_order
        Optional custom role ordering used when sorting `context_groupings`.
    doc_key
        Stable document key used to build the deterministic decision ID.
    matched
        The successful match produced by the curriculum matching engine. This includes
        the matched curriculum skeleton node, the primary matched segment, its
        ancestry, and any additional continuation segments.

    Returns
    -------
    SegmentDecision
        The canonical decision corresponding to this matched segment and curriculum
        skeleton node.

    Raises
    ------
    ValueError
        If the matched node uses an emit policy that this function does not handle.
    """

    node = matched.node
    segment = matched.segment
    context = build_context_groupings(
        ancestry=matched.ancestry,
        context_groupings_role_order=context_groupings_role_order,
        matched_node=node,
    )
    decision_id = f"curriculum_skeleton:{doc_key}:{segment.segment_id}"
    block_type = BlockType(segment.block_type) if segment.block_type else None

    # Combine text from bilingual pairs.
    all_texts = [segment.text or ""] + [
        extra.text for extra in matched.additional_segments if extra.text
    ]
    combined_text = "\n\n".join(t for t in all_texts if t.strip())

    # IGNORE.
    if node.emit == CurriculumEmitPolicy.IGNORE:
        return SegmentDecision(
            block_type=block_type,
            confidence=1.0,
            context_groupings=[],
            decision_id=decision_id,
            decision_type=SegmentDecisionType.IGNORE,
            groupings=[],
            leaves=[],
            rationale=f"Curriculum skeleton IGNORE: '{node.id}'.",
            rows=[],
            segment_id=segment.segment_id,
            segment_kind=segment.segment_kind,
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
                    local_code=node.local_code,
                    role=node.grouping_role,
                    source_label=node.source_label,
                    title=node.canonical_name.primary,
                )
            ],
            leaves=[],
            rationale=(
                f"Curriculum skeleton EMIT_GROUPING: '{node.id}' "
                f"-> {node.grouping_role.value}."
            ),
            rows=[],
            segment_id=segment.segment_id,
            segment_kind=segment.segment_kind,
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
                    body=combined_text,
                    role=node.leaf_role,
                    source_label=node.source_label,
                )
            ],
            rationale=(
                f"Curriculum skeleton EMIT_LEAF: '{node.id}' → {node.leaf_role.value}."
            ),
            rows=[],
            segment_id=segment.segment_id,
            segment_kind=segment.segment_kind,
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
                    local_code=node.local_code,
                    role=node.grouping_role,
                    source_label=node.source_label,
                    title=node.canonical_name.primary,
                )
            ],
            leaves=[
                LeafDecision(
                    body=combined_text,
                    role=node.leaf_role,
                    source_label=node.source_label,
                )
            ],
            rationale=f"Curriculum skeleton EMIT_GROUPING_AND_LEAF: '{node.id}'.",
            rows=[],
            segment_id=segment.segment_id,
            segment_kind=segment.segment_kind,
        )

    # EMIT_TABLE_ROWS.
    if node.emit == CurriculumEmitPolicy.EMIT_TABLE_ROWS:
        return _translate_table_rows(
            context=context, decision_id=decision_id, node=node, seg=segment
        )

    raise ValueError(f"Unhandled emit policy: {node.emit}")


def translate_segments(
    *,
    context_groupings_role_order: list[NodeRole] | None,
    curriculum_match_results: CurriculumMatchResult,
    curriculum_skeleton: CurriculumSkeleton,
    doc_key: str,
    matchable_segments: list[CurriculumMatchableSegment],
    pdf_name: str,
    segment_decisions_fp: Path,
) -> SegmentDecisionSet:
    """Translate CurriculumMatchResult into a list of SegmentDecisions.

    NB: `allow_multiple_segments=True` matches are merged into a single decision for
    the primary matched segment, with any continuation segments carried on
    `CurriculumMatchedSegment.additional_segments`. So this function does **not**
    guarantee one decision per original document segment.

    Parameters
    ----------
    context_groupings_role_order
        Custom role precedence for context groupings sorting.
    curriculum_match_results
        The raw CurriculumMatchResult from the matching engine.
    curriculum_skeleton
        The CurriculumSkeleton used for matching, needed to interpret emit policies and
        column mappings.
    doc_key
        Document key for decision ID generation.
    matchable_segments
        The original list of matchable segments, used to determine document order and
        to translate unmatched segments.
    pdf_name
        PDF name for metadata in the saved SegmentDecisions JSON.
    segment_decisions_fp
        File path where the resulting SegmentDecisions JSON should be saved for
        downstream consumption by the compiler and for human review.

    Returns
    -------
    SegmentDecisionSet
        The set of SegmentDecisions corresponding to the curriculum match results,
        ready for compilation into a CanonicalIR.
    """

    logger.info("Translating curriculum match results to SegmentDecisions...")

    segment_decisions: list[SegmentDecision] = []

    for matched_seg in curriculum_match_results.matched:
        decision = translate_matched_segment(
            context_groupings_role_order=context_groupings_role_order,
            doc_key=doc_key,
            matched=matched_seg,
        )
        segment_decisions.append(decision)

    for unmatched_seg in curriculum_match_results.unmatched:
        decision = translate_unmatched(doc_key=doc_key, seg=unmatched_seg)
        segment_decisions.append(decision)

    # Sort by document order for consistency.
    seg_order: dict[str, int] = {
        s.segment_id: s.document_order for s in matchable_segments
    }
    segment_decisions.sort(key=lambda d: seg_order.get(d.segment_id or "", 999_999))

    logger.success(f"Translated {len(segment_decisions)} total SegmentDecisions.")

    # Save segment decisions to a JSON file for downstream consumption by the compiler
    # and for human review.
    segment_decision_set = SegmentDecisionSet.model_validate(
        {
            "decision_set_id": compute_decision_set_id(decisions=segment_decisions),
            "decisions": [d.model_dump(mode="json") for d in segment_decisions],
            "doc_key": doc_key,
            "generator": f"curriculum_skeleton:{curriculum_skeleton.skeleton_id}",
            "pdf_name": pdf_name,
        }
    )
    write_to_json(fp=segment_decisions_fp, json_info=segment_decision_set)

    logger.success(f"Saved segment decisions to: {segment_decisions_fp}")

    return segment_decision_set


def translate_unmatched(
    *, doc_key: str, seg: CurriculumMatchableSegment
) -> SegmentDecision:
    """Convert an unmatched segment into a SegmentDecision.

    Bound caption blocks (whose text was transferred to a table's caption_text field)
    are marked IGNORE--they were intentionally neutralized and are not missing content.

    All other unmatched segments are marked UNRESOLVED so they surface in the canonical
    IR's `unresolved` list for human review. This prevents genuine curriculum content
    that the curriculum skeleton failed to match from being silently dropped.

    Parameters
    ----------
    doc_key
        Document key for decision ID generation.
    seg
        The unmatched `CurriculumMatchableSegment`.

    Returns
    -------
    SegmentDecision
        An IGNORE decision for bound captions, or an UNRESOLVED decision for genuinely
        unmatched segments.
    """

    # Bound captions are intentionally neutralized; they are not missing content and can
    # be (safely) silently ignored.
    if seg.is_bound_caption:
        return SegmentDecision(
            block_type=BlockType(seg.block_type) if seg.block_type else None,
            confidence=1.0,
            context_groupings=[],
            decision_id=f"curriculum_skeleton:{doc_key}:{seg.segment_id}:bound_caption",
            decision_type=SegmentDecisionType.IGNORE,
            groupings=[],
            leaves=[],
            rationale="Bound caption block; text transferred to table caption_text.",
            rows=[],
            segment_id=seg.segment_id,
            segment_kind=seg.segment_kind,
        )

    # Genuinely unmatched segment--flag for human review.
    return SegmentDecision(
        block_type=BlockType(seg.block_type) if seg.block_type else None,
        confidence=1.0,
        context_groupings=[],
        decision_id=f"curriculum_skeleton:{doc_key}:{seg.segment_id}:unmatched",
        decision_type=SegmentDecisionType.UNRESOLVED,
        groupings=[],
        leaves=[],
        rationale="No curriculum skeleton node matched this segment; flagged for human review.",
        rows=[],
        segment_id=seg.segment_id,
        segment_kind=seg.segment_kind,
    )
