"""This module contains schemas for exporting a *shape-preserving* Learning Commons
Knowledge Graph.

These models are intentionally **non-US-centric**:

1. All enum-like fields (jurisdiction, language, academic subject, adoption status,
    etc.) are modeled as strings.
2. Unknown/extra per-node and per-relationship details should go into `metadata`.
3. `notes` are for free use.
"""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime
from typing import Any, Literal, Optional, Self, Sequence
from urllib.parse import urlparse
from uuid import UUID

# Third Party Library
from pydantic import Field, field_validator, model_validator

# Package Library
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import (
    BaseSchema,
    LanguageField,
    NormalizedStatementType,
    validate_bbox_order,
)

_AllowedRelationshipTypes = {"hasChild", "supports", "buildsTowards", "relatesTo"}
_AllowedEntityKeys = {"identifier", "case_identifier_uuid"}
_MetadataT = dict[str, Any]
_ProgressionSubtype = Literal["developmental_prerequisite", "recurring_practice"]
_SFIDedupReviewFocus = Literal["general", "same_normalized_source_text"]
_ValidationLevel = Literal["error", "info"]
SFIDedupDecision = Literal["conflict", "keep_separate", "merge", "needs_review"]
SFIMergeDecision = Literal["conflict", "merged", "needs_review", "singleton"]


def _strip_and_require_non_empty_str(v: str) -> str:
    """Strip whitespace and require non-empty string for required fields.

    Parameters
    ----------
    v
        The input string value to validate.

    Returns
    -------
    str
        The validated and stripped string value.

    Raises
    ------
    TypeError
        If the input is not a string.
    ValueError
        If the input value is None or an empty string after stripping.
    """

    if v is None:
        raise ValueError("Required field cannot be None")

    if not isinstance(v, str):
        raise TypeError("Expected a string")

    v2 = v.strip()

    if not v2:
        raise ValueError("Required string field cannot be empty")

    return v2


def unique_clean_strings(values: Sequence[str]) -> list[str]:
    """Clean and de-duplicate strings while preserving order.

    Parameters
    ----------
    values
        Raw string values.

    Returns
    -------
    list[str]
        Cleaned unique strings.
    """

    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values:
        value_clean = str(value).strip()

        if not value_clean or value_clean in seen:
            continue

        cleaned.append(value_clean)
        seen.add(value_clean)

    return cleaned


# Schemas for extraction windows.
class CodeMatch(BaseSchema):
    """A KG config code regex match found in an extraction window."""

    code_type: str = Field(
        description="KG config local code pattern key, such as 'content_standard'."
    )
    end_char: int = Field(
        description="End character offset of the match within window source_text.", ge=0
    )
    start_char: int = Field(
        description="Start character offset of the match within window source_text.",
        ge=0,
    )
    value: str = Field(description="Matched source-code surface form.")

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        """Validate that `end_char` is not before `start_char`.

        Returns
        -------
        Self
            The validated code match.

        Raises
        ------
        ValueError
            If the end offset is smaller than the start offset.
        """

        if self.end_char < self.start_char:
            raise ValueError("end_char must be >= start_char.")

        return self


class CodeParentHint(BaseSchema):
    """A deterministic parent-code suggestion derived from KG config rules."""

    child_code: str = Field(description="Matched child code.")
    child_code_type: str = Field(description="KG config local child code type.")
    method: str = Field(description="KG config rule method used to derive parent_code.")
    parent_code: str = Field(description="Derived parent code.")
    parent_code_type: str = Field(description="KG config local parent code type.")


class ExtractionWindow(BaseSchema):
    """LLM-ready prompt payload for one Academic Standards extraction window."""

    block: Optional[dict[str, Any]] = Field(
        default=None, description="Block-specific source payload for block windows."
    )
    code_matches: list[CodeMatch] = Field(
        default_factory=list,
        description="KG config code matches found in source_text.",
    )
    code_parent_hints: list[CodeParentHint] = Field(
        default_factory=list,
        description="KG config derived code parent hints for later extraction/validation.",
    )
    deterministic_hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-semantic deterministic hints for LLM extraction and merging.",
    )
    doc_key: str = Field(description="Source DocumentIR doc_key.")
    framework_title: str = Field(description="KG config framework title.")
    pdf_name: Optional[str] = Field(default=None, description="Source PDF filename.")
    primary_language: str = Field(description="KG config primary language.")
    kg_extraction_instructions: str = Field(
        description="KG config.academic_standards.sfi_extraction_instructions."
    )
    segment_kind: Literal["block", "table"] = Field(description="Source segment kind.")
    source_provenance: list[dict[str, Any]] = Field(
        default_factory=list, description="Segment/page provenance for the source."
    )
    source_segment_ids: list[str] = Field(
        description="DocumentIR segment_id values included in this window.",
        min_length=1,
    )
    source_text: str = Field(
        description="Human-readable source text assembled from the window payload."
    )
    subject: str = Field(description="KG config subject.")
    table: Optional[ExtractionWindowTablePayload] = Field(
        default=None, description="Table-specific source payload for table windows."
    )
    window_id: str = Field(description="Deterministic extraction-window identifier.")
    window_index: int = Field(
        description="0-based index in extraction-window order.", ge=0
    )
    window_notes: list[str] = Field(
        default_factory=list, description="Implementation/debug notes for this window."
    )

    @model_validator(mode="after")
    def validate_payload_matches_segment_kind(self) -> Self:
        """Validate that block/table payloads match segment_kind.

        Returns
        -------
        Self
            The validated extraction window.

        Raises
        ------
        ValueError
            If the payload does not match the declared segment kind.
        """

        if self.segment_kind == "block" and self.block is None:
            raise ValueError("Block extraction windows require block payload.")

        if self.segment_kind == "block" and self.table is not None:
            raise ValueError("Block extraction windows must not include table payload.")

        if self.segment_kind == "table" and self.table is None:
            raise ValueError("Table extraction windows require table payload.")

        if self.segment_kind == "table" and self.block is not None:
            raise ValueError("Table extraction windows must not include block payload.")

        return self


class ExtractionWindowPlanArtifact(BaseSchema):
    """Persisted artifact summarizing planned extraction-window source units."""

    counts_by_reason: dict[str, int] = Field(default_factory=dict)
    counts_by_segment_kind: dict[str, int] = Field(default_factory=dict)
    plan_items: list[ExtractionWindowPlanItem] = Field(default_factory=list)
    total_plan_items: int = Field(ge=0)


class ExtractionWindowPlanItem(BaseSchema):
    """A planned DocumentIR source unit for Academic Standards extraction."""

    block_type: Optional[str] = Field(
        default=None,
        description="Block type for planned block windows; null for table windows.",
    )
    columns_signature: Optional[str] = Field(
        default=None,
        description="Table columns_signature for planned table windows; null for blocks.",
    )
    local_code: Optional[str] = Field(
        default=None, description="DocumentIR local_code for the segment, if present."
    )
    plan_id: str = Field(description="Deterministic plan item identifier.")
    plan_index: int = Field(description="0-based index in source-window plan order.")
    plan_reasons: list[str] = Field(
        default_factory=list,
        description="Deterministic reasons this source unit is planned for extraction.",
    )
    row_count: Optional[int] = Field(
        default=None,
        description="Number of source table rows for table plans; null for blocks.",
        ge=0,
    )
    segment_id: str = Field(description="DocumentIR segment_id.")
    segment_kind: Literal["block", "table"] = Field(description="Planned segment kind.")
    source_page_indexes: list[int] = Field(
        default_factory=list,
        description="Sorted unique 0-based source page indexes for this segment.",
    )

    @field_validator("plan_reasons")
    @classmethod
    def validate_plan_reasons(cls, v: list[str]) -> list[str]:
        """Validate that each plan item records at least one reason.

        Parameters
        ----------
        v
            Plan reasons.

        Returns
        -------
        list[str]
            Cleaned unique plan reasons.

        Raises
        ------
        ValueError
            If no non-empty plan reasons remain.
        """

        cleaned = unique_clean_strings(v)

        if not cleaned:
            raise ValueError("ExtractionWindowPlanItem requires plan_reasons.")

        return cleaned


class ExtractionWindowTablePayload(BaseSchema):
    """Table-specific payload included in an extraction window."""

    body_row_end_index_exclusive: int = Field(
        description="Exclusive end index in the source table rows for body rows.", ge=0
    )
    body_row_start_index: int = Field(
        description="Inclusive start index in the source table rows for body rows.",
        ge=0,
    )
    columns_signature: Optional[str] = Field(
        default=None, description="DocumentIR columns_signature for this table."
    )
    grid_sources: Optional[list[list[dict[str, Any]]]] = Field(
        default=None,
        description="Optional grid source-debug view aligned to selected row_indexes.",
    )
    header_row_count: int = Field(description="Number of source header rows.", ge=0)
    header_rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw source table header rows."
    )
    header_rows_canonical: list[list[str]] = Field(
        default_factory=list, description="Canonical header text rows from DocumentIR."
    )
    local_code: Optional[str] = Field(
        default=None, description="Resolved table local_code, if present."
    )
    n_cols: int = Field(description="Maximum source table column count.", ge=1)
    row_indexes: list[int] = Field(
        default_factory=list,
        description="Source table row indexes included in rows/rows_grid/rows_filldown.",
    )
    row_provenance: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Optional row provenance aligned to selected row_indexes.",
    )
    rows: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw selected source rows/cells."
    )
    rows_filldown: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Optional filldown rows aligned to selected row_indexes.",
    )
    rows_grid: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Optional grid-normalized rows aligned to selected row_indexes.",
    )
    source_table_row_count: int = Field(
        description="Total number of rows in the source TableSegment.", ge=0
    )

    @model_validator(mode="after")
    def validate_row_ranges(self) -> Self:
        """Validate row-index and row-range consistency.

        Returns
        -------
        Self
            The validated table payload.

        Raises
        ------
        ValueError
            If the row range or aligned helper views are inconsistent.
        """

        if self.body_row_end_index_exclusive < self.body_row_start_index:
            raise ValueError(
                "body_row_end_index_exclusive must be >= body_row_start_index."
            )

        if self.body_row_end_index_exclusive > self.source_table_row_count:
            raise ValueError(
                "body_row_end_index_exclusive cannot exceed source_table_row_count."
            )

        if len(self.rows) != len(self.row_indexes):
            raise ValueError("rows must be aligned to row_indexes.")

        for helper_field_name in [
            "grid_sources",
            "row_provenance",
            "rows_filldown",
            "rows_grid",
        ]:
            helper_value = getattr(self, helper_field_name)

            if helper_value is not None and len(helper_value) != len(self.row_indexes):
                raise ValueError(
                    f"{helper_field_name} must be aligned to row_indexes when present."
                )

        return self


