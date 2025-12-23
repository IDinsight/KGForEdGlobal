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

    # Capture the raw text if parsing/validation fails.
    if parsed is None:
        output_text = getattr(response, "output_text", None)
        raise QualityError(
            "Responses.parse returned no parsed output."
            + (f" output_text={output_text!r}" if output_text else ""),
            failed_content=output_text,  # Pass text back to caller
        )

    # Even if parsing succeeded, enforce quality checks.
    output_text = getattr(response, "output_text", None)
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
        model=model,
        instructions=instructions,
        input=input_items,
        temperature=0,
        top_p=1,
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
        validate_continuity_verdict(parsed)
    except QualityError as e:
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
            last_error = QualityError(
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

    raise QualityError(
        f"Extraction failed after {max_retries + 1} attempts for page " f"{page_index}."
    )


def validate_continuity_verdict(verdict: PageIRContinuityVerdict) -> None:
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

    # Kind consistency.
    if (
        verdict.is_continuation
        and verdict.continuation_kind == PageContinuationKind.NONE
    ):
        raise QualityError("is_continuation=true but continuation_kind=none.")
    if (not verdict.is_continuation) and verdict.continuation_kind in (
        PageContinuationKind.TABLE,
        PageContinuationKind.TEXT,
    ):
        # Allow 'unclear' when false, but table/text is usually inconsistent.
        raise QualityError("is_continuation=false but continuation_kind is table/text.")

    # 3. Logic: If continuation_kind is TABLE, we generally expect boundaries to be
    # modified.
    if (
        verdict.is_continuation
        and verdict.continuation_kind == PageContinuationKind.TABLE
    ) and (
        verdict.set_prev_item_boundary is not None
        and verdict.set_next_item_boundary is not None
        and verdict.set_prev_item_boundary == ItemBoundary.COMPLETE
        and verdict.set_next_item_boundary == ItemBoundary.COMPLETE
    ):
        raise QualityError(
            "Verdict claims Table continuation but suggests setting boundaries to "
            "COMPLETE. Tables continuing across pages usually require "
            "'truncated'/'resumed' boundaries."
        )

    # 4. Pairwise-safe boundary_state suggestions.
    if verdict.set_prev_boundary_state and verdict.set_prev_boundary_state not in (
        PageBoundaryState.STANDALONE,
        PageBoundaryState.CONTINUES_TO_NEXT,
    ):
        raise QualityError(
            "set_prev_boundary_state must be standalone or to_next (pairwise-safe)."
        )
    if verdict.set_next_boundary_state and verdict.set_next_boundary_state not in (
        PageBoundaryState.STANDALONE,
        PageBoundaryState.CONTINUES_FROM_PREV,
    ):
        raise QualityError(
            "set_next_boundary_state must be standalone or from_prev (pairwise-safe)."
        )

    # 5. repeats_header only makes sense for table continuations.
    if verdict.set_next_table_repeats_header is not None and not (
        verdict.is_continuation
        and verdict.continuation_kind == PageContinuationKind.TABLE
    ):
        raise QualityError(
            "set_next_table_repeats_header provided but verdict is not a table "
            "continuation."
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

    def _is_artifact(item: Any) -> bool:
        """Check if an item is an artifact block.

        Parameters
        ----------
        item
            The item to check.

        Returns
        -------
        bool
            True if the item is an artifact block, False otherwise.
        """

        if getattr(item, "kind", None) != "block":
            return False
        return getattr(item, "block_type", None) == BlockType.ARTIFACT

    def _is_resumed(boundary: str) -> bool:
        """Check if a boundary string indicates a resumed item.

        Parameters
        ----------
        boundary
            The boundary string to check.

        Returns
        -------
        bool
            True if the boundary indicates a resumed item, False otherwise.
        """

        return boundary == ItemBoundary.RESUMED.value

    def _is_truncated(boundary: str) -> bool:
        """Check if a boundary string indicates a truncated item.

        Parameters
        ----------
        boundary
            The boundary string to check.

        Returns
        -------
        bool
            True if the boundary indicates a truncated item, False otherwise.
        """

        return boundary == ItemBoundary.TRUNCATED.value

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
        raw_text = getattr(text_unit, "text", None) if text_unit else None
        list_items = getattr(item, "list_items", None) or []
        local_code = getattr(item, "local_code", None)

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
        if not (has_text or has_list or has_code):
            raise QualityError(
                f"Empty block at items[{i}]: text, list_items, and local_code are all "
                f"null/empty. Do not emit placeholder blocks (e.g., full-page artifacts)."
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

    # 3. Basic block invariants (lightweight, avoids semantic guesswork).
    for i, item in enumerate(items):
        if getattr(item, "kind", None) != "block":
            continue

        block_type = getattr(item, "block_type", None)
        text_unit = getattr(item, "text", None)
        list_items = getattr(item, "list_items", None) or []

        if block_type == BlockType.LIST:
            # Lists should be represented via list_items; text should be null.
            if text_unit is not None:
                raise QualityError(
                    f"List block must have text=null at items[{i}].text."
                )
            if not list_items:
                raise QualityError(
                    f"List block must have non-empty list_items at items[{i}].list_items."
                )
            for j, li in enumerate(list_items):
                # If marker is empty and text is short, it's likely a misclassification.
                marker = _safe_str(getattr(li, "marker", None))
                li_text = _textunit_text(getattr(li, "text", None))

                if not marker.strip() and len(li_text.strip()) < 3:
                    raise QualityError(
                        f"List item at items[{i}].list_items[{j}] has no marker and "
                        "insufficient text. This should likely be a paragraph."
                    )
        elif list_items:
            # Non-list blocks should not carry list_items.
            raise QualityError(
                f"Non-list block must have list_items=[] at items[{i}].list_items."
            )

    # 4. Table integrity (non-negotiable for deterministic stitching).
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
                # Spans should be >= 1; schema should already enforce, but keep a guard.
                col_span = int(getattr(cell, "col_span", 1) or 1)
                row_span = int(getattr(cell, "row_span", 1) or 1)
                if col_span < 1 or row_span < 1:
                    raise QualityError(
                        f"Invalid span at items[{i}].rows[{r}].cells[{c}] "
                        f"(col_span={col_span}, row_span={row_span})."
                    )

        # Lightweight column-count sanity checks (ignore spans).
        raw_widths = [len(getattr(rw, "cells", []) or []) for rw in rows]
        raw_widths = [w for w in raw_widths if w > 0]
        if raw_widths:
            max_raw = max(raw_widths)

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
                if max_raw > n_cols:
                    raise QualityError(
                        f"Table at items[{i}] has a row with {max_raw} cells but "
                        f"n_cols={n_cols}. Either increase n_cols or split/merge cells "
                        f"to match the visual grid."
                    )

            # Detect likely collapse: header shows multiple columns but body is mostly
            # 1 cell.
            if 0 < header_row_count < len(raw_widths):
                header_max = max(raw_widths[:header_row_count])
                body_widths = raw_widths[header_row_count:]
                if body_widths:
                    body_sorted = sorted(body_widths)
                    body_median = body_sorted[len(body_sorted) // 2]
                    frac_single = sum(w == 1 for w in body_widths) / len(body_widths)

                    # Strong guard against false positives (spanning label rows).
                    if header_max >= 3 and body_median <= 1 and frac_single >= 0.60:
                        raise QualityError(
                            f"Table at items[{i}] likely collapsed: header shows "
                            f"{header_max} columns, but {frac_single:.0%} of body rows "
                            f"have 1 cell (raw widths, spans ignored). "
                            "Split body rows into separate cells per visible column."
                        )

            # Catch wildly inconsistent widths overall (spans ignored).
            if max_raw >= 4:
                mode_raw, _ = Counter(raw_widths).most_common(1)[0]
                frac_single_all = sum(w == 1 for w in raw_widths) / len(raw_widths)
                if mode_raw == 1 and frac_single_all >= 0.70:
                    raise QualityError(
                        f"Table at items[{i}] appears mostly single-cell rows "
                        f"({frac_single_all:.0%} of rows have 1 cell). This often "
                        f"indicates the table grid was collapsed. Represent each "
                        f"visible column as a separate cell."
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

    # 5. Placeholder bbox detection (item bboxes are required).
    if len(top_level_bboxes) >= 6:
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

    # 6. Continuity consistency: page boundary_state vs. item boundary markers.
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

    # Only consider non-artifact items for continuity checks.
    non_artifact_items = [(i, it) for i, it in enumerate(items) if not _is_artifact(it)]

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

    # 7. Gross reading-order sanity check (avoid overfitting to 2-column pages). We
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
    )
