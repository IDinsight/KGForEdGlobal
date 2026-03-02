"""This is the main module for testing page_ir_extraction/utils.py."""

# Standard Library
from pathlib import Path

# Third Party Library
import pymupdf

from PIL import Image

# Package Library
from skg.page_ir_extraction import utils
from tests.constants import FIXTURES_DIR


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
