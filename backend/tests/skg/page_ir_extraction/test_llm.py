"""This is the main module for testing page_ir_extraction/llm.py."""

# pylint: disable=W0613
# Standard Library
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence

# Third Party Library
import pytest

from pydantic_ai import BinaryContent

# Package Library
from kgfeg.model_registry import ModelConfig
from kgfeg.page_ir_extraction import llm as llm_module
from kgfeg.page_ir_extraction.llm import ExtractionUsageTracker
from kgfeg.page_ir_extraction.schemas import (
    Block,
    ExtractionValidationIssue,
    ExtractionValidationVerdict,
    PageIR,
    TextUnit,
)
from kgfeg.utils.constants import BlockType, ItemBoundary


@pytest.fixture(scope="function")
def fixture_page_ir_minimal() -> PageIR:
    """Provide a minimal, schema-valid `PageIR`.

    Returns
    -------
    PageIR
        A PageIR with a single heading block.
    """

    return PageIR(
        items=[
            _make_block(
                bbox=(10.0, 10.0, 100.0, 40.0),
                block_type=BlockType.HEADING,
                boundary=ItemBoundary.COMPLETE,
                text="Heading",
            )
        ]
    )


@pytest.fixture(scope="function")
def fixture_usage_tracker() -> ExtractionUsageTracker:
    """Provide a fresh usage tracker per test.

    Returns
    -------
    ExtractionUsageTracker
        A zeroed usage tracker.
    """

    return ExtractionUsageTracker()


@dataclass(frozen=True)
class _StubUsage:
    """Minimal stand-in for `pydantic_ai.result.RunUsage`.

    Attributes
    ----------
    cache_read_tokens
        Cached input tokens read.
    cache_write_tokens
        Cached input tokens written.
    input_tokens
        Prompt tokens.
    output_tokens
        Completion tokens.
    requests
        Number of API requests (including retries).
    """

    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0


@dataclass(frozen=True)
class _StubRunResult:
    """Minimal stand-in for `pydantic_ai.result.RunResult`.

    Attributes
    ----------
    output
        Parsed structured output.
    usage_obj
        Usage counters for a single run.
    """

    output: Any
    usage_obj: _StubUsage

    def usage(self) -> _StubUsage:
        """Return the usage payload.

        Returns
        -------
        _StubUsage
            Usage counters for the run.
        """

        return self.usage_obj


class _StubAgent:
    """Minimal stand-in for `pydantic_ai.Agent` used by `llm.py`."""

    def __init__(self, result: _StubRunResult) -> None:
        """Initialize the stub agent.

        Parameters
        ----------
        result
            The canned result returned by `run_sync`.
        """

        self._result = result
        self.run_sync_calls: list[Sequence[Any]] = []

    def run_sync(self, user_prompt: Sequence[Any]) -> _StubRunResult:
        """Record the prompt and return the canned result.

        Parameters
        ----------
        user_prompt
            The prompt payload passed to the agent.

        Returns
        -------
        _StubRunResult
            The canned run result.
        """

        self.run_sync_calls.append(user_prompt)

        return self._result


def _install_stub_extraction_agent(
    *, agent: _StubAgent, monkeypatch: pytest.MonkeyPatch, raw_page_irs_dir: Path
) -> None:
    """Patch the extraction agent factory to return a stub agent.

    Parameters
    ----------
    agent
        Stub agent to return from the factory.
    monkeypatch
        Pytest monkeypatch fixture.
    raw_page_irs_dir
        Expected raw artifact directory forwarded to the factory.
    """

    expected_raw_dir = raw_page_irs_dir

    def fake_create_page_ir_extraction_agent(
        *,
        image_height: int,
        image_width: int,
        instructions: str,
        model_config: ModelConfig | None,
        page_index: int,
        raw_page_irs_dir: Path,
        verify_quality_fn: Callable[..., Any],
    ) -> _StubAgent:
        """Return the provided stub agent.

        Parameters
        ----------
        image_height
            Image height in pixels.
        image_width
            Image width in pixels.
        instructions
            System instructions.
        model_config
            The ModelConfig containing the model identifier and any relevant settings.
        page_index
            0-based page index.
        raw_page_irs_dir
            Raw artifact directory.
        verify_quality_fn
            Quality verification callback.

        Returns
        -------
        _StubAgent
            The stub agent.
        """

        assert raw_page_irs_dir == expected_raw_dir
        assert verify_quality_fn is llm_module.verify_page_ir_extraction_quality
        assert isinstance(instructions, str)
        return agent

    monkeypatch.setattr(
        llm_module,
        "create_page_ir_extraction_agent",
        fake_create_page_ir_extraction_agent,
    )


