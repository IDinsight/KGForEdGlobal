"""This module contains Agent definitions for the knowledge graph pipeline."""

# Standard Library
from typing import Callable

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, ModelRetry

# Package Library
from skg.kgs.schemas import (
    ExtractionWindow,
    LCDedupRequest,
    LCDedupResponse,
    LCGenerationRequest,
    LCGenerationResponse,
    SFIDedupReviewRequest,
    SFIDedupReviewResponse,
    SFIDedupValidationVerdict,
    SFIExtractionResult,
    SFIExtractionValidationVerdict,
    SFIHasChildResolutionRequest,
    SFIHasChildResolutionResponse,
    SFIHasChildValidationVerdict,
)
from skg.model_registry import ModelConfig
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig, _CreateKGLearningComponentsConfig


def create_lc_dedup_agent(
    *,
    instructions: str,
    lc_dedup_request: LCDedupRequest,
    max_retries: int = 3,
    model_config: ModelConfig,
    verify_quality_fn: Callable[..., None],
) -> Agent:
    """Create an Agent configured for LC duplicate-pair adjudication.

    The returned agent validates parsed LLM output against the supplied bounded
    adjudication request. Quality failures raise `ModelRetry` so pydantic-ai
    can ask the model to repair its structured response before duplicate
    groups are clustered.

    Parameters
    ----------
    instructions
        System-level duplicate-adjudication instructions.
    lc_dedup_request
        Bounded adjudication request being processed.
    max_retries
        Maximum number of quality-error retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    verify_quality_fn
        Callable with signature `(*, lc_dedup_request, lc_dedup_response)`
        that raises `QualityError` on failure.

    Returns
    -------
    Agent
        Configured LC dedup adjudication agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("learning_components"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(LCDedupResponse),
    )

    @agent.output_validator
    def validate_lc_dedup_quality(output: LCDedupResponse) -> LCDedupResponse:
        """Validate parsed LC duplicate-pair adjudication output.

        Parameters
        ----------
        output
            Parsed pair-verdict response from the model.

        Returns
        -------
        LCDedupResponse
            Validated pair-verdict response.

        Raises
        ------
        ModelRetry
            If output fails quality checks and should be corrected by the model.
        """

        attempt = attempt_counter["value"]

        try:
            verify_quality_fn(
                lc_dedup_request=lc_dedup_request, lc_dedup_response=output
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"LC dedup quality check failed for request "
                f"{lc_dedup_request.request_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured LC dedup output has quality issues and must "
                f"be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete LCDedupResponse that covers every pair in "
                f"the request exactly once, with a concise reason per verdict."
            ) from e

        attempt_counter["value"] += 1

        return output

    return agent


def create_lc_generation_agent(
    *,
    instructions: str,
    lc_config: _CreateKGLearningComponentsConfig,
    lc_generation_request: LCGenerationRequest,
    max_retries: int = 3,
    model_config: ModelConfig,
    verify_quality_fn: Callable[..., None],
) -> Agent:
    """Create an Agent configured for LC atomic-skill decomposition.

    The returned agent validates parsed LLM output against the supplied bounded
    LC generation request. Quality failures raise `ModelRetry` so pydantic-ai
    can ask the model to repair its structured response before
    LearningComponents are minted.

    Parameters
    ----------
    instructions
        System-level LC decomposition instructions.
    lc_config
        Learning Components runtime configuration (skill count/length knobs).
    lc_generation_request
        Bounded LC generation request being processed.
    max_retries
        Maximum number of quality-error retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    verify_quality_fn
        Callable with signature `(*, lc_config, lc_generation_request,
        lc_generation_response)` that raises `QualityError` on failure.

    Returns
    -------
    Agent
        Configured LC generation agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("learning_components"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(LCGenerationResponse),
    )

    @agent.output_validator
    def validate_lc_generation_quality(
        output: LCGenerationResponse,
    ) -> LCGenerationResponse:
        """Validate parsed LC atomic-skill decomposition output.

        Parameters
        ----------
        output
            Parsed atomic-skills response from the model.

        Returns
        -------
        LCGenerationResponse
            Validated atomic-skills response.

        Raises
        ------
        ModelRetry
            If output fails quality checks and should be corrected by the model.
        """

        attempt = attempt_counter["value"]

        try:
            verify_quality_fn(
                lc_config=lc_config,
                lc_generation_request=lc_generation_request,
                lc_generation_response=output,
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"LC generation quality check failed for request "
                f"{lc_generation_request.request_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured LC generation output has quality issues and "
                f"must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete LCGenerationResponse that covers every SFI "
                f"in the request exactly once, with non-empty atomic skills "
                f"directly supported by each SFI's text."
            ) from e

        attempt_counter["value"] += 1

        return output

    return agent


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
    """Create an integrity-checked SFI extraction producer agent.

    The producer LLM owns curriculum-semantic decisions. Its parsed result is returned
    unchanged after Python verifies only exact source references, runtime-configured
    contracts, and cross-object integrity. Integrity failures raise `ModelRetry` so the
    producer can repair malformed structured output without Python rewriting semantics.

    Parameters
    ----------
    instructions
        System-level SFI extraction instructions.
    kg_config
        Runtime KG configuration used for deterministic contract validation.
    max_retries
        Maximum number of structured-output or integrity retries.
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
        Configured SFI extraction producer agent.
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
    def validate_sfi_extraction_integrity(
        output: SFIExtractionResult,
    ) -> SFIExtractionResult:
        """Validate universal integrity without rewriting producer semantics.

        Parameters
        ----------
        output
            Parsed SFI extraction result from the producer LLM.

        Returns
        -------
        SFIExtractionResult
            Unmodified integrity-validated extraction result.

        Raises
        ------
        ModelRetry
            If the result violates a universal integrity constraint.
        """

        attempt = attempt_counter["value"]

        try:
            verify_integrity_fn(
                extraction_result=output, kg_config=kg_config, window=window
            )
        except QualityError as exc:
            truncated_message = str(exc)[:500]

            logger.error(
                f"SFI extraction integrity check failed for window "
                f"{window.window_index} attempt {attempt + 1}: "
                f"{truncated_message}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured SFI extraction output violates a universal "
                f"integrity rule and must be corrected.\n"
                f"ERROR: {str(exc)}\n\n"
                f"Return a complete SFIExtractionResult that fixes the reference or "
                f"configuration-contract error without changing source semantics "
                f"unless the source evidence requires it."
            ) from exc

        attempt_counter["value"] += 1
        return output

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
    """Create an integrity-checked SFI extraction checker agent.

    The checker independently accepts or corrects producer semantics. Python returns
    the verdict unchanged after validating only exact references, configured scope and
    type contracts, verdict consistency, and cross-object integrity.

    Parameters
    ----------
    draft_result
        First-stage extraction result reviewed by the checker.
    instructions
        System-level SFI validation instructions.
    kg_config
        Runtime KG configuration used for deterministic contract validation.
    max_retries
        Maximum number of structured-output or integrity retries.
    model_config
        Model configuration containing the model name and settings helpers.
    verify_integrity_fn
        Callable with signature
        `(*, draft_result, kg_config, validation_verdict, window)` that raises
        `QualityError` when a universal integrity constraint fails.
    window
        Source extraction window reviewed by both LLM stages.

    Returns
    -------
    Agent
        Configured SFI extraction checker agent.
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
        """Validate checker-verdict integrity without rewriting its correction.

        Parameters
        ----------
        output
            Parsed validation verdict from the checker LLM.

        Returns
        -------
        SFIExtractionValidationVerdict
            Unmodified integrity-validated checker verdict.

        Raises
        ------
        ModelRetry
            If the verdict or selected extraction result violates universal integrity.
        """

        attempt = attempt_counter["value"]

        try:
            verify_integrity_fn(
                draft_result=draft_result,
                kg_config=kg_config,
                validation_verdict=output,
                window=window,
            )
        except QualityError as exc:
            truncated_message = str(exc)[:500]

            logger.error(
                f"SFI validation integrity check failed for window "
                f"{window.window_index} attempt {attempt + 1}: "
                f"{truncated_message}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your SFI validation output violates a universal integrity rule and "
                f"must be corrected.\n"
                f"ERROR: {str(exc)}\n\n"
                f"Return a complete SFIExtractionValidationVerdict. When "
                f"passed=false, corrected_result must be a complete source-grounded "
                f"SFIExtractionResult with exact window identity, configured scope "
                f"contracts, and valid references."
            ) from exc

        attempt_counter["value"] += 1
        return output

    return agent


