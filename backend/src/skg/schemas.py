"""This module contains top-level Pydantic models."""

# Future Library
from __future__ import annotations

# Standard Library
import re

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Optional, Self

# Third Party Library
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    field_validator,
    model_validator,
)

# Package Library
from skg.utils.constants import (
    CONTEXT_GROUPINGS_ROLE_ORDER,
    NodeRole,
    SpineSplitApplyTo,
    SpineViolationPolicy,
)
from skg.utils.general import make_dir, validate_bbox_order, validate_bcp47

# Common fields with descriptions.
BBox = Annotated[
    list[float],
    AfterValidator(validate_bbox_order),
    Field(
        description="Bounding box [x0, y0, x1, y1] in absolute pixels (px) relative to the image dimensions.",
        max_length=4,
        min_length=4,
    ),
]
BCP47Str = Annotated[str, AfterValidator(validate_bcp47)]
LanguageField = Annotated[
    BCP47Str,
    Field(
        description="Strict BCP-47 language code (e.g., 'en', 'sw'). Use 'und' if unknown; use 'mul' if mixed languages.",
    ),
]


class BaseSchema(BaseModel):
    """Base model for all schemas."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Config schemas.
class ExtractionConfig(BaseSchema):
    """Configuration for page IR extraction from a PDF document."""

    always_double_check_first_attempt: bool = Field(
        False,
        description="Force LLM retry on first attempt. Useful for difficult/messy PDFs.",
    )
    country: str = Field(
        ..., description="The country associated with the PDF document."
    )
    dpi: int = Field(250, description="Render DPI for page images.")
    end_page: Optional[int] = Field(
        None, description="0-based end page (exclusive). Default: to end."
    )
    languages: list[LanguageField] = Field(
        ...,
        description="One or more languages associated with the PDF document (e.g. en-US, fr-FR).",
        min_length=1,
    )
    model: str = Field(
        "gpt-5.2-2025-12-11", description="OpenAI model for page IR extraction."
    )
    output_dir: Path = Field(..., description="Output directory root.")
    overwrite: bool = Field(False, description="Overwrite existing page IR JSONs.")
    pdf_fp: FilePath = Field(
        ...,
        description="The file path to the PDF document to extract curriculum data from.",
    )
    start_page: int = Field(0, description="0-based start page (inclusive).")
    use_text_layer_hints: bool = Field(
        True,
        description="Whether to extract and use text layer hints from the PDF during extraction.",
    )
    year: Optional[int] = Field(
        None, description="Document year (optional; overrides any inferred year)."
    )

    @model_validator(mode="after")
    def check_page_range(self) -> Self:
        """Ensure that if end_page is provided, it is strictly greater than start_page.

        Returns
        -------
        Self
            The passed in ExtractionConfig.

        Raises
        ------
        ValueError
            If end_page is not greater than start_page.
        """

        if self.end_page is not None and self.end_page <= self.start_page:
            raise ValueError(
                f"end_page ({self.end_page}) must be greater than start_page ({self.start_page})."
            )

        return self

    @field_validator("output_dir")
    @classmethod
    def ensure_output_dir_exists(cls, v: Path) -> Path:
        """Ensure the output directory exists. If it doesn't, it creates it (including
        parents).

        Parameters
        ----------
        v
            The output directory path.

        Returns
        -------
        Path
            The validated output directory path.
        """

        make_dir(v)

        return v


class VerificationConfig(BaseSchema):
    """Configuration for page IR verification from a PDF document."""

    always_double_check_first_attempt: bool = Field(
        False,
        description="Force LLM retry on first attempt. Useful for difficult/messy PDFs.",
    )
    end_page: Optional[int] = Field(
        None, description="0-based end page (exclusive). Default: to end."
    )
    min_confidence_to_patch: float = Field(
        0.75,
        ge=0.0,
        le=1.0,
        description="Only apply compiled continuity decisions/repeats_header patches when verdict.confidence >= this threshold.",
    )
    model: str = Field(
        "gpt-5.2-2025-12-11", description="OpenAI model for page IR verification."
    )
    start_page: int = Field(0, description="0-based start page (inclusive).")

    @model_validator(mode="after")
    def check_page_range(self) -> Self:
        """Ensure that if end_page is provided, it is strictly greater than start_page.

        Returns
        -------
        Self
            The passed in ExtractionConfig.

        Raises
        ------
        ValueError
            If end_page is not greater than start_page.
        """

        if self.end_page is not None and self.end_page <= self.start_page:
            raise ValueError(
                f"end_page ({self.end_page}) must be greater than start_page ({self.start_page})."
            )

        return self


class StitchingConfig(BaseSchema):
    """Configuration for document IR stitching from verified page IR JSONs.

    NB: `table_filldown_group_cols_max` (fill-down/rowspan reconstruction)

    1. Many curriculum PDFs use **merged cells/rowspans** in the *leftmost grouping
        columns* (e.g., **Topic**, **Sub-topic**, **Strand**, **Theme**). When
        extracted, those merged cells often appear as **blank cells** on subsequent
        rows.
    2. `table_filldown_group_cols_max` controls **how many leading columns** should
        have these visually empty cells **filled down** from the most recent non-empty
        value above. This reconstructs the intended grouping structure without changing
        the underlying table content.
            - Only the **first `table_filldown_group_cols_max` columns** are eligible
                for fill-down.
            - Columns beyond this are treated as **leaf/content columns** (e.g.,
                competences/outcomes, activities, expected standards), where blanks
                typically mean **“no content / not applicable”**, not “repeat previous”.
    3. Why not set it very large (e.g., 10)? Because non-grouping columns often contain
        legitimate blanks (or extraction misses). A large value can silently “invent”
        repeated activities/standards by copying prior rows, corrupting the extracted
        table semantics.
    3. Recommended default: 2. This matches the common pattern: **Topic + Sub-topic**
        (or equivalent) are the main rowspan/grouping columns in most primary tables.
    """

    keep_artifacts: bool = Field(
        False,
        description="Whether to keep artifacts such as page numbers, headers, footers, etc. after stitching.",
    )
    max_section_path_length: int = Field(
        12,
        description="Maximum number of section paths in the stack to maintain. For most PDFs, 12 is a good number that will capture enough breadcrumb context for heading traces.",
    )
    min_link_score: float = Field(
        3, description="Minimum link score to consider for stitching.", ge=0
    )
    overwrite: bool = Field(False, description="Overwrite existing document IR JSON.")
    repair_hyphenation: bool = Field(
        True, description="Whether to repair hyphenation for stitched text."
    )
    sort_items_by_bbox: bool = Field(
        False,
        description="Whether to sort items by their bounding box positions before stitching.",
    )
    table_filldown_enabled: bool = Field(
        True, description="Whether to enable table filldown during stitching."
    )
    table_filldown_group_cols_max: int = Field(
        3, description="Maximum number of group columns for table filldown.", ge=0
    )


class SpineCorrection(BaseSchema):
    """A single spine correction applied to a SegmentDecision."""

    kind: str
    detail: str


class SpineCorrectionReport(BaseSchema):
    """Report of spine corrections applied to a SegmentDecisionSet."""

    decision_results: dict[str, list[SpineCorrection]] = Field(default_factory=dict)
    decision_set_id: str
    flagged_decisions: list[str] = Field(default_factory=list)
    spine_name: str
    warnings: list[str] = Field(default_factory=list)


class SpineEmitGroupingTemplate(BaseSchema):
    """A single emitted grouping produced by a split rule.

    `title_template` uses regex capture groups via Match.expand, e.g.:
      - "GRADE \\1"
      - "\\2" (subject)
    """

    inherit_source_label: bool = Field(
        True, description="If True, copy source_label from the original grouping."
    )
    role: NodeRole = Field(..., description="Role of the emitted grouping.")
    source_label: str | None = Field(
        None,
        description="If provided, overrides the emitted grouping's source_label (verbatim).",
    )
    title_template: str = Field(
        ..., description="Regex expansion template using capture groups (Match.expand)."
    )


class SpineSplitRule(BaseSchema):
    """Deterministic splitting of fused headings/groupings into multiple groupings."""

    match: str = Field(..., description="Regex pattern used to match the input title.")
    flags: int = Field(
        re.IGNORECASE,
        description="Regex flags (int). Default IGNORECASE.",
    )
    apply_to: SpineSplitApplyTo = Field(
        SpineSplitApplyTo.ANY,
        description="Which grouping list(s) this rule may rewrite.",
    )
    emit: list[SpineEmitGroupingTemplate] = Field(
        ...,
        min_length=1,
        description="One or more emitted groupings produced from capture groups.",
    )

    @model_validator(mode="after")
    def _validate_regex(self) -> SpineSplitRule:
        """Validate that the regex pattern is valid.

        Returns
        -------
        SpineSplitRule
            The validated SpineSplitRule instance.

        Raises
        ------
        re.error
            If the regex pattern is invalid.
        """

        re.compile(self.match, self.flags)  # Raises if invalid

        return self


class SpineNormalizeConfig(BaseSchema):
    """Deterministic normalization knobs (no guessing)."""

    drop_stage_if_grade_present: bool = Field(
        False,
        description=(
            "If True, drop STAGE when any GRADE_LEVEL is present. "
            "Prefer keeping False and using wrapper patterns instead."
        ),
    )
    drop_stage_if_matches_wrapper_patterns: bool = Field(
        True,
        description="If True, drop STAGE titles that match wrapper patterns (non-curricular wrappers).",
    )
    stage_wrapper_title_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns (case-insensitive recommended) that identify non-curricular "
            "STAGE wrappers (e.g., 'lower primary', 'basic education')."
        ),
    )

    # Grade-band suppression (remove bands from GRADE_LEVEL containers)
    drop_grade_bands: bool = Field(
        False,
        description=(
            "If True, drop grade-band labels like 'Grades 1–3' from context_groupings. "
            "This should NOT delete the information; it can be recorded in spine notes."
        ),
    )
    grade_band_title_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "Regex patterns that identify grade-band titles (e.g., '^grades?\\s*\\d+\\s*[-–]\\s*\\d+')."
        ),
    )

    # Ordering / casing
    role_order: list[NodeRole] = Field(
        default_factory=lambda: list(CONTEXT_GROUPINGS_ROLE_ORDER),
        description="Canonical role order used when reordering grouping lists.",
    )
    normalize_whitespace: bool = Field(
        True,
        description="Collapse internal whitespace and trim titles during normalization.",
    )
    casefold_titles_for_matching: bool = Field(
        True,
        description="Casefold titles for internal matching/deduping (does not change stored title unless you choose).",
    )

    @model_validator(mode="after")
    def _validate_role_order(self) -> SpineNormalizeConfig:
        """Validate that role_order contains no duplicates.

        Returns
        -------
        SpineNormalizeConfig
            The validated SpineNormalizeConfig instance.

        Raises
        ------
        ValueError
            If role_order contains duplicate roles.
        """

        seen: set[NodeRole] = set()

        for r in self.role_order:
            if r in seen:
                raise ValueError(f"normalize.role_order contains duplicate role: {r}")

            seen.add(r)

        return self


class SpineConfig(BaseSchema):
    """Policy config for deterministic 'spine correction' between LLM decisions and compilation."""

    allow_partial_context: bool = Field(
        True,
        description="If True, allow emitting with a partial outer context (do not force missing roles).",
    )
    block_local_roles: list[NodeRole] = Field(
        default_factory=list,
        description="Allow-list for SegmentDecision.groupings[] (blocks).",
    )
    disallowed_block_local_roles: list[NodeRole] = Field(
        default_factory=list,
        description="Explicit disallow-list for block-local groupings.",
    )
    disallowed_outer_roles: list[NodeRole] = Field(
        default_factory=list, description="Explicit disallow-list for outer context."
    )
    disallowed_row_roles: list[NodeRole] = Field(
        default_factory=list,
        description="Explicit disallow-list for table row-local groupings.",
    )
    name: str = Field(
        ...,
        description="Human-readable spine template name (e.g., 'zambia_competence_tables').",
    )
    normalize: SpineNormalizeConfig = Field(
        default_factory=SpineNormalizeConfig,
        description="Deterministic normalization knobs.",
    )
    outer_context_roles: list[NodeRole] = Field(
        ...,
        min_length=0,
        description="Allow-list for SegmentDecision.context_groupings[].",
    )
    relocate_table_local_only_roles_to_rows: bool = Field(
        False,
        description="If True, relocate table_local_only_roles from context_groupings to each RowDecision.groupings (when rows are present).",
    )
    row_roles: list[NodeRole] = Field(
        default_factory=list,
        description="Allow-list for RowDecision.groupings[] (tables).",
    )
    split_rules: list[SpineSplitRule] = Field(
        default_factory=list, description="Deterministic regex-based split rules."
    )
    table_local_only_roles: list[NodeRole] = Field(
        default_factory=list,
        description="Roles that MUST NOT appear in table outer context. If seen in context_groupings, the corrector may relocate them to each row.groupings deterministically.",
    )
    violation_policy: SpineViolationPolicy = Field(
        SpineViolationPolicy.FLAG_UNRESOLVED,
        description="What to do when constraints cannot be satisfied without guessing.",
    )

    @model_validator(mode="after")
    def _validate_roles(self) -> SpineConfig:
        """Validate role lists for duplicates and conflicts.

        Returns
        -------
        SpineConfig
            The validated SpineConfig instance.

        Raises
        ------
        ValueError
            If any role lists contain duplicates or conflicting roles.
        """

        def _ensure_unique(*, name: str, roles: list[NodeRole]) -> None:
            """Ensure that the given role list contains unique roles.

            Parameters
            ----------
            name
                The name of the role list (for error messages).
            roles
                The list of roles to check.

            Raises
            ------
            ValueError
                If duplicate roles are found in the list.
            """

            if len(set(roles)) != len(roles):
                raise ValueError(f"{name} contains duplicate roles: {roles}")

        _ensure_unique(name="block_local_roles", roles=self.block_local_roles)
        _ensure_unique(
            name="disallowed_block_local_roles", roles=self.disallowed_block_local_roles
        )
        _ensure_unique(name="disallowed_outer_roles", roles=self.disallowed_outer_roles)
        _ensure_unique(name="disallowed_row_roles", roles=self.disallowed_row_roles)
        _ensure_unique(name="outer_context_roles", roles=self.outer_context_roles)
        _ensure_unique(name="row_roles", roles=self.row_roles)
        _ensure_unique(name="table_local_only_roles", roles=self.table_local_only_roles)

        # Disallowed roles should not be included in allow-lists.
        if set(self.disallowed_block_local_roles) & set(self.block_local_roles):
            overlap = set(self.disallowed_block_local_roles) & set(
                self.block_local_roles
            )
            raise ValueError(
                f"disallowed_block_local_roles overlaps block_local_roles: {overlap}"
            )
        if set(self.disallowed_outer_roles) & set(self.outer_context_roles):
            overlap = set(self.disallowed_outer_roles) & set(self.outer_context_roles)
            raise ValueError(
                f"disallowed_outer_roles overlaps outer_context_roles: {overlap}"
            )

        if set(self.disallowed_row_roles) & set(self.row_roles):
            overlap = set(self.disallowed_row_roles) & set(self.row_roles)
            raise ValueError(f"disallowed_row_roles overlaps row_roles: {overlap}")

        # Table-local-only roles should not be allowed in outer context for tables.
        overlap = set(self.table_local_only_roles) & set(self.outer_context_roles)
        if overlap and not self.relocate_table_local_only_roles_to_rows:
            raise ValueError(
                "table_local_only_roles overlaps outer_context_roles but relocation is disabled: "
                f"{overlap}"
            )

        return self


class CreateCanonicalConfig(BaseSchema):
    """Configuration for canonical IR creation from document IR."""

    always_double_check_first_attempt: bool = Field(
        False,
        description="Force LLM retry on first attempt. Useful for difficult/messy PDFs.",
    )
    canonical_grouping_min_confidence: float = Field(
        0.80,
        description="Minimum confidence level for canonical grouping.",
        ge=0.0,
        le=1.0,
    )
    max_table_rows_per_decision: int | None = Field(
        default=None,
        description="If set, only TABLE segments with *body rows* > this value are chunked into multiple SegmentDecisions. Blocks are never chunked.",
    )
    model: str = Field(
        "gpt-5.2-2025-12-11", description="OpenAI model for canonical IR."
    )
    overwrite: bool = Field(False, description="Overwrite existing canonical IR JSON.")
    segment_decision_conf_threshold: float = Field(
        0.75,
        description="The low confidence threshold for segment decisions. This is reflected in the segment decision system prompt for the LLM.",
        ge=0.0,
        le=1.0,
    )
    spine_policy: SpineConfig = Field(
        ...,
        description="Spine correction policy applied after LLM SegmentDecisions and before deterministic canonical compilation.",
    )
    structural_leaf_warn_threshold: float = Field(
        0.75,
        description="The confidence threshold below which structural leaves will emit warnings.",
        ge=0.0,
        le=1.0,
    )


class CreateKGConfig(BaseSchema):
    """Configuration for knowledge graph creation from canonical IR."""

    overwrite: bool = Field(False, description="Overwrite existing knowledge graphs.")


class RunConfig(BaseSchema):
    """Pydantic model for run configuration."""

    page_ir_extraction: ExtractionConfig
    page_ir_verification: VerificationConfig
    document_ir: StitchingConfig
    canonical_ir: CreateCanonicalConfig
    kgs: CreateKGConfig


class RunCtx(BaseSchema):
    """Pydantic model for run metadata."""

    completed_at: Optional[datetime] = None
    extra: dict[str, Any] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)
    run_id: str
    started_at: Optional[datetime] = None


# Global schemas.
class Limits(BaseSchema):
    """Pydantic model for global limits."""

    max_retry_attempts: int = Field(
        10, ge=0, description="Must be a non-negative integer"
    )


class ValidatorCall(BaseSchema):
    """Pydantic model for API response validation."""

    num_retries: int = 3
    validator_module: Callable[..., Any]
    validator_kwargs: dict[str, Any] = Field(default_factory=dict)
