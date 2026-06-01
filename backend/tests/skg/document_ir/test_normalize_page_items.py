"""This is the main module for testing document_ir/normalize_page_items.py."""

# Third Party Library
import pytest

# Package Library
from skg.document_ir import normalize_page_items
from skg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.utils.constants import BlockType, FigureKind, ItemBoundary

BBoxType = tuple[float, float, float, float]
ItemsType = list[tuple[int, Block | Table]]


def make_bbox(*, x0: float, x1: float, y0: float, y1: float) -> BBoxType:
    """Build a bbox tuple in the repository's expected order.

    Parameters
    ----------
    x0
        The left coordinate of the bounding box.
    x1
        The right coordinate of the bounding box.
    y0
        The top coordinate of the bounding box.
    y1
        The bottom coordinate of the bounding box.

    Returns
    -------
    BBoxType
        A bounding box tuple in the order (x0, y0, x1, y1).
    """

    return x0, y0, x1, y1


def make_block(
    *,
    bbox: BBoxType | None = None,
    block_type: BlockType,
    local_code: str | None = None,
    text: str | None = None,
) -> Block:
    """Build a minimal valid `Block` for the requested block type.

    Parameters
    ----------
    bbox
        The bounding box for the block. If `None`, a default bbox will be used.
    block_type
        The type of block to create (e.g., `BlockType.CAPTION`, `BlockType.FIGURE`).
    local_code
        An optional local code to assign to the block. If `None`, the block will have
        no local code.
    text
        The text content for the block. This is only used for non-figure blocks, as
        figure blocks will have a `FigureUnit` instead. If `None`, a default
        placeholder text will be used for non-figure blocks.

    Returns
    -------
    Block
        A `Block` instance with the specified properties, suitable for testing caption
        propagation and related logic.
    """

    bbox_value = bbox or make_bbox(x0=0.0, x1=100.0, y0=0.0, y1=20.0)

    if block_type == BlockType.FIGURE:
        return Block(
            bbox=bbox_value,
            block_type=block_type,
            boundary=ItemBoundary.COMPLETE,
            figure=make_figure_unit(alt_text="simple figure"),
            kind="block",
            list_items=None,
            local_code=local_code,
            text=None,
        )

    return Block(
        bbox=bbox_value,
        block_type=block_type,
        boundary=ItemBoundary.COMPLETE,
        figure=None,
        kind="block",
        list_items=None,
        local_code=local_code,
        text=make_text_unit(text=text or "placeholder text"),
    )


def make_figure_unit(alt_text: str) -> FigureUnit:
    """Build a minimal valid `FigureUnit` for figure-block tests.

    Parameters
    ----------
    alt_text
        The alt text to include in the figure unit. This is required to ensure the
        figure block is considered valid for caption propagation tests.

    Returns
    -------
    FigureUnit
        A minimal `FigureUnit` instance with the provided alt text and default values
        for other fields.
    """

    return FigureUnit(
        alt_text=alt_text,
        caption=None,
        contains_text=None,
        embedded_text=None,
        figure_kind=FigureKind.UNKNOWN,
    )


def make_page_ir(*, items: list[Block | Table], page_index: int = 0) -> PageIR:
    """Build a minimal valid `PageIR` for normalization tests.

    Parameters
    ----------
    items
        A list of `Block` and `Table` items to include in the page IR. The order of
        items in the list will be preserved in the `PageIR`.
    page_index
        The page index to assign to the `PageIR`. Defaults to 0.

    Returns
    -------
    PageIR
        A `PageIR` instance containing the provided items and page index, suitable for
        testing normalization and caption propagation logic.
    """

    return PageIR(items=items, page_index=page_index)


