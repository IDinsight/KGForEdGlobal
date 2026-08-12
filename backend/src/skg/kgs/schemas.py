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
from typing import Any, Callable, Literal, Optional, Self, Sequence
from urllib.parse import urlparse
from uuid import UUID

# Third Party Library
from pydantic import ConfigDict, Field, field_validator, model_validator

# Package Library
from skg.schemas import BaseSchema, LanguageField, NormalizedStatementType
from skg.utils.general import strip_and_require_non_empty_str

_AllowedRelationshipTypes = {"hasChild", "supports", "buildsTowards", "relatesTo"}
_AllowedEntityKeys = {"identifier", "case_identifier_uuid"}
_MetadataT = dict[str, Any]
_ProgressionSubtype = Literal["developmental_prerequisite", "recurring_practice"]
SFICodeResolutionMethod = Literal[
    "no_source_code",
    "review_selected_source_code",
    "single_source_code",
    "unresolved_multiple_source_codes",
]
SFIDedupDecision = Literal["conflict", "keep_separate", "merge", "needs_review"]
SFIMergeDecision = Literal["conflict", "merged", "needs_review", "singleton"]
SFISourceUnitKind = Literal[
    "block_list_item",
    "block_slice_text",
    "block_text",
    "figure_caption",
    "figure_embedded_text",
    "table_body_cell",
    "table_header_cell",
]


def _find_scope_value_conflicts(
    scope_value_maps: Sequence[dict[str, str]],
) -> dict[str, list[str]]:
    """Find contradictory values assigned to shared semantic scope dimensions.

    Parameters
    ----------
    scope_value_maps
        Source-backed scope mappings to compare.

    Returns
    -------
    dict[str, list[str]]
        Scope labels mapped to their distinct conflicting values. An empty mapping
        means every shared scope dimension is compatible.
    """

    values_by_scope_label: dict[str, set[str]] = {}

    for scope_values in scope_value_maps:
        for scope_label, scope_value in scope_values.items():
            values_by_scope_label.setdefault(scope_label, set()).add(scope_value)

    return {
        scope_label: sorted(scope_values)
        for scope_label, scope_values in sorted(values_by_scope_label.items())
        if len(scope_values) > 1
    }


def _source_refs_share_same_source_occurrence_cross_type_evidence(
    source_refs: Sequence[dict[str, Any]],
) -> bool:
    """Check whether source refs preserve one exact cross-type occurrence.

    Parameters
    ----------
    source_refs
        Candidate source-reference dictionaries from one proposed merge group.

    Returns
    -------
    bool
        True when all refs preserve one identical non-empty description-anchor set
        across multiple statement-type pairs.
    """

    if len(source_refs) < 2:
        return False

    type_pairs = {
        (
            str(source_ref.get("statement_type") or "").strip(),
            str(source_ref.get("normalized_statement_type") or "").strip(),
        )
        for source_ref in source_refs
    }

    if len(type_pairs) < 2:
        return False

    anchor_signatures = {
        tuple(
            sorted(
                (
                    str(anchor.get("source_unit_id") or "").strip(),
                    int(anchor.get("occurrence_index", -1)),
                    str(anchor.get("source_text") or ""),
                )
                for anchor in source_ref.get("description_source_anchors") or []
            )
        )
        for source_ref in source_refs
    }

    if len(anchor_signatures) != 1:
        return False

    only_signature = next(iter(anchor_signatures))
    return bool(only_signature) and all(
        source_unit_id and occurrence_index >= 0 and source_text
        for source_unit_id, occurrence_index, source_text in only_signature
    )


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


def _validate_unique_source_anchors(
    *, anchors: Sequence[SFISourceAnchor], field_name: str
) -> None:
    """Validate that one candidate anchor list contains no duplicate references.

    Parameters
    ----------
    anchors
        Candidate source anchors to validate.
    field_name
        Human-readable field name for validation errors.

    Raises
    ------
    ValueError
        If duplicate exact anchor references are present.
    """

    signatures = [
        (anchor.source_unit_id, anchor.occurrence_index, anchor.source_text)
        for anchor in anchors
    ]

    if len(signatures) != len(set(signatures)):
        raise ValueError(f"{field_name} must not contain duplicate source anchors.")


def clean_scope_values(*, field_name: str, values: dict[str, str]) -> dict[str, str]:
    """Clean and validate an ordered semantic scope-value mapping.

    Parameters
    ----------
    field_name
        Human-readable field name used in validation errors.
    values
        Mapping from configured scope statement-type labels to canonical values.

    Returns
    -------
    dict[str, str]
        Cleaned mapping preserving configured scope order.

    Raises
    ------
    TypeError
        If a scope label or canonical value is not a string.
    ValueError
        If a scope label or canonical value is blank after stripping.
    """

    cleaned: dict[str, str] = {}

    for statement_type, canonical_value in values.items():
        if not isinstance(statement_type, str) or not isinstance(canonical_value, str):
            raise TypeError(f"{field_name} labels and values must be strings.")

        statement_type_clean = statement_type.strip()
        canonical_value_clean = canonical_value.strip()

        if not statement_type_clean or not canonical_value_clean:
            raise ValueError(f"{field_name} labels and values must be non-empty.")

        if statement_type_clean in cleaned:
            raise ValueError(
                f"Duplicate {field_name} label {statement_type_clean!r} after "
                "stripping whitespace."
            )

        cleaned[statement_type_clean] = canonical_value_clean

    return cleaned


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


class _HasCaseIdentifierFields:
    """Structural type stub for models with case_identifier_uri/uuid fields."""

    case_identifier_uri: str
    case_identifier_uuid: UUID


class _HasDateFields:
    """Structural type stub for models with date_created/date_modified fields."""

    date_created: Optional[str]
    date_modified: Optional[str]


