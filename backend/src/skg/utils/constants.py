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


class CurriculumEmitPolicy(str, Enum):
    """Controls what the curriculum skeleton matching engine does when a segment
    matches a node.

    Policies include:
        - CONTAINER_ONLY: Structural-only node--no document segment to match.
        - EMIT_GROUPING: Node becomes a grouping in the SegmentDecision
        - EMIT_GROUPING_AND_LEAF: Node produces BOTH a grouping AND leaves.
        - EMIT_LEAF: Node's matched content is emitted as a leaf statement.
        - EMIT_TABLE_ROWS: Node is a table container; rows become RowDecision[].
        - IGNORE: Matched segment is consumed but not emitted.
    """

    CONTAINER_ONLY = "container_only"
    EMIT_GROUPING = "emit_grouping"
    EMIT_GROUPING_AND_LEAF = "emit_grouping_and_leaf"
    EMIT_LEAF = "emit_leaf"
    EMIT_TABLE_ROWS = "emit_table_rows"
    IGNORE = "ignore"


class CurriculumMatchTarget(str, Enum):
    """The specific content to extract from a matched segment for curriculum skeleton
    matching.
    """

    CAPTION = "caption"  # Caption text bound to a table (from caption_bindings)
    HEADING = "heading"  # Heading text only (requires block_type == HEADING)
    TEXT = "text"  # Block segment text content (combined_text or text.text)


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


class UnresolvedReason(str, Enum):
    """Reasons why a segment decision could not be resolved."""

    DECISION_UNRESOLVED = "decision_unresolved"
    ID_COLLISION = "id_collision"
    FLAGGED_UNRESOLVED = "flagged_unresolved"
    LOW_CONFIDENCE_TABLE_MAPPING = "low_confidence_table_mapping"
    LOW_CONFIDENCE_DECISION_NOT_MATERIALIZED = (
        "low_confidence_decision_not_materialized"
    )
    PARENT_CONFLICT = "parent_conflict"
    UNMATCHED_BLOCK = "unmatched_block"
    UNMATCHED_HEADING = "unmatched_heading"
    UNMATCHED_TABLE = "unmatched_table"


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
# 3. context_groupings[] should contain OUTER context only (stage/grade/subject/etc.)/
# 4. row-local groupings like TOPIC/SUBTOPIC should live in RowDecision.groupings[].
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
