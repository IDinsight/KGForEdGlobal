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

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table
from skg.page_ir_verification.llm import VerificationUsageTracker, verify_page_ir_pairs
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.page_ir_verification.utils import (
    EdgeVerdictRecord,
    PageIRVerificationDirs,
    is_artifact,
    is_probable_header_footer_noise,
)
from skg.schemas import VerificationConfig
from skg.utils.constants import BlockType, ItemBoundary
from skg.utils.general import make_dir, write_to_json


@dataclass(frozen=True)
class CandidatePairSpec:
    """Ordered candidate pair proposed for continuity verification.

    Attributes
    ----------
    crop_y_max
        Pair-specific crop limit for the next-page image.
    next_index
        Item index on page N+1.
    next_item
        Candidate continuation item on page N+1.
    next_rank
        Rank of the next-page candidate within its ordered pool for this previous
        anchor.
    prev_index
        Item index on page N.
    prev_item
        Candidate boundary anchor on page N.
    prev_rank
        Rank of the previous-page candidate within the ordered bottom-candidate pool.
    """

    crop_y_max: float
    next_index: int
    next_item: Block | Table
    next_rank: int
    prev_index: int
    prev_item: Block | Table
    prev_rank: int


@dataclass(frozen=True)
class VerifiedCandidateAttempt:
    """Successful verification attempt for a single candidate pair."""

    attempt_no: int
    crop_fp: Path
    spec: CandidatePairSpec
    verdict: PageIRContinuityVerdict


def _apply_visible_crop(
    *, candidates: list[tuple[int, Block | Table]], y_max: float, y_min: float
) -> list[tuple[int, Block | Table]]:
    """Restrict candidates to those whose bbox intersects [y_min, y_max].

    NB: item.bbox[1] is y0, item.bbox[3] is y1.

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

    f_y_min = float(y_min)
    f_y_max = float(y_max)

    return [
        (i, item)
        for i, item in candidates
        if not (float(item.bbox[3]) < f_y_min or float(item.bbox[1]) > f_y_max)
    ]


def _bottom_continuity_candidates(
    *,
    image_height: float,
    items: list[Block | Table],
    k: int = 3,
    visible_y_min: float | None = None,
) -> list[tuple[int, Block | Table]]:
    """Return up to k bottom-of-page candidates for continuity checks.

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
        candidates = _apply_visible_crop(
            candidates=candidates, y_max=float(image_height), y_min=float(visible_y_min)
        )

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by bottom-edge descending.
    candidates.sort(key=lambda c: float(c[1].bbox[3]), reverse=True)

    # Pick the primary bottommost candidate.
    first_i, first_item = _pick_bottommost(candidates)

    output: list[tuple[int, Block | Table]] = [(first_i, first_item)]
    seen: set[int] = {first_i}

    # Fill remaining slots with additional plausible near-bottom anchors.
    for i, item in candidates:
        if len(output) >= k:
            break
        if i in seen:
            continue

        # Always allow tables; for blocks avoid heading/caption as text anchors.
        if item.kind == "block" and _is_heading_or_caption_block(item):
            continue

        output.append((i, item))
        seen.add(i)

    return output


def _candidate_family(item: Block | Table) -> str:
    """Return the structural family used for continuity candidate matching.

    Families are intentionally coarser than full semantics but finer than `item.kind`.
    Tables only match tables, figures only match figures, and all other non-table
    blocks are treated as text/list-like block candidates.

    Parameters
    ----------
    item
        The item to classify.

    Returns
    -------
    str
        The candidate family: "table", "figure", or "block".
    """

    if item.kind == "table":
        return "table"
    if _is_figure_block(item):
        return "figure"

    return "block"


