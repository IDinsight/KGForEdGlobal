"""This module contains top-level Pydantic models."""

# Standard Library
import re

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
    StatementRole,
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


def validate_lp_role(*, field_name: str, role: NodeRole) -> NodeRole:
    """Validate that a learning progression role config field uses a concrete hierarchy
    role.

    Parameters
    ----------
    field_name
        The config field name for error messages.
    role
        The role value to validate.

    Returns
    -------
    NodeRole
        The validated role.

    Raises
    ------
    ValueError
        If the role is too generic to be useful for learning progression bucketing.
    """

    disallowed_roles = {NodeRole.FRAMEWORK, NodeRole.UNRESOLVED}

    if role in disallowed_roles:
        disallowed_values = ", ".join(sorted(item.value for item in disallowed_roles))
        raise ValueError(
            f"{field_name} cannot contain {role.value!r}. Use a concrete hierarchy role "
            f"such as 'subject', 'learning_area', 'strand', or 'theme'. "
            f"Disallowed roles: {disallowed_values}."
        )

    return role


def validate_lp_roles(
    *, field_name: str, roles: Optional[list[NodeRole]]
) -> Optional[list[NodeRole]]:
    """Validate a learning progression role list for non-empty, unique, concrete roles.

    Parameters
    ----------
    field_name
        The config field name for error messages.
    roles
        The ordered role list to validate.

    Returns
    -------
    Optional[list[NodeRole]]
        The validated role list.

    Raises
    ------
    ValueError
        If the list contains duplicates or disallowed roles.
    """

    if roles is None:
        return roles

    validated_roles: list[NodeRole] = []
    seen_roles: set[NodeRole] = set()

    for role in roles:
        validated_role = validate_lp_role(field_name=field_name, role=role)

        if validated_role in seen_roles:
            raise ValueError(
                f"{field_name} must not contain duplicate roles. "
                f"Duplicate value: {validated_role.value}."
            )

        seen_roles.add(validated_role)
        validated_roles.append(validated_role)

    return validated_roles


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


def validate_regex_prefixed_patterns(
    *, field_name: str, patterns: list[str]
) -> list[str]:
    """Validate regex-based path patterns in LC source filtering config.

    LC source path pattern fields support three pattern styles: plain substring,
    shell-style glob, and regex when prefixed with `re:`. Only regex-prefixed patterns
    need compilation validation; substring/glob patterns are returned unchanged.

    Parameters
    ----------
    field_name
        The config field name used in validation error messages.
    patterns
        Pattern strings from the config field.

    Returns
    -------
    list[str]
        The original pattern list, unchanged.

    Raises
    ------
    TypeError
        If a configured path pattern is not a string.
    ValueError
        If a regex-prefixed pattern is empty or cannot be compiled by `re`.
    """

    for idx, pattern in enumerate(patterns or []):
        if not isinstance(pattern, str):
            raise TypeError(
                f"{field_name}[{idx}] must be a string. Got {type(pattern).__name__}."
            )

        if not pattern.startswith("re:"):
            continue

        regex_body = pattern[3:]

        if not regex_body:
            raise ValueError(
                f"{field_name}[{idx}] is an empty regex pattern. "
                f"Use a non-empty pattern after the 're:' prefix."
            )

        try:
            re.compile(regex_body)
        except re.error as exc:
            raise ValueError(
                f"Invalid regex in {field_name}[{idx}] ({pattern!r}): {exc}"
            ) from exc

    return patterns


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
LCSourceNormalizedStatementType = Literal["Standard", "Standard Grouping", "Other"]


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


