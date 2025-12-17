"""This module contains utilities related to PDF documents."""

# Standard Library
import hashlib

from pathlib import Path
from typing import Any

# Third Party Library
import pymupdf


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