def _ensure_pair_specific_crop(
    *,
    crop_cache: dict[tuple[int, int], Path],
    next_page_image_fp: Path,
    next_page_index: int,
    output_dir: Path,
    spec: CandidatePairSpec,
) -> Path:
    """Create or reuse the pair-specific top crop of page N + 1 used for continuity
    verification.

    This prepares the "next page" image evidence for a single `CandidatePairSpec`. In
    the verification workflow, page N is always shown in full, but page N + 1 is shown
    only as a top crop that extends far enough to include the selected next-page
    candidate plus any configured padding. This keeps the verifier focused on the
    relevant boundary region while avoiding unnecessary visual context lower on page
    N + 1.

    The crop is specific to the candidate pair only through the next-page anchor: the
    cache key is `(spec.next_index, round(spec.crop_y_max))`. If a crop with the same
    target item and crop height has already been rendered during this page-pair run,
    the cached file path is returned immediately.

    Otherwise, the function:

    1. Builds a deterministic output filename from the next-page index, the candidate
        item's index, and the rounded crop height.
    2. Opens the full-page PNG for page N + 1.
    3. Clamps `spec.crop_y_max` to the valid image range `[1, image_height]`.
    4. Crops the rectangle `(0, 0, image_width, y_max)`, i.e. the full page width from
        the top of the page down to the requested bottom edge.
    5. Saves the crop, records it in `crop_cache`, and returns the path.

    NB:

    1. This function does **not** decide how much of page N + 1 should be visible; it
        trusts `spec.crop_y_max`, which is computed upstream during candidate-pair
        generation.
    2. The crop is an evidence-delivery optimization for the verifier. Candidate
        discovery still uses the full next-page PageIR JSON.

    Parameters
    ----------
    crop_cache
        In-memory cache mapping `(next_item_index, rounded_crop_y_max)` to the saved
        crop path for the current page-pair verification run.
    next_page_image_fp
        Path to the full rendered PNG for page N + 1.
    next_page_index
        Zero-based page index of page N + 1. Used only for deterministic output naming.
    output_dir
        Directory where pair-specific next-page crop images are written.
    spec
        Candidate pair specification containing the selected next-page item and the
        maximum y-coordinate to include in the crop.

    Returns
    -------
    Path
        Filesystem path to the rendered or reused crop image for page N+1.
    """

    crop_key = (spec.next_index, int(round(spec.crop_y_max)))
    cached_fp = crop_cache.get(crop_key)

    if cached_fp is not None:
        return cached_fp

    crop_y_max_px = int(round(spec.crop_y_max))
    crop_fp = output_dir / (
        f"{next_page_index:04}_top_to_item_{spec.next_index:03}_"
        f"ymax_{crop_y_max_px:05}.png"
    )
    make_dir(crop_fp.parent)

    with Image.open(next_page_image_fp) as img:
        w, h = img.size
        y = max(1, min(int(round(spec.crop_y_max)), h))
        img.crop((0, 0, w, y)).save(crop_fp)

    crop_cache[crop_key] = crop_fp

    return crop_fp


