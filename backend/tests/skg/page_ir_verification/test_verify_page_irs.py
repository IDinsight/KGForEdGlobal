"""This is the main module for testing page_ir_verification/verify_page_irs.py."""

# Standard Library
from unittest.mock import MagicMock, Mock, patch

# Third Party Library
import pytest

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification import verify_page_pairs
from skg.utils.constants import BlockType, ItemBoundary
from tests.constants import PARAM


def create_figure_block(
    *,
    alt_text: str,
    block_type: BlockType = BlockType.FIGURE,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    y0: float,
    y1: float,
) -> Block:
    """Create a valid figure block for continuity-candidate tests.

    Parameters
    ----------
    alt_text
        Short descriptive alt text for the figure metadata.
    block_type
        The block type to assign to the figure block.
    boundary
        The page-boundary state for the block.
    y0
        The top edge of the block bounding box.
    y1
        The bottom edge of the block bounding box.

    Returns
    -------
    Block
        A valid figure block.
    """

    return Block(
        bbox=[0.0, y0, 100.0, y1],
        block_type=block_type,
        boundary=boundary,
        figure=FigureUnit(alt_text=alt_text),
        kind="block",
        list_items=None,
        local_code=None,
        text=None,
    )


def create_mock_item_for_pick_bottommost(
    kind: str, boundary: ItemBoundary, is_figure: bool = False, is_viable: bool = False
) -> Mock:
    """Helper to create a mock Block or Table with explicit routing attributes.

    Parameters
    ----------
    kind
        The kind of the item (e.g., 'table', 'block').
    boundary
        The ItemBoundary enum value.
    is_figure
        Whether the mocked _is_figure_block should return True.
    is_viable
        Whether the mocked _is_viable_nonfigure_block_anchor should return True.

    Returns
    -------
    Mock
        The configured mock item.
    """

    item = Mock()
    item.kind = kind
    item.boundary = boundary
    item.is_figure = is_figure
    item.is_viable = is_viable
    return item


def create_mock_item_for_visible_crop(y0: float, y1: float) -> Mock:
    """Helper to create a mock item with specific y0 and y1 bounding box coordinates.

    Parameters
    ----------
    y0
        The y0 coordinate of the bounding box.
    y1
        The y1 coordinate of the bounding box.

    Returns
    -------
    Mock
        A mock item with the specified bounding box.
    """

    # bbox format: [x0, y0, x1, y1]. x-coordinates don't matter for this test.
    item = Mock()
    item.bbox = [0.0, float(y0), 100.0, float(y1)]
    return item


def create_table(
    *, boundary: ItemBoundary = ItemBoundary.COMPLETE, y0: float, y1: float
) -> Table:
    """Create a minimal valid table for continuity-candidate tests.

    Parameters
    ----------
    boundary
        The page-boundary state for the table.
    y0
        The top edge of the table bounding box.
    y1
        The bottom edge of the table bounding box.

    Returns
    -------
    Table
        A valid single-cell table.
    """

    return Table(
        bbox=[0.0, y0, 100.0, y1],
        boundary=boundary,
        header_row_count=0,
        kind="table",
        local_code=None,
        n_cols=1,
        repeats_header=None,
        rows=[
            TableRow(
                cells=[
                    TableCell(
                        col_span=1,
                        row_span=1,
                        text=TextUnit(language="en", text="Cell"),
                    )
                ]
            )
        ],
    )


def create_text_block(
    *,
    block_type: BlockType = BlockType.PARAGRAPH,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    text: str,
    y0: float,
    y1: float,
) -> Block:
    """Create a valid text block for continuity-candidate tests.

    Parameters
    ----------
    block_type
        The block type to assign to the block.
    boundary
        The page-boundary state for the block.
    text
        The text content for the block.
    y0
        The top edge of the block bounding box.
    y1
        The bottom edge of the block bounding box.

    Returns
    -------
    Block
        A valid text-bearing block.
    """

    return Block(
        bbox=[0.0, y0, 100.0, y1],
        block_type=block_type,
        boundary=boundary,
        figure=None,
        kind="block",
        list_items=None,
        local_code=None,
        text=TextUnit(language="en", text=text),
    )


