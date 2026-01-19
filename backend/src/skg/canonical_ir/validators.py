"""This module contains functionalities related to validating CanonicalIR information."""

# Package Library
from skg.canonical_ir.schemas import SegmentDecision
from skg.document_ir.schemas import Segment
from skg.page_ir_extraction.validators import QualityError
from skg.utils.constants import SegmentDecisionType


def validate_non_noop_emit_decision(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """If decision_type indicates emission, ensure something will actually be emitted.
    This prevents 'emit_*' decisions that are effectively empty.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    has_any_output = bool(
        segment_decision.context_groupings
        or segment_decision.groupings
        or segment_decision.leaves
        or segment_decision.rows
    )

    if not has_any_output:
        raise QualityError(
            f"Decision type '{segment_decision.decision_type.value}' emitted no output "
            f"(context_groupings/groupings/leaves/rows all empty). "
            f"This should usually be IGNORE or UNRESOLVED.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}"
        )


def validate_segment_kind_coherence(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure the decision structure matches the actual segment kind.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind == "block" and segment_decision.rows:
        raise QualityError(
            f"Block segment decision must not include rows[].\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}"
        )

    if segment.kind == "table" and segment_decision.block_type is not None:
        raise QualityError(
            f"Table segment decision must not include block_type.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  block_type: {segment_decision.block_type}"
        )


def validate_table_row_index(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Validate that RowDecision.row_index values are within range and unique. If this
    SegmentDecision represents a chunked slice of a table, also enforce that all
    RowDecision.row_index values lie within [row_range_start, row_range_end), where
    row_range_end is EXCLUSIVE.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    start = segment_decision.row_range_start
    end = segment_decision.row_range_end

    # If chunking is declared, it must be well-formed.
    if (start is None) != (end is None):
        raise QualityError(
            f"Chunked table decision must include both row_range_start and row_range_end (exclusive).\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  row_range_start: {start}\n"
            f"  row_range_end: {end}"
        )

    is_chunked = start is not None
    table_rows = segment.rows

    if is_chunked:
        if start < 0 or end < 0 or start >= end:
            raise QualityError(
                f"Invalid chunk boundaries for table decision. Expected start < end and both >= 0.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_range: [{start}, {end})"
            )

        if end > len(table_rows):
            raise QualityError(
                f"Chunk boundary row_range_end exceeds table length.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_range: [{start}, {end})\n"
                f"  table_rows: {len(table_rows)}"
            )


def validate_table_header_rows_not_emitted(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure RowDecision.row_index does not point into header rows. This prevents the
    model from interpreting table headers as real curriculum rows, especially for
    unchunked table decisions where chunk boundaries aren't present.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any RowDecision.row_index points into header rows.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    header_n = segment.header_row_count or 0

    if header_n <= 0:
        return

    bad = sorted(
        {rd.row_index for rd in segment_decision.rows if rd.row_index < header_n}
    )
    if bad:
        raise QualityError(
            f"RowDecision.row_index includes header rows; header rows must not be emitted.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  header_row_count: {header_n}\n"
            f"  bad_row_indices: {bad}"
        )


def validate_table_rows_vs_leaves(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Prevent double counting: if using rows[] for a table, top-level leaves[] should
    be empty.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If both rows[] and top-level leaves[] are present in a table decision.
    """

    if segment.kind != "table":
        return

    if segment_decision.rows and segment_decision.leaves:
        raise QualityError(
            f"Table decision includes both rows[] and top-level leaves[]. "
            f"Use rows[] only for table parsing to avoid duplication.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}"
        )


def validate_table_split_explosion(
    *, max_leaves_per_row: int = 25, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Heuristic guardrail: prevent the LLM from hallucinating/splitting excessively.

    Parameters
    ----------
    max_leaves_per_row
        The maximum allowed number of LeafDecisions per RowDecision.
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any RowDecision contains more than max_leaves_per_row LeafDecisions.
    """

    if segment.kind != "table":
        return

    for rd in segment_decision.rows:
        if len(rd.leaves) > max_leaves_per_row:
            raise QualityError(
                f"RowDecision produced too many leaves (>{max_leaves_per_row}).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  leaves_count: {len(rd.leaves)}"
            )


def validate_unique_table_rows(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Validate that all RowDecision.row_index values are unique.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    table_rows = segment.rows
    max_row_index = len(table_rows) - 1
    start = segment_decision.row_range_start
    end = segment_decision.row_range_end

    seen: set[int] = set()
    dupes: list[int] = []

    for rd in segment_decision.rows:
        # Global range check.
        if rd.row_index < 0 or rd.row_index > max_row_index:
            raise QualityError(
                f"RowDecision.row_index out of range.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  allowed: 0..{max_row_index}\n"
                f"  table_rows: {len(table_rows)}"
            )

        if start is not None and not start <= rd.row_index < end:
            raise QualityError(
                f"RowDecision.row_index outside decision chunk boundaries.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  allowed_chunk: [{start}, {end})"
            )

        if rd.row_index in seen:
            dupes.append(rd.row_index)
        seen.add(rd.row_index)

    if dupes:
        raise QualityError(
            f"Duplicate RowDecision.row_index values in table decision.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  duplicates: {sorted(set(dupes))}"
        )
