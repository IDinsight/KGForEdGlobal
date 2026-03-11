"""This is the main module for testing page_ir_verification/postprocess_page_irs.py."""

# Standard Library
from typing import Any

# Third Party Library
import pytest

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification import postprocess_page_irs
from skg.utils.constants import BlockType, ItemBoundary


def cell(
    *,
    col_span: int = 1,
    row_span: int = 1,
    synthetic: bool = False,
    text: str | None = "x",
) -> TableCell:
    """Create a TableCell with minimal boilerplate.

    Parameters
    ----------
    col_span
        Number of columns this cell spans.
    row_span
        Number of rows this cell spans.
    synthetic
        Whether this cell was inserted by a repair step.
    text
        Cell text content. None produces a null-text cell.

    Returns
    -------
    TableCell
        A valid TableCell instance.
    """

    return TableCell(
        col_span=col_span,
        row_span=row_span,
        synthetic=synthetic,
        text=TextUnit(language="en", text=text) if text is not None else None,
    )


def empty_cell() -> TableCell:
    """Create an empty (text=None) TableCell.

    Returns
    -------
    TableCell
        A TableCell with text=None.
    """

    return cell(text=None)


@pytest.fixture()
def empty_edges() -> set[tuple[int, int, int, int]]:
    """Return an empty set of verified table-continuation edges.

    Returns
    -------
    set[tuple[int, int, int, int]]
        Empty edge set.
    """

    return set()


def make_block(
    *,
    block_type: BlockType = BlockType.PARAGRAPH,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    local_code: str | None = None,
    text: str = "placeholder text for block",
    y0: float = 100.0,
    y1: float = 200.0,
) -> Block:
    """Create a minimal Block that passes all Pydantic validators.

    Parameters
    ----------
    block_type
        The visual structure of the block.
    boundary
        The semantic continuity flag for the block.
    local_code
        Optional explicit curriculum code.
    text
        The text content of the block.
    y0
        Top edge of the bounding box in pixels.
    y1
        Bottom edge of the bounding box in pixels.

    Returns
    -------
    Block
        A valid Block instance.
    """

    return Block(
        bbox=[0.0, y0, 100.0, y1],
        block_type=block_type,
        boundary=boundary,
        kind="block",
        local_code=local_code,
        text=TextUnit(language="en", text=text),
    )


def make_caption_block(
    *,
    local_code: str | None = None,
    text: str = "Table 1",
    y0: float = 100.0,
    y1: float = 120.0,
) -> Block:
    """Create a minimal caption Block.

    Parameters
    ----------
    local_code
        Optional explicit curriculum code on the caption.
    text
        The caption text content.
    y0
        Top edge of the bounding box in pixels.
    y1
        Bottom edge of the bounding box in pixels.

    Returns
    -------
    Block
        A valid caption Block instance.
    """

    return make_block(
        block_type=BlockType.CAPTION, local_code=local_code, text=text, y0=y0, y1=y1
    )


def make_page_ir(*, items: list[Block | Table], page_index: int = 0) -> PageIR:
    """Create a minimal PageIR wrapping the given items.

    Parameters
    ----------
    items
        The ordered list of content items on the page.
    page_index
        The 0-based index of the page in the PDF.

    Returns
    -------
    PageIR
        A valid PageIR instance.
    """

    return PageIR(
        image_height=1000, image_width=800, items=items, page_index=page_index
    )


def make_table(
    *,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    header_row_count: int = 1,
    local_code: str | None = None,
    n_cols: int = 3,
    num_body_rows: int = 2,
    repeats_header: bool | None = None,
    y0: float = 200.0,
    y1: float = 800.0,
) -> Table:
    """Create a minimal Table that passes all Pydantic validators.

    Parameters
    ----------
    boundary
        The semantic continuity flag for the table.
    header_row_count
        Number of header rows.
    local_code
        Optional explicit curriculum code.
    n_cols
        Number of columns in the table.
    num_body_rows
        Number of body rows to generate.
    repeats_header
        Whether header rows are visibly repeated (only valid for RESUMED/BOTH).
    y0
        Top edge of the bounding box in pixels.
    y1
        Bottom edge of the bounding box in pixels.

    Returns
    -------
    Table
        A valid Table instance.
    """

    def _row(*, prefix: str) -> TableRow:
        """Create a single table row with placeholder cells.

        Parameters
        ----------
        prefix
            Text prefix used to fill each cell.

        Returns
        -------
        TableRow
            A valid TableRow instance.
        """

        return TableRow(
            cells=[
                TableCell(text=TextUnit(language="en", text=f"{prefix}_c{c}"))
                for c in range(n_cols)
            ]
        )

    rows = [_row(prefix="h")] * header_row_count + [
        _row(prefix=f"r{r}") for r in range(num_body_rows)
    ]

    return Table(
        bbox=[0.0, y0, 100.0, y1],
        boundary=boundary,
        header_row_count=header_row_count,
        kind="table",
        local_code=local_code,
        n_cols=n_cols,
        repeats_header=repeats_header,
        rows=rows,
    )


def make_table_from_rows(
    *, header_row_count: int = 0, n_cols: int | None = None, rows: list[TableRow]
) -> Table:
    """Create a Table from explicit rows.

    Parameters
    ----------
    header_row_count
        Number of header rows.
    n_cols
        Number of intended columns (None to skip column-count validation).
    rows
        The explicit list of TableRow objects.

    Returns
    -------
    Table
        A valid Table instance.
    """

    return Table(
        bbox=[0.0, 100.0, 800.0, 900.0],
        header_row_count=header_row_count,
        kind="table",
        n_cols=n_cols,
        rows=rows,
    )


def row(*cells: TableCell) -> TableRow:
    """Create a TableRow from positional cell arguments.

    Parameters
    ----------
    *cells
        The cells to include in the row.

    Returns
    -------
    TableRow
        A valid TableRow instance.
    """

    return TableRow(cells=list(cells))


