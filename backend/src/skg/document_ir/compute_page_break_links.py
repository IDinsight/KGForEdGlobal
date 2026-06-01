"""This module contains utility functions for computing page break links for the
document IR.
"""

# Standard Library
from typing import Any, Literal

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.utils import (
    compatible_kinds_for_stitch,
    extract_table_or_figure_local_code,
    normalize_local_code,
    row_signature,
)
from skg.page_ir_extraction.schemas import Block, ListItem, PageIR, Table, TextUnit
from skg.page_ir_verification.utils import EdgeVerdictRecord, is_artifact
from skg.utils.constants import BlockType, ItemBoundary, PageBoundaryState

ItemKey = tuple[int, int]
RejectionReason = Literal[
    "blocked_by_intervening_content", "no_compatible_partner", "outside_edge_window"
]


def _append_rejected_warnings(
    *,
    is_prev: bool,
    items: list[tuple[int, Block | Table]],
    page_ir: PageIR,
    rejected_indices: list[int],
    rejection_reasons: dict[int, RejectionReason],
    warnings: list[str],
) -> None:
    """Append warnings for rejected candidates using precise rejection reasons.

    Parameters
    ----------
    is_prev
        If True, log previous-page candidates; else next-page candidates.
    items
        The page's normalized items list.
    page_ir
        The PageIR.
    rejected_indices
        A list of indices of rejected candidates.
    rejection_reasons
        Mapping from rejected candidate index to the reason it was rejected.
    warnings
        A list to append warning messages to.
    """

    if not rejected_indices:
        return

    page_label = "previous" if is_prev else "next"

    for rejected_index in rejected_indices:
        orig_index, item = items[rejected_index]
        rejection_reason = rejection_reasons.get(
            rejected_index, "blocked_by_intervening_content"
        )

        if rejection_reason == "outside_edge_window":
            msg = (
                f"Skipped stitching candidate on {page_label} page because it falls "
                f"outside the configured edge window: "
                f"page={page_ir.page_index} "
                f"item_index={orig_index} "
                f"kind={item.kind} "
                f"boundary={item.boundary.value}"
            )
        elif rejection_reason == "no_compatible_partner":
            msg = (
                f"Skipped stitching candidate on {page_label} page because no "
                f"stitch-compatible partner was found on the adjacent page: "
                f"page={page_ir.page_index} "
                f"item_index={orig_index} "
                f"kind={item.kind} "
                f"boundary={item.boundary.value}"
            )
        else:
            relation = "followed" if is_prev else "preceded"
            msg = (
                f"Skipped stitching candidate on {page_label} page because it is "
                f"{relation} by non-artifact content (would reorder content): "
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

    After candidate discovery, process_page_pair() asks whether the pages themselves
    claim continuity. It computes:
        - allowed_forward: current page boundary state must be CONTINUES_TO_NEXT or BOTH
        - allowed_backward: next page boundary state must be CONTINUES_FROM_PREV or BOTH
        - If both are true, the candidates pass unchanged.

    If not, there is one rescue path:
        - Table stitching can still proceed if there is a strong shared normalized
            local_code on both sides.
        - In that case, it filters candidates down to the tables sharing that common
            code.

    Otherwise it blocks stitching across that page break entirely and returns
    success=False.

    Example:

    Suppose page 10 says boundary_state="standalone" and page 11 also says
    boundary_state="standalone". Normally that blocks all linking. But if page 10 has a
    truncated table with local_code="Tableau 4" and page 11 has a resumed table with
    local_code="Table 4" after normalization/canonical comparison, the shared
    normalized code rescues table stitching even though page-level boundary states are
    not supportive. The rescue is intentionally restricted to tables. Caption blocks
    are not rescued this way.

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
    #
    # NB: This exception is intentionally restricted to Tables. Block continuations
    # (e.g., two caption slices for "Table 4") are not rescued here because a matching
    # caption local_code is weaker evidence than a matching table local_code--captions
    # are short, frequently duplicated, and less likely to represent true cross-page
    # continuations when neither page claims boundary continuity.
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

    NB: This is called only when edge_record.verdict.confidence >= threshold and
    edge_record.verdict.is_continuation is True. It validates that the verdict's item
    indices resolve to compatible items in the normalized item lists, applies
    `set_next_table_repeats_header` when present, and returns a direct link dict.

    NB: The normalized lists are tuples like (orig_index, item). That means
    sorting/filtering may have changed list position, but the code still links using
    the original item indices from the PageIR. This function resolves the verifier’s
    chosen original indices back into the normalized items via dict(prev_page_items)
    and dict(next_page_items).

    This function takes the verifier’s chosen item indices and turns them into a direct
    link as follows:

    1. Builds a verdict-oriented debug record.
    2. Reads prev_item_index and next_item_index from edge_record.
    3. Builds orig_index -> item lookups from the normalized page item lists.
    4. Resolves those indices against the normalized lists.
    5. Validates that the resolved items match the verdict’s continuation kind.
    6. If the verdict includes set_next_table_repeats_header, it mutates the next table
        item in place.
    7. Appends link debug + page-pair debug.
    8. Returns a single-entry link dict {(prev_page, prev_idx): (next_page, next_idx)}.

    Example:

    Suppose the verifier emits:
        - prev_item_index = 8
        - next_item_index = 0
        - continuation_kind = "table"
        - set_next_table_repeats_header = True

    Then _apply_verification_verdict() will:
        - find original item 8 on the previous normalized page
        - find original item 0 on the next normalized page
        - confirm they are both tables
        - set next_item.repeats_header = True
        - return {(prev_page, 8): (next_page, 0)}.

    This is one reason normalization happens first and linking second: the verdict
    patch mutates the same table object that downstream stitching will later consume.

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
    prev_page_index = current_page_ir.page_index
    next_page_index = next_page_ir.page_index

    # Shared debug record for verdict-based decisions.
    pair_debug: dict[str, Any] = {
        "from_page": prev_page_index,
        "to_page": next_page_index,
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
        kind_ok = compatible_kinds_for_stitch(next_item=next_item, prev_item=prev_item)
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

    # Coerce paragraph <-> list block_type mismatch so that downstream segment
    # stitching sees a homogeneous chain. The verifier has already confirmed these
    # items are a true continuation; the extractor simply classified the block_type
    # differently across the page break (a known extraction artifact). We normalize the
    # next item to match the previous item's block_type AND convert the payload to
    # satisfy BlockSlice validators, mirroring the in-place mutation pattern used for
    # repeats_header below.
    text_like_types = {BlockType.PARAGRAPH, BlockType.LIST}

    if (
        kind == "text"
        and isinstance(prev_item, Block)
        and isinstance(next_item, Block)
        and prev_item.block_type != next_item.block_type
        and prev_item.block_type in text_like_types
        and next_item.block_type in text_like_types
    ):
        src_type = next_item.block_type
        tgt_type = prev_item.block_type

        logger.info(
            f"Verdict coercion: normalizing next_item block_type from "
            f"{src_type.value!r} to {tgt_type.value!r} "
            f"for verdict link ({prev_page_index}, {prev_idx})->({next_page_index}, {next_idx})"
        )

        # Convert payload to match the target block_type. BlockSlice validators enforce
        # mutual exclusivity: PARAGRAPH requires text + no list_items, LIST requires
        # list_items + no text.
        if src_type == BlockType.LIST and tgt_type == BlockType.PARAGRAPH:
            # LIST -> PARAGRAPH: join list item texts into a single TextUnit.
            if next_item.list_items and not next_item.text:
                parts = [
                    li.text.text
                    for li in next_item.list_items
                    if li.text and li.text.text
                ]
                lang = (
                    next_item.list_items[0].text.language
                    if next_item.list_items
                    else "und"
                )
                next_item.text = TextUnit(
                    language=lang, text="\n".join(parts), text_en=None
                )
                next_item.list_items = None
        elif src_type == BlockType.PARAGRAPH and tgt_type == BlockType.LIST:
            # PARAGRAPH -> LIST: wrap text into a single ListItem.
            if next_item.text and not next_item.list_items:
                next_item.list_items = [ListItem(marker=None, text=next_item.text)]
                next_item.text = None

        next_item.block_type = tgt_type

    # Apply set_next_table_repeats_header to the raw item so downstream stitching uses
    # the verified value.
    if verdict.set_next_table_repeats_header is not None and isinstance(
        next_item, Table
    ):
        next_item.repeats_header = verdict.set_next_table_repeats_header

    # Create the direct link.
    link_key: ItemKey = (prev_page_index, prev_idx)
    link_val: ItemKey = (next_page_index, next_idx)

    link_debug.append(
        {
            "from_page": prev_page_index,
            "to_page": next_page_index,
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
        f"Verdict override: linked ({prev_page_index}, {prev_idx})->({next_page_index}, {next_idx}) "
        f"kind={kind} confidence={verdict.confidence}"
    )

    return {link_key: link_val}


def _are_items_compatible_for_emitted_link(
    *, next_item: Block | Table, prev_item: Block | Table
) -> bool:
    """Return whether a page-break continuation can be emitted as a final link.

    This is intentionally stricter than broad candidate compatibility. The linker may
    use semantic-light heuristics to notice that two items *could* be related across a
    page break, but any emitted link must also be materializable later by
    `build_stitched_segments()` into one stitched segment.

    Rules:

    1. Tables may link only to tables.
    2. Blocks may link only to blocks with the exact same `block_type`.
    3. Cross-kind links are never allowed.
    4. Broader fallback matches such as `PARAGRAPH <-> LIST` are rejected here, even if
        `compatible_kinds_for_stitch()` treats them as plausible continuation evidence.

    Parameters
    ----------
    next_item
        The candidate continuation item.
    prev_item
        The current item.

    Returns
    -------
    bool
        True if the link is safe to emit into the page-break link graph.
    """

    if not compatible_kinds_for_stitch(next_item=next_item, prev_item=prev_item):
        return False

    if isinstance(prev_item, Table) and isinstance(next_item, Table):
        return True

    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        return prev_item.block_type == next_item.block_type

    return False


def _bbox_contains(*, inner: list[float], outer: list[float], tol: float = 2.0) -> bool:
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


def _block_edge_fraction(*, next_item: Block, prev_item: Block) -> float:
    """Return the edge-fraction threshold used for Block continuation geometry.

    Parameters
    ----------
    next_item
        The next block.
    prev_item
        The previous block.

    Returns
    -------
    float
        The edge-fraction threshold used for block continuation checks.
    """

    boundary_aligned = prev_item.boundary in {
        ItemBoundary.TRUNCATED,
        ItemBoundary.BOTH,
    } and next_item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}

    return 0.20 if boundary_aligned else 0.17


def _build_edge_proximity_debug(
    *,
    edge_frac: float,
    next_bbox: list[float],
    next_page_h: int,
    prev_bbox: list[float],
    prev_page_h: int,
) -> dict[str, bool | float] | None:
    """Build a deterministic edge-proximity debug payload.

    Parameters
    ----------
    edge_frac
        The edge-fraction threshold used for continuity checks.
    next_bbox
        The next item's bounding box [x0, y0, x1, y1].
    next_page_h
        The next page height in pixels.
    prev_bbox
        The previous item's bounding box [x0, y0, x1, y1].
    prev_page_h
        The previous page height in pixels.

    Returns
    -------
    dict[str, bool | float] | None
        A debug payload describing whether each item is close enough to the page edge,
        or None when either page height is unavailable.
    """

    if not prev_page_h or not next_page_h:
        return None

    prev_near_bottom = prev_bbox[3] >= (prev_page_h * (1.0 - edge_frac))
    next_near_top = next_bbox[1] <= (next_page_h * edge_frac)

    return {
        "edge_frac": edge_frac,
        "next_near_top": next_near_top,
        "prev_near_bottom": prev_near_bottom,
    }


def _caption_anchor(item: Block) -> str:
    """For caption blocks, this extracts the strongest comparable anchor.

    First try normalized local_code. If missing, parse a table/figure code from caption
    text. Return empty string if nothing recognizable exists.

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

    # Canonicalize: use the same normalization as row_signature().
    canonical_rows = [list(row_signature(r)) for r in header_rows]

    # Join rows with "||" and cells with "|".
    return "||".join("|".join(row) for row in canonical_rows)


def _debug_features_for_pair(
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
        "same_columns_signature_strong": False,
        "same_columns_signature_weak": False,
        "same_local_code": False,
    }

    # local_code signal (works for both blocks and tables if present).
    if prev_item.local_code and next_item.local_code:
        output["same_local_code"] = normalize_local_code(
            prev_item.local_code
        ) == normalize_local_code(next_item.local_code)

    # Column signature signals (tables only). Mirrors _score_table_match: strong first,
    # weak fallback.
    if isinstance(prev_item, Table) and isinstance(next_item, Table):
        prev_sig_strong = _column_signature(mode="strong", table=prev_item)
        next_sig_strong = _column_signature(mode="strong", table=next_item)
        output["same_columns_signature_strong"] = bool(
            prev_sig_strong and next_sig_strong and prev_sig_strong == next_sig_strong
        )
        output["edge_proximity"] = _build_edge_proximity_debug(
            edge_frac=_table_edge_fraction(next_item=next_item, prev_item=prev_item),
            next_bbox=next_item.bbox,
            next_page_h=next_page_h,
            prev_bbox=prev_item.bbox,
            prev_page_h=prev_page_h,
        )

        if not output["same_columns_signature_strong"]:
            prev_sig_weak = _column_signature(mode="weak", table=prev_item)
            next_sig_weak = _column_signature(mode="weak", table=next_item)
            output["same_columns_signature_weak"] = bool(
                prev_sig_weak and next_sig_weak and prev_sig_weak == next_sig_weak
            )

    # block_type signal (blocks only).
    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        output["same_block_type"] = prev_item.block_type == next_item.block_type
        output["edge_proximity"] = _build_edge_proximity_debug(
            edge_frac=_block_edge_fraction(next_item=next_item, prev_item=prev_item),
            next_bbox=next_item.bbox,
            next_page_h=next_page_h,
            prev_bbox=prev_item.bbox,
            prev_page_h=prev_page_h,
        )

    return output


def _edge_window_indices(
    *, from_end: bool, items: list[tuple[int, Block | Table]], max_window_size: int
) -> set[int]:
    """Get the indices of up to k stitch-relevant items from the start or end of the
    items list.

    Example:

    Suppose the previous page ends with:
        - footnote
        - caption
        - truncated table
        - footer artifact

    The edge window will try not to waste slots on the footnote/caption/footer and will
    still pick the truncated table as a stitch-relevant edge item.

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
) -> tuple[
    list[int],
    list[int],
    dict[int, RejectionReason],
    list[int],
    list[int],
    dict[int, RejectionReason],
]:
    """Discover paired candidates across a page boundary with explicit rejection
    reasons.

    Rules are:

    1. Previous candidates must have boundary in {TRUNCATED, BOTH}.
    2. Next candidates must have boundary in {RESUMED, BOTH}.
    3. A previous candidate is valid iff (same idea for next candidates with prior
       items):
        - It has at least one stitch-compatible partner on the next page, AND
        - Everything after it on the previous page is ignorable.

    Parameters
    ----------
    next_items
        The next page's normalized items list.
    prev_items
        The previous page's normalized items list.

    Returns
    -------
    tuple[list[int], list[int], dict[int, RejectionReason], list[int], list[int], dict[int, RejectionReason]]
        A tuple containing:
            - Rejected previous-page candidate indices.
            - Valid previous-page candidate indices.
            - Rejection reasons for previous-page candidates.
            - Rejected next-page candidate indices.
            - Valid next-page candidate indices.
            - Rejection reasons for next-page candidates.
    """

    # Only consider boundary-marked candidates near the page edges. This reduces risk
    # of stitching an item in the middle of a page when real content follows/precedes.
    prev_edge = _edge_window_indices(from_end=True, items=prev_items, max_window_size=5)
    next_edge = _edge_window_indices(
        from_end=False, items=next_items, max_window_size=5
    )

    prev_signal_all = [
        index
        for index, (_, item) in enumerate(prev_items)
        if item.boundary in (ItemBoundary.TRUNCATED, ItemBoundary.BOTH)
    ]
    next_signal_all = [
        index
        for index, (_, item) in enumerate(next_items)
        if item.boundary in (ItemBoundary.RESUMED, ItemBoundary.BOTH)
    ]

    # Only evaluate edge window candidates; everything else is treated as rejected so
    # that we can still see warnings/debug output.
    prev_signal = [index for index in prev_signal_all if index in prev_edge]
    next_signal = [index for index in next_signal_all if index in next_edge]

    prev_valid: list[int] = []
    prev_rejection_reasons: dict[int, RejectionReason] = {
        i: "outside_edge_window" for i in prev_signal_all if i not in prev_edge
    }
    prev_rejected: list[int] = list(prev_rejection_reasons.keys())

    for index in prev_signal:
        prev_item = prev_items[index][1]
        has_next_partner = any(
            _are_items_compatible_for_emitted_link(
                next_item=next_items[next_index][1], prev_item=prev_item
            )
            for next_index in next_signal
        )

        if not has_next_partner:
            prev_rejected.append(index)
            prev_rejection_reasons[index] = "no_compatible_partner"
            continue

        if all(
            _safe_to_ignore_between_pages_relative(anchor=prev_item, item=later_item)
            for _, later_item in prev_items[index + 1 :]
        ):
            prev_valid.append(index)
        else:
            prev_rejected.append(index)
            prev_rejection_reasons[index] = "blocked_by_intervening_content"

    next_valid: list[int] = []
    next_rejection_reasons: dict[int, RejectionReason] = {
        i: "outside_edge_window" for i in next_signal_all if i not in next_edge
    }
    next_rejected: list[int] = list(next_rejection_reasons.keys())

    for index in next_signal:
        next_item = next_items[index][1]
        has_prev_partner = any(
            _are_items_compatible_for_emitted_link(
                next_item=next_item, prev_item=prev_items[prev_index][1]
            )
            for prev_index in prev_signal
        )

        if not has_prev_partner:
            next_rejected.append(index)
            next_rejection_reasons[index] = "no_compatible_partner"
            continue

        if all(
            _safe_to_ignore_between_pages_relative(anchor=next_item, item=prior_item)
            for _, prior_item in next_items[:index]
        ):
            next_valid.append(index)
        else:
            next_rejected.append(index)
            next_rejection_reasons[index] = "blocked_by_intervening_content"

    return (
        prev_rejected,
        prev_valid,
        prev_rejection_reasons,
        next_rejected,
        next_valid,
        next_rejection_reasons,
    )


def _is_embedded_overlay_figure(
    *, item: Block | Table, tables: list[Table], tol: float = 2.0
) -> bool:
    """Check if a figure Block is an embedded overlay within any Table's bounding box.

    This special-case helper checks whether a complete figure block is geometrically
    contained inside any same-page table bbox. If so, it is treated as an embedded
    overlay and does not consume edge-window budget. bbox_contains() does the geometry
    check.

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
        if _bbox_contains(inner=item.bbox, outer=t.bbox, tol=tol):
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
    """Check if items are visually contiguous across a page break. Previous item must
    be close enough to the bottom of its page, and next item close enough to the top of
    its page, according to the chosen edge fraction.

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


def _match_candidates(
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

    If both sides have valid candidates, this function tries to pair them. It does the
    following in order:

    1. Sort previous-page candidates by bottom edge descending
    2. Sort next-page candidates by top edge ascending
    3. For each previous candidate:
        - Scan unused next candidates
        - Keep only stitch-compatible ones
        - Compute a score with match_score()
        - Keep the best-scoring unused next candidate
    4. Reject the best if it is below min_link_score
    5. Otherwise, create the link and mark the next candidate as used

    This is greedy one-to-one matching. Each previous candidate gets at most one next
    candidate and each next candidate can be used only once.

    Example:

    Suppose page N has two valid candidates near the bottom:
        - P1 = paragraph
        - T1 = table

    and page N + 1 has two valid candidates near the top:
        - P2 = paragraph
        - T2 = table

    match_candidates() will score P1 against the compatible next candidates, score T1
    against the remaining compatible next candidates, and output two links if both best
    scores clear min_link_score.

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

        # Evaluate this previous candidate against all next candidates and keep track
        # of the best scoring match.
        for next_index in next_candidate_indices:
            if next_index in used_next_indices:
                continue

            next_item = next_page_items[next_index][1]

            if not _are_items_compatible_for_emitted_link(
                next_item=next_item, prev_item=prev_item
            ):
                continue

            score = _match_score(
                next_item=next_item,
                next_page_h=next_page_ir.image_height,
                prev_item=prev_item,
                prev_page_h=current_page_ir.image_height,
            )
            candidate_scores.append(
                {
                    "next_item_orig_index": next_page_items[next_index][0],
                    "features": _debug_features_for_pair(
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


def _match_score(
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


def _process_page_pair(
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
        prev_rejection_reasons,
        next_rejected_indices,
        next_candidate_indices,
        next_rejection_reasons,
    ) = _find_paired_candidates(next_items=next_page_items, prev_items=prev_page_items)

    # 3.
    prev_candidate_indices, next_candidate_indices, allow_stitching = (
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

    if not allow_stitching:
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
            _summarize_item_for_debug(
                item=prev_page_items[i][1],
                orig_item_index=prev_page_items[i][0],
                page_index=current_page_ir.page_index,
            )
            for i in prev_candidate_indices
        ],
        "next_candidates": [
            _summarize_item_for_debug(
                item=next_page_items[i][1],
                orig_item_index=next_page_items[i][0],
                page_index=next_page_ir.page_index,
            )
            for i in next_candidate_indices
        ],
        "prev_rejected": [
            {
                **_summarize_item_for_debug(
                    item=prev_page_items[i][1],
                    orig_item_index=prev_page_items[i][0],
                    page_index=current_page_ir.page_index,
                ),
                "rejection_reason": prev_rejection_reasons.get(i),
            }
            for i in prev_rejected_indices
        ],
        "next_rejected": [
            {
                **_summarize_item_for_debug(
                    item=next_page_items[i][1],
                    orig_item_index=next_page_items[i][0],
                    page_index=next_page_ir.page_index,
                ),
                "rejection_reason": next_rejection_reasons.get(i),
            }
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
        rejection_reasons=prev_rejection_reasons,
        warnings=warnings,
    )
    _append_rejected_warnings(
        is_prev=False,
        items=next_page_items,
        page_ir=next_page_ir,
        rejected_indices=next_rejected_indices,
        rejection_reasons=next_rejection_reasons,
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
            logger.warning(msg)
            warnings.append(msg)

        page_pair_debug.append(pair_debug)
        return {}

    # 7.
    links = _match_candidates(
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


def _safe_to_ignore_between_pages(item: Block | Table) -> bool:
    """Return True if this item is safe to ignore as 'between' content when determining
    whether an edge continuation item should be considered a candidate.

    Rules are:

    1. Artifacts are always ignorable.
    2. Blocks are ignorable if they are COMPLETE (not themselves continuing).
    3. Tables are NOT ignorable.

    NB: If a truncated paragraph is followed by a complete paragraph before page end,
    the truncated paragraph is NOT a safe continuation candidate, because stitching it
    would reorder actual content.

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

    It inherits the basic ignorable rules and adds one extra allowance: if the anchor
    is a Table, then a complete figure block geometrically inside that table is also
    ignorable.

    Example:

    Imagine page N ends with a truncated table, and near the bottom of the page there
    is also a complete embedded figure sitting inside that table’s bbox. This helper
    lets the table still count as a valid candidate, because that overlaid figure is
    treated as part of the table’s visual footprint rather than intervening content.

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

    # Allow complete FIGURE overlays *inside* a candidate TABLE.
    if (
        isinstance(anchor, Table)
        and isinstance(item, Block)
        and item.boundary == ItemBoundary.COMPLETE
        and item.block_type == BlockType.FIGURE
        and _bbox_contains(outer=anchor.bbox, inner=item.bbox)
    ):
        return True

    return False


def _score_block_match(
    *, next_item: Block, next_page_h: int, prev_item: Block, prev_page_h: int
) -> float:
    """Calculate match score specifically for Block <-> Block pairs.

    Example: paragraph continuation

    Page 6 ends with a truncated paragraph near the bottom.
    Page 7 starts with a resumed list near the top.

    Possible score:
        - Both text-like -> +2
        - Boundary aligned and text-like -> +1
        - Near edge on both sides -> +1
        - No local-code signal

    Total = 4. If min_link_score is below 4, this is a strong match.

    Example: caption continuation

    Page 20 ends with caption text Table 3 (continued...) and page 21 starts with
    caption text Table 3. Even if there is little other evidence, matching caption
    anchors give +4, plus any edge/type evidence already accumulated, and the function
    returns early from the caption branch.

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
        edge_frac=_block_edge_fraction(next_item=next_item, prev_item=prev_item),
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

    This scorer combines table-specific anchors in three tiers:

    1. A shared normalized `local_code` is the strongest textual anchor.
    2. Header/column signatures remain useful even when only one side has a
        `local_code`; this fixes the prior blind spot where a one-sided code suppressed
        header evidence completely.
    3. Geometry and structural similarity provide backstop evidence.

    Example: strongly anchored table continuation

    Page 9 ends with a truncated table whose propagated code is "Tableau 4".
    Page 10 starts with a resumed table whose caption normalization yields "Table 4".

    Possible score:
        - same normalized local_code -> +5
        - matching strong header signature -> +0.5
        - near edge on both sides -> +1
        - same number of columns -> +1
        - boundary aligned and equal cols -> +0.5
        - similar width -> +0.5

    Total = 8.5, which is extremely strong.

    Example: one-sided table code plus same header

    Page 14 ends with a truncated table with a propagated code.
    Page 15 starts with a resumed table whose code is missing, but the headers match.

    Possible score:
        - one-sided local_code availability
        - strong column signature match -> +3
        - near edge on both sides -> +1
        - same number of columns -> +1
        - width similarity -> +0.5

    Total = 5.5 before boundary bonus, which is strong enough to recover the
    previously missed continuation.

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

    prev_local_code = normalize_local_code(prev_item.local_code)
    next_local_code = normalize_local_code(next_item.local_code)

    # Strong textual/schema signals.
    if prev_local_code and next_local_code and prev_local_code == next_local_code:
        score += 5

    # Column-signature evidence remains useful even when only one side has a local
    # code. That situation commonly arises when caption propagation anchored just one
    # table slice. Keep the signal, but weight it below a two-sided local_code match.
    prev_sig_strong = _column_signature(mode="strong", table=prev_item)
    next_sig_strong = _column_signature(mode="strong", table=next_item)
    prev_sig_weak = _column_signature(mode="weak", table=prev_item)
    next_sig_weak = _column_signature(mode="weak", table=next_item)

    has_strong_signature_match = bool(
        prev_sig_strong and next_sig_strong and prev_sig_strong == next_sig_strong
    )
    has_weak_signature_match = bool(
        prev_sig_weak and next_sig_weak and prev_sig_weak == next_sig_weak
    )

    # Count how many local codes are present (results in 0, 1, or 2).
    local_code_count = bool(prev_local_code) + bool(next_local_code)

    if has_strong_signature_match:
        # Index 0: no codes (4.0), Index 1: one code (3.0), Index 2: both codes (0.5).
        score += (4.0, 3.0, 0.5)[local_code_count]
    elif has_weak_signature_match:
        # Index 0: no codes (2.0), Index 1: one code (1.5), Index 2: both codes (0.25).
        score += (2.0, 1.5, 0.25)[local_code_count]

    # NB: header_row_count equality is intentionally NOT scored here. It is already
    # captured by _column_signature (which uses header_row_count to select rows), and a
    # standalone bonus would reward coincidental matches (most tables have hrc=1).

    # Geometric evidence.
    if _is_vertical_continuation(
        edge_frac=_table_edge_fraction(next_item=next_item, prev_item=prev_item),
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


def _summarize_item_for_debug(
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


def _table_edge_fraction(*, next_item: Table, prev_item: Table) -> float:
    """Return the edge-fraction threshold used for Table continuation geometry.

    Parameters
    ----------
    next_item
        The next table.
    prev_item
        The previous table.

    Returns
    -------
    float
        The edge-fraction threshold used for table continuation checks.
    """

    boundary_aligned = prev_item.boundary in {
        ItemBoundary.TRUNCATED,
        ItemBoundary.BOTH,
    } and next_item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}

    return 0.25 if boundary_aligned else 0.20


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
    """Compute a mapping of (page_i, item_index) -> (page_i + 1, item_index) links for
    continuations.

    With `verdicts`, high-confidence verdicts take priority over heuristic scoring. If
    a verdict's confidence is at or above `verdict_confidence_threshold`, the verdict's
    decision (stitch or skip) is applied directly. Otherwise, the existing boundary
    flag and scoring heuristics are used.

    NB: compute_page_break_links() does not operate on raw PageIR.items. It operates on
    items_mapping, which is created immediately before linking by
    normalize_page_items() for each page. That normalization can filter artifacts,
    reorder items by bbox, clear contradictory repeats_header, and propagate
    caption-derived local_code values onto same-page tables/figures. So the linker is
    working on a cleaned, sometimes mutated view of the page.

    After all adjacent page pairs are processed, compute_page_break_links() returns one
    merged dict of forward continuation links across the whole document. That link map
    is then passed to build_stitched_segments() in the next stage of
    stitch_document_ir().

    The overall flow is:
        1. Normalization prepares "trustworthy" page items
        2. Caption propagation strengthens local anchors
        3. Page-pair processing either trusts the verifier or runs heuristics
        4. Candidate discovery is conservative
        5. Page-level guardrails may veto the whole boundary
        6. Matching is one-to-one and score-based
        7. Output is a sparse link graph used to build stitched multi-page segments.

    Example:

    If the verified pages are [0,1,2], this function will process:
        - page 0 -> page 1
        - page 1 -> page 2

    and return something like:

        {
            (0, 12): (1, 0),
            (1, 7): (2, 1),
        }

    meaning “item 12 on page 0 continues as item 0 on page 1,” and so on.

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
        Forward links for items that continue across a page break and are safe for
        stitched-segment materialization.
    """

    all_page_pair_links: dict[tuple[int, int], tuple[int, int]] = {}

    for current_page_ir, next_page_ir in zip(page_irs, page_irs[1:]):
        cur_page_index = current_page_ir.page_index
        next_page_index = next_page_ir.page_index

        logger.info(
            f"Computing page break links for pages {cur_page_index} -> {next_page_index}..."
        )

        edge_record = verdicts.get((cur_page_index, next_page_index))

        if edge_record is None:
            raise ValueError(
                f"Missing edge verdict for adjacent page pair "
                f"{cur_page_index}->{next_page_index}."
            )

        page_pair_links = _process_page_pair(
            current_page_ir=current_page_ir,
            edge_record=edge_record,
            link_debug=link_debug,
            min_link_score=min_link_score,
            next_page_ir=next_page_ir,
            next_page_items=items_mapping[next_page_index],
            page_pair_debug=page_pair_debug,
            prev_page_items=items_mapping[cur_page_index],
            verdict_confidence_threshold=verdict_confidence_threshold,
            warnings=warnings,
        )
        all_page_pair_links.update(page_pair_links)

    logger.success("Completed computing page break links!")

    return all_page_pair_links
