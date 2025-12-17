"""This module contains constants used throughout the application."""

# Standard Library
from enum import Enum


class BBoxKind(str, Enum):
    """Enumeration of bounding box kinds."""

    IMAGE_PIXELS = "image_pixels"
    PDF_POINTS = "pdf_points"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):
    """Enumeration of evidence kinds."""

    HEURISTIC = "heuristic"
    MANUAL_NOTE = "manual_note"
    OTHER = "other"
    PROVENANCE = "provenance"
    QUOTE = "quote"
    SIMILARITY = "similarity"
    TABLE_CELL = "table_cell"


class HierarchyNodeTypes:
    """Constants for common hierarchy node types.

    NB: Not an Enum because extraction might yield novel types (e.g. 'sub-strand') that
    we want to allow, but these are the standard targets.
    """

    GRADE = "grade"
    STAGE = "stage"
    STRAND = "strand"
    SUBJECT = "subject"
    THEME = "theme"
    TOPIC = "topic"
    UNIT = "unit"
    WEEK = "week"


class ListKind(str, Enum):
    """Enumeration of list marker kinds."""

    ALPHA = "alpha"  # Ordered alphabetic markers like a), b.
    BULLET = "bullet"  # Unordered bullet markers like •, -, –
    DASH = "dash"  # Dash-only markers when ambiguous
    NUMERIC = "numeric"  # Ordered numeric markers like 1., 2)
    OTHER = "other"
    ROMAN = "roman"  # Ordered roman numerals like i), iv.


class RelationshipType(str, Enum):
    """Enumeration of relationship types between statements."""

    BUILDS_TOWARDS = "buildsTowards"
    HAS_CHILD = "hasChild"
    HAS_EDUCATIONAL_ALIGNMENT = "hasEducationalAlignment"
    REFERENCES = "references"
    RELATES_TO = "relatesTo"
    SUPPORTS = "supports"


class SequenceKind(str, Enum):
    """Enumeration of sequence kinds."""

    GRADE = "grade"
    LESSON = "lesson"
    STAGE = "stage"
    TERM = "term"
    UNIT = "unit"
    UNKNOWN = "unknown"
    WEEK = "week"


class StatementRole(str, Enum):
    """Enumeration of statement roles."""

    ACTIVITY = "activity"  # Specific learning activities/exercises
    EXPECTATION = "expectation"  # Standards/outcomes/competences
    GUIDANCE = "guidance"  # Teacher notes/prerequisites
    PERFORMANCE_DESCRIPTOR = "performance_descriptor"  # Assessment criteria
    RESOURCE = "resource"  # Materials/textbooks listed
    UNKNOWN = "unknown"


class TableKind(str, Enum):
    """Enumeration of table kinds."""

    DATA = "data"
    LAYOUT = "layout"
    UNKNOWN = "unknown"


class TextFormat(str, Enum):
    """Enumeration of text formats."""

    LATEX = "latex"
    MARKDOWN = "markdown"
    PLAIN = "plain"


class TimeAllocationPeriod(str, Enum):
    """Enumeration of time allocation periods."""

    PER_DAY = "per_day"
    PER_TERM = "per_term"
    PER_WEEK = "per_week"
    TOTAL = "total"


class TimeAllocationUnit(str, Enum):
    """Enumeration of time allocation units."""

    HOURS = "hours"
    MINUTES = "minutes"
    PERIODS = "periods"
    WEEKS = "weeks"


class TranslationMethod(str, Enum):
    """Enumeration of translation methods."""

    HUMAN = "human"
    LLM = "llm"
    MT = "mt"