class TestApplyVisibleCrop:
    """Tests for the _apply_visible_crop function, which filters candidate items based
    on whether their bounding boxes intersect with a specified vertical crop region
    defined by y_min and y_max. We want to ensure that it correctly identifies
    intersections, including edge cases where items are fully inside, partially
    overlapping, touching the boundaries, or fully outside the crop region.
    """

    def test_empty_candidates_returns_empty(self) -> None:
        """Test that _apply_visible_crop returns an empty list when given an empty list
        of candidates, regardless of the crop region parameters.
        """

        result = verify_page_pairs._apply_visible_crop(
            candidates=[], y_max=500.0, y_min=100.0
        )
        assert result == []

    @PARAM(
        "y0, y1, expected_kept, scenario",
        [
            # Crop region is [100.0, 500.0].
            (200.0, 300.0, True, "Fully inside crop region"),
            (50.0, 150.0, True, "Overlaps top boundary (y1 > y_min)"),
            (450.0, 550.0, True, "Overlaps bottom boundary (y0 < y_max)"),
            (50.0, 100.0, True, "Touches top boundary exactly (y1 == y_min)"),
            (500.0, 550.0, True, "Touches bottom boundary exactly (y0 == y_max)"),
            (50.0, 600.0, True, "Fully engulfs the crop region"),
            (10.0, 90.0, False, "Fully outside above (y1 < y_min)"),
            (510.0, 600.0, False, "Fully outside below (y0 > y_max)"),
        ],
    )
    def test_intersection_logic(
        self, y0: float, y1: float, expected_kept: bool, scenario: str
    ) -> None:
        """Test the intersection logic of _apply_visible_crop with various bounding box
        positions relative to the crop region.

        Parameters
        ----------
        y0
            The y0 coordinate of the item's bounding box.
        y1
            The y1 coordinate of the item's bounding box.
        expected_kept
            Whether the item is expected to be kept (True) or discarded (False) after
            applying the crop.
        scenario
            A description of the test scenario for debugging purposes.
        """

        y_min, y_max = 100.0, 500.0
        item = create_mock_item_for_visible_crop(y0, y1)
        candidates = [(42, item)]  # Arbitrary index 42
        result = verify_page_pairs._apply_visible_crop(
            candidates=candidates, y_max=y_max, y_min=y_min
        )

        if expected_kept:
            assert len(result) == 1, f"Failed scenario: {scenario} (should be kept)"
            assert result[0] == (42, item)
        else:
            assert (
                len(result) == 0
            ), f"Failed scenario: {scenario} (should be discarded)"

    def test_multiple_candidates_filtering(self) -> None:
        """Test that _apply_visible_crop correctly filters multiple candidates in one
        call, keeping only those that intersect with the crop region and discarding
        those that don't.
        """

        y_min, y_max = 100.0, 500.0
        item_inside = create_mock_item_for_visible_crop(200, 300)
        item_outside = create_mock_item_for_visible_crop(10, 90)
        candidates = [(0, item_inside), (1, item_outside), (2, item_inside)]

        result = verify_page_pairs._apply_visible_crop(
            candidates=candidates, y_max=y_max, y_min=y_min
        )

        # Should keep items at index 0 and 2, but drop index 1.
        assert len(result) == 2
        assert result == [(0, item_inside), (2, item_inside)]


