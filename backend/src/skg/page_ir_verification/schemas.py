"""This module contains schemas used for **verifying** page Intermediate
Representations (IRs).
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Optional, Self

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Package Library
from skg.utils.constants import ItemBoundary, PageContinuationKind


# Schemas for primitives.
class BaseModelPageIRVerification(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Schemas for verification.
class PageIRContinuityVerdict(BaseModelPageIRVerification):
    """Schema for page IR continuity verification between two pages."""

    confidence: float = Field(
        ...,
        description="Verification confidence score (0.0 to 1.0).",
        ge=0.0,
        le=1.0,
    )
    continuation_kind: PageContinuationKind = Field(
        ..., description="Type of content continuing across the break."
    )
    is_continuation: bool = Field(
        ...,
        description="True if content clearly continues from prev page to next page.",
    )
    next_page_index: Optional[int] = Field(
        None,
        description="0-based index of the next page in the PDF. This should be populated by the Python pipeline; it may be null during verification.",
    )
    prev_page_index: Optional[int] = Field(
        None,
        description="0-based index of the previous page in the PDF. This should be populated by the Python pipeline; it may be null during verification.",
    )
    rationale: str = Field(
        ..., description="Explanation for the verdict.", min_length=50
    )
    set_next_item_boundary: Optional[ItemBoundary] = Field(
        None,
        description="Boundary patch for the first item on the next page. In pairwise verification, only set this in the POSITIVE case (resumed or null). Must be null when is_continuation=false.",
    )
    set_next_table_repeats_header: Optional[bool] = Field(
        None,
        description=f"If continuation_kind is '{PageContinuationKind.TABLE.value}' and the next page contains a repeated header, set this to true. Set to false if the header is not repeated. Null means leave as-is.",
    )
    set_prev_item_boundary: Optional[ItemBoundary] = Field(
        None,
        description="Boundary patch for the last item on the previous page. In pairwise verification, only set this in the POSITIVE case (truncated or null). Must be null when is_continuation=false.",
    )

    @model_validator(mode="after")
    def validate_boundary_edit_consistency(self) -> PageIRContinuityVerdict:
        """Validate consistency of suggested boundary edits with is_continuation.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If the suggested edits are inconsistent.
        """

        if self.is_continuation:
            if (
                self.set_prev_item_boundary is not None
                and self.set_prev_item_boundary != ItemBoundary.TRUNCATED
            ):
                raise ValueError(
                    f"Invalid edit: set_prev_item_boundary must be "
                    f"'{ItemBoundary.TRUNCATED.value}' or null."
                )

            if (
                self.set_next_item_boundary is not None
                and self.set_next_item_boundary != ItemBoundary.RESUMED
            ):
                raise ValueError(
                    f"Invalid edit: set_next_item_boundary must be "
                    f"'{ItemBoundary.RESUMED.value}' or null."
                )

            # Table specific check.
            if (
                self.continuation_kind != PageContinuationKind.TABLE
                and self.set_next_table_repeats_header is not None
            ):
                raise ValueError(
                    "set_next_table_repeats_header only allowed for table continuations."
                )
        else:
            # Negative case: the model must not propose boundary/table-header edits.
            # Directional edge-clearing is handled deterministically in Python.
            if (
                self.set_prev_item_boundary is not None
                or self.set_next_item_boundary is not None
            ):
                raise ValueError(
                    "If is_continuation=false, set_prev_item_boundary and "
                    "set_next_item_boundary must be null."
                )
            if self.set_next_table_repeats_header is not None:
                raise ValueError(
                    "If is_continuation=false, set_next_table_repeats_header must be null."
                )

        return self

    @model_validator(mode="after")
    def validate_confidence_policy(self) -> PageIRContinuityVerdict:
        """Validate that positive decisions meet the minimum confidence threshold.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If the confidence policy is violated.
        """

        # If the LLM is 'uncertain' (<= 0.49), it MUST default to False. Therefore, if
        # it chose True, it MUST be >= 0.50.
        if self.is_continuation and self.confidence < 0.50:
            raise ValueError(
                f"Violation of Uncertainty Policy: is_continuation=True requires "
                f"confidence >= 0.50. (Got {self.confidence})"
            )

        return self

    @model_validator(mode="after")
    def validate_continuation_consistency(self) -> PageIRContinuityVerdict:
        """Validate consistency between is_continuation and continuation_kind.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If the continuation fields are inconsistent.
        """

        # If not a continuation, kind must be NONE.
        if (
            not self.is_continuation
            and self.continuation_kind != PageContinuationKind.NONE
        ):
            raise ValueError(
                "If is_continuation=false, continuation_kind must be 'none'."
            )

        # If it is a continuation, kind must not be NONE.
        if self.is_continuation and self.continuation_kind == PageContinuationKind.NONE:
            raise ValueError(
                "If is_continuation=true, continuation_kind must not be 'none'."
            )

        return self


# Schemas for configs.
class VerificationConfig(BaseModelPageIRVerification):
    """Configuration for page IR verification from a PDF document."""

    end_page: Optional[int] = Field(
        None, description="0-based end page (exclusive). Default: to end."
    )
    min_confidence_to_patch: float = Field(
        0.75,
        ge=0.0,
        le=1.0,
        description="Only apply boundary/repeats_header patches when verdict.confidence >= this threshold.",
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
