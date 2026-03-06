"""This is the main module for testing page_ir_extraction/schemas.py."""

# Standard Library
from collections.abc import Callable

# Third Party Library
import pytest

from pydantic import ValidationError

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    ExtractionValidationIssue,
    ExtractionValidationVerdict,
    FigureUnit,
    ListItem,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
    _next_free_column,
    _occupied_columns_for_span,
    validate_validation_verdict_state,
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
        header_row_count: int = 1,
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


class TestNextFreeColumn:
    """Tests for _next_free_column."""

    def test_empty_set_returns_zero(self) -> None:
        """If no columns are occupied, the next free column should be 0."""

        assert _next_free_column(set()) == 0

    def test_skips_occupied(self) -> None:
        """If columns 0, 1, and 2 are occupied, the next free column should be 3."""

        assert _next_free_column({0, 1, 2}) == 3

    def test_finds_gap(self) -> None:
        """If columns 0 and 2 are occupied, the next free column should be 1."""

        assert _next_free_column({0, 2}) == 1


class TestOccupiedColumnsForSpan:
    """Tests for _occupied_columns_for_span."""

    def test_single_column(self) -> None:
        """A cell with col_span=1 starting at column 0 should occupy only column 0."""

        assert _occupied_columns_for_span(col_span=1, start_col=0) == {0}

    def test_multi_column(self) -> None:
        """A cell with col_span=3 starting at column 2 should occupy columns 2, 3, and
        4.
        """

        assert _occupied_columns_for_span(col_span=3, start_col=2) == {2, 3, 4}


class TestValidateValidationVerdictState:
    """Tests for the shared validate_validation_verdict_state utility."""

    def test_passing_with_corrected_raises(self) -> None:
        """A passing verdict must not include a corrected output, since that implies a
        change was made to fix an error. If the output is correct, there should be no
        change needed, and therefore no corrected output should be present.
        """

        with pytest.raises(ValueError, match="must not include a corrected output"):
            validate_validation_verdict_state(
                corrected_present=True, issues=[], passed=True
            )

    def test_passing_with_error_issues_raises(self) -> None:
        """A passing verdict must not include any error-severity issues, since that
        would contradict the notion of passing. If there are error-severity issues
        present, it indicates that there are problems that need to be addressed, which
        is incompatible with a passing verdict.
        """

        issue = type("I", (), {"severity": "error"})()

        with pytest.raises(ValueError, match="must not include any issue.*error"):
            validate_validation_verdict_state(
                corrected_present=False, issues=[issue], passed=True
            )

    def test_failing_without_corrected_raises(self) -> None:
        """A failing verdict must include a corrected output, since the presence of a
        failure implies that there is an issue that needs to be addressed, and the
        corrected output represents the necessary change to fix the error.
        """

        issue = type("I", (), {"severity": "error"})()

        with pytest.raises(ValueError, match="must include a corrected output"):
            validate_validation_verdict_state(
                corrected_present=False, issues=[issue], passed=False
            )

    def test_failing_without_issues_raises(self) -> None:
        """A failing verdict must include at least one issue, since the failure is
        meant to indicate that there is a problem that needs to be fixed, and without
        any issues provided, there would be no information about what the problem is or
        how to address it.
        """

        with pytest.raises(ValueError, match="must include at least one issue"):
            validate_validation_verdict_state(
                corrected_present=True, issues=[], passed=False
            )

    def test_failing_with_only_warnings_raises(self) -> None:
        """A failing verdict must include at least one error-severity issue, since
        warnings indicate potential problems but do not necessarily imply a failure.
        """

        issue = type("I", (), {"severity": "warning"})()

        with pytest.raises(ValueError, match="at least one issue.*severity='error'"):
            validate_validation_verdict_state(
                corrected_present=True, issues=[issue], passed=False
            )

    def test_passing_with_warnings_is_ok(self) -> None:
        """Passing verdict may include warning-severity issues."""

        issue = type("I", (), {"severity": "warning"})()

        # Should not raise.
        validate_validation_verdict_state(
            corrected_present=False, issues=[issue], passed=True
        )

    def test_failing_valid(self) -> None:
        """A well-formed failing verdict should not raise."""

        issue = type("I", (), {"severity": "error"})()
        validate_validation_verdict_state(
            corrected_present=True, issues=[issue], passed=False
        )