@patch("skg.page_ir_verification.verify_page_pairs.is_probable_header_footer_noise")
@patch("skg.page_ir_verification.verify_page_pairs.is_artifact")
class TestFilterCandidatePool:
    """Tests for the _filter_candidate_pool function, which filters out items based on
    artifact and noise heuristics. We want to ensure that it correctly filters items
    and handles edge cases like all items being filtered out or an empty input list.
    """

    def test_all_filtered_triggers_fallback(
        self, mock_is_artifact: MagicMock, mock_is_noise: MagicMock
    ) -> None:
        """Test the case where all items are filtered out, which should trigger the
        fallback mechanism that returns the original list with indices instead of an
        empty list.

        Parameters
        ----------
        mock_is_artifact
            The mocked is_artifact function.
        mock_is_noise
            The mocked is_probable_header_footer_noise function.
        """

        # Item 0: Artifact=True  -> Noise is NOT called (short-circuits)
        # Item 1: Artifact=False -> Noise is called, returns True (Noise)
        mock_is_artifact.side_effect = [True, False]

        mock_is_noise.side_effect = [True]
        items = ["item0", "item1"]
        result = verify_page_pairs._filter_candidate_pool(
            image_height=1000.0, items=items
        )
        assert result == [(0, "item0"), (1, "item1")]

    def test_empty_input_list(
        self, mock_is_artifact: MagicMock, mock_is_noise: MagicMock
    ) -> None:
        """Test the case where the input list of items is empty. The function should
        return an empty list and not call the artifact or noise functions at all.

        Parameters
        ----------
        mock_is_artifact
            The mocked is_artifact function.
        mock_is_noise
            The mocked is_probable_header_footer_noise function.
        """

        items: list[Block | Table] = []
        result = verify_page_pairs._filter_candidate_pool(
            image_height=1000.0, items=items
        )
        assert result == []
        mock_is_artifact.assert_not_called()
        mock_is_noise.assert_not_called()

    def test_mixed_candidates_filters_correctly(
        self, mock_is_artifact: MagicMock, mock_is_noise: MagicMock
    ) -> None:
        """Test a mix of items where some are artifacts, some are noise, and some are
        valid.

        Parameters
        ----------
        mock_is_artifact
            The mocked is_artifact function.
        mock_is_noise
            The mocked is_probable_header_footer_noise function.
        """

        # Item 0: Artifact=True  -> Noise is NOT called (short-circuits)
        # Item 1: Artifact=False -> Noise is called, returns True (Noise)
        # Item 2: Artifact=False -> Noise is called, returns False (Keep)
        # Item 3: Artifact=False -> Noise is called, returns False (Keep)
        mock_is_artifact.side_effect = [True, False, False, False]

        # We only provide 3 values because the first item short-circuits.
        mock_is_noise.side_effect = [True, False, False]

        items = ["item0", "item1", "item2", "item3"]
        result = verify_page_pairs._filter_candidate_pool(
            image_height=1000.0, items=items
        )

        assert result == [(2, "item2"), (3, "item3")]
        assert mock_is_artifact.call_count == 4
        assert mock_is_noise.call_count == 3  # Verifying the short-circuit!

    def test_none_filtered(
        self, mock_is_artifact: MagicMock, mock_is_noise: MagicMock
    ) -> None:
        """Test the case where none of the items are filtered out, so the output should
        be the same as the input list but with indices.

        Parameters
        ----------
        mock_is_artifact
            The mocked is_artifact function.
        mock_is_noise
            The mocked is_probable_header_footer_noise function.
        """

        mock_is_artifact.return_value = False
        mock_is_noise.return_value = False
        items = ["item0", "item1"]
        result = verify_page_pairs._filter_candidate_pool(
            image_height=1000.0, items=items
        )
        assert result == [(0, "item0"), (1, "item1")]


