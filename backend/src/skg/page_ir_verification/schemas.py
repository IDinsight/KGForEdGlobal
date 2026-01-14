"""This module contains schemas used for **verifying** page Intermediate
Representations (IRs).
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Optional

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from skg.schemas import BaseSchema
from skg.utils.constants import PageContinuationKind


class PageIRContinuityVerdict(BaseSchema):
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
    set_next_table_repeats_header: Optional[bool] = Field(
        None,
        description=f"Only allowed when is_continuation=true AND continuation_kind='{PageContinuationKind.TABLE.value}' AND next candidate is a table. True/false patches repeats_header; null leaves as-is.",
    )

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
    def validate_repeats_header_consistency(self) -> PageIRContinuityVerdict:
        """Validate internal consistency of the continuation verdict.

        Returns
        -------
        PageIRContinuityVerdict
            The passed in PageIRContinuityVerdict.

        Raises
        ------
        ValueError
            If any internal consistency invariant is violated.
        """

        # If no continuation, kind must be NONE and repeats_header must be null.
        if not self.is_continuation:
            if self.continuation_kind != PageContinuationKind.NONE:
                raise ValueError(
                    "If is_continuation=false, continuation_kind must be 'none'."
                )
            if self.set_next_table_repeats_header is not None:
                raise ValueError(
                    "If is_continuation=false, set_next_table_repeats_header must be null."
                )
            return self

        # Continuation true means kind must not be NONE.
        if self.continuation_kind == PageContinuationKind.NONE:
            raise ValueError(
                "If is_continuation=true, continuation_kind must not be 'none'."
            )

        # If not a table continuation, repeats_header must be null.
        if (
            self.continuation_kind != PageContinuationKind.TABLE
            and self.set_next_table_repeats_header is not None
        ):
            raise ValueError(
                "set_next_table_repeats_header only allowed for table continuations."
            )

        return self
