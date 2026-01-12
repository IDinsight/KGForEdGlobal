"""This module contains utility functions for document Intermediate Representations."""

# Standard Library
import re
import uuid

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from skg.document_ir.schemas import (
    BlockSegment,
    BlockSlice,
    DocumentIR,
    SectionHeadingRef,
    Segment,
    SegmentProvenance,
    TableRowProvenance,
    TableSegment,
    TableSlice,
)
from skg.extract_page_ir.schemas import (
    CurriculumBlock,
    CurriculumTable,
    ListItem,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification.utils import is_artifact
from skg.schemas import RunCtx, StitchingConfig
from skg.utils.constants import BlockType, ItemBoundary, PageBoundaryState
from skg.utils.general import (
    bbox_contains,
    compute_sha256_hex,
    make_dir,
    normalize_text,
    write_to_json,
)

ItemKey = tuple[int, int]
ChainItem = tuple[int, int, Block | Table]

# Compiled regexes.
_ALPHA_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_CAPTION_TABLE_RE = re.compile(
    r"^\s*(?:table|jedwali|tableau)\s*(?:no\.?|n\.?|na\.)?\s*(\d+(?:\.\d+)*)\s*(?:[:.\-–—]\s*)?",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")
_LOCAL_CODE_RE = re.compile(r"\s+")
_TABLE_FIGURE_RE = re.compile(
    r"^\s*(table|figure)\s+(\d+(?:\.\d+)*)\s*(?:[:.\-–—]\s*)?", re.IGNORECASE
)


@dataclass(frozen=True)
class DocumentIRDirs:
    """Dataclass for document IR directories."""

    root: Path


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
    m = _TABLE_FIGURE_RE.match(text)

    if not m:
        return ""

    kind = m.group(1).lower()
    num = m.group(2)

    return f"{kind} {num}"


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
    canonical_rows = [list(_row_signature(r)) for r in header_rows]

    # Join rows with "||" and cells with "|".
    return "||".join("|".join(row) for row in canonical_rows)


def _drop_repeated_header(
    *, base_header_rows: list[TableRow], header_row_count: int, next_table: Table
) -> tuple[list[TableRow], int]:
    """Return next_table.rows with repeated header removed if warranted.

    Parameters
    ----------
    base_header_rows
        Header rows from the first slice.
    header_row_count
        Number of header rows.
    next_table
        The next table slice.

    Returns
    -------
    tuple[list[TableRow], int]
        (rows_to_add, num_dropped_header_rows) where num_dropped_header_rows is how
        many rows were removed from the start due to repeated header detection.
    """

    rows_to_add = next_table.rows

    if header_row_count <= 0:
        return rows_to_add, 0

    if next_table.repeats_header is True:
        dropped = min(header_row_count, len(rows_to_add))

        return rows_to_add[dropped:], dropped

    if next_table.repeats_header is False:
        return rows_to_add, 0

    # Unknown: detect by exact header-row match to base.
    maybe_header = rows_to_add[:header_row_count]

    if (
        base_header_rows
        and len(base_header_rows) == len(maybe_header)
        and all(
            _row_signature(ra) == _row_signature(rb)
            for ra, rb in zip(base_header_rows, maybe_header)
        )
    ):
        dropped = min(header_row_count, len(rows_to_add))

        return rows_to_add[dropped:], dropped

    return rows_to_add, 0


def _edge_window_indices(
    *, from_end: bool, items: list[tuple[int, Block | Table]], k: int
) -> set[int]:
    """Get the indices of up to k stitch-relevant items from the start or end of the
    items list.

    Parameters
    ----------
    from_end
        If True, get from the end; else from the start.
    items
        The list of (orig_index, item) tuples.
    k
        The maximum number of non-artifact items to pick.

    Returns
    -------
    set[int]
        The set of picked indices.
    """

    if k <= 0:
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

        if len(picked) >= k:
            break

    return set(picked)


def _extract_table_local_code_from_caption(caption: Block) -> Optional[str]:
    """Extract a canonical table local_code (e.g., 'Table 4') from a caption block.
    This is used to propagate caption codes to the nearest following table on the same
    page when the table itself has local_code=None. We treat common non-English table
    prefixes (e.g., 'Jedwali', 'Tableau') as 'Table' for canonicalization.

    Parameters
    ----------
    caption
        The caption block.

    Returns
    -------
    Optional[str]
        The extracted table local_code, or None if not found.
    """

    if caption.block_type != BlockType.CAPTION:
        return None

    candidates: list[str] = []

    # Sometimes extraction/verification may already populate caption.local_code.
    if isinstance(caption.local_code, str) and caption.local_code.strip():
        candidates.append(caption.local_code.strip())

    # Otherwise parse from caption text.
    if caption.text is not None and isinstance(caption.text.text, str):
        text = caption.text.text.strip()

        if text:
            candidates.append(text)

    for cand in candidates:
        m = _CAPTION_TABLE_RE.match(cand)

        if m:
            num = m.group(1)  # Supports "4" or "1.2"
            return f"Table {num}"

    return None


def _fill_span_area(
    *,
    col_span: int,
    col_start: int,
    grid: list[list[dict[str, Any]]],
    row_span: int,
    row_start: int,
    segment_id: str,
    value: Any,
) -> None:
    """Fill a specific rectangular area of the grid.

    Parameters
    ----------
    col_span
        The column span.
    col_start
        The starting column index.
    grid
        The grid to populate.
    row_span
        The row span.
    row_start
        The starting row index.
    segment_id
        The TableSegment ID (for error messages).
    value
        The TextUnit-like value to fill in the spanned area.

    Raises
    ------
    ValueError
        If overlapping spans are detected.
    """

    for rr in range(row_start, row_start + row_span):
        for cc in range(col_start, col_start + col_span):
            if grid[rr][cc]["source_row"] != -1:
                raise ValueError(
                    f"Overlapping spans detected at (row={rr}, col={cc}) "
                    f"in TableSegment: {segment_id}."
                )

            grid[rr][cc] = {"text": value, "source_row": row_start}


def _finalize_table_structure(
    *,
    chain: list[tuple[int, int, Table]],
    header_rows: list[TableRow],
    local_code: Optional[str],
    segment_id: str,
    stitched_rows: list[TableRow],
    warnings: list[str],
) -> tuple[int, Optional[str], list[list[str]]]:
    """Compute final n_cols and header signatures.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the slices to
        stitch.
    header_rows
        The final list of header rows.
    local_code
        The table's local code.
    segment_id
        The TableSegment ID.
    stitched_rows
        The final list of stitched table rows.
    warnings
        A list to append warning messages to.

    Returns
    -------
    tuple[int, Optional[str], list[list[str]]]
        The final n_cols, columns signature, and canonical header rows.
    """

    columns_signature: Optional[str] = None
    header_rows_canonical: list[list[str]] = []

    if header_rows:
        header_rows_canonical = [list(_row_signature(hr)) for hr in header_rows]
        columns_signature = "||".join("|".join(hrc) for hrc in header_rows_canonical)

    declared_n_cols = max((table.n_cols or 0 for _, _, table in chain), default=0)
    computed_n_cols = max(
        (sum(cell.col_span for cell in row.cells) for row in stitched_rows),
        default=0,
    )
    n_cols = max(declared_n_cols, computed_n_cols)

    if 0 < declared_n_cols < computed_n_cols:
        msg = (
            f"n_cols inflation detected: computed_n_cols={computed_n_cols} > "
            f"declared_n_cols={declared_n_cols}. Using n_cols={n_cols}. "
            f"segment_id={segment_id} local_code={local_code!r}"
        )
        logger.warning(msg)
        warnings.append(msg)

    return n_cols, columns_signature, header_rows_canonical


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
    prev_edge = _edge_window_indices(from_end=True, items=prev_items, k=5)
    next_edge = _edge_window_indices(from_end=False, items=next_items, k=5)

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


def _join_text_unit_texts(
    *, repair_hyphenation: bool = True, text_units: list[TextUnit]
) -> str:
    """Join a list of TextUnit objects into a single combined string.

    If repair_hyphenation=False, then join all chunks using newline separators.

    If repair_hyphenation=True, apply deterministic joining rules:
        1. If previous chunk ends with '-' and next begins with lowercase: remove the
            hyphen and join with no space ("soft-" + "ware" -> "software").
        2. If previous chunk ends with '-' and next begins with non-lowercase: keep the
            hyphen and join with no space ("Non-" + "Profit" -> "Non-Profit").
        3. If previous chunk ends with strong sentence terminator (. ! ? ... …): start
            a new output chunk (paragraph/list boundary) and preserve it via newline.
        4. Otherwise, join with a single space (treat as flowing/wrapped text).

    Parameters
    ----------
    repair_hyphenation
        If True, repair hyphenation at line breaks.
    text_units
        The list of TextUnit objects.

    Returns
    -------
    str
        The combined text.
    """

    texts = [text_unit.text for text_unit in text_units if text_unit.text is not None]

    if not texts:
        return ""

    if len(texts) == 1:
        return texts[0]

    # If we're not repairing hyphenation, then we just join using newlines.
    if not repair_hyphenation:
        return "\n".join(texts)

    # Terminators that force a hard carriage return. We explicitly EXCLUDE commas,
    # colons, and semicolons, as they usually imply continuation of the current
    # thought/sentence.
    sentence_terminators = (".", "!", "?", "...", "…")

    output: list[str] = [texts[0]]

    for next_raw_text in texts[1:]:
        prev_raw_text = output[-1]
        prev_strip = prev_raw_text.rstrip()
        next_strip = (next_raw_text or "").lstrip()

        if not prev_strip:
            output[-1] = next_strip

            continue

        if not next_strip:
            # Keep previous as-is; skip empty continuation chunk.
            output[-1] = prev_strip

            continue

        prev_last = prev_strip[-1]
        next_first = next_strip[0]

        # Hyphenation handling.
        if prev_last == "-":
            # Case A: Standard word break (soft- \n ware) -> "software".
            if next_first.islower():
                output[-1] = prev_strip[:-1] + next_strip

            # Case B: Compound word break (Non- \n Profit) -> "Non-Profit". We keep the
            # hyphen, but strictly DO NOT insert a space.
            else:
                output[-1] = prev_strip + next_strip

            continue

        # Sentence terminators. If the previous line ended with a period, bang, or
        # question mark, we assume the next chunk is a new paragraph or list item.
        if prev_strip.endswith(sentence_terminators):
            output[-1] = prev_strip
            output.append(next_strip)

            continue

        # Default join (flowing text). If no terminator and no hyphen, we assume it's a
        # wrapped line. This covers:
        #   - "comma," + "next"
        #   - "no punct" + "continuation"
        #   - "proper noun" + "John"
        output[-1] = prev_strip + " " + next_strip

    return "\n".join(output)


def _populate_grid_spans(
    *, segment: TableSegment, grid: list[list[dict[str, Any]]], n_rows: int, n_cols: int
) -> None:
    """Handle cell parsing, cursor placement, and span explosion.

    Parameters
    ----------
    grid
        The grid to populate.
    n_cols
        The number of columns in the table.
    n_rows
        The number of rows in the table.
    segment
        The TableSegment to process.

    Raises
    ------
    ValueError
        If spans exceed table bounds or overlap.
    """

    for row_index, row in enumerate(segment.rows):
        cursor = 0

        for cell in row.cells:
            row_span, col_span = cell.row_span, cell.col_span

            # Treat truly empty cells as None, otherwise preserve full TextUnit-like
            # payload.
            raw_text = cell.text.text if isinstance(cell.text, TextUnit) else ""
            value = None if not raw_text.strip() else cell.text

            # Padding cell = extractor emitted an explicit blank cell in a column that
            # may already be occupied by a row-span from a previous row.
            is_padding = value is None and row_span == 1 and col_span == 1

            # If this is padding and the current slot is already occupied, consume
            # exactly one column and move on. This prevents right-shifting that can
            # overflow n_cols.
            if is_padding:
                if cursor < n_cols and grid[row_index][cursor]["source_row"] != -1:
                    cursor += 1
                    continue

                # If we're already past the edge due to earlier padding, ignore
                # trailing padding.
                if cursor >= n_cols:
                    continue

            # Advance cursor to next empty slot (normal behavior for real cells).
            while cursor < n_cols and grid[row_index][cursor]["source_row"] != -1:
                cursor += 1

            # Sanity check: ensure span fits.
            if cursor >= n_cols:
                # If the only thing left is padding, ignore it; otherwise this is a
                # real error.
                if is_padding:
                    continue

                raise ValueError(
                    f"Row {row_index} exceeds declared n_cols={n_cols} "
                    f"in TableSegment '{segment.segment_id}'."
                )

            # Validate spans.
            _validate_span_bounds(
                col_span=col_span,
                cursor=cursor,
                n_cols=n_cols,
                n_rows=n_rows,
                row_index=row_index,
                row_span=row_span,
                segment_id=segment.segment_id,
            )

            # Fill the spanned area.
            _fill_span_area(
                col_span=col_span,
                col_start=cursor,
                grid=grid,
                row_span=row_span,
                row_start=row_index,
                segment_id=segment.segment_id,
                value=value,
            )

            cursor += col_span


def _process_next_table_slice(
    *,
    current_local_code: Optional[str],
    next_item: Table,
    next_item_index: int,
    next_page_index: int,
    segment_header_row_count: int,
    segment_header_rows: list[TableRow],
    segment_id: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Process a subsequent table slice: resolve headers, code, and rows to append.

    The process is as follows:

    1. Resolve local code for display but compare using normalized form.
    2. Determine rows to drop/add.
    3. Create provenance.

    Parameters
    ----------
    current_local_code
        The current local code for the segment.
    next_item
        The next Table slice to process.
    next_item_index
        The item index of the next Table slice.
    next_page_index
        The page index of the next Table slice.
    segment_header_row_count
        The segment-level header row count.
    segment_header_rows
        The segment-level header rows.
    segment_id
        The TableSegment ID.
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[str, Any]
        A dictionary with keys:
    """

    # 1.
    next_local_code = canonicalize_local_code(next_item.local_code)

    if next_local_code and current_local_code:
        if normalize_local_code(next_local_code) != normalize_local_code(
            current_local_code
        ):
            msg = (
                f"Conflicting local_code in table chain {segment_id}: "
                f"{current_local_code!r} vs. {next_local_code!r} "
                f"(page={next_page_index}, item_index={next_item_index}). "
                f"Keeping {current_local_code!r}."
            )
            logger.warning(msg)
            warnings.append(msg)

    # Carry forward the segment code; only adopt the next code if missing.
    slice_local_code = current_local_code or next_local_code

    # 2.
    next_hrc = int(next_item.header_row_count or 0)

    if next_item.repeats_header is True:
        # Explicit drop.
        drop_count = next_hrc if next_hrc > 0 else segment_header_row_count

        if drop_count <= 0:
            msg = (
                f"Table continuation marked repeats_header=True but header_row_count is 0. "
                f"segment_id={segment_id}, page={next_page_index}."
            )
            logger.warning(msg)
            warnings.append(msg)

        dropped_header_rows = min(drop_count, len(next_item.rows))
        rows_to_add = next_item.rows[dropped_header_rows:]
    else:
        # Implicit match.
        match_k = segment_header_row_count
        if 0 < next_hrc < segment_header_row_count:
            # If next slice has *fewer* headers declared than the segment, use the
            # smaller number.
            match_k = next_hrc
        elif next_hrc > 0 and next_hrc != segment_header_row_count:
            msg = (
                f"header_row_count mismatch: seg={segment_header_row_count} vs next={next_hrc}. "
                f"Using match_k={match_k}."
            )
            logger.warning(msg)
            warnings.append(msg)
            match_k = min(segment_header_row_count, next_hrc)

        rows_to_add, dropped_header_rows = _drop_repeated_header(
            base_header_rows=segment_header_rows[:match_k],
            header_row_count=match_k,
            next_table=next_item,
        )

    # 3.
    new_provenance = SegmentProvenance(
        bbox=next_item.bbox,
        boundary=next_item.boundary,
        item_addr=create_item_addr(
            item_index=next_item_index, page_index=next_page_index
        ),
        item_index=next_item_index,
        kind=next_item.kind,
        local_code=slice_local_code,
        page_index=next_page_index,
        repeats_header=next_item.repeats_header,
    )
    new_slice = TableSlice(
        bbox=next_item.bbox,
        boundary=next_item.boundary,
        dropped_header_rows=dropped_header_rows,
        header_row_count=next_hrc,
        item_index=next_item_index,
        local_code=slice_local_code,
        page_index=next_page_index,
        repeats_header=next_item.repeats_header,
        rows=next_item.rows,
    )

    return {
        "slice": new_slice,
        "provenance": new_provenance,
        "rows_to_add": rows_to_add,
        "local_code": slice_local_code,
    }


def _resolve_header_row_count(
    *, first_item: Table, item_index: int, page_index: int, warnings: list[str]
) -> int:
    """Determine header row count, using inference if extractor provided none.

    Parameters
    ----------
    first_item
        The first Table item in the chain.
    item_index
        The item index of the first Table item in the chain.
    page_index
        The page index of the first Table item in the chain.
    warnings
        A list to append warning messages to.

    Returns
    -------
    int
        The resolved header row count.
    """

    header_row_count = first_item.header_row_count

    if header_row_count <= 0:
        inferred_hrc, confidence = infer_header_row_count_from_rows(
            max_header_rows=3, rows=first_item.rows
        )
        if inferred_hrc > 0 and confidence >= 0.65:
            msg = (
                f"Inferred header_row_count={inferred_hrc} (confidence={confidence:.2f}) "
                f"for table chain starting at (page={page_index}, item_index={item_index})."
            )
            logger.warning(msg)
            warnings.append(msg)
            return inferred_hrc

    return header_row_count


def _resolve_initial_local_code(chain: list[tuple[int, int, Table]]) -> Optional[str]:
    """Return the first non-null local code found in the chain.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the slices to
        stitch.

    Returns
    -------
    Optional[str]
        The resolved local code, or None if all slices lack it.
    """

    _, _, first_item = chain[0]
    first_code = canonicalize_local_code(first_item.local_code)

    return first_code or next(
        (
            c
            for *_, item in chain[1:]
            if (c := canonicalize_local_code(item.local_code))
        ),
        None,
    )


def _row_signature(row: TableRow) -> tuple[str, ...]:
    """Create a stable signature for a table row based on normalized cell texts.

    Parameters
    ----------
    row
        The table row.

    Returns
    -------
    tuple[str, ...]
        The row signature.
    """

    row_sig: list[str] = []

    for cell in getattr(row, "cells", None) or []:
        text_or_none = cell.text
        text = (
            normalize_text(text_or_none.text)
            if isinstance(text_or_none, TextUnit)
            else ""
        )
        row_sig.append(text)

    return tuple(row_sig)


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


def _validate_span_bounds(
    *,
    col_span: int,
    cursor: int,
    n_cols: int,
    n_rows: int,
    row_index: int,
    row_span: int,
    segment_id: str,
) -> None:
    """Validate that row and column spans fit within table limits.

    Parameters
    ----------
    col_span
        The column span.
    cursor
        The current column cursor.
    n_cols
        The number of columns in the table.
    n_rows
        The number of rows in the table.
    row_index
        The current row index.
    row_span
        The row span.
    segment_id
        The TableSegment ID for error reporting.

    Raises
    ------
    ValueError
        If spans exceed table bounds.
    """

    if row_index + row_span > n_rows:
        raise ValueError(
            f"row_span out of bounds (row={row_index}, row_span={row_span}, n_rows={n_rows}) "
            f"in TableSegment '{segment_id}'."
        )
    if cursor + col_span > n_cols:
        raise ValueError(
            f"col_span out of bounds (row={row_index}, col={cursor}, col_span={col_span}, n_cols={n_cols}) "
            f"in TableSegment '{segment_id}'."
        )


def assert_page_items_consumed_exactly_once(
    *,
    items_mapping: dict[int, list[tuple[int, Block | Table]]],
    segments: list[Segment],
) -> None:
    """Validate that every normalized PageIR item is consumed exactly once by segments.
    Expected universe of segments is derived from `items_mapping` (i.e.,
    post-normalization, with artifacts filtered if keep_artifacts=False upstream).

    Parameters
    ----------
    items_mapping
        Mapping of page_index to list of (item_index, item) tuples after normalization.
    segments
        The list of segments to validate.

    Raises
    ------
    ValueError
        Any of:
            - Missing items (expected but not present in any
                segment.segment_provenance).
            - Extra items (present in segment.segment_provenance but not expected).
            - Duplicate consumption (same (page_index,item_index) appears in > 1
                segment.segment_provenance).
    """

    expected: set[ItemKey] = {
        (page_index, orig_item_index)
        for page_index, items in items_mapping.items()
        for (orig_item_index, _) in items
    }
    used_by: dict[ItemKey, list[str]] = defaultdict(list)

    for segment in segments:
        for provenance in segment.segment_provenance:
            k: ItemKey = (provenance.page_index, provenance.item_index)
            used_by[k].append(segment.segment_id)

    seen = set(used_by.keys())
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    dupes = sorted([k for k, seg_keys in used_by.items() if len(seg_keys) > 1])

    if not (missing or extra or dupes):
        return

    def _fmt_keys(keys: list[ItemKey], limit: int = 25) -> str:
        """Format a list of (page_index, item_index) keys for display.

        Parameters
        ----------
        keys
            The list of keys.
        limit
            The maximum number of keys to show.

        Returns
        -------
        str
            The formatted string.
        """

        if not keys:
            return "[]"

        head = ", ".join([f"(p={p}, i={i})" for p, i in keys[:limit]])
        tail = "" if len(keys) <= limit else f", ... (+{len(keys) - limit} more)"

        return f"[{head}{tail}]"

    # For dupes, show which segments consumed them.
    dupe_details = ""

    if dupes:
        lines = [f"  {k} -> {used_by[k]}" for k in dupes[:10]]
        remaining = len(dupes) - 10

        if remaining > 0:
            lines.append(f"  ... (+{remaining} more)")

        joined_lines = "\n".join(lines)
        dupe_details = f"\nDuplicate details ...\n{joined_lines}"

    raise ValueError(
        f"Integrity check failed: normalized PageIR items were not consumed exactly once.\n"
        f"Missing (expected but not consumed): {_fmt_keys(missing)}\n"
        f"Extra (consumed but not expected): {_fmt_keys(extra)}\n"
        f"Duplicates (consumed >1 time): {_fmt_keys(dupes)}"
        f"{dupe_details}"
    )


def build_continuation_chain(
    *,
    items_lookup: dict[int, dict[int, Block | Table]],
    links: dict[ItemKey, ItemKey],
    start_item: Block | Table,
    start_key: ItemKey,
    warnings: list[str],
) -> list[ChainItem]:
    """Follow links to build a list of items belonging to one logical segment.

    Parameters
    ----------
    items_lookup
        A mapping of page_index to item_index to Item. This allows lookup of items by
        their original index, even if intermediate artifacts were filtered out.
    links
        A mapping of (page_index, item_index) to (next_page_index, next_item_index) for
        items that continue across page breaks.
    start_item
        The starting item of the chain.
    start_key
        The (page_index, item_index) of the starting item.
    warnings
        A list of warnings associated with this chain.

    Returns
    -------
    list[ChainItem]
        A list of (page_index, item_index, item) tuples representing the chain of
        continuation items.
    """

    chain: list[ChainItem] = []
    current_page_index, current_item_index = start_key
    current_item = start_item

    while True:
        chain.append((current_page_index, current_item_index, current_item))
        next_link = links.get((current_page_index, current_item_index), None)

        if not next_link:
            break

        next_page_index, next_item_index = next_link
        next_page_map = items_lookup.get(next_page_index, None)

        # Broken link (page missing).
        if next_page_map is None:
            msg = (
                f"Broken link from {(current_page_index, current_item_index)}->{next_link}: "
                f"Page {next_page_index} not found in lookup."
            )
            logger.warning(msg)
            warnings.append(msg)
            break

        # Look up the next item by original index.
        next_item = next_page_map.get(next_item_index)

        # Broken link (item missing on page).
        if next_item is None:
            msg = (
                f"Broken link from {(current_page_index, current_item_index)}->{next_link}: "
                f"Item {next_item_index} not found on page {next_page_index}."
            )
            logger.warning(msg)
            warnings.append(msg)
            break

        # Assert compatible kinds. NB: This is strictly a sanity check at this point
        # compatible kinds should have been checked in match_candidates (which is
        # upstream of this function call). However, it's cheap and worth checking again
        # to ensure nothing broke.
        assert compatible_kinds_for_stitch(next_item=next_item, prev_item=current_item)

        # Advance to next item.
        current_page_index, current_item_index = next_page_index, next_item_index
        current_item = next_item

    return chain


def compatible_kinds_for_stitch(
    *, next_item: Block | Table, prev_item: Block | Table
) -> bool:
    """Return True if two items are stitch-compatible.

    NB: This purpose of this function is to act as a gate that determines whether two
    items are stitchable. For example:

    1. Table <-> Table: stitchable
    2. Paragraph <-> Paragraph: stitchable
    3. Caption <-> Caption: stitchable
    4. Heading: unstitchable

    Parameters
    ----------
    next_item
        The next item.
    prev_item
        The previous item.

    Returns
    -------
    bool
        True if the two items are stitch-compatible.
    """

    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        # Allow CAPTION <-> CAPTION *only* when strongly anchored.
        if (
            prev_item.block_type == BlockType.CAPTION
            and next_item.block_type == BlockType.CAPTION
        ):
            # local_code match.
            prev_code = normalize_local_code(prev_item.local_code)
            next_code = normalize_local_code(next_item.local_code)
            if prev_code and next_code and prev_code == next_code:
                return True

            # Caption text begins with same "Table X"/"Figure Y".
            prev_text = (
                (prev_item.text.text or "").strip()
                if isinstance(prev_item.text, TextUnit)
                else ""
            )
            next_text = (
                (next_item.text.text or "").strip()
                if isinstance(next_item.text, TextUnit)
                else ""
            )
            prev_m = _TABLE_FIGURE_RE.match(prev_text)
            next_m = _TABLE_FIGURE_RE.match(next_text)

            if prev_m and next_m:
                prev_anchor = f"{prev_m.group(1).lower()} {prev_m.group(2)}"
                next_anchor = f"{next_m.group(1).lower()} {next_m.group(2)}"
                if prev_anchor == next_anchor:
                    return True

            # Otherwise, too risky.
            return False

        # Headings should still never stitch.
        if (
            prev_item.block_type == BlockType.HEADING
            or next_item.block_type == BlockType.HEADING
        ):
            return False

        # For all other blocks:
        #   - Allow exact block_type matches (e.g., FIGURE<->FIGURE).
        #   - Allow "text-like" continuation between PARAGRAPH and LIST (extractor can
        #       flip across pages).
        textlike = {BlockType.FOOTNOTE, BlockType.LIST, BlockType.PARAGRAPH}
        is_textlike_continuation = (
            prev_item.block_type in textlike and next_item.block_type in textlike
        )
        return is_textlike_continuation or (
            prev_item.block_type == next_item.block_type
        )

    # Table <-> Table allowed at this point but all others are not.
    return isinstance(prev_item, Table) and isinstance(next_item, Table)


def compute_page_break_links(
    *,
    items_mapping: dict[int, list[tuple[int, Block | Table]]],
    link_debug: list[dict[str, Any]],
    min_link_score: float,
    page_irs: list[PageIR],
    page_pair_debug: list[dict[str, Any]],
    warnings: list[str],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Compute a mapping of (page_i, item_index) -> (page_i+1, item_index) links for
    continuations. This uses only the already-verified item boundaries:

    1. prev item boundary must continue to next (TRUNCATED or BOTH).
    2. next item boundary must continue from prev (RESUMED or BOTH).

    We choose candidates nearest the bottom/top of each page to resolve ambiguity.

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
        logger.info(f"Computing page break links for pages {i} -> {i + 1}...")

        page_pair_links = process_page_pair(
            current_page_ir=page_irs[i],
            link_debug=link_debug,
            min_link_score=min_link_score,
            next_page_ir=page_irs[i + 1],
            next_page_items=items_mapping[page_irs[i + 1].page_index],
            page_pair_debug=page_pair_debug,
            prev_page_items=items_mapping[page_irs[i].page_index],
            warnings=warnings,
        )
        all_page_pair_links.update(page_pair_links)

    logger.success("Completed computing page break links!")

    return all_page_pair_links


def compute_segment_id(
    *, doc_key: str, item_index: int, kind: str, page_index: int
) -> str:
    """Compute a deterministic UUIDv5 for a stitched segment. Segment IDs must be
    stable across reruns and (ideally) globally unique across PDFs, so we include the
    PDF doc_key plus the first source item pointer.

    Parameters
    ----------
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    item_index
        0-based original item index within PageIR.items for the first slice.
    kind
        Segment kind ('block' or 'table').
    page_index
        0-based page index of the segment's first slice.

    Returns
    -------
    str
        UUIDv5 string.
    """

    name = f"{doc_key}:segment:{kind}:p{page_index:04d}:i{item_index:04d}"

    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def canonicalize_local_code(local_code: Optional[str]) -> Optional[str]:
    """Canonicalize a local code for display.

    NB:

    1. This is NOT for matching/comparison (use normalize_local_code for that).
    2. Goal is to preserve a display-safe value like "Table 4", not "table 4".

    Parameters
    ----------
    local_code
        The local code to canonicalize.

    Returns
    -------
    Optional[str]
        The canonicalized local code, or None if input is None/empty.
    """

    if not local_code:
        return None

    s = local_code.strip()
    if not s:
        return None

    # Canonicalize common table/figure code prefixes to a stable display form.
    # Examples:
    #   - "TABLE 4" -> "Table 4"
    #   - "Jedwali 4" -> "Table 4"
    #   - "Tableau 4" -> "Table 4"
    #   - "Figure 2" -> "Figure 2"
    m = _CAPTION_TABLE_RE.match(s)

    if m:
        return f"Table {m.group(1)}"

    m2 = _TABLE_FIGURE_RE.match(s)

    if m2:
        kind = m2.group(1).capitalize()  # Table/Figure
        num = m2.group(2)
        return f"{kind} {num}"

    # Otherwise keep as-is (trimmed).
    return s


def create_document_ir_dirs(*, output_dir: Path) -> DocumentIRDirs:
    """Create document IR directories for a given stitching run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    DocumentIRDirs
        The created document IR directories.
    """

    root = output_dir

    for p in [root]:
        make_dir(p)

    return DocumentIRDirs(root=root)


def create_item_addr(*, item_index: int, page_index: int) -> str:
    """Create a stable, reversible address for a raw PageIR item.

    Parameters
    ----------
    item_index
        The 0-based original item index within PageIR.items.
    page_index
        The 0-based page index.

    Returns
    -------
    str
        The item address.
    """

    return f"p{page_index}:raw{item_index}"


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

    sig_rows = [",".join(_row_signature(hr)) for hr in header_rows]
    n_cols = max(
        (sum(cell.col_span for cell in row.cells) for row in table.rows), default=0
    )
    base = f"hrc={hrc}|ncols={n_cols}|rows={'||'.join(sig_rows)}"

    return compute_sha256_hex(n_hex=24, s=base)


def expand_table_rows_to_rows_grid(
    *, segment: TableSegment
) -> tuple[list[TableRow], list[list[dict[str, Any]]]]:
    """Expand a stitched table's ragged rows with row_span/col_span into a rectangular
    grid of shape (n_rows x n_cols). Output rows have exactly n_cols cells each, and
    every output TableCell has row_span=1, col_span=1.

    Parameters
    ----------
    segment
        The stitched TableSegment.

    Returns
    -------
    tuple[list[TableRow], list[list[dict[str, Any]]]]
        A tuple where the first element is the list of expanded TableRows, and the
        second element is the grid_sources mapping (per-cell source row index).
    """

    n_cols = segment.n_cols
    n_rows = len(segment.rows)

    assert (
        n_cols > 0
    ), f"Cannot expand spans: invalid n_cols={segment.n_cols} (segment_id={segment.segment_id})"

    # Initialize empty grid: grid[row][col] = {"text": TextUnit | None, "source_row": int}
    grid: list[list[dict[str, Any]]] = [
        [{"text": None, "source_row": -1} for _ in range(n_cols)] for _ in range(n_rows)
    ]

    # Populate spans into grid.
    _populate_grid_spans(segment=segment, grid=grid, n_rows=n_rows, n_cols=n_cols)

    # Convert grid to TableRow list and aligned grid_sources.
    grid_sources: list[list[dict[str, Any]]] = []
    out_rows: list[TableRow] = []

    for r in range(n_rows):
        out_cells: list[TableCell] = []
        src_row: list[dict[str, Any]] = []

        for c in range(n_cols):
            out_cells.append(TableCell(text=grid[r][c]["text"], row_span=1, col_span=1))
            src_row.append({"source_row": grid[r][c]["source_row"]})

        out_rows.append(TableRow(cells=out_cells))
        grid_sources.append(src_row)

    return out_rows, grid_sources


def fill_down_table_rows(
    *, header_row_count: int, rows: list[TableRow], table_filldown_group_cols_max: int
) -> list[TableRow]:
    """Fill down visually empty cells in the first `filldown_group_cols_max` columns.
    This reconstructs the implicit semantics of merged cells/rowspans often used in
    curriculum tables (e.g., Topic/Sub-topic cells left blank on subsequent rows).

    The process is as follows:

    1. Header rows are NOT filled down.
    2. Only fills if the target cell is empty (None or whitespace).
    3. Never overwrites non-empty cells.
    4. Returns a deep-copied row list (does not mutate input).

    Parameters
    ----------
    header_row_count
        The number of header rows at the top of the table.
    rows
        The list of table rows to fill.
    table_filldown_group_cols_max
        The maximum number of leading group columns to fill down.

    Returns
    -------
    list[TableRow]
        The filled-down table rows.
    """

    if table_filldown_group_cols_max <= 0:
        return [row.model_copy(deep=True) for row in rows]

    # Don't mutate input rows.
    output_rows: list[TableRow] = [row.model_copy(deep=True) for row in rows]

    # Track last non-empty TextUnit per leading group column.
    last_non_empty: list[Optional[TextUnit]] = [None] * table_filldown_group_cols_max

    for row_index, row in enumerate(output_rows):
        # Never fill header rows.
        if row_index < max(0, int(header_row_count)):
            continue

        # Only fill the first `group_cols_max` columns.
        max_ci = min(table_filldown_group_cols_max, len(row.cells))

        for ci in range(max_ci):
            cell = row.cells[ci]

            # Determine emptiness (structural): visually empty cell means None or blank
            # text. In other words, only fill empty cells.
            is_empty = cell.text is None or (
                cell.text.text is None or cell.text.text.strip() == ""
            )

            if is_empty:
                last_non_empty_text_unit = last_non_empty[ci]
                if isinstance(last_non_empty_text_unit, BaseModel):
                    cell.text = last_non_empty_text_unit.model_copy(deep=True)
            else:
                last_non_empty[ci] = cell.text

    return output_rows


def infer_header_row_count_from_rows(
    *, max_header_rows: int = 3, rows: list[TableRow]
) -> tuple[int, float]:
    """Infer header_row_count from table rows using a deterministic heuristic. This
    function is intended ONLY as a fallback when the extractor gave
    header_row_count == 0.

    Parameters
    ----------
    max_header_rows
        The maximum number of header rows to consider.
    rows
        The list of table rows.

    Returns
    -------
    tuple[int, float]
        Tuple containing (header_row_count, confidence).
    """

    if not rows:
        return 0, 0.0

    def row_header_score(row: TableRow) -> float:
        """Compute a heuristic "header-likeness" score for a table row. Higher scores
        indicate more header-like rows.

        Parameters
        ----------
        row
            The table row.

        Returns
        -------
        float
            The header-likeness score.
        """

        parts: list[str] = []
        filled_cells = 0

        for cell in row.cells:
            if cell.text and cell.text.text and cell.text.text.strip():
                parts.append(cell.text.text.strip())
                filled_cells += 1

        text = " ".join(parts)

        if not text:
            return 0.0

        compact = re.sub(r"\s+", "", text)
        total = max(1, len(compact))

        # Calculate content ratios.
        alpha = len(_ALPHA_RE.findall(text))
        digits = len(_DIGIT_RE.findall(text))

        alpha_ratio = alpha / total
        digit_ratio = digits / total

        filled_ratio = filled_cells / max(1, len(row.cells))

        # Weighted: headers tend to be word-heavy, number-light, and spread across
        # columns.
        return (2.0 * alpha_ratio) + (1.0 * filled_ratio) - (1.5 * digit_ratio)

    # Walk the first N rows and count consecutive "header-like" rows from the top.
    count = 0
    scores: list[float] = []

    for row_index in range(min(max_header_rows, len(rows))):
        score = row_header_score(rows[row_index])
        scores.append(score)

        # Conservative threshold: first rows must look clearly "header-ish"
        # (word-heavy + reasonably filled).
        if score >= 1.15:
            count += 1
        else:
            break

    if count == 0:
        return 0, 0.0

    # Confidence increases with count and score strength.
    avg = sum(scores[:count]) / max(1, count)
    confidence = min(1.0, 0.55 + 0.15 * (count - 1) + 0.20 * max(0.0, avg - 1.15))

    return count, confidence


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


def materialize_segment(
    *,
    chain: list[ChainItem],
    doc_key: str,
    item_index: int,
    page_index: int,
    repair_hyphenation: bool,
    section_path: list[SectionHeadingRef],
    table_filldown_enabled: bool,
    table_filldown_group_cols_max: int,
    warnings: list[str],
) -> Segment:
    """Dispatch the continuation chain to the correct merging logic based on item type.

    Parameters
    ----------
    chain
        A list of (page_index, item_index, item) tuples representing a chain of
        continuation items.
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    item_index
        The starting item index of the chain.
    page_index
        The starting page index of the chain.
    repair_hyphenation
        If True, repair hyphenation at line breaks when combining text units.
    section_path
        The section path for the segment.
    table_filldown_enabled
        If True, apply fill-down logic to group columns in tables.
    table_filldown_group_cols_max
        The maximum number of leading group columns to fill down in tables.
    warnings
        A list to append warning messages to.

    Returns
    -------
    Segment
        The merged Segment (BlockSegment or TableSegment).
    """

    first_chain_item = chain[0][2]

    if isinstance(first_chain_item, Table):
        table_chain = [
            (page_index, item_index, item)
            for page_index, item_index, item in chain
            if isinstance(item, Table)
        ]

        if len(table_chain) != len(chain):
            msg = f"Mixed-kind chain starting at {(page_index, item_index)}; kept as standalone."
            logger.warning(msg)
            warnings.append(msg)

            # Fallback: treat first item standalone.
            table_chain = [(page_index, item_index, first_chain_item)]

        return stitch_table_chain(
            chain=table_chain,
            doc_key=doc_key,
            section_path=section_path,
            table_filldown_enabled=table_filldown_enabled,
            table_filldown_group_cols_max=table_filldown_group_cols_max,
            warnings=warnings,
        )

    block_chain = [
        (page_index, item_index, item)
        for page_index, item_index, item in chain
        if isinstance(item, Block)
    ]

    if len(block_chain) != len(chain):
        msg = f"Mixed-kind chain starting at {(page_index, item_index)}; kept as standalone."
        logger.warning(msg)
        warnings.append(msg)

        # Fallback: treat first item standalone.
        block_chain = [(page_index, item_index, first_chain_item)]

    return stitch_block_chain(
        chain=block_chain,
        doc_key=doc_key,
        repair_hyphenation=repair_hyphenation,
        section_path=section_path,
    )


def normalize_local_code(local_code: Optional[str]) -> Optional[str]:
    """Normalize a local code for comparison.

    Parameters
    ----------
    local_code
        The local code.

    Returns
    -------
    Optional[str]
        The normalized local code, or None if empty.
    """

    normalized_local_code = (
        local_code.strip() if local_code and local_code.strip() else None
    )

    if not normalized_local_code:
        return None

    # Collapse internal whitespace then case-fold.
    normalized_local_code = _LOCAL_CODE_RE.sub(" ", normalized_local_code.strip())
    return normalized_local_code.casefold()


def normalize_page_items(
    *,
    keep_artifacts: bool,
    page_ir: PageIR,
    sort_items_by_bbox: bool,
    warnings: list[str],
) -> list[tuple[int, Block | Table]]:
    """Normalize a PageIR item for stitching.

    Parameters
    ----------
    keep_artifacts
        If True, keep artifact blocks (page numbers, running headers/footers).
    page_ir
        Source PageIR.
    sort_items_by_bbox
        If True, sort items by their bounding box top-left y-coordinate (ascending),
        then x-coordinate (ascending). This ensures a consistent reading order.
    warnings
        A list to append warning messages to.

    Returns
    -------
    list[tuple[int, Union[Table, Block]]]
        List of (item_index, item) tuples after normalization.
    """

    items_mapping = []

    for index, item in enumerate(page_ir.items):
        if (
            isinstance(item, Table)
            and item.boundary in {ItemBoundary.COMPLETE, ItemBoundary.TRUNCATED}
            and item.repeats_header is not None
        ):
            msg = (
                f"Clearing repeats_header for table at index {index} on page {page_ir.page_index} "
                f"because boundary is {item.boundary}."
            )
            logger.warning(msg)
            warnings.append(msg)
            item.repeats_header = None

        if keep_artifacts or not is_artifact(item):
            items_mapping.append((index, item))

    if sort_items_by_bbox:
        # Capture the order of indices before the sort.
        original_order = [pair[0] for pair in items_mapping]

        def _sort_key(
            pair: tuple[int, Block | Table],
        ) -> tuple[float, float, float, float, int]:
            """Sorting key for bounding box scanline order. Top-to-bottom, then
            left-to-right.

            Parameters
            ----------
            pair
                The (original_index, item) tuple.

            Returns
            -------
            tuple[float, float, float, float, int]
                The sorting key.
            """

            orig_index, item = pair
            x0, y0, x1, y1 = item.bbox

            return y0, x0, x1, y1, orig_index

        items_mapping.sort(key=_sort_key)

        # Capture the order of indices after the sort.
        new_order = [pair[0] for pair in items_mapping]

        # If the sequences differ, the items were reordered.
        if original_order != new_order:
            msg = (
                f"Bbox ordering changed for page {page_ir.page_index}.\n"
                f"Items were re-sorted to follow a top-to-bottom, left-to-right reading order.\n"
                f"Original ordering: {original_order}\n"
                f"New ordering: {new_order}\n"
            )
            logger.warning(msg)
            warnings.append(msg)

    propagate_caption_table_local_codes(
        items=items_mapping, page_index=page_ir.page_index, warnings=warnings
    )

    return items_mapping


def persist_stitching_run(
    *, config: StitchingConfig, output_dir: Path
) -> tuple[DocumentIRDirs, RunCtx]:
    """Persist stitching run metadata.

    Parameters
    ----------
    config
        The stitching run configuration.
    output_dir
        The output directory for the stitching run results.

    Returns
    -------
    tuple[DocumentIRDirs, RunCtx]
        The created stitching directories and persisted stitching run metadata.
    """

    stitching_dirs = create_document_ir_dirs(output_dir=output_dir)
    exclude_keys = {"model", "overwrite"}
    stitching_run = RunCtx(
        extra={
            k: v
            for k, v in config.model_dump(mode="json").items()
            if k not in exclude_keys
        },
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "stitching_run.json", json_info=stitching_run)
    logger.info(f"Saving stitching results to: {stitching_dirs.root}")

    return stitching_dirs, stitching_run


def propagate_caption_table_local_codes(
    *, items: list[tuple[int, Block | Table]], page_index: int, warnings: list[str]
) -> None:
    """Propagate caption table codes to the nearest following table on the same page.
    Many curriculum PDFs label tables only in a caption block (e.g., 'Table 4: ...'),
    while the extracted Table item has local_code=None.

    Parameters
    ----------
    items
        The page's normalized items list.
    page_index
        The page index.
    warnings
        A list to append warning messages to.
    """

    for i, (caption_orig_index, item) in enumerate(items):
        if not isinstance(item, Block) or item.block_type != BlockType.CAPTION:
            continue

        code = _extract_table_local_code_from_caption(item)

        if not code:
            continue

        # Find the nearest following Table item and attach local_code if missing.
        for j in range(i + 1, len(items)):
            table_orig_index, next_item = items[j]

            if isinstance(next_item, Table):
                if not normalize_local_code(next_item.local_code):
                    next_item.local_code = code
                    msg = (
                        f"Propagated caption code '{code}' to table on page {page_index}: "
                        f"caption_raw_index={caption_orig_index}->table_raw_index={table_orig_index}."
                    )
                    logger.warning(msg)
                    warnings.append(msg)

                break

            # If we hit another caption before a table, don't skip past it.
            if (
                isinstance(next_item, Block)
                and next_item.block_type == BlockType.CAPTION
            ):
                break


def process_page_pair(
    *,
    current_page_ir: PageIR,
    link_debug: list[dict[str, Any]],
    min_link_score: float,
    next_page_ir: PageIR,
    next_page_items: list[tuple[int, Block | Table]],
    page_pair_debug: list[dict[str, Any]],
    prev_page_items: list[tuple[int, Block | Table]],
    warnings: list[str],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Orchestrate candidate finding, warning logging, and linking for a single pair of
    pages.

    The process is as follows:

    1. Identify candidates (rejected vs. valid).
    2. Apply page-level boundary state guardrails.
    3. Prepare a page-pair debug record.
    4. Append warnings for unsafe candidates (rejected).
    5. Append warnings for scenarios where no candidates exist.
    6. Compute links between valid candidates.
    7. Append page-pair debug info.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
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
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[tuple[int, int], tuple[int, int]]
        Forward links for items that continue across the page break.
    """

    # 1.
    (
        prev_rejected_indices,
        prev_candidate_indices,
        next_rejected_indices,
        next_candidate_indices,
    ) = _find_paired_candidates(
        prev_items=prev_page_items,
        next_items=next_page_items,
    )

    # 2.
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

    # 3.
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

    # 4.
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

    # 5.
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
            logger.error(f"{prev_candidate_indices = }")
            logger.error(f"{next_candidate_indices = }")
            logger.error(f"{prev_rejected_indices = }")
            logger.error(f"{next_rejected_indices = }")
            logger.warning(msg)
            warnings.append(msg)

        page_pair_debug.append(pair_debug)
        return {}

    # 6.
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

    # 7.
    page_pair_debug.append(pair_debug)

    return links


def row_provenance_by_stitched_index(
    *, segment: TableSegment
) -> list[TableRowProvenance]:
    """Return a list of length len(segment.rows) where each entry contains the
    provenance info (at least bbox + page_index + slice_index) for the stitched row.
    This is computed deterministically from `segment.slices` using the same
    header-dropping logic as stitching.

    NB:

    1. repeats_header=True -> drop header_row_count rows on that slice
    2. repeats_header=False -> drop nothing
    3. repeats_header=None -> *infer* repeated header by matching the slice's first
        header rows against the segment's header rows (conservative).

    Parameters
    ----------
    segment
        The stitched TableSegment.

    Returns
    -------
    list[TableRowProvenance]
        The per-stitched-row provenance mapping.

    Raises
    ------
    ValueError
        If the mapping length mismatches.
    """

    assert (
        segment.slices
    ), f"TableSegment {segment.segment_id} has no slices; cannot derive row provenance."

    mapping: list[TableRowProvenance] = []

    for slice_index, sl in enumerate(segment.slices):
        bbox = sl.bbox

        assert isinstance(bbox, list) and len(bbox) == 4, (
            f"Missing/invalid bbox for TableSlice (segment_id={segment.segment_id}, "
            f"slice_index={slice_index}, page_index={sl.page_index}): {bbox}"
        )

        # Use the actual number of header rows dropped during stitching. This avoids
        # provenance drift when slice.header_row_count is missing/0 but the stitcher
        # still drops repeated headers via canonical matching.
        drop = 0 if slice_index == 0 else sl.dropped_header_rows

        # Never drop beyond available rows.
        drop = min(drop, len(sl.rows))
        effective_rows = sl.rows[drop:]

        # Approximate per-row bbox from the slice bbox by evenly splitting the slice
        # bbox vertically across the slice's visual rows. This is deterministic and
        # makes debug output much more actionable than a coarse slice bbox.
        total_rows_in_slice = len(sl.rows)
        x0, y0, x1, y1 = bbox
        row_h = ((y1 - y0) / total_rows_in_slice) if total_rows_in_slice > 0 else 0.0

        for i, _ in enumerate(effective_rows):
            slice_row_index = drop + i
            row_bbox = [
                x0,
                y0 + row_h * slice_row_index,
                x1,
                y0 + row_h * (slice_row_index + 1),
            ]
            mapping.append(
                TableRowProvenance(
                    bbox=bbox,
                    dropped_header_rows=drop,
                    page_index=sl.page_index,
                    row_bbox=row_bbox,
                    slice_index=slice_index,
                    slice_row_index=slice_row_index,
                    slice_row_index_after_drop=i,
                    slice_total_rows=total_rows_in_slice,
                )
            )

    if len(mapping) != len(segment.rows):
        raise ValueError(
            f"Row <-> slice mapping length mismatch for TableSegment {segment.segment_id}. "
            f"Derived {len(mapping)} rows from slices, but segment.rows has {len(segment.rows)}."
        )

    return mapping


def save_document_ir(
    *,
    doc_key: str,
    document_ir_fp: Path,
    link_debug: list[dict[str, Any]],
    links: dict[tuple[int, int], tuple[int, int]],
    page_irs: list[PageIR],
    page_pair_debug: list[dict[str, Any]],
    pdf_name: str,
    segments: list[Segment],
    stitching_dirs: DocumentIRDirs,
    warnings: list[str],
) -> None:
    """Persist the final DocumentIR and stitch report.

    Parameters
    ----------
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    document_ir_fp
        The output file path for the DocumentIR JSON.
    link_debug
        List of per-link debug info.
    links
        Forward links for items that continue across page breaks.
    page_irs
        The list of PageIRs.
    page_pair_debug
        List of per-page-pair debug info.
    pdf_name
        The PDF file name.
    segments
        The list of stitched segments.
    stitching_dirs
        The stitching directories.
    warnings
        A list of warning messages.
    """

    # Write DocumentIR to file.
    first_page = page_irs[0]
    document_ir = DocumentIR(
        coord_space=first_page.coord_space,
        doc_key=doc_key,
        dpi=first_page.dpi,
        image_height=first_page.image_height,
        image_width=first_page.image_width,
        page_count=len(page_irs),
        pdf_name=pdf_name,
        segments=segments,
        warnings=warnings,
    )
    write_to_json(fp=document_ir_fp, json_info=document_ir)

    # Write a stitch report JSON artifact.
    stitch_report_fp = stitching_dirs.root / "stitch_report.json"
    table_segments_summary: list[dict[str, Any]] = []

    for segment in segments:
        if segment.kind != "table":
            continue

        pages = [slice.page_index for slice in segment.slices]
        table_segments_summary.append(
            {
                "segment_id": segment.segment_id,
                "local_code": segment.local_code,
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "slice_count": len(segment.slices),
                "header_row_count": segment.header_row_count,
                "slices": [
                    {
                        "page_index": slice.page_index,
                        "item_index": slice.item_index,
                        "boundary": slice.boundary.value,
                        "repeats_header": slice.repeats_header,
                        "dropped_header_rows": slice.dropped_header_rows,
                    }
                    for slice in segment.slices
                ],
            }
        )

    table_segments_summary.sort(
        key=lambda x: (
            x["page_start"] if x["page_start"] is not None else 10**9,
            x["segment_id"],
        )
    )
    stitch_report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "block_type_counts": dict(
            Counter(
                [
                    s.block_type.value
                    for s in segments
                    if getattr(s, "kind", None) == "block"
                ]
            )
        ),
        "doc_key": doc_key,
        "link_count": len(links),
        "link_debug": link_debug,
        "page_count": len(page_irs),
        "page_pair_debug": page_pair_debug,
        "pdf_name": pdf_name,
        "segment_kind_counts": dict(Counter([s.kind for s in segments])),
        "table_segments": table_segments_summary,
        "warnings": warnings,
    }

    write_to_json(fp=stitch_report_fp, json_info=stitch_report)


def stitch_block_chain(
    *,
    chain: list[tuple[int, int, Block]],
    doc_key: str,
    repair_hyphenation: bool,
    section_path: list[SectionHeadingRef],
) -> BlockSegment:
    """Stitch a chain of block slices.

    NB: Block continuation chains can carry different payload types. We treat them
    differently:

    1. Text (`Block.text`/`TextUnit`) is additive across slices: a paragraph can be
        split across pages (truncated/resumed). We therefore collect all slice
        TextUnits and join them deterministically into a single segment-level
        `combined_text`/`text`.
    2. Lists (`Block.list_items`) are also additive: list items may continue on the
        next page. We therefore concatenate list items from all slices into one
        segment-level list.
    3. Figures (`Block.figure`) are NOT merged across slices. Figure payloads are
        structured objects and merging arbitrary dicts is not reliable or
        deterministic. We keep the first non-null figure payload as the segment-level
        representative (`BlockSegment.figure`) and preserve per-slice figure payloads
        in `slices[].figure` for full fidelity.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Block) tuples representing the slices to
        stitch.
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    repair_hyphenation
        If True, repair hyphenation at line breaks when combining text units.
    section_path
        The section path for the segment.

    Returns
    -------
    BlockSegment
        The stitched BlockSegment.
    """

    first_chain_page_index, first_chain_item_index, first_chain_item = chain[0]
    segment_id = compute_segment_id(
        doc_key=doc_key,
        item_index=first_chain_item_index,
        kind="block",
        page_index=first_chain_page_index,
    )

    figure_payload: Optional[dict[str, Any]] = None
    list_items: list[ListItem] = []
    resolved_local_code: Optional[str] = None
    segment_provenance: list[SegmentProvenance] = []
    slices: list[BlockSlice] = []
    text_units: list[TextUnit] = []

    for page_index, item_index, block in chain:
        # Promote local_code across the stitched chain: take the first non-empty code
        # encountered in any slice.
        if resolved_local_code is None:
            if isinstance(block.local_code, str):
                lc = block.local_code.strip()
                if lc:
                    resolved_local_code = lc

        block_figure = (
            block.figure.model_dump(mode="json") if block.figure is not None else None
        )
        block_list_items = block.list_items if block.list_items else None
        slices.append(
            BlockSlice(
                bbox=block.bbox,
                block_type=block.block_type,
                boundary=block.boundary,
                figure=block_figure,
                item_index=item_index,
                list_items=block_list_items,
                local_code=block.local_code,
                page_index=page_index,
                text=block.text,
            )
        )
        segment_provenance.append(
            SegmentProvenance(
                bbox=block.bbox,
                boundary=block.boundary,
                item_addr=create_item_addr(
                    item_index=item_index, page_index=page_index
                ),
                item_index=item_index,
                kind=block.kind,
                local_code=block.local_code,
                page_index=page_index,
                repeats_header=None,
            )
        )

        if block.text is not None:
            text_units.append(block.text)
        if block_list_items:
            list_items.extend(block_list_items)
        if figure_payload is None and block_figure:
            figure_payload = block_figure

    combined_text: Optional[str] = None
    stitched_text: Optional[TextUnit] = first_chain_item.text

    if text_units:
        combined_text = _join_text_unit_texts(
            repair_hyphenation=repair_hyphenation, text_units=text_units
        )

        # If slice languages disagree, mark the stitched segment as mixed-language.
        languages = {text_unit.language for text_unit in text_units}

        if len(languages) > 1:
            # NB: Do NOT mutate any slice TextUnit -> create a new one instead.
            stitched_text = TextUnit(language="mul", text=combined_text, text_en=None)
        else:
            # Single language: avoid None if first slice lacked text.
            stitched_text = first_chain_item.text or text_units[0]

    return BlockSegment(
        block_type=first_chain_item.block_type,
        combined_text=combined_text,
        figure=figure_payload,
        list_items=list_items
        or (first_chain_item.list_items if first_chain_item.list_items else None),
        local_code=resolved_local_code,
        section_path=section_path,
        segment_id=segment_id,
        segment_provenance=segment_provenance,
        slices=slices,
        text=stitched_text,
    )


def stitch_table_chain(
    *,
    chain: list[tuple[int, int, Table]],
    doc_key: str,
    section_path: list[SectionHeadingRef],
    table_filldown_enabled: bool,
    table_filldown_group_cols_max: int,
    warnings: list[str],
) -> TableSegment:
    """Stitch a chain of table slices.

    For local_code determination, the process is as follows:

    1. If first.local_code is None but a later slice has one, promote the first
        non-null code that we encounter.
    2. Once a code is known, carry it forward to later slices that are missing it.
    3. After the loop, if local_code was discovered mid-chain, then backfill
        slices[0].local_code and provenance[0].local_code if missing.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the
        slices to stitch.
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    section_path
        The section path for the segment.
    table_filldown_enabled
        If True, apply fill-down logic to group columns in tables.
    table_filldown_group_cols_max
        The maximum number of leading group columns to fill down in tables.
    warnings
        A list to append warning messages to.

    Returns
    -------
    TableSegment
        The stitched TableSegment.
    """

    first_page_index, first_item_index, first_item = chain[0]

    # 1. Resolve initial segment state (ID, local_code, header_count).
    segment_id = compute_segment_id(
        doc_key=doc_key,
        item_index=first_item_index,
        kind="table",
        page_index=first_page_index,
    )

    # Resolve local code (look ahead if missing in first slice).
    local_code = _resolve_initial_local_code(chain)

    # Resolve header row count (inference if missing).
    header_row_count = _resolve_header_row_count(
        first_item=first_item,
        item_index=first_item_index,
        page_index=first_page_index,
        warnings=warnings,
    )

    # 2. Initialize stitched data.
    stitched_rows: list[TableRow] = list(first_item.rows)
    header_rows = stitched_rows[:header_row_count] if header_row_count > 0 else []

    # Create first slice and provenance.
    slices: list[TableSlice] = [
        TableSlice(
            bbox=first_item.bbox,
            boundary=first_item.boundary,
            dropped_header_rows=0,
            header_row_count=header_row_count,
            item_index=first_item_index,
            local_code=local_code,
            page_index=first_page_index,
            repeats_header=first_item.repeats_header,
            rows=first_item.rows,
        )
    ]
    segment_provenance: list[SegmentProvenance] = [
        SegmentProvenance(
            bbox=first_item.bbox,
            boundary=first_item.boundary,
            item_addr=create_item_addr(
                item_index=first_item_index, page_index=first_page_index
            ),
            item_index=first_item_index,
            kind=first_item.kind,
            local_code=local_code,
            page_index=first_page_index,
            repeats_header=first_item.repeats_header,
        )
    ]

    # 3. Process the chain loop.
    for next_page, next_item_idx, next_item in chain[1:]:
        slice_result = _process_next_table_slice(
            current_local_code=local_code,
            next_item=next_item,
            next_item_index=next_item_idx,
            next_page_index=next_page,
            segment_header_row_count=header_row_count,
            segment_header_rows=header_rows,
            segment_id=segment_id,
            warnings=warnings,
        )

        # Update state.
        local_code = slice_result["local_code"]  # Carry forward potentially new code
        slices.append(slice_result["slice"])
        segment_provenance.append(slice_result["provenance"])
        stitched_rows.extend(slice_result["rows_to_add"])

    # 4. Finalize columns.
    n_cols, columns_signature, header_rows_canonical = _finalize_table_structure(
        chain=chain,
        stitched_rows=stitched_rows,
        header_rows=header_rows,
        segment_id=segment_id,
        local_code=local_code,
        warnings=warnings,
    )

    # 5. Build objects.
    table_segment = TableSegment(
        columns_signature=columns_signature,
        header_row_count=header_row_count,
        header_rows=header_rows,
        header_rows_canonical=header_rows_canonical,
        local_code=local_code,
        n_cols=n_cols,
        rows=stitched_rows,
        section_path=section_path,
        segment_id=segment_id,
        segment_provenance=segment_provenance,
        slices=slices,
    )

    # 6. Post-processing (grid and provenance).
    rows_grid, grid_sources = expand_table_rows_to_rows_grid(segment=table_segment)
    row_provenance = row_provenance_by_stitched_index(segment=table_segment)

    rows_filldown = None
    if table_filldown_enabled:
        # Prefer grid if available, else raw rows.
        target_rows = rows_grid if rows_grid is not None else stitched_rows
        rows_filldown = fill_down_table_rows(
            table_filldown_group_cols_max=table_filldown_group_cols_max,
            header_row_count=header_row_count,
            rows=target_rows,
        )

    return table_segment.model_copy(
        update={
            "grid_sources": grid_sources,
            "row_provenance": row_provenance,
            "rows_filldown": rows_filldown,
            "rows_grid": rows_grid,
        }
    )


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


def update_section_stack(
    *,
    chain: list[tuple[int, int, Block | Table]],
    max_len: int,
    section_path_stack: list[SectionHeadingRef],
    warnings: list[str],
) -> list[SectionHeadingRef]:
    """Update the section path stack if the current chain represents a heading.

    Parameters
    ----------
    chain
        A list of (page_index, item_index, item) tuples representing a chain of
        continuation items.
    max_len
        The maximum length of the section path stack.
    section_path_stack
        The current section path stack.
    warnings
        A list to append warning messages to.

    Returns
    -------
    list[SectionHeadingRef]
        The updated section path stack.
    """

    # Use the first item in the chain (heading segments are standalone).
    first_chain_item = chain[0][2]

    if not (
        isinstance(first_chain_item, Block)
        and first_chain_item.block_type == BlockType.HEADING
    ):
        return section_path_stack

    text_or_none = first_chain_item.text
    heading_text = (
        (text_or_none.text or "").strip() if isinstance(text_or_none, TextUnit) else ""
    )
    local_code = (first_chain_item.local_code or "").strip()

    if not heading_text and not local_code:
        msg = (
            f"Heading block missing text and local_code; not added to section_path: "
            f"page_index={chain[0][0]}, item_index={chain[0][1]}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return section_path_stack

    section_path_stack.append(
        SectionHeadingRef(
            item_index=chain[0][1],
            page_index=chain[0][0],
            text=heading_text or local_code,
        )
    )

    return section_path_stack[-max_len:]
