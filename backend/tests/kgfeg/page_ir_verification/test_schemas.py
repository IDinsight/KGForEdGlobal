"""This is the main module for testing page_ir_verification/schemas.py."""

# Standard Library
from typing import Optional

# Third Party Library
import pytest

# Package Library
from kgfeg.page_ir_verification.schemas import (
    ContinuityValidationIssue,
    ContinuityValidationVerdict,
    PageIRContinuityVerdict,
)
from kgfeg.utils.constants import PageContinuationKind

_VALID_RATIONALE = "x" * 50  # Minimum-length rationale for all schemas.


def make_continuity_verdict(
    *,
    confidence: float = 0.85,
    continuation_kind: PageContinuationKind = PageContinuationKind.NONE,
    is_continuation: bool = False,
    next_page_index: Optional[int] = None,
    prev_page_index: Optional[int] = None,
    rationale: str = _VALID_RATIONALE,
    set_next_table_repeats_header: Optional[bool] = None,
) -> PageIRContinuityVerdict:
    """Build a `PageIRContinuityVerdict` with sensible defaults.

    Parameters
    ----------
    confidence
        Verification confidence score.
    continuation_kind
        Type of content continuing across the break.
    is_continuation
        Whether content continues from prev page to next page.
    next_page_index
        0-based index of the next page.
    prev_page_index
        0-based index of the previous page.
    rationale
        Explanation for the verdict.
    set_next_table_repeats_header
        Optional header-repeat patch signal.

    Returns
    -------
    PageIRContinuityVerdict
        A valid verdict instance.
    """

    return PageIRContinuityVerdict(
        confidence=confidence,
        continuation_kind=continuation_kind,
        is_continuation=is_continuation,
        next_page_index=next_page_index,
        prev_page_index=prev_page_index,
        rationale=rationale,
        set_next_table_repeats_header=set_next_table_repeats_header,
    )


def make_issue(
    *,
    description: str = "Something is wrong with the verdict fields.",
    field_name: Optional[str] = "is_continuation",
    severity: str = "error",
    suggested_fix: Optional[str] = "Set is_continuation=true",
) -> ContinuityValidationIssue:
    """Build a `ContinuityValidationIssue` with sensible defaults.

    Parameters
    ----------
    description
        Description of the discrepancy.
    field_name
        The verdict field that has the issue.
    severity
        Issue severity ("error"` or "warning").
    suggested_fix
        Actionable fix suggestion.

    Returns
    -------
    ContinuityValidationIssue
        A valid issue instance.
    """

    return ContinuityValidationIssue(
        description=description,
        field_name=field_name,
        severity=severity,
        suggested_fix=suggested_fix,
    )


def make_validation_verdict(
    *,
    corrected_verdict: Optional[PageIRContinuityVerdict] = None,
    issues: Optional[list[ContinuityValidationIssue]] = None,
    passed: bool = True,
    rationale: str = _VALID_RATIONALE,
) -> ContinuityValidationVerdict:
    """Build a `ContinuityValidationVerdict` with sensible defaults.

    Parameters
    ----------
    corrected_verdict
        Optional corrected verdict (required when `passed=False`).
    issues
        List of validation issues.
    passed
        Whether the verdict passed validation.
    rationale
        Explanation for the overall assessment.

    Returns
    -------
    ContinuityValidationVerdict
        A valid validation verdict instance.
    """

    return ContinuityValidationVerdict(
        corrected_verdict=corrected_verdict,
        issues=issues or [],
        passed=passed,
        rationale=rationale,
    )


class TestContinuityValidationVerdictInvalid:
    """Tests for invalid `ContinuityValidationVerdict` construction."""

    def test_error_issue_with_whitespace_only_fix_raises(self) -> None:
        """An error issue with whitespace-only `suggested_fix` raises."""

        corrected = make_continuity_verdict()
        error_issue = make_issue(severity="error", suggested_fix="   ")

        with pytest.raises(ValueError, match="must include a non-empty suggested_fix"):
            make_validation_verdict(
                corrected_verdict=corrected, issues=[error_issue], passed=False
            )

    def test_error_issue_without_suggested_fix_raises(self) -> None:
        """An error issue missing `suggested_fix` on a failing verdict raises."""

        corrected = make_continuity_verdict()
        error_issue = make_issue(severity="error", suggested_fix=None)

        with pytest.raises(ValueError, match="must include a non-empty suggested_fix"):
            make_validation_verdict(
                corrected_verdict=corrected, issues=[error_issue], passed=False
            )

    def test_failing_with_only_warnings_raises(self) -> None:
        """A failing verdict must include at least one error-severity issue."""

        corrected = make_continuity_verdict()
        warning = make_issue(severity="warning", suggested_fix=None)

        with pytest.raises(ValueError, match="at least one issue.*error"):
            make_validation_verdict(
                corrected_verdict=corrected, issues=[warning], passed=False
            )

    def test_failing_without_corrected_verdict_raises(self) -> None:
        """A failing verdict must include a corrected verdict."""

        error_issue = make_issue(severity="error")

        with pytest.raises(ValueError, match="must include a corrected"):
            make_validation_verdict(
                corrected_verdict=None, issues=[error_issue], passed=False
            )

    def test_failing_without_issues_raises(self) -> None:
        """A failing verdict must include at least one issue."""

        corrected = make_continuity_verdict()

        with pytest.raises(ValueError, match="at least one issue"):
            make_validation_verdict(
                corrected_verdict=corrected, issues=[], passed=False
            )

    def test_passing_with_corrected_verdict_raises(self) -> None:
        """A passing verdict must not include a corrected verdict."""

        corrected = make_continuity_verdict()

        with pytest.raises(ValueError, match="passing verdict.*must not include"):
            make_validation_verdict(corrected_verdict=corrected, passed=True)

    def test_passing_with_error_issue_raises(self) -> None:
        """A passing verdict must not include error-severity issues."""

        error_issue = make_issue(severity="error")

        with pytest.raises(ValueError, match="must not include any issue.*error"):
            make_validation_verdict(issues=[error_issue], passed=True)

    def test_rationale_too_short_raises(self) -> None:
        """A rationale shorter than 50 characters is rejected."""

        with pytest.raises(Exception):
            make_validation_verdict(rationale="Too short")


