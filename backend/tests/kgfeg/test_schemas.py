"""This is the main module for testing schemas.py."""

# Standard Library
from pathlib import Path
from typing import Any

# Third Party Library
import pytest

from pydantic import TypeAdapter, ValidationError

# Package Library
from kgfeg.schemas import (
    BBox,
    ExtractionConfig,
    VerificationConfig,
    _BCP47Str,
    _validate_bcp47,
    validate_bbox_order,
)
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


# The optional per-agent instruction fields on each page IR stage config.
_STAGE_INSTRUCTION_FIELDS: dict[str, tuple[str, str]] = {
    "extraction": ("extraction_instructions", "validation_instructions"),
    "verification": ("verification_instructions", "validation_instructions"),
}


def _build_stage_config(
    *, stage: str, tmp_path: Path, **overrides: Any
) -> ExtractionConfig | VerificationConfig:
    """Validate a minimal page IR stage config from raw (JSON-like) data.

    Parameters
    ----------
    stage
        "extraction" or "verification".
    tmp_path
        Temporary directory used as the extraction output directory.
    **overrides
        Extra raw keys to include in the config data.

    Returns
    -------
    ExtractionConfig | VerificationConfig
        The validated stage config.
    """

    if stage == "extraction":
        # `pdf_fp` only has to exist; its content is never read here.
        pdf_fp = tmp_path / "document.pdf"
        pdf_fp.write_bytes(b"%PDF-1.4\n")

        return ExtractionConfig.model_validate(
            {
                "country": "Testland",
                "languages": ["en"],
                "output_dir": str(tmp_path / "out"),
                "pdf_fp": str(pdf_fp),
                **overrides,
            }
        )

    return VerificationConfig.model_validate(overrides)


@PARAM(argnames="stage", argvalues=["extraction", "verification"])
def test_stage_config_instruction_fields_default_to_none_when_omitted(
    *, stage: str, tmp_path: Path
) -> None:
    """A stage config that omits both instruction fields still loads, with both unset
    (AC-001, AC-004).

    Parameters
    ----------
    stage
        Which page IR stage config to build.
    tmp_path
        Temporary directory for the extraction output directory.
    """

    config = _build_stage_config(stage=stage, tmp_path=tmp_path)

    for field in _STAGE_INSTRUCTION_FIELDS[stage]:
        assert getattr(config, field) is None


@PARAM(argnames="stage", argvalues=["extraction", "verification"])
def test_stage_config_instruction_fields_are_stripped_and_blank_becomes_none(
    *, stage: str, tmp_path: Path
) -> None:
    """Each instruction field keeps its own stripped text, and a blank or
    whitespace-only value becomes None so it can never add an empty prompt section.

    Parameters
    ----------
    stage
        Which page IR stage config to build.
    tmp_path
        Temporary directory for the extraction output directory.
    """

    first, second = _STAGE_INSTRUCTION_FIELDS[stage]

    config = _build_stage_config(
        stage=stage, tmp_path=tmp_path, **{first: "  rule one \n", second: "rule two"}
    )
    assert getattr(config, first) == "rule one"
    assert getattr(config, second) == "rule two"

    for blank in ("", "   ", "\n\t"):
        config = _build_stage_config(
            stage=stage, tmp_path=tmp_path, **{first: blank, second: blank}
        )
        assert getattr(config, first) is None
        assert getattr(config, second) is None


def test_stage_configs_still_reject_unknown_keys(tmp_path: Path) -> None:
    """Both page IR stage configs still reject unknown keys (`extra="forbid"`) after
    the new optional fields were added. One behavior, checked on both configs.

    Parameters
    ----------
    tmp_path
        Temporary directory for the extraction output directory.
    """

    for stage in _STAGE_INSTRUCTION_FIELDS:
        with pytest.raises(ValidationError, match="curriculum_instructions"):
            _build_stage_config(
                stage=stage, tmp_path=tmp_path, curriculum_instructions="x"
            )
