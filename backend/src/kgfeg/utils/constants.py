"""This module contains constants used throughout the application. Module specific
constants should be scoped to the module.
"""

# Standard Library
from enum import Enum


# Enums for various constant types.
class BlockType(str, Enum):
    """The visual structural category of a content block.

    Types include:
        - artifact: Page numbers, running headers/footers
        - caption: Text specifically describing a table or figure
        - figure: Diagrams/figures/illustrations/flowcharts (boxed visual regions)
        - footnote: Footnotes/endnotes (typically smaller font, may be separated by a
            line)
        - heading: Visually distinct titles, section headers
        - list: A group of items (bullets, numbers)
        - paragraph: Standard blocks of prose
    """

    ARTIFACT = "artifact"
    CAPTION = "caption"
    FIGURE = "figure"
    FOOTNOTE = "footnote"
    HEADING = "heading"
    LIST = "list"
    PARAGRAPH = "paragraph"


class FigureKind(str, Enum):
    """Classification of figure/diagram type (non-semantic)."""

    BARCODE = "barcode"
    CHART = "chart"
    DIAGRAM = "diagram"
    EQUATION = "equation"
    FLOWCHART = "flowchart"
    GRAPH = "graph"
    ILLUSTRATION = "illustration"
    IMAGE = "image"
    LOGO = "logo"
    MAP = "map"
    OTHER = "other"
    SCHEMATIC = "schematic"
    TIMELINE = "timeline"
    UNKNOWN = "unknown"


class ItemBoundary(str, Enum):
    """Enumeration for item boundary states on a page.

    NB: These are semantic continuity flags (not "missing borders"):
        - both: Item continues from previous page AND onto next page (middle slice of a
            long item)
        - complete: Item is fully contained on this page
        - resumed: Item continues from the previous page
        - truncated: Item continues onto the next page
    """

    BOTH = "both"
    COMPLETE = "complete"
    RESUMED = "resumed"
    TRUNCATED = "truncated"


class NodeRole(str, Enum):
    """The role of a node in the canonical IR structure."""

    FRAMEWORK = "framework"  # The root document
    GRADE_LEVEL = "grade_level"
    LEARNING_AREA = "learning_area"  # e.g., "Literacy and Language"
    PROSE = "prose"  # Document structure/prose headings (Vision, Intro, etc.)
    SECTION = "section"  # # Curriculum grouping when meaningful
    STAGE = "stage"
    SUBSTAGE = "substage"  # e.g., "PALIER N" — milestone within a unit/Jéego
    STRAND = "strand"  # e.g., "Main Competence"
    SUBJECT = "subject"  # e.g., "Mathematics"
    SUBSTRAND = "substrand"
    SUBTHEME = "subtheme"
    SUBTOPIC = "subtopic"
    TERM = "term"
    THEME = "theme"
    TOPIC = "topic"  # e.g., "Topic" or sub-strand
    UNIT = "unit"
    UNRESOLVED = "unresolved"  # Content that could not be classified
    WEEK = "week"


class PageBoundaryState(str, Enum):
    """Enumeration for page boundary states of items.

    NB: These are semantic continuity flags (not "missing borders"):
        - both: Item continues from previous page AND onto next page (middle slice of a
            long item)
        - from_prev: Item continues from the previous page
        - to_next: Item continues onto the next page
        - standalone: Item is fully contained on this page
    """

    BOTH = "both"
    CONTINUES_FROM_PREV = "from_prev"
    CONTINUES_TO_NEXT = "to_next"
    STANDALONE = "standalone"


class PageContinuationKind(str, Enum):
    """The type of content continuity detected between pages."""

    FIGURE = "figure"
    NONE = "none"
    TABLE = "table"
    TEXT = "text"


# Literals/sets/etc. for various constant types.
CaptionFigurePrefixes: tuple[str, ...] = (
    "diagramme",
    "fig",
    "fig.",
    "figure",
    "kielelezo",
    "mchoro",
    "schéma",
)
CaptionTablePrefixes: tuple[str, ...] = (
    "jedwali",
    "tab",
    "tab.",
    "table",
    "tableau",
    "tbl",
    "tbl.",
)