def test_block_artifact_whitespace_only_text_rejected(
    bbox: list[float], make_text_unit: Callable[..., TextUnit]
) -> None:
    """Artifact block with whitespace-only text should be rejected.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Block instance. This is necessary
        because the Block schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the text content of an artifact block.
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a TextUnit
        instance with whitespace-only text content to test the validation logic that
        enforces that if a Block is of type ARTIFACT, its text content must not be
        whitespace-only, ensuring that artifact blocks have meaningful text content
        rather than being empty or consisting solely of whitespace, which would not be
        valid for an artifact block.
    """

    with pytest.raises(ValidationError, match="requires non-empty text"):
        Block(
            bbox=bbox,
            block_type=BlockType.ARTIFACT,
            boundary=ItemBoundary.COMPLETE,
            figure=None,
            kind="block",
            list_items=None,
            local_code=None,
            text=make_text_unit(language="en", text="  \t  "),
        )


def test_block_caption_whitespace_only_text_rejected(
    bbox: list[float], make_text_unit: Callable[..., TextUnit]
) -> None:
    """Caption block with whitespace-only text should be rejected.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Block instance. This is necessary
        because the Block schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the text content of a caption block.
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a TextUnit
        instance with whitespace-only text content to test the validation logic that
        enforces that if a Block is of type CAPTION, its text content must not be
        whitespace-only, ensuring that caption blocks have meaningful text content
        rather than being empty or consisting solely of whitespace, which would not be
        valid for a caption block.
    """

    with pytest.raises(ValidationError, match="requires non-empty text"):
        Block(
            bbox=bbox,
            block_type=BlockType.CAPTION,
            boundary=ItemBoundary.COMPLETE,
            figure=None,
            kind="block",
            list_items=None,
            local_code=None,
            text=make_text_unit(language="en", text="\n"),
        )


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


def test_block_list_with_figure_rejected(
    bbox: list[float], make_text_unit: Callable[..., TextUnit]
) -> None:
    """List block with figure set should be rejected.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Block instance. This is necessary
        because the Block schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the block_type-specific invariants.
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a valid
        TextUnit instance for the ListItem text field, allowing us to isolate the test
        to the figure field by ensuring that the list item text content is valid and
        does not cause validation errors that would interfere with testing the figure
        validation logic that enforces that if a Block is of type LIST, the figure
        field must be null, since list blocks should not contain figure metadata, and
        the presence of figure metadata in a list block would violate the schema's
        rules for mutually-exclusive content based on block_type.
    """

    with pytest.raises(ValidationError, match="requires figure=null"):
        Block(
            bbox=bbox,
            block_type=BlockType.LIST,
            boundary=ItemBoundary.COMPLETE,
            figure=FigureUnit(
                alt_text="x",
                caption=None,
                contains_text=None,
                embedded_text=None,
                figure_kind=FigureKind.UNKNOWN,
            ),
            kind="block",
            list_items=[
                ListItem(
                    marker="•",
                    text=make_text_unit(language="en", text="item"),
                )
            ],
            local_code=None,
            text=None,
        )


def test_block_paragraph_whitespace_only_text_rejected(
    bbox: list[float], make_text_unit: Callable[..., TextUnit]
) -> None:
    """Paragraph block with whitespace-only text should be rejected.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Block instance. This is necessary
        because the Block schema requires a bbox field, and we need to provide a valid
        one to reach the validation logic for the text content of a paragraph block.
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a TextUnit
        instance with whitespace-only text content to test the validation logic that
        enforces that if a Block is of type PARAGRAPH, its text content must not be
        whitespace-only, ensuring that paragraph blocks have meaningful text content
        rather than being empty or consisting solely of whitespace, which would not be
        valid for a paragraph block.
    """

    with pytest.raises(ValidationError, match="requires non-empty text"):
        Block(
            bbox=bbox,
            block_type=BlockType.PARAGRAPH,
            boundary=ItemBoundary.COMPLETE,
            figure=None,
            kind="block",
            list_items=None,
            local_code=None,
            text=make_text_unit(language="en", text="   "),
        )


