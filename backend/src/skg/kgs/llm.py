"""This module contains functionalities related to LLM calls for constructing
relationships for the Learning Progressions KG.
"""

# Standard Library
from typing import Any, Callable, Optional

# Third Party Library
from loguru import logger
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

# Package Library
from skg.kgs.prompts import double_check_learning_progressions
from skg.kgs.schemas import ProgressionEdgesResponse
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import Limits

limits = Limits(max_retry_attempts=10)
openai_client = OpenAI()


@retry(
    reraise=True,
    retry=retry_if_exception_type(
        (
            TimeoutError,
            ConnectionError,
            OSError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )
    ),
    stop=stop_after_attempt(limits.max_retry_attempts),
    wait=wait_random_exponential(min=1, max=60),
)
def _call_openai_api_for_learning_progressions(
    *,
    always_double_check_first_attempt: bool,
    attempt: int,
    input_items: list[Any],
    instructions: str,
    model: str,
    validator: Optional[Callable[[ProgressionEdgesResponse], None]] = None,
) -> ProgressionEdgesResponse:
    """Wrapper for learning progressions inference calls with retries.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for messy data where the
        first attempt often fails but retries succeed.
    attempt
        The current attempt number (0-based).
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The learning progressions instructions to include.
    model
        The OpenAI model to use.
    validator
        Optional post-parse validator; raise QualityError to trigger correction.

    Returns
    -------
    ProgressionEdgesResponse
        The parsed response containing the progression edges.

    Raises
    ------
    QualityError
        If the response fails quality checks or cannot be parsed.
    """

    if attempt == 0 or not always_double_check_first_attempt:
        response = openai_client.responses.parse(
            input=input_items,
            instructions=instructions,
            model=model,
            reasoning={"effort": "high"},
            # temperature=0,
            text_format=ProgressionEdgesResponse,
            # top_p=1,
        )
    else:
        response = openai_client.responses.parse(
            input=input_items,
            instructions=instructions,
            model=model,
            reasoning={"effort": "high"},
            text_format=ProgressionEdgesResponse,
        )

    parsed = getattr(response, "output_parsed", None)
    output_text = getattr(response, "output_text", None)

    if parsed is None:
        raise QualityError(
            "Learning progressions inference returned no parsed output.",
            failed_content=output_text,
        )

    try:
        verify_learning_progressions(
            always_double_check_first_attempt=always_double_check_first_attempt,
            attempt=attempt,
            parsed=parsed,
            validator=validator,
        )
    except QualityError as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    return parsed


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
        The OpenAI model to use.
    user_message
        The primary user payload as a string.
    validator
        Optional post-parse validator; raise QualityError to trigger correction.

    Returns
    -------
    ProgressionEdgesResponse
        Structured edges list.
    """

    input_items: list[Any] = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": user_message}],
        }
    ]

    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api_for_learning_progressions(
                always_double_check_first_attempt=always_double_check_first_attempt,
                attempt=attempt,
                input_items=input_items,
                instructions=instructions,
                model=model,
                validator=validator,
            )
        except QualityError as e:
            if attempt == max_retries:
                logger.error(
                    "Learning progressions inference failed after exhausting retries."
                )
                raise  # Re-raise the final quality error

            # Append the assistant's failed attempt to history first. Without this, the
            # model doesn't know what it's correcting.
            if e.failed_content:
                logger.error(
                    f"Learning progressions failed content: {e.failed_content}"
                )
                input_items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": e.failed_content}],
                    }
                )

            if always_double_check_first_attempt and attempt == 0:
                input_items.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": double_check_learning_progressions().user_message,
                            }
                        ],
                    }
                )
            else:
                input_items.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    f"Your previous output had issues and must be corrected.\n"
                                    f"ERROR: {str(e)}\n\n"
                                    f"Return a complete ProgressionEdgesResponse object that matches the schema and fixes the issue."
                                ),
                            }
                        ],
                    }
                )
            continue
        except Exception as e:  # pylint: disable=broad-except
            # Let transient errors propagate (tenacity should cover most of these).
            if isinstance(
                e,
                (
                    TimeoutError,
                    ConnectionError,
                    OSError,
                    APIConnectionError,
                    APITimeoutError,
                    RateLimitError,
                    InternalServerError,
                ),
            ):
                raise

            # Handle general exceptions (like Pydantic ValidationErrors) that bubble up
            # from the API call but might not have attached text.
            last_error = QualityError(f"Structured parse/validation failed: {e}")

            if attempt >= max_retries:
                raise last_error from e

            # If possible, we should try to add the assistant's context here too, but
            # standard Python Exceptions won't carry the model output unless we wrap
            # them in _call_openai_api_for_learning_progressions. For now, we proceed
            # with the Error feedback.
            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"The previous response failed structured parsing/validation.\n"
                                f"ERROR: {e.__class__.__name__}: {e}\n\n"
                                f"Return a complete ProgressionEdgesResponse that matches the schema exactly."
                            ),
                        }
                    ],
                }
            )
            continue

    raise QualityError(
        f"Learning progressions inference failed after {max_retries + 1} attempts."
    )


def verify_learning_progressions(
    *,
    always_double_check_first_attempt: bool,
    attempt: int,
    parsed: ProgressionEdgesResponse,
    validator: Optional[Callable[[ProgressionEdgesResponse], None]] = None,
) -> None:
    """Validate the semantic consistency of a continuity verdict.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    attempt
        The current attempt number (0-based).
    parsed
        The parsed ProgressionEdgesResponse to validate.
    validator
        Optional post-parse validator; raise QualityError to trigger correction.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # Force retry on first attempt.
    if always_double_check_first_attempt and attempt == 0:
        raise QualityError("Reason does not matter and is overwritten in caller.")

    if validator is not None:
        validator(parsed)
