"""This module contains Agent definitions for Learning Components and Learning
Progressions KG inference.

Agents are constructed per-call via factory functions because the output validator
closures capture call-specific context (validator callable, double-check behaviour,
attempt counter).
"""

# Standard Library
from typing import Callable, Optional

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

# Package Library
from skg.kgs.prompts import (
    double_check_atomic_skills,
    double_check_learning_progressions,
)
from skg.kgs.schemas import AtomicSkillsResponse, ProgressionEdgesResponse
from skg.page_ir_extraction.validators import QualityError


def create_atomic_skills_agent(
    *,
    always_double_check_first_attempt: bool,
    instructions: str,
    max_retries: int = 3,
    model: str,
    validator: Optional[Callable[[AtomicSkillsResponse], None]] = None,
) -> Agent:
    """Create an Agent configured for atomic skills inference.

    The returned agent has an output validator that runs the caller-supplied validation
    function and raises `ModelRetry` on failure so pydantic-ai handles the correction
    loop automatically.

    Parameters
    ----------
    always_double_check_first_attempt
        If True, the very first successful parse is rejected with the "double check"
        message so the model reviews its own output before we accept it.
    instructions
        System-level instructions for the agent.
    max_retries
        Maximum number of output-validation retries (correction turns).
    model
        The model identifier (e.g., `openai:gpt-5.2-2025-12-11`).
    validator
        Optional post-parse validator; should raise `QualityError` on failure.

    Returns
    -------
    Agent
        A configured Agent with output type `AtomicSkillsResponse`.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model,
        instructions=instructions,
        model_settings=OpenAIResponsesModelSettings(
            openai_reasoning_effort="high", openai_reasoning_summary="detailed"
        ),
        output_retries=max_retries,
        output_type=AtomicSkillsResponse,
    )

    @agent.output_validator
    def validate_atomic_skills_output(
        output: AtomicSkillsResponse,
    ) -> AtomicSkillsResponse:
        """Output validator closure that runs the caller-supplied validation function
        and raises ModelRetry with an appropriate message on failure. Also handles the
        "double check" logic on the first attempt if enabled.

        Parameters
        ----------
        output
            The parsed AtomicSkillsResponse from the model.

        Returns
        -------
        AtomicSkillsResponse
            The validated atomic skills response.
        """

        attempt = attempt_counter["value"]

        # Force a double-check retry on the very first attempt.
        if always_double_check_first_attempt and attempt == 0:
            attempt_counter["value"] += 1
            raise ModelRetry(double_check_atomic_skills().user_message)

        try:
            if validator is not None:
                validator(output)
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"Atomic skills quality check failed, attempt {attempt}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your output had quality issues and must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete AtomicSkillsResponse that matches the schema "
                f"and fixes the issue."
            ) from e

        attempt_counter["value"] += 1
        return output

    return agent


def create_progression_edges_agent(
    *,
    always_double_check_first_attempt: bool,
    instructions: str,
    max_retries: int = 3,
    model: str,
    validator: Optional[Callable[[ProgressionEdgesResponse], None]] = None,
) -> Agent:
    """Create an Agent configured for learning progression edge inference.

    Parameters
    ----------
    always_double_check_first_attempt
        If True, the very first successful parse is rejected with the "double check"
        message so the model reviews its own output before we accept it.
    instructions
        System-level instructions for the agent.
    max_retries
        Maximum number of output-validation retries (correction turns).
    model
        The model identifier (e.g., `openai:gpt-5.2-2025-12-11`).
    validator
        Optional post-parse validator; should raise `QualityError` on failure.

    Returns
    -------
    Agent
        A configured Agent with output type ``ProgressionEdgesResponse``.
    """

    attempt_counter: dict[str, int] = {"value": 0}
    agent = Agent(
        model,
        instructions=instructions,
        model_settings=OpenAIResponsesModelSettings(
            openai_reasoning_effort="high", openai_reasoning_summary="detailed"
        ),
        output_retries=max_retries,
        output_type=ProgressionEdgesResponse,
    )

    @agent.output_validator
    def validate_progression_edges_output(
        output: ProgressionEdgesResponse,
    ) -> ProgressionEdgesResponse:
        """Output validator closure that runs the caller-supplied validation function
        and raises ModelRetry with an appropriate message on failure. Also handles the
        "double check" logic on the first attempt if enabled.

        Parameters
        ----------
        output
            The parsed ProgressionEdgesResponse from the model.

        Returns
        -------
        ProgressionEdgesResponse
            The validated progression edges response.
        """

        attempt = attempt_counter["value"]

        # Force a double-check retry on the very first attempt.
        if always_double_check_first_attempt and attempt == 0:
            attempt_counter["value"] += 1
            raise ModelRetry(double_check_learning_progressions().user_message)

        try:
            if validator is not None:
                validator(output)
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"Learning progressions quality check failed, attempt {attempt}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1
            raise ModelRetry(
                f"Your previous output had issues and must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete ProgressionEdgesResponse object that matches "
                f"the schema and fixes the issue."
            ) from e

        attempt_counter["value"] += 1
        return output

    return agent
