"""This module contains constants used throughout the application."""

# Standard Library
from enum import Enum
from typing import Literal


# Enums for various constant types.
class BlockType(str, Enum):
    """The visual structural category of a content block.

    Types include:
        - artifact: Page numbers, running headers/footers
        - caption: Text specifically describing a table or figure
        - figure: Diagrams/figures/illustrations/flowcharts (boxed visual regions)
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


class StatementRole(str, Enum):
    """Semantic role of a KG node in the hierarchy."""

    EXPECTATION = "expectation"  # Normative statement (standard/outcome)
    DESCRIPTOR = "descriptor"  # Performance indicator/benchmark
    FRAMEWORK = "framework"  # The root document
    GRADE_LEVEL = "grade_level"  # e.g., "Standard III"
    GUIDANCE = "guidance"  # Pedagogical guidance (activities/resources/teacher notes)
    SECTION = "section"  # Structural grouping (e.g., "Section One")
    STRAND = "strand"  # e.g., "Main Competence"
    SUBJECT = "subject"  # e.g., "Mathematics"
    TOPIC = "topic"  # e.g., "Topic" or sub-strand
    UNRESOLVED = "unresolved"  # Content that could not be classified


# Literals/sets for various constant types.
CurriculumRelationshipTypes = Literal["hasEducationalAlignment"]
NonArtifacts = {
    "acknowledgements",
    "acknowledgments",
    "bibliography",
    "contents",
    "list of figures",
    "list of tables",
    "preface",
    "reference list",
    "references",
    "table of contents",
}
NormalizedStatementType = Literal["Standard", "Standard Grouping", "Other"]
RelationshipTypes = Literal["hasChild", "supports", "buildsTowards", "relatesTo"]
SectionBreakHeadings = {
    "appendix",
    "bibliography",
    "contents",
    "index",
    "list of figures",
    "list of tables",
    "reference list",
    "references",
    "table of contents",
}
