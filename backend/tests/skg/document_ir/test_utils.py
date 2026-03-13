"""This is the main module for testing document_ir/utils.py."""

# Standard Library
from typing import Optional
from unittest.mock import MagicMock

# Third Party Library
import pytest

# Package Library
from skg.document_ir.utils import (
    ParsedCaptionCode,
    _normalize_caption_identifier,
    _strip_caption_trailing_separator,
    assert_page_items_consumed_exactly_once,
    compatible_kinds_for_stitch,
    extract_table_or_figure_local_code,
    normalize_local_code,
    normalize_text,
    parse_caption_code,
    row_signature,
)
from skg.page_ir_extraction.schemas import Block, Table, TableCell, TableRow, TextUnit
from skg.utils.constants import BlockType


def make_block(
    *,
    block_type: BlockType = BlockType.PARAGRAPH,
    local_code: Optional[str] = None,
    text: Optional[str] = None,
) -> MagicMock:
    """Create a lightweight `Block`-like mock suitable for stitching tests.

    Parameters
    ----------
    block_type
        The block type enum value.
    local_code
        Optional local code string.
    text
        Optional text content; when provided, a real `TextUnit` is attached.

    Returns
    -------
    MagicMock
        A `Block` mock with specified attributes.
    """

    mock = MagicMock(spec=Block)
    mock.block_type = block_type
    mock.local_code = local_code
    mock.text = make_text_unit(text=text) if text is not None else None
    return mock


def make_segment_stub(
    *, provenance_keys: list[tuple[int, int]], segment_id: str = "seg-1"
) -> MagicMock:
    """Create a minimal segment-like object with `segment_provenance`.

    Parameters
    ----------
    provenance_keys
        List of `(page_index, item_index)` tuples.
    segment_id
        Identifier for the segment.

    Returns
    -------
    MagicMock
        A mock with `segment_id` and `segment_provenance` attributes.
    """

    provenance_list = []

    for page_idx, item_idx in provenance_keys:
        prov = MagicMock()
        prov.page_index = page_idx
        prov.item_index = item_idx
        provenance_list.append(prov)

    seg = MagicMock()
    seg.segment_id = segment_id
    seg.segment_provenance = provenance_list
    return seg


def make_table_item(*, local_code: Optional[str] = None) -> MagicMock:
    """Create a lightweight `Table`-like mock.

    Parameters
    ----------
    local_code
        Optional local code string.

    Returns
    -------
    MagicMock
        A `Table` mock.
    """

    mock = MagicMock(spec=Table)
    mock.local_code = local_code
    return mock


def make_table_row(cell_texts: list[Optional[str]]) -> TableRow:
    """Create a `TableRow` from a list of cell text values.

    Parameters
    ----------
    cell_texts
        Ordered cell texts; `None` produces an empty cell.

    Returns
    -------
    TableRow
        A populated `TableRow`.
    """

    cells = []

    for ct in cell_texts:
        if ct is not None:
            cells.append(TableCell(text=make_text_unit(text=ct)))
        else:
            cells.append(TableCell(text=None))

    return TableRow(cells=cells)


def make_text_unit(*, language: str = "en", text: str) -> TextUnit:
    """Create a `TextUnit` with sensible defaults.

    Parameters
    ----------
    language
        BCP-47 language code.
    text
        The text content.

    Returns
    -------
    TextUnit
        A populated `TextUnit`.
    """

    return TextUnit(language=language, text=text)


class TestAssertPageItemsConsumedExactlyOnce:
    """Tests for `assert_page_items_consumed_exactly_once`."""

    def test_all_items_consumed_exactly_once(self) -> None:
        """No error is raised when every item is consumed exactly once."""

        block = make_block()
        items_mapping: dict[int, list[tuple[int, MagicMock]]] = {
            0: [(0, block), (1, block)],
            1: [(0, block)],
        }
        segments = [
            make_segment_stub(provenance_keys=[(0, 0), (0, 1), (1, 0)], segment_id="s1")
        ]

        # Should not raise.
        assert_page_items_consumed_exactly_once(
            items_mapping=items_mapping, segments=segments
        )

    def test_duplicate_consumption_raises(self) -> None:
        """Duplicate consumption of the same item raises `ValueError`."""

        block = make_block()
        items_mapping: dict[int, list[tuple[int, MagicMock]]] = {0: [(0, block)]}
        segments = [
            make_segment_stub(provenance_keys=[(0, 0)], segment_id="s1"),
            make_segment_stub(provenance_keys=[(0, 0)], segment_id="s2"),
        ]

        with pytest.raises(ValueError, match="Duplicates"):
            assert_page_items_consumed_exactly_once(
                items_mapping=items_mapping, segments=segments
            )

    def test_empty_mapping_and_segments(self) -> None:
        """Empty mapping and empty segments is a valid (vacuous) case."""

        assert_page_items_consumed_exactly_once(items_mapping={}, segments=[])

    def test_extra_items_raises(self) -> None:
        """Consuming items not in the mapping raises `ValueError`."""

        items_mapping: dict[int, list[tuple[int, MagicMock]]] = {}
        segments = [make_segment_stub(provenance_keys=[(5, 5)], segment_id="s1")]

        with pytest.raises(ValueError, match="Extra"):
            assert_page_items_consumed_exactly_once(
                items_mapping=items_mapping, segments=segments
            )

    def test_missing_items_raises(self) -> None:
        """Unconsumed items raise `ValueError`."""
        block = make_block()
        items_mapping: dict[int, list[tuple[int, MagicMock]]] = {
            0: [(0, block), (1, block)]
        }
        segments = [make_segment_stub(provenance_keys=[(0, 0)], segment_id="s1")]

        with pytest.raises(ValueError, match="Missing"):
            assert_page_items_consumed_exactly_once(
                items_mapping=items_mapping, segments=segments
            )