class TestAlignTableRowsWithRowspans:
    """Tests for align_table_rows_with_rowspans()."""

    def test_empty_page_irs(self) -> None:
        """Return no changes when page_irs is empty."""

        changes = postprocess_page_irs.align_table_rows_with_rowspans(page_irs={})

        assert not changes

    def test_multiple_tables_across_pages(self) -> None:
        """Process tables on multiple pages independently."""

        table_a = make_table_from_rows(
            n_cols=2,
            rows=[
                row(cell(text="a"), cell(text="a2")),  # Full-width anchor row
                row(cell(text="b")),
                row(cell(text="c")),
            ],
        )
        table_b = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="h1"), cell(text="h2"), cell(text="h3")),
                # Full-width anchor row
                row(cell(text="d")),
            ],
        )
        page_irs = {
            0: make_page_ir(items=[table_a], page_index=0),
            1: make_page_ir(items=[table_b], page_index=1),
        }

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        # table_a: 2 short rows, table_b: 1 short row.
        assert len(changes) == 3

    def test_pages_with_no_tables(self) -> None:
        """Return no changes when pages contain only blocks."""

        page_irs = {0: make_page_ir(items=[make_block()], page_index=0)}
        changes = postprocess_page_irs.align_table_rows_with_rowspans(page_irs=page_irs)

        assert not changes

    def test_single_table_with_rowspan(self) -> None:
        """Process a single table with a rowspan across pages."""

        table = make_table_from_rows(
            n_cols=2,
            rows=[
                row(cell(row_span=2, text="span"), cell(text="b")),
                row(cell(text="d")),
            ],
        )
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.align_table_rows_with_rowspans(page_irs=page_irs)

        assert len(changes) == 1
        assert len(table.rows[1].cells) == 2

    def test_skips_non_table_items(self) -> None:
        """Non-table items on the same page are ignored."""

        table = make_table_from_rows(
            n_cols=2,
            rows=[
                row(cell(text="h1"), cell(text="h2")),  # Full-width anchor row
                row(cell(text="a")),
            ],
        )
        page_irs = {
            0: make_page_ir(items=[make_block(), table, make_block()], page_index=0)
        }

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        assert len(changes) == 1
        assert changes[0]["item_index"] == 1


