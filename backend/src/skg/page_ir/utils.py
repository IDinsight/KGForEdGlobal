"""This module contains utility functions for page Intermediate Representations."""

# Standard Library
import re
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir.schemas import PageIR, PageIRContinuityVerdict
from skg.schemas import RunCtx
from skg.utils.constants import (
    BlockType,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import clamp, make_dir, near, open_json_type, write_to_json
from skg.utils.pdf import compute_doc_key

SECTION_BREAK_HEADINGS = {
    "appendix",
    "bibliography",
    "contents",
    "index",
    "list of figures",
    "list of tables",
    "reference list",
    "references",
    "table of contents",
}


@dataclass(frozen=True)
class PageIRExtractionDirs:
    """Dataclass for page IR extraction directories."""

    root: Path
    page_images: Path
    page_irs: Path


@dataclass(frozen=True)
class PageIRVerificationDirs:
    """Dataclass for page IR verification directories."""

    root: Path
    page_irs_pair_crops: Path
    page_irs_pair_reports: Path
    page_irs_verified: Path


def _block_type_val(v: Any) -> str:
    """Normalize block type enum/string to string.

    Parameters
    ----------
    v
        The block type value (enum or string).

    Returns
    -------
    str
        The normalized block type string.
    """

    if isinstance(v, dict):
        block_type = v.get("block_type")
    elif hasattr(v, "block_type"):  # Handle Pydantic object being passed directly
        block_type = getattr(v, "block_type")
    else:
        block_type = v

    return (
        str(getattr(block_type, "value", block_type)) if block_type is not None else ""
    )


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

    b = (
        item.get("boundary")
        if isinstance(item, dict)
        else getattr(item, "boundary", None)
    )

    return "" if b is None else b.value if hasattr(b, "value") else str(b)


def _boundary_val(v: Any, default_val: str = ItemBoundary.COMPLETE.value) -> str:
    """Normalize boundary enum/string to string.

    Parameters
    ----------
    v
        The boundary value (enum or string).
    default_val
        The default value to use if v is None.

    Returns
    -------
    str
        The normalized boundary string.
    """

    return getattr(v, "value", v) if v is not None else default_val


def _derive_boundary_state_from_items(
    non_artifact_items: list[tuple[int, Any]],
) -> PageBoundaryState:
    """Derive the page boundary state from item boundaries.

    Parameters
    ----------
    non_artifact_items
        The list of non-artifact items with their indices.

    Returns
    -------
    PageBoundaryState
        The derived page boundary state.
    """

    # Only consider non-artifact items for continuity.
    non_artifact = [it for _, it in non_artifact_items]

    if not non_artifact:
        return PageBoundaryState.STANDALONE

    any_from_prev = any(is_resumed(_boundary_str(it)) for it in non_artifact)
    any_to_next = any(is_truncated(_boundary_str(it)) for it in non_artifact)

    if any_from_prev and any_to_next:
        return PageBoundaryState.BOTH
    if any_from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if any_to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


def _has_boundary(it: dict[str, Any], b: ItemBoundary) -> bool:
    """Check if the item has the specified boundary flag.

    Parameters
    it
        The item to check.
    b
        The boundary flag to check for.

    Returns
    -------
    bool
        True if the item has the specified boundary flag, False otherwise.
    """

    v = it.get("boundary")

    if v is None:
        v = it.get("_orig_boundary")

    if v is None:
        v = ItemBoundary.COMPLETE.value

    v = getattr(v, "value", v)

    if v == getattr(b, "value", b):
        return True
    if v == ItemBoundary.BOTH.value:
        return b in (ItemBoundary.RESUMED, ItemBoundary.TRUNCATED)
    return False


def _truncate_text_word_boundary(
    *, max_len: int, mode: str, text: str
) -> tuple[str, bool]:
    """Truncate text without cutting mid-word.

    Valid modes are:
      - "head": Keep the beginning.
      - "tail": Keep the end.

    Parameters
    ----------
    max_len
        Maximum length of the returned text.
    mode
        Truncation mode ("head" or "tail").
    text
        The text to truncate.

    Returns
    -------
    tuple[str, bool]
        The truncated text and a boolean indicating if truncation occurred.
    """

    assert mode in (
        "head",
        "tail",
    ), f"Invalid mode: {mode}. Valid modes are: 'head' or 'tail'"

    if not isinstance(text, str):
        return "", False

    # Normalize whitespace so length comparisons are stable.
    t = re.sub(r"\s+", " ", text).strip()

    if len(t) <= max_len:
        return t, False

    if max_len <= 12:
        # Degenerate case; just slice.
        return (t[:max_len] if mode == "head" else t[-max_len:]), True

    if mode == "head":
        chunk = t[:max_len]
        if " " in chunk:
            chunk = chunk.rsplit(" ", 1)[0]
        return chunk + "...", True

    chunk = t[-max_len:]

    # If we started mid-word, drop the partial first word.
    if " " in chunk:
        chunk = chunk.split(" ", 1)[1]
    return "..." + chunk, True


def apply_continuity_edits(
    *,
    next_idx: int,
    next_item: dict[str, Any],
    next_page_items: list[dict[str, Any]],
    prev_idx: int,
    prev_item: dict[str, Any],
    prev_page_items: list[dict[str, Any]],
    verdict: PageIRContinuityVerdict,
) -> None:
    """Apply minimal edits to the extracted page IRs based on the continuity verdict.

    Parameters
    ----------
    next_idx
        Index of the continuity candidate item on the next page.
    next_item
        The actual item dictionary for the next page candidate.
    next_page_items
        The list of items on the next page.
    prev_idx
        Index of the continuity candidate item on the previous page.
    prev_item
        The actual item dictionary for the previous page candidate.
    prev_page_items
        The list of items on the previous page.
    verdict
        The continuity verdict from the model.
    """

    # If this pair is NOT a continuation or confidence is below the threshold, then
    # there are no continuity edits to apply.
    threshold = get_threshold_based_on_kind(
        next_item=next_item, prev_item=prev_item, verdict=verdict
    )
    if not verdict.is_continuation or float(verdict.clamped_confidence) < threshold:
        return

    # Update item-level boundaries (explicit edits from model).
    if verdict.set_prev_item_boundary is not None:
        ensure_boundary(
            desired=getattr(
                verdict.set_prev_item_boundary, "value", verdict.set_prev_item_boundary
            ),
            items=prev_page_items,
            index=prev_idx,
        )

    if verdict.set_next_item_boundary is not None:
        ensure_boundary(
            desired=getattr(
                verdict.set_next_item_boundary, "value", verdict.set_next_item_boundary
            ),
            items=next_page_items,
            index=next_idx,
        )

    # Enforce item-level consistency (implicit edits). If model verified continuity but
    # didn't explicitly set boundaries, force defaults.
    if verdict.set_prev_item_boundary is None:
        ensure_boundary(
            desired=ItemBoundary.TRUNCATED.value, index=prev_idx, items=prev_page_items
        )
    if verdict.set_next_item_boundary is None:
        ensure_boundary(
            desired=ItemBoundary.RESUMED.value, index=next_idx, items=next_page_items
        )

    # Table header repetition: set repeats_header only when the model provides it for a
    # verified table continuation.
    header_setting = verdict.set_next_table_repeats_header
    is_next_table = next_item.get("kind") == "table"
    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)
    is_table_continuation = (
        verdict.is_continuation and kind == PageContinuationKind.TABLE.value
    )
    if header_setting is not None and is_next_table and is_table_continuation:
        next_page_items[next_idx]["repeats_header"] = header_setting


