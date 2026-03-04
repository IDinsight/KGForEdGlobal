"""This module contains schemas used for **verifying** page Intermediate
Representations (IRs).
"""

# Standard Library
from typing import Literal, Optional, Self

# Third Party Library
from pydantic import Field, model_validator

# Package Library
from skg.schemas import BaseSchema
from skg.utils.constants import PageContinuationKind


# Schemas for verification.
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
    def validate_continuation_invariants(self) -> Self:
        """Enforce all schema-internal consistency rules in a single pass.

        Rules
        -----
        1. is_continuation=false -> continuation_kind MUST be NONE,
            set_next_table_repeats_header MUST be null.
        2. is_continuation=true -> continuation_kind MUST NOT be NONE, confidence MUST
            be >= 0.50.
        3. continuation_kind != TABLE -> set_next_table_repeats_header MUST be null.

        Returns
        -------
        PageIRContinuityVerdict
            The validated instance.

        Raises
        ------
        ValueError
            If any invariant is violated.
        """

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

        # is_continuation=true.
        if self.continuation_kind == PageContinuationKind.NONE:
            raise ValueError(
                "If is_continuation=true, continuation_kind must not be 'none'."
            )
        if self.confidence < 0.50:
            raise ValueError(
                f"Violation of Uncertainty Policy: is_continuation=True requires "
                f"confidence >= 0.50. (Got {self.confidence})"
            )

        # repeats_header only valid for table continuations.
        if (
            self.continuation_kind != PageContinuationKind.TABLE
            and self.set_next_table_repeats_header is not None
        ):
            raise ValueError(
                "set_next_table_repeats_header only allowed for table continuations."
            )

        return self


# Schemas for validation.
class ValidationIssue(BaseSchema):
    """A single issue found during validation of a continuity verdict against the
    source page images.
    """

    description: str = Field(
        ...,
        description=(
            "Clear description of the discrepancy between the verification verdict "
            "and the source images. Reference specific fields and quote relevant "
            "evidence where possible."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "The PageIRContinuityVerdict field that has the issue (e.g., "
            "'is_continuation', 'continuation_kind', 'set_next_table_repeats_header'). "
            "Null if the issue is holistic."
        ),
    )
    severity: Literal["error", "warning"] = Field(
        ...,
        description=(
            "'error' for issues that make the verdict materially incorrect "
            "(wrong continuation decision, wrong kind, wrong header patch). "
            "'warning' for minor concerns (slightly low confidence, weak rationale)."
        ),
    )
    suggested_fix: Optional[str] = Field(
        default=None,
        description=(
            "A concrete, actionable suggested fix (e.g., 'Set is_continuation=true "
            "and continuation_kind=table', 'Change confidence from 0.55 to 0.75'). "
            "Required for error-severity issues; optional for warnings."
        ),
    )


class ValidationVerdict(BaseSchema):
    """Structured verdict from the validation agent comparing a continuity verification
    result against the source page images.

    When the verdict is failing (passed=false), the validation agent must supply a
    corrected PageIRContinuityVerdict that fixes all error-severity issues.
    """

    corrected_verdict: Optional[PageIRContinuityVerdict] = Field(
        default=None,
        description=(
            "Corrected PageIRContinuityVerdict that fixes all error-severity issues. "
            "Required when passed=false; must be null when passed=true."
        ),
    )
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="List of issues found during validation. Must be non-empty when passed=false.",
    )
    passed: bool = Field(
        ...,
        description=(
            "True if the verification verdict is accurate and consistent with the "
            "source images; false if corrections are needed."
        ),
    )
    rationale: str = Field(
        ..., description="Brief explanation of the overall assessment.", min_length=30
    )

    @model_validator(mode="after")
    def validate_corrected_verdict_consistency(self) -> Self:
        """Validate that corrected_verdict is present iff passed=false.

        Returns
        -------
        ContinuityValidationVerdict
            The validated instance.

        Raises
        ------
        ValueError
            If corrected_verdict presence is inconsistent with passed.
        """

        if not self.passed and self.corrected_verdict is None:
            raise ValueError(
                "A failing verdict (passed=false) must include corrected_verdict "
                "with a complete, corrected PageIRContinuityVerdict that fixes all "
                "error-severity issues."
            )

        if self.passed and self.corrected_verdict is not None:
            raise ValueError(
                "A passing verdict (passed=true) must not include corrected_verdict. "
                "Set corrected_verdict to null when the verification is correct."
            )

        return self

    @model_validator(mode="after")
    def validate_error_issues_have_suggested_fix(self) -> Self:
        """Validate that every error-severity issue includes a suggested_fix.

        Returns
        -------
        ValidationVerdict
            The validated instance.

        Raises
        ------
        ValueError
            If any error-severity issue is missing a suggested_fix.
        """

        for i, issue in enumerate(self.issues):
            if issue.severity == "error" and (
                issue.suggested_fix is None or not issue.suggested_fix.strip()
            ):
                raise ValueError(
                    f"Error-severity issue at issues[{i}] must include a non-empty "
                    f"suggested_fix. Issue description: {issue.description[:200]}"
                )

        return self

    @model_validator(mode="after")
    def validate_fail_requires_error_issues(self) -> Self:
        """Validate that a failing verdict includes at least one error-severity issue.

        Returns
        -------
        ValidationVerdict
            The validated instance.

        Raises
        ------
        ValueError
            If passed=false but no error-severity issues are present.
        """

        if not self.passed:
            if not self.issues:
                raise ValueError(
                    "A failing verdict (passed=false) must include at least one issue."
                )

            has_errors = any(issue.severity == "error" for issue in self.issues)

            if not has_errors:
                raise ValueError(
                    "A failing verdict (passed=false) must include at least one issue "
                    "with severity='error'. If all issues are warnings, set passed=true."
                )

        return self

    @model_validator(mode="after")
    def validate_rationale_non_empty(self) -> Self:
        """Validate that rationale is non-empty.

        Returns
        -------
        ContinuityValidationVerdict
            The validated instance.

        Raises
        ------
        ValueError
            If rationale is empty or whitespace-only.
        """

        if not self.rationale or not self.rationale.strip():
            raise ValueError("Rationale must be non-empty.")

        return self