class TestOrderedNextCandidates:
    """Tests for ordered next-page continuity candidate selection."""

    def test_heading_and_caption_blocks_are_excluded_from_additional_candidates(
        self,
    ) -> None:
        """Test that heading and caption blocks are skipped when filling extra slots.

        The primary pick may still be a normal text block, but heading and caption
        blocks should not appear in the supplemental candidate pools that follow it.
        """

        first_paragraph = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Paragraph 1",
            y0=10.0,
            y1=20.0,
        )
        heading_block = create_text_block(
            block_type=BlockType.HEADING,
            boundary=ItemBoundary.COMPLETE,
            text="Heading",
            y0=25.0,
            y1=35.0,
        )
        caption_block = create_text_block(
            block_type=BlockType.CAPTION,
            boundary=ItemBoundary.COMPLETE,
            text="Caption",
            y0=40.0,
            y1=50.0,
        )
        second_paragraph = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Paragraph 2",
            y0=55.0,
            y1=65.0,
        )
        table_item = create_table(
            boundary=ItemBoundary.COMPLETE,
            y0=70.0,
            y1=90.0,
        )
        prev_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Previous paragraph",
            y0=900.0,
            y1=980.0,
        )

        result = verify_page_pairs._ordered_next_candidates(
            image_height=1000.0,
            items=[
                first_paragraph,
                heading_block,
                caption_block,
                second_paragraph,
                table_item,
            ],
            k=5,
            prev_item=prev_item,
            visible_y_max=None,
        )

        assert [index for index, _ in result] == [0, 3, 4]

    def test_invalid_k_raises_value_error(self) -> None:
        """Test that `k` must be at least one."""

        prev_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Previous paragraph",
            y0=900.0,
            y1=980.0,
        )

        with pytest.raises(ValueError, match="k must be >= 1, got 0"):
            verify_page_pairs._ordered_next_candidates(
                image_height=1000.0,
                items=[
                    create_text_block(
                        block_type=BlockType.PARAGRAPH,
                        boundary=ItemBoundary.COMPLETE,
                        text="Next paragraph",
                        y0=10.0,
                        y1=30.0,
                    )
                ],
                k=0,
                prev_item=prev_item,
                visible_y_max=None,
            )

    def test_raises_when_visible_crop_removes_every_candidate(self) -> None:
        """Test that an empty post-crop candidate pool raises `ValueError`."""

        next_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Hidden below crop",
            y0=250.0,
            y1=300.0,
        )
        prev_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Previous paragraph",
            y0=900.0,
            y1=980.0,
        )

        with pytest.raises(ValueError, match="No non-artifact items found."):
            verify_page_pairs._ordered_next_candidates(
                image_height=1000.0,
                items=[next_item],
                k=3,
                prev_item=prev_item,
                visible_y_max=100.0,
            )

    def test_same_family_candidates_preserve_reading_order_after_final_reordering(
        self,
    ) -> None:
        """Test that final ordering promotes same-family matches without scrambling
        them.

        This covers the branch where the primary candidate returned by `_pick_topmost`
        is cross-family, after which the final return value must still move same-family
        matches ahead of that cross-family primary while preserving within-pool reading
        order.
        """

        later_paragraph = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Later paragraph",
            y0=30.0,
            y1=40.0,
        )
        table_item = create_table(
            boundary=ItemBoundary.COMPLETE,
            y0=10.0,
            y1=20.0,
        )
        earlier_paragraph = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Earlier paragraph",
            y0=20.0,
            y1=25.0,
        )
        prev_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Previous paragraph",
            y0=900.0,
            y1=980.0,
        )

        with patch(
            "skg.page_ir_verification.verify_page_pairs._pick_topmost"
        ) as mock_pick_topmost:
            mock_pick_topmost.return_value = (1, table_item)
            result = verify_page_pairs._ordered_next_candidates(
                image_height=1000.0,
                items=[later_paragraph, table_item, earlier_paragraph],
                k=3,
                prev_item=prev_item,
                visible_y_max=None,
            )

        assert [index for index, _ in result] == [2, 0, 1]

    def test_uses_figure_family_before_generic_block_fallbacks(self) -> None:
        """Test that figure anchors prefer figure candidates over earlier text blocks."""

        paragraph_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Paragraph before figure",
            y0=5.0,
            y1=15.0,
        )
        figure_item = create_figure_block(
            alt_text="Diagram",
            block_type=BlockType.FIGURE,
            boundary=ItemBoundary.RESUMED,
            y0=20.0,
            y1=40.0,
        )
        table_item = create_table(
            boundary=ItemBoundary.COMPLETE,
            y0=45.0,
            y1=65.0,
        )
        prev_item = create_figure_block(
            alt_text="Previous diagram",
            block_type=BlockType.FIGURE,
            boundary=ItemBoundary.TRUNCATED,
            y0=900.0,
            y1=980.0,
        )

        result = verify_page_pairs._ordered_next_candidates(
            image_height=1000.0,
            items=[paragraph_item, figure_item, table_item],
            k=2,
            prev_item=prev_item,
            visible_y_max=None,
        )

        assert [index for index, _ in result] == [1, 0]