# Schemas for extraction windows.
class CodeMatch(BaseSchema):
    """A configured code-pattern match found in an extraction window."""

    code_type: str = Field(
        description="KG config local code pattern key, such as 'content_standard'."
    )
    end_char: int = Field(
        description="End character offset of the raw match within window source_text.",
        ge=0,
    )
    normalized_value: str = Field(
        description=(
            "Formatting-normalized code value for structured statement_code use."
        ),
        min_length=1,
    )
    raw_value: str = Field(
        description="Exact source-visible code surface form matched in source_text.",
        min_length=1,
    )
    start_char: int = Field(
        description="Start character offset of the raw match within window source_text.",
        ge=0,
    )

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

    child_code: str = Field(description="Formatting-normalized matched child code.")
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
        description="Typed raw and normalized KG config code matches in source_text.",
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
    kg_extraction_instructions: str = Field(
        description="KG config.academic_standards.sfi_extraction_instructions."
    )
    pdf_name: Optional[str] = Field(default=None, description="Source PDF filename.")
    primary_language: str = Field(description="KG config primary language.")
    segment_kind: Literal["block", "table"] = Field(description="Source segment kind.")
    scope_context_candidates: list[ExtractionWindowScopeContextCandidate] = Field(
        default_factory=list,
        description=(
            "Runtime-configured controlled scope values recognized from neighboring "
            "headings and section-path context. Context-only and not a resolved scope."
        ),
    )
    source_context_after: list[ExtractionWindowContextEvidence] = Field(
        default_factory=list,
        description=(
            "Bounded following same-page heading context. Context-only and not "
            "candidate evidence."
        ),
    )
    source_context_before: list[ExtractionWindowContextEvidence] = Field(
        default_factory=list,
        description=(
            "Bounded preceding same-page heading context. Context-only and not "
            "candidate evidence."
        ),
    )
    source_provenance: list[dict[str, Any]] = Field(
        default_factory=list, description="Segment/page provenance for the source."
    )
    source_section_path: list[dict[str, Any]] = Field(
        description=(
            "Source DocumentIR section-path references preserved in path order. "
            "These are source-context hints, not an inferred KG ancestor chain."
        )
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

        if any(
            context.context_direction != "following"
            for context in self.source_context_after
        ):
            raise ValueError(
                "source_context_after entries must have context_direction='following'."
            )

        if any(
            context.context_direction != "preceding"
            for context in self.source_context_before
        ):
            raise ValueError(
                "source_context_before entries must have context_direction='preceding'."
            )

        return self


class ExtractionWindowContextEvidence(BaseSchema):
    """One neighboring same-page heading supplied only as extraction context.

    The text in this model is source-visible, but it is not part of the target source
    unit for the extraction window. It may guide semantic scope interpretation when PDF
    reading order differs from visual layout, but it cannot be used for candidate
    anchors, candidate descriptions, or candidate source text unless the same wording
    is also visible in the target block or table payload.
    """

    block_type: str = Field(
        description="Source block type for the neighboring context segment."
    )
    context_direction: Literal["following", "preceding"] = Field(
        description="Whether the context segment follows or precedes the target segment."
    )
    document_segment_index: int = Field(
        description="0-based position of the context segment in DocumentIR.segments.",
        ge=0,
    )
    source_page_indexes: list[int] = Field(
        description="Sorted unique 0-based pages containing the context segment.",
        min_length=1,
    )
    source_segment_id: str = Field(
        description="DocumentIR segment_id for the context-only heading.",
        min_length=1,
    )
    source_text: str = Field(
        description="Exact source-visible text of the neighboring heading segment.",
        min_length=1,
    )
    source_visibility: Literal["context_only"] = Field(
        default="context_only",
        description="Fixed marker that prevents this text from becoming candidate evidence.",
    )


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


class ExtractionWindowScopeContextCandidate(BaseSchema):
    """One controlled scope value recognized from extraction context.

    This model preserves deterministic recognition evidence without deciding which
    value governs the target occurrence. The producer and checker apply runtime policy
    to select the active value. All text remains context-only and cannot support
    candidate anchors, descriptions, or source text.
    """

    canonical_value: str = Field(
        description="Configured canonical value recognized from the context text.",
        min_length=1,
    )
    context_direction: Literal["following", "preceding"] = Field(
        description="Source-order direction of the context relative to the target."
    )
    context_origin: Literal["neighbor_heading", "section_path"] = Field(
        description="Deterministic context channel that supplied this candidate."
    )
    document_segment_index: Optional[int] = Field(
        default=None,
        description=(
            "0-based DocumentIR segment position for neighbor-heading evidence; null "
            "for section-path evidence."
        ),
        ge=0,
    )
    item_index: Optional[int] = Field(
        default=None,
        description=(
            "Source page item index for section-path evidence; null when unavailable."
        ),
        ge=0,
    )
    matched_text: str = Field(
        description=(
            "Exact context-text line or full field matched to the controlled value."
        ),
        min_length=1,
    )
    origin_rank: int = Field(
        description=(
            "Zero-based nearest-first rank within the same context origin and direction."
        ),
        ge=0,
    )
    scope_statement_type: str = Field(
        description="Configured statement type used as an identity-scope dimension.",
        min_length=1,
    )
    source_page_indexes: list[int] = Field(
        description="Sorted unique 0-based pages associated with the context evidence.",
        min_length=1,
    )
    source_segment_id: Optional[str] = Field(
        default=None,
        description=(
            "DocumentIR segment ID for neighbor-heading evidence; null for section-path "
            "evidence."
        ),
    )
    source_text: str = Field(
        description="Complete source-context text from which matched_text was selected.",
        min_length=1,
    )
    source_visibility: Literal["context_only"] = Field(
        default="context_only",
        description="Fixed marker that prevents this text from becoming candidate evidence.",
    )

    @model_validator(mode="after")
    def validate_context_origin_fields(self) -> Self:
        """Validate origin-specific provenance fields.

        Returns
        -------
        Self
            The validated scope-context candidate.

        Raises
        ------
        ValueError
            If neighbor-heading or section-path provenance fields are inconsistent.
        """

        if self.context_origin == "neighbor_heading":
            if self.document_segment_index is None or not self.source_segment_id:
                raise ValueError(
                    "neighbor_heading scope context requires document_segment_index "
                    "and source_segment_id."
                )

        if self.context_origin == "section_path":
            if (
                self.document_segment_index is not None
                or self.source_segment_id is not None
            ):
                raise ValueError(
                    "section_path scope context must not include document_segment_index "
                    "or source_segment_id."
                )

            if self.context_direction != "preceding":
                raise ValueError(
                    "section_path scope context must have context_direction='preceding'."
                )

        if self.source_page_indexes != sorted(set(self.source_page_indexes)):
            raise ValueError(
                "scope-context source_page_indexes must be sorted and unique."
            )

        if self.matched_text not in self.source_text:
            raise ValueError(
                "scope-context matched_text must be an exact excerpt of source_text."
            )

        return self


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

    def _validate_body_range(self) -> None:
        """Validate the body row range against header and source-row bounds.

        Raises
        ------
        ValueError
            If the body range is inconsistent with header_row_count or exceeds
            source_table_row_count.
        """

        if self.header_row_count > self.source_table_row_count:
            raise ValueError("header_row_count cannot exceed source_table_row_count.")

        if self.body_row_end_index_exclusive < self.body_row_start_index:
            raise ValueError(
                "body_row_end_index_exclusive must be >= body_row_start_index."
            )

        if self.body_row_end_index_exclusive > self.source_table_row_count:
            raise ValueError(
                "body_row_end_index_exclusive cannot exceed source_table_row_count."
            )

    def _validate_populated_row_indexes(self) -> None:
        """Validate a non-empty, contiguous body-row selection.

        Raises
        ------
        ValueError
            If row_indexes are not one contiguous body-row range, fall outside the
            source-row bounds, or disagree with the declared body range.
        """

        first, last = self.row_indexes[0], self.row_indexes[-1]

        if self.row_indexes != list(range(first, last + 1)):
            raise ValueError("row_indexes must form one contiguous source-row range.")

        if first < self.header_row_count:
            raise ValueError(
                "row_indexes must reference body rows at or after header_row_count."
            )

        if last >= self.source_table_row_count:
            raise ValueError(
                "row_indexes cannot reference rows outside source_table_row_count."
            )

        if self.body_row_start_index != first:
            raise ValueError(
                "body_row_start_index must equal the first row_indexes value."
            )

        if self.body_row_end_index_exclusive != last + 1:
            raise ValueError(
                "body_row_end_index_exclusive must equal the last row index plus one."
            )

    def _validate_header_only_range(self) -> None:
        """Validate the empty body range used by a header-only table window.

        Raises
        ------
        ValueError
            If the body range is not the empty range beginning at header_row_count.
        """

        if (
            self.body_row_start_index != self.header_row_count
            or self.body_row_end_index_exclusive != self.header_row_count
        ):
            raise ValueError(
                "Header-only table windows must use an empty body range beginning at "
                "header_row_count."
            )

    def _validate_helper_alignment(self) -> None:
        """Validate that rows and optional helper views align to row_indexes.

        Raises
        ------
        ValueError
            If rows or any present helper view has a length differing from
            row_indexes.
        """

        expected_len = len(self.row_indexes)

        if len(self.rows) != expected_len:
            raise ValueError("rows must be aligned to row_indexes.")

        for helper_field_name in (
            "grid_sources",
            "row_provenance",
            "rows_filldown",
            "rows_grid",
        ):
            helper_value = getattr(self, helper_field_name)

            if helper_value is not None and len(helper_value) != expected_len:
                raise ValueError(
                    f"{helper_field_name} must be aligned to row_indexes when present."
                )

    @model_validator(mode="after")
    def validate_row_ranges(self) -> Self:
        """Validate row-index, row-range, and helper-view consistency.

        Empty `row_indexes` is valid only for a header-only table window. In that case,
        the body range must be the empty range beginning at `header_row_count`.

        Returns
        -------
        Self
            The validated table payload.

        Raises
        ------
        ValueError
            If the row range, row indexes, or aligned helper views are inconsistent.
        """

        self._validate_body_range()

        if self.row_indexes != sorted(set(self.row_indexes)):
            raise ValueError("row_indexes must be strictly increasing and unique.")

        if self.row_indexes:
            self._validate_populated_row_indexes()
        else:
            self._validate_header_only_range()

        self._validate_helper_alignment()

        return self


# Schemas for SFI candidate extraction.
class SFISourceAnchor(BaseSchema):
    """Exact source excerpt anchored to one stable extraction source unit."""

    occurrence_index: int = Field(
        description=(
            "0-based left-to-right non-overlapping occurrence of source_text "
            "within the complete referenced source unit. This disambiguates "
            "repeated identical excerpts."
        ),
        ge=0,
    )
    source_text: str = Field(
        description="Exact non-empty source-visible excerpt from the source unit.",
        min_length=1,
    )
    source_unit_id: str = Field(
        description=(
            "Stable source-unit identifier exposed in the compact extraction window. "
            "It is independent of extraction-window overlap and candidate type."
        ),
        min_length=1,
    )

    @field_validator("source_text", "source_unit_id", mode="before")
    @classmethod
    def strip_required_strings(cls, v: str) -> str:
        """Strip and require non-empty source-anchor strings.

        Parameters
        ----------
        v
            Raw source-anchor string.

        Returns
        -------
        str
            Cleaned non-empty string.

        Raises
        ------
        ValueError
            If the value is empty.
        """

        return strip_and_require_non_empty_str(v)


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
    code_scope_values: dict[str, str] = Field(
        description=(
            "Checker-approved semantic scope for the candidate's official code, keyed "
            "by the configured code-scope statement types in configured order. Use an "
            "empty mapping when statement_code is null or its code type has no "
            "configured scope."
        )
    )
    code_source_anchors: list[SFISourceAnchor] = Field(
        default_factory=list,
        description=(
            "Exact source anchors for the separately represented official identifier "
            "code. Empty when statement_code is null."
        ),
    )
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
    description_source_anchors: list[SFISourceAnchor] = Field(
        description=(
            "Ordered exact source anchors supporting the complete semantic candidate "
            "description. Runtime policy may permit noncontiguous fragments, such as "
            "a shared normative stem and one later list item."
        ),
        min_length=1,
    )
    identity_scope_values: dict[str, str] = Field(
        description=(
            "Checker-approved semantic identity scope for this candidate, keyed by "
            "the configured source-facing scope statement types in configured order. "
            "Use an empty mapping when the candidate statement type has no configured "
            "identity scope."
        )
    )
    language: LanguageField = Field(
        description="Language tag for description/source_text."
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description="Standard, Standard Grouping, or Other."
    )
    source_text: str = Field(
        description=(
            "Checker-approved bounded source-visible evidence for this candidate. "
            "This field is selected by the producer/checker and is persisted without "
            "Python reconstruction from description or code anchors."
        ),
        min_length=1,
    )
    statement_code: Optional[str] = Field(
        default=None,
        description=(
            "Formatting-normalized official statement code when candidate-local source "
            "evidence exposes one; optional for no-code curricula."
        ),
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

    @field_validator("code_scope_values", "identity_scope_values")
    @classmethod
    def validate_scope_values(cls, v: dict[str, str], info: Any) -> dict[str, str]:
        """Clean a checker-approved semantic scope mapping.

        Parameters
        ----------
        v
            Raw statement-type-to-scope-value mapping returned by the LLM.
        info
            Pydantic validation metadata identifying the scope field.

        Returns
        -------
        dict[str, str]
            Cleaned mapping preserving the model-supplied order.
        """

        return clean_scope_values(field_name=info.field_name, values=v)

    @field_validator("statement_code", mode="before")
    @classmethod
    def strip_statement_code(cls, v: Optional[str]) -> Optional[str]:
        """Normalize blank statement codes to `None`.

        Parameters
        ----------
        v
            Optional statement code.

        Returns
        -------
        Optional[str]
            Stripped code, or `None`.
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

    @model_validator(mode="after")
    def validate_source_anchor_uniqueness(self) -> Self:
        """Validate that candidate anchor lists do not contain exact duplicates.

        Returns
        -------
        Self
            Candidate with structurally unique description and code anchors.

        Raises
        ------
        ValueError
            If either anchor list contains an exact duplicate anchor.
        """

        _validate_unique_source_anchors(
            anchors=self.code_source_anchors, field_name="code_source_anchors"
        )
        _validate_unique_source_anchors(
            anchors=self.description_source_anchors,
            field_name="description_source_anchors",
        )
        return self


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


class SFIExtractionValidationIssue(BaseSchema):
    """One issue found by the second-stage SFI extraction validation LLM."""

    auxiliary_id: Optional[str] = Field(
        default=None,
        description="Optional window-local auxiliary ID associated with the issue.",
    )
    candidate_id: Optional[str] = Field(
        default=None,
        description="Optional window-local SFI candidate ID associated with the issue.",
    )
    issue_type: str = Field(
        description="Short general issue category, such as omission or source_fidelity.",
        min_length=1,
    )
    message: str = Field(
        description="Specific source-grounded description of the issue.", min_length=1
    )
    severity: Literal["error", "warning"] = Field(
        description="Whether the issue requires correction or is advisory only."
    )

    @field_validator("auxiliary_id", "candidate_id", mode="before")
    @classmethod
    def clean_optional_issue_ids(cls, v: Optional[str]) -> Optional[str]:
        """Strip optional issue target IDs and normalize blanks to `None`.

        Parameters
        ----------
        v
            Raw optional issue target ID.

        Returns
        -------
        Optional[str]
            Stripped ID, or `None` for a blank value.
        """

        if v is None:
            return None

        value = str(v).strip()
        return value or None

    @field_validator("issue_type", "message", mode="before")
    @classmethod
    def clean_required_issue_strings(cls, v: str) -> str:
        """Strip and require non-empty issue text fields.

        Parameters
        ----------
        v
            Raw issue string.

        Returns
        -------
        str
            Stripped issue string.

        Raises
        ------
        ValueError
            If the issue string is blank.
        """

        value = str(v or "").strip()

        if not value:
            raise ValueError("SFI extraction validation issue text is required.")

        return value


class SFIExtractionValidationVerdict(BaseSchema):
    """Second-stage LLM verdict for one draft SFI extraction result."""

    corrected_result: Optional[SFIExtractionResult] = Field(
        default=None,
        description=(
            "Complete corrected extraction result when passed is false; null when "
            "the draft is accepted unchanged."
        ),
    )
    issues: list[SFIExtractionValidationIssue] = Field(
        default_factory=list,
        description="Source-grounded validation issues found in the draft result.",
    )
    passed: bool = Field(
        description="True when the draft result requires no material correction."
    )
    rationale: str = Field(
        description="Concise overall assessment of the draft extraction result.",
        min_length=20,
    )

    @field_validator("rationale", mode="before")
    @classmethod
    def clean_rationale(cls, v: str) -> str:
        """Strip and require a non-empty validation rationale.

        Parameters
        ----------
        v
            Raw rationale.

        Returns
        -------
        str
            Stripped rationale.

        Raises
        ------
        ValueError
            If the rationale is blank.
        """

        rationale = str(v or "").strip()

        if not rationale:
            raise ValueError("SFI extraction validation rationale is required.")

        return rationale

    @model_validator(mode="after")
    def validate_verdict_contract(self) -> Self:
        """Validate pass/fail agreement with issues and corrected output.

        Returns
        -------
        Self
            Validated verdict.

        Raises
        ------
        ValueError
            If pass/fail state disagrees with corrected_result or error issues.
        """

        error_issues = [issue for issue in self.issues if issue.severity == "error"]

        if self.passed:
            if self.corrected_result is not None:
                raise ValueError(
                    "corrected_result must be null when validation passed is true."
                )

            if error_issues:
                raise ValueError(
                    "A passing validation verdict must not contain error issues."
                )
        else:
            if self.corrected_result is None:
                raise ValueError(
                    "corrected_result is required when validation passed is false."
                )

            if not error_issues:
                raise ValueError(
                    "A failing validation verdict must include at least one error issue."
                )

        return self


# Schemas for SFI candidate registry.
class SFIDedupContextItem(BaseSchema):
    """One context-bearing SFI candidate visible in a compact source window."""

    canonical_statement_value: Optional[str] = Field(
        default=None,
        description="Canonical controlled value for the context item, when configured.",
    )
    description: str = Field(description="Context item description.", min_length=1)
    normalized_statement_type: NormalizedStatementType = Field(
        description="Global normalized class for the context item."
    )
    source_text: str = Field(
        description="Source-visible text supporting the context item.", min_length=1
    )
    statement_type: str = Field(
        description="Canonical source-facing statement type for the context item.",
        min_length=1,
    )

    @field_validator("description", "source_text", "statement_type", mode="before")
    @classmethod
    def clean_required_strings(cls, v: str) -> str:
        """Strip and require non-empty context-item strings.

        Parameters
        ----------
        v
            Raw context-item string.

        Returns
        -------
        str
            Cleaned non-empty string.
        """

        return strip_and_require_non_empty_str(v)


class SFIDedupContextWindow(BaseSchema):
    """Compact source-window context shared by dedup review candidates."""

    boundary_markers: list[str] = Field(
        default_factory=list,
        description="Distinct source continuation or boundary markers for the window.",
    )
    context_items: list[SFIDedupContextItem] = Field(
        default_factory=list,
        description="Configured context-bearing SFI items visible in this window.",
    )
    page_indexes: list[int] = Field(
        default_factory=list,
        description="Sorted unique zero-based source page indexes represented here.",
    )
    section_labels: list[str] = Field(
        default_factory=list,
        description="Nearest visible section labels retained as fallible context.",
    )
    segment_kind: Literal["block", "table"] = Field(
        description="Extraction-window segment kind."
    )
    source_text_excerpt: str = Field(
        default="",
        description="Short source-text excerpt retained for local semantic context.",
    )
    window_index: int = Field(description="Zero-based extraction-window index.", ge=0)

    @field_validator("boundary_markers", "section_labels")
    @classmethod
    def clean_string_lists(cls, v: list[str]) -> list[str]:
        """Clean and de-duplicate compact context-window string lists.

        Parameters
        ----------
        v
            Raw string values.

        Returns
        -------
        list[str]
            Cleaned unique strings in stable order.
        """

        return unique_clean_strings(v)

    @field_validator("page_indexes")
    @classmethod
    def clean_page_indexes(cls, v: list[int]) -> list[int]:
        """Validate compact source page indexes.

        Parameters
        ----------
        v
            Raw page indexes.

        Returns
        -------
        list[int]
            Sorted unique non-negative page indexes.

        Raises
        ------
        ValueError
            If any page index is negative.
        """

        cleaned = sorted(set(int(index) for index in v or []))

        if any(index < 0 for index in cleaned):
            raise ValueError("page_indexes must be non-negative")

        return cleaned


class SFIRegistryArtifact(BaseSchema):
    """Persisted global SFI candidate registry artifact."""

    candidates: list[SFIRegistryCandidate] = Field(default_factory=list)
    country: str = Field(description="KG config metadata country.")
    dedup_context_windows: list[SFIDedupContextWindow] = Field(
        description=(
            "Compact extraction-window context pool shared by later SFI dedup review "
            "requests. Each source window appears at most once."
        ),
    )
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

    @model_validator(mode="after")
    def validate_dedup_context_window_indexes(self) -> Self:
        """Validate unique dedup context-window indexes.

        Returns
        -------
        Self
            Validated registry artifact.

        Raises
        ------
        ValueError
            If the shared context-window pool contains duplicate window indexes.
        """

        window_indexes = [window.window_index for window in self.dedup_context_windows]

        if len(window_indexes) != len(set(window_indexes)):
            raise ValueError(
                "dedup_context_windows must have unique window_index values"
            )

        return self


class SFIRegistryCandidate(BaseSchema):
    """Document-level wrapper around one window-local SFI candidate.

    The registry candidate is a temporary review handle for merge/dedup stages. It is
    not a final StandardsFrameworkItem and must not be used as a final KG ID.
    """

    applicable_code_type: Optional[str] = Field(
        default=None,
        description=(
            "Configured code type applicable to this candidate from either its "
            "resolved source code or statement-type policy."
        ),
    )
    candidate_payload: SFICandidate = Field(
        description="Original window-local SFI candidate payload."
    )
    canonical_statement_value: Optional[str] = Field(
        default=None,
        description="Canonical controlled statement value, when configured.",
    )
    canonical_statement_value_key: Optional[str] = Field(
        default=None,
        description="Normalized key for canonical_statement_value, when configured.",
    )
    code_bucket_key: Optional[str] = Field(
        default=None,
        description=(
            "Configured code scope + statement_type + normalized_statement_code "
            "bucket key, when coded."
        ),
    )
    code_scope_key: Optional[str] = Field(
        description=(
            "Deterministic ordered key for configured code-scope values, or null when "
            "the applicable code type is document-global or source scope is unresolved."
        )
    )
    code_scope_values: dict[str, str] = Field(
        description=(
            "Canonical controlled values for the configured statement types that "
            "scope this candidate's applicable code type."
        )
    )
    code_source_anchors: list[SFISourceAnchor] = Field(
        default_factory=list,
        description="Exact code anchors preserved from the checker-approved candidate.",
    )
    confidence: float = Field(
        description="Original candidate confidence.", ge=0.0, le=1.0
    )
    description: str = Field(
        description="Original candidate description.", min_length=1
    )
    description_source_anchors: list[SFISourceAnchor] = Field(
        description=(
            "Exact description anchors preserved from the checker-approved candidate."
        ),
        min_length=1,
    )
    identity_scope_key: Optional[str] = Field(
        description=(
            "Deterministic ordered key for configured semantic identity-scope values, "
            "or null when the candidate statement type has no configured identity "
            "scope."
        )
    )
    identity_scope_values: dict[str, str] = Field(
        description=(
            "Canonical controlled values for the configured semantic identity scope "
            "of this candidate statement type."
        )
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
    resolved_code_type: Optional[str] = Field(
        description=(
            "Configured code-pattern key authoritatively resolved for this candidate, "
            "or null when the candidate is uncoded."
        )
    )
    source_context_key: str = Field(
        description=(
            "Deterministic source-derived context key used as a no-code duplicate "
            "bucketing fallback and as later review evidence."
        ),
        min_length=1,
    )
    source_context_labels: list[str] = Field(
        description=(
            "Human-readable source-derived context labels, such as section path, "
            "source segment, table header, or table row context."
        ),
        min_length=1,
    )
    source_occurrence_location_key: str = Field(
        description=(
            "Deterministic type-independent key for the underlying source occurrence "
            "location. It excludes extraction-window identity and candidate "
            "classification so overlapping windows and classification variants can "
            "be compared safely."
        ),
        min_length=1,
    )
    source_segment_ids: list[str] = Field(
        description="ExtractionWindow.source_segment_ids for source recovery.",
        min_length=1,
    )
    source_text: str = Field(
        description="Original candidate source_text.", min_length=1
    )
    source_text_bucket_key: str = Field(
        description=(
            "Configured code scope, semantic identity scope, or source-context "
            "fallback + statement_type + normalized source_text bucket key."
        )
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
        description=(
            "Configured code scope, semantic identity scope, or source-context "
            "fallback + statement_type + normalized description bucket key."
        )
    )
    window_id: str = Field(description="ExtractionWindow.window_id.")
    window_index: int = Field(description="ExtractionWindow.window_index.", ge=0)

    @field_validator("code_scope_key", "identity_scope_key", mode="before")
    @classmethod
    def clean_code_scope_key(cls, v: Optional[str]) -> Optional[str]:
        """Strip an optional deterministic code-scope key.

        Parameters
        ----------
        v
            Raw optional code-scope key.

        Returns
        -------
        Optional[str]
            Stripped key, or `None` when blank.
        """

        if v is None:
            return None

        value = str(v).strip()
        return value or None

    @field_validator("applicable_code_type", "resolved_code_type", mode="before")
    @classmethod
    def clean_optional_code_type(cls, v: Optional[str]) -> Optional[str]:
        """Strip an optional applicable or resolved code type.

        Parameters
        ----------
        v
            Raw optional code type.

        Returns
        -------
        Optional[str]
            Stripped configured code type, or `None` when blank.
        """

        if v is None:
            return None

        value = str(v).strip()
        return value or None

    @field_validator("source_occurrence_location_key", mode="before")
    @classmethod
    def clean_source_occurrence_location_key(cls, v: str) -> str:
        """Strip and require the type-independent source-occurrence location key.

        Parameters
        ----------
        v
            Raw source-occurrence location key.

        Returns
        -------
        str
            Cleaned non-empty key.
        """

        return strip_and_require_non_empty_str(v)

    @field_validator("code_scope_values")
    @classmethod
    def validate_code_scope_values(cls, v: dict[str, str]) -> dict[str, str]:
        """Clean candidate code-scope values.

        Parameters
        ----------
        v
            Raw code-scope mapping.

        Returns
        -------
        dict[str, str]
            Cleaned ordered code-scope mapping.
        """

        return clean_scope_values(field_name="code_scope_values", values=v)

    @field_validator("identity_scope_values")
    @classmethod
    def validate_identity_scope_values(cls, v: dict[str, str]) -> dict[str, str]:
        """Clean candidate semantic identity-scope values.

        Parameters
        ----------
        v
            Raw identity-scope mapping.

        Returns
        -------
        dict[str, str]
            Cleaned ordered identity-scope mapping.
        """

        return clean_scope_values(field_name="identity_scope_values", values=v)

    @model_validator(mode="after")
    def validate_code_contract(self) -> Self:
        """Validate source-code, applicable-type, and scope consistency.

        Returns
        -------
        Self
            Validated registry candidate.

        Raises
        ------
        ValueError
            If source-code fields disagree, a resolved type differs from the applicable
            type, scope fields are only partially present, or scope exists without an
            applicable code type.
        """

        code_field_presence = {
            self.statement_code is not None,
            self.normalized_statement_code is not None,
            self.resolved_code_type is not None,
        }

        if len(code_field_presence) != 1:
            raise ValueError(
                "statement_code, normalized_statement_code, and resolved_code_type "
                "must either all be present or all be null."
            )

        if (
            self.resolved_code_type is not None
            and self.applicable_code_type != self.resolved_code_type
        ):
            raise ValueError(
                "applicable_code_type must equal resolved_code_type when a source "
                "code is present."
            )

        if bool(self.code_scope_key) != bool(self.code_scope_values):
            raise ValueError(
                "code_scope_key and code_scope_values must either both be present or "
                "both be empty."
            )

        if self.code_scope_key is not None and self.applicable_code_type is None:
            raise ValueError(
                "A candidate with configured code scope must define "
                "applicable_code_type."
            )

        return self

    @model_validator(mode="after")
    def validate_identity_scope_contract(self) -> Self:
        """Validate candidate semantic identity-scope field consistency.

        Returns
        -------
        Self
            Validated registry candidate.

        Raises
        ------
        ValueError
            If the identity-scope key and values are only partially populated.
        """

        if bool(self.identity_scope_key) != bool(self.identity_scope_values):
            raise ValueError(
                "identity_scope_key and identity_scope_values must either both be "
                "present or both be empty."
            )

        return self

    @model_validator(mode="after")
    def validate_source_anchor_contract(self) -> Self:
        """Validate registry anchors against the preserved extraction candidate.

        Returns
        -------
        Self
            Validated registry candidate.

        Raises
        ------
        ValueError
            If registry anchor fields differ from the original candidate payload.
        """

        if self.description_source_anchors != (
            self.candidate_payload.description_source_anchors
        ):
            raise ValueError(
                "description_source_anchors must exactly preserve candidate_payload."
            )

        if self.code_source_anchors != self.candidate_payload.code_source_anchors:
            raise ValueError(
                "code_source_anchors must exactly preserve candidate_payload."
            )

        return self


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
    canonical_code_selection_reason: Optional[str] = Field(
        default=None,
        description=(
            "Source-grounded reason for choosing canonical_code_source_candidate_id "
            "when a merge group contains multiple distinct normalized source codes."
        ),
    )
    canonical_code_source_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Candidate whose existing source-backed code should become canonical when "
            "a merge decision combines multiple distinct normalized source codes."
        ),
    )
    canonical_type_selection_reason: Optional[str] = Field(
        default=None,
        description=(
            "Source-grounded reason for choosing canonical_type_source_candidate_id "
            "when a merge group contains multiple statement-type pairs."
        ),
    )
    canonical_type_source_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Candidate whose existing statement_type and normalized_statement_type "
            "should become canonical for a mixed-type merge."
        ),
    )
    confidence: float = Field(
        default=0.5, description="LLM confidence in the decision.", ge=0.0, le=1.0
    )
    decision: SFIDedupDecision = Field(description="Closed dedup decision label.")
    reason: str = Field(
        description="Short source-grounded decision reason.", min_length=1
    )
    representative_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Existing candidate selected as the representative source-facing form "
            "for a merge decision."
        ),
    )

    @field_validator(
        "canonical_code_selection_reason",
        "canonical_code_source_candidate_id",
        "canonical_type_selection_reason",
        "canonical_type_source_candidate_id",
        "representative_candidate_id",
        mode="before",
    )
    @classmethod
    def clean_optional_selection_strings(cls, v: Optional[str]) -> Optional[str]:
        """Strip optional candidate-selection fields.

        Parameters
        ----------
        v
            Raw optional selection value.

        Returns
        -------
        Optional[str]
            Stripped value, or `None` when blank.
        """

        if v is None:
            return None

        value = str(v).strip()
        return value or None

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

    @field_validator("reason", mode="before")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        """Strip and require a non-empty source-grounded reason.

        Parameters
        ----------
        v
            Raw decision reason.

        Returns
        -------
        str
            Cleaned non-empty reason.
        """

        return strip_and_require_non_empty_str(v)


class SFIDedupReviewCandidate(BaseSchema):
    """Compact registry-candidate view for one bounded dedup review set."""

    applicable_code_type: Optional[str] = Field(
        default=None,
        description="Configured code type applicable to this candidate.",
    )
    canonical_statement_value: Optional[str] = Field(
        default=None,
        description="Canonical controlled statement value, when configured.",
    )
    code_scope_key: Optional[str] = Field(
        description="Deterministic configured code-scope key, when applicable."
    )
    code_scope_values: dict[str, str] = Field(
        description="Canonical configured code-scope values, when applicable."
    )
    code_source_anchors: list[SFISourceAnchor] = Field(
        default_factory=list,
        description="Exact source anchors supporting the candidate code.",
    )
    context_window_indexes: list[int] = Field(
        description="Shared request-level context windows relevant to this candidate.",
        min_length=1,
    )
    description: str = Field(description="Candidate description.", min_length=1)
    description_source_anchors: list[SFISourceAnchor] = Field(
        description="Exact source anchors composing the candidate description.",
        min_length=1,
    )
    identity_scope_key: Optional[str] = Field(
        description="Deterministic configured semantic identity-scope key, when any."
    )
    identity_scope_values: dict[str, str] = Field(
        description="Canonical configured semantic identity-scope values, when any."
    )
    language: LanguageField = Field(description="Candidate language tag.")
    normalized_statement_code: Optional[str] = Field(
        default=None, description="Registry-normalized official code, when present."
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description="Candidate normalized statement type."
    )
    registry_candidate_id: str = Field(description="Temporary registry candidate ID.")
    resolved_code_type: Optional[str] = Field(
        default=None,
        description="Configured code type resolved from the candidate's source code.",
    )
    source_occurrence_location_key: str = Field(
        description="Exact description-anchor occurrence key from the registry.",
        min_length=1,
    )
    source_text: str = Field(description="Source-visible evidence text.", min_length=1)
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
    window_index: int = Field(description="Source extraction window index.", ge=0)

    @field_validator(
        "context_window_indexes", "table_header_indexes", "table_row_indexes"
    )
    @classmethod
    def clean_indexes(cls, v: list[int]) -> list[int]:
        """Validate non-negative candidate index lists.

        Parameters
        ----------
        v
            Raw indexes.

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
            raise ValueError("candidate index lists must be non-negative")

        return cleaned

    @field_validator(
        "applicable_code_type",
        "code_scope_key",
        "identity_scope_key",
        "resolved_code_type",
        mode="before",
    )
    @classmethod
    def clean_optional_code_fields(cls, v: Optional[str]) -> Optional[str]:
        """Strip optional review-candidate code metadata.

        Parameters
        ----------
        v
            Raw optional code field.

        Returns
        -------
        Optional[str]
            Stripped value, or `None` when blank.
        """

        if v is None:
            return None

        value = str(v).strip()
        return value or None

    @field_validator(
        "description",
        "registry_candidate_id",
        "source_occurrence_location_key",
        "source_text",
        "statement_type",
        mode="before",
    )
    @classmethod
    def clean_required_strings(cls, v: str) -> str:
        """Strip required review-candidate strings.

        Parameters
        ----------
        v
            Raw required string.

        Returns
        -------
        str
            Cleaned non-empty string.
        """

        return strip_and_require_non_empty_str(v)

    @field_validator("code_scope_values")
    @classmethod
    def validate_code_scope_values(cls, v: dict[str, str]) -> dict[str, str]:
        """Clean review-candidate code-scope values.

        Parameters
        ----------
        v
            Raw code-scope mapping.

        Returns
        -------
        dict[str, str]
            Cleaned ordered code-scope mapping.
        """

        return clean_scope_values(field_name="code_scope_values", values=v)

    @field_validator("identity_scope_values")
    @classmethod
    def validate_identity_scope_values(cls, v: dict[str, str]) -> dict[str, str]:
        """Clean review-candidate semantic identity-scope values.

        Parameters
        ----------
        v
            Raw identity-scope mapping.

        Returns
        -------
        dict[str, str]
            Cleaned ordered identity-scope mapping.
        """

        return clean_scope_values(field_name="identity_scope_values", values=v)

    @model_validator(mode="after")
    def validate_code_scope_contract(self) -> Self:
        """Validate review-candidate code type and scope metadata.

        Returns
        -------
        Self
            Validated review candidate.

        Raises
        ------
        ValueError
            If code fields disagree, scope fields are partially populated, or scope
            exists without an applicable code type.
        """

        code_field_presence = {
            self.statement_code is not None,
            self.normalized_statement_code is not None,
            self.resolved_code_type is not None,
        }

        if len(code_field_presence) != 1:
            raise ValueError(
                "statement_code, normalized_statement_code, and resolved_code_type "
                "must either all be present or all be null."
            )

        if (
            self.resolved_code_type is not None
            and self.applicable_code_type != self.resolved_code_type
        ):
            raise ValueError(
                "applicable_code_type must equal resolved_code_type when a source "
                "code is present."
            )

        if bool(self.code_scope_key) != bool(self.code_scope_values):
            raise ValueError(
                "code_scope_key and code_scope_values must either both be present or "
                "both be empty."
            )

        if self.code_scope_key is not None and self.applicable_code_type is None:
            raise ValueError(
                "A review candidate with configured scope must define "
                "applicable_code_type."
            )

        return self

    @model_validator(mode="after")
    def validate_identity_scope_contract(self) -> Self:
        """Validate review-candidate semantic identity-scope consistency.

        Returns
        -------
        Self
            Validated review candidate.

        Raises
        ------
        ValueError
            If identity-scope key and values are only partially populated.
        """

        if bool(self.identity_scope_key) != bool(self.identity_scope_values):
            raise ValueError(
                "identity_scope_key and identity_scope_values must either both be "
                "present or both be empty."
            )

        return self

    @model_validator(mode="after")
    def validate_source_anchor_contract(self) -> Self:
        """Validate review-candidate code and source-anchor consistency.

        Returns
        -------
        Self
            Validated review candidate.

        Raises
        ------
        ValueError
            If code anchors disagree with statement_code or anchors are duplicated.
        """

        _validate_unique_source_anchors(
            anchors=self.description_source_anchors,
            field_name="description_source_anchors",
        )
        _validate_unique_source_anchors(
            anchors=self.code_source_anchors, field_name="code_source_anchors"
        )

        if self.statement_code is None and self.code_source_anchors:
            raise ValueError(
                "code_source_anchors must be empty when statement_code is null."
            )

        if self.statement_code is not None and not self.code_source_anchors:
            raise ValueError(
                "A non-null statement_code requires at least one code_source_anchor."
            )

        return self


class SFIDedupReviewRequest(BaseSchema):
    """Persisted prompt payload for one bounded SFI dedup review set."""

    bilingual_pair_policy: Optional[str] = Field(
        default=None, description="Optional bilingual pairing policy from KG config."
    )
    candidates: list[SFIDedupReviewCandidate] = Field(
        description="Bounded candidate records to review together.", min_length=2
    )
    context_windows: list[SFIDedupContextWindow] = Field(
        description=(
            "Shared compact source-window context. Candidates reference these windows "
            "through context_window_indexes."
        ),
        min_length=1,
    )
    review_set_id: str = Field(description="Deterministic review-set ID.")
    review_signals: list[SFIDedupReviewSignal] = Field(
        description=(
            "Explicit candidate-subset retrieval signals that caused the review set "
            "to be constructed. Signals are evidence only, not merge rules."
        ),
        min_length=1,
    )
    sfi_dedup_instructions: str = Field(
        description="Curriculum-specific deduplication instructions.",
        min_length=1,
    )

    @field_validator("review_set_id", "sfi_dedup_instructions", mode="before")
    @classmethod
    def clean_required_request_strings(cls, v: str) -> str:
        """Strip and require non-empty request strings.

        Parameters
        ----------
        v
            Raw required request string.

        Returns
        -------
        str
            Cleaned non-empty request string.
        """

        return strip_and_require_non_empty_str(v)

    @model_validator(mode="after")
    def validate_request_references(self) -> Self:
        """Validate candidate, context-window, and review-signal references.

        Returns
        -------
        Self
            Validated request.

        Raises
        ------
        ValueError
            If candidate IDs or context-window indexes are duplicated, or if any
            candidate or signal references data outside the request.
        """

        candidate_ids = [
            candidate.registry_candidate_id for candidate in self.candidates
        ]

        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Dedup review candidate IDs must be unique")

        context_window_indexes = [
            window.window_index for window in self.context_windows
        ]

        if len(context_window_indexes) != len(set(context_window_indexes)):
            raise ValueError("Dedup context-window indexes must be unique")

        known_candidate_ids = set(candidate_ids)
        known_window_indexes = set(context_window_indexes)

        for candidate in self.candidates:
            unknown_window_indexes = sorted(
                set(candidate.context_window_indexes) - known_window_indexes
            )

            if unknown_window_indexes:
                raise ValueError(
                    f"Candidate {candidate.registry_candidate_id!r} references unknown "
                    f"context windows: {unknown_window_indexes}"
                )

            if candidate.window_index not in candidate.context_window_indexes:
                raise ValueError(
                    f"Candidate {candidate.registry_candidate_id!r} must reference its "
                    f"own source window_index {candidate.window_index}."
                )

        for signal in self.review_signals:
            unknown_candidate_ids = sorted(
                set(signal.candidate_ids) - known_candidate_ids
            )

            if unknown_candidate_ids:
                raise ValueError(
                    f"Review signal {signal.signal_type!r} references unknown "
                    f"candidate IDs: {unknown_candidate_ids}"
                )

        return self


class SFIDedupReviewResponse(BaseSchema):
    """Structured LLM output for one bounded SFI dedup review set."""

    decision_groups: list[SFIDedupDecisionGroup] = Field(
        description="Decision groups covering every review candidate exactly once.",
        min_length=1,
    )
    review_set_id: str = Field(description="Review-set ID copied from the request.")

    @field_validator("review_set_id", mode="before")
    @classmethod
    def clean_review_set_id(cls, v: str) -> str:
        """Strip and require the response review-set ID.

        Parameters
        ----------
        v
            Raw review-set ID.

        Returns
        -------
        str
            Cleaned non-empty review-set ID.
        """

        return strip_and_require_non_empty_str(v)


class SFIDedupReviewSignal(BaseSchema):
    """One candidate-subset retrieval signal for a bounded dedup review set."""

    candidate_ids: list[str] = Field(
        description="Review candidate IDs to which this signal applies.", min_length=2
    )
    signal_type: str = Field(description="Short general signal category.", min_length=1)
    summary: str = Field(
        description="Human-readable explanation of the retrieval signal.", min_length=1
    )
    value: Optional[str] = Field(
        default=None,
        description="Optional human-readable value associated with the signal.",
    )

    @field_validator("candidate_ids")
    @classmethod
    def clean_candidate_ids(cls, v: list[str]) -> list[str]:
        """Clean and validate signal candidate IDs.

        Parameters
        ----------
        v
            Raw candidate IDs.

        Returns
        -------
        list[str]
            Cleaned unique candidate IDs.

        Raises
        ------
        ValueError
            If fewer than two unique candidate IDs remain.
        """

        cleaned = unique_clean_strings(v)

        if len(cleaned) < 2:
            raise ValueError("review signals require at least two candidate_ids")

        return cleaned

    @field_validator("signal_type", "summary", mode="before")
    @classmethod
    def clean_required_strings(cls, v: str) -> str:
        """Strip and require non-empty review-signal strings.

        Parameters
        ----------
        v
            Raw review-signal string.

        Returns
        -------
        str
            Cleaned non-empty string.
        """

        return strip_and_require_non_empty_str(v)

    @field_validator("value", mode="before")
    @classmethod
    def clean_optional_value(cls, v: Optional[str]) -> Optional[str]:
        """Strip an optional human-readable review-signal value.

        Parameters
        ----------
        v
            Raw optional value.

        Returns
        -------
        Optional[str]
            Cleaned value, or `None` when blank.
        """

        if v is None:
            return None

        value = str(v).strip()
        return value or None


class SFIDedupValidationIssue(BaseSchema):
    """One issue found by the second-stage SFI dedup validation LLM."""

    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Registry candidate IDs associated with the validation issue.",
    )
    issue_type: str = Field(
        description="Short general issue category, such as over_merge or scope_error.",
        min_length=1,
    )
    message: str = Field(
        description="Specific source-grounded description of the issue.", min_length=1
    )
    severity: Literal["error", "warning"] = Field(
        description="Whether the issue requires correction or is advisory only."
    )

    @field_validator("candidate_ids")
    @classmethod
    def clean_candidate_ids(cls, v: list[str]) -> list[str]:
        """Clean and validate issue candidate IDs.

        Parameters
        ----------
        v
            Raw registry candidate IDs.

        Returns
        -------
        list[str]
            Cleaned unique candidate IDs.

        Raises
        ------
        ValueError
            If candidate IDs are blank or duplicated.
        """

        cleaned = unique_clean_strings(v)

        if len(cleaned) != len(v):
            raise ValueError("candidate_ids must be unique and non-empty")

        return cleaned

    @field_validator("issue_type", "message", mode="before")
    @classmethod
    def clean_required_issue_strings(cls, v: str) -> str:
        """Strip and require non-empty issue text fields.

        Parameters
        ----------
        v
            Raw issue string.

        Returns
        -------
        str
            Stripped issue string.

        Raises
        ------
        ValueError
            If the issue string is blank.
        """

        value = str(v or "").strip()

        if not value:
            raise ValueError("SFI dedup validation issue text is required.")

        return value


class SFIDedupValidationVerdict(BaseSchema):
    """Second-stage LLM verdict for one draft SFI dedup review response."""

    corrected_response: Optional[SFIDedupReviewResponse] = Field(
        default=None,
        description=(
            "Complete corrected dedup response when passed is false; null when the "
            "draft is accepted unchanged."
        ),
    )
    issues: list[SFIDedupValidationIssue] = Field(
        default_factory=list,
        description="Source-grounded validation issues found in the draft response.",
    )
    passed: bool = Field(
        description="True when the draft response requires no material correction."
    )
    rationale: str = Field(
        description="Concise overall assessment of the draft dedup response.",
        min_length=20,
    )
    review_set_id: str = Field(
        description="Review-set ID copied from the request.", min_length=1
    )

    @field_validator("rationale", "review_set_id", mode="before")
    @classmethod
    def clean_required_verdict_strings(cls, v: str) -> str:
        """Strip and require non-empty verdict text fields.

        Parameters
        ----------
        v
            Raw verdict string.

        Returns
        -------
        str
            Stripped verdict string.

        Raises
        ------
        ValueError
            If the verdict string is blank.
        """

        value = str(v or "").strip()

        if not value:
            raise ValueError("SFI dedup validation verdict text is required.")

        return value

    @model_validator(mode="after")
    def validate_verdict_contract(self) -> Self:
        """Validate pass/fail agreement with issues and corrected output.

        Returns
        -------
        Self
            Validated verdict.

        Raises
        ------
        ValueError
            If pass/fail state disagrees with corrected_response or error issues.
        """

        error_issues = [issue for issue in self.issues if issue.severity == "error"]

        if self.passed:
            if self.corrected_response is not None:
                raise ValueError(
                    "corrected_response must be null when validation passed is true."
                )

            if error_issues:
                raise ValueError(
                    "A passing validation verdict must not contain error issues."
                )
        else:
            if self.corrected_response is None:
                raise ValueError(
                    "corrected_response is required when validation passed is false."
                )

            if not error_issues:
                raise ValueError(
                    "A failing validation verdict must include at least one error issue."
                )

        return self


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
    canonical_code_type: Optional[str] = Field(
        default=None,
        description="Resolved configured code type for the logical merged SFI.",
    )
    canonical_code_source_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Registry candidate whose source-backed code was selected as canonical "
            "for a mixed-code merge group."
        ),
    )
    canonical_normalized_statement_code: Optional[str] = Field(
        default=None,
        description="Resolved normalized code for the logical merged SFI, when coded.",
    )
    canonical_statement_code: Optional[str] = Field(
        default=None,
        description="Resolved source-backed code for the logical merged SFI, when coded.",
    )
    canonical_normalized_statement_type: Optional[NormalizedStatementType] = Field(
        default=None,
        description="Resolved normalized statement type for a mintable merge group.",
    )
    canonical_statement_type: Optional[str] = Field(
        default=None,
        description="Resolved source-facing statement type for a mintable merge group.",
    )
    canonical_type_selection_reason: Optional[str] = Field(
        default=None,
        description=(
            "Source-grounded reason for the canonical type selection in a mixed-type "
            "merge group."
        ),
    )
    canonical_type_source_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Registry candidate whose existing type pair was selected for a "
            "mixed-type merge group."
        ),
    )
    canonical_statement_value: Optional[str] = Field(
        default=None,
        description="Shared canonical controlled statement value, when unique.",
    )
    canonical_statement_value_key: Optional[str] = Field(
        default=None,
        description="Shared normalized canonical value key, when unique.",
    )
    canonical_statement_value_keys: list[str] = Field(
        default_factory=list,
        description="All normalized canonical value keys in the group.",
    )
    canonical_statement_values: list[str] = Field(
        default_factory=list,
        description="All canonical controlled statement values in the group.",
    )
    code_resolution_method: SFICodeResolutionMethod = Field(
        description="Deterministic method used to resolve the group's canonical code."
    )
    code_resolution_reason: str = Field(
        description="Source-grounded or deterministic canonical-code resolution reason.",
        min_length=1,
    )
    code_scope_key: Optional[str] = Field(
        description=(
            "Shared deterministic code-scope key when all candidates have one common "
            "configured code scope; otherwise null."
        )
    )
    code_scope_keys: list[str] = Field(
        description="All non-empty deterministic code-scope keys in the group."
    )
    code_scope_values: dict[str, str] = Field(
        description=(
            "Shared canonical configured code-scope values when the group has one "
            "common non-empty scope; otherwise empty."
        )
    )
    confidence_max: float = Field(
        description="Maximum candidate confidence in this group.", ge=0.0, le=1.0
    )
    confidence_min: float = Field(
        description="Minimum candidate confidence in this group.", ge=0.0, le=1.0
    )
    identity_scope_key: Optional[str] = Field(
        description=(
            "Shared deterministic semantic identity-scope key when all candidates "
            "have one common configured identity scope; otherwise null."
        )
    )
    identity_scope_keys: list[str] = Field(
        description="All non-empty semantic identity-scope keys in the group."
    )
    identity_scope_values: dict[str, str] = Field(
        description=(
            "Shared canonical semantic identity-scope values when the group has one "
            "common non-empty scope; otherwise empty."
        )
    )
    llm_decision: Optional[SFIDedupDecision] = Field(
        default=None, description="Original LLM decision, when reviewed by the LLM."
    )
    llm_review_set_id: Optional[str] = Field(
        default=None, description="Review-set ID that produced this group, when any."
    )
    merge_decision: SFIMergeDecision = Field(description="SFI merge outcome.")
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
    representative_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Registry candidate selected as the representative source-facing form "
            "for a mintable merge group."
        ),
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
        "canonical_code_source_candidate_id",
        "canonical_code_type",
        "canonical_normalized_statement_code",
        "canonical_normalized_statement_type",
        "canonical_statement_code",
        "canonical_statement_type",
        "canonical_type_selection_reason",
        "canonical_type_source_candidate_id",
        "code_scope_key",
        "identity_scope_key",
        "normalized_statement_code",
        "representative_candidate_id",
        "statement_code",
        mode="before",
    )
    @classmethod
    def clean_optional_resolution_strings(cls, v: Optional[str]) -> Optional[str]:
        """Strip optional code-resolution and representative-selection strings.

        Parameters
        ----------
        v
            Raw optional string.

        Returns
        -------
        Optional[str]
            Stripped string, or `None` when blank.
        """

        if v is None:
            return None

        value = str(v).strip()
        return value or None

    @field_validator("code_resolution_reason", mode="before")
    @classmethod
    def clean_code_resolution_reason(cls, v: str) -> str:
        """Strip and require a non-empty code-resolution reason.

        Parameters
        ----------
        v
            Raw resolution reason.

        Returns
        -------
        str
            Cleaned non-empty reason.
        """

        return strip_and_require_non_empty_str(v)

    @field_validator(
        "audit_flags",
        "audit_notes",
        "audit_peer_merge_group_ids",
        "candidate_descriptions",
        "candidate_source_texts",
        "canonical_statement_value_keys",
        "canonical_statement_values",
        "code_scope_keys",
        "identity_scope_keys",
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

    @field_validator("code_scope_values")
    @classmethod
    def validate_code_scope_values(cls, v: dict[str, str]) -> dict[str, str]:
        """Clean shared merge-group code-scope values.

        Parameters
        ----------
        v
            Raw shared code-scope mapping.

        Returns
        -------
        dict[str, str]
            Cleaned ordered code-scope mapping.
        """

        return clean_scope_values(field_name="code_scope_values", values=v)

    @field_validator("identity_scope_values")
    @classmethod
    def validate_identity_scope_values(cls, v: dict[str, str]) -> dict[str, str]:
        """Clean shared merge-group semantic identity-scope values.

        Parameters
        ----------
        v
            Raw shared identity-scope mapping.

        Returns
        -------
        dict[str, str]
            Cleaned ordered identity-scope mapping.
        """

        return clean_scope_values(field_name="identity_scope_values", values=v)

    @model_validator(mode="after")
    def validate_code_scope_contract(self) -> Self:
        """Validate aggregate code-scope fields against candidate source references.

        Returns
        -------
        Self
            Validated merge group.

        Raises
        ------
        ValueError
            If aggregate scope fields disagree with source references or a mintable
            coded group contains candidates with incompatible code types or scopes.
        """

        self._validate_code_scope_keys_consistency()
        self._validate_code_scope_values_consistency()
        self._validate_mintable_code_scope_contract()

        if (
            self.canonical_normalized_statement_code is None
            and self.canonical_code_type
        ):
            raise ValueError(
                "canonical_code_type must be null when no canonical normalized code "
                "is resolved."
            )

        return self

    def _validate_code_scope_keys_consistency(self) -> None:
        """Validate that aggregate code-scope keys mirror source references.

        Raises
        ------
        ValueError
            If `code_scope_keys` does not equal the non-empty code-scope keys preserved
            in `candidate_source_refs`.
        """

        source_scope_keys = {
            str(source_ref.get("code_scope_key") or "").strip()
            for source_ref in self.candidate_source_refs
            if str(source_ref.get("code_scope_key") or "").strip()
        }

        if set(self.code_scope_keys) != source_scope_keys:
            raise ValueError(
                "code_scope_keys must equal the non-empty code-scope keys preserved "
                "in candidate_source_refs."
            )

    def _validate_code_scope_values_consistency(self) -> None:
        """Validate aggregate code-scope values against the resolved scope key.

        Raises
        ------
        ValueError
            If code-scope values are set without a scope key, or if they do not equal
            the single source-backed value mapping for `code_scope_key`.
        """

        if self.code_scope_key is None:
            if self.code_scope_values:
                raise ValueError(
                    "code_scope_values must be empty when code_scope_key is null."
                )

            return

        matching_scope_values = {
            tuple(
                (str(key).strip(), str(value).strip())
                for key, value in (source_ref.get("code_scope_values") or {}).items()
            )
            for source_ref in self.candidate_source_refs
            if str(source_ref.get("code_scope_key") or "").strip()
            == self.code_scope_key
        }

        if len(matching_scope_values) != 1:
            raise ValueError(
                "code_scope_values must have one source-backed value mapping for "
                "the resolved code_scope_key."
            )

        expected_scope_values = dict(next(iter(matching_scope_values)))

        if self.code_scope_values != expected_scope_values:
            raise ValueError(
                "code_scope_values must equal the source-backed values for "
                "code_scope_key."
            )

    def _validate_mintable_code_scope_contract(self) -> None:
        """Validate canonical code policy for a mintable coded merge group.

        Same-type groups retain strict code-type and code-scope equality. Mixed-type
        groups may preserve classification-derived code-policy differences, but the
        aggregate canonical code and scope must come from one source-backed coded
        candidate and all shared scope dimensions must remain non-contradictory.

        Raises
        ------
        ValueError
            If canonical code metadata is missing, a same-type group has incompatible
            code policy, a mixed-type group has contradictory shared scope values, or
            the aggregate canonical code scope is not source-backed.
        """

        if (
            self.merge_decision not in {"merged", "singleton"}
            or self.canonical_normalized_statement_code is None
        ):
            return

        if self.canonical_code_type is None:
            raise ValueError("Mintable coded merge groups require canonical_code_type.")

        applicable_code_types = {
            str(source_ref.get("applicable_code_type") or "").strip()
            for source_ref in self.candidate_source_refs
        }
        observed_type_pairs = {
            (
                str(source_ref.get("statement_type") or "").strip(),
                str(source_ref.get("normalized_statement_type") or "").strip(),
            )
            for source_ref in self.candidate_source_refs
        }
        source_scope_signatures = {
            str(source_ref.get("code_scope_key") or "").strip()
            for source_ref in self.candidate_source_refs
        }
        expected_scope_key = (
            next(iter(source_scope_signatures)) or None
            if len(source_scope_signatures) == 1
            else None
        )
        strict_scope_compatible = bool(
            applicable_code_types == {self.canonical_code_type}
            and len(source_scope_signatures) == 1
            and self.code_scope_key == expected_scope_key
        )

        if strict_scope_compatible:
            return

        if len(observed_type_pairs) == 1:
            raise ValueError(
                "Every candidate in a same-type mintable coded merge group must have "
                "the canonical applicable code type and one common configured code "
                "scope."
            )

        if not _source_refs_share_same_source_occurrence_cross_type_evidence(
            self.candidate_source_refs
        ):
            raise ValueError(
                "Relaxed mixed-type code policy requires direct same-source-"
                "occurrence cross-type evidence in candidate_source_refs."
            )

        scope_conflicts = _find_scope_value_conflicts(
            [
                dict(source_ref.get("code_scope_values") or {})
                for source_ref in self.candidate_source_refs
            ]
        )

        if scope_conflicts:
            raise ValueError(
                f"Mixed-type mintable coded merge groups must not preserve "
                f"contradictory shared code-scope values: {scope_conflicts}."
            )

        canonical_source_candidate_id = self.canonical_code_source_candidate_id
        matching_canonical_source_refs = [
            source_ref
            for source_ref in self.candidate_source_refs
            if (
                (
                    canonical_source_candidate_id is None
                    or source_ref.get("registry_candidate_id")
                    == canonical_source_candidate_id
                )
                and source_ref.get("normalized_statement_code")
                == self.canonical_normalized_statement_code
                and source_ref.get("resolved_code_type") == self.canonical_code_type
                and (str(source_ref.get("code_scope_key") or "").strip() or None)
                == self.code_scope_key
                and dict(source_ref.get("code_scope_values") or {})
                == self.code_scope_values
            )
        ]

        if not matching_canonical_source_refs:
            raise ValueError(
                "A mixed-type mintable coded merge group must derive its aggregate "
                "canonical code type and code scope from one source-backed coded "
                "candidate in the group."
            )

    @model_validator(mode="after")
    def validate_identity_scope_contract(self) -> Self:
        """Validate aggregate semantic identity scope against candidate references.

        Same-type mintable groups require one exactly shared configured identity scope.
        Mixed-type merged groups use the selected canonical type source candidate's
        scope while permitting additional or missing dimensions caused by different
        statement-type policies. Shared dimensions must never contradict one another.

        Returns
        -------
        Self
            Validated merge group.

        Raises
        ------
        ValueError
            If aggregate scope fields are not source-backed, a same-type mintable group
            combines different scopes, or mixed-type candidates contradict one another
            on a shared scope dimension.
        """

        self._validate_aggregate_identity_scope_is_source_backed()

        if self.merge_decision not in {"merged", "singleton"}:
            return self

        if self._validate_same_type_identity_scope():
            return self

        self._validate_mixed_type_identity_scope()

        return self

    def _validate_aggregate_identity_scope_is_source_backed(self) -> None:
        """Validate that aggregate identity-scope fields are source-backed.

        Confirms that the aggregate identity-scope keys equal the non-empty
        identity-scope keys preserved across candidate references, and that the
        resolved identity-scope key and values are drawn from those references.

        Raises
        ------
        ValueError
            If the aggregate identity-scope keys do not equal the source-backed keys,
            identity-scope values are populated while the key is null, no single
            source-backed value mapping exists for the resolved key, or the aggregate
            values do not equal that source-backed mapping.
        """

        source_scope_keys = sorted(
            {
                str(source_ref.get("identity_scope_key") or "").strip()
                for source_ref in self.candidate_source_refs
                if str(source_ref.get("identity_scope_key") or "").strip()
            }
        )

        if set(self.identity_scope_keys) != set(source_scope_keys):
            raise ValueError(
                "identity_scope_keys must equal the non-empty identity-scope keys "
                "preserved in candidate_source_refs."
            )

        if self.identity_scope_key is None:
            if self.identity_scope_values:
                raise ValueError(
                    "identity_scope_values must be empty when identity_scope_key is null."
                )

            return

        matching_scope_values = {
            tuple(
                (str(key).strip(), str(value).strip())
                for key, value in (
                    source_ref.get("identity_scope_values") or {}
                ).items()
            )
            for source_ref in self.candidate_source_refs
            if str(source_ref.get("identity_scope_key") or "").strip()
            == self.identity_scope_key
        }

        if len(matching_scope_values) != 1:
            raise ValueError(
                "identity_scope_values must have one source-backed value mapping "
                "for the resolved identity_scope_key."
            )

        expected_scope_values = dict(next(iter(matching_scope_values)))

        if self.identity_scope_values != expected_scope_values:
            raise ValueError(
                "identity_scope_values must equal the source-backed values for "
                "identity_scope_key."
            )

    def _validate_same_type_identity_scope(self) -> bool:
        """Validate identity scope for a same-type mintable merge group.

        Applies only when every candidate preserves a single observed statement-type
        pair. Such a group must share one common configured identity scope, and the
        aggregate identity-scope key must equal that shared scope.

        Returns
        -------
        bool
            `True` when the group is same-type and identity-scope validation is
            therefore complete, `False` when the group is mixed-type and mixed-type
            validation must still run.

        Raises
        ------
        ValueError
            If a same-type group does not preserve one common configured identity
            scope, or the aggregate identity-scope key does not equal that shared scope.
        """

        observed_type_pairs = {
            (
                str(source_ref.get("statement_type") or "").strip(),
                str(source_ref.get("normalized_statement_type") or "").strip(),
            )
            for source_ref in self.candidate_source_refs
        }

        if len(observed_type_pairs) != 1:
            return False

        source_scope_signatures = {
            str(source_ref.get("identity_scope_key") or "").strip()
            for source_ref in self.candidate_source_refs
        }

        if len(source_scope_signatures) != 1:
            raise ValueError(
                "Every candidate in a same-type mintable merge group must preserve "
                "one common configured semantic identity scope."
            )

        expected_scope_key = next(iter(source_scope_signatures)) or None

        if self.identity_scope_key != expected_scope_key:
            raise ValueError(
                "identity_scope_key must equal the common source-backed identity "
                "scope for a same-type mintable merge group."
            )

        return True

    def _validate_mixed_type_identity_scope(self) -> None:
        """Validate identity scope for a mixed-type mintable merge group.

        Applies when a mintable group preserves more than one observed statement-type
        pair. Unless every candidate shares an identical scope key and value mapping,
        the group requires direct same-source-occurrence cross-type evidence. The
        aggregate scope must derive from the canonical type source candidate, and
        shared scope dimensions must not contradict one another.

        Raises
        ------
        ValueError
            If a relaxed mixed-type scope lacks same-source-occurrence cross-type
            evidence, the canonical type source candidate is missing or not preserved,
            the aggregate scope does not derive from that candidate, or candidates
            contradict one another on a shared scope dimension.
        """

        source_scope_signatures = {
            str(source_ref.get("identity_scope_key") or "").strip()
            for source_ref in self.candidate_source_refs
        }
        source_scope_value_signatures = {
            tuple(
                (str(key).strip(), str(value).strip())
                for key, value in (
                    source_ref.get("identity_scope_values") or {}
                ).items()
            )
            for source_ref in self.candidate_source_refs
        }
        strict_scope_compatible = bool(
            len(source_scope_signatures) == 1
            and len(source_scope_value_signatures) == 1
        )

        if (
            not strict_scope_compatible
            and not _source_refs_share_same_source_occurrence_cross_type_evidence(
                self.candidate_source_refs
            )
        ):
            raise ValueError(
                "Relaxed mixed-type identity scope requires direct same-source-"
                "occurrence cross-type evidence in candidate_source_refs."
            )

        if self.canonical_type_source_candidate_id is None:
            raise ValueError(
                "Mixed-type mintable merge groups require a canonical type source "
                "candidate before identity scope can be resolved."
            )

        canonical_source_ref = next(
            (
                source_ref
                for source_ref in self.candidate_source_refs
                if source_ref.get("registry_candidate_id")
                == self.canonical_type_source_candidate_id
            ),
            None,
        )

        if canonical_source_ref is None:
            raise ValueError(
                "The canonical type source candidate is not preserved in "
                "candidate_source_refs."
            )

        canonical_scope_key = (
            str(canonical_source_ref.get("identity_scope_key") or "").strip() or None
        )
        canonical_scope_values = dict(
            canonical_source_ref.get("identity_scope_values") or {}
        )

        if (
            self.identity_scope_key != canonical_scope_key
            or self.identity_scope_values != canonical_scope_values
        ):
            raise ValueError(
                "A mixed-type mintable merge group must derive aggregate identity "
                "scope from its canonical type source candidate."
            )

        scope_conflicts = _find_scope_value_conflicts(
            [
                dict(source_ref.get("identity_scope_values") or {})
                for source_ref in self.candidate_source_refs
            ]
        )

        if scope_conflicts:
            raise ValueError(
                f"Mixed-type mintable merge groups must not preserve contradictory "
                f"shared identity-scope values: {scope_conflicts}."
            )

    @model_validator(mode="after")
    def validate_type_resolution_contract(self) -> Self:
        """Validate canonical statement-type resolution for the merge group.

        Returns
        -------
        Self
            Validated merge group.

        Raises
        ------
        ValueError
            If mintable groups lack a canonical type pair, non-mintable groups assert
            one, or mixed-type selection metadata is inconsistent.
        """

        observed_pairs = {
            (
                str(source_ref.get("statement_type") or "").strip(),
                str(source_ref.get("normalized_statement_type") or "").strip(),
            )
            for source_ref in self.candidate_source_refs
        }
        observed_pairs.discard(("", ""))
        canonical_pair = (
            self.canonical_statement_type or "",
            self.canonical_normalized_statement_type or "",
        )
        selection_fields_present = bool(
            self.canonical_type_selection_reason
            or self.canonical_type_source_candidate_id
        )

        if self.merge_decision not in {"merged", "singleton"}:
            self._validate_non_mintable_type_contract(
                canonical_pair=canonical_pair,
                selection_fields_present=selection_fields_present,
            )

            return self

        if not all(canonical_pair):
            raise ValueError(
                "Mintable merge groups require canonical_statement_type and "
                "canonical_normalized_statement_type."
            )

        if canonical_pair not in observed_pairs:
            raise ValueError(
                "Canonical statement type must be preserved by a candidate source "
                "reference in the merge group."
            )

        if len(observed_pairs) == 1:
            if selection_fields_present:
                raise ValueError(
                    "Single-type merge groups must not define canonical type "
                    "selection fields."
                )

            return self

        self._validate_mixed_type_selection_contract(canonical_pair)

        return self

    def _validate_non_mintable_type_contract(
        self, *, canonical_pair: tuple[str, str], selection_fields_present: bool
    ) -> None:
        """Validate that a non-mintable group asserts no type-resolution fields.

        Parameters
        ----------
        canonical_pair
            The (statement type, normalized statement type) pair resolved on the group,
            with missing values normalized to empty strings.
        selection_fields_present
            Whether any canonical type selection metadata field is populated.

        Raises
        ------
        ValueError
            If a conflict or needs_review group defines any canonical statement-type
            resolution field.
        """

        if canonical_pair != ("", "") or selection_fields_present:
            raise ValueError(
                "Conflict and needs_review groups must not define canonical "
                "statement-type resolution fields."
            )

    def _validate_mixed_type_selection_contract(
        self, canonical_pair: tuple[str, str]
    ) -> None:
        """Validate canonical type selection for a mixed-type merged group.

        Applies when a merged group preserves more than one observed statement-type
        pair and must therefore record which source candidate the canonical type was
        selected from.

        Parameters
        ----------
        canonical_pair
            The (statement type, normalized statement type) pair resolved on the group,
            with missing values normalized to empty strings.

        Raises
        ------
        ValueError
            If the group is not merged, required selection metadata is missing, the
            selected candidate is absent or non-unique, or the canonical pair does not
            equal the selected candidate's type pair.
        """

        if self.merge_decision != "merged":
            raise ValueError(
                "Only merged groups may resolve multiple observed statement types."
            )

        if not self.canonical_type_source_candidate_id:
            raise ValueError(
                "Mixed-type merged groups require canonical_type_source_candidate_id."
            )

        if not self.canonical_type_selection_reason:
            raise ValueError(
                "Mixed-type merged groups require canonical_type_selection_reason."
            )

        if self.canonical_type_source_candidate_id not in self.registry_candidate_ids:
            raise ValueError(
                "canonical_type_source_candidate_id must belong to the merge group."
            )

        selected_refs = [
            source_ref
            for source_ref in self.candidate_source_refs
            if source_ref.get("registry_candidate_id")
            == self.canonical_type_source_candidate_id
        ]

        if len(selected_refs) != 1:
            raise ValueError(
                "canonical_type_source_candidate_id must identify exactly one "
                "candidate source reference."
            )

        selected_pair = (
            str(selected_refs[0].get("statement_type") or "").strip(),
            str(selected_refs[0].get("normalized_statement_type") or "").strip(),
        )

        if canonical_pair != selected_pair:
            raise ValueError(
                "Canonical statement type must equal the selected source candidate's "
                "type pair."
            )

    @model_validator(mode="after")
    def validate_representative_candidate_contract(self) -> Self:
        """Validate representative-candidate semantics for this merge group.

        Returns
        -------
        Self
            Validated merge group.

        Raises
        ------
        ValueError
            If a mintable group lacks a valid representative candidate or a
            non-mintable group asserts one.
        """

        representative_candidate_id = self.representative_candidate_id

        if self.merge_decision == "merged":
            if representative_candidate_id is None:
                raise ValueError("Merged groups require representative_candidate_id.")

            if representative_candidate_id not in self.registry_candidate_ids:
                raise ValueError(
                    "Merged-group representative_candidate_id must belong to "
                    "registry_candidate_ids."
                )

            return self

        if self.merge_decision == "singleton":
            if len(self.registry_candidate_ids) != 1:
                raise ValueError(
                    "Singleton groups require exactly one registry candidate."
                )

            if representative_candidate_id != self.registry_candidate_ids[0]:
                raise ValueError(
                    "Singleton representative_candidate_id must equal the sole "
                    "registry candidate ID."
                )

            return self

        if representative_candidate_id is not None:
            raise ValueError(
                "Conflict and needs_review groups must not define representative_candidate_id."
            )

        return self

    @model_validator(mode="after")
    def validate_code_resolution_contract(self) -> Self:
        """Validate canonical-code resolution against preserved source codes.

        Returns
        -------
        Self
            Validated merge group.

        Raises
        ------
        ValueError
            If the resolution method, canonical fields, source-code evidence, or merge
            decision are inconsistent.
        """

        source_normalized_codes = unique_clean_strings(self.normalized_statement_codes)
        source_statement_codes = unique_clean_strings(self.statement_codes)

        method_checks: dict[str, Callable[..., None]] = {
            "no_source_code": self._check_no_source_code_contract,
            "single_source_code": self._check_single_source_code_contract,
            "review_selected_source_code": (
                self._check_review_selected_source_code_contract
            ),
            "unresolved_multiple_source_codes": (
                self._check_unresolved_multiple_source_codes_contract
            ),
        }
        check = method_checks.get(self.code_resolution_method)

        if check is None:
            raise ValueError(
                f"Unsupported code_resolution_method {self.code_resolution_method!r}."
            )

        check(
            source_normalized_codes=source_normalized_codes,
            source_statement_codes=source_statement_codes,
        )

        return self

    def _check_no_source_code_contract(
        self,
        *,
        source_normalized_codes: Sequence[str],
        source_statement_codes: Sequence[str],
    ) -> None:
        """Validate the `no_source_code` resolution contract.

        Parameters
        ----------
        source_normalized_codes
            Distinct, cleaned normalized source statement codes.
        source_statement_codes
            Distinct, cleaned source statement codes.

        Raises
        ------
        ValueError
            If source-code lists are non-empty or any canonical code field is set.
        """

        if source_normalized_codes or source_statement_codes:
            raise ValueError(
                "no_source_code requires empty source statement-code lists."
            )

        if any(
            [
                self.canonical_code_type,
                self.canonical_code_source_candidate_id,
                self.canonical_normalized_statement_code,
                self.canonical_statement_code,
            ]
        ):
            raise ValueError("no_source_code must not define canonical code fields.")

    def _check_single_source_code_contract(
        self,
        *,
        source_normalized_codes: Sequence[str],
        source_statement_codes: Sequence[str],
    ) -> None:
        """Validate the `single_source_code` resolution contract.

        Parameters
        ----------
        source_normalized_codes
            Distinct, cleaned normalized source statement codes.
        source_statement_codes
            Distinct, cleaned source statement codes.

        Raises
        ------
        ValueError
            If there is not exactly one normalized source code, the canonical codes do
            not match the preserved source codes, or an LLM-selected source candidate
            is defined.
        """

        if len(source_normalized_codes) != 1:
            raise ValueError(
                "single_source_code requires exactly one distinct normalized "
                "source statement code."
            )

        if self.canonical_normalized_statement_code != source_normalized_codes[0]:
            raise ValueError(
                "single_source_code canonical normalized code must equal the "
                "single preserved normalized source code."
            )

        if not self.canonical_code_type:
            raise ValueError("single_source_code requires canonical_code_type.")

        if (
            not self.canonical_statement_code
            or self.canonical_statement_code not in source_statement_codes
        ):
            raise ValueError(
                "single_source_code canonical statement code must be preserved in "
                "statement_codes."
            )

        if self.canonical_code_source_candidate_id is not None:
            raise ValueError(
                "single_source_code must not require an LLM-selected source "
                "candidate."
            )

    def _check_review_selected_source_code_contract(
        self,
        *,
        source_normalized_codes: Sequence[str],
        source_statement_codes: Sequence[str],
    ) -> None:
        """Validate the `review_selected_source_code` resolution contract.

        Parameters
        ----------
        source_normalized_codes
            Distinct, cleaned normalized source statement codes.
        source_statement_codes
            Distinct, cleaned source statement codes.

        Raises
        ------
        ValueError
            If there are fewer than two normalized source codes, the group is not
            merged, no source candidate is selected, or the canonical codes are not
            preserved among the source codes.
        """

        if len(source_normalized_codes) < 2:
            raise ValueError(
                "review_selected_source_code requires multiple distinct normalized "
                "source codes."
            )

        if self.merge_decision != "merged":
            raise ValueError(
                "review_selected_source_code is valid only for a merged group."
            )

        if not self.canonical_code_source_candidate_id:
            raise ValueError(
                "review_selected_source_code requires "
                "canonical_code_source_candidate_id."
            )

        if not self.canonical_code_type:
            raise ValueError(
                "review_selected_source_code requires canonical_code_type."
            )

        if (
            not self.canonical_normalized_statement_code
            or self.canonical_normalized_statement_code not in source_normalized_codes
        ):
            raise ValueError(
                "Selected canonical normalized code must be preserved in "
                "normalized_statement_codes."
            )

        if (
            not self.canonical_statement_code
            or self.canonical_statement_code not in source_statement_codes
        ):
            raise ValueError(
                "Selected canonical statement code must be preserved in "
                "statement_codes."
            )

    def _check_unresolved_multiple_source_codes_contract(
        self,
        *,
        source_normalized_codes: Sequence[str],
        source_statement_codes: Sequence[str],
    ) -> None:
        """Validate the `unresolved_multiple_source_codes` resolution contract.

        Parameters
        ----------
        source_normalized_codes
            Distinct, cleaned normalized source statement codes.
        source_statement_codes
            Distinct, cleaned source statement codes.

        Raises
        ------
        ValueError
            If there are fewer than two normalized or source statement codes, the merge
            decision is not `conflict` or `needs_review`, or any canonical code field
            is set.
        """

        if len(source_normalized_codes) < 2:
            raise ValueError(
                "unresolved_multiple_source_codes requires multiple distinct "
                "normalized source codes."
            )

        if len(source_statement_codes) < 2:
            raise ValueError(
                "unresolved_multiple_source_codes requires multiple distinct "
                "source statement codes."
            )

        if self.merge_decision not in {"conflict", "needs_review"}:
            raise ValueError(
                "Unresolved multiple source codes are allowed only for conflict "
                "or needs_review groups."
            )

        if any(
            [
                self.canonical_code_type,
                self.canonical_code_source_candidate_id,
                self.canonical_normalized_statement_code,
                self.canonical_statement_code,
            ]
        ):
            raise ValueError(
                "Unresolved multiple source codes must not define canonical code "
                "fields."
            )


class SFIMergeReport(BaseSchema):
    """Persisted merge/dedup report artifact."""

    conflict_groups: list[SFIMergeGroup] = Field(default_factory=list)
    merge_groups: list[SFIMergeGroup] = Field(default_factory=list)
    needs_review_groups: list[SFIMergeGroup] = Field(default_factory=list)
    review_requests: list[SFIDedupReviewRequest] = Field(default_factory=list)
    review_responses: list[SFIDedupReviewResponse] = Field(default_factory=list)
    summary: SFIMergeSummary = Field(description="SFI merge/dedup summary.")


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


# Schemas for SFI finalization.
class SFIFinalContext(BaseSchema):
    """Recovered source context for one finalized SFI used for hasChild resolution."""

    audit_flags: list[str] = Field(default_factory=list)
    candidate_source_texts: list[str] = Field(default_factory=list)
    canonical_statement_value: Optional[str] = Field(
        default=None,
        description=(
            "Canonical controlled statement value inherited from final SFI "
            "finalization, when available."
        ),
    )
    canonical_statement_value_key: Optional[str] = Field(
        default=None,
        description=(
            "Normalized canonical controlled statement value key inherited from final "
            "SFI finalization, when available."
        ),
    )
    description: str = Field(description="Final SFI description.", min_length=1)
    final_sfi_uuid: UUID = Field(description="Final SFI UUID.")
    identity_scope_key: Optional[str] = Field(default=None)
    identity_scope_values: dict[str, str] = Field(default_factory=dict)
    normalized_statement_code: Optional[str] = Field(default=None)
    normalized_statement_type: NormalizedStatementType
    section_path_labels: list[str] = Field(default_factory=list)
    source_context_keys: list[str] = Field(default_factory=list)
    source_context_labels: list[str] = Field(
        description=(
            "Source-visible context labels recovered from candidate source refs, "
            "such as section labels and typed table header labels. These labels are "
            "used only as relationship-resolution evidence and never as minted "
            "relationship endpoints."
        ),
        min_length=1,
    )
    source_order: int = Field(description="Deterministic source-order index.", ge=0)
    source_page_indexes: list[int] = Field(default_factory=list)
    source_registry_candidate_ids: list[str] = Field(default_factory=list)
    source_segment_ids: list[str] = Field(default_factory=list)
    source_window_ids: list[str] = Field(default_factory=list)
    source_window_indexes: list[int] = Field(default_factory=list)
    statement_code: Optional[str] = Field(default=None)
    statement_type: str = Field(
        description="Source-facing statement type.", min_length=1
    )
    table_header_indexes: list[int] = Field(default_factory=list)
    table_row_indexes: list[int] = Field(default_factory=list)


class SFIFinalRecord(BaseSchema):
    """Final source-backed StandardsFrameworkItem record after SFI dedup.

    This schema mints deterministic final SFI identifiers and preserves source,
    candidate, merge, and audit provenance for later relationship resolution. It is not
    yet a complete exported KG node and does not assert hierarchy relationships.
    """

    academic_subject: str = Field(description="Academic subject from KG metadata.")
    attribution_statement: str = Field(description="Attribution text from KG metadata.")
    audit_flags: list[str] = Field(
        default_factory=list,
        description="Machine-readable audit flags inherited from the merge group.",
    )
    audit_notes: list[str] = Field(
        default_factory=list,
        description="Human-readable audit notes inherited from the merge group.",
    )
    audit_peer_merge_group_ids: list[str] = Field(
        default_factory=list,
        description="Peer merge groups relevant to inherited audit flags.",
    )
    author: str = Field(description="Author or issuing body from KG metadata.")
    candidate_descriptions: list[str] = Field(
        default_factory=list,
        description="Unique candidate descriptions preserved for audit.",
    )
    candidate_source_refs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-candidate source references preserved from SFI dedup step.",
    )
    candidate_source_texts: list[str] = Field(
        default_factory=list,
        description="Unique source-visible evidence quotes preserved for audit.",
    )
    canonical_code_source_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Registry candidate whose source-backed code was selected as canonical "
            "for a mixed-code merge."
        ),
    )
    canonical_code_type: Optional[str] = Field(
        default=None,
        description="Resolved configured code type for the final logical SFI.",
    )
    canonical_normalized_statement_code: Optional[str] = Field(
        default=None,
        description="Resolved normalized statement code for the final logical SFI.",
    )
    canonical_statement_code: Optional[str] = Field(
        default=None,
        description="Resolved source-backed statement code for the final logical SFI.",
    )
    canonical_type_selection_reason: Optional[str] = Field(
        default=None,
        description=(
            "Source-grounded reason for canonical statement-type selection in a "
            "mixed-type merge."
        ),
    )
    canonical_type_source_candidate_id: Optional[str] = Field(
        default=None,
        description=(
            "Registry candidate whose source-backed type pair was selected as "
            "canonical for a mixed-type merge."
        ),
    )
    canonical_statement_value: Optional[str] = Field(
        default=None,
        description="Canonical controlled statement value, when configured.",
    )
    canonical_statement_value_key: Optional[str] = Field(
        default=None,
        description="Normalized canonical controlled value key, when configured.",
    )
    case_identifier_uri: str = Field(
        description="CASE-compatible URI for the deterministic final SFI UUID."
    )
    case_identifier_uuid: UUID = Field(
        description="CASE-compatible deterministic final SFI UUID."
    )
    code_resolution_method: SFICodeResolutionMethod = Field(
        description="Method used to resolve the final SFI's canonical code."
    )
    code_resolution_reason: str = Field(
        description="Source-grounded or deterministic canonical-code resolution reason.",
        min_length=1,
    )
    confidence_max: float = Field(
        description="Maximum candidate confidence in this final SFI.", ge=0.0, le=1.0
    )
    confidence_min: float = Field(
        description="Minimum candidate confidence in this final SFI.", ge=0.0, le=1.0
    )
    description: str = Field(description="Final source-backed SFI description text.")
    final_sfi_uuid: UUID = Field(description="Deterministic final SFI UUID.")
    identifier: UUID = Field(description="Primary deterministic final SFI identifier.")
    identity_key: str = Field(
        description="Canonical deterministic identity string used to mint the UUID."
    )
    identity_scope_key: Optional[str] = Field(
        default=None,
        description="Resolved semantic identity-scope key for the final SFI.",
    )
    identity_scope_values: dict[str, str] = Field(
        default_factory=dict,
        description="Resolved canonical semantic identity-scope values.",
    )
    in_language: LanguageField = Field(description="Language tag for the final SFI.")
    jurisdiction: str = Field(description="Jurisdiction from KG metadata.")
    language: str = Field(description="Source language tag chosen for the final SFI.")
    license: str = Field(description="License from KG metadata.")
    merge_decision: SFIMergeDecision = Field(description="SFI merge decision.")
    merge_group_id: str = Field(description="Source SFI merge group ID.")
    merge_reason: str = Field(description="SFI merge or singleton reason.")
    metadata: _MetadataT = Field(
        default_factory=dict,
        description="Free-form deterministic metadata for downstream KG stages.",
    )
    normalized_statement_code: Optional[str] = Field(
        default=None,
        description="Resolved canonical normalized statement code, if any.",
    )
    normalized_statement_type: NormalizedStatementType = Field(
        description="Normalized SFI statement type."
    )
    provider: str = Field(description="Provider from KG metadata.")
    representative_candidate_id: str = Field(
        description=(
            "Registry candidate whose existing description and language supply the "
            "final source-facing surface form."
        ),
        min_length=1,
    )
    source_context_keys: list[str] = Field(
        default_factory=list,
        description="Source-context keys from registry candidate source refs.",
    )
    source_normalized_statement_codes: list[str] = Field(
        description="All normalized source-visible statement codes preserved for audit."
    )
    source_page_indexes: list[int] = Field(
        default_factory=list,
        description="0-based PDF page indexes recovered from DocumentIR source segments.",
    )
    source_registry_candidate_ids: list[str] = Field(
        description="Registry candidate IDs represented by this final SFI.",
        min_length=1,
    )
    source_segment_ids: list[str] = Field(
        default_factory=list, description="Merged source DocumentIR segment IDs."
    )
    source_statement_codes: list[str] = Field(
        description="All source-visible statement codes preserved for audit."
    )
    source_window_ids: list[str] = Field(
        default_factory=list, description="Merged extraction-window IDs."
    )
    source_window_indexes: list[int] = Field(
        default_factory=list, description="Merged extraction-window indexes."
    )
    statement_code: Optional[str] = Field(
        default=None,
        description="Resolved canonical source-backed statement code, if any.",
    )
    statement_type: str = Field(description="Source-facing SFI statement type.")

    @field_validator(
        "academic_subject",
        "attribution_statement",
        "author",
        "case_identifier_uri",
        "code_resolution_reason",
        "description",
        "identity_key",
        "in_language",
        "jurisdiction",
        "language",
        "license",
        "merge_group_id",
        "merge_reason",
        "normalized_statement_type",
        "provider",
        "representative_candidate_id",
        "statement_type",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        """Strip whitespace and require non-empty strings.

        Parameters
        ----------
        v
            Raw required string value.

        Returns
        -------
        str
            Cleaned non-empty string.
        """

        return strip_and_require_non_empty_str(v)

    @field_validator(
        "canonical_code_source_candidate_id",
        "canonical_code_type",
        "canonical_normalized_statement_code",
        "canonical_statement_code",
        "canonical_type_selection_reason",
        "canonical_type_source_candidate_id",
        "identity_scope_key",
        "normalized_statement_code",
        "statement_code",
        mode="before",
    )
    @classmethod
    def _strip_optional_strings(cls, v: Optional[str]) -> Optional[str]:
        """Strip optional strings and normalize blanks to None.

        Parameters
        ----------
        v
            Raw optional string value.

        Returns
        -------
        Optional[str]
            Cleaned string or None.
        """

        if v is None:
            return None

        v2 = v.strip()
        return v2 if v2 else None

    @field_validator("identity_scope_values")
    @classmethod
    def validate_identity_scope_values(cls, v: dict[str, str]) -> dict[str, str]:
        """Clean final-record semantic identity-scope values.

        Parameters
        ----------
        v
            Raw identity-scope mapping.

        Returns
        -------
        dict[str, str]
            Cleaned ordered identity-scope mapping.
        """

        return clean_scope_values(field_name="identity_scope_values", values=v)

    @field_validator(
        "audit_flags",
        "audit_notes",
        "audit_peer_merge_group_ids",
        "candidate_descriptions",
        "candidate_source_texts",
        "source_context_keys",
        "source_normalized_statement_codes",
        "source_registry_candidate_ids",
        "source_segment_ids",
        "source_statement_codes",
        "source_window_ids",
    )
    @classmethod
    def clean_string_lists(cls, v: list[str]) -> list[str]:
        """Clean string-list fields while preserving order.

        Parameters
        ----------
        v
            Raw string list.

        Returns
        -------
        list[str]
            Cleaned unique string list.
        """

        return unique_clean_strings(v)

    @field_validator("source_page_indexes", "source_window_indexes")
    @classmethod
    def clean_int_lists(cls, v: list[int]) -> list[int]:
        """Clean integer-list fields into sorted unique values.

        Parameters
        ----------
        v
            Raw integer values.

        Returns
        -------
        list[int]
            Sorted unique integer values.
        """

        return sorted(set(int(index) for index in v or []))

    @model_validator(mode="after")
    def validate_identifier_consistency(self) -> Self:
        """Validate that all identifier fields use the same UUID.

        Returns
        -------
        Self
            The validated final SFI record.

        Raises
        ------
        ValueError
            If identifier fields disagree or the URI does not end with the UUID.
        """

        if self.final_sfi_uuid != self.case_identifier_uuid:
            raise ValueError("final_sfi_uuid must equal case_identifier_uuid.")

        if self.final_sfi_uuid != self.identifier:
            raise ValueError("final_sfi_uuid must equal identifier.")

        if not self.case_identifier_uri.endswith(str(self.final_sfi_uuid)):
            raise ValueError("case_identifier_uri must end with final_sfi_uuid.")

        return self

    @model_validator(mode="after")
    def validate_identity_scope_contract(self) -> Self:
        """Validate final-record semantic identity-scope field consistency.

        Returns
        -------
        Self
            Validated final SFI record.

        Raises
        ------
        ValueError
            If identity-scope key and values are only partially populated.
        """

        if bool(self.identity_scope_key) != bool(self.identity_scope_values):
            raise ValueError(
                "identity_scope_key and identity_scope_values must either both be "
                "present or both be empty."
            )

        return self

    @model_validator(mode="after")
    def validate_representative_candidate_contract(self) -> Self:
        """Validate the final record's representative-candidate reference.

        Returns
        -------
        Self
            Validated final SFI record.

        Raises
        ------
        ValueError
            If the representative candidate is not preserved among the source registry
            candidate IDs.
        """

        if self.representative_candidate_id not in self.source_registry_candidate_ids:
            raise ValueError(
                "representative_candidate_id must be preserved in source_registry_candidate_ids."
            )

        return self

    @model_validator(mode="after")
    def validate_final_type_resolution_contract(self) -> Self:
        """Validate final-record canonical statement-type resolution metadata.

        Returns
        -------
        Self
            Validated final SFI record.

        Raises
        ------
        ValueError
            If mixed-type selection metadata is partially populated or references a
            candidate outside the preserved source candidate set.
        """

        selection_fields = (
            self.canonical_type_selection_reason,
            self.canonical_type_source_candidate_id,
        )

        if any(selection_fields) and not all(selection_fields):
            raise ValueError(
                "canonical type selection reason and source candidate ID must be "
                "present together."
            )

        if (
            self.canonical_type_source_candidate_id is not None
            and self.canonical_type_source_candidate_id
            not in self.source_registry_candidate_ids
        ):
            raise ValueError(
                "canonical_type_source_candidate_id must be preserved in "
                "source_registry_candidate_ids."
            )

        return self

    @model_validator(mode="after")
    def validate_final_code_resolution_contract(self) -> Self:
        """Validate final-record canonical-code resolution semantics.

        Returns
        -------
        Self
            Validated final SFI record.

        Raises
        ------
        ValueError
            If the resolution method conflicts with preserved source codes, canonical
            fields, merge eligibility, or source-candidate selection metadata.
        """

        source_normalized_codes = unique_clean_strings(
            self.source_normalized_statement_codes
        )
        source_statement_codes = unique_clean_strings(self.source_statement_codes)

        method_checks = {
            "no_source_code": self._check_final_no_source_code_contract,
            "single_source_code": self._check_final_single_source_code_contract,
            "review_selected_source_code": (
                self._check_final_review_selected_source_code_contract
            ),
        }
        check = method_checks.get(self.code_resolution_method)

        if check is None:
            raise ValueError(
                "Final SFI records cannot use unresolved_multiple_source_codes."
            )

        check(
            source_normalized_codes=source_normalized_codes,
            source_statement_codes=source_statement_codes,
        )

        return self

    def _check_final_no_source_code_contract(
        self,
        *,
        source_normalized_codes: Sequence[str],
        source_statement_codes: Sequence[str],
    ) -> None:
        """Validate the `no_source_code` contract for a final record.

        Parameters
        ----------
        source_normalized_codes
            Distinct, cleaned normalized source statement codes.
        source_statement_codes
            Distinct, cleaned source statement codes.

        Raises
        ------
        ValueError
            If source-code lists are non-empty or any canonical code field is set.
        """

        if source_normalized_codes or source_statement_codes:
            raise ValueError(
                "no_source_code final records require empty source-code lists."
            )

        if any(
            [
                self.canonical_code_type,
                self.canonical_code_source_candidate_id,
                self.canonical_normalized_statement_code,
                self.canonical_statement_code,
            ]
        ):
            raise ValueError(
                "no_source_code final records must not define canonical code fields."
            )

    def _check_final_single_source_code_contract(
        self,
        *,
        source_normalized_codes: Sequence[str],
        source_statement_codes: Sequence[str],
    ) -> None:
        """Validate the `single_source_code` contract for a final record.

        Parameters
        ----------
        source_normalized_codes
            Distinct, cleaned normalized source statement codes.
        source_statement_codes
            Distinct, cleaned source statement codes.

        Raises
        ------
        ValueError
            If there is not exactly one normalized source code, the canonical codes do
            not match the preserved source codes, or a reviewed source candidate is
            defined.
        """

        if len(source_normalized_codes) != 1:
            raise ValueError(
                "single_source_code final records require exactly one distinct "
                "normalized source code."
            )

        if self.canonical_normalized_statement_code != source_normalized_codes[0]:
            raise ValueError(
                "single_source_code canonical normalized code must equal the "
                "single preserved normalized source code."
            )

        if not self.canonical_code_type:
            raise ValueError(
                "single_source_code final records require canonical_code_type."
            )

        if (
            not self.canonical_statement_code
            or self.canonical_statement_code not in source_statement_codes
        ):
            raise ValueError(
                "single_source_code canonical statement code must be preserved in "
                "source_statement_codes."
            )

        if self.canonical_code_source_candidate_id is not None:
            raise ValueError(
                "single_source_code final records must not define a reviewed "
                "canonical code source candidate."
            )

    def _check_final_review_selected_source_code_contract(
        self,
        *,
        source_normalized_codes: Sequence[str],
        source_statement_codes: Sequence[str],
    ) -> None:
        """Validate the `review_selected_source_code` contract for a final record.

        Parameters
        ----------
        source_normalized_codes
            Distinct, cleaned normalized source statement codes.
        source_statement_codes
            Distinct, cleaned source statement codes.

        Raises
        ------
        ValueError
            If there are fewer than two normalized source codes, the record is not
            merged, no source candidate is selected, or the canonical codes are not
            preserved among the source codes.
        """

        if len(source_normalized_codes) < 2:
            raise ValueError(
                "review_selected_source_code final records require multiple "
                "distinct normalized source codes."
            )

        if self.merge_decision != "merged":
            raise ValueError(
                "review_selected_source_code final records require merge_decision "
                "'merged'."
            )

        if not self.canonical_code_source_candidate_id:
            raise ValueError(
                "review_selected_source_code final records require "
                "canonical_code_source_candidate_id."
            )

        if not self.canonical_code_type:
            raise ValueError(
                "review_selected_source_code final records require "
                "canonical_code_type."
            )

        if (
            not self.canonical_normalized_statement_code
            or self.canonical_normalized_statement_code not in source_normalized_codes
        ):
            raise ValueError(
                "Selected canonical normalized code must be preserved in "
                "source_normalized_statement_codes."
            )

        if (
            not self.canonical_statement_code
            or self.canonical_statement_code not in source_statement_codes
        ):
            raise ValueError(
                "Selected canonical statement code must be preserved in "
                "source_statement_codes."
            )

    @model_validator(mode="after")
    def validate_canonical_code_aliases(self) -> Self:
        """Validate canonical-code fields and downstream code aliases agree.

        Returns
        -------
        Self
            Validated final SFI record.

        Raises
        ------
        ValueError
            If canonical code fields disagree with the public statement-code fields or
            are absent from preserved source-code evidence.
        """

        if self.normalized_statement_code != self.canonical_normalized_statement_code:
            raise ValueError(
                "normalized_statement_code must equal canonical_normalized_statement_code."
            )

        if self.statement_code != self.canonical_statement_code:
            raise ValueError("statement_code must equal canonical_statement_code.")

        if (
            self.canonical_normalized_statement_code is not None
            and self.canonical_normalized_statement_code
            not in self.source_normalized_statement_codes
        ):
            raise ValueError(
                "Canonical normalized statement code must be preserved in "
                "source_normalized_statement_codes."
            )

        if (
            self.canonical_statement_code is not None
            and self.canonical_statement_code not in self.source_statement_codes
        ):
            raise ValueError(
                "Canonical statement code must be preserved in source_statement_codes."
            )

        return self


class SFIFinalSummary(BaseSchema):
    """Aggregate summary for final SFI records."""

    audit_flag_count_by_type: dict[str, int] = Field(default_factory=dict)
    eligible_merge_group_count: int = Field(default=0, ge=0)
    excluded_conflict_group_count: int = Field(default=0, ge=0)
    excluded_needs_review_group_count: int = Field(default=0, ge=0)
    final_sfi_count: int = Field(default=0, ge=0)
    final_sfi_count_by_normalized_statement_type: dict[str, int] = Field(
        default_factory=dict
    )
    final_sfi_count_by_statement_type: dict[str, int] = Field(default_factory=dict)
    final_sfis_with_statement_code: int = Field(default=0, ge=0)
    final_sfis_without_statement_code: int = Field(default=0, ge=0)
    same_code_disambiguated_final_sfi_count: int = Field(default=0, ge=0)
    source_registry_candidate_count: int = Field(default=0, ge=0)


# Schemas for SFI hasChild relationships.
class _SFIHasChildChildResolution(BaseSchema):
    """LLM decision for one child SFI's direct parent(s)."""

    child_final_sfi_uuid: UUID
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(
        description="Source-grounded parent-selection reason.", min_length=1
    )
    selected_parent_endpoint_ids: list[str] = Field(default_factory=list)
    unresolved: bool = Field(default=False)

    @field_validator("selected_parent_endpoint_ids")
    @classmethod
    def clean_selected_parent_endpoint_ids(cls, v: list[str]) -> list[str]:
        """Clean selected parent endpoint IDs.

        Parameters
        ----------
        v
            Raw selected parent endpoint IDs.

        Returns
        -------
        list[str]
            Cleaned unique selected parent endpoint IDs.
        """

        return unique_clean_strings(v)

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> Self:
        """Validate unresolved and selected-parent consistency.

        Returns
        -------
        Self
            Validated child resolution.

        Raises
        ------
        ValueError
            If an unresolved child also selects parents, or a resolved child selects
            no parents.
        """

        if self.unresolved and self.selected_parent_endpoint_ids:
            raise ValueError("Unresolved hasChild decisions must not select parents.")

        if not self.unresolved and not self.selected_parent_endpoint_ids:
            raise ValueError(
                "Resolved hasChild decisions must select at least one parent."
            )

        return self


class SFIHasChildCandidateParentSet(BaseSchema):
    """Debug artifact containing one child's bounded parent candidate set."""

    candidate_count_after_truncation: int = Field(ge=1)
    candidate_count_before_truncation: int = Field(ge=1)
    child_context: SFIFinalContext
    max_parent_candidates: int = Field(ge=2)
    parent_candidates: list[SFIHasChildParentCandidate] = Field(min_length=1)
    parent_requirements: list[SFIHasChildParentRequirement]
    truncation_notes: list[str] = Field(default_factory=list)
    was_truncated: bool = Field(default=False)

    @field_validator("parent_requirements")
    @classmethod
    def validate_parent_requirements(
        cls, v: list[SFIHasChildParentRequirement]
    ) -> list[SFIHasChildParentRequirement]:
        """Reject duplicate parent statement types in one child policy.

        Parameters
        ----------
        v
            Allowed direct-parent requirements for this child.

        Returns
        -------
        list[SFIHasChildParentRequirement]
            Validated requirements in configured order.

        Raises
        ------
        ValueError
            If one parent statement type appears more than once.
        """

        parent_statement_types = [
            requirement.parent_statement_type for requirement in v
        ]

        if len(parent_statement_types) != len(set(parent_statement_types)):
            raise ValueError(
                "parent_requirements must not repeat a parent statement_type."
            )

        return v


class SFIHasChildEdge(BaseSchema):
    """Final resolved hasChild edge between framework/finalized SFI endpoints."""

    child_final_sfi_uuid: UUID
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_reasons: list[str] = Field(default_factory=list)
    is_root_edge: bool = Field(default=False)
    llm_reason: str = Field(description="LLM parent-selection reason.", min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_endpoint_id: str = Field(
        description="Selected parent endpoint ID.", min_length=1
    )
    parent_final_sfi_uuid: Optional[UUID] = Field(default=None)
    relationship_id: UUID
    relationship_type: Literal["hasChild"] = "hasChild"
    source_entity: Literal["StandardsFramework", "StandardsFrameworkItem"]
    source_entity_uuid: UUID
    target_entity: Literal["StandardsFrameworkItem"] = "StandardsFrameworkItem"
    target_sfi_uuid: UUID
    unresolved_root_fallback: bool = Field(default=False)


class SFIHasChildScopeComparison(BaseSchema):
    """Deterministic structured-scope comparison for one parent candidate."""

    complete_match: bool = Field(
        default=False,
        description=(
            "Whether the candidate's own parent value and every available ancestor "
            "scope dimension match the child's finalized identity scope."
        ),
    )
    conflicting_ancestor_statement_types: list[str] = Field(default_factory=list)
    direct_parent_statement_type: Optional[str] = Field(default=None)
    direct_parent_value_match: Optional[bool] = Field(
        default=None,
        description=(
            "Exact comparison between the child's scope value for the candidate "
            "statement type and the candidate's canonical controlled value. Null "
            "means one side was unavailable."
        ),
    )
    matching_ancestor_statement_types: list[str] = Field(default_factory=list)
    missing_child_ancestor_statement_types: list[str] = Field(default_factory=list)

    @field_validator(
        "conflicting_ancestor_statement_types",
        "matching_ancestor_statement_types",
        "missing_child_ancestor_statement_types",
    )
    @classmethod
    def clean_scope_statement_types(cls, v: list[str]) -> list[str]:
        """Clean structured-scope statement-type lists.

        Parameters
        ----------
        v
            Raw scope statement-type labels.

        Returns
        -------
        list[str]
            Cleaned unique statement-type labels.
        """

        return unique_clean_strings(v)


class SFIHasChildParentCandidate(BaseSchema):
    """One bounded parent endpoint candidate for a finalized child SFI."""

    canonical_statement_value: Optional[str] = Field(
        default=None,
        description=(
            "Parent candidate canonical controlled statement value, when available."
        ),
    )
    canonical_statement_value_key: Optional[str] = Field(
        default=None,
        description=(
            "Parent candidate normalized canonical controlled statement value key, "
            "when available."
        ),
    )
    description: str = Field(description="Parent candidate display text.", min_length=1)
    endpoint_id: str = Field(description="Selectable parent endpoint ID.", min_length=1)
    endpoint_kind: Literal["StandardsFramework", "StandardsFrameworkItem"]
    evidence_reasons: list[str] = Field(
        description="Deterministic evidence channels that selected this candidate.",
        min_length=1,
    )
    evidence_summary: list[str] = Field(default_factory=list)
    final_sfi_uuid: Optional[UUID] = Field(default=None)
    identity_scope_key: Optional[str] = Field(default=None)
    identity_scope_values: dict[str, str] = Field(default_factory=dict)
    is_root: bool = Field(default=False)
    normalized_statement_code: Optional[str] = Field(default=None)
    normalized_statement_type: Optional[NormalizedStatementType] = Field(default=None)
    scope_comparison: SFIHasChildScopeComparison = Field(
        default_factory=SFIHasChildScopeComparison
    )
    source_context_keys: list[str] = Field(default_factory=list)
    source_order: Optional[int] = Field(default=None, ge=0)
    source_page_indexes: list[int] = Field(default_factory=list)
    source_relations: list[SFIHasChildSourceRelation] = Field(default_factory=list)
    source_segment_ids: list[str] = Field(default_factory=list)
    source_window_indexes: list[int] = Field(default_factory=list)
    statement_code: Optional[str] = Field(default=None)
    statement_type: Optional[str] = Field(default=None)


class SFIHasChildParentRequirement(BaseSchema):
    """Allowed direct-parent statement type and resolved-child cardinality."""

    max_count: Optional[int] = Field(default=None, ge=1)
    min_count: int = Field(ge=0)
    parent_statement_type: str = Field(min_length=1)

    @field_validator("parent_statement_type", mode="before")
    @classmethod
    def clean_parent_statement_type(cls, v: str) -> str:
        """Clean and require the parent statement-type label.

        Parameters
        ----------
        v
            Raw parent statement-type label.

        Returns
        -------
        str
            Cleaned non-empty parent statement-type label.
        """

        return strip_and_require_non_empty_str(v)

    @model_validator(mode="after")
    def validate_cardinality(self) -> Self:
        """Validate that max_count is not below min_count.

        Returns
        -------
        Self
            Validated parent requirement.

        Raises
        ------
        ValueError
            If max_count is smaller than min_count.
        """

        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError("max_count must be greater than or equal to min_count.")

        return self


class SFIHasChildResolutionRequest(BaseSchema):
    """Prompt payload for producer/checker selection of direct hasChild parents."""

    child_parent_sets: list[SFIHasChildCandidateParentSet] = Field(min_length=1)
    request_id: str = Field(description="Deterministic request ID.", min_length=1)
    sfi_has_child_instructions: str = Field(
        description="Curriculum-specific hasChild producer instructions.", min_length=1
    )
    sfi_has_child_validation_instructions: str = Field(
        description=(
            "Curriculum-specific semantic audit instructions for the independent "
            "hasChild checker."
        ),
        min_length=1,
    )


class SFIHasChildResolutionResponse(BaseSchema):
    """Structured LLM output for one hasChild parent-selection request."""

    child_resolutions: list[_SFIHasChildChildResolution] = Field(min_length=1)
    request_id: str = Field(
        description="Request ID copied from the prompt.", min_length=1
    )


class SFIHasChildResolutionSummary(BaseSchema):
    """Aggregate summary for producer/checker hasChild relationship resolution."""

    candidate_parent_set_count: int = Field(default=0, ge=0)
    checker_corrected_response_count: int = Field(default=0, ge=0)
    checker_request_count: int = Field(default=0, ge=0)
    checker_verdict_count: int = Field(default=0, ge=0)
    edge_count: int = Field(default=0, ge=0)
    final_sfi_count: int = Field(default=0, ge=0)
    generator_request_count: int = Field(default=0, ge=0)
    generator_response_count: int = Field(default=0, ge=0)
    root_edge_count: int = Field(default=0, ge=0)
    sfi_to_sfi_edge_count: int = Field(default=0, ge=0)
    truncated_candidate_parent_set_count: int = Field(default=0, ge=0)
    unresolved_child_count: int = Field(default=0, ge=0)


class SFIHasChildSourceRelation(BaseSchema):
    """Deterministic child-relative source structure for one parent candidate.

    The relation describes exact source geometry recovered from persisted extraction
    windows and source anchors. It is evidence for producer/checker review, not an
    automatic semantic parent decision.
    """

    child_row_index: Optional[int] = Field(default=None, ge=0)
    child_source_text: str = Field(min_length=1)
    child_source_unit_id: str = Field(min_length=1)
    parent_column_end_index_exclusive: Optional[int] = Field(default=None, ge=1)
    parent_column_start_index: Optional[int] = Field(default=None, ge=0)
    parent_origin_row_index: Optional[int] = Field(default=None, ge=0)
    parent_source_text: str = Field(min_length=1)
    parent_source_unit_id: str = Field(min_length=1)
    relation_kind: Literal[
        "parent_cell_applies_to_child_row", "same_raw_table_row", "same_source_unit"
    ]
    source_segment_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_relation(self) -> Self:
        """Validate relation-specific source coordinates.

        Returns
        -------
        Self
            Validated source relation.

        Raises
        ------
        ValueError
            If identifiers, rows, or column bounds contradict the relation kind.
        """

        if (self.parent_column_start_index is None) != (
            self.parent_column_end_index_exclusive is None
        ):
            raise ValueError(
                "Parent source-relation column bounds must be both present or both "
                "absent."
            )

        if (
            self.parent_column_start_index is not None
            and self.parent_column_end_index_exclusive is not None
            and self.parent_column_end_index_exclusive <= self.parent_column_start_index
        ):
            raise ValueError(
                "parent_column_end_index_exclusive must be greater than "
                "parent_column_start_index."
            )

        if self.relation_kind == "same_source_unit":
            if self.child_source_unit_id != self.parent_source_unit_id:
                raise ValueError(
                    "same_source_unit relations require identical source unit IDs."
                )

        if self.relation_kind == "same_raw_table_row":
            if (
                self.child_row_index is None
                or self.parent_origin_row_index is None
                or self.child_row_index != self.parent_origin_row_index
            ):
                raise ValueError(
                    "same_raw_table_row relations require equal child and parent "
                    "row indexes."
                )

        if self.relation_kind == "parent_cell_applies_to_child_row":
            if (
                self.child_row_index is None
                or self.parent_origin_row_index is None
                or self.parent_column_start_index is None
                or self.parent_column_end_index_exclusive is None
            ):
                raise ValueError(
                    "parent_cell_applies_to_child_row relations require child row, "
                    "parent origin row, and parent column bounds."
                )

            if self.parent_origin_row_index >= self.child_row_index:
                raise ValueError(
                    "parent_cell_applies_to_child_row requires an earlier parent "
                    "origin row."
                )

        return self


class SFIHasChildValidationIssue(BaseSchema):
    """One source-grounded issue found by the hasChild checker LLM."""

    child_final_sfi_uuid: Optional[UUID] = Field(default=None)
    issue_type: str = Field(
        description="Short general issue category, such as wrong_scope or omission.",
        min_length=1,
    )
    message: str = Field(
        description="Specific source-grounded description of the issue.", min_length=1
    )
    parent_endpoint_ids: list[str] = Field(default_factory=list)
    severity: Literal["error", "warning"] = Field(
        description="Whether the issue requires correction or is advisory only."
    )

    @field_validator("issue_type", "message", mode="before")
    @classmethod
    def clean_required_issue_strings(cls, v: str) -> str:
        """Strip and require non-empty checker issue text.

        Parameters
        ----------
        v
            Raw issue text.

        Returns
        -------
        str
            Cleaned non-empty issue text.
        """

        return strip_and_require_non_empty_str(v)

    @field_validator("parent_endpoint_ids")
    @classmethod
    def clean_parent_endpoint_ids(cls, v: list[str]) -> list[str]:
        """Clean parent endpoint IDs referenced by a checker issue.

        Parameters
        ----------
        v
            Raw parent endpoint IDs.

        Returns
        -------
        list[str]
            Cleaned unique parent endpoint IDs.
        """

        return unique_clean_strings(v)


class SFIHasChildValidationVerdict(BaseSchema):
    """Independent checker verdict for one draft hasChild response."""

    corrected_response: Optional[SFIHasChildResolutionResponse] = Field(
        default=None,
        description=(
            "Complete corrected hasChild response when passed is false; null when "
            "the producer draft is accepted unchanged."
        ),
    )
    issues: list[SFIHasChildValidationIssue] = Field(default_factory=list)
    passed: bool = Field(
        description="True when the producer draft requires no material correction."
    )
    rationale: str = Field(
        description="Concise overall assessment of the producer draft.", min_length=20
    )
    request_id: str = Field(
        description="Request ID copied from the original request.", min_length=1
    )

    @field_validator("rationale", "request_id", mode="before")
    @classmethod
    def clean_required_verdict_strings(cls, v: str) -> str:
        """Strip and require non-empty checker verdict text.

        Parameters
        ----------
        v
            Raw verdict text.

        Returns
        -------
        str
            Cleaned non-empty verdict text.
        """

        return strip_and_require_non_empty_str(v)

    @model_validator(mode="after")
    def validate_verdict_contract(self) -> Self:
        """Validate pass/fail agreement with issues and corrected output.

        Returns
        -------
        Self
            Validated checker verdict.

        Raises
        ------
        ValueError
            If pass/fail state disagrees with corrected_response or error issues.
        """

        error_issues = [issue for issue in self.issues if issue.severity == "error"]

        if self.passed:
            if self.corrected_response is not None:
                raise ValueError(
                    "corrected_response must be null when validation passed is true."
                )

            if error_issues:
                raise ValueError(
                    "A passing validation verdict must not contain error issues."
                )
        else:
            if self.corrected_response is None:
                raise ValueError(
                    "corrected_response is required when validation passed is false."
                )

            if not error_issues:
                raise ValueError(
                    "A failing validation verdict must include at least one error issue."
                )

        return self


# Schemas for Academic Standards.
class AcademicStandardsExportSummary(BaseSchema):
    """Aggregate summary for the final Academic Standards KG export."""

    final_sfi_count: int = Field(ge=0)
    finalization_exclusion_summary: dict[str, int] = Field(default_factory=dict)
    framework_count: int = Field(ge=0)
    has_child_relationship_count: int = Field(ge=0)
    learning_commons_node_count: int = Field(ge=0)
    learning_commons_relationship_count: int = Field(ge=0)
    learning_commons_unresolved_fallback_relationship_count: int = Field(ge=0)
    relationship_unresolved_edge_count: int = Field(ge=0)


class AcademicStandardsKGBundle(BaseSchema):
    """Complete final Academic Standards KG bundle for one source framework."""

    entity_provenance: dict[str, Any] = Field(default_factory=dict)
    framework: StandardsFramework
    items: list[StandardsFrameworkItem]
    relationships_has_child: list[Relationship]
    summary: AcademicStandardsExportSummary
    unresolved_items: AcademicStandardsUnresolvedItems
    validation_report: AcademicStandardsValidationReport


class AcademicStandardsLCExportSummary(BaseSchema):
    """Combined summary for the merged AS+LC KG bundle."""

    academic_standards: AcademicStandardsExportSummary
    learning_components: LCGenerationSummary
    total_node_count: int = Field(ge=0)
    total_relationship_count: int = Field(ge=0)


class AcademicStandardsLCKGBundle(BaseSchema):
    """Complete merged AS+LC KG bundle for one source framework.

    Composes the final Academic Standards bundle content, verbatim, with
    the LC layer: LearningComponent nodes, primary supports relationships,
    merged entity provenance, combined summary, and a merged-graph
    validation report.
    """

    entity_provenance: dict[str, Any] = Field(default_factory=dict)
    framework: StandardsFramework
    items: list[StandardsFrameworkItem]
    learning_components: list[LearningComponent]
    relationships_has_child: list[Relationship]
    relationships_supports: list[Relationship]
    summary: AcademicStandardsLCExportSummary
    unresolved_items: AcademicStandardsLCUnresolvedItems
    validation_report: AcademicStandardsValidationReport


class AcademicStandardsLCUnresolvedItems(BaseSchema):
    """Unresolved report for the merged AS+LC KG bundle."""

    academic_standards: AcademicStandardsUnresolvedItems
    learning_components: LCUnresolvedItems


class AcademicStandardsUnresolvedItems(BaseSchema):
    """Final unresolved report for Academic Standards export artifacts."""

    finalization_exclusion_summary: dict[str, int] = Field(default_factory=dict)
    relationship_unresolved_edges: list[dict[str, Any]] = Field(default_factory=list)


class AcademicStandardsValidationReport(BaseSchema):
    """Validation report for the compiled Academic Standards KG export."""

    errors: list[str] = Field(default_factory=list)
    input_fingerprints: dict[str, str] = Field(default_factory=dict)
    learning_commons_export_schema_version: str
    object_counts: dict[str, int] = Field(default_factory=dict)
    passed: bool
    validation_checks: list[str] = Field(default_factory=list)


# Schemas for Learning Component generation.
LCAncestorPathStatus = Literal["resolved", "unresolved_ancestor_path"]
LCExclusionReason = Literal[
    "empty_text",
    "grouping_node",
    "not_a_leaf",
    "not_in_allowlist",
    "unresolved_ancestor_path",
]
LCSelectionMode = Literal["explicit_allowlist", "leaf_default"]


class LCAtomicSkill(BaseSchema):
    """One atomic teachable skill decomposed from an LC-source SFI."""

    confidence: float = Field(
        description=(
            "Model confidence that this skill is directly supported by the "
            "source SFI text."
        ),
        ge=0.0,
        le=1.0,
    )
    description: str = Field(
        description="The atomic teachable skill statement, in the SFI's source language.",
        min_length=1,
    )
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "2-5 short lowercase keyword tags in the skill's source language. "
            "Semantic nomination signal for LC dedup blocking and later "
            "cross-SFI grouping."
        ),
    )


