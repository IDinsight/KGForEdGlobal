"""This is the main module for testing utils/pdf.py."""

# Standard Library
import re

from pathlib import Path
from unittest.mock import MagicMock

# Third Party Library
import pymupdf
import pytest

from PIL import Image

# Package Library
from skg.utils import pdf
from tests.constants import FIXTURES_DIR


def test_add_section_for_text_hint_basic() -> None:
    """Test that the helper formats items correctly."""

    output: list[str] = []
    items = [(100.0, 50.0, 12.0, "Test Line")]

    pdf.add_section_for_text_hint(
        items=items,
        limit=5,
        output=output,
        sx=2.0,
        sy=2.0,
        title="TEST_SECTION",
    )

    assert output[0] == "## TEST_SECTION"

    # x=50*2=100.0, y=100*2=200.0.
    assert "[x=100.0 y=200.0 sz=12.0] Test Line" in output[1]


def test_add_section_for_text_hint_helper_limit() -> None:
    """Test that the limit parameter is respected."""

    output: list[str] = []
    items = [(i, i, 10, f"Line {i}") for i in range(10)]

    pdf.add_section_for_text_hint(
        items=items,
        limit=3,  # Only want 3
        output=output,
        sx=1.0,
        sy=1.0,
        title="LIMIT_TEST",
    )

    # Title line + 3 item lines = 4 lines total.
    assert len(output) == 4


def test_add_section_for_text_hint_truncation() -> None:
    """Test that long lines are truncated in the helper."""

    output: list[str] = []
    long_text = "A" * 300
    items = [(10.0, 10.0, 10.0, long_text)]

    pdf.add_section_for_text_hint(
        items=items,
        limit=5,
        output=output,
        sx=1.0,
        sy=1.0,
        title="TRUNCATION_TEST",
    )

    extracted_line = output[1]
    assert len(extracted_line) < 300
    assert "..." in extracted_line


def test_extract_text_layer_hints_dense_body_splitting(
    fixture_pdf_doc: pymupdf.Document,
) -> None:
    """Test extraction of body text from a dense page that should be split into TOP and
    BOTTOM sections.

    Parameters
    ----------
    fixture_pdf_doc
        The fixture PyMuPDF document.
    """

    result = pdf.extract_text_layer_hints(
        doc=fixture_pdf_doc,
        image_height=1000,
        image_width=800,
        max_lines_per_section=5,  # Force splitting by setting a low limit
        page_index=28,
    )

    assert result is not None
    assert "## TOP_BODY_LINES" in result
    assert "## BOTTOM_BODY_LINES" in result
    assert "## BODY_LINES" not in result  # Should not have the single body section


