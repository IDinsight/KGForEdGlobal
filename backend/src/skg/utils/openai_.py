"""This module contains utilities for interacting with the OpenAI API."""

# Standard Library
from pathlib import Path
from typing import Any

# Third Party Library
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
from skg.ir.schemas import PageIR
from skg.prompts.ir import stage1_extraction_prompts
from skg.schemas import Limits
from skg.utils.general import encode_png_to_data_url

limits = Limits(max_retry_attempts=5)
openai_client = OpenAI()


class ExtractionQualityError(Exception):
    """Raised when the LLM returns valid JSON that is semantically poor."""

    def __init__(self, message: str, failed_content: str | None = None):
        """

        Parameters
        ----------
        message
            The error message.
        failed_content
            The content that caused the failure, if applicable.
        """

        super().__init__(message)

        self.failed_content = failed_content


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
def _call_openai_api(
    *, input_items: list[Any], instructions: str, model: str
) -> PageIR:
    """Raw wrapper for the API call to handle network retries independently.

    Parameters
    ----------
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The extraction instructions to include.
    model
        The OpenAI model to use.

    Returns
    -------
    PageIR
        The extracted PageIR.
    """

    response = openai_client.responses.parse(
        input=input_items,  # User content items
        instructions=instructions,  # System message at top-level
        model=model,
        temperature=0,
        text_format=PageIR,  # Pydantic for structured output parsing
        top_p=1,
    )

    parsed = getattr(response, "output_parsed", None)

    # Capture the raw text if parsing/validation fails.
    if parsed is None:
        output_text = getattr(response, "output_text", None)
        raise ExtractionQualityError(
            "Responses.parse returned no parsed output."
            + (f" output_text={output_text!r}" if output_text else ""),
            failed_content=output_text,  # Pass text back to caller
        )

    return parsed


def extract_page_ir_with_llm(*, model: str, page_index: int, png_fp: Path) -> PageIR:
    """Extract PageIR from a page image using LLM + Vision + Structured Outputs. Uses
    OpenAI Responses API structured parsing into a Pydantic model. Image is passed as
    an input_image with a base64 data URL.

    Parameters
    ----------
    model
        The OpenAI model to use.
    page_index
        The 0-based page index.
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    PageIR
        The extracted PageIR.
    """

    image_url = encode_png_to_data_url(png_fp)
    prompts = stage1_extraction_prompts(page_index=page_index)

    # Initial context.
    instructions = prompts.system_message
    input_items = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompts.user_message},
                {"type": "input_image", "image_url": image_url},
            ],
        },
    ]

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            page_ir = _call_openai_api(
                input_items=input_items, instructions=instructions, model=model
            )
            page_ir.page_index = page_index
            return page_ir
        except ExtractionQualityError as e:
            if attempt == max_retries:
                raise  # Re-raise the final quality error

            # Append the Assistant's failed attempt to history first. Without this, the
            # model doesn't know what it's correcting.
            if e.failed_content:
                input_items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": e.failed_content}],
                    }
                )

            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Your previous output had issues and must be corrected.\n"
                                f"ERROR: {str(e)}\n\n"
                                "Return a complete PageIR that matches the schema and fixes the issue."
                            ),
                        }
                    ],
                }
            )
            continue
        except Exception as e:  # pylint: disable=broad-except
            # Let transient errors propagate (Tenacity should cover most of these).
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
            last_error = ExtractionQualityError(
                f"Structured parse/validation failed on page {page_index}: {e}"
            )

            if attempt >= max_retries:
                raise last_error from e

            # If possible, we should try to add the assistant's context here too, but
            # standard Python Exceptions won't carry the model output unless we wrap
            # them in _call_openai_api. For now, we proceed with the Error feedback.
            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "The previous response failed structured parsing/validation.\n"
                                f"ERROR: {e.__class__.__name__}: {e}\n\n"
                                "Return a complete PageIR that matches the schema exactly."
                            ),
                        }
                    ],
                }
            )
            continue

    raise ExtractionQualityError(
        f"Extraction failed after {max_retries + 1} attempts for page " f"{page_index}."
    )
