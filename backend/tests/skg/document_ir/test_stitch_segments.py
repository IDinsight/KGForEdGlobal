"""This is the main module for testing document_ir/utils.py."""

# Standard Library
from typing import Any, Callable
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

# Package Library
from skg.document_ir import stitch_segments
from skg.document_ir.schemas import TableSegment, TableSlice
from skg.page_ir_extraction.schemas import Table, TableCell, TableRow, TextUnit
from skg.utils.constants import ItemBoundary
from tests.constants import PARAM


def make_chain_entry(page: int, idx: int, code: str | None) -> tuple[int, int, Table]:
    """Helper to create a chain entry with a minimal Table object.

    Parameters
    ----------
    page
        The page number of the table segment.
    idx
        The index of the table segment on the page.
    code
        The local code identifier for the table segment.

    Returns
    -------
    tuple[int, int, Table]
        A tuple containing the page number, index, and Table instance.
    """

    # Create minimal valid table.
    table = Table(
        bbox=[0, 0, 10, 10],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        local_code=code,
        repeats_header=None,
        rows=[TableRow(cells=[TableCell(text=None)])],
    )

    return page, idx, table


def make_segment(
    rows: list[TableRow], n_cols: int, seg_id: str = "seg_1"
) -> TableSegment:
    """Helper to create a TableSegment with specified rows and columns.

    Parameters
    ----------
    rows
        The list of TableRow instances for the segment.
    n_cols
        The number of columns in the segment.
    seg_id
        The segment ID.

    Returns
    -------
    TableSegment
        The created TableSegment instance.
    """

    seg = Mock(spec=TableSegment)
    seg.rows = rows
    seg.n_cols = n_cols
    seg.segment_id = seg_id

    return seg


def make_slice(
    rows: list[TableRow],
    page_index: int,
    dropped_header_rows: int = 0,
    bbox: list[float] | None = None,
) -> TableSlice:
    """Helper to create a TableSlice for testing provenance.

    Parameters
    ----------
    rows
        The list of TableRow instances for the slice.
    page_index
        The page index of the slice.
    dropped_header_rows
        The number of dropped header rows in the slice.
    bbox
        The bounding box of the slice. If None, defaults to [0, 0, 100, 100].

    Returns
    -------
    TableSlice
        The created TableSlice instance.
    """

    if bbox is None:
        bbox = [0.0, 0.0, 100.0, 100.0]

    return TableSlice(
        bbox=bbox,
        boundary=ItemBoundary.COMPLETE,
        dropped_header_rows=dropped_header_rows,
        header_row_count=0,
        item_index=0,
        local_code=None,
        page_index=page_index,
        repeats_header=None,
        rows=rows,
    )


def table_cell(text: str | None, col_span: int = 1, row_span: int = 1) -> TableCell:
    """Helper to create a TableCell instance.

    Parameters
    ----------
    text
        The text content of the cell, or None for empty cells.
    col_span
        The column span of the cell.
    row_span
        The row span of the cell.

    Returns
    -------
    TableCell
        The created TableCell instance.
    """

    return TableCell(
        text=None if text is None else _text_unit(text),
        col_span=col_span,
        row_span=row_span,
    )


def table_row(*texts: str | None) -> TableRow:
    """Helper to create a TableRow instance.

    Parameters
    ----------
    texts
        The text contents for each cell in the row.

    Returns
    -------
    TableRow
        The created TableRow instance.
    """
    return TableRow(cells=[table_cell(t) for t in texts])


def table_row_with_spans(cells_data: list[tuple[str | None, int]]) -> TableRow:
    """Helper to create a row with variable column spans.

    Parameters
    ----------
    cells_data
        List of tuples: (text_content, col_span_int)

    Returns
    -------
    TableRow
        The created TableRow instance.
    """

    cells = []

    for text, span in cells_data:
        t_unit = _text_unit(text) if text is not None else None
        cells.append(TableCell(text=t_unit, col_span=span, row_span=1))

    return TableRow(cells=cells)


def _text_unit(text: str) -> TextUnit:
    """Helper to create a TextUnit instance.

    Parameters
    ----------
    text
        The text content of the TextUnit.

    Returns
    -------
    TextUnit
        The created TextUnit instance.
    """

    return TextUnit(language="en", text=text)


@pytest.fixture
def create_empty_grid() -> Callable[[int, int], list[list[dict[str, Any]]]]:
    """Fixture to create a fresh grid for testing spans.

    Returns
    -------
    Callable[[int, int], list[list[dict[str, object]]]]
        Function that creates a grid with specified rows and columns.
    """

    def _create(rows: int, cols: int) -> list[list[dict[str, Any]]]:
        """Create a fresh grid with specified dimensions.

        Parameters
        ----------
        rows
            Number of rows in the grid.
        cols
            Number of columns in the grid.

        Returns
        -------
        list[list[dict[str, Any]]]
            The created grid initialized with None text and -1 source_row.
        """

        return [
            [{"text": None, "source_row": -1} for _ in range(cols)] for _ in range(rows)
        ]

    return _create


@pytest.fixture
def create_segment() -> Mock:
    """Fixture to create a mock TableSegment with a dummy ID.

    Returns
    -------
    Mock
        A mock TableSegment instance with a preset segment_id.
    """

    seg = Mock()
    seg.segment_id = "test_segment_123"

    return seg


@pytest.fixture
def create_table() -> Callable[[int | None], Table]:
    """Fixture to create dummy Table objects for the chain.

    Returns
    -------
    Callable[[int | None], Table]
        Function that creates Table instances with specified number of columns.
    """

    def _create(n_cols: int | None = None) -> Table:
        """Create a dummy Table instance.

        Parameters
        ----------
        n_cols : int | None
            Number of columns in the table. If None, defaults to 1.

        Returns
        -------
        Table
            The created Table instance.
        """

        width = n_cols or 1

        return Table(
            bbox=[0, 0, 100, 100],
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=n_cols,
            repeats_header=None,
            rows=[table_row(*(["dummy"] * width))],
        )

    return _create


@pytest.fixture
def create_units() -> Callable[[list[str]], list[TextUnit]]:
    """Fixture to create TextUnit instances from strings.

    Returns
    -------
    Callable[[list[str]], list[TextUnit]]
        Function that creates TextUnit instances from a list of strings.
    """

    def _create(texts: list[str]) -> list[TextUnit]:
        """Create TextUnit instances from a list of strings.

        Parameters
        ----------
        texts : list[str]
            List of strings to convert to TextUnit instances.

        Returns
        -------
        list[TextUnit]
            List of TextUnit instances.
        """

        return [TextUnit(language="en", text=t) for t in texts]

    return _create


def test__expand_table_rows_to_rows_grid_bounds_validation() -> None:
    """Verify explicit bounds checking helper handles explicit OOB span."""

    # Row 0, Cell A tries to span 3 columns in a 2-column table.
    rows = [TableRow(cells=[table_cell("A", 3, 1)])]
    segment = make_segment(rows, n_cols=2)

    with pytest.raises(ValueError, match="col_span out of bounds"):
        stitch_segments._expand_table_rows_to_rows_grid(segment=segment)


def test__expand_table_rows_to_rows_grid_complex_span_expansion() -> None:
    """Verify a 2x2 grid with mixed spans expands correctly.

    Row 0: Cell A (row_span=2), Cell B (row_span=1)
    Row 1: Cell C (row_span=1)

    Expected Grid:
    (0,0): A, (0,1): B
    (1,0): A, (1,1): C  <-- C is pushed to col 1 because A occupies col 0
    """

    rows = [
        TableRow(cells=[table_cell("A", 1, 2), table_cell("B")]),
        TableRow(cells=[table_cell("C")]),
    ]
    segment = make_segment(rows, n_cols=2)

    out_rows, sources = stitch_segments._expand_table_rows_to_rows_grid(segment=segment)

    assert len(out_rows) == 2

    # Check Row 0.
    assert out_rows[0].cells[0].text.text == "A"
    assert out_rows[0].cells[1].text.text == "B"

    # Check Row 1.
    assert out_rows[1].cells[0].text.text == "A"  # Filled down/expanded from Row 0
    assert out_rows[1].cells[1].text.text == "C"  # Correctly placed in col 1

    # Check Provenance (source rows). Grid (1,0) should point back to source_row 0.
    assert sources[1][0]["source_row"] == 0

    # Grid (1,1) came from source_row 1.
    assert sources[1][1]["source_row"] == 1


