"""This is the main module for testing page_ir_extraction/schemas.py."""

# Standard Library
from collections.abc import Callable

# Third Party Library
import pytest

from pydantic import ValidationError

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    ListItem,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
    ValidationIssue,
    ValidationVerdict,
)
from skg.utils.constants import BlockType, FigureKind, ItemBoundary
from tests.constants import PARAM


@pytest.fixture(scope="function")
def bbox() -> list[float]:
    """Return a valid, ordered bbox [x0, y0, x1, y1].

    Returns
    -------
    list[float]
        A valid bounding box with x0 < x1 and y0 < y1. The specific values are not
        important for validation tests, as long as the ordering is correct.
    """

    return [0.0, 0.0, 100.0, 50.0]


@pytest.fixture(scope="function")
def make_heading_block(
    bbox: list[float], make_text_unit: Callable[..., TextUnit]
) -> Callable[..., Block]:
    """Factory for creating a minimal valid heading Block.

    Parameters
    ----------
    bbox
        A valid bounding box to use for the Block.
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests.

    Returns
    -------
    Callable[..., Block]
        A factory function that creates Block instances of type HEADING with default
        text content. The factory accepts an optional text parameter to override the
        default heading text, allowing tests to easily generate heading blocks with
        specific text content while maintaining valid defaults for all other fields.
    """

    def _make(*, text: str = "Heading") -> Block:
        """Create a heading Block with default text, allowing override.

        Parameters
        ----------
        text
            The text content for the heading (default is "Heading").

        Returns
        -------
        Block
            A Block instance of type HEADING with the specified text and valid defaults
            for all other fields.
        """

        return Block(
            bbox=bbox,
            block_type=BlockType.HEADING,
            boundary=ItemBoundary.COMPLETE,
            figure=None,
            kind="block",
            list_items=None,
            local_code=None,
            text=make_text_unit(language="en", text=text),
        )

    return _make


@pytest.fixture(scope="function")
def make_minimal_page_ir(
    make_heading_block: Callable[..., Block],
) -> Callable[..., PageIR]:
    """Factory for creating a minimal valid PageIR.

    Parameters
    ----------
    make_heading_block
        A factory function for creating a valid heading Block, injected to allow for
        consistent Block creation across tests.

    Returns
    -------
    Callable[..., PageIR]
        A factory function that creates a PageIR instance containing a single heading
        block with default text.
    """

    def _make() -> PageIR:
        """Create a minimal PageIR with a single heading block.

        Returns
        -------
        PageIR
            A PageIR instance containing one Block of type HEADING with default text.
        """

        return PageIR(items=[make_heading_block(text="A")])

    return _make