class StructuredContextItem(BaseSchema):
    """KG config derived context item attached to an extraction window."""

    label: str = Field(description="Display label derived from the matched heading.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    normalized_statement_type: str = Field(
        description="Expected normalized SFI type if this context becomes a grouping."
    )
    role: str = Field(description="Context role, such as grade_level or strand.")
    rule_name: str = Field(description="KG config context-heading rule that matched.")
    source_heading_item_index: int = Field(
        description="Source PageIR item index for the heading reference.", ge=0
    )
    source_heading_page_index: int = Field(
        description="Source page index for the heading reference.", ge=0
    )
    source_text: str = Field(description="Raw source heading text that matched.")
    statement_type: str = Field(
        description="Source-facing statement type if this context becomes a grouping."
    )


# Schemas for SFI candidate extraction.
class SFIAuxiliaryCandidate(BaseSchema):
    """Auxiliary source material that should not become an SFI candidate.

    Examples include exemplars, activities, resources, teacher guidance, core
    competencies, descriptors, and assessment notes when the KG config says they should
    not become standalone StandardsFrameworkItems.
    """

    auxiliary_id: str = Field(description="Window-local auxiliary identifier.")
    auxiliary_type: str = Field(description="Source-facing auxiliary material type.")
    language: LanguageField = Field(description="Language tag for source_text.")
    rationale: str = Field(
        description="Why this material is auxiliary rather than an SFI candidate.",
        min_length=1,
    )
    related_candidate_ids: list[str] = Field(
        default_factory=list,
        description="Window-local candidate IDs this auxiliary material supports or describes.",
    )
    source_text: str = Field(
        description="Verbatim source text for the auxiliary material."
    )

    @field_validator(
        "auxiliary_id",
        "auxiliary_type",
        "rationale",
        "source_text",
        mode="before",
    )
    @classmethod
    def strip_required_strings(cls, v: str) -> str:
        """Strip and require non-empty required string fields.

        Parameters
        ----------
        v
            Raw string value.

        Returns
        -------
        str
            Cleaned string.

        Raises
        ------
        ValueError
            If the value is empty.
        """

        v2 = v.strip()

        if not v2:
            raise ValueError("Required string field cannot be empty")

        return v2


class SFICandidate(BaseSchema):
    """Candidate StandardsFrameworkItem extracted from one source window."""

    candidate_id: str = Field(description="Window-local stable candidate identifier.")
    confidence: float = Field(
        default=0.5,
        description="Confidence that this source material should become an SFI candidate.",
        ge=0.0,
        le=1.0,
    )
    description: str = Field(
        description="Candidate SFI description, preserving the source-language meaning.",
        min_length=1,
    )
    language: LanguageField = Field(
        description="Language tag for description/source_text."
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description="Standard, Standard Grouping, or Other."
    )
    source_text: str = Field(
        description="Verbatim source text supporting this candidate.", min_length=1
    )
    statement_code: Optional[str] = Field(
        default=None,
        description="Official/source statement code, if visible. Optional for no-code curricula.",
    )
    statement_type: str = Field(
        description="Source-facing statement type, e.g. grade, strand, content_standard, indicator."
    )
    table_header_indexes: list[int] = Field(
        default_factory=list,
        description=(
            "Source table header row indexes used by this candidate, if table-header "
            "derived. Use this for grouping SFIs whose visible source text appears in "
            "table headers rather than body rows."
        ),
    )
    table_row_indexes: list[int] = Field(
        default_factory=list,
        description=(
            "Source table body row indexes used by this candidate, if table-row derived."
        ),
    )

    @field_validator(
        "candidate_id", "description", "source_text", "statement_type", mode="before"
    )
    @classmethod
    def strip_required_strings(cls, v: str) -> str:
        """Strip and require non-empty required string fields.

        Parameters
        ----------
        v
            Raw string value.

        Returns
        -------
        str
            Cleaned string.

        Raises
        ------
        ValueError
            If the value is empty.
        """

        v2 = v.strip()

        if not v2:
            raise ValueError("Required string field cannot be empty")

        return v2

    @field_validator("statement_code", mode="before")
    @classmethod
    def strip_statement_code(cls, v: Optional[str]) -> Optional[str]:
        """Normalize blank statement codes to ``None``.

        Parameters
        ----------
        v
            Optional statement code.

        Returns
        -------
        Optional[str]
            Stripped code, or ``None``.
        """

        if v is None:
            return None

        v2 = v.strip()
        return v2 if v2 else None

    @field_validator("table_header_indexes", "table_row_indexes")
    @classmethod
    def validate_table_indexes(cls, v: list[int]) -> list[int]:
        """Clean and validate source table row/header indexes.

        Parameters
        ----------
        v
            Raw row or header indexes.

        Returns
        -------
        list[int]
            Sorted unique non-negative indexes.

        Raises
        ------
        ValueError
            If any index is negative.
        """

        cleaned = sorted(set(int(index) for index in v or []))

        if any(index < 0 for index in cleaned):
            raise ValueError("table indexes must be non-negative")

        return cleaned


class SFIExtractionResult(BaseSchema):
    """Structured LLM output for one extraction window."""

    auxiliary_candidates: list[SFIAuxiliaryCandidate] = Field(
        default_factory=list,
        description="Auxiliary material found in the window but not emitted as SFIs.",
    )
    extraction_notes: list[str] = Field(
        default_factory=list,
        description="Brief source-grounded notes, including why no candidates were returned.",
    )
    sfi_candidates: list[SFICandidate] = Field(
        default_factory=list,
        description="Candidate StandardsFrameworkItems extracted from this window.",
    )
    window_id: str = Field(description="ExtractionWindow.window_id for traceability.")
    window_index: int = Field(description="ExtractionWindow.window_index.", ge=0)
    window_source_segment_ids: list[str] = Field(
        description="ExtractionWindow.source_segment_ids copied from the input window.",
        min_length=1,
    )

    @field_validator("extraction_notes")
    @classmethod
    def clean_extraction_notes(cls, v: list[str]) -> list[str]:
        """Strip blank extraction notes while preserving order.

        Parameters
        ----------
        v
            Raw notes.

        Returns
        -------
        list[str]
            Cleaned notes.
        """

        cleaned: list[str] = []
        seen: set[str] = set()

        for note in v or []:
            note_clean = str(note).strip()

            if not note_clean or note_clean in seen:
                continue

            cleaned.append(note_clean)
            seen.add(note_clean)

        return cleaned

    @field_validator("window_id", mode="before")
    @classmethod
    def strip_window_id(cls, v: str) -> str:
        """Strip and require a non-empty window ID.

        Parameters
        ----------
        v
            Raw window ID.

        Returns
        -------
        str
            Cleaned window ID.

        Raises
        ------
        ValueError
            If window ID is empty.
        """

        v2 = v.strip()

        if not v2:
            raise ValueError("window_id must be non-empty")

        return v2

    @model_validator(mode="after")
    def validate_local_ids_are_unique(self) -> Self:
        """Validate unique window-local candidate and auxiliary IDs.

        Returns
        -------
        Self
            The validated extraction result.

        Raises
        ------
        ValueError
            If duplicate candidate or auxiliary IDs are present.
        """

        candidate_ids = [candidate.candidate_id for candidate in self.sfi_candidates]
        auxiliary_ids = [
            candidate.auxiliary_id for candidate in self.auxiliary_candidates
        ]

        duplicate_candidate_ids = sorted(
            {
                candidate_id
                for candidate_id in candidate_ids
                if candidate_ids.count(candidate_id) > 1
            }
        )
        duplicate_auxiliary_ids = sorted(
            {
                auxiliary_id
                for auxiliary_id in auxiliary_ids
                if auxiliary_ids.count(auxiliary_id) > 1
            }
        )

        if duplicate_candidate_ids:
            raise ValueError(f"Duplicate candidate IDs: {duplicate_candidate_ids}")

        if duplicate_auxiliary_ids:
            raise ValueError(f"Duplicate auxiliary IDs: {duplicate_auxiliary_ids}")

        known_candidate_ids = set(candidate_ids)

        for auxiliary_candidate in self.auxiliary_candidates:
            unknown_related = sorted(
                set(auxiliary_candidate.related_candidate_ids) - known_candidate_ids
            )

            if unknown_related:
                raise ValueError(
                    f"Auxiliary candidate {auxiliary_candidate.auxiliary_id!r} references "
                    f"unknown candidate IDs: {unknown_related}"
                )

        return self


class SFIExtractionSummary(BaseSchema):
    """Summary artifact for an SFI extraction run."""

    auxiliary_candidate_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    candidate_count_by_normalized_statement_type: dict[str, int] = Field(
        default_factory=dict
    )
    candidate_count_by_statement_type: dict[str, int] = Field(default_factory=dict)
    window_count: int = Field(default=0, ge=0)
    windows_with_auxiliary_candidates: int = Field(default=0, ge=0)
    windows_with_sfi_candidates: int = Field(default=0, ge=0)
    windows_without_candidates: int = Field(default=0, ge=0)


# Schemas for SFI candidate registry.
class SFIRegistryArtifact(BaseSchema):
    """Persisted global SFI candidate registry artifact."""

    candidates: list[SFIRegistryCandidate] = Field(default_factory=list)
    country: str = Field(description="KG config metadata country.")
    doc_key: Optional[str] = Field(
        default=None, description="Source DocumentIR doc_key."
    )
    duplicate_buckets: list[SFIRegistryDuplicateBucket] = Field(default_factory=list)
    framework_title: str = Field(description="KG config metadata framework title.")
    pdf_name: Optional[str] = Field(default=None, description="Source PDF filename.")
    primary_language: LanguageField = Field(
        description="KG config metadata primary language."
    )
    subject: str = Field(description="KG config metadata subject.")
    summary: SFIRegistrySummary = Field(description="Registry aggregate summary.")
    warnings: list[SFIRegistryWarning] = Field(default_factory=list)