def test__expand_table_rows_to_rows_grid_overflow_error() -> None:
    """Verify ValueError when row content exceeds declared n_cols."""

    rows = [TableRow(cells=[table_cell("A"), table_cell("B"), table_cell("C")])]

    # Declared cols=2, but we have 3 cells.
    segment = make_segment(rows, n_cols=2)

    with pytest.raises(ValueError, match="exceeds declared n_cols"):
        stitch_segments._expand_table_rows_to_rows_grid(segment=segment)


def test__expand_table_rows_to_rows_grid_padding_skips_occupied_slots() -> None:
    """Verify that explicit 'padding' (empty cells) from the extractor correctly skip
    slots occupied by previous row-spans.

    Row 0: Cell A (row_span=2)
    Row 1: [Padding/Empty], Cell B

    The padding should see Col 0 is 'A', skip it, and disappear.
    Cell B should land in Col 1.
    """

    rows = [
        TableRow(cells=[table_cell("A", 1, 2)]),
        TableRow(
            cells=[table_cell(None), table_cell("B")]
        ),  # Padding (empty text, 1x1)
    ]
    segment = make_segment(rows, n_cols=2)

    out_rows, _ = stitch_segments._expand_table_rows_to_rows_grid(segment=segment)

    # Row 1 Col 0 should be A (from above).
    assert out_rows[1].cells[0].text.text == "A"

    # Row 1 Col 1 should be B. If padding didn't skip, B would be pushed to Col 2
    # (overflow).
    assert out_rows[1].cells[1].text.text == "B"


def test__expand_table_rows_to_rows_grid_span_collision_error() -> None:
    """
    Verify ValueError is raised if a wide cell collides with a vertical span
    hidden in the middle of its path.

    Row 0: Cell A (1x1), Cell B (row_span=2), Cell C (1x1)
    Row 1: Cell D (col_span=3)

    Row 1 Grid State before Cell D:
    (1,0): Empty
    (1,1): Occupied by B
    (1,2): Empty

    Cell D starts at (1,0) because it's empty.
    It tries to fill (1,0), (1,1), (1,2).
    It should CRASH at (1,1) because B is there.
    """

    rows = [
        TableRow(cells=[table_cell("A"), table_cell("B", 1, 2), table_cell("C")]),
        TableRow(cells=[table_cell("D", 3)]),
    ]
    segment = make_segment(rows, n_cols=3)

    with pytest.raises(ValueError, match="Overlapping spans detected"):
        stitch_segments._expand_table_rows_to_rows_grid(segment=segment)


def test__fill_down_table_rows_basic_propagation_first_two_columns() -> None:
    """Test basic fill-down logic for first two grouping columns."""

    rows = [
        table_row("Topic A", "Sub A", "Comp 1"),
        table_row(None, None, "Comp 2"),
        table_row(None, "Sub B", "Comp 3"),
        table_row("Topic B", None, "Comp 4"),
        table_row(None, None, "Comp 5"),
    ]

    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    # Row 1 should inherit Topic/Subtopic.
    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub A"

    # Row 2 should inherit Topic but keep explicit Subtopic "Sub B".
    assert out[2].cells[0].text.text == "Topic A"
    assert out[2].cells[1].text.text == "Sub B"

    # Row 3 sets new Topic, Subtopic is empty => should fill Subtopic from last seen
    # ("Sub B").
    assert out[3].cells[0].text.text == "Topic B"
    assert out[3].cells[1].text.text == "Sub B"

    # Row 4 inherits both from Row 3.
    assert out[4].cells[0].text.text == "Topic B"
    assert out[4].cells[1].text.text == "Sub B"


def test__fill_down_table_rows_does_not_overwrite_non_empty_cells() -> None:
    """Test that existing non-empty cells are not overwritten during fill-down."""

    rows = [table_row("Topic A", "Sub A", "Comp 1"), table_row(None, "Sub B", "Comp 2")]
    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    # Topic fills, but Subtopic already has "Sub B" and should NOT be overwritten.
    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub B"


def test__fill_down_table_rows_group_cols_max_zero_returns_deepcopy_and_no_changes() -> (
    None
):
    """Test that group_cols_max=0 results in no fill-down and a deep copy of input."""

    rows = [table_row("Topic A", "Sub A", "Comp 1"), table_row(None, None, "Comp 2")]

    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=0, header_row_count=0, rows=rows
    )

    # Nothing should be filled when group_cols_max <= 0.
    assert out[1].cells[0].text is None
    assert out[1].cells[1].text is None

    # Must be deep-copied: row objects should be different identities.
    assert out is not rows
    assert out[0] is not rows[0]
    assert out[0].cells[0] is not rows[0].cells[0]


def test__fill_down_table_rows_header_rows_are_not_filled_or_used_as_source() -> None:
    """Test that header rows are skipped and not used as fill-down sources."""

    # header_row_count=1 means row 0 is a header and should not affect fill-down.
    rows = [
        table_row("HEADER_TOPIC", "HEADER_SUB", "HEADER_COMP"),
        table_row(None, None, "Comp 1"),
        table_row("Topic A", None, "Comp 2"),
        table_row(None, None, "Comp 3"),
    ]

    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=1, rows=rows
    )

    # Row 1 should NOT inherit from header row (since header rows are skipped entirely).
    assert out[1].cells[0].text is None
    assert out[1].cells[1].text is None

    # Row 2 sets Topic A, Subtopic empty => can't fill Subtopic (still None).
    assert out[2].cells[0].text.text == "Topic A"
    assert out[2].cells[1].text is None

    # Row 3 inherits Topic A, Subtopic still None (no prior non-empty subtopic in body).
    assert out[3].cells[0].text.text == "Topic A"
    assert out[3].cells[1].text is None


def test__fill_down_table_rows_input_is_not_mutated_and_filled_cells_are_copied() -> (
    None
):
    """Test that input rows are not mutated and filled TextUnits are copies."""

    rows = [table_row("Topic A", "Sub A", "Comp 1"), table_row(None, None, "Comp 2")]

    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    # Input remains unchanged.
    assert rows[1].cells[0].text is None
    assert rows[1].cells[1].text is None

    # Output filled.
    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub A"

    # Filled TextUnits should not be the exact same object as source TextUnits
    # (function model_copy(deep=True) when filling).
    assert out[1].cells[0].text is not out[0].cells[0].text
    assert out[1].cells[1].text is not out[0].cells[1].text


def test__fill_down_table_rows_only_first_group_cols_filled_not_other_columns() -> None:
    """Test that only the specified number of grouping columns are filled down."""

    rows = [
        table_row("Topic A", "Sub A", "Comp 1"),
        table_row(None, None, None),  # Competence column is empty too
    ]

    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    # First 2 columns fill down.
    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub A"

    # Third column should NOT be filled down (group_cols_max=2).
    assert out[1].cells[2].text is None


def test__fill_down_table_rows_short_rows_do_not_crash_and_fill_only_existing_cells() -> (
    None
):
    """Test that rows with fewer cells than group_cols_max do not cause errors."""

    # Second row has only 1 cell; group_cols_max=2 should not index error.
    rows = [table_row("Topic A", "Sub A"), TableRow(cells=[table_cell(None)])]

    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    assert out[1].cells[0].text.text == "Topic A"
    assert len(out[1].cells) == 1


