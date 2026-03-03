"""This module contains utilities related to PDF documents."""

# Standard Library
import hashlib

from pathlib import Path
from typing import Optional

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
