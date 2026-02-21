"""This module contains top-level Pydantic models."""

# Future Library
from __future__ import annotations

# Standard Library
import re

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
    SpineViolationPolicy,
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
        2, description="Maximum number of group columns for table filldown.", ge=0
    )
    verification_auto_stitch_confidence: float = Field(
        0.75,
        description="If a verified link has confidence >= this value, it will be automatically stitched.",
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


class CaptionContextPattern(BaseSchema):
    """A regex pattern for extracting groupings from table caption text.

    The pattern is searched (not full-match) against the caption text. If a match is
    found, `title_template` is expanded using `Match.expand` (supporting capture group
    back-references like `\\1`).
    """

    flags: int = Field(
        re.IGNORECASE, description="Regex flags (int). Default IGNORECASE."
    )
    match: str = Field(..., description="Regex pattern to search in caption text.")
    role: NodeRole = Field(..., description="Role of the derived grouping.")
    title_template: str = Field(
        ..., description="Regex expansion template using capture groups (Match.expand)."
    )

    @model_validator(mode="after")
    def _validate_regex(self) -> CaptionContextPattern:
        """Validate that the regex pattern is valid.

        Returns
        -------
        CaptionContextPattern
            The validated CaptionContextPattern instance.

        Raises
        ------
        re.error
            If the regex pattern is invalid.
        """

        self._compiled_re = re.compile(self.match, self.flags)  # Raises if invalid

        return self


class SpineConfig(BaseSchema):
    """Policy config for deterministic 'spine correction' between LLM decisions and
    compilation.

    Responsibilities:

    1. Caption context injection: derive missing outer-context anchors from table
        captions.
    2. Role allow/disallow filtering: enforce where specific roles may appear.
    3. Table-local-only role relocation: move roles from context to row groupings.
    4. Cross-chunk consistency check: detect conflicting context across table chunks.
    5. Hard shape enforcement: clear outputs for IGNORE/UNRESOLVED; demote empty
        flagged.

    Title normalization, deduplication, precedence reordering, regex-based splitting,
    and wrapper/grade-band dropping are handled by the segment decision cleaning and
    LLM grouping canonicalization steps.
    """

    allow_partial_context: bool = Field(
        True,
        description="If True, allow emitting with a partial outer context (do not force missing roles).",
    )
    block_local_roles: list[NodeRole] = Field(
        default_factory=list,
        description="Allow-list for SegmentDecision.groupings[] (blocks).",
    )
    caption_context_patterns: list[CaptionContextPattern] = Field(
        default_factory=list,
        description=(
            "Regex patterns for extracting groupings from table caption text. "
            "Each pattern is searched against the caption; if matched, a grouping is "
            "derived using the role and title_template. First match per role wins."
        ),
    )
    casefold_titles_for_matching: bool = Field(
        True,
        description=(
            "Casefold and strip accents from titles for internal conflict detection "
            "(caption injection, chunk consistency). Does not change stored titles."
        ),
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
    canonical_grouping_min_confidence: float = Field(
        0.80,
        description="Minimum confidence level for canonical grouping.",
        ge=0.0,
        le=1.0,
    )
    canonicalization_skip_roles: list[NodeRole] = Field(
        default_factory=lambda: [NodeRole.TOPIC, NodeRole.SUBTOPIC],
        description=(
            "Roles excluded from global grouping canonicalization map *application* "
            "(SegmentDecision.context_groupings and SegmentDecision.groupings). "
            "Default skips TOPIC/SUBTOPIC to avoid incorrect global merges."
        ),
    )
    context_groupings_role_order: list[NodeRole] = Field(
        default_factory=lambda: list(DEFAULT_CONTEXT_GROUPINGS_ROLE_ORDER),
        description=(
            "Outer -> inner precedence for SegmentDecision.context_groupings. "
            "Used by LLM instructions and deterministic validators. "
            "Only roles listed here are ranked; roles omitted are treated as unranked and "
            "ignored by precedence-based checks (ordering/outer-than-context)."
        ),
    )
    heading_role_hints: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Document-specific heading text → role constraints. Each entry has:"
            "- 'pattern': case-insensitive substring or regex to match against section_path headings"
            "- 'role': the NodeRole value to enforce (e.g., 'stage', 'unit', 'grade_level')"
            "- 'note': optional explanation for the LLM"
        ),
    )
    max_table_rows_per_decision: int | None = Field(
        default=None,
        description="If set, only TABLE segments with *body rows* > this value are chunked into multiple SegmentDecisions. Blocks are never chunked.",
    )
    model: str = Field(
        "gpt-5.2-2025-12-11", description="OpenAI model for canonical IR."
    )
    overwrite: bool = Field(False, description="Overwrite existing canonical IR JSON.")
    row_grouping_canonicalization_roles: Optional[list[NodeRole]] = Field(
        None,
        description=(
            "Optional list of roles for which table row-level groupings "
            "(RowDecision.groupings) should be included in global grouping "
            "canonicalization. When empty, row-level groupings are left as-emitted, and "
            "only context_groupings/groupings are canonicalized."
        ),
    )
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

    @field_validator("context_groupings_role_order")
    @classmethod
    def _validate_context_groupings_role_order(
        cls, v: list[NodeRole]
    ) -> list[NodeRole]:
        """Validate configured context-groupings role order.

        Notes: An empty list is allowed (disables precedence-based checks), but is
        usually not recommended.

        Parameters
        ----------
        v
            The list of NodeRoles representing the context groupings role order.

        Returns
        -------
        list[NodeRole]
            The validated list of NodeRoles for context groupings role order.

        Raises
        ------
        ValueError
            If there are duplicate NodeRoles in the list or if any NodeRole is invalid
            for context groupings (e.g., FRAMEWORK, PROSE, UNRESOLVED).
        """

        seen: set[NodeRole] = set()

        for r in v or []:
            if r in seen:
                raise ValueError(
                    f"Duplicate NodeRole in context_groupings_role_order: {r.value}"
                )

            if r in (NodeRole.FRAMEWORK, NodeRole.PROSE, NodeRole.UNRESOLVED):
                raise ValueError(
                    f"Invalid NodeRole in context_groupings_role_order (not a grouping container): {r.value}"
                )

            seen.add(r)

        return v

    @field_validator("row_grouping_canonicalization_roles")
    @classmethod
    def validate_row_grouping_canonicalization_roles(
        cls, v: list[NodeRole]
    ) -> list[NodeRole]:
        """Validate row-grouping canonicalization role list.

        Parameters
        ----------
        v
            The configured roles to canonicalize at row level.

        Returns
        -------
        list[NodeRole]
            The validated roles with duplicates rejected.

        Raises
        ------
        ValueError
            If the list contains duplicates or invalid roles.
        """

        if not v:
            return v

        seen: set[NodeRole] = set()

        for r in v:
            if r in seen:
                raise ValueError(
                    f"Duplicate NodeRole in row_grouping_canonicalization_roles: {r.value}"
                )

            if r in (NodeRole.FRAMEWORK, NodeRole.UNRESOLVED):
                raise ValueError(
                    f"Invalid NodeRole in row_grouping_canonicalization_roles: {r.value}"
                )

            seen.add(r)

        return v

    @model_validator(mode="after")
    def _validate_outer_context_roles_vs_precedence(self) -> Self:
        """Sanity-check coherence between:

        1. canonical_ir.context_groupings_role_order (ranked precedence)
        2. spine_policy.outer_context_roles (roles expected to live in
            context_groupings for chunked-table stability)

        Goals:

        1. Prevent obviously unstable roles (TOPIC/SUBTOPIC/PROSE/etc.) from being
            treated as chunk-stable outer context.
        2. Ensure that if both an outer-context role and a row-local role are ranked,
            the outer-context role is not ordered *inside* the row-local role (which
            would trigger grouping_outer_than_context errors and confuse LLM
            instructions).
        3. If WEEK is configured as an outer-context role, require it to be treated as
           table-wide context (not row-local) and explicitly ranked.

        Returns
        -------
        Self
            The validated CreateCanonicalConfig.

        Raises
        ------
        ValueError
            If configuration is internally inconsistent.
        """

        outer_ctx = set(self.spine_policy.outer_context_roles or [])
        row_roles = set(self.spine_policy.row_roles or [])
        table_local_only = set(self.spine_policy.table_local_only_roles or [])

        # These roles should never be enforced as "outer context" for chunked tables.
        disallowed_outer_ctx = {
            NodeRole.FRAMEWORK,
            NodeRole.UNRESOLVED,
            NodeRole.PROSE,
            NodeRole.TOPIC,
            NodeRole.SUBTOPIC,
        }

        overlap = outer_ctx & disallowed_outer_ctx

        if overlap:
            bad = ", ".join(sorted(r.value for r in overlap))
            raise ValueError(
                f"spine_policy.outer_context_roles contains role(s) that are unstable "
                f"or not valid as outer context for chunked tables: {bad}. "
                f"Remove them from outer_context_roles."
            )

        # Special handling for WEEK: allowed, but only if treated as table-wide (not
        # row-local) and explicitly ranked (so ordering validators can behave
        # deterministically).
        if NodeRole.WEEK in outer_ctx and (
            NodeRole.WEEK in row_roles or NodeRole.WEEK in table_local_only
        ):
            raise ValueError(
                "NodeRole.WEEK is listed in spine_policy.outer_context_roles, but is "
                "also listed as a row-local or table-local-only role "
                "(row_roles/table_local_only_roles). If WEEK is outer context for a "
                "chunked table, it must NOT be row-local; remove WEEK from "
                "row_roles/table_local_only_roles."
            )

        # Precedence coherence checks only apply to ranked roles.
        role_order = list(self.context_groupings_role_order or [])
        idx = {r: i for i, r in enumerate(role_order)}

        if role_order:
            # If WEEK is outer context, it should be explicitly ranked.
            if NodeRole.WEEK in outer_ctx and NodeRole.WEEK not in idx:
                raise ValueError(
                    "NodeRole.WEEK is listed in spine_policy.outer_context_roles but is "
                    "not present in canonical_ir.context_groupings_role_order. Add WEEK "
                    "to context_groupings_role_order (ranked) or remove it from "
                    "outer_context_roles."
                )

            # Ensure outer-context roles are not ranked *inside* row-local roles when
            # both are ranked. Skip dual-duty roles (in BOTH outer_context_roles and
            # row_roles) on the row side--these legitimately serve both roles depending
            # on table type.
            purely_row_roles = row_roles - outer_ctx

            for oc in outer_ctx:
                if oc not in idx:
                    continue

                for rr in purely_row_roles:
                    if rr not in idx:
                        continue

                    if idx[oc] > idx[rr]:
                        raise ValueError(
                            f"In canonical_ir.context_groupings_role_order, an "
                            f"outer-context role is ranked INNER than a row-local role, "
                            f"which can cause grouping_outer_than_context errors for tables. "
                            f"Problem: outer_context_role='{oc.value}' (rank {idx[oc]}) "
                            f"is after row_role='{rr.value}' (rank {idx[rr]}). "
                            f"Reorder context_groupings_role_order so outer-context "
                            f"roles are outer than row-local roles (or remove one of "
                            f"these roles from the ranked list)."
                        )

        return self


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