class LCContextSFI(BaseSchema):
    """One ancestor or sibling SFI carried as disambiguation-only LC context."""

    case_identifier_uuid: UUID
    description: str = Field(min_length=1)
    statement_type: str = Field(min_length=1)


class LCDedupConflict(BaseSchema):
    """One merge link dropped by the LC dedup chaining guard."""

    reason: str = Field(min_length=1)
    text_a: str = Field(min_length=1)
    text_b: str = Field(min_length=1)


class LCDedupGroup(BaseSchema):
    """One multi-claim duplicate group after LC dedup clustering."""

    canonical_text: str = Field(min_length=1)
    member_texts: list[str] = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    sfi_uuids: list[UUID] = Field(min_length=1)


class LCDedupGroups(BaseSchema):
    """LC dedup grouping artifact: exact + semantic duplicate clusters."""

    candidate_pair_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    conflicts: list[LCDedupConflict] = Field(default_factory=list)
    exact_duplicate_claim_count: int = Field(ge=0)
    groups: list[LCDedupGroup] = Field(default_factory=list)
    judged_same_count: int = Field(ge=0)
    total_claim_count: int = Field(ge=0)
    unique_text_count: int = Field(ge=0)


class LCDedupPair(BaseSchema):
    """One nominated candidate pair for LC duplicate adjudication."""

    nomination_rules: list[str] = Field(min_length=1)
    pair_id: int = Field(ge=0)
    scope_key: str = Field(min_length=1)
    statement_types_a: list[str] = Field(default_factory=list)
    statement_types_b: list[str] = Field(default_factory=list)
    text_a: str = Field(min_length=1)
    text_b: str = Field(min_length=1)


