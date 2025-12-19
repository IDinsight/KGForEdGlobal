"""This module contains the Pydantic schemas for the Intermediate Representation (IR)
component of the SKG system.

These schemas are needed:

1. To make extraction reliable across messy PDFs.
2. To keep the pipeline modular so Layer A (IR) doesn't leak Layer B (LC KG mapping).
3. To enable config-driven extraction.

Organization Notes
------------------

Schemas are organized into 4 logical layers, moving from low-level primitives to
high-level containers.

1. Primitives

These are the foundational mixins and base classes. They don't represent a "thing" in
the document themselves, but they provide capabilities to everything else.
    - BaseIRModel: The strict config enforcer.
    - SpatialIR: The "Physics" module. It says "I exist at these coordinates."
    - ProvenancePointer: This is the bridge between semantics (Layer 3) and the
        physical/visual elements (Layer 2). It allows a high-level concept like "Grade
        2 Math Expectations" to point downward and say, "I came from Page 4, Section
        1.2, this specific text coordinate." It inherits from SpatialIR and is used by
        everything in the structure (Nodes, Statements, Relationships).
    - StructuralElementIR: The "Identity" module. It says "I have a unique ID (ref) and
        I can be traced back to a source (provenance)."
    - TranslationMetaIR: This is the bridge between Source Language and Target
        Language. It ensures that we don't just have English text, but a record of how
        we got that English text (e.g., "Google Translate, 98% confidence"). It is a
        property attached to text fields within HierarchyNodeIR and StatementIR. It is
        a "helper struct."
    - CaptionedIR: A specialized identity for things that usually have titles (tables,
        diagrams).

2. Physical/visual elements

These schemas represent the artifacts actually printed on the PDF page. They are not
the "curriculum" itself; they are the raw materials containing the curriculum. They are
primarily visual or layout-based.
    - DiagramIR: Charts, images, flowcharts.
    - TableIR/TableCellIR: Grids of data.

Basically, if you can point to it on a page but haven't interpreted its meaning yet, it
belongs here.

3. Logical/semantic elements

This is the IR layer output. These schemas represent the abstract meaning extracted
from the physical/visual elements layer. They form a graph of knowledge (Curriculum
Standards) rather than a tree of PDF objects.
    - EvidenceIR: It supports RelationshipIR. In a Knowledge Graph, edges usually just
        exist (A -> B). In our system, edges act like legal arguments: "A relates to B
        because of this specific evidence." In other words, it doesn't stand alone. We
        will never find a list of EvidenceIR objects floating in the DocumentIR. They
        only exist inside a RelationshipIR to justify why that link was created.
    - SequenceIR/TimeAllocationIR: Logic attributes attached to the graph.
    - GraphElementIR: It defines the contract for "What is a Curriculum Item?" It
        extends the generic StructuralElementIR (Identity) by adding logic-layer
        attributes: confidence, ordering, sequence, tags, and time_allocation. It also
        separates the physical/visual elements (tables/diagrams) from the "logical"
        (nodes/statements). Physical items have captions and pixels. Graph items have
        confidence scores and pedagogical tags.
    - CurriculumElementIR: It represents an interpreted instructional/alignment object
        (activity/resource/assessment/etc.) that we intend to carry forward as a
        first-class semantic node and connect via hasEducationalAlignment edges.
    - HierarchyNodeIR: The scaffolding (Grade 2, Unit 4, Topic: Math).
    - StatementIR: The payload (The student must learn X).
    - RelationshipIR: The glue (Unit 4 contains Statement X).

Basically, if it describes "what a student learns" or "how the course is structured"
(regardless of whether it came from a table or a paragraph), it belongs here.

4. Containers

These are the packaging mechanisms that organize the other layers into deliverable
units. They handle aggregation and metadata.
    - ElementContainerIR: The standardized "bucket" that holds lists of the items above.
    - PageIR: A partial bucket (one page of work).
    - DocumentMetadataIR/ExtractionRunIR: The stamp on the envelope.
    - DocumentIR: The complete bucket (the final product).

Main takeaways:

1. The "bridge" is StructuralElementIR. This is the most important class. It unifies
    the physical (e.g., tables) and semantic (e.g., statements) worlds by ensuring
    everything has a ref and provenance. This is what allows us to say "This statement
    (semantic) came from Row 4 of this table (physical)."
2. Strict Separation of TableIR vs. StatementIR: We implicitly decide that a table is
    evidence, not content. We extract the table first (physical), and then parse that
    table to generate statements (semantic). This is a robust design pattern for messy
    PDFs.
3. Containers are "dumb": DocumentIR and PageIR are just lists of the other items. They
    don't have complex logic. This makes serialization (saving to JSON/Database) very
    easy.
"""

# Future Library
from __future__ import annotations

# Standard Library
import json

from collections import Counter
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

