"""This is the main module for testing page_ir_extraction/utils.py."""

# Standard Library
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Third Party Library
import pymupdf
import pytest

from PIL import Image

# Package Library
from kgfeg.page_ir_extraction import utils
from tests.constants import FIXTURES_DIR, PARAM
from tests.types_ import InstallLoguruMock


def make_table(extract_return: list[list[str | None]] | None = None) -> MagicMock:
    """Create a minimal table mock for `_extract_table_hint`.

    Parameters
    ----------
    extract_return
        The value to return for `table.extract()`.

    Returns
    -------
    MagicMock
        A mock table object.
    """

    t = MagicMock()
    t.extract.return_value = extract_return
    return t


def mock_page_find_tables(tables: list[MagicMock] | None) -> MagicMock:
    """Create a minimal page mock for `_extract_table_hint`.

    Parameters
    ----------
    tables
        The value to expose on the `finder_result.tables` attribute.

    Returns
    -------
    MagicMock
        A mock page object.
    """

    page = MagicMock()
    page.find_tables.return_value = SimpleNamespace(tables=tables)
    return page


def mock_page_get_text_text(raw_text: str) -> MagicMock:
    """Create a minimal page mock for `_extract_text_hint`.

    Parameters
    ----------
    raw_text
        The value to return for `page.get_text("text")`.

    Returns
    -------
    MagicMock
        A mock page object.
    """

    page = MagicMock()
    page.get_text.return_value = raw_text
    return page