def create_sfi_has_child_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    resolution_request: SFIHasChildResolutionRequest,
    verify_integrity_fn: Callable[..., None],
) -> Agent:
    """Create an integrity-checked hasChild parent-selection producer agent.

    The producer owns the semantic direct-parent decision within each supplied bounded
    candidate set. Python validates only universal response identity, child coverage,
    candidate membership, selection shape, and self-loop constraints.

    Parameters
    ----------
    instructions
        System-level hasChild parent-selection instructions.
    max_retries
        Maximum number of universal-integrity retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    resolution_request
        Bounded hasChild parent-selection request being processed.
    verify_integrity_fn
        Callable with signature `(*, resolution_request, resolution_response)` that
        raises `QualityError` when a universal integrity constraint fails.

    Returns
    -------
    Agent
        Configured hasChild producer agent.
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
    def validate_sfi_has_child_integrity(
        output: SFIHasChildResolutionResponse,
    ) -> SFIHasChildResolutionResponse:
        """Validate universal integrity of a producer hasChild response.

        Parameters
        ----------
        output
            Parsed producer response.

        Returns
        -------
        SFIHasChildResolutionResponse
            Integrity-validated producer response.

        Raises
        ------
        ModelRetry
            If the response violates a universal integrity rule.
        """

        attempt = attempt_counter["value"]

        try:
            verify_integrity_fn(
                resolution_request=resolution_request, resolution_response=output
            )
        except QualityError as exc:
            truncated_msg = str(exc)[:500]

            logger.error(
                f"SFI hasChild producer integrity check failed for request "
                f"{resolution_request.request_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured SFI hasChild response violates a universal "
                f"integrity rule and must be corrected.\n"
                f"ERROR: {str(exc)}\n\n"
                f"Return a complete SFIHasChildResolutionResponse that copies the "
                f"exact request_id, covers every child exactly once, and selects "
                f"only endpoints from each child's supplied candidate set."
            ) from exc

        attempt_counter["value"] += 1
        return output

    return agent


def create_sfi_has_child_validation_agent(
    *,
    draft_response: SFIHasChildResolutionResponse,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    resolution_request: SFIHasChildResolutionRequest,
    verify_integrity_fn: Callable[..., None],
) -> Agent:
    """Create an independent checker for one draft hasChild response.

    The checker receives the original bounded request and the producer's complete
    draft. It independently applies generic semantic review guidance and runtime
    validation instructions, then accepts the draft or returns a complete corrected
    response. Python validates only the universal verdict and response contracts.

    Parameters
    ----------
    draft_response
        Producer response to audit.
    instructions
        System-level checker instructions.
    max_retries
        Maximum number of universal-integrity retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    resolution_request
        Original bounded hasChild request.
    verify_integrity_fn
        Callable with signature
        `(*, draft_response, resolution_request, validation_verdict)` that raises
        `QualityError` when a universal integrity constraint fails.

    Returns
    -------
    Agent
        Configured independent hasChild checker agent.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("sfi_has_child"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(SFIHasChildValidationVerdict),
    )

    @agent.output_validator
    def validate_sfi_has_child_validation_integrity(
        output: SFIHasChildValidationVerdict,
    ) -> SFIHasChildValidationVerdict:
        """Validate universal integrity of a hasChild checker verdict.

        Parameters
        ----------
        output
            Parsed checker verdict.

        Returns
        -------
        SFIHasChildValidationVerdict
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
                resolution_request=resolution_request,
                validation_verdict=output,
            )
        except QualityError as exc:
            truncated_msg = str(exc)[:500]

            logger.error(
                f"SFI hasChild checker integrity check failed for request "
                f"{resolution_request.request_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your SFI hasChild checker verdict violates a universal integrity "
                f"rule and must be corrected.\n"
                f"ERROR: {str(exc)}\n\n"
                f"Return a complete SFIHasChildValidationVerdict. When passed=false, "
                f"corrected_response must be a complete response that copies the "
                f"exact request_id, covers every child exactly once, and selects "
                f"only supplied parent endpoints."
            ) from exc

        attempt_counter["value"] += 1
        return output

    return agent
