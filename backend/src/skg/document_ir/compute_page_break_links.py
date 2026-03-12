"""This module contains utility functions for computing page break links for the
document IR.
"""

# Standard Library
import hashlib

from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.utils import (
    compatible_kinds_for_stitch,
    extract_table_or_figure_local_code,
    normalize_local_code,
    row_signature,
)
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TextUnit
from skg.page_ir_verification.utils import EdgeVerdictRecord, is_artifact
from skg.utils.constants import BlockType, ItemBoundary, PageBoundaryState

ItemKey = tuple[int, int]


def _append_rejected_warnings(
    *,
    is_prev: bool,
    items: list[tuple[int, Block | Table]],
    page_ir: PageIR,
    rejected_indices: list[int],
    warnings: list[str],
) -> None:
    """Append warnings for candidates rejected due to unsafe content ordering.

    Parameters
    ----------
    is_prev
        If True, logging for previous-page candidates; else next-page candidates.
    items
        The page's normalized items list.
    page_ir
        The PageIR.
    rejected_indices
        A list of indices of rejected candidates.
    warnings
        A list to append warning messages to.
    """

    if not rejected_indices:
        return

    reason = "followed" if is_prev else "preceded"

    for r_index in rejected_indices:
        orig_index, item = items[r_index]
        msg = (
            f"Skipped stitching candidate on {'previous' if is_prev else 'next'} "
            f"page because it is {reason} by non-artifact content (would reorder content): "
            f"page={page_ir.page_index} "
            f"item_index={orig_index} "
            f"kind={item.kind} "
            f"boundary={item.boundary.value}"
        )
        logger.warning(msg)
        warnings.append(msg)


def _append_unmatched_warnings(
    *,
    current_page_ir: PageIR,
    next_candidate_indices: list[int],
    next_items: list[tuple[int, Block | Table]],
    next_page_ir: PageIR,
    prev_candidate_indices: list[int],
    prev_items: list[tuple[int, Block | Table]],
    warnings: list[str],
) -> None:
    """Append warnings when valid candidates exist on one side but not the other.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
    next_candidate_indices
        A list of indices of valid next-page candidates.
    next_items
        The next page's normalized items list.
    next_page_ir
        The next PageIR.
    prev_candidate_indices
        A list of indices of valid previous-page candidates.
    prev_items
        The previous page's normalized items list.
    warnings
        A list to append warning messages to.
    """

    if prev_candidate_indices and not next_candidate_indices:
        for prev_index in prev_candidate_indices:
            prev_orig_index, prev_item = prev_items[prev_index]
            msg = (
                f"Unmatched continuation on previous page (TRUNCATED/BOTH) "
                f"- no eligible next-page candidate: "
                f"page={current_page_ir.page_index} item_index={prev_orig_index} "
                f"kind={prev_item.kind} boundary={prev_item.boundary.value}"
            )
            logger.warning(msg)
            warnings.append(msg)

    if next_candidate_indices and not prev_candidate_indices:
        for next_index in next_candidate_indices:
            next_orig_index, next_item = next_items[next_index]
            msg = (
                f"Unmatched continuation on next page (RESUMED/BOTH) "
                f"- no eligible previous-page candidate: "
                f"page={next_page_ir.page_index} item_index={next_orig_index} "
                f"kind={next_item.kind} boundary={next_item.boundary.value}"
            )
            logger.warning(msg)
            warnings.append(msg)


def _apply_page_boundary_state_guardrails(
    *,
    current_page_ir: PageIR,
    next_candidate_indices: list[int],
    next_page_ir: PageIR,
    next_page_items: list[tuple[int, Block | Table]],
    prev_candidate_indices: list[int],
    prev_page_items: list[tuple[int, Block | Table]],
    warnings: list[str],
) -> tuple[list[int], list[int], bool]:
    """Check page-level boundary states: only stitch across this page break when both
    pages claim continuity in the appropriate direction.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
    next_candidate_indices
        A list of indices of valid next-page candidates.
    next_page_ir
        The next PageIR.
    next_page_items
        The next page's normalized items list.
    prev_candidate_indices
        A list of indices of valid previous-page candidates.
    prev_page_items
        The previous page's normalized items list.
    warnings
        A list to append warning messages to.

    Returns
    -------
    tuple[list[int], list[int], bool]
        The (potentially filtered) previous and next candidate indices, and a flag
        indicating if stitching is allowed to proceed.
    """

    allowed_forward = current_page_ir.boundary_state in (
        PageBoundaryState.CONTINUES_TO_NEXT,
        PageBoundaryState.BOTH,
    )
    allowed_backward = next_page_ir.boundary_state in (
        PageBoundaryState.CONTINUES_FROM_PREV,
        PageBoundaryState.BOTH,
    )

    if allowed_forward and allowed_backward:
        return prev_candidate_indices, next_candidate_indices, True

    # Exception: allow *table* stitching when there is a strong local_code match.
    prev_codes = {
        normalize_local_code(prev_page_items[prev_index][1].local_code)
        for prev_index in prev_candidate_indices
        if isinstance(prev_page_items[prev_index][1], Table)
        and normalize_local_code(prev_page_items[prev_index][1].local_code)
    }
    next_codes = {
        normalize_local_code(next_page_items[next_index][1].local_code)
        for next_index in next_candidate_indices
        if isinstance(next_page_items[next_index][1], Table)
        and normalize_local_code(next_page_items[next_index][1].local_code)
    }
    common_codes = prev_codes & next_codes

    if not common_codes:
        msg = (
            f"Page boundary_state guardrail blocked stitching across page break "
            f"{current_page_ir.page_index}->{next_page_ir.page_index}: "
            f"current={current_page_ir.boundary_state.value} "
            f"next={next_page_ir.boundary_state.value}"
        )
        logger.warning(msg)
        warnings.append(msg)

        return [], [], False

    # Restrict stitching candidates to those strongly-anchored tables.
    filtered_prev = [
        pidx
        for pidx in prev_candidate_indices
        if isinstance(prev_page_items[pidx][1], Table)
        and normalize_local_code(prev_page_items[pidx][1].local_code) in common_codes
    ]
    filtered_next = [
        nidx
        for nidx in next_candidate_indices
        if isinstance(next_page_items[nidx][1], Table)
        and normalize_local_code(next_page_items[nidx][1].local_code) in common_codes
    ]

    return filtered_prev, filtered_next, True


