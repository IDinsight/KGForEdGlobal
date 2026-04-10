"""This module contains the orchestration logic for Learning Components and Learning
Progressions KG inference via LLM.

The Agent definitions and output-validation wiring live in `agents.py`. This module is
responsible for prompt construction, creating fresh agents, running them with the user
message, and returning the validated structured output.

Orchestration flow
------------------

1. Build a fresh agent via the appropriate factory in `agents.py`.
2. Run the agent with the user message. The agent's internal output validator handles
   quality checks (including the optional "double-check first attempt" forced retry)
   via `ModelRetry`.
3. Return the validated structured output.
"""

# Standard Library
from typing import Callable, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.agents import create_atomic_skills_agent, create_progression_edges_agent
from skg.kgs.schemas import AtomicSkillsResponse, ProgressionEdgesResponse


def infer_atomic_skills(
    *,
    always_double_check_first_attempt: bool,
    instructions: str,
    max_retries: int = 3,
    model: str,
    user_message: str,
    validator: Optional[Callable[[AtomicSkillsResponse], None]] = None,
) -> AtomicSkillsResponse:
    """Call the LLM and return parsed/validated atomic skills.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    instructions
        The system instructions to include in the prompt for the LLM.
    max_retries
        Maximum number of retries for quality errors.
    model
        The model identifier (e.g., ``"openai:gpt-5.2-2025-12-11"``).
    user_message
        The primary user payload as a string.
    validator
        Optional post-parse validator; raise ``QualityError`` to trigger correction.

    Returns
    -------
    AtomicSkillsResponse
        Structured atomic skills list.
    """

    agent = create_atomic_skills_agent(
        always_double_check_first_attempt=always_double_check_first_attempt,
        instructions=instructions,
        max_retries=max_retries,
        model=model,
        validator=validator,
    )

    logger.info("Running atomic skills agent...")

    result = agent.run_sync(user_message)

    logger.success("Atomic skills inference succeeded!")

    return result.output


def infer_progression_edges(
    *,
    always_double_check_first_attempt: bool,
    instructions: str,
    max_retries: int = 3,
    model: str,
    user_message: str,
    validator: Optional[Callable[[ProgressionEdgesResponse], None]] = None,
) -> ProgressionEdgesResponse:
    """Call the LLM and return parsed/validated edges.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    instructions
        The system instructions to include in the prompt for the LLM.
    max_retries
        Maximum number of retries for quality errors.
    model
        The model identifier (e.g., `openai:gpt-5.2-2025-12-11`).
    user_message
        The primary user payload as a string.
    validator
        Optional post-parse validator; raise `QualityError` to trigger correction.

    Returns
    -------
    ProgressionEdgesResponse
        Structured edges list.
    """

    agent = create_progression_edges_agent(
        always_double_check_first_attempt=always_double_check_first_attempt,
        instructions=instructions,
        max_retries=max_retries,
        model=model,
        validator=validator,
    )

    logger.info("Running learning progression edges agent...")

    result = agent.run_sync(user_message)

    logger.success("Learning progressions inference succeeded!")

    return result.output
