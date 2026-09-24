"""This is the main module for testing page_ir_verification/verify_page_irs.py."""

# Standard Library
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, call, patch

# Third Party Library
import pytest

from PIL import Image

# Package Library
from kgfeg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from kgfeg.page_ir_verification import verify_page_pairs
from kgfeg.utils.constants import BlockType, ItemBoundary
from tests.constants import PARAM


class VerdictStub:
    """VerdictStub object compatible with the function under test."""

    def __init__(
        self,
        *,
        confidence: float,
        continuation_kind_value: str,
        is_continuation: bool,
        set_next_table_repeats_header: bool | None = None,
    ) -> None:
        """Initialize the verdict stub.

        Parameters
        ----------
        confidence
            Confidence score for the verdict.
        continuation_kind_value
            String value exposed via `continuation_kind.value`.
        is_continuation
            Whether the page-pair is judged to be continuous.
        set_next_table_repeats_header
            Optional table header patch flag.
        """

        self.confidence = confidence
        self.continuation_kind = SimpleNamespace(value=continuation_kind_value)
        self.is_continuation = is_continuation
        self.next_page_index: int | None = None
        self.prev_page_index: int | None = None
        self.set_next_table_repeats_header = set_next_table_repeats_header

    def model_dump(self, mode: str) -> dict[str, Any]:
        """Return a dict matching the accessed verdict fields.

        Parameters
        ----------
        mode
            Dump mode requested by the caller.

        Returns
        -------
        dict[str, Any]
            Serialized verdict content.
        """

        assert mode == "json"
        return {
            "confidence": self.confidence,
            "continuation_kind": self.continuation_kind.value,
            "is_continuation": self.is_continuation,
            "next_page_index": self.next_page_index,
            "prev_page_index": self.prev_page_index,
            "set_next_table_repeats_header": self.set_next_table_repeats_header,
        }


@dataclass(frozen=True)
class VerificationConfigStub:
    """Minimal config stub for candidate-pair generation tests."""

    min_confidence_to_patch: float = 0.0
    min_confidence_to_select_positive: float = 0.0
    min_confidence_to_stop_negative_search: float = 0.0
    model: str = "fake"
    next_page_crop_padding_px: float = 25.0


def create_block_item_json(*, repeats_header: bool | None = None) -> dict[str, Any]:
    """Create a representative block item JSON payload.

    Parameters
    ----------
    repeats_header
        Optional `repeats_header` field to include in the payload.

    Returns
    -------
    dict[str, Any]
        A representative block item JSON dictionary.
    """

    item_json: dict[str, Any] = {
        "bbox": [0.0, 10.0, 100.0, 40.0],
        "boundary": "truncated",
        "kind": "block",
        "meta": {"tags": ["body", "candidate"]},
        "text": {"language": "en", "text": "Body paragraph"},
    }

    if repeats_header is not None:
        item_json["repeats_header"] = repeats_header

    return item_json


def create_candidate_pair_spec(
    *,
    crop_y_max: float = 100.0,
    next_boundary: ItemBoundary = ItemBoundary.COMPLETE,
    next_index: int = 0,
    next_rank: int = 0,
    prev_boundary: ItemBoundary = ItemBoundary.TRUNCATED,
    prev_index: int = 0,
    prev_rank: int = 0,
) -> verify_page_pairs.CandidatePairSpec:
    """Create a minimal candidate-pair spec for verification-attempt tests.

    Parameters
    ----------
    crop_y_max
        Requested crop height in page coordinates.
    next_boundary
        Boundary state for the next-page item.
    next_index
        Next-page candidate index.
    next_rank
        Rank of the next-page candidate.
    prev_boundary
        Boundary state for the previous-page item.
    prev_index
        Previous-page candidate index.
    prev_rank
        Rank of the previous-page candidate.

    Returns
    -------
    verify_page_pairs.CandidatePairSpec
        A candidate-pair specification with valid block items.
    """

    next_item = create_text_block(
        boundary=next_boundary,
        text=f"Next {next_index}",
        y0=0.0,
        y1=max(1.0, crop_y_max),
    )
    prev_item = create_text_block(
        boundary=prev_boundary,
        text=f"Prev {prev_index}",
        y0=900.0,
        y1=980.0,
    )

    return verify_page_pairs.CandidatePairSpec(
        crop_y_max=crop_y_max,
        next_index=next_index,
        next_item=next_item,
        next_rank=next_rank,
        prev_index=prev_index,
        prev_item=prev_item,
        prev_rank=prev_rank,
    )


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


def create_image(*, fp: Path, size: tuple[int, int]) -> None:
    """Create a solid-color test image on disk.

    Parameters
    ----------
    fp
        Output path for the PNG image.
    size
        Image size as `(width, height)`.
    """

    fp.parent.mkdir(parents=True, exist_ok=True)

    with Image.new("RGB", size) as image:
        image.save(fp)


def create_list_item(*, marker: str | None, text: str) -> dict[str, Any]:
    """Create a list-item payload for block excerpt tests.

    Parameters
    ----------
    marker
        The list marker to attach to the item.
    text
        The text payload for the list item.

    Returns
    -------
    dict[str, Any]
        A PageIR-style list-item dictionary.
    """

    return {"marker": marker, "text": {"language": "en", "text": text}}


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


def create_page_ir(*, image_height: int, items: list[Block]) -> PageIR:
    """Create a minimal `PageIR` for candidate-pair generation tests.

    Parameters
    ----------
    image_height
        The rendered page height in pixels.
    items
        The page items in reading order.

    Returns
    -------
    PageIR
        A minimal page IR instance.
    """

    return PageIR(image_height=image_height, items=items)


def create_page_ir_like(*, image_height: float, items: list[Any]) -> SimpleNamespace:
    """Create a minimal page-IR-like object for orchestration tests.

    Parameters
    ----------
    image_height
        Page image height in pixels.
    items
        Ordered page items.

    Returns
    -------
    SimpleNamespace
        Object exposing `image_height` and `items`.
    """

    return SimpleNamespace(image_height=image_height, items=items)


def create_row(cells: list[Any]) -> dict[str, list[Any]]:
    """Create a table-row payload for table excerpt tests.

    Parameters
    ----------
    cells
        The row cells.

    Returns
    -------
    dict[str, list[Any]]
        A PageIR-style row dictionary.
    """

    return {"cells": cells}


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


def create_table_item_json() -> dict[str, Any]:
    """Create a representative table item JSON payload.

    Returns
    -------
    dict[str, Any]
        A representative table item JSON dictionary.
    """

    return {
        "bbox": [0.0, 10.0, 100.0, 80.0],
        "boundary": "resumed",
        "cells": [
            {
                "bbox": [0.0, 10.0, 50.0, 20.0],
                "col_span": 1,
                "column": 0,
                "is_header": True,
                "row": 0,
                "row_span": 1,
                "text": {"language": "en", "text": "Header"},
            }
        ],
        "kind": "table",
        "meta": {"source": {"page": 4}},
        "repeats_header": True,
    }


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


def create_text_cell(text: Any) -> dict[str, Any]:
    """Create a text-cell payload for table excerpt tests.

    Parameters
    ----------
    text
        The value to place under the cell `text` field.

    Returns
    -------
    dict[str, Any]
        A PageIR-style cell dictionary.
    """

    return {"text": text}


