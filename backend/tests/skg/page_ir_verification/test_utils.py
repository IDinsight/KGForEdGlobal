"""This is the main module for testing page_ir_verification/utils.py."""

# Standard Library
from typing import Union

# Third Party Library
import pytest

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification import utils
from skg.utils.constants import BlockType, ItemBoundary, PageBoundaryState


def make_artifact_block(
    *,
    bbox: tuple[float, float, float, float] = (100.0, 10.0, 200.0, 30.0),
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    text: str = "Page 1",
) -> Block:
    """Create a minimal valid artifact Block for testing.

    Parameters
    ----------
    bbox
        Bounding box as (x0, y0, x1, y1).
    boundary
        The item boundary state.
    text
        The text content.

    Returns
    -------
    Block
        A valid artifact Block instance.
    """

    return make_block(
        bbox=bbox, block_type=BlockType.ARTIFACT, boundary=boundary, text=text
    )


def make_block(
    *,
    bbox: tuple[float, float, float, float] = (100.0, 200.0, 400.0, 250.0),
    block_type: BlockType = BlockType.PARAGRAPH,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    text: str = "Some paragraph text for testing purposes.",
) -> Block:
    """Create a minimal valid Block for testing.

    Parameters
    ----------
    bbox
        Bounding box as (x0, y0, x1, y1).
    block_type
        The block type.
    boundary
        The item boundary state.
    text
        The text content of the block.

    Returns
    -------
    Block
        A valid Block instance.
    """

    return Block(
        bbox=list(bbox),
        block_type=block_type,
        boundary=boundary,
        kind="block",
        text=TextUnit(language="en", text=text) if text else None,
    )


def make_page_ir(
    *, image_height: int = 1000, items: list[Union[Block, Table]] | None = None
) -> PageIR:
    """Create a minimal valid PageIR for testing.

    Parameters
    ----------
    image_height
        The page image height in pixels.
    items
        The list of items on the page. Defaults to a single paragraph block.

    Returns
    -------
    PageIR
        A valid PageIR instance.
    """

    return PageIR(
        image_height=image_height, image_width=800, items=items or [make_block()]
    )


def make_table(
    *,
    bbox: tuple[float, float, float, float] = (50.0, 100.0, 500.0, 400.0),
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
) -> Table:
    """Create a minimal valid Table for testing.

    Parameters
    ----------
    bbox
        Bounding box as (x0, y0, x1, y1).
    boundary
        The item boundary state.

    Returns
    -------
    Table
        A valid Table instance.
    """

    return Table(
        bbox=list(bbox),
        boundary=boundary,
        kind="table",
        rows=[TableRow(cells=[TableCell(text=TextUnit(language="en", text="cell"))])],
    )