# Third Party Library
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Package Library
from skg.schemas import ExtractionRunIR
from skg.utils.constants import (
    AcademicSubject,
    AdoptionStatus,
    BBoxKind,
    CurriculumElementType,
    EvidenceKind,
    HierarchyNodeType,
    ListKind,
    PageKind,
    RelationshipType,
    SequenceKind,
    StatementRole,
    TableKind,
    TextFormat,
    TimeAllocationPeriod,
    TimeAllocationUnit,
    TranslationMethod,
)
from skg.utils.general import validate_bcp47

# Common fields with descriptions.
BBox = Annotated[list[float], Field(min_length=4, max_length=4)]
BCP47Str = Annotated[str, AfterValidator(validate_bcp47)]
DocKeyField = Annotated[
    str, Field(..., description="Deterministic document key (e.g., sha256 hex).")
]
LanguageField = Annotated[
    BCP47Str,
    Field(
        default="und",
        description="Strict BCP-47 language code (e.g., 'en', 'sw'). Use 'und' if unknown.",
    ),
]
PdfNameField = Annotated[str, Field(..., description="Source PDF filename (no path).")]
RefField = Annotated[
    str, Field(..., description="Local unique reference for this element.")
]


# Schemas for primitives.
class BaseIRModel(BaseModel):
    """Base model enforcing strict config across the entire IR."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class KeyValuePair(BaseIRModel):
    """Pydantic model for a generic key-value pair."""

    key: str
    value: str


class SpatialIR(BaseIRModel):
    """Mixin for elements that occupy a physical region on a page.

    Provides standard bounding box fields used by both provenance pointers and table
    cells.
    """

    bbox: Optional[BBox] = Field(
        default=None, description="Bounding box [x0,y0,x1,y1] if available."
    )
    bbox_kind: BBoxKind = Field(
        default=BBoxKind.UNKNOWN, description="Coordinate system for bbox."
    )


class ProvenancePointer(SpatialIR):
    """Pydantic model for a provenance pointer within a PDF document.

    This is required to trace extracted statements/nodes back to their source location
    in the PDF for auditing and debugging.

    NB:
    1. page_index is 0-based (matches PyMuPDF/internal rendering).
    2. section is a best-effort heading/label.
    3. bbox (inherited from SpatialIR) can refer to either PDF points or image pixels.
    """

    doc_key: DocKeyField
    extraction_method: Optional[str] = Field(
        default=None,
        description="e.g., 'vision', 'text', 'hybrid', 'manual', 'table-parser'",
    )
    image_dimensions: Optional[list[int]] = Field(
        default=None,
        description="Rendered page image width/height in pixels when bbox_kind=image_pixels.",
    )
    page_dimensions: Optional[list[float]] = Field(
        default=None, description="Page width/height for normalizing PDF points."
    )
    page_index: int = Field(..., ge=0, description="0-based page index in the PDF.")
    page_label: Optional[str] = Field(
        default=None,
        description="The human-readable page number printed on the page (e.g. 'iv', '12').",
    )
    pdf_name: PdfNameField
    render_dpi: Optional[int] = Field(
        default=None,
        description="DPI used to render the page image when bbox_kind=image_pixels.",
    )
    section: Optional[str] = Field(
        default=None, description="Nearest section/heading, if known."
    )
    table_col: Optional[int] = Field(default=None, ge=0)
    table_ref: Optional[str] = Field(
        default=None, description="ref of the table element"
    )
    table_row: Optional[int] = Field(default=None, ge=0)
    text_quote: Optional[str] = Field(
        default=None, description="Exact snippet of text from the PDF for verification."
    )


class StructuralElementIR(BaseIRModel):
    """Base model for any element that has a unique reference, location context, and
    provenance within the document structure.

    This acts as the structural anchor for all extracted content (Diagrams, Tables,
    Nodes, Statements).
    """

    is_continuation: bool = Field(
        default=False,
        description="True if this element continues from a previous page/section.",
    )
    parent_ref: Optional[str] = Field(
        default=None, description="ref of the parent/scoping node."
    )
    provenance: list[ProvenancePointer] = Field(
        default_factory=list, description="Pointers to source location in PDF."
    )
    ref: RefField


class CaptionedIR(StructuralElementIR):
    """Base model for structural elements that typically feature a caption, such as
    Tables, Diagrams, Figures, or Equations.
    """

    caption: Optional[str] = Field(
        default=None,
        description="Title or label text associated with this element (e.g. 'Table 1: Grade breakdown').",
    )


class TranslationMetaIR(BaseIRModel):
    """Pydantic model for translation provenance/metadata."""

    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Optional confidence score."
    )
    method: Optional[TranslationMethod] = Field(
        default=None, description="e.g., 'llm', 'mt', 'human'."
    )
    model: Optional[str] = Field(
        default=None, description="Translation model identifier if applicable."
    )
    provider: Optional[str] = Field(
        default=None, description="e.g., 'OpenAI', 'Google', 'human'."
    )
    source_language: BCP47Str = Field(..., description="Language of the original text.")
    target_language: BCP47Str = Field(
        default="en", description="Language of the translated text."
    )
    translated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the translation was performed.",
        examples=[datetime.now(timezone.utc)],
    )


# Schemas for physical/visual elements.
class DiagramIR(CaptionedIR):
    """Pydantic model for visual diagrams (charts, geometric shapes, flowcharts)
    that are not text tables.
    """

    description: Optional[str] = Field(
        default=None,
        description="Text description of the diagram content (e.g., 'Triangle with height 5').",
    )
    image_path: Optional[str] = Field(
        default=None, description="Relative path to extracted image asset."
    )
    instructional_context: Optional[str] = Field(
        default=None,
        description="Surrounding text or instruction related to this diagram (e.g., 'Look at the chart below').",
    )
    ocr_text: Optional[str] = Field(
        default=None,
        description="Raw text detected inside the diagram (e.g., labels, numbers).",
    )


class TableCellIR(SpatialIR):
    """Pydantic model for a single cell within a table.

    NB: TableIR carries page-level provenance (via CaptionedIR), whereas cells carry
    cell-local spatial hints (via SpatialIR).
    """

    col_header: Optional[str] = Field(default=None)
    col_idx: Optional[int] = Field(default=None, ge=0)
    col_span: int = Field(default=1, ge=1)
    is_header: bool = Field(default=False)
    row_idx: Optional[int] = Field(default=None, ge=0)
    row_span: int = Field(default=1, ge=1)
    text: str


class TableIR(CaptionedIR):
    """Pydantic model that captures tabular data embedded within curriculum content."""

    col_headers: Optional[list[str]] = Field(
        default=None, description="Explicit list of column headers for this table."
    )
    rows: list[list[TableCellIR]]
    table_kind: TableKind = Field(
        default=TableKind.UNKNOWN,
        description="Is this a semantic data table or just document layout?",
    )


# Schemas for logical/semantic elements.
class EvidenceIR(BaseIRModel):
    """Pydantic model for evidence supporting a relationship between two
    GraphElementIRs.
    """

    features: list[KeyValuePair] = Field(default_factory=list)
    kind: EvidenceKind = Field(default=EvidenceKind.OTHER)
    provenance: list[ProvenancePointer] = Field(default_factory=list)
    text: Optional[str] = Field(default=None)


class SequenceIR(BaseIRModel):
    """Pydantic model for structured sequencing hints to support scope-and-sequence
    inference.
    """

    index: Optional[int] = Field(
        default=None, ge=0, description="0-based index within its kind (if known)."
    )
    kind: SequenceKind = Field(default=SequenceKind.OTHER)
    label: Optional[str] = Field(
        default=None, description="Human label like 'Term 2' or 'Week 5' (if present)."
    )
    text_value: Optional[str] = Field(
        default=None,
        description="Original text representation for ranges (e.g. 'Weeks 2-4').",
    )


class TimeAllocationIR(BaseIRModel):
    """Pydantic model that captures time/duration constraints often found in curriculum
    headers.
    """

    period: Optional[TimeAllocationPeriod] = Field(
        default=None,
        description="Whether the allocation is per-week/term/day, or total.",
    )
    text_value: Optional[str] = Field(
        default=None,
        description="Raw/original time allocation text (e.g., '3–5 periods/week').",
    )
    unit: Optional[TimeAllocationUnit] = Field(
        default=None,
        description="Unit of the allocation (e.g., periods, minutes, hours).",
    )
    value: Optional[float] = Field(
        default=None,
        ge=0,
        description="Single numeric value if the allocation is not a range (e.g., 5).",
    )
    value_max: Optional[float] = Field(
        default=None,
        ge=0,
        description="Upper bound (inclusive) when the allocation is a range.",
    )
    value_min: Optional[float] = Field(
        default=None,
        ge=0,
        description="Lower bound (inclusive) when the allocation is a range.",
    )

    @model_validator(mode="after")
    def validate_time_allocation(self) -> TimeAllocationIR:
        """Validate that at least one of value, value_min/value_max, or text_value is
        provided, and that the numeric values are consistent.

        Returns
        -------
        TimeAllocationIR
            The validated TimeAllocationIR instance.

        Raises
        ------
        ValueError
            If the validation checks fail.
        """

        has_numeric = (
            self.value is not None
            or self.value_max is not None
            or self.value_min is not None
        )
        has_text = bool(self.text_value and self.text_value.strip())

        if not has_numeric and not has_text:
            raise ValueError(
                "TimeAllocationIR requires at least one of: value, "
                "value_min/value_max, or text_value."
            )

        if has_numeric and self.unit is None:
            raise ValueError(
                "TimeAllocationIR.unit is required when numeric values are provided."
            )

        if self.value_min is not None and self.value_max is not None:
            if self.value_min > self.value_max:
                raise ValueError(
                    f"TimeAllocationIR value_min ({self.value_min}) cannot exceed "
                    f"value_max ({self.value_max})."
                )

        if self.value is not None:
            if self.value_min is not None and self.value < self.value_min:
                raise ValueError(
                    f"TimeAllocationIR value ({self.value}) cannot be less than "
                    f"value_min ({self.value_min})."
                )
            if self.value_max is not None and self.value > self.value_max:
                raise ValueError(
                    f"TimeAllocationIR value ({self.value}) cannot exceed "
                    f"value_max ({self.value_max})."
                )

        return self


class GraphElementIR(StructuralElementIR):
    """Abstract base for any item appearing in the semantic hierarchy (Nodes and
    Statements).

    Extends `StructuralElementIR` with logic-layer metadata: confidence scores,
    language context, sequencing hints, and ordering.
    """

    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Extraction confidence score."
    )
    extra: list[KeyValuePair] = Field(
        default_factory=list,
        description="Additional metadata fields (e.g., inference breadcrumbs).",
    )
    grade_levels: list[str] = Field(
        default_factory=list,
        description="Normalized grade levels this element applies to (e.g., ['01','02']). Empty if unknown.",
    )
    grade_labels_raw: list[str] = Field(
        default_factory=list,
        description="Raw/local grade labels for this element (e.g., ['P1', 'Std I–II']).",
    )
    grade_span_raw: Optional[str] = Field(
        default=None,
        description="Raw grade band text, e.g. 'Std I–II' or 'Lower Primary'.",
    )
    language: LanguageField
    local_code: Optional[str] = Field(
        default=None,
        description="Document-specific code if present (e.g., '3.9.4.1').",
    )
    order: Optional[float] = Field(
        default=None, description="Ordering hint within siblings."
    )
    path: list[str] = Field(
        default_factory=list,
        description="Ordered list of ancestor refs from root to this element (including self) for debugging and deterministic ID construction.",
    )
    sequence: Optional[SequenceIR] = Field(
        default=None, description="Optional structured sequencing hint."
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Cross-cutting themes or tags (e.g. 'Life Skills', 'Gender', 'Digital Literacy').",
    )
    time_allocation: Optional[TimeAllocationIR] = Field(
        default=None,
        description="Explicit duration/pacing info (e.g. '5 periods per week').",
    )


class CurriculumElementIR(GraphElementIR):
    """Pydantic model for an instructional curriculum element.

    This represents things like activities, resources/materials, teacher notes, and
    assessments as first-class nodes (instead of only embedding them as `guidance`
    statements). These elements are intended to be linked to standards items or
    expectations via RelationshipIR edges (e.g., export-time `hasEducationalAlignment`).

    NB:
    1. Use `element_type` to keep downstream mapping deterministic.
    2. Use `text` as the primary payload (often extracted from a table cell or a
        paragraph block).
    """

    element_type: CurriculumElementType = Field(
        ...,
        description="Type of curriculum element (activity, resource, assessment, etc.).",
    )
    element_type_other: Optional[str] = Field(
        default=None,
        description="If element_type=='other', the raw/novel element type string (e.g., 'teaching_materials').",
    )
    cross_references: list[str] = Field(
        default_factory=list,
        description="Explicit codes or references cited within the text (e.g. 'See 1.2').",
    )
    list_marker: Optional[str] = Field(
        default=None,
        description="The bullet/number marker if extracted from a list (e.g., 'a)', '1.', '•').",
    )
    list_kind: Optional[ListKind] = Field(
        default=None,
        description="Normalized list kind for deterministic downstream splitting/alignment (e.g., numeric vs bullet).",
    )
    list_level: Optional[int] = Field(
        default=None,
        ge=0,
        description="List nesting depth if extracted from a nested list (0 = top-level list item).",
    )
    list_path: Optional[list[str]] = Field(
        default=None,
        description="Full list marker path from outermost to this item (e.g., ['1.', 'a)']).",
    )
    original_label: Optional[str] = Field(
        default=None,
        description="The original column header or label, e.g., 'Learning Activities', 'Resources', 'Teaching Notes'.",
    )
    source_field: Optional[str] = Field(
        default=None,
        description="Where this element came from (e.g., table column/header like 'Learning Activities').",
    )
    text: str = Field(
        ..., description="Original curriculum element text (language per `language`)."
    )
    text_en: Optional[str] = Field(
        default=None, description="English translation of text, if needed."
    )
    text_format: TextFormat = Field(
        default=TextFormat.PLAIN, description="Format of the text content."
    )
    translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if text_en was produced via translation.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Optional URL if the element references an external resource.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_element_type(cls, data: Any) -> Any:
        """Allow novel element types while keeping a closed enum.

        If `element_type` is not in CurriculumElementType, we downgrade to OTHER and
        store the raw value in `element_type_other`.

        Parameters
        ----------
        data
            The input data dictionary.

        Returns
        -------
        Any
            The normalized data dictionary.
        """

        if not isinstance(data, dict):
            return data

        v = data.get("element_type")
        if isinstance(v, str):
            try:
                CurriculumElementType(v)
            except Exception:  # pylint: disable=broad-except
                data.setdefault("element_type_other", v)
                data["element_type"] = CurriculumElementType.OTHER
        return data


class HierarchyNodeIR(GraphElementIR):
    """Pydantic model for a grouping node in the curriculum hierarchy:
    grade/stage/subject/theme/topic/unit/week/etc.

    This is required to represent the hierarchical structure of the curriculum
    as extracted from the document. It gives us a consistent way to build the
    `hasChild` relationship, regardless of how the PDF is organized (tables, headings,
    thematic tweaks, etc.). It also provides a stable anchor for attaching statements
    (e.g., "This expectation belongs under Grade 2 -> Math -> Topic X"). This is
    separate from statements because in many PDFs, grouping labels are not themselves
    standards---they're just containers.

    NB:
    1. `ref` is a *local* stable reference within the extracted IR (unique within
        document).
    2. Later stages will create deterministic global KG IDs.
    """

    description: Optional[str] = Field(
        default=None, description="Introductory text or summary of this grouping."
    )
    description_en: Optional[str] = Field(
        default=None, description="English translation of description."
    )
    description_translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if description_en was produced via translation.",
    )
    label: str = Field(
        ..., description="Original label text (language per `language`)."
    )
    label_en: Optional[str] = Field(
        default=None, description="English translation of label, if needed."
    )
    label_translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if label_en was produced via translation.",
    )
    list_index: Optional[str] = Field(
        default=None,
        description="The numbering/lettering of this node (e.g., '1.2', 'Theme 3', 'A'). Useful when the label is just text.",
    )
    node_type: HierarchyNodeType = Field(
        ...,
        description="e.g., 'grade', 'stage', 'subject', 'theme', 'strand', 'topic', 'unit', 'week'",
    )
    node_type_other: Optional[str] = Field(
        default=None,
        description="If node_type=='other', the raw/novel node type string (e.g., 'sub-strand').",
    )
    original_label: Optional[str] = Field(
        default=None,
        description="Raw heading/column label used in the PDF for this grouping level (e.g., 'Sub-theme', 'Strand').",
    )
    source_field: Optional[str] = Field(
        default=None,
        description="Source column/field name when extracted from a table (e.g., 'Theme', 'Unit', 'Week').",
    )
    subject_tag: Optional[str] = Field(
        default=None,
        description="Explicit subject label inherited from parent or section headers (e.g., 'Mathematics').",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_node_type(cls, data: Any) -> Any:
        """Allow novel node types while keeping a closed enum.

        If `node_type` is not in HierarchyNodeType, we downgrade to OTHER and store the
        raw value in `node_type_other`.

        Parameters
        ----------
        data
            The input data dictionary.

        Returns
        -------
            The normalized data dictionary.
        """

        if not isinstance(data, dict):
            return data

        v = data.get("node_type")
        if isinstance(v, str):
            try:
                HierarchyNodeType(v)
            except Exception:  # pylint: disable=broad-except
                data.setdefault("node_type_other", v)
                data["node_type"] = HierarchyNodeType.OTHER
        return data


class StatementIR(GraphElementIR):
    """Pydantic model for a statement attached to (or scoped by) a hierarchy node.

    This is required to capture the actual instructional meaning, with an explicit
    role. Statements represent the extracted text that actually matters: expectation
    (normative outcomes/competences/objectives), performance descriptors
    (indicators/expected standard/assessment criteria), and guidance
    (teacher notes / pedagogical guidance). Activities/resources/materials/examples
    should be extracted as CurriculumElementIR and linked via hasEducationalAlignment.
    It gives us a clean separation of "what students should learn" vs. "how to teach"
    vs. "how to assess. Statements need independent provenance, IDs, and edges.

    NB:
    1. expectation: normative learning outcome/competence/objective/standard
    2. performance_descriptor: indicators/benchmarks/expected standard/assessment
        criteria
    3. guidance: teacher notes/pedagogical guidance (activities/resources belong in
        CurriculumElementIR)
    4. parent_ref can point to a HierarchyNodeIR (normal) or another StatementIR
        (nested bullets).
    """

    cross_references: list[str] = Field(
        default_factory=list,
        description="Explicit codes or references cited within the text (e.g. 'See 1.2').",
    )
    is_composite: bool = Field(
        default=False,
        description="True if this statement contains multiple distinct outcomes.",
    )
    list_marker: Optional[str] = Field(
        default=None,
        description="The bullet/number marker if extracted from a list (e.g., 'a)', '1.', '•').",
    )
    list_kind: Optional[ListKind] = Field(
        default=None,
        description="Normalized list kind for deterministic downstream splitting/alignment (e.g., numeric vs bullet).",
    )
    list_level: Optional[int] = Field(
        default=None,
        ge=0,
        description="List nesting depth if extracted from a nested list (0 = top-level list item).",
    )
    list_path: Optional[list[str]] = Field(
        default=None,
        description="Full list marker path from outermost to this item (e.g., ['1.', 'a)']).",
    )
    original_label: Optional[str] = Field(
        default=None,
        description="The original column header or label, e.g., 'Specific Competence', 'Life Skills', 'Knowledge'.",
    )
    proposed_atomic_skills: Optional[list[str]] = Field(
        default=None,
        description="List of atomic skills if the statement is compound (for 'llm_atomic_skills' policy).",
    )
    role: StatementRole = Field(..., description="Statement role in Canonical IR.")
    source_field: Optional[str] = Field(
        default=None,
        description="Where this text came from (e.g., table column/header like 'Learning Activities').",
    )
    source_field_role_hint: Optional[str] = Field(
        default=None,
        description="Optional hint about the source_field's typical semantic role (e.g., 'activity', 'resource', 'guidance').",
    )
    text: str = Field(
        ..., description="Original statement text (language per `language`)."
    )
    text_en: Optional[str] = Field(
        default=None, description="English translation of text, if needed."
    )
    text_format: TextFormat = Field(
        default=TextFormat.PLAIN, description="Format of the text content."
    )
    translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if text_en was produced via translation.",
    )


class RelationshipIR(BaseIRModel):
    """Pydantic model for a relationship between two GraphElementIRs."""

    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidence: list[EvidenceIR] = Field(default_factory=list)
    extra: list[KeyValuePair] = Field(
        default_factory=list, description="Additional metadata fields."
    )
    inference_type: Optional[str] = Field(default=None)
    is_inferred: bool = Field(default=False)
    provenance: list[ProvenancePointer] = Field(default_factory=list)
    ref: RefField
    rel_type: RelationshipType
    source_ref: RefField
    target_ref: RefField


# Schemas for containers.
class ElementContainerIR(BaseIRModel):
    """Standard container mixin for lists of extracted graph elements.

    Used by PageIR (to hold partial extraction results) and DocumentIR (to hold the
    aggregated canonical results).
    """

    diagrams: list[DiagramIR] = Field(default_factory=list)
    curriculum_elements: list[CurriculumElementIR] = Field(default_factory=list)
    nodes: list[HierarchyNodeIR] = Field(default_factory=list)
    relationships: list[RelationshipIR] = Field(default_factory=list)
    statements: list[StatementIR] = Field(default_factory=list)
    tables: list[TableIR] = Field(default_factory=list)


class PageIR(ElementContainerIR):
    """Pydantic model for extraction output for a single page.

    This represents a unit of work for extraction and recovery. It allows us to do
    incremental extraction---page-by-page, resume mid-PDF, debug specific pages.

    Inherits all element lists (nodes, statements, tables, etc.) from
    ElementContainerIR.
    """

    page_index: int = Field(..., ge=0, description="0-based page index.")
    page_kind: PageKind = Field(
        default=PageKind.UNKNOWN,
        description="High-level page classification (content vs front matter) for deterministic downstream filtering.",
    )
    warnings: list[str] = Field(default_factory=list)


class DocumentMetadataIR(BaseIRModel):
    """Pydantic model for document-level metadata.

    This keeps document-level facts out of the hierarchy and statements. It gives us
    clean separation of "what this document is about" vs. "what it contains." It also
    makes it easier to produce one `StandardsFramework` per PDF with correct metadata.

    NB:
    1. extra: Allows for extensibility (e.g., custom fields not yet standardized).
    """

    academic_subjects_normalized: list[AcademicSubject] = Field(
        default_factory=list,
        description="Normalized academic subjects using the Learning Commons controlled vocabulary (for export).",
    )
    academic_subject_primary: Optional[AcademicSubject] = Field(
        default=None,
        description="Primary Learning Commons academicSubject value for the framework when exporting (required by LC KG).",
    )
    adoption_status: Optional[AdoptionStatus] = Field(
        default=None,
        description="Adoption status (e.g., 'adopted', 'draft') if known.",
    )
    attribution_statement: Optional[str] = Field(
        default=None, description="Attribution statement if required."
    )
    country: Optional[str] = Field(default=None)
    date_created: Optional[datetime] = Field(
        default=None,
        description="Document creation date if known (timezone-aware recommended).",
    )
    date_modified: Optional[datetime] = Field(
        default=None,
        description="Document last modified/published date if known (timezone-aware recommended).",
    )
    document_kind: Optional[str] = Field(default=None)
    extra: list[KeyValuePair] = Field(
        default_factory=list, description="Additional metadata fields."
    )
    framework_type: Optional[str] = Field(
        default=None,
        description="e.g., 'National Syllabus', 'Teacher Guide', 'Assessment Framework'.",
    )
    grade_labels_raw: list[str] = Field(
        default_factory=list,
        description="Raw/local grade labels as written in the document (e.g., ['P1', 'Std I–II', 'Lower Primary']).",
    )
    grade_range: Optional[str] = Field(
        default=None, description="e.g., 'Grade 1-3', 'Std I-VI'"
    )
    jurisdiction: Optional[str] = Field(default=None)
    languages: list[BCP47Str] = Field(
        default_factory=list, description="Languages present in the PDF."
    )
    license: Optional[str] = Field(
        default=None, description="License identifier/URL if known."
    )
    ministry_or_author: Optional[str] = Field(default=None)
    ministry_or_author_en: Optional[str] = Field(
        default=None, description="English translation of ministry/author, if needed."
    )
    ministry_or_author_translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if ministry_or_author_en was produced via translation.",
    )
    normalized_grade_levels: list[str] = Field(
        default_factory=list,
        description="Normalized list of grades (e.g. ['01', '02']).",
    )
    primary_language: Optional[BCP47Str] = Field(
        default=None,
        description="Primary/dominant language of the document content (BCP-47).",
    )
    provider: Optional[str] = Field(
        default=None,
        description="Provider/organization responsible for publishing the framework, if known.",
    )
    publisher: Optional[str] = Field(default=None)
    publisher_en: Optional[str] = Field(
        default=None, description="English translation of publisher, if needed."
    )
    publisher_translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if publisher_en was produced via translation.",
    )
    source_url: Optional[str] = Field(
        default=None, description="Canonical source URL if known."
    )
    subject_areas: list[str] = Field(
        default_factory=list,
        description="List of distinct subjects identified in the doc (e.g., ['Math', 'Science']).",
    )
    subject_areas_en: list[str] = Field(
        default_factory=list,
        description="English translations of subject_areas (if needed).",
    )
    subject_areas_translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if subject_areas_en was produced via translation.",
    )
    title: Optional[str] = Field(default=None)
    title_en: Optional[str] = Field(
        default=None, description="English translation of title, if needed."
    )
    title_translation_meta: Optional[TranslationMetaIR] = Field(
        default=None,
        description="Translation metadata if title_en was produced via translation.",
    )
    version: Optional[str] = Field(default=None)
    year: Optional[int] = Field(default=None, ge=1900, le=2100)

    @field_validator("extra", mode="before")
    @classmethod
    def convert_dict_to_kv_list(cls, v: Any) -> Any:
        """Allow Python code to pass a dict, but converts it to list[KeyValuePair] so
        the model remains valid and Strict Mode compliant.

        Parameters
        ----------
        v
            The input value for the `extra` field.

        Returns
        -------
        Any
            The converted list of KeyValuePair dictionaries, or the original value if
            it's not a dict.
        """

        if isinstance(v, dict):
            converted = []
            for key, val in v.items():
                # Value must be a string for KeyValuePair. If it's complex (like the
                # PyMuPDF dict), we serialize it to JSON.
                if isinstance(val, (dict, list, bool, int, float)):
                    val_str = json.dumps(val, default=str)
                else:
                    val_str = str(val)
                converted.append({"key": key, "value": val_str})
            return converted
        return v


class DocumentIR(ElementContainerIR):
    """Pydantic model for the complete extracted document intermediate representation.

    This is the canonical Layer A output for a PDF. It gives us a clean, stable
    contract between extraction and mapping.

    NB:
    1. pages: Retain page-level outputs for debugging/resume.
    2. root_node_refs: Explicit entry points into the intended top-level hierarchy for
       this PDF (often one per subject/section, but can be multiple). This prevents
       downstream mapping/export from guessing roots by `parent_ref is None`, which can
       be noisy when extraction produces orphans or parallel trees (e.g., front-matter,
       appendices, or separate subject sections). Exporters and mappers should start
       hierarchy traversal from `root_node_refs` to build deterministic `hasChild`
       structure and to avoid treating stray nodes as top-level roots.
    3. Inherits aggregated lists (nodes, statements, etc.) from ElementContainerIR.
    """

    doc_key: DocKeyField
    extraction_run: Optional[ExtractionRunIR] = Field(default=None)
    metadata: DocumentMetadataIR = Field(default_factory=DocumentMetadataIR)
    pages: list[PageIR] = Field(default_factory=list)
    pdf_name: PdfNameField
    root_node_refs: list[RefField] = Field(
        default_factory=list,
        description="refs of top-level HierarchyNodeIR roots (if multiple).",
    )
    schema_version: Literal["0.1"] = Field(default="0.1")

    @model_validator(mode="after")
    def validate_roots(self) -> DocumentIR:
        """Ensure that root_node_refs are valid and have no parent_ref.

        Returns
        -------
        DocumentIR
            The validated DocumentIR instance.

        Raises
        ------
        ValueError
            If any root_node_ref is unknown or has a parent_ref.
        """

        node_by_ref = {n.ref: n for n in self.nodes}
        for r in self.root_node_refs:
            if r not in node_by_ref:
                raise ValueError(f"root_node_refs contains unknown ref: {r}")
            if node_by_ref[r].parent_ref is not None:
                raise ValueError(
                    f"root node {r} has parent_ref={node_by_ref[r].parent_ref}"
                )
        return self

    @model_validator(mode="after")
    def validate_unique_refs(self) -> DocumentIR:
        """Ensure that all refs across all elements are unique.

        Returns
        -------
        DocumentIR
            The validated DocumentIR instance.

        Raises
        ------
        ValueError
            If any duplicate refs are found across elements.
        """

        all_refs: list[str] = (
            [n.ref for n in self.nodes]
            + [s.ref for s in self.statements]
            + [c.ref for c in self.curriculum_elements]
            + [t.ref for t in self.tables]
            + [d.ref for d in self.diagrams]
            + [r.ref for r in self.relationships]
        )

        counts = Counter(all_refs)
        dupes = [ref for ref, c in counts.items() if c > 1]
        if dupes:
            raise ValueError(f"Duplicate refs found: {sorted(dupes)[:20]}")
        return self

    @model_validator(mode="after")
    def validate_provenance_doc_identity(  # pylint:disable=too-complex
        self,
    ) -> DocumentIR:
        """Ensure all provenance pointers refer to this document's doc_key/pdf_name.

        Returns
        -------
        DocumentIR
            The validated DocumentIR instance.

        Raises
        ------
        ValueError
            If any provenance pointer has a mismatched doc_key or pdf_name.
        """

        def _check(ptr: ProvenancePointer, where: str) -> None:
            """Check a single ProvenancePointer for doc_key/pdf_name consistency.

            Parameters
            ----------
            ptr
                The ProvenancePointer to check.
            where
                String describing the location of the pointer for error messages.

            Raises
            ------
            ValueError
                If the doc_key or pdf_name do not match.
            """

            if ptr.doc_key != self.doc_key:
                raise ValueError(
                    f"Provenance doc_key mismatch at {where}. Got {ptr.doc_key} "
                    f"expected {self.doc_key}"
                )
            if ptr.pdf_name != self.pdf_name:
                raise ValueError(
                    f"Provenance pdf_name mismatch at {where}. Got {ptr.pdf_name} "
                    f"expected {self.pdf_name}"
                )

        def _check_container(container: ElementContainerIR, prefix: str) -> None:
            """Check all provenance pointers in an ElementContainerIR.

            Parameters
            ----------
            container
                The ElementContainerIR to check.
            prefix
                String prefix for error messages.
            """

            for el in (
                container.nodes
                + container.statements
                + container.curriculum_elements
                + container.tables
                + container.diagrams
            ):
                for i, ptr in enumerate(el.provenance):
                    _check(
                        ptr,
                        f"{prefix}.{el.__class__.__name__}[{el.ref}].provenance[{i}]",
                    )
            for rel in container.relationships:
                for i, ptr in enumerate(rel.provenance):
                    _check(ptr, f"{prefix}.RelationshipIR[{rel.ref}].provenance[{i}]")
                for e_i, ev in enumerate(rel.evidence):
                    for p_i, ptr in enumerate(ev.provenance):
                        _check(
                            ptr,
                            f"{prefix}.RelationshipIR[{rel.ref}].evidence[{e_i}].provenance[{p_i}]",
                        )

        _check_container(self, prefix="document")

        for page in self.pages:
            _check_container(page, prefix=f"page[{page.page_index}]")

        return self

    @model_validator(mode="after")
    def validate_relationship_endpoints(self) -> DocumentIR:
        """Ensure that all relationship endpoints refer to valid elements.

        Returns
        -------
        DocumentIR
            The validated DocumentIR instance.

        Raises
        ------
        ValueError
            If any relationship source_ref or target_ref is unknown.
        """

        valid = {
            x.ref
            for x in (
                self.nodes
                + self.statements
                + self.curriculum_elements
                + self.tables
                + self.diagrams
            )
        }
        for rel in self.relationships:
            if rel.source_ref not in valid:
                raise ValueError(
                    f"Relationship {rel.ref} has unknown source_ref={rel.source_ref}"
                )
            if rel.target_ref not in valid:
                raise ValueError(
                    f"Relationship {rel.ref} has unknown target_ref={rel.target_ref}"
                )
        return self