class LCDedupPairVerdict(BaseSchema):
    """LLM verdict for one nominated duplicate-candidate pair."""

    pair_id: int = Field(ge=0)
    reason: str = Field(max_length=500, min_length=1)
    same_skill: bool


class LCDedupRequest(BaseSchema):
    """Prompt payload for one batch of LC duplicate adjudications."""

    pairs: list[LCDedupPair] = Field(min_length=1)
    request_id: str = Field(description="Deterministic request ID.", min_length=1)


class LCDedupResponse(BaseSchema):
    """Structured LLM output for one LC dedup adjudication request."""

    request_id: str = Field(
        description="Request ID copied from the prompt.", min_length=1
    )
    verdicts: list[LCDedupPairVerdict] = Field(min_length=1)


class LCEligibilityReport(BaseSchema):
    """Coverage report for LC-source SFI selection."""

    excluded: list[LCExcludedSFI] = Field(default_factory=list)
    lc_selection_mode: LCSelectionMode
    lc_source_exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)
    total_lc_source_sfis_considered: int = Field(ge=0)
    total_lc_source_sfis_eligible: int = Field(ge=0)
    total_lc_source_sfis_empty_text: int = Field(ge=0)
    total_lc_source_sfis_excluded: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        """Validate that eligibility counts reconcile.

        Returns
        -------
        Self
            The validated eligibility report.

        Raises
        ------
        ValueError
            If counts do not reconcile with the excluded records.
        """

        if (
            self.total_lc_source_sfis_eligible + self.total_lc_source_sfis_excluded
            != self.total_lc_source_sfis_considered
        ):
            raise ValueError(
                f"LC eligibility counts do not reconcile: eligible "
                f"({self.total_lc_source_sfis_eligible}) + excluded "
                f"({self.total_lc_source_sfis_excluded}) != considered "
                f"({self.total_lc_source_sfis_considered})."
            )
        if len(self.excluded) != self.total_lc_source_sfis_excluded:
            raise ValueError(
                f"LC eligibility excluded records ({len(self.excluded)}) do not "
                f"match total_lc_source_sfis_excluded "
                f"({self.total_lc_source_sfis_excluded})."
            )
        return self


