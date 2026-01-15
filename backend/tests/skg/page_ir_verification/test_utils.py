"""This is the main module for testing page_ir_verification/utils.py."""

# Future Library
from __future__ import annotations

# Standard Library
from dataclasses import dataclass, field
from typing import Optional

# Package Library
from skg.page_ir_verification.utils import (
    _process_table_row,
    _trim_excess_cells,
    align_table_rows_with_rowspans,
)


@dataclass
class TextObj:
    """Mimics the nested text object structure in the JSON."""

    text: Optional[str] = None


@dataclass
class TableCell:
    """Mimics the TableCell structure in the JSON."""

    col_span: int = 1
    row_span: int = 1
    text: Optional[TextObj] = None

    @classmethod
    def from_str(cls, s: Optional[str], r: int = 1, c: int = 1) -> TableCell:
        """Utility to create a TableCell from a simple string (or None).

        Parameters
        ----------
        s : Optional[str]
            The text content of the cell. If None, represents an empty cell.
        r : int
            The row span of the cell.
        c : int
            The column span of the cell.

        Returns
        -------
        TableCell
            An instance of TableCell with the specified properties.
        """

        return cls(
            text=TextObj(text=s) if s is not None else None, row_span=r, col_span=c
        )


@dataclass
class MockRow:
    """Mimics the TableRow structure in the JSON."""

    cells: list[TableCell] = field(default_factory=list)


@dataclass
class MockItem:
    """Mimics the Table Item structure in the JSON."""

    n_cols: int = 0
    kind: str = "table"
    rows: list[MockRow] = field(default_factory=list)


@dataclass
class PageIR:
    """Mimics the PageIR structure in the JSON."""

    items: list[MockItem] = field(default_factory=list)


def get_text(cell: TableCell) -> Optional[str]:
    """Utility to extract text from a TableCell.

    Parameters
    ----------
    cell
        The TableCell from which to extract text.

    Returns
    -------
    Optional[str]
        The text content of the cell, or None if empty.
    """

    if cell.text and hasattr(cell.text, "text"):
        return cell.text.text
    return None


def test_trim_excess_cells() -> None:
    """Test that trailing empty 1x1 placeholders are removed correctly."""

    cells = [
        TableCell.from_str("A"),
        TableCell.from_str("B"),
        TableCell.from_str(None),  # Should be trimmed
        TableCell.from_str(None),  # Should be trimmed
    ]

    trimmed_count = _trim_excess_cells(n_cols=2, new_cells=cells)

    assert trimmed_count == 2
    assert len(cells) == 2
    assert get_text(cells[-1]) == "B"


def test_trim_stops_at_content() -> None:
    """Test that trimming stops when it hits a cell with text."""

    cells = [
        TableCell.from_str("A"),
        TableCell.from_str("B"),  # Extra cell, but has text --> should NOT trim
    ]
    trimmed_count = _trim_excess_cells(n_cols=1, new_cells=cells)

    assert trimmed_count == 0
    assert len(cells) == 2


def test_process_table_row_inserts_placeholder() -> None:
    """Verify that if active_span indicates a column is occupied, a placeholder is
    inserted before the next real cell.
    """

    active_span = [2, 0]  # Column 0 is occupied
    row = MockRow(cells=[TableCell.from_str("Data")])

    changes = _process_table_row(active_span=active_span, n_cols=2, row=row)

    assert len(row.cells) == 2
    assert get_text(row.cells[0]) is None, "First cell should be a placeholder"
    assert get_text(row.cells[1]) == "Data", "Second cell should be original data"
    assert changes["before_cells"] == 1
    assert changes["after_cells"] == 2


def test_tanzania_pdf_row_alignment() -> None:
    """Test the following scenario:

    Structure:

    Row A (Index 4):
      - Col 0: "2."
      - Col 1: "English" (Rowspan=2) <--- Key factor
      - Col 2: "2.1 Demonstrate..."
      - Col 3: "2.1.1 Develop..."

    Row B (Index 5 - The Problem Row):
      - Raw Data has 3 cells: [Null, "2.2 Comprehend...", "2.2.1 Comprehend..."]

    Expected Result:
      - The "English" rowspan from Row A occupies Col 1 in Row B.
      - Row B should shift right to accomodate this.
      - Result: [Null, Placeholder, "2.2...", "2.2.1..."]
    """

    # Setup Row A (the 'Header' for this section). NB: "English" has row_span=2.
    row_english = MockRow(
        cells=[
            TableCell.from_str("2."),
            TableCell.from_str("English", r=2),
            TableCell.from_str("2.1 Demonstrate..."),
            TableCell.from_str("2.1.1 Develop..."),
        ]
    )

    # Setup Row B (the row that needs fixing). NB: This row only has 3 cells. It starts
    # with a null cell (Col 0), then immediately lists the content that belongs in Col
    # 2, missing the spacer for Col 1.
    row_target = MockRow(
        cells=[
            TableCell.from_str(None),  # Belongs in Col 0
            TableCell.from_str(
                "2.2 Comprehend..."
            ),  # Belongs in Col 2 (Col 1 is English)
            TableCell.from_str("2.2.1 Comprehend..."),  # Belongs in Col 3
        ]
    )

    # Create Item and PageIR.
    item = MockItem(n_cols=4, rows=[row_english, row_target])
    page_ir = PageIR(items=[item])

    # Run the fix.
    changes = align_table_rows_with_rowspans(page_irs={15: page_ir})

    # We expect changes to have occurred on the second row (index 1 in the list).
    assert len(changes) > 0
    target_change = next((c for c in changes if c["row_index"] == 1), None)
    assert target_change is not None, "The target row should have been modified"

    # Verify the structure of the fixed row.
    fixed_cells = item.rows[1].cells

    assert len(fixed_cells) == 4, "Row should now have 4 columns"

    # Col 0: Original null.
    assert get_text(fixed_cells[0]) is None

    # Col 1: The INSERTED placeholder (due to 'English' rowspan). It should have no
    # text, row_span=1, col_span=1.
    assert get_text(fixed_cells[1]) is None
    assert fixed_cells[1].col_span == 1
    assert fixed_cells[1].row_span == 1

    # Col 2: The shifted content.
    text = get_text(fixed_cells[2])
    assert text is not None and "2.2 Comprehend" in text

    # Col 3: The last cell.
    text = get_text(fixed_cells[3])
    assert text is not None and "2.2.1 Comprehend" in text