class TestCompatibleKindsForStitch:
    """Tests for `compatible_kinds_for_stitch`."""

    def test_block_vs_table_incompatible(self) -> None:
        """A `Block` and a `Table` are never stitch-compatible."""

        assert (
            compatible_kinds_for_stitch(
                next_item=make_table_item(), prev_item=make_block()
            )
            is False
        )

    def test_caption_different_codes_incompatible(self) -> None:
        """Captions with different local codes are incompatible."""

        prev = make_block(
            block_type=BlockType.CAPTION, local_code="Table 4", text="Table 4 title"
        )
        nxt = make_block(
            block_type=BlockType.CAPTION, local_code="Figure 1", text="Figure 1 title"
        )
        assert compatible_kinds_for_stitch(next_item=nxt, prev_item=prev) is False

    def test_caption_same_code_compatible(self) -> None:
        """Captions with matching local codes are stitch-compatible."""

        prev = make_block(
            block_type=BlockType.CAPTION, local_code="Table 4", text="Table 4 title"
        )
        nxt = make_block(
            block_type=BlockType.CAPTION, local_code="Table 4", text="Table 4 continued"
        )
        assert compatible_kinds_for_stitch(next_item=nxt, prev_item=prev) is True

    def test_heading_never_stitches(self) -> None:
        """Headings are never stitch-compatible with anything."""

        heading = make_block(block_type=BlockType.HEADING, text="Section 1")
        paragraph = make_block(block_type=BlockType.PARAGRAPH, text="hello")
        assert (
            compatible_kinds_for_stitch(next_item=heading, prev_item=paragraph) is False
        )
        assert (
            compatible_kinds_for_stitch(next_item=paragraph, prev_item=heading) is False
        )

    def test_paragraph_list_fallback(self) -> None:
        """Paragraph and list types are allowed as a fallback pair."""

        para = make_block(block_type=BlockType.PARAGRAPH, text="hello")
        lst = make_block(block_type=BlockType.LIST, text="item")
        assert compatible_kinds_for_stitch(next_item=lst, prev_item=para) is True
        assert compatible_kinds_for_stitch(next_item=para, prev_item=lst) is True

    def test_paragraph_same_type_compatible(self) -> None:
        """Two paragraphs are stitch-compatible."""

        a = make_block(block_type=BlockType.PARAGRAPH, text="hello")
        b = make_block(block_type=BlockType.PARAGRAPH, text="world")
        assert compatible_kinds_for_stitch(next_item=b, prev_item=a) is True

    def test_table_table_compatible(self) -> None:
        """Two `Table` items are stitch-compatible."""

        assert (
            compatible_kinds_for_stitch(
                next_item=make_table_item(), prev_item=make_table_item()
            )
            is True
        )


class TestExtractTableOrFigureLocalCode:
    """Tests for `extract_table_or_figure_local_code`."""

    def test_empty_string(self) -> None:
        """Empty text returns `None`."""

        assert extract_table_or_figure_local_code(text="") is None

    def test_figure_prefix(self) -> None:
        """Figure prefix produces a canonical `'Figure N'` code."""

        assert extract_table_or_figure_local_code(text="Fig. 3 caption") == "Figure 3"

    def test_no_match(self) -> None:
        """Text without a caption prefix returns `None`."""

        assert extract_table_or_figure_local_code(text="Just some text") is None

    def test_roman_numeral(self) -> None:
        """Roman numeral identifiers are preserved in raw form."""

        assert (
            extract_table_or_figure_local_code(text="Table III: contents")
            == "Table III"
        )

    def test_table_prefix(self) -> None:
        """Table prefix produces a canonical `'Table N'` code."""

        assert (
            extract_table_or_figure_local_code(text="Table 2.1 some desc")
            == "Table 2.1"
        )