def test__extract_table_hint_returns_none_if_all_tables_are_unusable(
    mock_loguru_logger: InstallLoguruMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If every table is empty, trivially small, or throws, return None.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    monkeypatch
        Fixture that allows patching attributes on the utils module.
    """

    calls = mock_loguru_logger(utils)

    serializer = MagicMock(return_value="should-not-be-called")
    monkeypatch.setattr(utils, "_serialize_table", serializer)

    t0 = make_table(extract_return=[["ignored"]])
    t0.extract.side_effect = RuntimeError("explode")

    t1 = make_table(extract_return=[])
    t2 = make_table(extract_return=[[None, "   "]])  # 0 non-empty
    t3 = make_table(extract_return=[["only-one"], [None]])  # 1 non-empty

    page = mock_page_find_tables(tables=[t0, t1, t2, t3])

    assert utils._extract_table_hint(page=page, page_index=4) is None
    serializer.assert_not_called()

    # We should warn for the one extract explosion.
    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "Page 5:" in calls[0]["message"]
    assert "failed to extract table 0" in calls[0]["message"]
    assert "explode" in calls[0]["message"]


def test__extract_table_hint_returns_none_and_logs_warning_if_finder_result_missing_tables(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """A defensive check: if `find_tables()` returns an unexpected shape, we should
    still fail closed (warn + return None).

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    calls = mock_loguru_logger(utils)

    page = MagicMock()
    page.find_tables.return_value = object()  # No `.tables` attr

    assert utils._extract_table_hint(page=page, page_index=0) is None
    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "Page 1:" in calls[0]["message"]
    assert "failed to extract tables" in calls[0]["message"]
    assert "tables" in calls[0]["message"].lower()


def test__extract_table_hint_returns_none_and_logs_warning_on_find_tables_exception(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """If PyMuPDF fails during table detection, consume the error, log, and return None.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    calls = mock_loguru_logger(utils)

    page = MagicMock()
    page.find_tables.side_effect = RuntimeError("boom")

    assert utils._extract_table_hint(page=page, page_index=0) is None

    page.find_tables.assert_called_once_with()
    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "Page 1:" in calls[0]["message"]
    assert "failed to extract tables" in calls[0]["message"]
    assert "boom" in calls[0]["message"]


def test__extract_table_hint_returns_none_when_no_tables_found(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """If table detection succeeds but returns no tables, return None and do not log.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    calls = mock_loguru_logger(utils)
    page = mock_page_find_tables(tables=[])

    assert utils._extract_table_hint(page=page, page_index=7) is None
    assert calls == []


def test__extract_table_hint_skips_bad_tables_but_serializes_good_ones(
    mock_loguru_logger: InstallLoguruMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stress-case coverage:

    1. One table raises during extract() (should warn + continue)
    2. One table returns None/empty (should skip)
    3. One table is 'trivially small' after stripping (should skip)
    4. Two tables are valid (should serialize + join with a blank line)

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    monkeypatch
        Fixture that allows patching attributes on the utils module.
    """

    calls = mock_loguru_logger(utils)

    # Patch serializer so we can verify ordering + which indices are kept.
    serialize_calls: list[tuple[int, list[list[str | None]]]] = []

    def _fake_serialize_table(
        *, table_data: list[list[str | None]], table_index: int
    ) -> str:
        """Capture calls to the serializer for later inspection.

        Parameters
        ----------
        table_data
            The raw extracted table data passed to the serializer.
        table_index
            The index of the table on the page, as passed to the serializer.

        Returns
        -------
        str
            A fake serialized string that encodes the table index and number of
            non-empty cells, for testing purposes.
        """

        serialize_calls.append((table_index, table_data))
        non_empty = sum(
            1 for row in table_data for cell in row if cell is not None and cell.strip()
        )
        return f"tbl{table_index}-ne{non_empty}-rows{len(table_data)}"

    monkeypatch.setattr(utils, "_serialize_table", _fake_serialize_table)

    # Extract raises.
    t0 = make_table(extract_return=[["ignored"]])
    t0.extract.side_effect = ValueError("bad table")

    # Extract returns None (skip).
    t1 = make_table(extract_return=None)

    # Trivially small after stripping (only 1 non-empty cell).
    t2 = make_table(extract_return=[[None, "   ", "x"], ["", None, "\t"]])

    # Valid (two real cells, lots of whitespace).
    t3 = make_table(extract_return=[["x", "  ", None], [None, "y", "\n"]])

    # Valid large table with only two non-empty cells.
    big: list[list[str | None]] = [["   " for _ in range(20)] for _ in range(100)]
    big[0][0] = "A"
    big[-1][-1] = "B"
    t4 = make_table(extract_return=big)

    page = mock_page_find_tables(tables=[t0, t1, t2, t3, t4])

    out = utils._extract_table_hint(page=page, page_index=2)

    # Only tables 3 and 4 should survive, and they should retain original indices.
    assert out is not None
    assert out.split("\n\n", maxsplit=1)[0].startswith("tbl3-")
    assert out.split("\n\n")[1].startswith("tbl4-")

    assert [idx for idx, _ in serialize_calls] == [3, 4]
    assert serialize_calls[0][1] == t3.extract.return_value
    assert serialize_calls[1][1] == t4.extract.return_value

    # We should have exactly one warning (from the table-0 extract failure).
    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "Page 3:" in calls[0]["message"]
    assert "failed to extract table 0" in calls[0]["message"]
    assert "bad table" in calls[0]["message"]


def test__extract_text_hint_accepts_real_pymupdf_text_layer(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """Integration-ish check with a real `pymupdf.Page` object.

    This guards against accidental divergence between our mocks and the actual
    `page.get_text("text")` output.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    calls = mock_loguru_logger(utils)
    doc = pymupdf.open()  # Create an in-memory PDF

    try:
        page = doc.new_page()

        # Insert enough text to clear the min-length gate regardless of threshold.
        page.insert_text((72, 72), "Hello world " * 4000)
        out = utils._extract_text_hint(page=page, page_index=0)

        assert out is not None
        assert "Hello world" in out
        assert calls == []
    finally:
        doc.close()


def test__extract_text_hint_accepts_text_with_lots_of_newlines_tabs_and_returns_raw(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """Newlines/tabs are common in PDF text extraction and should count as acceptable
    characters in the printable ratio computation.

    This is a 'stress' case: a huge fraction of the extracted text is whitespace
    control characters that we explicitly whitelist.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    min_len = int(getattr(utils, "_MIN_TEXT_LENGTH"))
    seed = "A" * max(min_len, 1)

    raw_text = seed + ("\n\t\r" * 2000) + "\n"  # Trailing newline gets stripped

    calls = mock_loguru_logger(utils)
    page = mock_page_get_text_text(raw_text=raw_text)

    out = utils._extract_text_hint(page=page, page_index=1)

    assert out == raw_text
    assert calls == []


def test__extract_text_hint_prioritizes_printable_ratio_check_over_replacement_ratio(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """If both the printable ratio and replacement ratio are bad, the printable ratio
    check should short-circuit first.

    This test guards against accidental re-ordering of quality gates.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    min_len = int(getattr(utils, "_MIN_TEXT_LENGTH"))
    min_printable = float(getattr(utils, "_MIN_PRINTABLE_RATIO"))
    max_repl = float(getattr(utils, "_MAX_REPLACEMENT_CHAR_RATIO"))

    if not 0 < min_printable <= 1 or not 0 <= max_repl < 1:
        pytest.skip("Unexpected thresholds; cannot construct robust test")

    a = max(min_len, 1)  # Normal printable characters
    c = (1 - min_printable) / min_printable

    # Find counts that satisfy:
    #
    # 1. printable_ratio < min_printable
    # 2. replacement_ratio > max_repl
    k_repl = a

    for _ in range(20):
        k_repl *= 2
        n_nonprintable = int(c * (a + k_repl)) + 1
        total = a + k_repl + n_nonprintable
        printable_ratio = (a + k_repl) / total
        replacement_ratio = k_repl / total

        if printable_ratio < min_printable and replacement_ratio > max_repl:
            break
    else:
        pytest.skip("Could not satisfy both gate conditions with current thresholds")

    raw_text = ("A" * a) + ("\ufffd" * k_repl) + ("\x00" * n_nonprintable)

    calls = mock_loguru_logger(utils)
    page = mock_page_get_text_text(raw_text=raw_text)

    assert utils._extract_text_hint(page=page, page_index=0) is None
    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "low printable ratio" in calls[0]["message"]
    assert "replacement chars" not in calls[0]["message"]


@PARAM(("raw_text", "expected_stripped_len"), [("", 0), (" \n\t\r  ", 0)])
def test__extract_text_hint_rejects_empty_or_whitespace_text_layer_and_logs_debug(
    mock_loguru_logger: InstallLoguruMock, raw_text: str, expected_stripped_len: int
) -> None:
    """Empty/whitespace-only text layers should be rejected before any ratio
    computations.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    raw_text
        The page text layer to return.
    expected_stripped_len
        Expected `len(raw_text.strip())`.
    """

    calls = mock_loguru_logger(utils)
    page = mock_page_get_text_text(raw_text=raw_text)

    assert utils._extract_text_hint(page=page, page_index=0) is None

    assert len(calls) == 1
    assert calls[0]["level"] == "DEBUG"
    assert "Page 1:" in calls[0]["message"]
    assert "text layer too short" in calls[0]["message"]
    assert f"({expected_stripped_len} chars)" in calls[0]["message"]
    assert "Skipping hint" in calls[0]["message"]


def test__extract_text_hint_rejects_excessive_replacement_chars_and_logs_warning(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """A text layer with too many replacement characters (\ufffd) should be rejected
    even if it is otherwise printable.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    min_len = int(getattr(utils, "_MIN_TEXT_LENGTH"))
    max_repl = float(getattr(utils, "_MAX_REPLACEMENT_CHAR_RATIO"))

    if max_repl < 0 or max_repl >= 1:
        pytest.skip(
            "Unexpected _MAX_REPLACEMENT_CHAR_RATIO; cannot construct robust test"
        )

    printable_seed = "A" * max(min_len, 1)

    # Minimal replacement count to push replacement_ratio strictly above threshold.
    k_repl = int((max_repl * len(printable_seed)) / (1 - max_repl)) + 1
    raw_text = printable_seed + ("\ufffd" * k_repl)

    calls = mock_loguru_logger(utils)
    page = mock_page_get_text_text(raw_text=raw_text)

    assert utils._extract_text_hint(page=page, page_index=9) is None

    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "Page 10:" in calls[0]["message"]
    assert "excessive replacement chars" in calls[0]["message"]
    assert "Skipping hint" in calls[0]["message"]
    assert "printable ratio" not in calls[0]["message"].lower()


def test__extract_text_hint_rejects_low_printable_ratio_and_logs_warning(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """A text layer dominated by non-printable characters should be rejected.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    min_len = int(getattr(utils, "_MIN_TEXT_LENGTH"))
    min_printable = float(getattr(utils, "_MIN_PRINTABLE_RATIO"))

    if min_printable <= 0 or min_printable > 1:
        pytest.skip("Unexpected _MIN_PRINTABLE_RATIO; cannot construct robust test")

    # Ensure we pass the min-length check and then fail the printable ratio check.
    printable_seed = "A" * max(min_len, 1)

    # Minimal non-printable count to push printable_ratio strictly below threshold.
    n_nonprintable = (
        int((len(printable_seed) * (1 - min_printable)) / min_printable) + 1
    )
    raw_text = printable_seed + ("\x00" * n_nonprintable)

    calls = mock_loguru_logger(utils)
    page = mock_page_get_text_text(raw_text=raw_text)

    assert utils._extract_text_hint(page=page, page_index=4) is None

    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "Page 5:" in calls[0]["message"]
    assert "low printable ratio" in calls[0]["message"]
    assert "Skipping hint" in calls[0]["message"]
    assert "replacement" not in calls[0]["message"].lower()


def test__extract_text_hint_rejects_text_layer_below_min_length_threshold(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """Stress the min-length gate with an input that is exactly 1 character below the
    module threshold.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    min_len = int(getattr(utils, "_MIN_TEXT_LENGTH"))

    # Build a string that is *just* under the threshold after stripping.
    below = "A" * max(min_len - 1, 0)
    calls = mock_loguru_logger(utils)
    page = mock_page_get_text_text(raw_text=below)

    assert utils._extract_text_hint(page=page, page_index=6) is None

    # The debug log should report the stripped length, not the raw length.
    assert len(calls) == 1
    assert calls[0]["level"] == "DEBUG"
    assert "Page 7:" in calls[0]["message"]
    assert f"({len(below.strip())} chars)" in calls[0]["message"]


def test__extract_text_hint_returns_none_and_logs_warning_on_get_text_exception(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """If PyMuPDF fails to extract the text layer, we should consume the error, emit a
    warning, and return None.

    Parameters
    ----------
    mock_loguru_logger
        Fixture that patches the module's loguru logger and captures log calls.
    """

    calls = mock_loguru_logger(utils)

    page = MagicMock()
    page.get_text.side_effect = RuntimeError("boom")

    assert utils._extract_text_hint(page=page, page_index=2) is None

    page.get_text.assert_called_once_with("text")
    assert len(calls) == 1
    assert calls[0]["level"] == "WARNING"
    assert "Page 3:" in calls[0]["message"]
    assert "failed to extract text layer" in calls[0]["message"]
    assert "boom" in calls[0]["message"]


def test__serialize_table_collapses_internal_newlines_and_strips_cells() -> None:
    """Newlines inside cells must be replaced with spaces and then stripped.

    This protects the downstream LLM format: each table row should stay a single line
    in the serialized output (no embedded newlines coming from cell contents).
    """

    table_data: list[list[str | None]] = [
        ["  a\nb  ", "x\ny\nz", None],
    ]

    out = utils._serialize_table(table_data=table_data, table_index=0)
    lines = out.splitlines()

    assert lines[0] == "### Table 0"
    assert lines[1] == "  row 0: | a b | x y z |  |"
    assert "\n" not in lines[1]  # Row line should be single-line


def test__serialize_table_handles_ragged_rows_and_empty_rows() -> None:
    """Ragged tables (varying row lengths) should serialize without special-casing.
    Empty rows still produce a row line with an empty cell region.
    """

    table_data: list[list[str | None]] = [[], ["a"], ["a", "b", "c"]]

    out = utils._serialize_table(table_data=table_data, table_index=5)

    assert out.splitlines() == [
        "### Table 5",
        "  row 0: |  |",
        "  row 1: | a |",
        "  row 2: | a | b | c |",
    ]


def test__serialize_table_renders_header_and_rows_with_none_cells_as_empty_strings() -> (
    None
):
    """Render a small table and verify the exact wire format.

    This checks the core contract:

    1. Header contains the table index.
    2. Rows are numbered from 0.
    3. Cells are pipe-delimited.
    4. None is rendered as an empty string.
    5. Cell strings are stripped.
    """

    table_data: list[list[str | None]] = [
        ["a", None, "  c  "],
        [None, "", "d"],
    ]

    out = utils._serialize_table(table_data=table_data, table_index=2)

    assert out.splitlines() == [
        "### Table 2",
        "  row 0: | a |  | c |",
        "  row 1: |  |  | d |",
    ]


def test__serialize_table_returns_header_only_for_empty_table_data() -> None:
    """An empty `table_data` should serialize to just the header line."""

    table_data: list[list[str | None]] = []

    out = utils._serialize_table(table_data=table_data, table_index=9)

    assert out == "### Table 9"


def test__serialize_table_stress_large_table_preserves_row_count_and_no_embedded_newlines() -> (
    None
):
    """Stress test a large table to catch formatting regressions.

    Verifies:

    1. Output has exactly 1 header + N row lines.
    2. Specific cells with embedded newlines are collapsed.
    3. Row lines themselves do not contain embedded newlines.
    """

    n_rows = 120
    n_cols = 35
    table_data: list[list[str | None]] = []

    for r in range(n_rows):
        row: list[str | None] = []

        for c in range(n_cols):
            if (r + c) % 11 == 0:
                row.append(f"  r{r}\n c{c}  ")
            elif (r + c) % 7 == 0:
                row.append(None)
            else:
                row.append(f"v{r}_{c}")

        table_data.append(row)

    out = utils._serialize_table(table_data=table_data, table_index=3)
    lines = out.splitlines()

    assert len(lines) == 1 + n_rows
    assert lines[0] == "### Table 3"
    assert lines[1].startswith("  row 0: | ")
    assert lines[-1].startswith(f"  row {n_rows - 1}: | ")
    assert all("\n" not in line for line in lines)

    # Spot-check a known newline-containing cell got collapsed properly.
    # (0 + 0) % 11 == 0, so the first cell should be "r0  c0" after newline -> space +
    # strip.
    assert "r0  c0" in lines[1]


def test_render_and_save_page_to_png_dpi_scaling_is_accurate(tmp_path: Path) -> None:
    """Test that rendering at different DPIs produces correct pixel dimensions.

    Parameters
    ----------
    tmp_path
        The temporary directory path.
    """

    doc = pymupdf.open(FIXTURES_DIR / "utils" / "uganda_regular.pdf")
    page = doc.load_page(0)
    pdf_w, pdf_h = (  # Original PDF is peculiarly rotated 90 degrees
        page.rect.height,
        page.rect.width,
    )

    target_dpi = 144
    output_fp = tmp_path / "high_res.png"

    utils.render_and_save_page_to_png(
        doc=doc,
        dpi=target_dpi,
        fix_rotation=True,
        output_png_fp=output_fp,
        page_index=0,
    )

    with Image.open(output_fp) as img:
        px_width, px_height = img.size

        # Calculate expected pixels: points * (dpi / 72).
        expected_w = pdf_w * (target_dpi / 72)
        expected_h = pdf_h * (target_dpi / 72)

        # Allow small rounding differences (e.g. +/- 1 pixel).
        assert abs(px_width - expected_w) < 2
        assert abs(px_height - expected_h) < 2


def test_render_and_save_page_to_png_fix_rotation_corrects_landscape_rotated_page(
    tmp_path: Path,
) -> None:
    """Test that a rotated Landscape page is corrected to Portrait when
    fix_rotation=True.

    Parameters
    ----------
    tmp_path
        The temporary directory path.
    """

    doc = pymupdf.open(FIXTURES_DIR / "utils" / "uganda_rotated.pdf")
    page_index = 0
    page = doc.load_page(page_index)

    assert page.rotation != 0, "Fixture page is not rotated!"
    assert page.rect.width > page.rect.height, "Fixture page is not landscape!"

    output_fp = tmp_path / "fixed.png"
    dpi = 72

    utils.render_and_save_page_to_png(
        doc=doc,
        dpi=dpi,
        fix_rotation=True,
        page_index=page_index,
        output_png_fp=output_fp,
    )

    assert output_fp.exists()

    with Image.open(output_fp) as img:
        width, height = img.size
        assert height > width, (
            f"Image should be Portrait after fix. Got {width}x{height}. "
            f"Original Rotation was {page.rotation}."
        )


def test_render_and_save_page_to_png_fix_rotation_disabled_preserves_orientation(
    tmp_path: Path,
) -> None:
    """Test that when fix_rotation=False, the page orientation is preserved.

    Parameters
    ----------
    tmp_path
        The temporary directory path.
    """

    doc = pymupdf.open(FIXTURES_DIR / "utils" / "uganda_rotated.pdf")
    output_fp = tmp_path / "ignored.png"
    dpi = 72

    utils.render_and_save_page_to_png(
        doc=doc, dpi=dpi, fix_rotation=False, output_png_fp=output_fp, page_index=0
    )

    with Image.open(output_fp) as img:
        width, height = img.size
        assert width > height, "Image should remain Landscape when fix is disabled."


def test_render_and_save_page_to_png_regular_page_is_untouched(tmp_path: Path) -> None:
    """Test that a standard Portrait page remains unchanged when fix_rotation=True.

    Parameters
    ----------
    tmp_path
        The temporary directory path.
    """

    doc = pymupdf.open(FIXTURES_DIR / "utils" / "uganda_regular.pdf")
    page = doc.load_page(0)

    assert page.rotation == 90  # Original PDF is peculiarly rotated 90 degrees
    assert page.rect.height < page.rect.width

    output_fp = tmp_path / "regular.png"

    utils.render_and_save_page_to_png(
        doc=doc, dpi=72, fix_rotation=True, output_png_fp=output_fp, page_index=0
    )

    with Image.open(output_fp) as img:
        width, height = img.size
        assert height > width
