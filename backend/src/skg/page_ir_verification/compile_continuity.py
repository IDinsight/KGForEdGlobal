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


def _apply_all_local_code_patches(
    *, local_code_patch: dict[tuple[int, int], str], page_irs: dict[int, PageIR]
) -> list[dict[str, Any]]:
    """Apply local code modifications to page items.

    Parameters
    ----------
    local_code_patch
        Dictionary containing local codes to patch.
    page_irs
        Mapping of page_index to PageIR objects.

    Returns
    -------
    list[dict[str, Any]]
        List of local code changes applied.
    """

    local_code_changes: list[dict[str, Any]] = []

    for (page_index, item_index), code in sorted(local_code_patch.items()):
        item = (page_irs[page_index].items or [])[item_index]
        before = _normalize_local_code(getattr(item, "local_code", None))

        if before is None:
            item.local_code = code
            local_code_changes.append(
                {
                    "after": code,
                    "before": None,
                    "item_index": item_index,
                    "page": page_index,
                }
            )

    return local_code_changes


def _apply_edge_verdicts(
    *,
    bools: dict[tuple[int, int], list[bool]],
    effective_local_codes: dict[tuple[int, int], str | None],
    local_code_conflicts: list[dict[str, Any]],
    local_code_patch: dict[tuple[int, int], str],
    min_confidence_to_patch: float,
    repeats_header_patch: dict[tuple[int, int], bool],
    sorted_edge_records: list[EdgeVerdictRecord],
) -> list[dict[str, Any]]:
    """Apply decisions from edge verdicts to the state dictionaries.

    Parameters
    ----------
    bools
        Dictionary of boolean boundary flags.
    effective_local_codes
        Dictionary of current effective local codes.
    local_code_conflicts
        List to record local code conflicts.
    local_code_patch
        Dictionary to record local code patches.
    min_confidence_to_patch
        Minimum confidence threshold to apply edits.
    repeats_header_patch
        Dictionary to record repeats_header modifications.
    sorted_edge_records
        List of validated and sorted edge verdict records.

    Returns
    -------
    list[dict[str, Any]]
        List of applied edge summaries.
    """

    applied_edges: list[dict[str, Any]] = []

    for record in sorted_edge_records:
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

    return applied_edges


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

    # Pair reports can get out of sync with PageIR item indices if upstream extraction
    # outputs changed but old reports were reused.
    if prev_key not in bools or next_key not in bools:
        logger.warning(
            "Skipping edge verdict because candidate keys were not found in current "
            f"PageIRs: prev_key={prev_key} next_key={next_key} "
            f"(pages {record.prev_page_index}->{record.next_page_index})."
        )
        return {
            "prev_index": record.prev_candidate_index,
            "next_index": record.next_candidate_index,
            "prev_page": record.prev_page_index,
            "next_page": record.next_page_index,
            "is_continuation": verdict.is_continuation,
            "continuation_kind": verdict.continuation_kind.value,
            "confidence": verdict.confidence,
            "eligible_by_confidence": should_apply,
            "applied": False,
            "skipped": True,
            "skip_reason": "missing_candidate_key",
        }

    _apply_verdict_mutations(
        bools=bools,
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_patch=local_code_patch,
        next_key=next_key,
        prev_key=prev_key,
        record=record,
        repeats_header_patch=repeats_header_patch,
        should_apply=should_apply,
    )

    return {
        "prev_index": record.prev_candidate_index,
        "next_index": record.next_candidate_index,
        "prev_page": record.prev_page_index,
        "next_page": record.next_page_index,
        "is_continuation": verdict.is_continuation,
        "continuation_kind": verdict.continuation_kind.value,
        "confidence": verdict.confidence,
        "eligible_by_confidence": should_apply,
        "applied": should_apply,
        "skipped": False,
    }


def _apply_verdict_mutations(
    *,
    bools: dict[tuple[int, int], list[bool]],
    effective_local_codes: dict[tuple[int, int], str | None],
    local_code_conflicts: list[dict[str, Any]],
    local_code_patch: dict[tuple[int, int], str],
    next_key: tuple[int, int],
    prev_key: tuple[int, int],
    record: "EdgeVerdictRecord",
    repeats_header_patch: dict[tuple[int, int], bool],
    should_apply: bool,
) -> None:
    """Applies in-place mutations for a single edge verdict if eligible.

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
    next_key
        The tuple key (page_idx, item_idx) for the next candidate.
    prev_key
        The tuple key (page_idx, item_idx) for the previous candidate.
    record
        The edge verdict record to apply.
    repeats_header_patch
        Mapping of (page_idx, item_idx) to desired repeats_header boolean.
    should_apply
        Boolean indicating whether the confidence meets the threshold to apply edits.
    """

    if should_apply:
        verdict = record.verdict

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


