"""This module contains utility functions related to page IR **verification**."""

# Standard Library
import re
import uuid

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

# Third Party Library
import pymupdf

from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TableCell, TextUnit
from skg.page_ir_verification.llm import verify_page_ir_pairs
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.schemas import RunCtx, VerificationConfig
from skg.utils.constants import (
    BlockType,
    CaptionTablePrefixes,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import make_dir, open_json_type, truncate_text, write_to_json
from skg.utils.pdf import crop_image_to_top

# Compiled regexes.
_TABLE_PREFIX_RE = "|".join(re.escape(t) for t in CaptionTablePrefixes)
TABLE_CODE_RE = re.compile(
    rf"^\s*(?:{_TABLE_PREFIX_RE})\s+(?P<num>\d+(?:\.\d+)*)\b",
    re.IGNORECASE,
)


class EdgeVerdictRecord(NamedTuple):
    """Edge verdict record between two page IR candidates."""

    next_candidate_index: int
    next_page_index: int
    prev_candidate_index: int
    prev_page_index: int
    verdict: PageIRContinuityVerdict


@dataclass(frozen=True)
class PageIRVerificationDirs:
    """Dataclass for page IR verification directories."""

    root: Path
    page_irs_pair_crops: Path
    page_irs_pair_reports: Path
    page_irs_verified: Path


def _apply_single_edge_verdict(
    *,
    bools: dict[tuple[int, int], list[bool]],
    effective_local_codes: dict[tuple[int, int], str | None],
    local_code_conflicts: list[dict[str, Any]],
    local_code_patch: dict[tuple[int, int], str],
    min_confidence: float,
    record: EdgeVerdictRecord,
    repeats_header_patch: dict[tuple[int, int], bool],
) -> dict[str, Any]:
    """Apply logic for a single edge verdict and return the summary dict. Updates
    `bools` and `repeats_header_patch` in-place.

    Parameters
    ----------
    bools
        Mapping of (page_idx, item_idx) to [from_prev, to_next] booleans.
    effective_local_codes
        Mapping of (page_idx, item_idx) to effective local_code (after prior patches).
    local_code_conflicts
        List to append any local_code conflicts detected.
    local_code_patch
        Mapping of (page_idx, item_idx) to desired local_code string.
    min_confidence
        Minimum confidence threshold to apply edits.
    record
        The edge verdict record to apply.
    repeats_header_patch
        Mapping of (page_idx, item_idx) to desired repeats_header boolean.

    Returns
    -------
    dict[str, Any]
        Summary of the applied edge verdict.
    """

    verdict = record.verdict
    prev_key = (record.prev_page_index, record.prev_candidate_index)
    next_key = (record.next_page_index, record.next_candidate_index)
    should_apply = verdict.confidence >= min_confidence

    if should_apply:
        if verdict.is_continuation:
            bools[prev_key][1] = True  # prev.to_next
            bools[next_key][0] = True  # next.from_prev

            # Propagate local_code across TRUE continuation edges when one side is
            # missing.
            if verdict.continuation_kind in {
                PageContinuationKind.TABLE,
                PageContinuationKind.FIGURE,
            }:
                prev_code = effective_local_codes.get(prev_key)
                next_code = effective_local_codes.get(next_key)

                # If exactly one side has a code, copy it across and update the
                # effective map so multi-page chains propagate. NB: setdefault prevents
                # later edges from overwriting an earlier propagation decision for the
                # same key.
                if prev_code and not next_code:
                    effective_local_codes[next_key] = prev_code
                    local_code_patch.setdefault(next_key, prev_code)
                elif next_code and not prev_code:
                    effective_local_codes[prev_key] = next_code
                    local_code_patch.setdefault(prev_key, next_code)
                elif prev_code and next_code and prev_code != next_code:
                    local_code_conflicts.append(
                        {
                            "prev_page": record.prev_page_index,
                            "next_page": record.next_page_index,
                            "prev_index": record.prev_candidate_index,
                            "next_index": record.next_candidate_index,
                            "prev_code": prev_code,
                            "next_code": next_code,
                            "continuation_kind": verdict.continuation_kind.value,
                        }
                    )

            if (
                verdict.continuation_kind == PageContinuationKind.TABLE
                and verdict.set_next_table_repeats_header is not None
            ):
                repeats_header_patch[next_key] = verdict.set_next_table_repeats_header
        else:
            # Clear only the directional connection for THIS candidate pair.
            bools[prev_key][1] = False
            bools[next_key][0] = False

    # Return summary for reporting.
    return {
        "prev_index": record.prev_candidate_index,
        "next_index": record.next_candidate_index,
        "prev_page": record.prev_page_index,
        "next_page": record.next_page_index,
        "is_continuation": verdict.is_continuation,
        "continuation_kind": verdict.continuation_kind.value,
        "confidence": verdict.confidence,
        "applied": should_apply,
    }


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


def _bools_to_boundary(from_prev: bool, to_next: bool) -> ItemBoundary:
    """Convert (from_prev, to_next) booleans to ItemBoundary enum.

    Parameters
    ----------
    from_prev
        Whether the item continues from the previous page.
    to_next
        Whether the item continues onto the next page.

    Returns
    -------
    ItemBoundary
        The corresponding ItemBoundary enum.
    """

    if from_prev and to_next:
        return ItemBoundary.BOTH

    if from_prev:
        return ItemBoundary.RESUMED

    if to_next:
        return ItemBoundary.TRUNCATED

    return ItemBoundary.COMPLETE


def _boundary_to_bools(boundary: ItemBoundary | None) -> tuple[bool, bool]:
    """Return (from_prev, to_next).

    Parameters
    ----------
    boundary
        The item boundary state.

    Returns
    -------
    tuple[bool, bool]
        A tuple indicating (from_prev, to_next).
    """

    if boundary == ItemBoundary.BOTH:
        return True, True

    if boundary == ItemBoundary.RESUMED:
        return True, False

    if boundary == ItemBoundary.TRUNCATED:
        return False, True

    return False, False  # COMPLETE or None


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

    for li in list_items[: min(6, len(list_items))]:
        if isinstance(li, dict):
            marker = li.get("marker") or ""
            text = _get_text_content(li.get("text"))
            li_text = truncate_text(max_chars=180, text=text)
            preview.append((marker + " " + li_text).strip())
        else:
            preview.append(truncate_text(max_chars=180, text=str(li)))

    return preview


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
    bottom_body = (
        body_rows[-preview_rows:] if len(body_rows) > (2 * preview_rows) else []
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


def _normalize_local_code(code: str | None) -> str | None:
    """Normalize a local_code string by stripping whitespace.

    Parameters
    ----------
    code
        The local_code string to normalize.

    Returns
    -------
    str | None
        The normalized local_code, or None if empty.
    """

    return (code or "").strip() or None


def _process_table_item(
    *, item: Any, item_index: int, page_index: int
) -> list[dict[str, Any]]:
    """Process a single table item to fix row alignments.

    Parameters
    ----------
    item
        The document item (must be of kind 'table').
    item_index
        The index of the item in the page.
    page_index
        The index of the page.

    Returns
    -------
    list[dict[str, Any]]
        List of changes made to this table.
    """

    item_changes: list[dict[str, Any]] = []
    n_cols = item.n_cols

    if not isinstance(n_cols, int) or n_cols <= 0:
        return item_changes

    # active_span[c] = number of FUTURE rows that still occupy column c.
    active_span = [0] * n_cols

    for row_index, row in enumerate(item.rows or []):
        # Process the row without decrementing active_span yet.
        row_change = _process_table_row(active_span=active_span, n_cols=n_cols, row=row)

        if row_change:
            full_change = {
                "type": "rowspan_alignment_inserted_placeholders",
                "page": page_index,
                "item_index": item_index,
                "row_index": row_index,
                **row_change,
            }
            item_changes.append(full_change)

        # Decrement active_span after finishing the row.
        active_span = [max(0, x - 1) for x in active_span]

    return item_changes


def _process_table_row(
    *, active_span: list[int], n_cols: int, row: Any
) -> dict[str, Any]:
    """Process a single row to align cells based on active rowspans.

    Parameters
    ----------
    active_span
        List tracking remaining rowspan counts for each column.
    n_cols
        The total number of columns in the table.
    row
        The row object containing cells.

    Returns
    -------
    dict[str, Any]
        A dictionary of changes if modifications were made, otherwise empty.
    """

    old_cells = list(row.cells or [])
    new_cells: list[TableCell] = []
    col = 0

    # Fill cells and handle gaps.
    for cell in old_cells:
        # Insert placeholders for columns occupied by prior rowspans.
        while col < n_cols and active_span[col] > 0:
            new_cells.append(TableCell(col_span=1, row_span=1, text=None))
            col += 1

        if col >= n_cols:
            break

        new_cells.append(cell)

        # Update active_span for the current cell's dimensions.
        col_span = int(getattr(cell, "col_span", 1) or 1)
        row_span = int(getattr(cell, "row_span", 1) or 1)

        if row_span > 1:
            for dc in range(col_span):
                target_col = col + dc
                if target_col < n_cols:
                    active_span[target_col] = max(active_span[target_col], row_span)

        col += col_span

    # Fill trailing gaps if we haven't reached n_cols yet.
    while col < n_cols and active_span[col] > 0:
        new_cells.append(TableCell(col_span=1, row_span=1, text=None))
        col += 1

    # Trim excess placeholders.
    trimmed = _trim_excess_cells(n_cols=n_cols, new_cells=new_cells)

    # Return change summary if differences exist.
    if len(new_cells) != len(old_cells) or trimmed > 0:
        row.cells = new_cells
        return {
            "before_cells": len(old_cells),
            "after_cells": len(new_cells),
            "trimmed_trailing_placeholders": trimmed,
        }

    return {}


def _reconcile_item_state(
    *,
    item: Any,
    item_index: int,
    flags: list[bool],
    page_index: int,
    repeats_header_patch: dict[tuple[int, int], bool],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Update item boundary and table headers based on calculated flags.

    Parameters
    ----------
    item
        The PageIR item to update.
    item_index
        The index of the item on the page.
    flags
        The (from_prev, to_next) boolean flags.
    page_index
        The index of the page containing the item.
    repeats_header_patch
        Mapping of (page_idx, item_idx) to desired repeats_header boolean.

    Returns
    -------
    tuple[dict[str, Any] | None, dict[str, Any] | None]
        A tuple of (boundary_change, header_change) summaries, or None if no change.
    """

    from_prev, to_next = flags
    boundary_change = None
    header_change = None

    before_boundary = item.boundary
    after_boundary = _bools_to_boundary(from_prev, to_next)

    if before_boundary != after_boundary:
        boundary_change = {
            "page": page_index,
            "item_index": item_index,
            "before": getattr(before_boundary, "value", None),
            "after": after_boundary.value,
        }
        item.boundary = after_boundary

    # repeats_header only meaningful if table continues from prev.
    if item.kind == "table":
        table = item

        # Case A: Connection broken (not RESUMED or BOTH) --> Clear header.
        if item.boundary not in {ItemBoundary.RESUMED, ItemBoundary.BOTH}:
            if table.repeats_header is not None:
                header_change = {
                    "page": page_index,
                    "item_index": item_index,
                    "before": table.repeats_header,
                    "after": None,
                }
                table.repeats_header = None

        # Case B: Connection exists --> Apply patch if present.
        else:
            key = (page_index, item_index)
            if key in repeats_header_patch:
                desired = repeats_header_patch[key]
                if table.repeats_header != desired:
                    header_change = {
                        "page": page_index,
                        "item_index": item_index,
                        "before": table.repeats_header,
                        "after": desired,
                    }
                    table.repeats_header = desired

    return boundary_change, header_change


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


def _trim_excess_cells(*, n_cols: int, new_cells: list[TableCell]) -> int:
    """Remove trailing placeholders if the row exceeds the expected column count.

    Parameters
    ----------
    n_cols
        The target number of columns.
    new_cells
        The list of cells to trim (modified in place).

    Returns
    -------
    int
        The number of cells trimmed.
    """

    def _is_removable_placeholder(*, cell: TableCell) -> bool:
        """Check if a cell is an empty placeholder valid for trimming.

        Parameters
        ----------
        cell
            The cell to inspect.

        Returns
        -------
        bool
            True if the cell is a 1x1 empty placeholder.
        """

        col_span = int(getattr(cell, "col_span", 1) or 1)
        row_span = int(getattr(cell, "row_span", 1) or 1)
        text = getattr(cell, "text", None)

        return col_span == 1 and row_span == 1 and text is None

    trimmed = 0

    while len(new_cells) > n_cols and new_cells:
        if _is_removable_placeholder(cell=new_cells[-1]):
            new_cells.pop()
            trimmed += 1
        else:
            break

    return trimmed


def align_table_rows_with_rowspans(
    *, page_irs: dict[int, PageIR]
) -> list[dict[str, Any]]:
    """Insert placeholder empty cells where prior-row rowspans occupy columns. Fixes
    common failure mode where a row under a row-spanned subject shifts left.

    Parameters
    ----------
    page_irs
        Mapping of page_index to PageIR objects.

    Returns
    -------
    list[dict[str, Any]]
        A list of change summaries for rows that were modified.
    """

    changes: list[dict[str, Any]] = []

    for page_index in sorted(page_irs.keys()):
        page_ir = page_irs[page_index]

        for item_index, item in enumerate(page_ir.items or []):
            if item.kind != "table":
                continue

            table_changes = _process_table_item(
                item=item, item_index=item_index, page_index=page_index
            )
            changes.extend(table_changes)

    return changes


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
    """

    assert k >= 1, f"k must be >= 1, got {k}"

    # First candidate MUST match existing behavior.
    first_i, first_item = bottommost_continuity_candidate(
        image_height=image_height, items=items, visible_y_min=visible_y_min
    )

    # Recompute the same candidate pool used by bottommost_continuity_candidate.
    candidates: list[tuple[int, Block | Table]] = [
        (i, item)
        for i, item in enumerate(items)
        if not (
            is_artifact(item)
            or is_probable_header_footer_noise(image_height=image_height, item=item)
        )
    ]

    if visible_y_min is not None:
        y_min = float(visible_y_min)
        cropped = [
            (i, item)
            for i, item in candidates
            if _bbox_intersects_y_range(
                bbox=item.bbox, y_max=float(image_height), y_min=y_min
            )
        ]
        assert cropped, "No bottom-crop-visible candidates found."
        candidates = cropped

    assert candidates, "No non-artifact items found."

    # Sort by bottom-edge descending.
    candidates.sort(key=lambda c: float(c[1].bbox[3]), reverse=True)

    output: list[tuple[int, Block | Table]] = [(first_i, first_item)]
    seen: set[int] = {first_i}

    # Fill remaining slots with additional plausible near-bottom anchors.
    for i, item in candidates:
        if len(output) >= k:
            break
        if i in seen:
            continue

        # Always allow tables; for blocks avoid heading/caption as text anchors.
        if item.kind != "table" and (
            isinstance(item, Block)
            and item.block_type in {BlockType.CAPTION, BlockType.HEADING}
        ):
            continue

        output.append((i, item))
        seen.add(i)

    assert output, "No suitable continuity candidates found."
    return output


def bottommost_continuity_candidate(
    *,
    image_height: float,
    items: list[Block | Table],
    visible_y_min: float | None = None,
) -> tuple[int, Block | Table]:
    """Pick the best "bottom of page" candidate for continuity checks.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of PageIR items on the page.
    visible_y_min
        If provided, restrict candidate selection to items whose bbox intersects the
        visible crop range [visible_y_min, image_height] in full-page coordinates.

    Returns
    -------
    tuple[int, Block | Table]
        The index and item of the chosen bottom-most candidate.
    """

    # Filter candidates.
    candidates = [
        (i, item)
        for i, item in enumerate(items)
        if not (
            is_artifact(item)
            or is_probable_header_footer_noise(image_height=image_height, item=item)
        )
    ]

    # If we are verifying using a bottom-crop image, restrict candidates to items that
    # actually appear in that crop (in full-page coordinate space). This prevents
    # choosing an item the model cannot see, which would cause false negatives.
    if visible_y_min is not None:
        y_min = float(visible_y_min)
        cropped = [
            (i, item)
            for i, item in candidates
            if _bbox_intersects_y_range(
                bbox=item.bbox, y_max=float(image_height), y_min=y_min
            )
        ]
        assert cropped, "No bottom-crop-visible candidates found."
        candidates = cropped

    assert candidates, "No non-artifact items found."

    # Sort by bottom-edge (y1) descending (bbox is [x0, y0, x1, y1]).
    candidates.sort(key=lambda c: float(c[1].bbox[3]), reverse=True)

    # Weak prior: if the extractor flagged any items as TRUNCATED/BOTH, prefer those
    # as boundary candidates. (We still verify with the LLM; this only affects which
    # item we ask about.)
    preferred = [
        (i, item)
        for i, item in candidates
        if item.boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}
    ]

    def _pick(
        sorted_candidates: list[tuple[int, Block | Table]],
    ) -> tuple[int, Block | Table] | None:
        """Pick a candidate from an already y-sorted list, or return None.

        Parameters
        ----------
        sorted_candidates
            List of candidates sorted by bottom-edge descending.

        Returns
        -------
        tuple[int, Block | Table] | None
            The picked candidate index and item, or None if no suitable candidate.
        """

        # Prefer a Table if it is "near" the bottom (within the bottom 5 items).
        for i, item in sorted_candidates[:5]:
            if item.kind == "table":
                return i, item

        # Otherwise pick the first non-table block, but never anchor on HEADING/CAPTION.
        for i, item in sorted_candidates:
            if item.kind != "table":
                if isinstance(item, Block) and item.block_type in (
                    BlockType.CAPTION,
                    BlockType.HEADING,
                ):
                    continue

                return i, item

        return None

    # Try preferred candidates first; fall back to geometric selection if needed.
    picked = _pick(preferred)
    if picked is not None:
        return picked

    picked = _pick(candidates)
    if picked is not None:
        return picked

    # Last resort: take the absolute bottom item.
    return candidates[0]


def compile_continuity_from_edge_verdicts(
    *,
    edge_records: list[EdgeVerdictRecord],
    min_confidence_to_patch: float,
    page_irs: dict[int, PageIR],
) -> dict[str, Any]:
    """Apply all continuity decisions in one pass as follows:

    1. Positive edge --> set prev.to_next and next.from_prev.
    2. Negative edge --> clear ONLY that directional connection

    Then recompute ItemBoundary enums from bits and also enforce repeats_header
    consistency with boundary state.

    Parameters
    ----------
    edge_records
        List of edge verdict records.
    min_confidence_to_patch
        Minimum confidence threshold to apply edits.
    page_irs
        Mapping of page_index to PageIR objects.

    Returns
    -------
    dict[str, Any]
        A summary of applied edits.
    """

    # Initialize bools: (page_idx, item_idx) -> [from_prev, to_next].
    bools: dict[tuple[int, int], list[bool]] = {}

    # Initialize effective local codes.
    effective_local_codes: dict[tuple[int, int], str | None] = {}

    for page_index in sorted(page_irs):
        page = page_irs[page_index]
        for item_index, item in enumerate(page.items or []):
            fp, tn = _boundary_to_bools(item.boundary)
            bools[(page_index, item_index)] = [fp, tn]
            effective_local_codes[(page_index, item_index)] = _normalize_local_code(
                getattr(item, "local_code", None)
            )

    # Apply edge decisions (set or clear only that edge).
    applied_edges: list[dict[str, Any]] = []
    local_code_conflicts: list[dict[str, Any]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    for record in edge_records:
        summary = _apply_single_edge_verdict(
            bools=bools,
            effective_local_codes=effective_local_codes,
            local_code_conflicts=local_code_conflicts,
            local_code_patch=local_code_patch,
            min_confidence=min_confidence_to_patch,
            record=record,
            repeats_header_patch=repeats_header_patch,
        )
        applied_edges.append(summary)

    # Write boundaries back and enforce repeats_header consistency.
    boundary_changes: list[dict[str, Any]] = []
    repeats_header_changes: list[dict[str, Any]] = []

    for page_index, item_index in sorted(bools):
        flags = bools[(page_index, item_index)]
        item = (page_irs[page_index].items or [])[item_index]

        b_change, h_change = _reconcile_item_state(
            item=item,
            item_index=item_index,
            flags=flags,
            page_index=page_index,
            repeats_header_patch=repeats_header_patch,
        )

        if b_change:
            boundary_changes.append(b_change)
        if h_change:
            repeats_header_changes.append(h_change)

    # Apply local_code patches.
    local_code_changes: list[dict[str, Any]] = []

    for (page_index, item_index), code in local_code_patch.items():
        item = (page_irs[page_index].items or [])[item_index]
        before = _normalize_local_code(getattr(item, "local_code", None))
        if before is None:
            item.local_code = code
            local_code_changes.append(
                {
                    "page": page_index,
                    "item_index": item_index,
                    "before": None,
                    "after": code,
                }
            )

    return {
        "applied_edges": applied_edges,
        "boundary_changes": boundary_changes,
        "local_code_changes": local_code_changes,
        "local_code_conflicts": local_code_conflicts,
        "repeats_header_changes": repeats_header_changes,
    }


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


def derive_page_boundary_state(*, page_ir: PageIR) -> PageBoundaryState:
    """Derive page-level boundary_state from verified item boundaries.

    Parameters
    ----------
    page_ir
        The page IR dictionary.

    Returns
    -------
    PageBoundaryState
        The derived page-level boundary state.
    """

    items = page_ir.items or []
    image_height = page_ir.image_height

    candidates = [
        item
        for item in items
        if not is_artifact(item)
        and not is_probable_header_footer_noise(image_height=image_height, item=item)
    ]

    if not candidates:
        return PageBoundaryState.STANDALONE

    from_prev = any(
        item.boundary.value in (ItemBoundary.RESUMED.value, ItemBoundary.BOTH.value)
        for item in candidates
    )
    to_next = any(
        item.boundary.value in (ItemBoundary.TRUNCATED.value, ItemBoundary.BOTH.value)
        for item in candidates
    )

    if from_prev and to_next:
        return PageBoundaryState.BOTH
    if from_prev:
        return PageBoundaryState.CONTINUES_FROM_PREV
    if to_next:
        return PageBoundaryState.CONTINUES_TO_NEXT
    return PageBoundaryState.STANDALONE


def execute_verification_attempts(
    *,
    config: VerificationConfig,
    page_images_dir: Path,
    page_index: int,
    pairs: list[tuple[int, Block | Table, int, Block | Table]],
    next_crop_fp: Path,
) -> dict[str, Any]:
    """Run model verification on the list of pairs until a match is found or list
    exhausted.

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
    primary_verdict: PageIRContinuityVerdict | None = None

    # Default selection is the first pair (primary).
    selected_prev_index, selected_next_index = pairs[0][0], pairs[0][2]
    selected_verdict: PageIRContinuityVerdict | None = None

    # For each candidate pair:
    #  - Strip existing boundary hints (so model isn't biased from extraction).
    #  - Call the model to verify continuity.
    #  - Record the attempt summary.
    #  - If a high confidence patch is found, break early.
    for attempt_no, (pi, pitem, ni, nitem) in enumerate(pairs):
        try:
            verdict = verify_page_ir_pairs(
                force_llm_retry_on_first_attempt=config.force_llm_retry_on_first_attempt,
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
            )
        except Exception as e:  # pylint: disable=broad-except
            attempt_summaries.append(
                {
                    "attempt_no": attempt_no,
                    "prev_candidate_index": pi,
                    "next_candidate_index": ni,
                    "error": str(e),
                }
            )
            continue

        attempt_summaries.append(
            {
                "attempt_no": attempt_no,
                "prev_candidate_index": pi,
                "next_candidate_index": ni,
                "is_continuation": verdict.is_continuation,
                "continuation_kind": verdict.continuation_kind.value,
                "confidence": verdict.confidence,
                "set_next_table_repeats_header": verdict.set_next_table_repeats_header,
            }
        )

        # Capture primary verdict.
        if attempt_no == 0:
            primary_verdict = verdict

        # Early exit on high confidence to patch.
        if verdict.confidence >= config.min_confidence_to_patch:
            selected_prev_index, selected_next_index = pi, ni
            selected_verdict = verdict
            break

    # If we didn't find a high confidence patch, fall back to the primary pair verdict.
    selected_verdict = selected_verdict or primary_verdict
    assert selected_verdict, f"No high confidence patches found for: {pairs}"

    return {
        "attempt_summaries": attempt_summaries,
        "selected_verdict": selected_verdict,
        "selected_prev_index": selected_prev_index,
        "selected_next_index": selected_next_index,
    }


def find_caption_code(items: list[Block | Table]) -> str | None:
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

    for item in items:
        if item.kind == "block" and item.block_type == BlockType.CAPTION:
            # Prefer extractor-provided local_code.
            code = (item.local_code or "").strip()
            if code and TABLE_CODE_RE.match(code):
                return code

            # Fallback: parse from caption text if local_code missing.
            text = (
                (item.text.text or "").strip()
                if isinstance(item.text, TextUnit)
                else ""
            )
            if text and (m := TABLE_CODE_RE.match(text)) is not None:
                # Normalize to canonical "Table {num}" even if original language was
                # "Tableau/Jedwali/Tab."
                return f"Table {m.group('num')}"

    return None


def fix_false_truncated_prose_before_table(
    *, page_irs: dict[int, PageIR]
) -> list[dict[str, Any]]:
    """If a page ends with a truncated prose block but the next page starts with a
    table/caption and there is no resumed prose block, clear the truncation when the
    prose appears complete. This repairs false-positive prose truncations at section
    breaks into tables.

    Parameters
    ----------
    page_irs
        Mapping of page_index to PageIR objects.

    Returns
    -------
    list[dict[str, Any]]
        A list of change summaries for items that were modified.
    """

    changes: list[dict[str, Any]] = []
    page_indices = sorted(page_irs.keys())

    for i in range(len(page_indices) - 1):
        p_idx = page_indices[i]
        n_idx = page_indices[i + 1]
        prev = page_irs[p_idx]
        nxt = page_irs[n_idx]

        prev_items = [it for it in (prev.items or []) if not is_artifact(it)]
        nxt_items = [it for it in (nxt.items or []) if not is_artifact(it)]

        if not prev_items or not nxt_items:
            continue

        last_prev = prev_items[-1]
        first_next = nxt_items[0]

        # Only consider prose blocks marked truncated/both.
        if (
            last_prev.kind != "block"
            or (last_prev.boundary not in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH})
            or (last_prev.block_type not in {BlockType.PARAGRAPH, BlockType.LIST})
        ):
            continue

        # Only fire when next page starts with a table/caption.
        next_is_tableish = first_next.kind == "table" or (
            first_next.kind == "block" and first_next.block_type == BlockType.CAPTION
        )

        if not next_is_tableish:
            continue

        # If next page has a resumed prose block early on, let it be a real
        # continuation.
        has_resumed_prose = any(
            item.kind == "block"
            and item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
            and item.block_type in {BlockType.PARAGRAPH, BlockType.LIST}
            for item in (nxt.items or [])[:6]
        )

        if has_resumed_prose:
            continue

        # If the prose ends cleanly, treat as complete.
        text_or_none = last_prev.text
        text = (
            (text_or_none.text or "").strip()
            if isinstance(text_or_none, TextUnit)
            else ""
        )
        ends_cleanly = bool(
            re.search(r"[.!?]['\"\)]?\s*$", text)
        ) and not text.endswith("-")

        if not ends_cleanly:
            continue

        old = last_prev.boundary
        last_prev.boundary = ItemBoundary.COMPLETE
        prev.boundary_state = derive_page_boundary_state(page_ir=prev)

        changes.append(
            {
                "type": "clear_false_truncated_prose_before_table",
                "page": p_idx,
                "old_boundary": old.value,
                "new_boundary": last_prev.boundary.value,
            }
        )

    return changes


def generate_candidate_pairs(
    *, next_crop_fp: Path, next_page_ir: PageIR, prev_page_ir: PageIR
) -> tuple[list[tuple[int, Block | Table, int, Block | Table]], dict[str, int]]:
    """Generate and deduplicate the list of candidate pairs to verify.

    Parameters
    ----------
    next_crop_fp
        Filepath to the cropped image of the next page.
    next_page_ir
        The PageIR of the next page.
    prev_page_ir
        The PageIR of the previous page.

    Returns
    -------
    tuple[list[tuple[int, Block | Table, int, Block | Table]], dict[str, int]]
        A tuple of (candidate pairs list, primary indices dict).
    """

    prev_items = prev_page_ir.items or []
    next_items = next_page_ir.items or []

    # Build candidate pools. Instead of only one bottom item, get a small list of
    # plausible bottom items. `bottom_continuity_candidates()` guarantees the
    # bottommost primary candidate is first (deterministic), followed by near-bottom
    # alternatives in case the true continuation anchor isn't the bottommost one.
    prev_candidates = bottom_continuity_candidates(
        image_height=prev_page_ir.image_height, items=prev_items
    )
    prev_index, prev_item = prev_candidates[0]

    # Get top candidates on next page.
    visible_y_max = float(pymupdf.Pixmap(str(next_crop_fp)).height)
    next_candidates_primary = top_continuity_candidates_paired(
        image_height=next_page_ir.image_height,
        items=next_items,
        prev_item=prev_item,
        visible_y_max=visible_y_max,
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
                visible_y_max=visible_y_max,
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

    primary_indices = {
        "prev_candidate_index": prev_index,
        "next_candidate_index": next_index,
    }

    return deduped_pairs, primary_indices


def is_artifact(item: Block | Table) -> bool:
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

    return False if item.kind != "block" else item.block_type == BlockType.ARTIFACT


def is_probable_header_footer_noise(
    *, image_height: float, item: Block | Table
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

    if item.kind != "block":
        return False

    text_or_none = item.text
    text = text_or_none.text.strip() if isinstance(text_or_none, TextUnit) else ""

    if not text:
        return False

    # Very small box height is usually a strong cue (page numbers, running headers).
    bbox = item.bbox
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
    if (len(t) <= 12 and re.fullmatch(r"(\d+|[ivxlcdm]+)", t.lower())) or (
        len(t) <= 20 and re.fullmatch(r"(page\s*)?\d+(\s*/\s*\d+)?", t.lower())
    ):
        return True

    return False


def load_page_irs_from_verification(
    *, doc_key: str, verified_page_irs_dir: Path
) -> list[PageIR]:
    """Load and validate all verified page IR JSONs from the verification output
    directory.

    Parameters
    ----------
    doc_key
        The document key for all page IRs.
    verified_page_irs_dir
        Directory containing the verified page IR JSONs.

    Returns
    -------
    list[PageIR]
        The loaded and validated PageIRs in filename order.

    Raises
    ------
    FileNotFoundError
        If no verified page IR JSON files are found in the specified directory.
    ValueError
        If any verified PageIR is missing page_index.
        If the page_index sequence is non-contiguous or does not start at 0.
        If there are inconsistent doc_key or pdf_name values across pages.
        If there are inconsistent coord_space, dpi, image_width, or image_height
            values across pages.
    """

    json_files = sorted(verified_page_irs_dir.glob("*.json"))

    if not json_files:
        raise FileNotFoundError(
            f"No verified page IR JSON files found in: {verified_page_irs_dir}"
        )

    page_irs: list[PageIR] = [
        PageIR.model_validate(open_json_type(fp)) for fp in json_files
    ]

    # Validate page_index exists and sort by it (not filename).
    if any(p.page_index is None for p in page_irs):
        raise ValueError(
            "One or more verified PageIRs are missing page_index. Cannot stitch reliably."
        )

    page_irs.sort(key=lambda p: p.page_index)
    page_indexes = [p.page_index for p in page_irs]
    expected = list(range(len(page_irs)))

    if page_indexes != expected:
        raise ValueError(
            f"Non-contiguous page_index sequence. Got {page_indexes[:10]}..."
        )

    # Validate doc_key consistency + presence.
    if doc_key is None:
        raise ValueError(
            "extraction_run.json is missing extra.doc_key (expected_doc_key)."
        )

    doc_keys = {p.doc_key for p in page_irs if p.doc_key}
    pdf_names = {p.pdf_name for p in page_irs if p.pdf_name}

    if not doc_keys:
        raise ValueError(
            "All verified PageIRs are missing doc_key. "
            "Ensure extraction step populates PageIR.doc_key for every page."
        )
    if len(doc_keys) > 1 or len(pdf_names) > 1:
        raise ValueError(
            "Inconsistent pdf_name or doc_key across pages:\n"
            f"{sorted(doc_keys)}\n{sorted(pdf_names)}"
        )

    only_doc_key = next(iter(doc_keys))
    if only_doc_key != doc_key:
        raise ValueError(f"Expected doc_key '{doc_key}', got '{only_doc_key}'")

    # Validate coordinate space, dimensions, and dpi consistency/presence.
    coord_spaces = {p.coord_space for p in page_irs if p.coord_space is not None}
    dpis = {p.dpi for p in page_irs if p.dpi is not None}
    heights = {p.image_height for p in page_irs if p.image_height is not None}
    widths = {p.image_width for p in page_irs if p.image_width is not None}

    if len(coord_spaces) > 1 or len(dpis) > 1:
        raise ValueError(
            "Inconsistent coordinate spaces or DPIs across pages:\n"
            f"{coord_spaces=}\n{dpis=}\n{widths=}\n{heights=}"
        )

    if (
        any(p.dpi is None for p in page_irs)
        or any(p.image_width is None for p in page_irs)
        or any(p.image_height is None for p in page_irs)
    ):
        raise ValueError(
            "One or more verified PageIRs are missing dpi, image_width, or image_height."
        )

    return page_irs


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


def normalize_empty_table_cells_to_null(
    *, page_irs: dict[int, PageIR]
) -> list[dict[str, Any]]:
    """Convert visually-empty table cell text like '' / ' ' / '\\n' into text=None.
    This stabilizes rowspan logic and downstream canonicalization by making emptiness
    explicit.

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

    for page_index in sorted(page_irs.keys()):
        page_ir = page_irs[page_index]
        for item_index, item in enumerate(page_ir.items or []):
            if item.kind != "table":
                continue

            for row_index, row in enumerate(item.rows or []):
                for cell_index, cell in enumerate(row.cells or []):
                    text_or_none = cell.text
                    if (
                        isinstance(text_or_none, TextUnit)
                        and not (text_or_none.text or "").strip()
                    ):
                        cell.text = None
                        changes.append(
                            {
                                "type": "empty_string_cell_to_null",
                                "page": page_index,
                                "item_index": item_index,
                                "row_index": row_index,
                                "cell_index": cell_index,
                            }
                        )

    return changes


def normalize_table_row_cell_counts(
    *, page_irs: dict[int, PageIR]
) -> list[dict[str, Any]]:
    """Ensure each table row has exactly n_cols cells. Fixes the common LLM error of
    dropping empty cells at the start/end of rows.

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

    for page_index in sorted(page_irs.keys()):
        page_ir = page_irs[page_index]
        for item_index, item in enumerate(page_ir.items or []):
            if item.kind != "table":
                continue

            # We only fix tables where the LLM explicitly stated the column count.
            n_cols = item.n_cols
            if not isinstance(n_cols, int) or n_cols <= 0:
                continue

            rows = item.rows or []
            for row_index, row in enumerate(rows):
                cells = row.cells or []

                # Count effective columns, respecting merged cells.
                effective_cols = sum((cell.col_span or 1) for cell in cells)

                # Keep as is if the row already covers the table width (including
                # merged cells).
                if effective_cols >= n_cols:
                    continue

                missing = n_cols - effective_cols

                # Heuristic: left vs. right padding. If the first cell contains a code
                # (e.g., "3.2"), the missing cells are likely leading empty columns
                # (Subject/Competency columns). Otherwise, we assume they are trailing
                # empty columns.
                first_text = ""
                if cells and cells[0].text:
                    first_text = cells[0].text.text or ""

                # Regex for "1.2", "A.1", "3.2.1" at start of string.
                codeish = bool(
                    re.search(r"(^|\n)\s*[A-Z0-9]+(\.[A-Z0-9]+)+", first_text)
                )
                pad = [
                    TableCell(col_span=1, row_span=1, text=None) for _ in range(missing)
                ]
                row.cells = (pad + cells) if codeish else (cells + pad)
                changes.append(
                    {
                        "after": n_cols,
                        "before_cells": len(cells),
                        "before_effective_cols": effective_cols,
                        "item_index": item_index,
                        "page": page_index,
                        "row_index": row_index,
                        "side": "left" if codeish else "right",
                        "type": "pad_table_row_cells",
                    }
                )

    return changes


def persist_verification_run(
    *, config: VerificationConfig, output_dir: Path
) -> tuple[PageIRVerificationDirs, RunCtx]:
    """Persist verification run metadata.

    Parameters
    ----------
    config
        The verification run configuration.
    output_dir
        The output directory for the verification run results.

    Returns
    -------
    tuple[PageIRVerificationDirs, RunCtx]
        The created verification directories and persisted verification run metadata.
    """

    verification_dirs = create_page_ir_verification_dirs(output_dir=output_dir)
    verification_run = RunCtx(
        extra={
            "end_page_cli": config.end_page,  # Keep original config value (may be None)
            "min_confidence_to_patch": config.min_confidence_to_patch,
            "start_page_cli": config.start_page,
        },
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "verification_run.json", json_info=verification_run)
    logger.info(f"Saving verification results to: {verification_dirs.root}")

    return verification_dirs, verification_run


def postprocess_verified_page_irs(
    *, page_irs: dict[int, PageIR], verification_dirs: PageIRVerificationDirs
) -> None:
    """Run all postpass fixes before writing verified JSONs.

    NB: Order matters here. Don't change unless you know what you are doing!

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.
    verification_dirs
        The verification directories.
    """

    # Fix false truncated prose before tables.
    prose_table_fix_changes = fix_false_truncated_prose_before_table(page_irs=page_irs)

    # Enrich data by flowing local codes across the now-verified boundaries.
    table_code_changes = propagate_table_local_codes(page_irs=page_irs)

    # Normalize empty-string cells into explicit nulls.
    empty_cell_changes = normalize_empty_table_cells_to_null(page_irs=page_irs)

    # Insert placeholders under rowspans to prevent column drift.
    rowspan_alignment_changes = align_table_rows_with_rowspans(page_irs=page_irs)

    # Fix structural "empty cell" hallucinations from the extraction model.
    pad_changes = normalize_table_row_cell_counts(page_irs=page_irs)

    # Persist what was changed for audit/debug.
    write_to_json(
        fp=verification_dirs.root / "postprocess_report.json",
        json_info={
            "prose_table_fix_changes": prose_table_fix_changes,
            "table_local_code_changes": table_code_changes,
            "empty_cell_null_changes": empty_cell_changes,
            "rowspan_alignment_changes": rowspan_alignment_changes,
            "table_row_padding_changes": pad_changes,
        },
    )


def propagate_table_local_codes(*, page_irs: dict[int, PageIR]) -> list[dict[str, Any]]:
    """Carry forward "Table X" codes across VERIFIED continuation boundaries.

    This post-pass is intentionally conservative:

    1. Only propagate codes *across page boundaries* when the verification step has
        marked the table as RESUMED/BOTH (on this page) and TRUNCATED/BOTH (on the
        prior page).
    2. Only carry *one* code forward: the code of the **last table in reading order**
        on the page that continues onto the next page (TRUNCATED/BOTH).
    3. Never let later, non-continuing tables overwrite the carry-forward code.

    In short, this function "guarantees" the code that is propagated to page N+1 is the
    code of the table that actually continues off page N, and it prevents a later
    complete/misclassified “table-ish” object from overwriting that carry-forward value.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.

    Returns
    -------
    list[dict[str, Any]]
        A list of changes made during the postpass.
    """

    carry_from_prev: str | None = None
    changes: list[dict[str, Any]] = []

    for page_idx in sorted(page_irs.keys()):
        page = page_irs[page_idx]
        items = page.items or []

        # If the previous page "carried" a table code, but this page doesn't actually
        # resume a table, drop it so it can't block caption seeding or future logic.
        if carry_from_prev is not None and not any(
            item.kind == "table"
            and item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
            for item in items
        ):
            carry_from_prev = None

        # Seed carry_from_prev from a caption *only if* we don't already have carry
        # from the previous page. This supports patterns like "Table 3 (continued)" at
        # the top of the page when the table itself is missing a code.
        #
        # NB: Only look at captions that appear *before the first table item* to avoid
        # accidentally grabbing a caption for a later, unrelated table/rubric.
        if carry_from_prev is None and any(
            item.kind == "table"
            and item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
            and not (item.local_code or "").strip()
            for item in items
        ):
            first_relevant_table_index = next(
                (
                    j
                    for j, item in enumerate(items)
                    if item.kind == "table"
                    and item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
                    and not (item.local_code or "").strip()
                ),
                None,
            )
            caption_scope = (
                items[:first_relevant_table_index]
                if first_relevant_table_index is not None
                else []
            )
            caption_code = find_caption_code(caption_scope)
            if caption_code:
                carry_from_prev = caption_code.strip() or None

        carry_to_next: str | None = None

        # Ensure we only apply carry_from_prev once on this page (if multiple resumed
        # tables exist, we can't disambiguate with a single code; leave the rest
        # unresolved).
        applied_prev_carry = False

        for item_index, item in enumerate(items):
            if item.kind != "table":
                continue

            boundary = item.boundary
            is_resumed = boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
            is_truncated = boundary in {ItemBoundary.TRUNCATED, ItemBoundary.BOTH}
            code = (item.local_code or "").strip()

            # Fill missing code on a resumed table from the previous page's carry.
            if is_resumed and not code and carry_from_prev and not applied_prev_carry:
                item.local_code = carry_from_prev
                code = carry_from_prev
                applied_prev_carry = True
                changes.append(
                    {
                        "item_index": item_index,
                        "page": page_idx,
                        "set_local_code": carry_from_prev,
                        "type": "propagate_table_local_code",
                    }
                )

            # Decide what we carry forward to the NEXT page: only the code of the table
            # that actually continues off this page (TRUNCATED/BOTH). If multiple
            # tables continue, we carry the last one in reading order.
            if is_truncated and code:
                carry_to_next = code

        # Carry forward only the table that continues onto the next page.
        carry_from_prev = carry_to_next

    return changes


def save_verified_page_irs(
    *, page_irs: dict[int, PageIR], verification_dirs: PageIRVerificationDirs
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

        # Derive page-level boundary_state from verified item boundaries.
        page_ir.boundary_state = derive_page_boundary_state(page_ir=page_ir)

        # Write verified JSON.
        write_to_json(
            fp=verification_dirs.page_irs_verified / f"{i:04}.json", json_info=page_ir
        )

    logger.success("All verified page IR JSONs saved successfully!")


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
    """

    assert k >= 1, f"k must be >= 1, got {k}"

    # First candidate MUST match existing behavior.
    first_i, first_item = topmost_continuity_candidate_paired(
        image_height=image_height,
        items=items,
        prev_item=prev_item,
        visible_y_max=visible_y_max,
    )

    # Recompute the same candidate pool used by topmost_continuity_candidate_paired.
    candidates: list[tuple[int, Block | Table]] = [
        (i, item)
        for i, item in enumerate(items)
        if not (
            is_artifact(item)
            or is_probable_header_footer_noise(image_height=image_height, item=item)
        )
    ]

    if visible_y_max is not None:
        cropped = [
            (i, item)
            for i, item in candidates
            if _bbox_intersects_y_range(bbox=item.bbox, y_max=visible_y_max, y_min=0.0)
        ]
        candidates = cropped or candidates
        assert (
            candidates
        ), f"No top-crop-visible candidates found.\n{candidates = }\n{cropped = }\n{visible_y_max = }"

    assert candidates, "No non-artifact items found."

    # Sort by top-edge ascending.
    candidates.sort(key=lambda p: float(p[1].bbox[1]))

    output: list[tuple[int, Block | Table]] = [(first_i, first_item)]
    seen: set[int] = {first_i}

    # Prefer same-kind first, then cross-kind, while keeping reading-order stability.
    same_kind: list[tuple[int, Block | Table]] = []
    other_kind: list[tuple[int, Block | Table]] = []

    for i, item in candidates:
        if i in seen:
            continue

        # For blocks, avoid heading/caption as text anchors.
        if item.kind != "table" and (
            isinstance(item, Block)
            and item.block_type in {BlockType.CAPTION, BlockType.HEADING}
        ):
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

    assert output, "No suitable continuity candidates found."
    return output


def topmost_continuity_candidate_paired(
    *,
    image_height: float,
    items: list[Block | Table],
    prev_item: Block | Table,
    visible_y_max: float | None = None,
) -> tuple[int, Block | Table]:
    """Pick the best "top of page" candidate, preferring the same kind as prev_item.

    The process is as follows:

    1. Filter out artifacts and noise.
    2. Sort by top edge (y0) ascending (closest to top first).
    3. Scan the top items:
        - If we find an item of the SAME kind as prev_item (Table/Block), return it.
        - This allows us to skip over a heading/caption to link Table-to-Table, or skip
            over a top-aligned Table to link Text-to-Text.
    4. Fallback: Return the absolute top-most item.

    Parameters
    ----------
    image_height
        The height of the page image in pixels.
    items
        List of items to search.
    prev_item
        The chosen previous page candidate item.
    visible_y_max
        If provided, restrict candidate selection to items whose bbox intersects the
        visible crop range [0, visible_y_max] in full-page coordinates.

    Returns
    -------
    tuple[int, Block | Table]
        The index and the chosen item.

    Raises
    ------
    ValueError
        If no non-artifact items are found.
        If no top-crop-visible candidates are found when visible_y_max is provided.
    """

    candidates = [
        (i, item)
        for i, item in enumerate(items)
        if not (
            is_artifact(item)
            or is_probable_header_footer_noise(image_height=image_height, item=item)
        )
    ]

    # If we are verifying using a top-crop image, restrict candidates to items that
    # actually appear in that crop (in full-page coordinate space). This prevents
    # choosing an item the model cannot see, which would cause false negatives.
    if visible_y_max is not None:
        cropped = [
            (i, item)
            for i, item in candidates
            if _bbox_intersects_y_range(bbox=item.bbox, y_max=visible_y_max, y_min=0.0)
        ]
        candidates = cropped or candidates
        assert (
            candidates
        ), f"No top-crop-visible candidates found.\n{candidates = }\n{cropped = }\n{visible_y_max = }"

    assert candidates, "No non-artifact items found."

    # Sort by top-edge (y0) ascending (bbox is [x0, y0, x1, y1]).
    candidates.sort(key=lambda p: float(p[1].bbox[1]))

    # Weak prior: if the extractor flagged any items as RESUMED/BOTH, prefer those as
    # next-page boundary candidates. We still verify with the LLM; this only affects
    # which item we ask about.
    preferred = [
        (i, item)
        for i, item in candidates
        if item.boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
    ]

    # If prev ended with a Table, prefer to resume a Table.
    if prev_item.kind == "table":
        # Create a combined stream of items from preferred and candidates, opting for
        # preferred and filtering on items that are tables.
        table_search = (
            (i, item)
            for source in (preferred, candidates)
            for i, item in source
            if item.kind == "table"
        )

        # Return the first found table or default to candidates[0].
        return next(table_search, candidates[0])

    # Otherwise (prev ended with a Block), pick the first non-table Block near the top,
    # but never anchor text continuation on a HEADING/CAPTION.
    valid_items = (
        (i, item)
        for source in (preferred, candidates)
        for i, item in source
        if item.kind != "table"
        and not (
            isinstance(item, Block)
            and item.block_type in {BlockType.CAPTION, BlockType.HEADING}
        )
    )

    # Return the first match or default to candidates[0].
    return next(valid_items, candidates[0])


def verify_single_page_pair(
    *,
    config: VerificationConfig,
    page_images_dir: Path,
    page_index: int,
    page_irs: dict[int, PageIR],
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

    # Skip if either page has no items
    if not (prev_page_ir.items and next_page_ir.items):
        logger.warning(
            f"Skipping continuity check for pages {page_index}-{page_index + 1}: "
            f"prev_items={len(prev_page_ir.items)} "
            f"next_items={len(next_page_ir.items)}"
        )
        return None

    # Crop top of next image (page N+1) and compute visible range.
    next_crop_fp = (
        verification_dirs.page_irs_pair_crops / f"{page_index + 1:04}_top.png"
    )
    crop_image_to_top(
        input_png_fp=page_images_dir / f"{page_index + 1:04}.png",
        output_png_fp=next_crop_fp,
    )

    pairs, primary_indices = generate_candidate_pairs(
        next_crop_fp=next_crop_fp, next_page_ir=next_page_ir, prev_page_ir=prev_page_ir
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
    )

    # Record the edge verdict (single selected pair per boundary).
    selected_verdict = result["selected_verdict"]
    selected_verdict.prev_page_index = page_index
    selected_verdict.next_page_index = page_index + 1
    record = EdgeVerdictRecord(
        next_candidate_index=result["selected_next_index"],
        next_page_index=page_index + 1,
        prev_candidate_index=result["selected_prev_index"],
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
                "prev_candidate_index": result["selected_prev_index"],
                "next_candidate_index": result["selected_next_index"],
            },
            "verdict": selected_verdict.model_dump(mode="json"),
        },
    )

    logger.success(
        f"Finished verifying continuity between pages {page_index} and {page_index + 1}!"
    )

    return record
