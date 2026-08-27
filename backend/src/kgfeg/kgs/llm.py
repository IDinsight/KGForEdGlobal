"""This module contains the LLM orchestration for Academic Standards knowledge graph
creation.

SFI extraction, SFI deduplication, and hasChild resolution use two-stage
producer/checker flows. Each producer returns a complete draft result, and an
independent validation agent reviews the same bounded evidence, accepts the draft, or
returns a complete corrected result.
"""

# Standard Library
from dataclasses import dataclass
from typing import Optional

# Third Party Library
from loguru import logger

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs.agents import (
    create_lc_dedup_agent,
    create_lc_generation_agent,
    create_lc_generation_validation_agent,
    create_sfi_dedup_agent,
    create_sfi_dedup_validation_agent,
    create_sfi_extraction_agent,
    create_sfi_extraction_validation_agent,
    create_sfi_has_child_agent,
    create_sfi_has_child_validation_agent,
)
from kgfeg.kgs.prompts import (
    build_lc_dedup_prompt,
    build_lc_generation_prompt,
    extract_sfi_candidates_from_window,
    resolve_sfi_has_child_parents,
    review_sfi_dedup_candidates,
    validate_lc_generation_response,
    validate_sfi_dedup_response,
    validate_sfi_extraction_result,
    validate_sfi_has_child_response,
)
from kgfeg.kgs.schemas import (
    ExtractionWindow,
    LCDedupRequest,
    LCDedupResponse,
    LCGenerationRequest,
    LCGenerationResponse,
    LCGenerationValidationVerdict,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIDedupValidationVerdict,
    SFIExtractionResult,
    SFIExtractionValidationVerdict,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
    SFIHasChildValidationVerdict,
)
from kgfeg.kgs.validators import (
    verify_lc_dedup_quality,
    verify_lc_generation_quality,
    verify_lc_generation_validation_integrity,
    verify_sfi_dedup_review_integrity,
    verify_sfi_dedup_validation_integrity,
    verify_sfi_extraction_integrity,
    verify_sfi_extraction_validation_integrity,
    verify_sfi_has_child_resolution_integrity,
    verify_sfi_has_child_validation_integrity,
)
from kgfeg.schemas import CreateKGConfig, _CreateKGLearningComponentsConfig
from kgfeg.utils.general import AgentUsageBucket


@dataclass(frozen=True)
class LCGenerationRun:
    """Producer/validator outputs for one bounded LC generation request."""

    draft_response: LCGenerationResponse
    final_response: LCGenerationResponse
    validation_verdict: LCGenerationValidationVerdict


@dataclass(frozen=True)
class SFIHasChildResolutionRun:
    """Producer/checker outputs for one bounded hasChild request."""

    draft_response: SFIHasChildResolutionResponse
    final_response: SFIHasChildResolutionResponse
    validation_verdict: SFIHasChildValidationVerdict


@dataclass
class KGUsageTracker:
    """Track LLM token usage for the KG pipeline."""

    lc_dedup: AgentUsageBucket
    lc_generation: AgentUsageBucket
    lc_generation_validation: AgentUsageBucket
    sfi_dedup: AgentUsageBucket
    sfi_dedup_validation: AgentUsageBucket
    sfi_extraction: AgentUsageBucket
    sfi_extraction_validation: AgentUsageBucket
    sfi_has_child: AgentUsageBucket
    sfi_has_child_validation: AgentUsageBucket

    def __init__(self) -> None:
        """Initialize empty usage buckets for KG LLM agents."""

        self.lc_dedup = AgentUsageBucket(agent_name="lc_dedup")
        self.lc_generation = AgentUsageBucket(agent_name="lc_generation")
        self.lc_generation_validation = AgentUsageBucket(
            agent_name="lc_generation_validation"
        )
        self.sfi_dedup = AgentUsageBucket(agent_name="sfi_dedup")
        self.sfi_dedup_validation = AgentUsageBucket(agent_name="sfi_dedup_validation")
        self.sfi_extraction = AgentUsageBucket(agent_name="sfi_extraction")
        self.sfi_extraction_validation = AgentUsageBucket(
            agent_name="sfi_extraction_validation"
        )
        self.sfi_has_child = AgentUsageBucket(agent_name="sfi_has_child")
        self.sfi_has_child_validation = AgentUsageBucket(
            agent_name="sfi_has_child_validation"
        )

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
            "lc_generation_validation": self.lc_generation_validation,
            "sfi_dedup": self.sfi_dedup,
            "sfi_dedup_validation": self.sfi_dedup_validation,
            "sfi_extraction": self.sfi_extraction,
            "sfi_extraction_validation": self.sfi_extraction_validation,
            "sfi_has_child": self.sfi_has_child,
            "sfi_has_child_validation": self.sfi_has_child_validation,
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


