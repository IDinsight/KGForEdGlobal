"""This module contains constants used throughout the application."""

# Standard Library
from enum import Enum


class AcademicSubject(str, Enum):
    """Enumeration of academic subjects."""

    ENGLISH_LANGUAGE_ARTS = "English Language Arts"
    MATHEMATICS = "Mathematics"
    OTHER = "other"
    SCIENCE = "Science"
    SOCIAL_STUDIES = "Social Studies"


class AdoptionStatus(str, Enum):
    """Enumeration of adoption statuses for standards."""

    ADOPTED = "Adopted"
    DRAFT = "Draft"
    IMPLEMENTED = "Implemented"
    OTHER = "other"
    PROPOSED = "Proposed"
    RETIRED = "Retired"


class BBoxKind(str, Enum):
    """Enumeration of bounding box kinds."""

    IMAGE_PIXELS = "image_pixels"
    PDF_POINTS = "pdf_points"
    UNKNOWN = "unknown"


class CurriculumElementType(str, Enum):
    """Enumeration of curriculum element types."""

    ACTIVITY = "activity"
    ASSESSMENT = "assessment"
    EXAMPLE = "example"
    LESSON = "lesson"
    MATERIAL = "material"
    OTHER = "other"
    RESOURCE = "resource"
    TEACHER_NOTE = "teacher_note"


class EvidenceKind(str, Enum):
    """Enumeration of evidence kinds."""

    HEURISTIC = "heuristic"
    MANUAL_NOTE = "manual_note"
    OTHER = "other"
    PROVENANCE = "provenance"
    QUOTE = "quote"
    SIMILARITY = "similarity"
    TABLE_CELL = "table_cell"


class HierarchyNodeType(str, Enum):
    """Enumeration of common hierarchy node types."""

    COMPETENCY_AREA = "competency_area"
    DOMAIN = "domain"
    GRADE = "grade"
    LEARNING_AREA = "learning_area"
    MODULE = "module"
    OTHER = "other"
    QUARTER = "quarter"
    SEMESTER = "semester"
    STAGE = "stage"
    STRAND = "strand"
    SUBDOMAIN = "subdomain"
    SUBJECT = "subject"
    SUBTHEME = "subtheme"
    SUBTOPIC = "subtopic"
    TERM = "term"
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
    OTHER = "other"
    STAGE = "stage"
    TERM = "term"
    UNIT = "unit"
    WEEK = "week"


class StatementRole(str, Enum):
    """Enumeration of statement roles in the Canonical Curriculum IR.

    Canonical roles:
      - expectation: normative outcomes/competences/objectives/standards
      - performance_descriptor: indicators/benchmarks/expected standard/assessment
        criteria
      - guidance: teacher notes / pedagogical guidance (NOT activities/resources)

    NB: Activities/resources/materials/examples should be represented as
    CurriculumElementIR with an appropriate CurriculumElementType.
    """

    EXPECTATION = "expectation"
    GUIDANCE = "guidance"
    PERFORMANCE_DESCRIPTOR = "performance_descriptor"
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
    OTHER = "other"
    PLAIN = "plain"


class TimeAllocationPeriod(str, Enum):
    """Enumeration of time allocation periods."""

    OTHER = "other"
    PER_DAY = "per_day"
    PER_TERM = "per_term"
    PER_WEEK = "per_week"
    TOTAL = "total"


class TimeAllocationUnit(str, Enum):
    """Enumeration of time allocation units."""

    HOURS = "hours"
    MINUTES = "minutes"
    OTHER = "other"
    PERIODS = "periods"
    WEEKS = "weeks"


class TranslationMethod(str, Enum):
    """Enumeration of translation methods."""

    HUMAN = "human"
    LLM = "llm"
    MT = "mt"
    OTHER = "other"
