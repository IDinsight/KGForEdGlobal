"""This module contains the orchestration logic for Learning Components and Learning
Progressions KG inference via LLM.

The Agent definitions and output-validation wiring live in `agents.py`. This module is
responsible for prompt construction, creating fresh agents, running the initial agent,
then always running a separate validation agent that reviews the initial structured
output against the original task context.

Orchestration flow
------------------

1. Build and run a fresh initial agent via the appropriate factory in `agents.py`.
2. Let that agent's internal output validator handle Python-side quality checks via
   `ModelRetry`.
3. Build and run a fresh validation agent in a separate conversation using the
   original instructions, original user payload, and the initial structured output.
4. Return the validation agent's final structured output.
"""

# Standard Library
from typing import Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.kgs.agents import (
    create_atomic_skills_agent,
    create_atomic_skills_validation_agent,
    create_progression_edges_agent,
    create_progression_edges_validation_agent,
)
from skg.kgs.prompts import (
    validate_atomic_skills_output,
    validate_progression_edges_output,
)
from skg.kgs.schemas import AtomicSkillsResponse, ProgressionEdgesResponse
from skg.kgs.validators import AtomicSkillsValidator, ProgressionEdgesValidator


def _run_atomic_skills_validation_agent(
    *,
    draft_output: AtomicSkillsResponse,
    instructions: str,
    max_retries: int,
    user_message: str,
    validator: Optional[AtomicSkillsValidator] = None,
) -> AtomicSkillsResponse:
    """Run the atomic-skills validation agent on an already-validated draft output.

    Parameters
    ----------
    draft_output
        The output from the initial agent pass, which should already be validated by
        the agent's internal validator.
    instructions
        The original system instructions to include in the prompt for the validation
        agent.
    max_retries
        Maximum number of retries for quality errors on the validation agent.
    user_message
        The original primary user payload as a string, to include in the validation
        agent prompt.
    validator
        Optional post-parse validator for the validation agent; raise `QualityError` to
        trigger correction and retry.

    Returns
    -------
    AtomicSkillsResponse
        Structured atomic skills list after the validation-agent review pass.
    """

    logger.info("Running atomic skills validation agent...")

    prompts = validate_atomic_skills_output(
        draft_response_json=draft_output.model_dump_json(),
        original_instructions=instructions,
        original_user_message=user_message,
    )
    agent = create_atomic_skills_validation_agent(
        instructions=prompts.system_message,
        max_retries=max_retries,
        model_config=Settings.llm_config("kgs"),
        validator=validator,
    )
    result = agent.run_sync(prompts.user_message)

    logger.success("Atomic skills validation succeeded!")

    return result.output


def _run_progression_edges_validation_agent(
    *,
    draft_output: ProgressionEdgesResponse,
    instructions: str,
    max_retries: int,
    user_message: str,
    validator: Optional[ProgressionEdgesValidator] = None,
) -> ProgressionEdgesResponse:
    """Run the progression-edges validation agent on an already-validated draft output.

    Parameters
    ----------
    draft_output
        The output from the initial agent pass, which should already be validated by
        the agent's internal validator.
    instructions
        The original system instructions to include in the prompt for the validation
        agent.
    max_retries
        Maximum number of retries for quality errors on the validation agent.
    user_message
        The original primary user payload as a string, to include in the validation
        agent prompt.
    validator
        Optional post-parse validator for the validation agent; raise `QualityError` to
        trigger correction and retry.

    Returns
    -------
    ProgressionEdgesResponse
        Structured progression edges list after the validation-agent review pass.
    """

    logger.info("Running learning progression validation agent...")

    prompts = validate_progression_edges_output(
        draft_response_json=draft_output.model_dump_json(),
        original_instructions=instructions,
        original_user_message=user_message,
    )
    agent = create_progression_edges_validation_agent(
        instructions=prompts.system_message,
        max_retries=max_retries,
        model_config=Settings.llm_config("kgs"),
        validator=validator,
    )
    result = agent.run_sync(prompts.user_message)

    logger.success("Learning progression validation succeeded!")

    return result.output


def infer_atomic_skills(
    *,
    instructions: str,
    max_retries: int = 3,
    user_message: str,
    validator: Optional[AtomicSkillsValidator] = None,
) -> AtomicSkillsResponse:
    """Call the LLM and return final reviewed atomic skills.

    Parameters
    ----------
    instructions
        The system instructions to include in the prompt for the initial LLM call.
    max_retries
        Maximum number of retries for quality errors on each agent pass.
    user_message
        The primary user payload as a string.
    validator
        Optional post-parse validator; raise ``QualityError`` to trigger correction.

    Returns
    -------
    AtomicSkillsResponse
        Structured atomic skills list after the validation-agent review pass.
    """

    agent = create_atomic_skills_agent(
        instructions=instructions,
        max_retries=max_retries,
        model_config=Settings.llm_config("kgs"),
        validator=validator,
    )

    logger.info("Running atomic skills agent...")

    result = agent.run_sync(user_message)

    logger.success("Atomic skills initial inference succeeded!")

    return _run_atomic_skills_validation_agent(
        draft_output=result.output,
        instructions=instructions,
        max_retries=max_retries,
        user_message=user_message,
        validator=validator,
    )


def infer_progression_edges(
    *,
    instructions: str,
    max_retries: int = 3,
    user_message: str,
    validator: Optional[ProgressionEdgesValidator] = None,
) -> ProgressionEdgesResponse:
    """Call the LLM and return final reviewed progression edges.

    Parameters
    ----------
    instructions
        The system instructions to include in the prompt for the initial LLM call.
    max_retries
        Maximum number of retries for quality errors on each agent pass.
    user_message
        The primary user payload as a string.
    validator
        Optional post-parse validator; raise `QualityError` to trigger correction.

    Returns
    -------
    ProgressionEdgesResponse
        Structured edges list after the validation-agent review pass.
    """

    agent = create_progression_edges_agent(
        instructions=instructions,
        max_retries=max_retries,
        model_config=Settings.llm_config("kgs"),
        validator=validator,
    )

    logger.info("Running learning progression edges agent...")

    result = agent.run_sync(user_message)

    logger.success("Learning progressions initial inference succeeded!")

    return _run_progression_edges_validation_agent(
        draft_output=result.output,
        instructions=instructions,
        max_retries=max_retries,
        user_message=user_message,
        validator=validator,
    )
