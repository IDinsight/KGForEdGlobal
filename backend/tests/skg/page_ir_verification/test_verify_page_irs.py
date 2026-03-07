"""This is the main module for testing page_ir_verification/verify_page_irs.py."""

# Standard Library
from unittest.mock import MagicMock, Mock, patch

# Package Library
from skg.page_ir_extraction.schemas import Block, Table, TextUnit
from skg.page_ir_verification import verify_page_pairs
from skg.utils.constants import BlockType
from tests.constants import PARAM


def create_mock_item(y0: float, y1: float) -> Mock:
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
        item = create_mock_item(y0, y1)
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
        item_inside = create_mock_item(200, 300)
        item_outside = create_mock_item(10, 90)
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
