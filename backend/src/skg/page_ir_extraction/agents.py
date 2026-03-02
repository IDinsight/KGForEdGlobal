"""This module contains Agent definitions for page IR extraction and validation.

The extraction Agent is constructed per-call via a factory function because the output
validator closure captures page-specific context (image dimensions, attempt counter,
artifact persistence directory).

The validation Agent is simpler. It receives a PageIR JSON and the source image and
returns a structured ValidationVerdict. It is also constructed per-call to ensure a
fresh conversation history.
"""

# Standard Library
from pathlib import Path
from typing import Callable

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, ModelRetry, ModelSettings

# Package Library
from skg.page_ir_extraction.schemas import PageIR, ValidationVerdict
from skg.page_ir_extraction.utils import persist_page_ir_attempt_artifacts
from skg.page_ir_extraction.validators import QualityError

DEFAULT_MODEL_SETTINGS = ModelSettings(temperature=0, top_p=1)
DEFAULT_OUTPUT_RETRIES = 3
DEFAULT_VALIDATION_RETRIES = 1


def create_page_ir_extraction_agent(
    *,
    image_height: int,
    image_width: int,
    instructions: str,
    max_retries: int = DEFAULT_OUTPUT_RETRIES,
    model: str,
    page_index: int,
    raw_page_irs_dir: Path,
    verify_quality_fn: Callable,
) -> tuple[Agent, dict[str, int]]:
    """Create an Agent configured for page IR extraction.

    The returned agent has an output validator that runs quality checks and raises
    ModelRetry on failure, letting pydantic-ai handle the correction loop.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    instructions
        System-level extraction instructions.
    max_retries
        Maximum number of quality-error retries (correction turns).
    model
        The model identifier (e.g., 'openai:gpt-5.2-2025-12-11').
    page_index
        The 0-based page index.
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.
    verify_quality_fn
        Callable with signature `(*, attempt, image_height, image_width, page_ir)`
        that raises `QualityError` on failure. Injected to avoid circular imports
        between agents.py and llm.py.

    Returns
    -------
    tuple[Agent, dict[str, int]]
        The configured Agent and a mutable attempt counter dict (key `"value"`). The
        counter is shared with the output validator closure so the caller can inspect
        how many attempts were made.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model,
        instructions=instructions,
        model_settings=DEFAULT_MODEL_SETTINGS,
        output_retries=max_retries,
        output_type=PageIR,
    )

    @agent.output_validator
    def validate_page_ir_quality(output: PageIR) -> PageIR:
        """Validate quality of the parsed PageIR.

        Runs the injected quality-verification function. On failure, persists artifacts
        and raises ModelRetry so pydantic-ai appends the error to the conversation and
        retries.

        Parameters
        ----------
        output
            The parsed PageIR output from the model to validate.

        Returns
        -------
        PageIR
            The validated PageIR (same as input if validation passes).
        """

        attempt = attempt_counter["value"]

        # Populate fields that Python fills post-extraction.
        output.image_width = image_width
        output.image_height = image_height
        output.page_index = page_index

        try:
            verify_quality_fn(
                attempt=attempt,
                image_height=image_height,
                image_width=image_width,
                page_ir=output,
            )
        except QualityError as e:
            persist_page_ir_attempt_artifacts(
                attempt=attempt,
                error=e,
                model=model,
                output_text=None,
                page_index=page_index,
                parsed=output,
                raw_page_irs_dir=raw_page_irs_dir,
            )
            truncated_msg = str(e)[:500]

            logger.error(
                f"Quality check failed on page {page_index}, attempt {attempt}: {truncated_msg}"
            )

            attempt_counter["value"] += 1

            raise ModelRetry(
                f"Your output had quality issues and must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete PageIR that matches the schema and fixes "
                f"the issue."
            ) from e

        # Success: persist and increment.
        persist_page_ir_attempt_artifacts(
            attempt=attempt,
            error=None,
            model=model,
            output_text=None,
            page_index=page_index,
            parsed=output,
            raw_page_irs_dir=raw_page_irs_dir,
        )
        attempt_counter["value"] += 1

        return output

    return agent, attempt_counter


def create_page_ir_validation_agent(
    *, instructions: str, model: str, max_retries: int = DEFAULT_VALIDATION_RETRIES
) -> Agent:
    """Create an Agent configured for validating an extracted PageIR against a source
    page image.

    The validation agent receives a PageIR JSON and the source PNG, then returns a
    structured ValidationVerdict indicating whether the extraction is faithful. Each
    call creates a fresh agent with no conversation history.

    Parameters
    ----------
    instructions
        System-level validation instructions.
    model
        The model identifier (e.g., 'openai:gpt-5.2-2025-12-11').
    max_retries
        Maximum number of retries if the verdict itself fails structural checks.

    Returns
    -------
    Agent
        The configured validation Agent with output type ValidationVerdict.
    """

    agent = Agent(
        model,
        instructions=instructions,
        model_settings=DEFAULT_MODEL_SETTINGS,
        output_retries=max_retries,
        output_type=ValidationVerdict,
    )

    @agent.output_validator
    def validate_verdict_consistency(output: ValidationVerdict) -> ValidationVerdict:
        """Validate structural consistency of the validation verdict.

        Enforces that failing verdicts include actionable error-severity issues and
        that the rationale is substantive. These checks complement the Pydantic
        model validators on ValidationVerdict by providing LLM-friendly correction
        messages via ModelRetry.

        Parameters
        ----------
        output
            The parsed ValidationVerdict from the model.

        Returns
        -------
        ValidationVerdict
            The validated verdict (same as input if checks pass).

        Raises
        ------
        ModelRetry
            If the verdict fails consistency checks, with a message guiding the model
            to correct the output. This triggers a retry with the error appended to the
            conversation history.
        """

        if not output.rationale or not output.rationale.strip():
            raise ModelRetry(
                "Rationale must be non-empty. Provide a brief explanation of your "
                "assessment."
            )

        if not output.passed:
            if not output.issues:
                raise ModelRetry(
                    "A failing verdict (passed=false) must include at least one "
                    "issue. Either set passed=true or describe the issues found."
                )

            has_errors = any(issue.severity == "error" for issue in output.issues)

            if not has_errors:
                raise ModelRetry(
                    "A failing verdict (passed=false) must include at least one "
                    "issue with severity='error'. If all issues are only warnings, "
                    "set passed=true instead."
                )

        return output

    return agent