def apply_non_continuity_edits(
    *,
    next_idx: int,
    next_item: dict[str, Any],
    next_page_items: list[dict[str, Any]],
    prev_idx: int,
    prev_item: dict[str, Any],
    prev_page_items: list[dict[str, Any]],
    verdict: PageIRContinuityVerdict,
) -> None:
    """If the model is VERY confident there is no continuation between these two
    candidates, clear seam-level continuity flags on just these items.

    This is a 'patch' for extractor false-positives (e.g., a table marked resumed when
    it's actually a new table).

    NB: We only clear the seam-relevant side, preserving the other side when
    boundary="both".

    Parameters
    ----------
    next_idx
        Index of the continuity candidate item on the next page.
    next_item
        The actual item dictionary for the next page candidate.
    next_page_items
        The list of items on the next page.
    prev_idx
        Index of the continuity candidate item on the previous page.
    prev_item
        The actual item dictionary for the previous page candidate.
    prev_page_items
        The list of items on the previous page.
    verdict
        The continuity verdict from the model.
    """

    if verdict.is_continuation:
        return

    # If confidence is not high enough and we don't have ordering safety, skip.
    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)
    ordering_safety_hard_negative = (
        (not verdict.is_continuation)
        and kind == PageContinuationKind.NONE.value
        and not (prev_item.get("kind") == "table" and next_item.get("kind") == "table")
        and has_structural_block_above_candidate(
            candidate_index=next_idx, items=next_page_items
        )
    )
    neg_threshold = get_negative_threshold_based_on_kind(
        next_item=next_item, prev_item=prev_item
    )
    if (
        float(verdict.clamped_confidence) < neg_threshold
        and not ordering_safety_hard_negative
    ):
        return

    # Previous page item (seam to NEXT corresponds to TRUNCATED).
    # NB: If prev_boundary is RESUMED or COMPLETE, leave it as-is.
    prev_boundary = _boundary_val(prev_page_items[prev_idx].get("boundary"))
    if prev_boundary == ItemBoundary.TRUNCATED.value:
        # It only claimed "continues to next"; clear it.
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.COMPLETE.value,
            index=prev_idx,
            items=prev_page_items,
        )
    elif prev_boundary == ItemBoundary.BOTH.value:
        # Remove the "to next" claim but preserve "from prev".
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.RESUMED.value,
            index=prev_idx,
            items=prev_page_items,
        )

    # Next page item (seam from PREV corresponds to RESUMED).
    # NB: If next_boundary is TRUNCATED or COMPLETE, leave it as-is.
    next_boundary = _boundary_val(next_page_items[next_idx].get("boundary"))
    if next_boundary == ItemBoundary.RESUMED.value:
        # It only claimed "continues from prev"; clear it.
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.COMPLETE.value,
            index=next_idx,
            items=next_page_items,
        )
    elif next_boundary == ItemBoundary.BOTH.value:
        # Remove the "from prev" claim but preserve "to next".
        ensure_boundary(
            allow_downgrade_both=True,
            desired=ItemBoundary.TRUNCATED.value,
            index=next_idx,
            items=next_page_items,
        )

    # repeats_header only has meaning for resumed/both table continuations.
    if next_item.get("kind") == "table":
        next_page_items[next_idx]["repeats_header"] = None