class TestFindCaptionCode:
    """Tests for find_caption_code()."""

    def test_abbreviated_prefix_tab_dot(self) -> None:
        """Recognize the abbreviated 'tab.' prefix."""

        items: list[Block | Table] = [
            make_caption_block(text="Tab. 12 Résumé"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Tab. 12"

    def test_caption_local_code_non_table_prefix_skipped(self) -> None:
        """Skip a caption whose local_code doesn't match a table prefix pattern."""

        items: list[Block | Table] = [
            make_caption_block(local_code="Figure 2", text="Figure 2: Something"),
        ]

        assert postprocess_page_irs.find_caption_code(items=items) is None

    def test_caption_local_code_takes_priority_over_text(self) -> None:
        """Prefer caption.local_code over parsing caption.text."""

        items: list[Block | Table] = [
            make_caption_block(local_code="Table 1", text="Tableau 99: something"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Table 1"

    def test_caption_text_fallback_dotted_number(self) -> None:
        """Parse a dotted number from caption text (e.g. 'Table 2.1')."""

        items: list[Block | Table] = [
            make_caption_block(text="Table 2.1 Weekly Schedule"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Table 2.1"

    def test_caption_text_fallback_when_no_local_code(self) -> None:
        """Parse the code from caption text when local_code is absent."""

        items: list[Block | Table] = [
            make_caption_block(text="Tableau 5: Résultats"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Tableau 5"

    def test_caption_text_no_number_returns_none(self) -> None:
        """Return None when caption text has a table prefix but no number."""

        items: list[Block | Table] = [
            make_caption_block(text="Table of Contents"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) is None

    def test_caption_with_local_code_matching_table_pattern(self) -> None:
        """Return the local_code when a caption has a matching table-pattern code."""

        items: list[Block | Table] = [
            make_caption_block(local_code="Table 3.2", text="Table 3.2: Overview"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Table 3.2"

    def test_caption_with_non_table_prefix_returns_none(self) -> None:
        """Return None when the caption text uses a figure prefix instead of table."""

        items: list[Block | Table] = [
            make_caption_block(text="Figure 7: Diagram"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) is None

    def test_empty_items_returns_none(self) -> None:
        """Return None when the items list is empty."""

        assert postprocess_page_irs.find_caption_code(items=[]) is None

    def test_nearest_caption_wins_reverse_order(self) -> None:
        """The nearest (last in reading order) matching caption wins."""

        items: list[Block | Table] = [
            make_caption_block(text="Table 1: First", y0=50.0, y1=70.0),
            make_block(text="Some paragraph", y0=80.0, y1=100.0),
            make_caption_block(text="Table 2: Second", y0=110.0, y1=130.0),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Table 2"

    def test_no_captions_returns_none(self) -> None:
        """Return None when no caption blocks exist in items."""

        items: list[Block | Table] = [make_block(text="Some paragraph"), make_table()]
        assert postprocess_page_irs.find_caption_code(items=items) is None

    def test_skips_non_matching_caption_finds_earlier_match(self) -> None:
        """Skip a non-matching caption and find an earlier matching one."""

        items: list[Block | Table] = [
            make_caption_block(text="Table 1: First", y0=50.0, y1=70.0),
            make_caption_block(text="No code here", y0=80.0, y1=100.0),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Table 1"

    def test_swahili_prefix_jedwali(self) -> None:
        """Recognize the Swahili 'jedwali' prefix."""

        items: list[Block | Table] = [
            make_caption_block(text="Jedwali 4 Ratiba ya Wiki"),
        ]
        assert postprocess_page_irs.find_caption_code(items=items) == "Jedwali 4"


class TestGetHeaderEffectiveCols:
    """Tests for _get_header_effective_cols()."""

    def test_col_span_in_header(self) -> None:
        """A header cell with col_span > 1 contributes its full span."""

        rows = [row(cell(col_span=2, text="wide"), cell(text="b"))]
        result = postprocess_page_irs._get_header_effective_cols(
            header_row_count=1, rows=rows
        )

        assert result == 3

    def test_max_across_multiple_header_rows(self) -> None:
        """Return the maximum effective width across multiple header rows."""

        rows = [
            row(cell(text="a"), cell(text="b")),
            row(cell(text="c"), cell(text="d"), cell(text="e")),
            row(cell(text="f")),  # Body row, not counted
        ]
        result = postprocess_page_irs._get_header_effective_cols(
            header_row_count=2, rows=rows
        )

        assert result == 3

    def test_single_header_row(self) -> None:
        """Return the effective column count of a single header row."""

        rows = [
            row(cell(text="a"), cell(text="b"), cell(text="c")),
            row(cell(text="d")),
        ]
        result = postprocess_page_irs._get_header_effective_cols(
            header_row_count=1, rows=rows
        )

        assert result == 3

    def test_zero_header_rows(self) -> None:
        """Return 0 when header_row_count is 0."""

        rows = [row(cell(text="a"), cell(text="b"))]
        result = postprocess_page_irs._get_header_effective_cols(
            header_row_count=0, rows=rows
        )

        assert result == 0


class TestNormalizeEmptyTableCells:
    """Tests for normalize_empty_table_cells()."""

    def test_already_empty_string_no_change(self) -> None:
        """A cell whose text is already '' produces no change record."""

        table = make_table_from_rows(rows=[row(cell(text=""))])
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert not changes

    def test_change_record_has_correct_indices(self) -> None:
        """Change records carry the correct page, item, row, and cell indices."""

        table = make_table_from_rows(rows=[row(cell(text="ok")), row(cell(text="\t"))])
        page_irs = {2: make_page_ir(items=[make_block(), table], page_index=2)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert len(changes) == 1
        assert changes[0]["page"] == 2
        assert changes[0]["item_index"] == 1
        assert changes[0]["row_index"] == 1
        assert changes[0]["cell_index"] == 0

    def test_empty_page_irs(self) -> None:
        """Return no changes when page_irs is empty."""

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs={})

        assert not changes

    def test_multiple_cells_some_normalized(self) -> None:
        """Only whitespace-only cells are normalized; others are left alone."""

        table = make_table_from_rows(
            n_cols=3, rows=[row(cell(text="ok"), cell(text=" "), cell(text="fine"))]
        )
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert len(changes) == 1
        assert changes[0]["cell_index"] == 1

    def test_newline_only_text_normalized(self) -> None:
        """A cell with only newlines is normalized to ''."""

        table = make_table_from_rows(rows=[row(cell(text="\n\n"))])
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert len(changes) == 1
        assert table.rows[0].cells[0].text.text == ""

    def test_no_tables(self) -> None:
        """Return no changes when pages contain only blocks."""

        page_irs = {0: make_page_ir(items=[make_block()], page_index=0)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert not changes

    def test_non_empty_text_preserved(self) -> None:
        """A cell with real content is not modified."""

        table = make_table_from_rows(rows=[row(cell(text="hello"))])
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert not changes
        assert table.rows[0].cells[0].text.text == "hello"

    def test_null_text_cell_no_change(self) -> None:
        """A cell with text=None produces no change record."""

        table = make_table_from_rows(rows=[row(empty_cell())])
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert not changes

    def test_whitespace_only_text_normalized_to_empty(self) -> None:
        """A cell with whitespace-only text is normalized to ''."""

        table = make_table_from_rows(rows=[row(cell(text="  \n  "))])
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_empty_table_cells(page_irs=page_irs)

        assert len(changes) == 1
        assert changes[0]["type"] == "normalize_empty_string_cell_text"
        assert changes[0]["before_text"] == "  \n  "
        assert table.rows[0].cells[0].text.text == ""


class TestNormalizeTableRowCellCounts:
    """Tests for normalize_table_row_cell_counts()."""

    def test_col_span_counted_in_effective_width(self) -> None:
        """A row with col_span cells reaching n_cols is not padded."""

        table = make_table_from_rows(
            n_cols=4,
            rows=[row(cell(col_span=2, text="wide"), cell(text="b"), cell(text="c"))],
        )
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        assert not changes

    def test_empty_page_irs(self) -> None:
        """Return no changes when page_irs is empty."""

        changes = postprocess_page_irs.normalize_table_row_cell_counts(page_irs={})

        assert not changes

    def test_multiple_tables_across_pages(self) -> None:
        """Tables on different pages are processed independently."""

        table_a = make_table_from_rows(
            n_cols=2,
            rows=[
                row(cell(text="a"), cell(text="a2")),
                row(cell(text="b")),
                row(cell(text="c")),
            ],
        )
        table_b = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="h1"), cell(text="h2"), cell(text="h3")),
                row(cell(text="d")),
            ],
        )
        page_irs = {
            0: make_page_ir(items=[table_a], page_index=0),
            1: make_page_ir(items=[table_b], page_index=1),
        }

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        assert len(changes) == 3

    def test_n_cols_none_skipped(self) -> None:
        """Tables with n_cols=None produce no changes."""

        table = make_table_from_rows(n_cols=None, rows=[row(cell())])
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        assert not changes

    def test_no_conflict_keys_defaults_to_empty(self) -> None:
        """When rowspan_conflict_keys is None, no annotations are added."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),
                row(cell(text="d")),
            ],
        )
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs, rowspan_conflict_keys=None
        )

        assert len(changes) == 1
        assert "prior_rowspan_conflict" not in changes[0]

    def test_no_tables(self) -> None:
        """Return no changes when pages contain only blocks."""

        page_irs = {0: make_page_ir(items=[make_block()], page_index=0)}
        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        assert not changes

    def test_pads_short_rows(self) -> None:
        """Short rows are padded to n_cols."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),
                row(cell(text="d")),
            ],
        )
        page_irs = {0: make_page_ir(items=[table], page_index=0)}

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        assert len(changes) == 1
        assert len(table.rows[1].cells) == 3

    def test_rowspan_conflict_keys_passed_through(self) -> None:
        """Rows with rowspan conflict keys are annotated in change records."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),
                row(cell(text="d")),
            ],
        )
        page_irs = {0: make_page_ir(items=[table], page_index=0)}
        conflict_keys: set[tuple[int, int, int]] = {(0, 0, 1)}

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs, rowspan_conflict_keys=conflict_keys
        )

        assert len(changes) == 1
        assert changes[0].get("prior_rowspan_conflict") is True

    def test_skips_non_table_items(self) -> None:
        """Non-table items on the same page are ignored."""

        table = make_table_from_rows(
            n_cols=2, rows=[row(cell(text="h1"), cell(text="h2")), row(cell(text="a"))]
        )
        page_irs = {
            0: make_page_ir(items=[make_block(), table, make_block()], page_index=0)
        }

        changes = postprocess_page_irs.normalize_table_row_cell_counts(
            page_irs=page_irs
        )

        assert len(changes) == 1
        assert changes[0]["item_index"] == 1


class TestProcessTableNormalization:
    """Tests for _process_table_normalization()."""

    def test_already_correct_width_no_changes(self) -> None:
        """Return no changes when all rows already match n_cols."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),
                row(cell(text="d"), cell(text="e"), cell(text="f")),
            ],
        )
        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
        )

        assert not changes

    def test_change_record_indices(self) -> None:
        """Change records carry the correct page, item_index, and row_index."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),
                row(cell(text="d")),
            ],
        )
        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=4, page_index=7, rowspan_conflict_keys=set()
        )

        assert changes[0]["page"] == 7
        assert changes[0]["item_index"] == 4
        assert changes[0]["row_index"] == 1

    def test_mixed_short_and_correct_rows(self) -> None:
        """Only short rows produce changes; correct-width rows are skipped."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),  # Ok
                row(cell(text="d")),  # Short
                row(cell(text="e"), cell(text="f"), cell(text="g")),  # Ok
                row(cell(text="h"), cell(text="i")),  # Short
            ],
        )
        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
        )

        assert len(changes) == 2
        assert changes[0]["row_index"] == 1
        assert changes[1]["row_index"] == 3

    def test_n_cols_none_returns_empty(self) -> None:
        """Return no changes when n_cols is None."""

        table = make_table_from_rows(n_cols=None, rows=[row(cell(), cell())])
        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
        )

        assert not changes

    def test_n_cols_zero_raises(self) -> None:
        """Raise ValueError when n_cols is 0."""

        table = make_table_from_rows(n_cols=3, rows=[row(cell(), cell(), cell())])
        table.n_cols = 0

        with pytest.raises(ValueError):
            postprocess_page_irs._process_table_normalization(
                item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
            )

    def test_overwide_row_recorded_as_exceeds(self) -> None:
        """An over-wide row is recorded but not destructively repaired."""

        table = make_table_from_rows(
            n_cols=None,
            rows=[row(cell(text="a"), cell(text="b"), cell(text="c"))],
        )
        table.n_cols = 2

        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
        )

        assert len(changes) == 1
        assert changes[0]["type"] == "table_row_effective_cols_exceeds_n_cols"
        assert changes[0]["before_effective_cols"] == 3

    def test_overwide_row_trimmed_if_trailing_synthetics(self) -> None:
        """An over-wide row with trailing synthetics gets them trimmed."""

        table = make_table_from_rows(
            n_cols=None,
            rows=[row(cell(text="a"), cell(text="b"), cell(synthetic=True, text=None))],
        )
        table.n_cols = 2

        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
        )

        assert len(changes) == 1
        assert changes[0]["trimmed_trailing_placeholders"] == 1
        assert changes[0]["after_effective_cols"] == 2
        assert len(table.rows[0].cells) == 2

    def test_pads_short_row_left_when_header_full_width(self) -> None:
        """Pad left when the header row covers the full table width."""

        table = make_table_from_rows(
            header_row_count=1,
            n_cols=3,
            rows=[
                row(cell(text="h1"), cell(text="h2"), cell(text="h3")),
                row(cell(text="d"), cell(text="e")),  # Missing 1 cell
            ],
        )
        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
        )

        assert len(changes) == 1
        assert changes[0]["side"] == "left"
        assert changes[0]["side_reason"] == "header_full_width"
        assert table.rows[1].cells[0].synthetic is True
        assert table.rows[1].cells[1].text.text == "d"

    def test_pads_short_row_right_by_default(self) -> None:
        """A short row is right-padded with synthetic cells by default."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),
                row(cell(text="d")),  # Missing 2 cells
            ],
        )
        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=set()
        )

        assert len(changes) == 1
        assert changes[0]["type"] == "pad_table_row_cells"
        assert changes[0]["side"] == "right"
        assert changes[0]["row_index"] == 1
        assert len(table.rows[1].cells) == 3
        assert table.rows[1].cells[1].synthetic is True
        assert table.rows[1].cells[2].synthetic is True

    def test_rowspan_conflict_key_annotated_on_overwide_row(self) -> None:
        """Over-wide row changes on rows with prior rowspan conflicts are annotated."""

        table = make_table_from_rows(
            n_cols=None,
            rows=[row(cell(text="a"), cell(text="b"), cell(text="c"))],
        )
        table.n_cols = 2
        conflict_keys: set[tuple[int, int, int]] = {(5, 2, 0)}

        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=2, page_index=5, rowspan_conflict_keys=conflict_keys
        )

        assert len(changes) == 1
        assert changes[0].get("prior_rowspan_conflict") is True

    def test_rowspan_conflict_key_annotated_on_pad_change(self) -> None:
        """Padding changes on rows with prior rowspan conflicts are annotated."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(text="a"), cell(text="b"), cell(text="c")),
                row(cell(text="d")),  # Short row
            ],
        )
        conflict_keys: set[tuple[int, int, int]] = {(0, 0, 1)}

        changes = postprocess_page_irs._process_table_normalization(
            item=table, item_index=0, page_index=0, rowspan_conflict_keys=conflict_keys
        )

        assert len(changes) == 1
        assert changes[0].get("prior_rowspan_conflict") is True


