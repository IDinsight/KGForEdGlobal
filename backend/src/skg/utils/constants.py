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


class FrontMatterHeadings(str, Enum):
    """Document-structure headings that are NOT part of curriculum hierarchy.

    These are common in curriculum PDFs (Vision, Introduction, Assessment guidance,
    etc.) but should NOT become NodeRole.SECTION in the standards tree.
    """

    ACKNOWLEDGMENT = "acknowledgment"
    ACKNOWLEDGMENTS = "acknowledgments"
    ACKNOWLEDGEMENT = "acknowledgement"
    ACKNOWLEDGEMENTS = "acknowledgements"
    AIMS = "aims"
    ASSESSMENT = "assessment"
    BACKGROUND = "background"
    FOREWORD = "foreword"
    INTRODUCTION = "introduction"
    MISSION = "mission"
    OBJECTIVES = "objectives"
    PREFACE = "preface"
    PURPOSE = "purpose"
    RATIONALE = "rationale"
    SUGGESTED_TEACHING_METHODOLOGY = "suggested teaching methodology"
    STRUCTURE_OF_SYLLABUS = "structure of syllabus"
    STRUCTURE_OF_THE_SYLLABUS = "structure of the syllabus"
    TEACHING_METHODOLOGY = "teaching methodology"
    TIME_ALLOCATION = "time allocation"
    VISION = "vision"


class GroupingCanonicalizationAction(str, Enum):
    """Canonicalization action for curriculum groupings.

    Options are:

    1. DROP -> drop this grouping entirely (output must be empty)
    2. KEEP -> keep as-is (output must be empty or exactly the same as input)
    3. REPLACE -> replace with exactly 1 canonical grouping (same role)
    4. SPLIT -> replace with 2+ canonical groupings (roles may differ)
    """

    DROP = "drop"
    KEEP = "keep"
    REPLACE = "replace"
    SPLIT = "split"


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
    LEARNING_AREA = "learning_area"  # e.g., "Literacy and Language"
    PROSE = "prose"  # Document structure/prose headings (Vision, Intro, etc.)
    SECTION = "section"  # # Curriculum grouping when meaningful
    STAGE = "stage"
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


class SegmentDecisionType(str, Enum):
    """Enumeration of high-level actions for segment decisions."""

    EMIT_FLAGGED_UNRESOLVED = "emit_flagged_unresolved"
    EMIT_GROUPINGS_AND_LEAVES = "emit_groupings_and_leaves"
    EMIT_GROUPINGS_ONLY = "emit_groupings_only"
    EMIT_LEAVES_ONLY = "emit_leaves_only"
    IGNORE = "ignore"
    UNRESOLVED = "unresolved"


class SpineSplitApplyTo(str, Enum):
    """Define where split rules may be applied."""

    ANY = "any"
    BLOCK_LOCAL = "block_local"  # SegmentDecision.groupings
    OUTER_CONTEXT = "outer_context"  # SegmentDecision.context_groupings
    TABLE_ROW_LOCAL = "table_row_local"  # RowDecision.groupings


class SpineViolationPolicy(str, Enum):
    """Define what to do when a decision cannot be normalized to the spine without
    guessing.

    Attributes
    ----------
    FLAG_UNRESOLVED
        Set decision_type=EMIT_FLAGGED_UNRESOLVED
    KEEP_AS_IS
        Do not rewrite; allow compiler to proceed (rarely recommended)
    """

    FLAG_UNRESOLVED = "flag_unresolved"
    KEEP_AS_IS = "keep_as_is"


class StatementRole(str, Enum):
    """Semantic role of a KG node in the hierarchy."""

    EXPECTATION = "expectation"  # Normative statement (standard/outcome)
    DESCRIPTOR = "descriptor"  # Performance indicator/benchmark
    GUIDANCE = "guidance"  # Pedagogical guidance (activities/resources/teacher notes)


# Literals/sets for various constant types.
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