class LCExcludedSFI(BaseSchema):
    """One SFI excluded from LC-source selection, with the exclusion reason."""

    final_sfi_uuid: UUID
    normalized_statement_type: NormalizedStatementType
    reason: LCExclusionReason
    statement_type: str


class LCFrameworkContext(BaseSchema):
    """Framework context attached once per LC generation request."""

    academic_subject: str = Field(min_length=1)
    in_language: LanguageField
    jurisdiction: str = Field(min_length=1)
    name: str = Field(min_length=1)


class LCGenerationFailure(BaseSchema):
    """One LC generation request that produced no valid decomposition."""

    error_message: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    sfi_uuids: list[UUID] = Field(min_length=1)


class LCGenerationRequest(BaseSchema):
    """Prompt payload for LLM decomposition of LC-source SFIs.

    Requests never carry `statement_code` as decomposition input: source PDFs
    can contain malformed or mismatched codes. The SFI text plus ancestor
    context is the semantic source of truth; codes remain metadata.
    """

    framework_context: LCFrameworkContext
    request_id: str = Field(description="Deterministic request ID.", min_length=1)
    sfis: list[LCRequestSFI] = Field(min_length=1)


class LCGenerationResponse(BaseSchema):
    """Structured LLM output for one LC generation request.

    Carries raw atomic-skill decompositions, not LearningComponent nodes:
    LC minting derives LC nodes (deterministic identity, provenance) from
    these.
    """

    items: list[LCResponseSFI] = Field(min_length=1)
    request_id: str = Field(
        description="Request ID copied from the prompt.", min_length=1
    )