class TestProcessPageTables:
    """Tests for _process_page_tables()."""

    def test_carry_applied_only_to_first_resumed_table(self) -> None:
        """The carried code is only applied to the first resumed table on the page;
        subsequent resumed tables do not receive it.
        """

        table_a = make_table(
            boundary=ItemBoundary.RESUMED, repeats_header=False, y0=200.0, y1=400.0
        )
        table_b = make_table(
            boundary=ItemBoundary.RESUMED, repeats_header=False, y0=500.0, y1=800.0
        )
        changes: list[dict[str, Any]] = []

        postprocess_page_irs._process_page_tables(
            carry_from_prev="Table 1",
            changes=changes,
            items=[(0, table_a), (1, table_b)],
            page_idx=5,
            resumed_table_keys={(5, 0), (5, 1)},
            truncated_table_keys=set(),
        )

        assert table_a.local_code == "Table 1"
        assert table_b.local_code is None

    def test_last_truncated_table_wins(self) -> None:
        """When multiple tables are truncated, the last in reading order wins."""

        table_a = make_table(
            boundary=ItemBoundary.TRUNCATED, local_code="Table A", y0=200.0, y1=400.0
        )
        table_b = make_table(
            boundary=ItemBoundary.TRUNCATED, local_code="Table B", y0=500.0, y1=800.0
        )

        result = postprocess_page_irs._process_page_tables(
            carry_from_prev=None,
            changes=[],
            items=[(0, table_a), (1, table_b)],
            page_idx=0,
            resumed_table_keys=set(),
            truncated_table_keys={(0, 0), (0, 1)},
        )

        assert result == "Table B"

    def test_no_tables_returns_none(self) -> None:
        """Return None carry when the page has no tables."""

        result = postprocess_page_irs._process_page_tables(
            carry_from_prev=None,
            changes=[],
            items=[(0, make_block())],
            page_idx=0,
            resumed_table_keys=set(),
            truncated_table_keys=set(),
        )
        assert result is None

    def test_no_truncated_table_returns_none(self) -> None:
        """Return None when no table on the page is truncated."""

        table = make_table(local_code="Table 1")
        result = postprocess_page_irs._process_page_tables(
            carry_from_prev=None,
            changes=[],
            items=[(0, table)],
            page_idx=0,
            resumed_table_keys=set(),
            truncated_table_keys=set(),
        )

        assert result is None

    def test_resumed_and_truncated_same_table(self) -> None:
        """A table that is both resumed and truncated (BOTH boundary) receives the
        carry and also forwards its code.
        """

        table = make_table(boundary=ItemBoundary.BOTH, repeats_header=False)
        changes: list[dict[str, Any]] = []

        result = postprocess_page_irs._process_page_tables(
            carry_from_prev="Table 5",
            changes=changes,
            items=[(0, table)],
            page_idx=3,
            resumed_table_keys={(3, 0)},
            truncated_table_keys={(3, 0)},
        )

        assert table.local_code == "Table 5"
        assert result == "Table 5"

    def test_resumed_table_receives_carried_code(self) -> None:
        """A resumed table with no code receives the carried code from the previous page."""

        table = make_table(boundary=ItemBoundary.RESUMED, repeats_header=False)
        changes: list[dict[str, Any]] = []
        postprocess_page_irs._process_page_tables(
            carry_from_prev="Table 1",
            changes=changes,
            items=[(0, table)],
            page_idx=5,
            resumed_table_keys={(5, 0)},
            truncated_table_keys=set(),
        )

        assert table.local_code == "Table 1"
        assert len(changes) == 1
        assert changes[0]["type"] == "propagate_table_local_code"

    def test_resumed_table_with_conflicting_code_logs_conflict(self) -> None:
        """A resumed table whose code differs from the carry logs a conflict and keeps
        its existing code.
        """

        table = make_table(
            boundary=ItemBoundary.RESUMED, local_code="Table 2", repeats_header=False
        )
        changes: list[dict[str, Any]] = []

        postprocess_page_irs._process_page_tables(
            carry_from_prev="Table 1",
            changes=changes,
            items=[(0, table)],
            page_idx=5,
            resumed_table_keys={(5, 0)},
            truncated_table_keys=set(),
        )

        assert table.local_code == "Table 2"
        assert len(changes) == 1
        assert changes[0]["type"] == "propagate_table_local_code_conflict"

    def test_resumed_table_with_matching_code_consumes_carry(self) -> None:
        """A resumed table whose code matches the carry consumes it without changes."""

        table = make_table(
            boundary=ItemBoundary.RESUMED, local_code="Table 1", repeats_header=False
        )
        changes: list[dict[str, Any]] = []

        postprocess_page_irs._process_page_tables(
            carry_from_prev="Table 1",
            changes=changes,
            items=[(0, table)],
            page_idx=5,
            resumed_table_keys={(5, 0)},
            truncated_table_keys=set(),
        )

        assert table.local_code == "Table 1"
        assert len(changes) == 0

    def test_truncated_table_carries_code_forward(self) -> None:
        """A truncated table's code is carried forward to the next page."""

        table = make_table(boundary=ItemBoundary.TRUNCATED, local_code="Table 3")
        result = postprocess_page_irs._process_page_tables(
            carry_from_prev=None,
            changes=[],
            items=[(0, table)],
            page_idx=0,
            resumed_table_keys=set(),
            truncated_table_keys={(0, 0)},
        )

        assert result == "Table 3"

    def test_truncated_table_without_code_carries_none(self) -> None:
        """A truncated table with no code carries None (clears any prior carry)."""

        table = make_table(boundary=ItemBoundary.TRUNCATED)

        result = postprocess_page_irs._process_page_tables(
            carry_from_prev=None,
            changes=[],
            items=[(0, table)],
            page_idx=0,
            resumed_table_keys=set(),
            truncated_table_keys={(0, 0)},
        )

        assert result is None