class TestNormalizeCaptionIdentifier:
    """Tests for `_normalize_caption_identifier`."""

    def test_basic_numeric(self) -> None:
        """Numeric identifiers are returned unchanged after casefolding."""

        assert _normalize_caption_identifier(identifier="1") == "1"

    def test_casefold(self) -> None:
        """Uppercase letters are casefolded."""

        assert _normalize_caption_identifier(identifier="III") == "iii"

    def test_dotted_identifier(self) -> None:
        """Dotted sub-identifiers are preserved (whitespace stripped)."""

        assert _normalize_caption_identifier(identifier="2.1.A") == "2.1.a"

    def test_empty_string(self) -> None:
        """Empty string returns empty string."""

        assert _normalize_caption_identifier(identifier="") == ""

    def test_internal_whitespace_removed(self) -> None:
        """Internal whitespace is collapsed away entirely."""

        assert _normalize_caption_identifier(identifier="1 . 2") == "1.2"

    def test_mixed_case_with_spaces(self) -> None:
        """Mixed case and spaces are normalised together."""

        assert _normalize_caption_identifier(identifier=" A b ") == "ab"

    def test_none_input(self) -> None:
        """`None` is treated as empty and returns empty string."""

        assert _normalize_caption_identifier(identifier=None) == ""  # type: ignore[arg-type]


class TestNormalizeLocalCode:
    """Tests for `normalize_local_code`."""

    def test_casefold(self) -> None:
        """Mixed-case input is casefolded."""

        assert normalize_local_code(local_code="Table 1") == "table 1"

    def test_collapses_whitespace(self) -> None:
        """Internal whitespace is collapsed to a single space."""

        assert normalize_local_code(local_code="Table   2") == "table 2"

    def test_empty_string_returns_none(self) -> None:
        """Empty string returns `None`."""

        assert normalize_local_code(local_code="") is None

    def test_none_returns_none(self) -> None:
        """`None` input returns `None`."""

        assert normalize_local_code(local_code=None) is None

    def test_strips_surrounding_whitespace(self) -> None:
        """Leading/trailing whitespace is removed."""

        assert normalize_local_code(local_code="  Figure 5  ") == "figure 5"

    def test_whitespace_only_returns_none(self) -> None:
        """Whitespace-only string returns `None`."""

        assert normalize_local_code(local_code="   ") is None


class TestNormalizeText:
    """Tests for `normalize_text`."""

    def test_collapses_whitespace(self) -> None:
        """Multiple spaces are collapsed to one."""

        assert normalize_text(text="hello   world") == "hello world"

    def test_empty_string(self) -> None:
        """Empty string returns empty string."""

        assert normalize_text(text="") == ""

    def test_lowercases(self) -> None:
        """Text is lowercased."""

        assert normalize_text(text="HELLO") == "hello"

    def test_newlines_collapsed(self) -> None:
        """Newlines and tabs are collapsed into a single space."""

        assert normalize_text(text="a\n\tb") == "a b"

    def test_nfkc_normalization(self) -> None:
        """NFKC unicode normalization is applied (e.g., ﬁ ligature)."""

        assert normalize_text(text="\ufb01gure") == "figure"

    def test_none_returns_empty(self) -> None:
        """`None` input returns empty string."""

        assert normalize_text(text=None) == ""

    def test_strips_edges(self) -> None:
        """Leading and trailing whitespace is stripped."""

        assert normalize_text(text="  hi  ") == "hi"


