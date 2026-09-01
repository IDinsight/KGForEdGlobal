"""This is the main module for testing regexes.py."""

# Standard Library
import re

# Third Party Library
import pytest

# Package Library
from kgfeg.regexes import (
    _CAPTION_NUMERIC_IDENTIFIER_RE,
    _CAPTION_ROMAN_NUMERAL_RE,
    ALPHA_RE,
    CAPTION_IDENTIFIER_RE,
    DIGIT_RE,
    FIGURE_PREFIX_PATTERN,
    TABLE_CODE_RE,
    TABLE_PREFIX_PATTERN,
    TOKEN_RE,
    WS_RE,
)
from kgfeg.utils.constants import CaptionFigurePrefixes, CaptionTablePrefixes
from tests.constants import PARAM


def _assert_full_match(*, pattern: re.Pattern[str], text: str) -> None:
    """Assert that *pattern* matches the entirety of *text*.

    Parameters
    ----------
    pattern
        Compiled regex pattern.
    text
        String that should be fully matched.
    """

    m = pattern.fullmatch(text)
    assert m is not None, f"Expected full match for {text!r}"


def _assert_no_match(*, pattern: re.Pattern[str], text: str) -> None:
    """Assert that *pattern* does not match anywhere in *text*.

    Parameters
    ----------
    pattern
        Compiled regex pattern.
    text
        String that should not be matched.
    """

    assert pattern.search(text) is None, f"Unexpected match in {text!r}"


def _assert_search_match(*, expected: str, pattern: re.Pattern[str], text: str) -> None:
    """Assert that *pattern* finds *expected* inside *text*.

    Parameters
    ----------
    expected
        The substring that the match should equal.
    pattern
        Compiled regex pattern.
    text
        String to search.
    """

    m = pattern.search(text)
    assert m is not None, f"Expected to find {expected!r} in {text!r}"
    assert m.group(0) == expected


def _compile_identifier(flags: int = 0) -> re.Pattern[str]:
    """Compile the `CAPTION_IDENTIFIER_RE` raw string into a pattern.

    Parameters
    ----------
    flags
        Optional regex flags.

    Returns
    -------
    re.Pattern[str]
        Compiled pattern.
    """

    return re.compile(rf"^{CAPTION_IDENTIFIER_RE}$", flags=flags)


def _compile_numeric_identifier(flags: int = 0) -> re.Pattern[str]:
    """Compile the `CAPTION_NUMERIC_IDENTIFIER_RE` raw string into a pattern.

    Parameters
    ----------
    flags
        Optional regex flags.

    Returns
    -------
    re.Pattern[str]
        Compiled pattern.
    """

    return re.compile(rf"^{_CAPTION_NUMERIC_IDENTIFIER_RE}$", flags=flags)


class TestAlphaRe:
    """Tests for `ALPHA_RE`, single alphabetical character matcher."""

    def test_finds_first_alpha_in_mixed_string(self) -> None:
        """Verify search returns the first alphabetical character in a mixed string."""

        _assert_search_match(expected="h", pattern=ALPHA_RE, text="123 hello")

    @PARAM(
        "char",
        ["a", "z", "A", "Z", "é", "à", "ç", "ñ", "ü", "À", "Ö", "Ø", "ö", "ø", "ÿ"],
        ids=lambda c: f"char_{c}",
    )
    def test_matches_alpha_characters(self, char: str) -> None:
        """Verify that single alphabetical and accented characters match.

        Parameters
        ----------
        char
            Character expected to match.
        """

        _assert_full_match(pattern=ALPHA_RE, text=char)

    @PARAM(
        "char",
        ["0", "9", " ", "×", "÷", "!", "@", "-"],
        ids=lambda c: f"char_ord{ord(c)}",
    )
    def test_rejects_non_alpha_characters(self, char: str) -> None:
        """Verify that digits, symbols, and multiplication/division signs do not match.

        Parameters
        ----------
        char
            Character expected to *not* match.
        """

        _assert_no_match(pattern=ALPHA_RE, text=char)


class TestCaptionNumericIdentifierRe:
    """Tests for `CAPTION_NUMERIC_IDENTIFIER_RE`, numeric caption IDs."""

    @PARAM("text", ["1", "12", "1.1", "3-A", "4/B", "1.2.3-final", "10.20.30"])
    def test_matches_valid_identifiers(self, text: str) -> None:
        """Verify that well-formed numeric identifiers match.

        Parameters
        ----------
        text
            Identifier string expected to match.
        """

        _assert_full_match(pattern=_compile_numeric_identifier(), text=text)

    @PARAM("text", ["", "A", ".1", "-1", "/1"])
    def test_rejects_invalid_identifiers(self, text: str) -> None:
        """Verify that malformed identifiers do not match.

        Parameters
        ----------
        text
            String expected to *not* match.
        """

        assert _compile_numeric_identifier().fullmatch(text) is None


