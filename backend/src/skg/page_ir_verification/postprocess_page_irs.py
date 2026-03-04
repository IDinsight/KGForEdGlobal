"""This module contains utility functions related to post-processing verified page IRs."""

# Standard Library
import re

from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TableCell, TextUnit
from skg.page_ir_verification.utils import (
    PageIRVerificationDirs,
    derive_page_boundary_state,
    is_artifact,
)
from skg.utils.constants import BlockType, CaptionTablePrefixes, ItemBoundary
from skg.utils.general import write_to_json

# Compiled regexes.
TABLE_PREFIX_RE = "|".join(re.escape(t) for t in CaptionTablePrefixes)
TABLE_CODE_RE = re.compile(
    rf"^\s*(?:{TABLE_PREFIX_RE})\s+(?P<num>\d+(?:\.\d+)*)\b", re.IGNORECASE
)


def _get_header_effective_cols(*, header_row_count: int, rows: list[Any]) -> int:
    """Calculate the max width found in the header rows.

    Parameters
    ----------
    header_row_count
        The number of header rows in the table.
    rows
        The list of all rows in the table.

    Returns
    -------
    int
        The maximum effective column count found in the header rows, accounting for
        col_span.
    """

    return max(
        (
            sum((c.col_span or 1) for c in (r.cells or []))
            for r in rows[:header_row_count]
        ),
        default=0,
    )


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


def _process_table_normalization(
    *, item: Block | Table, item_index: int, page_index: int
) -> list[dict[str, Any]]:
    """Analyze a single table and normalizes its rows.

    Parameters
    ----------
    item
        The table item to process.
    item_index
        The index of the item on the page.
    page_index
        The index of the page containing the item.

    Returns
    -------
    list[dict[str, Any]]
        A list of changes made to the table rows.
    """

    n_cols = item.n_cols

    if not isinstance(n_cols, int) or n_cols <= 0:
        return []

    rows = item.rows or []
    header_row_count = int(item.header_row_count or 0)

    # Determine padding strategy based on table signals.
    pad_left = _should_pad_left(
        header_row_count=header_row_count, n_cols=n_cols, rows=rows
    )

    if pad_left:
        is_header_full = (
            _get_header_effective_cols(header_row_count=header_row_count, rows=rows)
            == n_cols
        )
        side_reason = "header_full_width" if is_header_full else "modal_leading_blank"
    else:
        side_reason = "default_right"

    table_changes = []

    # Apply normalization to rows.
    for row_index, row in enumerate(rows):
        cells = row.cells or []
        effective_cols = sum((cell.col_span or 1) for cell in cells)

        # Record over-wide rows (common in messy PDFs/extraction noise).
        if effective_cols > n_cols:
            before_cells = len(cells)
            before_effective = effective_cols

            # If the overflow is just trailing empty placeholders, trim them.
            trimmed = _trim_excess_cells(n_cols=n_cols, new_cells=cells)

            after_cells = len(cells)
            after_effective = sum((cell.col_span or 1) for cell in cells)

            table_changes.append(
                {
                    "type": "table_row_effective_cols_exceeds_n_cols",
                    "page": page_index,
                    "item_index": item_index,
                    "row_index": row_index,
                    "n_cols": n_cols,
                    "before_cells": before_cells,
                    "after_cells": after_cells,
                    "before_effective_cols": before_effective,
                    "after_effective_cols": after_effective,
                    "trimmed_trailing_placeholders": trimmed,
                }
            )

            # We do not attempt destructive fixes beyond trimming empty placeholders.
            continue

        if effective_cols == n_cols:
            continue

        missing = n_cols - effective_cols
        padding = [TableCell(col_span=1, row_span=1, text=None) for _ in range(missing)]

        # Apply the fix.
        row.cells = (padding + cells) if pad_left else (cells + padding)

        table_changes.append(
            {
                "after": n_cols,
                "before_cells": len(cells),
                "before_effective_cols": effective_cols,
                "item_index": item_index,
                "page": page_index,
                "row_index": row_index,
                "side": "left" if pad_left else "right",
                "side_reason": side_reason,
                "type": "pad_table_row_cells",
            }
        )

    return table_changes


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


def _should_pad_left(*, header_row_count: int, n_cols: int, rows: list) -> bool:
    """Decide if the table requires left-padding based on header and body signals.

    Parameters
    ----------
    header_row_count
        The number of header rows in the table.
    n_cols
        The expected number of columns in the table.
    rows
        The list of table rows.

    Returns
    -------
    bool
        True if left-padding is needed, False if right-padding or no padding is
        preferred.
    """

    # Header covers full width.
    header_effective_cols = _get_header_effective_cols(
        header_row_count=header_row_count, rows=rows
    )
    if header_row_count > 0 and header_effective_cols == n_cols:
        return True

    # Modal leading blank.
    rows_for_modal = rows[header_row_count:] if header_row_count > 0 else rows

    full_width_rows_cells = [
        cs
        for r in rows_for_modal
        if (cs := r.cells or []) and sum((c.col_span or 1) for c in cs) >= n_cols
    ]

    if not full_width_rows_cells:
        return False

    leading_blank_count = sum(
        1
        for cs in full_width_rows_cells
        if cs[0].text is None or (cs[0].text.text or "").strip() == ""
    )

    return (
        len(full_width_rows_cells) >= 3
        and (leading_blank_count / len(full_width_rows_cells)) >= 0.6
    )


def _trim_excess_cells(*, n_cols: int, new_cells: list[TableCell]) -> int:
    """Remove trailing placeholders if the row exceeds the expected column count.

    NB: We trim based on *effective* columns (sum of col_span), not raw cell count.
    This avoids incorrect trimming when some real cells have col_span > 1.

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

    def _is_removable_placeholder(cell: TableCell) -> bool:
        """True if the cell is a 1x1 empty placeholder.

        Parameters
        ----------
        cell
            The cell to check.

        Returns
        -------
        bool
            True if the cell is a removable placeholder, False otherwise.
        """

        col_span = int(getattr(cell, "col_span", 1) or 1)
        row_span = int(getattr(cell, "row_span", 1) or 1)
        text = getattr(cell, "text", None)
        return col_span == 1 and row_span == 1 and text is None

    trimmed = 0
    effective_cols = sum(int(getattr(c, "col_span", 1) or 1) for c in new_cells)

    # Only trim *empty placeholders* and only until effective_cols fits n_cols.
    while effective_cols > n_cols and new_cells:
        tail = new_cells[-1]

        if _is_removable_placeholder(tail):
            new_cells.pop()
            trimmed += 1
            effective_cols -= 1  # Placeholder is guaranteed col_span==1 above
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

    for page_index, page_ir in sorted(page_irs.items()):
        for item_index, item in enumerate(page_ir.items or []):
            if item.kind != "table":
                continue

            changes.extend(
                _process_table_normalization(
                    item=item, item_index=item_index, page_index=page_index
                )
            )

    return changes


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

    logger.success(
        f"Saved postprocess report with "
        f"{len(prose_table_fix_changes)} prose-table fixes, "
        f"{len(table_code_changes)} table code propagations, "
        f"{len(empty_cell_changes)} empty cell normalizations, "
        f"{len(rowspan_alignment_changes)} rowspan alignment changes, and "
        f"{len(pad_changes)} table row padding changes to: "
        f"{verification_dirs.root / 'postprocess_report.json'}"
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