class TestDerivePageBoundaryState:
    """Tests for the _derive_page_boundary_state function."""

    def test_all_complete_returns_standalone(self) -> None:
        """A page with only complete items should be STANDALONE."""

        page_ir = make_page_ir(
            items=[
                make_block(boundary=ItemBoundary.COMPLETE),
                make_block(
                    boundary=ItemBoundary.COMPLETE, text="Another paragraph here."
                ),
            ]
        )
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir)
            == PageBoundaryState.STANDALONE
        )

    def test_artifacts_are_excluded_from_derivation(self) -> None:
        """Artifact items should be excluded; only non-artifact items count."""

        page_ir = make_page_ir(
            items=[
                make_artifact_block(boundary=ItemBoundary.TRUNCATED),
                make_block(boundary=ItemBoundary.COMPLETE),
            ]
        )
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir)
            == PageBoundaryState.STANDALONE
        )

    def test_both_boundary_item_returns_both(self) -> None:
        """A page with a BOTH-boundary item should return BOTH."""

        page_ir = make_page_ir(items=[make_block(boundary=ItemBoundary.BOTH)])
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir) == PageBoundaryState.BOTH
        )

    def test_fallback_when_all_items_filtered(self) -> None:
        """When all items are filtered (artifact + noise), fall back to raw items."""

        page_ir = make_page_ir(
            image_height=1000,
            items=[make_artifact_block(boundary=ItemBoundary.TRUNCATED)],
        )

        # Fallback uses raw items, so the truncated artifact drives the result.
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir)
            == PageBoundaryState.CONTINUES_TO_NEXT
        )

    def test_noise_items_are_excluded_from_derivation(self) -> None:
        """Header/footer noise items should be excluded from derivation."""

        page_ir = make_page_ir(
            image_height=1000,
            items=[
                # This is a page number near bottom —> should be filtered as noise.
                make_block(
                    bbox=(350.0, 960.0, 400.0, 980.0),
                    block_type=BlockType.PARAGRAPH,
                    boundary=ItemBoundary.TRUNCATED,
                    text="42",
                ),
                make_block(boundary=ItemBoundary.COMPLETE),
            ],
        )
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir)
            == PageBoundaryState.STANDALONE
        )

    def test_resumed_and_truncated_items_returns_both(self) -> None:
        """A page with resumed and truncated items should return BOTH."""

        page_ir = make_page_ir(
            items=[
                make_block(boundary=ItemBoundary.RESUMED),
                make_block(
                    boundary=ItemBoundary.TRUNCATED, text="Cut off sentence that"
                ),
            ]
        )
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir) == PageBoundaryState.BOTH
        )

    def test_resumed_item_returns_continues_from_prev(self) -> None:
        """A page with a resumed item should be CONTINUES_FROM_PREV."""

        page_ir = make_page_ir(
            items=[
                make_block(boundary=ItemBoundary.RESUMED),
                make_block(
                    boundary=ItemBoundary.COMPLETE, text="Another paragraph here."
                ),
            ]
        )
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir)
            == PageBoundaryState.CONTINUES_FROM_PREV
        )

    def test_table_boundary_is_considered(self) -> None:
        """Table items with non-complete boundaries should influence the state."""

        page_ir = make_page_ir(
            items=[
                make_table(boundary=ItemBoundary.RESUMED),
                make_block(boundary=ItemBoundary.COMPLETE),
            ]
        )
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir)
            == PageBoundaryState.CONTINUES_FROM_PREV
        )

    def test_truncated_item_returns_continues_to_next(self) -> None:
        """A page with a truncated item should be CONTINUES_TO_NEXT."""

        page_ir = make_page_ir(
            items=[
                make_block(boundary=ItemBoundary.COMPLETE),
                make_block(
                    boundary=ItemBoundary.TRUNCATED, text="Cut off sentence that"
                ),
            ]
        )
        assert (
            utils._derive_page_boundary_state(page_ir=page_ir)
            == PageBoundaryState.CONTINUES_TO_NEXT
        )


class TestIsArtifact:
    """Tests for the is_artifact function."""

    def test_artifact_block_returns_true(self) -> None:
        """An artifact-typed block should be identified as an artifact."""

        block = make_artifact_block()
        assert utils.is_artifact(item=block) is True

    def test_heading_block_returns_false(self) -> None:
        """A heading block should not be identified as an artifact."""

        block = make_block(block_type=BlockType.HEADING)
        assert utils.is_artifact(item=block) is False

    def test_non_artifact_block_returns_false(self) -> None:
        """A paragraph block should not be identified as an artifact."""

        block = make_block(block_type=BlockType.PARAGRAPH)
        assert utils.is_artifact(item=block) is False

    def test_table_returns_false(self) -> None:
        """A table item should not be identified as an artifact."""

        table = make_table()
        assert utils.is_artifact(item=table) is False


