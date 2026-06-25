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
    SFIExtractionResult,
)
from skg.model_registry import ModelConfig
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig


def create_sfi_dedup_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    review_request: SFIDedupReviewRequest,
    verify_quality_fn: Callable[..., None],
) -> Agent:
    """Create an Agent configured for bounded SFI deduplication review.

    The returned agent validates parsed output against the supplied review request.
    Quality failures raise `ModelRetry` so pydantic-ai can ask the model to repair
    the structured response before converting decisions into merge groups.

    Parameters
    ----------
    instructions
        System-level SFI deduplication instructions.
    max_retries
        Maximum number of quality-error retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    review_request
        Bounded review set being processed.
    verify_quality_fn
        Callable with signature `(*, review_request, review_response)` that raises
        `QualityError` on failure.

    Returns
    -------
    Agent
        Configured SFI dedup review agent.
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
    def validate_sfi_dedup_quality(
        output: SFIDedupReviewResponse,
    ) -> SFIDedupReviewResponse:
        """Validate parsed SFI dedup review output.

        Parameters
        ----------
        output
            Parsed SFI dedup review response from the model.

        Returns
        -------
        SFIDedupReviewResponse
            Validated dedup review response.

        Raises
        ------
        ModelRetry
            If output fails quality checks and should be corrected by the model.
        """

        attempt = attempt_counter["value"]

        try:
            verify_quality_fn(review_request=review_request, review_response=output)
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"SFI dedup quality check failed for review set "
                f"{review_request.review_set_id} attempt {attempt + 1}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured SFI dedup output has quality issues and must be "
                f"corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete SFIDedupReviewResponse that covers every input "
                f"candidate exactly once and fixes the issue."
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
    verify_quality_fn: Callable[..., None],
    window: ExtractionWindow,
) -> Agent:
    """Create an Agent configured for SFI candidate extraction.

    The returned agent validates parsed output with Python quality checks. Quality
    failures raise ModelRetry so pydantic-ai can ask the model to repair its own
    structured response.

    Parameters
    ----------
    instructions
        System-level SFI extraction instructions.
    kg_config
        Runtime KG configuration used for document-specific validation.
    max_retries
        Maximum number of quality-error retries.
    model_config
        Model configuration containing the model name and model settings helpers.
    verify_quality_fn
        Callable with signature `(*, extraction_result, kg_config, window)` that raises
        `QualityError` on failure.
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
        """Validate parsed SFI extraction output.

        Parameters
        ----------
        output
            Parsed SFI extraction result from the model.

        Returns
        -------
        SFIExtractionResult
            Validated extraction result.

        Raises
        ------
        ModelRetry
            If output fails quality checks and should be corrected by the model.
        """

        attempt = attempt_counter["value"]

        try:
            verify_quality_fn(
                extraction_result=output, kg_config=kg_config, window=window
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"SFI extraction quality check failed for window "
                f"{window.window_index} attempt {attempt + 1}: {truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your structured SFI extraction output has quality issues and must "
                f"be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete SFIExtractionResult that fixes the issue while "
                f"preserving source fidelity."
            ) from e

        attempt_counter["value"] += 1
        return output

    return agent
