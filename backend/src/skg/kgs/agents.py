"""This module contains Agent definitions for Learning Components and Learning
Progressions KG inference.

The primary inference Agents are constructed per-call via factory functions because
their output-validator closures capture call-specific context (validator callable and
attempt counter).

Separate validation Agents are also constructed per-call. They run in a fresh
conversation after the initial response has already passed Python-side validation, and
produce the final reviewed/corrected structured output.
"""

# Standard Library
from typing import Optional

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, ModelRetry

# Package Library
from skg.kgs.schemas import AtomicSkillsResponse, ProgressionEdgesResponse
from skg.kgs.validators import AtomicSkillsValidator, ProgressionEdgesValidator
from skg.model_registry import ModelConfig
from skg.page_ir_extraction.validators import QualityError


def _raise_quality_retry(
    *, agent_label: str, attempt: int, error: QualityError, retry_message: str
) -> None:
    """Log a quality failure and raise ModelRetry with a correction message.

    Parameters
    ----------
    agent_label
        Human-readable label for logging.
    attempt
        The current attempt index.
    error
        The caught quality error.
    retry_message
        The retry instruction to send back to the model.

    Raises
    ------
    ModelRetry
        Always raised with the provided retry message.
    """

    truncated_msg = str(error)[:500]

    logger.error(
        f"{agent_label} quality check failed, attempt {attempt}: {truncated_msg}"
    )

    raise ModelRetry(retry_message) from error


def create_atomic_skills_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    validator: Optional[AtomicSkillsValidator] = None,
) -> Agent:
    """Create the initial Agent configured for atomic skills inference.

    The returned agent has an output validator that runs the caller-supplied Python
    validation function and raises `ModelRetry` on failure so pydantic-ai handles the
    correction loop automatically.

    Parameters
    ----------
    instructions
        System-level instructions for the agent.
    max_retries
        Maximum number of output-validation retries (correction turns).
    model_config
        The ModelConfig containing the model identifier and any relevant settings.
    validator
        Optional post-parse validator; should raise `QualityError` on failure.

    Returns
    -------
    Agent
        A configured Agent with output type `AtomicSkillsResponse`.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("learning_components"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(AtomicSkillsResponse),
    )

    @agent.output_validator
    def validate_atomic_skills_output(
        output: AtomicSkillsResponse,
    ) -> AtomicSkillsResponse:
        """Run Python-side validation on the parsed atomic skills response.

        Parameters
        ----------
        output
            The parsed `AtomicSkillsResponse` output from the agent.

        Returns
        -------
        AtomicSkillsResponse
            The same output if validation passes; otherwise raises `ModelRetry` to
            trigger a retry.
        """

        attempt = attempt_counter["value"]

        try:
            if validator is not None:
                validator(output)
        except QualityError as e:
            attempt_counter["value"] += 1
            _raise_quality_retry(
                agent_label="Atomic skills agent",
                attempt=attempt,
                error=e,
                retry_message=(
                    "Your output had quality issues and must be corrected.\n"
                    f"ERROR: {str(e)}\n\n"
                    "Return a complete AtomicSkillsResponse that matches the schema "
                    "and fixes the issue."
                ),
            )

        attempt_counter["value"] += 1
        return output

    return agent


def create_atomic_skills_validation_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    validator: Optional[AtomicSkillsValidator] = None,
) -> Agent:
    """Create the second-pass validation Agent for atomic skills inference.

    This agent runs after the initial atomic-skills agent has already produced a
    Python-validated `AtomicSkillsResponse`. It reviews that draft against the original
    task context and returns the final corrected response.

    Parameters
    ----------
    instructions
        System-level validation instructions.
    max_retries
        Maximum number of output-validation retries (correction turns).
    model_config
        The ModelConfig containing the model identifier and any relevant settings.
    validator
        Optional post-parse validator; should raise `QualityError` on failure.

    Returns
    -------
    Agent
        A configured validation Agent with output type `AtomicSkillsResponse`.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("learning_components"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(AtomicSkillsResponse),
    )

    @agent.output_validator
    def validate_atomic_skills_validation_output(
        output: AtomicSkillsResponse,
    ) -> AtomicSkillsResponse:
        """Run Python-side validation on the validation agent's final output.

        Parameters
        ----------
        output
            The parsed `AtomicSkillsResponse` output from the validation agent.

        Returns
        -------
        AtomicSkillsResponse
            The same output if validation passes; otherwise raises `ModelRetry` to
            trigger a retry.
        """

        attempt = attempt_counter["value"]

        try:
            if validator is not None:
                validator(output)
        except QualityError as e:
            attempt_counter["value"] += 1
            _raise_quality_retry(
                agent_label="Atomic skills validation agent",
                attempt=attempt,
                error=e,
                retry_message=(
                    "Your revised AtomicSkillsResponse still has quality issues and "
                    "must be corrected.\n"
                    f"ERROR: {str(e)}\n\n"
                    "Return a complete AtomicSkillsResponse that fixes this issue "
                    "while preserving any already-correct content."
                ),
            )

        attempt_counter["value"] += 1
        return output

    return agent


