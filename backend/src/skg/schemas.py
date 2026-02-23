"""This module contains top-level Pydantic models."""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Optional, Self, cast
from uuid import UUID

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
    DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER,
    NodeRole,
    SegmentDecisionType,
)
from skg.utils.general import make_dir, validate_bbox_order, validate_bcp47

# Common fields with descriptions.
AuxStatementHandling = Literal[
    "drop", "export_as_sfi_other", "attach_to_expectation_metadata"
]
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
ExportDialect = Literal["lc_public_strict", "global_relaxed"]
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
    next_page_crop_padding_px: int = Field(
        120,
        description="When cropping the top of page N+1 for verification, include this many extra pixels below the selected next candidate bbox.",
        ge=0,
    )
    start_page: int = Field(0, description="0-based start page (inclusive).")

    @model_validator(mode="after")
    def check_page_range(self) -> Self:
        """Ensure that if end_page is provided, it is strictly greater than start_page.

        Returns
        -------
        Self
            The passed in VerificationConfig.

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
        1.0, description="Minimum link score to consider for stitching.", ge=0
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
        1, description="Maximum number of group columns for table filldown.", ge=0
    )
    verification_auto_stitch_confidence: float = Field(
        0.75,
        description="If a verified link has confidence >= this value, it will be automatically stitched.",
    )


class CreateCanonicalConfig(BaseSchema):
    """Configuration for canonical IR creation from document IR."""

    bind_unknown_caption: bool = Field(
        True,
        description="Whether to bind captions whose kind cannot be confidently classified during deterministic caption→table binding.",
    )
    caption_max_gap_segments: int = Field(
        2,
        description="Maximum number of non-table segments allowed between a caption block and the table it binds to.",
        ge=0,
    )
    caption_max_page_distance: int = Field(
        1,
        description="Maximum page distance allowed between a caption block and the table it binds to.",
        ge=0,
    )
    curriculum_skeleton_fp: FilePath = Field(
        ...,
        description="Filesystem path to the CurriculumSkeleton JSON used for deterministic segment decisions.",
    )
    max_skip_distance: int = Field(
        2,
        description="Maximum number of curriculum skeleton nodes to probe ahead during forward-only matching.",
        ge=1,
    )
    overwrite: bool = Field(
        False,
        description="Overwrite existing canonical IR artifacts on disk (e.g., segment decisions/canonical IR JSON).",
    )


class CreateKGConfig(BaseSchema):
    """Configuration for knowledge graph creation from canonical IR.

    Notes
    -----
    1. export_dialect defaults to "global_relaxed". We *can* keep "lc_public_strict" as
        an option for internal experiments, but the schemas/models are intentionally
        non-US-centric.
    2. namespace_uuid MUST be pinned and never changed once you start generating IDs.
    """

    academic_subject_default: str = Field(
        description=(
            "Default high-level academic subject classification for the framework "
            "(e.g., Mathematics, English Language Arts, Science). Used when canonical "
            "IR does not provide a subject (or when exporting a single subject partition)."
        ),
    )
    adoption_status: str = Field(
        description=(
            "Adoption status of the framework (e.g., Draft, Adopted). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    always_double_check_first_attempt: bool = Field(
        False,
        description="Force LLM retry on first attempt. Useful for difficult/messy PDFs.",
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
    aux_statement_parenting: Literal["as_siblings", "under_expectation"] = Field(
        default="as_siblings",
        description=(
            "If exporting guidance/descriptors as SFIs, choose whether they remain "
            "siblings under the grouping parent or are re-parented under the "
            "expectation they belong to."
        ),
    )
    case_uri_base: str = Field(
        default="urn:lc:case:",
        description="Stable CASE identifier URI prefix (e.g., urn:lc:case:).",
    )
    description_text_policy: Literal["source", "prefer_text_en"] = "source"
    descriptor_handling: AuxStatementHandling = Field(
        default="export_as_sfi_other",
        description="How to handle descriptor statements during KG export.",
    )
    export_dialect: ExportDialect = "global_relaxed"
    export_in_language_policy: Literal["default", "source"] = "source"
    generate_progressions: bool = True
    grouping_role_policy: Literal["loose", "whitelist"] = Field(
        default="loose",
        description=(
            "How to interpret node roles as hierarchy groupings during standards export. "
            "'loose' = current behavior (any non-statement role becomes a grouping). "
            "'whitelist' = only roles in grouping_roles_whitelist are groupings."
        ),
    )
    grouping_roles_whitelist: set[NodeRole] = Field(
        default_factory=lambda: set(DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER)
        - {NodeRole.PROSE},
        description=(
            "When grouping_role_policy='whitelist', only these roles count as groupings "
            "(emitted as normalizedStatementType='Standard Grouping', eligible for pruning, "
            "and used as aux-parenting anchors). Default excludes PROSE."
        ),
    )
    guidance_handling: AuxStatementHandling = Field(
        default="drop",
        description="How to handle guidance statements during KG export.",
    )
    jurisdiction_default: str = Field(
        description=(
            "Default jurisdiction that issued the framework (e.g., Zambia, Uganda). "
            "Used when canonical IR does not provide jurisdiction."
        ),
    )
    language_default: LanguageField
    learning_component_policy: Literal["1_to_1", "split_bullets"] = "1_to_1"
    lc_max_splits_per_standard: int = Field(
        default=25,
        description="Maximum number of LearningComponents to emit per Standard SFI when splitting.",
        ge=1,
    )
    license: str = Field(
        description=(
            "License string for the framework content. This may be an SPDX-like label "
            "or a publisher-defined license statement; must be present even if it is "
            "a conservative placeholder."
        ),
    )
    model: str = Field("gpt-5.2-2025-12-11", description="OpenAI model for KGs.")
    namespace_uuid: UUID = Field(
        default=UUID("b9a2b2d5-0f6c-4f3f-8d32-b7a66f999c5a"),
        description="Pinned UUID namespace used with uuid5 for deterministic IDs.",
    )
    non_grouping_role_handling: Literal["drop", "export_as_sfi_other"] = Field(
        default="drop",
        description=(
            "When grouping_role_policy='whitelist': what to do with nodes that are neither "
            "statement roles (expectation/descriptor/guidance) nor allowed groupings. "
            "'drop' removes them; 'export_as_sfi_other' emits them as SFIs with type 'Other'."
        ),
    )
    non_standard_columns_signature: set[str] = Field(
        default_factory=set,
        description=(
            "Normalized columns signature that identify non-standards tables when "
            "using by_columns_signature. Example columns signature: "
            "'cinyanja term 2 - weekly schedule|||monday|tuesday|wednesday|thursday|friday'."
        ),
    )
    non_standard_decision_types: set[SegmentDecisionType] = Field(
        default_factory=lambda: {SegmentDecisionType.IGNORE},
        description=(
            "Decision types that should be treated as non-standards and dropped "
            "when using by_decision_type."
        ),
    )
    non_standard_segment_drop_policy: list[
        Literal["by_columns_signature", "by_decision_type"]
    ] = Field(
        default_factory=lambda: cast(
            list[Literal["by_columns_signature", "by_decision_type"]],
            ["by_decision_type"],
        ),
        description=(
            "One or more policies used to drop non-standards segments from KG export. "
            "Policies are OR'ed together (if any policy matches -> drop)."
        ),
    )
    overwrite: bool = Field(False, description="Overwrite existing knowledge graphs.")
    progressions_builds_towards_min_confidence: float = Field(
        default=0.60,
        description="Minimum confidence to emit buildsTowards relationships.",
        ge=0.0,
        le=1.0,
    )
    progressions_cross_grade_builds_towards: bool = True
    progressions_cross_grade_relates_to_max_items_per_subject: int = Field(
        default=10,
        description="Cross-grade relatesTo: max sampled Standards per subject per grade.",
        ge=1,
    )
    progressions_cross_grade_relates_to: bool = True
    progressions_cross_stage_builds_towards: bool = Field(
        default=False,
        description=(
            "Cross-grade buildsTowards fallback: if either adjacent level bucket is "
            "banded (low != high), infer buildsTowards across adjacent level ranges "
            "(e.g., I–II -> III–VI)."
        ),
    )
    progressions_cross_stage_relates_to: bool = Field(
        default=False,
        description=(
            "Cross-grade relatesTo fallback: if either adjacent level bucket is banded "
            "(low!=high), infer relatesTo across adjacent level ranges "
            "(e.g., I–II <-> III–VI)."
        ),
    )
    progressions_relates_to_max_edges_per_sfi: int = Field(
        default=3,
        ge=1,
        description="Cap the number of relatesTo edges per SFI (undirected cap).",
    )
    progressions_relates_to_min_confidence: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to emit relatesTo relationships (kept higher to avoid over-linking).",
    )
    progressions_within_grade_allow_banded_levels: bool = Field(
        default=False,
        description=(
            "If false (default), Phase 1 and Phase 3 'within-grade' inference only runs "
            "on single-level buckets where grade_ordinal_low == grade_ordinal_high. "
            "If true, allow within-grade inference to also run on banded/stage buckets "
            "(low != high), e.g., 'Std I–II'."
        ),
    )
    progressions_within_grade_builds_towards: bool = True
    progressions_within_grade_relates_to: bool = Field(
        default=True,
        description=(
            "Enable within-grade relatesTo inference (cross-subject only). "
            "Threads within the same subject are skipped to reduce noise."
        ),
    )
    progressions_within_grade_relates_to_max_items_per_subject: int = Field(
        default=5,
        description="Within-grade relatesTo (cross-subject only): max sampled standards per subject (keeps LLM calls bounded).",
        ge=1,
    )
    provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often the organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )
    prune_empty_groupings: bool = Field(
        default=True,
        description="If true, drop grouping StandardsFrameworkItems that have zero exported children after filtering, repeating to a fixpoint. No reattachment is performed.",
    )

    @model_validator(mode="after")
    def _validate_grouping_role_policy(self) -> CreateKGConfig:
        """Validate that if grouping_role_policy is 'whitelist', then
        grouping_roles_whitelist is non-empty and does not include
        FRAMEWORK/UNRESOLVED.

        Returns
        -------
        CreateKGConfig
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If grouping_role_policy is 'whitelist' but grouping_roles_whitelist is
            empty or includes disallowed roles.
        """

        if self.grouping_role_policy == "whitelist":
            if not self.grouping_roles_whitelist:
                raise ValueError(
                    "grouping_role_policy='whitelist' requires a non-empty grouping_roles_whitelist."
                )

            # These should never be treated as hierarchy groupings.
            disallowed = {NodeRole.FRAMEWORK, NodeRole.UNRESOLVED}
            overlap = disallowed & set(self.grouping_roles_whitelist)

            if overlap:
                raise ValueError(
                    f"grouping_roles_whitelist cannot include FRAMEWORK/UNRESOLVED: {overlap}"
                )

        return self

    @model_validator(mode="after")
    def _validate_parenting_relevance(self) -> CreateKGConfig:
        """Validate that aux_statement_parenting is compatible with guidance/descriptor
        handling.

        Returns
        -------
        CreateKGConfig
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If aux_statement_parenting is 'under_expectation' but neither guidance nor
            descriptor handling is set to export as SFI.
        """

        relevant_any = self.guidance_handling in {
            "export_as_sfi_other",
            "attach_to_expectation_metadata",
        } or self.descriptor_handling in {
            "export_as_sfi_other",
            "attach_to_expectation_metadata",
        }

        if self.aux_statement_parenting == "under_expectation" and not relevant_any:
            raise ValueError(
                "aux_statement_parenting='under_expectation' requires exporting "
                "or attaching guidance/descriptors to expectations (set guidance_handling "
                "or descriptor_handling to 'export_as_sfi_other' or "
                "'attach_to_expectation_metadata')."
            )

        # attach-to-expectation requires the export-time anchor discovery implemented
        # by aux_statement_parenting="under_expectation". If we leave
        # aux_statement_parenting="as_siblings", export_academic_standards will skip
        # emitting aux nodes (to avoid SFIs) but will never attach them -> silent loss.
        attach_any = (
            self.guidance_handling == "attach_to_expectation_metadata"
            or self.descriptor_handling == "attach_to_expectation_metadata"
        )
        if attach_any and self.aux_statement_parenting != "under_expectation":
            raise ValueError(
                "guidance_handling/descriptor_handling='attach_to_expectation_metadata' "
                "requires aux_statement_parenting='under_expectation' so aux statements "
                "can be anchored to the most recent expectation during export."
            )

        return self

    @model_validator(mode="after")
    def _validate_stable_bases(self) -> CreateKGConfig:
        """Validate that case_uri_base is non-empty and stable.

        Returns
        -------
        CreateKGConfig
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If case_uri_base is empty.
        """

        if not self.case_uri_base:
            raise ValueError("case_uri_base must be non-empty and stable.")

        return self


class RunConfig(BaseSchema):
    """Pydantic model for run configuration."""

    page_ir_extraction: ExtractionConfig
    page_ir_verification: VerificationConfig
    document_ir: StitchingConfig
    canonical_ir: Optional[CreateCanonicalConfig] = None
    kgs: Optional[CreateKGConfig] = None


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
