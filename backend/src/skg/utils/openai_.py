"""This module contains utilities for interacting with the OpenAI API."""

# Standard Library
from collections import Counter
from pathlib import Path
from typing import Any, Optional

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
    *,
    image_height: int,
    image_width: int,
    input_items: list[Any],
    instructions: str,
    model: str,
) -> PageIR:
    """Raw wrapper for the API call to handle network retries independently.

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

    # Even if parsing succeeded, enforce quality checks.
    output_text = getattr(response, "output_text", None)
    try:
        validate_page_ir_quality(
            image_height=image_height, image_width=image_width, page_ir=parsed
        )
    except ExtractionQualityError as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise ExtractionQualityError(str(e), failed_content=output_text) from e

    return parsed


def _validate_bbox(
    *, bbox: list[float], image_height: int, image_width: int, tol: float, where_: str
) -> None:
    """Validate a single bbox.

    Parameters
    ----------
    bbox
        The bbox to validate.
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    where_
        Description of where the bbox is located (for error messages).

    Raises
    ------
    ExtractionQualityError
        If the bbox is invalid.
    """

    if len(bbox) != 4:
        raise ExtractionQualityError(f"Invalid bbox length at {where_}: {bbox}")

    x0, y0, x1, y1 = bbox
    if not (x1 > x0 and y1 > y0):
        raise ExtractionQualityError(f"Inverted/degenerate bbox at {where_}: {bbox}")

    if x0 < -tol or y0 < -tol or x1 > image_width + tol or y1 > image_height + tol:
        raise ExtractionQualityError(
            f"Out-of-bounds bbox at {where_} for {image_width}x{image_height}: {bbox}"
        )


def extract_page_ir(
    *,
    country: str,
    image_height: int,
    image_width: int,
    languages: list[str],
    model: str,
    page_index: int,
    png_fp: Path,
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
    model
        The OpenAI model to use.
    page_index
        The 0-based page index.
    png_fp
        The PNG file path of the page image.
    year
        Year context for the prompt.

    Returns
    -------
    PageIR
        The extracted PageIR.
    """

    image_url = encode_png_to_data_url(png_fp)
    prompts = stage1_extraction_prompts(
        country=country,
        image_height=image_height,
        image_width=image_width,
        languages=languages,
        page_index=page_index,
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

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api(
                image_height=image_height,
                image_width=image_width,
                input_items=input_items,
                instructions=instructions,
                model=model,
            )
        except ExtractionQualityError as e:
            if attempt == max_retries:
                raise  # Re-raise the final quality error

            # Append the Assistant's failed attempt to history first. Without this, the
            # model doesn't know what it's correcting.
            if e.failed_content:
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


def validate_page_ir_quality(  # pylint: disable=R0912,R1260
    *, image_height: int, image_width: int, page_ir: PageIR
) -> None:
    """Validate semantic quality of a parsed PageIR.

    This is intentionally *not* a schema validator. Instead, it catches common failure
    modes that still parse:
        - whitespace-only blocks
        - bboxes out of bounds / inverted
        - repeated/placeholder bboxes (e.g., many items sharing the same [0,0,*,*])

    Parameters
    ----------
    image_height
        The image height in pixels.
    image_width
        The image width in pixels.
    page_ir
        The PageIR to validate.

    Raises
    ------
    ExtractionQualityError
        If any quality checks fail.
    """

    if image_width <= 0 or image_height <= 0:
        raise ExtractionQualityError(
            f"Invalid image dimensions: {image_width}x{image_height}."
        )

    tol = 2.0  # Small tolerance for rounding

    items = page_ir.items or []

    # 1. No whitespace-only text blocks.
    for i, item in enumerate(items):
        if getattr(item, "kind", None) == "block":
            text_unit = getattr(item, "text", None)
            if (
                text_unit is not None
                and isinstance(text_unit.text, str)
                and text_unit.text.strip() == ""
            ):
                raise ExtractionQualityError(
                    f"Whitespace-only block text at items[{i}].text; remove this "
                    f"block."
                )

    # 2. Validate bboxes (top-level items + nested text/cells).
    top_level_bboxes = []
    for i, item in enumerate(items):
        bbox = getattr(item, "bbox", None)
        if bbox is not None:
            _validate_bbox(
                bbox=bbox,
                image_height=image_height,
                image_width=image_width,
                tol=tol,
                where_=f"items[{i}].bbox",
            )
            top_level_bboxes.append(tuple(map(float, bbox)))

        if getattr(item, "kind", None) == "block":
            text_unit = getattr(item, "text", None)
            if text_unit is not None and getattr(text_unit, "bbox", None) is not None:
                _validate_bbox(
                    bbox=text_unit.bbox,
                    image_height=image_height,
                    image_width=image_width,
                    tol=tol,
                    where_=f"items[{i}].text.bbox",
                )

            list_items = getattr(item, "list_items", None)
            if list_items:
                for j, li in enumerate(list_items):
                    if li.text and li.text.bbox is not None:
                        _validate_bbox(
                            bbox=li.text.bbox,
                            image_height=image_height,
                            image_width=image_width,
                            tol=tol,
                            where_=f"items[{i}].list_items[{j}].text.bbox",
                        )

        if getattr(item, "kind", None) == "table":
            rows = getattr(item, "rows", None) or []
            for r, row in enumerate(rows):
                for c, cell in enumerate(getattr(row, "cells", None) or []):
                    if cell.bbox is not None:
                        _validate_bbox(
                            bbox=cell.bbox,
                            image_height=image_height,
                            image_width=image_width,
                            tol=tol,
                            where_=f"items[{i}].rows[{r}].cells[{c}].bbox",
                        )
                    if cell.text is not None and cell.text.bbox is not None:
                        _validate_bbox(
                            bbox=cell.text.bbox,
                            image_height=image_height,
                            image_width=image_width,
                            tol=tol,
                            where_=f"items[{i}].rows[{r}].cells[{c}].text.bbox",
                        )

    # 3. Detect placeholder bboxes: many items sharing the exact same box.
    if len(top_level_bboxes) >= 6:
        counts = Counter(top_level_bboxes)
        (most_common_bbox, most_common_count) = counts.most_common(1)[0]
        frac = most_common_count / max(1, len(top_level_bboxes))

        x0, y0, x1, y1 = most_common_bbox
        starts_at_origin = abs(x0) <= tol and abs(y0) <= tol
        area = max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
        page_area = float(image_width) * float(image_height)
        area_frac = area / page_area if page_area > 0 else 0.0

        if frac >= 0.30 and starts_at_origin:
            raise ExtractionQualityError(
                "Too many items share the same origin-anchored bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, frac={frac:.2f}). "
                "This looks like a placeholder; set bbox=null when unsure."
            )

        if frac >= 0.20 and area_frac >= 0.85:
            raise ExtractionQualityError(
                "Too many items share a near-full-page bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, frac={frac:.2f}, area_frac={area_frac:.2f}). "
                "Do not use full-page bboxes as placeholders; use bbox=null when unsure."
            )