class SFIRegistryCandidate(BaseSchema):
    """Document-level wrapper around one window-local SFI candidate.

    The registry candidate is a temporary review handle for merge/dedup stages. It is
    not a final StandardsFrameworkItem and must not be used as a final KG ID.
    """

    candidate_payload: SFICandidate = Field(
        description="Original window-local SFI candidate payload."
    )
    code_bucket_key: Optional[str] = Field(
        default=None,
        description="statement_type + normalized_statement_code bucket key, when coded.",
    )
    confidence: float = Field(
        description="Original candidate confidence.", ge=0.0, le=1.0
    )
    description: str = Field(
        description="Original candidate description.", min_length=1
    )
    language: LanguageField = Field(description="Original candidate language tag.")
    normalized_description: str = Field(
        description="Lightweight normalized candidate description."
    )
    normalized_source_text: str = Field(
        description="Lightweight normalized candidate source_text."
    )
    normalized_statement_code: Optional[str] = Field(
        default=None, description="Lightweight normalized statement_code, when present."
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description="Original candidate normalized statement type."
    )
    registry_candidate_id: str = Field(
        description="Temporary document-level candidate handle for review and merge."
    )
    source_context_key: str = Field(
        description=(
            "Deterministic source-derived context key used to scope no-code "
            "duplicate bucketing and later review."
        ),
        min_length=1,
    )
    source_context_labels: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable source-derived context labels, such as section path, "
            "source segment, table header, or table row context."
        ),
    )
    source_segment_ids: list[str] = Field(
        description="ExtractionWindow.source_segment_ids for source recovery.",
        min_length=1,
    )
    source_text: str = Field(
        description="Original candidate source_text.", min_length=1
    )
    source_text_bucket_key: str = Field(
        description="statement_type + normalized source_text bucket key."
    )
    source_window_candidate_id: str = Field(
        description="Original window-local candidate_id."
    )
    source_window_candidate_index: int = Field(
        description="0-based candidate position within the extraction result.", ge=0
    )
    statement_code: Optional[str] = Field(
        default=None, description="Original candidate statement_code, when present."
    )
    statement_type: str = Field(description="Original candidate statement_type.")
    table_header_indexes: list[int] = Field(
        default_factory=list, description="Original candidate table_header_indexes."
    )
    table_row_indexes: list[int] = Field(
        default_factory=list, description="Original candidate table_row_indexes."
    )
    text_bucket_key: str = Field(
        description="statement_type + normalized description bucket key."
    )
    window_id: str = Field(description="ExtractionWindow.window_id.")
    window_index: int = Field(description="ExtractionWindow.window_index.", ge=0)


class SFIRegistryDuplicateBucket(BaseSchema):
    """Possible SFI duplicate bucket for LLM-assisted merge review."""

    bucket_id: str = Field(description="Deterministic duplicate bucket ID.")
    bucket_key: str = Field(description="Normalized bucket key.")
    bucket_type: Literal["code", "description_text", "source_text"] = Field(
        description="Duplicate signal type used to form the bucket."
    )
    candidate_count: int = Field(
        description="Number of candidates in the bucket.", ge=2
    )
    description_examples: list[str] = Field(
        default_factory=list, description="Up to five source candidate descriptions."
    )
    evidence_strength: Literal["strong_signal", "medium_signal", "weak_signal"] = Field(
        description="LLM-facing duplicate-signal strength hint."
    )
    merge_policy_hint: Literal["review_required"] = Field(
        default="review_required",
        description="Reminder that this bucket is not an automatic merge decision.",
    )
    registry_candidate_ids: list[str] = Field(
        description="Registry candidates included in this possible duplicate bucket.",
        min_length=2,
    )
    statement_types: list[str] = Field(
        default_factory=list, description="Statement types present in the bucket."
    )
    window_indexes: list[int] = Field(
        default_factory=list,
        description="Extraction window indexes present in the bucket.",
    )


class SFIRegistrySummary(BaseSchema):
    """Aggregate summary for SFI candidate registry."""

    auxiliary_candidate_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    candidate_count_by_language: dict[str, int] = Field(default_factory=dict)
    candidate_count_by_normalized_statement_type: dict[str, int] = Field(
        default_factory=dict
    )
    candidate_count_by_statement_type: dict[str, int] = Field(default_factory=dict)
    candidates_with_statement_code: int = Field(default=0, ge=0)
    candidates_without_statement_code: int = Field(default=0, ge=0)
    extraction_window_count: int = Field(default=0, ge=0)
    largest_duplicate_buckets: list[dict[str, Any]] = Field(default_factory=list)
    possible_duplicate_bucket_count: int = Field(default=0, ge=0)
    warning_count: int = Field(default=0, ge=0)
    warning_count_by_type: dict[str, int] = Field(default_factory=dict)


class SFIRegistryWarning(BaseSchema):
    """Registry warning for SFI candidate review."""

    bucket_id: Optional[str] = Field(
        default=None, description="Associated duplicate bucket ID, when applicable."
    )
    message: str = Field(description="Human-readable warning message.", min_length=1)
    registry_candidate_ids: list[str] = Field(
        default_factory=list, description="Candidate IDs related to this warning."
    )
    severity: Literal["info", "warning"] = Field(description="Warning severity.")
    warning_id: str = Field(description="Stable local warning ID.")
    warning_type: str = Field(description="Machine-readable warning type.")


# Schemas for SFI merge/dedup.
class SFIDedupDecisionGroup(BaseSchema):
    """One LLM decision group for a bounded SFI dedup review set."""

    candidate_ids: list[str] = Field(
        description="Registry candidate IDs assigned to this decision group.",
        min_length=1,
    )
    confidence: float = Field(
        default=0.5, description="LLM confidence in the decision.", ge=0.0, le=1.0
    )
    decision: SFIDedupDecision = Field(description="Closed dedup decision label.")
    reason: str = Field(
        description="Short source-grounded decision reason.", min_length=1
    )

    @field_validator("candidate_ids")
    @classmethod
    def clean_candidate_ids(cls, v: list[str]) -> list[str]:
        """Clean and validate decision candidate IDs.

        Parameters
        ----------
        v
            Raw candidate IDs.

        Returns
        -------
        list[str]
            Cleaned candidate IDs.

        Raises
        ------
        ValueError
            If candidate IDs are blank or duplicated.
        """

        cleaned = unique_clean_strings(v)

        if len(cleaned) != len(v):
            raise ValueError("candidate_ids must be unique and non-empty")

        return cleaned


class SFIDedupReviewCandidate(BaseSchema):
    """Compact registry-candidate view for one bounded dedup review set."""

    code_bucket_key: Optional[str] = Field(
        default=None, description="Candidate code duplicate bucket key, when present."
    )
    description: str = Field(description="Candidate description.", min_length=1)
    language: LanguageField = Field(description="Candidate language tag.")
    normalized_description: str = Field(
        description="Registry-normalized candidate description."
    )
    normalized_source_text: str = Field(
        description="Registry-normalized candidate source_text."
    )
    normalized_statement_code: Optional[str] = Field(
        default=None, description="Registry-normalized statement code, when present."
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description="Candidate normalized statement type."
    )
    registry_candidate_id: str = Field(
        description="Temporary registry candidate ID from Step 6."
    )
    source_context_key: str = Field(
        description="Deterministic source-derived context key from the registry.",
        min_length=1,
    )
    source_context_labels: list[str] = Field(
        default_factory=list,
        description="Human-readable source-derived context labels from the registry.",
    )
    source_segment_ids: list[str] = Field(
        description="Source segment IDs associated with the candidate.", min_length=1
    )
    source_text: str = Field(description="Source-visible evidence text.", min_length=1)
    source_text_bucket_key: str = Field(
        description="Candidate source-text duplicate bucket key."
    )
    statement_code: Optional[str] = Field(
        default=None, description="Original statement code, when present."
    )
    statement_type: str = Field(description="Canonical source-facing statement type.")
    table_header_indexes: list[int] = Field(
        default_factory=list, description="Source table header indexes."
    )
    table_row_indexes: list[int] = Field(
        default_factory=list, description="Source table row indexes."
    )
    text_bucket_key: str = Field(
        description="Candidate description duplicate bucket key."
    )
    window_id: str = Field(description="Source extraction window ID.")
    window_index: int = Field(description="Source extraction window index.", ge=0)


class SFIDedupReviewRequest(BaseSchema):
    """Persisted prompt payload for one bounded SFI dedup review set."""

    bilingual_pair_policy: Optional[str] = Field(
        default=None, description="Optional bilingual pairing policy from KG config."
    )
    candidates: list[SFIDedupReviewCandidate] = Field(
        description="Bounded candidate records to review together.", min_length=2
    )
    review_focus: _SFIDedupReviewFocus = Field(
        description=(
            "Prompt focus for this review set. Use 'same_normalized_source_text' "
            "when the set was selected because candidates share exact registry-"
            "normalized source text; otherwise use 'general'."
        )
    )
    review_reasons: list[str] = Field(
        description="Deterministic reasons this candidate set was selected for review.",
        min_length=1,
    )
    review_set_id: str = Field(description="Deterministic review-set ID.")
    sfi_deduplication_instructions: str = Field(
        description="Curriculum-specific Step 7 deduplication instructions.",
        min_length=1,
    )

    @field_validator("review_reasons")
    @classmethod
    def clean_review_reasons(cls, v: list[str]) -> list[str]:
        """Clean and deduplicate review reasons.

        Parameters
        ----------
        v
            Raw review reasons.

        Returns
        -------
        list[str]
            Cleaned review reasons.
        """

        return unique_clean_strings(v)

    @model_validator(mode="after")
    def validate_candidate_ids_unique(self) -> Self:
        """Validate that request candidate IDs are unique.

        Returns
        -------
        Self
            The validated request.

        Raises
        ------
        ValueError
            If duplicate candidate IDs are present.
        """

        candidate_ids = [
            candidate.registry_candidate_id for candidate in self.candidates
        ]
        duplicate_candidate_ids = sorted(
            {
                candidate_id
                for candidate_id in candidate_ids
                if candidate_ids.count(candidate_id) > 1
            }
        )

        if duplicate_candidate_ids:
            raise ValueError(
                f"Duplicate review candidate IDs: {duplicate_candidate_ids}"
            )

        return self


