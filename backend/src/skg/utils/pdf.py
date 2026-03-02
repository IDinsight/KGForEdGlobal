"""This module contains utilities related to PDF documents."""

# Standard Library
import hashlib

from pathlib import Path
from typing import Optional

# Third Party Library
import pymupdf

from PIL import Image

# Package Library
from skg.utils.general import make_dir


def compute_doc_key(*, n_hex: int = 64, pdf_fp: Path) -> str:
    """Compute deterministic doc_key from PDF bytes (sha256 hex).

    Parameters
    ----------
    n_hex
        Number of hex characters to return. Defaults to 64 (full sha256).
    pdf_fp
        Path to the PDF file.

    Returns
    -------
    str
        The computed document key.
    """

    data = pdf_fp.read_bytes()
    h = hashlib.sha256(data).hexdigest()

    return h[:n_hex]


def crop_image_to_ymax(
    *, input_png_fp: Path, output_png_fp: Path, y_max: float
) -> None:
    """Crop a rendered page PNG to [0, y_max] in pixel coordinates.

    Parameters
    ----------
    input_png_fp
        Full-page PNG path (the extraction-time rendered page image).
    output_png_fp
        Where to write the cropped PNG.
    y_max
        The maximum Y coordinate (in pixels) to crop to. Values outside the image
        height will be clamped to the image bounds.
    """

    with Image.open(input_png_fp) as img:
        w, h = img.size
        y = max(1, min(int(round(y_max)), h))

        make_dir(output_png_fp.parent)
        img.crop((0, 0, w, y)).save(output_png_fp)


def validate_page_count(
    *, doc: pymupdf.Document, end_page: Optional[int], start_page: Optional[int]
) -> tuple[int, int, int]:
    """Validate the start and end pages against document page count.

    Parameters
    ----------
    doc
        The PyMuPDF document to validate page count against.
    end_page
        0-based end page (exclusive).
    start_page
        0-based start page (inclusive).

    Returns
    -------
    tuple[int, int, int]
        The document page count and resolved start and end pages.

    Raises
    ------
    ValueError
        If start_page or end_page are out of valid range.
    """

    page_count = doc.page_count
    end_page = end_page or page_count
    start_page = start_page or 0

    if not 0 <= start_page < end_page <= page_count:
        raise ValueError(
            f"Invalid pages. start_page: {start_page} end_page: {end_page} page_count: {page_count}"
        )

    return page_count, start_page, end_page
