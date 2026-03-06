"""This module contains utility functions related to post-processing verified page IRs."""

# Standard Library
import json
import re

from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import (
    Block,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification.utils import PageIRVerificationDirs, is_artifact
from skg.utils.constants import BlockType, CaptionTablePrefixes
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
        (sum(c.col_span for c in r.cells) for r in rows[:header_row_count]),
        default=0,
    )


def _insert_placeholders(
    *, active_span: list[int], cells: list[Any], n_cols: int
) -> tuple[list[Any], int]:
    """Create a new list of cells with placeholders inserted for active rowspans.

    Parameters
    ----------
    active_span
        List tracking remaining rowspan counts for each column.
    cells
        The list of original table cells.
    n_cols
        The total number of columns in the table.

    Returns
    -------
    tuple[list[Any], int]
        (new_cells, dropped_cells) where dropped_cells counts original cells dropped
        due to overflow.
    """

    new_cells: list[Any] = []
    col = 0
    dropped_cells = 0

    for idx, cell in enumerate(cells):
        while col < n_cols and active_span[col] > 0:
            new_cells.append(TableCell(col_span=1, row_span=1, text=None))
            col += 1

        if col >= n_cols:
            # No remaining width. This indicates an inconsistency between prior
            # rowspans and the extracted cells in this row. We drop the remaining cells
            # to preserve the table's declared width (n_cols).
            dropped_cells = len(cells) - idx

            if dropped_cells > 0:
                logger.warning(
                    f"Row overflow while inserting rowspan placeholders: "
                    f"dropping {dropped_cells} cell(s) beyond n_cols={n_cols}."
                )

            break

        new_cells.append(cell)

        col_span = cell.col_span
        row_span = cell.row_span

        _update_active_span(
            active_span=active_span,
            col=col,
            col_span=col_span,
            n_cols=n_cols,
            row_span=row_span,
        )

        col += col_span

    # Fill trailing gaps if we haven't reached n_cols yet.
    while col < n_cols and active_span[col] > 0:
        new_cells.append(TableCell(col_span=1, row_span=1, text=None))
        col += 1

    return new_cells, dropped_cells


def _is_synthetic_placeholder_cell(cell: TableCell) -> bool:
    """True if the cell is a synthetic 1x1 empty placeholder inserted by postprocess.

    Parameters
    ----------
    cell
        The cell to check.

    Returns
    -------
    bool
        True if the cell matches the lightweight placeholder shape used by rowspan
        alignment and row-padding repair steps.
    """

    return cell.col_span == 1 and cell.row_span == 1 and cell.text is None


def _process_page_tables(
    *,
    carry_from_prev: str | None,
    changes: list[dict[str, Any]],
    items: list[tuple[int, Block | Table]],
    page_idx: int,
    resumed_table_keys: set[tuple[int, int]],
    truncated_table_keys: set[tuple[int, int]],
) -> str | None:
    """Process tables on a page, applying carried codes and finding the next carry.

    This uses VERIFIED table-continuation edges (not raw extractor boundaries) to
    decide whether a table is "resumed" (incoming edge) or "truncated" (outgoing edge).

    Parameters
    ----------
    carry_from_prev
        The code carried from the previous page.
    changes
        The list of changes to append mutations to.
    items
        The list of (original_item_index, item) tuples on the current page.
    page_idx
        The index of the current page.
    resumed_table_keys
        Set of (page_idx, item_idx) keys that have an incoming VERIFIED table edge.
    truncated_table_keys
        Set of (page_idx, item_idx) keys that have an outgoing VERIFIED table edge.

    Returns
    -------
    str | None
        The code to carry forward to the next page.
    """

    carry_to_next: str | None = None
    applied_prev_carry = False

    for item_index, item in ((i, itm) for i, itm in items if itm.kind == "table"):
        is_resumed = (page_idx, item_index) in resumed_table_keys
        is_truncated = (page_idx, item_index) in truncated_table_keys
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
        elif (
            is_resumed
            and code
            and carry_from_prev
            and code == carry_from_prev
            and not applied_prev_carry
        ):
            # Carry already satisfied by this first resumed table; consume it so we do
            # not incorrectly apply it to later resumed tables on the same page.
            applied_prev_carry = True
        elif (
            is_resumed
            and code
            and carry_from_prev
            and code != carry_from_prev
            and not applied_prev_carry
        ):
            logger.warning(
                f"Table local_code conflict on page {page_idx} item {item_index}: "
                f"carried '{carry_from_prev}' from previous page but table already has "
                f"'{code}' — keeping existing and NOT applying carried code to any other "
                f"resumed tables on this page."
            )
            changes.append(
                {
                    "type": "propagate_table_local_code_conflict",
                    "page": page_idx,
                    "item_index": item_index,
                    "carried_local_code": carry_from_prev,
                    "existing_local_code": code,
                }
            )
            applied_prev_carry = True  # Consume the carry for this page

        # Decide what we carry forward to the NEXT page.
        if is_truncated and code:
            carry_to_next = code

    return carry_to_next


