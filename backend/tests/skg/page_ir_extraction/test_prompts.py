"""This is the main module for testing page_ir_extraction/prompts.py.

These tests are *deterministic*: they validate that the prompt templates continue to
encode the non-negotiable contracts the pipeline depends on (e.g., "image is source of
truth", bbox bounds, failure verdict rules), and that optional PDF-layer hints are
appended with the correct wrappers when provided.
"""

# Standard Library
import json

# Third Party Library
import pytest

# Package Library
from skg.page_ir_extraction.prompts import (
    extract_page_ir_from_pdf_page,
    validate_page_ir_extraction,
)
from skg.utils.constants import BlockType, FigureKind
from skg.utils.general import PromptPair


@pytest.fixture(scope="function")
def extract_prompt_pair_base() -> PromptPair:
    """Create a baseline extraction PromptPair with no PDF-derived hints.

    Returns
    -------
    PromptPair
        The PromptPair returned by `extract_page_ir_from_pdf_page`.
    """

    return extract_page_ir_from_pdf_page(
        image_height=800,
        image_width=600,
        languages=["en", "sw"],
        page_index=1,
        table_layer_hint=None,
        text_layer_hint=None,
    )


@pytest.fixture(scope="function")
def validate_prompt_pair_base() -> PromptPair:
    """Create a baseline validation PromptPair.

    Returns
    -------
    PromptPair
        The PromptPair returned by `validate_page_ir_extraction`.
    """

    return validate_page_ir_extraction(
        image_height=800,
        image_width=600,
        page_index=3,
        page_ir_json='{"items": [], "page_index": null}',
    )


def _pair_text(*, field: str, pair: PromptPair) -> str:
    """Access a PromptPair message field (system/user).

    Parameters
    ----------
    field
        The field name ("system_message" or "user_message").
    pair
        The PromptPair instance.

    Returns
    -------
    str
        The message text.

    Raises
    ------
    AssertionError
        If the field cannot be accessed from the pair.
    """

    if hasattr(pair, field):
        return getattr(pair, field)

    if isinstance(pair, dict) and field in pair:
        return str(pair[field])

    raise AssertionError(f"Unsupported PromptPair type; cannot access '{field}'")


def test_extract_page_ir_from_pdf_page_system_message_contains_core_hard_rules(
    extract_prompt_pair_base: object,
) -> None:
    """Guard the non-negotiable extraction contract clauses from accidental edits.

    If any of these clauses regress, model behavior can change materially
    (hallucinations, wrong reading order, missing bottom content, etc.).

    Parameters
    ----------
    extract_prompt_pair_base
        The baseline PromptPair for extraction, without PDF-derived hints.
    """

    system_message = _pair_text(field="system_message", pair=extract_prompt_pair_base)

    # Hard rules: if these disappear, extraction quality can regress catastrophically.
    assert "## HARD RULES" in system_message
    assert "IMAGE IS SOURCE OF TRUTH" in system_message
    assert "READING ORDER" in system_message
    assert "VERBATIM / NO HALLUCINATION" in system_message
    assert "BOTTOM SCAN" in system_message

    # Hint policy: prevents blindly copying PDF text/table layers.
    assert "IMAGE REMAINS AUTHORITATIVE" in system_message
    assert "WHEN HINTS CONFLICT WITH IMAGE" in system_message
    assert "hints only" in system_message.lower()


def test_extract_page_ir_from_pdf_page_system_message_injects_dimensions_languages_and_enums(
    extract_prompt_pair_base: object,
) -> None:
    """Ensure key dynamic context is interpolated into the extraction prompt.

    This verifies that:

    1. Bbox bounds are keyed to the actual rendered image dimensions
    2. Language context is included verbatim
    3. The allowed enums (BlockType/FigureKind) are embedded as machine-readable JSON
    """

    system_message = _pair_text(field="system_message", pair=extract_prompt_pair_base)

    assert "Pixel coordinates (px) relative to 600x800" in system_message
    assert "0 <= x0 < x1 <= 600 and 0 <= y0 < y1 <= 800" in system_message
    assert "Expected languages (hints): en, sw." in system_message

    allowed_block_types = json.dumps([bt.value for bt in BlockType], ensure_ascii=False)
    allowed_figure_kinds = json.dumps(
        [fk.value for fk in FigureKind], ensure_ascii=False
    )

    assert f"Valid block_type values: {allowed_block_types}" in system_message
    assert f"Set figure.figure_kind to one of {allowed_figure_kinds}" in system_message


def test_extract_page_ir_from_pdf_page_user_message_appends_both_hints_in_expected_order() -> (
    None
):
    """When both hints are present, they must be appended in a stable order.

    The order matters for readability and to keep the "spelling" (text layer) guidance
    preceding the "table structure" guidance.
    """

    table_layer_hint = "### Table 0\n  row 0: | a | b |"
    text_layer_hint = "Some PDF text"

    pair = extract_page_ir_from_pdf_page(
        image_height=800,
        image_width=600,
        languages=["en"],
        page_index=0,
        table_layer_hint=table_layer_hint,
        text_layer_hint=text_layer_hint,
    )
    user_message = _pair_text(field="user_message", pair=pair)

    text_idx = user_message.find("## PDF TEXT LAYER REFERENCE")
    table_idx = user_message.find("## PDF TABLE LAYER REFERENCE")

    assert text_idx != -1
    assert table_idx != -1
    assert (
        text_idx < table_idx
    ), "Expected text-layer section to precede table-layer section"

    # Both wrappers must be present and not nested.
    assert f"<text_layer>\n{text_layer_hint}\n</text_layer>" in user_message
    assert f"<table_layer>\n{table_layer_hint}\n</table_layer>" in user_message


