"""This is the main module for testing schemas.py."""

# Third Party Library
import pytest

from pydantic import TypeAdapter, ValidationError

# Package Library
from kgfeg.schemas import BBox, _BCP47Str, _validate_bcp47, validate_bbox_order
from tests.constants import PARAM


@PARAM(
    argnames=("code", "expected"),
    argvalues=[
        ("en_us", "en-US"),
        (" en-US ", "en-US"),
        ("zh_hans_cn", "zh-Hans-CN"),
        ("mul", "mul"),
        (" und ", "und"),
        ("", "und"),
    ],
)
def test__validate_bcp47_normalizes_and_standardizes(
    *, code: str, expected: str
) -> None:
    """`validate_bcp47` normalizes underscore/whitespace and standardizes tag casing.

    Parameters
    ----------
    code
        The BCP-47 language tag to validate, which may have non-standard formatting.
    expected
        The expected output tag after validation, which is the canonicalized form of
        the input tag if valid, or "und" if the input is empty or only whitespace.
    """

    assert _validate_bcp47(code=code) == expected


def test__validate_bcp47_raises_on_parseable_but_invalid_tags() -> None:
    """Parseable-but-invalid tags raise `ValueError` (langcodes `is_valid()` is False)."""

    with pytest.raises(
        expected_exception=ValueError, match=r"Invalid BCP-47 language tag"
    ):
        _ = _validate_bcp47(code="en-US-foobar")


@PARAM(
    argnames=("code", "match"),
    argvalues=[
        ("   ", r"Unparseable language tag"),
        ("en-!!", r"Unparseable language tag"),
    ],
)
def test__validate_bcp47_raises_on_unparseable_tags(*, code: str, match: str) -> None:
    """Unparseable tags raise a `ValueError` with a stable, descriptive message.

    Parameters
    ----------
    code
        The BCP-47 language tag to validate, which is unparseable and should trigger a
        `ValueError`.
    match
        A regex pattern that should match the error message of the raised `ValueError`.
    """

    with pytest.raises(expected_exception=ValueError, match=match):
        _ = _validate_bcp47(code=code)


def test__validate_bcp47_type_annotated_validator_runs_in_pydantic() -> None:
    """`BCP47Str` (Annotated + AfterValidator) applies `validate_bcp47` in Pydantic
    models.
    """

    ta = TypeAdapter(_BCP47Str)
    assert ta.validate_python("en_us") == "en-US"

    with pytest.raises(ValidationError):
        ta.validate_python("en-!!")


@PARAM(
    argnames=("bbox", "expected"),
    argvalues=[
        # Already well-ordered.
        ([0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 2.0, 3.0]),
        # Inverted X axis swaps.
        ([5.0, 1.0, 2.0, 3.0], [2.0, 1.0, 5.0, 3.0]),
        # Inverted Y axis swaps.
        ([0.0, 9.0, 2.0, 3.0], [0.0, 3.0, 2.0, 9.0]),
        # Degenerate X axis expands by 1px.
        ([4.0, 1.0, 4.0, 3.0], [4.0, 1.0, 5.0, 3.0]),
        # Degenerate Y axis expands by 1px.
        ([0.0, 7.0, 2.0, 7.0], [0.0, 7.0, 2.0, 8.0]),
        # Degenerate both axes expands both.
        ([0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 1.0]),
    ],
)
def test_validate_bbox_order_autocorrects_and_does_not_mutate_input(
    *, bbox: list[float], expected: list[float]
) -> None:
    """`validate_bbox_order` auto-corrects inverted/degenerate bboxes without mutating
    input.

    Parameters
    ----------
    bbox
        The bounding box to validate, which must be a list of 4 floats.
    expected
        The expected output bbox after validation, which must be a list of 4 floats.
    """

    bbox_before = list(bbox)
    result = validate_bbox_order(bbox=bbox)
    assert result == expected
    assert bbox == bbox_before


def test_validate_bbox_order_bbox_type_annotated_validator_runs_in_pydantic() -> None:
    """`BBox` (Annotated + AfterValidator) applies `validate_bbox_order` in Pydantic
    models.
    """

    ta = TypeAdapter(BBox)
    assert ta.validate_python([5.0, 0.0, 1.0, 2.0]) == [1.0, 0.0, 5.0, 2.0]

    with pytest.raises(ValidationError):
        ta.validate_python([0.0, 1.0, 2.0])


@PARAM(
    argnames=("bbox",),
    argvalues=[
        ([],),
        ([1.0],),
        ([1.0, 2.0, 3.0],),
        ([1.0, 2.0, 3.0, 4.0, 5.0],),
    ],
)
def test_validate_bbox_order_raises_on_wrong_length(*, bbox: list[float]) -> None:
    """`validate_bbox_order` rejects bboxes not of length 4.

    Parameters
    ----------
    bbox
        The bounding box to validate, which must be a list of 4 floats.
    """

    with pytest.raises(
        expected_exception=ValueError,
        match=r"Bounding box must have exactly 4 numbers",
    ):
        _ = validate_bbox_order(bbox=bbox)
