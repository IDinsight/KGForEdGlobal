"""This is the main module for testing page_ir_verification/prompts.py."""

# Standard Library
import json

from typing import Any

# Package Library
from skg.page_ir_verification.prompts import (
    validate_page_ir_continuity_verdict,
    verify_page_ir_pairs_from_extraction,
)
from skg.utils.constants import PageContinuationKind

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_confidence_to_patch": 0.70,
    "min_confidence_to_select_positive": 0.60,
    "min_confidence_to_stop_negative_search": 0.80,
}


def _call_validate(
    *,
    min_confidence_to_patch: float = _DEFAULT_THRESHOLDS["min_confidence_to_patch"],
    min_confidence_to_select_positive: float = _DEFAULT_THRESHOLDS[
        "min_confidence_to_select_positive"
    ],
    min_confidence_to_stop_negative_search: float = _DEFAULT_THRESHOLDS[
        "min_confidence_to_stop_negative_search"
    ],
    next_item_excerpt: dict[str, Any] | None = None,
    next_page_index: int = 1,
    prev_item_excerpt: dict[str, Any] | None = None,
    prev_page_index: int = 0,
    verdict_json: str | None = None,
) -> Any:
    """Call `validate_page_ir_continuity_verdict` with defaults.

    Parameters
    ----------
    min_confidence_to_patch
        Patch threshold.
    min_confidence_to_select_positive
        Positive selection threshold.
    min_confidence_to_stop_negative_search
        Negative search-stop threshold.
    next_item_excerpt
        Candidate excerpt for top of next page.
    next_page_index
        0-based next page index.
    prev_item_excerpt
        Candidate excerpt for bottom of previous page.
    prev_page_index
        0-based previous page index.
    verdict_json
        JSON string of the verdict to validate.

    Returns
    -------
    PromptPair
        The generated prompt pair.
    """

    if verdict_json is None:
        verdict_json = json.dumps(
            {
                "confidence": 0.85,
                "continuation_kind": "none",
                "is_continuation": False,
                "rationale": "x" * 50,
                "set_next_table_repeats_header": None,
            }
        )

    return validate_page_ir_continuity_verdict(
        min_confidence_to_patch=min_confidence_to_patch,
        min_confidence_to_select_positive=min_confidence_to_select_positive,
        min_confidence_to_stop_negative_search=min_confidence_to_stop_negative_search,
        next_item_excerpt=next_item_excerpt or make_item_excerpt(),
        next_page_index=next_page_index,
        prev_item_excerpt=prev_item_excerpt or make_item_excerpt(),
        prev_page_index=prev_page_index,
        verdict_json=verdict_json,
    )


def _call_verify(
    *,
    min_confidence_to_patch: float = _DEFAULT_THRESHOLDS["min_confidence_to_patch"],
    min_confidence_to_select_positive: float = _DEFAULT_THRESHOLDS[
        "min_confidence_to_select_positive"
    ],
    min_confidence_to_stop_negative_search: float = _DEFAULT_THRESHOLDS[
        "min_confidence_to_stop_negative_search"
    ],
    next_item: dict[str, Any] | None = None,
    next_page_index: int = 1,
    prev_item: dict[str, Any] | None = None,
    prev_page_index: int = 0,
) -> Any:
    """Call `verify_page_ir_pairs_from_extraction` with defaults.

    Parameters
    ----------
    min_confidence_to_patch
        Patch threshold.
    min_confidence_to_select_positive
        Positive selection threshold.
    min_confidence_to_stop_negative_search
        Negative search-stop threshold.
    next_item
        Candidate item near top of next page.
    next_page_index
        0-based next page index.
    prev_item
        Candidate item near bottom of previous page.
    prev_page_index
        0-based previous page index.

    Returns
    -------
    PromptPair
        The generated prompt pair.
    """

    return verify_page_ir_pairs_from_extraction(
        min_confidence_to_patch=min_confidence_to_patch,
        min_confidence_to_select_positive=min_confidence_to_select_positive,
        min_confidence_to_stop_negative_search=min_confidence_to_stop_negative_search,
        next_item=next_item or make_item_excerpt(),
        next_page_index=next_page_index,
        prev_item=prev_item or make_item_excerpt(),
        prev_page_index=prev_page_index,
    )