def create_progression_edges_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    validator: Optional[ProgressionEdgesValidator] = None,
) -> Agent:
    """Create the initial Agent configured for learning progression edge inference.

    Parameters
    ----------
    instructions
        System-level instructions for the agent.
    max_retries
        Maximum number of output-validation retries (correction turns).
    model_config
        The ModelConfig containing the model identifier and any relevant settings.
    validator
        Optional post-parse validator; should raise `QualityError` on failure.

    Returns
    -------
    Agent
        A configured Agent with output type ``ProgressionEdgesResponse``.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("learning_progressions"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(ProgressionEdgesResponse),
    )

    @agent.output_validator
    def validate_progression_edges_output(
        output: ProgressionEdgesResponse,
    ) -> ProgressionEdgesResponse:
        """Run Python-side validation on the parsed progression-edge response.

        Parameters
        ----------
        output
            The parsed `ProgressionEdgesResponse` output from the agent.

        Returns
        -------
        ProgressionEdgesResponse
            The same output if validation passes; otherwise raises `ModelRetry` to
            trigger a retry.
        """

        attempt = attempt_counter["value"]

        try:
            if validator is not None:
                validator(output)
        except QualityError as e:
            attempt_counter["value"] += 1
            _raise_quality_retry(
                agent_label="Learning progressions agent",
                attempt=attempt,
                error=e,
                retry_message=(
                    "Your previous output had issues and must be corrected.\n"
                    f"ERROR: {str(e)}\n\n"
                    "Return a complete ProgressionEdgesResponse object that matches "
                    "the schema and fixes the issue."
                ),
            )

        attempt_counter["value"] += 1
        return output

    return agent


def create_progression_edges_validation_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model_config: ModelConfig,
    validator: Optional[ProgressionEdgesValidator] = None,
) -> Agent:
    """Create the second-pass validation Agent for learning progression edges.

    This agent runs after the initial progression-edge agent has already produced a
    Python-validated `ProgressionEdgesResponse`. It reviews that draft against the
    original task context and returns the final corrected response.

    Parameters
    ----------
    instructions
        System-level validation instructions.
    max_retries
        Maximum number of output-validation retries (correction turns).
    model_config
        The ModelConfig containing the model identifier and any relevant settings.
    validator
        Optional post-parse validator; should raise `QualityError` on failure.

    Returns
    -------
    Agent
        A configured validation Agent with output type ``ProgressionEdgesResponse``.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model_config.model,
        instructions=instructions,
        model_settings=model_config.kgs_settings("learning_progressions"),
        output_retries=max_retries,
        output_type=model_config.wrap_output_type(ProgressionEdgesResponse),
    )

    @agent.output_validator
    def validate_progression_edges_validation_output(
        output: ProgressionEdgesResponse,
    ) -> ProgressionEdgesResponse:
        """Run Python-side validation on the validation agent's final output.

        Parameters
        ----------
        output
            The parsed `ProgressionEdgesResponse` output from the validation agent.

        Returns
        -------
        ProgressionEdgesResponse
            The same output if validation passes; otherwise raises `ModelRetry` to
            trigger a retry.
        """

        attempt = attempt_counter["value"]

        try:
            if validator is not None:
                validator(output)
        except QualityError as e:
            attempt_counter["value"] += 1
            _raise_quality_retry(
                agent_label="Learning progressions validation agent",
                attempt=attempt,
                error=e,
                retry_message=(
                    "Your revised ProgressionEdgesResponse still has quality issues "
                    "and must be corrected.\n"
                    f"ERROR: {str(e)}\n\n"
                    "Return a complete ProgressionEdgesResponse that fixes this "
                    "issue while preserving any already-correct edges."
                ),
            )

        attempt_counter["value"] += 1
        return output

    return agent