def test_block_valid_figure(bbox: list[float]) -> None:
    """A valid figure block should be accepted.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Block instance. This is necessary
        because the Block schema requires a bbox field, and we need to provide a valid
        one to create a valid figure block, which allows us to test that a Block of
        type FIGURE with appropriate figure metadata is accepted as valid according to
        the schema.
    """

    block = Block(
        bbox=bbox,
        block_type=BlockType.FIGURE,
        boundary=ItemBoundary.COMPLETE,
        figure=FigureUnit(
            alt_text="flowchart",
            caption=None,
            contains_text=False,
            embedded_text=None,
            figure_kind=FigureKind.DIAGRAM,
        ),
        kind="block",
        list_items=None,
        local_code=None,
        text=None,
    )
    assert block.figure is not None


def test_block_valid_list(
    bbox: list[float], make_text_unit: Callable[..., TextUnit]
) -> None:
    """A valid list block should be accepted.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Block instance. This is necessary
        because the Block schema requires a bbox field, and we need to provide a valid
        one to create a valid list block, which allows us to test that a Block of type
        LIST with appropriate list items is accepted as valid according to the schema.
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests.
    """

    block = Block(
        bbox=bbox,
        block_type=BlockType.LIST,
        boundary=ItemBoundary.COMPLETE,
        figure=None,
        kind="block",
        list_items=[
            ListItem(marker="1.", text=make_text_unit(language="en", text="First")),
            ListItem(marker="2.", text=make_text_unit(language="en", text="Second")),
        ],
        local_code=None,
        text=None,
    )
    assert len(block.list_items) == 2


