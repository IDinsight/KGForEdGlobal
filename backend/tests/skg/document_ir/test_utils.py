"""This is the main module for testing document_ir/utils.py."""

# Standard Library
from typing import Callable

# Third Party Library
import pytest

# Package Library
from skg.document_ir import utils
from skg.page_ir_extraction.schemas import TableCell, TableRow, TextUnit


def _table_cell(text: str | None) -> TableCell:
    """Helper to create a TableCell instance.

    Parameters
    ----------
    text
        The text content of the cell, or None for empty cells.

    Returns
    -------
    TableCell
        The created TableCell instance.
    """

    return TableCell(
        text=None if text is None else _text_unit(text), col_span=1, row_span=1
    )


def _table_row(*texts: str | None) -> TableRow:
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
    return TableRow(cells=[_table_cell(t) for t in texts])


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
        utils._join_text_unit_texts(text_units=units)
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

    assert utils._join_text_unit_texts(text_units=units) == "Note: This is important."


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

    assert utils._join_text_unit_texts(text_units=units) == "However, we found that..."


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

    assert utils._join_text_unit_texts(text_units=units_1) == "End.\nStart"

    # Ellipsis (TextUnit might split "..." across chunks, but here we assume the unit
    # ends with it).
    units_2 = create_units(["To be continued...", "Chapter 2"])

    assert (
        utils._join_text_unit_texts(text_units=units_2)
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

    assert utils._join_text_unit_texts(text_units=units_soft) == "software"

    # Compound words (uppercase follows) -> Keep hyphen, no space.
    units_compound = create_units(["Non-", "Profit"])

    assert utils._join_text_unit_texts(text_units=units_compound) == "Non-Profit"

    # Compound Words (number follows) -> Keep hyphen, no space.
    units_number = create_units(["pre-", "1990"])

    assert utils._join_text_unit_texts(text_units=units_number) == "pre-1990"


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
    expected = "The longterm plan, created by NASA, is bold.\n" "It starts...\n" "Now."

    units = create_units(inputs)
    result = utils._join_text_unit_texts(text_units=units)

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
    assert utils._join_text_unit_texts(text_units=units) == "We met John at the park."


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
    assert utils._join_text_unit_texts(text_units=units) == "Start End."


def test_fill_down_table_rows_basic_propagation_first_two_columns() -> None:
    """Test basic fill-down logic for first two grouping columns."""

    rows = [
        _table_row("Topic A", "Sub A", "Comp 1"),
        _table_row(None, None, "Comp 2"),
        _table_row(None, "Sub B", "Comp 3"),
        _table_row("Topic B", None, "Comp 4"),
        _table_row(None, None, "Comp 5"),
    ]

    out = utils.fill_down_table_rows(
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


def test_fill_down_table_rows_does_not_overwrite_non_empty_cells() -> None:
    """Test that existing non-empty cells are not overwritten during fill-down."""

    rows = [
        _table_row("Topic A", "Sub A", "Comp 1"),
        _table_row(None, "Sub B", "Comp 2"),
    ]
    out = utils.fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    # Topic fills, but Subtopic already has "Sub B" and should NOT be overwritten.
    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub B"


def test_fill_down_table_rows_group_cols_max_zero_returns_deepcopy_and_no_changes() -> (
    None
):
    """Test that group_cols_max=0 results in no fill-down and a deep copy of input."""

    rows = [
        _table_row("Topic A", "Sub A", "Comp 1"),
        _table_row(None, None, "Comp 2"),
    ]

    out = utils.fill_down_table_rows(
        table_filldown_group_cols_max=0, header_row_count=0, rows=rows
    )

    # Nothing should be filled when group_cols_max <= 0.
    assert out[1].cells[0].text is None
    assert out[1].cells[1].text is None

    # Must be deep-copied: row objects should be different identities.
    assert out is not rows
    assert out[0] is not rows[0]
    assert out[0].cells[0] is not rows[0].cells[0]


def test_fill_down_table_rows_header_rows_are_not_filled_or_used_as_source() -> None:
    """Test that header rows are skipped and not used as fill-down sources."""

    # header_row_count=1 means row 0 is a header and should not affect fill-down.
    rows = [
        _table_row("HEADER_TOPIC", "HEADER_SUB", "HEADER_COMP"),
        _table_row(None, None, "Comp 1"),
        _table_row("Topic A", None, "Comp 2"),
        _table_row(None, None, "Comp 3"),
    ]

    out = utils.fill_down_table_rows(
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


def test_fill_down_table_rows_input_is_not_mutated_and_filled_cells_are_copied() -> (
    None
):
    """Test that input rows are not mutated and filled TextUnits are copies."""

    rows = [
        _table_row("Topic A", "Sub A", "Comp 1"),
        _table_row(None, None, "Comp 2"),
    ]

    out = utils.fill_down_table_rows(
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


def test_fill_down_table_rows_only_first_group_cols_filled_not_other_columns() -> None:
    """Test that only the specified number of grouping columns are filled down."""

    rows = [
        _table_row("Topic A", "Sub A", "Comp 1"),
        _table_row(None, None, None),  # Competence column is empty too
    ]

    out = utils.fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    # First 2 columns fill down.
    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub A"

    # Third column should NOT be filled down (group_cols_max=2).
    assert out[1].cells[2].text is None


def test_fill_down_table_rows_short_rows_do_not_crash_and_fill_only_existing_cells() -> (
    None
):
    """Test that rows with fewer cells than group_cols_max do not cause errors."""

    # Second row has only 1 cell; group_cols_max=2 should not index error.
    rows = [
        _table_row("Topic A", "Sub A"),
        TableRow(cells=[_table_cell(None)]),
    ]

    out = utils.fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    assert out[1].cells[0].text.text == "Topic A"
    assert len(out[1].cells) == 1


def test_fill_down_table_rows_whitespace_text_is_treated_as_empty_and_filled() -> None:
    """Test that cells with only whitespace are treated as empty and filled down."""

    rows = [
        _table_row("Topic A", "Sub A", "Comp 1"),
        _table_row("   ", "", "Comp 2"),  # Whitespace/empty strings count as empty
    ]

    out = utils.fill_down_table_rows(
        table_filldown_group_cols_max=2, header_row_count=0, rows=rows
    )

    assert out[1].cells[0].text.text == "Topic A"
    assert out[1].cells[1].text.text == "Sub A"