class TestProcessTableItem:
    """Tests for _process_table_item()."""

    def test_change_records_include_page_and_item(self) -> None:
        """Change records include the correct page and item_index."""

        table = make_table_from_rows(
            n_cols=2,
            rows=[
                row(cell(row_span=2, text="span"), cell(text="b")),
                row(cell(text="d")),
            ],
        )

        changes = postprocess_page_irs._process_table_item(
            item=table, item_index=3, page_index=7
        )

        assert changes[0]["page"] == 7
        assert changes[0]["item_index"] == 3
        assert changes[0]["row_index"] == 1

    def test_multi_row_span_propagates_across_rows(self) -> None:
        """A row_span=3 cell propagates placeholders across two subsequent rows."""

        table = make_table_from_rows(
            n_cols=2,
            rows=[
                row(cell(row_span=3, text="span"), cell(text="b")),
                row(cell(text="d")),  # Needs placeholder at col 0
                row(cell(text="f")),  # Needs placeholder at col 0
            ],
        )

        changes = postprocess_page_irs._process_table_item(
            item=table, item_index=0, page_index=0
        )

        assert len(changes) == 2
        assert all(
            c["type"] == "rowspan_alignment_inserted_placeholders" for c in changes
        )
        assert len(table.rows[1].cells) == 2
        assert len(table.rows[2].cells) == 2

    def test_n_cols_zero_raises(self) -> None:
        """Return no changes when n_cols is 0 (invalid but handled gracefully)."""

        # n_cols must be >= 1 per schema, so we bypass by setting after construction.
        table = make_table_from_rows(n_cols=3, rows=[row(cell(), cell(), cell())])
        table.n_cols = 0

        with pytest.raises(ValueError):
            postprocess_page_irs._process_table_item(
                item=table, item_index=0, page_index=0
            )

    def test_no_n_cols_returns_empty(self) -> None:
        """Return no changes when n_cols is None."""

        table = make_table_from_rows(n_cols=None, rows=[row(cell(), cell())])

        changes = postprocess_page_irs._process_table_item(
            item=table, item_index=0, page_index=0
        )

        assert not changes

    def test_no_rowspans_no_changes(self) -> None:
        """A table with no rowspans produces no changes."""

        table = make_table_from_rows(
            n_cols=2,
            rows=[
                row(cell(text="a"), cell(text="b")),
                row(cell(text="c"), cell(text="d")),
            ],
        )

        changes = postprocess_page_irs._process_table_item(
            item=table, item_index=0, page_index=0
        )

        assert not changes

    def test_simple_rowspan_inserts_placeholder(self) -> None:
        """A row_span=2 cell in row 0 causes a placeholder insertion in row 1."""

        table = make_table_from_rows(
            n_cols=3,
            rows=[
                row(cell(row_span=2, text="span"), cell(text="b"), cell(text="c")),
                row(cell(text="d"), cell(text="e")),  # Missing col 0 placeholder
            ],
        )

        changes = postprocess_page_irs._process_table_item(
            item=table, item_index=0, page_index=0
        )

        assert len(changes) == 1
        assert changes[0]["type"] == "rowspan_alignment_inserted_placeholders"
        assert changes[0]["row_index"] == 1
        assert len(table.rows[1].cells) == 3


class TestProcessTableRow:
    """Tests for _process_table_row()."""

    def test_already_aligned_returns_none(self) -> None:
        """Return None when the row is already aligned with no active spans."""

        active_span = [0, 0, 0]
        row_ = row(cell(), cell(), cell())

        result = postprocess_page_irs._process_table_row(
            active_span=active_span, n_cols=3, row=row_
        )

        assert result is None

    def test_inserts_placeholders_and_mutates_row(self) -> None:
        """Insert placeholders and mutate row.cells when alignment is needed."""

        active_span = [2, 0, 0]
        row_ = row(cell(text="a"), cell(text="b"))

        result = postprocess_page_irs._process_table_row(
            active_span=active_span, n_cols=3, row=row_
        )

        assert result is not None
        assert result["type"] == "rowspan_alignment_inserted_placeholders"
        assert result["before_cells"] == 2
        assert result["after_cells"] == 3
        assert row_.cells[0].synthetic is True
        assert row_.cells[1].text.text == "a"

    def test_inserts_placeholders_updates_active_span(self) -> None:
        """Active span is updated in-place after successful placeholder insertion."""

        active_span = [2, 0, 0]
        row_ = row(cell(row_span=2, text="a"), cell(text="b"))

        postprocess_page_irs._process_table_row(
            active_span=active_span, n_cols=3, row=row_
        )

        # After decrement by caller: active_span should reflect the placement.
        # Cell "a" at col 1 with row_span=2 -> active_span[1] = 2.
        assert active_span[1] == 2

    def test_overflow_conflict_preserves_original_row(self) -> None:
        """On overflow conflict, the original row is preserved."""

        active_span = [2, 0, 0]
        original_cells = [cell(text="a"), cell(text="b"), cell(text="c")]
        row_ = row(*original_cells)

        result = postprocess_page_irs._process_table_row(
            active_span=active_span, n_cols=3, row=row_
        )

        assert result is not None
        assert result["type"] == "rowspan_alignment_conflict_overflow"
        assert len(row_.cells) == 3  # Original preserved.
        assert row_.cells[0].text.text == "a"

    def test_overflow_conflict_still_advances_spans(self) -> None:
        """On overflow conflict, active_span is still advanced for the original row."""

        active_span = [0, 0, 0]
        row_ = row(cell(row_span=4, text="big"), cell(text="b"), cell(text="c"))

        # First process normally (no conflict here).
        postprocess_page_irs._process_table_row(
            active_span=active_span, n_cols=3, row=row_
        )

        # active_span[0] should be 4 from the row_span=4 cell.
        assert active_span[0] == 4


