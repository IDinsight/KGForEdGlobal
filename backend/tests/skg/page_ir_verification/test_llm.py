"""This is the main module for testing page_ir_verification/llm.py."""

# Standard Library
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Package Library
from skg.page_ir_verification.llm import (
    VerificationUsageTracker,
    _run_validation_agent,
    verify_page_ir_pairs,
)
from skg.page_ir_verification.schemas import (
    ContinuityValidationIssue,
    ContinuityValidationVerdict,
    PageIRContinuityVerdict,
)
from skg.utils.constants import PageContinuationKind

_THRESHOLDS: dict[str, float] = {
    "min_confidence_to_patch": 0.70,
    "min_confidence_to_select_positive": 0.60,
    "min_confidence_to_stop_negative_search": 0.80,
}
_VALID_RATIONALE = "x" * 50


def _stub_prev_next_items() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return minimal prev/next item dicts that parse as blocks.

    Returns
    -------
    tuple[dict[str, Any], dict[str, Any]]
        A (prev_item, next_item) pair.
    """

    prev = {
        "bbox": [0, 0, 100, 50],
        "block_type": "paragraph",
        "boundary": "complete",
        "kind": "block",
        "text": {"language": "en", "text": "End of paragraph."},
    }
    nxt = {
        "bbox": [0, 0, 100, 50],
        "block_type": "paragraph",
        "boundary": "complete",
        "kind": "block",
        "text": {"language": "en", "text": "Start of next paragraph."},
    }
    return prev, nxt


def make_passing_validation_verdict() -> ContinuityValidationVerdict:
    """Build a passing `ContinuityValidationVerdict`.

    Returns
    -------
    ContinuityValidationVerdict
        A passing validation verdict with no issues.
    """

    return ContinuityValidationVerdict(
        corrected_verdict=None, issues=[], passed=True, rationale=_VALID_RATIONALE
    )


def make_verdict(
    *,
    confidence: float = 0.85,
    continuation_kind: PageContinuationKind = PageContinuationKind.NONE,
    is_continuation: bool = False,
    set_next_table_repeats_header: bool | None = None,
) -> PageIRContinuityVerdict:
    """Build a `PageIRContinuityVerdict` with sensible defaults.

    Parameters
    ----------
    confidence
        Verification confidence score.
    continuation_kind
        Type of content continuing across the break.
    is_continuation
        Whether content continues across the page break.
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
        rationale=_VALID_RATIONALE,
        set_next_table_repeats_header=set_next_table_repeats_header,
    )


def make_failing_validation_verdict(
    corrected_verdict: PageIRContinuityVerdict | None = None,
) -> ContinuityValidationVerdict:
    """Build a failing `ContinuityValidationVerdict` with a corrected verdict.

    Parameters
    ----------
    corrected_verdict
        The corrected verdict to embed. Defaults to a standard non-continuation verdict.

    Returns
    -------
    ContinuityValidationVerdict
        A failing validation verdict.
    """

    return ContinuityValidationVerdict(
        corrected_verdict=corrected_verdict or make_verdict(confidence=0.90),
        issues=[
            ContinuityValidationIssue(
                description="The continuation decision is incorrect.",
                field_name="is_continuation",
                severity="error",
                suggested_fix="Set is_continuation=false",
            )
        ],
        passed=False,
        rationale=_VALID_RATIONALE,
    )


def make_mock_agent_result(*, output: Any) -> MagicMock:
    """Build a mock pydantic-ai agent result with a usage method.

    Parameters
    ----------
    output
        The output value the result should return.

    Returns
    -------
    MagicMock
        A mock agent result with `output` and `usage()` configured.
    """

    result = MagicMock()
    result.output = output
    result.usage.return_value = MagicMock(
        cache_read_tokens=10,
        cache_write_tokens=20,
        request_tokens=100,
        response_tokens=50,
        requests=1,
        total_tokens=150,
    )

    return result


