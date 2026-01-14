"""This module contains functionalities related to LLM calls for page IR **extraction**."""

# Standard Library
import json

from datetime import datetime, timezone
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
from skg.page_ir_extraction.schemas import PageIR
from skg.page_ir_extraction.validators import (
    PageIRExtractionQualityCtx,
    QualityError,
    validate_artifacts_are_true_artifacts,
    validate_basic_block_invariants,
    validate_continuity_for_extraction,
    validate_figure_blocks_are_well_formed,
    validate_footnote_blocks_are_plausible,
    validate_full_page_bboxes,
    validate_full_page_figure_requires_double_check,
    validate_gross_reading_order,
    validate_image_dimensions,
    validate_item_bboxes_required_and_in_bounds,
    validate_no_duplicate_item_bboxes,
    validate_no_whitespace_or_empty_blocks,
    validate_placeholder_bboxes,
    validate_table_integrity,
    validate_table_spans_are_sane,
)
from skg.prompts.page_ir_extraction import extract_page_ir_from_pdf_page
from skg.schemas import Limits
from skg.utils.constants import BlockType
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
    attempt: int,
    image_height: int,
    image_width: int,
    input_items: list[Any],
    instructions: str,
    model: str,
    page_index: int,
    raw_page_irs_dir: Path,
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
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.

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
        qe = QualityError(
            (
                f"Responses.parse returned no parsed output. output_text={output_text!r}"
                if output_text
                else ""
            ),
            failed_content=output_text,  # Pass text back to caller
        )
        _persist_page_ir_attempt_artifacts(
            attempt=attempt,
            error=qe,
            model=model,
            output_text=output_text,
            page_index=page_index,
            parsed=None,
            raw_page_irs_dir=raw_page_irs_dir,
        )
        raise qe

    # Populate image dimensions and page index.
    parsed.image_width = image_width
    parsed.image_height = image_height
    parsed.page_index = page_index

    try:
        verify_page_ir_extraction_quality(
            attempt=attempt,
            image_height=image_height,
            image_width=image_width,
            page_ir=parsed,
        )
    except QualityError as e:
        _persist_page_ir_attempt_artifacts(
            attempt=attempt,
            error=e,
            model=model,
            output_text=output_text,
            page_index=page_index,
            parsed=parsed,
            raw_page_irs_dir=raw_page_irs_dir,
        )

        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

    _persist_page_ir_attempt_artifacts(
        attempt=attempt,
        error=None,
        model=model,
        output_text=output_text,
        page_index=page_index,
        parsed=parsed,
        raw_page_irs_dir=raw_page_irs_dir,
    )

    return parsed


def _persist_page_ir_attempt_artifacts(
    *,
    attempt: int,
    error: Exception | None,
    model: str,
    output_text: str | None,
    page_index: int,
    parsed: PageIR | None,
    raw_page_irs_dir: Path,
) -> None:
    """Persist raw artifacts from a page IR extraction attempt.

    Parameters
    ----------
    attempt
        The attempt number (0-based).
    error
        The error encountered (if any).
    model
        The OpenAI model used.
    output_text
        The raw output text from the model (if any).
    page_index
        The 0-based page index.
    parsed
        The parsed PageIR object (if any).
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.
    """

    stem = f"{page_index:04d}.attempt{attempt:02d}"

    if output_text is not None:
        (raw_page_irs_dir / f"{stem}.output.txt").write_text(
            output_text, encoding="utf-8"
        )

    if parsed is not None:
        parsed_dict = parsed.model_dump(mode="json")
        (raw_page_irs_dir / f"{stem}.parsed.json").write_text(
            json.dumps(parsed_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if error is not None:
        (raw_page_irs_dir / f"{stem}.error.txt").write_text(
            f"{error.__class__.__name__}: {str(error)}", encoding="utf-8"
        )

    meta = {
        "attempt": attempt,
        "has_error": error is not None,
        "has_output_text": output_text is not None,
        "has_parsed": parsed is not None,
        "model": model,
        "page_index": page_index,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (raw_page_irs_dir / f"{stem}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
    raw_page_irs_dir: Path,
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
    raw_page_irs_dir
        Directory to save raw page IR extraction artifacts.
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
                attempt=attempt,
                image_height=image_height,
                image_width=image_width,
                input_items=input_items,
                instructions=instructions,
                model=model,
                page_index=page_index,
                raw_page_irs_dir=raw_page_irs_dir,
            )
        except QualityError as e:
            if attempt == max_retries:
                logger.error("Extraction failed after exhausting retries.")
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


def verify_page_ir_extraction_quality(
    *, attempt: int, image_height: int, image_width: int, page_ir: PageIR
) -> None:
    """Validate *quality* (not schema) of a parsed PageIR.

    Parameters
    ----------
    attempt
        The extraction attempt number (0-based).
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

    ctx = PageIRExtractionQualityCtx(
        boundary_state=page_ir.boundary_state,
        image_height=image_height,
        image_width=image_width,
        items=page_ir.items,
        non_artifact_items=[
            (i, item)
            for i, item in enumerate(page_ir.items)
            if item.kind != "block" or item.block_type != BlockType.ARTIFACT
        ],
        page_bbox=(0.0, 0.0, float(image_width), float(image_height)),
        page_ir=page_ir,
        tol=2.0,  # Small tolerance for rounding
        top_level_bboxes=[],
    )
    validate_image_dimensions(ctx)
    validate_no_whitespace_or_empty_blocks(ctx)
    validate_item_bboxes_required_and_in_bounds(ctx)
    validate_full_page_bboxes(ctx)
    validate_full_page_figure_requires_double_check(attempt=attempt, ctx=ctx)
    validate_no_duplicate_item_bboxes(ctx)
    validate_basic_block_invariants(ctx)
    validate_footnote_blocks_are_plausible(ctx)
    validate_figure_blocks_are_well_formed(ctx)
    validate_artifacts_are_true_artifacts(ctx)
    validate_table_integrity(ctx)
    validate_table_spans_are_sane(ctx)
    validate_placeholder_bboxes(ctx)
    validate_continuity_for_extraction(ctx)
    validate_gross_reading_order(ctx)