def _initialize_states(
    page_irs: dict[int, PageIR],
) -> tuple[dict[tuple[int, int], list[bool]], dict[tuple[int, int], str | None]]:
    """Extract initial boundary booleans and local codes from page items.

    Parameters
    ----------
    page_irs
        Mapping of page_index to PageIR objects.

    Returns
    -------
    tuple
        A tuple containing the initialized bools dictionary and effective
        local codes dictionary.
    """

    bools: dict[tuple[int, int], list[bool]] = {}
    effective_local_codes: dict[tuple[int, int], str | None] = {}

    for page_index in sorted(page_irs):
        page = page_irs[page_index]

        for item_index, item in enumerate(page.items or []):
            fp, tn = _boundary_to_bools(item.boundary)
            bools[(page_index, item_index)] = [fp, tn]
            effective_local_codes[(page_index, item_index)] = _normalize_local_code(
                getattr(item, "local_code", None)
            )

    return bools, effective_local_codes


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


def _reconcile_all_item_states(
    *,
    bools: dict[tuple[int, int], list[bool]],
    page_irs: dict[int, PageIR],
    repeats_header_patch: dict[tuple[int, int], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconcile updated boundaries and repeats_header states.

    Parameters
    ----------
    bools
        Dictionary of updated boolean boundary flags.
    page_irs
        Mapping of page_index to PageIR objects.
    repeats_header_patch
        Dictionary of repeats_header patches to apply.

    Returns
    -------
    tuple
        A tuple containing the lists of boundary changes and repeats_header changes.
    """

    boundary_changes: list[dict[str, Any]] = []
    repeats_header_changes: list[dict[str, Any]] = []

    for page_index, item_index in sorted(bools):
        flags = bools[(page_index, item_index)]
        item = (page_irs[page_index].items or [])[item_index]

        b_change, h_change = _reconcile_item_state(
            flags=flags,
            item=item,
            item_index=item_index,
            page_index=page_index,
            repeats_header_patch=repeats_header_patch,
        )

        if b_change:
            boundary_changes.append(b_change)
        if h_change:
            repeats_header_changes.append(h_change)

    return boundary_changes, repeats_header_changes


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


def _sort_and_validate_edge_records(
    edge_records: list[EdgeVerdictRecord],
) -> list[EdgeVerdictRecord]:
    """Sort edge records deterministically and log warnings for anomalies.

    Parameters
    ----------
    edge_records
        List of edge verdict records to sort and validate.

    Returns
    -------
    list[EdgeVerdictRecord]
        Deterministically sorted edge records.
    """

    sorted_edge_records = sorted(
        edge_records,
        key=lambda r: (
            int(r.prev_page_index),
            int(r.next_page_index),
            int(r.prev_candidate_index),
            int(r.next_candidate_index),
        ),
    )
    seen_boundaries: set[tuple[int, int]] = set()

    for r in sorted_edge_records:
        boundary = (int(r.prev_page_index), int(r.next_page_index))

        if boundary in seen_boundaries:
            logger.warning(
                f"Duplicate edge record detected for boundary {boundary}; "
                "continuity compilation assumes at most one selected pair per boundary."
            )
        else:
            seen_boundaries.add(boundary)

        if int(r.next_page_index) != int(r.prev_page_index) + 1:
            logger.warning(
                f"Non-adjacent edge record detected: {r.prev_page_index}->{r.next_page_index}. "
                "This is unexpected for page-pair continuity verification."
            )

    return sorted_edge_records


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

    bools, effective_local_codes = _initialize_states(page_irs)
    sorted_edge_records = _sort_and_validate_edge_records(edge_records)

    local_code_conflicts: list[dict[str, Any]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    applied_edges = _apply_edge_verdicts(
        bools=bools,
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_patch=local_code_patch,
        min_confidence_to_patch=min_confidence_to_patch,
        repeats_header_patch=repeats_header_patch,
        sorted_edge_records=sorted_edge_records,
    )

    boundary_changes, repeats_header_changes = _reconcile_all_item_states(
        bools=bools,
        page_irs=page_irs,
        repeats_header_patch=repeats_header_patch,
    )

    local_code_changes = _apply_all_local_code_patches(
        local_code_patch=local_code_patch, page_irs=page_irs
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
