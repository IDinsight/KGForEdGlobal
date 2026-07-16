"""This module contains the LLM orchestration for Academic Standards knowledge graph
creation.

SFI extraction and SFI deduplication use two-stage producer/checker flows. Each
producer returns a complete draft result, and an independent validation agent reviews
the same bounded evidence, accepts the draft, or returns a complete corrected result.
"""

# Standard Library
from dataclasses import dataclass

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.kgs.agents import (
    create_sfi_dedup_agent,
    create_sfi_dedup_validation_agent,
    create_sfi_extraction_agent,
    create_sfi_extraction_validation_agent,
    create_sfi_has_child_agent,
)
from skg.kgs.prompts import (
    extract_sfi_candidates_from_window,
    resolve_sfi_has_child_parents,
    review_sfi_dedup_candidates,
    validate_sfi_dedup_response,
    validate_sfi_extraction_result,
)
from skg.kgs.schemas import (
    ExtractionWindow,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIDedupValidationVerdict,
    SFIExtractionResult,
    SFIExtractionValidationVerdict,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
)
from skg.kgs.validators import (
    verify_sfi_dedup_review_integrity,
    verify_sfi_dedup_validation_integrity,
    verify_sfi_extraction_integrity,
    verify_sfi_extraction_validation_integrity,
    verify_sfi_has_child_resolution_quality,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import AgentUsageBucket


@dataclass
class KGUsageTracker:
    """Track LLM token usage for the KG pipeline."""

    sfi_dedup: AgentUsageBucket
    sfi_dedup_validation: AgentUsageBucket
    sfi_extraction: AgentUsageBucket
    sfi_extraction_validation: AgentUsageBucket
    sfi_has_child: AgentUsageBucket

    def __init__(self) -> None:
        """Initialize empty usage buckets for KG LLM agents."""

        self.sfi_dedup = AgentUsageBucket(agent_name="sfi_dedup")
        self.sfi_dedup_validation = AgentUsageBucket(agent_name="sfi_dedup_validation")
        self.sfi_extraction = AgentUsageBucket(agent_name="sfi_extraction")
        self.sfi_extraction_validation = AgentUsageBucket(
            agent_name="sfi_extraction_validation"
        )
        self.sfi_has_child = AgentUsageBucket(agent_name="sfi_has_child")

    def to_dict(self) -> dict[str, object]:
        """Serialize usage totals to a JSON-friendly dictionary.

        Returns
        -------
        dict[str, object]
            Usage summary with per-agent and total counts.
        """

        agent_buckets = {
            "sfi_dedup": self.sfi_dedup,
            "sfi_dedup_validation": self.sfi_dedup_validation,
            "sfi_extraction": self.sfi_extraction,
            "sfi_extraction_validation": self.sfi_extraction_validation,
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


def _run_sfi_dedup_validation(
    *,
    draft_response: SFIDedupReviewResponse,
    review_request: SFIDedupReviewRequest,
    usage_tracker: KGUsageTracker,
) -> SFIDedupValidationVerdict:
    """Run the second-stage LLM review for one draft SFI dedup response.

    Parameters
    ----------
    draft_response
        First-stage dedup response to review.
    review_request
        Original bounded dedup request.
    usage_tracker
        Usage tracker for validation-agent token accounting.

    Returns
    -------
    SFIDedupValidationVerdict
        Parsed and integrity-validated checker verdict.
    """

    prompts = validate_sfi_dedup_response(
        draft_response=draft_response, review_request=review_request
    )
    agent = create_sfi_dedup_validation_agent(
        draft_response=draft_response,
        instructions=prompts.system_message,
        model_config=Settings.llm_config("kgs"),
        review_request=review_request,
        verify_integrity_fn=verify_sfi_dedup_validation_integrity,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_dedup_validation.add_run_usage(result.usage())
    return result.output


def _run_sfi_extraction_validation(
    *,
    draft_result: SFIExtractionResult,
    extraction_window: ExtractionWindow,
    kg_config: CreateKGConfig,
    usage_tracker: KGUsageTracker,
) -> SFIExtractionValidationVerdict:
    """Run the second-stage LLM review for one draft extraction result.

    Parameters
    ----------
    draft_result
        First-stage extraction result to review.
    extraction_window
        Source-faithful extraction window.
    kg_config
        Country/document-specific KG extraction configuration.
    usage_tracker
        Usage tracker for validation-agent token accounting.

    Returns
    -------
    SFIExtractionValidationVerdict
        Parsed and integrity-validated checker verdict.
    """

    prompts = validate_sfi_extraction_result(
        draft_result=draft_result,
        extraction_window=extraction_window,
        kg_config=kg_config,
    )
    agent = create_sfi_extraction_validation_agent(
        draft_result=draft_result,
        instructions=prompts.system_message,
        kg_config=kg_config,
        model_config=Settings.llm_config("kgs"),
        verify_integrity_fn=verify_sfi_extraction_validation_integrity,
        window=extraction_window,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_extraction_validation.add_run_usage(result.usage())
    return result.output


def extract_sfi_candidates(
    *,
    extraction_window: ExtractionWindow,
    kg_config: CreateKGConfig,
    usage_tracker: KGUsageTracker,
) -> SFIExtractionResult:
    """Extract, validate, and if necessary correct SFIs from one source window.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to inspect.
    kg_config
        Country/document-specific KG extraction configuration.
    usage_tracker
        Usage tracker to accumulate extraction and validation token usage.

    Returns
    -------
    SFIExtractionResult
        Final semantic result accepted or corrected by the checker and validated for
        universal integrity.
    """

    prompts = extract_sfi_candidates_from_window(
        extraction_window=extraction_window, kg_config=kg_config
    )
    agent = create_sfi_extraction_agent(
        instructions=prompts.system_message,
        kg_config=kg_config,
        model_config=Settings.llm_config("kgs"),
        verify_integrity_fn=verify_sfi_extraction_integrity,
        window=extraction_window,
    )
    extraction_run = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_extraction.add_run_usage(extraction_run.usage())
    draft_result = extraction_run.output
    validation_verdict = _run_sfi_extraction_validation(
        draft_result=draft_result,
        extraction_window=extraction_window,
        kg_config=kg_config,
        usage_tracker=usage_tracker,
    )

    if validation_verdict.passed:
        final_result = draft_result

        logger.info(
            f"SFI validation accepted extraction window "
            f"{extraction_window.window_index} without correction."
        )
    else:
        corrected_result = validation_verdict.corrected_result
        assert corrected_result is not None
        final_result = corrected_result

        logger.warning(
            f"SFI validation corrected extraction window "
            f"{extraction_window.window_index}: "
            f"issues={len(validation_verdict.issues)}; "
            f"rationale={validation_verdict.rationale[:300]}"
        )

    verify_sfi_extraction_integrity(
        extraction_result=final_result, kg_config=kg_config, window=extraction_window
    )
    return final_result


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
    """Produce, independently validate, and finalize one bounded SFI dedup response.

    Parameters
    ----------
    review_request
        Bounded dedup review request to inspect.
    usage_tracker
        Usage tracker to accumulate producer and checker token usage.

    Returns
    -------
    SFIDedupReviewResponse
        Final accepted or checker-corrected dedup review response.

    Raises
    ------
    ValueError
        If a dedup validation verdict does not contain a corrected response.
    """

    prompts = review_sfi_dedup_candidates(review_request)
    agent = create_sfi_dedup_agent(
        instructions=prompts.system_message,
        model_config=Settings.llm_config("kgs"),
        review_request=review_request,
        verify_integrity_fn=verify_sfi_dedup_review_integrity,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_dedup.add_run_usage(result.usage())
    draft_response = result.output

    validation_verdict = _run_sfi_dedup_validation(
        draft_response=draft_response,
        review_request=review_request,
        usage_tracker=usage_tracker,
    )

    if validation_verdict.passed:
        final_response = draft_response
    else:
        corrected_response = validation_verdict.corrected_response

        if corrected_response is None:
            raise ValueError(
                "A failing SFI dedup validation verdict must include a corrected "
                "response."
            )

        final_response = corrected_response

        logger.warning(
            f"SFI dedup checker corrected review set "
            f"{review_request.review_set_id}: {validation_verdict.rationale}"
        )

    verify_sfi_dedup_review_integrity(
        review_request=review_request, review_response=final_response
    )
    return final_response
