"""This module contains Agent definitions for page IR extraction.

The Agent is constructed per-call via a factory function because the output validator
closure captures page-specific context (image dimensions, attempt counter, artifact
persistence directory).
"""

# Standard Library
from pathlib import Path
from typing import Callable

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, ModelRetry, ModelSettings

# Package Library
from skg.page_ir_extraction.schemas import PageIR
from skg.page_ir_extraction.utils import persist_page_ir_attempt_artifacts
from skg.page_ir_extraction.validators import QualityError

DEFAULT_MODEL_SETTINGS = ModelSettings(temperature=0, top_p=1)
DEFAULT_OUTPUT_RETRIES = 3


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
