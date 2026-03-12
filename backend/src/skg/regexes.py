"""This module contains regexes used across the codebase. They are defined here to
avoid duplication and to ensure consistency.
"""

# Standard Library
import re

# Package Library
from skg.utils.constants import CaptionFigurePrefixes, CaptionTablePrefixes

# Matches a single alphabetical character, including standard English letters
# (A-Z, a-z) and extended Latin/Western European accented characters
# (like é, à, ç, ñ, ü). The ranges À-Ö, Ø-ö, and ø-ÿ are specifically chosen to skip
# the multiplication (×) and division (÷) signs, which are awkwardly wedged in the
# middle of those Unicode blocks.
ALPHA_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")

# Matches a base number, optionally followed by sub-identifiers separated by a dot,
# slash, or hyphen. For example: "1", "12", "1.1", "3-A", "4/B", "1.2.3-final".
CAPTION_NUMERIC_IDENTIFIER_RE = r"\d+(?:[./-][A-Za-z0-9]+)*"

# Matches valid Roman Numerals from 1 to 3999. The positive lookahead (?=[MDCLXVI])
# ensures it doesn't match an empty string.
CAPTION_ROMAN_NUMERAL_RE = (
    r"(?=[MDCLXVI])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
)

# Matches exactly one uppercase letter. For example: "A", "B", "Z".
CAPTION_SINGLE_LETTER_RE = r"[A-Z]"

# Combines the three above regexes into a single non-capturing group using the OR (|)
# operator. For example: Any valid identifier format (e.g., "1.2", "IV", or "A").
CAPTION_IDENTIFIER_RE = (
    rf"(?:"
    rf"{CAPTION_NUMERIC_IDENTIFIER_RE}"
    rf"|{CAPTION_ROMAN_NUMERAL_RE}"
    rf"|{CAPTION_SINGLE_LETTER_RE}"
    rf")"
)

# Matches one or more consecutive dash-like characters. Includes the standard hyphen
# (-), minus sign (−), en-dash (–), em-dash (—), and figure dash (‒). Usually used to
# normalize messy typography (e.g., replacing weird unicode dashes with a standard
# hyphen).
DASH_RE = re.compile(r"[-‐‒–—−]+")

# Matches a single numeric digit. NB: Because there is no re.ASCII flag, this will
# catch standard 0-9 as well as Unicode digits from other scripts (like Arabic-Indic
# numerals: ٠, ١, ٢).
DIGIT_RE = re.compile(r"\d")

# Dynamically builds an OR-separated string of escaped figure prefixes (like "Figure",
# "Fig.", "Fig"). Sorting by length in reverse ensures longer prefixes ("Figure") are
# checked before shorter ones ("Fig"), preventing partial matches.
FIGURE_PREFIX_RE = "|".join(
    re.escape(prefix) for prefix in sorted(CaptionFigurePrefixes, key=len, reverse=True)
)

# Matches Roman numerals from 1 to 15 (I through XV) as whole words (\b). Because of
# re.IGNORECASE, it will catch "iv", "IV", "Iv", etc. Hardcoding these is a quick, safe
# way to handle low numbers without a complex regex.
ROMAN_RE = re.compile(
    r"\b(XV|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|V|IV|III|II|I)\b", re.IGNORECASE
)

# Matches specific structural or educational keywords, ignoring case. The \b at the
# start and end ensures it only matches whole words (e.g., "class" matches, but
# "classic" does not). Catches English structural terms (grade, chapter, module, week)
# and French ones (niveau, étape, semaine). Also catches specific shorthand patterns
# like "p 1" (page 1) or "std iv" (standard 4).
STRUCTURAL_CONTEXT_CUE_RE = re.compile(
    r"\b("
    r"grade|class|primary|standard|std\.?|stage|theme|sub[-\s]?theme|strand|subject|"
    r"learning\s+area|unit|week|term|chapter|module|p\s*[1-9]|std\s*[ivx]+"
    r"|palier|jéego|j[ée]ego|semaine|étape|activit[ée]s|niveau|comp[ée]tence"
    r")\b",
    flags=re.IGNORECASE,
)

# Dynamically builds an OR-separated string of escaped table prefixes (like "Table",
# "Tab.", "Tab"). Like the figure pattern, sorting by descending length prevents short
# prefixes from overriding longer ones.
TABLE_PREFIX_PATTERN = "|".join(
    re.escape(prefix) for prefix in sorted(CaptionTablePrefixes, key=len, reverse=True)
)

# Matches the start of a string (^), optional space, a table prefix, space, and a
# number. It captures the numeric portion (e.g., "1", "3.2.1") into a named group
# called "num". This allows us to easily extract just the number later using
# match.group('num').
TABLE_CODE_RE = re.compile(
    rf"^\s*(?:{TABLE_PREFIX_PATTERN})\s+(?P<num>\d+(?:\.\d+)*)\b", re.IGNORECASE
)

# Matches a standard JSON Web Token (JWT) or similar 3-part base64url-encoded string.
# (?i) makes it case-insensitive. It optionally matches the word "bearer " before the
# token. The core pattern matches exactly three chunks of
# letters/numbers/hyphens/underscores separated by dots.
TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+)?([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)"
)

# Matches any sequence of one or more whitespace characters (spaces, tabs, newlines,
# etc.). Typically used to normalize spacing (e.g., replacing multiple spaces with a
# single space) or to split strings.
WS_RE = re.compile(r"\s+")