class AcademicStandardsDefaultLevelContext(BaseSchema):
    """Document-level fallback level context for Academic Standards export.

    This is useful when the source curriculum has a clear document-wide level (e.g., a
    single-grade CE1 PDF) but the canonical hierarchy does not contain an explicit
    grade/stage ancestor above every expectation. Explicit ancestor-derived grade/stage
    context remains the preferred source of truth when `apply_when_missing_only` is
    True.

    Examples
    --------
    1. Single-grade Senegal reading PDF
        A PDF title says "2ème étape (CE1)", but most extracted expectations live under
        section/palier/week nodes rather than an explicit `grade_level` ancestor. Use:

            {
                "kind": "grade_level",
                "label": "CE1",
                "ordinal_low": 1,
                "ordinal_high": 1,
                "source": "config: framework title says 2ème étape (CE1)",
                "apply_to_roles": ["expectation"],
                "apply_when_missing_only": true
            }

        Exported expectation SFIs without explicit level context receive
        `grade_key='CE1'` and grade ordinals `1..1` in `metadata.progression_context`.

    2. Banded stage-only framework
        A framework covers Standards III–VI as one band. Use:

            {
                "kind": "stage",
                "label": "Standard III–VI",
                "ordinal_low": 3,
                "ordinal_high": 6,
                "source": "config: document scope",
                "apply_to_roles": ["expectation"],
                "apply_when_missing_only": true
            }

        Exported expectation SFIs without explicit level context receive
        `stage_key='Standard III–VI'` and stage ordinals `3..6`.
    """

    apply_to_roles: set[StatementRole] = Field(
        default_factory=lambda: {StatementRole.EXPECTATION},
        description=(
            "Canonical statement roles that receive this default when their explicit "
            "grade/stage context is missing. Default applies only to expectation nodes."
        ),
    )
    apply_when_missing_only: bool = Field(
        default=True,
        description=(
            "If true, preserve explicit ancestor-derived grade/stage context and use "
            "this default only when no level context is present. If false, this default "
            "can override explicit context for the configured roles."
        ),
    )
    kind: Literal["grade_level", "stage"] = Field(
        default="grade_level",
        description=(
            "Which progression-context fields this default populates: grade_* for "
            "'grade_level' or stage_* for 'stage'."
        ),
    )
    label: str = Field(
        description="Human-readable level label, e.g. 'CE1' or 'Standard III–VI'.",
        min_length=1,
    )
    ordinal_high: int = Field(
        description="Highest ordinal for this level or band.", ge=0
    )
    ordinal_low: int = Field(description="Lowest ordinal for this level or band.", ge=0)
    source: str = Field(
        default="config",
        description=(
            "Human-readable provenance for the default, e.g. 'config: document title' "
            "or 'human_review'."
        ),
        min_length=1,
    )

    @field_validator("label", "source", mode="before")
    @classmethod
    def _strip_default_level_strings(cls, v: Any, info: Any) -> str:
        """Strip required default-level strings and reject empty values.

        Parameters
        ----------
        v
            The configured value for the field being validated.
        info
            Pydantic field validation info; used to name the bad field in errors.

        Returns
        -------
        str
            The stripped, non-empty string value.

        Raises
        ------
        TypeError
            If the configured value is not a string.
        ValueError
            If the configured value is empty after stripping whitespace.
        """

        if not isinstance(v, str):
            raise TypeError(
                f"as_default_level_context.{info.field_name} must be a string. "
                f"Got {type(v).__name__}."
            )

        v2 = v.strip()

        if not v2:
            raise ValueError(
                f"as_default_level_context.{info.field_name} must be non-empty."
            )

        return v2

    @model_validator(mode="after")
    def _validate_ordinal_bounds(self) -> Self:
        """Validate that the low ordinal is not greater than the high ordinal.

        Returns
        -------
        Self
            The validated DefaultLevelContext object.

        Raises
        ------
        ValueError
            If `ordinal_low` is greater than `ordinal_high`.
        """

        if self.ordinal_low > self.ordinal_high:
            raise ValueError(
                "as_default_level_context.ordinal_low must be <= "
                "as_default_level_context.ordinal_high."
            )

        return self


