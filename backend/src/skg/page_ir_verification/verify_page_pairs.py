"""This module contains functionalities related to verifying the continuity between
pairs of page IR JSONs
"""

# Standard Library
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger
from PIL import Image
from pydantic_ai.result import RunUsage

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table
from skg.page_ir_verification.llm import verify_page_ir_pairs
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.page_ir_verification.utils import (
    EdgeVerdictRecord,
    PageIRVerificationDirs,
    is_artifact,
    is_probable_header_footer_noise,
)
from skg.schemas import VerificationConfig
from skg.utils.constants import BlockType, ItemBoundary
from skg.utils.general import make_dir, open_json_type, write_to_json


@dataclass
class AgentUsageBucket:
    """Accumulated token usage for a single agent type (e.g., verification or
    validation).

    Attributes
    ----------
    agent_name
        Human-readable label (e.g., "verification", "validation").
    cache_read_tokens
        Total cache-read input tokens across all calls.
    cache_write_tokens
        Total cache-write tokens across all calls.
    input_tokens
        Total prompt/input tokens across all calls.
    output_tokens
        Total completion/output tokens across all calls.
    requests
        Total API requests (including retries within a single agent run).
    runs
        Number of agent.run_sync() invocations.
    """

    agent_name: str
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    runs: int = 0

    def add_run_usage(self, usage: RunUsage) -> None:
        """Accumulate a single RunUsage into this bucket.

        Parameters
        ----------
        usage
            The RunUsage returned by `result.usage()`.
        """

        self.cache_read_tokens += usage.cache_read_tokens
        self.cache_write_tokens += usage.cache_write_tokens
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.requests += usage.requests
        self.runs += 1

    def to_dict(self) -> dict[str, int | str]:
        """Serialize to a JSON-friendly dictionary.

        Returns
        -------
        dict[str, int | str]
            Dictionary with all tracked fields.
        """

        return {
            "agent_name": self.agent_name,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "requests": self.requests,
            "runs": self.runs,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


@dataclass
class VerificationUsageTracker:
    """Track LLM token usage across the entire verification pipeline run.

    Maintains separate buckets for each agent type and provides a summary suitable for
    persisting in `verification_run.json`.
    """

    verification: AgentUsageBucket
    validation: AgentUsageBucket

    def __init__(self) -> None:
        """Initialize empty usage buckets for verification and validation agents."""

        self.verification = AgentUsageBucket(agent_name="verification")
        self.validation = AgentUsageBucket(agent_name="validation")

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-friendly dictionary with per-agent and total summaries.

        Returns
        -------
        dict[str, object]
            Dictionary containing `agents` breakdown and `totals`.
        """

        verification_d = self.verification.to_dict()
        validation_d = self.validation.to_dict()

        totals = {
            "cache_read_tokens": (
                self.verification.cache_read_tokens + self.validation.cache_read_tokens
            ),
            "cache_write_tokens": (
                self.verification.cache_write_tokens
                + self.validation.cache_write_tokens
            ),
            "input_tokens": (
                self.verification.input_tokens + self.validation.input_tokens
            ),
            "output_tokens": (
                self.verification.output_tokens + self.validation.output_tokens
            ),
            "requests": self.verification.requests + self.validation.requests,
            "runs": self.verification.runs + self.validation.runs,
            "total_tokens": (
                self.verification.input_tokens
                + self.verification.output_tokens
                + self.validation.input_tokens
                + self.validation.output_tokens
            ),
        }

        return {
            "agents": {"verification": verification_d, "validation": validation_d},
            "totals": totals,
        }


def _apply_visible_crop(
    *, candidates: list[tuple[int, Block | Table]], y_max: float, y_min: float
) -> list[tuple[int, Block | Table]]:
    """Restrict candidates to those whose bbox intersects [y_min, y_max].

    Parameters
    ----------
    candidates
        Pre-filtered candidate pool.
    y_max
        Upper bound of the visible crop region (inclusive).
    y_min
        Lower bound of the visible crop region (inclusive).

    Returns
    -------
    list[tuple[int, Block | Table]]
        Candidates intersecting the crop region (may be empty).
    """

    return [
        (i, item)
        for i, item in candidates
        if _bbox_intersects_y_range(bbox=item.bbox, y_max=y_max, y_min=y_min)
    ]


def _bbox_intersects_y_range(*, bbox: list[float], y_max: float, y_min: float) -> bool:
    """Return True if bbox intersects the vertical range [y_min, y_max]. bbox is
    [x0, y0, x1, y1] in full-page pixel coords.

    Parameters
    ----------
    bbox
        The bounding box to check.
    y_max
        The maximum y coordinate of the range.
    y_min
        The minimum y coordinate of the range.

    Returns
    -------
    bool
        True if the bbox intersects the y range, False otherwise.
    """

    y0 = float(bbox[1])
    y1 = float(bbox[3])

    return not (y1 < float(y_min) or y0 > float(y_max))


def _extract_figure_preview(figure: dict[str, Any], max_chars: int) -> dict[str, str]:
    """Extract verification fields from a figure dictionary.

    Parameters
    ----------
    figure
        The figure dictionary from PageIR.
    max_chars
        Maximum characters to keep for text previews.

    Returns
    -------
    dict[str, str]
        The figure preview dictionary.
    """

    preview: dict[str, str] = {}

    if f_kind := figure.get("figure_kind"):
        preview["kind"] = str(f_kind)

    if alt := figure.get("alt_text"):
        preview["alt_text"] = truncate_text(max_chars=max_chars, text=alt)

    # Handle text wrappers for caption and embedded_text.
    for field in ("caption", "embedded_text"):
        obj = figure.get(field)

        if isinstance(obj, dict):
            text = _get_text_content(obj)

            if text:
                preview[field] = truncate_text(max_chars=max_chars, text=text)

    return preview


def _extract_list_preview(list_items: list[Any]) -> list[str]:
    """Extract a short preview of list items (max 6).

    Parameters
    ----------
    list_items
        The list of list item objects.

    Returns
    -------
    list[str]
        The list of preview strings.
    """

    preview: list[str] = []

    for li in list_items[:6]:
        if isinstance(li, dict):
            marker = li.get("marker") or ""
            text = _get_text_content(li.get("text"))
            li_text = truncate_text(max_chars=180, text=text)
            preview.append((marker + " " + li_text).strip())
        else:
            preview.append(truncate_text(max_chars=180, text=str(li)))

    return preview


def _filter_candidate_pool(
    *, image_height: float, items: list[Block | Table]
) -> list[tuple[int, Block | Table]]:
    """Filter items to exclude artifacts and header/footer noise.

    Falls back to the full item list if every item was filtered out (avoids empty-pool
    crashes on sparse pages).

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        All PageIR items on the page.

    Returns
    -------
    list[tuple[int, Block | Table]]
        Non-empty list of (item_index, item) pairs.
    """

    candidates = [
        (i, item)
        for i, item in enumerate(items)
        if not (
            is_artifact(item)
            or is_probable_header_footer_noise(image_height=image_height, item=item)
        )
    ]

    return candidates or list(enumerate(items))


def _get_text_content(obj: Any) -> str:
    """Safely extract 'text' field from a dictionary wrapper.

    Parameters
    ----------
    obj
        The object to extract text from.

    Returns
    -------
    str
        The extracted text, or an empty string if not found.
    """

    return str(obj.get("text") or "") if isinstance(obj, dict) else ""


def _is_heading_or_caption_block(item: Block | Table) -> bool:
    """Return True if item is a Block with block_type HEADING or CAPTION.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is a heading or caption block.
    """

    return isinstance(item, Block) and item.block_type in {
        BlockType.CAPTION,
        BlockType.HEADING,
    }


def _make_block_excerpt(
    *, bbox: Any, item: dict[str, Any], local_code: Any, max_text_chars: int
) -> dict[str, Any]:
    """Handle Block specific extraction (Text, Lists, Figures).

    Parameters
    ----------
    bbox
        The bounding box of the block.
    item
        The PageIR block item dictionary.
    local_code
        The local code of the block.
    max_text_chars
        Maximum characters to keep for text previews.

    Returns
    -------
    dict[str, Any]
        The block excerpt dictionary.
    """

    text = _get_text_content(item.get("text"))
    text_preview = truncate_text(max_chars=max_text_chars, text=text)

    list_items = item.get("list_items")
    list_preview = _extract_list_preview(list_items) if list_items else []

    figure = item.get("figure")
    figure_preview = {}

    if isinstance(figure, dict):
        figure_preview = _extract_figure_preview(figure, max_text_chars)

    output: dict[str, Any] = {
        "kind": "block",
        "bbox": bbox,
        "local_code": local_code,
        "block_type": item["block_type"],
    }

    if text_preview:
        output["text_preview"] = text_preview
    if list_preview:
        output["list_preview"] = list_preview
    if figure_preview:
        output["figure_preview"] = figure_preview

    return output


def _make_table_excerpt(
    *,
    bbox: Any,
    item: dict[str, Any],
    local_code: Any,
    max_cell_chars: int,
    preview_rows: int,
) -> dict[str, Any]:
    """Handle Table specific extraction.

    Parameters
    ----------
    bbox
        The bounding box of the table.
    item
        The PageIR table item dictionary.
    local_code
        The local code of the table.
    max_cell_chars
        Maximum characters to keep per table cell in previews.
    preview_rows
        Number of table rows to include in header/body previews.

    Returns
    -------
    dict[str, Any]
        The table excerpt dictionary.
    """

    rows = item.get("rows") or []
    header_row_count = int(item.get("header_row_count") or 0)

    header_rows = rows[: min(header_row_count, preview_rows)]
    body_rows = rows[header_row_count:]
    top_body = body_rows[:preview_rows]

    # Show bottom rows whenever the table extends beyond the top preview, but
    # de-duplicate any overlap (small tables where top and bottom slices intersect).
    if len(body_rows) > preview_rows:
        bottom_slice = body_rows[-preview_rows:]

        # Only keep rows from the bottom slice that aren't already in the top slice.
        top_end_index = preview_rows  # index into body_rows
        bottom_start_index = len(body_rows) - preview_rows
        bottom_body = (
            bottom_slice
            if bottom_start_index >= top_end_index
            else body_rows[top_end_index:]
        )
    else:
        bottom_body = []

    return {
        "kind": "table",
        "bbox": bbox,
        "local_code": local_code,
        "header_row_count": header_row_count,
        "n_cols": item.get("n_cols"),
        "row_count": len(rows),
        "header_preview": [
            _table_row_preview(max_cell_chars=max_cell_chars, row=r)
            for r in header_rows
        ],
        "top_rows_preview": [
            _table_row_preview(max_cell_chars=max_cell_chars, row=r) for r in top_body
        ],
        "bottom_rows_preview": [
            _table_row_preview(max_cell_chars=max_cell_chars, row=r)
            for r in bottom_body
        ],
    }


def _pick_bottommost(
    candidates: list[tuple[int, Block | Table]],
) -> tuple[int, Block | Table]:
    """Pick the best bottom-of-page candidate from a pre-sorted list.

    Candidates must already be sorted by bottom-edge (y1) descending.

    Selection priority:

    1. Extractor-flagged TRUNCATED/BOTH items (preferred boundary hints).
    2. Tables near the bottom (within top-5 by y1).
    3. Non-heading/non-caption blocks.
    4. Absolute bottom item (last resort).

    Parameters
    ----------
    candidates
        Non-empty list of (item_index, item) pairs sorted by y1 descending.

    Returns
    -------
    tuple[int, Block | Table]
        The picked candidate index and item.
    """

    preferred = [
        (i, item)
        for i, item in candidates
        if item.boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}
    ]

    def _pick(
        sorted_candidates: list[tuple[int, Block | Table]],
    ) -> tuple[int, Block | Table] | None:
        """Pick the best candidate from the provided list using the defined priority.

        Parameters
        ----------
        sorted_candidates
            List of (item_index, item) pairs sorted by y1 descending.

        Returns
        -------
        tuple[int, Block | Table] | None
            The picked candidate index and item, or None if no suitable candidate found.
        """

        # Prefer a Table if it is "near" the bottom (within the bottom 5 items).
        for i, item in sorted_candidates[:5]:
            if item.kind == "table":
                return i, item

        # Otherwise pick the first non-table block, but never anchor on HEADING/CAPTION.
        for i, item in sorted_candidates:
            if item.kind != "table" and not _is_heading_or_caption_block(item):
                return i, item

        return None

    picked = _pick(preferred)

    if picked is not None:
        return picked

    picked = _pick(candidates)

    if picked is not None:
        return picked

    return candidates[0]


def _pick_topmost(
    *, candidates: list[tuple[int, Block | Table]], prev_item: Block | Table
) -> tuple[int, Block | Table]:
    """Pick the best top-of-page candidate from a pre-sorted list.

    Candidates must already be sorted by top-edge (y0) ascending.

    Selection priority:

    1. Extractor-flagged RESUMED/BOTH items matching prev_item kind.
    2. Same-kind match (Table→Table or non-heading Block→Block).
    3. Absolute top item (last resort).

    Parameters
    ----------
    candidates
        Non-empty list of (item_index, item) pairs sorted by y0 ascending.
    prev_item
        The chosen previous page candidate item.

    Returns
    -------
    tuple[int, Block | Table]
        The picked candidate index and item.
    """

    preferred = [
        (i, item)
        for i, item in candidates
        if item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
    ]

    # Build a deduplicated search order: preferred items first, then remaining
    # candidates (preserving positional order within each group).
    seen_indices: set[int] = {i for i, _ in preferred}
    unique_ordered: list[tuple[int, Block | Table]] = list(preferred) + [
        (i, item) for i, item in candidates if i not in seen_indices
    ]

    if prev_item.kind == "table":
        table_match = next(
            ((i, item) for i, item in unique_ordered if item.kind == "table"),
            None,
        )
        return table_match if table_match is not None else candidates[0]

    valid_match = next(
        (
            (i, item)
            for i, item in unique_ordered
            if item.kind != "table" and not _is_heading_or_caption_block(item)
        ),
        None,
    )
    return valid_match if valid_match is not None else candidates[0]


def _table_row_preview(*, max_cell_chars: int, row: dict[str, Any]) -> list[str]:
    """Convert a row dict into a list of truncated cell strings.

    Parameters
    ----------
    max_cell_chars
        Maximum characters to keep per cell.
    row
        The table row dictionary.

    Returns
    -------
    list[str]
        List of truncated cell strings.
    """

    cells = row.get("cells") or []
    output: list[str] = []

    for cell in cells:
        if not isinstance(cell, dict):
            output.append(truncate_text(max_chars=max_cell_chars, text=str(cell)))
            continue

        text_or_none = cell.get("text", None)
        text = text_or_none["text"] if isinstance(text_or_none, dict) else ""
        output.append(truncate_text(max_chars=max_cell_chars, text=text))

    return output


def bottom_continuity_candidates(
    *,
    image_height: float,
    items: list[Block | Table],
    k: int = 3,
    visible_y_min: float | None = None,
) -> list[tuple[int, Block | Table]]:
    """Return up to k strong "bottom of page" candidates for continuity checks.

    This is a generalization of `bottommost_continuity_candidate`. The first element of
    the returned list is guaranteed to match the choice made by
    `bottommost_continuity_candidate` and subsequent candidates are additional
    near-bottom items that are plausible alternatives (e.g., a paragraph above a
    complete table).

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of PageIR items on the page.
    k
        Maximum number of candidates to return. Must be >= 1.
    visible_y_min
        If provided, restrict candidate selection to items whose bbox intersects the
        visible crop range [visible_y_min, image_height] in full-page coordinates.

    Returns
    -------
    list[tuple[int, Block | Table]]
        A list of (item_index, item) pairs. Length is in [1, k].

    Raises
    ------
    ValueError
        If k < 1, or if no candidates are found after filtering and cropping.
    """

    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")

    # Build the candidate pool once and reuse for both primary pick and extras.
    candidates = _filter_candidate_pool(image_height=image_height, items=items)

    if visible_y_min is not None:
        cropped = _apply_visible_crop(
            candidates=candidates, y_max=float(image_height), y_min=float(visible_y_min)
        )
        candidates = cropped

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by bottom-edge descending.
    candidates.sort(key=lambda c: float(c[1].bbox[3]), reverse=True)

    # Pick the primary candidate using the same logic as
    # bottommost_continuity_candidate.
    first_i, first_item = _pick_bottommost(candidates=candidates)

    output: list[tuple[int, Block | Table]] = [(first_i, first_item)]
    seen: set[int] = {first_i}

    # Fill remaining slots with additional plausible near-bottom anchors.
    for i, item in candidates:
        if len(output) >= k:
            break
        if i in seen:
            continue

        # Always allow tables; for blocks avoid heading/caption as text anchors.
        if item.kind != "table" and _is_heading_or_caption_block(item):
            continue

        output.append((i, item))
        seen.add(i)

    return output


def crop_image_to_ymax(
    *, input_png_fp: Path, output_png_fp: Path, y_max: float
) -> None:
    """Crop a rendered page PNG to [0, y_max] in pixel coordinates.

    Parameters
    ----------
    input_png_fp
        Full-page PNG path (the extraction-time rendered page image).
    output_png_fp
        Where to write the cropped PNG.
    y_max
        The maximum Y coordinate (in pixels) to crop to. Values outside the image
        height will be clamped to the image bounds.
    """

    with Image.open(input_png_fp) as img:
        w, h = img.size
        y = max(1, min(int(round(y_max)), h))

        make_dir(output_png_fp.parent)
        img.crop((0, 0, w, y)).save(output_png_fp)


def execute_verification_attempts(
    *,
    config: VerificationConfig,
    page_images_dir: Path,
    page_index: int,
    pairs: list[tuple[int, Block | Table, int, Block | Table]],
    next_crop_fp: Path,
    usage_tracker: VerificationUsageTracker,
) -> dict[str, Any]:
    """Execute the ordered list of verification attempts for a single page pair,
    accumulating results and selecting the best verdict.

    Parameters
    ----------
    config
        The verification configuration.
    page_images_dir
        Directory containing page images.
    page_index
        The 0-based index of the previous page (N).
    pairs
        List of candidate pairs to verify.
    next_crop_fp
        Filepath to the cropped image of the next page.
    usage_tracker
        Tracker to accumulate token usage across all verification attempts.

    Returns
    -------
    dict[str, Any]
        A dictionary containing:
          - attempt_summaries: List of attempt summaries.
          - selected_verdict: The selected PageIRContinuityVerdict.
          - selected_prev_index: The index of the selected previous item.
          - selected_next_index: The index of the selected next item.

    Raises
    ------
    RuntimeError
        If all verification attempts fail.
    """

    attempt_summaries: list[dict[str, Any]] = []

    # Store successful attempts as tuples: (attempt_no, confidence, pi, ni, verdict).
    successful_attempts: list[tuple[int, float, int, int, PageIRContinuityVerdict]] = []

    for attempt_no, (pi, pitem, ni, nitem) in enumerate(pairs):
        try:
            verdict = verify_page_ir_pairs(
                model=config.model,
                next_item=nitem.model_dump(mode="json"),
                next_item_excerpt=make_verification_excerpt(
                    item=strip_continuity_hints(nitem.model_dump(mode="json"))
                ),
                next_page_index=page_index + 1,
                next_png=next_crop_fp,
                prev_item=pitem.model_dump(mode="json"),
                prev_item_excerpt=make_verification_excerpt(
                    item=strip_continuity_hints(pitem.model_dump(mode="json"))
                ),
                prev_page_index=page_index,
                prev_png=page_images_dir / f"{page_index:04}.png",
                usage_tracker=usage_tracker,
            )
        except Exception as e:  # pylint: disable=broad-except
            attempt_summaries.append(
                {
                    "attempt_no": attempt_no,
                    "prev_item_index": pi,
                    "next_item_index": ni,
                    "error": str(e),
                }
            )
            continue

        attempt_summaries.append(
            {
                "attempt_no": attempt_no,
                "prev_item_index": pi,
                "next_item_index": ni,
                "is_continuation": verdict.is_continuation,
                "continuation_kind": verdict.continuation_kind.value,
                "confidence": verdict.confidence,
                "set_next_table_repeats_header": verdict.set_next_table_repeats_header,
            }
        )

        successful_attempts.append((attempt_no, verdict.confidence, pi, ni, verdict))

        # Early exit only when we find a confident positive continuation. A confident
        # "no continuation" on the primary pair should NOT prevent us from trying
        # alternate candidate pairs that may reveal the true continuation.
        if (
            verdict.is_continuation
            and verdict.confidence >= config.min_confidence_to_patch
        ):
            break

    if not successful_attempts:
        errors = [s["error"] for s in attempt_summaries if "error" in s]
        raise RuntimeError(
            f"All {len(pairs)} verification attempts failed for page pair "
            f"{page_index}->{page_index + 1}. Errors: {errors}"
        )

    # Selection: prefer confident positive continuations over confident negatives. A
    # high-confidence "no continuation" is less trustworthy than a moderate-confidence
    # "yes continuation" found on an alternate pair.
    confident_positives = [
        a
        for a in successful_attempts
        if a[4].is_continuation and a[1] >= config.min_confidence_to_patch
    ]

    if confident_positives:
        # Among confident positives, pick the highest confidence.
        selected = max(confident_positives, key=lambda x: x[1])
    else:
        # No confident positive found--pick highest confidence overall.
        selected = max(successful_attempts, key=lambda x: x[1])

    _, _, selected_prev_index, selected_next_index, selected_verdict = selected

    return {
        "attempt_summaries": attempt_summaries,
        "selected_verdict": selected_verdict,
        "selected_prev_index": selected_prev_index,
        "selected_next_index": selected_next_index,
    }


def generate_candidate_pairs(
    *,
    crop_y_max: float,
    next_page_ir: PageIR,
    prev_candidates: list[tuple[int, Block | Table]],
) -> tuple[list[tuple[int, Block | Table, int, Block | Table]], dict[str, int]]:
    """Generate and deduplicate the list of candidate pairs to verify.

    Parameters
    ----------
    crop_y_max
        The y-coordinate (in original page coordinates) used to crop the next page
        image. Used as `visible_y_max` when selecting top-of-page candidates on the
        next page.
    next_page_ir
        The PageIR of the next page.
    prev_candidates
        Pre-computed bottom-of-page candidates from the previous page, as returned by
        `bottom_continuity_candidates()`. The first element is the primary candidate.

    Returns
    -------
    tuple[list[tuple[int, Block | Table, int, Block | Table]], dict[str, int]]
        A tuple of (candidate pairs list, primary indices dict).
    """

    next_items = next_page_ir.items

    # prev_candidates already computed by caller — reuse directly.
    prev_index, prev_item = prev_candidates[0]

    # Get top candidates on next page, restricted to the crop region. crop_y_max is the
    # y-coordinate the caller used to create the crop image, so it exactly defines the
    # visible region.
    next_candidates_primary = top_continuity_candidates_paired(
        image_height=next_page_ir.image_height,
        items=next_items,
        prev_item=prev_item,
        visible_y_max=crop_y_max,
    )
    next_index, next_item = next_candidates_primary[0]

    # Build ordered candidate pairs: Always try primary pair first. Then try additional
    # previous candidates, preferring same-kind next candidates first.
    pairs: list[tuple[int, Block | Table, int, Block | Table]] = [
        (prev_index, prev_item, next_index, next_item)
    ]

    # Start with the primary pair. Then, for each possible bottom candidate, get a few
    # top candidates on the next page and order them so same-kind is tried first
    # (table -> table, block -> block, then mixed). Duplicate pairs are expected and
    # removed by the de-dupe step below.
    for pi, pitem in prev_candidates:
        next_candidates = (
            next_candidates_primary
            if pi == prev_index
            else top_continuity_candidates_paired(
                image_height=next_page_ir.image_height,
                items=next_items,
                prev_item=pitem,
                visible_y_max=crop_y_max,
            )
        )

        # Same-kind first, then cross-kind.
        same = [(ni, nit) for (ni, nit) in next_candidates if nit.kind == pitem.kind]
        other = [(ni, nit) for (ni, nit) in next_candidates if nit.kind != pitem.kind]

        for ni, nitem in same + other:
            pairs.append((pi, pitem, ni, nitem))

    # De-dupe pairs (to avoid re-checking the same pair), preserve order, and cap
    # attempts (to limit model calls).
    seen_pairs: set[tuple[int, int]] = set()
    deduped_pairs: list[tuple[int, Block | Table, int, Block | Table]] = []

    for pi, pitem, ni, nitem in pairs:
        key = (pi, ni)

        if key not in seen_pairs:
            seen_pairs.add(key)
            deduped_pairs.append((pi, pitem, ni, nitem))

            if len(deduped_pairs) >= 9:
                break

    primary_indices = {"prev_item_index": prev_index, "next_item_index": next_index}

    return deduped_pairs, primary_indices


def load_edge_verdict_from_pair_report(pair_report_fp: Path) -> EdgeVerdictRecord:
    """Load a single EdgeVerdictRecord from a persisted pair report JSON.

    Parameters
    ----------
    pair_report_fp
        Path to the pair report JSON file (e.g., `0003_0004.json`).

    Returns
    -------
    EdgeVerdictRecord
        The reconstructed edge verdict record.

    Raises
    ------
    KeyError
        If the pair report JSON is missing required fields.
    """

    data = open_json_type(pair_report_fp)
    verdict = PageIRContinuityVerdict.model_validate(data["verdict"])
    selection = data["selected_candidate_selection"]
    return EdgeVerdictRecord(
        next_item_index=selection["next_item_index"],
        next_page_index=verdict.next_page_index,
        prev_item_index=selection["prev_item_index"],
        prev_page_index=verdict.prev_page_index,
        verdict=verdict,
    )


def make_verification_excerpt(
    *,
    item: dict[str, Any],
    max_cell_chars: int = 80,
    max_text_chars: int = 600,
    preview_rows: int = 3,
) -> dict[str, Any]:
    """Create a compact, verification-only excerpt of a PageIR item.

    Parameters
    ----------
    item
        The PageIR item dictionary.
    max_cell_chars
        Maximum characters to keep per table cell in previews.
    max_text_chars
        Maximum characters to keep for text previews.
    preview_rows
        Number of table rows to include in header/body previews.

    Returns
    -------
    dict[str, Any]
        The verification excerpt of the item.
    """

    bbox = item["bbox"]
    kind = item["kind"]
    local_code = item.get("local_code", None)

    if kind == "table":
        return _make_table_excerpt(
            bbox=bbox,
            item=item,
            local_code=local_code,
            max_cell_chars=max_cell_chars,
            preview_rows=preview_rows,
        )

    if kind == "block":
        return _make_block_excerpt(
            bbox=bbox, item=item, local_code=local_code, max_text_chars=max_text_chars
        )

    return {
        "kind": kind or "unknown",
        "bbox": bbox,
        "local_code": local_code,
        "preview": truncate_text(
            max_chars=max_text_chars, text=_get_text_content(item.get("text"))
        ),
    }


def strip_continuity_hints(item_json: dict[str, Any]) -> dict[str, Any]:
    """Remove continuity metadata so the LLM isn't biased by extractor state.

    Parameters
    ----------
    item_json
        The item JSON dictionary.

    Returns
    -------
    dict[str, Any]
        The cleaned item JSON dictionary.
    """

    output = deepcopy(item_json)
    output.pop("boundary", None)

    # Only tables have repeats_header.
    if output.get("kind") == "table":
        output.pop("repeats_header", None)

    return output


def top_continuity_candidates_paired(
    *,
    image_height: float,
    items: list[Block | Table],
    k: int = 3,
    prev_item: Block | Table,
    visible_y_max: float | None = None,
) -> list[tuple[int, Block | Table]]:
    """Return up to k strong "top of page" candidates for continuity checks.

    This is a generalization of `topmost_continuity_candidate_paired`. The first
    element of the returned list is guaranteed to match the choice made by
    `topmost_continuity_candidate_paired` and subsequent candidates are additional
    top-visible items ordered to try same-kind continuations first (Table -> Table,
    Block -> Block), then cross-kind.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of PageIR items on the next page.
    k
        Maximum number of candidates to return. Must be >= 1.
    prev_item
        The chosen previous page candidate item.
    visible_y_max
        If provided, restrict candidate selection to items whose bbox intersects the
        visible crop range [0, visible_y_max] in full-page coordinates.

    Returns
    -------
    list[tuple[int, Block | Table]]
        A list of (item_index, item) pairs. Length is in [1, k].

    Raises
    ------
    ValueError
        If no non-artifact items are found.
        If no top-crop-visible candidates are found when visible_y_max is provided.
    """

    assert k >= 1, f"k must be >= 1, got {k}"

    # Build the candidate pool once and reuse for both primary pick and extras.
    candidates = _filter_candidate_pool(image_height=image_height, items=items)

    if visible_y_max is not None:
        cropped = _apply_visible_crop(
            candidates=candidates, y_max=visible_y_max, y_min=0.0
        )
        candidates = cropped

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by top-edge ascending.
    candidates.sort(key=lambda p: float(p[1].bbox[1]))

    # Pick the primary candidate using the same logic as
    # topmost_continuity_candidate_paired.
    first_i, first_item = _pick_topmost(candidates=candidates, prev_item=prev_item)

    output: list[tuple[int, Block | Table]] = [(first_i, first_item)]
    seen: set[int] = {first_i}

    # Prefer same-kind first, then cross-kind, while keeping reading-order stability.
    same_kind: list[tuple[int, Block | Table]] = []
    other_kind: list[tuple[int, Block | Table]] = []

    for i, item in candidates:
        # For blocks, avoid heading/caption as text anchors.
        if i in seen or (item.kind != "table" and _is_heading_or_caption_block(item)):
            continue

        if item.kind == prev_item.kind:
            same_kind.append((i, item))
        else:
            other_kind.append((i, item))

    for bucket in (same_kind, other_kind):
        for i, item in bucket:
            if len(output) >= k:
                break

            if i in seen:
                continue

            output.append((i, item))
            seen.add(i)

    return output


def truncate_text(*, max_chars: int, text: str) -> str:
    """Return a single-line truncated preview string.

    Parameters
    ----------
    max_chars
        The maximum number of characters to return (including ellipsis).
    text
        The text to truncate.

    Returns
    -------
    str
        The truncated text.
    """

    text = (text or "").replace("\n", " ").strip()

    return (
        text
        if len(text) <= max_chars
        else text[: max(0, max_chars - 3)].rstrip() + "..."
    )


def verify_single_page_pair(
    *,
    config: VerificationConfig,
    page_images_dir: Path,
    page_index: int,
    page_irs: dict[int, PageIR],
    usage_tracker: VerificationUsageTracker,
    verification_dirs: PageIRVerificationDirs,
) -> EdgeVerdictRecord | None:
    """Handle the verification logic for a specific pair of pages.

    Parameters
    ----------
    config
        The verification run configuration.
    page_images_dir
        Directory containing the page images.
    page_index
        The index of the previous page in the pair to verify.
    page_irs
        The dictionary of page IRs by page index.
    usage_tracker
        Tracker to accumulate token usage from both verification and validation agents.
    verification_dirs
        The verification directories.

    Returns
    -------
    EdgeVerdictRecord | None
        The created EdgeVerdictRecord if verification was performed, or None if skipped.
    """

    assert (
        page_index in page_irs and (page_index + 1) in page_irs
    ), f"Missing page IR for {page_index} or {page_index + 1}"

    prev_page_ir, next_page_ir = page_irs[page_index], page_irs[page_index + 1]

    # Skip if either page has no items.
    if not (prev_page_ir.items and next_page_ir.items):
        logger.warning(
            f"Skipping continuity check for pages {page_index}-{page_index + 1}: "
            f"prev_items={len(prev_page_ir.items)} "
            f"next_items={len(next_page_ir.items)}"
        )
        return None

    # Select the primary next candidate on the FULL next page (N + 1) without any crop
    # restrictions, then crop page N + 1 down to just below that candidate (+ padding).
    prev_items = prev_page_ir.items
    next_items = next_page_ir.items
    prev_candidates = bottom_continuity_candidates(
        image_height=prev_page_ir.image_height, items=prev_items
    )
    _, prev_item = prev_candidates[0]
    next_candidates_primary_full = top_continuity_candidates_paired(
        image_height=next_page_ir.image_height,
        items=next_items,
        prev_item=prev_item,
        visible_y_max=None,  # Full page
    )

    # Crop using the lowest bbox bottom (y1) among the top-3 next candidates. This
    # avoids cropping out the 2nd/3rd candidate that we may still test.
    top_k = next_candidates_primary_full[:3]

    if not top_k:
        # Extremely defensive fallback; should not happen if next_page_ir has items.
        crop_y_max = float(config.next_page_crop_padding_px) + 1.0
    else:
        max_y1 = max(float(nit.bbox[3]) for _, nit in top_k)
        crop_y_max = max_y1 + float(config.next_page_crop_padding_px)

    next_crop_fp = (
        verification_dirs.page_irs_pair_crops / f"{page_index + 1:04}_top.png"
    )

    crop_image_to_ymax(
        input_png_fp=page_images_dir / f"{page_index + 1:04}.png",
        output_png_fp=next_crop_fp,
        y_max=crop_y_max,
    )

    pairs, primary_indices = generate_candidate_pairs(
        crop_y_max=crop_y_max,
        next_page_ir=next_page_ir,
        prev_candidates=prev_candidates,
    )

    # Run verification attempts on the generated pairs.
    logger.info(
        f"Verifying continuity between pages {page_index} and {page_index + 1}..."
    )

    result = execute_verification_attempts(
        config=config,
        next_crop_fp=next_crop_fp,
        page_images_dir=page_images_dir,
        page_index=page_index,
        pairs=pairs,
        usage_tracker=usage_tracker,
    )

    # Record the edge verdict (single selected pair per boundary).
    selected_verdict = result["selected_verdict"]
    selected_verdict.prev_page_index = page_index
    selected_verdict.next_page_index = page_index + 1
    record = EdgeVerdictRecord(
        next_item_index=result["selected_next_index"],
        next_page_index=page_index + 1,
        prev_item_index=result["selected_prev_index"],
        prev_page_index=page_index,
        verdict=selected_verdict,
    )

    write_to_json(
        fp=verification_dirs.page_irs_pair_reports
        / f"{page_index:04}_{page_index + 1:04}.json",
        json_info={
            "attempts": result["attempt_summaries"],
            "primary_candidate_selection": primary_indices,
            "selected_candidate_selection": {
                "prev_item_index": result["selected_prev_index"],
                "next_item_index": result["selected_next_index"],
            },
            "verdict": selected_verdict.model_dump(mode="json"),
        },
    )

    logger.success(
        f"Finished verifying continuity between pages {page_index} and {page_index + 1}!"
    )

    return record