def _apply_verification_verdict(
    *,
    current_page_ir: PageIR,
    edge_record: EdgeVerdictRecord,
    link_debug: list[dict[str, Any]],
    next_page_ir: PageIR,
    next_page_items: list[tuple[int, Block | Table]],
    page_pair_debug: list[dict[str, Any]],
    prev_page_items: list[tuple[int, Block | Table]],
) -> dict[ItemKey, ItemKey]:
    """Attempt to create a stitching link from a high-confidence verification verdict.

    This is called only when edge_record.verdict.confidence >= threshold and
    edge_record.verdict.is_continuation is True. It validates that the verdict's item
    indices resolve to compatible items in the normalized item lists, applies
    `set_next_table_repeats_header` when present, and returns a direct link dict.

    Parameters
    ----------
    current_page_ir
        The previous PageIR.
    edge_record
        The high-confidence edge verdict record to apply.
    link_debug
        List to append per-link debug info to.
    next_page_ir
        The next PageIR.
    next_page_items
        The next page's normalized items list.
    page_pair_debug
        List to append per-page-pair debug info to.
    prev_page_items
        The previous page's normalized items list.

    Returns
    -------
    dict[ItemKey, ItemKey]
        A single-entry link dict `{(prev_page, prev_item) : (next_page, next_item)}`.
    """

    verdict = edge_record.verdict
    prev_page = current_page_ir.page_index
    next_page = next_page_ir.page_index

    # Shared debug record for verdict-based decisions.
    pair_debug: dict[str, Any] = {
        "from_page": prev_page,
        "to_page": next_page,
        "verdict_override": True,
        "verdict_confidence": verdict.confidence,
        "verdict_is_continuation": verdict.is_continuation,
        "verdict_continuation_kind": verdict.continuation_kind.value,
        "verdict_prev_item_index": edge_record.prev_item_index,
        "verdict_next_item_index": edge_record.next_item_index,
        "chosen_links": [],
    }

    prev_idx = edge_record.prev_item_index
    next_idx = edge_record.next_item_index
    assert (
        isinstance(prev_idx, int)
        and isinstance(next_idx, int)
        and prev_idx >= 0
        and next_idx >= 0
    )

    # Build lookup: orig_item_index -> item (from the normalized items list).
    prev_lookup: dict[int, Block | Table] = dict(prev_page_items)
    next_lookup: dict[int, Block | Table] = dict(next_page_items)

    prev_item = prev_lookup.get(prev_idx)
    next_item = next_lookup.get(next_idx)
    assert prev_item and next_item

    # Validate that the items match the verdict's continuation_kind.
    kind = verdict.continuation_kind.value
    kind_ok = False

    if kind == "table":
        kind_ok = isinstance(prev_item, Table) and isinstance(next_item, Table)
    elif kind == "text":
        kind_ok = (
            isinstance(prev_item, Block)
            and isinstance(next_item, Block)
            and prev_item.block_type != BlockType.FIGURE
            and next_item.block_type != BlockType.FIGURE
        )
    elif kind == "figure":
        kind_ok = (
            isinstance(prev_item, Block)
            and isinstance(next_item, Block)
            and prev_item.block_type == BlockType.FIGURE
            and next_item.block_type == BlockType.FIGURE
        )

    assert kind_ok, (
        f"Verification verdict continuation_kind does not match resolved items: "
        f"kind={kind} "
        f"prev_item_type={type(prev_item).__name__} "
        f"prev_block_type={getattr(prev_item, 'block_type', None)} "
        f"next_item_type={type(next_item).__name__} "
        f"next_block_type={getattr(next_item, 'block_type', None)}"
    )

    # Apply set_next_table_repeats_header to the raw item so downstream stitching uses
    # the verified value.
    if verdict.set_next_table_repeats_header is not None and isinstance(
        next_item, Table
    ):
        next_item.repeats_header = verdict.set_next_table_repeats_header

    # Create the direct link.
    link_key: ItemKey = (prev_page, prev_idx)
    link_val: ItemKey = (next_page, next_idx)

    link_debug.append(
        {
            "from_page": prev_page,
            "to_page": next_page,
            "prev_item_orig_index": prev_idx,
            "next_item_orig_index": next_idx,
            "score": verdict.confidence,
            "note": "verdict_override",
            "verdict_continuation_kind": verdict.continuation_kind.value,
        }
    )
    pair_debug["chosen_links"].append(
        {
            "prev_item_orig_index": prev_idx,
            "next_item_orig_index": next_idx,
            "score": verdict.confidence,
        }
    )
    pair_debug["note"] = "verdict_accepted"
    page_pair_debug.append(pair_debug)

    logger.info(
        f"Verdict override: linked ({prev_page}, {prev_idx})->({next_page}, {next_idx}) "
        f"kind={kind} confidence={verdict.confidence}"
    )

    return {link_key: link_val}


def _caption_anchor(item: Block) -> str:
    """Get the caption anchor.

    Parameters
    ----------
    item
        The item to get the caption anchor for.


    Returns
    -------
    str
        The caption anchor.
    """

    # Strongest anchor: local_code (already canonicalized upstream).
    if item.local_code and item.local_code.strip():
        return normalize_local_code(item.local_code) or ""

    # Fallback: parse prefix like "Table 4"/"Figure 2" from caption text.
    text_or_none = item.text
    text = (
        (text_or_none.text or "").strip() if isinstance(text_or_none, TextUnit) else ""
    )
    code = extract_table_or_figure_local_code(text)

    if not code:
        return ""

    return normalize_local_code(code) or ""