class SFIDedupReviewResponse(BaseSchema):
    """Structured LLM output for one bounded SFI dedup review set."""

    decision_groups: list[SFIDedupDecisionGroup] = Field(
        description="Decision groups covering every review candidate exactly once.",
        min_length=1,
    )
    review_set_id: str = Field(description="Review-set ID copied from the request.")


class SFIMergeGroup(BaseSchema):
    """One merge-group record carried forward to final SFI construction."""

    audit_flags: list[str] = Field(
        default_factory=list,
        description="Machine-readable audit flags for downstream review.",
    )
    audit_notes: list[str] = Field(
        default_factory=list,
        description="Human-readable audit notes for downstream review.",
    )
    audit_peer_merge_group_ids: list[str] = Field(
        default_factory=list,
        description="Related merge-group IDs relevant to audit flags.",
    )
    candidate_descriptions: list[str] = Field(
        default_factory=list, description="Unique candidate descriptions for audit."
    )
    candidate_source_refs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-candidate source references preserved for later stages.",
    )
    candidate_source_texts: list[str] = Field(
        default_factory=list, description="Unique source-visible evidence snippets."
    )
    confidence_max: float = Field(
        description="Maximum candidate confidence in this group.", ge=0.0, le=1.0
    )
    confidence_min: float = Field(
        description="Minimum candidate confidence in this group.", ge=0.0, le=1.0
    )
    llm_decision: Optional[SFIDedupDecision] = Field(
        default=None, description="Original LLM decision, when reviewed by the LLM."
    )
    llm_review_set_id: Optional[str] = Field(
        default=None, description="Review-set ID that produced this group, when any."
    )
    merge_decision: SFIMergeDecision = Field(description="Step 7 merge outcome.")
    merge_group_id: str = Field(description="Temporary deterministic merge-group ID.")
    merge_reason: str = Field(description="Short merge or review reason.", min_length=1)
    normalized_statement_code: Optional[str] = Field(
        default=None, description="Shared normalized statement code, when unique."
    )
    normalized_statement_codes: list[str] = Field(
        default_factory=list, description="All normalized statement codes in the group."
    )
    normalized_statement_type: Optional[NormalizedStatementType] = Field(
        default=None, description="Shared normalized statement type, when unique."
    )
    normalized_statement_types: list[str] = Field(
        default_factory=list, description="All normalized statement types in the group."
    )
    registry_candidate_ids: list[str] = Field(
        description="Registry candidates included in this merge group.", min_length=1
    )
    source_segment_ids: list[str] = Field(
        default_factory=list, description="Merged source segment IDs."
    )
    source_window_ids: list[str] = Field(
        default_factory=list, description="Merged source extraction-window IDs."
    )
    source_window_indexes: list[int] = Field(
        default_factory=list, description="Merged extraction-window indexes."
    )
    statement_code: Optional[str] = Field(
        default=None, description="Shared original statement code, when unique."
    )
    statement_codes: list[str] = Field(
        default_factory=list, description="All original statement codes in the group."
    )
    statement_type: Optional[str] = Field(
        default=None, description="Shared statement type, when unique."
    )
    statement_types: list[str] = Field(
        default_factory=list, description="All statement types in the group."
    )

    @field_validator(
        "audit_flags",
        "audit_notes",
        "audit_peer_merge_group_ids",
        "candidate_descriptions",
        "candidate_source_texts",
        "normalized_statement_codes",
        "normalized_statement_types",
        "registry_candidate_ids",
        "source_segment_ids",
        "source_window_ids",
        "statement_codes",
        "statement_types",
    )
    @classmethod
    def clean_string_lists(cls, v: list[str]) -> list[str]:
        """Clean string-list fields while preserving order.

        Parameters
        ----------
        v
            Raw string values.

        Returns
        -------
        list[str]
            Cleaned string values.
        """

        return unique_clean_strings(v)

    @field_validator("source_window_indexes")
    @classmethod
    def clean_source_window_indexes(cls, v: list[int]) -> list[int]:
        """Clean source window indexes.

        Parameters
        ----------
        v
            Raw source window indexes.

        Returns
        -------
        list[int]
            Sorted unique source window indexes.
        """

        return sorted(set(int(index) for index in v or []))


class SFIMergeReport(BaseSchema):
    """Persisted merge/dedup report artifact."""

    conflict_groups: list[SFIMergeGroup] = Field(default_factory=list)
    merge_groups: list[SFIMergeGroup] = Field(default_factory=list)
    needs_review_groups: list[SFIMergeGroup] = Field(default_factory=list)
    review_requests: list[SFIDedupReviewRequest] = Field(default_factory=list)
    review_responses: list[SFIDedupReviewResponse] = Field(default_factory=list)
    summary: SFIMergeSummary = Field(description="Step 7 merge/dedup summary.")


class SFIMergeSummary(BaseSchema):
    """Aggregate summary for merge groups."""

    audit_flag_count_by_type: dict[str, int] = Field(default_factory=dict)
    candidate_count: int = Field(default=0, ge=0)
    conflict_group_count: int = Field(default=0, ge=0)
    dedup_review_request_count: int = Field(default=0, ge=0)
    dedup_review_response_count: int = Field(default=0, ge=0)
    merge_group_audit_flag_count: int = Field(default=0, ge=0)
    merge_group_count: int = Field(default=0, ge=0)
    merged_group_count: int = Field(default=0, ge=0)
    needs_review_group_count: int = Field(default=0, ge=0)
    reviewed_candidate_count: int = Field(default=0, ge=0)
    singleton_group_count: int = Field(default=0, ge=0)
    unreviewed_singleton_count: int = Field(default=0, ge=0)


# CURRENTLY UNUSED #
def _validate_iso8601_str(v: Optional[str]) -> Optional[str]:
    """Validate ISO-8601 parseability for timestamps if provided.

    Parameters
    ----------
    v
        The date string to validate.

    Returns
    -------
    Optional[str]
        The validated date string or None.

    Raises
    ------
    TypeError
        If the input is not a string or None.
    ValueError
        If the input string is not a valid ISO-8601 datetime.
    """

    if v is None:
        return None

    if not isinstance(v, str):
        raise TypeError("dateCreated/dateModified must be ISO-8601 strings or None")

    v2 = v.strip()

    if not v2:
        return None

    # Accept common ISO-8601 forms; supports "Z" suffix via replace.
    try:
        datetime.fromisoformat(v2.replace("Z", "+00:00"))
    except Exception as e:
        raise ValueError(f"Invalid ISO-8601 datetime string: {v2}") from e

    return v2


class _HasDateFields:
    """Structural type stub for models with date_created/date_modified fields."""

    date_created: Optional[str]
    date_modified: Optional[str]


class _HasCaseIdentifierFields:
    """Structural type stub for models with case_identifier_uri/uuid fields."""

    case_identifier_uri: str
    case_identifier_uuid: UUID


class _CaseIdentifierMixin:
    """Mixin providing CASE-style URI/UUID validation.

    Consuming models must declare `case_identifier_uri: str` and
    `case_identifier_uuid: UUID`.
    """

    @field_validator("case_identifier_uri")
    @classmethod
    def _validate_case_identifier_uri_is_uri_like(cls, v: str) -> str:
        """Validate case_identifier_uri looks like a URI/URN (supports http(s), urn,
        etc.).

        Parameters
        ----------
        v
            The case_identifier_uri string to validate.

        Returns
        -------
        str
            The validated case_identifier_uri string.

        Raises
        ------
        ValueError
            If the case_identifier_uri does not include a URI scheme.
        """

        parsed = urlparse(v)

        if not parsed.scheme:
            raise ValueError(
                "case_identifier_uri must include a URI scheme (e.g., urn:, http:, https:)"
            )

        return v

    @model_validator(mode="after")
    def _check_case_uri_contains_uuid(
        self: _HasCaseIdentifierFields,
    ) -> _HasCaseIdentifierFields:
        """Validate that case_identifier_uri includes case_identifier_uuid (deterministic
        traceability).

        Returns
        -------
        Self
            The validated model instance.

        Raises
        ------
        ValueError
            If case_identifier_uri does not include case_identifier_uuid.
        """

        if str(self.case_identifier_uuid) not in self.case_identifier_uri:
            raise ValueError("case_identifier_uri must include case_identifier_uuid")

        return self


class _DateValidationMixin:
    """Mixin providing ISO-8601 date validation and modified >= created check.

    Consuming models must declare `date_created: Optional[str]` and
    `date_modified: Optional[str]`.
    """

    @field_validator("date_created", "date_modified")
    @classmethod
    def _validate_iso8601_dates(cls, v: Optional[str]) -> Optional[str]:
        """Validate that date_created and date_modified, if provided, are valid
        ISO-8601 strings.

        Parameters
        ----------
        v
            The date string to validate.

        Returns
        -------
        Optional[str]
            The validated date string or None.
        """

        return _validate_iso8601_str(v)

    @model_validator(mode="after")
    def _check_modified_not_before_created(self: _HasDateFields) -> _HasDateFields:
        """If both dates exist, ensure dateModified >= dateCreated.

        Returns
        -------
        Self
            The validated model instance.

        Raises
        ------
        ValueError
            If dateModified is before dateCreated.
        """

        if self.date_created and self.date_modified:
            created = datetime.fromisoformat(self.date_created.replace("Z", "+00:00"))
            modified = datetime.fromisoformat(self.date_modified.replace("Z", "+00:00"))

            if modified < created:
                raise ValueError("dateModified must be >= dateCreated")

        return self


# Schemas for LLM responses.
class AtomicSkill(BaseSchema):
    """An atomic skill extracted from a single expectation statement.

    NB:

    1. `description` is the atomic skill statement (display-language policy).
    2. `rationale` is optional guidance explaining the decomposition decision.
    """

    description: str = Field(
        description="Atomic skill statement (not an activity/resource).", min_length=1
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Optional brief rationale explaining the decomposition.",
    )


class SFIAtomicSkills(BaseSchema):
    """Atomic skills for a single StandardsFrameworkItem (expectation)."""

    sfi_uuid: UUID = Field(
        description="CASE UUID of the supporting StandardsFrameworkItem."
    )
    skills: list[AtomicSkill] = Field(default_factory=list)


class AtomicSkillsResponse(BaseSchema):
    """Top-level structured response for atomic skills inference."""

    items: list[SFIAtomicSkills] = Field(default_factory=list)