def test_figure_contains_text_none_with_embedded_text_rejected(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """contains_text=None with embedded_text present should be rejected.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests.
    """

    with pytest.raises(
        ValidationError,
        match="contains_text=null requires figure.embedded_text=null",
    ):
        FigureUnit(
            alt_text="chart",
            caption=None,
            contains_text=None,
            embedded_text=make_text_unit(language="en", text="some text"),
            figure_kind=FigureKind.UNKNOWN,
        )


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


def test_figure_valid_with_text(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """A valid FigureUnit with contains_text=True and embedded_text should be accepted.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests.
    """

    fig = FigureUnit(
        alt_text="bar chart with labels",
        caption=make_text_unit(language="en", text="Figure 1: Revenue"),
        contains_text=True,
        embedded_text=make_text_unit(language="en", text="Q1 Q2 Q3"),
        figure_kind=FigureKind.CHART,
    )
    assert fig.contains_text is True
    assert fig.embedded_text is not None


def test_figure_valid_without_text() -> None:
    """A valid FigureUnit with no text should be accepted."""

    fig = FigureUnit(
        alt_text="decorative image",
        caption=None,
        contains_text=False,
        embedded_text=None,
        figure_kind=FigureKind.TIMELINE,
    )
    assert fig.contains_text is False


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


def test_list_item_valid_with_null_marker(
    make_text_unit: Callable[..., TextUnit],
) -> None:
    """A ListItem with marker=None should be accepted (e.g., TOC entries).

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a valid
        TextUnit instance for the ListItem text field, allowing us to test that a
        ListItem with marker=None is accepted as valid when the text content is valid,
        which is important for cases like table of contents entries where a marker may
        not be present.
    """

    item = ListItem(marker=None, text=make_text_unit(language="en", text="Chapter 1"))
    assert item.marker is None


def test_list_item_valid_with_marker(make_text_unit: Callable[..., TextUnit]) -> None:
    """A ListItem with a real marker should be accepted.

    Parameters
    ----------
    make_text_unit
        A factory function for creating TextUnit instances, injected to allow for
        consistent TextUnit creation across tests. This is used to generate a valid
        TextUnit instance for the ListItem text field, allowing us to test that a
        ListItem with a real marker (e.g., "•") is accepted as valid when the text
        content is valid, which is important for typical list items that include a
        marker.
    """

    item = ListItem(marker="•", text=make_text_unit(language="en", text="Bullet"))
    assert item.marker == "•"


def test_page_ir_defaults(make_heading_block: Callable[..., Block]) -> None:
    """PageIR optional fields should default correctly.

    Parameters
    ----------
    make_heading_block
        A factory function for creating a valid heading Block, injected to allow for
        consistent Block creation across tests. This is used to create a valid PageIR
        instance with minimal required fields, allowing us to test that all optional
        fields in the PageIR schema default to None or the expected default value when
        not provided.
    """

    page = PageIR(items=[make_heading_block(text="Title")])
    assert page.coord_space == "px"
    assert page.doc_key is None
    assert page.dpi is None
    assert page.image_height is None
    assert page.image_width is None
    assert page.page_index is None
    assert page.pdf_name is None


def test_page_ir_empty_items() -> None:
    """PageIR with empty items list should be accepted (blank pages exist)."""

    page = PageIR(items=[])
    assert page.items == []


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


def test_table_header_row_count_error_includes_local_code(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """header_row_count error message should include local_code when present.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests. This is used to generate a valid
        Table instance with a local_code value and an invalid header_row_count, allowing
        us to test that the ValidationError message includes the local_code value when
        header_row_count exceeds the number of rows, which is important for providing
        clear and specific error messages that help identify which table has the issue
        when multiple tables are present.
    """

    row0 = TableRow(cells=[make_table_cell(text="A")])

    with pytest.raises(ValidationError, match="local_code='Table 1.2'"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=5,
            kind="table",
            local_code="Table 1.2",
            n_cols=None,
            repeats_header=None,
            rows=[row0],
        )


def test_table_n_cols_none_skips_column_checks(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """When n_cols is None, uneven row widths are allowed.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests.
    """

    row0 = TableRow(cells=[make_table_cell(text="A"), make_table_cell(text="B")])
    row1 = TableRow(
        cells=[
            make_table_cell(text="1"),
            make_table_cell(text="2"),
            make_table_cell(text="3"),
        ]
    )

    # Should not raise.
    table = Table(
        bbox=bbox,
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        local_code=None,
        n_cols=None,
        repeats_header=None,
        rows=[row0, row1],
    )
    assert len(table.rows) == 2


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


def test_table_placed_cell_exceeds_n_cols(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """A cell whose placement + col_span exceeds n_cols should be rejected.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests.
    """

    # 3 cells of col_span=1 in a row with n_cols=2.
    row0 = TableRow(
        cells=[
            make_table_cell(col_span=1, row_span=1, text="A"),
            make_table_cell(col_span=1, row_span=1, text="B"),
            make_table_cell(col_span=1, row_span=1, text="C"),
        ]
    )

    with pytest.raises(ValidationError, match="Placed cell exceeds n_cols"):
        Table(
            bbox=bbox,
            boundary=ItemBoundary.COMPLETE,
            header_row_count=0,
            kind="table",
            local_code=None,
            n_cols=2,
            repeats_header=None,
            rows=[row0],
        )


def test_table_repeats_header_allowed_with_both_boundary(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """repeats_header should be allowed when boundary is BOTH.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests.
    """

    row0 = TableRow(cells=[make_table_cell(text="H1"), make_table_cell(text="H2")])
    row1 = TableRow(cells=[make_table_cell(text="1"), make_table_cell(text="2")])

    table = Table(
        bbox=bbox,
        boundary=ItemBoundary.BOTH,
        header_row_count=1,
        kind="table",
        local_code=None,
        n_cols=2,
        repeats_header=True,
        rows=[row0, row1],
    )
    assert table.repeats_header is True


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


def test_table_repeats_header_true_does_not_requires_header_rows(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """repeats_header=True with header_row_count=0 should NOT be rejected.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests.
    """

    row0 = TableRow(cells=[make_table_cell(text="A"), make_table_cell(text="B")])
    row1 = TableRow(cells=[make_table_cell(text="1"), make_table_cell(text="2")])
    Table(
        bbox=bbox,
        boundary=ItemBoundary.RESUMED,
        header_row_count=0,
        kind="table",
        local_code=None,
        n_cols=2,
        repeats_header=True,
        rows=[row0, row1],
    )


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


def test_table_valid_with_row_and_col_spans(
    bbox: list[float], make_table_cell: Callable[..., TableCell]
) -> None:
    """A table with valid row/col spans should be accepted.

    Parameters
    ----------
    bbox
        A valid bounding box to use for creating the Table instance.
    make_table_cell
        A factory function for creating TableCell instances, injected to allow for
        consistent TableCell creation across tests.
    """

    # 3x3 grid: cell (0,0) spans 2 rows, cell (0,1) spans 2 cols.
    row0 = TableRow(
        cells=[
            make_table_cell(col_span=1, row_span=2, text="A"),
            make_table_cell(col_span=2, row_span=1, text="BC"),
        ]
    )
    row1 = TableRow(
        cells=[
            # col 0 occupied by row span; cells go to col 1, col 2.
            make_table_cell(col_span=1, row_span=1, text="D"),
            make_table_cell(col_span=1, row_span=1, text="E"),
        ]
    )

    table = Table(
        bbox=bbox,
        boundary=ItemBoundary.COMPLETE,
        header_row_count=0,
        kind="table",
        local_code=None,
        n_cols=3,
        repeats_header=None,
        rows=[row0, row1],
    )
    assert len(table.rows) == 2


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
        ExtractionValidationVerdict(
            corrected_page_ir=make_minimal_page_ir(),
            issues=[
                ExtractionValidationIssue(
                    description="Wrong classification",
                    item_index=0,
                    severity="error",
                    suggested_fix="   ",
                )
            ],
            passed=False,
            rationale="Needs correction.asdkufhdashgads;ghdasjkhgafua;gha;lghasgh;jasfhgaskfjd",
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
        ExtractionValidationVerdict(
            corrected_page_ir=make_minimal_page_ir(),
            issues=[
                ExtractionValidationIssue(
                    description="Slightly loose bbox",
                    item_index=0,
                    severity="warning",
                    suggested_fix=None,
                )
            ],
            passed=False,
            rationale="Only minor issues.ourghrhgrakegheragkeeklrbrekbherl;hghdgsa;djghasklgag",
        )


def test_verdict_failing_requires_non_empty_issues(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """A failing verdict must include at least one issue.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    with pytest.raises(ValidationError, match="must include at least one issue"):
        ExtractionValidationVerdict(
            corrected_page_ir=make_minimal_page_ir(),
            issues=[],
            passed=False,
            rationale="Needs work" + "x" * 60,
        )


def test_verdict_passing_rejects_corrected_page_ir(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """A passing verdict must not include a corrected_page_ir.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    with pytest.raises(ValidationError, match="must not include a corrected output"):
        ExtractionValidationVerdict(
            corrected_page_ir=make_minimal_page_ir(),
            issues=[],
            passed=True,
            rationale="All good" + "x" * 60,
        )


def test_verdict_passing_rejects_error_severity_issues(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """A passing verdict must not include any error-severity issue.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    with pytest.raises(ValidationError, match="must not include any issue"):
        ExtractionValidationVerdict(
            corrected_page_ir=None,
            issues=[
                ExtractionValidationIssue(
                    description="Bad",
                    item_index=0,
                    severity="error",
                    suggested_fix="Fix it",
                )
            ],
            passed=True,
            rationale="Looks fine" + "x" * 60,
        )


def test_verdict_rationale_min_length_enforced() -> None:
    """Rationale must be at least 50 characters."""

    with pytest.raises(ValidationError, match="min_length|at least 50"):
        ExtractionValidationVerdict(
            corrected_page_ir=None,
            issues=[],
            passed=True,
            rationale="Short",
        )


def test_verdict_valid_failing(
    make_minimal_page_ir: Callable[..., PageIR],
) -> None:
    """A well-formed failing verdict should be accepted.

    Parameters
    ----------
    make_minimal_page_ir
        A factory function for creating a minimal valid PageIR, injected to allow for
        consistent PageIR creation across tests.
    """

    v = ExtractionValidationVerdict(
        corrected_page_ir=make_minimal_page_ir(),
        issues=[
            ExtractionValidationIssue(
                description="Wrong classification",
                item_index=0,
                severity="error",
                suggested_fix="Change block_type to heading",
            )
        ],
        passed=False,
        rationale="Classification error on first item needs correction" + "x" * 20,
    )
    assert v.passed is False
    assert v.corrected_page_ir is not None


def test_verdict_valid_passing() -> None:
    """A well-formed passing verdict should be accepted."""

    v = ExtractionValidationVerdict(
        corrected_page_ir=None,
        issues=[
            ExtractionValidationIssue(
                description="Slightly off bbox",
                item_index=0,
                severity="warning",
                suggested_fix=None,
            )
        ],
        passed=True,
        rationale="Extraction is faithful with only minor bbox imprecision" + "x" * 20,
    )
    assert v.passed is True