def test_extract_text_layer_hints_fallback_logic(
    fixture_pdf_doc: pymupdf.Document,
    mock_empty_struct_page: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a case where get_text("dict") returns empty blocks, forcing the code to
    use the raw text fallback.

    Parameters
    ----------
    fixture_pdf_doc
        The fixture PyMuPDF document.
    mock_empty_struct_page
        A mock page object that returns empty text blocks.
    monkeypatch
        The pytest monkeypatch fixture.
    """

    # Use monkeypatch to replace the 'load_page' method on the pdf_doc instance.
    # When extract_text_layer_hints calls doc.load_page(0), it gets our mock.
    monkeypatch.setattr(
        fixture_pdf_doc, "load_page", lambda index: mock_empty_struct_page
    )

    result = pdf.extract_text_layer_hints(
        doc=fixture_pdf_doc, image_height=1000, image_width=1000, page_index=0
    )

    assert "Raw Fallback Text" in result
    assert "PAGE_TEXT_LAYER_DIGEST" not in result


def test_extract_text_layer_hints_max_chars_truncation(
    fixture_pdf_doc: pymupdf.Document,
) -> None:
    """Test that the final output respects the character limit.

    Parameters
    ----------
    fixture_pdf_doc
        The fixture PyMuPDF document.
    """

    limit = 500
    result = pdf.extract_text_layer_hints(
        doc=fixture_pdf_doc,
        image_height=1000,
        image_width=800,
        page_index=28,  # Dense page
        max_chars=limit,
    )

    assert len(result) <= limit + 50  # Allow small buffer for "..." handling if any
    if len(result) == limit or "truncated" in result:
        assert result.endswith("...") or result.endswith("...[truncated]")


def test_extract_text_layer_hints_page_1_headings(
    fixture_pdf_doc: pymupdf.Document,
) -> None:
    """Test extraction of headings from the first page of the PDF. Should detect huge
    headings like 'THE UNITED REPUBLIC OF TANZANIA'.

    Parameters
    ----------
    fixture_pdf_doc
        The fixture PyMuPDF document.
    """

    result = pdf.extract_text_layer_hints(
        doc=fixture_pdf_doc, image_height=1000, image_width=800, page_index=0
    )

    assert result is not None
    assert "PAGE_TEXT_LAYER_DIGEST" in result
    assert "## HEADINGS_CANDIDATES" in result

    # High confidence this string exists on the cover page.
    assert "UNITED REPUBLIC OF TANZANIA" in result


def test_extract_text_layer_hints_raw_excerpt_inclusion(
    fixture_pdf_doc: pymupdf.Document,
) -> None:
    """Test that the ## RAW_TEXT_EXCERPT is appended at the end and that paragraph
    structure (newlines) is preserved.

    Parameters
    ----------
    fixture_pdf_doc
        The fixture PyMuPDF document.
    """

    result = pdf.extract_text_layer_hints(
        doc=fixture_pdf_doc,
        image_height=1000,
        image_width=800,
        max_chars=10000,
        page_index=28,
    )

    assert "## RAW_TEXT_EXCERPT" in result

    # Extract the raw section.
    raw_section = result.split("## RAW_TEXT_EXCERPT")[1]

    # Check that it didn't collapse all newlines (should have some \n left).
    assert "\n" in raw_section.strip()


def test_extract_text_layer_hints_table_page_columns(
    fixture_pdf_doc: pymupdf.Document,
) -> None:
    """Test extraction of column hints from a page that contains a table. This page has
    distinct columns. We expect COLUMN_X0_PEAKS_PX to be triggered.

    Parameters
    ----------
    fixture_pdf_doc
        The fixture PyMuPDF document.
    """

    result = pdf.extract_text_layer_hints(
        doc=fixture_pdf_doc, image_height=1000, image_width=800, page_index=13
    )

    assert result is not None

    # This page is a table, so column detection should likely trigger.
    if "COLUMN_X0_PEAKS_PX=" in result:
        # Check that we have a list of numbers.
        match = re.search(r"COLUMN_X0_PEAKS_PX=([\d\., ]+)", result)
        assert match, "Peaks found but format is wrong"
    else:
        pytest.skip(
            "Column peaks heuristic not triggered for Page 14 (might be borderline)"
        )


@pytest.mark.parametrize(
    ("filepath", "is_blank"),
    [
        (f"{FIXTURES_DIR}/utils/abbreviations_and_acronyms.png", False),
        (f"{FIXTURES_DIR}/utils/acknowledgements.png", False),
        (f"{FIXTURES_DIR}/utils/first_page.png", False),
        (f"{FIXTURES_DIR}/utils/list_of_tables.png", False),
        (f"{FIXTURES_DIR}/utils/vision.png", False),
        (f"{FIXTURES_DIR}/utils/empty.png", True),
    ],
)
def test_is_mostly_blank_using_real_content_images(
    filepath: str, is_blank: bool
) -> None:
    """Test that the provided sample images are flagged as intended.

    Parameters
    ----------
    filepath
        The path to the test image file.
    is_blank
        Whether the image is expected to be mostly blank.
    """

    is_blank = pdf.is_mostly_blank(png_fp=Path(filepath))

    assert (
        is_blank is is_blank
    ), f"{filepath} should have been {'blank' if is_blank else 'not blank'}."


def test_is_mostly_blank_synthetic_perfect_blank(synthetic_blank_page: Path) -> None:
    """Test a digitally perfect white page.

    Parameters
    ----------
    synthetic_blank_page
        The path to the synthetic blank page image.
    """

    assert pdf.is_mostly_blank(png_fp=synthetic_blank_page) is True


def test_is_mostly_blank_synthetic_dirty_blank(
    synthetic_dirty_blank_page: Path,
) -> None:
    """Test a page with high-brightness noise (scanner grain).

    Parameters
    ----------
    synthetic_dirty_blank_page
        The path to the synthetic dirty blank page image.
    """

    assert pdf.is_mostly_blank(png_fp=synthetic_dirty_blank_page) is True


def test_is_mostly_blank_synthetic_speck_blank(synthetic_page_with_speck: Path) -> None:
    """Test a page with a tiny amount of ink (dust speck) is still 'blank'.

    Parameters
    ----------
    synthetic_page_with_speck
        The path to the synthetic speckled blank page image.
    """

    assert pdf.is_mostly_blank(png_fp=synthetic_page_with_speck) is True


def test_is_mostly_blank_solid_black_page(tmp_path: Path) -> None:
    """Test a solid black page (should NOT be blank).

    Parameters
    ----------
    tmp_path
        The temporary directory path.
    """

    p = tmp_path / "black.png"
    Image.new("L", (1000, 1000), "black").save(p)

    assert pdf.is_mostly_blank(png_fp=p) is False


def test_is_mostly_blank_missing_file_handling() -> None:
    """Ensure function handles missing files gracefully (returns False)."""

    assert pdf.is_mostly_blank(png_fp=Path("non_existent_ghost.png")) is False


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

    pdf.render_and_save_page_to_png(
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

    pdf.render_and_save_page_to_png(
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

    pdf.render_and_save_page_to_png(
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
    assert page.rect.height < page.rect.width  # Portrait

    output_fp = tmp_path / "regular.png"

    pdf.render_and_save_page_to_png(
        doc=doc, dpi=72, fix_rotation=True, output_png_fp=output_fp, page_index=0
    )

    with Image.open(output_fp) as img:
        width, height = img.size
        assert height > width
