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
from pydantic_ai import Agent, ModelRetry

# Package Library
from kgfeg.model_registry import ModelConfig
from kgfeg.page_ir_extraction.schemas import ExtractionValidationVerdict, PageIR
from kgfeg.page_ir_extraction.utils import persist_page_ir_attempt_artifacts
from kgfeg.page_ir_extraction.validators import QualityError


def create_page_ir_extraction_agent(
    *,
    image_height: int,
    image_width: int,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    page_index: int,
    raw_page_irs_dir: Path,
    validation_cycle: int = 0,
    verify_quality_fn: Callable,
) -> Agent:
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
    model_config
        The ModelConfig containing the model identifier and any relevant settings.
    page_index
        The 0-based page index.
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.
    validation_cycle
        The 0-based validation cycle index. Forwarded to artifact persistence so
        filenames are unique across extraction -> validation retries.
    verify_quality_fn
        Callable with signature `(*, attempt, image_height, image_width, page_ir)`
        that raises `QualityError` on failure. Injected to avoid circular imports
        between agents.py and llm.py.

    Returns
    -------
    Agent
        The configured Agent. An internal attempt counter is used by the output
        validator closure for artifact persistence; callers do not need access to it.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.page_ir_extraction_settings("extraction"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(PageIR),
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

        # Side-effect: populate Python-filled provenance fields before quality checks
        # run. This is intentional since these fields are not set by the LLM and must
        # be present for validators that inspect page_index or image dimensions. On
        # retry, the next output object will be similarly mutated.
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
                model=model_config.model,
                output_text=None,
                page_index=page_index,
                parsed=output,
                raw_page_irs_dir=raw_page_irs_dir,
                validation_cycle=validation_cycle,
            )
            truncated_msg = str(e)[:500]

            logger.error(
                f"Quality check failed on page {page_index + 1}, attempt {attempt + 1}: {truncated_msg}"
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
            model=model_config.model,
            output_text=None,
            page_index=page_index,
            parsed=output,
            raw_page_irs_dir=raw_page_irs_dir,
            validation_cycle=validation_cycle,
        )
        attempt_counter["value"] += 1
        return output

    return agent


def create_page_ir_validation_agent(
    *,
    image_height: int,
    image_width: int,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    page_index: int,
    verify_quality_fn: Callable,
) -> Agent:
    """Create an Agent configured for validating an extracted PageIR against a source
    page image.

    The validation agent receives a PageIR JSON and the source PNG, then returns a
    structured ValidationVerdict indicating whether the extraction is faithful. When
    the verdict is failing, it also includes a corrected PageIR. The output validator
    runs the same Python quality checks on the corrected PageIR, raising ModelRetry if
    the correction itself has quality issues.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    instructions
        System-level validation instructions.
    max_retries
        Maximum number of retries if the corrected PageIR fails quality checks.
    model_config
        The ModelConfig containing the model identifier and any relevant settings.
    page_index
        The 0-based page index.
    verify_quality_fn
        Callable with signature `(*, attempt, image_height, image_width, page_ir)` that
        raises `QualityError` on failure. Used to validate the corrected PageIR.

    Returns
    -------
    Agent
        The configured validation Agent with output type ValidationVerdict.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.page_ir_extraction_settings("validation"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(ExtractionValidationVerdict),
    )

    @agent.output_validator
    def validate_verdict_and_correction(
        output: ExtractionValidationVerdict,
    ) -> ExtractionValidationVerdict:
        """Validate structural consistency of the verdict and quality of the corrected
        PageIR (when present).

        When the verdict is failing and includes a corrected_page_ir, this validator
        runs the same Python quality checks that the extraction agent's output
        validator uses. If the corrected PageIR fails quality checks, ModelRetry is
        raised so the validation agent can fix its own correction.

        Parameters
        ----------
        output
            The parsed ValidationVerdict from the model.

        Returns
        -------
        ExtractionValidationVerdict
            The validated verdict (same as input if checks pass).
        """

        attempt = attempt_counter["value"]

        if output.corrected_page_ir is not None:
            # Populate Python-filled provenance fields on the corrected PageIR before
            # quality checks, mirroring what the extraction agent's validator does.
            output.corrected_page_ir.image_width = image_width
            output.corrected_page_ir.image_height = image_height
            output.corrected_page_ir.page_index = page_index

            try:
                verify_quality_fn(
                    attempt=attempt,
                    image_height=image_height,
                    image_width=image_width,
                    page_ir=output.corrected_page_ir,
                )
            except QualityError as e:
                truncated_msg = str(e)[:500]

                logger.error(
                    f"Validation agent's corrected PageIR failed quality checks on "
                    f"page {page_index + 1}, attempt {attempt + 1}: {truncated_msg}"
                )

                attempt_counter["value"] += 1
                raise ModelRetry(
                    f"Your corrected_page_ir has quality issues and must be fixed.\n"
                    f"ERROR: {str(e)}\n\n"
                    f"Return a complete ValidationVerdict with a corrected_page_ir "
                    f"that fixes this issue while preserving all other corrections."
                ) from e

        attempt_counter["value"] += 1
        return output

    return agent