def create_verdict(
    *,
    confidence: float,
    continuation_kind_value: str,
    is_continuation: bool,
    set_next_table_repeats_header: bool | None = None,
) -> SimpleNamespace:
    """Create a lightweight verdict object with the accessed attributes.

    Parameters
    ----------
    confidence
        The verdict confidence score.
    continuation_kind_value
        String value exposed via `continuation_kind.value`.
    is_continuation
        Whether the verdict is a positive continuation.
    set_next_table_repeats_header
        Optional table-header patch hint.

    Returns
    -------
    SimpleNamespace
        A verdict-like object compatible with the function under test.
    """

    return SimpleNamespace(
        confidence=confidence,
        continuation_kind=SimpleNamespace(value=continuation_kind_value),
        is_continuation=is_continuation,
        set_next_table_repeats_header=set_next_table_repeats_header,
    )


def create_verification_dirs(*, root_dir: Path) -> SimpleNamespace:
    """Create a minimal verification-dirs-like object.

    Parameters
    ----------
    root_dir
        Base directory used to derive report and crop paths.

    Returns
    -------
    SimpleNamespace
        Object exposing the directory attributes used by the function.
    """

    return SimpleNamespace(
        page_irs_pair_crops=root_dir / "pair_crops",
        page_irs_pair_reports=root_dir / "pair_reports",
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


@patch("kgfeg.page_ir_verification.verify_page_pairs.is_probable_header_footer_noise")
@patch("kgfeg.page_ir_verification.verify_page_pairs.is_artifact")
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


class TestGenerateCandidatePairs:
    """Tests for candidate-pair generation across a page boundary."""

    @patch("kgfeg.page_ir_verification.verify_page_pairs._ordered_next_candidates")
    def test_calls_ordered_next_candidates_once_for_primary_reporting_and_once_per_prev_anchor(
        self, mock_ordered_next_candidates: MagicMock
    ) -> None:
        """Test that the function performs a dedicated primary lookup before pair
        expansion.

        The pre-loop lookup establishes `primary_indices` from the first previous-page
        anchor, while the loop then recomputes ordered next candidates for each
        previous anchor to build the full verification workload.

        Parameters
        ----------
        mock_ordered_next_candidates
            The mocked _ordered_next_candidates function, which should be called once
            for the primary candidate selection and then once per previous anchor
            during pair expansion.
        """

        config = VerificationConfigStub(next_page_crop_padding_px=25.0)
        prev_item_a = create_text_block(
            boundary=ItemBoundary.TRUNCATED, text="Previous A", y0=900.0, y1=960.0
        )
        prev_item_b = create_text_block(
            boundary=ItemBoundary.TRUNCATED, text="Previous B", y0=940.0, y1=990.0
        )
        next_item_a = create_text_block(
            boundary=ItemBoundary.RESUMED, text="Next A", y0=10.0, y1=40.0
        )
        next_item_b = create_text_block(
            boundary=ItemBoundary.COMPLETE, text="Next B", y0=50.0, y1=90.0
        )
        next_page_ir = create_page_ir(
            image_height=1000, items=[next_item_a, next_item_b]
        )
        prev_candidates: list[tuple[int, Block]] = [
            (10, prev_item_a),
            (11, prev_item_b),
        ]
        mock_ordered_next_candidates.side_effect = [
            [(0, next_item_a)],
            [(0, next_item_a), (1, next_item_b)],
            [(1, next_item_b)],
        ]

        pair_specs, primary_indices = verify_page_pairs._generate_candidate_pairs(
            config=config, next_page_ir=next_page_ir, prev_candidates=prev_candidates
        )

        assert primary_indices == {"next_item_index": 0, "prev_item_index": 10}
        assert [
            (spec.prev_index, spec.next_index, spec.prev_rank, spec.next_rank)
            for spec in pair_specs
        ] == [
            (10, 0, 0, 0),
            (10, 1, 0, 1),
            (11, 1, 1, 0),
        ]
        assert mock_ordered_next_candidates.call_count == 3
        mock_ordered_next_candidates.assert_has_calls(
            [
                call(
                    image_height=1000, items=next_page_ir.items, prev_item=prev_item_a
                ),
                call(
                    image_height=1000, items=next_page_ir.items, prev_item=prev_item_a
                ),
                call(
                    image_height=1000, items=next_page_ir.items, prev_item=prev_item_b
                ),
            ]
        )

    @patch("kgfeg.page_ir_verification.verify_page_pairs._ordered_next_candidates")
    def test_caps_crop_y_max_and_skips_duplicate_pairs(
        self, mock_ordered_next_candidates: MagicMock
    ) -> None:
        """Test that crop padding is capped at page height and duplicate pairs are
        skipped.

        Parameters
        ----------
        mock_ordered_next_candidates
            The mocked _ordered_next_candidates function, which should be called for
            each previous anchor and return candidate pools that test the crop padding
            logic and duplicate pair skipping. In this test, the second previous anchor
            would generate a duplicate pair with the first if not for the skipping
            logic, and the crop padding should be applied to limit candidates to a
            reasonable area below the page boundary.
        """

        config = VerificationConfigStub(next_page_crop_padding_px=50.0)
        prev_item = create_text_block(
            boundary=ItemBoundary.TRUNCATED, text="Previous anchor", y0=920.0, y1=990.0
        )
        next_item_bottom = create_text_block(
            boundary=ItemBoundary.RESUMED, text="Bottom candidate", y0=930.0, y1=980.0
        )
        next_item_mid = create_text_block(
            boundary=ItemBoundary.COMPLETE, text="Middle candidate", y0=70.0, y1=100.0
        )
        next_page_ir = create_page_ir(
            image_height=1000, items=[next_item_bottom, next_item_mid]
        )
        mock_ordered_next_candidates.side_effect = [
            [(0, next_item_bottom)],
            [(0, next_item_bottom), (0, next_item_bottom), (1, next_item_mid)],
        ]

        pair_specs, primary_indices = verify_page_pairs._generate_candidate_pairs(
            config=config, next_page_ir=next_page_ir, prev_candidates=[(7, prev_item)]
        )

        assert primary_indices == {"next_item_index": 0, "prev_item_index": 7}
        assert [
            (spec.prev_index, spec.next_index, spec.crop_y_max) for spec in pair_specs
        ] == [
            (7, 0, 1000.0),
            (7, 1, 150.0),
        ]

    @patch("kgfeg.page_ir_verification.verify_page_pairs._ordered_next_candidates")
    def test_stops_after_nine_candidate_pairs(
        self, mock_ordered_next_candidates: MagicMock
    ) -> None:
        """Test that pair generation stops once the nine-pair workload limit is reached.

        Parameters
        ----------
        mock_ordered_next_candidates
            The mocked _ordered_next_candidates function, which should be called for
            each previous anchor until the nine-pair limit is reached. In this test, we
            set up enough previous anchors and next candidates that without the limit,
            we would generate more than nine pairs. We want to confirm that the
            function stops generating pairs once it hits the limit, even if there are
            more candidates available.
        """

        config = VerificationConfigStub(next_page_crop_padding_px=10.0)
        prev_candidates: list[tuple[int, Block]] = [
            (
                10,
                create_text_block(
                    boundary=ItemBoundary.TRUNCATED,
                    text="Previous 10",
                    y0=900.0,
                    y1=930.0,
                ),
            ),
            (
                11,
                create_text_block(
                    boundary=ItemBoundary.TRUNCATED,
                    text="Previous 11",
                    y0=910.0,
                    y1=940.0,
                ),
            ),
            (
                12,
                create_text_block(
                    boundary=ItemBoundary.TRUNCATED,
                    text="Previous 12",
                    y0=920.0,
                    y1=950.0,
                ),
            ),
            (
                13,
                create_text_block(
                    boundary=ItemBoundary.TRUNCATED,
                    text="Previous 13",
                    y0=930.0,
                    y1=960.0,
                ),
            ),
        ]
        next_items = [
            create_text_block(
                boundary=ItemBoundary.RESUMED,
                text="Next 0",
                y0=10.0,
                y1=20.0,
            ),
            create_text_block(
                boundary=ItemBoundary.COMPLETE,
                text="Next 1",
                y0=30.0,
                y1=40.0,
            ),
            create_text_block(
                boundary=ItemBoundary.COMPLETE,
                text="Next 2",
                y0=50.0,
                y1=60.0,
            ),
        ]
        next_page_ir = create_page_ir(
            image_height=1000,
            items=next_items,
        )
        ordered_next_candidates = [
            (0, next_items[0]),
            (1, next_items[1]),
            (2, next_items[2]),
        ]
        mock_ordered_next_candidates.side_effect = [
            ordered_next_candidates,
            ordered_next_candidates,
            ordered_next_candidates,
            ordered_next_candidates,
            ordered_next_candidates,
        ]

        pair_specs, primary_indices = verify_page_pairs._generate_candidate_pairs(
            config=config, next_page_ir=next_page_ir, prev_candidates=prev_candidates
        )

        assert primary_indices == {
            "next_item_index": 0,
            "prev_item_index": 10,
        }
        assert len(pair_specs) == 9
        assert [spec.prev_index for spec in pair_specs] == [
            10,
            10,
            10,
            11,
            11,
            11,
            12,
            12,
            12,
        ]
        assert [spec.next_index for spec in pair_specs] == [0, 1, 2, 0, 1, 2, 0, 1, 2]
        assert mock_ordered_next_candidates.call_count == 4


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
            "kgfeg.page_ir_verification.verify_page_pairs._pick_topmost"
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


@patch("kgfeg.page_ir_verification.verify_page_pairs._is_viable_nonfigure_block_anchor")
@patch("kgfeg.page_ir_verification.verify_page_pairs._is_figure_block")
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


class TestPickTopmost:
    """Tests for the _pick_topmost selection logic."""

    def test_falls_back_to_absolute_top_item_when_no_family_or_viable_block_exists(
        self,
    ) -> None:
        """Test that the absolute top item is returned when no preferred branch matches.

        This covers the final fallback where the candidate list contains no same-family
        match for the previous anchor and no viable non-figure block candidate.
        """

        heading_item = create_text_block(
            block_type=BlockType.HEADING,
            boundary=ItemBoundary.COMPLETE,
            text="Heading",
            y0=5.0,
            y1=15.0,
        )
        figure_item = create_figure_block(
            alt_text="Diagram",
            block_type=BlockType.FIGURE,
            boundary=ItemBoundary.COMPLETE,
            y0=20.0,
            y1=40.0,
        )
        prev_item = create_table(boundary=ItemBoundary.TRUNCATED, y0=900.0, y1=980.0)
        result = verify_page_pairs._pick_topmost(
            candidates=[(0, heading_item), (1, figure_item)], prev_item=prev_item
        )
        assert result == (0, heading_item)

    def test_prefers_resumed_same_family_candidate_over_earlier_cross_family_item(
        self,
    ) -> None:
        """Test that a preferred same-family match beats an earlier cross-family item.

        The candidate list is already sorted by y0, so the first item is earlier on the
        page. `_pick_topmost` should still choose the resumed same-family candidate
        once preferred items are searched before the remaining pool.
        """

        earlier_table = create_table(boundary=ItemBoundary.COMPLETE, y0=5.0, y1=20.0)
        resumed_paragraph = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.RESUMED,
            text="Continuation paragraph",
            y0=25.0,
            y1=45.0,
        )
        prev_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Previous paragraph",
            y0=900.0,
            y1=980.0,
        )
        result = verify_page_pairs._pick_topmost(
            candidates=[(0, earlier_table), (1, resumed_paragraph)], prev_item=prev_item
        )
        assert result == (1, resumed_paragraph)

    def test_skips_heading_same_family_candidate_for_block_anchor_and_uses_viable_block(
        self,
    ) -> None:
        """Test that heading blocks do not satisfy block-anchor same-family matching.

        For block anchors, `_pick_topmost` requires the chosen same-family candidate to
        be a viable non-figure block, so a heading should be skipped in favor of a
        later paragraph.
        """

        heading_item = create_text_block(
            block_type=BlockType.HEADING,
            boundary=ItemBoundary.RESUMED,
            text="Section heading",
            y0=5.0,
            y1=15.0,
        )
        paragraph_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Body continuation",
            y0=20.0,
            y1=40.0,
        )
        prev_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Previous paragraph",
            y0=900.0,
            y1=980.0,
        )

        result = verify_page_pairs._pick_topmost(
            candidates=[(0, heading_item), (1, paragraph_item)], prev_item=prev_item
        )
        assert result == (1, paragraph_item)

    def test_uses_first_viable_nonfigure_block_when_no_same_family_match_exists(
        self,
    ) -> None:
        """Test that the generic viable-block fallback is used when families do not
        match.
        """

        figure_item = create_figure_block(
            alt_text="Diagram",
            block_type=BlockType.FIGURE,
            boundary=ItemBoundary.COMPLETE,
            y0=5.0,
            y1=20.0,
        )
        paragraph_item = create_text_block(
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            text="Fallback paragraph",
            y0=25.0,
            y1=45.0,
        )
        prev_item = create_table(boundary=ItemBoundary.TRUNCATED, y0=900.0, y1=980.0)
        result = verify_page_pairs._pick_topmost(
            candidates=[(0, figure_item), (1, paragraph_item)], prev_item=prev_item
        )
        assert result == (1, paragraph_item)