class CreateKGConfig(BaseSchema):
    """Configuration for knowledge graph creation from canonical IR.

    Notes
    -----
    1. as_export_dialect defaults to "global_relaxed". We *can* keep "lc_public_strict" as
        an option for internal experiments, but the schemas/models are intentionally
        non-US-centric.
    2. namespace_uuid MUST be pinned and never changed once you start generating IDs.
    """

    as_academic_subject_default: str = Field(
        description=(
            "Default high-level academic subject classification for the framework "
            "(e.g., Mathematics, English Language Arts, Science). Used when canonical "
            "IR does not provide a subject (or when exporting a single subject partition)."
        ),
    )
    as_adoption_status: str = Field(
        description=(
            "Adoption status of the framework (e.g., Draft, Adopted). "
            "In `lc_public_strict`, this should conform to LC enum values; "
            "in `global_relaxed`, free-form values are allowed."
        ),
    )
    as_attribution_statement: str = Field(
        description=(
            "Attribution text required to credit the original publisher/owner "
            "of the standards framework (e.g., Ministry of Education, year, source)."
        ),
    )
    as_author: str = Field(
        description=(
            "Human or organization name considered the author/owner of the framework "
            "(e.g., 'Ministry of Education (Zambia)')."
        ),
    )
    as_aux_statement_parenting: Literal["as_siblings", "under_expectation"] = Field(
        default="as_siblings",
        description=(
            "If exporting guidance/descriptors as SFIs, choose whether they remain "
            "siblings under the grouping parent or are re-parented under the "
            "expectation they belong to."
        ),
    )
    as_case_uri_base: str = Field(
        default="urn:lc:case:",
        description="Stable CASE identifier URI prefix (e.g., urn:lc:case:).",
    )
    as_default_level_context: Optional[AcademicStandardsDefaultLevelContext] = Field(
        default=None,
        description=(
            "Optional document-level fallback level context for Academic Standards "
            "export. Use for single-grade/single-stage PDFs where the document scope "
            "is clear but not represented as an explicit grade/stage ancestor above "
            "every expectation. The fallback is written into SFI grade_level and "
            "metadata.progression_context so Learning Progressions can bucket "
            "within-grade standards."
        ),
    )
    as_description_text_policy: Literal["source", "prefer_text_en"] = Field(
        default="source",
        description=(
            "How to populate the 'description' text on exported SFIs. "
            "'source' uses the original-language body text; "
            "'prefer_text_en' uses the English translation when available."
        ),
    )
    as_descriptor_handling: AuxStatementHandling = Field(
        default="export_as_sfi_other",
        description="How to handle descriptor statements during KG export.",
    )
    as_export_dialect: ExportDialect = Field(
        default="global_relaxed",
        description=(
            "Export schema dialect. 'lc_public_strict' enforces LC KG public schema "
            "constraints (US-centric CASE conventions); 'global_relaxed' permits "
            "non-US metadata shapes and free-form fields for international curricula."
        ),
    )
    as_framework_name: Optional[str] = Field(
        default=None,
        description=(
            "Optional explicit title for the exported StandardsFramework root node. "
            "Use this when the canonical IR root label is a filename or other "
            "non-human-readable placeholder. If omitted, the exporter falls back to "
            "the canonical root node display text, then the PDF filename."
        ),
    )
    as_grouping_role_policy: Literal["loose", "whitelist"] = Field(
        default="whitelist",
        description=(
            "How to interpret node roles as hierarchy groupings during standards export. "
            "'whitelist' is the safer default for international curricula: only roles in "
            "as_grouping_roles_whitelist are treated as hierarchy groupings. "
            "'loose' is opt-in legacy behavior where any non-statement role becomes a grouping."
        ),
    )
    as_grouping_roles_whitelist: set[NodeRole] = Field(
        default_factory=lambda: set(DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER)
        - {NodeRole.PROSE},
        description=(
            "When as_grouping_role_policy='whitelist', only these roles count as groupings "
            "(emitted as normalizedStatementType='Standard Grouping', eligible for pruning, "
            "and used as aux-parenting anchors). Default excludes PROSE."
        ),
    )
    as_guidance_handling: AuxStatementHandling = Field(
        default="drop",
        description="How to handle guidance statements during KG export.",
    )
    as_jurisdiction_default: str = Field(
        description=(
            "Default jurisdiction that issued the framework (e.g., Zambia, Uganda). "
            "Used when canonical IR does not provide jurisdiction."
        ),
    )
    as_language_default: LanguageField = Field(
        description=(
            "Default BCP-47 language code used as the fallback `in_language` when the "
            "language of the exported framework/item text cannot be determined reliably."
        ),
    )
    as_license: str = Field(
        description=(
            "License string for the framework content. This may be an SPDX-like label "
            "or a publisher-defined license statement; must be present even if it is "
            "a conservative placeholder."
        ),
    )
    as_non_grouping_role_handling: Literal["drop", "export_as_sfi_other"] = Field(
        default="drop",
        description=(
            "When as_grouping_role_policy='whitelist': what to do with nodes that are neither "
            "statement roles (expectation/descriptor/guidance) nor allowed groupings. "
            "'drop' removes them. 'export_as_sfi_other' is leaf-only: the node may be "
            "emitted as an SFI with type 'Other' only when it has no canonical children; "
            "structural non-grouping parents are dropped and their children are hoisted "
            "to the nearest surviving ancestor during export."
        ),
    )
    as_non_standard_columns_signature: set[str] = Field(
        default_factory=set,
        description=(
            "Normalized columns signature that identify non-standards tables when "
            "using by_columns_signature. Example columns signature: "
            "'cinyanja term 2 - weekly schedule|||monday|tuesday|wednesday|thursday|friday'."
        ),
    )
    as_non_standard_decision_types: set[SegmentDecisionType] = Field(
        default_factory=lambda: {SegmentDecisionType.IGNORE},
        description=(
            "Decision types that should be treated as non-standards and dropped "
            "when using by_decision_type."
        ),
    )
    as_non_standard_segment_drop_policy: list[
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
    as_provider: str = Field(
        description=(
            "Provider/host name for the exported KG dataset (often the organization/product). "
            "Used for attribution and provenance in downstream systems."
        ),
    )
    as_prune_empty_groupings: bool = Field(
        default=True,
        description="If true, drop grouping StandardsFrameworkItems that have zero exported children after filtering and after reattachment hoists children of dropped nodes to their nearest surviving ancestor, repeating to a fixpoint.",
    )
    generate_learning_progressions: bool = Field(
        default=True,
        description=(
            "Whether to run LLM-based learning progression inference (buildsTowards / relatesTo) "
            "after exporting the standards hierarchy. Disable to skip learning progression "
            "generation entirely (useful for quick re-exports or debugging)."
        ),
    )
    lc_atomic_skills_batch_size: int = Field(
        default=5,
        description="Number of expectation SFIs to send per LLM call when lc_policy='llm_atomic_skills'.",
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
            "export falls back to a 1-to-1 LC for the affected batch."
        ),
        ge=1,
        le=25,
    )
    lc_atomic_skills_require_rationale: bool = Field(
        default=True,
        description="If True, require a short rationale for each atomic skill in the LLM response.",
    )
    lc_max_splits_per_standard: int = Field(
        default=25,
        description="Maximum number of LearningComponents to emit per Standard SFI when splitting.",
        ge=1,
    )
    lc_output_language_policy: Literal["english", "explicit_tag", "source"] = Field(
        default="source",
        description=(
            "Controls both the output-language instruction sent to the LearningComponents "
            "LLM prompt and the emitted `in_language` tag on derived LearningComponents. "
            "'source' uses the source SFI/framework language metadata; if that resolves to "
            "'mul', the prompt instructs the model to use the same language(s) as the input "
            "text rather than the raw tag. 'english' forces English output and emits "
            "`in_language='en'`. 'explicit_tag' forces a specific BCP-47 tag from "
            "`lc_output_language_tag`."
        ),
    )
    lc_output_language_tag: Optional[LanguageField] = Field(
        default=None,
        description=(
            "Explicit BCP-47 language tag used when lc_output_language_policy='explicit_tag' "
            "(for example 'fr', 'wo', or 'en'). Ignored otherwise."
        ),
    )
    lc_policy: Literal["1_to_1", "split_bullets", "llm_atomic_skills"] = Field(
        default="1_to_1",
        description=(
            "LearningComponent creation strategy. "
            "'1_to_1' creates exactly one LC per expectation SFI; "
            "'split_bullets' splits multi-bullet expectation bodies into separate LCs; "
            "'llm_atomic_skills' uses an LLM to decompose each expectation into 1–N atomic skill LCs."
        ),
    )
    lc_source_labels_exclude: set[str] = Field(
        default_factory=set,
        description=(
            "Optional blocklist of SFI metadata.source_label values excluded from LC "
            "generation."
        ),
    )
    lc_source_labels_include: set[str] = Field(
        default_factory=set,
        description=(
            "Optional allowlist of SFI metadata.source_label values eligible for LC "
            "generation. Empty set means no source-label allowlist restriction."
        ),
    )
    lc_source_max_path_depth: Optional[int] = Field(
        default=None,
        description=(
            "Optional maximum canonical_path_key depth for LC source eligibility. "
            "Use with care; statement type/source-label filters are usually clearer."
        ),
        ge=0,
    )
    lc_source_min_path_depth: Optional[int] = Field(
        default=None,
        description=(
            "Optional minimum canonical_path_key depth for LC source eligibility. "
            "Use with care; statement type/source-label filters are usually clearer."
        ),
        ge=0,
    )
    lc_source_normalized_statement_types: set[LCSourceNormalizedStatementType] = Field(
        default_factory=lambda: cast(
            set[LCSourceNormalizedStatementType], {"Standard"}
        ),
        description=(
            "Normalized StandardsFrameworkItem types eligible to generate "
            "LearningComponents. This is an LC-source decision, not an Academic "
            "Standards export decision. Default keeps current behavior by considering "
            "only normative Standard SFIs."
        ),
    )
    lc_source_path_patterns_exclude: list[str] = Field(
        default_factory=list,
        description=(
            "Optional exclude patterns for SFI metadata.canonical_path_key or its "
            "role-only path pattern. Supported forms: substring, glob (*, ?, []), or "
            "regex prefixed with 're:'."
        ),
    )
    lc_source_path_patterns_include: list[str] = Field(
        default_factory=list,
        description=(
            "Optional include patterns for SFI metadata.canonical_path_key or its "
            "role-only path pattern. Supported forms: substring, glob (*, ?, []), or "
            "regex prefixed with 're:'. Empty list means no path include restriction."
        ),
    )
    lc_source_roles_exclude: set[str] = Field(
        default_factory=set,
        description=(
            "SFI metadata.role values that must never generate LearningComponents. "
            "Compared case-insensitively after whitespace normalization."
        ),
    )
    lc_source_roles_include: set[str] = Field(
        default_factory=lambda: {"expectation"},
        description=(
            "SFI metadata.role values eligible to generate LearningComponents. "
            "Empty set means no role include restriction. Default is {'expectation'}."
        ),
    )
    lc_source_statement_types_exclude: set[str] = Field(
        default_factory=set,
        description=(
            "Optional blocklist of SFI.statement_type values excluded from LC generation. "
            "Use this to keep broad competency Standards in Academic Standards while "
            "preventing them from becoming LC sources."
        ),
    )
    lc_source_statement_types_include: set[str] = Field(
        default_factory=set,
        description=(
            "Optional allowlist of SFI.statement_type values eligible for LC generation. "
            "Empty set means no statement-type allowlist restriction."
        ),
    )
    lp_builds_towards_min_confidence: float = Field(
        default=0.60,
        description="Minimum confidence to emit buildsTowards relationships.",
        ge=0.0,
        le=1.0,
    )
    lp_cross_grade_builds_towards: bool = Field(
        default=True,
        description="Enable cross-grade buildsTowards progression inference between adjacent single-level grade buckets.",
    )
    lp_cross_grade_relates_to_max_items_per_subject: int = Field(
        default=10,
        description="Cross-grade relatesTo: max sampled Standards per subject per grade.",
        ge=1,
    )
    lp_cross_grade_relates_to: bool = Field(
        default=True,
        description="Enable cross-grade relatesTo progression inference between adjacent single-level grade buckets.",
    )
    lp_cross_level_thread_roles: Optional[list[NodeRole]] = Field(
        default=None,
        description=(
            "Ordered list of canonical IR node roles whose labels form the "
            "cross-level thread identity for LP Phases 2 and 4. Only entries in "
            "progression_context.topic_path_parts with a role in this list are "
            "included in the LP thread key. Example: ['strand'] matches "
            "'Activités numériques' across adjacent levels. When None, uses "
            "thread_key from progression_context."
        ),
        min_length=1,
    )
    lp_cross_stage_builds_towards: bool = Field(
        default=False,
        description=(
            "Cross-grade buildsTowards fallback: if either adjacent level bucket is "
            "banded (low != high), infer buildsTowards across adjacent level ranges "
            "(e.g., I–II -> III–VI)."
        ),
    )
    lp_cross_stage_relates_to: bool = Field(
        default=False,
        description=(
            "Cross-grade relatesTo fallback: if either adjacent level bucket is banded "
            "(low!=high), infer relatesTo across adjacent level ranges "
            "(e.g., I–II <-> III–VI)."
        ),
    )
    lp_excluded_subject_labels: list[str] = Field(
        default=["UNSPECIFIED_SUBJECT"],
        description=(
            "Subject labels to exclude from Phase 3 (within-grade cross-subject "
            "relatesTo) and Phase 4 (cross-grade same-subject relatesTo) "
            "sampling. Default excludes 'UNSPECIFIED_SUBJECT' since including "
            "unmapped items in cross-subject pairing adds noise without value."
        ),
    )
    lp_grade_label_map: Optional[dict[str, int]] = Field(
        default=None,
        description=(
            "Explicit mapping from grade_key labels (as they appear in "
            "progression_context.grade_key, normalized to lowercase + strip) to "
            "integer ordinals for LP inference. Ordinals represent each level's "
            "position in the sequence (not necessarily the grade number); "
            "adjacency is determined by ordinals differing by exactly 1. "
            "Multiple grade_keys may map to the same ordinal to merge subtrees "
            "(e.g., 'paliers du niveau ce1' and 'planification ce1' both -> 1). "
            "Config keys must be lowercase + stripped. When None, falls back to "
            "_parse_ordinal heuristics on grade_ordinal_low/high."
        ),
    )
    lp_relates_to_max_edges_per_sfi: int = Field(
        default=3,
        ge=1,
        description="Cap the number of relatesTo edges per SFI (undirected cap).",
    )
    lp_relates_to_min_confidence: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Minimum confidence to emit relatesTo relationships (kept higher to avoid over-linking).",
    )
    lp_subject_role: Optional[NodeRole] = Field(
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
    lp_within_grade_allow_banded_levels: bool = Field(
        default=False,
        description=(
            "If false (default), Phase 1 and Phase 3 'within-grade' inference only runs "
            "on single-level buckets where grade_ordinal_low == grade_ordinal_high. "
            "If true, allow within-grade inference to also run on banded/stage buckets "
            "(low != high), e.g., 'Std I–II'."
        ),
    )
    lp_within_grade_builds_towards: bool = Field(
        default=True,
        description="Enable within-grade buildsTowards progression inference (sequential standards within the same grade/subject thread).",
    )
    lp_within_grade_relates_to: bool = Field(
        default=True,
        description=(
            "Enable within-grade relatesTo inference (cross-subject only). "
            "Threads within the same subject are skipped to reduce noise."
        ),
    )
    lp_within_grade_relates_to_max_items_per_subject: int = Field(
        default=5,
        description="Within-grade relatesTo (cross-subject only): max sampled standards per subject (keeps LLM calls bounded).",
        ge=1,
    )
    lp_within_level_bucket_roles: Optional[list[NodeRole]] = Field(
        default=None,
        description=(
            "Ordered list of canonical IR node roles whose labels form the "
            "within-level bucket identity for Phase 1 within-level buildsTowards "
            "and Phase 3 within-level relatesTo sampling. Use this to decide which "
            "items are allowed to be compared inside the same level. Example: "
            "['strand'] groups all items in the same strand across smaller units, "
            "weeks, topics, or substages. When None, uses thread_key from "
            "progression_context."
        ),
        min_length=1,
    )
    lp_within_level_fallback_fields: list[
        Literal["statement_type", "statement_code", "source_label", "academic_subject"]
    ] = Field(
        default_factory=list,
        description=(
            "Ordered source fields used as fallback within-level bucket segments "
            "when `lp_within_level_bucket_roles` cannot produce a key for an item. "
            "This is useful when the hierarchy path is shallow but source fields "
            "such as statement_type identify stable skill tracks. Empty list means "
            "no source-field fallback."
        ),
    )
    namespace_uuid: UUID = Field(
        default=UUID("b9a2b2d5-0f6c-4f3f-8d32-b7a66f999c5a"),
        description="Pinned UUID namespace used with uuid5 for deterministic IDs.",
    )
    overwrite: bool = Field(False, description="Overwrite existing knowledge graphs.")

    @field_validator(
        "as_academic_subject_default",
        "as_adoption_status",
        "as_attribution_statement",
        "as_author",
        "as_case_uri_base",
        "as_jurisdiction_default",
        "as_license",
        "as_provider",
        mode="before",
    )
    @classmethod
    def _strip_and_require_non_empty_kg_strings(cls, v: Any, info: Any) -> str:
        """Strip required KG string config fields and reject empty values.

        These fields are copied into required LC KG entity fields during export.
        Validating them at config-load time catches bad run configs before the exporter
        starts writing nodes, relationships, or intermediate artifacts.

        Parameters
        ----------
        v
            The configured value for the field being validated.
        info
            Pydantic field validation info; used to name the bad field in errors.

        Returns
        -------
        str
            The stripped, non-empty string value.

        Raises
        ------
        TypeError
            If the configured value is not a string.
        ValueError
            If the configured value is None or empty after stripping whitespace.
        """

        if v is None:
            raise ValueError(f"{info.field_name} must be a non-empty string.")

        if not isinstance(v, str):
            raise TypeError(
                f"{info.field_name} must be a string. Got {type(v).__name__}."
            )

        v2 = v.strip()

        if not v2:
            raise ValueError(f"{info.field_name} must be a non-empty string.")

        return v2

    @model_validator(mode="after")
    def _validate_atomic_skills_bounds(self) -> Self:
        """Validate that `lc_atomic_skills_min_per_sfi` is less than or equal to
        `lc_max_splits_per_standard` when using `llm_atomic_skills`.

        Returns
        -------
        Self
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If `lc_atomic_skills_min_per_sfi` is greater than
            `lc_max_splits_per_standard`.
        """

        if (
            self.lc_policy == "llm_atomic_skills"
            and self.lc_atomic_skills_min_per_sfi > self.lc_max_splits_per_standard
        ):
            raise ValueError(
                f"lc_atomic_skills_min_per_sfi ({self.lc_atomic_skills_min_per_sfi}) "
                f"must be <= lc_max_splits_per_standard ({self.lc_max_splits_per_standard})."
            )

        return self

    @model_validator(mode="after")
    def _validate_lc_output_language_policy(self) -> Self:
        """Validate LC output-language settings.

        Returns
        -------
        Self
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If lc_output_language_policy='explicit_tag' but lc_output_language_tag is
            missing.
        """

        if (
            self.lc_output_language_policy == "explicit_tag"
            and not self.lc_output_language_tag
        ):
            raise ValueError(
                "lc_output_language_policy='explicit_tag' requires "
                "lc_output_language_tag to be provided."
            )

        return self

    @model_validator(mode="after")
    def _validate_lc_source_path_depth_bounds(self) -> Self:
        """Validate optional LC source path-depth bounds.

        Raises
        ------
        ValueError
            If both `lc_source_min_path_depth` and `lc_source_max_path_depth` are
            provided but min > max.
        """

        if (
            self.lc_source_min_path_depth is not None
            and self.lc_source_max_path_depth is not None
            and self.lc_source_min_path_depth > self.lc_source_max_path_depth
        ):
            raise ValueError(
                "lc_source_min_path_depth must be <= lc_source_max_path_depth."
            )

        return self

    @field_validator(
        "lc_source_path_patterns_exclude", "lc_source_path_patterns_include"
    )
    @classmethod
    def _validate_lc_source_regex_path_patterns(
        cls, v: list[str], info: Any
    ) -> list[str]:
        """Validate regex-prefixed LC source path patterns at config-load time.

        Parameters
        ----------
        v
            The configured path pattern list. Entries that start with `re:` are
            compiled to ensure malformed regexes fail fast before KG export.
        info
            Pydantic field validation info; used to report the field name.

        Returns
        -------
        list[str]
            The original pattern list, unchanged.

        Raises
        ------
        ValueError
            If a regex-prefixed path pattern is empty or invalid.
        """

        return validate_regex_prefixed_patterns(field_name=info.field_name, patterns=v)

    @field_validator("lp_within_level_bucket_roles", "lp_cross_level_thread_roles")
    @classmethod
    def _validate_lp_bucket_or_thread_roles(
        cls, v: Optional[list[NodeRole]], info: Any
    ) -> Optional[list[NodeRole]]:
        """Validate learning progression bucket/thread-key roles.

        Parameters
        ----------
        v
            The ordered role list that defines a within-level bucket identity or a
            cross-level thread identity.
        info
            Pydantic field validation info used to report the field name.

        Returns
        -------
        Optional[list[NodeRole]]
            The validated role list.

        Raises
        ------
        ValueError
            If the role list contains duplicates or disallowed roles.
        """

        return validate_lp_roles(field_name=info.field_name, roles=v)

    @field_validator("lp_subject_role")
    @classmethod
    def _validate_lp_subject_role(cls, v: Optional[NodeRole]) -> Optional[NodeRole]:
        """Validate the configured subject-like bucketing role.

        Parameters
        ----------
        v
            The role used to derive subject buckets for learning progression inference.

        Returns
        -------
        Optional[NodeRole]
            The validated subject role.

        Raises
        ------
        ValueError
            If the role is too generic to support learning progression bucketing.
        """

        if v is None:
            return v

        return validate_lp_role(field_name="lp_subject_role", role=v)

    @field_validator("as_framework_name", mode="before")
    @classmethod
    def _validate_framework_name(cls, v: Optional[str]) -> Optional[str]:
        """Trim optional `as_framework_name` and treat empty strings as None.

        Parameters
        ----------
        v
            The optional override value supplied in config.

        Returns
        -------
        Optional[str]
            The trimmed override string, or None when empty/unset.

        Raises
        ------
        TypeError
            If the value is not a string or None.
        """

        if v is None:
            return None

        if not isinstance(v, str):
            raise TypeError("as_framework_name must be a string or None")

        v2 = v.strip()
        return v2 if v2 else None

    @model_validator(mode="after")
    def _validate_grouping_role_policy(self) -> Self:
        """Validate that if `as_grouping_role_policy` is 'whitelist', then
        `as_grouping_roles_whitelist` is non-empty and does not include
        FRAMEWORK/UNRESOLVED.

        Returns
        -------
        Self
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If `as_grouping_role_policy` is 'whitelist' but
            `as_grouping_roles_whitelist` is empty or includes disallowed roles.
        """

        if self.as_grouping_role_policy == "whitelist":
            if not self.as_grouping_roles_whitelist:
                raise ValueError(
                    "as_grouping_role_policy='whitelist' requires a non-empty "
                    "as_grouping_roles_whitelist."
                )

            # These should never be treated as hierarchy groupings.
            disallowed = {NodeRole.FRAMEWORK, NodeRole.UNRESOLVED}
            overlap = disallowed & set(self.as_grouping_roles_whitelist)

            if overlap:
                raise ValueError(
                    f"as_grouping_roles_whitelist cannot include FRAMEWORK/UNRESOLVED: {overlap}"
                )

        return self

    @model_validator(mode="after")
    def _validate_parenting_relevance(self) -> Self:
        """Validate that as_aux_statement_parenting is compatible with guidance/descriptor
        handling.

        Returns
        -------
        Self
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If as_aux_statement_parenting is 'under_expectation' but neither guidance
            nor descriptor handling is set to export as SFI.
        """

        relevant_any = self.as_guidance_handling in {
            "export_as_sfi_other",
            "attach_to_expectation_metadata",
        } or self.as_descriptor_handling in {
            "export_as_sfi_other",
            "attach_to_expectation_metadata",
        }

        if self.as_aux_statement_parenting == "under_expectation" and not relevant_any:
            raise ValueError(
                "as_aux_statement_parenting='under_expectation' requires exporting "
                "or attaching guidance/descriptors to expectations (set as_guidance_handling "
                "or as_descriptor_handling to 'export_as_sfi_other' or "
                "'attach_to_expectation_metadata')."
            )

        # attach-to-expectation requires the export-time anchor discovery implemented
        # by as_aux_statement_parenting="under_expectation". If we leave
        # as_aux_statement_parenting="as_siblings", export_academic_standards will skip
        # emitting aux nodes (to avoid SFIs) but will never attach them -> silent loss.
        attach_any = (
            self.as_guidance_handling == "attach_to_expectation_metadata"
            or self.as_descriptor_handling == "attach_to_expectation_metadata"
        )
        if attach_any and self.as_aux_statement_parenting != "under_expectation":
            raise ValueError(
                "as_guidance_handling/as_descriptor_handling='attach_to_expectation_metadata' "
                "requires as_aux_statement_parenting='under_expectation' so aux statements "
                "can be anchored to the most recent expectation during export."
            )

        return self

    @model_validator(mode="after")
    def _validate_stable_bases(self) -> Self:
        """Validate that as_case_uri_base is non-empty and stable.

        Returns
        -------
        Self
            The validated CreateKGConfig object.

        Raises
        ------
        ValueError
            If as_case_uri_base is empty.
        """

        if not self.as_case_uri_base:
            raise ValueError("as_case_uri_base must be non-empty and stable.")

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
    models: dict[str, str] = Field(
        default_factory=dict,
        description="Dictionary mapping model types to their identifiers used during the run.",
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