class TestPropagateTableLocalCodes:
    """Tests for propagate_table_local_codes()."""

    def test_artifact_items_are_skipped(self) -> None:
        """Artifact blocks are filtered out and do not interfere with propagation."""

        page_irs = {
            0: make_page_ir(
                items=[
                    make_block(
                        block_type=BlockType.ARTIFACT, text="42", y0=950.0, y1=980.0
                    ),
                    make_table(
                        boundary=ItemBoundary.TRUNCATED,
                        local_code="Table 1",
                        y0=200.0,
                        y1=900.0,
                    ),
                ],
                page_index=0,
            ),
            1: make_page_ir(
                items=[
                    make_block(
                        block_type=BlockType.ARTIFACT, text="43", y0=10.0, y1=40.0
                    ),
                    make_table(
                        boundary=ItemBoundary.RESUMED,
                        repeats_header=False,
                        y0=50.0,
                        y1=800.0,
                    ),
                ],
                page_index=1,
            ),
        }
        edges = {(0, 1, 1, 1)}

        postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        assert page_irs[1].items[1].local_code == "Table 1"

    def test_caption_seeds_code_for_resumed_table_without_code(self) -> None:
        """A caption before a resumed table with no code seeds the carry."""

        page_irs = {
            0: make_page_ir(
                items=[make_table(boundary=ItemBoundary.TRUNCATED)], page_index=0
            ),
            1: make_page_ir(
                items=[
                    make_caption_block(text="Table 7: Results", y0=50.0, y1=70.0),
                    make_table(
                        boundary=ItemBoundary.RESUMED,
                        repeats_header=False,
                        y0=80.0,
                        y1=400.0,
                    ),
                ],
                page_index=1,
            ),
        }
        edges = {(0, 0, 1, 1)}

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        assert page_irs[1].items[1].local_code == "Table 7"

        propagate_changes = [
            c for c in changes if c["type"] == "propagate_table_local_code"
        ]

        assert len(propagate_changes) == 1

    def test_chain_propagation_across_three_pages(self) -> None:
        """Code propagates across a three-page table span."""

        page_irs = {
            0: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.TRUNCATED, local_code="Table 1"),
                ],
                page_index=0,
            ),
            1: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.BOTH, repeats_header=False),
                ],
                page_index=1,
            ),
            2: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.RESUMED, repeats_header=False),
                ],
                page_index=2,
            ),
        }
        edges = {(0, 0, 1, 0), (1, 0, 2, 0)}

        postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        assert page_irs[1].items[0].local_code == "Table 1"
        assert page_irs[2].items[0].local_code == "Table 1"

    def test_conflict_logged_when_codes_differ(self) -> None:
        """A conflict is logged when the resumed table has a different code."""

        page_irs = {
            0: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.TRUNCATED, local_code="Table 1"),
                ],
                page_index=0,
            ),
            1: make_page_ir(
                items=[
                    make_table(
                        boundary=ItemBoundary.RESUMED,
                        local_code="Table 99",
                        repeats_header=False,
                    ),
                ],
                page_index=1,
            ),
        }
        edges = {(0, 0, 1, 0)}

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        conflict_changes = [
            c for c in changes if c["type"] == "propagate_table_local_code_conflict"
        ]

        assert len(conflict_changes) == 1
        assert page_irs[1].items[0].local_code == "Table 99"

    def test_does_not_overwrite_existing_code(self) -> None:
        """A resumed table that already has a code is not overwritten."""

        page_irs = {
            0: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.TRUNCATED, local_code="Table 1"),
                ],
                page_index=0,
            ),
            1: make_page_ir(
                items=[
                    make_table(
                        boundary=ItemBoundary.RESUMED,
                        local_code="Table 1",
                        repeats_header=False,
                    ),
                ],
                page_index=1,
            ),
        }
        edges = {(0, 0, 1, 0)}

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        assert page_irs[1].items[0].local_code == "Table 1"

        propagate_changes = [
            c for c in changes if c["type"] == "propagate_table_local_code"
        ]

        assert len(propagate_changes) == 0

    def test_empty_page_irs_returns_no_changes(self, empty_edges: set) -> None:
        """No changes when page_irs is empty."""

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs={}, verified_table_continuation_edges=empty_edges
        )

        assert not changes

    def test_no_edges_no_changes(self, empty_edges: set) -> None:
        """No changes when there are no verified table edges."""

        page_irs = {
            0: make_page_ir(items=[make_table(local_code="Table 1")], page_index=0),
        }

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=empty_edges
        )

        assert not changes

    def test_no_resumed_table_on_page_drops_carry(self) -> None:
        """Carry is dropped when the next page has no VERIFIED-resumed tables."""

        page_irs = {
            0: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.TRUNCATED, local_code="Table 1"),
                ],
                page_index=0,
            ),
            # COMPLETE, not resumed.
            1: make_page_ir(items=[make_table()], page_index=1),
        }

        # Edge exists but the table on page 1 is not in resumed_table_keys because
        # it references item 0 on page 1, which is COMPLETE. The edge set references
        # specific items; the propagation checks resumed_table_keys.
        edges: set[tuple[int, int, int, int]] = set()

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        drop_changes = [
            c
            for c in changes
            if c["type"] == "propagate_table_local_code_dropped_no_resumed_table"
        ]

        # No edge means no carry is established from page 0, so no drop either.
        assert page_irs[1].items[0].local_code is None
        assert len(drop_changes) == 0

    def test_page_gap_drops_carry(self) -> None:
        """A non-contiguous page gap drops the carried code."""

        page_irs = {
            0: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.TRUNCATED, local_code="Table 1"),
                ],
                page_index=0,
            ),
            # Page 1 is missing -> gap.
            2: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.RESUMED, repeats_header=False),
                ],
                page_index=2,
            ),
        }
        edges = {(0, 0, 2, 0)}

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        # The gap should have dropped the carry, so the table on page 2 should NOT
        # receive the code via the carry mechanism.
        gap_drops = [
            c
            for c in changes
            if c["type"] == "propagate_table_local_code_dropped_due_to_page_gap"
        ]

        assert len(gap_drops) == 1

    def test_single_edge_propagates_code_to_resumed_table(self) -> None:
        """A code on the truncated table propagates to the resumed table on the next
        page.
        """

        page_irs = {
            0: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.TRUNCATED, local_code="Table 1"),
                ],
                page_index=0,
            ),
            1: make_page_ir(
                items=[
                    make_table(boundary=ItemBoundary.RESUMED, repeats_header=False),
                ],
                page_index=1,
            ),
        }
        edges = {(0, 0, 1, 0)}

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=edges
        )

        assert page_irs[1].items[0].local_code == "Table 1"

        propagate_changes = [
            c for c in changes if c["type"] == "propagate_table_local_code"
        ]

        assert len(propagate_changes) == 1

    def test_single_page_no_edges_returns_no_changes(self, empty_edges: set) -> None:
        """No changes when there is only one page (no boundaries to propagate across)."""

        page_irs = {
            0: make_page_ir(items=[make_table(local_code="Table 1")], page_index=0),
        }

        changes = postprocess_page_irs.propagate_table_local_codes(
            page_irs=page_irs, verified_table_continuation_edges=empty_edges
        )

        assert not changes