def _column_signature(*, mode: str, table: Table) -> str:
    """Compute a deterministic, semantic-light columns signature from a PageIR Table.

    Parameters
    ----------
    mode
      - "strong": uses header_row_count rows (fallback to 1 row if missing/0)
      - "weak": uses only the first row (more tolerant if header_row_count is wrong)
    table
        The PageIR Table.

    Returns
    -------
    str
        The columns signature.
    """

    if not table.rows:
        return ""

    assert mode in (
        "strong",
        "weak",
    ), f"Invalid mode: {mode}. Valid modes are 'strong' or 'weak'."

    hrc = int(table.header_row_count or 0)
    n = (hrc if hrc > 0 else 1) if mode == "strong" else 1
    header_rows = table.rows[:n]

    # Canonicalize: use the same normalization as _row_signature().
    canonical_rows = [list(row_signature(r)) for r in header_rows]

    # Join rows with "||" and cells with "|".
    return "||".join("|".join(row) for row in canonical_rows)


def _edge_window_indices(
    *, from_end: bool, items: list[tuple[int, Block | Table]], max_window_size: int
) -> set[int]:
    """Get the indices of up to k stitch-relevant items from the start or end of the
    items list.

    Parameters
    ----------
    from_end
        If True, get from the end; else from the start.
    items
        The list of (orig_index, item) tuples.
    max_window_size
        The maximum number of non-artifact items to pick.

    Returns
    -------
    set[int]
        The set of picked indices.
    """

    if max_window_size <= 0:
        return set(range(len(items)))

    picked: list[int] = []
    tables = [item for _, item in items if isinstance(item, Table)]
    it = range(len(items) - 1, -1, -1) if from_end else range(len(items))

    for index in it:
        _, item = items[index]

        # Skip artifacts AND ignorable COMPLETE headings/captions/footnotes. Otherwise
        # the edge window can get "consumed" by these items and miss the real
        # truncated/resumed continuation content just above/below them. Also, don't let
        # embedded overlay figures consume the edge window.
        if _safe_to_ignore_between_pages(item) or _is_embedded_overlay_figure(
            item=item, tables=tables
        ):
            continue

        picked.append(index)

        if len(picked) >= max_window_size:
            break

    return set(picked)


def _find_paired_candidates(
    *,
    next_items: list[tuple[int, Block | Table]],
    prev_items: list[tuple[int, Block | Table]],
) -> tuple[list[int], list[int], list[int], list[int]]:
    """Paired candidate discovery across a page boundary.

    Rules are:

    1. Previous candidates must have boundary in {TRUNCATED, BOTH}.
    2. Next candidates must have boundary in {RESUMED, BOTH}.
    3. A previous candidate is valid iff (same idea for next candidates with prior
        items):
         - It has at least one stitch-compatible partner on the next page, AND
         - Everything after it on the prev page is ignorable.

    Parameters
    ----------
    next_items
        The next page's normalized items list.
    prev_items
        The previous page's normalized items list.

    Returns
    -------
    tuple[list[int], list[int], list[int], list[int]]
        A tuple containing:
            - A list of indices of rejected previous-page candidates.
            - A list of indices of valid previous-page candidates.
            - A list of indices of rejected next-page candidates.
            - A list of indices of valid next-page candidates.
    """

    # Only consider boundary-marked candidates near the page edges. This reduces risk
    # of stitching an item in the middle of a page when real content follows/precedes.
    prev_edge = _edge_window_indices(from_end=True, items=prev_items, max_window_size=5)
    next_edge = _edge_window_indices(
        from_end=False, items=next_items, max_window_size=5
    )

    prev_signal_all = [
        i
        for i, (_, item) in enumerate(prev_items)
        if item.boundary in (ItemBoundary.TRUNCATED, ItemBoundary.BOTH)
    ]
    next_signal_all = [
        i
        for i, (_, item) in enumerate(next_items)
        if item.boundary in (ItemBoundary.RESUMED, ItemBoundary.BOTH)
    ]

    # Only evaluate edge window candidates; everything else is treated as rejected so
    # that we can still see warnings/debug output.
    prev_signal = [i for i in prev_signal_all if i in prev_edge]
    next_signal = [i for i in next_signal_all if i in next_edge]

    prev_valid, prev_rejected = [], [i for i in prev_signal_all if i not in prev_edge]

    for i in prev_signal:
        prev_item = prev_items[i][1]
        has_next_partner = any(
            compatible_kinds_for_stitch(prev_item=prev_item, next_item=next_items[j][1])
            for j in next_signal
        )

        if not has_next_partner:
            prev_rejected.append(i)

            continue

        # Anything after it must be ignorable (artifacts or COMPLETE blocks).
        if all(
            _safe_to_ignore_between_pages_relative(anchor=prev_item, item=later)
            for _, later in prev_items[i + 1 :]
        ):
            prev_valid.append(i)
        else:
            prev_rejected.append(i)

    next_valid, next_rejected = [], [i for i in next_signal_all if i not in next_edge]

    for i in next_signal:
        next_item = next_items[i][1]
        has_prev_partner = any(
            compatible_kinds_for_stitch(prev_item=prev_items[j][1], next_item=next_item)
            for j in prev_signal
        )

        if not has_prev_partner:
            next_rejected.append(i)

            continue

        # Anything before it must be ignorable (artifacts or COMPLETE blocks).
        if all(
            _safe_to_ignore_between_pages_relative(anchor=next_item, item=prior)
            for _, prior in next_items[:i]
        ):
            next_valid.append(i)
        else:
            next_rejected.append(i)

    return prev_rejected, prev_valid, next_rejected, next_valid