def test__bottom_continuity_candidates_honors_visible_y_min_before_filling_output() -> (
    None
):
    """Test that `visible_y_min` restricts both the primary pick and extra candidates."""

    hidden_bottom = create_table(boundary=ItemBoundary.TRUNCATED, y0=100.0, y1=140.0)
    visible_table = create_table(boundary=ItemBoundary.COMPLETE, y0=780.0, y1=860.0)
    visible_block = create_text_block(
        block_type=BlockType.PARAGRAPH,
        boundary=ItemBoundary.COMPLETE,
        text="Visible paragraph",
        y0=720.0,
        y1=770.0,
    )
    result = verify_page_pairs._bottom_continuity_candidates(
        image_height=1000.0,
        items=[hidden_bottom, visible_block, visible_table],
        k=3,
        visible_y_min=700.0,
    )
    assert [index for index, _ in result] == [2, 1]


def test__bottom_continuity_candidates_raises_for_invalid_k() -> None:
    """Test that ``bottom_continuity_candidates()`` rejects `k < 1`."""

    item = create_text_block(text="Body", y0=10.0, y1=20.0)

    with pytest.raises(ValueError, match="k must be >= 1"):
        verify_page_pairs._bottom_continuity_candidates(
            image_height=1000.0, items=[item], k=0, visible_y_min=None
        )


