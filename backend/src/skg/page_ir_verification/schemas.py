"""This module contains schemas used for **verifying** page Intermediate
Representations (IRs).
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Optional

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

    clamped_confidence: float | None = Field(
        None,
        description="Postprocess/effective confidence used for applying edits. If omitted, defaults to `confidence` (clamped to [0, 1]).",
        ge=0.0,
        le=1.0,
    )
    next_page_index: int = Field(..., description="0-based index of the next page.")
    prev_page_index: int = Field(..., description="0-based index of the previous page.")

    # What the model thinks.
    confidence: float = Field(
        ...,
        description="Page continuation confidence score (0.0 to 1.0).",
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
    rationale: str = Field(..., description="Explanation for the verdict.")

    # Minimal suggested edits (null means leave as-is).
    set_next_item_boundary: Optional[ItemBoundary] = Field(
        None,
        description="Boundary state for the first item on the next page. In pairwise verification, do not set 'both'—only 'resumed' (or null).",
    )
    set_next_table_repeats_header: Optional[bool] = Field(
        None,
        description="If continuation_kind is 'table' and the next page contains a repeated header, set this to true. Set to false if the header is not repeated. Null means leave as-is.",
    )
    set_prev_item_boundary: Optional[ItemBoundary] = Field(
        None,
        description="Boundary state for the last item on the previous page. In pairwise verification, do not set 'both'—only 'truncated' (or null).",
    )

    @model_validator(mode="after")
    def inject_default_clamped_confidence(self) -> PageIRContinuityVerdict:
        """Inject default clamped confidence if not provided. Here, we just hard clamp
        into [0, 1] (this is just range-safety, not the veto clamp) so that it is
        always populated for reports and consistent downstream comparisons.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict with clamped_confidence populated.
        """

        if self.clamped_confidence is None:
            self.clamped_confidence = max(0.0, min(1.0, float(self.confidence)))

        return self

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
            If the suggested edits are inconsistent with is_continuation.
        """

        if self.is_continuation:
            # If continuing, suggested item boundaries (if provided) must be compatible.
            if self.set_prev_item_boundary in {
                ItemBoundary.COMPLETE,
                ItemBoundary.BOTH,
                ItemBoundary.RESUMED,
            }:
                raise ValueError(
                    f"When is_continuation=true, set_prev_item_boundary cannot be "
                    f"{ItemBoundary.COMPLETE.value}/{ItemBoundary.RESUMED.value}/{ItemBoundary.BOTH.value}."
                )
            if self.set_next_item_boundary in {
                ItemBoundary.COMPLETE,
                ItemBoundary.BOTH,
                ItemBoundary.TRUNCATED,
            }:
                raise ValueError(
                    f"When is_continuation=true, set_next_item_boundary cannot be "
                    f"{ItemBoundary.COMPLETE.value}/{ItemBoundary.TRUNCATED.value}/{ItemBoundary.BOTH.value}."
                )
            if (
                self.continuation_kind != PageContinuationKind.TABLE
                and self.set_next_table_repeats_header is not None
            ):
                raise ValueError(
                    "set_next_table_repeats_header only allowed for table continuations."
                )
        elif (
            self.set_prev_item_boundary is not None
            or self.set_next_item_boundary is not None
            or self.set_next_table_repeats_header is not None
        ):
            raise ValueError(
                "When is_continuation=false, all set_* fields must be null."
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

    @model_validator(mode="after")
    def validate_page_indices(self) -> PageIRContinuityVerdict:
        """Validate page index ordering and adjacency.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If page indices are not in valid order or not adjacent.
        """

        if self.prev_page_index >= self.next_page_index:
            raise ValueError(
                f"Invalid page index order: prev={self.prev_page_index}, next={self.next_page_index}"
            )
        if self.next_page_index != self.prev_page_index + 1:
            raise ValueError(
                f"Continuity verdict must be for adjacent pages: prev={self.prev_page_index}, next={self.next_page_index}"
            )

        return self