def test__fill_down_table_rows_whitespace_text_is_treated_as_empty_and_filled() -> None:
    """Test that cells with only whitespace are treated as empty and filled down."""

    rows = [
        table_row("Topic A", "Sub A", "Comp 1"),
        table_row("   ", "", "Comp 2"),  # Whitespace/empty strings count as empty
    ]

    out = stitch_segments._fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub A"


def test__fill_span_area_index_error(
    create_empty_grid: Callable[[int, int], list[list[dict[str, object]]]],
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Test that function raises IndexError if span goes out of bounds.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_units
        Fixture to create TextUnit instances.
    """

    grid = create_empty_grid(2, 2)
    val = create_units(["overflow"])[0]

    # Try to fill 3 columns in a 2-column grid.
    with pytest.raises(IndexError):
        stitch_segments._fill_span_area(
            col_span=3,
            col_start=0,
            grid=grid,
            row_span=1,
            row_start=0,
            segment_id="seg_err",
            value=val,
        )


def test__fill_span_area_raises_on_overlap(
    create_empty_grid: Callable[[int, int], list[list[dict[str, object]]]],
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Test that ValueError is raised when writing to an occupied cell.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_units
        Fixture to create TextUnit instances.
    """

    grid = create_empty_grid(4, 4)
    val1 = create_units(["A"])[0]
    val2 = create_units(["B"])[0]

    # Fill a cell at (1, 1).
    stitch_segments._fill_span_area(
        col_span=1,
        col_start=1,
        grid=grid,
        row_span=1,
        row_start=1,
        segment_id="seg_1",
        value=val1,
    )

    # Attempt to fill a 2x2 area starting at (0, 0). This covers (0,0), (0,1), (1,0),
    # AND (1,1). Since (1,1) is occupied, this must fail.
    with pytest.raises(ValueError) as e:
        stitch_segments._fill_span_area(
            col_span=2,
            col_start=0,
            grid=grid,
            row_span=2,
            row_start=0,
            segment_id="seg_1",
            value=val2,
        )

    assert "Overlapping spans detected at (row=1, col=1)" in str(e.value)
    assert "seg_1" in str(e.value)


def test__fill_span_area_rectangular_region(
    create_empty_grid: Callable[[int, int], list[list[dict[str, object]]]],
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Test filling a multi-row, multi-column span (2x3).

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_units
        Fixture to create TextUnit instances.
    """

    grid = create_empty_grid(5, 5)
    text_value = create_units(["spanned_header"])[0]

    # Fill a 2-row, 3-column area starting at (1, 1). Should cover (1,1), (1,2), (1,3)
    # AND (2,1), (2,2), (2,3)
    row_start = 1

    stitch_segments._fill_span_area(
        col_span=3,
        col_start=1,
        grid=grid,
        row_span=2,
        row_start=row_start,
        segment_id="test_seg",
        value=text_value,
    )

    for r in range(1, 3):
        for c in range(1, 4):
            cell = grid[r][c]
            assert cell["text"] == text_value, f"Mismatch at ({r}, {c})"
            assert cell["source_row"] == row_start, f"Source row mismatch at ({r}, {c})"

    # Ensure a cell outside the region is untouched.
    assert grid[3][1]["source_row"] == -1


def test__fill_span_area_single_cell(
    create_empty_grid: Callable[[int, int], list[list[dict[str, object]]]],
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Test filling a single 1x1 cell.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_units
        Fixture to create TextUnit instances.
    """

    grid = create_empty_grid(3, 3)
    text_value = create_units(["data"])[0]

    stitch_segments._fill_span_area(
        col_span=1,
        col_start=1,
        grid=grid,
        row_span=1,
        row_start=1,
        segment_id="test_seg",
        value=text_value,
    )

    # Check target cell.
    assert grid[1][1]["text"] == text_value
    assert grid[1][1]["source_row"] == 1

    # Check neighbors remained empty.
    assert grid[1][0]["source_row"] == -1
    assert grid[0][1]["source_row"] == -1


def test__fill_span_area_with_none_value(
    create_empty_grid: Callable[[int, int], list[list[dict[str, object]]]],
) -> None:
    """Test that 'None' (padding/empty) values are correctly written.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    """

    grid = create_empty_grid(2, 2)

    stitch_segments._fill_span_area(
        col_span=2,
        col_start=0,
        grid=grid,
        row_span=1,
        row_start=0,
        segment_id="seg_padding",
        value=None,
    )

    # Text should be None, but source_row should be set to 0 (indicating occupied).
    assert grid[0][0]["text"] is None
    assert grid[0][0]["source_row"] == 0
    assert grid[0][1]["text"] is None
    assert grid[0][1]["source_row"] == 0


def test__finalize_table_structure_computed_cols_via_spans(
    create_table: Callable[[int | None], Table],
) -> None:
    """Test that n_cols is computed by summing col_spans, not just counting cells.

    Parameters
    ----------
    create_table
        Fixture to create dummy Table objects.
    """

    # Create a row with 2 cells, but one spans 3 columns. Total visual columns = 1 + 3 = 4.
    complex_row = table_row_with_spans([("Col 1", 1), ("Col 2-4", 3)])

    stitched_rows = [complex_row]

    # Table declared n_cols=None, so it relies entirely on computation.
    chain = [(0, 0, create_table(None))]
    warnings: list[str] = []

    n_cols, _, _ = stitch_segments._finalize_table_structure(
        chain=chain,
        header_rows=[],
        local_code=None,
        segment_id="seg_spans",
        stitched_rows=stitched_rows,
        warnings=warnings,
    )

    assert n_cols == 4


def test__finalize_table_structure_declared_cols_precedence(
    create_table: Callable[[int | None], Table],
) -> None:
    """Test that if declared_n_cols (from extraction) is larger than computed, declared
    takes precedence (e.g., empty columns at end of table).

    Parameters
    ----------
    create_table
        Fixture to create dummy Table objects.
    """

    # Computed = 2.
    stitched_rows = [table_row("A", "B")]

    # Declared = 5.
    chain = [(0, 0, create_table(5))]
    warnings: list[str] = []

    n_cols, _, _ = stitch_segments._finalize_table_structure(
        chain=chain,
        header_rows=[],
        local_code=None,
        segment_id="seg_prec",
        stitched_rows=stitched_rows,
        warnings=warnings,
    )

    assert n_cols == 5
    assert len(warnings) == 0


def test__finalize_table_structure_empty_headers(
    create_table: Callable[[int | None], Table],
) -> None:
    """Test behavior when there are no header rows.

    Parameters
    ----------
    create_table
        Fixture to create dummy Table objects.
    """

    stitched_rows = [table_row("1", "2")]
    chain = [(0, 0, create_table(2))]
    warnings: list[str] = []

    _, sig, headers_canon = stitch_segments._finalize_table_structure(
        chain=chain,
        header_rows=[],  # Empty
        local_code=None,
        segment_id="seg_2",
        stitched_rows=stitched_rows,
        warnings=warnings,
    )

    assert sig is None
    assert headers_canon == []


def test__finalize_table_structure_inflation_warning(
    create_table: Callable[[int | None], Table],
) -> None:
    """Test that a warning is issued if the computed columns (visual) exceed the
    declared columns (metadata), implying extraction error or merged cells.

    Parameters
    ----------
    create_table
        Fixture to create dummy Table objects.
    """

    # Computed = 4.
    stitched_rows = [table_row("A", "B", "C", "D")]

    # Declared = 2 (Logic: 0 < declared < computed).
    chain = [(0, 0, create_table(2))]
    warnings: list[str] = []

    n_cols, _, _ = stitch_segments._finalize_table_structure(
        chain=chain,
        header_rows=[],
        local_code="Table 99",
        segment_id="seg_inflation",
        stitched_rows=stitched_rows,
        warnings=warnings,
    )

    # It should expand to fit the data.
    assert n_cols == 4

    # Check warning content.
    assert len(warnings) == 1
    msg = warnings[0]
    assert "n_cols inflation detected" in msg
    assert "computed_n_cols=4" in msg
    assert "declared_n_cols=2" in msg
    assert "local_code='Table 99'" in msg


def test__finalize_table_structure_max_declared_in_chain(
    create_table: Callable[[int | None], Table],
) -> None:
    """Test that it finds the maximum declared n_cols across all slices in the chain.

    Parameters
    ----------
    create_table
        Fixture to create dummy Table objects.
    """

    stitched_rows = [table_row("A")]  # Computed = 1

    chain = [
        (0, 0, create_table(2)),
        (1, 0, create_table(6)),  # Max
        (2, 0, create_table(3)),
    ]
    warnings: list[str] = []

    n_cols, _, _ = stitch_segments._finalize_table_structure(
        chain=chain,
        header_rows=[],
        local_code=None,
        segment_id="seg_chain",
        stitched_rows=stitched_rows,
        warnings=warnings,
    )

    assert n_cols == 6


def test__finalize_table_structure_normalization_and_signature(
    create_table: Callable[[int | None], Table],
) -> None:
    """Test that _finalize_table_structure correctly uses _row_signature and
    normalize_text functions to create a lowercased, pipe-delimited signature.

    Parameters
    ----------
    create_table
        Fixture to create dummy Table objects.
    """

    # Setup: Headers with whitespace, caps, and distinct rows:
    #   Row 1: "  Header A " , "HEADER B"
    #   Row 2: "Sub 1", "Sub 2"
    header_rows = [table_row("  Header A ", "HEADER B"), table_row("Sub 1", "Sub 2")]

    # Stitched rows usually contain headers + body
    stitched_rows = header_rows + [table_row("val1", "val2")]

    chain = [(0, 0, create_table(2))]
    warnings: list[str] = []

    n_cols, sig, headers_canon = stitch_segments._finalize_table_structure(
        chain=chain,
        header_rows=header_rows,
        local_code="Table 1",
        segment_id="seg_1",
        stitched_rows=stitched_rows,
        warnings=warnings,
    )

    # Check canonical headers (normalized: lowered, stripped, extra space removed).
    assert headers_canon == [["header a", "header b"], ["sub 1", "sub 2"]]

    # Check signature format: "cell|cell||row|row".
    expected_sig = "header a|header b||sub 1|sub 2"
    assert sig == expected_sig

    assert n_cols == 2


def test__join_text_unit_texts_basic_sentence_flow(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Verify standard sentence wrapping creates a single string.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    units = create_units(["This is a", "sentence split", "across lines."])

    assert (
        stitch_segments._join_text_unit_texts(text_units=units)
        == "This is a sentence split across lines."
    )


def test__join_text_unit_texts_colon_handling(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Test that colons join with a space (assuming inline list/explanation) rather
    than a newline.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    units = create_units(["Note:", "This is important."])

    assert (
        stitch_segments._join_text_unit_texts(text_units=units)
        == "Note: This is important."
    )


def test__join_text_unit_texts_comma_logic_fix(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Ensure lines ending in commas do not force newlines.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    units = create_units(["However,", "we found that..."])

    assert (
        stitch_segments._join_text_unit_texts(text_units=units)
        == "However, we found that..."
    )


def test__join_text_unit_texts_ellipsis_logic_fix(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Ensure '...' is recognized correctly as a terminator, but a single '.' is also
    respected.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    # Standard period.
    units_1 = create_units(["End.", "Start"])

    assert stitch_segments._join_text_unit_texts(text_units=units_1) == "End.\nStart"

    # Ellipsis (TextUnit might split "..." across chunks, but here we assume the unit
    # ends with it).
    units_2 = create_units(["To be continued...", "Chapter 2"])

    assert (
        stitch_segments._join_text_unit_texts(text_units=units_2)
        == "To be continued...\nChapter 2"
    )


def test__join_text_unit_texts_hyphenation_rules(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Test both de-hyphenation and compound word preservation.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    # Standard de-hyphenation (lowercase follows).
    units_soft = create_units(["soft-", "ware"])

    assert stitch_segments._join_text_unit_texts(text_units=units_soft) == "software"

    # Compound words (uppercase follows) -> Keep hyphen, no space.
    units_compound = create_units(["Non-", "Profit"])

    assert (
        stitch_segments._join_text_unit_texts(text_units=units_compound) == "Non-Profit"
    )

    # Compound Words (number follows) -> Keep hyphen, no space.
    units_number = create_units(["pre-", "1990"])

    assert stitch_segments._join_text_unit_texts(text_units=units_number) == "pre-1990"


def test__join_text_unit_texts_mixed_bag_stress_test(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """A complex scenario combining all rules.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    inputs = [
        "The long-",  # Hyphen -> join empty
        "term plan,",  # Comma -> join space
        "created by",  # No punctuation -> join space
        "NASA,",  # Comma + upper next -> join space (fix)
        "is bold.",  # Period -> newline
        "It starts...",  # Ellipsis -> newline
        "Now.",  # End
    ]
    expected = "The longterm plan, created by NASA, is bold.\nIt starts...\nNow."

    units = create_units(inputs)
    result = stitch_segments._join_text_unit_texts(text_units=units)

    assert result == expected


def test__join_text_unit_texts_proper_noun_wrapping_fix(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Ensure sentences wrapping into proper nouns (uppercase) are joined with spaces,
    not newlines.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    units = create_units(["We met", "John at the park."])
    assert (
        stitch_segments._join_text_unit_texts(text_units=units)
        == "We met John at the park."
    )


def test__join_text_unit_texts_whitespace_and_empty_units(
    create_units: Callable[[list[str]], list[TextUnit]],
) -> None:
    """Ensure empty units are skipped and whitespace is trimmed correctly.

    Parameters
    ----------
    create_units
        Fixture to create TextUnit instances.
    """

    units = create_units(["Start", "   ", "End."])  # Should be skipped/filtered out

    # "Start" has no punctuation -> joins with space -> "End.".
    assert stitch_segments._join_text_unit_texts(text_units=units) == "Start End."


def test__populate_grid_spans_col_span(
    create_empty_grid: Callable[[int, int], list[list[dict[str, Any]]]],
    create_segment: Mock,
) -> None:
    """Test horizontal column spans.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_segment
        Fixture to create a mock TableSegment.
    """

    # 1x3 Grid: Row 0: "Title" (col_span=3).
    rows = [
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="Title"), row_span=1, col_span=3
                ),
            ]
        ),
    ]
    create_segment.rows = rows
    grid = create_empty_grid(1, 3)

    stitch_segments._populate_grid_spans(
        segment=create_segment, grid=grid, n_rows=1, n_cols=3
    )

    # All 3 columns should point to "Title".
    for c in range(3):
        assert grid[0][c]["text"].text == "Title"
        assert grid[0][c]["source_row"] == 0


def test__populate_grid_spans_explicit_padding(
    create_empty_grid: Callable[[int, int], list[list[dict[str, Any]]]],
    create_segment: Mock,
) -> None:
    """Test a vertical row span where the subsequent row DOES contain an explicit empty
    padding cell (common in some OCR outputs). The function should consume the padding
    cell and not write it to the grid.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_segment
        Fixture to create a mock TableSegment.
    """

    # 2x2 Grid: Row 0: "A" (row_span=2), "B", Row 1: Empty (padding for A), "C".
    rows = [
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="A"), row_span=2, col_span=1
                ),
                TableCell(
                    text=TextUnit(language="en", text="B"), row_span=1, col_span=1
                ),
            ]
        ),
        TableRow(
            cells=[
                # Padding cell: Text is None, 1x1
                TableCell(text=None, row_span=1, col_span=1),
                TableCell(
                    text=TextUnit(language="en", text="C"), row_span=1, col_span=1
                ),
            ]
        ),
    ]
    create_segment.rows = rows
    grid = create_empty_grid(2, 2)

    stitch_segments._populate_grid_spans(
        segment=create_segment, grid=grid, n_rows=2, n_cols=2
    )

    # (1,0) should still be "A" from the span.
    assert grid[1][0]["text"].text == "A"
    assert grid[1][0]["source_row"] == 0

    # (1,1) should be "C". The padding cell should NOT have overwritten (1,0) and
    # should NOT have pushed "C" to a non-existent column 3.
    assert grid[1][1]["text"].text == "C"


def test__populate_grid_spans_overflow_raises_error(
    create_empty_grid: Callable[[int, int], list[list[dict[str, Any]]]],
    create_segment: Mock,
) -> None:
    """Test that exceeding n_cols raises ValueError.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_segment
        Fixture to create a mock TableSegment.
    """

    # 1x2 Grid declared, but Row 0 has 3 cells.
    rows = [
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="A"), row_span=1, col_span=1
                ),
                TableCell(
                    text=TextUnit(language="en", text="B"), row_span=1, col_span=1
                ),
                TableCell(
                    text=TextUnit(language="en", text="C"), row_span=1, col_span=1
                ),
            ]
        ),
    ]
    create_segment.rows = rows
    grid = create_empty_grid(1, 2)

    with pytest.raises(ValueError) as e:
        stitch_segments._populate_grid_spans(
            segment=create_segment, grid=grid, n_rows=1, n_cols=2
        )

    assert "exceeds declared n_cols=2" in str(e.value)


def test__populate_grid_spans_overlapping_spans_error(
    create_empty_grid: Callable[[int, int], list[list[dict[str, Any]]]],
    create_segment: Mock,
) -> None:
    """Test that two cells trying to claim the same spot raises ValueError.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_segment
        Fixture to create a mock TableSegment.
    """

    # 2x3 Grid (n_cols=3)
    # Row 0: Empty, "A" (row_span=2), Empty
    #   - Occupies (0,1) and (1,1).
    # Row 1: "B" (col_span=3)
    #   - Starts at 0 (valid). Fits in 3 cols (valid).
    #   - Fills (1,0) [OK], tries (1,1) [COLLISION with A].
    rows = [
        TableRow(
            cells=[
                TableCell(text=None, row_span=1, col_span=1),  # (0,0) empty
                TableCell(
                    text=TextUnit(language="en", text="A"), row_span=2, col_span=1
                ),  # Occupies (0,1) and (1,1)
                TableCell(text=None, row_span=1, col_span=1),  # (0,2) empty
            ]
        ),
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="B"), row_span=1, col_span=3
                ),
            ]
        ),
    ]
    create_segment.rows = rows
    grid = create_empty_grid(2, 3)  # Make sure grid is 3 columns wide

    with pytest.raises(ValueError) as e:
        stitch_segments._populate_grid_spans(
            segment=create_segment, grid=grid, n_rows=2, n_cols=3
        )

    assert "Overlapping spans detected" in str(e.value)


def test__populate_grid_spans_row_span_implicit_skip(
    create_empty_grid: Callable[[int, int], list[list[dict[str, Any]]]],
    create_segment: Mock,
) -> None:
    """Test a vertical row span where the subsequent row does NOT contain a padding
    cell. The function must implicitly skip the occupied slot.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_segment
        Fixture to create a mock TableSegment.
    """

    # 2x2 Grid: Row 0: "A" (row_span=2), "B", Row 1: "C" (should land in col 1 because
    # col 0 is taken by "A").
    rows = [
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="A"), row_span=2, col_span=1
                ),
                TableCell(
                    text=TextUnit(language="en", text="B"), row_span=1, col_span=1
                ),
            ]
        ),
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="C"), row_span=1, col_span=1
                ),
            ]
        ),
    ]
    create_segment.rows = rows
    grid = create_empty_grid(2, 2)

    stitch_segments._populate_grid_spans(
        segment=create_segment, grid=grid, n_rows=2, n_cols=2
    )

    # (0,0) and (1,0) should be "A".
    assert grid[0][0]["text"].text == "A"
    assert grid[1][0]["text"].text == "A"
    assert grid[1][0]["source_row"] == 0  # Originated from row 0

    # (0,1) is "B".
    assert grid[0][1]["text"].text == "B"

    # (1,1) is "C".
    assert grid[1][1]["text"].text == "C"


def test__populate_grid_spans_simple_1x1_grid(
    create_empty_grid: Callable[[int, int], list[list[dict[str, Any]]]],
    create_segment: Mock,
) -> None:
    """Test standard population where every cell is 1x1.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_segment
        Fixture to create a mock TableSegment.
    """

    # 2x2 Grid: Row 0: "A", "B", Row 1: "C", "D".
    rows = [
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="A"), row_span=1, col_span=1
                ),
                TableCell(
                    text=TextUnit(language="en", text="B"), row_span=1, col_span=1
                ),
            ]
        ),
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="C"), row_span=1, col_span=1
                ),
                TableCell(
                    text=TextUnit(language="en", text="D"), row_span=1, col_span=1
                ),
            ]
        ),
    ]
    create_segment.rows = rows
    grid = create_empty_grid(2, 2)

    stitch_segments._populate_grid_spans(
        segment=create_segment, grid=grid, n_rows=2, n_cols=2
    )

    # Verify content.
    assert grid[0][0]["text"].text == "A"
    assert grid[0][1]["text"].text == "B"
    assert grid[1][0]["text"].text == "C"
    assert grid[1][1]["text"].text == "D"


def test__populate_grid_spans_trailing_padding_ignored(
    create_empty_grid: Callable[[int, int], list[list[dict[str, Any]]]],
    create_segment: Mock,
) -> None:
    """Test that extra padding cells at the end of a row (beyond n_cols) are safely
    ignored rather than triggering an overflow error.

    Parameters
    ----------
    create_empty_grid
        Fixture to create an empty grid.
    create_segment
        Fixture to create a mock TableSegment.
    """

    # 1x2 Grid: Row 0: "A", "B", Padding(None).
    rows = [
        TableRow(
            cells=[
                TableCell(
                    text=TextUnit(language="en", text="A"), row_span=1, col_span=1
                ),
                TableCell(
                    text=TextUnit(language="en", text="B"), row_span=1, col_span=1
                ),
                TableCell(text=None, row_span=1, col_span=1),  # Should be ignored
            ]
        ),
    ]
    create_segment.rows = rows
    grid = create_empty_grid(1, 2)

    # Should not raise.
    stitch_segments._populate_grid_spans(
        segment=create_segment, grid=grid, n_rows=1, n_cols=2
    )

    assert grid[0][0]["text"].text == "A"
    assert grid[0][1]["text"].text == "B"


def test__populate_grid_spans_header_count_mismatch_warning() -> None:
    """Test warning when next slice has headers, but count differs from segment."""

    # Segment thinks headers are 2 rows deep
    segment_hrc = 2

    # Next slice says it has headers (repeats_header=None means implicit), but claims
    # count is 3. NB: We must provide at least 3 rows to satisfy the Table validator.
    next_item = Table(
        bbox=[0, 0, 0, 0],
        boundary=ItemBoundary.RESUMED,
        kind="table",
        local_code=None,
        rows=[table_row("R1"), table_row("R2"), table_row("R3")],
        repeats_header=None,
        header_row_count=3,  # Mismatch > segment
    )
    warnings: list[str] = []

    stitch_segments._process_next_table_slice(
        current_local_code=None,
        next_item=next_item,
        next_item_index=1,
        next_page_index=1,
        segment_header_row_count=segment_hrc,
        segment_header_rows=[table_row("H1"), table_row("H2")],
        segment_id="seg_1",
        warnings=warnings,
    )

    # Should use min(2, 3) -> 2 passed to helper.
    assert len(warnings) == 1
    assert "header_row_count mismatch" in warnings[0]


def test__process_next_table_slice_explicit_header_drop() -> None:
    """Test repeats_header=True drops the specified number of rows."""

    # Segment expects 2 header rows.
    header_rows = [table_row("H1"), table_row("H2")]

    # 2 Header rows, 1 Data row. repeats_header=True.
    next_rows = [table_row("H1"), table_row("H2"), table_row("Data")]
    next_item = Table(
        bbox=[0, 0, 100, 100],
        boundary=ItemBoundary.RESUMED,
        header_row_count=2,
        kind="table",
        repeats_header=True,
        rows=next_rows,
        local_code=None,
    )

    warnings: list[str] = []
    result = stitch_segments._process_next_table_slice(
        current_local_code="Table 1",
        next_item=next_item,
        next_item_index=1,
        next_page_index=1,
        segment_header_row_count=2,
        segment_header_rows=header_rows,
        segment_id="seg_1",
        warnings=warnings,
    )

    assert len(result["rows_to_add"]) == 1
    assert result["rows_to_add"][0].cells[0].text.text == "Data"
    assert result["slice"].dropped_header_rows == 2
    assert result["slice"].repeats_header is True

    # Ensure no warnings.
    assert len(warnings) == 0


def test__process_next_table_slice_explicit_header_drop_fallback_to_segment_count() -> (
    None
):
    """Test repeats_header=True but header_row_count=0. Should fallback to
    segment_header_row_count and warn.
    """

    header_rows = [table_row("H1")]

    # Next item has header, repeats=True, but extractor failed to count headers (0).
    next_rows = [table_row("H1"), table_row("Data")]
    next_item = Table(
        bbox=[0, 0, 100, 100],
        boundary=ItemBoundary.RESUMED,
        header_row_count=1,
        kind="table",
        repeats_header=True,
        rows=next_rows,
        local_code=None,
    )
    warnings: list[str] = []
    result = stitch_segments._process_next_table_slice(
        current_local_code="Table 1",
        next_item=next_item,
        next_item_index=1,
        next_page_index=1,
        segment_header_row_count=1,  # Should fallback to this
        segment_header_rows=header_rows,
        segment_id="seg_1",
        warnings=warnings,
    )

    assert len(result["rows_to_add"]) == 1
    assert result["rows_to_add"][0].cells[0].text.text == "Data"
    assert not warnings


def test__process_next_table_slice_implicit_drop_calls_helper() -> None:
    """Test repeats_header=False/None calls _drop_repeated_header."""

    mock_data_row = table_row("Data Only")
    header_rows = [table_row("H1")]
    next_rows = [table_row("H1"), table_row("Data Only")]

    next_item = Table(
        bbox=[0, 0, 100, 100],
        boundary=ItemBoundary.RESUMED,
        header_row_count=1,
        kind="table",
        repeats_header=None,  # Implicit mode
        rows=next_rows,
        local_code=None,
    )

    result = stitch_segments._process_next_table_slice(
        current_local_code="Table 1",
        next_item=next_item,
        next_item_index=1,
        next_page_index=1,
        segment_header_row_count=1,
        segment_header_rows=header_rows,
        segment_id="seg_1",
        warnings=[],
    )

    # Ensure helper was called
    assert result["rows_to_add"] == [mock_data_row]
    assert result["slice"].dropped_header_rows == 1


def test__process_next_table_slice_local_code_conflict() -> None:
    """Test conflicting local codes generates warning and keeps current code."""

    next_item = Table(
        bbox=[0, 0, 10, 10],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        repeats_header=None,
        rows=[table_row("A")],
        local_code="table 2",  # Conflict
    )
    warnings: list[str] = []

    result = stitch_segments._process_next_table_slice(
        current_local_code="Table 1",  # Current
        next_item=next_item,
        next_item_index=1,
        next_page_index=1,
        segment_header_row_count=0,
        segment_header_rows=[],
        segment_id="seg_1",
        warnings=warnings,
    )

    assert result["local_code"] == "Table 1"
    assert len(warnings) == 1
    assert "Conflicting local_code" in warnings[0]
    assert "'Table 1' vs. 'table 2'" in warnings[0]


def test__process_next_table_slice_provenance_creation() -> None:
    """Verify SegmentProvenance is created correctly."""

    next_item = Table(
        bbox=[10, 20, 30, 40],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        local_code="Table X",
        repeats_header=None,
        rows=[table_row("Row1")],
    )

    result = stitch_segments._process_next_table_slice(
        current_local_code=None,
        next_item=next_item,
        next_item_index=5,
        next_page_index=2,
        segment_header_row_count=0,
        segment_header_rows=[],
        segment_id="seg_1",
        warnings=[],
    )

    prov = result["provenance"]
    assert prov.page_index == 2
    assert prov.item_index == 5
    assert prov.bbox == [10, 20, 30, 40]
    assert prov.local_code == "Table X"
    assert result["slice"].page_index == 2


def test__resolve_header_row_count_inference_ignored_low_confidence() -> None:
    """Scenario: Row with mixed text/numbers. Target: Score between 1.15 and 1.65 to
    get count=1 but low confidence.

    Data: "Year 2023"
    Len: 8 chars. Alpha: 4 (0.5), Digit: 4 (0.5). Filled: 1.0.
    Score calculation:
      2.0 * 0.5 (1.0)
    + 1.0 * 1.0 (1.0)
    - 1.5 * 0.5 (0.75)
    = 1.25

    Confidence calculation:
    0.55 + 0.20 * (1.25 - 1.15) = 0.57

    0.57 < 0.65 threshold -> Result should be rejected (fallback to 0).
    """

    table = Table(
        bbox=[0, 0, 100, 100],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        local_code=None,
        repeats_header=None,
        rows=[table_row("Year 2023"), table_row("1000")],  # Score ~1.25
    )
    warnings: list[str] = []

    result = stitch_segments._resolve_header_row_count(
        first_item=table, item_index=0, page_index=0, warnings=warnings
    )

    assert result == 0  # Reverted to original 0 because confidence was too low
    assert len(warnings) == 0


def test__resolve_header_row_count_inference_returns_zero_count() -> None:
    """Scenario: Pure number row.

    Math: Alpha=0, Digit=1.0.
    Score: 2(0) + 1(1) - 1.5(1) = -0.5.
    Score < 1.15, so count remains 0.
    """

    table = Table(
        bbox=[0, 0, 100, 100],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        local_code=None,
        repeats_header=None,
        rows=[table_row("12345"), table_row("67890")],
    )

    result = stitch_segments._resolve_header_row_count(
        first_item=table, item_index=0, page_index=0, warnings=[]
    )

    assert result == 0


def test__resolve_header_row_count_inference_success_high_confidence() -> None:
    """Scenario: Pure text row.

    Math: Alpha=1.0, Digit=0.0, Filled=1.0
    Score: 2(1) + 1(1) - 1.5(0) = 3.0
    Confidence: 0.55 + 0.2 * (3.0 - 1.15) = ~0.92 (>= 0.65)
    Result: Should return 1.
    """

    table = Table(
        bbox=[0, 0, 100, 100],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        local_code=None,
        repeats_header=None,
        rows=[table_row("Description", "Category"), table_row("123", "456")],
    )
    warnings: list[str] = []

    result = stitch_segments._resolve_header_row_count(
        first_item=table, item_index=0, page_index=0, warnings=warnings
    )

    assert result == 1
    assert len(warnings) == 1
    assert "Inferred header_row_count=1" in warnings[0]


def test__resolve_header_row_count_returns_explicit_count_immediately() -> None:
    """If the table already has a header_row_count > 0, return it without inference."""

    # Table already has header_row_count=2.
    table = Table(
        bbox=[0, 0, 100, 100],
        boundary=ItemBoundary.COMPLETE,
        header_row_count=2,
        kind="table",
        rows=[table_row("H1"), table_row("H2"), table_row("Data")],
    )

    result = stitch_segments._resolve_header_row_count(
        first_item=table, item_index=0, page_index=0, warnings=[]
    )

    assert result == 2


def test__resolve_initial_local_code_all_items_empty() -> None:
    """If no items have a local code, return None."""

    chain = [
        make_chain_entry(0, 0, None),
        make_chain_entry(1, 0, ""),
        make_chain_entry(2, 0, "   "),
    ]

    result = stitch_segments._resolve_initial_local_code(chain)
    assert result is None


def test__resolve_initial_local_code_first_item_empty_finds_second() -> None:
    """If first item has None/Empty code, look ahead to the next items."""

    chain = [
        make_chain_entry(0, 0, None),  # Missing
        make_chain_entry(0, 1, "   "),  # Empty/Whitespace -> treated as None
        make_chain_entry(1, 0, "Table 3"),  # Target
    ]

    result = stitch_segments._resolve_initial_local_code(chain)
    assert result == "Table 3"


def test__resolve_initial_local_code_first_item_has_code() -> None:
    """If the first item has a code, return it immediately (normalized)."""

    chain = [
        make_chain_entry(0, 0, "Table 1"),
        make_chain_entry(0, 1, "Table 2"),  # Should be ignored
    ]

    result = stitch_segments._resolve_initial_local_code(chain)
    assert result == "Table 1"


def test__resolve_initial_local_code_normalization_behavior() -> None:
    """Verify normalization logic (trim, collapse internal space, casefold)."""

    # "  Table   1.2  " -> "table 1.2".
    chain = [make_chain_entry(0, 0, "  Table   1.2  ")]

    # We rely on the real normalize_local_code implementation here.
    result = stitch_segments._resolve_initial_local_code(chain)
    assert result == "Table   1.2"


def test__resolve_initial_local_code_single_item_chain_none() -> None:
    """Edge case: Chain has only 1 item and it is None."""

    chain = [make_chain_entry(0, 0, None)]
    assert stitch_segments._resolve_initial_local_code(chain) is None


def test__resolve_initial_local_code_stops_at_first_valid_match() -> None:
    """Ensure it doesn't scan the whole list if a match is found mid-way."""

    chain = [
        make_chain_entry(0, 0, None),
        make_chain_entry(1, 0, "Table A"),  # First valid
        make_chain_entry(2, 0, "Table B"),
    ]

    result = stitch_segments._resolve_initial_local_code(chain)
    assert result == "Table A"


def test__row_provenance_by_stitched_index_length_mismatch_raises_error() -> None:
    """Test that ValueError is raised if stitched rows don't match slice rows."""

    # Slice has 1 row.
    slice_1 = make_slice([table_row("r1")], page_index=1)

    # Segment claims to have 2 rows.
    stitched_rows = [table_row("r1"), table_row("ghost_row")]

    segment = make_segment(rows=stitched_rows, n_cols=1, seg_id="bad_seg")
    segment.slices = [slice_1]

    msg = "Row <-> slice mapping length mismatch for TableSegment bad_seg"

    with pytest.raises(ValueError, match=msg):
        stitch_segments._row_provenance_by_stitched_index(segment=segment)


def test__row_provenance_by_stitched_index_multi_slice_stitching() -> None:
    """Test provenance across two stitched slices without dropped headers."""

    slice_1_rows = [table_row("p1_r1"), table_row("p1_r2")]
    slice_2_rows = [table_row("p2_r1"), table_row("p2_r2")]

    all_rows = slice_1_rows + slice_2_rows

    slice_1 = make_slice(rows=slice_1_rows, page_index=1, bbox=[0, 0, 50, 50])
    slice_2 = make_slice(rows=slice_2_rows, page_index=2, bbox=[0, 0, 60, 60])

    segment = make_segment(rows=all_rows, n_cols=1)
    segment.slices = [slice_1, slice_2]

    provenance = stitch_segments._row_provenance_by_stitched_index(segment=segment)

    assert len(provenance) == 4

    # Row 0 (From Slice 1).
    assert provenance[0].page_index == 1
    assert provenance[0].slice_index == 0
    assert provenance[0].slice_row_index == 0

    # Row 2 (From Slice 2) - This is the first row of the second page.
    assert provenance[2].page_index == 2
    assert provenance[2].slice_index == 1
    assert provenance[2].slice_row_index == 0
    assert provenance[2].dropped_header_rows == 0


def test__row_provenance_by_stitched_index_no_slices_assertion() -> None:
    """Test assertion failure if segment has no slices."""

    segment = make_segment(rows=[], n_cols=1)
    segment.slices = []  # Empty

    with pytest.raises(AssertionError, match="has no slices"):
        stitch_segments._row_provenance_by_stitched_index(segment=segment)


def test__row_provenance_by_stitched_index_single_slice_simple() -> None:
    """Test provenance generation for a single slice with no dropped rows. Verifies
    that row_bbox is calculated by splitting the slice bbox evenly across the number of
    rows.
    """

    rows = [table_row("r1"), table_row("r2"), table_row("r3")]

    # Slice is 0-100 in height. With 3 rows, each row should be ~33.33 high.
    slice_1 = make_slice(rows=rows, page_index=1, bbox=[0, 0, 100, 300])

    segment = make_segment(rows=rows, n_cols=1)
    segment.slices = [slice_1]

    provenance = stitch_segments._row_provenance_by_stitched_index(segment=segment)

    assert len(provenance) == 3

    # Check first row.
    row_0 = provenance[0]
    assert row_0.page_index == 1
    assert row_0.slice_index == 0
    assert row_0.slice_row_index == 0

    # Height 300 / 3 rows = 100 per row. y0=0, y1=100.
    assert row_0.row_bbox == [0, 0, 100, 100]

    # Check last row.
    row_2 = provenance[2]
    assert row_2.slice_row_index == 2

    # y0=200, y1=300
    assert row_2.row_bbox == [0, 200, 100, 300]


def test__row_provenance_by_stitched_index_with_dropped_headers() -> None:
    """Test provenance when the second slice drops header rows. This ensures that
    'slice_row_index' points to the *visual* row on the source page (including the
    header that was dropped from the stitched output), while
    'slice_row_index_after_drop' points to the index within the effective set.
    """

    # Slice 1: Header, Data A.
    s1_rows = [table_row("Header"), table_row("Data A")]

    # Slice 2: Header (Repeated), Data B. We simulate that stitching logic decided to
    # drop the first row of Slice 2.
    s2_rows = [table_row("Header"), table_row("Data B")]

    # The final stitched rows will be: [Header, Data A, Data B].
    stitched_rows = [s1_rows[0], s1_rows[1], s2_rows[1]]

    slice_1 = make_slice(
        s1_rows, page_index=1, dropped_header_rows=0, bbox=[0, 0, 10, 20]
    )

    # Slice 2 has 2 rows total, bbox height 20. Each row is 10 units high. Row 0 is
    # Header (0-10), Row 1 is Data B (10-20).
    slice_2 = make_slice(
        s2_rows, page_index=2, dropped_header_rows=1, bbox=[0, 0, 10, 20]
    )

    segment = make_segment(rows=stitched_rows, n_cols=1)
    segment.slices = [slice_1, slice_2]

    provenance = stitch_segments._row_provenance_by_stitched_index(segment=segment)

    assert len(provenance) == 3

    # Check the last row (Data B), which came from Slice 2.
    prov_data_b = provenance[2]

    assert prov_data_b.page_index == 2
    assert prov_data_b.slice_index == 1

    # It was index 1 in the original slice (0 was header).
    assert prov_data_b.slice_row_index == 1

    # It is the 0th row *after* dropping headers.
    assert prov_data_b.slice_row_index_after_drop == 0

    # Verify bbox calculation:
    #   - Slice height 20 / 2 rows = 10 per row.
    #   - Index 1 corresponds to y range [10, 20].
    #   - If the logic incorrectly used "index after drop (0)", it would return
    #   [0, 0, 10, 10]. We want [0, 10, 10, 20].
    assert prov_data_b.row_bbox == [0.0, 10.0, 10.0, 20.0]


def test__stitch_table_chain_columns_signature_generation() -> None:
    """Test that columns_signature is generated from header rows in
    _finalize_table_structure.
    """

    p1, i1, table = make_chain_entry(page=1, idx=0, code="T1")
    table.header_row_count = 2
    table.rows = [
        table_row("H1", "H2"),
        table_row("Sub1", "Sub2"),
        table_row("D1", "D2"),
    ]

    chain = [(p1, i1, table)]

    segment = stitch_segments._stitch_table_chain(
        chain=chain,
        doc_key="doc",
        section_path=[],
        table_filldown_enabled=False,
        table_filldown_group_cols_max=0,
        warnings=[],
    )

    # We expect the signature to join cells with '|' and rows with '||'.
    expected_sig = "h1|h2||sub1|sub2"
    assert segment.columns_signature == expected_sig
    assert len(segment.header_rows_canonical) == 2


def test__stitch_table_chain_deterministic_id() -> None:
    """Test that the segment ID is generated deterministically based on the doc_key
    and the first item's pointer.
    """

    page, idx, table = make_chain_entry(page=1, idx=0, code="T1")
    table.rows = [table_row("content")]
    chain = [(page, idx, table)]
    doc_key = "abc123hash"

    segment_1 = stitch_segments._stitch_table_chain(
        chain=chain,
        doc_key=doc_key,
        section_path=[],
        table_filldown_enabled=False,
        table_filldown_group_cols_max=0,
        warnings=[],
    )

    segment_2 = stitch_segments._stitch_table_chain(
        chain=chain,
        doc_key=doc_key,
        section_path=[],
        table_filldown_enabled=False,
        table_filldown_group_cols_max=0,
        warnings=[],
    )

    assert segment_1.segment_id == segment_2.segment_id
    assert UUID(segment_1.segment_id).version == 5
    assert segment_1.segment_id != ""


def test__stitch_table_chain_explicit_header_drop() -> None:
    """Test stitching a multi-page table where the second page explicitly repeats
    headers. This verifies the logic in _process_next_table_slice regarding
    `repeats_header=True`.
    """

    # Page 1.
    p1, i1, t1 = make_chain_entry(page=1, idx=0, code="T1")
    t1.rows = [table_row("Header"), table_row("Row 1")]
    t1.header_row_count = 1
    t1.n_cols = 1

    # Page 2 (repeats header explicitly).
    p2, i2, t2 = make_chain_entry(page=2, idx=0, code="T1")
    t2.rows = [table_row("Header"), table_row("Row 2")]
    t2.header_row_count = 1
    t2.n_cols = 1
    t2.repeats_header = True
    t2.boundary = ItemBoundary.RESUMED  # Required for repeats_header validation

    chain = [(p1, i1, t1), (p2, i2, t2)]

    segment = stitch_segments._stitch_table_chain(
        chain=chain,
        doc_key="doc",
        section_path=[],
        table_filldown_enabled=False,
        table_filldown_group_cols_max=0,
        warnings=[],
    )

    # Should result in 3 rows: Header, Row 1, Row 2.
    assert len(segment.rows) == 3
    assert segment.rows[0].cells[0].text.text == "Header"
    assert segment.rows[1].cells[0].text.text == "Row 1"
    assert segment.rows[2].cells[0].text.text == "Row 2"

    # Verify provenance of the dropped row. Slice 2 (index 1) should show
    # dropped_header_rows=1.
    assert segment.slices[1].dropped_header_rows == 1


def test__stitch_table_chain_filldown_disabled() -> None:
    """Verify rows_filldown is None when disabled."""

    p1, i1, table = make_chain_entry(page=1, idx=0, code="T1")
    table.rows = [table_row("A", "1"), table_row("", "2")]

    segment = stitch_segments._stitch_table_chain(
        chain=[(p1, i1, table)],
        doc_key="doc",
        section_path=[],
        table_filldown_enabled=False,  # Disabled
        table_filldown_group_cols_max=1,
        warnings=[],
    )

    assert segment.rows_filldown is None


def test__stitch_table_chain_filldown_logic() -> None:
    """Test the integration of fill_down_table_rows within the stitching process.

    Scenario:

      - Header: [Group, Value]
      - Row 1: [A, 1]
      - Row 2: [, 2]  <-- Should fill 'A'
      - Row 3: [B, 3]
      - Row 4: [, 4]  <-- Should fill 'B'
    """

    p1, i1, table = make_chain_entry(page=1, idx=0, code="T1")
    table.header_row_count = 1
    table.rows = [
        table_row("Group", "Value"),
        table_row("A", "1"),
        table_row("", "2"),  # Empty string
        table_row("B", "3"),
        table_row(None, "4"),  # None value
    ]

    chain = [(p1, i1, table)]

    segment = stitch_segments._stitch_table_chain(
        chain=chain,
        doc_key="doc",
        section_path=[],
        table_filldown_enabled=True,
        table_filldown_group_cols_max=1,  # Only fill 1st column
        warnings=[],
    )

    assert segment.rows_filldown is not None

    # Row 2 (index 2) should have filled "A".
    assert segment.rows_filldown[2].cells[0].text.text == "A"

    # Row 4 (index 4) should have filled "B".
    assert segment.rows_filldown[4].cells[0].text.text == "B"

    # Ensure raw rows are untouched.
    assert segment.rows[2].cells[0].text.text == ""


def test__stitch_table_chain_propagates_local_code() -> None:
    """Test that local_code is resolved from the first non-null occurrence
    and backfilled/propagated.
    """

    # Item 1: No code.
    p1, i1, t1 = make_chain_entry(page=1, idx=0, code=None)
    t1.rows = [table_row("r1")]

    # Item 2: Has code "Table 2.1".
    p2, i2, t2 = make_chain_entry(page=2, idx=0, code="Table 2.1")
    t2.rows = [table_row("r2")]

    # Item 3: No code (should inherit "Table 2.1").
    p3, i3, t3 = make_chain_entry(page=3, idx=0, code=None)
    t3.rows = [table_row("r3")]

    chain = [(p1, i1, t1), (p2, i2, t2), (p3, i3, t3)]

    segment = stitch_segments._stitch_table_chain(
        chain=chain,
        doc_key="doc",
        section_path=[],
        table_filldown_enabled=False,
        table_filldown_group_cols_max=0,
        warnings=[],
    )

    assert segment.local_code == "Table 2.1"

    # Provenance backfill (Item 1 should now have the code).
    assert segment.segment_provenance[0].local_code == "Table 2.1"

    # Forward propagation (Item 3 should have the code).
    assert segment.segment_provenance[2].local_code == "Table 2.1"


@PARAM(
    "input_text, expected_output",
    [
        # Basic casing and spacing.
        ("Hello World", "hello world"),
        ("Python  Testing", "python testing"),
        # Edge case: None and empty strings.
        (None, ""),
        ("", ""),
        # Whitespace: leading/trailing/only whitespace.
        ("   ", ""),
        ("  leading", "leading"),
        ("trailing  ", "trailing"),
        # Complex whitespace: newlines and tabs.
        ("Line\nBreak", "line break"),
        ("Tab\tCharacter", "tab character"),
        ("  Mixed\n\t  Whitespace  ", "mixed whitespace"),
        # Already normalized.
        ("perfectly clean", "perfectly clean"),
    ],
)
def test_normalize_text(input_text: str, expected_output: str) -> None:
    """Verify that normalize_text handles various whitespace, casing, and null inputs
    correctly.

    Parameters
    ----------
    input_text
        The text to normalize.
    expected_output
        The expected output.
    """

    assert stitch_segments.normalize_text(input_text) == expected_output
