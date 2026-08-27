"""This module contains context-dependent validation functions for **verified** PageIR
continuity verdicts.

These checks require access to the candidate items (prev_item / next_item) and
therefore cannot live inside the Pydantic model validators on
`PageIRContinuityVerdict`. Schema-internal invariants (is_continuation vs.
continuation_kind consistency, confidence thresholds, repeats_header <-> table-only)
are enforced by the model validators in `schemas.py`.
"""

# Package Library
from kgfeg.page_ir_extraction.schemas import Block, Table
from kgfeg.page_ir_extraction.validators import QualityError
from kgfeg.page_ir_verification.schemas import PageIRContinuityVerdict
from kgfeg.utils.constants import BlockType, PageContinuationKind


def validate_item_continuation_kind(
    *,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Validate that the continuation kind is compatible with the item types.

    Parameters
    ----------
    next_item
        The next page candidate item.
    prev_item
        The previous page candidate item.
    verdict
        The continuation verdict from the model.

    Raises
    ------
    QualityError
        If the continuation kind is incompatible with the candidate item types.
    """

    if not verdict.is_continuation:
        return

    kind = verdict.continuation_kind
    prev_kind = prev_item.kind
    next_kind = next_item.kind

    if kind == PageContinuationKind.TEXT:
        if prev_kind != "block" or next_kind != "block":
            raise QualityError(
                f"continuation_kind='text' requires both candidates to be Blocks. "
                f"Found: {prev_kind} -> {next_kind}."
            )

        # Disallow 'text' continuation on figures; figure continuation must use
        # continuation_kind='figure'.
        if (
            prev_item.block_type == BlockType.FIGURE
            or next_item.block_type == BlockType.FIGURE
        ):
            raise QualityError(
                "continuation_kind='text' requires both candidates to be non-figure Blocks. "
                "Use continuation_kind='figure' for figure continuations."
            )

    if kind == PageContinuationKind.TABLE and (
        prev_kind != "table" or next_kind != "table"
    ):
        raise QualityError(
            f"continuation_kind='table' requires both candidates to be Tables. "
            f"Found: {prev_kind} -> {next_kind}."
        )

    if kind == PageContinuationKind.FIGURE:
        is_prev_figure = (
            prev_kind == "block" and prev_item.block_type == BlockType.FIGURE
        )
        is_next_figure = (
            next_kind == "block" and next_item.block_type == BlockType.FIGURE
        )

        if not (is_prev_figure and is_next_figure):
            raise QualityError(
                "continuation_kind='figure' requires both candidates to be "
                "Blocks with block_type='figure'."
            )


def validate_repeats_header_requires_table_item(
    *, next_item: Block | Table, verdict: PageIRContinuityVerdict
) -> None:
    """Validate that repeats_header is only patched when next_item is actually a table.

    The schema-internal check (continuation must be true + kind must be TABLE) is
    already enforced by the model validator. This function adds the external constraint
    that the next-page candidate must actually be a table. It does NOT
    require header_row_count >= 1 for a True patch. Step 2 is allowed to recover the
    visual repeated-header signal even when page-local extraction missed the header
    rows or counted them incorrectly.

    NB: Table.repeats_header is only valid when next_item.boundary is RESUMED or BOTH
    (enforced by the Table schema). However, Step 2 exists specifically because
    extraction may have misclassified the next-page table boundary as COMPLETE even
    when the table is clearly a continuation. We therefore allow the verifier to
    request a repeats_header patch even if the current extracted boundary is not
    RESUMED/BOTH. The compile layer applies updates in an invariant-safe order:
    clear repeats_header first when a boundary is being downgraded, update the
    boundary, then apply any validator-approved explicit repeats_header patch.

    Parameters
    ----------
    next_item
        The next page candidate item.
    verdict
        The continuation verdict from the model.

    Raises
    ------
    QualityError
        If set_next_table_repeats_header is set but the next-page candidate is not a
        table.
    """

    if verdict.set_next_table_repeats_header is None:
        return

    if next_item.kind != "table":
        raise QualityError(
            "set_next_table_repeats_header is only valid when next_item is a table."
        )


def validate_semantic_flow(
    *,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Catch semantic hallucinations (e.g., text flowing into/from a Section Header).

    Parameters
    ----------
    next_item
        The next page candidate item.
    prev_item
        The previous page candidate item.
    verdict
        The continuation verdict from the model.

    Raises
    ------
    QualityError
        If text continuation flows into or from a heading.
    """

    if not verdict.is_continuation:
        return

    if (
        verdict.continuation_kind == PageContinuationKind.TEXT
        and next_item.kind == "block"
        and next_item.block_type in {BlockType.CAPTION, BlockType.HEADING}
    ):
        text_preview = next_item.text.text[:30] + "..." if next_item.text else "EMPTY"
        raise QualityError(
            f"Invalid Text Continuation: The next item is a HEADING/CAPTION ('{text_preview}'). "
            f"Standard text does not continue directly into a heading."
        )

    if (
        verdict.continuation_kind == PageContinuationKind.TEXT
        and prev_item.kind == "block"
        and prev_item.block_type in {BlockType.CAPTION, BlockType.HEADING}
    ):
        text_preview = prev_item.text.text[:30] + "..." if prev_item.text else "EMPTY"
        raise QualityError(
            f"Invalid Text Continuation: The previous item is a HEADING/CAPTION ('{text_preview}'). "
            f"Headings/captions do not get truncated into a text continuation."
        )
