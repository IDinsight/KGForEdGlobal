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


class NodeRole(str, Enum):
    """The role of a node in the canonical IR structure."""

    FRAMEWORK = "framework"  # The root document
    GRADE_LEVEL = "grade_level"
    SECTION = "section"  # Structural grouping (e.g., "Section One")
    STAGE = "stage"
    STRAND = "strand"  # e.g., "Main Competence"
    SUBJECT = "subject"  # e.g., "Mathematics"
    SUBSTRAND = "substrand"
    SUBTOPIC = "subtopic"
    THEME = "theme"
    TOPIC = "topic"  # e.g., "Topic" or sub-strand
    UNIT = "unit"
    UNRESOLVED = "unresolved"  # Content that could not be classified
    WEEK = "week"


class SegmentDecisionType(str, Enum):
    """Enumeration of high-level actions for segment decisions."""

    EMIT_GROUPINGS_AND_LEAVES = "emit_groupings_and_leaves"
    EMIT_GROUPINGS_ONLY = "emit_groupings_only"
    EMIT_LEAVES_ONLY = "emit_leaves_only"
    IGNORE = "ignore"
    UNRESOLVED = "unresolved"


class StatementRole(str, Enum):
    """Semantic role of a KG node in the hierarchy."""

    EXPECTATION = "expectation"  # Normative statement (standard/outcome)
    DESCRIPTOR = "descriptor"  # Performance indicator/benchmark
    GUIDANCE = "guidance"  # Pedagogical guidance (activities/resources/teacher notes)


# Literals/sets/etc. for various constant types.
CaptionFigurePrefixes: tuple[str, ...] = (
    "fig",
    "fig.",
    "figure",
    "kielelezo",
    "mchoro",
)
CaptionKind = Literal["figure", "table", "unknown"]
CaptionTablePrefixes: tuple[str, ...] = (
    "jedwali",
    "tab",
    "tab.",
    "table",
    "tableau",
    "tbl",
    "tbl.",
)
CurriculumRelationshipTypes = Literal["hasEducationalAlignment"]
NonArtifacts = {
    "abbreviations and acronyms",
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
UnresolvedReason = Literal[
    "ID_COLLISION",
    "LOW_CONFIDENCE_TABLE_MAPPING",
    "LOW_CONFIDENCE_DECISION_NOT_MATERIALIZED",
    "PARENT_CONFLICT",
    "UNMATCHED_BLOCK",
    "UNMATCHED_HEADING",
    "UNMATCHED_TABLE",
]