class LCGenerationSummary(BaseSchema):
    """Aggregate summary for the LC generation phase."""

    lc_confidence_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Claim decomposition confidences bucketed by first decimal.",
    )
    lc_count_by_language: dict[str, int] = Field(default_factory=dict)
    lc_count_by_source_statement_type: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "LearningComponent counts per claiming source statement type; a "
            "node claimed by several types counts once per type."
        ),
    )
    lc_dedup_candidate_pair_count: int = Field(ge=0)
    lc_dedup_conflict_count: int = Field(ge=0)
    lc_dedup_judged_same_count: int = Field(ge=0)
    lc_generation_failed_sfis_count: int = Field(ge=0)
    lc_max_splits_observed: int = Field(ge=0)
    lc_multi_claim_lc_count: int = Field(ge=0)
    lc_multi_parent_lc_count: int = Field(ge=0)
    lc_selection_mode: LCSelectionMode
    lc_source_exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)
    lc_splits_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Atomic-skill counts per decomposed seed SFI.",
    )
    llm_request_count: int = Field(ge=0)
    llm_response_count: int = Field(ge=0)
    manual_review_overrides: Optional[dict[str, Any]] = Field(default=None)
    total_lc_claims: int = Field(ge=0)
    total_lc_source_sfis_considered: int = Field(ge=0)
    total_lc_source_sfis_eligible: int = Field(ge=0)
    total_lc_source_sfis_empty_text: int = Field(ge=0)
    total_lc_source_sfis_excluded: int = Field(ge=0)
    total_lcs: int = Field(ge=0)
    total_supports_edges: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class LCRequestSFI(BaseSchema):
    """One LC-source SFI in a generation request, with its prompt context."""

    ancestor_path: list[LCContextSFI] = Field(
        default_factory=list,
        description=(
            "Resolved hasChild ancestors ordered framework root first, direct "
            "parent last. Disambiguation-only context and the authoritative "
            "source of grade/curriculum scope."
        ),
    )
    ancestor_path_status: LCAncestorPathStatus = Field(
        default="resolved",
        description=(
            "'unresolved_ancestor_path' when the seed's ancestor path crosses "
            "an unresolved root-fallback edge (only possible when the manual-"
            "review override admits such seeds); its ancestor_path is then "
            "incomplete and must not be used to derive curriculum scope."
        ),
    )
    description: str = Field(
        description="Final source-backed SFI description text.", min_length=1
    )
    final_sfi_uuid: UUID
    language: str = Field(
        description="Source language tag chosen for the final SFI.", min_length=1
    )
    siblings: list[LCContextSFI] = Field(
        default_factory=list,
        description=(
            "Sibling SFIs under the same hasChild parent, populated only when "
            "lc_include_sibling_context is configured. Disambiguation-only."
        ),
    )
    statement_type: str = Field(min_length=1)