class ProgressionEdge(BaseSchema):
    """A single suggested edge between two StandardsFrameworkItems."""

    confidence: float = Field(
        description="0..1 calibrated confidence (higher = more certain).",
        ge=0.0,
        le=1.0,
    )
    progression_subtype: Optional[_ProgressionSubtype] = Field(
        default=None,
        description=(
            "For Phase 1 within-level buildsTowards only: "
            "'developmental_prerequisite' means the source is a meaningful prerequisite "
            "for a more complex or dependent target; 'recurring_practice' means the "
            "target is a later curriculum occurrence continuing practice of the same "
            "or substantially similar skill."
        ),
    )
    rationale: str = Field(
        description="Brief rationale for the edge (>= 50 chars).",
        min_length=50,
    )
    source_sfi_uuid: str = Field(description="UUID string of the source SFI.")
    target_sfi_uuid: str = Field(description="UUID string of the target SFI.")

    @field_validator("rationale", mode="before")
    @classmethod
    def _strip_rationale(cls, v: Any) -> str:
        """Strip whitespace and validate that rationale is a string of at least 50
        characters.

        Parameters
        ----------
        v
            The input value to validate.

        Returns
        -------
        str
            The validated and stripped rationale string.

        Raises
        ------
        ValueError
            If the rationale is not a string or is less than 50 characters after
            stripping.
        """

        s = str(v or "").strip()

        if len(s) < 50:
            raise ValueError("rationale must be >= 50 characters")

        return s

    @field_validator("source_sfi_uuid", "target_sfi_uuid", mode="before")
    @classmethod
    def _validate_uuid_str(cls, v: Any) -> str:
        """Strip whitespace and validate that the value is a parseable UUID string.

        Parameters
        ----------
        v
            The input value to validate.

        Returns
        -------
        str
            The validated and stripped UUID string.

        Raises
        ------
        ValueError
            If the input value is null, empty, or not a valid UUID string.
        """

        if v is None:
            raise ValueError("UUID cannot be null")

        s = str(v).strip()

        if not s:
            raise ValueError("UUID cannot be empty")

        try:
            UUID(s)
        except Exception as e:  # pylint: disable=broad-except
            raise ValueError(f"Invalid UUID string: {s}") from e

        return s


class ProgressionEdgesResponse(BaseSchema):
    """Top-level structured response: a list of edges (may be empty)."""

    edges: list[ProgressionEdge] = Field(default_factory=list)


