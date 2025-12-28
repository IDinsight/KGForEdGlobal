"""This module contains constants used throughout the application."""

# Standard Library
from enum import Enum
from typing import Annotated

# Third Party Library
from pydantic import Field

# Common fields with descriptions.
DocKeyField = Annotated[
    str, Field(..., description="Deterministic document key (e.g., sha256 hex).")
]
PdfNameField = Annotated[str, Field(..., description="Source PDF filename (no path).")]


# Enums for various constant types.
class BlockType(str, Enum):
    """The visual structural category of a content block."""

    ARTIFACT = (
        "artifact"  # Page numbers, running headers/footers (to be filtered later)
    )
    CAPTION = "caption"  # Text specifically describing a table or figure
    FIGURE = (
        "figure"  # Diagrams/figures/illustrations/flowcharts (boxed visual regions)
    )
    HEADING = "heading"  # Visually distinct titles, section headers
    LIST = "list"  # A group of items (bullets, numbers)
    PARAGRAPH = "paragraph"  # Standard blocks of prose


class FigureKind(str, Enum):
    """Classification of figure/diagram type (non-semantic)."""

    CHART = "chart"
    DIAGRAM = "diagram"
    FLOWCHART = "flowchart"
    GRAPH = "graph"
    ILLUSTRATION = "illustration"
    IMAGE = "image"
    MAP = "map"
    SCHEMATIC = "schematic"
    TIMELINE = "timeline"
    UNKNOWN = "unknown"


class ItemBoundary(str, Enum):
    """Enumeration for item boundary states on a page."""

    BOTH = "both"  # Item continues from prev page AND to next page
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


class PageContinuationKind(str, Enum):
    """The type of content continuity detected between pages."""

    FIGURE = "figure"
    NONE = "none"
    TABLE = "table"
    TEXT = "text"
    UNCLEAR = "unclear"