def _process_table_item(
    *, item: Table, item_index: int, page_index: int
) -> list[dict[str, Any]]:
    """Process a single table item to fix row alignments.

    Parameters
    ----------
    item
        The table item to process.
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

    # active_span[c] = number of rows remaining (including the current row) that column
    # c is occupied due to rowspans from prior rows. We decrement after processing each
    # row.
    active_span = [0] * n_cols

    for row_index, row in enumerate(item.rows):
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
    *, item: Table, item_index: int, page_index: int
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

    rows = item.rows
    header_row_count = item.header_row_count

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
        # NB: `cells` is a direct reference to `row.cells` (not a copy). This is
        # intentional: `_trim_excess_cells` mutates the list in-place via `.pop()`,
        # which must modify the actual row. Do NOT copy `row.cells` here without
        # also re-assigning `row.cells = cells` after trimming.
        cells = row.cells
        effective_cols = sum(cell.col_span for cell in cells)

        # Record over-wide rows (common in messy PDFs/extraction noise).
        if effective_cols > n_cols:
            before_cells = len(cells)
            before_effective = effective_cols

            # If the overflow is just trailing empty placeholders, trim them.
            trimmed = _trim_excess_cells(n_cols=n_cols, new_cells=cells)

            after_cells = len(cells)
            after_effective = sum(cell.col_span for cell in cells)

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
    *, active_span: list[int], n_cols: int, row: TableRow
) -> dict[str, Any] | None:
    """Process a single row to align cells based on active rowspans.

    This function only inserts placeholders for active rowspans. It does NOT trim
    excess cells---that responsibility belongs to `normalize_table_row_cell_counts`,
    which handles all width corrections in a single pass.

    Parameters
    ----------
    active_span
        List tracking remaining rowspan counts for each column.
    n_cols
        The total number of columns in the table.
    row
        The table row containing cells.

    Returns
    -------
    dict[str, Any] | None
        A dictionary of changes if modifications were made, otherwise None.
    """

    old_cells = list(row.cells)

    # Pre-check: if the row already spans the full table width (or more), assume the
    # extraction model has already materialized any implicit rowspan occupancy. In
    # that case, skip placeholder insertion but still update `active_span` for any
    # new rowspans introduced by this row's cells.
    old_effective = sum(c.col_span for c in old_cells)

    if old_effective >= n_cols:
        _update_spans_only(active_span=active_span, cells=old_cells, n_cols=n_cols)
        return None

    # Insert placeholders.
    new_cells, dropped_cells = _insert_placeholders(
        active_span=active_span, cells=old_cells, n_cols=n_cols
    )

    if len(new_cells) != len(old_cells) or dropped_cells:
        row.cells = new_cells
        change = {"before_cells": len(old_cells), "after_cells": len(new_cells)}

        if dropped_cells:
            change["overflow_dropped_cells"] = dropped_cells

        return change

    return None


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

    # Modal leading blank. Ignore synthetic leading placeholders inserted by the
    # rowspan-alignment pass. Those cells are repair artifacts, not extraction evidence
    # about where naturally missing cells belong.
    rows_for_modal = rows[header_row_count:] if header_row_count > 0 else rows

    natural_full_width_rows_cells = [
        r.cells
        for r in rows_for_modal
        if sum(c.col_span for c in r.cells) >= n_cols
        and r.cells
        and not _is_synthetic_placeholder_cell(r.cells[0])
    ]

    if not natural_full_width_rows_cells:
        return False

    leading_blank_count = sum(
        1
        for cs in natural_full_width_rows_cells
        if cs[0].text is None or (cs[0].text.text or "").strip() == ""
    )

    return (
        len(natural_full_width_rows_cells) >= 3
        and (leading_blank_count / len(natural_full_width_rows_cells)) >= 0.6
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

    trimmed = 0
    effective_cols = sum(c.col_span for c in new_cells)

    # Only trim *empty placeholders* and only until effective_cols fits n_cols.
    while effective_cols > n_cols and new_cells:
        tail = new_cells[-1]

        if _is_synthetic_placeholder_cell(tail):
            new_cells.pop()
            trimmed += 1
            effective_cols -= 1  # Placeholder is guaranteed col_span==1 above
        else:
            break

    return trimmed


def _update_active_span(
    *, active_span: list[int], col: int, col_span: int, n_cols: int, row_span: int
) -> None:
    """Update the active span list for a given cell's dimensions.

    Parameters
    ----------
    active_span
        List tracking remaining rowspan counts for each column.
    col
        The current starting column index for the cell.
    col_span
        The number of columns the cell spans.
    n_cols
        The total number of columns in the table.
    row_span
        The number of rows the cell spans.
    """

    if row_span > 1:
        for dc in range(col_span):
            target_col = col + dc

            if target_col < n_cols:
                active_span[target_col] = max(active_span[target_col], row_span)


def _update_spans_only(
    *, active_span: list[int], cells: list[Any], n_cols: int
) -> None:
    """Update active spans without inserting new placeholder cells.

    Parameters
    ----------
    active_span
        List tracking remaining rowspan counts for each column.
    cells
        The list of original table cells.
    n_cols
        The total number of columns in the table.
    """

    col = 0

    for cell in cells:
        while col < n_cols and active_span[col] > 0:
            col += 1

        if col >= n_cols:
            break

        col_span = cell.col_span
        row_span = cell.row_span

        _update_active_span(
            active_span=active_span,
            col=col,
            col_span=col_span,
            n_cols=n_cols,
            row_span=row_span,
        )
        col += col_span


def _validate_page_gap(
    *,
    carry_from_prev: str | None,
    changes: list[dict[str, Any]],
    last_page_idx: int | None,
    page_idx: int,
) -> str | None:
    """Clear the carried code if a gap is detected between the current and last page.

    Parameters
    ----------
    carry_from_prev
        The code carried from the previous page.
    changes
        The list of change records to append to when a carried code is dropped due to a
        page gap.
    last_page_idx
        The index of the last processed page.
    page_idx
        The index of the current page.

    Returns
    -------
    str | None
        The updated carried code.
    """

    if last_page_idx is not None and page_idx != last_page_idx + 1:
        if carry_from_prev is not None:
            logger.warning(
                f"Non-contiguous page indices detected ({last_page_idx} -> {page_idx}); "
                f"dropping carried table local_code '{carry_from_prev}' and not "
                f"propagating across the gap."
            )
            changes.append(
                {
                    "type": "propagate_table_local_code_dropped_due_to_page_gap",
                    "from_page": last_page_idx,
                    "to_page": page_idx,
                    "dropped_local_code": carry_from_prev,
                }
            )
            return None

    return carry_from_prev


def align_table_rows_with_rowspans(page_irs: dict[int, PageIR]) -> list[dict[str, Any]]:
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

        for item_index, item in enumerate(page_ir.items):
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
                # Return the verbatim matched prefix + number from the source text. Do
                # NOT canonicalize to English (e.g., "Tableau 3" stays as-is);
                # canonicalization is the responsibility of downstream pipeline stages,
                # not the verification/postprocess layer.
                return m.group(0).strip()

    return None


def load_verified_table_continuation_edges(
    verification_dirs: PageIRVerificationDirs,
) -> set[tuple[int, int, int, int]]:
    """Load the set of VERIFIED table-continuation edges from the compile report.

    The compile step (compile_continuity_from_edge_verdicts) writes
    `continuity_compile_report.json`, whose `applied_edges` list includes the edges
    that were actually applied (i.e., met min_confidence_to_patch and were not skipped).

    We treat an edge as a VERIFIED table continuation edge iff:

    1. applied == True
    2. is_continuation == True
    3. continuation_kind == "table"

    Returns
    -------
    set[tuple[int, int, int, int]]
        A set of edges represented as tuples of (prev_page, prev_index, next_page,
        next_index).
    """

    fp = verification_dirs.root / "continuity_compile_report.json"
    report = json.loads(fp.read_text())
    edges: set[tuple[int, int, int, int]] = set()

    for edge in report.get("applied_edges", []) or []:
        if not edge.get("applied", False):
            continue
        if not edge.get("is_continuation", False):
            continue
        if (edge.get("continuation_kind") or "").lower() != "table":
            continue

        edges.add(
            (
                int(edge["prev_page"]),
                int(edge["prev_index"]),
                int(edge["next_page"]),
                int(edge["next_index"]),
            )
        )

    return edges


def normalize_empty_table_cells(page_irs: dict[int, PageIR]) -> list[dict[str, Any]]:
    """Normalize visually-empty table cell TextUnit text like '' / ' ' / '\\n' into
    text='' while preserving the TextUnit object (and its provenance such as bbox).
    This stabilizes rowspan logic and downstream canonicalization by making emptiness
    explicit without dropping metadata.

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

        for item_index, item in enumerate(page_ir.items):
            if item.kind != "table":
                continue

            for row_index, row in enumerate(item.rows):
                for cell_index, cell in enumerate(row.cells):
                    text_or_none = cell.text

                    if (
                        isinstance(text_or_none, TextUnit)
                        and not (text_or_none.text or "").strip()
                    ):
                        before_text = text_or_none.text
                        text_or_none.text = ""
                        changes.append(
                            {
                                "type": "normalize_empty_string_cell_text",
                                "page": page_index,
                                "item_index": item_index,
                                "row_index": row_index,
                                "cell_index": cell_index,
                                "before_text": before_text,
                            }
                        )

    return changes