def _is_embedded_overlay_figure(
    *, item: Block | Table, tables: list[Table], tol: float = 2.0
) -> bool:
    """Check if a figure Block is an embedded overlay within any Table's bounding box.

    Parameters
    ----------
    item
        The item to check.
    tables
        The list of PageIR Tables on the same page.
    tol
        The tolerance for bounding box containment.

    Returns
    -------
    bool
        True if the item is an embedded overlay figure within any table.
    """

    if (
        not isinstance(item, Block)
        or item.block_type != BlockType.FIGURE
        or item.boundary != ItemBoundary.COMPLETE
    ):
        return False

    for t in tables:
        if bbox_contains(inner=item.bbox, outer=t.bbox, tol=tol):
            return True

    return False


def _is_vertical_continuation(
    *,
    prev_bbox: list[float],
    next_bbox: list[float],
    prev_page_h: int,
    next_page_h: int,
    edge_frac: float,
) -> bool:
    """Check if items are visually contiguous across a page break.

    Parameters
    ----------
    prev_bbox
        The previous item's bounding box [x0, y0, x1, y1].
    next_bbox
        The next item's bounding box [x0, y0, x1, y1].
    prev_page_h
        The previous page height in pixels.
    next_page_h
        The next page height in pixels.
    edge_frac
        The edge fraction threshold.

    Returns
    -------
    bool
        True if the items are visually contiguous across the page break.
    """

    prev_near_bottom = prev_bbox[3] >= (prev_page_h * (1.0 - edge_frac))
    next_near_top = next_bbox[1] <= (next_page_h * edge_frac)

    return prev_near_bottom and next_near_top


def _safe_to_ignore_between_pages(item: Block | Table) -> bool:
    """Return True if this item is safe to ignore as 'between' content when determining
    whether an edge continuation item should be considered a candidate.

    Rules are:

    1. Artifacts are always ignorable.
    2. Blocks are ignorable if they are COMPLETE (not themselves continuing).
    3. Tables are NOT ignorable.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is safe to ignore.
    """

    if is_artifact(item):
        return True

    if isinstance(item, Block) and item.boundary == ItemBoundary.COMPLETE:
        return item.block_type in {
            BlockType.CAPTION,
            BlockType.FOOTNOTE,
            BlockType.HEADING,
        }

    return False


def _safe_to_ignore_between_pages_relative(
    *, anchor: Block | Table, item: Block | Table
) -> bool:
    """Similar to _safe_to_ignore_between_pages(), but allows certain items that are
    geometrically contained inside the anchor (e.g., overlay figures inside a table).

    Parameters
    ----------
    anchor
        The anchor item.
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is safe to ignore.
    """

    if _safe_to_ignore_between_pages(item):
        return True

    # Allow complete FIGURE overlays *inside* a candidate TABLE
    if (
        isinstance(anchor, Table)
        and isinstance(item, Block)
        and item.boundary == ItemBoundary.COMPLETE
        and item.block_type == BlockType.FIGURE
        and bbox_contains(outer=anchor.bbox, inner=item.bbox)
    ):
        return True

    return False


def _score_block_match(
    *, next_item: Block, next_page_h: int, prev_item: Block, prev_page_h: int
) -> float:
    """Calculate match score specifically for Block <-> Block pairs.

    Parameters
    ----------
    next_item
        The next block.
    next_page_h
        The next page height in pixels.
    prev_item
        The previous block.
    prev_page_h
        The previous page height in pixels.

    Returns
    -------
    float
        The match score.
    """

    score = 0.0
    textlike = {BlockType.FOOTNOTE, BlockType.LIST, BlockType.PARAGRAPH}

    if prev_item.block_type == next_item.block_type:
        score += 2
    elif prev_item.block_type in textlike and next_item.block_type in textlike:
        # Allow continuation where extractor flips paragraph <-> list across pages.
        score += 2

    # Boundary-alignment bonus: If the verified PageIR marks continuation across the
    # page break, give a small boost so we don't over-rely on strict geometry.
    if (
        prev_item.boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}
        and next_item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
        and prev_item.block_type in textlike
        and next_item.block_type in textlike
    ):
        score += 1

    # Geometric evidence.
    if _is_vertical_continuation(
        edge_frac=(
            0.20
            if (
                prev_item.boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}
                and next_item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
            )
            else 0.17
        ),
        next_bbox=next_item.bbox,
        next_page_h=next_page_h,
        prev_bbox=prev_item.bbox,
        prev_page_h=prev_page_h,
    ):
        score += 1

    # Caption <-> Caption special handling.
    if (
        prev_item.block_type == BlockType.CAPTION
        and next_item.block_type == BlockType.CAPTION
    ):
        prev_anchor = _caption_anchor(prev_item)
        next_anchor = _caption_anchor(next_item)

        if prev_anchor and next_anchor and prev_anchor == next_anchor:
            score += 4

        # Caption matches return early.
        return score

    # Generic local code match.
    if (
        prev_item.local_code
        and next_item.local_code
        and normalize_local_code(prev_item.local_code)
        == normalize_local_code(next_item.local_code)
    ):
        score += 1

    return score


