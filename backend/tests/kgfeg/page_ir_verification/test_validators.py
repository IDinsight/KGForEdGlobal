"""This is the main module for testing page_ir_verification/validators.py."""

# Standard Library
from typing import Optional

# Third Party Library
import pytest

# Package Library
from kgfeg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    ListItem,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from kgfeg.page_ir_extraction.validators import QualityError
from kgfeg.page_ir_verification.schemas import PageIRContinuityVerdict
from kgfeg.page_ir_verification.validators import (
    validate_item_continuation_kind,
    validate_repeats_header_requires_table_item,
    validate_semantic_flow,
)
from kgfeg.utils.constants import BlockType, ItemBoundary, PageContinuationKind

_VALID_RATIONALE = "x" * 50


def make_block(
    *,
    block_type: BlockType = BlockType.PARAGRAPH,
    text: str = "Some paragraph text for testing.",
) -> Block:
    """Build a minimal `Block` instance.

    Parameters
    ----------
    block_type
        The visual structure of the block.
    text
        The text content (ignored for FIGURE blocks).

    Returns
    -------
    Block
        A valid Block instance.
    """

    if block_type == BlockType.FIGURE:
        return Block(
            bbox=[0, 0, 100, 50],
            block_type=BlockType.FIGURE,
            boundary=ItemBoundary.COMPLETE,
            figure=FigureUnit(alt_text="a diagram"),
            kind="block",
            text=None,
        )

    if block_type == BlockType.LIST:
        return Block(
            bbox=[0, 0, 100, 50],
            block_type=BlockType.LIST,
            boundary=ItemBoundary.COMPLETE,
            kind="block",
            list_items=[
                ListItem(marker="•", text=TextUnit(language="en", text="item one"))
            ],
            text=None,
        )

    return Block(
        bbox=[0, 0, 100, 50],
        block_type=block_type,
        boundary=ItemBoundary.COMPLETE,
        kind="block",
        text=TextUnit(language="en", text=text),
    )


def make_table() -> Table:
    """Build a minimal `Table` instance.

    Returns
    -------
    Table
        A valid Table with one header row and one data row.
    """

    return Table(
        bbox=[0, 0, 500, 300],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=1,
        kind="table",
        rows=[
            TableRow(cells=[TableCell(text=TextUnit(language="en", text="Header"))]),
            TableRow(cells=[TableCell(text=TextUnit(language="en", text="Data"))]),
        ],
    )


def make_verdict(
    *,
    confidence: float = 0.85,
    continuation_kind: PageContinuationKind = PageContinuationKind.NONE,
    is_continuation: bool = False,
    set_next_table_repeats_header: Optional[bool] = None,
) -> PageIRContinuityVerdict:
    """Build a `PageIRContinuityVerdict` with sensible defaults.

    Parameters
    ----------
    confidence
        Verification confidence score.
    continuation_kind
        Type of content continuing across the break.
    is_continuation
        Whether content continues across the page break.
    set_next_table_repeats_header
        Optional header-repeat patch signal.

    Returns
    -------
    PageIRContinuityVerdict
        A valid verdict instance.
    """

    return PageIRContinuityVerdict(
        confidence=confidence,
        continuation_kind=continuation_kind,
        is_continuation=is_continuation,
        rationale=_VALID_RATIONALE,
        set_next_table_repeats_header=set_next_table_repeats_header,
    )


