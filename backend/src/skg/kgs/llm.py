"""This module contains the orchestration logic for creating various knowledge graph
artifacts using an LLM.
"""

# Standard Library
from dataclasses import dataclass
from typing import Optional

# Package Library
from skg.config import Settings
from skg.kgs.agents import (
    create_lc_dedup_agent,
    create_lc_generation_agent,
    create_sfi_dedup_agent,
    create_sfi_extraction_agent,
    create_sfi_has_child_agent,
)
from skg.kgs.prompts import (
    build_lc_dedup_prompt,
    build_lc_generation_prompt,
    extract_sfi_candidates_from_window,
    resolve_sfi_has_child_parents,
    review_sfi_dedup_candidates,
)
from skg.kgs.schemas import (
    ExtractionWindow,
    LCDedupRequest,
    LCDedupResponse,
    LCGenerationRequest,
    LCGenerationResponse,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIExtractionResult,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
)
from skg.kgs.validators import (
    verify_lc_dedup_quality,
    verify_lc_generation_quality,
    verify_sfi_dedup_review_quality,
    verify_sfi_extraction_quality,
    verify_sfi_has_child_resolution_quality,
)
from skg.schemas import CreateKGConfig, _CreateKGLearningComponentsConfig
from skg.utils.general import AgentUsageBucket


@dataclass
class KGUsageTracker:
    """Track LLM token usage for the KG pipeline."""

    lc_dedup: AgentUsageBucket
    lc_generation: AgentUsageBucket
    sfi_dedup: AgentUsageBucket
    sfi_extraction: AgentUsageBucket
    sfi_has_child: AgentUsageBucket

    def __init__(self) -> None:
        """Initialize empty usage buckets for KG LLM agents."""

        self.lc_dedup = AgentUsageBucket(agent_name="lc_dedup")
        self.lc_generation = AgentUsageBucket(agent_name="lc_generation")
        self.sfi_dedup = AgentUsageBucket(agent_name="sfi_dedup")
        self.sfi_extraction = AgentUsageBucket(agent_name="sfi_extraction")
        self.sfi_has_child = AgentUsageBucket(agent_name="sfi_has_child")

    def to_dict(self) -> dict[str, object]:
        """Serialize usage totals to a JSON-friendly dictionary.

        Returns
        -------
        dict[str, object]
            Usage summary with per-agent and total counts.
        """

        agent_buckets = {
            "lc_dedup": self.lc_dedup,
            "lc_generation": self.lc_generation,
            "sfi_dedup": self.sfi_dedup,
            "sfi_extraction": self.sfi_extraction,
            "sfi_has_child": self.sfi_has_child,
        }
        totals = {
            "cache_read_tokens": sum(
                bucket.cache_read_tokens for bucket in agent_buckets.values()
            ),
            "cache_write_tokens": sum(
                bucket.cache_write_tokens for bucket in agent_buckets.values()
            ),
            "input_tokens": sum(
                bucket.input_tokens for bucket in agent_buckets.values()
            ),
            "output_tokens": sum(
                bucket.output_tokens for bucket in agent_buckets.values()
            ),
            "requests": sum(bucket.requests for bucket in agent_buckets.values()),
            "runs": sum(bucket.runs for bucket in agent_buckets.values()),
            "total_tokens": sum(
                bucket.input_tokens + bucket.output_tokens
                for bucket in agent_buckets.values()
            ),
        }
        return {
            "agents": {
                agent_name: bucket.to_dict()
                for agent_name, bucket in agent_buckets.items()
            },
            "totals": totals,
        }