class TestIsProbableHeaderFooterNoise:
    """Tests for the is_probable_header_footer_noise function."""

    def test_block_in_middle_of_page_returns_false(self) -> None:
        """A block in the middle of the page should not be noise."""

        block = make_block(
            bbox=(100.0, 400.0, 400.0, 430.0), block_type=BlockType.PARAGRAPH, text="42"
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is False
        )

    def test_figure_block_returns_false(self) -> None:
        """A figure block (text is None) should not be noise."""

        block = Block(
            bbox=[350.0, 960.0, 400.0, 980.0],
            block_type=BlockType.FIGURE,
            boundary=ItemBoundary.COMPLETE,
            figure=FigureUnit(alt_text="small icon", figure_kind="image"),
            kind="block",
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is False
        )

    def test_long_text_near_bottom_returns_false(self) -> None:
        """Long text near bottom that doesn't match patterns should not be noise."""

        block = make_block(
            bbox=(100.0, 960.0, 500.0, 980.0),
            block_type=BlockType.PARAGRAPH,
            text="This is a full sentence that is not a page number at all.",
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is False
        )

    def test_page_number_near_bottom(self) -> None:
        """A short numeric string near the bottom margin should be noise."""

        block = make_block(
            bbox=(350.0, 960.0, 400.0, 980.0), block_type=BlockType.ARTIFACT, text="42"
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is True
        )

    def test_page_number_near_top(self) -> None:
        """A short numeric string near the top margin should be noise."""

        block = make_block(
            bbox=(350.0, 10.0, 400.0, 40.0), block_type=BlockType.PARAGRAPH, text="7"
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is True
        )

    def test_page_number_pattern_without_prefix(self) -> None:
        """'3/10' near bottom should be noise."""

        block = make_block(
            bbox=(350.0, 960.0, 400.0, 980.0),
            block_type=BlockType.PARAGRAPH,
            text="3/10",
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is True
        )

    def test_page_x_of_y_pattern(self) -> None:
        """'Page 3 / 10' near bottom should be noise."""

        block = make_block(
            bbox=(300.0, 955.0, 450.0, 975.0),
            block_type=BlockType.PARAGRAPH,
            text="Page 3 / 10",
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is True
        )

    def test_roman_numeral_near_bottom(self) -> None:
        """A roman numeral near the bottom should be noise."""

        block = make_block(
            bbox=(350.0, 960.0, 400.0, 980.0),
            block_type=BlockType.PARAGRAPH,
            text="xiv",
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is True
        )

    def test_table_always_returns_false(self) -> None:
        """Tables should never be classified as header/footer noise."""

        table = make_table(bbox=(50.0, 0.0, 500.0, 30.0))
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=table)
            is False
        )

    def test_tall_block_near_bottom_returns_false(self) -> None:
        """A tall block near bottom (box_h > threshold) should not be noise."""

        block = make_block(
            bbox=(100.0, 850.0, 500.0, 980.0), block_type=BlockType.PARAGRAPH, text="42"
        )
        assert (
            utils.is_probable_header_footer_noise(image_height=1000.0, item=block)
            is False
        )


class TestRequireNonNegativeInt:
    """Tests for the _require_non_negative_int function."""

    def test_bool_raises(self) -> None:
        """A boolean should raise ValueError even though bool is a subclass of int."""

        with pytest.raises(ValueError, match="invalid test_field"):
            utils._require_non_negative_int(
                field_name="test_field", report_name="0001_0002.json", value=True
            )

    def test_error_message_includes_report_name(self) -> None:
        """The error message should reference the report name."""

        with pytest.raises(ValueError, match="my_report.json"):
            utils._require_non_negative_int(
                field_name="some_field", report_name="my_report.json", value=-10
            )

    def test_float_raises(self) -> None:
        """A float should raise ValueError."""

        with pytest.raises(ValueError, match="invalid test_field"):
            utils._require_non_negative_int(
                field_name="test_field", report_name="0001_0002.json", value=3.14
            )

    def test_negative_int_raises(self) -> None:
        """A negative integer should raise ValueError."""

        with pytest.raises(ValueError, match="invalid test_field=-1"):
            utils._require_non_negative_int(
                field_name="test_field", report_name="0001_0002.json", value=-1
            )

    def test_none_raises(self) -> None:
        """None should raise ValueError."""

        with pytest.raises(ValueError, match="invalid test_field"):
            utils._require_non_negative_int(
                field_name="test_field", report_name="0001_0002.json", value=None
            )

    def test_string_raises(self) -> None:
        """A string should raise ValueError."""

        with pytest.raises(ValueError, match="invalid test_field"):
            utils._require_non_negative_int(
                field_name="test_field", report_name="0001_0002.json", value="5"
            )

    def test_valid_positive_int(self) -> None:
        """A positive integer should be accepted."""

        result = utils._require_non_negative_int(
            field_name="test_field", report_name="0001_0002.json", value=42
        )
        assert result == 42

    def test_valid_zero(self) -> None:
        """Zero should be accepted as a valid non-negative integer."""

        result = utils._require_non_negative_int(
            field_name="test_field", report_name="0001_0002.json", value=0
        )
        assert result == 0
