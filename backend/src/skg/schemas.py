"""This module contains top-level Pydantic models."""

# Standard Library
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
from skg.utils.general import make_dir, validate_bcp47

# Common fields with descriptions.
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
        "gpt-5.2-2025-12-11", description="OpenAI model for page IR extraction."
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
    """Configuration for document IR stitching from verified page IR JSONs."""

    keep_artifacts: bool = Field(
        False,
        description="Whether to keep artifacts such as page numbers, headers, footers, etc. after stitching.",
    )
    repair_hyphenation: bool = Field(
        True, description="Whether to repair hyphenation for stitched text."
    )
    overwrite: bool = Field(False, description="Overwrite existing document IR JSON.")


class RunConfig(BaseSchema):
    """Pydantic model for run configuration."""

    page_ir_extraction: ExtractionConfig
    page_ir_verification: VerificationConfig
    document_ir: StitchingConfig


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