def normalize_table_row_cell_counts(
    page_irs: dict[int, PageIR],
) -> list[dict[str, Any]]:
    """Ensure each table row has an effective width of n_cols columns (accounting for
    col_span). Fixes the common LLM error of dropping empty cells at the start/end of
    rows.

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
        for item_index, item in enumerate(page_ir.items):
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

    # Enrich data by flowing local codes across VERIFIED table-continuation edges.
    #
    # NB: compile_continuity already propagates local_code at the edge level (between
    # the exact candidate pair items). This second pass operates at the chain level: it
    # carries codes sequentially across multi-page table spans and seeds missing codes
    # from nearby captions. Both passes guard against double-patching (compile skips
    # items that already have a code; this pass checks `not code` before applying).
    verified_table_edges = load_verified_table_continuation_edges(verification_dirs)
    table_code_changes = propagate_table_local_codes(
        page_irs=page_irs, verified_table_continuation_edges=verified_table_edges
    )

    # Normalize empty-string cell text to stabilize downstream logic that distinguishes
    # empty vs. non-empty cells.
    empty_cell_changes = normalize_empty_table_cells(page_irs)

    # Insert placeholders under rowspans to prevent column drift.
    #
    # NB: This runs BEFORE normalize_table_row_cell_counts (padding). The padding
    # heuristic now ignores synthetic leading placeholders inserted by this pass, so
    # rowspan repair does not bias the later left-vs-right padding decision.
    rowspan_alignment_changes = align_table_rows_with_rowspans(page_irs)

    # Fix structural "empty cell" hallucinations from the extraction model.
    pad_changes = normalize_table_row_cell_counts(page_irs)

    # Persist what was changed for audit/debug.
    write_to_json(
        fp=verification_dirs.root / "postprocess_report.json",
        json_info={
            "table_local_code_changes": table_code_changes,
            "empty_cell_text_normalization_changes": empty_cell_changes,
            "rowspan_alignment_changes": rowspan_alignment_changes,
            "table_row_normalization_changes": pad_changes,
        },
    )

    logger.success(
        f"Saved postprocess report with "
        f"{len(table_code_changes)} table code propagations, "
        f"{len(empty_cell_changes)} empty cell normalizations, "
        f"{len(rowspan_alignment_changes)} rowspan alignment changes, and "
        f"{len(pad_changes)} table row normalization changes to: "
        f"{verification_dirs.root / 'postprocess_report.json'}"
    )


def propagate_table_local_codes(
    *,
    page_irs: dict[int, PageIR],
    verified_table_continuation_edges: set[tuple[int, int, int, int]],
) -> list[dict[str, Any]]:
    """Carry forward "Table X" codes across VERIFIED table-continuation edges.

    This post-pass is intentionally conservative:

    1. Only propagate codes across page boundaries when the compile step has actually
       *applied* a TABLE continuation edge (i.e., met min_confidence_to_patch).
    2. Only carry *one* code forward: the code of the **last table in reading order**
       on the page that continues onto the next page (outgoing verified edge).
    3. Never let later, non-continuing tables overwrite the carry-forward code.

    Parameters
    ----------
    page_irs
        The dictionary of page IRs by page index.
    verified_table_continuation_edges
        Set of (prev_page, prev_item_index, next_page, next_item_index) tuples for
        TABLE continuation edges that were actually applied by the compile step.

    Returns
    -------
    list[dict[str, Any]]
        A list of changes made during the postpass.
    """

    # Precompute which table items have VERIFIED incoming/outgoing edges.
    resumed_table_keys: set[tuple[int, int]] = {
        (next_page, next_index)
        for (
            prev_page,
            prev_index,
            next_page,
            next_index,
        ) in verified_table_continuation_edges
    }
    truncated_table_keys: set[tuple[int, int]] = {
        (prev_page, prev_index)
        for (
            prev_page,
            prev_index,
            next_page,
            next_index,
        ) in verified_table_continuation_edges
    }

    carry_from_prev: str | None = None
    changes: list[dict[str, Any]] = []
    last_page_idx: int | None = None

    for page_idx in sorted(page_irs.keys()):
        carry_from_prev = _validate_page_gap(
            carry_from_prev=carry_from_prev,
            changes=changes,
            last_page_idx=last_page_idx,
            page_idx=page_idx,
        )

        page = page_irs[page_idx]
        items = [(i, it) for i, it in enumerate(page.items) if not is_artifact(it)]

        # Drop the carried code if the current page has no VERIFIED-resumed tables.
        if carry_from_prev is not None and not any(
            item.kind == "table" and (page_idx, item_index) in resumed_table_keys
            for item_index, item in items
        ):
            carry_from_prev = None

        # Attempt to find a table code from a caption before the first VERIFIED-resumed
        # table.
        if carry_from_prev is None:
            first_relevant_table_pos = next(
                (
                    pos
                    for pos, (orig_idx, item) in enumerate(items)
                    if item.kind == "table"
                    and (page_idx, orig_idx) in resumed_table_keys
                    and not (item.local_code or "").strip()
                ),
                None,
            )

            if first_relevant_table_pos is not None:
                caption_scope = [item for _, item in items[:first_relevant_table_pos]]
                caption_code = find_caption_code(caption_scope)

                if caption_code:
                    carry_from_prev = caption_code.strip() or None

        carry_to_next = _process_page_tables(
            carry_from_prev=carry_from_prev,
            changes=changes,
            items=items,
            page_idx=page_idx,
            resumed_table_keys=resumed_table_keys,
            truncated_table_keys=truncated_table_keys,
        )

        # Carry forward only the table that continues onto the next page.
        carry_from_prev = carry_to_next
        last_page_idx = page_idx

    return changes