class TestContinuityValidationIssue:
    """Tests for `ContinuityValidationIssue` construction."""

    def test_field_name_null(self) -> None:
        """A holistic issue with `field_name=None` is valid."""

        issue = make_issue(field_name=None)

        assert issue.field_name is None

    def test_invalid_severity_raises(self) -> None:
        """A severity value other than "error" or "warning" raises."""

        with pytest.raises(Exception):
            make_issue(severity="critical")

    def test_valid_error_issue(self) -> None:
        """An error issue with all fields populated is valid."""

        issue = make_issue(severity="error", suggested_fix="Fix it")

        assert issue.severity == "error"
        assert issue.suggested_fix == "Fix it"

    def test_valid_warning_issue_no_fix(self) -> None:
        """A warning issue without a suggested fix is valid."""

        issue = make_issue(severity="warning", suggested_fix=None)

        assert issue.severity == "warning"
        assert issue.suggested_fix is None


class TestContinuityValidationVerdictValid:
    """Tests for valid `ContinuityValidationVerdict` construction."""

    def test_failing_with_error_and_corrected_verdict(self) -> None:
        """A failing verdict with an error issue and corrected verdict is valid."""

        corrected = make_continuity_verdict(
            continuation_kind=PageContinuationKind.TEXT, is_continuation=True
        )
        error_issue = make_issue(
            severity="error", suggested_fix="Set is_continuation=true"
        )
        verdict = make_validation_verdict(
            corrected_verdict=corrected, issues=[error_issue], passed=False
        )

        assert verdict.passed is False
        assert verdict.corrected_verdict is not None

    def test_failing_with_mixed_issues(self) -> None:
        """A failing verdict with both error and warning issues is valid."""

        corrected = make_continuity_verdict()
        error_issue = make_issue(severity="error", suggested_fix="Fix the field")
        warning_issue = make_issue(severity="warning", suggested_fix=None)
        verdict = make_validation_verdict(
            corrected_verdict=corrected,
            issues=[error_issue, warning_issue],
            passed=False,
        )

        assert len(verdict.issues) == 2

    def test_passing_no_issues(self) -> None:
        """A passing verdict with no issues is valid."""

        verdict = make_validation_verdict(passed=True)

        assert verdict.passed is True
        assert verdict.issues == []
        assert verdict.corrected_verdict is None

    def test_passing_with_warnings_only(self) -> None:
        """A passing verdict may include warning-severity issues."""

        warning = make_issue(severity="warning", suggested_fix=None)
        verdict = make_validation_verdict(issues=[warning], passed=True)

        assert verdict.passed is True
        assert len(verdict.issues) == 1


