"""This is the main module for testing utils/general.py."""

# Package Library
from skg.utils import general
from tests.constants import PARAM


@PARAM(
    "input_text, expected_output",
    [
        # Basic casing and spacing.
        ("Hello World", "hello world"),
        ("Python  Testing", "python testing"),
        # Edge case: None and empty strings.
        (None, ""),
        ("", ""),
        # Whitespace: leading/trailing/only whitespace.
        ("   ", ""),
        ("  leading", "leading"),
        ("trailing  ", "trailing"),
        # Complex whitespace: newlines and tabs.
        ("Line\nBreak", "line break"),
        ("Tab\tCharacter", "tab character"),
        ("  Mixed\n\t  Whitespace  ", "mixed whitespace"),
        # Already normalized.
        ("perfectly clean", "perfectly clean"),
    ],
)
def test_normalize_text(input_text: str, expected_output: str) -> None:
    """Verify that normalize_text handles various whitespace, casing, and null inputs
    correctly.

    Parameters
    ----------
    input_text
        The text to normalize.
    expected_output
        The expected output.
    """

    assert general.normalize_text(input_text) == expected_output