def _execute_verification_attempts(
    *,
    config: VerificationConfig,
    next_page_image_fp: Path,
    page_index: int,
    pair_crop_dir: Path,
    pairs: list[CandidatePairSpec],
    usage_tracker: VerificationUsageTracker,
) -> dict[str, Any]:
    """Execute ordered verification attempts for a single page boundary.

    Parameters
    ----------
    config
        The verification configuration.
    next_page_image_fp
        Full-page PNG path for page N+1.
    page_index
        The 0-based index of the previous page (N).
    pair_crop_dir
        Directory where pair-specific next-page crops are written.
    pairs
        Ordered candidate pair specifications to verify.
    usage_tracker
        Tracker to accumulate token usage across all verification attempts.

    Returns
    -------
    dict[str, Any]
        Attempt summaries plus the selected candidate pair and verdict.

    Raises
    ------
    RuntimeError
        If all verification attempts fail.
    """

    attempt_summaries: list[dict[str, Any]] = []
    crop_cache: dict[tuple[int, int], Path] = {}
    early_stop_reason: str | None = None
    successful_attempts: list[VerifiedCandidateAttempt] = []

    for attempt_no, spec in enumerate(pairs):
        logger.info(
            f"Verifying pages {page_index + 1}-{page_index + 2} | "
            f"attempt {attempt_no + 1}/{len(pairs)} | "
            f"prev_item={spec.prev_index} (rank {spec.prev_rank}) | "
            f"next_item={spec.next_index} (rank {spec.next_rank})"
        )

        crop_fp = _ensure_pair_specific_crop(
            crop_cache=crop_cache,
            next_page_image_fp=next_page_image_fp,
            next_page_index=page_index + 1,
            output_dir=pair_crop_dir,
            spec=spec,
        )

        try:
            next_item_json = spec.next_item.model_dump(mode="json")
            prev_item_json = spec.prev_item.model_dump(mode="json")
            verdict = verify_page_ir_pairs(
                min_confidence_to_patch=config.min_confidence_to_patch,
                min_confidence_to_select_positive=config.min_confidence_to_select_positive,
                min_confidence_to_stop_negative_search=(
                    config.min_confidence_to_stop_negative_search
                ),
                next_item=next_item_json,
                next_item_excerpt=_make_verification_excerpt(
                    item=_strip_continuity_hints(next_item_json)
                ),
                next_page_index=page_index + 1,
                next_png=crop_fp,
                prev_item=prev_item_json,
                prev_item_excerpt=_make_verification_excerpt(
                    item=_strip_continuity_hints(prev_item_json)
                ),
                prev_page_index=page_index,
                prev_png=next_page_image_fp.parent / f"{page_index:04}.png",
                usage_tracker=usage_tracker,
            )
        except Exception as error:  # pylint: disable=broad-except
            attempt_summaries.append(
                {
                    "attempt_no": attempt_no,
                    "crop_y_max": spec.crop_y_max,
                    "error": str(error),
                    "next_item_index": spec.next_index,
                    "next_rank": spec.next_rank,
                    "prev_item_index": spec.prev_index,
                    "prev_rank": spec.prev_rank,
                }
            )
            continue

        attempt = VerifiedCandidateAttempt(
            attempt_no=attempt_no, crop_fp=crop_fp, spec=spec, verdict=verdict
        )
        successful_attempts.append(attempt)
        is_eligible_for_patch = _is_patchable_positive(config=config, verdict=verdict)

        # Stop searching if a primary-primary pair in the same family yields a very
        # high-confidence negative. This avoids expensive fallback searches for
        # alternate anchors when the best candidate is already strongly disproven.
        is_early_stop_negative = (
            spec.prev_rank == 0
            and spec.next_rank == 0
            and not verdict.is_continuation
            and verdict.confidence
            >= float(config.min_confidence_to_stop_negative_search)
            and _shares_candidate_family(left=spec.prev_item, right=spec.next_item)
        )

        attempt_summary = {
            "attempt_no": attempt_no,
            "confidence": verdict.confidence,
            "continuation_kind": verdict.continuation_kind.value,
            "crop_y_max": spec.crop_y_max,
            "crop_png_fp": str(crop_fp),
            "eligible_for_patch": is_eligible_for_patch,
            "is_continuation": verdict.is_continuation,
            "next_item_index": spec.next_index,
            "next_rank": spec.next_rank,
            "prev_item_index": spec.prev_index,
            "prev_rank": spec.prev_rank,
            "set_next_table_repeats_header": verdict.set_next_table_repeats_header,
        }

        if (
            attempt.spec.next_rank == 0
            and attempt.spec.prev_rank == 0
            and is_eligible_for_patch
        ):
            early_stop_reason = "primary_primary_patchable_positive"
            attempt_summary["early_stop_reason"] = early_stop_reason
            attempt_summaries.append(attempt_summary)

            logger.info(
                f"Stopping verification early for pages {page_index + 1}-{page_index + 2} "
                f"after attempt {attempt_no}: primary-primary pair is patchable positive."
            )

            break

        if is_early_stop_negative:
            early_stop_reason = "primary_primary_same_family_high_confidence_negative"
            attempt_summary["early_stop_reason"] = early_stop_reason
            attempt_summaries.append(attempt_summary)

            logger.info(
                f"Stopping verification early for pages {page_index + 1}-{page_index + 2} "
                f"after attempt {attempt_no}: primary-primary pair is a same-family "
                f"high-confidence negative (confidence={verdict.confidence})."
            )

            break

        attempt_summaries.append(attempt_summary)

    if not successful_attempts:
        errors = [
            summary["error"] for summary in attempt_summaries if "error" in summary
        ]
        raise RuntimeError(
            f"All {len(pairs)} verification attempts failed for page pair "
            f"{page_index + 1}->{page_index + 2}. Errors: {errors}"
        )

    # Select the best explanatory attempt using the priority key ranking policy.
    selected_attempt = min(
        successful_attempts,
        key=lambda attempt_: _pair_priority_key(attempt=attempt_, config=config),
    )

    return {
        "attempt_summaries": attempt_summaries,
        "early_stop_reason": early_stop_reason,
        "selected_eligible_for_patch": _is_patchable_positive(
            config=config, verdict=selected_attempt.verdict
        ),
        "selected_next_index": selected_attempt.spec.next_index,
        "selected_prev_index": selected_attempt.spec.prev_index,
        "selected_verdict": selected_attempt.verdict,
    }