def test__bottom_continuity_candidates_raises_when_crop_removes_all_candidates() -> (
    None
):
    """Test that cropping can eliminate every candidate and trigger the empty-pool error."""

    low_item = create_text_block(text="Low", y0=10.0, y1=20.0)

    with pytest.raises(ValueError, match="No non-artifact items found."):
        verify_page_pairs._bottom_continuity_candidates(
            image_height=1000.0,
            items=[low_item],
            k=3,
            visible_y_min=900.0,
        )


def test__bottom_continuity_candidates_returns_primary_then_non_heading_extras() -> (
    None
):
    """Test that extras preserve near-bottom order while skipping heading anchors."""

    primary_table = create_table(boundary=ItemBoundary.TRUNCATED, y0=880.0, y1=980.0)
    heading_block = create_text_block(
        block_type=BlockType.HEADING,
        boundary=ItemBoundary.COMPLETE,
        text="Section 2",
        y0=820.0,
        y1=870.0,
    )
    body_block = create_text_block(
        block_type=BlockType.PARAGRAPH,
        boundary=ItemBoundary.COMPLETE,
        text="Body continuation",
        y0=760.0,
        y1=810.0,
    )
    second_table = create_table(boundary=ItemBoundary.COMPLETE, y0=700.0, y1=750.0)
    result = verify_page_pairs._bottom_continuity_candidates(
        image_height=1000.0,
        items=[second_table, body_block, heading_block, primary_table],
        k=3,
        visible_y_min=None,
    )
    assert [index for index, _ in result] == [3, 1, 0]


def test__bottom_continuity_candidates_sorts_by_bottom_edge_before_primary_pick() -> (
    None
):
    """Test that the primary picker receives candidates sorted by descending `y1`."""

    lower_item = create_text_block(text="Lower", y0=100.0, y1=150.0)
    highest_item = create_text_block(text="Highest", y0=300.0, y1=950.0)
    middle_item = create_table(y0=200.0, y1=600.0)

    with patch(
        "kgfeg.page_ir_verification.verify_page_pairs._pick_bottommost"
    ) as mock_pick_bottommost:
        mock_pick_bottommost.return_value = (1, highest_item)
        result = verify_page_pairs._bottom_continuity_candidates(
            image_height=1000.0,
            items=[lower_item, highest_item, middle_item],
            k=1,
            visible_y_min=None,
        )

    sorted_candidates = mock_pick_bottommost.call_args.kwargs.get("candidates")

    if sorted_candidates is None:
        sorted_candidates = mock_pick_bottommost.call_args.args[0]

    assert [index for index, _ in sorted_candidates] == [1, 2, 0]
    assert result == [(1, highest_item)]


def test__truncate_text_appends_ellipsis_when_text_exceeds_limit() -> None:
    """Append an ellipsis when the normalized text is longer than the limit."""

    assert (
        verify_page_pairs._truncate_text(max_chars=8, text="abcdefghijk") == "abcde..."
    )


def test__truncate_text_keeps_text_shorter_than_limit() -> None:
    """Return the original text when it already fits within the limit."""

    assert verify_page_pairs._truncate_text(max_chars=12, text="hello") == "hello"


def test__truncate_text_replaces_newlines_and_strips_surrounding_whitespace() -> None:
    """Normalize newlines to spaces and trim leading and trailing whitespace."""

    assert (
        verify_page_pairs._truncate_text(
            max_chars=50, text="  First line\nsecond line\n third line  "
        )
        == "First line second line  third line"
    )


def test__truncate_text_returns_ellipsis_only_when_limit_is_less_than_three() -> None:
    """Return only an ellipsis when truncation is required below three characters."""

    assert verify_page_pairs._truncate_text(max_chars=2, text="abcdef") == "..."


def test_builds_record_and_writes_report_from_selected_attempt() -> None:
    """Test that the function wires helper outputs into the final record and report.

    This covers the successful orchestration path: previous-page candidates are chosen,
    candidate pairs are generated, verification attempts are executed, page indices are
    stamped onto the selected verdict, and the JSON report payload is written with the
    expected summary fields.
    """

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.95,
        model="test-model",
    )
    next_item = create_text_block(
        boundary=ItemBoundary.RESUMED, text="Continuation paragraph", y0=12.0, y1=40.0
    )
    prev_item = create_text_block(
        boundary=ItemBoundary.TRUNCATED, text="Previous paragraph", y0=920.0, y1=980.0
    )
    page_irs = {
        4: create_page_ir_like(image_height=1000.0, items=[prev_item]),
        5: create_page_ir_like(image_height=1100.0, items=[next_item]),
    }
    primary_indices = {"next_item_index": 7, "prev_item_index": 3}
    selected_verdict = VerdictStub(
        confidence=0.93,
        continuation_kind_value="text_continuation",
        is_continuation=True,
        set_next_table_repeats_header=None,
    )
    execute_result = {
        "attempt_summaries": [{"attempt_no": 0, "is_continuation": True}],
        "early_stop_reason": "patchable_positive",
        "selected_eligible_for_patch": True,
        "selected_next_index": 7,
        "selected_prev_index": 3,
        "selected_verdict": selected_verdict,
    }
    page_images_dir = Path("/tmp/pages")
    usage_tracker = MagicMock()
    verification_dirs = create_verification_dirs(root_dir=Path("/tmp/verification"))

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._bottom_continuity_candidates",
            return_value=[(3, prev_item)],
        ) as mock_bottom_candidates,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._execute_verification_attempts",
            return_value=execute_result,
        ) as mock_execute_attempts,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._generate_candidate_pairs",
            return_value=([MagicMock()], primary_indices),
        ) as mock_generate_pairs,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.write_to_json"
        ) as mock_write,
    ):
        record = verify_page_pairs.verify_single_page_pair(
            config=config,
            page_images_dir=page_images_dir,
            page_index=4,
            page_irs=page_irs,
            usage_tracker=usage_tracker,
            verification_dirs=verification_dirs,
        )

    mock_bottom_candidates.assert_called_once_with(
        image_height=1000.0, items=[prev_item]
    )
    mock_generate_pairs.assert_called_once_with(
        config=config, next_page_ir=page_irs[5], prev_candidates=[(3, prev_item)]
    )
    mock_execute_attempts.assert_called_once_with(
        config=config,
        next_page_image_fp=page_images_dir / "0005.png",
        page_index=4,
        pair_crop_dir=verification_dirs.page_irs_pair_crops,
        pairs=[mock_generate_pairs.return_value[0][0]],
        usage_tracker=usage_tracker,
    )
    mock_write.assert_called_once()

    assert selected_verdict.next_page_index == 5
    assert selected_verdict.prev_page_index == 4
    assert record is not None
    assert record.next_item_index == 7
    assert record.next_page_index == 5
    assert record.prev_item_index == 3
    assert record.prev_page_index == 4
    assert record.verdict is selected_verdict

    write_kwargs = mock_write.call_args.kwargs
    assert write_kwargs["fp"] == (
        verification_dirs.page_irs_pair_reports / "0004_0005.json"
    )
    assert write_kwargs["json_info"] == {
        "attempts": [{"attempt_no": 0, "is_continuation": True}],
        "primary_candidate_selection": {"next_item_index": 7, "prev_item_index": 3},
        "selected_candidate_selection": {
            "eligible_for_patch": True,
            "next_item_index": 7,
            "prev_item_index": 3,
        },
        "selection_policy": {
            "early_stop_reason": "patchable_positive",
            "min_confidence_to_patch": 0.8,
            "min_confidence_to_select_positive": 0.9,
            "min_confidence_to_stop_negative_search": 0.95,
        },
        "verdict": {
            "confidence": 0.93,
            "continuation_kind": "text_continuation",
            "is_continuation": True,
            "next_page_index": 5,
            "prev_page_index": 4,
            "set_next_table_repeats_header": None,
        },
    }


