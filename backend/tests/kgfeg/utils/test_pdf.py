"""This is the main module for testing utils/pdf.py."""

# Standard Library
from pathlib import Path
from unittest.mock import MagicMock

# Third Party Library
import pymupdf
import pytest

# Package Library
from kgfeg.utils import pdf
from tests.constants import FIXTURES_DIR, PARAM


@PARAM(
    ("content", "expected_hash"),
    [
        # sha256 of b"dummy pdf content"
        (
            b"dummy pdf content",
            "26084f449206454b4070d1cb9033dac539e78e799c67833351876cab673ca26e",
        ),
        # sha256 of empty file
        (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ],
)
def test_compute_doc_key_known_content(
    tmp_path: Path, content: bytes, expected_hash: str
) -> None:
    """Test that the function computes the correct full SHA-256 hash for known bytes.

    Parameters
    ----------
    tmp_path
        The pytest temporary directory path fixture.
    content
        The byte content to write to the dummy file.
    expected_hash
        The expected full 64-character SHA-256 hex digest.
    """

    test_file = tmp_path / "test_doc.pdf"
    test_file.write_bytes(content)

    result = pdf.compute_doc_key(pdf_fp=test_file)

    assert result == expected_hash
    assert len(result) == 64


@PARAM(
    ("n_hex", "expected_length"),
    [
        (8, 8),
        (16, 16),
        (32, 32),
        (64, 64),
        (100, 64),  # Max length of sha256 hexdigest is 64
        (0, 0),
    ],
)
def test_compute_doc_key_n_hex_truncation(
    tmp_path: Path, n_hex: int, expected_length: int
) -> None:
    """Test that the n_hex parameter correctly truncates the hash string.

    Parameters
    ----------
    tmp_path
        The pytest temporary directory path fixture.
    n_hex
        The number of hex characters to request.
    expected_length
        The expected length of the returned string.
    """

    test_file = tmp_path / "test_truncation.pdf"
    test_file.write_bytes(b"some random content to hash")

    result = pdf.compute_doc_key(n_hex=n_hex, pdf_fp=test_file)

    assert len(result) == expected_length


def test_compute_doc_key_determinism_with_fixture() -> None:
    """Test that hashing an actual PDF file is deterministic and valid.

    This ensures that multiple reads of the same file produce the exact same key.
    """

    pdf_path = FIXTURES_DIR / "utils" / "uganda_regular.pdf"

    result_1 = pdf.compute_doc_key(pdf_fp=pdf_path)
    result_2 = pdf.compute_doc_key(pdf_fp=pdf_path)

    assert result_1 == result_2
    assert len(result_1) == 64

    # Validate that it is actually a hex string (will raise ValueError if not)
    int(result_1, 16)


def test_compute_doc_key_missing_file() -> None:
    """Test that attempting to hash a non-existent file raises FileNotFoundError."""

    missing_file = Path("this_file_does_not_exist.pdf")

    with pytest.raises(FileNotFoundError):
        pdf.compute_doc_key(pdf_fp=missing_file)


@PARAM(
    ("start_page", "end_page", "expected_end"),
    [
        (None, None, 10),  # Defaults to 0 and page_count
        (0, 10, 10),  # Explicit full range
        (2, 5, 5),  # Explicit sub-range
        (0, None, 10),  # Default end_page
        (None, 8, 8),  # Default start_page
    ],
)
def test_validate_page_count_valid(
    start_page: int | None, end_page: int | None, expected_end: int
) -> None:
    """Test that valid page ranges return correctly.

    Parameters
    ----------
    start_page
        The start page index.
    end_page
        The end page index.
    expected_end
        The expected resolved end page.
    """

    doc = MagicMock(spec=pymupdf.Document)
    doc.page_count = 10

    page_count, _, resolved_end = pdf.validate_page_count(
        doc=doc, start_page=start_page, end_page=end_page
    )

    assert page_count == 10
    assert resolved_end == expected_end


@PARAM(
    ("start_page", "end_page"),
    [
        (-1, 5),  # Negative start page
        (5, 5),  # Start page equals end page (0 pages to process)
        (6, 5),  # Start page greater than end page
        (0, 11),  # End page exceeds total page count
        (10, None),  # Start page equals total page count (out of bounds)
        (11, None),  # Start page exceeds total page count
    ],
)
def test_validate_page_count_invalid(
    start_page: int | None, end_page: int | None
) -> None:
    """Test that invalid page ranges raise a ValueError.

    Parameters
    ----------
    start_page
        The start page index.
    end_page
        The end page index.
    """

    doc = MagicMock(spec=pymupdf.Document)
    doc.page_count = 10

    with pytest.raises(ValueError, match="Invalid pages"):
        pdf.validate_page_count(doc=doc, start_page=start_page, end_page=end_page)


def test_validate_page_count_with_fixture(
    fixture_pdf_doc: pymupdf.Document,
) -> None:
    """Test validate_page_count using an actual document fixture.

    Parameters
    ----------
    fixture_pdf_doc
        The fixture PyMuPDF document.
    """

    actual_count = fixture_pdf_doc.page_count

    page_count, _, resolved_end = pdf.validate_page_count(
        doc=fixture_pdf_doc, start_page=None, end_page=None
    )

    assert page_count == actual_count
    assert resolved_end == actual_count