# Schemas for nodes.
class StandardsFramework(_CaseIdentifierMixin, _DateValidationMixin, BaseSchema):
    """Root node for a standards framework (typically one per PDF).

    This represents the top-level standards document/container in the LC KG. All
    StandardsFrameworkItems (SFIs) should be reachable from this framework via
    `hasChild` relationships.
    """

    academic_subject: str = Field(
        description=(
            "High-level academic subject classification for the framework "
            "(e.g., Mathematics, English Language Arts, Science). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    adoption_status: str = Field(
        description=(
            "Adoption status of the framework (e.g., Draft, Adopted). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    attribution_statement: str = Field(
        description=(
            "Attribution text required to credit the original publisher/owner "
            "of the standards framework (e.g., Ministry of Education, year, source)."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of the framework "
            "(e.g., 'Ministry of Education (Zambia)')."
        ),
    )
    case_identifier_uri: str = Field(
        description=(
            "Stable URI identifier for the framework object. LC KG aligns with "
            "CASE-style identifiers; for non-CASE sources this may be a synthetic "
            "deterministic URI/URN minted by the pipeline (e.g., urn:uuid:<uuid>)."
        ),
    )
    case_identifier_uuid: UUID = Field(
        description=(
            "Stable UUID identifier for the framework object. In LC KG/CASE contexts, "
            "this is used as a stable cross-system identifier. For non-CASE sources, "
            "this may be a synthetic deterministic UUIDv5 minted by the pipeline."
        ),
    )
    date_created: Optional[str] = Field(
        default=None,
        description=(
            "Creation timestamp for the framework (ISO-8601 string), if known. "
            "Optional; often unavailable for PDFs."
        ),
    )
    date_modified: Optional[str] = Field(
        default=None,
        description=(
            "Last-modified timestamp for the framework (ISO-8601 string), if known. "
            "Optional; often unavailable for PDFs."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable description of the framework. Optional; may be generated "
            "from document metadata or left empty."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this entity in the export. Must be "
            "deterministic across reruns (UUIDv5 recommended)."
        ),
    )
    in_language: LanguageField = Field(
        description=(
            "Language tag for the framework (e.g., en-US). In `lc_public_strict`, "
            "this should conform to LC enum values; in `global_relaxed`, any valid "
            "BCP-47 language tag is allowed."
        ),
    )
    jurisdiction: str = Field(
        description=(
            "Jurisdiction that issued the framework (e.g., Zambia, Uganda). "
            "In `lc_public_strict`, this may require an LC-safe fallback "
            "(with the true value stored in provenance)."
        ),
    )
    license: str = Field(
        description=(
            "License string for the framework content. This may be an SPDX-like label "
            "or a publisher-defined license statement; must be present even if it is "
            "a conservative placeholder."
        ),
    )
    metadata: _MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., doc_key, source PDF name, "
            "dialect fallback details). This should not be relied on as LC KG canonical fields."
        ),
    )
    name: str = Field(
        description=(
            "Human-readable name/title of the framework, typically derived from the PDF title "
            "or cover page (e.g., 'Lower Primary Education Syllabi Grade 1–3 (2024)')."
        ),
    )
    notes: Optional[str] = Field(
        default=None,
        description=(
            "Optional notes field for additional human-readable context. "
            "This is not always populated; use for brief clarifications."
        ),
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )

    @field_validator(
        "academic_subject",
        "adoption_status",
        "attribution_statement",
        "author",
        "case_identifier_uri",
        "in_language",
        "jurisdiction",
        "license",
        "name",
        "provider",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.
        """

        return _strip_and_require_non_empty_str(v)


class StandardsFrameworkItem(_CaseIdentifierMixin, _DateValidationMixin, BaseSchema):
    """Standards item or grouping within a standards framework.

    This is the primary node type in the academic standards hierarchy. Both
    organizational groupings (e.g., Grade, Subject, Topic) and normative learning
    expectations (e.g., outcomes/competences/objectives) are represented using this
    entity type. Hierarchy is represented via `hasChild` edges.
    """

    academic_subject: str = Field(
        description=(
            "High-level academic subject classification for the item. "
            "In strict exports this should conform to LC enums; "
            "in relaxed exports this may be a free-form subject label."
        ),
    )
    attribution_statement: str = Field(
        description=(
            "Attribution text required to credit the original publisher/owner "
            "of the standards content that this item derives from."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of this standards item, "
            "typically inherited from the framework (e.g., Ministry of Education)."
        ),
    )

    # LC KG conventions (relationships commonly key off CASE ids).
    case_identifier_uri: str = Field(
        description=(
            "Stable URI identifier for this standards item. LC KG commonly aligns with "
            "CASE-style URIs. For non-CASE sources, this may be a synthetic deterministic "
            "URI/URN minted by the pipeline (e.g., urn:uuid:<uuid>)."
        ),
    )

    case_identifier_uuid: UUID = Field(
        description=(
            "Stable UUID identifier for this standards item. Used by LC KG exports as a "
            "canonical cross-object key for relationships (hasChild/buildsTowards/relatesTo). "
            "For non-CASE sources, this should be deterministic (UUIDv5 recommended)."
        ),
    )
    date_created: Optional[str] = Field(
        default=None,
        description=(
            "Creation timestamp for this item (ISO-8601 string), if known. Optional."
        ),
    )
    date_modified: Optional[str] = Field(
        default=None,
        description=(
            "Last-modified timestamp for this item (ISO-8601 string), if known. Optional."
        ),
    )

    # NB: LC KG says description is optional, but *we* keep it required so that we
    # never export a blank item.
    description: str = Field(
        description=(
            "Primary human-readable text of the standards item. "
            "For grouping items, this is typically the label/title (e.g., 'Grade 2'). "
            "For normative items, this is the learning expectation statement."
        ),
    )

    # LC KG: gradeLevel is 0...n.
    grade_level: list[str] = Field(
        default_factory=list,
        description=(
            "Zero or more grade-level tags associated with this item (e.g., ['Grade 2']). "
            "May be empty for non-grade-banded or stage-banded frameworks."
        ),
    )

    metadata: _MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., canonical node id, "
            "source PDF provenance pointers, dialect fallbacks). Not a core LC KG field."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this entity in the export. Must be deterministic "
            "across reruns (UUIDv5 recommended)."
        ),
    )
    in_language: LanguageField = Field(
        description=(
            "Language tag for the item text (e.g., en-US). "
            "In strict exports this should conform to LC enums; "
            "in relaxed exports any valid BCP-47 language tag is allowed."
        ),
    )
    jurisdiction: str = Field(
        description=(
            "Jurisdiction that issued the standards (e.g., Zambia, Uganda). "
            "In strict exports this may require a fallback value; store original in provenance."
        ),
    )
    license: str = Field(
        description=(
            "License string for the standards content. Must be present even if it is "
            "a conservative placeholder when the original license is unknown."
        ),
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description=(
            "Normalized LC statement classification. Typical values include: "
            "'Standard' for normative expectations, 'Standard Grouping' for organizational "
            "nodes, and 'Other' for descriptors/indicators/guidance depending on policy."
        ),
    )
    notes: Optional[str] = Field(
        default=None, description="Optional human-readable notes/context."
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )
    statement_code: Optional[str] = Field(
        default=None,
        description=(
            "Stable code/notation for this item from the source framework, if available "
            "(e.g., '2.1.5.1'). This is a key traceability aid and may support progression inference."
        ),
    )
    statement_type: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable source label for the item (e.g., 'Subject', 'Topic', "
            "'Specific competence', 'Expected Standard', 'Indicator'). "
            "This is not the normalized LC type; it preserves source semantics."
        ),
    )

    @field_validator(
        "academic_subject",
        "attribution_statement",
        "author",
        "case_identifier_uri",
        "description",
        "in_language",
        "jurisdiction",
        "license",
        "provider",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.
        """

        return _strip_and_require_non_empty_str(v)

    @field_validator("statement_code", "statement_type", mode="before")
    @classmethod
    def _strip_optional_strings(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace for optional string fields; treat empty as None.

        Parameters
        ----------
        v
            The input optional string value to validate.

        Returns
        -------
        Optional[str]
            The validated and stripped string value, or None.

        Raises
        ------
        TypeError
            If the input is not a string or None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("Expected a string or None")

        v2 = v.strip()

        return v2 if v2 else None

    @field_validator("grade_level")
    @classmethod
    def _validate_grade_level(cls, v: list[str]) -> list[str]:
        """Ensure gradeLevel entries are non-empty strings, de-duplicated, and
        stable-ordered.

        Parameters
        ----------
        v
            The list of grade level strings to validate.

        Returns
        -------
        list[str]
            The validated list of grade level strings.

        Raises
        ------
        TypeError
            If the input is not a list of strings or contains non-string items.
        """

        if v is None:
            return []

        if not isinstance(v, list):
            raise TypeError("grade_level must be a list of strings")

        cleaned: list[str] = []
        seen: set[str] = set()

        for item in v:
            if not isinstance(item, str):
                raise TypeError("grade_level must contain only strings")

            s = item.strip()

            if not s:
                continue

            if s not in seen:
                cleaned.append(s)
                seen.add(s)

        return cleaned

    @model_validator(mode="after")
    def _check_statement_code_not_empty_if_present(self) -> StandardsFrameworkItem:
        """If statementCode is present, it must be a non-empty trimmed string.

        Returns
        -------
        StandardsFrameworkItem
            The validated StandardsFrameworkItem object.

        Raises
        ------
        ValueError
            If statementCode is an empty string.
        """

        if self.statement_code is not None and not self.statement_code.strip():
            raise ValueError("statementCode must be non-empty when provided")

        return self


class LearningComponent(_DateValidationMixin, BaseSchema):
    """Granular skill/concept aligned to one or more standards items via `supports`.

    LearningComponents represent skill/concept units that can be aligned to
    StandardsFrameworkItems using `supports` relationships:

      (:LearningComponent)-[:supports]->(:StandardsFrameworkItem)
    """

    academic_subject: str = Field(
        description=(
            "High-level academic subject classification for the component "
            "(e.g., Mathematics, English Language Arts). In strict exports this should "
            "conform to LC enum values; in relaxed exports free-form values are allowed."
        ),
    )
    attribution_statement: str = Field(
        description=(
            "Attribution text required to credit the original publisher/owner of the "
            "source curriculum content that this component derives from."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of this component, "
            "typically inherited from the framework (e.g., Ministry of Education)."
        ),
    )
    date_created: Optional[str] = Field(
        default=None,
        description=(
            "Creation timestamp for the component (ISO-8601 string), if known. Optional."
        ),
    )
    date_modified: Optional[str] = Field(
        default=None,
        description=(
            "Last-modified timestamp for the component (ISO-8601 string), if known. Optional."
        ),
    )
    description: str = Field(
        description=(
            "Primary human-readable text describing the skill/concept represented by the "
            "LearningComponent. In a 1-to-1 policy, this may be identical to the supporting "
            "standards expectation statement."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this entity in the export. Must be deterministic "
            "across reruns (UUIDv5 recommended)."
        ),
    )
    in_language: LanguageField = Field(
        description=(
            "Language tag for the component text (e.g., en-US). In strict exports this should "
            "conform to LC enum values; in relaxed exports any valid BCP-47 language tag is allowed."
        ),
    )
    license: str = Field(
        description=(
            "License string for the component content. Must be present even if it is a "
            "conservative placeholder when the original license is unknown."
        ),
    )
    metadata: _MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., canonical node ids, "
            "doc_key references, provenance pointers, dialect fallback notes). "
            "Not a core LC KG field; consider omitting from strict exports."
        ),
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )

    @field_validator(
        "academic_subject",
        "attribution_statement",
        "author",
        "description",
        "in_language",
        "license",
        "provider",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.
        """

        return _strip_and_require_non_empty_str(v)


# Schemas for relationship.
class Relationship(_DateValidationMixin, BaseSchema):
    """LC KG relationship record (shared schema across relationship types).

    Relationships connect two entities in the LC KG export. The meaning of the edge is
    defined by `relationshipType` (e.g., hasChild, supports, buildsTowards, relatesTo).
    """

    attribution_statement: str = Field(
        description=(
            "Attribution text required to credit the original publisher/owner of the "
            "source content that this relationship derives from."
        ),
    )
    author: str = Field(
        description=(
            "Human or organization name considered the author/owner of this relationship record, "
            "typically inherited from the framework/provider."
        ),
    )
    date_created: Optional[str] = Field(
        default=None,
        description="Creation timestamp for this relationship (ISO-8601 string), if known. Optional.",
    )
    date_modified: Optional[str] = Field(
        default=None,
        description="Last-modified timestamp for this relationship (ISO-8601 string), if known. Optional.",
    )
    description: str = Field(
        default="",
        description=(
            "Human-readable description of the relationship. LC expects this to be present; "
            "if omitted/blank, the model will deterministically fill a canonical description."
        ),
    )
    identifier: UUID = Field(
        description=(
            "Primary internal identifier for this relationship record. Must be deterministic "
            "across reruns (UUIDv5 recommended)."
        ),
    )
    license: str = Field(
        description=(
            "License string for the relationship record. Often inherited from the provider "
            "dataset license (e.g., a CC BY URL)."
        ),
    )
    metadata: _MetadataT = Field(
        default_factory=dict,
        description=(
            "Free-form metadata for pipeline/internal use (e.g., inference provenance pointers). "
            "Not part of LC’s public relationship schema; consider omitting in strict exports."
        ),
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often your organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )
    relationship_type: str = Field(
        description=(
            "Normalized relationship label defining the semantic meaning of the connection "
            "(e.g., hasChild, supports, buildsTowards, relatesTo)."
        ),
    )
    source_entity: str = Field(
        description=(
            "Entity type where the relationship originates (e.g., StandardsFramework, "
            "StandardsFrameworkItem, LearningComponent)."
        ),
    )
    source_entity_key: str = Field(
        description=(
            "The identifier property name on the source entity used by this relationship "
            "(e.g., identifier, case_identifier_uuid)."
        ),
    )
    source_entity_value: str = Field(
        description="The identifier value of the source entity (string UUID)."
    )
    target_entity: str = Field(
        description="Entity type where the relationship points (destination node type).",
    )
    target_entity_key: str = Field(
        description=(
            "The identifier property name on the target entity used by this relationship "
            "(e.g., identifier, case_identifier_uuid)."
        ),
    )
    target_entity_value: str = Field(
        description="The identifier value of the target entity (string UUID)."
    )

    @field_validator(
        "attribution_statement",
        "author",
        "license",
        "provider",
        "relationship_type",
        "source_entity",
        "source_entity_key",
        "source_entity_value",
        "target_entity",
        "target_entity_key",
        "target_entity_value",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings for required fields.

        Parameters
        ----------
        v
            The input string value to validate.

        Returns
        -------
        str
            The validated and stripped string value.
        """

        return _strip_and_require_non_empty_str(v)

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: Optional[str]) -> str:
        """Strip description; allow blank here (we deterministically fill in model
        validator).

        Parameters
        ----------
        v
            The input description string to validate.

        Returns
        -------
        str
            The validated and stripped description string (may be empty).

        Raises
        ------
        TypeError
            If the input is not a string or None.
        """

        if v is None:
            return ""

        if not isinstance(v, str):
            raise TypeError("description must be a string")

        return v.strip()

    def _validate_has_child(self) -> None:
        """Validate 'hasChild' constraints: (Framework|SFI) -> SFI using CASE UUID
        endpoints.

        Raises
        ------
        ValueError
            If any of the hasChild constraints are violated.
        """

        if self.target_entity != "StandardsFrameworkItem":
            raise ValueError("hasChild targetEntity must be StandardsFrameworkItem")

        if self.source_entity not in {"StandardsFramework", "StandardsFrameworkItem"}:
            raise ValueError(
                "hasChild sourceEntity must be StandardsFramework or StandardsFrameworkItem"
            )

        if (
            self.source_entity_key != "case_identifier_uuid"
            or self.target_entity_key != "case_identifier_uuid"
        ):
            raise ValueError("hasChild must use case_identifier_uuid endpoints")

    def _validate_supports(self) -> None:
        """Validate 'supports' constraints: LearningComponent -> StandardsFrameworkItem.

        Raises
        ------
        ValueError
            If any of the supports constraints are violated.
        """

        if (
            self.source_entity != "LearningComponent"
            or self.target_entity != "StandardsFrameworkItem"
        ):
            raise ValueError(
                "supports must be LearningComponent -> StandardsFrameworkItem"
            )

        if not (
            self.source_entity_key == "identifier"
            and self.target_entity_key == "case_identifier_uuid"
        ):
            raise ValueError(
                "supports must use source identifier + target case_identifier_uuid"
            )

    def _validate_progression(self) -> None:
        """Validate buildsTowards/relatesTo constraints: SFI -> SFI using CASE UUID
        endpoints.

        Raises
        ------
        ValueError
            If any of the progression constraints are violated.
        """

        if (
            self.source_entity != "StandardsFrameworkItem"
            or self.target_entity != "StandardsFrameworkItem"
        ):
            raise ValueError(
                f"{self.relationship_type} must be StandardsFrameworkItem -> StandardsFrameworkItem"
            )

        if (
            self.source_entity_key != "case_identifier_uuid"
            or self.target_entity_key != "case_identifier_uuid"
        ):
            raise ValueError(
                f"{self.relationship_type} must use case_identifier_uuid endpoints"
            )

    def _validate_common_schema(self) -> None:
        """Validate allowed values for relationship types and entity keys.

        Raises
        ------
        ValueError
            If any common schema constraints are violated.
        """

        if self.relationship_type not in _AllowedRelationshipTypes:
            raise ValueError(
                f"Unsupported relationshipType: {self.relationship_type}\n"
                f"Valid relationship types are: {_AllowedRelationshipTypes}"
            )

        if self.source_entity_key not in _AllowedEntityKeys:
            raise ValueError(f"Invalid sourceEntityKey: {self.source_entity_key}")

        if self.target_entity_key not in _AllowedEntityKeys:
            raise ValueError(f"Invalid targetEntityKey: {self.target_entity_key}")

    def _validate_data_integrity(self) -> None:
        """Validate that endpoint values are valid UUID strings.

        Raises
        ------
        ValueError
            If either endpoint value is not a valid UUID string.
        """

        try:
            UUID(str(self.source_entity_value))
        except Exception as e:
            raise ValueError(
                f"sourceEntityValue is not a UUID: {self.source_entity_value}"
            ) from e

        try:
            UUID(str(self.target_entity_value))
        except Exception as e:
            raise ValueError(
                f"targetEntityValue is not a UUID: {self.target_entity_value}"
            ) from e

    def _validate_type_specific_logic(self) -> None:
        """Dispatch validation to specific methods based on relationship type."""

        if self.relationship_type == "hasChild":
            self._validate_has_child()
        elif self.relationship_type == "supports":
            self._validate_supports()
        elif self.relationship_type in {"buildsTowards", "relatesTo"}:
            self._validate_progression()

    @model_validator(mode="after")
    def _prevent_self_loops(self) -> Relationship:
        """Prevent self-loop relationships (especially harmful for progressions/tree
        edges).

        Returns
        -------
        Relationship
            The validated Relationship object.

        Raises
        ------
        ValueError
            If the relationship connects an entity to itself.
        """

        if (
            self.source_entity == self.target_entity
            and self.source_entity_key == self.target_entity_key
            and self.source_entity_value == self.target_entity_value
        ):
            raise ValueError("Relationship cannot connect an entity to itself")

        return self

    @model_validator(mode="after")
    def _validate_relationship_shape(self) -> Relationship:
        """Orchestrator for relationship validation."""

        self._validate_common_schema()
        self._validate_data_integrity()
        self._validate_type_specific_logic()

        return self

    @model_validator(mode="after")
    def _fill_missing_description(self) -> Relationship:
        """Deterministically fill description if missing/blank (LC expects it to be
        present).

        Returns
        -------
        Relationship
            The Relationship object with a filled description if it was missing.
        """

        if not self.description:
            default_map = {
                "hasChild": "A hasChild relationship links a parent framework/item to a child standards item.",
                "supports": "A supports relationship links a learning component to a standards item it supports.",
                "buildsTowards": "A buildsTowards relationship indicates prerequisite progression from one standards item to another.",
                "relatesTo": "A relatesTo relationship indicates an associative connection between two standards items.",
            }
            self.description = default_map.get(
                self.relationship_type,
                f"A {self.relationship_type} relationship between {self.source_entity} and {self.target_entity}.",
            )

        return self


