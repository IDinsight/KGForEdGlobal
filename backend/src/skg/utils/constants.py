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


class SegmentDecisionType(str, Enum):
    """Enumeration of high-level actions for segment decisions."""

    EMIT_FLAGGED_UNRESOLVED = "emit_flagged_unresolved"
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

# Context grouping role precedence (outer -> inner). Used to enforce consistent
# ordering of SegmentDecision.context_groupings[] across segments and (especially)
# across chunked table decisions.
#
# NB:
# 1. This order is *configurable per curriculum document* (via config.json) because
#   some curricula place certain containers (e.g., SECTION vs. WEEK) in different
#   relative positions.
# 2. Only roles present in DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER participate in
#   precedence-based checks. Roles omitted from the configured order are treated as
#   "unranked" and do not factor into context-grouping ordering/outer-ness validators.
# 3. context_groupings[] should contain OUTER context only (stage/grade/subject/etc.).
# 4. row-local groupings like TOPIC/SUBTOPIC usually live in RowDecision.groupings[].
#   However, some curricula surface them as true outer context, so they remain in the
#   default precedence list and can be kept/configured there when needed.
# 5. Order matters here!
DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER: tuple[NodeRole, ...] = (
    NodeRole.STAGE,
    NodeRole.GRADE_LEVEL,
    NodeRole.LEARNING_AREA,
    NodeRole.SUBJECT,
    NodeRole.STRAND,
    NodeRole.SUBSTRAND,
    NodeRole.THEME,
    NodeRole.SUBTHEME,
    NodeRole.TERM,
    NodeRole.UNIT,
    NodeRole.SUBSTAGE,
    NodeRole.SECTION,
    NodeRole.WEEK,
    NodeRole.TOPIC,
    NodeRole.SUBTOPIC,
)