def make_item_excerpt(
    *, kind: str = "block", text: str = "Sample text"
) -> dict[str, Any]:
    """Build a minimal candidate item excerpt dict.

    Parameters
    ----------
    kind
        The item kind ("block" or "table").
    text
        Text content for the excerpt.

    Returns
    -------
    dict[str, Any]
        A minimal excerpt dictionary.
    """

    return {"kind": kind, "text": text}


class TestValidatePageIrContinuityVerdict:
    """Tests for `validate_page_ir_continuity_verdict`."""

    def test_system_message_contains_all_continuation_kinds(self) -> None:
        """System message references every `PageContinuationKind` value."""

        result = _call_validate()

        for kind in PageContinuationKind:
            assert kind.value in result.system_message

    def test_system_message_contains_formatted_thresholds(self) -> None:
        """System message renders thresholds as 2-decimal-place strings."""

        result = _call_validate(
            min_confidence_to_patch=0.7,
            min_confidence_to_select_positive=0.6,
            min_confidence_to_stop_negative_search=0.8,
        )

        assert "0.70" in result.system_message
        assert "0.60" in result.system_message
        assert "0.80" in result.system_message

    def test_system_message_describes_checker_mode(self) -> None:
        """System message identifies the agent as operating in CHECKER MODE."""

        result = _call_validate()

        assert "CHECKER MODE" in result.system_message

    def test_system_message_mentions_severity_guide(self) -> None:
        """System message includes the error/warning severity guide."""

        result = _call_validate()

        assert "error" in result.system_message.lower()
        assert "warning" in result.system_message.lower()

    def test_system_message_references_corrected_verdict_requirement(self) -> None:
        """System message instructs to provide corrected_verdict when passed=false."""

        result = _call_validate()

        assert "corrected_verdict" in result.system_message
        assert "passed=false" in result.system_message

    def test_user_message_embeds_candidate_excerpts_verbatim(self) -> None:
        """Candidate excerpt dicts appear in the user message without transformation."""

        prev = {"kind": "table", "note": "fin de tableau"}
        nxt = {"kind": "block", "text": "Début"}

        result = _call_validate(next_item_excerpt=nxt, prev_item_excerpt=prev)
        parsed = json.loads(result.user_message)

        assert parsed["prev_candidate_item"] == prev
        assert parsed["next_candidate_item"] == nxt

    def test_user_message_contains_page_indices(self) -> None:
        """User message embeds the provided page indices."""

        result = _call_validate(next_page_index=10, prev_page_index=9)
        parsed = json.loads(result.user_message)

        assert parsed["prev_page_index"] == 9
        assert parsed["next_page_index"] == 10

    def test_user_message_contains_thresholds(self) -> None:
        """User message embeds all three threshold values."""

        result = _call_validate(
            min_confidence_to_patch=0.75,
            min_confidence_to_select_positive=0.65,
            min_confidence_to_stop_negative_search=0.85,
        )
        parsed = json.loads(result.user_message)
        thresholds = parsed["thresholds"]

        assert thresholds["min_confidence_to_patch"] == 0.75
        assert thresholds["min_confidence_to_select_positive"] == 0.65
        assert thresholds["min_confidence_to_stop_negative_search"] == 0.85

    def test_user_message_embeds_verdict_as_parsed_object(self) -> None:
        """The verdict_json string is parsed and embedded as a JSON object, not a string."""

        verdict = json.dumps(
            {
                "confidence": 0.90,
                "continuation_kind": "table",
                "is_continuation": True,
                "rationale": "x" * 50,
                "set_next_table_repeats_header": True,
            }
        )
        result = _call_validate(verdict_json=verdict)
        parsed = json.loads(result.user_message)

        assert isinstance(parsed["verification_verdict"], dict)
        assert parsed["verification_verdict"]["confidence"] == 0.90
        assert parsed["verification_verdict"]["is_continuation"] is True

    def test_user_message_is_valid_compact_json(self) -> None:
        """User message is valid JSON with compact separators."""

        result = _call_validate()
        parsed = json.loads(result.user_message)

        assert isinstance(parsed, dict)
        assert ": " not in result.user_message
        assert ", " not in result.user_message

    def test_user_message_preserves_unicode(self) -> None:
        """Non-ASCII characters are preserved in the user message."""

        item = make_item_excerpt(text="Évaluation des acquis — palier 2")
        result = _call_validate(prev_item_excerpt=item)

        assert "Évaluation" in result.user_message
        assert "\\u" not in result.user_message

    def test_verify_vs_validate_system_messages_differ(self) -> None:
        """The two functions produce different system messages (different agent roles)."""

        verify_result = _call_verify()
        validate_result = _call_validate()

        assert verify_result.system_message != validate_result.system_message

    def test_verify_vs_validate_user_message_keys_differ(self) -> None:
        """validate includes `verification_verdict` key; verify does not."""

        verify_parsed = json.loads(_call_verify().user_message)
        validate_parsed = json.loads(_call_validate().user_message)

        assert "verification_verdict" not in verify_parsed
        assert "verification_verdict" in validate_parsed


