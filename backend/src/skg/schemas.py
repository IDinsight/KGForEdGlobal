"""This module contains top-level Pydantic models."""

# Standard Library
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional, Self

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
from skg.utils.general import make_dir


def _validate_bcp47(code: str) -> str:
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


# Common fields with descriptions.
_BCP47Str = Annotated[str, AfterValidator(_validate_bcp47)]
BBox = Annotated[
    list[float],
    AfterValidator(validate_bbox_order),
    Field(
        description="Bounding box [x0, y0, x1, y1] in absolute pixels (px) relative to the image dimensions.",
        max_length=4,
        min_length=4,
    ),
]
LanguageField = Annotated[
    _BCP47Str,
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


class CreateKGConfig(BaseSchema):
    """Configuration for knowledge graph creation from document IR."""

    document_profile_fp: FilePath = Field(
        ...,
        description="Filesystem path to the DocumentProfile JSON for the curriculum.",
    )
    overwrite: bool = Field(
        False, description="Overwrite existing knowledge graph artifacts."
    )


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