class TestCaptionRomanNumeralRe:
    """Tests for `CAPTION_ROMAN_NUMERAL_RE`,  Roman numerals 1–3999."""

    @pytest.fixture()
    def pattern(self) -> re.Pattern[str]:
        """Return a compiled version of the raw `CAPTION_ROMAN_NUMERAL_RE`.

        Returns
        -------
        re.Pattern[str]
            Compiled pattern anchored to start/end.
        """

        return re.compile(rf"^{_CAPTION_ROMAN_NUMERAL_RE}$")

    @PARAM(
        "text", ["I", "IV", "IX", "XIV", "XL", "XC", "CD", "CM", "MCMXCIX", "MMMCMXCIX"]
    )
    def test_matches_valid_roman_numerals(
        self, *, pattern: re.Pattern[str], text: str
    ) -> None:
        """Verify that valid Roman numerals match.

        Parameters
        ----------
        pattern
            Compiled pattern.
        text
            Roman numeral string expected to match.
        """

        _assert_full_match(pattern=pattern, text=text)

    @PARAM("text", ["", "MMMM", "IIII", "VV", "abc"])
    def test_rejects_invalid_roman_numerals(
        self, *, pattern: re.Pattern[str], text: str
    ) -> None:
        """Verify that invalid or empty strings do not match.

        Parameters
        ----------
        pattern
            Compiled pattern.
        text
            String expected to *not* match.
        """

        assert pattern.fullmatch(text) is None


class TestCaptionIdentifierRe:
    """Tests for `CAPTION_IDENTIFIER_RE`, combined identifier pattern."""

    @PARAM("text", ["1", "1.2", "1.2.3-final", "IV", "XIV", "A", "Z"])
    def test_matches_any_valid_identifier_type(self, text: str) -> None:
        """Verify numeric, Roman numeral, and single-letter identifiers all match.

        Parameters
        ----------
        text
            Identifier expected to match.
        """

        _assert_full_match(pattern=_compile_identifier(), text=text)

    @PARAM("text", ["", "ab", "a"])
    def test_rejects_invalid_identifiers(self, text: str) -> None:
        """Verify that lowercase multi-char or empty strings do not match.

        Parameters
        ----------
        text
            String expected to *not* match.
        """

        assert _compile_identifier().fullmatch(text) is None


class TestDigitRe:
    """Tests for `DIGIT_RE`, single digit matcher."""

    @PARAM("char", list("0123456789"))
    def test_matches_ascii_digits(self, char: str) -> None:
        """Verify that ASCII digits 0–9 match.

        Parameters
        ----------
        char
            Digit character.
        """

        _assert_full_match(pattern=DIGIT_RE, text=char)

    def test_matches_unicode_digit(self) -> None:
        """Verify that non-ASCII Unicode digits (e.g. Arabic-Indic) match."""

        # Arabic-Indic digit five: ٥.
        _assert_full_match(pattern=DIGIT_RE, text="\u0665")

    @PARAM("char", ["a", " ", "!", "Z"])
    def test_rejects_non_digits(self, char: str) -> None:
        """Verify that non-digit characters do not match.

        Parameters
        ----------
        char
            Character expected to *not* match.
        """

        _assert_no_match(pattern=DIGIT_RE, text=char)


class TestFigurePrefixRe:
    """Tests for `FIGURE_PREFIX_RE`, dynamically built figure prefix pattern."""

    @pytest.fixture()
    def pattern(self) -> re.Pattern[str]:
        """Return a compiled version of the `FIGURE_PREFIX_RE` raw string.

        Returns
        -------
        re.Pattern[str]
            Compiled pattern anchored to start/end, case-insensitive.
        """

        return re.compile(rf"^(?:{FIGURE_PREFIX_PATTERN})$", re.IGNORECASE)

    def test_is_nonempty_string(self) -> None:
        """Verify that `FIGURE_PREFIX_RE` is a non-empty string."""

        assert isinstance(FIGURE_PREFIX_PATTERN, str)
        assert len(FIGURE_PREFIX_PATTERN) > 0

    def test_longer_prefixes_ordered_first(self) -> None:
        """Verify that the OR alternation lists longer prefixes before shorter ones."""

        parts = FIGURE_PREFIX_PATTERN.split("|")
        lengths = [len(p) for p in parts]
        assert lengths == sorted(lengths, reverse=True)

    def test_matches_known_prefixes(self, pattern: re.Pattern[str]) -> None:
        """Verify that at least a few common figure prefixes match.

        Parameters
        ----------
        pattern
            Compiled figure-prefix pattern.
        """

        for prefix in CaptionFigurePrefixes:
            _assert_full_match(pattern=pattern, text=prefix)


