"""This module contains utility functions related to page IR **verification**."""

# Standard Library
import re
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TableCell, TextUnit
from skg.page_ir_verification.schemas import PageIRContinuityVerdict
from skg.schemas import ExtractionConfig, RunCtx, VerificationConfig
from skg.utils.constants import (
    BlockType,
    CaptionTablePrefixes,
    ItemBoundary,
    PageBoundaryState,
    PageContinuationKind,
)
from skg.utils.general import (
    compare_directories,
    make_dir,
    open_json_type,
    write_to_json,
)
from skg.utils.pdf import compute_doc_key

# Compiled regexes.
TABLE_PREFIX_RE = "|".join(re.escape(t) for t in CaptionTablePrefixes)
TABLE_CODE_RE = re.compile(
    rf"^\s*(?:{TABLE_PREFIX_RE})\s+(?P<num>\d+(?:\.\d+)*)\b", re.IGNORECASE
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


@dataclass(frozen=True)
class VerificationVerdict:
    """Parsed verdict from the page-pair verification step.

    Fields
    ------
    confidence
        Model confidence in the verdict (0.0–1.0).
    continuation_kind
        The kind of continuation ('table', 'text', 'figure', or None).
    is_continuation
        Whether the page pair contains a cross-page continuation.
    next_item_index
        Original item index in PageIR.items on the next page (from
        selected_candidate_selection.next_candidate_index). None if absent.
    next_page_index
        0-based page index of the next page.
    prev_item_index
        Original item index in PageIR.items on the previous page (from
        selected_candidate_selection.prev_candidate_index). None if absent.
    prev_page_index
        0-based page index of the previous page.
    set_next_table_repeats_header
        If not None, override the next table's repeats_header flag with this value.
    """

    confidence: float
    continuation_kind: Optional[str]
    is_continuation: bool
    next_item_index: Optional[int]
    next_page_index: int
    prev_item_index: Optional[int]
    prev_page_index: int
    set_next_table_repeats_header: Optional[bool]


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


def cross_check_extraction_run(
    *,
    expected_doc_key: str,
    extraction_config: ExtractionConfig,
    page_images_dir: Path,
    page_irs_dir: Path,
) -> list[int]:
    """Cross-check that the extraction run matches expected parameters and that page
    IRs are present.

    Parameters
    ----------
    expected_doc_key
        The expected document key (hex string) from the extraction run metadata.
    extraction_config
        The extraction configuration used for the run.
    page_images_dir
        Directory containing the rendered page images from extraction.
    page_irs_dir
        Directory containing the extracted page IR JSON files.

    Returns
    -------
    list[int]
        A list of page indices for which page IRs are present and verified.

    Raises
    ------
    ValueError
        If the computed document key does not match the expected key.
    """

    assert compare_directories(page_images_dir, page_irs_dir)
    computed_doc_key = compute_doc_key(n_hex=64, pdf_fp=extraction_config.pdf_fp)

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify(): {extraction_config.pdf_fp}\n"
            f"  computed doc_key:         {computed_doc_key}\n"
            f"  extraction_run.json key:  {expected_doc_key}\n"
            f"You are likely verifying against a different PDF than the one used for "
            f"extraction. Pass the same PDF used in the extraction step or re-run "
            f"extraction."
        )

    json_fps = sorted(page_irs_dir.glob("*.json"))
    page_indices = sorted(int(fp.stem) for fp in json_fps if fp.stem.isdigit())
    assert page_indices, f"No page IR JSONs found in: {page_irs_dir}"
    return page_indices


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


def load_verification_verdicts(
    verdict_dir: Path,
) -> dict[tuple[int, int], VerificationVerdict]:
    """Load all verification verdict JSONs.

    Parameters
    ----------
    verdict_dir
        Directory containing `*.json` verdict files (e.g., `0003_0004.json`).

    Returns
    -------
    dict[tuple[int, int], VerificationVerdict]
        Mapping of `(prev_page_index, next_page_index)` to parsed verdict.

    Raises
    ------
    NotADirectoryError
        If the specified verdict_dir is not a directory.
    """

    verdicts: dict[tuple[int, int], VerificationVerdict] = {}

    if not verdict_dir.is_dir():
        raise NotADirectoryError(f"Verdict directory not found: {verdict_dir}")

    for fp in sorted(verdict_dir.glob("*.json")):
        data = open_json_type(fp)
        verdict_data = data["verdict"]
        selection = data["selected_candidate_selection"]

        verdict = VerificationVerdict(
            confidence=float(verdict_data["confidence"]),
            continuation_kind=verdict_data["continuation_kind"],
            is_continuation=bool(verdict_data["is_continuation"]),
            next_item_index=selection["next_candidate_index"],
            next_page_index=int(verdict_data["next_page_index"]),
            prev_item_index=selection["prev_candidate_index"],
            prev_page_index=int(verdict_data["prev_page_index"]),
            set_next_table_repeats_header=verdict_data["set_next_table_repeats_header"],
        )
        verdicts[(verdict.prev_page_index, verdict.next_page_index)] = verdict

    logger.info(f"Loaded {len(verdicts)} verification verdict(s) from: {verdict_dir}")

    return verdicts


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
    exclude_keys = {"model", "overwrite"}
    extra = {
        k: v for k, v in config.model_dump(mode="json").items() if k not in exclude_keys
    }
    verification_run = RunCtx(
        extra=extra,
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