def _run_lc_generation_validation(
    *,
    draft_response: LCGenerationResponse,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_generation_request: LCGenerationRequest,
    usage_tracker: KGUsageTracker,
) -> LCGenerationValidationVerdict:
    """Audit one draft LC generation response with the independent validator.

    Parameters
    ----------
    draft_response
        Producer response to audit.
    lc_config
        Learning Components runtime configuration.
    lc_generation_request
        Original bounded LC generation request.
    usage_tracker
        Usage tracker for validator token accounting.

    Returns
    -------
    LCGenerationValidationVerdict
        Parsed and integrity-validated verdict.
    """

    prompts = validate_lc_generation_response(
        draft_response=draft_response,
        generation_instructions=lc_config.generation_instructions,
        lc_generation_request=lc_generation_request,
        lc_generation_validation_instructions=(
            lc_config.lc_generation_validation_instructions
        ),
    )
    agent = create_lc_generation_validation_agent(
        draft_response=draft_response,
        instructions=prompts.system_message,
        lc_config=lc_config,
        lc_generation_request=lc_generation_request,
        model_config=Settings.llm_config("kgs"),
        verify_integrity_fn=verify_lc_generation_validation_integrity,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.lc_generation_validation.add_run_usage(result.usage())
    return result.output


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


def _run_sfi_has_child_validation(
    *,
    draft_response: SFIHasChildResolutionResponse,
    resolution_request: SFIHasChildResolutionRequest,
    usage_tracker: KGUsageTracker,
) -> SFIHasChildValidationVerdict:
    """Run the independent checker for one draft hasChild response.

    Parameters
    ----------
    draft_response
        Producer response to audit.
    resolution_request
        Original bounded hasChild request.
    usage_tracker
        Usage tracker for checker token accounting.

    Returns
    -------
    SFIHasChildValidationVerdict
        Parsed and integrity-validated checker verdict.
    """

    prompts = validate_sfi_has_child_response(
        draft_response=draft_response, resolution_request=resolution_request
    )
    agent = create_sfi_has_child_validation_agent(
        draft_response=draft_response,
        instructions=prompts.system_message,
        model_config=Settings.llm_config("kgs"),
        resolution_request=resolution_request,
        verify_integrity_fn=verify_sfi_has_child_validation_integrity,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_has_child_validation.add_run_usage(result.usage())
    return result.output


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


def generate_learning_components_for_request(
    *,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_generation_request: LCGenerationRequest,
    usage_tracker: KGUsageTracker,
) -> LCGenerationRun:
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
    LCGenerationRun
        Producer draft, validation verdict, and accepted or corrected final
        response.

    Raises
    ------
    ValueError
        If a failing validation verdict omits its complete corrected response.
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
    producer_run = agent.run_sync(prompts.user_message)
    usage_tracker.lc_generation.add_run_usage(producer_run.usage())
    draft_response = producer_run.output

    validation_verdict = _run_lc_generation_validation(
        draft_response=draft_response,
        lc_config=lc_config,
        lc_generation_request=lc_generation_request,
        usage_tracker=usage_tracker,
    )
    if validation_verdict.passed:
        final_response = draft_response
    else:
        corrected_response = validation_verdict.corrected_response
        if corrected_response is None:
            raise ValueError(
                f"A failing LC generation validation verdict must include a "
                f"complete corrected response; request "
                f"{lc_generation_request.request_id!r} returned none."
            )
        final_response = corrected_response
        logger.info(
            f"LC generation validator corrected request "
            f"{lc_generation_request.request_id}: {validation_verdict.rationale}"
        )

    return LCGenerationRun(
        draft_response=draft_response,
        final_response=final_response,
        validation_verdict=validation_verdict,
    )


def resolve_sfi_has_child_parent_request(
    *, resolution_request: SFIHasChildResolutionRequest, usage_tracker: KGUsageTracker
) -> SFIHasChildResolutionRun:
    """Produce, independently validate, and finalize one hasChild response.

    Parameters
    ----------
    resolution_request
        Bounded hasChild parent-selection request.
    usage_tracker
        Usage tracker for producer and checker token accounting.

    Returns
    -------
    SFIHasChildResolutionRun
        Producer draft, checker verdict, and accepted or corrected final response.

    Raises
    ------
    ValueError
        If a failing checker verdict omits its complete corrected response.
    """

    prompts = resolve_sfi_has_child_parents(resolution_request)
    agent = create_sfi_has_child_agent(
        instructions=prompts.system_message,
        model_config=Settings.llm_config("kgs"),
        resolution_request=resolution_request,
        verify_integrity_fn=verify_sfi_has_child_resolution_integrity,
    )
    producer_run = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_has_child.add_run_usage(producer_run.usage())
    draft_response = producer_run.output
    validation_verdict = _run_sfi_has_child_validation(
        draft_response=draft_response,
        resolution_request=resolution_request,
        usage_tracker=usage_tracker,
    )

    if validation_verdict.passed:
        final_response = draft_response
    else:
        corrected_response = validation_verdict.corrected_response

        if corrected_response is None:
            raise ValueError(
                "A failing hasChild validation verdict must include a complete "
                "corrected response."
            )

        final_response = corrected_response

        logger.warning(
            f"SFI hasChild checker corrected request "
            f"{resolution_request.request_id}: {validation_verdict.rationale}"
        )

    verify_sfi_has_child_resolution_integrity(
        resolution_request=resolution_request, resolution_response=final_response
    )
    return SFIHasChildResolutionRun(
        draft_response=draft_response,
        final_response=final_response,
        validation_verdict=validation_verdict,
    )


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
