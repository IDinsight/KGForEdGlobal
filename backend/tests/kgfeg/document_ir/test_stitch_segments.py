"""This is the main module for testing document_ir/stitch_segments.py."""

# Standard Library
from typing import Any

# Third Party Library
import pytest

# Package Library
from kgfeg.document_ir.schemas import SectionHeadingRef, TableSegment
from kgfeg.document_ir.stitch_segments import (
    _are_items_compatible_for_segment_stitching,
    _build_continuation_chain,
    _dfs,
    _drop_repeated_header,
    _expand_header_row_to_n_cols,
    _expand_table_rows_to_rows_grid,
    _fill_down_table_rows,
    _fill_span_area,
    _finalize_table_structure,
    _infer_header_row_count_from_rows,
    _materialize_segment,
    _populate_grid_spans,
    _process_next_table_slice,
    _repair_short_rows_missing_trailing_cols_as_colspan,
    _resolve_header_row_count,
    _resolve_initial_local_code,
    _stitch_block_chain,
    _stitch_table_chain,
    _update_section_stack,
    _validate_link_graph,
    build_stitched_segments,
)
from kgfeg.page_ir_extraction.schemas import (
    Block,
    ListItem,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from kgfeg.schemas import StitchingConfig
from kgfeg.utils.constants import BlockType, ItemBoundary, PageBoundaryState


@pytest.fixture(name="doc_key")
def fixture_doc_key() -> str:
    """Return a deterministic document key for segment-ID tests.

    Returns
    -------
    str
        A fixed document key string for use in tests that require stable segment IDs.
    """

    return "doc-key-123"


@pytest.fixture(name="stitching_config")
def fixture_stitching_config() -> StitchingConfig:
    """Return a default stitching config for end-to-end stitching tests.

    Returns
    -------
    StitchingConfig
        A stitching config with default values for all settings.
    """

    return StitchingConfig(
        keep_artifacts=False,
        max_section_path_length=12,
        min_link_score=1.0,
        overwrite=False,
        repair_hyphenation=True,
        sort_items_by_bbox=False,
        table_filldown_enabled=True,
        table_filldown_group_cols_max=1,
        verification_auto_stitch_confidence=0.75,
    )


def make_bbox(
    *, x0: float = 0.0, x1: float = 100.0, y0: float = 0.0, y1: float = 20.0
) -> list[float]:
    """Build a valid bbox in page-IR coordinate order.

    Parameters
    ----------
    x0
        The left coordinate for the bbox, which may be used for debugging and warnings.
    x1
        The right coordinate for the bbox, which may be used for debugging and warnings.
    y0
        The top coordinate for the bbox, which may be used for debugging and warnings.
    y1
        The bottom coordinate for the bbox, which may be used for debugging and warnings.

    Returns
    -------
    list[float]
        A bbox in page-IR coordinate order with the requested values.
    """

    return [x0, y0, x1, y1]


def make_block(
    *,
    bbox: list[float] | None = None,
    block_type: BlockType = BlockType.PARAGRAPH,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    figure: Any | None = None,
    list_items: list[ListItem] | None = None,
    local_code: str | None = None,
    text: str | TextUnit | None = "Text",
) -> Block:
    """Build a valid block for the requested block type.

    Parameters
    ----------
    bbox
        The bounding box for the block in page-IR coordinates. If not provided, a
        default bbox will be used.
    block_type
        The block type for the block, which may be used for debugging and warnings.
    boundary
        The boundary flag for the block, which may affect stitching logic. Defaults to
        `ItemBoundary.COMPLETE`.
    figure
        The figure data for the block, which should only be provided for figure blocks
        and may be used for debugging and warnings.
    list_items
        The list items for the block, which should only be provided for list blocks and
        may be used for debugging and warnings.
    local_code
        The local code for the block, which may be used for debugging and warnings.
    text
        The text for the block, which will be converted into a `TextUnit` if provided
        as a string. This should not be provided for figure or list blocks, but may be
        used for debugging and warnings when it is.

    Returns
    -------
    Block
        A block with the requested structure and minimal valid values for all other
        fields.
    """

    bbox_value = bbox if bbox is not None else make_bbox()
    figure_value = figure if block_type == BlockType.FIGURE else None
    list_items_value = list_items if block_type == BlockType.LIST else None
    text_value: TextUnit | None

    if block_type in {BlockType.FIGURE, BlockType.LIST}:
        text_value = None
    elif isinstance(text, TextUnit):
        text_value = text
    elif isinstance(text, str):
        text_value = make_text_unit(text=text)
    else:
        text_value = None

    if block_type == BlockType.LIST and list_items_value is None:
        list_items_value = [make_list_item(text="Item 1")]

    return Block(
        bbox=bbox_value,
        block_type=block_type,
        boundary=boundary,
        figure=figure_value,
        kind="block",
        list_items=list_items_value,
        local_code=local_code,
        text=text_value,
    )


def make_list_item(*, marker: str | None = "•", text: str = "Item") -> ListItem:
    """Build a valid list item.

    Parameters
    ----------
    marker
        The list marker for the item, which may be used for debugging and warnings.
    text
        The text for the item, which will be converted into a `TextUnit`. This may be
        used for debugging and warnings.

    Returns
    -------
    ListItem
        A list item with the requested marker and text.
    """

    return ListItem(marker=marker, text=make_text_unit(text=text))


def make_page_ir(
    *,
    boundary_state: PageBoundaryState = PageBoundaryState.STANDALONE,
    items: list[Block | Table] | None = None,
    page_index: int = 0,
) -> PageIR:
    """Build a minimal `PageIR` with page index and items.

    Parameters
    ----------
    boundary_state
        The page boundary state for the page, which may be used for stitching logic.
        Defaults to `PageBoundaryState.STANDALONE`.
    items
        The items to include in the page IR, which may be used for stitching logic and
        should be consistent with the boundary state when possible (e.g., a page with
        `boundary_state=PageBoundaryState.START` should ideally have at least one item
        with `boundary=ItemBoundary.TRUNCATED`). If not provided, a default single
        paragraph block will be used.
    page_index
        The page index for the page IR, which may be used for debugging and warnings.

    Returns
    -------
    PageIR
        A page IR with the requested boundary state, items, and page index, and minimal
        valid values for all other fields.
    """

    return PageIR(
        boundary_state=boundary_state, items=items or [], page_index=page_index
    )


def make_section_heading_ref(
    *, item_index: int = 0, page_index: int = 0, text: str = "Heading"
) -> SectionHeadingRef:
    """Build a lightweight section-path heading reference.

    Parameters
    ----------
    item_index
        The item index for the heading reference, which may be used for debugging and
        warnings.
    page_index
        The page index for the heading reference, which may be used for debugging and
        warnings.
    text
        The text for the heading reference, which may be used for debugging and
        warnings.

    Returns
    -------
    SectionHeadingRef
        A section heading reference with the requested values.
    """

    return SectionHeadingRef(item_index=item_index, page_index=page_index, text=text)


def make_table(
    *,
    bbox: list[float] | None = None,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    header_row_count: int = 0,
    local_code: str | None = None,
    n_cols: int | None = None,
    repeats_header: bool | None = None,
    rows: list[TableRow] | None = None,
) -> Table:
    """Build a valid table.

    Parameters
    ----------
    bbox
        The bounding box for the table in page-IR coordinates. If not provided, a
        default bbox will be used.
    boundary
        The boundary flag for the table, which may affect stitching logic. Defaults to
        `ItemBoundary.COMPLETE`.
    header_row_count
        The number of header rows in the table, which may be zero. This may be
        overridden by header inference logic in stitching, but should be set to the
        extractor output when available.
    local_code
        The local code for the table, which may be used for debugging and warnings.
    n_cols
        The declared number of columns in the table, which may differ from the row
        lengths. This may be overridden by stitching logic if rows with more cells are
        encountered, but should be set to the extractor output when available.
    repeats_header
        Whether the table is marked as repeating its header on continuation. This may
        be used for stitching logic but should be set to the extractor output when
        available.
    rows
        The table rows to include in the table, which may have fewer cells than n_cols
        and may have row/col spans. If not provided, a default 1 x 1 body will be used.

    Returns
    -------
    Table
        A table with the requested structure and minimal valid values for all other
        fields.
    """

    bbox_value = bbox if bbox is not None else make_bbox()
    rows_value = rows if rows is not None else [make_table_row(texts=["Cell"])]
    return Table(
        bbox=bbox_value,
        boundary=boundary,
        header_row_count=header_row_count,
        kind="table",
        local_code=local_code,
        n_cols=n_cols,
        repeats_header=repeats_header,
        rows=rows_value,
    )


def make_table_cell(
    *, col_span: int = 1, row_span: int = 1, text: str | None = "Cell"
) -> TableCell:
    """Build a table cell, preserving true empty cells as `text=None`.

    Parameters
    ----------
    col_span
        The column span for the cell, which must be a positive integer.
    row_span
        The row span for the cell, which must be a positive integer.
    text
        The text for the cell, which will be converted into a `TextUnit`. If `None`,
        the cell will be treated as an empty cell with no text, which is important to
        preserve as it interacts with fill-down and span expansion logic in non-trivial
        ways.

    Returns
    -------
    TableCell
        A table cell with the requested spans and text.
    """

    text_unit = None if text is None else make_text_unit(text=text)
    return TableCell(col_span=col_span, row_span=row_span, text=text_unit)


def make_table_row(
    *, cells: list[TableCell] | None = None, texts: list[str | None] | None = None
) -> TableRow:
    """Build a `TableRow` from either explicit cells or a list of text values.

    Parameters
    ----------
    cells
        The explicit cells to include in the row, which may have custom spans and text.
    texts
        The text values to include in the row, which will be converted into single-span
        cells. If `cells` is also provided, this will be ignored.

    Returns
    -------
    TableRow
        A table row with the requested cells or text values, and default spans.
    """

    if cells is None:
        resolved_texts = texts if texts is not None else ["Cell"]
        cells = [make_table_cell(text=text) for text in resolved_texts]

    return TableRow(cells=cells)


def make_table_segment(
    *,
    header_row_count: int = 0,
    n_cols: int = 2,
    rows: list[TableRow] | None = None,
    segment_id: str = "segment-1",
) -> TableSegment:
    """Build a minimal valid `TableSegment` for span/grid helper tests.

    Parameters
    ----------
    header_row_count
        The number of header rows in the segment.
    n_cols
        The declared number of columns in the segment, which may differ from the row
        lengths.
    rows
        The table rows to include in the segment, which may have fewer cells than
        n_cols and may have row/col spans. If not provided, a default 1 x 2 body will
        be used.
    segment_id
        The segment ID to use in provenance and warnings.

    Returns
    -------
    TableSegment
        A table segment with the requested rows and structure, and minimal valid values
        for all other fields.
    """

    rows_value = rows if rows is not None else [make_table_row(texts=["A", "B"])]
    header_rows = rows_value[:header_row_count]
    header_rows_canonical = [
        [cell.text.text if cell.text is not None else "" for cell in row.cells]
        for row in header_rows
    ]
    segment_provenance = [
        {
            "bbox": make_bbox(),
            "boundary": ItemBoundary.COMPLETE,
            "item_addr": "p0:raw0",
            "item_index": 0,
            "kind": "table",
            "local_code": None,
            "page_index": 0,
            "repeats_header": None,
        }
    ]
    slices = [
        {
            "bbox": make_bbox(),
            "boundary": ItemBoundary.COMPLETE,
            "dropped_header_rows": 0,
            "header_row_count": header_row_count,
            "item_index": 0,
            "local_code": None,
            "page_index": 0,
            "repeats_header": None,
            "rows": rows_value,
        }
    ]

    return TableSegment(
        columns_signature=(
            TableSegment._build_columns_signature(
                header_rows_canonical=header_rows_canonical,
            )
            if header_rows_canonical
            else None
        ),
        grid_sources=None,
        header_row_count=header_row_count,
        header_rows=header_rows,
        header_rows_canonical=header_rows_canonical,
        kind="table",
        local_code=None,
        n_cols=n_cols,
        row_provenance=None,
        rows=rows_value,
        rows_filldown=None,
        rows_grid=None,
        section_path=[],
        segment_id=segment_id,
        segment_provenance=segment_provenance,
        slices=slices,
    )


def make_text_unit(
    *, language: str = "en", text: str = "Text", text_en: str | None = None
) -> TextUnit:
    """Build a `TextUnit` with optional English text.

    Parameters
    ----------
    language
        The language code for the text unit, which may be used for debugging and
        warnings.
    text
        The text for the text unit, which may be used for debugging and warnings.
    text_en
        The English text for the text unit, which may be used for debugging and
        warnings.

    Returns
    -------
    TextUnit
        A text unit with the requested values.
    """

    return TextUnit(language=language, text=text, text_en=text_en)


class TestAreItemsCompatibleForSegmentStitching:
    """Tests for `_are_items_compatible_for_segment_stitching()`."""

    def test_returns_false_for_different_block_types(self) -> None:
        """It should reject block links when block types differ."""

        next_item = make_block(block_type=BlockType.LIST)
        prev_item = make_block(block_type=BlockType.PARAGRAPH, text="Paragraph")

        assert (
            _are_items_compatible_for_segment_stitching(
                next_item=next_item, prev_item=prev_item
            )
            is False
        )

    def test_returns_true_for_matching_block_types(self) -> None:
        """It should accept block links when both block types match exactly."""

        next_item = make_block(block_type=BlockType.PARAGRAPH, text="Next")
        prev_item = make_block(block_type=BlockType.PARAGRAPH, text="Prev")

        assert (
            _are_items_compatible_for_segment_stitching(
                next_item=next_item, prev_item=prev_item
            )
            is True
        )

    def test_returns_true_for_table_to_table_links(self) -> None:
        """It should accept table continuations."""

        next_item = make_table(rows=[make_table_row(texts=["B"])])
        prev_item = make_table(rows=[make_table_row(texts=["A"])])

        assert (
            _are_items_compatible_for_segment_stitching(
                next_item=next_item, prev_item=prev_item
            )
            is True
        )


class TestBuildContinuationChain:
    """Tests for `_build_continuation_chain()`."""

    def test_builds_a_two_item_chain(self) -> None:
        """It should follow one forward link and return both items in order."""

        first_item = make_block(boundary=ItemBoundary.TRUNCATED, text="A")
        second_item = make_block(boundary=ItemBoundary.RESUMED, text="B")
        items_lookup = {0: {0: first_item}, 1: {0: second_item}}
        warnings: list[str] = []

        chain = _build_continuation_chain(
            items_lookup=items_lookup,
            links={(0, 0): (1, 0)},
            start_item=first_item,
            start_key=(0, 0),
            warnings=warnings,
        )

        assert [(page_index, item_index) for page_index, item_index, _ in chain] == [
            (0, 0),
            (1, 0),
        ]
        assert not warnings

    def test_warns_and_stops_for_a_broken_destination(self) -> None:
        """It should append a warning and stop when a destination page is missing."""

        first_item = make_block(boundary=ItemBoundary.TRUNCATED, text="A")
        items_lookup = {0: {0: first_item}}
        warnings: list[str] = []

        chain = _build_continuation_chain(
            items_lookup=items_lookup,
            links={(0, 0): (9, 0)},
            start_item=first_item,
            start_key=(0, 0),
            warnings=warnings,
        )

        assert len(chain) == 1
        assert "Page 9 not found" in warnings[0]


class TestBuildStitchedSegments:
    """Tests for `build_stitched_segments()`."""

    def test_builds_segments_in_reading_order_and_applies_heading_context(
        self, *, doc_key: str, stitching_config: StitchingConfig
    ) -> None:
        """It should stitch a continuation chain once and carry heading context forward.

        Parameters
        ----------
        doc_key
            The document key fixture for deterministic segment IDs in tests.
        stitching_config
            The stitching config fixture for default stitching settings in tests.
        """

        heading = make_block(
            bbox=make_bbox(x0=0.0, x1=100.0, y0=0.0, y1=10.0),
            block_type=BlockType.HEADING,
            text="Mathematics",
        )
        paragraph_page_0 = make_block(
            bbox=make_bbox(x0=0.0, x1=100.0, y0=20.0, y1=40.0),
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.TRUNCATED,
            text="Count numbers",
        )
        paragraph_page_1 = make_block(
            bbox=make_bbox(x0=0.0, x1=100.0, y0=0.0, y1=20.0),
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.RESUMED,
            text="up to ten",
        )
        items_mapping = {
            0: [(0, heading), (1, paragraph_page_0)],
            1: [(0, paragraph_page_1)],
        }
        links = {(0, 1): (1, 0)}
        page_irs = [
            make_page_ir(items=[heading, paragraph_page_0], page_index=0),
            make_page_ir(items=[paragraph_page_1], page_index=1),
        ]
        warnings: list[str] = []

        segments = build_stitched_segments(
            config=stitching_config,
            doc_key=doc_key,
            items_mapping=items_mapping,
            links=links,
            page_irs=page_irs,
            warnings=warnings,
        )

        assert len(segments) == 2
        assert segments[0].kind == "block"
        assert segments[0].text is not None
        assert segments[0].text.text == "Mathematics"
        assert segments[1].kind == "block"
        assert segments[1].combined_text == "Count numbers up to ten"
        assert segments[1].section_path == [
            make_section_heading_ref(item_index=0, page_index=0, text="Mathematics")
        ]
        assert not warnings


class TestDfs:
    """Tests for `_dfs()`."""

    def test_marks_acyclic_nodes_as_visited(self) -> None:
        """It should finish an acyclic traversal with all nodes marked visited."""

        links = {(0, 0): (1, 0), (1, 0): (2, 0)}
        visit_state: dict[tuple[int, int], int] = {}

        _dfs(links=links, node_key=(0, 0), path=[], visit_state=visit_state)

        assert visit_state[(0, 0)] == 2
        assert visit_state[(1, 0)] == 2
        assert visit_state[(2, 0)] == 2

    def test_raises_for_a_cycle(self) -> None:
        """It should raise on cyclic page-break links."""

        links = {(0, 0): (1, 0), (1, 0): (0, 0)}

        with pytest.raises(ValueError, match="Cycle detected"):
            _dfs(links=links, node_key=(0, 0), path=[], visit_state={})


class TestDropRepeatedHeader:
    """Tests for `_drop_repeated_header()`."""

    def test_drops_matching_header_rows(self) -> None:
        """It should remove repeated headers when the top rows match the base header."""

        header = make_table_row(texts=["Topic", "Outcome"])
        body = make_table_row(texts=["Counting", "Recognize"])
        next_table = make_table(
            boundary=ItemBoundary.RESUMED,
            header_row_count=1,
            repeats_header=True,
            rows=[header, body],
        )

        rows_to_add, dropped_count = _drop_repeated_header(
            base_header_rows=[header], header_row_count=1, next_table=next_table
        )

        assert dropped_count == 1
        assert rows_to_add == [body]

    def test_keeps_all_rows_when_the_header_does_not_match(self) -> None:
        """It should keep the rows when the candidate header does not match the base."""

        base_header = make_table_row(texts=["Topic", "Outcome"])
        mismatched_top_row = make_table_row(texts=["Checkpoint", "Week 1"])
        body = make_table_row(texts=["Counting", "Recognize"])
        next_table = make_table(
            boundary=ItemBoundary.RESUMED,
            header_row_count=1,
            repeats_header=True,
            rows=[mismatched_top_row, body],
        )

        rows_to_add, dropped_count = _drop_repeated_header(
            base_header_rows=[base_header], header_row_count=1, next_table=next_table
        )

        assert dropped_count == 0
        assert rows_to_add == [mismatched_top_row, body]


class TestExpandHeaderRowToNCols:
    """Tests for `_expand_header_row_to_n_cols()`."""

    def test_pads_short_rows_to_the_declared_width(self) -> None:
        """It should pad header rows with empty strings when they are too short."""

        row = make_table_row(texts=["Topic", "Outcome"])

        assert _expand_header_row_to_n_cols(
            local_code="Table 1", n_cols=3, row=row, segment_id="seg-1", warnings=[]
        ) == ["topic", "outcome", ""]

    def test_truncates_overwide_rows_and_warns(self) -> None:
        """It should truncate header rows that expand past n_cols."""

        row = make_table_row(texts=["A", "B", "C"])
        warnings: list[str] = []

        expanded = _expand_header_row_to_n_cols(
            local_code="Table 1",
            n_cols=2,
            row=row,
            segment_id="seg-1",
            warnings=warnings,
        )

        assert expanded == ["a", "b"]
        assert "expanded wider than n_cols" in warnings[0]


class TestExpandTableRowsToRowsGrid:
    """Tests for `_expand_table_rows_to_rows_grid()`."""

    def test_expands_rowspans_and_padding_cells_into_a_rectangular_grid(self) -> None:
        """It should preserve span semantics while producing a full rectangular grid."""

        rows = [
            make_table_row(
                cells=[
                    make_table_cell(row_span=2, text="Topic"),
                    make_table_cell(text="Outcome 1"),
                ],
            ),
            make_table_row(
                cells=[make_table_cell(text=None), make_table_cell(text="Outcome 2")],
            ),
        ]
        segment = make_table_segment(n_cols=2, rows=rows)

        rows_grid, grid_sources = _expand_table_rows_to_rows_grid(segment)

        assert [cell.text.text for cell in rows_grid[0].cells] == ["Topic", "Outcome 1"]
        assert [cell.text.text for cell in rows_grid[1].cells] == ["Topic", "Outcome 2"]
        assert grid_sources[0][0]["source_row"] == 0
        assert grid_sources[1][0]["source_row"] == 0
        assert grid_sources[1][1]["source_row"] == 1


class TestFillDownTableRows:
    """Tests for `_fill_down_table_rows()`."""

    def test_fills_only_body_rows_and_only_the_requested_group_columns(self) -> None:
        """It should fill down blank leading cells while leaving headers untouched."""

        rows = [
            make_table_row(texts=["Topic", "Outcome"]),
            make_table_row(texts=["Counting", "Recognize 1-5"]),
            make_table_row(texts=[None, "Recognize 6-10"]),
        ]

        filled_rows = _fill_down_table_rows(
            header_row_count=1, rows=rows, table_filldown_group_cols_max=1
        )

        assert filled_rows[0].cells[0].text is not None
        assert filled_rows[0].cells[0].text.text == "Topic"
        assert filled_rows[2].cells[0].text is not None
        assert filled_rows[2].cells[0].text.text == "Counting"
        assert filled_rows[2].cells[1].text is not None
        assert filled_rows[2].cells[1].text.text == "Recognize 6-10"


class TestFillSpanArea:
    """Tests for `_fill_span_area()`."""

    def test_fills_the_requested_rectangle(self) -> None:
        """It should write the same value into every covered grid position."""

        grid: list[list[dict[str, int | TextUnit | None]]] = [
            [{"source_row": -1, "text": None}, {"source_row": -1, "text": None}],
            [{"source_row": -1, "text": None}, {"source_row": -1, "text": None}],
        ]
        value = make_text_unit(text="Merged")

        _fill_span_area(
            col_span=2,
            col_start=0,
            grid=grid,
            row_span=2,
            row_start=0,
            segment_id="seg-1",
            value=value,
        )

        assert isinstance(grid[0][0]["text"], TextUnit)
        assert grid[0][0]["text"].text == "Merged"
        assert isinstance(grid[1][1]["text"], TextUnit)
        assert grid[1][1]["text"].text == "Merged"
        assert grid[1][1]["source_row"] == 0

    def test_raises_for_overlapping_spans(self) -> None:
        """It should reject writes into already occupied grid positions."""

        grid = [[{"source_row": 0, "text": make_text_unit(text="Taken")}]]

        with pytest.raises(ValueError, match="Overlapping spans"):
            _fill_span_area(
                col_span=1,
                col_start=0,
                grid=grid,
                row_span=1,
                row_start=0,
                segment_id="seg-1",
                value=make_text_unit(text="New"),
            )


class TestFinalizeTableStructure:
    """Tests for `_finalize_table_structure()`."""

    def test_computes_columns_signature_and_warns_on_n_cols_inflation(self) -> None:
        """It should widen n_cols to the stitched rows and canonicalize headers."""

        chain = [
            (
                0,
                0,
                make_table(
                    header_row_count=1,
                    n_cols=2,
                    rows=[make_table_row(texts=["H1", "H2"])],
                ),
            )
        ]
        header_rows = [make_table_row(texts=["H1", "H2"])]
        stitched_rows = [
            make_table_row(texts=["H1", "H2"]),
            make_table_row(texts=["A", "B", "C"]),
        ]
        warnings: list[str] = []

        n_cols, columns_signature, header_rows_canonical = _finalize_table_structure(
            chain=chain,
            header_rows=header_rows,
            local_code="Table 1",
            segment_id="seg-1",
            stitched_rows=stitched_rows,
            warnings=warnings,
        )

        assert n_cols == 3
        assert columns_signature == "h1|h2|"
        assert header_rows_canonical == [["h1", "h2", ""]]
        assert "n_cols inflation detected" in warnings[0]


class TestInferHeaderRowCountFromRows:
    """Tests for `_infer_header_row_count_from_rows()`."""

    def test_detects_a_single_header_row(self) -> None:
        """It should infer one header row for a word-heavy top row followed by digits."""

        rows = [
            make_table_row(texts=["Topic", "Expected Standard"]),
            make_table_row(texts=["1", "2"]),
        ]

        header_row_count, confidence = _infer_header_row_count_from_rows(
            max_header_rows=3, rows=rows
        )

        assert header_row_count == 1
        assert confidence > 0.65

    def test_returns_zero_for_non_header_like_rows(self) -> None:
        """It should return zero when the top rows do not look header-like."""

        rows = [make_table_row(texts=["1", "2"]), make_table_row(texts=["3", "4"])]

        assert _infer_header_row_count_from_rows(max_header_rows=3, rows=rows) == (
            0,
            0.0,
        )


class TestMaterializeSegment:
    """Tests for `_materialize_segment()`."""

    def test_dispatches_block_chains_to_block_materialization(
        self, doc_key: str
    ) -> None:
        """It should return a `BlockSegment` for a homogeneous block chain.

        Parameters
        ----------
        doc_key
            The document key fixture for deterministic segment IDs in tests.
        """

        chain = [
            (
                0,
                0,
                make_block(
                    block_type=BlockType.PARAGRAPH,
                    boundary=ItemBoundary.TRUNCATED,
                    text="Hello",
                ),
            ),
            (
                1,
                0,
                make_block(
                    block_type=BlockType.PARAGRAPH,
                    boundary=ItemBoundary.RESUMED,
                    text="world",
                ),
            ),
        ]

        segment = _materialize_segment(
            chain=chain,
            doc_key=doc_key,
            item_index=0,
            page_index=0,
            repair_hyphenation=True,
            section_path=[],
            table_filldown_enabled=True,
            table_filldown_group_cols_max=1,
            warnings=[],
        )

        assert segment.kind == "block"
        assert segment.combined_text == "Hello world"

    def test_raises_for_mixed_kind_chains(self, doc_key: str) -> None:
        """It should reject a chain that mixes blocks and tables.

        Parameters
        ----------
        doc_key
            The document key fixture for deterministic segment IDs in tests.
        """

        chain = [
            (0, 0, make_block(text="A")),
            (1, 0, make_table(rows=[make_table_row(texts=["B"])])),
        ]

        with pytest.raises(ValueError, match="Mixed-kind chain"):
            _materialize_segment(
                chain=chain,
                doc_key=doc_key,
                item_index=0,
                page_index=0,
                repair_hyphenation=True,
                section_path=[],
                table_filldown_enabled=True,
                table_filldown_group_cols_max=1,
                warnings=[],
            )


class TestPopulateGridSpans:
    """Tests for `_populate_grid_spans()`."""

    def test_populates_a_mutable_grid_with_span_expansion(self) -> None:
        """It should write text payloads and source rows into the supplied grid."""

        rows = [
            make_table_row(
                cells=[
                    make_table_cell(row_span=2, text="Topic"),
                    make_table_cell(text="Outcome 1"),
                ],
            ),
            make_table_row(
                cells=[
                    make_table_cell(text=None),
                    make_table_cell(text="Outcome 2"),
                ],
            ),
        ]
        grid: list[list[dict[str, int | TextUnit | None]]] = [
            [{"source_row": -1, "text": None}, {"source_row": -1, "text": None}],
            [{"source_row": -1, "text": None}, {"source_row": -1, "text": None}],
        ]
        segment = make_table_segment(n_cols=2, rows=rows)

        _populate_grid_spans(grid=grid, n_cols=2, n_rows=2, segment=segment)

        assert isinstance(grid[0][0]["text"], TextUnit)
        assert grid[0][0]["text"].text == "Topic"
        assert isinstance(grid[1][0]["text"], TextUnit)
        assert grid[1][0]["text"].text == "Topic"
        assert grid[1][0]["source_row"] == 0
        assert isinstance(grid[1][1]["text"], TextUnit)
        assert grid[1][1]["text"].text == "Outcome 2"


class TestProcessNextTableSlice:
    """Tests for `_process_next_table_slice()`."""

    def test_drops_repeated_header_and_preserves_existing_segment_code(self) -> None:
        """It should keep the current code on conflict and infer effective header rows."""

        header = make_table_row(texts=["Topic", "Outcome"])
        body = make_table_row(texts=["Counting", "Recognize"])
        next_item = make_table(
            boundary=ItemBoundary.RESUMED,
            header_row_count=0,
            local_code="Table 2",
            repeats_header=True,
            rows=[header, body],
        )
        warnings: list[str] = []

        result = _process_next_table_slice(
            current_local_code="Table 1",
            next_item=next_item,
            next_item_index=4,
            next_page_index=2,
            segment_header_row_count=1,
            segment_header_rows=[header],
            segment_id="seg-1",
            warnings=warnings,
        )

        assert result["local_code"] == "Table 1"
        assert result["slice"].dropped_header_rows == 1
        assert result["slice"].header_row_count == 1
        assert result["slice"].repeats_header is True
        assert len(result["rows_to_add"]) == 1
        assert any("Conflicting local_code" in warning for warning in warnings)
        assert any("Inferred header_row_count=1" in warning for warning in warnings)


class TestRepairShortRowsMissingTrailingColsAsColspan:
    """Tests for `_repair_short_rows_missing_trailing_cols_as_colspan()`."""

    def test_extends_the_last_cell_to_fill_missing_trailing_columns(self) -> None:
        """It should convert a short non-header row into a trailing colspan."""

        rows = [
            make_table_row(texts=["Header 1", "Header 2", "Header 3"]),
            make_table_row(texts=["Section", "Checkpoint"]),
        ]
        warnings: list[str] = []

        repaired_rows = _repair_short_rows_missing_trailing_cols_as_colspan(
            header_row_count=1,
            n_cols=3,
            rows=rows,
            segment_id="seg-1",
            warnings=warnings,
        )

        assert repaired_rows[0].cells[0].col_span == 1
        assert repaired_rows[1].cells[-1].col_span == 2
        assert "table_colspan_repair" in warnings[0]


class TestResolveHeaderRowCount:
    """Tests for `_resolve_header_row_count()`."""

    def test_keeps_an_explicit_header_row_count(self) -> None:
        """It should preserve extractor output when it is already positive."""

        first_item = make_table(
            header_row_count=2,
            rows=[make_table_row(texts=["A"]), make_table_row(texts=["B"])],
        )

        assert (
            _resolve_header_row_count(
                first_item=first_item, item_index=0, page_index=0, warnings=[]
            )
            == 2
        )

    def test_uses_confident_inference_when_extractor_left_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It should adopt inferred headers only when confidence is high enough.

        Parameters
        ----------
        monkeypatch
            The pytest monkeypatch fixture for patching the inference function.
        """

        first_item = make_table(
            header_row_count=0, rows=[make_table_row(texts=["Topic", "Outcome"])]
        )
        warnings: list[str] = []

        monkeypatch.setattr(
            "kgfeg.document_ir.stitch_segments._infer_header_row_count_from_rows",
            lambda *, max_header_rows, rows: (1, 0.90),
        )

        resolved = _resolve_header_row_count(
            first_item=first_item, item_index=0, page_index=0, warnings=warnings
        )

        assert resolved == 1
        assert "Inferred header_row_count=1" in warnings[0]


class TestResolveInitialLocalCode:
    """Tests for `_resolve_initial_local_code()`."""

    def test_returns_the_first_non_empty_code_in_the_chain(self) -> None:
        """It should skip blank leading codes and adopt the first later code."""

        chain = [
            (0, 0, make_table(local_code="   ", rows=[make_table_row(texts=["A"])])),
            (
                1,
                0,
                make_table(local_code="Table 7", rows=[make_table_row(texts=["B"])]),
            ),
        ]

        assert _resolve_initial_local_code(chain=chain) == "Table 7"


class TestStitchBlockChain:
    """Tests for `_stitch_block_chain()`."""

    def test_combines_text_and_promotes_the_first_non_empty_local_code(
        self, doc_key: str
    ) -> None:
        """It should merge text slices, track provenance, and mark mixed languages as
        `mul`.

        Parameters
        ----------
        doc_key
            The document key to use for provenance entries.
        """

        first_block = make_block(
            block_type=BlockType.PARAGRAPH,
            local_code=None,
            text=make_text_unit(language="fr", text="Bonjour"),
        )
        second_block = make_block(
            block_type=BlockType.PARAGRAPH,
            local_code="3.1",
            text=make_text_unit(language="en", text="world"),
        )

        segment = _stitch_block_chain(
            chain=[(0, 0, first_block), (1, 0, second_block)],
            doc_key=doc_key,
            repair_hyphenation=True,
            section_path=[],
        )

        assert segment.kind == "block"
        assert segment.combined_text == "Bonjour world"
        assert segment.local_code == "3.1"
        assert segment.text is not None
        assert segment.text.language == "mul"
        assert len(segment.slices) == 2
        assert len(segment.segment_provenance) == 2


class TestStitchTableChain:
    """Tests for `_stitch_table_chain()`."""

    def test_stitches_two_table_slices_and_populates_grid_outputs(
        self, doc_key: str
    ) -> None:
        """It should drop repeated headers, backfill local code, and build fill-down
        rows.

        Parameters
        ----------
        doc_key
            The document key to use for provenance entries.
        """

        header = make_table_row(texts=["Topic", "Outcome"])
        first_body = make_table_row(texts=["Counting", "Recognize 1-5"])
        repeated_header = make_table_row(texts=["Topic", "Outcome"])
        second_body = make_table_row(texts=[None, "Recognize 6-10"])
        first_table = make_table(
            boundary=ItemBoundary.TRUNCATED,
            header_row_count=1,
            local_code=None,
            n_cols=2,
            rows=[header, first_body],
        )
        second_table = make_table(
            boundary=ItemBoundary.RESUMED,
            header_row_count=1,
            local_code="Table 1",
            n_cols=2,
            repeats_header=True,
            rows=[repeated_header, second_body],
        )
        warnings: list[str] = []

        segment = _stitch_table_chain(
            chain=[(0, 0, first_table), (1, 0, second_table)],
            doc_key=doc_key,
            section_path=[],
            table_filldown_enabled=True,
            table_filldown_group_cols_max=1,
            warnings=warnings,
        )

        assert segment.kind == "table"
        assert segment.local_code == "Table 1"
        assert segment.slices[0].local_code == "Table 1"
        assert segment.header_row_count == 1
        assert len(segment.rows) == 3
        assert segment.rows_grid is not None
        assert segment.row_provenance is not None
        assert segment.rows_filldown is not None
        assert segment.rows_filldown[2].cells[0].text is not None
        assert segment.rows_filldown[2].cells[0].text.text == "Counting"
        assert len(segment.row_provenance) == 3


class TestValidateLinkGraph:
    """Tests for `_validate_link_graph()`."""

    def test_accepts_a_valid_functional_acyclic_graph(self) -> None:
        """It should not raise for a simple forward-compatible graph."""

        items_mapping = {
            0: [(0, make_block(boundary=ItemBoundary.TRUNCATED, text="A"))],
            1: [(0, make_block(boundary=ItemBoundary.RESUMED, text="B"))],
        }
        items_lookup = {page: dict(items) for page, items in items_mapping.items()}
        links = {(0, 0): (1, 0)}

        _validate_link_graph(items_lookup=items_lookup, links=links)

    def test_rejects_destination_with_indegree_greater_than_one(self) -> None:
        """It should raise when two sources point to the same destination."""

        items_mapping = {
            0: [
                (0, make_block(boundary=ItemBoundary.TRUNCATED, text="A")),
                (1, make_block(boundary=ItemBoundary.TRUNCATED, text="B")),
            ],
            1: [(0, make_block(boundary=ItemBoundary.RESUMED, text="C"))],
        }
        items_lookup = {page: dict(items) for page, items in items_mapping.items()}
        links = {(0, 0): (1, 0), (0, 1): (1, 0)}

        with pytest.raises(ValueError, match="indegree=2"):
            _validate_link_graph(items_lookup=items_lookup, links=links)

    def test_rejects_incompatible_block_link(self) -> None:
        """It should raise when a block continuation changes block type."""

        items_mapping = {
            0: [(0, make_block(block_type=BlockType.PARAGRAPH, text="A"))],
            1: [(0, make_block(block_type=BlockType.LIST))],
        }
        items_lookup = {page: dict(items) for page, items in items_mapping.items()}
        links = {(0, 0): (1, 0)}

        with pytest.raises(ValueError, match="not segment-stitchable"):
            _validate_link_graph(items_lookup=items_lookup, links=links)


class TestUpdateSectionStack:
    """Tests for `_update_section_stack()`."""

    def test_adds_a_new_heading_reference(self) -> None:
        """It should append a heading reference when the chain starts with a heading."""

        chain = [(2, 3, make_block(block_type=BlockType.HEADING, text="Theme 4"))]

        updated = _update_section_stack(
            chain=chain, max_len=12, section_path_stack=[], warnings=[]
        )

        assert updated == [
            make_section_heading_ref(item_index=3, page_index=2, text="Theme 4")
        ]

    def test_dedupes_consecutive_identical_headings(self) -> None:
        """It should avoid appending the same heading text twice in a row."""

        existing_stack = [
            make_section_heading_ref(item_index=0, page_index=0, text="Mathematics")
        ]
        chain = [(1, 0, make_block(block_type=BlockType.HEADING, text="Mathematics"))]

        updated = _update_section_stack(
            chain=chain, max_len=12, section_path_stack=existing_stack, warnings=[]
        )

        assert updated == existing_stack

    def test_warns_when_a_heading_has_no_text_or_local_code(self) -> None:
        """It should leave the stack unchanged for malformed heading blocks."""

        malformed_heading = Block.model_construct(
            bbox=make_bbox(),
            block_type=BlockType.HEADING,
            boundary=ItemBoundary.COMPLETE,
            figure=None,
            kind="block",
            list_items=None,
            local_code=None,
            text=None,
        )
        warnings: list[str] = []

        updated = _update_section_stack(
            chain=[(0, 0, malformed_heading)],
            max_len=12,
            section_path_stack=[],
            warnings=warnings,
        )

        assert not updated
        assert "Heading block missing text and local_code" in warnings[0]
