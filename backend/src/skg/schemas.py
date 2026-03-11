"""This module contains top-level Pydantic models."""

# Standard Library
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Self, cast
from uuid import UUID

# Third Party Library
import langcodes

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
from skg.utils.general import make_dir


def validate_bbox_order(bbox: list[float]) -> list[float]:
    """Ensure bbox is well-ordered: [x0, y0, x1, y1] with x0 < x1 and y0 < y1.

    Parameters
    ----------
    bbox
        The bounding box to validate.

    Returns
    -------
    list[float]
        The validated bounding box.

    Raises
    ------
    ValueError
        If the bounding box does not have exactly 4 numbers.
    """

    if len(bbox) != 4:
        raise ValueError(
            f"Bounding box must have exactly 4 numbers: [x0, y0, x1, y1]. Got: {bbox}"
        )

    x0, y0, x1, y1 = bbox

    # Auto-correct inverted or zero-dimension axes. For equal dimensions, add 1 pixel.
    if x0 >= x1:
        if x0 > x1:
            x0, x1 = x1, x0
        else:
            x1 = x0 + 1.0
    if y0 >= y1:
        if y0 > y1:
            y0, y1 = y1, y0
        else:
            y1 = y0 + 1.0

    return [x0, y0, x1, y1]


def validate_bcp47(code: str) -> str:
    """Validates that a string is a valid BCP-47 language tag.

    Parameters
    ----------
    code
        The language tag to validate.

    Returns
    -------
    str
        The standardized version (e.g., 'en_us' -> 'en-US').

    Raises
    ------
    ValueError
        If the language tag is invalid or unparseable.
    """

    code = (code or "und").strip().replace("_", "-")
    if code in {"und", "mul"}:
        return code

    try:
        lang = langcodes.Language.get(code)
        if not lang.is_valid():
            raise ValueError(f"Invalid BCP-47 language tag: '{code}'")
        return lang.to_tag()
    except langcodes.LanguageTagError as exc:
        raise ValueError(f"Unparseable language tag: '{code}'") from exc


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

    country: str = Field(
        ..., description="The country associated with the PDF document."
    )
    dpi: int = Field(250, description="Render DPI for page images.")
    end_page: Optional[int] = Field(
        None, description="0-based end page (exclusive). Default None is to end."
    )
    languages: list[LanguageField] = Field(
        ...,
        description="One or more languages associated with the PDF document (e.g. en-US, fr-FR).",
        min_length=1,
    )
    model: str = Field(
        "openai:gpt-5.2-2025-12-11", description="Model for page IR extraction."
    )
    output_dir: Path = Field(..., description="Output directory root.")
    overwrite: bool = Field(False, description="Overwrite existing page IR JSONs.")
    pdf_fp: FilePath = Field(
        ...,
        description="The file path to the PDF document to extract curriculum data from.",
    )
    start_page: Optional[int] = Field(
        None, description="0-based start page (inclusive)."
    )
    use_extracted_hints: bool = Field(
        False,
        description=(
            "Whether or not to extract text layer and table layer hints using PyMuPDF "
            "as additional context for the extraction agent's prompt. This is helpful "
            "for PDF with non-English text and accents."
        ),
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

        if (
            self.end_page is not None
            and self.start_page is not None
            and self.end_page <= self.start_page
        ):
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

    end_page: Optional[int] = Field(
        None, description="0-based end page (exclusive). Default: to end."
    )
    min_confidence_to_patch: float = Field(
        0.75,
        ge=0.0,
        le=1.0,
        description="Only apply compiled continuity decisions/repeats_header patches when verdict.confidence >= this threshold.",
    )
    min_confidence_to_select_positive: float = Field(
        0.50,
        description="Minimum confidence for a positive continuation verdict to outrank negatives during attempt selection. This does not control patching.",
        ge=0.0,
        le=1.0,
    )
    min_confidence_to_stop_negative_search: float = Field(
        0.95,
        description="Minimum confidence for a same-family primary-primary negative verdict to stop alternate candidate-pair search for a page boundary. This controls verification search budget, not compile-time patching.",
        ge=0.0,
        le=1.0,
    )
    model: str = Field(
        "openai:gpt-5.2-2025-12-11",
        description="OpenAI model for page IR verification.",
    )
    next_page_crop_padding_px: int = Field(
        120,
        description="When cropping the top of page N+1 for verification, include this many extra pixels below the selected next candidate bbox. Crops are pair-specific.",
        ge=0,
    )
    overwrite: bool = Field(
        False,
        description="If True, re-verify all page pairs even if pair reports already exist on disk. If False, reuse existing pair reports (resumed run support).",
    )
    start_page: Optional[int] = Field(
        None, description="0-based start page (inclusive)."
    )

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

        if (
            self.end_page is not None
            and self.start_page is not None
            and self.end_page <= self.start_page
        ):
            raise ValueError(
                f"end_page ({self.end_page}) must be greater than start_page ({self.start_page})."
            )

        return self

    @model_validator(mode="after")
    def check_confidences(self) -> Self:
        """Ensure confidence thresholds remain logically consistent.

        Returns
        -------
        Self
            The passed in VerificationConfig.

        Raises
        ------
        ValueError
            If min_confidence_to_select_positive is greater than
                min_confidence_to_patch.
            If min_confidence_to_stop_negative_search is lower than
                min_confidence_to_patch.
        """

        if self.min_confidence_to_select_positive > self.min_confidence_to_patch:
            raise ValueError(
                "min_confidence_to_select_positive must be <= min_confidence_to_patch so selection remains at least as permissive as patching."
            )

        if self.min_confidence_to_stop_negative_search < self.min_confidence_to_patch:
            raise ValueError(
                "min_confidence_to_stop_negative_search must be >= min_confidence_to_patch so early negative stopping remains at least as conservative as patching."
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
        ge=0,
        le=1,
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
    description_text_policy: Literal["source", "prefer_text_en"] = Field(
        default="source",
        description=(
            "How to populate the 'description' text on exported SFIs. "
            "'source' uses the original-language body text; "
            "'prefer_text_en' uses the English translation when available."
        ),
    )
    descriptor_handling: AuxStatementHandling = Field(
        default="export_as_sfi_other",
        description="How to handle descriptor statements during KG export.",
    )
    export_dialect: ExportDialect = Field(
        default="global_relaxed",
        description=(
            "Export schema dialect. 'lc_public_strict' enforces LC KG public schema "
            "constraints (US-centric CASE conventions); 'global_relaxed' permits "
            "non-US metadata shapes and free-form fields for international curricula."
        ),
    )
    export_in_language_policy: Literal["default", "source"] = Field(
        default="source",
        description=(
            "Controls the 'inLanguage' value on exported SFIs. "
            "'source' uses the language detected on each statement's body text; "
            "'default' always uses `language_default`."
        ),
    )
    generate_progressions: bool = Field(
        default=True,
        description=(
            "Whether to run LLM-based progression inference (buildsTowards / relatesTo) "
            "after exporting the standards hierarchy. Disable to skip progression "
            "generation entirely (useful for quick re-exports or debugging)."
        ),
    )
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
    language_default: LanguageField = Field(
        description=(
            "Default BCP-47 language code for the framework (e.g., 'en', 'fr', 'sw'). "
            "Used as the fallback inLanguage when per-statement language detection is "
            "unavailable or when export_in_language_policy='default'."
        ),
    )
    lc_atomic_skills_batch_size: int = Field(
        default=5,
        description="Number of expectation SFIs to send per LLM call when learning_component_policy='llm_atomic_skills'.",
        ge=1,
        le=50,
    )
    lc_atomic_skills_include_aux_statements: bool = Field(
        default=True,
        description="If True, include SFI.metadata['aux_statements'] as additional context for atomic skills decomposition.",
    )
    lc_atomic_skills_include_topic_context: bool = Field(
        default=True,
        description="If True, include SFI.metadata['progression_context'] topic/grade hints in the atomic skills prompt.",
    )
    lc_atomic_skills_min_per_sfi: int = Field(
        default=1,
        description=(
            "Minimum number of atomic skills required per SFI in the LLM response. "
            "If unmet, the batch will be corrected/retried; if still failing, the "
            "export falls back to a 1-to-1 LC for affected SFIs."
        ),
        ge=1,
        le=25,
    )
    lc_atomic_skills_require_rationale: bool = Field(
        default=True,
        description="If True, require a short rationale for each atomic skill in the LLM response.",
    )
    learning_component_policy: Literal[
        "1_to_1", "split_bullets", "llm_atomic_skills"
    ] = Field(
        default="1_to_1",
        description=(
            "LearningComponent creation strategy. "
            "'1_to_1' creates exactly one LC per expectation SFI; "
            "'split_bullets' splits multi-bullet expectation bodies into separate LCs; "
            "'llm_atomic_skills' uses an LLM to decompose each expectation into 1–N atomic skill LCs."
        ),
    )
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
    progressions_cross_grade_builds_towards: bool = Field(
        default=True,
        description="Enable cross-grade buildsTowards progression inference between adjacent single-level grade buckets.",
    )
    progressions_cross_grade_match_roles: Optional[list[str]] = Field(
        default=None,
        description=(
            "Ordered list of canonical IR node roles whose labels form the "
            "cross-grade thread identity for LP Phases 2 and 4. Only entries in "
            "progression_context.topic_path_parts with a role in this list are "
            "included in the LP thread key. Example: ['strand'] matches "
            "'Activités numériques' across CE1 and CE2. Also used for Phase 1 "
            "within-grade grouping (collapses week/substage fragmentation). "
            "When None, uses thread_key from progression_context (current behavior)."
        ),
        min_length=1,
    )
    progressions_cross_grade_relates_to_max_items_per_subject: int = Field(
        default=10,
        description="Cross-grade relatesTo: max sampled Standards per subject per grade.",
        ge=1,
    )
    progressions_cross_grade_relates_to: bool = Field(
        default=True,
        description="Enable cross-grade relatesTo progression inference between adjacent single-level grade buckets.",
    )
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
    progressions_excluded_subject_labels: list[str] = Field(
        default=["UNSPECIFIED_SUBJECT"],
        description=(
            "Subject labels to exclude from Phase 3 (within-grade cross-subject "
            "relatesTo) and Phase 4 (cross-grade same-subject relatesTo) "
            "sampling. Default excludes 'UNSPECIFIED_SUBJECT' since including "
            "unmapped items in cross-subject pairing adds noise without value."
        ),
    )
    progressions_grade_label_map: Optional[dict[str, int]] = Field(
        default=None,
        description=(
            "Explicit mapping from grade_key labels (as they appear in "
            "progression_context.grade_key, normalized to lowercase + strip) to "
            "integer ordinals for LP inference. Ordinals represent each level's "
            "position in the sequence (not necessarily the grade number); "
            "adjacency is determined by ordinals differing by exactly 1. "
            "Multiple grade_keys may map to the same ordinal to merge subtrees "
            "(e.g., 'paliers du niveau ce1' and 'planification ce1' both → 1). "
            "Config keys must be lowercase + stripped. When None, falls back to "
            "_parse_ordinal heuristics on grade_ordinal_low/high."
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
    progressions_subject_role: Optional[str] = Field(
        default=None,
        description=(
            "Canonical IR node role to use as the 'subject' for Phase 3 "
            "(within-grade cross-subject relatesTo) and Phase 4 "
            "(cross-grade same-subject relatesTo). When set, the LP bucketing "
            "code looks for this role in each expectation's "
            "progression_context.topic_path_parts and uses the first matching "
            "label as subject_label. When None, defaults to searching for "
            "'subject' then 'learning_area' roles."
        ),
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
    progressions_within_grade_builds_towards: bool = Field(
        default=True,
        description="Enable within-grade buildsTowards progression inference (sequential standards within the same grade/subject thread).",
    )
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
        description="If true, drop grouping StandardsFrameworkItems that have zero exported children after filtering and after reattachment hoists children of dropped nodes to their nearest surviving ancestor, repeating to a fixpoint.",
    )

    @model_validator(mode="after")
    def _validate_atomic_skills_bounds(self) -> Self:
        """Validate that lc_atomic_skills_min_per_sfi is less than or equal to
        lc_max_splits_per_standard when using llm_atomic_skills.

        Returns
        -------
        Self
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If lc_atomic_skills_min_per_sfi is greater than lc_max_splits_per_standard.
        """

        if (
            self.learning_component_policy == "llm_atomic_skills"
            and self.lc_atomic_skills_min_per_sfi > self.lc_max_splits_per_standard
        ):
            raise ValueError(
                f"lc_atomic_skills_min_per_sfi ({self.lc_atomic_skills_min_per_sfi}) "
                f"must be <= lc_max_splits_per_standard ({self.lc_max_splits_per_standard})."
            )

        return self

    @field_validator("progressions_grade_label_map")
    @classmethod
    def _validate_grade_label_map_keys(
        cls, v: Optional[dict[str, int]]
    ) -> Optional[dict[str, int]]:
        """Enforce that all keys are lowercase + stripped, and values are non-negative.

        Parameters
        ----------
        v
            The grade label map to validate.

        Returns
        -------
        Optional[dict[str, int]]
            The validated grade label map.

        Raises
        ------
        ValueError
            If any key is not lowercase + stripped, or any value is negative.
        """

        if v is None:
            return v

        for key, val in v.items():
            normalized = key.strip().lower()

            if key != normalized:
                raise ValueError(
                    f"progressions_grade_label_map key must be lowercase + stripped: "
                    f"got {key!r}, expected {normalized!r}"
                )

            if not isinstance(val, int) or val < 0:
                raise ValueError(
                    f"progressions_grade_label_map values must be non-negative integers: "
                    f"got {val!r} for key {key!r}"
                )

        return v

    @field_validator("progressions_subject_role")
    @classmethod
    def _validate_subject_role_non_empty(cls, v: Optional[str]) -> Optional[str]:
        """Ensure progressions_subject_role is non-empty when provided.

        Parameters
        ----------
        v
            The subject role string to validate.

        Returns
        -------
        Optional[str]
            The validated subject role string.

        Raises
        ------
        ValueError
            If the value is an empty string.
        """

        if v is not None and not v.strip():
            raise ValueError("progressions_subject_role must be non-empty when set")

        return v

    @model_validator(mode="after")
    def _validate_grouping_role_policy(self) -> Self:
        """Validate that if grouping_role_policy is 'whitelist', then
        grouping_roles_whitelist is non-empty and does not include
        FRAMEWORK/UNRESOLVED.

        Returns
        -------
        Self
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
    def _validate_parenting_relevance(self) -> Self:
        """Validate that aux_statement_parenting is compatible with guidance/descriptor
        handling.

        Returns
        -------
        Self
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
    def _validate_stable_bases(self) -> Self:
        """Validate that case_uri_base is non-empty and stable.

        Returns
        -------
        Self
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

    page_ir_extraction: ExtractionConfig = Field(
        description="Configuration for page-level IR extraction from the source PDF."
    )
    page_ir_verification: VerificationConfig = Field(
        description="Configuration for page-boundary verification between adjacent pages."
    )
    document_ir: StitchingConfig = Field(
        description="Configuration for stitching verified page IRs into a single document IR."
    )
    canonical_ir: Optional[CreateCanonicalConfig] = Field(
        default=None,
        description="Configuration for canonical IR creation. If None, the canonical IR step is skipped.",
    )
    kgs: Optional[CreateKGConfig] = Field(
        default=None,
        description="Configuration for knowledge graph creation. If None, the KG step is skipped.",
    )


class RunCtx(BaseSchema):
    """Pydantic model for run metadata."""

    completed_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the run completed."
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata attached to the run (e.g., status, error details, doc_key).",
    )
    models: list[str] = Field(
        default_factory=list,
        description="Ordered list of model identifiers used during the run.",
    )
    run_id: str = Field(
        description="Unique identifier for this run (typically a UUID or slug)."
    )
    started_at: Optional[datetime] = Field(
        default=None, description="UTC timestamp when the run started."
    )


# Global schemas.
class Limits(BaseSchema):
    """Pydantic model for global limits."""

    max_retry_attempts: int = Field(
        10, ge=0, description="Must be a non-negative integer"
    )