class TestParseCaptionCode:
    """Tests for `parse_caption_code`."""

    def test_dotted_identifier(self) -> None:
        """Dotted numeric identifiers are recognised."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(
            text="Table 2.1: Description"
        )
        assert result is not None
        assert result.identifier_raw == "2.1"
        assert result.kind == "table"

    def test_empty_string(self) -> None:
        """Empty text returns `None`."""

        assert parse_caption_code(text="") is None

    def test_figure_match(self) -> None:
        """`'Figure N'` text is parsed as a figure caption."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(
            text="Figure 5 - Title"
        )
        assert result is not None
        assert result.identifier_raw == "5"
        assert result.kind == "figure"
        assert result.prefix_raw == "Figure"

    def test_french_tableau_prefix(self) -> None:
        """French `'Tableau'` prefix is parsed as a table caption."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(
            text="Tableau 7: contenu"
        )
        assert result is not None
        assert result.identifier_raw == "7"
        assert result.kind == "table"

    def test_identifier_normalized(self) -> None:
        """The `identifier_normalized` field is casefolded and whitespace-free."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(text="Table III title")
        assert result is not None
        assert result.identifier_normalized == "iii"

    def test_no_identifier_returns_none(self) -> None:
        """Prefix without a valid identifier returns `None`."""

        assert parse_caption_code(text="Summary of results") is None

    def test_none_input(self) -> None:
        """`None` text returns `None`."""

        assert parse_caption_code(text=None) is None  # type: ignore[arg-type]

    def test_roman_numeral_identifier(self) -> None:
        """Roman numeral identifiers are accepted."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(
            text="Table IV description"
        )
        assert result is not None
        assert result.identifier_raw == "IV"

    def test_short_prefix_fig(self) -> None:
        """Abbreviated `'Fig.'` prefix is parsed correctly."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(text="Fig. 10 caption")
        assert result is not None
        assert result.identifier_raw == "10"
        assert result.kind == "figure"
        assert result.prefix_raw == "Fig."

    def test_single_letter_identifier(self) -> None:
        """Single uppercase letter identifier is accepted."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(text="Table A: data")
        assert result is not None
        assert result.identifier_raw == "A"

    def test_swahili_jedwali_prefix(self) -> None:
        """Swahili `'Jedwali'` prefix is parsed as a table caption."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(
            text="Jedwali 3 maelezo"
        )
        assert result is not None
        assert result.identifier_raw == "3"
        assert result.kind == "table"

    def test_table_with_no_prefix(self) -> None:
        """Text not starting with a prefix returns `None`."""

        assert parse_caption_code(text="Some random text 123") is None

    def test_trailing_separator_stripped_from_label_raw(self) -> None:
        """Trailing colon / dash is stripped from `label_raw`."""

        result: Optional[ParsedCaptionCode] = parse_caption_code(
            text="Table 1: Description"
        )
        assert result is not None
        assert not result.label_raw.endswith(":")


class TestRowSignature:
    """Tests for `row_signature`."""

    def test_all_empty_cells(self) -> None:
        """Row of empty cells produces a tuple of empty strings."""

        row: TableRow = make_table_row(cell_texts=[None, None])
        assert row_signature(row=row) == ("", "")

    def test_mixed_cells(self) -> None:
        """Mix of populated and empty cells produces correct signature."""

        row: TableRow = make_table_row(cell_texts=["Hello", None, "World"])
        assert row_signature(row=row) == ("hello", "", "world")

    def test_normalization_applied(self) -> None:
        """Text normalization (lowercase, whitespace collapse) is applied."""

        row: TableRow = make_table_row(cell_texts=["  HELLO   WORLD  "])
        assert row_signature(row=row) == ("hello world",)

    def test_single_cell(self) -> None:
        """Single-cell row returns a one-element tuple."""

        row: TableRow = make_table_row(cell_texts=["abc"])
        assert row_signature(row=row) == ("abc",)


class TestStripCaptionTrailingSeparator:
    """Tests for `_strip_caption_trailing_separator`."""

    def test_colon_stripped(self) -> None:
        """Trailing colon and whitespace are removed."""

        assert _strip_caption_trailing_separator(text="Table 1:") == "Table 1"

    def test_dot_stripped(self) -> None:
        """Trailing dot is removed."""

        assert _strip_caption_trailing_separator(text="Figure 2.") == "Figure 2"

    def test_em_dash_stripped(self) -> None:
        """Trailing em-dash is stripped."""

        assert _strip_caption_trailing_separator(text="Tab 3—") == "Tab 3"

    def test_en_dash_stripped(self) -> None:
        """Trailing en-dash is stripped."""

        assert _strip_caption_trailing_separator(text="Fig 1 –") == "Fig 1"

    def test_hyphen_stripped(self) -> None:
        """Trailing hyphen is removed."""

        assert _strip_caption_trailing_separator(text="Table 1 -") == "Table 1"

    def test_mixed_trailing_separators(self) -> None:
        """Multiple mixed trailing separators are stripped."""

        assert _strip_caption_trailing_separator(text="Table 1 :.--") == "Table 1"

    def test_no_trailing_separator(self) -> None:
        """Text without a trailing separator is returned unchanged."""

        assert _strip_caption_trailing_separator(text="Table 1") == "Table 1"

    def test_only_separators(self) -> None:
        """String of only separators returns empty string."""

        assert _strip_caption_trailing_separator(text=":.-") == ""

    def test_whitespace_before_separator(self) -> None:
        """Whitespace before the separator is also stripped."""

        assert _strip_caption_trailing_separator(text="Figure 5  :  ") == "Figure 5"