def _score_table_match(
    *, next_item: Table, next_page_h: int, prev_item: Table, prev_page_h: int
) -> float:
    """Calculate match score specifically for Table <-> Table pairs.

    Parameters
    ----------
    next_item
        The next table.
    next_page_h
        The next page height in pixels.
    prev_item
        The previous table.
    prev_page_h
        The previous page height in pixels.

    Returns
    -------
    float
        The match score.
    """

    score = 0.0

    # Strong textual/schema signals.
    if (
        prev_item.local_code
        and next_item.local_code
        and normalize_local_code(prev_item.local_code)
        == normalize_local_code(next_item.local_code)
    ):
        score += 5

    # Column signature match (only when local_code is missing). This helps in cases
    # where PDFs omit table numbering but reuse the same header.
    if not normalize_local_code(prev_item.local_code) and not normalize_local_code(
        next_item.local_code
    ):
        prev_sig_strong = _column_signature(mode="strong", table=prev_item)
        next_sig_strong = _column_signature(mode="strong", table=next_item)

        if prev_sig_strong and next_sig_strong and prev_sig_strong == next_sig_strong:
            score += 2
        else:
            # Fallback if header_row_count is wrong/noisy.
            prev_sig_weak = _column_signature(mode="weak", table=prev_item)
            next_sig_weak = _column_signature(mode="weak", table=next_item)

            if prev_sig_weak and next_sig_weak and prev_sig_weak == next_sig_weak:
                score += 1

    if encode_table(prev_item) == encode_table(next_item):
        score += 4

    if prev_item.header_row_count == next_item.header_row_count:
        score += 1

    # Geometric evidence.
    if _is_vertical_continuation(
        edge_frac=(
            0.25
            if prev_item.boundary
            in {
                ItemBoundary.TRUNCATED,
                ItemBoundary.BOTH,
            }
            and next_item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
            else 0.20
        ),
        next_bbox=next_item.bbox,
        next_page_h=next_page_h,
        prev_bbox=prev_item.bbox,
        prev_page_h=prev_page_h,
    ):
        score += 1

    # Structural similarity (column count).
    prev_cols = prev_item.n_cols or max(
        (len(row.cells) for row in prev_item.rows), default=0
    )
    next_cols = next_item.n_cols or max(
        (len(row.cells) for row in next_item.rows), default=0
    )
    score += int(prev_cols > 0 and prev_cols == next_cols)

    # Boundary-alignment bonus: Helps when headers/local_code are missing but the
    # verified PageIR says this continues.
    if (
        prev_item.boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}
        and next_item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
        and prev_cols == next_cols
    ):
        score += 0.5

    # Bbox width similarity.
    prev_w = max(0.0, prev_item.bbox[2] - prev_item.bbox[0])
    next_w = max(0.0, next_item.bbox[2] - next_item.bbox[0])

    if prev_w > 0 and next_w > 0 and min(prev_w, next_w) / max(prev_w, next_w) >= 0.90:
        score += 0.5

    return score


def bbox_contains(*, inner: list[float], outer: list[float], tol: float = 2.0) -> bool:
    """Return True if `inner` bbox is fully contained in `outer` bbox (with tolerance).

    Parameters
    ----------
    inner
        The inner bounding box [x0, y0, x1, y1].
    outer
        The outer bounding box [x0, y0, x1, y1].
    tol
        Tolerance in pixels.

    Returns
    -------
    bool
        True if `inner` is contained in `outer`, False otherwise.
    """

    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner

    return (
        ix0 >= ox0 - tol and iy0 >= oy0 - tol and ix1 <= ox1 + tol and iy1 <= oy1 + tol
    )