@pytest.fixture(scope="function")
def make_simple_table(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> Callable[..., Table]:
    """Factory for creating a small, valid 2x2 table.

    Parameters
    ----------
    bbox
        A valid bounding box to use for the Table.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests.

    Returns
    -------
    Callable[..., Table]
        A factory function that creates a simple 2x2 Table instance with default cell
        content.
    """

    def _make(
        *,
        boundary: ItemBoundary = ItemBoundary.COMPLETE,
        header_row_count: int = 0,
        n_cols: int | None = 2,
        repeats_header: bool | None = None,
    ) -> Table:
        """Create a simple 2x2 Table with default cell content, allowing overrides for
        table-level fields.

        Parameters
        ----------
        boundary
            The ItemBoundary for the table (default is COMPLETE).
        header_row_count
            The number of header rows in the table (default is 0).
        n_cols
            The number of columns in the table (default is 2).
        repeats_header
            Whether the table repeats its header on each page (default is None, which
            is treated as False). This field is only valid when boundary is RESUMED or
            BOTH.

        Returns
        -------
        Table
            A Table instance with 2 rows and 2 columns, containing simple text content
            in each cell.
        """

        row0 = TableRow(
            cells=[
                make_table_cell(col_span=1, row_span=1, text="A"),
                make_table_cell(col_span=1, row_span=1, text="B"),
            ]
        )
        row1 = TableRow(
            cells=[
                make_table_cell(col_span=1, row_span=1, text="1"),
                make_table_cell(col_span=1, row_span=1, text="2"),
            ]
        )

        return Table(
            bbox=bbox,
            boundary=boundary,
            header_row_count=header_row_count,
            kind="table",
            local_code=None,
            n_cols=n_cols,
            repeats_header=repeats_header,
            rows=[row0, row1],
        )

    return _make


@pytest.fixture(scope="function")
def make_table_cell(
    make_text_unit: Callable[..., TextUnit],
) -> Callable[..., TableCell]:
    """Factory for creating TableCell objects.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests.

    Returns
    -------
    Callable[..., TableCell]
        A factory function that creates TableCell instances with default values.
    """

    def _make(
        *, col_span: int = 1, row_span: int = 1, text: str | None = "x"
    ) -> TableCell:
        """Create a TableCell with default values, allowing overrides for col_span,
        row_span, and text.

        Parameters
        ----------
        col_span
            The number of columns this cell spans (default is 1).
        row_span
            The number of rows this cell spans (default is 1).
        text
            The text content of the cell (default is "x"). If None, the cell will
            have no text, which is only valid for non-spanned cells.

        Returns
        -------
        TableCell
            A TableCell instance with the specified col_span, row_span, and text. If
            text is None, the cell will have no text content.
        """

        text_unit = None if text is None else make_text_unit(language="en", text=text)
        return TableCell(col_span=col_span, row_span=row_span, text=text_unit)

    return _make


@pytest.fixture(scope="function")
def make_text_unit() -> Callable[..., TextUnit]:
    """Factory for creating TextUnit objects.

    Returns
    -------
    Callable[..., TextUnit]
        A factory function that creates TextUnit instances with default values. The
        factory accepts optional overrides for language and text content, allowing
        tests to easily generate TextUnit instances with specific properties while
        maintaining valid defaults.
    """

    def _make(*, language: str = "en", text: str = "hello") -> TextUnit:
        """Create a TextUnit with default values, allowing overrides for language and
        text.

        Parameters
        ----------
        language
            The language code for the text (default is "en" for English).
        text
            The actual text content (default is "hello").

        Returns
        -------
        TextUnit
            A TextUnit instance with the specified language and text.
        """

        return TextUnit(language=language, text=text)

    return _make


@PARAM(
    "block_type,fields,expected_match",
    [
        (BlockType.FIGURE, {"figure": None}, r"requires figure metadata"),
        (
            BlockType.FIGURE,
            {"figure": {"alt_text": "x"}, "text": {"text": "bad"}},
            r"requires text=null",
        ),
        (
            BlockType.FIGURE,
            {"figure": {"alt_text": "x"}, "list_items": [{"text": {"text": "bad"}}]},
            r"requires list_items=null",
        ),
        (BlockType.LIST, {"list_items": None}, r"requires non-empty list_items"),
        (
            BlockType.LIST,
            {"list_items": [{"text": {"text": "x"}}], "text": {"text": "bad"}},
            r"requires text=null",
        ),
        (BlockType.HEADING, {"text": None}, r"requires non-empty text"),
        (
            BlockType.HEADING,
            {"text": {"text": "x"}, "figure": {"alt_text": "x"}},
            r"requires figure=null",
        ),
        (
            BlockType.HEADING,
            {"text": {"text": "x"}, "list_items": [{"text": {"text": "x"}}]},
            r"requires list_items=null",
        ),
    ],
)
def test_block_enforces_block_type_specific_invariants(
    bbox: list[float], block_type: BlockType, fields: dict, expected_match: str
) -> None:
    """Block validators must enforce mutually-exclusive content by block_type.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Block instance. This is necessary
        because the Block schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the block_type-specific invariants.
    block_type
        The BlockType to test. This determines the baseline valid payload structure and
        which fields are expected to be null or non-null, which is necessary for
        testing the validation logic that enforces the mutually-exclusive content rules
        based on block_type.
    fields
        A dict of field overrides to apply on top of a valid baseline payload for the
        given block_type.
    expected_match
        A regex pattern that the ValidationError message is expected to match when the
        Block is created with the specified block_type and field overrides, which
        should trigger validation errors due to violations of the invariants that
        certain fields must be null or non-null depending on the block_type.
    """

    # Build a minimal baseline payload and patch in invalid combinations.
    payload = {
        "bbox": bbox,
        "block_type": block_type,
        "boundary": ItemBoundary.COMPLETE,
        "figure": None,
        "kind": "block",
        "list_items": None,
        "local_code": None,
        "text": {"language": "en", "text": "ok"},
    }

    if block_type == BlockType.FIGURE:
        payload["text"] = None
        payload["list_items"] = None
        payload["figure"] = {
            "alt_text": "surface description",
            "caption": None,
            "contains_text": None,
            "embedded_text": None,
            "figure_kind": "unknown",
        }

    if block_type == BlockType.LIST:
        payload["text"] = None
        payload["figure"] = None
        payload["list_items"] = [
            {"marker": "1.", "text": {"language": "en", "text": "item"}}
        ]

    # Apply invalid overrides.
    for k, v in fields.items():
        if k == "figure" and isinstance(v, dict):
            # Extract or initialize a baseline valid figure dict.
            fig_data = payload.get("figure")

            if not isinstance(fig_data, dict):
                fig_data = {
                    "alt_text": "surface description",
                    "caption": None,
                    "contains_text": None,
                    "embedded_text": None,
                    "figure_kind": "unknown",
                }

            # Update specific fields safely without complex subscripting.
            if "alt_text" in v:
                fig_data["alt_text"] = v["alt_text"]

            payload["figure"] = fig_data
        elif k == "list_items" and isinstance(v, list):
            payload["list_items"] = [
                {
                    "marker": li.get("marker", "•"),
                    "text": {"language": "en", "text": li["text"]["text"]},
                }
                for li in v
            ]
        elif k == "text" and isinstance(v, dict):
            payload["text"] = {"language": "en", "text": v["text"]}
        else:
            payload[k] = v

    with pytest.raises(ValidationError, match=expected_match):
        Block.model_validate(payload)


def test_figure_unit_caption_text_must_not_be_whitespace_only(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """If caption is present, caption.text must not be whitespace-only.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a TextUnit
        instance with whitespace-only text content to test the validation logic that
        enforces that if a caption is provided for a figure, the text of that caption
        must contain non-whitespace characters, ensuring that captions have meaningful
        content rather than being empty or consisting solely of whitespace, which would
        not be valid for a caption.
    """

    with pytest.raises(
        ValidationError, match=r"caption\.text must not be whitespace-only"
    ):
        FigureUnit(
            alt_text="diagram",
            caption=make_text_unit(language="en", text="   "),
            contains_text=None,
            embedded_text=None,
            figure_kind=FigureKind.UNKNOWN,
        )


def test_figure_unit_contains_text_false_requires_embedded_text_null(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """contains_text=false forbids embedded_text (must be null).

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests.
    """

    with pytest.raises(
        ValidationError,
        match=r"contains_text=false requires figure\.embedded_text=null",
    ):
        FigureUnit(
            alt_text="figure",
            caption=None,
            contains_text=False,
            embedded_text=make_text_unit(language="en", text="inside"),
            figure_kind=FigureKind.UNKNOWN,
        )


@PARAM(
    "contains_text,embedded_text,expected_match",
    [
        (True, None, r"contains_text=true requires figure\.embedded_text"),
        (True, "   ", r"embedded_text is whitespace-only"),
    ],
)
def test_figure_unit_contains_text_true_requires_valid_embedded_text(
    contains_text: bool,
    embedded_text: str | None,
    expected_match: str,
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """contains_text=true implies embedded_text must exist and be non-empty.

    Parameters
    ----------
    contains_text
        The value for the contains_text field in the FigureUnit being tested.
    embedded_text
        The value for the embedded_text field in the FigureUnit being tested.
    expected_match
        The regex pattern that the ValidationError message is expected to match when
        the FigureUnit is created with the specified contains_text and embedded_text
        values.
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests.
    """

    embedded = (
        None
        if embedded_text is None
        else make_text_unit(language="en", text=embedded_text)
    )

    with pytest.raises(ValidationError, match=expected_match):
        FigureUnit(
            alt_text="figure",
            caption=None,
            contains_text=contains_text,
            embedded_text=embedded,
            figure_kind=FigureKind.UNKNOWN,
        )


def test_figure_unit_equation_requires_contains_text_true() -> None:
    """Equation figure kinds must have contains_text=true (and therefore embedded_text)."""

    with pytest.raises(
        ValidationError,
        match=r"figure_kind='equation' requires figure\.contains_text=true",
    ):
        FigureUnit(
            alt_text="equation",
            caption=None,
            contains_text=False,
            embedded_text=None,
            figure_kind=FigureKind.EQUATION,
        )


def test_figure_unit_requires_non_empty_alt_text() -> None:
    """alt_text is required and must not be whitespace-only."""

    with pytest.raises(
        ValidationError, match=r"alt_text must be present and non-empty"
    ):
        FigureUnit(
            alt_text="   ",
            caption=None,
            contains_text=None,
            embedded_text=None,
            figure_kind=FigureKind.UNKNOWN,
        )


def test_list_item_marker_must_not_be_whitespace_only(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """Whitespace-only markers are invalid; use null or a real marker.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a valid
        TextUnit instance for the ListItem text field, allowing us to isolate the test
        to the marker field by ensuring that the text content is valid and does not
        cause validation errors that would interfere with testing the marker validation
        logic.
    """

    with pytest.raises(
        ValidationError, match=r"marker must be null or a non-whitespace string"
    ):
        ListItem(marker="   ", text=make_text_unit(language="en", text="x"))


def test_list_item_text_must_not_be_whitespace_only(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """ListItem.text.text must contain non-whitespace characters.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a TextUnit
        instance with whitespace-only text content to test the validation logic that
        enforces that ListItem.text.text must not be whitespace-only, ensuring that
        list items have meaningful text content rather than being empty or consisting
        solely of whitespace, which would not be valid in a list context.
    """

    with pytest.raises(
        ValidationError, match=r"List item text must not be whitespace-only"
    ):
        ListItem(marker="•", text=make_text_unit(language="en", text="   "))


def test_page_ir_union_parses_block_and_table_items(
    make_heading_block: Callable[..., Block], make_simple_table: Callable[..., Table]
) -> None:
    """PageIR.items must accept both Block and Table and parse them correctly from dicts.

    Parameters
    ----------
    make_heading_block
        A factory function for creating a valid heading Block, injected to allow for
        consistent Block creation across tests.
    make_simple_table
        A factory function for creating a simple Table instance, injected to allow for
        consistent Table creation across tests.
    """

    block = make_heading_block(text="T")
    table = make_simple_table()
    data = {
        "items": [
            block.model_dump(),
            table.model_dump(),
        ]
    }

    page_ir = PageIR.model_validate(data)

    assert len(page_ir.items) == 2
    assert isinstance(page_ir.items[0], Block)
    assert isinstance(page_ir.items[1], Table)
    assert page_ir.items[0].kind == "block"
    assert page_ir.items[1].kind == "table"


def test_table_header_row_count_cannot_exceed_row_count(
    make_simple_table: Callable[..., Table],
) -> None:
    """header_row_count must be <= number of extracted rows.

    Parameters
    ----------
    make_simple_table
        A factory function for creating a simple Table instance, injected to allow for
        consistent Table creation across tests. This is used to generate a valid Table
        instance that we can then attempt to create with an invalid header_row_count to
        test the validation behavior.
    """

    with pytest.raises(
        ValidationError, match=r"header_row_count.*cannot exceed number of rows"
    ):
        make_simple_table(header_row_count=3)


def test_table_n_cols_requires_at_least_one_row_reaches_n_cols(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """If n_cols is set, at least one row must reach n_cols to avoid underfilled grids.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance. This is necessary
        because the Table schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the n_cols field.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests. This is used to generate TableCell
        instances with specific col_span values to test the validation logic that
        checks whether at least one row in the table has a total col_span that equals
        the n_cols value for the table, which is necessary to ensure that the defined
        number of columns is actually represented in the table structure and to prevent
        cases where n_cols is set to a value that is never reached by any row, which
        would indicate an invalid or underfilled table layout that does not conform to
        the specified column constraints.
    """

    row0 = TableRow(
        cells=[
            make_table_cell(col_span=1, row_span=1, text="A"),
            make_table_cell(col_span=1, row_span=1, text="B"),
        ]
    )
    row1 = TableRow(
        cells=[
            make_table_cell(col_span=1, row_span=1, text="1"),
            make_table_cell(col_span=1, row_span=1, text="2"),
        ]
    )

    with pytest.raises(ValidationError, match=r"no row reaches that width"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=3,
            repeats_header=None,
            rows=[row0, row1],
        )


def test_table_n_cols_rejects_cell_col_span_exceeding_n_cols(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """If n_cols is set, no cell may have col_span > n_cols.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance. This is necessary
        because the Table schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the n_cols field.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests. This is used to generate TableCell
        instances with specific col_span values to test the validation logic that
        checks whether any cell's col_span exceeds the n_cols value for the table,
        which would indicate an invalid table structure where a single cell claims to
        span more columns than the total number of columns defined for the table,
        leading to an impossible layout.
    """

    row0 = TableRow(cells=[make_table_cell(col_span=3, row_span=1, text="wide")])
    row1 = TableRow(
        cells=[
            make_table_cell(col_span=1, row_span=1, text="x"),
            make_table_cell(col_span=1, row_span=1, text="y"),
        ]
    )

    with pytest.raises(ValidationError, match=r"col_span exceeds n_cols"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=2,
            repeats_header=None,
            rows=[row0, row1],
        )


def test_table_n_cols_rejects_row_width_exceeding_n_cols(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """If n_cols is set, sum(col_span) for each row must not exceed n_cols.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance. This is necessary
        because the Table schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the n_cols field.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests. This is used to generate TableCell
        instances with specific col_span values to test the validation logic that
        checks whether the total col_span for a row exceeds the n_cols value for the
        table, which would indicate an invalid table structure where a single row
        claims to span more columns than the total number of columns defined for the
        table, leading to an impossible layout and violating the defined column
        constraints for the table.
    """

    row0 = TableRow(
        cells=[
            make_table_cell(col_span=2, row_span=1, text="A"),
            make_table_cell(col_span=1, row_span=1, text="B"),
        ]
    )
    row1 = TableRow(
        cells=[
            make_table_cell(col_span=1, row_span=1, text="1"),
            make_table_cell(col_span=1, row_span=1, text="2"),
        ]
    )

    with pytest.raises(ValidationError, match=r"Row exceeds n_cols"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=2,
            repeats_header=None,
            rows=[row0, row1],
        )


def test_table_repeats_header_requires_boundary_resumed_or_both(
    make_simple_table: Callable[..., Table],
) -> None:
    """repeats_header may only be set for resumed/both tables.

    Parameters
    ----------
    make_simple_table
        A factory function for creating a simple Table instance, injected to allow for
        consistent Table creation across tests. This is used to generate Table
        instances with different boundary and repeats_header values to test the
        validation logic that enforces the relationship between these fields.
    """

    with pytest.raises(
        ValidationError, match=r"repeats_header is only allowed when boundary"
    ):
        make_simple_table(boundary=ItemBoundary.COMPLETE, repeats_header=True)

    # Sanity: allowed when RESUMED.
    table = make_simple_table(boundary=ItemBoundary.RESUMED, repeats_header=True)
    assert table.repeats_header is True


def test_table_requires_at_least_one_row(bbox: list[float]) -> None:
    """Table.rows has min_length=1 to prevent empty-table hallucinations.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance. This is necessary
        because the Table schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the rows field.
    """

    with pytest.raises(ValidationError, match=r"min_length|at least 1"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=None,
            repeats_header=None,
            rows=[],
        )


def test_table_row_requires_at_least_one_cell() -> None:
    """TableRow.cells has min_length=1 to prevent empty-row hallucinations."""

    with pytest.raises(ValidationError, match=r"min_length|at least 1"):
        TableRow(cells=[])


def test_table_row_span_cannot_run_past_bottom(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """row_span must not exceed remaining rows (bounds safety).

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance. This is necessary
        because the Table schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the row_span field.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests. This is used to generate TableCell
        instances with specific row_span values to test the validation logic that
        checks whether a cell's row_span exceeds the number of remaining rows in the
        table.
    """

    row0 = TableRow(cells=[make_table_cell(col_span=1, row_span=2, text="merged")])

    # n_rows=2, r=1 with row_span=2 would overflow; place the bad cell on row1.
    row1_bad = TableRow(cells=[make_table_cell(col_span=1, row_span=2, text="oops")])

    with pytest.raises(ValidationError, match=r"row_span exceeds table bounds"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=None,
            repeats_header=None,
            rows=[row0, row1_bad],
        )


def test_table_spanned_cells_require_text(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """Merged/spanned cells must not be text=null (prevents empty merges).

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance. This is necessary
        because the Table schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the text field in spanned cells.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests. This is used to generate TableCell
        instances with specific col_span and text values to test the validation logic
        that enforces that spanned cells must have text content (i.e., text must not be
        null) to prevent invalid empty merges that would not make sense in a table
        context.
    """

    row0 = TableRow(
        cells=[
            make_table_cell(col_span=2, row_span=1, text=None),
        ]
    )
    row1 = TableRow(
        cells=[
            make_table_cell(col_span=1, row_span=1, text="x"),
            make_table_cell(col_span=1, row_span=1, text="y"),
        ]
    )

    with pytest.raises(ValidationError, match=r"Spanned cell must not have text=null"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=2,
            repeats_header=None,
            rows=[row0, row1],
        )


def test_text_unit_rejects_extra_fields(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """BaseSchema forbids unknown fields; this must hold for extraction models too.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a valid
        TextUnit instance that we can then attempt to create with extra fields to test
        the validation behavior.
    """

    with pytest.raises(
        ValidationError, match=r"Extra inputs are not permitted|extra_forbidden"
    ):
        TextUnit(language="en", text="hi", unexpected="nope")  # type: ignore[arg-type]


def test_validation_verdict_error_issues_require_suggested_fix(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """Every severity='error' issue must include a non-empty suggested_fix.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    with pytest.raises(
        ValidationError, match=r"must include a non-empty suggested_fix"
    ):
        ValidationVerdict(
            corrected_page_ir=make_minimal_page_ir(),
            issues=[
                ValidationIssue(
                    description="Wrong classification",
                    item_index=0,
                    severity="error",
                    suggested_fix="   ",
                )
            ],
            passed=False,
            rationale="Needs correction.",
        )


def test_validation_verdict_failed_requires_at_least_one_error_issue(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """passed=false cannot be paired with warning-only issues.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    with pytest.raises(
        ValidationError, match=r"must include at least one issue.*severity='error'"
    ):
        ValidationVerdict(
            corrected_page_ir=make_minimal_page_ir(),
            issues=[
                ValidationIssue(
                    description="Slightly loose bbox",
                    item_index=0,
                    severity="warning",
                    suggested_fix=None,
                )
            ],
            passed=False,
            rationale="Only minor issues.",
        )


def test_validation_verdict_must_not_include_corrected_page_ir_when_passed(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """passed=true forbids corrected_page_ir to avoid unnecessary overhead.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    with pytest.raises(
        ValidationError, match=r"passing verdict.*must not include corrected_page_ir"
    ):
        ValidationVerdict(
            corrected_page_ir=make_minimal_page_ir(),
            issues=[],
            passed=True,
            rationale="Looks good.",
        )


def test_validation_verdict_rationale_must_be_non_empty() -> None:
    """rationale is required and must not be whitespace-only."""

    with pytest.raises(ValidationError, match=r"Rationale must be non-empty"):
        ValidationVerdict(
            corrected_page_ir=None, issues=[], passed=True, rationale="   "
        )


def test_validation_verdict_requires_corrected_page_ir_when_failed(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """passed=false requires corrected_page_ir so the pipeline can apply fixes
    immediately.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    with pytest.raises(
        ValidationError, match=r"failing verdict.*must include corrected_page_ir"
    ):
        ValidationVerdict(
            corrected_page_ir=None,
            issues=[
                ValidationIssue(
                    description="Missing content",
                    item_index=None,
                    severity="error",
                    suggested_fix="Add missing block",
                )
            ],
            passed=False,
            rationale="Material errors present.",
        )

    # Sanity: passes when corrected_page_ir is provided and issues include an error +
    # fix.
    verdict = ValidationVerdict(
        corrected_page_ir=make_minimal_page_ir(),
        issues=[
            ValidationIssue(
                description="Missing content",
                item_index=None,
                severity="error",
                suggested_fix="Add missing block",
            )
        ],
        passed=False,
        rationale="Material errors present.",
    )
    assert verdict.passed is False