def test_clamps_saved_crop_height_to_page_height_when_requested_crop_exceeds_image(
    tmp_path: Path,
) -> None:
    """Test that oversized requested crops save the full page height, not a taller
    image.

    The helper keeps the rounded, requested crop height in the cache key and filename,
    but the actual raster crop must clamp to the real page height.

    Parameters
    ----------
    tmp_path
        The temporary directory provided by pytest for storing test files.
    """

    crop_cache: dict[tuple[int, int], Path] = {}
    next_page_image_fp = tmp_path / "next_page.png"
    output_dir = tmp_path / "pair_crops"
    spec = create_candidate_pair_spec(crop_y_max=120.6, next_index=2, prev_index=0)
    create_image(fp=next_page_image_fp, size=(60, 80))

    crop_fp = verify_page_pairs._ensure_pair_specific_crop(
        crop_cache=crop_cache,
        next_page_image_fp=next_page_image_fp,
        next_page_index=7,
        output_dir=output_dir,
        spec=spec,
    )

    assert crop_cache == {(2, 121): crop_fp}
    assert crop_fp.name == "0007_top_to_item_002_ymax_00121.png"

    with Image.open(crop_fp) as cropped_image:
        assert cropped_image.size == (60, 80)


def test_enforces_minimum_one_pixel_crop_when_requested_crop_rounds_to_zero(
    tmp_path: Path,
) -> None:
    """Test that tiny requested crops still produce a non-empty, 1-pixel-tall image.

    This guards the lower clamp that prevents invalid zero-height image crops.

    Parameters
    ----------
    tmp_path
        The temporary directory provided by pytest for storing test files.
    """

    crop_cache: dict[tuple[int, int], Path] = {}
    next_page_image_fp = tmp_path / "next_page.png"
    output_dir = tmp_path / "pair_crops"
    spec = create_candidate_pair_spec(crop_y_max=0.4, next_index=1, prev_index=0)
    create_image(fp=next_page_image_fp, size=(50, 90))

    crop_fp = verify_page_pairs._ensure_pair_specific_crop(
        crop_cache=crop_cache,
        next_page_image_fp=next_page_image_fp,
        next_page_index=3,
        output_dir=output_dir,
        spec=spec,
    )

    assert crop_cache == {(1, 0): crop_fp}
    assert crop_fp.name == "0003_top_to_item_001_ymax_00000.png"

    with Image.open(crop_fp) as cropped_image:
        assert cropped_image.size == (50, 1)


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


def test_make_block_excerpt_builds_all_preview_sections_with_truncation_and_caps() -> (
    None
):
    """Test that block excerpts normalize and truncate text across all preview channels.

    This tests with a mixed payload containing body text, more than six list items, and
    figure metadata. The assertions verify that each preview path applies the correct
    truncation logic and that the list preview is capped.
    """

    item = {
        "block_type": BlockType.PARAGRAPH,
        "figure": {
            "alt_text": "ABCDEFGHIJKL",
            "caption": {"text": "Caption line"},
            "embedded_text": {"text": "Embedded words here"},
            "figure_kind": "diagram",
        },
        "kind": "block",
        "list_items": [
            create_list_item(marker="1.", text="A" * 200),
            create_list_item(marker="-", text="Second item"),
            "third item",
            create_list_item(marker="4.", text="Fourth item"),
            create_list_item(marker="5.", text="Fifth item"),
            create_list_item(marker="6.", text="Sixth item"),
            create_list_item(marker="7.", text="Seventh item should be dropped"),
        ],
        "text": {"language": "en", "text": "Line 1\nLine 2\nLine 3"},
    }

    excerpt = verify_page_pairs._make_block_excerpt(
        bbox=[0.0, 10.0, 100.0, 80.0], item=item, local_code="B-01", max_text_chars=10
    )

    assert excerpt["kind"] == "block"
    assert excerpt["bbox"] == [0.0, 10.0, 100.0, 80.0]
    assert excerpt["local_code"] == "B-01"
    assert excerpt["block_type"] == BlockType.PARAGRAPH
    assert excerpt["text_preview"] == "Line 1..."
    assert excerpt["figure_preview"] == {
        "alt_text": "ABCDEFG...",
        "caption": "Caption...",
        "embedded_text": "Embedde...",
        "kind": "diagram",
    }
    assert len(excerpt["list_preview"]) == 6
    assert excerpt["list_preview"][0].startswith("1. ")
    assert excerpt["list_preview"][0].endswith("...")
    assert excerpt["list_preview"][1] == "- Second item"
    assert excerpt["list_preview"][2] == "third item"
    assert excerpt["list_preview"][5] == "6. Sixth item"


def test_make_block_excerpt_omits_empty_or_malformed_preview_sections() -> None:
    """Test that block excerpts do not emit empty preview keys for blank inputs.

    This tests the scenario where text is blank, the list payload is empty, and figure
    subfields are missing or malformed. The helper should return only the required
    structural fields.
    """

    item = {
        "block_type": BlockType.PARAGRAPH,
        "figure": {"caption": "not-a-dict", "embedded_text": {"not_text": "ignored"}},
        "kind": "block",
        "list_items": [],
        "text": {"language": "en", "text": "  \n  "},
    }

    excerpt = verify_page_pairs._make_block_excerpt(
        bbox=[5.0, 15.0, 95.0, 45.0], item=item, local_code=None, max_text_chars=20
    )
    assert excerpt == {
        "bbox": [5.0, 15.0, 95.0, 45.0],
        "block_type": BlockType.PARAGRAPH,
        "kind": "block",
        "local_code": None,
    }


def test_make_table_excerpt_handles_malformed_cells_and_overstated_header_counts() -> (
    None
):
    """Test that table excerpts normalize odd cell payloads without crashing.

    This targets two brittle edges: malformed cell `text` payloads and a
    `header_row_count` that exceeds the available rows. The helper should keep the
    declared header count, preview only the rows that exist, and normalize odd cell
    shapes into safe strings.
    """

    item = {
        "header_row_count": 5,
        "n_cols": 3,
        "rows": [
            create_row(
                cells=[
                    create_text_cell(text={"text": "ok"}),
                    create_text_cell(text="wrong-shape"),
                    123,
                ],
            ),
            create_row(cells=[create_text_cell(text={"text": "still header"}), None]),
        ],
    }
    excerpt = verify_page_pairs._make_table_excerpt(
        bbox=[2.0, 4.0, 50.0, 40.0],
        item=item,
        local_code=None,
        max_cell_chars=20,
        preview_rows=2,
    )
    assert excerpt == {
        "bbox": [2.0, 4.0, 50.0, 40.0],
        "bottom_rows_preview": [],
        "header_preview": [["ok", "", "123"], ["still header", "None"]],
        "header_row_count": 5,
        "kind": "table",
        "local_code": None,
        "n_cols": 3,
        "row_count": 2,
        "top_rows_preview": [],
    }


