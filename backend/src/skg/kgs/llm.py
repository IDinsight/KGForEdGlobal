"""This module contains the orchestration logic for creating various knowledge graph
artifacts using an LLM.
"""

# Standard Library
from dataclasses import dataclass

# Package Library
from skg.config import Settings
from skg.kgs.agents import create_sfi_dedup_agent, create_sfi_extraction_agent
from skg.kgs.prompts import (
    extract_sfi_candidates_from_window,
    review_sfi_dedup_candidates,
)
from skg.kgs.schemas import (
    ExtractionWindow,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIExtractionResult,
)
from skg.kgs.validators import (
    verify_sfi_dedup_review_quality,
    verify_sfi_extraction_quality,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import AgentUsageBucket


@dataclass
class KGUsageTracker:
    """Track LLM token usage for the KG pipeline."""

    sfi_dedup: AgentUsageBucket
    sfi_extraction: AgentUsageBucket

    def __init__(self) -> None:
        """Initialize empty usage buckets for KG LLM agents."""

        self.sfi_dedup = AgentUsageBucket(agent_name="sfi_dedup")
        self.sfi_extraction = AgentUsageBucket(agent_name="sfi_extraction")

    def to_dict(self) -> dict[str, object]:
        """Serialize usage totals to a JSON-friendly dictionary.

        Returns
        -------
        dict[str, object]
            Usage summary with per-agent and total counts.
        """

        agent_buckets = {
            "sfi_dedup": self.sfi_dedup,
            "sfi_extraction": self.sfi_extraction,
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