class TestValidateItemContinuationKind:
    """Tests for `validate_item_continuation_kind`."""

    def test_figure_continuation_with_prev_non_figure_block_raises(self) -> None:
        """Figure continuation where prev_item is a non-figure block raises."""

        with pytest.raises(QualityError, match="block_type='figure'"):
            validate_item_continuation_kind(
                next_item=make_block(block_type=BlockType.FIGURE),
                prev_item=make_block(block_type=BlockType.PARAGRAPH),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.FIGURE, is_continuation=True
                ),
            )

    def test_figure_continuation_with_table_raises(self) -> None:
        """Figure continuation where one candidate is a table raises."""

        with pytest.raises(QualityError, match="block_type='figure'"):
            validate_item_continuation_kind(
                next_item=make_block(block_type=BlockType.FIGURE),
                prev_item=make_table(),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.FIGURE, is_continuation=True
                ),
            )

    def test_figure_continuation_with_two_figure_blocks(self) -> None:
        """Figure continuation between two figure blocks passes."""

        validate_item_continuation_kind(
            next_item=make_block(block_type=BlockType.FIGURE),
            prev_item=make_block(block_type=BlockType.FIGURE),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.FIGURE, is_continuation=True
            ),
        )

    def test_not_continuation_always_passes(self) -> None:
        """A non-continuation verdict is never rejected regardless of item types."""

        verdict = make_verdict(is_continuation=False)

        # block-block, table-table, mixed -> all fine when is_continuation=False.
        for prev, nxt in [
            (make_block(), make_block()),
            (make_table(), make_table()),
            (make_block(), make_table()),
        ]:
            validate_item_continuation_kind(
                next_item=nxt, prev_item=prev, verdict=verdict
            )

    def test_table_continuation_with_next_block_raises(self) -> None:
        """Table continuation where next_item is a block raises `QualityError`."""

        with pytest.raises(QualityError, match="both candidates to be Tables"):
            validate_item_continuation_kind(
                next_item=make_block(),
                prev_item=make_table(),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TABLE, is_continuation=True
                ),
            )

    def test_table_continuation_with_prev_block_raises(self) -> None:
        """Table continuation where prev_item is a block raises `QualityError`."""

        with pytest.raises(QualityError, match="both candidates to be Tables"):
            validate_item_continuation_kind(
                next_item=make_table(),
                prev_item=make_block(),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TABLE, is_continuation=True
                ),
            )

    def test_table_continuation_with_two_tables(self) -> None:
        """Table continuation between two tables passes."""

        validate_item_continuation_kind(
            next_item=make_table(),
            prev_item=make_table(),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.TABLE, is_continuation=True
            ),
        )

    def test_text_continuation_with_next_figure_block_raises(self) -> None:
        """Text continuation where next_item is a figure block raises `QualityError`."""

        with pytest.raises(QualityError, match="non-figure Blocks"):
            validate_item_continuation_kind(
                next_item=make_block(block_type=BlockType.FIGURE),
                prev_item=make_block(block_type=BlockType.PARAGRAPH),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_text_continuation_with_next_table_raises(self) -> None:
        """Text continuation where next_item is a table raises `QualityError`."""

        with pytest.raises(QualityError, match="both candidates to be Blocks"):
            validate_item_continuation_kind(
                next_item=make_table(),
                prev_item=make_block(),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_text_continuation_with_prev_figure_block_raises(self) -> None:
        """Text continuation where prev_item is a figure block raises `QualityError`."""

        with pytest.raises(QualityError, match="non-figure Blocks"):
            validate_item_continuation_kind(
                next_item=make_block(block_type=BlockType.PARAGRAPH),
                prev_item=make_block(block_type=BlockType.FIGURE),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_text_continuation_with_prev_table_raises(self) -> None:
        """Text continuation where prev_item is a table raises `QualityError`."""

        with pytest.raises(QualityError, match="both candidates to be Blocks"):
            validate_item_continuation_kind(
                next_item=make_block(),
                prev_item=make_table(),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_text_continuation_with_two_paragraph_blocks(self) -> None:
        """Text continuation between two paragraph blocks passes."""

        validate_item_continuation_kind(
            next_item=make_block(block_type=BlockType.PARAGRAPH),
            prev_item=make_block(block_type=BlockType.PARAGRAPH),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.TEXT, is_continuation=True
            ),
        )


class TestValidateRepeatsHeaderRequiresTableItem:
    """Tests for `validate_repeats_header_requires_table_item`."""

    def test_null_patch_always_passes(self) -> None:
        """When `set_next_table_repeats_header` is None, any next_item is fine."""

        verdict = make_verdict(
            continuation_kind=PageContinuationKind.TABLE,
            is_continuation=True,
            set_next_table_repeats_header=None,
        )

        # Passes for both tables and blocks.
        validate_repeats_header_requires_table_item(
            next_item=make_table(), verdict=verdict
        )
        validate_repeats_header_requires_table_item(
            next_item=make_block(), verdict=verdict
        )

    def test_false_patch_with_block_raises(self) -> None:
        """`set_next_table_repeats_header=False` with a block next_item raises."""

        with pytest.raises(QualityError, match="next_item is a table"):
            validate_repeats_header_requires_table_item(
                next_item=make_block(),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TABLE,
                    is_continuation=True,
                    set_next_table_repeats_header=False,
                ),
            )

    def test_false_patch_with_table_passes(self) -> None:
        """`set_next_table_repeats_header=False` with a table next_item passes."""

        validate_repeats_header_requires_table_item(
            next_item=make_table(),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.TABLE,
                is_continuation=True,
                set_next_table_repeats_header=False,
            ),
        )

    def test_true_patch_with_block_raises(self) -> None:
        """`set_next_table_repeats_header=True` with a block next_item raises."""

        with pytest.raises(QualityError, match="next_item is a table"):
            validate_repeats_header_requires_table_item(
                next_item=make_block(),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TABLE,
                    is_continuation=True,
                    set_next_table_repeats_header=True,
                ),
            )

    def test_true_patch_with_table_passes(self) -> None:
        """`set_next_table_repeats_header=True` with a table next_item passes."""

        validate_repeats_header_requires_table_item(
            next_item=make_table(),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.TABLE,
                is_continuation=True,
                set_next_table_repeats_header=True,
            ),
        )


