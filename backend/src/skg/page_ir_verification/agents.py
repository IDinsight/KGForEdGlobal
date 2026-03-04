"""This module contains Agent definitions for page IR continuity verification and
validation.

The verification Agent produces a PageIRContinuityVerdict (no reasoning, lower effort).
The validation Agent receives the verification verdict + source images and returns a
ContinuityValidationVerdict (high reasoning effort).

Both agents are constructed per-call via factory functions because the output validator
closures capture call-specific context (candidate items, attempt counter).
"""

# Standard Library
from typing import Callable

# Third Party Library
from loguru import logger
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.openai import OpenAIResponsesModelSettings

# Package Library
from skg.page_ir_extraction.schemas import Block, Table
from skg.page_ir_extraction.validators import QualityError
from skg.page_ir_verification.schemas import (
    ContinuityValidationVerdict,
    PageIRContinuityVerdict,
)


def create_continuity_verification_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model: str,
    next_item: Block | Table,
    prev_item: Block | Table,
    verify_continuity_fn: Callable,
) -> Agent:
    """Create an Agent configured for page IR continuity verification.

    The returned agent has an output validator that runs context-dependent validation
    checks and raises ModelRetry on failure.

    Parameters
    ----------
    instructions
        System-level verification instructions.
    max_retries
        Maximum number of quality-error retries (correction turns).
    model
        The model identifier (e.g., 'openai:gpt-5.2-2025-12-11').
    next_item
        The next page candidate item (parsed Block or Table).
    prev_item
        The previous page candidate item (parsed Block or Table).
    verify_continuity_fn
        Callable that will raise `QualityError` on failure. Injected to avoid circular
        imports between agents.py and llm.py.

    Returns
    -------
    Agent
        The configured verification Agent with output type PageIRContinuityVerdict.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model,
        instructions=instructions,
        model_settings=OpenAIResponsesModelSettings(temperature=0.0, top_p=0.95),
        output_retries=max_retries,
        output_type=PageIRContinuityVerdict,
    )

    @agent.output_validator
    def validate_continuity_verdict(
        output: PageIRContinuityVerdict,
    ) -> PageIRContinuityVerdict:
        """Validate context-dependent invariants of the continuity verdict.

        Schema-internal invariants (is_continuation <-> continuation_kind, confidence
        threshold, repeats_header <-> table-only) are already enforced by the Pydantic
        model validators at parse time. This validator runs checks that require the
        candidate items (prev_item/next_item).

        Parameters
        ----------
        output
            The parsed PageIRContinuityVerdict from the model.

        Returns
        -------
        PageIRContinuityVerdict
            The validated verdict.
        """

        attempt = attempt_counter["value"]

        try:
            verify_continuity_fn(
                next_item=next_item, prev_item=prev_item, verdict=output
            )
        except QualityError as e:
            truncated_msg = str(e)[:500]

            logger.error(
                f"Verification quality check failed, attempt {attempt}: "
                f"{truncated_msg}"
            )

            attempt_counter["value"] += 1

            raise ModelRetry(
                f"Your output had quality issues and must be corrected.\n"
                f"ERROR: {str(e)}\n\n"
                f"Return a complete PageIRContinuityVerdict that matches the schema "
                f"and fixes the issue."
            ) from e

        attempt_counter["value"] += 1

        return output

    return agent


def create_continuity_validation_agent(
    *,
    instructions: str,
    max_retries: int = 3,
    model: str,
    next_item: Block | Table,
    prev_item: Block | Table,
    verify_continuity_fn: Callable,
) -> Agent:
    """Create an Agent configured for validating a continuity verification verdict
    against the source page images.

    The validation agent receives the verification verdict JSON and the source page
    images, then returns a ContinuityValidationVerdict. When the verdict is failing,
    it includes a corrected PageIRContinuityVerdict. The output validator runs the
    same context-dependent checks on the corrected verdict.

    Parameters
    ----------
    instructions
        System-level validation instructions.
    max_retries
        Maximum number of retries if the corrected verdict fails quality checks.
    model
        The model identifier (e.g., 'openai:gpt-5.2-2025-12-11').
    next_item
        The next page candidate item (parsed Block or Table).
    prev_item
        The previous page candidate item (parsed Block or Table).
    verify_continuity_fn
        Callable that will raise `QualityError` on failure. Injected to avoid circular
        imports between agents.py and llm.py.

    Returns
    -------
    Agent
        The configured validation Agent with output type ContinuityValidationVerdict.
    """

    attempt_counter: dict[str, int] = {"value": 0}

    agent = Agent(
        model,
        instructions=instructions,
        model_settings=OpenAIResponsesModelSettings(
            openai_reasoning_effort="high", openai_reasoning_summary="detailed"
        ),
        output_retries=max_retries,
        output_type=ContinuityValidationVerdict,
    )

    @agent.output_validator
    def validate_correction_quality(
        output: ContinuityValidationVerdict,
    ) -> ContinuityValidationVerdict:
        """Validate the corrected verdict (when present) using the same
        context-dependent checks as the verification agent.

        Parameters
        ----------
        output
            The parsed ContinuityValidationVerdict from the model.

        Returns
        -------
        ContinuityValidationVerdict
            The validated verdict.
        """

        attempt = attempt_counter["value"]

        if output.corrected_verdict is not None:
            try:
                verify_continuity_fn(
                    next_item=next_item,
                    prev_item=prev_item,
                    verdict=output.corrected_verdict,
                )
            except QualityError as e:
                truncated_msg = str(e)[:500]
                logger.error(
                    f"Validation agent's corrected verdict failed quality checks, "
                    f"attempt {attempt}: {truncated_msg}"
                )
                attempt_counter["value"] += 1

                raise ModelRetry(
                    f"Your corrected_verdict has quality issues and must be fixed.\n"
                    f"ERROR: {str(e)}\n\n"
                    f"Return a complete ContinuityValidationVerdict with a "
                    f"corrected_verdict that fixes this issue while preserving all "
                    f"other corrections."
                ) from e

        attempt_counter["value"] += 1

        return output

    return agent
