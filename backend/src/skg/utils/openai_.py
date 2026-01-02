"""This module contains utilities for interacting with the OpenAI API."""

# Standard Library
from collections import Counter
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
from skg.prompts.page_ir import (
    extract_page_ir_from_pdf_page,
    verify_page_ir_pairs_from_extraction,
)
from skg.schemas import Limits
from skg.utils.constants import (
    BlockType,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import encode_png_to_data_url

limits = Limits(max_retry_attempts=5)
openai_client = OpenAI()


class QualityError(Exception):
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
            "Responses.parse returned no parsed output."
            + (f" output_text={output_text!r}" if output_text else ""),
            failed_content=output_text,  # Pass text back to caller
        )

    # Even if parsing succeeded, enforce quality checks. NB: populate image dims so
    # PageIR's clamp validator can run.
    parsed.image_width = image_width
    parsed.image_height = image_height

    # Clamp any slightly out-of-bounds bboxes now that dimensions are known.
    parsed.clamp_bboxes_within_image()

    try:
        validate_page_ir_extraction_quality(
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
        validate_continuity_verdict(parsed)
    except QualityError as e:
        # Attach the raw output so the correction attempt can see what it wrote.
        raise QualityError(str(e), failed_content=output_text) from e

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
    tol
        Tolerance for out-of-bounds checks.
    where_
        Description of where the bbox is located (for error messages).

    Raises
    ------
    QualityError
        If the bbox is invalid.
    """

    if len(bbox) != 4:
        raise QualityError(f"Invalid bbox length at {where_}: {bbox}")

    x0, y0, x1, y1 = bbox

    if not (x1 > x0 and y1 > y0):
        raise QualityError(f"Inverted/degenerate bbox at {where_}: {bbox}")

    if x0 < -tol or y0 < -tol or x1 > image_width + tol or y1 > image_height + tol:
        raise QualityError(
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

    max_retries = 2
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
                raise  # Re-raise the final quality error

            # Append the Assistant's failed attempt to history first. Without this, the
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
        f"Extraction failed after {max_retries + 1} attempts for page " f"{page_index}."
    )


def validate_continuity_verdict(  # pylint: disable=R0912,R1260
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

    # 1. Consistency: If is_continuation is True, we usually expect high confidence.
    if verdict.is_continuation and verdict.confidence < 0.5:
        # This is a soft check, but often indicates hallucination. We might not raise
        # an error, but logging it is wise.
        logger.warning(
            f"Low confidence ({verdict.confidence}) for continuation verdict."
        )

    # 2. Sanity: indices should be ordered.
    if verdict.prev_page_index >= verdict.next_page_index:
        raise QualityError(
            f"Invalid page index order: prev={verdict.prev_page_index} "
            f"next={verdict.next_page_index}"
        )
    if verdict.next_page_index - verdict.prev_page_index != 1:
        logger.warning(
            "Non-adjacent page indices in verdict "
            f"({verdict.prev_page_index}->{verdict.next_page_index}); expected adjacency."
        )

    # 2b. If this is NOT a continuation, it makes no sense to suggest
    # 'truncated'/'resumed' boundaries on the boundary items.
    if not verdict.is_continuation and (
        verdict.set_prev_item_boundary
        in (ItemBoundary.TRUNCATED, ItemBoundary.RESUMED, ItemBoundary.BOTH)
        or verdict.set_next_item_boundary
        in (ItemBoundary.TRUNCATED, ItemBoundary.RESUMED, ItemBoundary.BOTH)
    ):
        raise QualityError(
            "is_continuation=false but verdict suggests truncated/resumed item boundaries."
        )

    # 3. Logic: if it's a continuation, it's never correct to *set* either side to
    # COMPLETE (if no change is needed, leave set_* null.)
    if verdict.is_continuation:
        # Step-2 pairwise rule: only correct clearly incompatible boundaries. Do NOT
        # set BOTH; we can't infer that from just N bottom + N+1 top.
        if (
            verdict.set_prev_item_boundary is not None
            and verdict.set_prev_item_boundary != ItemBoundary.TRUNCATED
        ):
            raise QualityError(
                "For continuation=true (Step 2), set_prev_item_boundary must be TRUNCATED "
                "(or null). Do not set BOTH in pairwise verification."
            )

        if (
            verdict.set_next_item_boundary is not None
            and verdict.set_next_item_boundary != ItemBoundary.RESUMED
        ):
            raise QualityError(
                "For continuation=true (Step 2), set_next_item_boundary must be RESUMED "
                "(or null). Do not set BOTH in pairwise verification."
            )

    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)

    # continuation_kind must match is_continuation.
    if verdict.is_continuation and kind == PageContinuationKind.NONE.value:
        raise QualityError("is_continuation=true requires continuation_kind != 'none'.")
    if not verdict.is_continuation and kind != PageContinuationKind.NONE.value:
        raise QualityError("is_continuation=false requires continuation_kind='none'.")

    # Pairwise rule: do not set page boundary states in Step 2.
    if (
        getattr(verdict, "set_prev_boundary_state", None) is not None
        or getattr(verdict, "set_next_boundary_state", None) is not None
    ):
        raise QualityError("Step-2 verdict must not set page boundary_state fields.")

    # If not a continuation, Step-2 must not suggest ANY edits (pairwise limitation).
    if not verdict.is_continuation and (
        verdict.set_prev_item_boundary is not None
        or verdict.set_next_item_boundary is not None
        or verdict.set_next_table_repeats_header is not None
    ):
        raise QualityError("is_continuation=false must leave all set_* fields null.")

    # 4. Table-specific checks.
    is_table_cont = verdict.is_continuation and kind == PageContinuationKind.TABLE.value

    if is_table_cont:
        if verdict.set_next_table_repeats_header is not None and not isinstance(
            verdict.set_next_table_repeats_header, bool
        ):
            raise QualityError(
                "set_next_table_repeats_header must be a boolean when provided."
            )
    elif verdict.set_next_table_repeats_header is not None:
        raise QualityError(
            "set_next_table_repeats_header is only valid for table continuations."
        )


def validate_page_ir_extraction_quality(  # pylint: disable=R0912,R0915,R1260
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

    if image_width <= 0 or image_height <= 0:
        raise QualityError(f"Invalid image dimensions: {image_width}x{image_height}.")

    tol = 2.0  # Small tolerance for rounding
    items = page_ir.items or []
    boundary_state = getattr(page_ir, "boundary_state", PageBoundaryState.STANDALONE)
    page_bbox = (0.0, 0.0, float(image_width), float(image_height))

    # Only consider non-artifact items for continuity checks.
    non_artifact_items = [
        (i, it)
        for i, it in enumerate(items)
        if getattr(it, "kind", None) != "block"
        or getattr(it, "block_type", None) != BlockType.ARTIFACT
    ]

    def _boundary_str(item: Any) -> str:
        """Get the boundary string of an item.

        Parameters
        ----------
        item
            The item to get the boundary string from.

        Returns
        -------
        str
            The boundary string, or empty string if not set.
        """

        b = getattr(item, "boundary", None)
        return "" if b is None else b.value if hasattr(b, "value") else str(b)

    def _derive_boundary_state_from_items() -> PageBoundaryState:
        """Derive the page boundary state from item boundaries.

        Returns
        -------
        PageBoundaryState
            The derived page boundary state.
        """

        # Only consider non-artifact items for continuity.
        non_artifact = [it for _, it in non_artifact_items]

        if not non_artifact:
            return PageBoundaryState.STANDALONE

        any_from_prev = any(_is_resumed(_boundary_str(it)) for it in non_artifact)
        any_to_next = any(_is_truncated(_boundary_str(it)) for it in non_artifact)

        if any_from_prev and any_to_next:
            return PageBoundaryState.BOTH
        if any_from_prev:
            return PageBoundaryState.CONTINUES_FROM_PREV
        if any_to_next:
            return PageBoundaryState.CONTINUES_TO_NEXT
        return PageBoundaryState.STANDALONE

    def _effective_row_width(row: Any) -> int:
        """Determine the effective row width of a row (sum of col_spans).

        Parameters
        ----------
        row
            The row to check.

        Returns
        -------
        int
            The effective row width.
        """

        return sum(int(getattr(c, "col_span", 1) or 1) for c in (row.cells or []))

    def _ensure_text_en_none(tu: Any, where_: str) -> None:
        """Enforce that extraction does not populate English translations.

        Parameters
        ----------
        tu
            The TextUnit-like object.
        where_
            Description of where the TextUnit is located (for error messages).

        Raises
        ------
        QualityError
            If text_en is populated during extraction.
        """

        if tu is None:
            return

        # TextUnit.text_en must be null/omitted during extraction; translation happens
        # later.
        if getattr(tu, "text_en", None) is not None:
            raise QualityError(f"text_en must be null during extraction at {where_}.")

    def _is_full_page_bbox(bb: tuple[float, ...]) -> bool:
        """Check if a bbox is effectively full-page within tolerance.

        Parameters
        ----------
        bb
            The bbox to check.

        Returns
        -------
        bool
            True if the bbox is full-page within tolerance, False otherwise.
        """

        x0, y0, x1, y1 = bb

        return (
            abs(x0 - page_bbox[0]) <= tol
            and abs(y0 - page_bbox[1]) <= tol
            and abs(x1 - page_bbox[2]) <= tol
            and abs(y1 - page_bbox[3]) <= tol
        )

    def _is_resumed(boundary: str) -> bool:
        """Check if a boundary string indicates a resumed or both item.

        Parameters
        ----------
        boundary
            The boundary string to check.

        Returns
        -------
        bool
            True if the boundary indicates a resumed or both item, False otherwise.
        """

        return boundary in (ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value)

    def _is_truncated(boundary: str) -> bool:
        """Check if a boundary string indicates a truncated or both item.

        Parameters
        ----------
        boundary
            The boundary string to check.

        Returns
        -------
        bool
            True if the boundary indicates a truncated or both item, False otherwise.
        """

        return boundary in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value)

    def _safe_str(v: Any) -> str:
        """Convert None/non-str to a safe string ('') and keep strings as-is.

        Parameters
        ----------
        v
            The value to convert.

        Returns
        -------
        str
            The safe string.
        """

        return v if isinstance(v, str) else ""

    def _textunit_text(tu: Any) -> str:
        """Safely extract tu.text from a TextUnit-like object.

        Parameters
        ----------
        tu
            The TextUnit-like object.

        Returns
        -------
        str
            The text content, or empty string if not available.
        """

        return "" if tu is None else _safe_str(getattr(tu, "text", None))

    # 1. No whitespace-only text blocks, list items, or local codes.
    for i, item in enumerate(items):
        if getattr(item, "kind", None) != "block":
            continue

        text_unit = getattr(item, "text", None)
        _ensure_text_en_none(text_unit, f"items[{i}].text")

        block_type = getattr(item, "block_type", None)
        raw_text = getattr(text_unit, "text", None) if text_unit else None
        list_items = getattr(item, "list_items", None) or []
        local_code = getattr(item, "local_code", None)
        fig = getattr(item, "figure", None)

        # Figure captions are also TextUnits.
        if fig is not None:
            _ensure_text_en_none(
                getattr(fig, "caption", None), f"items[{i}].figure.caption"
            )

        # Check for whitespace-only main text. We define has_text as: exists, is
        # string, and is not empty.
        has_text = False
        if isinstance(raw_text, str):
            if not raw_text.strip():
                raise QualityError(
                    f"Whitespace-only block text at items[{i}].text; remove this block."
                )
            has_text = True

        # Check for whitespace-only list items.
        for j, li in enumerate(list_items):
            li_unit = getattr(li, "text", None)
            li_raw = getattr(li_unit, "text", None) if li_unit else None
            if isinstance(li_raw, str) and not li_raw.strip():
                raise QualityError(
                    f"Whitespace-only list item text at items[{i}].list_items[{j}].text; "
                    "remove this list item."
                )

        # Check for empty blocks (no payload at all).
        has_list = len(list_items) > 0
        has_code = isinstance(local_code, str) and bool(local_code.strip())
        has_figure = (block_type == BlockType.FIGURE) and (fig is not None)
        if not (has_text or has_list or has_code or has_figure):
            raise QualityError(
                f"Empty block at items[{i}]: text, list_items, and local_code are all "
                f"null/empty (and figure is null). Do not emit placeholder blocks."
            )

    # 2. Item-level bbox is REQUIRED and must be in-bounds.
    top_level_bboxes = []
    for i, item in enumerate(items):
        bbox = getattr(item, "bbox", None)
        if bbox is None:
            raise QualityError(
                f"Missing required item bbox at items[{i}].bbox. "
                "Every block/table must have an item-level bbox."
            )

        _validate_bbox(
            bbox=bbox,
            image_height=image_height,
            image_width=image_width,
            tol=tol,
            where_=f"items[{i}].bbox",
        )
        top_level_bboxes.append(tuple(map(float, bbox)))

    # 3. Reject full-page bboxes unless the page truly is a single full-page figure.
    full_page_idxs = [
        i for i, bb in enumerate(top_level_bboxes) if _is_full_page_bbox(bb)
    ]
    if full_page_idxs:
        # Only allow if it's literally one item and it's a figure.
        if not (
            len(items) == 1
            and getattr(items[0], "kind", None) == "block"
            and getattr(items[0], "block_type", None) == BlockType.FIGURE
        ):
            raise QualityError(
                f"Full-page bbox used as a placeholder for items {full_page_idxs}. "
                "BBoxes must be tight to each item. Full-page bbox is only allowed for "
                "a single full-page figure."
            )

    # 4. Basic block invariants (lightweight, avoids semantic guesswork).
    for i, item in enumerate(items):
        if getattr(item, "kind", None) != "block":
            continue

        block_type = getattr(item, "block_type", None)
        text_unit = getattr(item, "text", None)
        raw_list_items = getattr(item, "list_items", None)
        list_items = raw_list_items or []
        fig = getattr(item, "figure", None)

        # Non-figure blocks must not carry figure metadata.
        if block_type != BlockType.FIGURE and fig is not None:
            raise QualityError(
                f"Non-figure block must have figure=null at items[{i}].figure."
            )

        if block_type == BlockType.LIST:
            # Lists should be represented via list_items; text should be null.
            if text_unit is not None:
                raise QualityError(
                    f"List block must have text=null at items[{i}].text."
                )
            if fig is not None:
                raise QualityError(
                    f"List block must have figure=null at items[{i}].figure."
                )
            if not list_items:
                raise QualityError(
                    f"List block must have non-empty list_items at items[{i}].list_items."
                )
            for j, li in enumerate(list_items):
                # If marker is empty and text is short, it's likely a misclassification.
                marker = _safe_str(getattr(li, "marker", None))
                li_text = _textunit_text(getattr(li, "text", None))
                _ensure_text_en_none(
                    getattr(li, "text", None), f"items[{i}].list_items[{j}].text"
                )
                if not marker.strip() and len(li_text.strip()) < 3:
                    raise QualityError(
                        f"List item at items[{i}].list_items[{j}] has no marker and "
                        "insufficient text. This should likely be a paragraph."
                    )
        elif block_type == BlockType.FIGURE:
            # Figure blocks: no text, no list_items, must have figure metadata.
            if text_unit is not None:
                raise QualityError(
                    f"Figure block must have text=null at items[{i}].text."
                )
            if list_items:
                raise QualityError(
                    f"Figure block must have list_items=null/omitted at items[{i}].list_items."
                )
            if fig is None:
                raise QualityError(
                    f"Figure block must have non-null figure at items[{i}].figure."
                )

            # If alt_text is present, it must not be whitespace-only.
            alt = getattr(fig, "alt_text", None)
            if isinstance(alt, str) and not alt.strip():
                raise QualityError(
                    f"Whitespace-only figure.alt_text at items[{i}].figure.alt_text."
                )

            # If caption is present, it must not be whitespace-only.
            cap = getattr(fig, "caption", None)
            if cap is not None and not _textunit_text(cap).strip():
                raise QualityError(
                    f"Whitespace-only figure.caption at items[{i}].figure.caption."
                )
        elif raw_list_items is not None:
            raise QualityError(
                f"Non-list block must have list_items=null/omitted at items[{i}].list_items."
            )

    # 5. Table integrity (non-negotiable for deterministic stitching).
    for i, item in enumerate(items):
        if getattr(item, "kind", None) != "table":
            continue

        rows = getattr(item, "rows", None) or []
        if len(rows) == 0:
            raise QualityError(
                f"Empty table (rows=[]) at items[{i}].rows. Do not emit empty tables."
            )

        header_row_count = int(getattr(item, "header_row_count", 0) or 0)
        if header_row_count < 0:
            raise QualityError(
                f"Invalid header_row_count={header_row_count} at "
                f"items[{i}].header_row_count."
            )
        if header_row_count > len(rows):
            raise QualityError(
                f"header_row_count={header_row_count} exceeds total rows={len(rows)} "
                f"at items[{i}].header_row_count."
            )

        for r, row in enumerate(rows):
            cells = getattr(row, "cells", None) or []
            if len(cells) == 0:
                raise QualityError(
                    f"Table row with zero cells at items[{i}].rows[{r}].cells."
                )
            for c, cell in enumerate(cells):
                _ensure_text_en_none(
                    getattr(cell, "text", None), f"items[{i}].rows[{r}].cells[{c}].text"
                )

                # Spans should be >= 1; schema should already enforce, but keep a guard.
                col_span = int(getattr(cell, "col_span", 1) or 1)
                row_span = int(getattr(cell, "row_span", 1) or 1)
                if col_span < 1 or row_span < 1:
                    raise QualityError(
                        f"Invalid span at items[{i}].rows[{r}].cells[{c}] "
                        f"(col_span={col_span}, row_span={row_span})."
                    )

        # Lightweight column-count sanity checks (span-aware).
        #
        # NB: We must account for col_span when assessing whether a table has
        # "collapsed" into single-cell rows. Many curricula tables use spanning
        # header/label cells, so ignoring spans causes false positives.
        cell_counts = [len(getattr(rw, "cells", []) or []) for rw in rows]
        eff_widths = [_effective_row_width(rw) for rw in rows]
        eff_widths = [w for w in eff_widths if w > 0]
        if eff_widths:
            max_eff = max(eff_widths)

            # If the model provided n_cols, ensure it can accommodate the widest row.
            n_cols = getattr(item, "n_cols", None)
            if n_cols is not None:
                if not isinstance(n_cols, int):
                    raise QualityError(
                        f"n_cols must be an int or null at items[{i}].n_cols; "
                        f"got {type(n_cols)}"
                    )
                if n_cols < 1 or n_cols > 50:
                    raise QualityError(
                        f"Suspicious n_cols={n_cols} at items[{i}].n_cols "
                        f"(expected 1..50 or null)."
                    )
                if max_eff > n_cols:
                    raise QualityError(
                        f"Table at items[{i}] has a row with effective width {max_eff} "
                        f"(sum of col_span) but n_cols={n_cols}. Either increase n_cols "
                        "or adjust/split/merge cells to match the visual grid."
                    )

            # Detect likely collapse: header shows multiple columns but body is mostly
            # 1 column in effective width.
            if 0 < header_row_count < len(eff_widths):
                header_max = max(eff_widths[:header_row_count])
                body_widths = eff_widths[header_row_count:]
                if body_widths:
                    body_sorted = sorted(body_widths)
                    body_median = body_sorted[len(body_sorted) // 2]
                    frac_single = sum(w == 1 for w in body_widths) / len(body_widths)

                    if header_max >= 3 and body_median <= 1 and frac_single >= 0.60:
                        # Include raw cell counts for debugging.
                        header_cell_max = max(cell_counts[:header_row_count])
                        raise QualityError(
                            f"Table at items[{i}] likely collapsed: header shows "
                            f"{header_max} columns (effective, spans-aware; raw max "
                            f"cells={header_cell_max}), but {frac_single:.0%} of body "
                            f"rows have effective width 1. Split body rows into "
                            f"separate cells per visible column (or set correct "
                            f"col_span values)."
                        )

            # Catch wildly inconsistent widths overall (span-aware).
            if max_eff >= 4:
                mode_eff, _ = Counter(eff_widths).most_common(1)[0]
                frac_single_all = sum(w == 1 for w in eff_widths) / len(eff_widths)
                if mode_eff == 1 and frac_single_all >= 0.70:
                    raise QualityError(
                        f"Table at items[{i}] appears mostly single-column rows "
                        f"({frac_single_all:.0%} of rows have effective width 1). This "
                        f"often indicates the table grid was collapsed. Represent each "
                        f"visible column as a separate cell (or set correct col_span "
                        f"values)."
                    )

        # Deep table content check.
        any_content_in_table = False
        for r, row in enumerate(rows):
            for c, cell in enumerate(row.cells):
                t = _textunit_text(getattr(cell, "text", None))
                if t.strip():
                    any_content_in_table = True
                    break
        if not any_content_in_table:
            raise QualityError(f"Table at items[{i}] contains no text content.")

    # 6. Placeholder bbox detection (item bboxes are required).
    if len(top_level_bboxes) >= 3:
        counts = Counter(top_level_bboxes)
        most_common_bbox, most_common_count = counts.most_common(1)[0]
        frac = most_common_count / max(1, len(top_level_bboxes))

        x0, y0, x1, y1 = most_common_bbox
        starts_at_origin = abs(x0) <= tol and abs(y0) <= tol
        area = max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))
        page_area = float(image_width) * float(image_height)
        area_frac = area / page_area if page_area > 0 else 0.0

        if frac >= 0.30 and starts_at_origin:
            raise QualityError(
                "Too many items share the same origin-anchored bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, frac={frac:.2f}). "
                "This looks like a placeholder; bboxes must be localized to each item."
            )

        if frac >= 0.20 and area_frac >= 0.85:
            raise QualityError(
                "Too many items share a near-full-page bbox "
                f"{list(most_common_bbox)} (count={most_common_count}, frac={frac:.2f}, area_frac={area_frac:.2f}). "
                "Do not use near-full-page bboxes as placeholders; bboxes must be localized."
            )

    # 7. Continuity consistency: page boundary_state vs. item boundary markers.
    expected_bs = _derive_boundary_state_from_items()

    if boundary_state != expected_bs:
        logger.warning(
            f"boundary_state mismatch on page {getattr(page_ir, 'page_index', None)}: "
            f"got={boundary_state} expected={expected_bs}. Overwriting."
        )
        page_ir.boundary_state = expected_bs
        boundary_state = expected_bs

    bs = (
        boundary_state.value
        if hasattr(boundary_state, "value")
        else str(boundary_state)
    )
    states_requiring_prev = {
        PageBoundaryState.CONTINUES_FROM_PREV.value,
        PageBoundaryState.BOTH.value,
    }
    states_requiring_next = {
        PageBoundaryState.CONTINUES_TO_NEXT.value,
        PageBoundaryState.BOTH.value,
    }
    needs_from_prev = bs in states_requiring_prev
    needs_to_next = bs in states_requiring_next

    if needs_from_prev:
        if not non_artifact_items:
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues from "
                f"previous page, but there are no non-artifact items."
            )

        # Look in the first few non-artifact items for a resumed marker.
        window = [it for _, it in non_artifact_items[:5]]
        if not any(_is_resumed(_boundary_str(it)) for it in window):
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues from "
                f" previous page, but no resumed boundary found in first few "
                f"non-artifact items."
            )

    if needs_to_next:
        if not non_artifact_items:
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues to next "
                "page, but there are no non-artifact items."
            )

        # Look in the last few non-artifact items for a truncated marker.
        window = [it for _, it in non_artifact_items[-5:]]
        if not any(_is_truncated(_boundary_str(it)) for it in window):
            raise QualityError(
                f"boundary_state='{boundary_state}' implies content continues to "
                f" next page, but no truncated boundary found in last few non-artifact "
                f"items."
            )

    if bs == PageBoundaryState.STANDALONE.value:
        # Standalone pages should not claim resumed/truncated content (excluding artifacts).
        any_resumed_or_truncated = any(
            _is_resumed(_boundary_str(it)) or _is_truncated(_boundary_str(it))
            for _, it in non_artifact_items
        )
        if any_resumed_or_truncated:
            raise QualityError(
                f"boundary_state='{PageBoundaryState.STANDALONE.value}' but found "
                "resumed/truncated boundaries on non-artifact items."
            )

    # 8. Gross reading-order sanity check (avoid overfitting to 2-column pages). We
    # only flag big backward jumps *within roughly the same column*.
    if len(non_artifact_items) >= 3:
        prev_bbox = getattr(non_artifact_items[0][1], "bbox", [0.0, 0.0, 0.0, 0.0])
        prev_x0, prev_y0 = float(prev_bbox[0]), float(prev_bbox[1])

        max_y_backjump = 0.15 * float(image_height)  # Big jump threshold
        same_col_dx = 0.20 * float(image_width)  # "Same column" threshold

        for idx, it in non_artifact_items[1:]:
            bbox = getattr(it, "bbox", None)
            if bbox is None:
                continue

            x0, y0 = float(bbox[0]), float(bbox[1])
            y_backjump = prev_y0 - y0
            x_diff = x0 - prev_x0  # Pos means moved right, neg means moved left

            # Only flag if we jumped UP and moved LEFT (back to start of a new
            # column/line) or if we jumped UP and stayed in the same horizontal lane.
            if y_backjump > max_y_backjump:
                if x_diff < -same_col_dx or abs(x_diff) < same_col_dx:
                    raise QualityError(
                        f"Likely reading-order violation at items[{idx}]. "
                        "Items jump upwards significantly without a clear column shift."
                    )

            prev_x0, prev_y0 = x0, y0


def verify_page_ir_pairs(
    *,
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

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            return _call_openai_api_for_page_ir_verification(
                input_items=input_items,
                instructions=instructions,
                model=model,
            )
        except QualityError as e:
            if attempt == max_retries:
                logger.warning(
                    f"Verification failed after retries for pages "
                    f"{prev_page_index}->{next_page_index}. Using default False verdict."
                )
                # Fallback: Return a 'safe' negative verdict rather than crashing the
                # whole pipeline.
                return PageIRContinuityVerdict(
                    prev_page_index=prev_page_index,
                    next_page_index=next_page_index,
                    confidence=0.0,
                    continuation_kind=PageContinuationKind.NONE,
                    is_continuation=False,
                    rationale=f"Automatic failure after retries: {str(e)}",
                    set_next_item_boundary=None,
                    set_next_table_repeats_header=None,
                    set_prev_item_boundary=None,
                )

            # Add feedback to history.
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

    return PageIRContinuityVerdict(
        prev_page_index=prev_page_index,
        next_page_index=next_page_index,
        confidence=0.0,
        continuation_kind=PageContinuationKind.NONE,
        is_continuation=False,
        rationale="Fallback.",
        set_next_item_boundary=None,
        set_next_table_repeats_header=None,
        set_prev_item_boundary=None,
    )
