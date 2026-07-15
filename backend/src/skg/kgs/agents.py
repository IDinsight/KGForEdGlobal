"""This module contains Agent definitions for the knowledge graph pipeline."""

# Standard Library
from typing import Callable

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, ModelRetry

# Package Library
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
from skg.kgs.validators import canonicalize_sfi_extraction_result
from skg.model_registry import ModelConfig
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig


def create_sfi_dedup_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    review_request: SFIDedupReviewRequest,
    verify_integrity_fn: Callable[..., None],
) -> Agent:
    """Create an Agent configured for bounded SFI deduplication review.

    The producer agent returns a complete draft dedup response. Python validates only
    universal response integrity, such as review-set identity and exact candidate
    coverage. Curriculum-specific semantic review is delegated to a separate checker
    LLM.

    Parameters
    ----------
    instructions
        System-level SFI deduplication instructions.
    max_retries
        Maximum number of universal-integrity retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    review_request
        Bounded review set being processed.
    verify_integrity_fn
        Callable with signature `(*, review_request, review_response)` that raises
        `QualityError` when a universal integrity constraint fails.

    Returns
    -------
    Agent
        Configured SFI dedup producer agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("sfi_dedup"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(SFIDedupReviewResponse),
    )

    @agent.output_validator
    def validate_sfi_dedup_integrity(
        output: SFIDedupReviewResponse,
    ) -> SFIDedupReviewResponse:
        """Validate universal integrity of a draft SFI dedup response.

        Parameters
        ----------
        output
            Parsed SFI dedup review response from the producer LLM.

        Returns
        -------
        SFIDedupReviewResponse
            Integrity-validated draft dedup response.

        Raises
        ------
        ModelRetry
            If the draft violates a universal response-integrity rule.
        """

        attempt = attempt_counter["value"]

        try:
            verify_integrity_fn(review_request=review_request, review_response=output)
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"SFI dedup integrity check failed for review set "
                f"{review_request.review_set_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured SFI dedup output violates a universal integrity "
                f"rule and must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete SFIDedupReviewResponse that uses the exact "
                f"review_set_id and covers every input candidate exactly once."
            ) from e

        attempt_counter["value"] += 1
        return output

    return agent


def create_sfi_dedup_validation_agent(
    *,
    draft_response: SFIDedupReviewResponse,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    review_request: SFIDedupReviewRequest,
    verify_integrity_fn: Callable[..., None],
) -> Agent:
    """Create an Agent that reviews and corrects a draft SFI dedup response.

    The checker receives the original bounded review request and the producer's
    complete draft response. It independently applies generic semantic review guidance
    and the curriculum-specific runtime instructions, then either accepts the draft or
    returns a complete corrected response. Python validates only universal integrity of
    the verdict and selected response.

    Parameters
    ----------
    draft_response
        First-stage SFI dedup response to review.
    instructions
        System-level SFI dedup checker instructions.
    max_retries
        Maximum number of universal-integrity retries.
    model_config
        Model configuration containing the model name and settings helpers.
    review_request
        Original bounded review request reviewed by both LLM stages.
    verify_integrity_fn
        Callable with signature
        `(*, draft_response, review_request, validation_verdict)` that raises
        `QualityError` when a universal integrity constraint fails.

    Returns
    -------
    Agent
        Configured SFI dedup validation agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("sfi_dedup"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(SFIDedupValidationVerdict),
    )

    @agent.output_validator
    def validate_sfi_dedup_validation_integrity(
        output: SFIDedupValidationVerdict,
    ) -> SFIDedupValidationVerdict:
        """Validate universal integrity of a dedup checker verdict.

        Parameters
        ----------
        output
            Parsed validation verdict from the checker LLM.

        Returns
        -------
        SFIDedupValidationVerdict
            Integrity-validated checker verdict.

        Raises
        ------
        ModelRetry
            If the verdict or corrected response violates a universal integrity rule.
        """

        attempt = attempt_counter["value"]

        try:
            verify_integrity_fn(
                draft_response=draft_response,
                review_request=review_request,
                validation_verdict=output,
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"SFI dedup validation integrity check failed for review set "
                f"{review_request.review_set_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your SFI dedup validation output violates a universal integrity "
                f"rule and must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete SFIDedupValidationVerdict. When passed=false, "
                f"corrected_response must be a complete SFIDedupReviewResponse that "
                f"uses the exact review_set_id and covers every candidate exactly "
                f"once."
            ) from e

        attempt_counter["value"] += 1
        return output

    return agent


