"""Deterministic matching engine for skeleton-based curriculum parsing.

Replaces the LLM-based segment decision generation, context propagation,
spine correction, and canonicalization steps of the current pipeline.

Core invariant: the cursor only moves FORWARD through the skeleton's
depth-first traversal order.  It never backtracks.  This reflects the fact
that curriculum documents are read top-to-bottom, and the skeleton's child
ordering mirrors document order.
"""

# Standard Library
import re

from dataclasses import dataclass, field
from typing import Optional

# Package Library
from skg.canonical_ir.schemas import (
    CurriculumEmitPolicy,
    CurriculumMatchRule,
    CurriculumSkeleton,
    CurriculumSkeletonNode,
)
from skg.document_ir.schemas import Segment
from skg.utils.constants import CurriculumMatchTarget

# ── Data classes ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MatchableSegment:
    """Normalized view of a DocumentIR segment for the matching engine.

    All fields the matching engine needs are pre-extracted here so the engine
    never touches DocumentIR internals.  The ``raw_segment`` reference is kept
    for downstream translation (table row extraction needs the full TableSegment).
    """

    segment_id: str
    segment_kind: str  # "block" or "table"
    block_type: Optional[str]  # BlockType.value or None for tables
    text: Optional[str]  # Combined text for blocks; None for tables
    page_index: int  # From first slice
    document_order: int  # Index in document_ir.segments
    raw_segment: Segment  # Reference back to full segment

    # Table-specific (populated from caption_bindings + TableSegment).
    caption_text: Optional[str] = None
    caption_kind: Optional[str] = None
    caption_segment_id: Optional[str] = None
    caption_page_index: Optional[int] = None
    caption_gap_segments: Optional[int] = None
    columns_signature: Optional[str] = None
    header_rows_canonical: tuple[tuple[str, ...], ...] = ()  # Immutable for frozen


@dataclass
class MatchedSegment:
    """A document segment successfully matched to a skeleton node."""

    segment: MatchableSegment
    node: CurriculumSkeletonNode
    ancestry: list[CurriculumSkeletonNode]  # Root → matched node (inclusive)
    additional_segments: list[MatchableSegment] = field(default_factory=list)
    # ↑ For allow_multiple_segments (bilingual pairs)


@dataclass
class CursorJump:
    """Diagnostic: the cursor skipped over skeleton nodes to find a match."""

    from_node_id: str
    to_node_id: str
    skipped_count: int
    segment_id: str


@dataclass
class MatchResult:
    """Complete output of the matching engine."""

    matched: list[MatchedSegment]
    unmatched: list[MatchableSegment]
    skipped_node_ids: set[str]
    cursor_jumps: list[CursorJump]


# ── Tree helpers ─────────────────────────────────────────────────────────────────