def bottommost_continuity_candidate(
    *,
    image_height: float,
    items: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """Pick the best "bottom of page" candidate for continuity checks.

    Heuristic:
        - Choose the non-artifact item with largest bbox y1 (closest to bottom).
        - If multiple items are near the bottom-most y1 (within a small delta),
            prefer a table among that near-bottom set.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of PageIR items on the page.

    Returns
    -------
    tuple[int, dict[str, Any]]
        The index and item of the chosen candidate.

    Raises
    ------
    ValueError
        If no non-artifact items are found.
    """

    candidates = [
        (i, it)
        for i, it in enumerate(items)
        if (not is_artifact(it))
        and (not is_probable_header_footer_noise(image_height=image_height, item=it))
    ]
    if not candidates:
        raise ValueError("No non-artifact items found.")

    contentish = [
        (i, it)
        for (i, it) in candidates
        if not (
            it.get("kind") == "block"
            and str(it.get("block_type") or "").lower() in {"heading", "caption"}
        )
    ]
    candidates = contentish or candidates

    # Look at the last 5 candidates by vertical position (closest to bottom). If any
    # are explicitly marked as truncated, prefer those (tables first).
    last_n = 5
    bottom_sorted = sorted(
        candidates, key=lambda p: (float(p[1]["bbox"][3]), p[0]), reverse=True
    )
    bottom_slice = bottom_sorted[: min(last_n, len(bottom_sorted))]
    truncated = [
        (i, it) for (i, it) in bottom_slice if _has_boundary(it, ItemBoundary.TRUNCATED)
    ]
    if truncated:
        truncated_non_fig = [
            (i, it) for (i, it) in truncated if not is_figure_block(it)
        ]
        base = truncated_non_fig if truncated_non_fig else truncated
        truncated_tables = [(i, it) for (i, it) in base if it.get("kind") == "table"]
        chosen_trunc = truncated_tables if truncated_tables else base
        return max(chosen_trunc, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

    # 2. Table-in-bottom-band override (bottom 20%), when edge-most is not a table.
    edge_item = max(candidates, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

    # If the edge-most item is a small "minor" block (e.g., footnote-ish), but there's
    # a table close to the bottom, prefer the table as the continuity anchor.
    if edge_item[1].get("kind") != "table" and is_minor_edge_block(
        image_height=image_height, item=edge_item[1]
    ):
        tables = [(i, it) for (i, it) in candidates if it.get("kind") == "table"]
        if tables:
            # Bottom-most table by y1.
            best_table = max(tables, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

            # Use a slightly wider band than 20% (tables often end above the margin).
            if float(best_table[1]["bbox"][3]) >= 0.70 * image_height:
                return best_table

    if edge_item[1].get("kind") != "table":
        bottom_band_y = 0.80 * image_height
        tables_in_band = [
            (i, it)
            for (i, it) in candidates
            if it.get("kind") == "table" and float(it["bbox"][3]) >= bottom_band_y
        ]
        if tables_in_band:
            return max(tables_in_band, key=lambda p: (float(p[1]["bbox"][3]), p[0]))

    # Edge-first: find the max y1, then consider items close to that edge.
    max_y1 = max(float(it["bbox"][3]) for _, it in candidates)
    edge_slop = min(200.0, max(80.0, 0.04 * image_height))
    near_edge = [
        (i, it)
        for i, it in candidates
        if near(float(it["bbox"][3]), max_y1, tol=edge_slop)
    ]
    table_near_edge = [(i, it) for i, it in near_edge if it.get("kind") == "table"]
    if table_near_edge:
        chosen = table_near_edge
    else:
        non_fig = [(i, it) for (i, it) in near_edge if not is_figure_block(it)]
        chosen = non_fig if non_fig else near_edge

    return max(chosen, key=lambda p: (float(p[1]["bbox"][3]), p[0]))


def create_page_ir_extraction_dirs(
    *, doc_key: str, output_dir: Path
) -> PageIRExtractionDirs:
    """Create page IR extraction directories for a given document key.

    Parameters
    ----------
    doc_key
        The document key.
    output_dir
        The output directory root.

    Returns
    -------
    PageIRExtractionDirs
        The created page IR extraction directories.
    """

    root = output_dir / doc_key / "extraction"
    page_images = root / "page_images"
    page_irs = root / "page_irs"

    for p in [root, page_images, page_irs]:
        make_dir(p)

    return PageIRExtractionDirs(root=root, page_images=page_images, page_irs=page_irs)


def create_page_ir_verification_dirs(*, output_dir: Path) -> PageIRVerificationDirs:
    """Create page IR verification directories for a given verification run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    PageIRVerificationDirs
        The created page IR verification directories.
    """

    root = output_dir
    page_irs_pair_crops = root / "page_irs_pair_crops"
    page_irs_pair_reports = root / "page_irs_pair_reports"
    page_irs_verified = root / "page_irs_verified"

    for p in [root, page_irs_pair_crops, page_irs_pair_reports, page_irs_verified]:
        make_dir(p)

    return PageIRVerificationDirs(
        root=root,
        page_irs_pair_crops=page_irs_pair_crops,
        page_irs_pair_reports=page_irs_pair_reports,
        page_irs_verified=page_irs_verified,
    )


def derive_page_boundary_state(*, page_ir: dict[str, Any]) -> PageBoundaryState:
    """Derive page-level boundary_state from verified item boundaries.

    Scan all non-artifact, non-header/footer-noise items:
      - from_prev if ANY item is resumed/both
      - to_next if ANY item is truncated/both

    Parameters
    ----------
    page_ir
        The page IR dictionary.

    Returns
    -------
    PageBoundaryState
        The derived page-level boundary state.
    """

    items = page_ir.get("items") or []
    image_height = float(page_ir.get("image_height") or 0.0)

    candidates = [
        it
        for it in items
        if not is_artifact(it)
        and not is_probable_header_footer_noise(image_height=image_height, item=it)
    ]

    if not candidates:
        return PageBoundaryState.STANDALONE

    from_prev = any(
        _boundary_val(it.get("boundary"))
        in (ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value)
        for it in candidates
    )
    to_next = any(
        _boundary_val(it.get("boundary"))
        in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value)
        for it in candidates
    )

    return encode_boundary_state(from_prev, to_next)


def encode_boundary_state(from_prev: bool, to_next: bool) -> PageBoundaryState:
    """Encode (from_prev, to_next) boolean flags into a PageBoundaryState enum.

    Parameters
    ----------
    from_prev
        Whether the item continues from the previous page.
    to_next
        Whether the item continues to the next page.

    Returns
    -------
    PageBoundaryState
            The encoded PageBoundaryState enum member.
    """

    if from_prev and to_next:
        return PageBoundaryState.BOTH
    if from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


def ensure_boundary(
    *,
    allow_downgrade_both: bool = False,
    desired: str,
    items: list[dict[str, Any]],
    index: int,
) -> None:
    """Ensure that the item at the given index has the desired boundary flag.

    Parameters
    ----------
    allow_downgrade_both
        If True, allow downgrading BOTH to a single-sided boundary.
    desired
        Desired boundary flag ("complete", "truncated", "resumed", or "both").
    items
        List of PageIR items.
    index
        Index of the item to update.

    Raises
    ------
    ValueError
        If an invalid boundary flag is provided.
    """

    target_boundary: ItemBoundary = ItemBoundary(desired)

    current_val = items[index].get("boundary")
    current_boundary = ItemBoundary(_boundary_val(current_val))

    if (
        target_boundary == ItemBoundary.TRUNCATED
        and current_boundary == ItemBoundary.RESUMED
    ):
        target_boundary = ItemBoundary.BOTH
    elif (
        target_boundary == ItemBoundary.RESUMED
        and current_boundary == ItemBoundary.TRUNCATED
    ):
        target_boundary = ItemBoundary.BOTH
    elif (
        (not allow_downgrade_both)
        and current_boundary == ItemBoundary.BOTH
        and target_boundary in (ItemBoundary.RESUMED, ItemBoundary.TRUNCATED)
    ):
        target_boundary = ItemBoundary.BOTH
    elif (
        (not allow_downgrade_both)
        and current_boundary == ItemBoundary.BOTH
        and target_boundary == ItemBoundary.COMPLETE
    ):
        target_boundary = ItemBoundary.BOTH

    if current_boundary != target_boundary:
        items[index]["boundary"] = target_boundary.value


def extract_text(v: Any) -> Optional[str]:
    """Extract text from a TextUnit-like structure.

    Parameters
    ----------
    v
        The TextUnit-like structure.

    Returns
    -------
    Optional[str]
        The extracted text, or None if not found.
    """

    if isinstance(v, str):
        return v

    if isinstance(v, dict):
        # Common shapes: {"text": "..."} or {"text": {"text": "..."}}.
        inner = v.get("text")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, dict):
            inner2 = inner.get("text")
            return inner2 if isinstance(inner2, str) else None
    return None


def find_caption_code(items: list[dict[str, Any]]) -> str | None:
    """Find the first valid Table-like local_code from a caption block.

    Parameters
    ----------
    items
        List of PageIR items.

    Returns
    -------
    str | None
        The found caption local_code, or None if not found.
    """

    for it in items:
        if (
            it.get("kind") == "block"
            and _boundary_val(it.get("block_type"), default_val="")
            == BlockType.CAPTION.value
        ):
            code = _boundary_val(it.get("local_code"), default_val="")
            if code and str(code).lower().startswith("table"):
                return str(code)

    return None


def fix_false_repeats_header_on_continuation(*, next_item: Any, prev_item: Any) -> None:
    """Fix false repeats_header=true flags on continued tables where headers do not
    match. If a table continues across a page boundary (prev last is truncated table,
    next first is resumed table) and next_table.repeats_header==True, but the header
    signatures DO NOT match, then auto-fix by setting repeats_header=False (and log a
    warning).

    NB: This does NOT attempt to change header_row_count; it only corrects
    repeats_header.

    Parameters
    ----------
    next_item
        The page IR for the next page.
    prev_item
        The page IR for the previous page.
    """

    # Only table-to-table continuations.
    prev_kind = (
        prev_item.get("kind")
        if isinstance(prev_item, dict)
        else getattr(prev_item, "kind", None)
    )
    next_kind = (
        next_item.get("kind")
        if isinstance(next_item, dict)
        else getattr(next_item, "kind", None)
    )
    if (
        prev_kind != PageContinuationKind.TABLE.value
        or next_kind != PageContinuationKind.TABLE.value
    ):
        return

    if not is_truncated(_boundary_str(prev_item)) or not is_resumed(
        _boundary_str(next_item)
    ):
        return

    current_val = (
        next_item.get("repeats_header")
        if isinstance(next_item, dict)
        else getattr(next_item, "repeats_header", None)
    )

    if current_val is not True:
        return

    prev_sig = table_header_signature(prev_item)
    next_sig = table_header_signature(next_item)

    # If we cannot compute signatures, be conservative and skip.
    if prev_sig is None or next_sig is None:
        return

    if prev_sig != next_sig:
        logger.warning(
            "Table continuation has repeats_header=true but header rows do not match "
            "the previous page's header. This is likely a false repeated-header flag. "
            "Overwriting repeats_header to false."
        )
        if isinstance(next_item, dict):
            next_item["repeats_header"] = False
        else:
            next_item.repeats_header = False


def fix_repeats_header_for_continued_tables(*, next_item: Any, prev_item: Any) -> None:
    """Fix repeats_header on continued tables based on header signature matching. If
    the last continued item on prev page is a table and the first continued item on
    next page is a table, and their header signatures match, then enforce
    next_table.repeats_header=True.

    Parameters
    ----------
    next_item
        The page IR for the next page.
    prev_item
        The page IR for the previous page.
    """

    # Only for table-to-table continuations.
    prev_kind = (
        prev_item.get("kind")
        if isinstance(prev_item, dict)
        else getattr(prev_item, "kind", None)
    )
    next_kind = (
        next_item.get("kind")
        if isinstance(next_item, dict)
        else getattr(next_item, "kind", None)
    )
    if (
        prev_kind != PageContinuationKind.TABLE.value
        or next_kind != PageContinuationKind.TABLE.value
    ):
        return

    if not is_truncated(_boundary_str(prev_item)) or not is_resumed(
        _boundary_str(next_item)
    ):
        return

    prev_sig = table_header_signature(prev_item)
    next_sig = table_header_signature(next_item)

    if prev_sig is None or next_sig is None:
        return

    if prev_sig == next_sig:
        # Enforce repeats_header on the resumed page.
        current_val = (
            next_item.get("repeats_header")
            if isinstance(next_item, dict)
            else getattr(next_item, "repeats_header", None)
        )
        if current_val is False:
            logger.warning(
                "Detected repeated table header on continued table but "
                "repeats_header=false; overwriting to true."
            )
            if isinstance(next_item, dict):
                next_item["repeats_header"] = True
            else:
                next_item.repeats_header = True


def get_negative_threshold_based_on_kind(
    *, next_item: dict[str, Any], prev_item: dict[str, Any]
) -> float:
    """Get confidence threshold for applying no-continuation patches based on
    continuation kind.

    NB: Require VERY high confidence because clearing boundaries can remove useful
    extractor signal. Tables/figures are easier to judge visually, so allow a slightly
    lower threshold than plain text.
    """

    prev_kind = prev_item.get("kind")
    next_kind = next_item.get("kind")

    # Table to table and figure to figure are usually visually obvious.
    if prev_kind == "table" and next_kind == "table":
        return 0.75

    if (
        prev_kind == "block"
        and next_kind == "block"
        and is_figure_block(prev_item)
        and is_figure_block(next_item)
    ):
        return 0.75

    # Other kinds (text to text, table to text, figure to text, etc.) are harder. Can
    # be more conservative (if we want).
    return 0.75


def get_threshold_based_on_kind(
    *,
    next_item: dict[str, Any],
    prev_item: dict[str, Any],
    verdict: PageIRContinuityVerdict,
) -> float:
    """Get confidence threshold based on continuation kind.

    Parameters
    ----------
    next_item
        The actual item dictionary for the next page candidate.
    prev_item
        The actual item dictionary for the previous page candidate.
    verdict
        The continuity verdict from the model.

    Returns
    -------
    float
        The confidence threshold for applying implicit edits.
    """

    kind = verdict.continuation_kind.value

    # NB: Figure continuations should use continuation_kind="figure" and require high
    # confidence.
    is_fig_pair = is_figure_block(prev_item) or is_figure_block(next_item)

    if kind == "table":
        threshold = 0.75
    elif kind == "text":
        threshold = 0.75
    elif kind == "none":
        threshold = 0.75
    elif not (verdict.is_continuation and is_fig_pair):
        threshold = 1.1  # Impossible threshold --> no downstream edits
    else:
        threshold = 0.75

    return threshold


def has_structural_block_above_candidate(
    *,
    candidate_index: int,
    items: list[dict[str, Any]],
    require_section_break_text: bool = True,
) -> bool:
    """Determine if there is a non-artifact structural block (HEADING/CAPTION/TITLE)
    immediately above the candidate *as the first meaningful thing on the page*.

    The process is as follows:

    1. We scan upwards from the candidate and stop once we hit real content
        (paragraph/list/table/figure). This prevents false positives from earlier
        headings that are not actually the "start context" for the candidate.
    2. If require_section_break_text=True, only treat certain known section headers
        (e.g., "bibliography", "list of tables") as hard negatives.

    Parameters
    ----------
    candidate_index
        The index of the candidate item in the items list.
    items
        The list of PageIR items.
    require_section_break_text
        If True, only treat known section break headings as valid structural blocks.

    Returns
    -------
    bool
        True if a structural block is found above the candidate, False otherwise.
    """

    structural = {"heading", "caption", "title"}

    # Walk upward from the candidate, nearest blocks first.
    for it in reversed(items[:candidate_index]):
        kind = it.get("kind")
        if kind == "table":
            # Table above candidate means candidate isn't top-of-page content.
            return False

        if kind != "block":
            continue

        bt = (it.get("block_type") or "").lower()
        if bt == "artifact":
            continue

        # If we hit real body content above the candidate, then the candidate is not
        # "starting under a new heading at top of page" in the relevant sense.
        if bt in {"paragraph", "list"}:
            return False

        if bt in structural:
            if not require_section_break_text:
                return True

            text_unit = it.get("text") or {}
            txt = (text_unit.get("text") or "").strip().lower()
            return txt in SECTION_BREAK_HEADINGS

        # If we encounter other block types, keep scanning.
        if bt == "figure":
            return False

    return False


def is_artifact(item: dict[str, Any]) -> bool:
    """Check if an item is an artifact.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is an artifact, False otherwise.
    """

    if item.get("kind") != "block":
        return False
    return _block_type_val(item) == BlockType.ARTIFACT.value


def is_figure_block(item: dict[str, Any]) -> bool:
    """Check if an item is a figure/diagram block.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is a figure block, False otherwise.
    """

    if item.get("kind") != "block":
        return False
    return _block_type_val(item) == BlockType.FIGURE.value


def is_full_page_bbox(
    *, bb: tuple[float, ...], page_bbox: tuple[float, ...], tol: float
) -> bool:
    """Check if a bbox is effectively full-page within tolerance.

    Parameters
    ----------
    bb
        The bbox to check.
    page_bbox
        The page bbox.
    tol
        The tolerance for comparison.

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


def is_minor_edge_block(*, image_height: float, item: dict[str, Any]) -> bool:
    """Return True for small, low-information blocks near an edge that often sit
    below/above the real continuation content (e.g., short notes, tiny captions).

    NB: Keep this conservative to avoid suppressing real content.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is a minor edge block, False otherwise.
    """

    bt = _block_type_val(item)

    # If not a block then it's never "minor". In addition, headings are usually
    # meaningful context; do not treat as "minor".
    if item.get("kind") != "block" or bt == BlockType.HEADING.value:
        return False

    # Figures/diagrams often appear as small isolated blocks near the edge (icons,
    # stamps, small illustrations). For continuity anchoring, treat *small* figures as
    # "minor" so they don't steal the anchor from a nearby table/text.
    if bt == BlockType.FIGURE.value:
        _, y0, _, y1 = map(float, item["bbox"])
        box_h = y1 - y0
        return box_h <= max(180.0, 0.10 * image_height)

    text = (extract_text(item.get("text")) or "").strip()
    if not text:
        return False

    _, y0, _, y1 = map(float, item["bbox"])
    box_h = y1 - y0

    # Captions are often the first/last thing near the edge ("e.g., Table 2: ..."), and
    # we usually want the *table* (or main text) as the continuity anchor. Treat
    # caption blocks as minor based on visual size, not character length.
    if bt == BlockType.CAPTION.value and box_h <= max(180.0, 0.08 * image_height):
        return True

    # "minor" if it's short AND visually small.
    if len(text) <= 80 and box_h <= max(120.0, 0.06 * image_height):
        return True

    return False


def is_probable_header_footer_noise(
    *,
    image_height: float,
    item: dict[str, Any],
) -> bool:
    """Heuristic to exclude common header/footer noise (page numbers, running headers).

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is likely header/footer noise, False otherwise.
    """

    text = (extract_text(item.get("text")) or "").strip()

    if item.get("kind") != "block" or not text:
        return False

    # Very small box height is usually a strong cue (page numbers, running headers).
    bbox = item["bbox"]
    _, y0, _, y1 = map(float, bbox)
    near_top = y0 <= 0.06 * image_height
    near_bottom = y1 >= 0.94 * image_height
    if not (near_top or near_bottom):
        return False

    # Require small box height to avoid sparse pages from being mis-classified as
    # footer/header noise.
    box_h = y1 - y0
    if box_h > max(90.0, 0.05 * image_height):
        return False

    # Common page number/footer patterns (keep conservative).
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= 12 and re.fullmatch(r"(\d+|[ivxlcdm]+)", t.lower()):
        return True
    if len(t) <= 20 and re.fullmatch(r"(page\s*)?\d+(\s*/\s*\d+)?", t.lower()):
        return True

    return False


def is_resumed(boundary: str) -> bool:
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


def is_truncated(boundary: str) -> bool:
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


def item_snippet(
    *, item: dict[str, Any], max_len: int = 260, text_mode: str = "head"
) -> dict[str, Any]:
    """Create a small, stable representation of an item to show the verifier model.

    Parameters
    ----------
    item
        The item to create a snippet for.
    max_len
        Maximum length of text fields.
    text_mode
        Text truncation mode ("head" or "tail").

    Returns
    -------
    dict[str, Any]
        The item snippet.
    """

    kind = item["kind"]
    assert kind in {"block", "table"}, f"Unexpected item kind: {kind}"
    out: dict[str, Any] = {
        "kind": kind,
        "bbox": item["bbox"],
        "boundary": item.get("boundary"),
        "local_code": item.get("local_code"),
    }

    if kind == "block":
        bt = _block_type_val(item)
        out["block_type"] = bt
        out["language"] = (item.get("text") or {}).get("language")
        text = (item.get("text") or {}).get("text")
        if isinstance(text, str):
            snippet, was_truncated = _truncate_text_word_boundary(
                max_len=max_len, mode=text_mode, text=text
            )
            out["text"] = snippet
            out["text_was_truncated"] = was_truncated
            out["text_snippet_mode"] = text_mode
        if bt == BlockType.LIST.value:
            lis = item.get("list_items") or []
            out["list_items"] = [
                {
                    "marker": (li.get("marker") or ""),
                    "text": (extract_text(li.get("text")) or "")[:120],
                }
                for li in lis[:6]
            ]

        # Include lightweight metadata so the verifier isn't looking at an "empty"
        # excerpt for figures.
        if is_figure_block(item):
            fig = item.get("figure") or {}
            cap = fig.get("caption")
            out["figure"] = {
                "figure_kind": fig.get("figure_kind"),
                "contains_text": fig.get("contains_text"),
                "alt_text": (fig.get("alt_text") or "")[:200],
                "caption": (extract_text(cap) or "")[:max_len],
                "caption_language": (
                    (cap or {}).get("language") if isinstance(cap, dict) else None
                ),
            }
    else:
        out["header_row_count"] = item.get("header_row_count")
        out["repeats_header"] = item.get("repeats_header")
        out["n_cols"] = item.get("n_cols")
        out["n_rows"] = len(item.get("rows") or [])
        rows = item.get("rows") or []

        # Show up to 2 header rows + 1 first body row + last 2 rows.
        hrc = item.get("header_row_count") or 0
        head = rows[: min(len(rows), min(2, hrc) if hrc else 2)]

        first_body = rows[hrc : hrc + 1] if (hrc and len(rows) > hrc) else []
        tail = rows[-2:] if len(rows) > 2 else []

        def row_to_text(r: dict[str, Any]) -> list[Optional[str]]:
            """Convert a table row to a list of cell texts.

            Parameters
            r
                The table row.

            Returns
            -------
            list[Optional[str]]
                The list of cell texts.
            """

            out_cells: list[Optional[str]] = []
            for c in r.get("cells", []):
                t = extract_text(c.get("text"))
                if isinstance(t, str):
                    t = t[:120]
                out_cells.append(t)
            return out_cells

        out["rows_head"] = [row_to_text(r) for r in head]
        out["rows_first_body"] = [row_to_text(r) for r in first_body]
        out["rows_tail"] = [row_to_text(r) for r in tail]

    return out


def load_page_irs_from_extraction(
    *, end_page: int, page_irs_dir: Path, start_page: int
) -> dict[int, dict[str, Any]]:
    """Load page IR JSONs from the extraction output directory.

    Parameters
    ----------
    end_page
        0-based end page (exclusive).
    page_irs_dir
        Directory containing the page IR JSONs.
    start_page
        0-based start page (inclusive).

    Returns
    -------
    dict[int, dict[str, Any]]
        The dictionary of page IRs by page index.
    """

    page_irs: dict[int, dict[str, Any]] = {
        i: PageIR.model_validate(
            open_json_type(page_irs_dir / f"{i:04}.json")
        ).model_dump(mode="json")
        for i in range(start_page, end_page)
    }

    # Preserve extraction hints (internal-only) so reports can show what the extractor
    # believed. Verification will PATCH only when confidence is high.
    for page_ir in page_irs.values():
        for item in page_ir.get("items", []):
            item["_orig_boundary"] = item.get("boundary")

    return page_irs


def min_crop_height_px(*, kind: str, page_h_px: int) -> int:
    """Get the minimum crop height in pixels for continuity candidate extraction.

    kind:
      - "table": show more (tables need extra rows)
      - "figure": show more (figures need more visual context)
      - default: text-ish blocks

    Parameters
    ----------
    kind
        The kind of item ("table" or other).
    page_h_px
        The height of the page in pixels.

    Returns
    -------
    int
        The minimum crop height in pixels.
    """

    if kind == "table":
        return clamp(0.33 * page_h_px, low=min(900, page_h_px), high=1600)

    if kind == "figure":
        # Bigger than text blocks so the verifier sees enough context/continuation cues.
        return clamp(0.28 * page_h_px, low=750, high=1500)

    return clamp(0.16 * page_h_px, low=450, high=1000)


def normalize_table_row_cell_counts(
    *, page_irs: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ensure each table row has exactly n_cols cells by inserting blank cells where
    extraction omitted visually-empty/row-spanned leading columns. This makes
    downstream column-based parsing deterministic.

    Parameters
    ----------
    page_irs
        Mapping of page index to PageIR dict.

    Returns
    -------
    list[dict[str, Any]]
        A list of change records describing the modifications made.
    """

    changes: list[dict[str, Any]] = []

    for page_idx in sorted(page_irs.keys()):
        page = page_irs[page_idx]
        for item_idx, it in enumerate(page.get("items", [])):
            if it.get("kind") != "table":
                continue

            n_cols = it.get("n_cols")
            if not isinstance(n_cols, int) or n_cols <= 0:
                continue

            rows = it.get("rows", [])
            for row_idx, row in enumerate(rows):
                cells = row.get("cells", [])
                if not isinstance(cells, list):
                    continue

                # Keep as is.
                if len(cells) == n_cols or len(cells) > n_cols:
                    continue

                missing = n_cols - len(cells)

                # Heuristic: if the first visible cell looks like a competence code
                # anywhere at the start of a line (e.g., "3.2", "5.2"), the missing
                # cells are almost certainly LEADING (SN/Subject columns under a
                # row-span).
                first_txt = ""
                if cells and isinstance(cells[0], dict):
                    t = cells[0].get("text") or {}
                    first_txt = t.get("text") or ""

                codeish = bool(re.search(r"(^|\n)\s*\d+\.\d+", first_txt))
                pad = [
                    {"col_span": 1, "row_span": 1, "text": None} for _ in range(missing)
                ]
                row["cells"] = (pad + cells) if codeish else (cells + pad)

                changes.append(
                    {
                        "type": "pad_table_row_cells",
                        "page": page_idx,
                        "item_index": item_idx,
                        "row_index": row_idx,
                        "n_cols": n_cols,
                        "before": len(cells),
                        "after": len(row["cells"]),
                        "side": "left" if codeish else "right",
                    }
                )

    return changes


def pad_inches(kind: str) -> float:
    """Get the padding in inches for continuity candidate extraction.

    Parameters
    ----------
    kind
        The kind of item ("table" or other).

    Returns
    -------
    float
        The padding in inches.
    """

    if kind == "table":
        return 0.35
    if kind == "figure":
        return 0.50
    return 0.25


def persist_extraction_run(
    *,
    country: str,
    dpi: int,
    end_page: Optional[int],
    pdf_fp: Path,
    languages: list[str],
    model: str,
    output_dir: Path,
    overwrite: bool,
    start_page: int,
    use_text_layer_hints: bool,
) -> tuple[str, PageIRExtractionDirs, RunCtx]:
    """Persist extraction run metadata.

    Parameters
    ----------
    country
        The country associated with the PDF document.
    dpi
        Render DPI for page images.
    end_page
        0-based end page (exclusive).
    pdf_fp
        The file path to the PDF document to extract curriculum data from.
    languages
        One or more languages associated with the PDF document.
    model
        OpenAI model for page IR extraction.
    output_dir
        Output directory root.
    overwrite
        Specifies whether to overwrite existing per-page artifacts.
    start_page
        0-based start page (inclusive).
    use_text_layer_hints
        Whether to extract and use text layer hints from the PDF during extraction.

    Returns
    -------
    tuple[str, ExtractionDirs, RunCtx]
        The document key, extraction directories, and extraction run record.
    """

    doc_key = compute_doc_key(n_hex=64, pdf_fp=pdf_fp)
    extraction_dirs = create_page_ir_extraction_dirs(
        doc_key=doc_key, output_dir=output_dir
    )
    extraction_run = RunCtx(
        extra={
            "country": country,
            "doc_key": doc_key,
            "dpi": dpi,
            "end_page_cli": end_page,  # Keep original CLI value (may be None)
            "languages": languages,
            "pdf_name": pdf_fp.name,
            "overwrite": overwrite,
            "start_page": start_page,
            "use_text_layer_hints": use_text_layer_hints,
        },
        models=[model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(
        fp=extraction_dirs.root / "extraction_run.json", json_info=extraction_run
    )
    logger.info(f"Extraction directory: {extraction_dirs.root}")

    return doc_key, extraction_dirs, extraction_run


def persist_verification_run(
    *,
    end_page: Optional[int],
    model: str,
    output_dir: Path,
    start_page: int,
    **kwargs: Any,
) -> tuple[PageIRVerificationDirs, RunCtx]:
    """Persist verification run metadata.

    Parameters
    ----------
    end_page
        0-based end page (exclusive).
    model
        OpenAI model for page IR continuity verification.
    output_dir
        The output directory for the verified page IR JSONs.
    start_page
        0-based start page (inclusive).
    kwargs
        Additional extraction run configuration parameters.

    Returns
    -------
    tuple[PageIRVerificationDirs, RunCtx]
        The created verification directories and persisted verification run metadata.
    """

    extra = kwargs.get("extra", {})
    extra.update(
        {
            "end_page_cli": end_page,  # Keep original CLI value (may be None)
            "start_page_cli": start_page,
        }
    )
    extra.pop("status", None)
    verification_dirs = create_page_ir_verification_dirs(output_dir=output_dir)
    verification_run = RunCtx(
        extra=extra,
        models=[model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "verification_run.json", json_info=verification_run)
    logger.info(f"Verification directory: {output_dir}")

    return verification_dirs, verification_run


def postprocess_verified_page_irs(
    *, page_irs: dict[int, dict[str, Any]], verification_dirs: PageIRVerificationDirs
) -> None:
    """Run all postpass fixes before writing verified JSONs.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.
    verification_dirs
        The verification directories.
    """

    table_code_changes = propagate_table_local_codes(page_irs=page_irs)
    pad_changes = normalize_table_row_cell_counts(page_irs=page_irs)

    # Persist what was changed for audit/debug.
    write_to_json(
        fp=verification_dirs.root / "postprocess_report.json",
        json_info={
            "table_local_code_changes": table_code_changes,
            "table_row_padding_changes": pad_changes,
        },
    )


def propagate_table_local_codes(
    *, page_irs: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Carry forward the most recent "Table X" local_code across continuation segments
    when repeats_header=true or boundary indicates continuation and local_code is null.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.

    Returns
    -------
    list[dict[str, Any]]
        A list of changes made during the postpass.
    """

    carried_table_code: str | None = None
    changes: list[dict[str, Any]] = []

    for i in sorted(page_irs.keys()):
        page = page_irs[i]
        items = page.get("items", [])

        # Capture any caption-defined table code on the page (preferred source of
        # truth).
        if (caption_code := find_caption_code(items)) is not None:
            carried_table_code = caption_code

        table_continues_to_next = False

        for idx, it in enumerate(items):
            if it.get("kind") != "table":
                continue

            boundary = _boundary_val(it.get("boundary"), default_val="")

            # If this is a new table start (not continuing from prev), we generally
            # shouldn't apply the *previous* table's code. We should only carry forward
            # if we are inside a specific chain.
            is_resumed_or_both = boundary in (
                ItemBoundary.RESUMED.value,
                ItemBoundary.BOTH.value,
            )

            # If this item does NOT continue from the previous one, break the chain.
            if not is_resumed_or_both:
                carried_table_code = None

            # Update carry if THIS item has an explicit code.
            code = (it.get("local_code") or "").strip()
            if code:
                carried_table_code = code

            # Fill missing local_code if we’re clearly in a continuation chain.
            elif is_resumed_or_both and carried_table_code:
                it["local_code"] = carried_table_code
                changes.append(
                    {
                        "type": "propagate_table_local_code",
                        "page": i,
                        "item_index": idx,
                        "set_local_code": carried_table_code,
                    }
                )

            # Check if this table continues to the NEXT page to decide if we should
            # keep `carried_table_code` alive for the next page loop.
            if boundary in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value):
                table_continues_to_next = True

        # Only keep carrying forward when the table actually continues to the next page.
        if not table_continues_to_next:
            carried_table_code = None

    return changes


def sanitize_verdict_for_candidate_kinds(
    *,
    next_item: dict[str, Any],
    prev_item: dict[str, Any],
    verdict: PageIRContinuityVerdict,
) -> PageIRContinuityVerdict:
    """Drop (veto) continuations that are structurally impossible for the chosen
    candidates.

    Parameters
    ----------
    next_item
        The actual item dictionary for the next page candidate.
    prev_item
        The actual item dictionary for the previous page candidate.
    verdict
        The continuation verdict from the model.

    Returns
    -------
    PageIRContinuityVerdict
        The sanitized verdict.
    """

    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)

    if verdict.is_continuation and kind == PageContinuationKind.NONE.value:
        return veto_continuation(
            reason=f"continuation_kind={PageContinuationKind.NONE.value} is incompatible with is_continuation=true",
            verdict=verdict,
        )

    if (not verdict.is_continuation) and kind != PageContinuationKind.NONE.value:
        verdict.continuation_kind = PageContinuationKind.NONE
        verdict.set_prev_item_boundary = None
        verdict.set_next_item_boundary = None
        verdict.set_next_table_repeats_header = None
        return verdict

    prev_kind = prev_item.get("kind")
    next_kind = next_item.get("kind")

    # Text continuations must be block-to-block (never into/from a table).
    if kind == PageContinuationKind.TEXT.value and (
        prev_kind != "block" or next_kind != "block"
    ):
        return veto_continuation(
            reason="continuation_kind=text requires both candidates to be block items",
            verdict=verdict,
        )

    # Table continuations must be table-to-table.
    if kind == PageContinuationKind.TABLE.value and (
        prev_kind != "table" or next_kind != "table"
    ):
        return veto_continuation(
            reason="continuation_kind=table requires both candidates to be table items",
            verdict=verdict,
        )

    # Figure continuations must be figure-to-figure blocks.
    if kind == PageContinuationKind.FIGURE.value and (
        not (is_figure_block(prev_item) and is_figure_block(next_item))
    ):
        return veto_continuation(
            reason="continuation_kind=figure requires both candidates to be figure blocks",
            verdict=verdict,
        )

    return verdict


def save_verified_page_irs(
    *, page_irs: dict[int, dict[str, Any]], verification_dirs: PageIRVerificationDirs
) -> None:
    """Save verified page IRs to the verified directory.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.
    verification_dirs
        The verification directories.
    """

    logger.info(
        f"Saving all verified page IR JSONs to: {verification_dirs.page_irs_verified}"
    )

    for i in sorted(page_irs.keys()):
        page_ir = page_irs[i]

        # Remove internal-only fields before writing outputs (schema forbids extras).
        for it in page_ir.get("items", []):
            it.pop("_orig_boundary", None)

        # Derive page-level boundary_state from verified item boundaries.
        page_ir["boundary_state"] = derive_page_boundary_state(page_ir=page_ir).value

        # Write verified JSON.
        write_to_json(
            fp=verification_dirs.page_irs_verified / f"{i:04}.json", json_info=page_ir
        )

    logger.success("All verified page IR JSONs saved successfully!")


def should_veto_text_continuation_due_to_ordering_safety(
    *,
    next_idx: int,
    next_item: dict[str, Any],
    next_page_items: list[dict[str, Any]],
    verdict: PageIRContinuityVerdict,
) -> bool:
    """Deterministically veto TEXT/LIST continuations when a structural heading/caption
    appears above the next candidate.

    Parameters
    ----------
    next_idx
        Index of the continuity candidate item on the next page.
    next_item
        The actual item dictionary for the next page candidate.
    next_page_items
        The list of items on the next page.
    verdict
        The continuity verdict from the model.

    Returns
    -------
    bool
        True if the continuation should be vetoed due to ordering safety.
    """

    if not verdict.is_continuation:
        return False

    kind = getattr(verdict.continuation_kind, "value", verdict.continuation_kind)
    if kind != PageContinuationKind.TEXT.value:
        return False

    # Do not apply this rule to TABLE continuations.
    if next_item.get("kind") == "table":
        return False

    return has_structural_block_above_candidate(
        candidate_index=next_idx, items=next_page_items
    )


def table_header_signature(table: Any) -> tuple[tuple[str, ...], ...] | None:
    """Extract a normalized table header signature from a Table-like object.

    Parameters
    ----------
    table
        The Table-like object.

    Returns
    -------
    tuple[tuple[str, ...], ...] | None
        The table header signature, or None if no valid header exists.
    """

    if isinstance(table, dict):
        rows = table.get("rows") or []
        h = int(table.get("header_row_count") or 0)
    else:
        rows = getattr(table, "rows", None) or []
        h = int(getattr(table, "header_row_count", 0) or 0)

    if h <= 0 or len(rows) < h:
        return None

    sig = []
    for r in rows[:h]:
        cells = r.get("cells", []) if isinstance(r, dict) else getattr(r, "cells", [])
        row_sig = []
        for c in cells:
            txt_unit = (
                c.get("text") if isinstance(c, dict) else getattr(c, "text", None)
            )
            cleaned_text = " ".join(
                (textunit_text(txt_unit) or "").strip().lower().split()
            )
            row_sig.append(cleaned_text)
        sig.append(tuple(row_sig))

    # If the header is entirely empty, ignore.
    if not any(any(cell for cell in row) for row in sig):
        return None

    return tuple(sig)


def textunit_text(tu: Any) -> str:
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

    if tu is None:
        return ""

    value = tu.get("text") if isinstance(tu, dict) else getattr(tu, "text", None)

    return value if isinstance(value, str) else ""


def topmost_continuity_candidate(
    *,
    image_height: float,
    items: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    """Pick the best "top of page" candidate for continuity checks.

    Heuristic:
        - Choose the non-artifact item with smallest bbox y0 (closest to top).
        - If multiple items are near the top-most y0 (within a small delta),
            prefer a table among that near-top set.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of items to search.

    Returns
    -------
    tuple[int, dict]
        The index and the chosen item.

    Raises
    ------
    ValueError
        If no non-artifact items are found.
    """

    candidates = [
        (i, it)
        for i, it in enumerate(items)
        if (not is_artifact(it))
        and (not is_probable_header_footer_noise(image_height=image_height, item=it))
    ]
    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Look at the first ~5 candidates by vertical position (closest to top). If any are
    # explicitly marked as resumed, prefer those (tables first).
    first_n = 5
    top_sorted = sorted(candidates, key=lambda p: (float(p[1]["bbox"][1]), p[0]))
    top_slice = top_sorted[: min(first_n, len(top_sorted))]
    resumed = [
        (i, it) for (i, it) in top_slice if _has_boundary(it, ItemBoundary.RESUMED)
    ]
    if resumed:
        resumed_non_fig = [(i, it) for (i, it) in resumed if not is_figure_block(it)]
        base = resumed_non_fig if resumed_non_fig else resumed
        resumed_tables = [(i, it) for (i, it) in base if it.get("kind") == "table"]
        chosen_res = resumed_tables if resumed_tables else base
        return min(chosen_res, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # 2. Table-in-top-band override (top 20%), when edge-most is not a table.
    edge_item = min(candidates, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # If the edge-most item is a small "minor" block (short note), but a continuation
    # table starts near the top, prefer the table.
    if edge_item[1].get("kind") != "table" and is_minor_edge_block(
        image_height=image_height, item=edge_item[1]
    ):
        tables = [(i, it) for (i, it) in candidates if it.get("kind") == "table"]
        if tables:
            best_table = min(tables, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

            # Wider band than 20% (tables can start below a small header line).
            if float(best_table[1]["bbox"][1]) <= 0.30 * image_height:
                return best_table

    if edge_item[1].get("kind") != "table":
        top_band_y = 0.20 * image_height
        tables_in_band = [
            (i, it)
            for (i, it) in candidates
            if it.get("kind") == "table" and float(it["bbox"][1]) <= top_band_y
        ]
        if tables_in_band:
            return min(tables_in_band, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

    # Edge-first: find the min y0, then consider items close to that edge.
    min_y0 = min(float(it["bbox"][1]) for _, it in candidates)
    edge_slop = min(200.0, max(80.0, 0.04 * image_height))
    near_edge = [
        (i, it)
        for i, it in candidates
        if near(float(it["bbox"][1]), min_y0, tol=edge_slop)
    ]
    table_near_edge = [(i, it) for i, it in near_edge if it.get("kind") == "table"]
    if table_near_edge:
        chosen = table_near_edge
    else:
        non_fig = [(i, it) for (i, it) in near_edge if not is_figure_block(it)]
        chosen = non_fig if non_fig else near_edge

    return min(chosen, key=lambda p: (float(p[1]["bbox"][1]), p[0]))


def topmost_continuity_candidate_paired(
    *, image_height: float, items: list[dict[str, Any]], prev_item: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Pick the best next-page candidate *conditioned on* the chosen previous page
    candidate. Falls back to `topmost_continuity_candidate` if no good match is found.

    Motivation:
    - If the prev candidate is a table, we should strongly prefer the first real table
      near the top of the next page (even if there are headings above it).
    - If the prev candidate is a block, we should strongly prefer a block near the top,
      avoiding selecting a table just because it starts near the edge.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of items to search.
    prev_item
        The chosen previous page candidate item.

    Returns
    -------
    tuple[int, dict[str, Any]]
        The index and the chosen item.

    Raises
    ------
    ValueError
        If no non-artifact items or continuity candidates are found.
    """

    preferred_kind = prev_item.get("kind")

    # Use the same base candidate filtering as the unpaired selector.
    candidates = [
        (i, it)
        for i, it in enumerate(items)
        if (not is_artifact(it))
        and (not is_probable_header_footer_noise(image_height=image_height, item=it))
    ]

    if not candidates:
        raise ValueError("No non-artifact items found.")

    # Prefer table when the previous item is a table.
    if preferred_kind == "table":
        tables = [(i, it) for (i, it) in candidates if it.get("kind") == "table"]
        if tables:
            # If any table is explicitly RESUMED, pick the top-most resumed table.
            resumed_tables = [
                (i, it) for (i, it) in tables if _has_boundary(it, ItemBoundary.RESUMED)
            ]
            if resumed_tables:
                return min(resumed_tables, key=lambda p: (float(p[1]["bbox"][1]), p[0]))

            # Otherwise pick the top-most table, but only if it's not absurdly far down.
            best_table = min(tables, key=lambda p: (float(p[1]["bbox"][1]), p[0]))
            if float(best_table[1]["bbox"][1]) <= 0.65 * image_height:
                return best_table

    # Prefer block when the previous item is a block.
    if preferred_kind == "block":
        blocks = [(i, it) for (i, it) in candidates if it.get("kind") != "table"]
        if not blocks:
            raise ValueError("No continuity candidates found")

        # If we have any non-minor blocks near the top, drop minor edge blocks (e.g.,
        # short captions like "Table 2: ...") so they don't become the anchor.
        non_minor = [
            (i, it)
            for (i, it) in blocks
            if not is_minor_edge_block(image_height=image_height, item=it)
        ]
        blocks = non_minor or blocks

        # If previous is paragraph/list, prefer non-heading/non-caption blocks on next
        # page.
        prev_bt = str(prev_item.get("block_type") or "").lower()
        if prev_bt in {"paragraph", "list"}:
            contentish = [
                (i, it)
                for (i, it) in blocks
                if str(it.get("block_type") or "").lower() not in {"heading", "caption"}
            ]
            blocks = contentish or blocks

        non_figure_blocks = [(i, it) for (i, it) in blocks if not is_figure_block(it)]
        return non_figure_blocks[0] if non_figure_blocks else blocks[0]

    # Fallback to unpaired logic.
    return topmost_continuity_candidate(image_height=image_height, items=items)


def veto_continuation(
    *, reason: str, verdict: PageIRContinuityVerdict
) -> PageIRContinuityVerdict:
    """Veto a continuation claim by forcing is_continuation=False with low confidence.

    Parameters
    ----------
    reason
        The reason for vetoing the verdict.
    verdict
        The continuity verdict from the model.

    Returns
    -------
    PageIRContinuityVerdict
        The modified verdict with the veto applied.
    """

    logger.warning(f"Vetoing continuation due to: {reason}")

    verdict.is_continuation = False

    # Candidate mismatch means we can't trust the continuation claim between THESE two
    # items, not "there is definitely no continuation anywhere". Keep this
    # continuation_kind="none" and low-confidence so downstream edit-application
    # thresholds will not apply.
    verdict.clamped_confidence = min(float(verdict.confidence), 0.49)
    verdict.continuation_kind = PageContinuationKind.NONE
    verdict.rationale = (verdict.rationale or "") + f" | Postprocess veto: {reason}"

    # NB: Never apply edits if we veto the continuation claim.
    verdict.set_prev_item_boundary = None
    verdict.set_next_item_boundary = None
    verdict.set_next_table_repeats_header = None

    return verdict
