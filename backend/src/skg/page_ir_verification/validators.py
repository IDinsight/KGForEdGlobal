"""This module contains functionalities related to validating the **verified** PageIR
information.
"""

# Package Library
from skg.page_ir_extraction.schemas import Block, Table
from skg.page_ir_extraction.validators import QualityError
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.utils.constants import BlockType, ItemBoundary, PageContinuationKind


def validate_boundary_logic(
    *,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Ensure the effective boundary (original + edit) supports continuation. If
    is_continuation=True, the items effectively cannot be 'complete'.

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
        If any quality checks fail.
    """

    if not verdict.is_continuation:
        return

    # Calculate effective boundaries (apply edits if present, else use original).
    eff_prev_boundary = verdict.set_prev_item_boundary or prev_item.boundary
    eff_next_boundary = verdict.set_next_item_boundary or next_item.boundary

    # Previous item (bottom of Page N) must be TRUNCATED or BOTH. It cannot be COMPLETE.
    if eff_prev_boundary == ItemBoundary.COMPLETE.value:
        raise QualityError(
            f"verdict.is_continuation=True, but previous item is '{ItemBoundary.COMPLETE.value}' "
            f"and no set_prev_item_boundary edit was proposed to fix it."
        )

    # Next item (top of Page N+1) must be RESUMED or BOTH. It cannot be COMPLETE.
    if eff_next_boundary == ItemBoundary.COMPLETE.value:
        raise QualityError(
            f"verdict.is_continuation=True, but next item is '{ItemBoundary.COMPLETE.value}' "
            f"and no set_next_item_boundary edit was proposed to fix it."
        )


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
        If any quality checks fail.
    """

    kind = verdict.continuation_kind.value
    prev_kind = prev_item.kind
    next_kind = next_item.kind

    # Text continuations must be block-to-block (never into/from a table).
    if kind == PageContinuationKind.TEXT.value and (
        prev_kind != "block" or next_kind != "block"
    ):
        raise QualityError(
            f"continuation_kind='text' requires both candidates to be Blocks. "
            f"Found: {prev_kind} -> {next_kind}."
        )

    # Table continuation: table --> table OR table --> caption.
    if kind == PageContinuationKind.TABLE.value:
        # Prev item MUST be a table.
        if prev_kind != "table":
            raise QualityError(
                f"continuation_kind='table' requires the previous item to be a Table "
                f"(found {prev_kind})."
            )

        # Next item must be a table OR a valid caption.
        is_next_table = next_kind == "table"
        is_next_valid_caption = False

        if next_kind == "block":
            # Check if block type allows it to function as a table label (captions,
            # headings, or paragraphs can structurally appear here).
            if next_item.block_type in {
                BlockType.CAPTION,
                BlockType.HEADING,
                BlockType.PARAGRAPH,
            }:
                is_next_valid_caption = True

        if not (is_next_table or is_next_valid_caption):
            raise QualityError(
                f"continuation_kind='table' requires the next item to be a Table "
                f"or a caption/heading Block. Found BlockType: "
                f"{next_item.block_type.value}."
            )

    # Figure continuations must be figure-to-figure blocks.
    if kind == PageContinuationKind.FIGURE.value:
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


def validate_negative_case_logic(
    *,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Ensure that if is_continuation=False, items are not left in a 'dangling' state.
    If the model rejects continuation, it implies the items are NOT connected.
    Therefore, they should not remain 'TRUNCATED' (at bottom) or 'RESUMED' (at top)
    unless they connect to *something else* (which pairwise verification assumes they
    don't).

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
        If items are left hanging (TRUNCATED/RESUMED) without a valid link.
    """

    if verdict.is_continuation:
        return

    # Calculate effective boundaries.
    eff_prev_boundary = verdict.set_prev_item_boundary or prev_item.boundary
    eff_next_boundary = verdict.set_next_item_boundary or next_item.boundary

    # Check previous item (bottom of Page N). If it was TRUNCATED, and we now say
    # "False", it should probably be COMPLETE.
    if eff_prev_boundary in {ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value}:
        raise QualityError(
            f"verdict.is_continuation=False, but the previous item is still marked "
            f"'{eff_prev_boundary}'. This implies it continues. "
            f"Please set set_prev_item_boundary='{ItemBoundary.COMPLETE.value}' "
            f"to close it, or change is_continuation to true."
        )

    # Check next item (top of Page N+1). If it was RESUMED, and we now say "False", it
    # should probably be COMPLETE.
    if eff_next_boundary in {ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value}:
        raise QualityError(
            f"verdict.is_continuation=False, but the next item is still marked "
            f"'{eff_next_boundary}'. This implies it resumes from somewhere. "
            f"Please set set_next_item_boundary='{ItemBoundary.COMPLETE.value}' "
            f"to close it, or change is_continuation to true."
        )


def validate_page_continuation_kind(verdict: PageIRContinuityVerdict) -> None:
    """Check if continuations are structurally possible.

    Parameters
    ----------
    verdict
        The continuation verdict from the model.

    Raises
    ------
    QualityError
        If any continuation invariant is violated.
    """

    kind = verdict.continuation_kind.value

    if verdict.is_continuation and kind == PageContinuationKind.NONE.value:
        raise QualityError(
            "If is_continuation=true, continuation_kind cannot be 'none'."
        )

    if (not verdict.is_continuation) and kind != PageContinuationKind.NONE.value:
        raise QualityError(
            "If is_continuation=false, continuation_kind must be 'none' "
            "and all set_* fields must be null."
        )


def validate_semantic_flow(
    *, next_item: Block | Table, verdict: PageIRContinuityVerdict
) -> None:
    """Catch semantic hallucinations, e.g., Text flowing into a Section Header.

    Parameters
    ----------
    next_item
        The next page candidate item.
    verdict
        The continuation verdict from the model.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if not verdict.is_continuation:
        return

    # Text cannot continue into a Heading/Title.
    if (
        verdict.continuation_kind == PageContinuationKind.TEXT.value
        and next_item.kind == "block"
        and next_item.block_type == BlockType.HEADING
    ):
        text_preview = next_item.text.text[:30] + "..." if next_item.text else "EMPTY"
        raise QualityError(
            f"Invalid Text Continuation: The next item is a HEADING ('{text_preview}'). "
            f"Standard text does not continue directly into a heading."
        )

    # Table headers cannot repeat if the next item is a caption. If the next item is a
    # caption, it cannot 'contain' the repeated header rows. If headers repeat, the
    # container MUST be a Table. A caption block cannot physically contain the repeated
    # grid rows.
    if (
        verdict.continuation_kind == PageContinuationKind.TABLE.value
        and verdict.set_next_table_repeats_header is True
        and next_item.kind != "table"
    ):
        raise QualityError(
            "Logic Error: You set set_next_table_repeats_header=true, but the next "
            "item is a Block (Caption), not a Table. Captions cannot contain repeated "
            "table headers."
        )