class LCResponseSFI(BaseSchema):
    """Atomic skills decomposed from one LC-source SFI.

    A single skill is a valid decomposition: an already-atomic SFI yields
    exactly one cleanly restated skill.
    """

    sfi_uuid: UUID = Field(description="Final SFI UUID copied from the request batch.")
    skills: list[LCAtomicSkill] = Field(min_length=1)


class LCUnresolvedItems(BaseSchema):
    """Unresolved report for the LC generation phase."""

    lc_generation_failures: list[LCGenerationFailure] = Field(default_factory=list)
    lc_source_exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)


# Schemas for nodes.
class StandardsFramework(_CaseIdentifierMixin, _DateValidationMixin, BaseSchema):
    """Root node for a standards framework (typically one per PDF).

    This represents the top-level standards document/container in the LearningCommons
    KG. All StandardsFrameworkItems (SFIs) should be reachable from this framework via
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
    is_current: bool = Field(
        default=True,
        description=(
            "Whether this framework is the current version represented for its "
            "jurisdiction and subject."
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

        return strip_and_require_non_empty_str(v)


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
    alternate_statement_code: Optional[str] = Field(
        default=None,
        description=(
            "Optional source-authoritative alternate code used by practitioners. "
            "Pipeline-normalized code variants must not be placed here unless the "
            "source identifies them as genuine alternate codes."
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
    is_current: bool = Field(
        default=True,
        description=(
            "Whether this standards item belongs to the current version of its "
            "framework."
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

        return strip_and_require_non_empty_str(v)

    @field_validator(
        "alternate_statement_code", "statement_code", "statement_type", mode="before"
    )
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

        return strip_and_require_non_empty_str(v)

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


# Schemas for Learning Commons.
class _LearningCommonsWireModel(BaseSchema):
    """Base model for Learning Commons JSONL wire-format records."""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class LearningCommonsNode(_LearningCommonsWireModel):
    """One Learning Commons graph-node JSONL record."""

    identifier: str
    labels: list[Literal["StandardsFramework", "StandardsFrameworkItem"]]
    properties: (
        LearningCommonsStandardsFrameworkProperties
        | LearningCommonsStandardsFrameworkItemProperties
    )
    type: Literal["node"] = "node"

    @model_validator(mode="after")
    def validate_node_shape(self) -> LearningCommonsNode:
        """Validate label cardinality, property type, and identifier duplication.

        Returns
        -------
        LearningCommonsNode
            The validated Learning Commons node record.

        Raises
        ------
        ValueError
            If labels do not identify exactly one supported node type, the property
            model does not match the label, or identifiers differ.
        """

        if len(self.labels) != 1:
            raise ValueError(
                "LearningCommonsNode.labels must contain exactly one label."
            )

        label = self.labels[0]

        if label == "StandardsFramework" and not isinstance(
            self.properties, LearningCommonsStandardsFrameworkProperties
        ):
            raise ValueError(
                "StandardsFramework nodes require framework property records."
            )

        if label == "StandardsFrameworkItem" and not isinstance(
            self.properties, LearningCommonsStandardsFrameworkItemProperties
        ):
            raise ValueError(
                "StandardsFrameworkItem nodes require item property records."
            )

        if self.identifier != self.properties.identifier:
            raise ValueError(
                "LearningCommonsNode identifier must equal properties.identifier."
            )

        return self


class LearningCommonsRelationship(_LearningCommonsWireModel):
    """One Learning Commons graph-relationship JSONL record."""

    identifier: str
    label: str
    properties: LearningCommonsRelationshipProperties
    source_identifier: str
    source_labels: list[str]
    target_identifier: str
    target_labels: list[str]
    type: Literal["relationship"] = "relationship"

    @model_validator(mode="after")
    def validate_relationship_shape(self) -> LearningCommonsRelationship:
        """Validate relationship label, identifiers, and endpoint labels.

        Returns
        -------
        LearningCommonsRelationship
            The validated Learning Commons relationship record.

        Raises
        ------
        ValueError
            If the outer label or identifier disagrees with the property record, or
            endpoint labels are empty.
        """

        if self.identifier != self.properties.identifier:
            raise ValueError(
                "LearningCommonsRelationship identifier must equal properties.identifier."
            )

        if self.label != self.properties.relationship_type:
            raise ValueError(
                "LearningCommonsRelationship label must equal properties.relationshipType."
            )

        if not self.source_labels or not self.target_labels:
            raise ValueError(
                "LearningCommonsRelationship endpoint labels must be non-empty."
            )

        return self


class LearningCommonsRelationshipProperties(_LearningCommonsWireModel):
    """String-valued properties for one Learning Commons relationship record."""

    attribution_statement: str = Field(alias="attributionStatement")
    author: str
    date_created: str | None = Field(default=None, alias="dateCreated")
    date_modified: str | None = Field(default=None, alias="dateModified")
    description: str
    identifier: str
    license: str
    provider: str
    relationship_type: str = Field(alias="relationshipType")
    resolution_status: str | None = Field(default=None, alias="resolutionStatus")
    source_entity: str = Field(alias="sourceEntity")
    source_entity_key: str = Field(alias="sourceEntityKey")
    source_entity_value: str = Field(alias="sourceEntityValue")
    target_entity: str = Field(alias="targetEntity")
    target_entity_key: str = Field(alias="targetEntityKey")
    target_entity_value: str = Field(alias="targetEntityValue")


class LearningCommonsStandardsFrameworkItemProperties(_LearningCommonsWireModel):
    """String-valued properties for one Learning Commons standards-item node."""

    academic_subject: str = Field(alias="academicSubject")
    adoption_status: str = Field(alias="adoptionStatus")
    alternate_statement_code: str | None = Field(
        default=None, alias="alternateStatementCode"
    )
    attribution_statement: str = Field(alias="attributionStatement")
    author: str
    case_identifier_uri: str = Field(alias="caseIdentifierURI")
    case_identifier_uuid: str = Field(alias="caseIdentifierUUID")
    date_created: str | None = Field(default=None, alias="dateCreated")
    date_modified: str | None = Field(default=None, alias="dateModified")
    description: str
    grade_level: str | None = Field(default=None, alias="gradeLevel")
    identifier: str
    in_language: str = Field(alias="inLanguage")
    is_current: Literal["true", "false"] = Field(alias="isCurrent")
    jurisdiction: str
    license: str
    normalized_statement_type: str | None = Field(
        default=None, alias="normalizedStatementType"
    )
    notes: str | None = None
    provider: str
    statement_code: str | None = Field(default=None, alias="statementCode")
    statement_type: str | None = Field(default=None, alias="statementType")


class LearningCommonsStandardsFrameworkProperties(_LearningCommonsWireModel):
    """String-valued properties for one Learning Commons framework node record."""

    academic_subject: str = Field(alias="academicSubject")
    adoption_status: str = Field(alias="adoptionStatus")
    attribution_statement: str = Field(alias="attributionStatement")
    author: str
    case_identifier_uri: str = Field(alias="caseIdentifierURI")
    case_identifier_uuid: str = Field(alias="caseIdentifierUUID")
    date_created: str | None = Field(default=None, alias="dateCreated")
    date_modified: str | None = Field(default=None, alias="dateModified")
    description: str | None = None
    identifier: str
    in_language: str = Field(alias="inLanguage")
    is_current: Literal["true", "false"] = Field(alias="isCurrent")
    jurisdiction: str
    license: str
    name: str
    notes: str | None = None
    provider: str


# CURRENTLY UNUSED #
# Schemas for LLM responses.
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

        return strip_and_require_non_empty_str(v)