def make_mock_block_item() -> MagicMock:
    """Build a mock Block item.

    Returns
    -------
    MagicMock
        A mock with kind="block".
    """

    block = MagicMock()
    block.kind = "block"
    return block


class TestRunValidationAgent:
    """Tests for `_run_validation_agent`."""

    @patch("skg.page_ir_verification.llm.create_continuity_validation_agent")
    @patch("skg.page_ir_verification.llm.validate_page_ir_continuity_verdict")
    def test_accumulates_validation_usage(
        self, mock_prompts: MagicMock, mock_create_agent: MagicMock
    ) -> None:
        """Token usage is recorded in the tracker's validation bucket.

        Parameters
        ----------
        mock_prompts
            Mock for the prompt builder function, which returns system and user messages.
        mock_create_agent
            Mock for the agent factory, which returns a mock agent instance.
        """

        mock_prompts.return_value = MagicMock(system_message="sys", user_message="user")
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = make_mock_agent_result(
            output=make_passing_validation_verdict()
        )
        mock_create_agent.return_value = mock_agent

        tracker = VerificationUsageTracker()
        _run_validation_agent(
            min_confidence_to_patch=0.70,
            min_confidence_to_select_positive=0.60,
            min_confidence_to_stop_negative_search=0.80,
            next_item=make_mock_block_item(),
            next_item_excerpt={"kind": "block"},
            next_page_index=1,
            next_png_bytes=b"png-next",
            prev_item=make_mock_block_item(),
            prev_item_excerpt={"kind": "block"},
            prev_page_index=0,
            prev_png_bytes=b"png-prev",
            usage_tracker=tracker,
            verdict=make_verdict(),
        )

        assert tracker.validation.runs >= 1

    @patch("skg.page_ir_verification.llm.create_continuity_validation_agent")
    @patch("skg.page_ir_verification.llm.validate_page_ir_continuity_verdict")
    def test_passes_verdict_json_to_prompt_builder(
        self, mock_prompts: MagicMock, mock_create_agent: MagicMock
    ) -> None:
        """The verdict is serialized via `model_dump_json` and passed to the prompt
        builder.

        Parameters
        ----------
        mock_prompts
            Mock for the prompt builder function, which returns system and user messages.
        mock_create_agent
            Mock for the agent factory, which returns a mock agent instance.
        """

        mock_prompts.return_value = MagicMock(system_message="sys", user_message="user")
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = make_mock_agent_result(
            output=make_passing_validation_verdict()
        )
        mock_create_agent.return_value = mock_agent

        verdict = make_verdict(confidence=0.77)
        tracker = VerificationUsageTracker()

        _run_validation_agent(
            min_confidence_to_patch=0.70,
            min_confidence_to_select_positive=0.60,
            min_confidence_to_stop_negative_search=0.80,
            next_item=make_mock_block_item(),
            next_item_excerpt={"kind": "block"},
            next_page_index=1,
            next_png_bytes=b"png-next",
            prev_item=make_mock_block_item(),
            prev_item_excerpt={"kind": "block"},
            prev_page_index=0,
            prev_png_bytes=b"png-prev",
            usage_tracker=tracker,
            verdict=verdict,
        )

        call_kwargs = mock_prompts.call_args.kwargs
        assert call_kwargs["verdict_json"] == verdict.model_dump_json()

    @patch("skg.page_ir_verification.llm.create_continuity_validation_agent")
    @patch("skg.page_ir_verification.llm.validate_page_ir_continuity_verdict")
    def test_returns_validation_verdict_output(
        self, mock_prompts: MagicMock, mock_create_agent: MagicMock
    ) -> None:
        """Returns the agent result's output directly.

        Parameters
        ----------
        mock_prompts
            Mock for the prompt builder function, which returns system and user messages.
        mock_create_agent
            Mock for the agent factory, which returns a mock agent instance.
        """

        expected_verdict = make_passing_validation_verdict()
        mock_prompts.return_value = MagicMock(system_message="sys", user_message="user")
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = make_mock_agent_result(
            output=expected_verdict
        )
        mock_create_agent.return_value = mock_agent

        tracker = VerificationUsageTracker()
        result = _run_validation_agent(
            min_confidence_to_patch=0.70,
            min_confidence_to_select_positive=0.60,
            min_confidence_to_stop_negative_search=0.80,
            next_item=make_mock_block_item(),
            next_item_excerpt={"kind": "block"},
            next_page_index=1,
            next_png_bytes=b"png-next",
            prev_item=make_mock_block_item(),
            prev_item_excerpt={"kind": "block"},
            prev_page_index=0,
            prev_png_bytes=b"png-prev",
            usage_tracker=tracker,
            verdict=make_verdict(),
        )

        assert result is expected_verdict

    @patch("skg.page_ir_verification.llm.create_continuity_validation_agent")
    @patch("skg.page_ir_verification.llm.validate_page_ir_continuity_verdict")
    def test_user_prompt_includes_both_images(
        self, mock_prompts: MagicMock, mock_create_agent: MagicMock
    ) -> None:
        """The agent receives a user prompt containing both PNG byte payloads.

        Parameters
        ----------
        mock_prompts
            Mock for the prompt builder function, which returns system and user messages.
        mock_create_agent
            Mock for the agent factory, which returns a mock agent instance.
        """

        mock_prompts.return_value = MagicMock(system_message="sys", user_message="user")
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = make_mock_agent_result(
            output=make_passing_validation_verdict()
        )
        mock_create_agent.return_value = mock_agent

        prev_bytes = b"PREV_IMAGE_BYTES"
        next_bytes = b"NEXT_IMAGE_BYTES"
        tracker = VerificationUsageTracker()

        _run_validation_agent(
            min_confidence_to_patch=0.70,
            min_confidence_to_select_positive=0.60,
            min_confidence_to_stop_negative_search=0.80,
            next_item=make_mock_block_item(),
            next_item_excerpt={"kind": "block"},
            next_page_index=1,
            next_png_bytes=next_bytes,
            prev_item=make_mock_block_item(),
            prev_item_excerpt={"kind": "block"},
            prev_page_index=0,
            prev_png_bytes=prev_bytes,
            usage_tracker=tracker,
            verdict=make_verdict(),
        )

        user_prompt = mock_agent.run_sync.call_args[0][0]
        binary_items = [item for item in user_prompt if hasattr(item, "data")]

        assert len(binary_items) == 2
        assert binary_items[0].data == prev_bytes
        assert binary_items[1].data == next_bytes


