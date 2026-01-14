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

    # Previous item (bottom of Page N) must be TRUNCATED or BOTH (i.e., it must
    # continue TO the next page). In pairwise mode, if the extractor marked it as
    # RESUMED (from previous) or left it null, the model MUST set
    # set_prev_item_boundary='truncated' so Python can merge to BOTH when needed.
    if eff_prev_boundary not in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}:
        raise QualityError(
            f"verdict.is_continuation=True requires prev boundary in {{'{ItemBoundary.TRUNCATED.value}','{ItemBoundary.BOTH.value}'}}. "
            f"Got effective prev boundary={eff_prev_boundary}. "
            f"Set set_prev_item_boundary='{ItemBoundary.TRUNCATED.value}' when missing/incompatible."
        )

    # Next item (top of Page N+1) must be RESUMED or BOTH (i.e., it must continue FROM
    # the previous page). In pairwise mode, if the extractor marked it as TRUNCATED (to
    # next) or left it null, the model MUST set set_next_item_boundary='resumed' so
    # Python can merge to BOTH when needed.
    if eff_next_boundary not in {ItemBoundary.RESUMED, ItemBoundary.BOTH}:
        raise QualityError(
            f"verdict.is_continuation=True requires next boundary in {{'{ItemBoundary.RESUMED.value}','{ItemBoundary.BOTH.value}'}}. "
            f"Got effective next boundary={eff_next_boundary}. "
            f"Set set_next_item_boundary='{ItemBoundary.RESUMED.value}' when missing/incompatible."
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

    # Table continuations must be table-to-table (never into/from a block).
    if kind == PageContinuationKind.TABLE.value and (
        prev_kind != "table" or next_kind != "table"
    ):
        raise QualityError(
            f"continuation_kind='table' requires both candidates to be Tables. "
            f"Found: {prev_kind} -> {next_kind}."
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


def validate_negative_case_logic(*, verdict: PageIRContinuityVerdict) -> None:
    """Negative case policy:

    1. The model must not propose boundary edits (set_* fields must be null).
    2. Directional edge-clearing between these two candidates is handled in Python.

    Parameters
    ----------
    verdict
        The continuation verdict from the model.

    Raises
    ------
    QualityError
        If items are left hanging (TRUNCATED/RESUMED) without a valid link.
    """

    if verdict.is_continuation:
        return

    if (
        verdict.set_prev_item_boundary is not None
        or verdict.set_next_item_boundary is not None
        or verdict.set_next_table_repeats_header is not None
    ):
        raise QualityError(
            "Negative case (is_continuation=false) requires all set_* fields to be null. "
            "Directional edge-clearing is applied deterministically in Python."
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
            f"If is_continuation=true, continuation_kind cannot be '{PageContinuationKind.NONE.value}'."
        )

    if (not verdict.is_continuation) and kind != PageContinuationKind.NONE.value:
        raise QualityError(
            "If is_continuation=false, continuation_kind must be 'none' and all "
            "set_* fields must be null."
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
        verdict.continuation_kind == PageContinuationKind.TEXT
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
        verdict.continuation_kind == PageContinuationKind.TABLE
        and verdict.set_next_table_repeats_header is True
        and next_item.kind != "table"
    ):
        raise QualityError(
            "Logic Error: You set set_next_table_repeats_header=true, but the next "
            "item is a Block (Caption), not a Table. Captions cannot contain repeated "
            "table headers."
        )
