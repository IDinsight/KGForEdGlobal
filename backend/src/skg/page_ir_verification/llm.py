"""This module contains functionalities related to LLM calls for page IR
**verification**.
"""

# Standard Library
from pathlib import Path
from typing import Any

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
from skg.page_ir_extraction.schemas import Block, Table
from skg.page_ir_extraction.validators import QualityError
from skg.page_ir_verification.prompts import (
    double_check_page_ir_verification,
    verify_page_ir_pairs_from_extraction,
)
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.page_ir_verification.validators import (
    validate_item_continuation_kind,
    validate_page_continuation_kind,
    validate_repeats_header_logic,
    validate_semantic_flow,
)
from skg.schemas import Limits
from skg.utils.general import encode_png_to_data_url

limits = Limits(max_retry_attempts=5)
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
def _call_openai_api_for_page_ir_verification(
    *,
    always_double_check_first_attempt: bool,
    attempt: int,
    input_items: list[Any],
    instructions: str,
    model: str,
    next_item: Block | Table,
    prev_item: Block | Table,
) -> PageIRContinuityVerdict:
    """Wrapper for verification API calls with retries.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    attempt
        The current attempt number (0-based).
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The verification instructions to include.
    model
        The OpenAI model to use.
    next_item
        The next page candidate item.
    prev_item
        The previous page candidate item.

    Returns
    -------
    PageIRContinuityVerdict
        The extracted PageIRContinuityVerdict.

    Raises
    ------
    QualityError
        If the response could not be parsed or failed quality checks.
    """

    if attempt == 0 or not always_double_check_first_attempt:
        response = openai_client.responses.parse(
            input=input_items,
            instructions=instructions,
            model=model,
            # reasoning={"effort": "high"},
            temperature=0,
            text_format=PageIRContinuityVerdict,
            top_p=1.0,
        )
    else:
        response = openai_client.responses.parse(
            input=input_items,
            instructions=instructions,
            model=model,
            reasoning={"effort": "high"},
            text_format=PageIRContinuityVerdict,
        )

    parsed = getattr(response, "output_parsed", None)
    output_text = getattr(response, "output_text", None)

    if parsed is None:
        raise QualityError(
            "Continuity verification returned no parsed output.",
            failed_content=output_text,
        )

    try:
        verify_page_ir_continuity_verdict(
            always_double_check_first_attempt=always_double_check_first_attempt,
            attempt=attempt,
            next_item=next_item,
            prev_item=prev_item,
            verdict=parsed,
        )
    except QualityError as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    return parsed


def verify_page_ir_continuity_verdict(
    *,
    always_double_check_first_attempt: bool,
    attempt: int,
    next_item: Block | Table,
    prev_item: Block | Table,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Validate the semantic consistency of a continuity verdict.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    attempt
        The current attempt number (0-based).
    next_item
        The next page candidate item.
    prev_item
        The previous page candidate item.
    verdict
        The PageIRContinuityVerdict to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    # Force retry on first attempt.
    if always_double_check_first_attempt and attempt == 0:
        raise QualityError("Reason does not matter and is overwritten in caller.")

    if verdict.is_continuation and verdict.confidence < 0.5:
        # This is a soft check, but often indicates hallucination. We might not raise
        # an error, but logging it is wise.
        logger.warning(
            f"Low confidence ({verdict.confidence}) for continuation verdict."
        )

    validate_page_continuation_kind(verdict)
    validate_item_continuation_kind(
        next_item=next_item, prev_item=prev_item, verdict=verdict
    )
    validate_repeats_header_logic(next_item=next_item, verdict=verdict)
    validate_semantic_flow(next_item=next_item, verdict=verdict)


def verify_page_ir_pairs(
    *,
    always_double_check_first_attempt: bool,
    max_retries: int = 3,
    model: str,
    next_item: dict[str, Any],
    next_item_excerpt: dict[str, Any],
    next_page_index: int,
    next_png: Path,
    prev_item: dict[str, Any],
    prev_item_excerpt: dict[str, Any],
    prev_page_index: int,
    prev_png: Path,
) -> PageIRContinuityVerdict:
    """Verify continuity between two PageIR excerpts using LLM.

    Parameters
    ----------
    always_double_check_first_attempt
        Whether to force a retry on the first attempt. Useful for difficult/messy pages.
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.
    next_item
        The candidate item near top item from page N+1 JSON.
    next_item_excerpt
        The excerpt JSON of the candidate item near top of page N+1.
    next_page_index
        The 0-based index of the next page (N+1).
    next_png
        The PNG file path of page N+1.
    prev_item
        The candidate item near bottom item from page N JSON.
    prev_item_excerpt
        The excerpt JSON of the candidate item near bottom of page N.
    prev_page_index
        The 0-based index of the previous page (N).
    prev_png
        The PNG file path of page N.

    Returns
    -------
    PageIRContinuityVerdict
        The continuity verdict between the two pages.

    Raises
    ------
    Exception
        For transient API errors.
    QualityError
        If the LLM returns invalid or poor-quality output.
    """

    prev_image_url = encode_png_to_data_url(prev_png)
    next_image_url = encode_png_to_data_url(next_png)
    prompts = verify_page_ir_pairs_from_extraction(
        next_item=next_item_excerpt,
        next_page_index=next_page_index,
        prev_item=prev_item_excerpt,
        prev_page_index=prev_page_index,
    )
    instructions = prompts.system_message
    input_items: list[Any] = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompts.user_message},
                {
                    "type": "input_text",
                    "text": "IMAGE A: ENTIRETY of Page N (previous page).",
                },
                {"type": "input_image", "image_url": prev_image_url},
                {
                    "type": "input_text",
                    "text": "IMAGE B: TOP crop of page N+1 (next page).",
                },
                {"type": "input_image", "image_url": next_image_url},
            ],
        }
    ]

    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api_for_page_ir_verification(
                always_double_check_first_attempt=always_double_check_first_attempt,
                attempt=attempt,
                input_items=input_items,
                instructions=instructions,
                model=model,
                next_item=(
                    Block.model_validate(next_item)
                    if next_item["kind"] == "block"
                    else Table.model_validate(next_item)
                ),
                prev_item=(
                    Block.model_validate(prev_item)
                    if prev_item["kind"] == "block"
                    else Table.model_validate(prev_item)
                ),
            )
        except QualityError as e:
            if attempt == max_retries:
                logger.error(
                    f"Verification failed after exhausting retries for pages "
                    f"{prev_page_index}-{next_page_index}."
                )
                raise  # Re-raise the final quality error

            # Append the assistant's failed attempt to history first. Without this, the
            # model doesn't know what it's correcting.
            if e.failed_content:
                logger.error(f"Verification failed content: {e.failed_content}")
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
                                "text": double_check_page_ir_verification().user_message,
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
                                    f"Return a complete PageIRContinuityVerdict that matches the schema and fixes the issue."
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
            # them in _call_openai_api_for_page_ir_verification. For now, we proceed
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
                                f"Return a complete PageIRContinuityVerdict that matches the schema exactly."
                            ),
                        }
                    ],
                }
            )
            continue

    raise QualityError(f"Verification failed after {max_retries + 1} attempts.")