def make_table(
    *,
    bbox: BBoxType | None = None,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    local_code: str | None = None,
    repeats_header: bool | None = None,
    text: str = "value",
) -> Table:
    """Build a minimal one-cell `Table` suitable for caption propagation tests.

    Parameters
    ----------
    bbox
        The bounding box for the table. If `None`, a default bbox will be used.
    boundary
        The boundary status for the table. Defaults to `ItemBoundary.COMPLETE`.
    local_code
        An optional local code to assign to the table. If `None`, the table will have
        no local code.
    repeats_header
        An optional flag indicating whether the table repeats its header on
        continuation. This is not relevant for caption propagation tests, but is
        included to ensure that the presence of this metadata does not interfere with
        the logic. Defaults to `None`.
    text
        The text content for the single cell in the table. Defaults to "value". This is
        included to ensure the table is considered valid and non-empty for caption
        propagation tests.

    Returns
    -------
    Table
        A `Table` instance with the specified properties, containing a single cell with
        the provided text, suitable for testing caption propagation and related logic.
    """

    bbox_value = bbox or make_bbox(x0=0.0, x1=100.0, y0=0.0, y1=40.0)

    return Table(
        bbox=bbox_value,
        boundary=boundary,
        header_row_count=0,
        kind="table",
        local_code=local_code,
        n_cols=1,
        repeats_header=repeats_header,
        rows=[
            TableRow(
                cells=[
                    TableCell(
                        col_span=1,
                        row_span=1,
                        synthetic=False,
                        text=make_text_unit(text=text),
                    )
                ]
            )
        ],
    )


def make_text_unit(*, language: str = "en", text: str) -> TextUnit:
    """Build a minimal valid `TextUnit` instance.

    Parameters
    ----------
    language
        The language code for the text unit. Defaults to "en".
    text
        The text content of the unit.

    Returns
    -------
    TextUnit
        A `TextUnit` instance with the provided text and language, and `None` for the
        English text field.
    """

    return TextUnit(language=language, text=text, text_en=None)


@pytest.fixture()
def artifact_block() -> Block:
    """Return a minimal artifact block used by filtering and scan tests.

    Returns
    -------
    Block
        A `Block` instance with `BlockType.ARTIFACT` and placeholder text, suitable for
        testing artifact filtering and skipping logic in caption propagation.
    """

    return make_block(block_type=BlockType.ARTIFACT, text="Page 1")


@pytest.fixture()
def caption_block() -> Block:
    """Return a caption block with a parseable table label in its text.

    Returns
    -------
    Block
        A `Block` instance with `BlockType.CAPTION` and text containing a parseable
        caption code ("Table 2"), suitable for testing caption code resolution and
        propagation logic.
    """

    return make_block(block_type=BlockType.CAPTION, text="Table 2: Weekly objectives")


@pytest.fixture()
def figure_block() -> Block:
    """Return a minimal figure block for figure-propagation tests.

    Returns
    -------
    Block
        A `Block` instance with `BlockType.FIGURE` and a valid `FigureUnit`, suitable
        for testing caption propagation to figure targets and ensuring that figure
        blocks are recognized as valid targets even without text content or local codes.
    """

    return make_block(block_type=BlockType.FIGURE)


@pytest.fixture()
def table_item() -> Table:
    """Return a minimal table item with no existing local code.

    Returns
    -------
    Table
        A `Table` instance with no local code and a single cell containing placeholder
        text, suitable for testing caption propagation to table targets and ensuring
        that tables without existing local codes can receive propagated caption codes
        during normalization.
    """

    return make_table()


class TestFindNextNonArtifact:
    """Tests for `_find_next_non_artifact`."""

    def test_returns_first_non_artifact_after_start(
        self, artifact_block: Block, table_item: Table
    ) -> None:
        """It should skip artifact blocks and return the next real content item.

        Parameters
        ----------
        artifact_block
            A pytest fixture providing a minimal artifact block, used to test that the
            function correctly identifies and skips artifacts when searching for the
            next non-artifact item.
        table_item
            A pytest fixture providing a minimal table item, used to test that the
            function correctly returns the next non-artifact item after skipping
            artifacts.
        """

        items: ItemsType = [(0, artifact_block), (1, table_item)]
        result = normalize_page_items._find_next_non_artifact(
            items=items, start_index=0
        )

        assert result == (1, 1, table_item)

    def test_returns_none_when_only_artifacts_remain(
        self, artifact_block: Block
    ) -> None:
        """It should return `None` when no non-artifact item exists after the start.

        Parameters
        ----------
        artifact_block
            A pytest fixture providing a minimal artifact block, used to test that the
            function returns `None` when the only items remaining after the start index
            are artifacts.
        """

        items: ItemsType = [
            (0, artifact_block),
        ]

        result = normalize_page_items._find_next_non_artifact(
            items=items, start_index=0
        )

        assert result is None


