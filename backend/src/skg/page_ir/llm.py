"""This module contains functionalities related to LLM calls for page IRs."""

# Standard Library
from pathlib import Path
from typing import Any, Optional

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
from skg.page_ir.schemas import PageIR, PageIRContinuityVerdict
from skg.page_ir.validators import (
    QualityError,
    build_page_ir_extraction_quality_ctx,
    validate_and_reconcile_continuity_for_extraction,
    validate_basic_block_invariants,
    validate_full_page_bboxes,
    validate_gross_reading_order,
    validate_image_dimensions,
    validate_item_bboxes_required_and_in_bounds,
    validate_no_whitespace_or_empty_blocks,
    validate_non_continuation_has_no_resumed_truncated_boundaries,
    validate_page_indices,
    validate_placeholder_bboxes,
    validate_table_integrity,
)
from skg.prompts.page_ir import (
    extract_page_ir_from_pdf_page,
    verify_page_ir_pairs_from_extraction,
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
def _call_openai_api_for_page_ir_extraction(
    *,
    image_height: int,
    image_width: int,
    input_items: list[Any],
    instructions: str,
    model: str,
) -> PageIR:
    """Wrapper for extraction API calls with retries.

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
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

    Raises
    ------
    QualityError
        If the response could not be parsed or failed quality checks.
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
    output_text = getattr(response, "output_text", None)

    # Capture the raw text if parsing/validation fails.
    if parsed is None:
        raise QualityError(
            (
                f"Responses.parse returned no parsed output. output_text={output_text!r}"
                if output_text
                else ""
            ),
            failed_content=output_text,  # Pass text back to caller
        )

    # Even if parsing succeeded, enforce quality checks. NB: populate image dimenions
    # so PageIR's clamp validator can run.
    parsed.image_width = image_width
    parsed.image_height = image_height

    # Clamp any slightly out-of-bounds bboxes now that dimensions are known.
    parsed.clamp_bboxes_within_image()

    try:
        verify_page_ir_extraction_quality(
            image_height=image_height, image_width=image_width, page_ir=parsed
        )
    except QualityError as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    return parsed


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
    *, input_items: list[Any], instructions: str, model: str
) -> PageIRContinuityVerdict:
    """Wrapper for verification API calls with retries.

    Parameters
    ----------
    input_items
        The list of messages to send to the OpenAI API.
    instructions
        The verification instructions to include.
    model
        The OpenAI model to use.

    Returns
    -------
    PageIRContinuityVerdict
        The extracted PageIRContinuityVerdict.

    Raises
    ------
    QualityError
        If the response could not be parsed or failed quality checks.
    """

    response = openai_client.responses.parse(
        input=input_items,
        instructions=instructions,
        model=model,
        temperature=0,
        text_format=PageIRContinuityVerdict,
        top_p=1,
    )

    parsed = getattr(response, "output_parsed", None)
    output_text = getattr(response, "output_text", None)

    if parsed is None:
        raise QualityError(
            "Continuity verification returned no parsed output.",
            failed_content=output_text,
        )

    try:
        verify_page_ir_continuity_verdict(parsed)
    except QualityError as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    return parsed


def extract_page_ir(
    *,
    country: str,
    image_height: int,
    image_width: int,
    languages: list[str],
    max_retries: int = 2,
    model: str,
    page_index: int,
    png_fp: Path,
    text_layer_hints: Optional[str] = None,
    year: Optional[int] = None,
) -> PageIR:
    """Extract PageIR from a page image using LLM + Vision + Structured Outputs.

    Parameters
    ----------
    country
        Country context for the prompt.
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    languages
        Expected languages context for the prompt.
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.
    page_index
        The 0-based page index.
    png_fp
        The PNG file path of the page image.
    text_layer_hints
        Optional text layer hints from the PDF.
    year
        Year context for the prompt.

    Returns
    -------
    PageIR
        The extracted PageIR.

    Raises
    ------
    Exception
        For transient API errors.
    QualityError
        If extraction fails after retries.
    """

    image_url = encode_png_to_data_url(png_fp)
    prompts = extract_page_ir_from_pdf_page(
        country=country,
        image_height=image_height,
        image_width=image_width,
        languages=languages,
        page_index=page_index,
        text_layer_hints=text_layer_hints,
        year=year,
    )
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

    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api_for_page_ir_extraction(
                image_height=image_height,
                image_width=image_width,
                input_items=input_items,
                instructions=instructions,
                model=model,
            )
        except QualityError as e:
            if attempt == max_retries:
                logger.error("Extraction failed after retries.")
                raise  # Re-raise the final quality error

            # Append the assistant's failed attempt to history first. Without this, the
            # model doesn't know what it's correcting.
            if e.failed_content:
                logger.error(f"Extraction failed content: {e.failed_content}")
                input_items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": e.failed_content}],
                    }
                )

            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"Your previous output had issues and must be corrected.\n"
                                f"ERROR: {str(e)}\n\n"
                                f"Return a complete PageIR that matches the schema and fixes the issue."
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
            last_error = QualityError(
                f"Structured parse/validation failed on page {page_index}: {e}"
            )

            if attempt >= max_retries:
                raise last_error from e

            # If possible, we should try to add the assistant's context here too, but
            # standard Python Exceptions won't carry the model output unless we wrap
            # them in _call_openai_api_for_page_ir_extraction. For now, we proceed with
            # the Error feedback.
            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"The previous response failed structured parsing/validation.\n"
                                f"ERROR: {e.__class__.__name__}: {e}\n\n"
                                f"Return a complete PageIR that matches the schema exactly."
                            ),
                        }
                    ],
                }
            )
            continue

    raise QualityError(
        f"Extraction failed after {max_retries + 1} attempts for page: {page_index}."
    )


def verify_page_ir_continuity_verdict(
    verdict: PageIRContinuityVerdict,
) -> None:
    """Validate the semantic consistency of a continuity verdict.

    Parameters
    ----------
    verdict
        The PageIRContinuityVerdict to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if verdict.is_continuation and verdict.confidence < 0.5:
        # This is a soft check, but often indicates hallucination. We might not raise
        # an error, but logging it is wise.
        logger.warning(
            f"Low confidence ({verdict.confidence}) for continuation verdict."
        )

    validate_page_indices(verdict)
    validate_non_continuation_has_no_resumed_truncated_boundaries(verdict)


def verify_page_ir_extraction_quality(
    *, image_height: int, image_width: int, page_ir: PageIR
) -> None:
    """Validate *quality* (not schema) of a parsed PageIR.

    This validator is designed for the *stitching step* (Document IR creation). It
    enforces non-negotiables:
      - Item-level bboxes exist and are sane
      - No whitespace-only blocks
      - Basic table integrity (no empty tables)
      - Continuity signals are internally consistent (page boundary_state vs. item
            boundaries)
      - Gross reading-order violations are caught (without overfitting to 1- or
            2-column pages)

    Parameters
    ----------
    image_height
        Rendered page image height in pixels.
    image_width
        Rendered page image width in pixels.
    page_ir
        Parsed PageIR object.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    ctx = build_page_ir_extraction_quality_ctx(
        image_height=image_height, image_width=image_width, page_ir=page_ir
    )
    validate_image_dimensions(ctx)
    validate_no_whitespace_or_empty_blocks(ctx)
    validate_item_bboxes_required_and_in_bounds(ctx)
    validate_full_page_bboxes(ctx)
    validate_basic_block_invariants(ctx)
    validate_table_integrity(ctx)
    validate_placeholder_bboxes(ctx)
    validate_and_reconcile_continuity_for_extraction(ctx)
    validate_gross_reading_order(ctx)


def verify_page_ir_pairs(
    *,
    max_retries: int = 2,
    model: str,
    next_page_index: int,
    next_item_excerpt: dict[str, Any],
    next_top_png: Path,
    prev_bottom_png: Path,
    prev_item_excerpt: dict[str, Any],
    prev_page_index: int,
) -> PageIRContinuityVerdict:
    """Verify continuity between two PageIR excerpts using LLM.

    Parameters
    ----------
    max_retries
        Maximum number of retries for quality errors.
    model
        The OpenAI model to use.
    next_page_index
        The 0-based index of the next page (N+1).
    next_item_excerpt
        Excerpt of the candidate near top item from page N+1 JSON.
    next_top_png
        The PNG file path of the top crop of page N+1.
    prev_bottom_png
        The PNG file path of the bottom crop of page N.
    prev_item_excerpt
        Excerpt of the candidate near bottom item from page N JSON.
    prev_page_index
        The 0-based index of the previous page (N).

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

    prev_bottom_image_url = encode_png_to_data_url(prev_bottom_png)
    next_top_image_url = encode_png_to_data_url(next_top_png)
    prompts = verify_page_ir_pairs_from_extraction(
        next_item_excerpt=next_item_excerpt,
        next_page_index=next_page_index,
        prev_item_excerpt=prev_item_excerpt,
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
                    "text": "IMAGE A: bottom crop of page N (prev page).",
                },
                {"type": "input_image", "image_url": prev_bottom_image_url},
                {
                    "type": "input_text",
                    "text": "IMAGE B: top crop of page N+1 (next page).",
                },
                {"type": "input_image", "image_url": next_top_image_url},
            ],
        }
    ]

    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api_for_page_ir_verification(
                input_items=input_items,
                instructions=instructions,
                model=model,
            )
        except QualityError as e:
            if attempt == max_retries:
                logger.error(
                    f"Verification failed after retries for pages "
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

            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Your previous verdict was logically inconsistent: {str(e)}. Please correct it.",
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
            # them in _call_openai_api_for_page_ir_extraction. For now, we proceed with
            # the Error feedback.
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