def test_extract_page_ir_from_pdf_page_user_message_appends_table_layer_hint_with_wrapper_and_separation() -> (
    None
):
    """Table-layer hint must be appended with the <table_layer> wrapper and separated.

    This ensures the hint is present and labeled as a *structural* reference, while the
    image remains authoritative for boundaries/classification.
    """

    table_layer_hint = "### Table 0\n  row 0: | a | b |"

    pair = extract_page_ir_from_pdf_page(
        image_height=800,
        image_width=600,
        languages=["en"],
        page_index=0,
        table_layer_hint=table_layer_hint,
        text_layer_hint=None,
    )
    user_message = _pair_text(field="user_message", pair=pair)

    assert "Return the PageIR JSON only" in user_message

    # PDF TABLE LAYER REFERENCE" in user_message.
    assert "structural hint for tables" in user_message
    assert "image remains authoritative" in user_message.lower()
    assert f"<table_layer>\n{table_layer_hint}\n</table_layer>" in user_message
    assert "<text_layer>" not in user_message


def test_extract_page_ir_from_pdf_page_user_message_appends_text_layer_hint_with_wrapper_and_separation() -> (
    None
):
    """Text-layer hint must be appended with the <text_layer> wrapper and separated.

    This guards both correct wiring (hint included) and correct framing (spelling help
    only; do not copy reading order/structure).
    """

    text_layer_hint = "Áccented \n text — ɓ ɗ Ƴ ŋ ñ é ü"

    pair = extract_page_ir_from_pdf_page(
        image_height=800,
        image_width=600,
        languages=["en"],
        page_index=0,
        table_layer_hint=None,
        text_layer_hint=text_layer_hint,
    )
    user_message = _pair_text(field="user_message", pair=pair)

    assert "Return the PageIR JSON only" in user_message

    # "PDF TEXT LAYER REFERENCE" in user_message.
    assert "character-accurate" in user_message
    assert "Do NOT copy its structure or reading order" in user_message
    assert (
        f"<text_layer>\n{text_layer_hint}\n</text_layer>" in user_message
    ), f"{user_message = }"
    assert "<table_layer>" not in user_message


def test_extract_page_ir_from_pdf_page_user_message_has_base_instructions_and_no_hint_tags_by_default(
    extract_prompt_pair_base: object,
) -> None:
    """Without hints, user_message should contain only the extraction instructions.

    Parameters
    ----------
    extract_prompt_pair_base
        The baseline PromptPair for extraction, without PDF-derived hints.
    """

    user_message = _pair_text(field="user_message", pair=extract_prompt_pair_base)

    assert "Extract PageIR for the provided image" in user_message
    assert "page_index=1" in user_message
    assert "scan the bottom ~10% of the page" in user_message
    assert user_message.rstrip().endswith("Return the PageIR JSON only.")

    # Ensure we don't accidentally include hint sections when hints are None.
    assert "<text_layer>" not in user_message
    assert "<table_layer>" not in user_message
    assert "PDF TEXT LAYER REFERENCE" not in user_message
    assert "PDF TABLE LAYER REFERENCE" not in user_message


def test_validate_page_ir_extraction_system_message_contains_failure_contract_and_correction_rules(
    validate_prompt_pair_base: object,
) -> None:
    """Validation prompt must enforce a strict failure contract and correction rules.

    Parameters
    ----------
    validate_prompt_pair_base
        The baseline PromptPair for validation, without PDF-derived hints.
    """

    system_message = _pair_text(field="system_message", pair=validate_prompt_pair_base)

    assert "You are a quality assurance agent" in system_message
    assert "rendered at 600x800 pixels" in system_message
    assert "You are NOT re-extracting the page from scratch" in system_message

    # Failure contract (non-negotiable).
    assert "passed=false MUST include" in system_message
    assert "corrected_page_ir" in system_message
    assert "When passed=true, do NOT include corrected_page_ir" in system_message

    # Material-severity guidance and suggested fixes requirements.
    assert "## SEVERITY GUIDE" in system_message
    assert "suggested_fix" in system_message


def test_validate_page_ir_extraction_user_message_includes_page_index_and_json_payload_verbatim(
    validate_prompt_pair_base: object,
) -> None:
    """User message must carry page_index and the exact JSON payload inside a code
    fence.

    Parameters
    ----------
    validate_prompt_pair_base
        The baseline PromptPair for validation, without PDF-derived hints.
    """

    user_message = _pair_text(field="user_message", pair=validate_prompt_pair_base)

    assert "page_index=3" in user_message
    assert "## Extracted PageIR JSON" in user_message
    assert "```json" in user_message
    assert '{"items": [], "page_index": null}' in user_message
    assert user_message.rstrip().endswith("all fixes applied.")
