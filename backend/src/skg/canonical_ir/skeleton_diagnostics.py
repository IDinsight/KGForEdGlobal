"""Match diagnostics and reporting for skeleton-based pipeline.

Generates a structured ``MatchReport`` from the matching engine's output.
The report is used for:

* Per-skeleton quality checks ("is this skeleton healthy for this document?").
* CI/CD gating — a healthy match means >90% node coverage with no large jumps.
* Debugging — lists unmatched segments and unexpected skipped nodes.
"""

# Standard Library
from dataclasses import dataclass, field

# Package Library
from skg.canonical_ir.schemas import CurriculumEmitPolicy, CurriculumSkeleton
from skg.canonical_ir.skeleton_engine import (
    CursorJump,
    MatchResult,
    dfs_all,
    dfs_matchable,
)


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
