"""This module contains constants used throughout the application."""

# Standard Library
from enum import Enum
from typing import Annotated

# Third Party Library
from pydantic import AfterValidator, Field

# Package Library
from skg.utils.general import validate_bcp47

# Common fields with descriptions.
BBox = Annotated[
    list[float],
    Field(
        min_length=4,
        max_length=4,
        description="Bounding box [x0, y0, x1, y1] in absolute pixels (px) relative to the image dimensions.",
    ),
]
BCP47Str = Annotated[str, AfterValidator(validate_bcp47)]
DocKeyField = Annotated[
    str, Field(..., description="Deterministic document key (e.g., sha256 hex).")
]
LanguageField = Annotated[
    BCP47Str,
    Field(
        default="unk",
        description="Strict BCP-47 language code (e.g., 'en', 'sw'). Use 'unk' if unknown.",
    ),
]
PdfNameField = Annotated[str, Field(..., description="Source PDF filename (no path).")]


# Enums for various constant types.
class BlockType(str, Enum):
    """The visual structural category of a content block."""

    ARTIFACT = (
        "artifact"  # Page numbers, running headers/footers (to be filtered later)
    )
    CAPTION = "caption"  # Text specifically describing a table or figure
    HEADING = "heading"  # Visually distinct titles, section headers
    KEY_VALUE = (
        "key_value"  # Distinct "Label: Value" pairs (common in metadata sections)
    )
    LIST = "list"  # A group of items (bullets, numbers)
    PARAGRAPH = "paragraph"  # Standard blocks of prose


class ContentRole(str, Enum):
    """The logical role of the content, used to aid stitching and semantic
    classification.
    """

    CURRICULUM_CODE = "curriculum_code"  # Explicit codes standing alone (e.g. "3.1.2")
    LIST_BODY = "list_body"  # A paragraph that is logically part of a list item (e.g., a continuation from prev page)
    LIST_MARKER = "list_marker"  # The enumerator itself (e.g. "1.", "a)", "•")
    LIST_ITEM = "list_item"  # A standard complete list item
    REGULAR = "regular"  # Standard content
    SECTION_HEADER = (
        "section_header"  # Explicit section dividers (e.g. "Topic 1: Numbers")
    )
    TEACHER_GUIDANCE = "teacher_guidance"  # Text visually distinguished as instructions (italics/sidebars)


class ItemBoundary(str, Enum):
    """Enumeration for item boundary states on a page."""

    COMPLETE = "complete"  # Item is fully contained on this page
    RESUMED = "resumed"  # Item is a continuation from top
    TRUNCATED = "truncated"  # Item is cut off at the bottom


class LinkType(str, Enum):
    """Enumeration of link types between items on different pages."""

    CONTINUE_LIST = "continue_list"
    JOIN_TEXT = "join_text"
    MERGE_TABLE = "merge_table"


class PageBoundaryState(str, Enum):
    """Enumeration for page boundary states of items."""

    BOTH = "both"  # Breaks at both top and bottom
    CONTINUES_FROM_PREV = "from_prev"  # Visual break at the top
    CONTINUES_TO_NEXT = "to_next"  # Visual break at the bottom
    STANDALONE = "standalone"  # Page starts and ends cleanly


class TextStyle(str, Enum):
    """Enumeration of text styles."""

    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    UPPERCASE = "uppercase"
