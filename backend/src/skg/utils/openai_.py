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
from PIL import Image
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

# Package Library
from skg.ir.schemas import PageIR, PageKind
from skg.prompts.ir import extract_page_ir_info
from skg.schemas import Limits
from skg.utils.general import encode_png_to_data_url

limits = Limits(max_retry_attempts=5)
openai_client = OpenAI()


class ExtractionQualityError(Exception):
    """Raised when the LLM returns valid JSON that is semantically poor."""


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
    if parsed is None:
        # Best-effort: include any text the SDK exposes for debugging.
        output_text = getattr(response, "output_text", None)
        raise ExtractionQualityError(
            "Responses.parse returned no parsed output."
            + (f" output_text={output_text!r}" if output_text else "")
        )

    return parsed


def _is_visually_blank_page(png_fp: Path) -> bool:
    """Check if the rendered page image is nearly blank (separator/intentional blank
    page). Uses grayscale histogram stats (no OCR).

    Parameters
    ----------
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    bool
        True if the page is visually blank, False otherwise.
    """

    try:
        with Image.open(png_fp) as im:
            im = im.convert("L")

            # Downsample aggressively for speed.
            im.thumbnail((600, 600))

            hist = im.histogram()  # 256 bins
            total = float(sum(hist)) or 1.0

            # Fraction of "dark-ish" pixels (tuned to catch faint text).
            dark_frac = sum(hist[:200]) / total

            mean = sum(i * c for i, c in enumerate(hist)) / total
            var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / total
            std = var**0.5

            # Conservative thresholds:
            # - very low dark pixels
            # - low contrast (std)
            # - fairly light background
            return dark_frac < 0.006 and std < 14.0 and mean > 210.0
    except Exception:  # pylint: disable=broad-except
        # If we can't read the image, don't treat it as blank.
        return False


def _validate_extraction(
    page_ir: PageIR, page_index: int, png_fp: Path | None = None
) -> None:
    """Performs semantic checks on the extracted IR.

    Parameters
    ----------
    page_ir
        The extracted PageIR.
    page_index
        The 0-based page index.
    png_fp
        The PNG file path of the page image (optional, for blank page checks).

    Raises
    ------
    ExtractionQualityError
        If the output looks like a hallucination or lazy refusal, triggering a retry.
    """

    # 1. Check for "Empty" extraction. It is extremely rare for a curriculum page to
    # have ZERO nodes, statements, tables, or diagrams. If so, the model likely failed
    # to read the image.
    has_content = (
        bool(page_ir.nodes)
        or bool(page_ir.statements)
        or bool(page_ir.tables)
        or bool(page_ir.diagrams)
        or bool(page_ir.curriculum_elements)
    )
    if not has_content:
        # Allow truly non-content pages if the model classified them as such (front
        # matter, TOC, etc.).
        if page_ir.page_kind not in (None, PageKind.UNKNOWN, PageKind.CONTENT):
            page_ir.warnings = page_ir.warnings or []
            page_ir.warnings.append(
                f"Allowed empty extraction because page_kind={page_ir.page_kind.value}"
            )
            return

        # Allow visually blank pages anywhere in the PDF (separators/intentional
        # blanks).
        if png_fp is not None and _is_visually_blank_page(png_fp):
            page_ir.warnings = page_ir.warnings or []
            page_ir.warnings.append(
                "Allowed empty extraction because page appears visually blank"
            )
            return

        # Otherwise: treat as vision failure and retry.
        raise ExtractionQualityError(
            f"Page {page_index} extraction resulted in ZERO content elements. "
            "Not marked as non-content and not visually blank -> likely vision failure."
        )

    # 2. Enforce double extraction for tables. If the model extracted tables (physical)
    # but zero semantic items (nodes/statements/elements), it likely failed to process
    # the table content ("lazy" extraction). We exempt non-content pages (like TOCs)
    # where tables might just be lists of page numbers.
    semantic_count = (
        len(page_ir.nodes or [])
        + len(page_ir.statements or [])
        + len(page_ir.curriculum_elements or [])
    )
    if (
        page_ir.tables
        and semantic_count == 0
        and page_ir.page_kind
        not in (PageKind.TOC, PageKind.LIST_OF_TABLES, PageKind.FRONT_MATTER)
    ):
        raise ExtractionQualityError(
            f"Page {page_index} extracted {len(page_ir.tables)} tables but ZERO "
            f"semantic items. Double extraction failed (LLM likely lazy). Retrying."
        )

    # 3. Check for "Refusal" hallucinations. Sometimes models return valid JSON where
    # the text fields say "I cannot read this".
    refusal_keywords = [
        "cannot read",
        "unable to extract",
        "i cannot",
        "blurred image",
        "no text found",
        "protected document",
    ]

    # Scan a sample of text fields.
    all_text = []
    for s in page_ir.statements or []:
        all_text.append((s.text or "").lower())
    for n in page_ir.nodes or []:
        all_text.append((n.label or "").lower())

    for text in all_text:
        if any(kw in text for kw in refusal_keywords) and len(text) < 100:
            raise ExtractionQualityError(
                f"Page {page_index} contains refusal text: '{text}'. Retrying."
            )


def extract_page_ir_with_llm(
    *, context_text: str | None = None, model: str, page_index: int, png_fp: Path
) -> PageIR:
    """Extract PageIR from a page image using LLM + Vision + Structured Outputs. Uses
    OpenAI Responses API structured parsing into a Pydantic model. Image is passed as
    an input_image with a base64 data URL.

    NB:
    1. Uses OpenAI Responses API with `text.format.type="json_schema"` for structured
        outputs.
    2. Sends the PNG as an `input_image` with a base64 data URL.
    3. Provenance: The model is allowed to emit provenance pointers, but it should use
        placeholder doc_key/pdf_name (normalize_provenance will overwrite).
    4. Includes a 'Self-Correction Loop': if the model output fails validation, we
        feed the error back to the model and ask it to try again.

    Parameters
    ----------
    context_text
        Optional additional context text to include in the prompt.
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

    Raises
    ------
    Exception
        For transient API/network errors.
    ExtractionQualityError
        If the extraction quality is poor after retries.
    """

    image_url = encode_png_to_data_url(png_fp)
    prompts = extract_page_ir_info(context_text=context_text, page_index=page_index)

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

    max_semantic_retries = 2  # 1 initial try + 2 retries = 3 attempts total

    for attempt in range(max_semantic_retries + 1):
        try:
            # 1. Call API (network retries handled by @retry).
            page_ir = _call_openai_api(
                input_items=input_items, instructions=instructions, model=model
            )

            # 2. Semantic validation.
            _validate_extraction(page_ir, page_index, png_fp=png_fp)

            # 3. Success.
            page_ir.page_index = page_index
            return page_ir

        except ExtractionQualityError as e:
            if attempt >= max_semantic_retries:
                raise  # Re-raise the final quality error

            # Feed the error back and ask for a corrected full PageIR.
            input_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Your previous output had issues and must be corrected.\n"
                                f"ERROR: {str(e)}\n\n"
                                "Return a complete PageIR that matches the schema and fixes the issue. "
                                "Do not omit content. Preserve any correct content you already found."
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

            # Convert schema/parse/validation failures into feedback-loop errors
            # (retryable).
            last_error = ExtractionQualityError(
                f"Structured parse/validation failed on page {page_index}: "
                f"{e.__class__.__name__}: {e}"
            )
            if attempt >= max_semantic_retries:
                raise last_error from e

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
        f"Extraction failed after {max_semantic_retries + 1} attempts for page "
        f"{page_index}."
    )
