"""This module contains utilities related to PDF documents."""

# Standard Library
import hashlib

from pathlib import Path
from typing import Any

# Third Party Library
import pymupdf

from PIL import Image


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


def get_page_dimensions(doc: pymupdf.Document, page_index: int) -> tuple[float, float]:
    """Get PDF page dimensions in points from a PyMuPDF document.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    page_index
        The 0-based page index.

    Returns
    -------
    tuple[float, float]
        The (width, height) of the page in points.
    """

    page = doc.load_page(page_index)
    r = page.rect
    return float(r.width), float(r.height)


def get_pdf_metadata(doc: pymupdf.Document) -> dict[str, Any]:
    """Get PDF metadata from a PyMuPDF document.

    NB: Expected PyMuPDF keys are: title, author, subject, keywords, creator, producer,
    creationDate, and modDate.

    Parameters
    ----------
    doc
        The PyMuPDF document.

    Returns
    -------
    dict[str, Any]
        The PDF metadata.
    """

    md = doc.metadata or {}
    return {k: v for k, v in md.items() if v}


def is_mostly_blank(*, png_fp: Path) -> bool:
    """Check if the rendered page image is nearly blank (separator/intentional blank
    page). Uses grayscale histogram stats (no OCR).

    This version is robust to:
      - crop marks / page frame lines near edges
      - page-number footer boxes near the bottom

    Parameters
    ----------
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    bool
        True if the page is visually blank, False otherwise.
    """

    try:
        with Image.open(png_fp) as im:
            im = im.convert("L")
            w, h = im.size

            # Ignore outer border where crop marks/page frame lines live. 3% is usually
            # enough at 200 DPI; keep it small to avoid skipping real content.
            pad_x = int(w * 0.03)
            pad_y = int(h * 0.03)
            x0 = min(max(pad_x, 0), w - 1)
            y0 = min(max(pad_y, 0), h - 1)
            x1 = max(min(w - pad_x, w), x0 + 1)
            y1 = max(min(h - pad_y, h), y0 + 1)

            im = im.crop((x0, y0, x1, y1))

            # Ignore bottom strip where page number boxes often sit. Keep 92% of the
            # (already border-cropped) height.
            bw, bh = im.size
            bottom_keep = int(bh * 0.92)
            if bottom_keep > 20:  # Avoid degenerate crops on tiny images
                im = im.crop((0, 0, bw, bottom_keep))

            # Downsample aggressively for speed.
            im.thumbnail((600, 600))

            # Histogram over 0..255.
            hist = im.histogram()
            total = float(sum(hist)) or 1.0

            # Fraction of "dark-ish" pixels (tuned to catch faint text but not crop
            # marks). 0..199 are darker than mid-gray.
            dark_frac = sum(hist[:200]) / total

            # Contrast and brightness stats.
            mean = sum(i * c for i, c in enumerate(hist)) / total
            var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / total
            std = var**0.5

            # Conservative thresholds:
            # - very low dark pixels
            # - low contrast (std)
            # - fairly light background
            return dark_frac < 0.006 and std < 14.0 and mean > 210.0
    except Exception:  # pylint: disable=broad-except
        # If we can't read the image, don't treat it as blank.
        return False


def read_png_dimensions(*, png_fp: Path) -> tuple[int, int]:
    """Read PNG width/height without external dependencies.

    Parameters
    ----------
    png_fp
        Path to a rendered PNG.

    Returns
    -------
    tuple[int, int]
        (width_px, height_px)

    Raises
    ------
    ValueError
        If the file is not a valid PNG.
    """

    data = png_fp.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG file: {png_fp}")

    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def render_page_to_png(
    *,
    doc: pymupdf.Document,
    dpi: int = 200,
    page_index: int,
    output_png_fp: Path,
) -> None:
    """Render a PDF page to PNG. Rendering guarantees that we capture everything
    visible on the page, consistently sized by DPI.

    NB: PDFs are typically 72 points/inch. So scale = dpi / 72 is the standard
    conversion. So scaling via Matrix(scale, scale) gives us a predictable pixel size
    and quality for downstream vision/OCR/LLM steps.

    Parameters
    ----------
    doc
        The PyMuPDF document.
    dpi
        Render DPI for page images.
    page_index
        The 0-based page index to render.
    output_png_fp
        The output PNG file path.
    """

    page = doc.load_page(page_index)

    scale = dpi / 72.0
    mat = pymupdf.Matrix(scale, scale)

    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.set_dpi(dpi, dpi)
    output_png_fp.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_png_fp))