def adjudicate_lc_dedup_request(
    *,
    lc_dedup_instructions: Optional[str],
    lc_dedup_request: LCDedupRequest,
    usage_tracker: KGUsageTracker,
) -> LCDedupResponse:
    """Adjudicate one bounded batch of duplicate-candidate pairs via LLM.

    Parameters
    ----------
    lc_dedup_instructions
        Optional curriculum-specific adjudication policy, or None for the
        generic rubric alone.
    lc_dedup_request
        Bounded adjudication request.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    LCDedupResponse
        Parsed and quality-validated pair-verdict response.
    """

    prompts = build_lc_dedup_prompt(
        lc_dedup_instructions=lc_dedup_instructions,
        lc_dedup_request=lc_dedup_request,
    )
    agent = create_lc_dedup_agent(
        instructions=prompts.system_message,
        lc_dedup_request=lc_dedup_request,
        model_config=Settings.llm_config("kgs"),
        verify_quality_fn=verify_lc_dedup_quality,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.lc_dedup.add_run_usage(result.usage())
    return result.output


def extract_sfi_candidates(
    *,
    extraction_window: ExtractionWindow,
    kg_config: CreateKGConfig,
    usage_tracker: KGUsageTracker,
) -> SFIExtractionResult:
    """Extract SFI candidates from one extraction window using an LLM.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to inspect.
    kg_config
        Country/document-specific KG extraction configuration.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    SFIExtractionResult
        Parsed and quality-validated SFI extraction result.
    """

    prompts = extract_sfi_candidates_from_window(
        extraction_window=extraction_window, kg_config=kg_config
    )
    agent = create_sfi_extraction_agent(
        instructions=prompts.system_message,
        kg_config=kg_config,
        model_config=Settings.llm_config("kgs"),
        verify_quality_fn=verify_sfi_extraction_quality,
        window=extraction_window,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_extraction.add_run_usage(result.usage())
    return result.output


def generate_learning_components_for_request(
    *,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_generation_request: LCGenerationRequest,
    usage_tracker: KGUsageTracker,
) -> LCGenerationResponse:
    """Decompose one bounded LC generation request into atomic skills via LLM.

    Parameters
    ----------
    lc_config
        Learning Components runtime configuration (instructions + validator
        knobs).
    lc_generation_request
        Bounded LC generation request.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    LCGenerationResponse
        Parsed and quality-validated atomic-skills response.
    """

    prompts = build_lc_generation_prompt(
        generation_instructions=lc_config.generation_instructions,
        lc_generation_request=lc_generation_request,
    )
    agent = create_lc_generation_agent(
        instructions=prompts.system_message,
        lc_config=lc_config,
        lc_generation_request=lc_generation_request,
        model_config=Settings.llm_config("kgs"),
        verify_quality_fn=verify_lc_generation_quality,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.lc_generation.add_run_usage(result.usage())
    return result.output


def resolve_sfi_has_child_parent_request(
    *, resolution_request: SFIHasChildResolutionRequest, usage_tracker: KGUsageTracker
) -> SFIHasChildResolutionResponse:
    """Resolve direct hasChild parents for one bounded request using an LLM.

    Parameters
    ----------
    resolution_request
        Bounded hasChild parent-selection request.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    SFIHasChildResolutionResponse
        Parsed and quality-validated hasChild resolution response.
    """

    prompts = resolve_sfi_has_child_parents(resolution_request)
    agent = create_sfi_has_child_agent(
        instructions=prompts.system_message,
        model_config=Settings.llm_config("kgs"),
        resolution_request=resolution_request,
        verify_quality_fn=verify_sfi_has_child_resolution_quality,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_has_child.add_run_usage(result.usage())
    return result.output


def review_sfi_dedup_set(
    *, review_request: SFIDedupReviewRequest, usage_tracker: KGUsageTracker
) -> SFIDedupReviewResponse:
    """Review one bounded SFI dedup set using an LLM.

    Parameters
    ----------
    review_request
        Bounded dedup review request to inspect.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    SFIDedupReviewResponse
        Parsed and quality-validated SFI dedup review response.
    """

    prompts = review_sfi_dedup_candidates(review_request)
    agent = create_sfi_dedup_agent(
        instructions=prompts.system_message,
        model_config=Settings.llm_config("kgs"),
        review_request=review_request,
        verify_quality_fn=verify_sfi_dedup_review_quality,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_dedup.add_run_usage(result.usage())
    return result.output