def _extract_figure_preview(
    *, figure: dict[str, Any], max_chars: int
) -> dict[str, str]:
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
        preview["alt_text"] = _truncate_text(max_chars=max_chars, text=alt)

    # Handle text wrappers for caption and embedded_text.
    for field in ("caption", "embedded_text"):
        obj = figure.get(field)

        if isinstance(obj, dict):
            text = _get_text_content(obj)

            if text:
                preview[field] = _truncate_text(max_chars=max_chars, text=text)

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
            li_text = _truncate_text(max_chars=180, text=text)
            preview.append((marker + " " + li_text).strip())
        else:
            preview.append(_truncate_text(max_chars=180, text=str(li)))

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


def _generate_candidate_pairs(
    *,
    config: VerificationConfig,
    next_page_ir: PageIR,
    prev_candidates: list[tuple[int, Block | Table]],
) -> tuple[list[CandidatePairSpec], dict[str, int]]:
    """Generate the ranked verification workload for a single page boundary.

    This function does two related but distinct pieces of work, which is why
    `_ordered_next_candidates(...)` is called twice for the primary previous-page
    anchor.

    First, before the loop, it computes the *primary* next-page match for the first
    previous-page candidate. Those indices are returned separately in
    `primary_indices` for reporting, logging, and downstream bookkeeping. That pre-loop
    call intentionally happens before pair expansion so the caller always gets the
    canonical primary boundary pair, even if later pair generation hits deduplication
    or the global pair cap.

    Second, inside the loop, it recomputes ordered next-page candidates for *each*
    previous-page anchor and expands them into concrete `CandidatePairSpec` objects.
    Each spec stores the previous/next ranks, the item indices, and a pair-specific
    `crop_y_max` that limits how much of page N + 1 is shown to the verifier. Candidate
    discovery itself still uses the full next-page JSON; the crop is only an evidence
    delivery hint for the image sent with that pair.

    During expansion, duplicate `(prev_index, next_index)` pairs are skipped and the
    total workload is capped at nine candidate pairs (can be changed or parameterized
    if needed in the future).

    Parameters
    ----------
    config
        The verification configuration.
    next_page_ir
        PageIR for page N+1.
    prev_candidates
        Ordered bottom-of-page candidates from page N. The first element is treated as
        the primary previous-page anchor.

    Returns
    -------
    tuple[list[CandidatePairSpec], dict[str, int]]
        Ordered pair specs plus the primary candidate indices for reporting.
    """

    next_items = next_page_ir.items
    primary_prev_index, primary_prev_item = prev_candidates[0]
    primary_next_candidates = _ordered_next_candidates(
        image_height=next_page_ir.image_height,
        items=next_items,
        prev_item=primary_prev_item,
    )
    primary_next_index, _ = primary_next_candidates[0]
    primary_indices = {
        "next_item_index": primary_next_index,
        "prev_item_index": primary_prev_index,
    }
    pair_specs: list[CandidatePairSpec] = []
    seen_pairs: set[tuple[int, int]] = set()

    for prev_rank, (prev_index, prev_item) in enumerate(prev_candidates):
        ordered_next_candidates = _ordered_next_candidates(
            image_height=next_page_ir.image_height,
            items=next_items,
            prev_item=prev_item,
        )

        for next_rank, (next_index, next_item) in enumerate(ordered_next_candidates):
            pair_key = (prev_index, next_index)

            if pair_key in seen_pairs:
                continue

            seen_pairs.add(pair_key)
            crop_y_max = min(
                float(next_page_ir.image_height),
                float(next_item.bbox[3]) + float(config.next_page_crop_padding_px),
            )
            pair_specs.append(
                CandidatePairSpec(
                    crop_y_max=crop_y_max,
                    next_index=next_index,
                    next_item=next_item,
                    next_rank=next_rank,
                    prev_index=prev_index,
                    prev_item=prev_item,
                    prev_rank=prev_rank,
                )
            )

            if len(pair_specs) >= 9:
                return pair_specs, primary_indices

    return pair_specs, primary_indices


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


def _is_figure_block(item: Block | Table) -> bool:
    """Return True if item is a Block with block_type FIGURE.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is a figure block.
    """

    return isinstance(item, Block) and item.block_type == BlockType.FIGURE


