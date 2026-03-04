"""This module contains utility functions related to compiling the page IR continuity
using edge verdicts.
"""

# Standard Library
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table
from skg.page_ir_verification.utils import EdgeVerdictRecord, PageIRVerificationDirs
from skg.utils.constants import ItemBoundary, PageContinuationKind
from skg.utils.general import write_to_json


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
                    existing = local_code_patch.get(next_key)

                    if existing and existing != prev_code:
                        logger.warning(
                            f"local_code propagation conflict at page "
                            f"{record.next_page_index} item "
                            f"{record.next_candidate_index}: existing "
                            f"'{existing}' vs incoming '{prev_code}' — "
                            f"keeping earlier propagation."
                        )
                    else:
                        effective_local_codes[next_key] = prev_code
                        local_code_patch.setdefault(next_key, prev_code)
                elif next_code and not prev_code:
                    existing = local_code_patch.get(prev_key)

                    if existing and existing != next_code:
                        logger.warning(
                            f"local_code propagation conflict at page "
                            f"{record.prev_page_index} item "
                            f"{record.prev_candidate_index}: existing "
                            f"'{existing}' vs incoming '{next_code}' — "
                            f"keeping earlier propagation."
                        )
                    else:
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


def _reconcile_item_state(
    *,
    item: Block | Table,
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


def compile_continuity_from_edge_verdicts(
    *,
    edge_records: list[EdgeVerdictRecord],
    min_confidence_to_patch: float,
    page_irs: dict[int, PageIR],
    verification_dirs: PageIRVerificationDirs,
) -> None:
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
    verification_dirs
        Directory paths for the verification run, used for writing the continuity
        compile report.
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

    compile_report = {
        "applied_edges": applied_edges,
        "boundary_changes": boundary_changes,
        "local_code_changes": local_code_changes,
        "local_code_conflicts": local_code_conflicts,
        "repeats_header_changes": repeats_header_changes,
    }
    write_to_json(
        fp=verification_dirs.root / "continuity_compile_report.json",
        json_info=compile_report,
    )