class TestResolveLabelCode:
    """Tests for `_resolve_label_code`."""

    def test_falls_back_to_text_when_local_code_is_missing(self) -> None:
        """It should parse a leading caption code from the block text."""

        label_block = make_block(
            block_type=BlockType.CAPTION, local_code=None, text="Tableau 4: Résultats"
        )

        result = normalize_page_items._resolve_label_code(label_block)

        assert result == "Tableau 4"

    def test_prefers_valid_local_code_over_text(self) -> None:
        """It should prefer an already-populated parseable local code."""

        label_block = make_block(
            block_type=BlockType.CAPTION,
            local_code="Figure III",
            text="Figure IV: Different surface text",
        )

        result = normalize_page_items._resolve_label_code(label_block)

        assert result == "Figure III"

    def test_returns_none_when_neither_local_code_nor_text_is_parseable(self) -> None:
        """It should return `None` when no caption-like code can be resolved."""

        label_block = make_block(
            block_type=BlockType.CAPTION,
            local_code="Section A",
            text="Weekly objectives and teaching notes",
        )

        result = normalize_page_items._resolve_label_code(label_block)

        assert result is None


class TestTryAssignImmediate:
    """Tests for `_try_assign_immediate`."""

    def test_assigns_raw_code_to_immediate_table_target(self) -> None:
        """It should write the raw caption code to an unlabeled immediate table."""

        label_block = make_block(
            block_type=BlockType.CAPTION, text="Tableau 4: Résultats"
        )
        target_table = make_table(local_code=None)
        warnings: list[str] = []

        was_assigned = normalize_page_items._try_assign_immediate(
            code="Tableau 4",
            label_info=(3, label_block),
            page_index=2,
            target_info=(4, target_table),
            warnings=warnings,
        )

        assert was_assigned is True
        assert target_table.local_code == "Tableau 4"
        assert len(warnings) == 1
        assert "Propagated label code 'Tableau 4'" in warnings[0]

    def test_returns_false_for_incompatible_immediate_target(self) -> None:
        """It should return `False` when the next item kind cannot receive the code."""

        label_block = make_block(
            block_type=BlockType.CAPTION, text="Table 3: Objectives"
        )
        paragraph_block = make_block(
            block_type=BlockType.PARAGRAPH, text="Some explanatory prose."
        )
        warnings: list[str] = []

        was_assigned = normalize_page_items._try_assign_immediate(
            code="Table 3",
            label_info=(1, label_block),
            page_index=0,
            target_info=(2, paragraph_block),
            warnings=warnings,
        )

        assert was_assigned is False
        assert not warnings

    def test_treats_canonical_match_as_success_without_overwrite(self) -> None:
        """It should stop scanning when the target already carries an equivalent code."""

        label_block = make_block(block_type=BlockType.CAPTION, text="Fig. III: Example")
        target_figure = make_block(block_type=BlockType.FIGURE, local_code="Figure III")
        warnings: list[str] = []

        was_assigned = normalize_page_items._try_assign_immediate(
            code="Fig. III",
            label_info=(5, label_block),
            page_index=7,
            target_info=(6, target_figure),
            warnings=warnings,
        )

        assert was_assigned is True
        assert target_figure.local_code == "Figure III"
        assert not warnings

    def test_warns_on_conflicting_immediate_target_without_overwrite(self) -> None:
        """It should warn and preserve the existing code when the target conflicts."""

        label_block = make_block(block_type=BlockType.CAPTION, text="Figure 2: Example")
        target_figure = make_block(block_type=BlockType.FIGURE, local_code="Figure 3")
        warnings: list[str] = []

        was_assigned = normalize_page_items._try_assign_immediate(
            code="Figure 2",
            label_info=(8, label_block),
            page_index=4,
            target_info=(9, target_figure),
            warnings=warnings,
        )

        assert was_assigned is True
        assert target_figure.local_code == "Figure 3"
        assert len(warnings) == 1
        assert "Caption/figure code conflict on page 4" in warnings[0]