def _is_patchable_positive(
    *, config: VerificationConfig, verdict: PageIRContinuityVerdict
) -> bool:
    """Return whether a verdict is both positive and eligible for patching.

    Parameters
    ----------
    config
        The verification configuration containing the patching confidence threshold.
    verdict
        The continuity verdict to evaluate.

    Returns
    -------
    bool
        True if the verdict is a positive continuation with confidence above the
        patching threshold, False otherwise.
    """

    return (
        verdict.is_continuation and verdict.confidence >= config.min_confidence_to_patch
    )


def _is_viable_nonfigure_block_anchor(item: Block | Table) -> bool:
    """Return True for block anchors suitable for text/list-style continuity.

    Parameters
    ----------
    item
        The item to evaluate.

    Returns
    -------
    bool
        True if the item is a non-figure Block that is not a HEADING or CAPTION, False
        otherwise.
    """

    return (
        isinstance(item, Block)
        and not _is_heading_or_caption_block(item)
        and item.block_type != BlockType.FIGURE
    )


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
    text_preview = _truncate_text(max_chars=max_text_chars, text=text)

    list_items = item.get("list_items")
    list_preview = _extract_list_preview(list_items) if list_items else []

    figure = item.get("figure")
    figure_preview = {}

    if isinstance(figure, dict):
        figure_preview = _extract_figure_preview(
            figure=figure, max_chars=max_text_chars
        )

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
    # deduplicate any overlap (small tables where top and bottom slices intersect).
    bottom_body: list[Any] = []

    if len(body_rows) > preview_rows:
        bottom_slice = body_rows[-preview_rows:]

        # Only keep rows from the bottom slice that aren't already in the top slice.
        top_end_index = preview_rows  # Index into body_rows
        bottom_start_index = len(body_rows) - preview_rows
        bottom_body = (
            bottom_slice
            if bottom_start_index >= top_end_index
            else body_rows[top_end_index:]
        )

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


