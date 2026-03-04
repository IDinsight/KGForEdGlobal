"""This is the main module for testing page_ir_verification/postprocess_page_irs.py."""

# Standard Library
from dataclasses import dataclass, field
from typing import Optional, Self

# Package Library
from skg.page_ir_verification.postprocess_page_irs import _trim_excess_cells


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
    def from_str(cls, s: Optional[str], r: int = 1, c: int = 1) -> Self:
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
        Self
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


def test__trim_excess_cells() -> None:
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


def test__trim_excess_cells_stops_at_content() -> None:
    """Test that trimming stops when it hits a cell with text."""

    cells = [
        TableCell.from_str("A"),
        TableCell.from_str("B"),  # Extra cell, but has text --> should NOT trim
    ]
    trimmed_count = _trim_excess_cells(n_cols=1, new_cells=cells)

    assert trimmed_count == 0
    assert len(cells) == 2
