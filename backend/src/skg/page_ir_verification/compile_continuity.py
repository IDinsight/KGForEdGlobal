"""This module contains utility functions related to compiling the page IR continuity
using edge verdicts.
"""

# Standard Library
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.page_ir_extraction.schemas import Block, PageIR, Table
from skg.page_ir_verification.utils import (
    EdgeVerdictRecord,
    PageIRContinuityVerdict,
    PageIRVerificationDirs,
)
from skg.utils.constants import ItemBoundary, PageContinuationKind
from skg.utils.general import write_to_json


def _apply_all_local_code_patches(
    *, local_code_patch: dict[tuple[int, int], str], page_irs: dict[int, PageIR]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply local code modifications to page items.

    This function never overwrites an existing local_code on an item. If a patch would
    overwrite a non-empty local_code, it is skipped and recorded.

    Parameters
    ----------
    local_code_patch
        Dictionary containing local codes to patch.
    page_irs
        Mapping of page_index to PageIR objects.

    Returns
    -------
    tuple[list[dict[str, Any]], list[dict[str, Any]]]
        (local_code_changes, local_code_patch_skips)
    """

    local_code_changes: list[dict[str, Any]] = []
    local_code_patch_skips: list[dict[str, Any]] = []

    for (page_index, item_index), code in sorted(local_code_patch.items()):
        item = page_irs[page_index].items[item_index]
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
        elif before != code:
            local_code_patch_skips.append(
                {
                    "desired": code,
                    "existing": before,
                    "item_index": item_index,
                    "page": page_index,
                    "reason": "item_already_has_local_code",
                }
            )
            logger.warning(
                f"Page {page_index} item {item_index}: skipping local_code patch "
                f"'{code}' because item already has local_code='{before}'."
            )

    return local_code_changes, local_code_patch_skips


def _apply_edge_verdicts(
    *,
    bools: dict[tuple[int, int], list[bool]],
    dirty_keys: set[tuple[int, int]],
    effective_local_codes: dict[tuple[int, int], str | None],
    local_code_conflicts: list[dict[str, Any]],
    local_code_propagation_conflicts: list[dict[str, Any]],
    local_code_patch: dict[tuple[int, int], str],
    min_confidence_to_patch: float,
    repeats_header_patch: dict[tuple[int, int], bool],
    sorted_edge_records: list[EdgeVerdictRecord],
) -> list[dict[str, Any]]:
    """Apply decisions from edge verdicts to the state dictionaries.

    For each edge record: if confidence meets the threshold, apply mutations
    (boundary bools, local_code propagation, repeats_header patches). Otherwise
    preserve extraction-time boundaries unchanged.

    Parameters
    ----------
    bools
        Dictionary of boolean boundary flags.
    dirty_keys
        Set to track which (page_idx, item_idx) keys were mutated.
    effective_local_codes
        Dictionary of current effective local codes.
    local_code_conflicts
        List to record local code conflicts.
    local_code_propagation_conflicts
        List to record propagation conflicts when multiple edges attempt to set
        different local_code values for the same target.
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
        verdict = record.verdict
        prev_key = (record.prev_page_index, record.prev_item_index)
        next_key = (record.next_page_index, record.next_item_index)
        should_apply = verdict.confidence >= min_confidence_to_patch

        # Pair reports can get out of sync with PageIR item indices if upstream
        # extraction outputs changed but old reports were reused.
        if prev_key not in bools or next_key not in bools:
            logger.warning(
                "Skipping edge verdict because candidate keys were not found in "
                f"current PageIRs: prev_key={prev_key} next_key={next_key} "
                f"(pages {record.prev_page_index}->{record.next_page_index})."
            )
            applied_edges.append(
                _make_edge_summary(
                    record=record,
                    should_apply=should_apply,
                    skip_reason="missing_candidate_key",
                    skipped=True,
                )
            )
            continue

        if not should_apply:
            applied_edges.append(
                _make_edge_summary(
                    record=record,
                    should_apply=should_apply,
                    skip_reason="below_confidence_threshold",
                    skipped=True,
                )
            )
            continue

        _mutate_for_edge(
            bools=bools,
            dirty_keys=dirty_keys,
            effective_local_codes=effective_local_codes,
            local_code_conflicts=local_code_conflicts,
            local_code_patch=local_code_patch,
            local_code_propagation_conflicts=local_code_propagation_conflicts,
            next_key=next_key,
            prev_key=prev_key,
            record=record,
            repeats_header_patch=repeats_header_patch,
        )

        applied_edges.append(
            _make_edge_summary(
                record=record,
                should_apply=should_apply,
                skipped=False,
            )
        )

    return applied_edges


def _make_edge_summary(
    *,
    record: EdgeVerdictRecord,
    should_apply: bool,
    skip_reason: str | None = None,
    skipped: bool,
) -> dict[str, Any]:
    """Build a JSON-serializable summary dict for a single edge verdict.

    Parameters
    ----------
    record
        The edge verdict record.
    should_apply
        Whether the confidence met the threshold.
    skip_reason
        Reason for skipping, if applicable.
    skipped
        Whether the verdict was skipped entirely (e.g., missing keys or below
        threshold).

    Returns
    -------
    dict[str, Any]
        Summary dictionary.
    """

    verdict = record.verdict
    summary: dict[str, Any] = {
        "prev_index": record.prev_item_index,
        "next_index": record.next_item_index,
        "prev_page": record.prev_page_index,
        "next_page": record.next_page_index,
        "is_continuation": verdict.is_continuation,
        "continuation_kind": verdict.continuation_kind.value,
        "confidence": verdict.confidence,
        "eligible_by_confidence": should_apply,
        "applied": should_apply and not skipped,
        "skipped": skipped,
    }

    if skip_reason is not None:
        summary["skip_reason"] = skip_reason

    return summary


def _mutate_for_edge(
    *,
    bools: dict[tuple[int, int], list[bool]],
    dirty_keys: set[tuple[int, int]],
    effective_local_codes: dict[tuple[int, int], str | None],
    local_code_conflicts: list[dict[str, Any]],
    local_code_propagation_conflicts: list[dict[str, Any]],
    local_code_patch: dict[tuple[int, int], str],
    next_key: tuple[int, int],
    prev_key: tuple[int, int],
    record: EdgeVerdictRecord,
    repeats_header_patch: dict[tuple[int, int], bool],
) -> None:
    """Apply in-place mutations for a single confident edge verdict.

    Parameters
    ----------
    bools
        Mapping of (page_idx, item_idx) to [from_prev, to_next] booleans.
    dirty_keys
        Set to track which keys were mutated by edge verdicts.
    effective_local_codes
        Mapping of (page_idx, item_idx) to effective local_code (after prior patches).
    local_code_conflicts
        List to append any local_code conflicts detected.
    local_code_propagation_conflicts
        List to append any conflicts between multiple propagated local_code values
        targeting the same item.
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
    """

    verdict = record.verdict

    if verdict.is_continuation:
        if not bools[prev_key][1]:
            bools[prev_key][1] = True
            dirty_keys.add(prev_key)
        if not bools[next_key][0]:
            bools[next_key][0] = True
            dirty_keys.add(next_key)

        _propagate_local_codes(
            effective_local_codes=effective_local_codes,
            local_code_conflicts=local_code_conflicts,
            local_code_propagation_conflicts=local_code_propagation_conflicts,
            local_code_patch=local_code_patch,
            next_key=next_key,
            prev_key=prev_key,
            record=record,
            verdict=verdict,
        )

        if (
            verdict.continuation_kind == PageContinuationKind.TABLE
            and verdict.set_next_table_repeats_header is not None
        ):
            repeats_header_patch[next_key] = verdict.set_next_table_repeats_header
    else:
        # Clear only the directional connection for THIS candidate pair.
        if bools[prev_key][1]:
            bools[prev_key][1] = False
            dirty_keys.add(prev_key)
        if bools[next_key][0]:
            bools[next_key][0] = False
            dirty_keys.add(next_key)


def _propagate_local_codes(
    *,
    effective_local_codes: dict[tuple[int, int], str | None],
    local_code_conflicts: list[dict[str, Any]],
    local_code_propagation_conflicts: list[dict[str, Any]],
    local_code_patch: dict[tuple[int, int], str],
    next_key: tuple[int, int],
    prev_key: tuple[int, int],
    record: EdgeVerdictRecord,
    verdict: PageIRContinuityVerdict,
) -> None:
    """Propagate local_code across a TRUE continuation edge when one side is missing.

    Parameters
    ----------
    effective_local_codes
        Mapping of (page_idx, item_idx) to effective local_code.
    local_code_conflicts
        List to append any conflicts detected.
    local_code_propagation_conflicts
        List to append propagation conflicts when multiple edges attempt to set
        different local_code values for the same target.
    local_code_patch
        Mapping of (page_idx, item_idx) to desired local_code string.
    next_key
        The tuple key for the next candidate.
    prev_key
        The tuple key for the previous candidate.
    record
        The edge verdict record.
    verdict
        The verdict object.
    """

    # Only propagate local_code for table and figure continuations. Text blocks
    # (paragraphs, headings, lists) rarely carry local_code, and propagating across a
    # text break risks incorrectly assigning a code from one logical block to an
    # unrelated one.
    if verdict.continuation_kind not in {
        PageContinuationKind.TABLE,
        PageContinuationKind.FIGURE,
    }:
        return

    prev_code = effective_local_codes.get(prev_key)
    next_code = effective_local_codes.get(next_key)

    if prev_code and not next_code:
        _try_propagate_code(
            code=prev_code,
            effective_local_codes=effective_local_codes,
            local_code_patch=local_code_patch,
            local_code_propagation_conflicts=local_code_propagation_conflicts,
            source_key=prev_key,
            target_key=next_key,
        )
    elif next_code and not prev_code:
        _try_propagate_code(
            code=next_code,
            effective_local_codes=effective_local_codes,
            local_code_patch=local_code_patch,
            local_code_propagation_conflicts=local_code_propagation_conflicts,
            source_key=next_key,
            target_key=prev_key,
        )
    elif prev_code and next_code and prev_code != next_code:
        local_code_conflicts.append(
            {
                "prev_page": record.prev_page_index,
                "next_page": record.next_page_index,
                "prev_index": record.prev_item_index,
                "next_index": record.next_item_index,
                "prev_code": prev_code,
                "next_code": next_code,
                "continuation_kind": verdict.continuation_kind.value,
            }
        )


def _try_propagate_code(
    *,
    code: str,
    effective_local_codes: dict[tuple[int, int], str | None],
    local_code_patch: dict[tuple[int, int], str],
    local_code_propagation_conflicts: list[dict[str, Any]],
    source_key: tuple[int, int],
    target_key: tuple[int, int],
) -> None:
    """Attempt to propagate a local_code to a target key.

    This updates effective_local_codes in-place and records a patch via
    local_code_patch.setdefault(). If a different code has already been propagated to
    the same target_key earlier in the compile pass, this records a propagation
    conflict and keeps the earlier propagated value.

    Parameters
    ----------
    code
        The local_code to propagate.
    effective_local_codes
        Mapping of effective local codes (updated in-place).
    local_code_patch
        Mapping of local code patches (updated in-place via setdefault).
    local_code_propagation_conflicts
        List to append propagation conflicts.
    source_key
        The (page_idx, item_idx) key that the code was propagated from.
    target_key
        The (page_idx, item_idx) key to propagate to.
    """

    existing = local_code_patch.get(target_key)

    if existing and existing != code:
        local_code_propagation_conflicts.append(
            {
                "target_page": target_key[0],
                "target_index": target_key[1],
                "source_page": source_key[0],
                "source_index": source_key[1],
                "existing_code": existing,
                "incoming_code": code,
                "kept_code": existing,
                "reason": "propagation_conflict_keep_earlier",
            }
        )
        logger.warning(
            f"local_code propagation conflict at page {target_key[0]} "
            f"item {target_key[1]}: existing '{existing}' vs incoming "
            f"'{code}' — keeping earlier propagation."
        )
        return

    effective_local_codes[target_key] = code
    local_code_patch.setdefault(target_key, code)


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


def _deduplicate_and_sort_edge_records(
    *, edge_records: list[EdgeVerdictRecord], min_confidence_to_patch: float
) -> tuple[list[EdgeVerdictRecord], list[dict[str, Any]]]:
    """Select at most one edge record per boundary, then sort deterministically.

    Continuity compilation assumes at most one selected pair per page boundary
    (prev_page_index, next_page_index). If multiple records exist for the same
    boundary, this function selects the "best" one and discards the others:

    Selection order (highest priority first):

    1. Prefer *confident positive continuations* (is_continuation=True and
       verdict.confidence >= min_confidence_to_patch)
    2. If no confident positives exist, select the record with the highest
       verdict.confidence
    3. Tie-breakers (determinism): prefer is_continuation=True, then lower
       (prev_item_index, next_item_index)

    It logs a warning for each deduplicated boundary and returns a report of the
    discarded records for inclusion in the compile report.

    Parameters
    ----------
    edge_records
        List of edge verdict records to sort, validate, and deduplicate.
    min_confidence_to_patch
        Minimum confidence threshold to consider an edge a "confident positive"
        continuation for selection purposes.

    Returns
    -------
    tuple[list[EdgeVerdictRecord], list[dict[str, Any]]]
        (sorted_edge_records, boundary_duplicate_resolutions)
    """

    def _brief(r: EdgeVerdictRecord) -> dict[str, Any]:
        """Helper to create a brief summary of an edge record for reporting purposes.

        Parameters
        ----------
        r
            The EdgeVerdictRecord to summarize.

        Returns
        -------
        dict[str, Any]
            A brief summary of the edge record's key attributes.
        """

        v = r.verdict
        return {
            "prev_index": int(r.prev_item_index),
            "next_index": int(r.next_item_index),
            "is_continuation": bool(v.is_continuation),
            "continuation_kind": v.continuation_kind.value,
            "confidence": float(v.confidence),
        }

    boundary_map: dict[tuple[int, int], list[EdgeVerdictRecord]] = {}
    for r in edge_records:
        boundary = (int(r.prev_page_index), int(r.next_page_index))
        boundary_map.setdefault(boundary, []).append(r)

    boundary_duplicate_resolutions: list[dict[str, Any]] = []
    selected: list[EdgeVerdictRecord] = []

    for boundary, records in sorted(boundary_map.items()):
        if len(records) == 1:
            selected.append(records[0])
            continue

        best = _select_best_edge_record_for_boundary(
            min_confidence_to_patch=min_confidence_to_patch, records=records
        )

        discarded = [r for r in records if r is not best]
        discarded = sorted(
            discarded,
            key=lambda r: (
                -float(r.verdict.confidence),
                -(1 if r.verdict.is_continuation else 0),
                int(r.prev_item_index),
                int(r.next_item_index),
            ),
        )

        logger.warning(
            f"Duplicate edge records detected for boundary {boundary}; selected "
            f"prev_index={best.prev_item_index} next_index={best.next_item_index} "
            f"(confidence={best.verdict.confidence}, is_continuation={best.verdict.is_continuation}) "
            f"and discarded {len(discarded)} other record(s)."
        )

        boundary_duplicate_resolutions.append(
            {
                "prev_page": boundary[0],
                "next_page": boundary[1],
                "selected": _brief(best),
                "discarded": [_brief(r) for r in discarded],
                "reason": "dedupe_one_record_per_boundary_prefer_confident_positive",
                "min_confidence_to_patch": float(min_confidence_to_patch),
            }
        )
        selected.append(best)

    # Sort deterministically after selection.
    sorted_edge_records = sorted(
        selected,
        key=lambda r: (
            int(r.prev_page_index),
            int(r.next_page_index),
            int(r.prev_item_index),
            int(r.next_item_index),
        ),
    )

    for r in sorted_edge_records:
        if int(r.next_page_index) != int(r.prev_page_index) + 1:
            logger.warning(
                f"Non-adjacent edge record detected: {r.prev_page_index}->{r.next_page_index}. "
                "This is unexpected for page-pair continuity verification."
            )

    return sorted_edge_records, boundary_duplicate_resolutions


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

        for item_index, item in enumerate(page.items):
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


def _patch_repeats_header(
    *, desired: bool | None, item_index: int, page_index: int, table: Table
) -> dict[str, Any]:
    """Patch table.repeats_header and adjust header_row_count for consistency.

    Enforces the invariant from Table.validate_repeats_header_consistency:

    - repeats_header=True requires header_row_count >= 1
    - repeats_header=False requires header_row_count == 0
    - repeats_header=None has no header_row_count constraint

    Parameters
    ----------
    desired
        The desired repeats_header value (True, False, or None).
    item_index
        The index of the item on the page.
    page_index
        The index of the page containing the table.
    table
        The Table item to patch.

    Returns
    -------
    dict[str, Any]
        Summary of the change applied.
    """

    before_repeats = table.repeats_header
    before_hrc = table.header_row_count

    table.repeats_header = desired

    # Adjust header_row_count to satisfy the Pydantic model invariant. When clearing
    # repeats_header to None, leave header_row_count as-is (no constraint applies).
    if desired is True and table.header_row_count == 0:
        # The LLM says headers are repeated but extraction counted 0 header rows.
        # Assume 1 header row (the most common case) so the invariant holds.
        table.header_row_count = 1
        logger.info(
            f"Page {page_index} item {item_index}: set header_row_count=1 to satisfy "
            f"repeats_header=True invariant (was 0)."
        )
    elif desired is False and table.header_row_count > 0:
        # The LLM says headers are NOT repeated, so the counted header rows are
        # actually body rows on this continuation page.
        table.header_row_count = 0
        logger.info(
            f"Page {page_index} item {item_index}: set header_row_count=0 to satisfy "
            f"repeats_header=False invariant (was {before_hrc})."
        )

    change: dict[str, Any] = {
        "page": page_index,
        "item_index": item_index,
        "before": before_repeats,
        "after": desired,
    }

    if table.header_row_count != before_hrc:
        change["header_row_count_before"] = before_hrc
        change["header_row_count_after"] = table.header_row_count

    return change


def _reconcile_dirty_item_states(
    *,
    bools: dict[tuple[int, int], list[bool]],
    dirty_keys: set[tuple[int, int]],
    page_irs: dict[int, PageIR],
    repeats_header_patch: dict[tuple[int, int], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconcile updated boundaries and repeats_header states for only the items
    that were touched by edge verdicts.

    NB: This also reconciles any items with repeats_header patches even when their
    boundary booleans did not change.

    Parameters
    ----------
    bools
        Dictionary of updated boolean boundary flags.
    dirty_keys
        Set of (page_idx, item_idx) keys that were mutated during edge application.
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

    # Also include keys that have a repeats_header patch even if the boundary bits did
    # not change (e.g., boundary already RESUMED/BOTH but header behavior needs
    # patching).
    all_keys = dirty_keys | set(repeats_header_patch.keys())

    for page_index, item_index in sorted(all_keys):
        flags = bools[(page_index, item_index)]
        item = page_irs[page_index].items[item_index]

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

    When patching repeats_header, also adjusts header_row_count to maintain the
    consistency invariant enforced by Table.validate_repeats_header_consistency:

    - repeats_header=True requires header_row_count >= 1
    - repeats_header=False requires header_row_count == 0

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

    # repeats_header only meaningful for tables.
    if item.kind != "table":
        return boundary_change, header_change

    table = item

    # Case A: Connection broken (not RESUMED or BOTH) --> Clear header.
    if item.boundary not in {ItemBoundary.RESUMED, ItemBoundary.BOTH}:
        if table.repeats_header is not None:
            header_change = _patch_repeats_header(
                desired=None,
                item_index=item_index,
                page_index=page_index,
                table=table,
            )

            # When a table transitions from continuation (repeats_header=False,
            # header_row_count=0) to COMPLETE/TRUNCATED, the zeroed header count may be
            # a leftover artifact. That is, the original extraction may have had real
            # header rows that were cleared during an earlier repeats_header=False
            # patch. Flag for human review since we can't recover the correct count
            # automatically.
            if table.header_row_count == 0:
                logger.warning(
                    f"Page {page_index} item {item_index}: boundary downgraded to "
                    f"{item.boundary.value} with header_row_count=0. This table may "
                    f"have lost its original header count from a prior "
                    f"repeats_header=False patch. Flagging for review."
                )

                if header_change is not None:
                    header_change["needs_review"] = True
                    header_change["review_reason"] = (
                        "header_row_count=0 after boundary downgrade; may need "
                        "manual correction"
                    )

        return boundary_change, header_change

    # Case B: Connection exists --> Apply patch if present.
    key = (page_index, item_index)

    if key in repeats_header_patch:
        desired = repeats_header_patch[key]

        if table.repeats_header != desired:
            header_change = _patch_repeats_header(
                desired=desired,
                item_index=item_index,
                page_index=page_index,
                table=table,
            )

    return boundary_change, header_change


def _select_best_edge_record_for_boundary(
    *, min_confidence_to_patch: float, records: list[EdgeVerdictRecord]
) -> EdgeVerdictRecord:
    """Select the best edge record for a single page boundary.

    Mirrors verify_page_pairs.execute_verification_attempts:

    1. Prefer confident positive continuations (is_continuation=True and
        verdict.confidence >= min_confidence_to_patch).
    2. If no confident positive exists, select the highest-confidence record overall.
    3. Deterministic tie-breakers: prefer is_continuation=True, then lower
        (prev_item_index, next_item_index).
    """

    if not records:
        raise ValueError("records must be non-empty")

    confident_positives = [
        r
        for r in records
        if bool(r.verdict.is_continuation)
        and float(r.verdict.confidence) >= float(min_confidence_to_patch)
    ]
    candidates = confident_positives if confident_positives else records

    return max(
        candidates,
        key=lambda r: (
            float(r.verdict.confidence),
            1 if bool(r.verdict.is_continuation) else 0,
            -int(r.prev_item_index),
            -int(r.next_item_index),
        ),
    )


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
    sorted_edge_records, boundary_duplicate_resolutions = (
        _deduplicate_and_sort_edge_records(
            edge_records=edge_records, min_confidence_to_patch=min_confidence_to_patch
        )
    )

    dirty_keys: set[tuple[int, int]] = set()
    local_code_conflicts: list[dict[str, Any]] = []
    local_code_propagation_conflicts: list[dict[str, Any]] = []
    local_code_patch: dict[tuple[int, int], str] = {}
    repeats_header_patch: dict[tuple[int, int], bool] = {}

    applied_edges = _apply_edge_verdicts(
        bools=bools,
        dirty_keys=dirty_keys,
        effective_local_codes=effective_local_codes,
        local_code_conflicts=local_code_conflicts,
        local_code_propagation_conflicts=local_code_propagation_conflicts,
        local_code_patch=local_code_patch,
        min_confidence_to_patch=min_confidence_to_patch,
        repeats_header_patch=repeats_header_patch,
        sorted_edge_records=sorted_edge_records,
    )

    boundary_changes, repeats_header_changes = _reconcile_dirty_item_states(
        bools=bools,
        dirty_keys=dirty_keys,
        page_irs=page_irs,
        repeats_header_patch=repeats_header_patch,
    )

    local_code_changes, local_code_patch_skips = _apply_all_local_code_patches(
        local_code_patch=local_code_patch, page_irs=page_irs
    )

    compile_report = {
        "applied_edges": applied_edges,
        "boundary_duplicate_resolutions": boundary_duplicate_resolutions,
        "boundary_changes": boundary_changes,
        "local_code_changes": local_code_changes,
        "local_code_patch_skips": local_code_patch_skips,
        "local_code_propagation_conflicts": local_code_propagation_conflicts,
        "local_code_conflicts": local_code_conflicts,
        "repeats_header_changes": repeats_header_changes,
    }

    write_to_json(
        fp=verification_dirs.root / "continuity_compile_report.json",
        json_info=compile_report,
    )
