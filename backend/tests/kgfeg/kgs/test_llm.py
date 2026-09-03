"""Test Learning Progressions usage and shared KG model plumbing."""

# Standard Library
from types import ModuleType

# Third Party Library
import pytest

# Package Library
import kgfeg.kgs.agents as kg_agents
import kgfeg.kgs.llm as kg_llm
import kgfeg.kgs.prompts as kg_prompts

from kgfeg.config import BackendSettings, Settings
from kgfeg.kgs.llm import KGUsageTracker

_KG_AGENT_NAMES = (
    "lc_dedup",
    "lc_generation",
    "lc_generation_validation",
    "lp_generation",
    "lp_generation_validation",
    "sfi_dedup",
    "sfi_dedup_validation",
    "sfi_extraction",
    "sfi_extraction_validation",
    "sfi_has_child",
    "sfi_has_child_validation",
)


@pytest.mark.parametrize(
    ("expected_settings", "model_name"),
    (
        (
            {
                "anthropic_effort": "medium",
                "anthropic_thinking": {"type": "adaptive"},
                "max_tokens": 8192,
            },
            "anthropic:claude-sonnet-4-6",
        ),
        (
            {
                "max_tokens": 8192,
                "openai_reasoning_effort": "low",
                "openai_reasoning_summary": "detailed",
            },
            "openai:gpt-5.2",
        ),
    ),
)
def test_learning_progressions_model_settings_resolve_from_shared_kg_model(
    expected_settings: dict[str, object],
    model_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LP provider settings resolve through the shared KG model configuration.

    Parameters
    ----------
    expected_settings
        Provider-specific settings expected from the model registry.
    model_name
        Shared KG model identifier to resolve.
    monkeypatch
        Pytest helper that restores the shared settings singleton after the test.
    """

    monkeypatch.setattr(Settings, "LLM_ANTHROPIC_EFFORT", "medium")
    monkeypatch.setattr(Settings, "LLM_KG_MODEL", model_name)
    monkeypatch.setattr(Settings, "LLM_MAX_OUTPUT_TOKENS", 8192)
    monkeypatch.setattr(Settings, "LLM_OPENAI_REASONING_EFFORT", "low")

    model_config = Settings.llm_config("kgs")

    assert model_config.model == model_name
    assert model_config.kgs_settings("learning_progressions") == expected_settings


@pytest.mark.parametrize(
    "module",
    (
        pytest.param(kg_agents, id="agent-factories"),
        pytest.param(kg_llm, id="llm-calls"),
        pytest.param(kg_prompts, id="prompts"),
    ),
)
def test_no_learning_progressions_agent_prompt_or_call_exists(
    module: ModuleType,
) -> None:
    """Usage plumbing exposes no LP agent, prompt, or LLM-call implementation.

    Parameters
    ----------
    module
        Production module whose locally defined callables are inspected.
    """

    progression_callables = {
        name
        for name, value in vars(module).items()
        if callable(value)
        and getattr(value, "__module__", None) == module.__name__
        and ("lp" in name.lower().split("_") or "progression" in name.lower())
    }

    assert progression_callables == set()


def test_no_learning_progressions_model_environment_setting_exists() -> None:
    """LP uses the shared KG model field without a dedicated environment setting."""

    model_fields = set(BackendSettings.model_fields)
    progression_model_fields = {
        name
        for name in model_fields
        if name.startswith("LLM_")
        and ("LP" in name.split("_") or "PROGRESSION" in name)
    }

    assert "LLM_KG_MODEL" in model_fields
    assert progression_model_fields == set()


def test_usage_tracker_aggregates_learning_progressions_with_existing_buckets() -> None:
    """Aggregate totals include every LP and pre-existing KG usage bucket."""

    tracker = KGUsageTracker()

    for index, agent_name in enumerate(_KG_AGENT_NAMES, start=1):
        bucket = getattr(tracker, agent_name)
        bucket.cache_read_tokens = index
        bucket.cache_write_tokens = index * 2
        bucket.input_tokens = index * 3
        bucket.output_tokens = index * 4
        bucket.requests = index * 5
        bucket.runs = index * 6

    serialized = tracker.to_dict()

    assert set(serialized["agents"]) == set(_KG_AGENT_NAMES)
    assert serialized["totals"] == {
        "cache_read_tokens": 66,
        "cache_write_tokens": 132,
        "input_tokens": 198,
        "output_tokens": 264,
        "requests": 330,
        "runs": 396,
        "total_tokens": 462,
    }


def test_usage_tracker_initializes_learning_progressions_buckets() -> None:
    """LP producer and checker buckets start separate, named, and zeroed."""

    tracker = KGUsageTracker()

    assert tracker.lp_generation.agent_name == "lp_generation"
    assert tracker.lp_generation_validation.agent_name == "lp_generation_validation"
    assert tracker.lp_generation is not tracker.lp_generation_validation
    assert tracker.lp_generation is not tracker.lc_generation

    for bucket in (tracker.lp_generation, tracker.lp_generation_validation):
        assert bucket.cache_read_tokens == 0
        assert bucket.cache_write_tokens == 0
        assert bucket.input_tokens == 0
        assert bucket.output_tokens == 0
        assert bucket.requests == 0
        assert bucket.runs == 0


def test_usage_tracker_serializes_learning_progressions_buckets() -> None:
    """Per-agent serialization retains all LP producer and checker counters."""

    tracker = KGUsageTracker()
    tracker.lp_generation.cache_read_tokens = 11
    tracker.lp_generation.cache_write_tokens = 12
    tracker.lp_generation.input_tokens = 13
    tracker.lp_generation.output_tokens = 14
    tracker.lp_generation.requests = 15
    tracker.lp_generation.runs = 16
    tracker.lp_generation_validation.input_tokens = 21
    tracker.lp_generation_validation.output_tokens = 22
    tracker.lp_generation_validation.requests = 23
    tracker.lp_generation_validation.runs = 24

    agents = tracker.to_dict()["agents"]

    assert agents["lp_generation"] == {
        "agent_name": "lp_generation",
        "cache_read_tokens": 11,
        "cache_write_tokens": 12,
        "input_tokens": 13,
        "output_tokens": 14,
        "requests": 15,
        "runs": 16,
        "total_tokens": 27,
    }
    assert agents["lp_generation_validation"] == {
        "agent_name": "lp_generation_validation",
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "input_tokens": 21,
        "output_tokens": 22,
        "requests": 23,
        "runs": 24,
        "total_tokens": 43,
    }