def test_make_table_excerpt_splits_top_and_bottom_body_previews_without_overlap() -> (
    None
):
    """Test that table excerpts window long bodies without duplicating overlap rows.

    This tests the deduplication branch where the top and bottom body slices would
    otherwise overlap. The helper should keep the header rows, the top body window, and
    only the non-overlapping tail rows.
    """

    item = {
        "header_row_count": 1,
        "n_cols": 2,
        "rows": [
            create_row(cells=[create_text_cell(text={"text": "abcdefghi"}), "H2"]),
            create_row(cells=[create_text_cell(text={"text": "row1-long"}), "B1"]),
            create_row(cells=[create_text_cell(text={"text": "row2-long"}), "B2"]),
            create_row(cells=[create_text_cell(text={"text": "row3-long"}), "B3"]),
            create_row(cells=[create_text_cell(text={"text": "row4-long"}), "B4"]),
            create_row(cells=[create_text_cell(text={"text": "row5-long"}), "B5"]),
        ],
    }
    excerpt = verify_page_pairs._make_table_excerpt(
        bbox=[0.0, 0.0, 100.0, 120.0],
        item=item,
        local_code="T-01",
        max_cell_chars=8,
        preview_rows=3,
    )

    assert excerpt == {
        "bbox": [0.0, 0.0, 100.0, 120.0],
        "bottom_rows_preview": [["row4-...", "B4"], ["row5-...", "B5"]],
        "header_preview": [["abcde...", "H2"]],
        "header_row_count": 1,
        "kind": "table",
        "local_code": "T-01",
        "n_cols": 2,
        "row_count": 6,
        "top_rows_preview": [
            ["row1-...", "B1"],
            ["row2-...", "B2"],
            ["row3-...", "B3"],
        ],
    }


def test_raises_runtime_error_when_all_attempts_fail() -> None:
    """Test that the function raises when every verification attempt errors out.

    The raised error should mention the page pair and preserve the collected exception
    messages from all failed attempts.
    """

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.95,
        model="test-model",
        next_page_crop_padding_px=25.0,
    )
    pair_a = create_candidate_pair_spec(
        crop_y_max=10.0,
        next_index=1,
        next_rank=0,
        prev_index=0,
        prev_rank=0,
    )
    pair_b = create_candidate_pair_spec(
        crop_y_max=20.0,
        next_index=2,
        next_rank=1,
        prev_index=3,
        prev_rank=1,
    )

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._ensure_pair_specific_crop",
            side_effect=[Path("/tmp/crops/a.png"), Path("/tmp/crops/b.png")],
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._make_verification_excerpt",
            return_value={"excerpt": True},
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._strip_continuity_hints",
            side_effect=lambda item_json: item_json,
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.verify_page_ir_pairs",
            side_effect=[ValueError("bad-a"), RuntimeError("bad-b")],
        ),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            verify_page_pairs._execute_verification_attempts(
                config=config,
                next_page_image_fp=Path("/tmp/pages/0001.png"),
                page_index=5,
                pair_crop_dir=Path("/tmp/crops"),
                pairs=[pair_a, pair_b],
                usage_tracker=MagicMock(),
            )

    message = str(exc_info.value)
    assert "All 2 verification attempts failed" in message
    assert "page pair 6->7" in message
    assert "bad-a" in message
    assert "bad-b" in message


def test_raises_value_error_when_either_required_page_ir_is_missing() -> None:
    """Test that a missing page IR for either side of the pair raises `ValueError`."""

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.95,
        model="test-model",
    )
    usage_tracker = MagicMock()
    verification_dirs = create_verification_dirs(root_dir=Path("/tmp/verification"))

    with pytest.raises(ValueError, match="Missing page IR for page index 3 or 4"):
        verify_page_pairs.verify_single_page_pair(
            config=config,
            page_images_dir=Path("/tmp/pages"),
            page_index=3,
            page_irs={3: create_page_ir_like(image_height=1000.0, items=[])},
            usage_tracker=usage_tracker,
            verification_dirs=verification_dirs,
        )


def test_records_errors_then_selects_the_later_successful_attempt() -> None:
    """Test that failed attempts are summarized and later successes remain selectable.

    This covers the mixed outcome path where one verification call raises and a later
    call succeeds. The function should preserve the error summary, continue iterating,
    and return the successful candidate selection.
    """

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.95,
        model="test-model",
        next_page_crop_padding_px=25.0,
    )
    crop_fp_a = Path("/tmp/crops/a.png")
    crop_fp_b = Path("/tmp/crops/b.png")
    pair_a = create_candidate_pair_spec(
        crop_y_max=40.0,
        next_index=10,
        next_rank=0,
        prev_index=1,
        prev_rank=0,
    )
    pair_b = create_candidate_pair_spec(
        crop_y_max=80.0,
        next_index=11,
        next_rank=1,
        prev_index=2,
        prev_rank=1,
    )
    usage_tracker = MagicMock()
    verdict = create_verdict(
        confidence=0.77, continuation_kind_value="none", is_continuation=False
    )

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._ensure_pair_specific_crop",
            side_effect=[crop_fp_a, crop_fp_b],
        ) as mock_ensure_crop,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._make_verification_excerpt",
            return_value={"excerpt": True},
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._strip_continuity_hints",
            side_effect=lambda item_json: item_json,
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.verify_page_ir_pairs",
            side_effect=[RuntimeError("boom"), verdict],
        ) as mock_verify,
    ):
        result = verify_page_pairs._execute_verification_attempts(
            config=config,
            next_page_image_fp=Path("/tmp/pages/0001.png"),
            page_index=0,
            pair_crop_dir=Path("/tmp/crops"),
            pairs=[pair_a, pair_b],
            usage_tracker=usage_tracker,
        )

    assert mock_ensure_crop.call_count == 2
    assert mock_verify.call_count == 2
    assert result["early_stop_reason"] is None
    assert result["selected_eligible_for_patch"] is False
    assert result["selected_next_index"] == 11
    assert result["selected_prev_index"] == 2
    assert result["selected_verdict"] is verdict
    assert result["attempt_summaries"] == [
        {
            "attempt_no": 0,
            "crop_y_max": 40.0,
            "error": "boom",
            "next_item_index": 10,
            "next_rank": 0,
            "prev_item_index": 1,
            "prev_rank": 0,
        },
        {
            "attempt_no": 1,
            "confidence": 0.77,
            "continuation_kind": "none",
            "crop_png_fp": str(crop_fp_b),
            "crop_y_max": 80.0,
            "eligible_for_patch": False,
            "is_continuation": False,
            "next_item_index": 11,
            "next_rank": 1,
            "prev_item_index": 2,
            "prev_rank": 1,
            "set_next_table_repeats_header": None,
        },
    ]