def _install_stub_validation_agent(
    *, agent: _StubAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch the validation agent factory to return a stub agent.

    Parameters
    ----------
    agent
        Stub agent to return from the factory.
    monkeypatch
        Pytest monkeypatch fixture.
    """

    def fake_create_page_ir_validation_agent(
        *,
        image_height: int,
        image_width: int,
        instructions: str,
        model_config: ModelConfig,
        page_index: int,
        verify_quality_fn: Callable[..., Any],
    ) -> _StubAgent:
        """Return the provided stub agent.

        Parameters
        ----------
        image_height
            Image height in pixels.
        image_width
            Image width in pixels.
        instructions
            System instructions.
        model_config
            The ModelConfig containing the model identifier and any relevant settings.
        page_index
            0-based page index.
        verify_quality_fn
            Quality verification callback.

        Returns
        -------
        _StubAgent
            The stub agent.
        """

        assert verify_quality_fn is llm_module.verify_page_ir_extraction_quality
        assert isinstance(instructions, str)
        return agent

    monkeypatch.setattr(
        llm_module,
        "create_page_ir_validation_agent",
        fake_create_page_ir_validation_agent,
    )


def _make_attempt_and_ctx_validator(
    *, attempt_expected: int, calls: list[str], name: str, seen_ctx: list[Any]
) -> Callable[..., None]:
    """Create a validator stub that accepts `attempt` and `ctx`.

    Parameters
    ----------
    attempt_expected
        Expected attempt number.
    calls
        List to append call names to.
    name
        Validator name to record.
    seen_ctx
        A single-element list used to capture the first `ctx` object.

    Returns
    -------
    Callable[..., None]
        A callable matching the validator signature `(*, attempt, ctx) -> None`.
    """

    def validator(*, attempt: int, ctx: Any) -> None:
        """Record a call for an `(attempt, ctx)` validator.

        Parameters
        ----------
        attempt
            Attempt number.
        ctx
            The shared quality context.
        """

        assert attempt == attempt_expected

        calls.append(name)

        if not seen_ctx:
            seen_ctx.append(ctx)

    return validator


def _make_block(
    *,
    bbox: tuple[float, float, float, float],
    block_type: BlockType,
    boundary: ItemBoundary = ItemBoundary.COMPLETE,
    text: str = "Hello",
) -> Block:
    """Create a schema-valid `Block` for tests.

    Parameters
    ----------
    bbox
        Pixel bounding box (x0, y0, x1, y1).
    block_type
        Block classification.
    boundary
        Semantic continuity boundary.
    text
        Text payload for non-list, non-figure blocks.

    Returns
    -------
    Block
        A valid block instance.
    """

    text_unit = TextUnit(language="en", text=text, text_en=None)

    return Block(
        bbox=bbox,
        block_type=block_type,
        boundary=boundary,
        figure=None,
        kind="block",
        list_items=None,
        local_code=None,
        text=text_unit,
    )


def _make_ctx_only_validator(
    *, calls: list[str], name: str, seen_ctx: list[Any]
) -> Callable[[Any], None]:
    """Create a validator stub that accepts only `ctx`.

    Parameters
    ----------
    calls
        List to append call names to.
    name
        Validator name to record.
    seen_ctx
        A single-element list used to capture the first `ctx` object.

    Returns
    -------
    Callable[[Any], None]
        A callable matching the validator signature `(ctx) -> None`.
    """

    def validator(ctx: Any) -> None:
        """Record a call for a `ctx`-only validator.

        Parameters
        ----------
        ctx
            The shared quality context.
        """

        calls.append(name)

        if not seen_ctx:
            seen_ctx.append(ctx)

    return validator


class TestExtractionUsageTracker:
    """Tests for ExtractionUsageTracker initialization and serialization."""

    def test_initial_state_is_zeroed(self) -> None:
        """A freshly created tracker should have all counters at zero."""

        tracker = ExtractionUsageTracker()

        assert tracker.extraction.input_tokens == 0
        assert tracker.extraction.output_tokens == 0
        assert tracker.extraction.requests == 0
        assert tracker.extraction.runs == 0
        assert tracker.validation.input_tokens == 0
        assert tracker.validation.output_tokens == 0
        assert tracker.validation.requests == 0
        assert tracker.validation.runs == 0

    def test_to_dict_totals_are_correct(self) -> None:
        """to_dict() totals should be the sum of extraction + validation buckets."""

        tracker = ExtractionUsageTracker()

        # Simulate some usage by directly setting attributes.
        tracker.extraction.cache_read_tokens = 10
        tracker.extraction.cache_write_tokens = 20
        tracker.extraction.input_tokens = 100
        tracker.extraction.output_tokens = 50
        tracker.extraction.requests = 3
        tracker.extraction.runs = 1

        tracker.validation.cache_read_tokens = 5
        tracker.validation.cache_write_tokens = 15
        tracker.validation.input_tokens = 80
        tracker.validation.output_tokens = 40
        tracker.validation.requests = 2
        tracker.validation.runs = 1

        d = tracker.to_dict()

        totals = d["totals"]
        assert totals["cache_read_tokens"] == 15
        assert totals["cache_write_tokens"] == 35
        assert totals["input_tokens"] == 180
        assert totals["output_tokens"] == 90
        assert totals["requests"] == 5
        assert totals["runs"] == 2
        assert totals["total_tokens"] == 270

    def test_to_dict_contains_agent_names(self) -> None:
        """to_dict() should contain per-agent breakdown keyed by agent name."""

        tracker = ExtractionUsageTracker()
        d = tracker.to_dict()

        assert "agents" in d
        assert "extraction" in d["agents"]
        assert "validation" in d["agents"]

    def test_to_dict_zero_state(self) -> None:
        """to_dict() on a fresh tracker should have all-zero totals."""

        tracker = ExtractionUsageTracker()
        d = tracker.to_dict()
        totals = d["totals"]

        assert totals["total_tokens"] == 0
        assert totals["requests"] == 0
        assert totals["runs"] == 0


def test__run_validation_agent_invokes_agent_and_tracks_usage(
    fixture_page_ir_minimal: PageIR,
    fixture_usage_tracker: ExtractionUsageTracker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_run_validation_agent` should run the agent and accumulate usage.

    Parameters
    ----------
    fixture_page_ir_minimal
        Extracted PageIR passed to validation.
    fixture_usage_tracker
        Usage tracker used by the orchestration code.
    monkeypatch
        Pytest monkeypatch fixture.
    """

    prompts = SimpleNamespace(system_message="SYS", user_message="USER")

    def fake_validate_page_ir_extraction(
        *, image_height: int, image_width: int, page_index: int, page_ir_json: str
    ) -> Any:
        """Return a minimal prompt pair and validate plumbing.

        Parameters
        ----------
        image_height
            Image height in pixels.
        image_width
            Image width in pixels.
        page_index
            0-based page index.
        page_ir_json
            PageIR serialized to JSON.

        Returns
        -------
        Any
            An object with `system_message` and `user_message`.
        """

        assert image_height == 200
        assert image_width == 100
        assert page_index == 0
        assert isinstance(page_ir_json, str) and page_ir_json
        return prompts

    verdict = ExtractionValidationVerdict(
        passed=True,
        rationale="oksaga;ughweaklberqklbhrugh3q4ou43u3khgkhrg;klwhgv;hevklfavladsklakdlsf",
    )
    stub_agent = _StubAgent(
        result=_StubRunResult(
            output=verdict,
            usage_obj=_StubUsage(
                cache_read_tokens=1,
                cache_write_tokens=2,
                input_tokens=3,
                output_tokens=4,
                requests=5,
            ),
        )
    )

    monkeypatch.setattr(
        llm_module,
        "validate_page_ir_extraction",
        fake_validate_page_ir_extraction,
    )
    _install_stub_validation_agent(agent=stub_agent, monkeypatch=monkeypatch)

    out = llm_module._run_validation_agent(
        image_height=200,
        image_width=100,
        page_index=0,
        page_ir=fixture_page_ir_minimal,
        png_bytes=b"\x89PNG\r\n\x1a\n...",
        usage_tracker=fixture_usage_tracker,
    )

    assert out is verdict
    assert fixture_usage_tracker.validation.requests == 5
    assert fixture_usage_tracker.validation.runs == 1

    assert len(stub_agent.run_sync_calls) == 1
    user_prompt = stub_agent.run_sync_calls[0]
    assert user_prompt[0] == "USER"
    assert isinstance(user_prompt[1], BinaryContent)
    assert user_prompt[1].data.startswith(b"\x89PNG")


def test_extract_page_ir_accumulates_extraction_usage(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_blank_page: Path,
    tmp_path: Path,
) -> None:
    """extract_page_ir should accumulate extraction agent usage in the tracker.

    Parameters
    ----------
    monkeypatch
        Pytest monkeypatch fixture.
    synthetic_blank_page
        PNG fixture path provided by the shared conftest.
    tmp_path
        Temporary directory for raw extraction artifacts.
    """

    tracker = ExtractionUsageTracker()

    extracted = PageIR(
        items=[
            _make_block(
                bbox=(1.0, 1.0, 10.0, 10.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Extracted",
            )
        ]
    )
    stub_agent = _StubAgent(
        result=_StubRunResult(
            output=extracted,
            usage_obj=_StubUsage(
                cache_read_tokens=5,
                cache_write_tokens=10,
                input_tokens=100,
                output_tokens=50,
                requests=2,
            ),
        )
    )

    passing_verdict = ExtractionValidationVerdict(
        passed=True,
        rationale="Good" + "x" * 60,
    )

    monkeypatch.setattr(
        llm_module,
        "extract_page_ir_from_pdf_page",
        lambda **kw: SimpleNamespace(system_message="SYS", user_message="USER"),
    )
    monkeypatch.setattr(
        llm_module, "_run_validation_agent", lambda **kw: passing_verdict
    )

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _install_stub_extraction_agent(
        agent=stub_agent, monkeypatch=monkeypatch, raw_page_irs_dir=raw_dir
    )

    llm_module.extract_page_ir(
        image_height=3508,
        image_width=2480,
        languages=["en"],
        page_index=0,
        pdf_page=None,
        png_fp=synthetic_blank_page,
        raw_page_irs_dir=raw_dir,
        usage_tracker=tracker,
    )

    assert tracker.extraction.input_tokens == 100
    assert tracker.extraction.output_tokens == 50
    assert tracker.extraction.cache_read_tokens == 5
    assert tracker.extraction.cache_write_tokens == 10
    assert tracker.extraction.requests == 2
    assert tracker.extraction.runs == 1


def test_extract_page_ir_asserts_when_validation_fails_without_corrected_page_ir(
    monkeypatch: pytest.MonkeyPatch, synthetic_blank_page: Path, tmp_path: Path
) -> None:
    """extract_page_ir should raise AssertionError when validation fails but
    corrected_page_ir is None.

    Parameters
    ----------
    monkeypatch
        Pytest monkeypatch fixture.
    synthetic_blank_page
        PNG fixture path provided by the shared conftest.
    tmp_path
        Temporary directory for raw extraction artifacts.
    """

    tracker = ExtractionUsageTracker()

    extracted = PageIR(
        items=[
            _make_block(
                bbox=(1.0, 1.0, 10.0, 10.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Extracted",
            )
        ]
    )
    stub_agent = _StubAgent(
        result=_StubRunResult(
            output=extracted,
            usage_obj=_StubUsage(input_tokens=1, output_tokens=1, requests=1),
        )
    )

    def fake_run_validation_agent(**kwargs: Any) -> ExtractionValidationVerdict:
        """Ignore arguments and return the bad verdict.

        Returns
        -------
        ExtractionValidationVerdict
             The bad verdict with no corrected PageIR.
        """

        return ExtractionValidationVerdict.model_construct(
            corrected_page_ir=None,
            issues=[],
            passed=False,
            rationale="Something wrong" + "x" * 60,
        )

    monkeypatch.setattr(llm_module, "_run_validation_agent", fake_run_validation_agent)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _install_stub_extraction_agent(
        agent=stub_agent, monkeypatch=monkeypatch, raw_page_irs_dir=raw_dir
    )

    # Patch prompt builder to return a minimal object.
    monkeypatch.setattr(
        llm_module,
        "extract_page_ir_from_pdf_page",
        lambda **kw: SimpleNamespace(system_message="SYS", user_message="USER"),
    )

    with pytest.raises(AssertionError, match="no corrected PageIR"):
        llm_module.extract_page_ir(
            image_height=3508,
            image_width=2480,
            languages=["en"],
            page_index=0,
            pdf_page=None,
            png_fp=synthetic_blank_page,
            raw_page_irs_dir=raw_dir,
            usage_tracker=tracker,
        )


def test_extract_page_ir_passes_pdf_hints_into_prompt_builder(
    fixture_usage_tracker: ExtractionUsageTracker,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_blank_page: Path,
    tmp_path: Path,
) -> None:
    """`extract_page_ir` should forward PDF-derived hints into prompt construction.

    This covers the `pdf_page is not None` branch and checks that:

    1. `extract_page_text_layer_hints` is invoked
    2. The returned `table_hint`/`text_hint` are passed into
        `extract_page_ir_from_pdf_page`

    Parameters
    ----------
    fixture_usage_tracker
        Usage tracker used by the orchestration code.
    monkeypatch
        Pytest monkeypatch fixture.
    synthetic_blank_page
        PNG fixture path provided by the shared conftest.
    tmp_path
        Temporary directory for raw extraction artifacts.
    """

    extracted = PageIR(
        items=[
            _make_block(
                bbox=(1.0, 1.0, 10.0, 10.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Extracted",
            )
        ]
    )
    stub_agent = _StubAgent(
        result=_StubRunResult(
            output=extracted,
            usage_obj=_StubUsage(input_tokens=1, output_tokens=1, requests=1),
        )
    )
    passing_verdict = ExtractionValidationVerdict(
        passed=True,
        rationale="okaoiugyreaoghyreoaughber;ahdgdashjasdgkhjadasdgadsfdasfdasdsas;g",
    )

    def fake_run_validation_agent(
        *,
        image_height: int,
        image_width: int,
        page_index: int,
        page_ir: PageIR,
        png_bytes: bytes,
        usage_tracker: ExtractionUsageTracker,
    ) -> ExtractionValidationVerdict:
        """Return a passing verdict.

        Parameters
        ----------
        image_height
            Image height in pixels.
        image_width
            Image width in pixels.
        page_index
            0-based page index.
        page_ir
            Extracted PageIR passed to validation.
        png_bytes
            PNG bytes of the page image.
        usage_tracker
            Shared usage tracker.

        Returns
        -------
        ExtractionValidationVerdict
            A passing verdict.
        """

        return passing_verdict

    def fake_extract_page_text_layer_hints(*, page: Any, page_index: int) -> Any:
        """Return a hint payload and validate arguments.

        Parameters
        ----------
        page
            The PDF page object.
        page_index
            0-based page index.

        Returns
        -------
        Any
            A hint-like object with `has_hints`, `table_hint`, and `text_hint`.
        """

        assert page is pdf_page
        assert page_index == 0
        return SimpleNamespace(has_hints=True, table_hint="TABLE", text_hint="TEXT")

    seen_prompt_kwargs: dict[str, Any] = {}

    def fake_extract_page_ir_from_pdf_page(
        *,
        image_height: int,
        image_width: int,
        languages: list[str],
        page_index: int,
        table_layer_hint: str | None,
        text_layer_hint: str | None,
    ) -> Any:
        """Capture kwargs and return a minimal prompt pair.

        Parameters
        ----------
        image_height
            Image height in pixels.
        image_width
            Image width in pixels.
        languages
            Languages list forwarded from the caller.
        page_index
            0-based page index.
        table_layer_hint
            Table hint derived from the PDF layer.
        text_layer_hint
            Text hint derived from the PDF layer.

        Returns
        -------
        Any
            An object with `system_message` and `user_message`.
        """

        seen_prompt_kwargs.update(
            {
                "image_height": image_height,
                "image_width": image_width,
                "languages": languages,
                "page_index": page_index,
                "table_layer_hint": table_layer_hint,
                "text_layer_hint": text_layer_hint,
            }
        )
        return SimpleNamespace(system_message="SYS", user_message="USER")

    pdf_page = SimpleNamespace()

    monkeypatch.setattr(llm_module, "_run_validation_agent", fake_run_validation_agent)
    monkeypatch.setattr(
        llm_module, "extract_page_ir_from_pdf_page", fake_extract_page_ir_from_pdf_page
    )
    monkeypatch.setattr(
        llm_module, "extract_page_text_layer_hints", fake_extract_page_text_layer_hints
    )

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _install_stub_extraction_agent(
        agent=stub_agent, monkeypatch=monkeypatch, raw_page_irs_dir=raw_dir
    )

    out = llm_module.extract_page_ir(
        image_height=3508,
        image_width=2480,
        languages=["en"],
        page_index=0,
        pdf_page=pdf_page,
        png_fp=synthetic_blank_page,
        raw_page_irs_dir=raw_dir,
        usage_tracker=fixture_usage_tracker,
    )

    assert out is extracted
    assert seen_prompt_kwargs["table_layer_hint"] == "TABLE"
    assert seen_prompt_kwargs["text_layer_hint"] == "TEXT"


def test_extract_page_ir_returns_corrected_page_ir_when_validation_fails(
    fixture_usage_tracker: ExtractionUsageTracker,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_blank_page: Path,
    tmp_path: Path,
) -> None:
    """`extract_page_ir` should return a corrected PageIR when validation fails.

    Parameters
    ----------
    fixture_usage_tracker
        Usage tracker used by the orchestration code.
    monkeypatch
        Pytest monkeypatch fixture.
    synthetic_blank_page
        PNG fixture path provided by the shared conftest.
    tmp_path
        Temporary directory for raw extraction artifacts.
    """

    corrected = PageIR(
        items=[
            _make_block(
                bbox=(2.0, 2.0, 20.0, 20.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Corrected",
            )
        ]
    )
    extracted = PageIR(
        items=[
            _make_block(
                bbox=(1.0, 1.0, 10.0, 10.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Extracted",
            )
        ]
    )
    stub_agent = _StubAgent(
        result=_StubRunResult(
            output=extracted,
            usage_obj=_StubUsage(input_tokens=10, output_tokens=20, requests=1),
        )
    )

    issue = ExtractionValidationIssue(
        description="Wrong text",
        item_index=0,
        severity="error",
        suggested_fix="Fix the text",
    )
    failing_verdict = ExtractionValidationVerdict(
        corrected_page_ir=corrected,
        issues=[issue],
        passed=False,
        rationale="Mismatchoo3rhyg;lehgb;leahb;kqgb3qo;uhbo;3owlghrelbvhaeklbvfdakdkhh",
    )

    def fake_run_validation_agent(
        *,
        image_height: int,
        image_width: int,
        page_index: int,
        page_ir: PageIR,
        png_bytes: bytes,
        usage_tracker: ExtractionUsageTracker,
    ) -> ExtractionValidationVerdict:
        """Return a failing verdict that includes a correction.

        Parameters
        ----------
        image_height
            Image height in pixels.
        image_width
            Image width in pixels.
        page_index
            0-based page index.
        page_ir
            Extracted PageIR passed to validation.
        png_bytes
            PNG bytes of the page image.
        usage_tracker
            Shared usage tracker.

        Returns
        -------
        ExtractionValidationVerdict
            A failing verdict with a corrected PageIR.
        """

        assert page_ir is extracted
        assert png_bytes
        assert usage_tracker is fixture_usage_tracker
        return failing_verdict

    monkeypatch.setattr(llm_module, "_run_validation_agent", fake_run_validation_agent)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _install_stub_extraction_agent(
        agent=stub_agent, monkeypatch=monkeypatch, raw_page_irs_dir=raw_dir
    )

    out = llm_module.extract_page_ir(
        image_height=3508,
        image_width=2480,
        languages=["en"],
        page_index=0,
        pdf_page=None,
        png_fp=synthetic_blank_page,
        raw_page_irs_dir=raw_dir,
        usage_tracker=fixture_usage_tracker,
    )

    assert out is corrected
    assert fixture_usage_tracker.extraction.runs == 1


def test_extract_page_ir_returns_extraction_page_ir_when_validation_passes(
    fixture_usage_tracker: ExtractionUsageTracker,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_blank_page: Path,
    tmp_path: Path,
) -> None:
    """`extract_page_ir` should return the extracted PageIR when validation passes.

    Parameters
    ----------
    fixture_usage_tracker
        Usage tracker used by the orchestration code.
    monkeypatch
        Pytest monkeypatch fixture.
    synthetic_blank_page
        PNG fixture path provided by the shared conftest.
    tmp_path
        Temporary directory for raw extraction artifacts.
    """

    extracted = PageIR(
        items=[
            _make_block(
                bbox=(1.0, 1.0, 10.0, 10.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Extracted",
            )
        ]
    )
    stub_agent = _StubAgent(
        result=_StubRunResult(
            output=extracted,
            usage_obj=_StubUsage(input_tokens=1, output_tokens=2, requests=1),
        )
    )
    passing_verdict = ExtractionValidationVerdict(
        passed=True,
        rationale="All goodxxxxxxxxxxxxxxxxxxxxxxxxxxxxasdfakdugawergaerwgaklguagha",
    )

    def fake_run_validation_agent(
        *,
        image_height: int,
        image_width: int,
        page_index: int,
        page_ir: PageIR,
        png_bytes: bytes,
        usage_tracker: ExtractionUsageTracker,
    ) -> ExtractionValidationVerdict:
        """Return a passing verdict.

        Parameters
        ----------
        image_height
            Image height in pixels.
        image_width
            Image width in pixels.
        page_index
            0-based page index.
        page_ir
            Extracted PageIR passed to validation.
        png_bytes
            PNG bytes of the page image.
        usage_tracker
            Shared usage tracker.

        Returns
        -------
        ExtractionValidationVerdict
            A passing verdict.
        """

        assert page_ir is extracted
        assert png_bytes
        assert usage_tracker is fixture_usage_tracker
        return passing_verdict

    monkeypatch.setattr(llm_module, "_run_validation_agent", fake_run_validation_agent)

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _install_stub_extraction_agent(
        agent=stub_agent, monkeypatch=monkeypatch, raw_page_irs_dir=raw_dir
    )

    out = llm_module.extract_page_ir(
        image_height=3508,
        image_width=2480,
        languages=["en"],
        page_index=0,
        pdf_page=None,
        png_fp=synthetic_blank_page,
        raw_page_irs_dir=raw_dir,
        usage_tracker=fixture_usage_tracker,
    )

    assert out is extracted
    assert fixture_usage_tracker.extraction.runs == 1


def test_extract_page_ir_skips_hints_when_pdf_page_is_none(
    monkeypatch: pytest.MonkeyPatch, synthetic_blank_page: Path, tmp_path: Path
) -> None:
    """When pdf_page is None, hint extraction should be skipped and hints passed as
    None to the prompt builder.

    Parameters
    ----------
    monkeypatch
        Pytest monkeypatch fixture.
    synthetic_blank_page
        PNG fixture path provided by the shared conftest.
    tmp_path
        Temporary directory for raw extraction artifacts.
    """

    tracker = ExtractionUsageTracker()

    extracted = PageIR(
        items=[
            _make_block(
                bbox=(1.0, 1.0, 10.0, 10.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Body",
            )
        ]
    )
    stub_agent = _StubAgent(
        result=_StubRunResult(
            output=extracted,
            usage_obj=_StubUsage(input_tokens=1, output_tokens=1, requests=1),
        )
    )

    passing_verdict = ExtractionValidationVerdict(
        passed=True,
        rationale="Fine" + "x" * 60,
    )

    hint_called = []

    def fake_extract_hints(**kwargs: Any) -> Any:
        """Record that hint extraction was called and return no hints.

        Returns
        -------
        Any
            An object with has_hints=False and no hints.
        """

        hint_called.append(True)
        return SimpleNamespace(has_hints=False, table_hint=None, text_hint=None)

    seen_prompt_kwargs: dict[str, Any] = {}

    def fake_prompt_builder(**kwargs: Any) -> Any:
        """Record the kwargs passed to the prompt builder and return a dummy prompt
        pair.

        Parameters
        ----------
        **kwargs
            Keyword arguments passed to the prompt builder, expected to include
            'table_layer_hint' and 'text_layer_hint'.

        Returns
        -------
        Any
            An object with `system_message` and `user_message` attributes.
        """

        seen_prompt_kwargs.update(kwargs)
        return SimpleNamespace(system_message="SYS", user_message="USER")

    monkeypatch.setattr(llm_module, "extract_page_text_layer_hints", fake_extract_hints)
    monkeypatch.setattr(
        llm_module, "extract_page_ir_from_pdf_page", fake_prompt_builder
    )
    monkeypatch.setattr(
        llm_module,
        "_run_validation_agent",
        lambda **kw: passing_verdict,
    )

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _install_stub_extraction_agent(
        agent=stub_agent, monkeypatch=monkeypatch, raw_page_irs_dir=raw_dir
    )

    llm_module.extract_page_ir(
        image_height=3508,
        image_width=2480,
        languages=["en"],
        page_index=0,
        pdf_page=None,
        png_fp=synthetic_blank_page,
        raw_page_irs_dir=raw_dir,
        usage_tracker=tracker,
    )

    # extract_page_text_layer_hints should NOT have been called.
    assert not hint_called

    # Hints should be None in the prompt builder call.
    assert seen_prompt_kwargs["table_layer_hint"] is None
    assert seen_prompt_kwargs["text_layer_hint"] is None


def test_verify_page_ir_extraction_quality_calls_validators_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`verify_page_ir_extraction_quality` should call validators in a fixed order.

    Parameters
    ----------
    monkeypatch
        Pytest monkeypatch fixture.
    """

    calls: list[str] = []
    seen_ctx: list[Any] = []

    monkeypatch.setattr(
        llm_module,
        "validate_image_dimensions",
        _make_ctx_only_validator(
            calls=calls, name="validate_image_dimensions", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_extraction_text_constraints",
        _make_ctx_only_validator(
            calls=calls, name="validate_extraction_text_constraints", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_item_bboxes_required_and_in_bounds",
        _make_ctx_only_validator(
            calls=calls,
            name="validate_item_bboxes_required_and_in_bounds",
            seen_ctx=seen_ctx,
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_full_page_bboxes",
        _make_ctx_only_validator(
            calls=calls, name="validate_full_page_bboxes", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_full_page_figure_requires_double_check",
        _make_attempt_and_ctx_validator(
            attempt_expected=2,
            calls=calls,
            name="validate_full_page_figure_requires_double_check",
            seen_ctx=seen_ctx,
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_no_duplicate_item_bboxes",
        _make_ctx_only_validator(
            calls=calls, name="validate_no_duplicate_item_bboxes", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_basic_block_invariants",
        _make_ctx_only_validator(
            calls=calls, name="validate_basic_block_invariants", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_footnote_blocks_are_plausible",
        _make_ctx_only_validator(
            calls=calls,
            name="validate_footnote_blocks_are_plausible",
            seen_ctx=seen_ctx,
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_figure_blocks_are_well_formed",
        _make_ctx_only_validator(
            calls=calls,
            name="validate_figure_blocks_are_well_formed",
            seen_ctx=seen_ctx,
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_artifacts_are_true_artifacts",
        _make_ctx_only_validator(
            calls=calls, name="validate_artifacts_are_true_artifacts", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_table_integrity",
        _make_ctx_only_validator(
            calls=calls, name="validate_table_integrity", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_placeholder_bboxes",
        _make_ctx_only_validator(
            calls=calls, name="validate_placeholder_bboxes", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_continuity_for_extraction",
        _make_ctx_only_validator(
            calls=calls, name="validate_continuity_for_extraction", seen_ctx=seen_ctx
        ),
    )
    monkeypatch.setattr(
        llm_module,
        "validate_gross_reading_order",
        _make_ctx_only_validator(
            calls=calls, name="validate_gross_reading_order", seen_ctx=seen_ctx
        ),
    )

    page_ir = PageIR(
        items=[
            _make_block(
                bbox=(0.0, 0.0, 50.0, 20.0),
                block_type=BlockType.ARTIFACT,
                boundary=ItemBoundary.COMPLETE,
                text="Page 1",
            ),
            _make_block(
                bbox=(0.0, 30.0, 200.0, 80.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Body",
            ),
        ]
    )

    llm_module.verify_page_ir_extraction_quality(
        attempt=2,
        image_height=400,
        image_width=300,
        page_ir=page_ir,
    )

    assert calls == [
        "validate_image_dimensions",
        "validate_extraction_text_constraints",
        "validate_item_bboxes_required_and_in_bounds",
        "validate_full_page_bboxes",
        "validate_full_page_figure_requires_double_check",
        "validate_no_duplicate_item_bboxes",
        "validate_basic_block_invariants",
        "validate_footnote_blocks_are_plausible",
        "validate_figure_blocks_are_well_formed",
        "validate_artifacts_are_true_artifacts",
        "validate_table_integrity",
        "validate_placeholder_bboxes",
        "validate_continuity_for_extraction",
        "validate_gross_reading_order",
    ]

    ctx = seen_ctx[0]
    assert ctx.image_height == 400
    assert ctx.image_width == 300
    assert ctx.page_bbox == (0.0, 0.0, 300.0, 400.0)
    assert ctx.page_ir is page_ir
    assert ctx.items == page_ir.items
    assert ctx.non_artifact_items[0][0] == 1


def test_verify_quality_non_artifact_items_all_included_when_no_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When there are no ARTIFACT blocks, non_artifact_items should include every item
    with its original index.

    Parameters
    ----------
    monkeypatch
        Pytest monkeypatch fixture.
    """

    captured_ctx: list[Any] = []

    def capturing_validator(ctx: Any) -> None:
        """A validator that captures the ctx object for inspection.

        Parameters
        ----------
        ctx
            The shared quality context passed to validators.
        """

        if not captured_ctx:
            captured_ctx.append(ctx)

    # Patch all validators to no-ops, but capture ctx from the first one.
    for name in [
        "validate_image_dimensions",
        "validate_extraction_text_constraints",
        "validate_item_bboxes_required_and_in_bounds",
        "validate_full_page_bboxes",
        "validate_no_duplicate_item_bboxes",
        "validate_basic_block_invariants",
        "validate_footnote_blocks_are_plausible",
        "validate_figure_blocks_are_well_formed",
        "validate_artifacts_are_true_artifacts",
        "validate_table_integrity",
        "validate_placeholder_bboxes",
        "validate_continuity_for_extraction",
        "validate_gross_reading_order",
    ]:
        monkeypatch.setattr(llm_module, name, capturing_validator)

    monkeypatch.setattr(
        llm_module,
        "validate_full_page_figure_requires_double_check",
        lambda *, attempt, ctx: None,
    )

    page_ir = PageIR(
        items=[
            _make_block(
                bbox=(0.0, 0.0, 50.0, 20.0),
                block_type=BlockType.HEADING,
                boundary=ItemBoundary.COMPLETE,
                text="Title",
            ),
            _make_block(
                bbox=(0.0, 30.0, 200.0, 80.0),
                block_type=BlockType.PARAGRAPH,
                boundary=ItemBoundary.COMPLETE,
                text="Body",
            ),
        ]
    )

    llm_module.verify_page_ir_extraction_quality(
        attempt=0,
        image_height=400,
        image_width=300,
        page_ir=page_ir,
    )

    ctx = captured_ctx[0]

    # All items should be non-artifact.
    assert len(ctx.non_artifact_items) == 2
    assert ctx.non_artifact_items[0] == (0, page_ir.items[0])
    assert ctx.non_artifact_items[1] == (1, page_ir.items[1])