class TestTablePrefixPattern:
    """Tests for `TABLE_PREFIX_PATTERN`, dynamically built table prefix pattern."""

    def test_is_nonempty_string(self) -> None:
        """Verify that `TABLE_PREFIX_PATTERN` is a non-empty string."""

        assert isinstance(TABLE_PREFIX_PATTERN, str)
        assert len(TABLE_PREFIX_PATTERN) > 0

    def test_longer_prefixes_ordered_first(self) -> None:
        """Verify that the OR alternation lists longer prefixes before shorter ones."""

        parts = TABLE_PREFIX_PATTERN.split("|")
        lengths = [len(p) for p in parts]
        assert lengths == sorted(lengths, reverse=True)

    def test_matches_known_prefixes(self) -> None:
        """Verify that all entries from `CaptionTablePrefixes` match."""

        pat = re.compile(rf"^(?:{TABLE_PREFIX_PATTERN})$", re.IGNORECASE)

        for prefix in CaptionTablePrefixes:
            _assert_full_match(pattern=pat, text=prefix)


class TestTableCodeRe:
    """Tests for `TABLE_CODE_RE`, table prefix + numeric capture."""

    def test_captures_dotted_number(self) -> None:
        """Verify that a dotted table number is captured correctly."""

        prefix = sorted(CaptionTablePrefixes, key=len, reverse=True)[0]
        m = TABLE_CODE_RE.match(f"  {prefix} 3.2.1 extra text")
        assert m is not None
        assert m.group("num") == "3.2.1"

    def test_captures_simple_number(self) -> None:
        """Verify that a simple table reference captures its number."""

        prefix = sorted(CaptionTablePrefixes, key=len, reverse=True)[0]
        m = TABLE_CODE_RE.match(f"{prefix} 42")
        assert m is not None
        assert m.group("num") == "42"

    def test_case_insensitive(self) -> None:
        """Verify that matching is case-insensitive."""

        prefix = sorted(CaptionTablePrefixes, key=len, reverse=True)[0]
        m = TABLE_CODE_RE.match(f"{prefix.upper()} 7")
        assert m is not None
        assert m.group("num") == "7"

    def test_no_match_without_prefix(self) -> None:
        """Verify no match when the string lacks a table prefix."""

        assert TABLE_CODE_RE.match("Something 42") is None


class TestTokenRe:
    """Tests for `TOKEN_RE`, JWT/3-part base64url token matcher."""

    def test_bearer_case_insensitive(self) -> None:
        """Verify that the `bearer` prefix is matched case-insensitively."""

        m = TOKEN_RE.search("BEARER aaa.bbb.ccc")
        assert m is not None
        assert m.group(1) == "aaa.bbb.ccc"

    def test_matches_bare_jwt(self) -> None:
        """Verify that a bare three-part token matches and is captured."""

        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc_DEF-123"
        m = TOKEN_RE.search(token)
        assert m is not None
        assert m.group(1) == token

    def test_matches_bearer_prefix(self) -> None:
        """Verify that a token preceded by `Bearer ` is captured without the prefix."""

        token = "aaa.bbb.ccc"
        m = TOKEN_RE.search(f"Bearer {token}")
        assert m is not None
        assert m.group(1) == token

    def test_no_match_two_parts(self) -> None:
        """Verify that a two-part string does not match."""

        _assert_no_match(pattern=TOKEN_RE, text="aaa.bbb")


class TestWsRe:
    """Tests for `WS_RE` – whitespace sequence matcher."""

    def test_no_match_without_whitespace(self) -> None:
        """Verify no match in a string without any whitespace."""

        _assert_no_match(pattern=WS_RE, text="nospaces")

    @PARAM(
        "text,expected_parts",
        [
            ("hello world", ["hello", "world"]),
            ("a  b\tc\nd", ["a", "b", "c", "d"]),
            ("  leading", ["", "leading"]),
        ],
        ids=["single_space", "mixed_whitespace", "leading_whitespace"],
    )
    def test_splits_on_whitespace(
        self, *, expected_parts: list[str], text: str
    ) -> None:
        """Verify that splitting on `WS_RE` produces expected tokens.

        Parameters
        ----------
        expected_parts
            Expected list of parts after splitting.
        text
            String to split.
        """

        assert WS_RE.split(text) == expected_parts

    def test_sub_normalizes_whitespace(self) -> None:
        """Verify that substituting with a single space normalizes whitespace."""

        assert WS_RE.sub(" ", "a  \t\n  b") == "a b"