class TestShouldPadLeft:
    """Tests for _should_pad_left()."""

    def test_default_right_when_header_narrower_than_n_cols(self) -> None:
        """Return False when header is narrower than n_cols and no modal signal."""

        rows = [
            row(cell(text="a"), cell(text="b")),  # Header: 2 cols
            row(cell(text="c"), cell(text="d"), cell(text="e")),
            row(cell(text="f"), cell(text="g"), cell(text="h")),
            row(cell(text="i"), cell(text="j"), cell(text="k")),
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=1, n_cols=3, rows=rows
        )

        assert result is False

    def test_default_right_when_no_headers_and_no_modal_signal(self) -> None:
        """Return False when there are no headers and no modal leading-blank signal."""

        rows = [
            row(cell(text="a"), cell(text="b"), cell(text="c")),
            row(cell(text="d"), cell(text="e"), cell(text="f")),
            row(cell(text="g"), cell(text="h"), cell(text="i")),
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=0, n_cols=3, rows=rows
        )

        assert result is False

    def test_header_full_width_triggers_left_pad(self) -> None:
        """Return True when the header row covers the full n_cols width."""

        rows = [
            row(cell(text="h1"), cell(text="h2"), cell(text="h3")),  # Full width
            row(cell(text="a"), cell(text="b")),  # Short body row
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=1, n_cols=3, rows=rows
        )

        assert result is True

    def test_ignores_synthetic_leading_placeholders_for_modal(self) -> None:
        """Synthetic leading cells are excluded from modal leading-blank analysis."""

        # All body rows are full-width but their leading cell is synthetic (from
        # rowspan repair). These should NOT count toward the modal leading-blank signal
        # because they are repair artifacts.
        body_rows = [
            row(cell(synthetic=True, text=None), cell(text="b"), cell(text="c"))
            for _ in range(5)
        ]
        rows = body_rows
        result = postprocess_page_irs._should_pad_left(
            header_row_count=0, n_cols=3, rows=rows
        )

        assert result is False

    def test_modal_leading_blank_below_threshold_returns_false(self) -> None:
        """Return False when leading-blank ratio is below 60%."""

        # 1 out of 4 full-width rows = 25% < 60%.
        rows = [
            row(empty_cell(), cell(text="b"), cell(text="c")),
            row(cell(text="d"), cell(text="e"), cell(text="f")),
            row(cell(text="g"), cell(text="h"), cell(text="i")),
            row(cell(text="j"), cell(text="k"), cell(text="l")),
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=0, n_cols=3, rows=rows
        )

        assert result is False

    def test_modal_leading_blank_triggers_left_pad(self) -> None:
        """Return True when >= 60% of full-width body rows have a blank leading cell."""

        # 4 out of 5 full-width body rows have blank leading cell = 80% > 60%.
        rows = [
            row(empty_cell(), cell(text="b"), cell(text="c")),
            row(empty_cell(), cell(text="e"), cell(text="f")),
            row(empty_cell(), cell(text="h"), cell(text="i")),
            row(empty_cell(), cell(text="k"), cell(text="l")),
            row(cell(text="m"), cell(text="n"), cell(text="o")),
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=0, n_cols=3, rows=rows
        )

        assert result is True

    def test_modal_requires_minimum_three_full_width_rows(self) -> None:
        """Return False when fewer than 3 full-width body rows exist, even if all have
        blank leading cells.
        """

        rows = [
            row(empty_cell(), cell(text="b"), cell(text="c")),
            row(empty_cell(), cell(text="e"), cell(text="f")),
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=0, n_cols=3, rows=rows
        )

        assert result is False

    def test_no_full_width_body_rows_returns_false(self) -> None:
        """Return False when no body rows reach n_cols."""

        rows = [
            row(cell(text="h1"), cell(text="h2")),  # Header, not full
            row(cell(text="a")),  # body, not full
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=1, n_cols=3, rows=rows
        )

        assert result is False

    def test_whitespace_only_text_counts_as_blank(self) -> None:
        """A leading cell with whitespace-only text counts as blank for modal."""

        rows = [
            row(cell(text="  "), cell(text="b"), cell(text="c")),
            row(cell(text="\n"), cell(text="e"), cell(text="f")),
            row(cell(text="\t"), cell(text="h"), cell(text="i")),
            row(cell(text="j"), cell(text="k"), cell(text="l")),
        ]
        result = postprocess_page_irs._should_pad_left(
            header_row_count=0, n_cols=3, rows=rows
        )

        assert result is True