def test_removes_boundary_but_preserves_non_table_repeats_header_field() -> None:
    """Test that non-table items keep `repeats_header` because the function guards on
    kind.

    This validates that the helper does not blindly drop `repeats_header` from all item
    payloads. The current behavior is intentionally table-specific.
    """

    item_json = create_block_item_json(repeats_header=False)
    cleaned_item_json = verify_page_pairs._strip_continuity_hints(item_json=item_json)

    assert cleaned_item_json == {
        "bbox": [0.0, 10.0, 100.0, 40.0],
        "kind": "block",
        "meta": {"tags": ["body", "candidate"]},
        "repeats_header": False,
        "text": {"language": "en", "text": "Body paragraph"},
    }
    assert item_json["boundary"] == "truncated"
    assert item_json["repeats_header"] is False


def test_removes_table_continuity_hints_without_mutating_input() -> None:
    """Test that table-specific continuity hints are stripped from a deep-copied
    payload.

    This covers the actual table path: both `boundary` and `repeats_header` should be
    removed, while nested payload content should be preserved and detached from the
    original input object.
    """

    item_json = create_table_item_json()
    cleaned_item_json = verify_page_pairs._strip_continuity_hints(item_json=item_json)
    cleaned_item_json["meta"]["source"]["page"] = 99

    assert cleaned_item_json == {
        "bbox": [0.0, 10.0, 100.0, 80.0],
        "cells": [
            {
                "bbox": [0.0, 10.0, 50.0, 20.0],
                "col_span": 1,
                "column": 0,
                "is_header": True,
                "row": 0,
                "row_span": 1,
                "text": {"language": "en", "text": "Header"},
            }
        ],
        "kind": "table",
        "meta": {"source": {"page": 99}},
    }
    assert item_json == {
        "bbox": [0.0, 10.0, 100.0, 80.0],
        "boundary": "resumed",
        "cells": [
            {
                "bbox": [0.0, 10.0, 50.0, 20.0],
                "col_span": 1,
                "column": 0,
                "is_header": True,
                "row": 0,
                "row_span": 1,
                "text": {"language": "en", "text": "Header"},
            }
        ],
        "kind": "table",
        "meta": {"source": {"page": 4}},
        "repeats_header": True,
    }


def test_returns_none_and_skips_downstream_work_when_either_page_has_no_items() -> None:
    """Test that empty page items short-circuit verification and return `None`.

    The function should not attempt candidate selection, verification, or report
    writing when either page in the boundary pair has an empty item list.
    """

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.95,
        model="test-model",
    )
    page_irs = {
        0: create_page_ir_like(image_height=1000.0, items=[]),
        1: create_page_ir_like(
            image_height=1000.0,
            items=[
                create_text_block(
                    boundary=ItemBoundary.COMPLETE,
                    text="Next page paragraph",
                    y0=10.0,
                    y1=30.0,
                )
            ],
        ),
    }
    usage_tracker = MagicMock()
    verification_dirs = create_verification_dirs(root_dir=Path("/tmp/verification"))

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._bottom_continuity_candidates"
        ) as mock_bottom_candidates,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._execute_verification_attempts"
        ) as mock_execute_attempts,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._generate_candidate_pairs"
        ) as mock_generate_pairs,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.write_to_json"
        ) as mock_write,
    ):
        result = verify_page_pairs.verify_single_page_pair(
            config=config,
            page_images_dir=Path("/tmp/pages"),
            page_index=0,
            page_irs=page_irs,
            usage_tracker=usage_tracker,
            verification_dirs=verification_dirs,
        )

    assert result is None
    mock_bottom_candidates.assert_not_called()
    mock_execute_attempts.assert_not_called()
    mock_generate_pairs.assert_not_called()
    mock_write.assert_not_called()


def test_reuses_cached_crop_for_same_next_index_and_same_rounded_crop_height(
    tmp_path: Path,
) -> None:
    """Test that cache reuse depends only on next index and rounded crop height.

    Two pair specs with different previous anchors but the same next-page target and
    the same rounded `crop_y_max` should resolve to the same on-disk crop without
    reopening or rewriting the image.

    Parameters
    ----------
    tmp_path
        The temporary directory provided by pytest for storing test files.
    """

    crop_cache: dict[tuple[int, int], Path] = {}
    next_page_image_fp = tmp_path / "next_page.png"
    output_dir = tmp_path / "pair_crops"
    spec_a = create_candidate_pair_spec(crop_y_max=20.4, next_index=4, prev_index=0)
    spec_b = create_candidate_pair_spec(crop_y_max=20.49, next_index=4, prev_index=9)
    create_image(fp=next_page_image_fp, size=(40, 100))

    first_crop_fp = verify_page_pairs._ensure_pair_specific_crop(
        crop_cache=crop_cache,
        next_page_image_fp=next_page_image_fp,
        next_page_index=5,
        output_dir=output_dir,
        spec=spec_a,
    )

    with Image.open(first_crop_fp) as cropped_image:
        assert cropped_image.size == (40, 20)

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.Image.open", autospec=True
        ) as mock_image_open,
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.make_dir", autospec=True
        ) as mock_make_dir,
    ):
        second_crop_fp = verify_page_pairs._ensure_pair_specific_crop(
            crop_cache=crop_cache,
            next_page_image_fp=next_page_image_fp,
            next_page_index=5,
            output_dir=output_dir,
            spec=spec_b,
        )

    assert first_crop_fp == second_crop_fp
    assert crop_cache == {(4, 20): first_crop_fp}
    mock_image_open.assert_not_called()
    mock_make_dir.assert_not_called()


def test_stops_early_for_primary_primary_patchable_positive() -> None:
    """Test that a patchable primary-primary positive stops further verification.

    When the first pair is rank-0 on both sides and produces a patch-eligible positive,
    the function should record the early-stop reason and skip later candidate pairs.
    """

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.95,
        model="test-model",
    )
    pair_a = create_candidate_pair_spec(
        crop_y_max=50.0,
        next_boundary=ItemBoundary.RESUMED,
        next_index=3,
        next_rank=0,
        prev_boundary=ItemBoundary.TRUNCATED,
        prev_index=4,
        prev_rank=0,
    )
    pair_b = create_candidate_pair_spec(
        crop_y_max=90.0,
        next_index=8,
        next_rank=1,
        prev_index=9,
        prev_rank=1,
    )
    verdict = create_verdict(
        confidence=0.92,
        continuation_kind_value="continuous_text",
        is_continuation=True,
        set_next_table_repeats_header=True,
    )

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._ensure_pair_specific_crop",
            return_value=Path("/tmp/crops/first.png"),
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._make_verification_excerpt",
            return_value={"excerpt": True},
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._strip_continuity_hints",
            side_effect=lambda item_json: item_json,
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.verify_page_ir_pairs",
            return_value=verdict,
        ) as mock_verify,
    ):
        result = verify_page_pairs._execute_verification_attempts(
            config=config,
            next_page_image_fp=Path("/tmp/pages/0001.png"),
            page_index=0,
            pair_crop_dir=Path("/tmp/crops"),
            pairs=[pair_a, pair_b],
            usage_tracker=MagicMock(),
        )

    assert mock_verify.call_count == 1
    assert result["early_stop_reason"] == "primary_primary_patchable_positive"
    assert result["selected_eligible_for_patch"] is True
    assert result["selected_next_index"] == 3
    assert result["selected_prev_index"] == 4
    assert result["attempt_summaries"] == [
        {
            "attempt_no": 0,
            "confidence": 0.92,
            "continuation_kind": "continuous_text",
            "crop_png_fp": "/tmp/crops/first.png",
            "crop_y_max": 50.0,
            "early_stop_reason": "primary_primary_patchable_positive",
            "eligible_for_patch": True,
            "is_continuation": True,
            "next_item_index": 3,
            "next_rank": 0,
            "prev_item_index": 4,
            "prev_rank": 0,
            "set_next_table_repeats_header": True,
        }
    ]


