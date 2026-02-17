"""This module contains functionalities related to validating the **verified** PageIR
information.
"""

# Package Library
from skg.page_ir_extraction.schemas import Block, Table
from skg.page_ir_extraction.validators import QualityError
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.utils.constants import BlockType, PageContinuationKind


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


def validate_overconfident_table_negative(
    *,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Catch a common false-negative: model says two adjacent tables are unrelated with
    very high confidence, even though the tables share the same basic schema.

    This is intentionally general (country-agnostic) and conservative: it only triggers
    when BOTH candidates are tables, the model says "none", confidence is very high,
    AND the number of columns matches.

    In such cases, downstream stitching often benefits from a forced re-check (or a
    second-pass verifier prompt).

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
        If the model is overconfidently wrong about two tables not being continuations,
        which is a common failure mode that can often be fixed with a re-check or a
        second verification prompt.
    """

    if (
        verdict.is_continuation
        or verdict.continuation_kind != PageContinuationKind.NONE
        or (prev_item.kind != "table" or next_item.kind != "table")
        or (verdict.confidence is None or verdict.confidence <= 0.90)
    ):
        return

    # Schema similarity signal: ifcolumn counts match, it's plausible this is a
    # continuation.
    if prev_item.n_cols == next_item.n_cols and prev_item.n_cols is not None:
        raise QualityError(
            f"Suspicious overconfident non-continuation: both candidates are tables "
            f"with the same n_cols={prev_item.n_cols}, but verdict is "
            f"is_continuation=false with confidence={verdict.confidence:.2f}. "
            f"Recommend re-checking with the double-check prompt or lowering confidence."
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
            "If is_continuation=false, continuation_kind must be 'none'."
        )


def validate_repeats_header_logic(
    *, next_item: Block | Table, verdict: PageIRContinuityVerdict
) -> None:
    """If repeats_header is patched, it must be a table continuation and next_item must
    be table.

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

    if verdict.set_next_table_repeats_header is None:
        return

    if (
        not verdict.is_continuation
        or verdict.continuation_kind != PageContinuationKind.TABLE
    ):
        raise QualityError(
            "set_next_table_repeats_header may only be set when is_continuation=true "
            "and continuation_kind='table'."
        )

    if next_item.kind != "table":
        raise QualityError(
            "set_next_table_repeats_header is only valid when next_item is a table."
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