def _make_verification_excerpt(
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

    kind = item["kind"]
    assert kind in ("block", "table"), f"Unexpected item kind: {kind}"
    bbox = item["bbox"]
    local_code = item.get("local_code", None)

    return (
        _make_table_excerpt(
            bbox=bbox,
            item=item,
            local_code=local_code,
            max_cell_chars=max_cell_chars,
            preview_rows=preview_rows,
        )
        if kind == "table"
        else _make_block_excerpt(
            bbox=bbox, item=item, local_code=local_code, max_text_chars=max_text_chars
        )
    )


def _ordered_next_candidates(
    *,
    image_height: float,
    items: list[Block | Table],
    k: int = 3,
    prev_item: Block | Table,
    visible_y_max: float | None = None,
) -> list[tuple[int, Block | Table]]:
    """Return ordered next-page candidates for a given previous-page anchor.

    Same-family matches are ranked before cross-family matches while preserving the
    reading-order stability. Families are finer than raw `item.kind`: tables match
    tables, figure blocks match figure blocks, and other non-table blocks match other
    non-table blocks. The top candidate is initially picked using `_pick_topmost`, and
    subsequent items are added to a pool of up to `k` candidates before a final
    re-ordering strictly enforces same-family priority.

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
        List of (item_index, item) pairs ordered by same-family priority and reading
        order. Length is in [1, k].

    Raises
    ------
    ValueError
        If k < 1.
        If no non-artifact items are found.
    """

    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")

    # Build the candidate pool once and reuse for both primary pick and extras.
    candidates = _filter_candidate_pool(image_height=image_height, items=items)

    if visible_y_max is not None:
        candidates = _apply_visible_crop(
            candidates=candidates, y_max=visible_y_max, y_min=0.0
        )

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Sort by top-edge ascending.
    candidates.sort(key=lambda p: float(p[1].bbox[1]))

    # Pick the primary candidate using topmost logic.
    first_i, first_item = _pick_topmost(candidates=candidates, prev_item=prev_item)

    next_candidates: list[tuple[int, Block | Table]] = [(first_i, first_item)]
    seen: set[int] = {first_i}

    same_family_pool: list[tuple[int, Block | Table]] = []
    other_family_pool: list[tuple[int, Block | Table]] = []

    for i, item in candidates:
        # For blocks, avoid heading/caption as text anchors.
        if i in seen or _is_heading_or_caption_block(item):
            continue

        if _shares_candidate_family(left=item, right=prev_item):
            same_family_pool.append((i, item))
        else:
            other_family_pool.append((i, item))

    # Fill the candidate list up to `k` elements.
    for bucket in (same_family_pool, other_family_pool):
        for i, item in bucket:
            if len(next_candidates) >= k:
                break

            next_candidates.append((i, item))

    # Finally, strictly enforce same-family before other-family for the `k` selected
    # items.
    same_family_final = [
        (index, item)
        for index, item in next_candidates
        if _shares_candidate_family(left=item, right=prev_item)
    ]
    other_family_final = [
        (index, item)
        for index, item in next_candidates
        if not _shares_candidate_family(left=item, right=prev_item)
    ]

    return same_family_final + other_family_final


def _pair_priority_key(
    *, attempt: VerifiedCandidateAttempt, config: VerificationConfig
) -> tuple[int, float, int, int, int, int]:
    """Return the ranking key used to select the best explanatory verdict.

    Ranking policy
    --------------

    1. Positive continuations at or above `min_confidence_to_select_positive`.
    2. All remaining attempts.
    3. Within a bucket, prefer higher confidence, then lower candidate ranks, then
        same-family matches, then aligned boundary hints.

    Parameters
    ----------
    attempt
        The verification attempt to generate a key for.
    config
        The verification configuration containing the confidence threshold.

    Returns
    -------
    tuple[int, float, int, int, int, int]
        The priority key tuple.
    """

    verdict = attempt.verdict
    spec = attempt.spec
    is_strong_positive = (
        verdict.is_continuation
        and verdict.confidence >= config.min_confidence_to_select_positive
    )

    same_family_penalty = (
        0 if _shares_candidate_family(left=spec.prev_item, right=spec.next_item) else 1
    )

    next_boundary_good = spec.next_item.boundary in {
        ItemBoundary.BOTH,
        ItemBoundary.RESUMED,
    }
    prev_boundary_good = spec.prev_item.boundary in {
        ItemBoundary.BOTH,
        ItemBoundary.TRUNCATED,
    }
    boundary_penalty = 0 if next_boundary_good and prev_boundary_good else 1

    return (
        0 if is_strong_positive else 1,
        -verdict.confidence,
        spec.prev_rank,
        spec.next_rank,
        same_family_penalty,
        boundary_penalty,
    )


def _pick_bottommost(
    candidates: list[tuple[int, Block | Table]],
) -> tuple[int, Block | Table]:
    """Pick the best bottom-of-page candidate from a pre-sorted list.

    NB: Candidates must already be sorted by bottom-edge (y1) descending.

    Selection priority:

    1. Extractor-flagged TRUNCATED/BOTH items (preferred boundary hints).
    2. Tables near the bottom (within top-5 by y1).
    3. Figure blocks near the bottom (within top-5 by y1).
    4. Non-heading/non-caption, non-figure blocks.
    5. Absolute bottom item (last resort).

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

        # Then prefer a figure block near the bottom so figure continuations surface
        # before generic block -> block pairings.
        for i, item in sorted_candidates[:5]:
            if _is_figure_block(item):
                return i, item

        # Otherwise pick the first non-figure block, but never anchor on
        # HEADING/CAPTION.
        for i, item in sorted_candidates:
            if _is_viable_nonfigure_block_anchor(item):
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

    NB: Candidates must already be sorted by top-edge (y0) ascending.

    Selection priority:

    1. Extractor-flagged RESUMED/BOTH items matching prev_item family.
    2. Same-family match (Table -> Table, Figure -> Figure, non-figure
        Block -> non-figure Block). A candidate is a viable same-family match if it
        shares a family with the anchor. If the anchor is a block, the candidate must
        also be a viable non-figure block.
    3. First viable non-figure block (fallback for generic block continuity).
    4. Absolute top item (last resort).

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

    # 1.
    preferred = [
        (i, item)
        for i, item in candidates
        if item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
    ]

    # Build a deduplicated search order: preferred items first, then remaining
    # candidates (preserving positional order within each group).
    seen_indices: set[int] = {i for i, _ in preferred}
    unique_ordered: list[tuple[int, Block | Table]] = preferred + [
        (i, item) for i, item in candidates if i not in seen_indices
    ]

    # 2.
    family_match = next(
        (
            (i, item)
            for i, item in unique_ordered
            if _shares_candidate_family(left=prev_item, right=item)
            and (
                _candidate_family(prev_item) != "block"
                or _is_viable_nonfigure_block_anchor(item)
            )
        ),
        None,
    )

    if family_match is not None:
        return family_match

    # 3.
    valid_match = next(
        (
            (i, item)
            for i, item in unique_ordered
            if _is_viable_nonfigure_block_anchor(item)
        ),
        None,
    )

    # 4.
    return valid_match if valid_match is not None else candidates[0]


def _shares_candidate_family(*, left: Block | Table, right: Block | Table) -> bool:
    """Return True if two items belong to the same continuity candidate family.

    Parameters
    ----------
    left
        The first item to compare.
    right
        The second item to compare.

    Returns
    -------
    bool
        True if both items belong to the same candidate family, False otherwise.
    """

    return _candidate_family(left) == _candidate_family(right)


def _strip_continuity_hints(item_json: dict[str, Any]) -> dict[str, Any]:
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
            output.append(_truncate_text(max_chars=max_cell_chars, text=str(cell)))
            continue

        text_or_none = cell.get("text", None)
        text = text_or_none["text"] if isinstance(text_or_none, dict) else ""
        output.append(_truncate_text(max_chars=max_cell_chars, text=text))

    return output


def _truncate_text(*, max_chars: int, text: str) -> str:
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
    """Handle continuity verification for a specific pair of pages.

    Parameters
    ----------
    config
        The verification run configuration.
    page_images_dir
        Directory containing the page images.
    page_index
        The index of the previous page in the pair to verify.
    page_irs
        PageIR objects keyed by page index.
    usage_tracker
        Tracker to accumulate token usage from both verification and validation agents.
    verification_dirs
        Verification output directories.

    Returns
    -------
    EdgeVerdictRecord | None
        The created edge verdict record if verification was performed, or None if the
        pair was skipped.

    Raises
    ------
    ValueError
        If the required PageIRs for the page pair are missing.
    """

    if page_index not in page_irs or (page_index + 1) not in page_irs:
        raise ValueError(
            f"Missing page IR for page index {page_index} or {page_index + 1}."
        )

    next_page_ir = page_irs[page_index + 1]
    prev_page_ir = page_irs[page_index]

    if not (prev_page_ir.items and next_page_ir.items):
        logger.warning(
            f"Skipping continuity check for pages {page_index + 1}-{page_index + 2}: "
            f"prev_items={len(prev_page_ir.items)} "
            f"next_items={len(next_page_ir.items)}"
        )
        return None

    prev_candidates = _bottom_continuity_candidates(
        image_height=prev_page_ir.image_height, items=prev_page_ir.items
    )
    pairs, primary_indices = _generate_candidate_pairs(
        config=config, next_page_ir=next_page_ir, prev_candidates=prev_candidates
    )

    logger.info(
        f"Verifying continuity between pages {page_index + 1} and {page_index + 2}..."
    )

    result = _execute_verification_attempts(
        config=config,
        next_page_image_fp=page_images_dir / f"{page_index + 1:04}.png",
        page_index=page_index,
        pair_crop_dir=verification_dirs.page_irs_pair_crops,
        pairs=pairs,
        usage_tracker=usage_tracker,
    )
    selected_verdict = result["selected_verdict"]
    selected_verdict.next_page_index = page_index + 1
    selected_verdict.prev_page_index = page_index
    record = EdgeVerdictRecord(
        next_item_index=result["selected_next_index"],
        next_page_index=selected_verdict.next_page_index,
        prev_item_index=result["selected_prev_index"],
        prev_page_index=selected_verdict.prev_page_index,
        verdict=selected_verdict,
    )

    write_to_json(
        fp=verification_dirs.page_irs_pair_reports
        / f"{page_index:04}_{page_index + 1:04}.json",
        json_info={
            "attempts": result["attempt_summaries"],
            "selection_policy": {
                "min_confidence_to_patch": config.min_confidence_to_patch,
                "min_confidence_to_select_positive": (
                    config.min_confidence_to_select_positive
                ),
                "min_confidence_to_stop_negative_search": (
                    config.min_confidence_to_stop_negative_search
                ),
                "early_stop_reason": result["early_stop_reason"],
            },
            "primary_candidate_selection": primary_indices,
            "selected_candidate_selection": {
                "eligible_for_patch": result["selected_eligible_for_patch"],
                "next_item_index": result["selected_next_index"],
                "prev_item_index": result["selected_prev_index"],
            },
            "verdict": selected_verdict.model_dump(mode="json"),
        },
    )

    logger.success(
        f"Finished verifying continuity between pages {page_index + 1} and {page_index + 2}!"
    )

    return record