class TestVerifyPageIrPairsFromExtraction:
    """Tests for `verify_page_ir_pairs_from_extraction`."""

    def test_system_message_contains_all_continuation_kinds(self) -> None:
        """System message references every `PageContinuationKind` value."""

        result = _call_verify()

        for kind in PageContinuationKind:
            assert kind.value in result.system_message

    def test_system_message_contains_formatted_thresholds(self) -> None:
        """System message renders thresholds as 2-decimal-place strings."""

        result = _call_verify(
            min_confidence_to_patch=0.7,
            min_confidence_to_select_positive=0.6,
            min_confidence_to_stop_negative_search=0.8,
        )

        assert "0.70" in result.system_message
        assert "0.60" in result.system_message
        assert "0.80" in result.system_message

    def test_system_message_mentions_uncertainty_policy(self) -> None:
        """System message includes the uncertainty policy rule
        (confidence <= 0.49 -> false).
        """

        result = _call_verify()

        assert "0.49" in result.system_message
        assert "is_continuation=false" in result.system_message

    def test_system_message_not_empty_and_stripped(self) -> None:
        """System message is non-empty and has no leading/trailing whitespace."""

        result = _call_verify()

        assert len(result.system_message) > 0
        assert result.system_message == result.system_message.strip()

    def test_user_message_contains_page_indices(self) -> None:
        """User message embeds the provided page indices."""

        result = _call_verify(next_page_index=5, prev_page_index=4)
        parsed = json.loads(result.user_message)

        assert parsed["prev_page_index"] == 4
        assert parsed["next_page_index"] == 5

    def test_user_message_contains_thresholds(self) -> None:
        """User message embeds all three threshold values."""

        result = _call_verify(
            min_confidence_to_patch=0.72,
            min_confidence_to_select_positive=0.63,
            min_confidence_to_stop_negative_search=0.88,
        )
        parsed = json.loads(result.user_message)
        thresholds = parsed["thresholds"]

        assert thresholds["min_confidence_to_patch"] == 0.72
        assert thresholds["min_confidence_to_select_positive"] == 0.63
        assert thresholds["min_confidence_to_stop_negative_search"] == 0.88

    def test_user_message_embeds_candidate_items_verbatim(self) -> None:
        """Candidate item dicts appear in the user message without transformation."""

        prev = {"kind": "table", "rows": [["a", "b"]], "special_chars": "é à ü"}
        nxt = {"kind": "block", "text": "Début du paragraphe"}

        result = _call_verify(next_item=nxt, prev_item=prev)
        parsed = json.loads(result.user_message)

        assert parsed["prev_candidate_item"] == prev
        assert parsed["next_candidate_item"] == nxt

    def test_user_message_is_valid_compact_json(self) -> None:
        """User message is valid JSON with no whitespace padding (compact separators)."""

        result = _call_verify()
        parsed = json.loads(result.user_message)

        assert isinstance(parsed, dict)

        # Compact separators -> no space after colon or comma.
        assert ": " not in result.user_message
        assert ", " not in result.user_message

    def test_user_message_not_empty_and_stripped(self) -> None:
        """User message is non-empty and has no leading/trailing whitespace."""

        result = _call_verify()

        assert len(result.user_message) > 0
        assert result.user_message == result.user_message.strip()

    def test_user_message_preserves_unicode(self) -> None:
        """Non-ASCII characters in items are preserved (ensure_ascii=False)."""

        item = make_item_excerpt(text="Compétences générales — étape 3")
        result = _call_verify(prev_item=item)

        assert "Compétences" in result.user_message
        assert "\\u" not in result.user_message