class TestPageIRContinuityVerdictInvalid:
    """Tests for invalid `PageIRContinuityVerdict` construction."""

    def test_confidence_above_1_raises(self) -> None:
        """Confidence > 1.0 is rejected by the field constraint."""

        with pytest.raises(Exception):
            make_continuity_verdict(confidence=1.01)

    def test_confidence_below_0_raises(self) -> None:
        """Confidence < 0.0 is rejected by the field constraint."""

        with pytest.raises(Exception):
            make_continuity_verdict(confidence=-0.01)

    def test_continuation_low_confidence_raises(self) -> None:
        """`is_continuation=True` with confidence < 0.50 raises."""

        with pytest.raises(ValueError, match="Uncertainty Policy"):
            make_continuity_verdict(
                confidence=0.49,
                continuation_kind=PageContinuationKind.TEXT,
                is_continuation=True,
            )

    def test_continuation_with_kind_none_raises(self) -> None:
        """`is_continuation=True` with `continuation_kind=NONE` raises."""

        with pytest.raises(ValueError, match="must not be 'none'"):
            make_continuity_verdict(
                continuation_kind=PageContinuationKind.NONE, is_continuation=True
            )

    def test_figure_continuation_with_repeats_header_raises(self) -> None:
        """`set_next_table_repeats_header` on a figure continuation raises."""

        with pytest.raises(
            ValueError, match="set_next_table_repeats_header only allowed"
        ):
            make_continuity_verdict(
                confidence=0.80,
                continuation_kind=PageContinuationKind.FIGURE,
                is_continuation=True,
                set_next_table_repeats_header=False,
            )

    def test_non_table_continuation_with_repeats_header_raises(self) -> None:
        """`set_next_table_repeats_header` on a non-table continuation raises."""

        with pytest.raises(
            ValueError, match="set_next_table_repeats_header only allowed"
        ):
            make_continuity_verdict(
                confidence=0.80,
                continuation_kind=PageContinuationKind.TEXT,
                is_continuation=True,
                set_next_table_repeats_header=True,
            )

    def test_not_continuation_with_non_none_kind_raises(self) -> None:
        """`is_continuation=False` with `continuation_kind != NONE` raises."""

        with pytest.raises(ValueError, match="continuation_kind must be 'none'"):
            make_continuity_verdict(
                continuation_kind=PageContinuationKind.TEXT, is_continuation=False
            )

    def test_not_continuation_with_repeats_header_set_raises(self) -> None:
        """`is_continuation=False` with `set_next_table_repeats_header` set raises."""

        with pytest.raises(
            ValueError, match="set_next_table_repeats_header must be null"
        ):
            make_continuity_verdict(
                is_continuation=False, set_next_table_repeats_header=True
            )

    def test_rationale_too_short_raises(self) -> None:
        """A rationale shorter than 50 characters is rejected."""

        with pytest.raises(Exception):
            make_continuity_verdict(rationale="Too short")


class TestPageIRContinuityVerdictValid:
    """Tests for valid `PageIRContinuityVerdict` construction."""

    def test_confidence_at_boundary_050(self) -> None:
        """A continuation with confidence exactly 0.50 is valid."""

        verdict = make_continuity_verdict(
            confidence=0.50,
            continuation_kind=PageContinuationKind.TEXT,
            is_continuation=True,
        )

        assert verdict.confidence == 0.50

    def test_continuation_figure(self) -> None:
        """A figure continuation is valid."""

        verdict = make_continuity_verdict(
            confidence=0.60,
            continuation_kind=PageContinuationKind.FIGURE,
            is_continuation=True,
        )

        assert verdict.continuation_kind == PageContinuationKind.FIGURE

    def test_continuation_table_with_repeats_header_false(self) -> None:
        """A table continuation may set `set_next_table_repeats_header=False`."""

        verdict = make_continuity_verdict(
            confidence=0.90,
            continuation_kind=PageContinuationKind.TABLE,
            is_continuation=True,
            set_next_table_repeats_header=False,
        )

        assert verdict.set_next_table_repeats_header is False

    def test_continuation_table_with_repeats_header_null(self) -> None:
        """A table continuation may leave `set_next_table_repeats_header` null."""

        verdict = make_continuity_verdict(
            confidence=0.90,
            continuation_kind=PageContinuationKind.TABLE,
            is_continuation=True,
            set_next_table_repeats_header=None,
        )

        assert verdict.set_next_table_repeats_header is None

    def test_continuation_table_with_repeats_header_true(self) -> None:
        """A table continuation may set `set_next_table_repeats_header=True`."""

        verdict = make_continuity_verdict(
            confidence=0.90,
            continuation_kind=PageContinuationKind.TABLE,
            is_continuation=True,
            set_next_table_repeats_header=True,
        )

        assert verdict.set_next_table_repeats_header is True

    def test_continuation_text(self) -> None:
        """A text continuation with confidence >= 0.50 is valid."""

        verdict = make_continuity_verdict(
            confidence=0.75,
            continuation_kind=PageContinuationKind.TEXT,
            is_continuation=True,
        )

        assert verdict.is_continuation is True
        assert verdict.continuation_kind == PageContinuationKind.TEXT

    def test_not_continuation_with_kind_none(self) -> None:
        """A non-continuation verdict with kind=NONE is valid."""

        verdict = make_continuity_verdict(
            continuation_kind=PageContinuationKind.NONE, is_continuation=False
        )

        assert verdict.is_continuation is False
        assert verdict.continuation_kind == PageContinuationKind.NONE

    def test_page_indices_populated(self) -> None:
        """Page indices can be populated on a valid verdict."""

        verdict = make_continuity_verdict(
            is_continuation=False, next_page_index=4, prev_page_index=3
        )

        assert verdict.prev_page_index == 3
        assert verdict.next_page_index == 4