class TestVerificationUsageTracker:
    """Tests for `VerificationUsageTracker`."""

    def test_init_bucket_names(self) -> None:
        """Buckets are labelled with the correct agent names."""

        tracker = VerificationUsageTracker()

        assert tracker.verification.agent_name == "verification"
        assert tracker.validation.agent_name == "validation"

    def test_init_creates_separate_buckets(self) -> None:
        """Verification and validation buckets are distinct objects."""

        tracker = VerificationUsageTracker()

        assert tracker.verification is not tracker.validation

    def test_to_dict_agents_has_both_buckets(self) -> None:
        """`agents` sub-dict contains both `verification` and `validation`."""

        tracker = VerificationUsageTracker()
        agents = tracker.to_dict()["agents"]

        assert "verification" in agents
        assert "validation" in agents

    def test_to_dict_has_agents_and_totals_keys(self) -> None:
        """`to_dict` returns a dict with `agents` and `totals` top-level keys."""

        tracker = VerificationUsageTracker()
        result = tracker.to_dict()

        assert "agents" in result
        assert "totals" in result

    def test_to_dict_totals_sum_across_buckets(self) -> None:
        """Totals aggregate token counts from both buckets correctly."""

        tracker = VerificationUsageTracker()

        # Simulate usage by directly setting bucket fields.
        tracker.verification.input_tokens = 100
        tracker.verification.output_tokens = 40
        tracker.verification.cache_read_tokens = 5
        tracker.verification.cache_write_tokens = 10
        tracker.verification.requests = 2
        tracker.verification.runs = 1
        tracker.validation.input_tokens = 200
        tracker.validation.output_tokens = 80
        tracker.validation.cache_read_tokens = 15
        tracker.validation.cache_write_tokens = 20
        tracker.validation.requests = 3
        tracker.validation.runs = 1

        totals = tracker.to_dict()["totals"]

        assert totals["input_tokens"] == 300
        assert totals["output_tokens"] == 120
        assert totals["cache_read_tokens"] == 20
        assert totals["cache_write_tokens"] == 30
        assert totals["requests"] == 5
        assert totals["runs"] == 2
        assert totals["total_tokens"] == 420

    def test_to_dict_total_tokens_equals_input_plus_output(self) -> None:
        """`total_tokens` is the sum of all input and output tokens (both buckets)."""

        tracker = VerificationUsageTracker()
        tracker.verification.input_tokens = 50
        tracker.verification.output_tokens = 25
        tracker.validation.input_tokens = 30
        tracker.validation.output_tokens = 15

        totals = tracker.to_dict()["totals"]

        expected = 50 + 25 + 30 + 15
        assert totals["total_tokens"] == expected

    def test_to_dict_totals_zero_when_no_usage(self) -> None:
        """All totals are zero when no usage has been recorded."""

        tracker = VerificationUsageTracker()
        totals = tracker.to_dict()["totals"]

        assert totals["input_tokens"] == 0
        assert totals["output_tokens"] == 0
        assert totals["total_tokens"] == 0
        assert totals["requests"] == 0
        assert totals["runs"] == 0