class TestTryFallbackScan:
    """Tests for `_try_fallback_scan`."""

    def test_assigns_to_nearest_later_compatible_target(
        self, artifact_block: Block
    ) -> None:
        """It should scan forward past non-target items and assign the first match.

        Parameters
        ----------
        artifact_block
            A pytest fixture providing a minimal artifact block, used to test that the
            scan correctly skips artifacts when looking for a compatible target to
            assign a caption code to.
        """

        paragraph_block = make_block(
            block_type=BlockType.PARAGRAPH, text="Introductory prose."
        )
        target_table = make_table(local_code=None)
        warnings: list[str] = []
        items: ItemsType = [
            (0, artifact_block),
            (1, paragraph_block),
            (2, target_table),
        ]

        normalize_page_items._try_fallback_scan(
            code="Table 5",
            items=items,
            label_orig_index=10,
            page_index=3,
            start_index=0,
            warnings=warnings,
        )

        assert target_table.local_code == "Table 5"
        assert len(warnings) == 1
        assert "nearest following table" in warnings[0]

    def test_stops_before_jumping_over_another_labeled_caption(self) -> None:
        """It should stop scanning when another explicit caption label is encountered."""

        next_caption = make_block(
            block_type=BlockType.CAPTION, text="Table 6: A different table"
        )
        later_table = make_table(local_code=None)
        warnings: list[str] = []
        items: ItemsType = [(0, next_caption), (1, later_table)]

        normalize_page_items._try_fallback_scan(
            code="Table 5",
            items=items,
            label_orig_index=4,
            page_index=1,
            start_index=0,
            warnings=warnings,
        )

        assert later_table.local_code is None
        assert not warnings

    def test_warns_on_conflicting_compatible_target(self) -> None:
        """It should warn and stop when the first compatible target has a different code."""

        conflicting_table = make_table(local_code="Table 8")
        warnings: list[str] = []
        items: ItemsType = [(0, conflicting_table)]

        normalize_page_items._try_fallback_scan(
            code="Table 7",
            items=items,
            label_orig_index=2,
            page_index=6,
            start_index=0,
            warnings=warnings,
        )

        assert conflicting_table.local_code == "Table 8"
        assert len(warnings) == 1
        assert "Caption/table code conflict on page 6" in warnings[0]