def dfs_matchable(root: CurriculumSkeletonNode) -> list[CurriculumSkeletonNode]:
    """Flatten skeleton into DFS order, keeping only matchable nodes.

    A node is matchable if it is NOT ``CONTAINER_ONLY`` and has at least one
    ``match_rule``.  The framework root is excluded (it has no match rules).

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
        if node.emit != CurriculumEmitPolicy.CONTAINER_ONLY and node.match_rules:
            nodes.append(node)
        for child in node.children:
            _walk(child)

    _walk(root)
    return nodes


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
        nodes.append(node)
        for child in node.children:
            _walk(child)

    _walk(root)
    return nodes


def build_ancestry_map(
    root: CurriculumSkeletonNode,
) -> dict[str, list[CurriculumSkeletonNode]]:
    """Build node_id → full ancestry chain (root → node, inclusive).

    Parameters
    ----------
    root
        The root SkeletonNode.

    Returns
    -------
    dict[str, list[SkeletonNode]]
        Mapping from each node ID to its full ancestry chain.
    """

    result: dict[str, list[CurriculumSkeletonNode]] = {}

    def _walk(
        node: CurriculumSkeletonNode, ancestors: list[CurriculumSkeletonNode]
    ) -> None:
        chain = ancestors + [node]
        result[node.id] = chain
        for child in node.children:
            _walk(child, chain)

    _walk(root, [])
    return result


# ── Segment-node matching ────────────────────────────────────────────────────────


def segment_matches_node(
    segment: MatchableSegment, node: CurriculumSkeletonNode
) -> bool:
    """Test whether a document segment matches a skeleton node.

    ANY match_rule succeeding is sufficient (OR logic across rules).

    Parameters
    ----------
    segment
        The MatchableSegment to test.
    node
        The SkeletonNode to test against.

    Returns
    -------
    bool
        True if any of the node's match_rules match the segment.
    """

    if node.emit == CurriculumEmitPolicy.CONTAINER_ONLY:
        return False

    return any(_rule_matches(segment, rule) for rule in node.match_rules)


def _rule_matches(segment: MatchableSegment, rule: CurriculumMatchRule) -> bool:
    """Test a single match rule against a segment.

    Within a rule, ALL specified conditions must hold (AND logic).

    Parameters
    ----------
    segment
        The MatchableSegment to test.
    rule
        The MatchRule to apply.

    Returns
    -------
    bool
        True if all conditions in the rule are satisfied.
    """

    # Check structural constraints first (cheap).
    if rule.require_segment_kind and segment.segment_kind != rule.require_segment_kind:
        return False

    if rule.require_block_type and segment.block_type != rule.require_block_type:
        return False

    # Get the text to match against.
    target_text = _get_target_text(segment, rule.target)

    if target_text is None:
        return False

    # Regex match.
    return bool(re.search(rule.pattern, target_text, re.IGNORECASE | re.UNICODE))


def _get_target_text(
    segment: MatchableSegment, target: CurriculumMatchTarget
) -> Optional[str]:
    """Extract the text field from the segment based on match target.

    Parameters
    ----------
    segment
        The MatchableSegment to extract text from.
    target
        Which text field to extract.

    Returns
    -------
    str | None
        The relevant text, or None if the target is not available.
    """

    if target == CurriculumMatchTarget.TEXT:
        return segment.text

    if target == CurriculumMatchTarget.HEADING:
        if segment.block_type == "heading":
            return segment.text
        return None

    if target == CurriculumMatchTarget.CAPTION:
        return segment.caption_text

    return None


# ── Main matching algorithm ──────────────────────────────────────────────────────


def match(
    segments: list[MatchableSegment],
    curriculum_skeleton: CurriculumSkeleton,
    *,
    max_skip_distance: int = 20,
) -> MatchResult:
    """Deterministic forward-only matching of document segments to skeleton nodes.

    The cursor starts at the first matchable node and only moves forward.
    When a segment matches a node beyond ``max_skip_distance``, a diagnostic
    ``CursorJump`` is recorded.

    Parameters
    ----------
    segments
        MatchableSegments in document order.
    curriculum_skeleton
        A validated CurriculumSkeleton.
    max_skip_distance
        Maximum nodes to skip before recording a diagnostic warning.
        The engine will still find the match (full scan) but will flag it.

    Returns
    -------
    MatchResult
        Matched segments, unmatched segments, skipped node IDs, and jumps.
    """

    matchable_nodes = dfs_matchable(curriculum_skeleton.root)
    ancestry_map = build_ancestry_map(curriculum_skeleton.root)

    cursor = 0
    results: list[MatchedSegment] = []
    unmatched: list[MatchableSegment] = []
    cursor_jumps: list[CursorJump] = []

    for segment in segments:
        matched = False

        for probe in range(cursor, len(matchable_nodes)):
            node = matchable_nodes[probe]

            if not segment_matches_node(segment, node):
                continue

            # Record large jump diagnostic.
            if probe - cursor > max_skip_distance:
                prev_id = (
                    matchable_nodes[cursor].id
                    if cursor < len(matchable_nodes)
                    else "END"
                )
                cursor_jumps.append(
                    CursorJump(
                        from_node_id=prev_id,
                        to_node_id=node.id,
                        skipped_count=probe - cursor,
                        segment_id=segment.segment_id,
                    )
                )

            ancestry = ancestry_map[node.id]

            # Multi-match: append to previous match if same node.
            if (
                node.allow_multiple_segments
                and results
                and results[-1].node.id == node.id
            ):
                results[-1].additional_segments.append(segment)
            else:
                results.append(
                    MatchedSegment(
                        segment=segment,
                        node=node,
                        ancestry=ancestry,
                    )
                )

            # Advance cursor.
            cursor = probe if node.allow_multiple_segments else probe + 1
            matched = True
            break

        if not matched:
            unmatched.append(segment)

    # Compute skipped nodes.
    matched_node_ids = {r.node.id for r in results}
    all_ids: set[str] = set()

    for n in dfs_all(curriculum_skeleton.root):
        all_ids.add(n.id)

    skipped = all_ids - matched_node_ids

    return MatchResult(
        matched=results,
        unmatched=unmatched,
        skipped_node_ids=skipped,
        cursor_jumps=cursor_jumps,
    )