@patch("skg.page_ir_verification.verify_page_pairs._is_viable_nonfigure_block_anchor")
@patch("skg.page_ir_verification.verify_page_pairs._is_figure_block")
class TestPickBottommost:
    """Test suite for the _pick_bottommost selection logic."""

    def setup_method(self) -> None:
        """Setup logic to configure the mocked helper functions before each test."""

        # By setting the side_effects to lambda functions, the patches will dynamically
        # return whatever we set on our mock items in the tests.
        self.mock_is_fig_side_effect = lambda item: getattr(item, "is_figure", False)
        self.mock_is_viable_side_effect = lambda item: getattr(item, "is_viable", False)

    def test_absolute_fallback_returns_first_item(
        self, mock_is_figure: MagicMock, mock_is_viable: MagicMock
    ) -> None:
        """Test that if nothing matches any criteria, candidates[0] is returned.

        Parameters
        ----------
        mock_is_figure
            The mocked _is_figure_block function, which will return True if the item
            has the attribute is_figure set to True.
        mock_is_viable
            The mocked _is_viable_nonfigure_block_anchor function, which will return
            True if the item has the attribute is_viable set to True.
        """

        mock_is_figure.side_effect = self.mock_is_fig_side_effect
        mock_is_viable.side_effect = self.mock_is_viable_side_effect

        # Neither preferred, neither tables, neither figures, neither viable.
        item0 = create_mock_item_for_pick_bottommost("block", ItemBoundary.COMPLETE)
        item1 = create_mock_item_for_pick_bottommost("block", ItemBoundary.COMPLETE)

        candidates = [(0, item0), (1, item1)]
        result = verify_page_pairs._pick_bottommost(candidates)
        assert result == (0, item0)

    def test_fallback_to_regular_candidates(
        self, mock_is_figure: MagicMock, mock_is_viable: MagicMock
    ) -> None:
        """Test that if no preferred candidates exist, it uses the regular list.

        Parameters
        ----------
        mock_is_figure
            The mocked _is_figure_block function, which will return True if the item
            has the attribute is_figure set to True.
        mock_is_viable
            The mocked _is_viable_nonfigure_block_anchor function, which will return
            True if the item has the attribute is_viable set to True.
        """

        mock_is_figure.side_effect = self.mock_is_fig_side_effect
        mock_is_viable.side_effect = self.mock_is_viable_side_effect

        item0 = create_mock_item_for_pick_bottommost("block", ItemBoundary.COMPLETE)
        item1 = create_mock_item_for_pick_bottommost(
            "table", ItemBoundary.COMPLETE
        )  # Not preferred, but best
        candidates = [(0, item0), (1, item1)]

        result = verify_page_pairs._pick_bottommost(candidates)
        assert result == (1, item1)

    def test_preferred_figure_in_top_5(
        self, mock_is_figure: MagicMock, mock_is_viable: MagicMock
    ) -> None:
        """Test that a preferred figure in the top 5 is picked if no table exists.

        Parameters
        ----------
        mock_is_figure
            The mocked _is_figure_block function, which will return True if the item
            has the attribute is_figure set to True.
        mock_is_viable
            The mocked _is_viable_nonfigure_block_anchor function, which will return
            True if the item has the attribute is_viable set to True.
        """

        mock_is_figure.side_effect = self.mock_is_fig_side_effect
        mock_is_viable.side_effect = self.mock_is_viable_side_effect

        item0 = create_mock_item_for_pick_bottommost("block", ItemBoundary.COMPLETE)
        item1 = create_mock_item_for_pick_bottommost(
            "block", ItemBoundary.BOTH, is_figure=True
        )  # Should win
        item2 = create_mock_item_for_pick_bottommost(
            "block", ItemBoundary.TRUNCATED, is_viable=True
        )

        candidates = [(0, item0), (1, item1), (2, item2)]

        result = verify_page_pairs._pick_bottommost(candidates)
        assert result == (1, item1)

    def test_preferred_table_in_top_5(
        self, mock_is_figure: MagicMock, mock_is_viable: MagicMock
    ) -> None:
        """Test that a preferred (TRUNCATED) table in the top 5 is picked first.

        Parameters
        ----------
        mock_is_figure
            The mocked _is_figure_block function, which will return True if the item
            has the attribute is_figure set to True.
        mock_is_viable
            The mocked _is_viable_nonfigure_block_anchor function, which will return
            True if the item has the attribute is_viable set to True.
        """

        mock_is_figure.side_effect = self.mock_is_fig_side_effect
        mock_is_viable.side_effect = self.mock_is_viable_side_effect

        item0 = create_mock_item_for_pick_bottommost("block", ItemBoundary.COMPLETE)
        item1 = create_mock_item_for_pick_bottommost(
            "table", ItemBoundary.TRUNCATED
        )  # Should win
        item2 = create_mock_item_for_pick_bottommost(
            "block", ItemBoundary.COMPLETE, is_viable=True
        )
        candidates = [(0, item0), (1, item1), (2, item2)]

        result = verify_page_pairs._pick_bottommost(candidates)
        assert result == (1, item1)

    def test_table_outside_top_5_is_ignored_by_first_loop(
        self, mock_is_figure: MagicMock, mock_is_viable: MagicMock
    ) -> None:
        """Test that a table outside the first 5 elements misses the table priority.

        Parameters
        ----------
        mock_is_figure
            The mocked _is_figure_block function, which will return True if the item
            has the attribute is_figure set to True.
        mock_is_viable
            The mocked _is_viable_nonfigure_block_anchor function, which will return
            True if the item has the attribute is_viable set to True.
        """

        mock_is_figure.side_effect = self.mock_is_fig_side_effect
        mock_is_viable.side_effect = self.mock_is_viable_side_effect

        # Items 0 through 4 are PREFERRED blocks. This fills up the first 5 slots
        # of the `preferred` list inside the function.
        candidates = [
            (i, create_mock_item_for_pick_bottommost("block", ItemBoundary.TRUNCATED))
            for i in range(5)
        ]

        # Item 5 (6th item) is a preferred table. Because 5 preferred items precede it,
        # it falls outside the [:5] slice when _pick(preferred) is called.
        table_item = create_mock_item_for_pick_bottommost(
            "table", ItemBoundary.TRUNCATED
        )
        candidates.append((5, table_item))

        # Item 6 is a preferred viable block.
        viable_item = create_mock_item_for_pick_bottommost(
            "block", ItemBoundary.TRUNCATED, is_viable=True
        )
        candidates.append((6, viable_item))

        result = verify_page_pairs._pick_bottommost(candidates)

        # The table is missed because it is the 6th preferred item. The loop falls
        # through to the viable block loop, which checks all items.
        assert result == (6, viable_item)