class TestNormalizePageItems:
    """Tests for `normalize_page_items`."""

    def test_filters_artifacts_and_sorts_preserving_valid_table_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It should drop artifacts, sort by bbox, and preserve valid table metadata.

        Parameters
        ----------
        monkeypatch
            The pytest fixture for monkeypatching functions during the test.
        """

        artifact = make_block(
            bbox=make_bbox(x0=0.0, x1=50.0, y0=5.0, y1=15.0),
            block_type=BlockType.ARTIFACT,
            text="Running header",
        )
        late_heading = make_block(
            bbox=make_bbox(x0=0.0, x1=100.0, y0=200.0, y1=220.0),
            block_type=BlockType.HEADING,
            text="Later heading",
        )
        early_table = make_table(
            bbox=make_bbox(x0=0.0, x1=100.0, y0=100.0, y1=140.0),
            boundary=ItemBoundary.RESUMED,
            repeats_header=True,
        )
        page_ir = make_page_ir(
            items=[late_heading, artifact, early_table], page_index=9
        )
        warnings: list[str] = []

        def _fake_is_artifact(item: Block | Table) -> bool:
            """Return ``True`` only for artifact blocks during normalization tests.

            Parameters
            ----------
            item
                The item to check for artifact status.

            Returns
            -------
            bool
                `True` if the item is an artifact block, `False` otherwise.
            """

            return isinstance(item, Block) and item.block_type == BlockType.ARTIFACT

        monkeypatch.setattr(
            normalize_page_items,
            "is_artifact",
            lambda item: _fake_is_artifact(item=item),
        )

        result = normalize_page_items.normalize_page_items(
            keep_artifacts=False,
            page_ir=page_ir,
            sort_items_by_bbox=True,
            warnings=warnings,
        )

        assert [index for index, _ in result] == [2, 0]
        assert early_table.repeats_header is True
        assert not any("Clearing repeats_header" in warning for warning in warnings)
        assert any(
            "Bbox ordering changed for page 9" in warning for warning in warnings
        )

    def test_propagates_caption_codes_after_sorting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It should reorder items first, then allow caption propagation on that order.

        Parameters
        ----------
        monkeypatch
            The pytest fixture for monkeypatching functions during the test.
        """

        table_item_ = make_table(
            bbox=make_bbox(x0=0.0, x1=100.0, y0=200.0, y1=260.0), local_code=None
        )
        caption = make_block(
            bbox=make_bbox(x0=0.0, x1=100.0, y0=100.0, y1=120.0),
            block_type=BlockType.CAPTION,
            text="Table 3: Ordered after sorting",
        )
        page_ir = make_page_ir(items=[table_item_, caption], page_index=5)
        warnings: list[str] = []

        monkeypatch.setattr(normalize_page_items, "is_artifact", lambda item: False)

        result = normalize_page_items.normalize_page_items(
            keep_artifacts=True,
            page_ir=page_ir,
            sort_items_by_bbox=True,
            warnings=warnings,
        )

        assert [index for index, _ in result] == [1, 0]
        assert caption.local_code == "Table 3"
        assert table_item_.local_code == "Table 3"


class TestPropagateCaptionLocalCodes:
    """Tests for `propagate_caption_local_codes`."""

    def test_assigns_to_immediate_same_page_table(self) -> None:
        """It should normalize the caption and assign its code to the next table."""

        caption = make_block(block_type=BlockType.CAPTION, text="Tableau 8: Mesure")
        table_item_ = make_table(local_code=None)
        items: ItemsType = [(0, caption), (1, table_item_)]
        warnings: list[str] = []

        normalize_page_items._propagate_caption_local_codes(
            items=items, page_index=12, warnings=warnings
        )

        assert caption.local_code == "Tableau 8"
        assert table_item_.local_code == "Tableau 8"
        assert len(warnings) == 1

    def test_does_not_jump_over_next_labeled_caption(self) -> None:
        """It should not let one caption steal the object owned by the next caption."""

        first_caption = make_block(block_type=BlockType.CAPTION, text="Table 1: First")
        second_caption = make_block(
            block_type=BlockType.CAPTION, text="Table 2: Second"
        )
        table_item_ = make_table(local_code=None)
        items: ItemsType = [(0, first_caption), (1, second_caption), (2, table_item_)]
        warnings: list[str] = []

        normalize_page_items._propagate_caption_local_codes(
            items=items, page_index=2, warnings=warnings
        )

        assert first_caption.local_code == "Table 1"
        assert second_caption.local_code == "Table 2"
        assert table_item_.local_code == "Table 2"
        assert len(warnings) == 1
        assert "Table 2" in warnings[0]

    def test_falls_back_to_later_figure_after_non_target_block(self) -> None:
        """It should scan past a non-target block and bind the nearest later figure."""

        caption = make_block(block_type=BlockType.CAPTION, text="Figure 2: Triangle")
        paragraph = make_block(
            block_type=BlockType.PARAGRAPH, text="A short explanation."
        )
        figure = make_block(block_type=BlockType.FIGURE, local_code=None)
        items: ItemsType = [(0, caption), (1, paragraph), (2, figure)]
        warnings: list[str] = []

        normalize_page_items._propagate_caption_local_codes(
            items=items, page_index=4, warnings=warnings
        )

        assert caption.local_code == "Figure 2"
        assert figure.local_code == "Figure 2"
        assert len(warnings) == 1
        assert "nearest following figure" in warnings[0]