# Schemas for provenance.
class BBox(BaseSchema):
    """Bounding box in pixel coordinates."""

    coord_space: Literal["px"] = "px"
    x0: float = Field(..., description="Left coordinate in pixels.", ge=0.0)
    x1: float = Field(..., description="Right coordinate in pixels.", ge=0.0)
    y0: float = Field(..., description="Top coordinate in pixels.", ge=0.0)
    y1: float = Field(..., description="Bottom coordinate in pixels.", ge=0.0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_list(cls, data: Any) -> Any:
        """Coerce a list or tuple of 4 numbers into a BBox dict.

        Parameters
        ----------
        data
            The input data to validate, which may be a dict or a list/tuple of 4
            numbers.

        Returns
        -------
        Any
            The validated BBox data, either as a dict or the original data if it was
            not a list/tuple of 4 numbers.

        Raises
        ------
        ValueError
            If the input is a list/tuple but does not have exactly 4 numbers.
        """

        if isinstance(data, (list, tuple)):
            if len(data) != 4:
                raise ValueError(
                    "Bounding box must have exactly 4 numbers: [x0, y0, x1, y1]."
                )

            return {"x0": data[0], "y0": data[1], "x1": data[2], "y1": data[3]}
        return data

    @model_validator(mode="after")
    def _normalize_axis_order(self) -> BBox:
        """Normalize bbox ordering and expand zero-size axes.

        Returns
        -------
        BBox
            The BBox object with normalized coordinates.
        """

        self.x0, self.y0, self.x1, self.y1 = validate_bbox_order(
            [self.x0, self.y0, self.x1, self.y1]
        )
        return self


class EntityProvenance(BaseSchema):
    """Provenance information for a node."""

    bbox: Optional[BBox] = None
    canonical_node_id: str
    columns_signatures: list[str] = Field(
        default_factory=list,
        description=(
            "Columns signature(s) from the source segment decision(s) for this entity, "
            "if the node originated from table-based extraction. Empty for non-table nodes."
        ),
    )
    dialect_fallbacks: dict[str, str] = Field(default_factory=dict)
    entity_identifier: UUID
    entity_type: str = Field(
        default="",
        description="Entity type label (e.g., StandardsFrameworkItem, LearningComponent).",
    )
    local_code: Optional[str] = Field(default=None)
    page_indices: list[int] = Field(default_factory=list)
    role: str = Field(
        default="",
        description=(
            "Source role label for the entity (e.g., NodeRole value for SFIs, "
            "'framework' for StandardsFramework, 'learning_component' for LCs)."
        ),
    )
    section_path_text: list[str] = Field(default_factory=list)
    source_decision_ids: list[str] = Field(default_factory=list)
    source_segment_ids: list[str] = Field(default_factory=list)
    text: Optional[TextUnit] = None


# Schemas for export configurations.
class EntityProvenanceExport(BaseSchema):
    """Schema for entity provenance export.

    Flat lookup table: export_id -> canonical_node_id, source provenance fields.
    Designed for debugging and auditing without cracking open nested entity metadata.
    """

    doc_key: Optional[str] = None
    entities: list[EntityProvenance] = Field(
        default_factory=list, description="List of entities."
    )
    pdf_name: Optional[str] = None


class HierarchyOrderExport(BaseSchema):
    """Schema for exporting explicit ordering of child SFIs under parent SFIs."""

    order: dict[str, list[str]] = Field(
        default_factory=dict, description="Order of child SFIs."
    )


# Schemas for graph validation reporting.
class GraphValidationIssue(BaseSchema):
    """A single validation finding."""

    code: str
    context: dict[str, Any] = Field(default_factory=dict)
    level: _ValidationLevel
    message: str


class GraphValidationReport(BaseSchema):
    """Accumulates validation issues and basic knowledge graph building stats."""

    doc_key: Optional[str] = None
    issues: list[GraphValidationIssue] = Field(default_factory=list)
    pdf_name: Optional[str] = None
    stats: dict[str, Any] = Field(default_factory=dict)

    def add(
        self,
        *,
        code: str,
        context: Optional[dict[str, Any]] = None,
        level: _ValidationLevel,
        message: str,
    ) -> None:
        """Add a validation issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        context
            Optional additional context for debugging.
        level
            Severity level of the issue.
        message
            Human-readable description of the issue.
        """

        self.issues.append(
            GraphValidationIssue(
                code=code, context=context or {}, level=level, message=message
            )
        )

    def error(
        self, *, code: str, context: Optional[dict[str, Any]] = None, message: str
    ) -> None:
        """Add an error-level issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        context
            Optional additional context for debugging.
        message
            Human-readable description of the issue.
        """

        self.add(code=code, context=context, level="error", message=message)

    def errors(self) -> list[GraphValidationIssue]:
        """Get all error-level issues.

        Returns
        -------
        list[GraphValidationIssue]
            List of error-level issues.
        """

        return [i for i in self.issues if i.level == "error"]

    def has_errors(self) -> bool:
        """Check if any error-level issues are present.

        Returns
        -------
        bool
            True if any error-level issues are present, False otherwise.
        """

        return any(i.level == "error" for i in self.issues)

    def info(
        self, *, code: str, context: Optional[dict[str, Any]] = None, message: str
    ) -> None:
        """Add an info-level issue.

        Parameters
        ----------
        code
            Short machine-readable code for the issue.
        context
            Optional additional context for debugging.
        message
            Human-readable description of the issue.
        """

        self.add(code=code, context=context, level="info", message=message)

    def raise_if_errors(self) -> None:
        """Raise a ValueError if any errors are present in the report.

        Raises
        ------
        ValueError
            If any errors are present in the report.
        """

        if not self.has_errors():
            return

        # Keep the exception message readable.
        lines = ["GraphValidationReport pre-validation failed:"]

        for i in self.errors()[:15]:
            lines.append(f"- [{i.code}] {i.message}")

        if len(self.errors()) > 15:
            lines.append(f"- ... plus {len(self.errors()) - 15} more errors")

        raise ValueError("\n".join(lines))


class PolicyCoverageReport(BaseSchema):
    """Aggregate report explaining what was emitted, dropped, and why.

    This is the primary debuggability artifact for the KG export pipeline. It answers
    "why was this node dropped?" and provides summary statistics for every export phase.
    """

    doc_key: Optional[str] = None
    generated_at: Optional[str] = None
    pdf_name: Optional[str] = None

    # Node-level drop accounting (academic standards).
    drop_reason_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Complete count of Academic Standards drop reasons, keyed by the raw "
            "drop-reason taxonomy string. This preserves new upstream drop reasons "
            "even before curated report fields are added."
        ),
    )
    dropped_aux_attached_to_expectation: int = Field(
        default=0,
        description=(
            "Aux guidance/descriptor nodes converted to expectation metadata "
            "attachments and therefore not emitted as standalone SFIs."
        ),
    )
    dropped_aux_descendants_suppressed: int = Field(
        default=0,
        description=(
            "Descendant nodes suppressed because they lived under an aux node that was "
            "converted into expectation metadata."
        ),
    )
    dropped_due_to_expectation_metadata_attachment: int = Field(
        default=0,
        description=(
            "Total nodes dropped because of expectation-metadata attachment handling: "
            "attached aux nodes plus descendants suppressed below attached aux nodes."
        ),
    )
    dropped_by_columns_signature: dict[str, int] = Field(
        default_factory=dict,
        description="Count of nodes dropped per columns_signature value.",
    )
    dropped_by_decision_type: dict[str, int] = Field(
        default_factory=dict,
        description="Count of nodes dropped per segment decision type (e.g., ignore, unresolved).",
    )
    dropped_descriptor: int = Field(
        default=0, description="Nodes dropped because as_descriptor_handling == 'drop'."
    )
    dropped_guidance: int = Field(
        default=0, description="Nodes dropped because as_guidance_handling == 'drop'."
    )
    dropped_non_grouping_role: int = Field(
        default=0,
        description=(
            "Total nodes dropped with a drop:non_grouping_role:* reason. See "
            "dropped_non_grouping_role_counts for the suffix-level breakdown."
        ),
    )
    dropped_non_grouping_role_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Count of nodes dropped per drop:non_grouping_role:* suffix, such as "
            "'drop' or 'structural_parent'."
        ),
    )
    pruned_empty_groupings: int = Field(
        default=0,
        description="Grouping nodes pruned because they had zero emitted children.",
    )
    total_canonical_nodes: int = 0
    total_emitted_sfis: int = 0

    # Canonical-node accounting completeness.
    coverage_accounted_canonical_nodes: int = Field(
        default=0,
        description=(
            "Number of non-root canonical node IDs covered by the union of emitted "
            "SFI source nodes and dropped canonical nodes."
        ),
    )
    coverage_accounting_ok: bool = Field(
        default=True,
        description=(
            "True when every non-root canonical node is accounted for exactly once as "
            "either emitted as an SFI or intentionally dropped by Academic Standards "
            "policy, with no emitted/dropped overlap and no non-canonical node IDs "
            "appearing in either set."
        ),
    )
    coverage_details_limit: int = Field(
        default=200,
        description="Maximum number of node IDs included per coverage-details list.",
    )
    coverage_details_truncated: bool = Field(
        default=False,
        description=(
            "Whether any coverage-details list was truncated because it exceeded "
            "coverage_details_limit."
        ),
    )
    coverage_emitted_and_dropped_overlap_count: int = Field(
        default=0,
        description=(
            "Canonical node IDs that appear both as emitted SFI source nodes and as "
            "dropped nodes."
        ),
    )
    coverage_emitted_and_dropped_overlap_node_ids: list[str] = Field(
        default_factory=list,
        description="Example canonical node IDs both emitted and dropped.",
    )
    coverage_emitted_sfis_missing_canonical_node_id_count: int = Field(
        default=0,
        description=(
            "Emitted SFI rows whose metadata lacks canonical_node_id and therefore "
            "cannot be tied back to a Canonical IR node for coverage accounting."
        ),
    )
    coverage_emitted_sfis_missing_canonical_node_id_examples: list[str] = Field(
        default_factory=list,
        description=(
            "Example emitted SFI UUIDs whose metadata lacks canonical_node_id."
        ),
    )
    coverage_noncanonical_dropped_node_count: int = Field(
        default=0,
        description=(
            "Academic Standards drop_reasons node IDs that are not non-root Canonical "
            "IR node IDs."
        ),
    )
    coverage_noncanonical_dropped_node_ids: list[str] = Field(
        default_factory=list,
        description="Example dropped node IDs not present in Canonical IR.",
    )
    coverage_noncanonical_emitted_node_count: int = Field(
        default=0,
        description=(
            "Emitted SFI metadata canonical_node_id values that are not non-root "
            "Canonical IR node IDs."
        ),
    )
    coverage_noncanonical_emitted_node_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Example emitted canonical_node_id values not present in Canonical IR."
        ),
    )
    coverage_over_accounted_canonical_nodes: int = Field(
        default=0,
        description=(
            "Canonical-node accounting anomalies caused by emitted/dropped overlap or "
            "node IDs in emitted/drop accounting that do not exist in the Canonical IR."
        ),
    )
    coverage_unaccounted_canonical_nodes: int = Field(
        default=0,
        description=(
            "Canonical nodes that are neither emitted as SFIs nor present in Academic "
            "Standards drop_reasons."
        ),
    )
    coverage_unaccounted_node_ids: list[str] = Field(
        default_factory=list,
        description="Example canonical node IDs not emitted and not dropped.",
    )

    # Aux reparenting/attachment and hierarchy-hoisting stats.
    attach_only_newly_attached_aux_node_count: int = Field(
        default=0,
        description=(
            "Unique aux node IDs newly discovered and attached during the step-4 "
            "attach-only discovery pass."
        ),
    )
    attached_aux_subtree_root_count: int = Field(
        default=0,
        description=(
            "Attached aux nodes that still had exported child subtrees when subtree "
            "suppression ran."
        ),
    )
    child_layout_aux_attached_count: int = Field(
        default=0,
        description=(
            "Aux statements discovered as canonical children of an expectation and "
            "attached during step 3 export-tree construction."
        ),
    )
    dropped_parents_processed: int = Field(
        default=0,
        description=(
            "Dropped parents with emitted children that were processed during hierarchy "
            "hoisting."
        ),
    )
    dropped_parents_removed_from_parent_lists_count: int = Field(
        default=0,
        description=(
            "Dropped parents whose stale references were removed from at least one "
            "export parent child-list."
        ),
    )
    orphan_aux_count: int = Field(
        default=0,
        description=(
            "Total unique aux nodes that could not be attached to an owning "
            "expectation (for example, no preceding expectation in sibling order)."
        ),
    )
    reattach_appended_without_anchor_order_count: int = Field(
        default=0,
        description=(
            "Hoist operations that appended children because no anchor-based ordering "
            "signal was available."
        ),
    )
    reattach_original_sibling_fallback_count: int = Field(
        default=0,
        description=(
            "Hoist operations that used original sibling-position fallback because "
            "canonical edge ordering was unavailable."
        ),
    )
    reattached_children_count: int = Field(
        default=0,
        description="Emitted children newly inserted under surviving ancestors.",
    )
    removed_dropped_parent_reference_list_count: int = Field(
        default=0,
        description=(
            "Total number of export parent child-lists modified while removing stale "
            "dropped-parent references."
        ),
    )
    sibling_aux_reparented_count: int = Field(
        default=0,
        description=(
            "Aux sibling statements reparented to the most recent preceding "
            "expectation during step 3 export-tree construction."
        ),
    )
    suppressed_attached_aux_descendant_count: int = Field(
        default=0,
        description=(
            "Descendant nodes suppressed below attached aux nodes so they cannot be "
            "hoisted back into the exported hierarchy."
        ),
    )
    suppressed_attached_aux_node_count: int = Field(
        default=0,
        description=(
            "Attached aux nodes newly suppressed as standalone SFIs by the "
            "attach-to-expectation policy enforcement step."
        ),
    )
    total_attached_aux_node_count: int = Field(
        default=0,
        description=(
            "Total unique aux node IDs tracked as attached to an expectation after "
            "the attach-only discovery pass (steps 3-4 combined)."
        ),
    )

    # LC stats.
    lc_fallback_sfis_count: int = Field(
        default=0,
        description="LC-source SFIs that fell back to deterministic 1_to_1 generation.",
    )
    lc_max_splits_observed: int = 0
    lc_source_exclusion_reason_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Counts of LC-source eligibility exclusion reasons. The eligible reason is "
            "omitted so this field focuses on exclusions."
        ),
    )
    lc_split_policy: str = ""
    lc_splits_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Distribution of split counts: how many SFIs produced N LCs. Keys are stringified integers (e.g., '1': 500, '2': 50).",
    )
    total_lc_source_sfis_considered: int = Field(
        default=0,
        description="Total SFIs considered by LC-source eligibility filtering.",
    )
    total_lc_source_sfis_eligible: int = Field(
        default=0,
        description="Total SFIs eligible to generate LearningComponents.",
    )
    total_lc_source_sfis_empty_text: int = Field(
        default=0,
        description=(
            "Eligible LC-source SFIs skipped or producing zero LCs because usable text "
            "was empty."
        ),
    )
    total_lc_source_sfis_excluded: int = Field(
        default=0,
        description="Total SFIs excluded by LC-source eligibility filtering.",
    )
    total_lcs: int = 0

    # LP stats (populated only when `generate_learning_progressions` is True).
    lp_bucket_drop_counts: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Summarized Learning Progressions bucket/source drops copied from the LP "
            "report."
        ),
    )
    lp_candidate_builds_towards: int = Field(
        default=0,
        description="Candidate buildsTowards edges before filtering.",
    )
    lp_candidate_edges_after_dedupe: int = Field(
        default=0,
        description="Total candidate edges remaining after deduplication.",
    )
    lp_candidate_edges_pre_dedupe: int = Field(
        default=0,
        description="Total candidate edges before deduplication.",
    )
    lp_candidate_relates_to: int = Field(
        default=0,
        description="Candidate relatesTo edges before filtering.",
    )
    lp_dropped_cap_relates: int = Field(
        default=0,
        description="relatesTo edges dropped due to per-node cap.",
    )
    lp_dropped_dedupe: int = Field(
        default=0,
        description="Edges dropped during deduplication.",
    )
    lp_dropped_doc_order_builds: int = Field(
        default=0,
        description="buildsTowards edges dropped by document-order filter.",
    )
    lp_dropped_low_conf_builds: int = Field(
        default=0,
        description="buildsTowards edges dropped due to low confidence.",
    )
    lp_dropped_low_conf_relates: int = Field(
        default=0,
        description="relatesTo edges dropped due to low confidence.",
    )
    lp_final_relationship_counts: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Final Learning Progressions relationship counts copied from "
            "learning_progressions.report['final_relationship_counts']."
        ),
    )
    lp_kept_builds_towards: int = Field(
        default=0,
        description="Final kept buildsTowards edges after all filters.",
    )
    lp_kept_builds_towards_before_doc_order: int = Field(
        default=0,
        description="Kept buildsTowards edges before document-order filter.",
    )
    lp_kept_relates_to: int = Field(
        default=0,
        description="Final kept relatesTo edges after all filters.",
    )
    lp_kept_relates_to_after_threshold: int = Field(
        default=0,
        description="Kept relatesTo edges after confidence threshold filter.",
    )
    lp_phase_toggles: dict[str, Any] = Field(default_factory=dict)
    lp_thresholds: dict[str, Any] = Field(default_factory=dict)

    # Detailed per-node drop log (first N for debuggability).
    drop_details: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-node drop log (capped at drop_details_limit entries). Each entry "
            "includes canonical_node_id, role, and drop_reason."
        ),
    )
    drop_details_limit: int = Field(
        default=200,
        description="Maximum number of drop_details entries included in this report.",
    )
    drop_details_total_count: int = Field(
        default=0,
        description="Total number of dropped nodes before drop_details truncation.",
    )
    drop_details_truncated: bool = Field(
        default=False,
        description="Whether drop_details was truncated because it exceeded the limit.",
    )