@PARAM(
    "kind, block_type, expected",
    [
        # A Block that IS an artifact.
        ("block", BlockType.ARTIFACT, True),
        # A Block that is NOT an artifact (e.g., a paragraph or heading).
        ("block", BlockType.PARAGRAPH, False),
        ("block", BlockType.HEADING, False),
        # A Table (kind is "table", so block_type doesn't matter).
        ("table", None, False),
    ],
)
def test_is_artifact(kind: str, block_type: BlockType, expected: bool) -> None:
    """Test the is_artifact function with various combinations of kind and block_type.

    Parameters
    ----------
    kind
        The kind of the item (e.g., "block", "table").
    block_type
        The block type of the item (e.g., "artifact", "paragraph"). This is
        only relevant if kind is "block".
    expected
        The expected result from is_artifact.
    """

    mock_item = Mock()
    mock_item.kind = kind
    mock_item.block_type = block_type

    result = verify_page_pairs.is_artifact(mock_item)
    assert result is expected


@PARAM(
    "kind, text_val, bbox, expected, scenario",
    [
        # Early exits: wrong kind or no text.
        ("table", "12", [0, 10, 100, 30], False, "Fails: Is a table, not a block"),
        ("block", None, [0, 10, 100, 30], False, "Fails: Block has no text"),
        ("block", "", [0, 10, 100, 30], False, "Fails: Block text is empty"),
        # Position and height bounds (image height is 1000 for all tests). Top
        # threshold: y0 <= 60 (0.06 * 1000). Bottom threshold: y1 >= 940 (0.94 * 1000).
        # Max box height allowed: max(90.0, 50.0) = 90.0.
        ("block", "12", [0, 400, 100, 420], False, "Fails: Middle of the page"),
        (
            "block",
            "12",
            [0, 10, 100, 150],
            False,
            "Fails: Near top, but box height too large (140 > 90)",
        ),
        # Successful regex matches (valid headers/footers).
        (
            "block",
            "12",
            [0, 10, 100, 30],
            True,
            "Passes: Standard digit page number near top",
        ),
        (
            "block",
            " xiv ",
            [0, 950, 100, 970],
            True,
            "Passes: Roman numeral with padding near bottom",
        ),
        ("block", "Page 5", [0, 10, 100, 30], True, "Passes: 'Page N' pattern"),
        (
            "block",
            "Page 12 / 20",
            [0, 950, 100, 970],
            True,
            "Passes: 'Page N / M' pattern near bottom",
        ),
        (
            "block",
            "2 / 5",
            [0, 10, 100, 30],
            True,
            "Passes: 'N / M' pattern without 'Page'",
        ),
        # Regex failures (looks like a header, but contains actual content).
        (
            "block",
            "Chapter 1",
            [0, 10, 100, 30],
            False,
            "Fails: Doesn't match page number regex patterns",
        ),
        (
            "block",
            "A very long running header string",
            [0, 10, 100, 30],
            False,
            "Fails: String too long (>20 chars)",
        ),
    ],
)
def test_is_probable_header_footer_noise(
    kind: str, text_val: str | None, bbox: list[int], expected: bool, scenario: str
) -> None:
    """Test the is_probable_header_footer_noise function with various item
    configurations.

    Parameters
    ----------
    kind
        The kind of the item (e.g., "block", "table").
    text_val
        The text content of the item, or None if no text.
    bbox
        The bounding box of the item in the format [x0, y0, x1, y1].
    expected
        The expected result from is_probable_header_footer_noise.
    scenario
        A description of the test scenario for debugging purposes.
    """

    image_height = 1000.0
    mock_item = Mock()
    mock_item.kind = kind
    mock_item.bbox = bbox

    if text_val is None:
        mock_item.text = None
    else:
        mock_text_unit = MagicMock(spec=TextUnit)
        mock_text_unit.text = text_val
        mock_item.text = mock_text_unit

    result = verify_page_pairs.is_probable_header_footer_noise(
        image_height=image_height, item=mock_item
    )
    assert result is expected, f"Failed scenario: {scenario}"