class TestVerifyPageIrPairs:
    """Tests for `verify_page_ir_pairs`."""

    def _call(
        self,
        *,
        mock_create_verification: MagicMock,
        next_item: dict[str, Any] | None = None,
        prev_item: dict[str, Any] | None = None,
        tmp_path: Path,
        tracker: VerificationUsageTracker | None = None,
        verification_verdict: PageIRContinuityVerdict | None = None,
    ) -> PageIRContinuityVerdict:
        """Call `verify_page_ir_pairs` with standard mocks/defaults.

        Parameters
        ----------
        mock_create_verification
            Mock for `create_continuity_verification_agent`.
        next_item
            Next candidate item dict.
        prev_item
            Previous candidate item dict.
        tmp_path
            Temporary directory for PNG stubs.
        tracker
            Usage tracker.
        verification_verdict
            Verdict the verification agent should return.

        Returns
        -------
        PageIRContinuityVerdict
            The final verdict.
        """

        prev_default, next_default = _stub_prev_next_items()
        prev_item = prev_item or prev_default
        next_item = next_item or next_default

        prev_png = tmp_path / "prev.png"
        next_png = tmp_path / "next.png"
        prev_png.write_bytes(b"fake-prev-png")
        next_png.write_bytes(b"fake-next-png")

        v_verdict = verification_verdict or make_verdict()
        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = make_mock_agent_result(output=v_verdict)
        mock_create_verification.return_value = mock_agent

        return verify_page_ir_pairs(
            min_confidence_to_patch=_THRESHOLDS["min_confidence_to_patch"],
            min_confidence_to_select_positive=_THRESHOLDS[
                "min_confidence_to_select_positive"
            ],
            min_confidence_to_stop_negative_search=_THRESHOLDS[
                "min_confidence_to_stop_negative_search"
            ],
            next_item=next_item,
            next_item_excerpt={"kind": "block"},
            next_page_index=1,
            next_png=next_png,
            prev_item=prev_item,
            prev_item_excerpt={"kind": "block"},
            prev_page_index=0,
            prev_png=prev_png,
            usage_tracker=tracker or VerificationUsageTracker(),
        )

    @patch("skg.page_ir_verification.llm._run_validation_agent")
    @patch("skg.page_ir_verification.llm.create_continuity_verification_agent")
    def test_parses_table_item_when_kind_is_table(
        self,
        mock_create_verification: MagicMock,
        mock_run_validation: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When an item dict has `kind='table'`, it is parsed as a Table.

        Parameters
        ----------
        mock_create_verification
            Mock for the verification agent factory, which returns a mock agent instance.
        mock_run_validation
            Mock for the validation agent runner, which returns a passing verdict.
        tmp_path
            Temporary directory for PNG stubs.
        """

        mock_run_validation.return_value = make_passing_validation_verdict()

        table_item: dict[str, Any] = {
            "bbox": [0, 0, 500, 300],
            "boundary": "complete",
            "header_row_count": 1,
            "kind": "table",
            "rows": [
                {"cells": [{"text": {"language": "en", "text": "Header"}}]},
                {"cells": [{"text": {"language": "en", "text": "Data"}}]},
            ],
        }
        _, next_block = _stub_prev_next_items()

        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = make_mock_agent_result(output=make_verdict())
        mock_create_verification.return_value = mock_agent

        (tmp_path / "prev.png").write_bytes(b"fake-prev-png")
        (tmp_path / "next.png").write_bytes(b"fake-next-png")

        # Should not raise since table is parsed correctly.
        verify_page_ir_pairs(
            min_confidence_to_patch=0.70,
            min_confidence_to_select_positive=0.60,
            min_confidence_to_stop_negative_search=0.80,
            next_item=next_block,
            next_item_excerpt={"kind": "block"},
            next_page_index=1,
            next_png=tmp_path / "next.png",
            prev_item=table_item,
            prev_item_excerpt={"kind": "table"},
            prev_page_index=0,
            prev_png=tmp_path / "prev.png",
            usage_tracker=VerificationUsageTracker(),
        )

        # The agent factory received a Table instance for prev_item.
        create_kwargs = mock_create_verification.call_args.kwargs
        assert create_kwargs["prev_item"].kind == "table"

    @patch("skg.page_ir_verification.llm._run_validation_agent")
    @patch("skg.page_ir_verification.llm.create_continuity_verification_agent")
    def test_reads_png_files_from_disk(
        self,
        mock_create_verification: MagicMock,
        mock_run_validation: MagicMock,
        tmp_path: Path,
    ) -> None:
        """PNG bytes passed to validation agent match what was written to disk.

        Parameters
        ----------
        mock_create_verification
            Mock for the verification agent factory, which returns a mock agent instance.
        mock_run_validation
            Mock for the validation agent runner, which returns a passing verdict.
        tmp_path
            Temporary directory for PNG stubs.
        """

        mock_run_validation.return_value = make_passing_validation_verdict()

        prev_png = tmp_path / "prev.png"
        next_png = tmp_path / "next.png"
        prev_png.write_bytes(b"PREV_CONTENT")
        next_png.write_bytes(b"NEXT_CONTENT")

        prev_item, next_item = _stub_prev_next_items()

        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = make_mock_agent_result(output=make_verdict())
        mock_create_verification.return_value = mock_agent

        verify_page_ir_pairs(
            min_confidence_to_patch=0.70,
            min_confidence_to_select_positive=0.60,
            min_confidence_to_stop_negative_search=0.80,
            next_item=next_item,
            next_item_excerpt={"kind": "block"},
            next_page_index=1,
            next_png=next_png,
            prev_item=prev_item,
            prev_item_excerpt={"kind": "block"},
            prev_page_index=0,
            prev_png=prev_png,
            usage_tracker=VerificationUsageTracker(),
        )

        call_kwargs = mock_run_validation.call_args.kwargs
        assert call_kwargs["prev_png_bytes"] == b"PREV_CONTENT"
        assert call_kwargs["next_png_bytes"] == b"NEXT_CONTENT"

    @patch("skg.page_ir_verification.llm._run_validation_agent")
    @patch("skg.page_ir_verification.llm.create_continuity_verification_agent")
    def test_returns_corrected_verdict_when_validation_fails(
        self,
        mock_create_verification: MagicMock,
        mock_run_validation: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When the validation agent fails, the corrected verdict is returned.

        Parameters
        ----------
        mock_create_verification
            Mock for the verification agent factory, which returns a mock agent instance.
        mock_run_validation
            Mock for the validation agent runner, which returns a failing verdict with
            a corrected verdict.
        tmp_path
            Temporary directory for PNG stubs.
        """

        corrected = make_verdict(confidence=0.92)
        mock_run_validation.return_value = make_failing_validation_verdict(
            corrected_verdict=corrected
        )

        result = self._call(
            mock_create_verification=mock_create_verification, tmp_path=tmp_path
        )

        assert result is corrected

    @patch("skg.page_ir_verification.llm._run_validation_agent")
    @patch("skg.page_ir_verification.llm.create_continuity_verification_agent")
    def test_returns_original_verdict_when_validation_passes(
        self,
        mock_create_verification: MagicMock,
        mock_run_validation: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When the validation agent passes, the original verification verdict is
        returned.

        Parameters
        ----------
        mock_create_verification
            Mock for the verification agent factory, which returns a mock agent instance.
        mock_run_validation
            Mock for the validation agent runner, which returns a passing verdict.
        tmp_path
            Temporary directory for PNG stubs.
        """

        original = make_verdict(confidence=0.85)
        mock_run_validation.return_value = make_passing_validation_verdict()

        result = self._call(
            mock_create_verification=mock_create_verification,
            tmp_path=tmp_path,
            verification_verdict=original,
        )

        assert result is original

    @patch("skg.page_ir_verification.llm._run_validation_agent")
    @patch("skg.page_ir_verification.llm.create_continuity_verification_agent")
    def test_validation_agent_receives_verification_verdict(
        self,
        mock_create_verification: MagicMock,
        mock_run_validation: MagicMock,
        tmp_path: Path,
    ) -> None:
        """The validation agent is called with the verification agent's verdict.

        Parameters
        ----------
        mock_create_verification
            Mock for the verification agent factory, which returns a mock agent instance.
        mock_run_validation
            Mock for the validation agent runner, which returns a passing verdict.
        tmp_path
            Temporary directory for PNG stubs.
        """

        original = make_verdict(confidence=0.75)
        mock_run_validation.return_value = make_passing_validation_verdict()

        self._call(
            mock_create_verification=mock_create_verification,
            tmp_path=tmp_path,
            verification_verdict=original,
        )

        call_kwargs = mock_run_validation.call_args.kwargs
        assert call_kwargs["verdict"] is original

    @patch("skg.page_ir_verification.llm._run_validation_agent")
    @patch("skg.page_ir_verification.llm.create_continuity_verification_agent")
    def test_verification_usage_tracked(
        self,
        mock_create_verification: MagicMock,
        mock_run_validation: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verification agent usage is recorded in the tracker.

        Parameters
        ----------
        mock_create_verification
            Mock for the verification agent factory, which returns a mock agent instance.
        mock_run_validation
            Mock for the validation agent runner, which returns a passing verdict.
        tmp_path
            Temporary directory for PNG stubs.
        """

        mock_run_validation.return_value = make_passing_validation_verdict()
        tracker = VerificationUsageTracker()

        self._call(
            mock_create_verification=mock_create_verification,
            tmp_path=tmp_path,
            tracker=tracker,
        )

        assert tracker.verification.runs >= 1