def create_sfi_extraction_agent(
    *,
    instructions: str,
    kg_config: CreateKGConfig,
    max_retries: int = 3,
    model_config: ModelConfig,
    verify_integrity_fn: Callable[..., None],
    window: ExtractionWindow,
) -> Agent:
    """Create an Agent configured for SFI candidate extraction.

    The returned agent canonicalizes anchor-derived evidence text, candidate source
    order, IDs, and auxiliary references before applying universal Python integrity
    checks. Semantic review and curriculum-specific correction are performed by a
    separate validation LLM after this draft result is produced. Integrity failures
    raise `ModelRetry` so the model can repair malformed or unsupported references.

    Parameters
    ----------
    instructions
        System-level SFI extraction instructions.
    kg_config
        Runtime KG configuration used for universal policy validation.
    max_retries
        Maximum number of integrity-error retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    verify_integrity_fn
        Callable with signature `(*, extraction_result, kg_config, window)` that raises
        `QualityError` when a universal integrity constraint fails.
    window
        Source extraction window being processed.

    Returns
    -------
    Agent
        Configured SFI extraction agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("sfi_extraction"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(SFIExtractionResult),
    )

    @agent.output_validator
    def validate_sfi_extraction_quality(
        output: SFIExtractionResult,
    ) -> SFIExtractionResult:
        """Validate universal integrity of a draft SFI extraction output.

        Parameters
        ----------
        output
            Parsed SFI extraction result from the model.

        Returns
        -------
        SFIExtractionResult
            Integrity-validated draft extraction result.

        Raises
        ------
        ModelRetry
            If output fails universal integrity checks and should be corrected.
        """

        attempt = attempt_counter["value"]

        try:
            canonical_output = canonicalize_sfi_extraction_result(
                extraction_result=output, window=window
            )
            verify_integrity_fn(
                extraction_result=canonical_output, kg_config=kg_config, window=window
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"SFI extraction integrity check failed for window "
                f"{window.window_index} attempt {attempt + 1}: {truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured SFI extraction output violates a universal integrity rule and must "
                f"be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete SFIExtractionResult that fixes the issue while "
                f"preserving source fidelity."
            ) from e

        attempt_counter["value"] += 1
        return canonical_output

    return agent


def create_sfi_extraction_validation_agent(
    *,
    draft_result: SFIExtractionResult,
    instructions: str,
    kg_config: CreateKGConfig,
    max_retries: int = 3,
    model_config: ModelConfig,
    verify_integrity_fn: Callable[..., None],
    window: ExtractionWindow,
) -> Agent:
    """Create an Agent that reviews and corrects a draft SFI extraction result.

    The validation agent receives the same compact source window as the extraction
    agent plus the complete draft result. It applies generic checker instructions and
    curriculum-specific runtime guidance, then either accepts the draft or returns a
    complete corrected `SFIExtractionResult`. Python canonicalizes any corrected result
    from its exact anchors before validating universal integrity constraints.

    Parameters
    ----------
    draft_result
        First-stage SFI extraction result to review.
    instructions
        System-level SFI validation instructions.
    kg_config
        Runtime KG configuration used for universal integrity validation.
    max_retries
        Maximum number of integrity-error retries.
    model_config
        Model configuration containing the model name and settings helpers.
    verify_integrity_fn
        Callable with signature
        `(*, draft_result, kg_config, validation_verdict, window)` that raises
        `QualityError` on failure.
    window
        Source extraction window reviewed by both LLM stages.

    Returns
    -------
    Agent
        Configured SFI extraction validation agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("sfi_extraction"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(SFIExtractionValidationVerdict),
    )

    @agent.output_validator
    def validate_sfi_extraction_validation_integrity(
        output: SFIExtractionValidationVerdict,
    ) -> SFIExtractionValidationVerdict:
        """Validate universal integrity of a validation verdict and correction.

        Parameters
        ----------
        output
            Parsed validation verdict from the checker LLM.

        Returns
        -------
        SFIExtractionValidationVerdict
            Integrity-validated validation verdict.

        Raises
        ------
        ModelRetry
            If the verdict or corrected result violates a universal integrity rule.
        """

        attempt = attempt_counter["value"]

        try:
            canonical_output = output

            if output.corrected_result is not None:
                canonical_corrected_result = canonicalize_sfi_extraction_result(
                    extraction_result=output.corrected_result, window=window
                )
                canonical_output = output.model_copy(
                    deep=True, update={"corrected_result": canonical_corrected_result}
                )

            verify_integrity_fn(
                draft_result=draft_result,
                kg_config=kg_config,
                validation_verdict=canonical_output,
                window=window,
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"SFI validation integrity check failed for window "
                f"{window.window_index} attempt {attempt + 1}: {truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your SFI validation output violates a universal integrity rule and "
                f"must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete SFIExtractionValidationVerdict. When passed=false, "
                f"corrected_result must be a complete source-grounded "
                f"SFIExtractionResult with exact window identity and valid references."
            ) from e

        attempt_counter["value"] += 1
        return canonical_output

    return agent


def create_sfi_has_child_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    resolution_request: SFIHasChildResolutionRequest,
    verify_quality_fn: Callable[..., None],
) -> Agent:
    """Create an Agent configured for `hasChild` parent selection.

    The returned agent validates parsed LLM output against the supplied bounded
    parent-selection request. Quality failures raise `ModelRetry` so pydantic-ai can
    ask the model to repair its structured response before final hasChild edges are
    minted.

    Parameters
    ----------
    instructions
        System-level hasChild parent-selection instructions.
    max_retries
        Maximum number of quality-error retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    resolution_request
        Bounded hasChild parent-selection request being processed.
    verify_quality_fn
        Callable with signature `(*, resolution_request, resolution_response)` that
        raises `QualityError` on failure.

    Returns
    -------
    Agent
        Configured hasChild parent-selection agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("sfi_has_child"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(SFIHasChildResolutionResponse),
    )

    @agent.output_validator
    def validate_sfi_has_child_quality(
        output: SFIHasChildResolutionResponse,
    ) -> SFIHasChildResolutionResponse:
        """Validate parsed hasChild parent-selection output.

        Parameters
        ----------
        output
            Parsed hasChild resolution response from the model.

        Returns
        -------
        SFIHasChildResolutionResponse
            Validated hasChild resolution response.

        Raises
        ------
        ModelRetry
            If output fails quality checks and should be corrected by the model.
        """

        attempt = attempt_counter["value"]

        try:
            verify_quality_fn(
                resolution_request=resolution_request, resolution_response=output
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"SFI hasChild quality check failed for request "
                f"{resolution_request.request_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured SFI hasChild output has quality issues and must "
                f"be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete SFIHasChildResolutionResponse that covers every "
                f"child exactly once and only selects parents from the provided "
                f"candidate sets."
            ) from e

        attempt_counter["value"] += 1
        return output

    return agent