class TestSimulateRowspanAlignment:
    """Tests for _simulate_rowspan_alignment()."""

    def test_already_aligned_no_active_spans(self) -> None:
        """A row with no active spans and correct cell count is already aligned."""

        cells = [cell(), cell(), cell()]
        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[0, 0, 0], cells=cells, n_cols=3
        )

        assert result["status"] == "already_aligned"
        assert result["inserted_placeholders"] == 0

    def test_empty_cells_all_occupied(self) -> None:
        """All columns occupied -> all placeholders, no original cells to place."""

        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[2, 2, 2], cells=[], n_cols=3
        )

        assert result["status"] == "needs_placeholders"
        assert result["inserted_placeholders"] == 3

    def test_needs_placeholders_single_occupied_column(self) -> None:
        """Insert a placeholder when column 0 is occupied by a prior rowspan."""

        cells = [cell(text="a"), cell(text="b")]
        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[2, 0, 0], cells=cells, n_cols=3
        )

        assert result["status"] == "needs_placeholders"
        assert result["inserted_placeholders"] == 1
        assert len(result["proposed_cells"]) == 3
        assert result["proposed_cells"][0].synthetic is True
        assert result["proposed_cells"][1].text.text == "a"
        assert result["proposed_cells"][2].text.text == "b"

    def test_needs_placeholders_two_occupied_columns(self) -> None:
        """Insert two placeholders when columns 0 and 1 are occupied."""

        cells = [cell(text="c")]
        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[3, 2, 0], cells=cells, n_cols=3
        )

        assert result["status"] == "needs_placeholders"
        assert result["inserted_placeholders"] == 2
        assert result["proposed_cells"][2].text.text == "c"

    def test_overflow_conflict_col_span_exceeds(self) -> None:
        """Overflow conflict when a cell's col_span pushes past n_cols."""

        cells = [cell(col_span=2, text="wide")]
        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[0, 0, 0], cells=cells, n_cols=1
        )

        assert result["status"] == "overflow_conflict"

    def test_overflow_conflict_too_many_cells(self) -> None:
        """Overflow conflict when original cells cannot fit after placeholders."""

        cells = [cell(text="a"), cell(text="b"), cell(text="c")]
        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[2, 0, 0], cells=cells, n_cols=3
        )

        assert result["status"] == "overflow_conflict"
        assert result["overflow_original_cells"] > 0
        assert result["proposed_cells"] is None

    def test_trailing_placeholders_for_occupied_tail(self) -> None:
        """Trailing placeholders are inserted when tail columns are occupied."""

        cells = [cell(text="a")]
        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[0, 3, 3], cells=cells, n_cols=3
        )

        assert result["status"] == "needs_placeholders"
        assert result["inserted_placeholders"] == 2
        assert len(result["proposed_cells"]) == 3

    def test_updated_active_span_reflects_new_rowspans(self) -> None:
        """The updated_active_span reflects rowspans introduced by the current row."""

        cells = [cell(row_span=3, text="span"), cell(text="b"), cell(text="c")]
        result = postprocess_page_irs._simulate_rowspan_alignment(
            active_span=[0, 0, 0], cells=cells, n_cols=3
        )

        assert result["status"] == "already_aligned"
        assert result["updated_active_span"][0] == 3


class TestTrimExcessCells:
    """Tests for _trim_excess_cells()."""

    def test_col_span_respected_in_effective_count(self) -> None:
        """Effective column count respects col_span when deciding whether to trim."""

        # effective = 2 + 1 + 1 = 4, n_cols = 3. Tail is synthetic -> trim 1.
        cells = [
            cell(col_span=2, text="wide"),
            cell(text="b"),
            cell(synthetic=True, text=None),
        ]
        trimmed = postprocess_page_irs._trim_excess_cells(n_cols=3, new_cells=cells)

        assert trimmed == 1
        assert len(cells) == 2

    def test_does_not_trim_non_synthetic_cells(self) -> None:
        """Non-synthetic trailing cells are never trimmed even if row overflows."""

        cells = [cell(text="a"), cell(text="b"), cell(text="c"), cell(text="d")]
        trimmed = postprocess_page_irs._trim_excess_cells(n_cols=3, new_cells=cells)

        assert trimmed == 0
        assert len(cells) == 4

    def test_no_trim_when_at_n_cols(self) -> None:
        """No trimming when effective columns already equals n_cols."""

        cells = [cell(text="a"), cell(text="b"), cell(text="c")]
        trimmed = postprocess_page_irs._trim_excess_cells(n_cols=3, new_cells=cells)

        assert trimmed == 0
        assert len(cells) == 3

    def test_no_trim_when_under_n_cols(self) -> None:
        """No trimming when effective columns is below n_cols."""

        cells = [cell(text="a"), cell(text="b")]
        trimmed = postprocess_page_irs._trim_excess_cells(n_cols=3, new_cells=cells)

        assert trimmed == 0
        assert len(cells) == 2

    def test_stops_at_first_non_synthetic_tail(self) -> None:
        """Trimming stops when the tail cell is not synthetic."""

        cells = [
            cell(text="a"),
            cell(text="b"),
            cell(text="real"),
            cell(synthetic=True, text=None),
        ]
        trimmed = postprocess_page_irs._trim_excess_cells(n_cols=3, new_cells=cells)

        # Trims 1 synthetic, then hits "real" and stops. Still 3 cells, effective=3.
        assert trimmed == 1
        assert len(cells) == 3

    def test_trims_multiple_trailing_synthetics(self) -> None:
        """Multiple trailing synthetic cells are trimmed until n_cols is reached."""

        cells = [
            cell(text="a"),
            cell(text="b"),
            cell(text="c"),
            cell(synthetic=True, text=None),
            cell(synthetic=True, text=None),
        ]
        trimmed = postprocess_page_irs._trim_excess_cells(n_cols=3, new_cells=cells)

        assert trimmed == 2
        assert len(cells) == 3

    def test_trims_only_enough_synthetics(self) -> None:
        """Only trim enough synthetic cells to reach n_cols, leaving extras."""

        cells = [
            cell(text="a"),
            cell(text="b"),
            cell(synthetic=True, text=None),
            cell(synthetic=True, text=None),
            cell(synthetic=True, text=None),
        ]
        trimmed = postprocess_page_irs._trim_excess_cells(n_cols=3, new_cells=cells)

        assert trimmed == 2
        assert len(cells) == 3


class TestUpdateActiveSpan:
    """Tests for _update_active_span()."""

    def test_col_span_2_row_span_3(self) -> None:
        """A cell spanning 2 columns with row_span=3 sets both columns."""

        active_span = [0, 0, 0, 0]

        postprocess_page_irs._update_active_span(
            active_span=active_span, col=1, col_span=2, n_cols=4, row_span=3
        )

        assert active_span == [0, 3, 3, 0]

    def test_does_not_overflow_past_n_cols(self) -> None:
        """Columns beyond n_cols are silently ignored."""

        active_span = [0, 0, 0]

        postprocess_page_irs._update_active_span(
            active_span=active_span,
            col=2,
            col_span=3,
            n_cols=3,
            row_span=2,
        )

        assert active_span == [0, 0, 2]

    def test_preserves_higher_existing_span(self) -> None:
        """When active_span already has a higher value, it is preserved (max)."""

        active_span = [0, 5, 0]

        postprocess_page_irs._update_active_span(
            active_span=active_span,
            col=1,
            col_span=1,
            n_cols=3,
            row_span=2,
        )

        assert active_span == [0, 5, 0]

    def test_row_span_1_does_not_modify(self) -> None:
        """A cell with row_span=1 should not modify the active span."""

        active_span = [0, 0, 0]

        postprocess_page_irs._update_active_span(
            active_span=active_span, col=0, col_span=1, n_cols=3, row_span=1
        )

        assert active_span == [0, 0, 0]

    def test_row_span_2_sets_span(self) -> None:
        """A cell with row_span=2 should set active_span for its column."""

        active_span = [0, 0, 0]

        postprocess_page_irs._update_active_span(
            active_span=active_span,
            col=1,
            col_span=1,
            n_cols=3,
            row_span=2,
        )

        assert active_span == [0, 2, 0]