def test_stops_early_for_primary_primary_same_family_high_confidence_negative() -> None:
    """Test that a strong same-family negative stops fallback search early.

    This covers the negative early-stop branch, which requires both candidates to be
    primary rank, the verdict to be negative above the configured threshold, and the
    pair to share a candidate family.
    """

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.95,
        model="test-model",
    )
    pair_a = create_candidate_pair_spec(
        crop_y_max=55.0,
        next_boundary=ItemBoundary.RESUMED,
        next_index=6,
        next_rank=0,
        prev_boundary=ItemBoundary.TRUNCATED,
        prev_index=7,
        prev_rank=0,
    )
    pair_b = create_candidate_pair_spec(
        crop_y_max=95.0,
        next_index=12,
        next_rank=1,
        prev_index=13,
        prev_rank=1,
    )
    verdict = create_verdict(
        confidence=0.96, continuation_kind_value="none", is_continuation=False
    )

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._ensure_pair_specific_crop",
            return_value=Path("/tmp/crops/negative.png"),
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._make_verification_excerpt",
            return_value={"excerpt": True},
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._strip_continuity_hints",
            side_effect=lambda item_json: item_json,
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.verify_page_ir_pairs",
            return_value=verdict,
        ) as mock_verify,
    ):
        result = verify_page_pairs._execute_verification_attempts(
            config=config,
            next_page_image_fp=Path("/tmp/pages/0001.png"),
            page_index=0,
            pair_crop_dir=Path("/tmp/crops"),
            pairs=[pair_a, pair_b],
            usage_tracker=MagicMock(),
        )

    assert mock_verify.call_count == 1
    assert (
        result["early_stop_reason"]
        == "primary_primary_same_family_high_confidence_negative"
    )
    assert result["selected_eligible_for_patch"] is False
    assert result["selected_next_index"] == 6
    assert result["selected_prev_index"] == 7
    assert result["attempt_summaries"] == [
        {
            "attempt_no": 0,
            "confidence": 0.96,
            "continuation_kind": "none",
            "crop_png_fp": "/tmp/crops/negative.png",
            "crop_y_max": 55.0,
            "early_stop_reason": "primary_primary_same_family_high_confidence_negative",
            "eligible_for_patch": False,
            "is_continuation": False,
            "next_item_index": 6,
            "next_rank": 0,
            "prev_item_index": 7,
            "prev_rank": 0,
            "set_next_table_repeats_header": None,
        }
    ]


def test_table_row_preview_returns_empty_list_for_missing_cells() -> None:
    """Return an empty preview when the row has no cells key."""

    row: dict[str, Any] = {}
    assert not verify_page_pairs._table_row_preview(max_cell_chars=20, row=row)


def test_table_row_preview_returns_empty_strings_for_dict_cells_without_text_dict() -> (
    None
):
    """Use empty strings when dict cells do not expose a nested text payload."""

    row = create_row(cells=[{}, {"text": None}, {"text": "plain string"}])
    assert verify_page_pairs._table_row_preview(max_cell_chars=20, row=row) == [
        "",
        "",
        "",
    ]


def test_table_row_preview_stringifies_non_dict_cells_before_truncating() -> None:
    """Convert non-dict cells to strings before applying truncation."""

    row = create_row(cells=[12345, True, ["abc", "def"]])
    assert verify_page_pairs._table_row_preview(max_cell_chars=8, row=row) == [
        "12345",
        "True",
        "['abc...",
    ]


def test_table_row_preview_truncates_dict_cell_text_and_preserves_cell_order() -> None:
    """Preview dict-backed cells in order and truncate each cell independently."""

    row = create_row(
        cells=[
            {"text": {"text": "Alpha Beta"}},
            {"text": {"text": "Gamma Delta Epsilon"}},
            {"text": {"text": "Zeta"}},
        ]
    )
    assert verify_page_pairs._table_row_preview(max_cell_chars=10, row=row) == [
        "Alpha Beta",
        "Gamma D...",
        "Zeta",
    ]


def test_uses_pair_priority_key_to_select_the_best_successful_attempt() -> None:
    """Test that final selection delegates ordering to `_pair_priority_key()`.

    This covers the multi-success path without early stopping. The function should keep
    all successful attempts, call the priority helper, and return the attempt with the
    lowest ranking key.
    """

    config = VerificationConfigStub(
        min_confidence_to_patch=0.8,
        min_confidence_to_select_positive=0.9,
        min_confidence_to_stop_negative_search=0.99,
        model="test-model",
    )
    pair_a = create_candidate_pair_spec(
        crop_y_max=30.0,
        next_index=21,
        next_rank=1,
        prev_index=31,
        prev_rank=1,
    )
    pair_b = create_candidate_pair_spec(
        crop_y_max=60.0,
        next_index=22,
        next_rank=2,
        prev_index=32,
        prev_rank=2,
    )
    verdict_a = create_verdict(
        confidence=0.61, continuation_kind_value="none", is_continuation=False
    )
    verdict_b = create_verdict(
        confidence=0.85, continuation_kind_value="continuous_text", is_continuation=True
    )

    def priority_side_effect(
        *,
        attempt: verify_page_pairs.VerifiedCandidateAttempt,
        config: VerificationConfigStub,
    ) -> tuple[int, int]:
        """Return a deterministic key that favors the second attempt.

        Parameters
        ----------
        attempt
            The verification attempt for which to compute the priority key.
        config
            The verification configuration, which is not used in this side effect but
            is included to match the signature of the real function.
        """

        del config
        return (0, 1) if attempt.spec.next_index == 22 else (1, 0)

    with (
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._ensure_pair_specific_crop",
            side_effect=[Path("/tmp/crops/a.png"), Path("/tmp/crops/b.png")],
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._make_verification_excerpt",
            return_value={"excerpt": True},
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._strip_continuity_hints",
            side_effect=lambda item_json: item_json,
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs.verify_page_ir_pairs",
            side_effect=[verdict_a, verdict_b],
        ),
        patch(
            "kgfeg.page_ir_verification.verify_page_pairs._pair_priority_key",
            side_effect=priority_side_effect,
        ) as mock_priority,
    ):
        result = verify_page_pairs._execute_verification_attempts(
            config=config,
            next_page_image_fp=Path("/tmp/pages/0001.png"),
            page_index=0,
            pair_crop_dir=Path("/tmp/crops"),
            pairs=[pair_a, pair_b],
            usage_tracker=MagicMock(),
        )

    assert mock_priority.call_count == 2
    assert result["early_stop_reason"] is None
    assert result["selected_eligible_for_patch"] is True
    assert result["selected_next_index"] == 22
    assert result["selected_prev_index"] == 32
    assert result["selected_verdict"] is verdict_b