def compute_page_break_links(
    *,
    items_mapping: dict[int, list[tuple[int, Block | Table]]],
    link_debug: list[dict[str, Any]],
    min_link_score: float,
    page_irs: list[PageIR],
    page_pair_debug: list[dict[str, Any]],
    verdict_confidence_threshold: float,
    verdicts: dict[tuple[int, int], EdgeVerdictRecord],
    warnings: list[str],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Compute a mapping of (page_i, item_index) -> (page_i+1, item_index) links for
    continuations.

    With `verdicts`, high-confidence verdicts take priority over heuristic scoring. If
    a verdict's confidence is at or above `verdict_confidence_threshold`, the verdict's
    decision (stitch or skip) is applied directly. Otherwise the existing boundary-flag
    and scoring heuristics are used.

    Parameters
    ----------
    items_mapping
        Mapping of page_index to list of (item_index, item) tuples after normalization.
    link_debug
        List to append per-link debug information to.
    min_link_score
        Minimum score for a link to be considered valid.
    page_irs
        The list of PageIRs for the document.
    page_pair_debug
        List to append per-page-pair debug information to.
    verdict_confidence_threshold
        Minimum verdict confidence to bypass heuristic scoring.
    verdicts
        Mapping of `(prev_page_index, next_page_index)` to edge verdict records.
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[tuple[int, int], tuple[int, int]]
        Forward links for items that continue across a page break.
    """

    all_page_pair_links: dict[tuple[int, int], tuple[int, int]] = {}

    # Process one pair of pages at a time.
    for i in range(len(page_irs) - 1):
        cur_page_index = page_irs[i].page_index
        next_page_index = page_irs[i + 1].page_index
        logger.info(
            f"Computing page break links for pages {cur_page_index} -> {next_page_index}..."
        )

        page_pair_links = process_page_pair(
            current_page_ir=page_irs[i],
            edge_record=verdicts[(cur_page_index, next_page_index)],
            link_debug=link_debug,
            min_link_score=min_link_score,
            next_page_ir=page_irs[i + 1],
            next_page_items=items_mapping[page_irs[i + 1].page_index],
            page_pair_debug=page_pair_debug,
            prev_page_items=items_mapping[page_irs[i].page_index],
            verdict_confidence_threshold=verdict_confidence_threshold,
            warnings=warnings,
        )
        all_page_pair_links.update(page_pair_links)

    logger.success("Completed computing page break links!")

    return all_page_pair_links


def compute_sha256_hex(*, n_hex: int = 16, s: str) -> str:
    """Compute the SHA-256 hex digest of a string and return the first `n_hex`
    characters.

    Parameters
    ----------
    n_hex
        Number of hex characters to return from the digest.
    s
        The input string to hash.

    Returns
    -------
    str
        The first `n_hex` characters of the SHA-256 hex digest of `s`.
    """

    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n_hex]


def debug_features_for_pair(
    *,
    next_item: Block | Table,
    next_page_h: int,
    prev_item: Block | Table,
    prev_page_h: int,
) -> dict[str, Any]:
    """Return semantic-light debug signals explaining why two items might stitch. This
    is used only for reporting/debugging and should remain deterministic.

    Parameters
    ----------
    next_item
        The next item.
    next_page_h
        The next page height.
    prev_item
        The previous item.
    prev_page_h
        The previous page height.

    Returns
    -------
    dict[str, Any]
        A dictionary of debug features.
    """

    output: dict[str, Any] = {
        "prev_kind": prev_item.kind,
        "next_kind": next_item.kind,
        "edge_proximity": None,
        "same_block_type": False,
        "same_columns_signature": False,
        "same_local_code": False,
        "same_schema": False,
    }

    # local_code signal (works for both blocks and tables if present).
    if prev_item.local_code and next_item.local_code:
        output["same_local_code"] = normalize_local_code(
            prev_item.local_code
        ) == normalize_local_code(next_item.local_code)

    # Schema signal (tables only).
    if isinstance(prev_item, Table) and isinstance(next_item, Table):
        prev_sig = _column_signature(mode="strong", table=prev_item)
        next_sig = _column_signature(mode="strong", table=next_item)
        output["same_columns_signature"] = bool(
            prev_sig and next_sig and prev_sig == next_sig
        )
        output["same_schema"] = encode_table(prev_item) == encode_table(next_item)

    # block_type signal (blocks only).
    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        output["same_block_type"] = prev_item.block_type == next_item.block_type

        # Edge proximity (blocks only; helpful for text continuation).
        if prev_page_h and next_page_h:
            edge_frac = 0.17
            prev_near_bottom = prev_item.bbox[3] >= (prev_page_h * (1.0 - edge_frac))
            next_near_top = next_item.bbox[1] <= (next_page_h * edge_frac)
            output["edge_proximity"] = {
                "prev_near_bottom": prev_near_bottom,
                "next_near_top": next_near_top,
            }

    return output


def encode_table(table: Table) -> str:
    """Create a stable fingerprint for a table's schema using header rows. Used for
    matching table continuations when local_code is missing.

    Parameters
    ----------
    table
        The curriculum table.

    Returns
    -------
    str
        The table schema fingerprint.
    """

    hrc = int(table.header_row_count)
    header_rows = table.rows[:hrc] if hrc > 0 else []

    # Fall back to first row if header_count is 0.
    if not header_rows and table.rows:
        header_rows = [table.rows[0]]

    sig_rows = [",".join(row_signature(hr)) for hr in header_rows]
    n_cols = max(
        (sum(cell.col_span for cell in row.cells) for row in table.rows), default=0
    )
    base = f"hrc={hrc}|ncols={n_cols}|rows={'||'.join(sig_rows)}"

    return compute_sha256_hex(n_hex=24, s=base)


def match_candidates(
    *,
    current_page_ir: PageIR,
    link_debug: list[dict[str, Any]],
    min_link_score: float,
    next_candidate_indices: list[int],
    next_page_ir: PageIR,
    next_page_items: list[tuple[int, Block | Table]],
    pair_debug: dict[str, Any],
    prev_candidate_indices: list[int],
    prev_page_items: list[tuple[int, Block | Table]],
    warnings: list[str],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Sort candidates by proximity and find the best matches.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
    link_debug
        List to append per-link debug info to.
    min_link_score
        Minimum score for a link to be considered valid.
    next_candidate_indices
        A list of indices of valid next-page candidates.
    next_page_ir
        The next PageIR.
    next_page_items
        The next page's normalized items list.
    pair_debug
        Dict to append per-page-pair debug info to.
    prev_candidate_indices
        A list of indices of valid previous-page candidates.
    prev_page_items
        The previous page's normalized items list.
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[tuple[int, int], tuple[int, int]]
        Forward links for items that continue across the page break.
    """

    # Sort bottom of previous page by highest y-coordinate and top of next page by
    # lowest y-coordinate.
    prev_candidate_indices.sort(
        key=lambda index: float(prev_page_items[index][1].bbox[3]),
        reverse=True,
    )
    next_candidate_indices.sort(
        key=lambda index: float(next_page_items[index][1].bbox[1])
    )

    page_pair_links: dict[tuple[int, int], tuple[int, int]] = {}
    used_next_indices: set[int] = set()

    for prev_index in prev_candidate_indices:
        best: tuple[float, int] = (float("-inf"), -1)  # (score, next_index)
        candidate_scores: list[dict[str, Any]] = []
        prev_orig_index, prev_item = prev_page_items[prev_index]

        # Find first compatible next candidate not used.
        for next_index in next_candidate_indices:
            if next_index in used_next_indices:
                continue

            next_item = next_page_items[next_index][1]

            if not compatible_kinds_for_stitch(
                next_item=next_item, prev_item=prev_item
            ):
                continue

            score = match_score(
                next_item=next_item,
                next_page_h=next_page_ir.image_height,
                prev_item=prev_item,
                prev_page_h=current_page_ir.image_height,
            )
            candidate_scores.append(
                {
                    "next_item_orig_index": next_page_items[next_index][0],
                    "features": debug_features_for_pair(
                        next_item=next_item,
                        next_page_h=next_page_ir.image_height,
                        prev_item=prev_item,
                        prev_page_h=current_page_ir.image_height,
                    ),
                    "score": score,
                }
            )

            if score > best[0]:
                best = (score, next_index)

        if best[1] != -1:
            best_score, best_next_index = best

            # Retrieve the original index of the matched next item.
            match_orig_index = next_page_items[best_next_index][0]

            # Enforce minimum confidence threshold: if the match is too weak, do not
            # stitch. This prevents accidental cross-links when multiple candidates
            # exist near the page edges.
            if best_score < min_link_score:
                msg = (
                    f"Rejected weak continuation match across page break "
                    f"{current_page_ir.page_index}->{next_page_ir.page_index}: "
                    f"prev_item_orig_index={prev_orig_index}, next_item_orig_index={match_orig_index}, "
                    f"score={best_score} < min_link_score={min_link_score}"
                )
                logger.warning(msg)
                warnings.append(msg)
                link_debug.append(
                    {
                        "from_page": current_page_ir.page_index,
                        "to_page": next_page_ir.page_index,
                        "prev_item_orig_index": prev_orig_index,
                        "next_item_orig_index": match_orig_index,
                        "score": best_score,
                        "candidate_scores": candidate_scores,
                        "note": "rejected_weak_match",
                    }
                )
                continue

            # Store page pair link: (Page A, Orig Index A) -> (Page B, Orig Index B).
            page_pair_links[(current_page_ir.page_index, prev_orig_index)] = (
                next_page_ir.page_index,
                match_orig_index,
            )
            used_next_indices.add(best_next_index)

            # Debug record for the chosen link (and all candidates considered).
            link_debug.append(
                {
                    "from_page": current_page_ir.page_index,
                    "to_page": next_page_ir.page_index,
                    "prev_item_orig_index": prev_orig_index,
                    "next_item_orig_index": match_orig_index,
                    "score": best_score,
                    "candidate_scores": candidate_scores,
                }
            )
            pair_debug["chosen_links"].append(
                {
                    "prev_item_orig_index": prev_orig_index,
                    "next_item_orig_index": match_orig_index,
                    "score": best_score,
                }
            )
        else:
            # No link found for this prev candidate: also record (useful for debugging).
            link_debug.append(
                {
                    "from_page": current_page_ir.page_index,
                    "to_page": next_page_ir.page_index,
                    "prev_item_orig_index": prev_orig_index,
                    "next_item_orig_index": None,
                    "score": None,
                    "candidate_scores": candidate_scores,
                    "note": "no_compatible_next_candidate",
                }
            )

    return page_pair_links


def match_score(
    *,
    next_item: Block | Table,
    next_page_h: int,
    prev_item: Block | Table,
    prev_page_h: int,
) -> float:
    """Score a potential continuation match (higher is better).

    Parameters
    ----------
    next_item
        The next item.
    next_page_h
        The next page height.
    prev_item
        The previous item.
    prev_page_h
        The previous page height.

    Returns
    -------
    float
        The match score.
    """

    if isinstance(prev_item, Table) and isinstance(next_item, Table):
        return _score_table_match(
            next_item=next_item,
            next_page_h=next_page_h,
            prev_item=prev_item,
            prev_page_h=prev_page_h,
        )

    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        return _score_block_match(
            next_item=next_item,
            next_page_h=next_page_h,
            prev_item=prev_item,
            prev_page_h=prev_page_h,
        )

    return float("-inf")


def process_page_pair(
    *,
    current_page_ir: PageIR,
    edge_record: EdgeVerdictRecord,
    link_debug: list[dict[str, Any]],
    min_link_score: float,
    next_page_ir: PageIR,
    next_page_items: list[tuple[int, Block | Table]],
    page_pair_debug: list[dict[str, Any]],
    prev_page_items: list[tuple[int, Block | Table]],
    verdict_confidence_threshold: float,
    warnings: list[str],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Orchestrate candidate finding, warning logging, and linking for a single pair of
    pages.

    The process is as follows:

    1. If a high-confidence verification verdict exists, apply it directly.
    2. Identify candidates (rejected vs. valid).
    3. Apply page-level boundary state guardrails.
    4. Prepare a page-pair debug record.
    5. Append warnings for unsafe candidates (rejected).
    6. Append warnings for scenarios where no candidates exist.
    7. Compute links between valid candidates.
    8. Append page-pair debug info.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
    edge_record
        Edge verdict record for this page pair. If above the confidence threshold, it
        bypasses heuristic scoring.
    link_debug
        List to append per-link debug info to.
    min_link_score
        Minimum score for a link to be considered valid.
    next_page_ir
        The next PageIR.
    next_page_items
        The next page's normalized items list.
    page_pair_debug
        Optional list to append per-page-pair debug info to.
    prev_page_items
        The previous page's normalized items list.
    verdict_confidence_threshold
        Minimum verdict confidence to bypass heuristic scoring.
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[tuple[int, int], tuple[int, int]]
        Forward links for items that continue across the page break.
    """

    verdict = edge_record.verdict

    # 1.
    if verdict.confidence >= verdict_confidence_threshold:
        if not verdict.is_continuation:
            # High-confidence "no continuation" —> skip this page pair entirely.
            page_pair_debug.append(
                {
                    "from_page": current_page_ir.page_index,
                    "to_page": next_page_ir.page_index,
                    "verdict_override": True,
                    "verdict_confidence": verdict.confidence,
                    "verdict_is_continuation": False,
                    "verdict_continuation_kind": verdict.continuation_kind.value,
                    "chosen_links": [],
                    "note": "verdict_no_continuation",
                }
            )
            logger.info(
                f"Verdict override: no continuation for pages "
                f"{current_page_ir.page_index}->{next_page_ir.page_index} "
                f"(confidence={verdict.confidence})"
            )
            return {}

        # High-confidence "yes continuation" —> try to apply the verdict directly.
        return _apply_verification_verdict(
            current_page_ir=current_page_ir,
            edge_record=edge_record,
            link_debug=link_debug,
            next_page_ir=next_page_ir,
            next_page_items=next_page_items,
            page_pair_debug=page_pair_debug,
            prev_page_items=prev_page_items,
        )

    # 2.
    (
        prev_rejected_indices,
        prev_candidate_indices,
        next_rejected_indices,
        next_candidate_indices,
    ) = _find_paired_candidates(
        prev_items=prev_page_items,
        next_items=next_page_items,
    )

    # 3.
    prev_candidate_indices, next_candidate_indices, success = (
        _apply_page_boundary_state_guardrails(
            current_page_ir=current_page_ir,
            next_candidate_indices=next_candidate_indices,
            next_page_ir=next_page_ir,
            next_page_items=next_page_items,
            prev_candidate_indices=prev_candidate_indices,
            prev_page_items=prev_page_items,
            warnings=warnings,
        )
    )

    if not success:
        return {}

    # 4.
    pair_debug: dict[str, Any] = {
        "from_page": current_page_ir.page_index,
        "to_page": next_page_ir.page_index,
        "prev_candidate_item_indices": [
            prev_page_items[i][0] for i in prev_candidate_indices
        ],
        "next_candidate_item_indices": [
            next_page_items[i][0] for i in next_candidate_indices
        ],
        "prev_rejected_item_indices": [
            prev_page_items[i][0] for i in prev_rejected_indices
        ],
        "next_rejected_item_indices": [
            next_page_items[i][0] for i in next_rejected_indices
        ],
        "prev_candidates": [
            summarize_item_for_debug(
                item=prev_page_items[i][1],
                orig_item_index=prev_page_items[i][0],
                page_index=current_page_ir.page_index,
            )
            for i in prev_candidate_indices
        ],
        "next_candidates": [
            summarize_item_for_debug(
                item=next_page_items[i][1],
                orig_item_index=next_page_items[i][0],
                page_index=next_page_ir.page_index,
            )
            for i in next_candidate_indices
        ],
        "prev_rejected": [
            summarize_item_for_debug(
                item=prev_page_items[i][1],
                orig_item_index=prev_page_items[i][0],
                page_index=current_page_ir.page_index,
            )
            for i in prev_rejected_indices
        ],
        "next_rejected": [
            summarize_item_for_debug(
                item=next_page_items[i][1],
                orig_item_index=next_page_items[i][0],
                page_index=next_page_ir.page_index,
            )
            for i in next_rejected_indices
        ],
        "chosen_links": [],
    }

    # 5.
    _append_rejected_warnings(
        is_prev=True,
        items=prev_page_items,
        page_ir=current_page_ir,
        rejected_indices=prev_rejected_indices,
        warnings=warnings,
    )
    _append_rejected_warnings(
        is_prev=False,
        items=next_page_items,
        page_ir=next_page_ir,
        rejected_indices=next_rejected_indices,
        warnings=warnings,
    )

    # 6.
    if not prev_candidate_indices or not next_candidate_indices:
        # Only emit "unmatched" warnings if the missing side has *no* continuation
        # signals at all (neither valid candidates nor rejected boundary-marked items).
        # If the missing side has rejected indices, we already logged the true reason
        # via _append_rejected_warnings(), so an additional "unmatched" warning is
        # redundant and confusing.
        should_emit_unmatched = (
            prev_candidate_indices
            and not next_candidate_indices
            and not next_rejected_indices
        ) or (
            next_candidate_indices
            and not prev_candidate_indices
            and not prev_rejected_indices
        )

        if should_emit_unmatched:
            _append_unmatched_warnings(
                current_page_ir=current_page_ir,
                next_candidate_indices=next_candidate_indices,
                next_items=next_page_items,
                next_page_ir=next_page_ir,
                prev_candidate_indices=prev_candidate_indices,
                prev_items=prev_page_items,
                warnings=warnings,
            )
        else:
            # Emit one concise summary line instead (much clearer than "unmatched").
            msg = (
                f"No links created for page break {current_page_ir.page_index}->{next_page_ir.page_index} "
                f"(candidates missing after safety checks): "
                f"prev_candidates={len(prev_candidate_indices)} prev_rejected={len(prev_rejected_indices)} "
                f"next_candidates={len(next_candidate_indices)} next_rejected={len(next_rejected_indices)}."
            )
            logger.warning(f"{prev_candidate_indices = }")
            logger.warning(f"{next_candidate_indices = }")
            logger.warning(f"{prev_rejected_indices = }")
            logger.warning(f"{next_rejected_indices = }")
            logger.warning(msg)
            warnings.append(msg)

        page_pair_debug.append(pair_debug)
        return {}

    # 7.
    links = match_candidates(
        current_page_ir=current_page_ir,
        link_debug=link_debug,
        min_link_score=min_link_score,
        next_candidate_indices=next_candidate_indices,
        next_page_ir=next_page_ir,
        next_page_items=next_page_items,
        pair_debug=pair_debug,
        prev_candidate_indices=prev_candidate_indices,
        prev_page_items=prev_page_items,
        warnings=warnings,
    )

    # 8.
    page_pair_debug.append(pair_debug)

    return links


def summarize_item_for_debug(
    *, item: Block | Table, orig_item_index: int, page_index: int
) -> dict[str, Any]:
    """Return a JSON-safe summary of an item for debug/reporting purposes.

    Parameters
    ----------

    item
        The Block or Table item.
    orig_item_index
        The original item index within PageIR.items.
    page_index
        The 0-based page index.

    Returns
    -------
    dict[str, Any]
        The item summary.
    """

    output: dict[str, Any] = {
        "page_index": page_index,
        "item_index": orig_item_index,
        "item_addr": f"p{page_index}:raw{orig_item_index}",
        "kind": item.kind,
        "boundary": item.boundary.value,
        "local_code": item.local_code,
        "bbox": item.bbox,
    }

    if isinstance(item, Block):
        text_or_none = item.text
        text = (
            (text_or_none.text or "").strip()
            if isinstance(text_or_none, TextUnit)
            else ""
        )
        output["block_type"] = item.block_type.value
        output["text_snippet"] = text[:200]
    else:
        output["n_rows"] = int(len(item.rows))
        output["n_cols"] = None if item.n_cols is None else int(item.n_cols)
        output["repeats_header"] = item.repeats_header
        output["header_row_count"] = int(item.header_row_count)

    return output