class TestValidateSemanticFlow:
    """Tests for `validate_semantic_flow`."""

    def test_error_message_includes_text_preview(self) -> None:
        """The error message includes a preview of the offending item's text."""

        with pytest.raises(QualityError, match="Chapter 5"):
            validate_semantic_flow(
                next_item=make_block(
                    block_type=BlockType.HEADING, text="Chapter 5: Results"
                ),
                prev_item=make_block(block_type=BlockType.PARAGRAPH),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_not_continuation_always_passes(self) -> None:
        """A non-continuation verdict is never rejected."""

        validate_semantic_flow(
            next_item=make_block(block_type=BlockType.HEADING),
            prev_item=make_block(block_type=BlockType.HEADING),
            verdict=make_verdict(is_continuation=False),
        )

    def test_table_continuation_with_heading_next_passes(self) -> None:
        """Table continuation is not checked for heading semantics (only text is)."""

        validate_semantic_flow(
            next_item=make_block(block_type=BlockType.HEADING, text="Title"),
            prev_item=make_block(block_type=BlockType.PARAGRAPH),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.TABLE, is_continuation=True
            ),
        )

    def test_text_continuation_between_footnotes_passes(self) -> None:
        """Text continuation between footnote blocks passes (not heading/caption)."""

        validate_semantic_flow(
            next_item=make_block(block_type=BlockType.FOOTNOTE, text="continued note."),
            prev_item=make_block(block_type=BlockType.FOOTNOTE, text="1. See the"),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.TEXT, is_continuation=True
            ),
        )

    def test_text_continuation_between_paragraphs_passes(self) -> None:
        """Text continuation between two paragraphs passes."""

        validate_semantic_flow(
            next_item=make_block(block_type=BlockType.PARAGRAPH),
            prev_item=make_block(block_type=BlockType.PARAGRAPH),
            verdict=make_verdict(
                continuation_kind=PageContinuationKind.TEXT, is_continuation=True
            ),
        )

    def test_text_continuation_from_caption_raises(self) -> None:
        """Text continuation where prev_item is a caption raises `QualityError`."""

        with pytest.raises(QualityError, match="HEADING/CAPTION"):
            validate_semantic_flow(
                next_item=make_block(block_type=BlockType.PARAGRAPH),
                prev_item=make_block(block_type=BlockType.CAPTION, text="Figure 2"),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_text_continuation_from_heading_raises(self) -> None:
        """Text continuation where prev_item is a heading raises `QualityError`."""

        with pytest.raises(QualityError, match="HEADING/CAPTION"):
            validate_semantic_flow(
                next_item=make_block(block_type=BlockType.PARAGRAPH),
                prev_item=make_block(
                    block_type=BlockType.HEADING, text="Section Title"
                ),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_text_continuation_into_heading_raises(self) -> None:
        """Text continuation where next_item is a heading raises `QualityError`."""

        with pytest.raises(QualityError, match="HEADING/CAPTION"):
            validate_semantic_flow(
                next_item=make_block(block_type=BlockType.HEADING, text="Chapter 5"),
                prev_item=make_block(block_type=BlockType.PARAGRAPH),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )

    def test_text_continuation_into_caption_raises(self) -> None:
        """Text continuation where next_item is a caption raises `QualityError`."""

        with pytest.raises(QualityError, match="HEADING/CAPTION"):
            validate_semantic_flow(
                next_item=make_block(
                    block_type=BlockType.CAPTION, text="Table 1: Results"
                ),
                prev_item=make_block(block_type=BlockType.PARAGRAPH),
                verdict=make_verdict(
                    continuation_kind=PageContinuationKind.TEXT, is_continuation=True
                ),
            )
