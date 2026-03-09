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
from skg.schemas import VerificationConfig
from skg.utils.constants import ItemBoundary, PageContinuationKind
from skg.utils.general import write_to_json


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

    Raises
    ------
    ValueError
        If any edge verdict references a (page_idx, item_idx) candidate key that is not
        present in the current PageIR-derived state dictionaries. This indicates stale
        or mismatched verification inputs and compile aborts rather than silently
        skipping the edge.
    """

    applied_edges: list[dict[str, Any]] = []

    for record in sorted_edge_records:
        verdict = record.verdict
        prev_key = (record.prev_page_index, record.prev_item_index)
        next_key = (record.next_page_index, record.next_item_index)
        should_apply = verdict.confidence >= min_confidence_to_patch

        # Compiled edge verdicts should always refer to items that exist in the current
        # PageIR set. If not, the verification outputs and PageIR inputs are out of
        # sync (for example: stale pair reports, changed PageIR extraction, or a
        # resumed run against different inputs). This is a hard error, not a
        # recoverable skip.
        missing_parts: list[str] = []

        if prev_key not in bools:
            missing_parts.append(f"prev_key={prev_key}")
        if next_key not in bools:
            missing_parts.append(f"next_key={next_key}")

        if missing_parts:
            details = ", ".join(missing_parts)
            raise ValueError(
                f"Edge verdict references candidate item(s) that do not exist in the "
                f"current PageIR set. This usually indicates stale pair reports or "
                f"mismatched PageIR inputs. "
                f"Boundary={record.prev_page_index}->{record.next_page_index}; "
                f"{details}; record={_brief_edge_record(record)}"
            )

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


def _bools_to_boundary(*, from_prev: bool, to_next: bool) -> ItemBoundary:
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


def _brief_edge_record(record: EdgeVerdictRecord) -> dict[str, Any]:
    """Return a brief JSON-serializable summary of an edge record.

    Parameters
    ----------
    record
        The edge verdict record to summarize.

    Returns
    -------
    dict[str, Any]
        Summary of the record's compile-relevant fields.
    """

    verdict = record.verdict
    return {
        "prev_index": int(record.prev_item_index),
        "next_index": int(record.next_item_index),
        "is_continuation": bool(verdict.is_continuation),
        "continuation_kind": verdict.continuation_kind.value,
        "confidence": float(verdict.confidence),
        "set_next_table_repeats_header": verdict.set_next_table_repeats_header,
    }


def _collect_verified_table_continuation_edges(
    applied_edges: list[dict[str, Any]],
) -> set[tuple[int, int, int, int]]:
    """Return the applied VERIFIED table-continuation edges in tuple form.

    Parameters
    ----------
    applied_edges
        The compile-time applied edge summaries.

    Returns
    -------
    set[tuple[int, int, int, int]]
        A set of (prev_page, prev_index, next_page, next_index) tuples for edges that
        were actually applied and represent TABLE continuations.
    """

    verified_table_edges: set[tuple[int, int, int, int]] = set()

    for edge in applied_edges:
        if (
            edge.get("applied")
            and edge.get("is_continuation")
            and (edge.get("continuation_kind") or "").lower() == "table"
        ):
            verified_table_edges.add(
                (
                    int(edge["prev_page"]),
                    int(edge["prev_index"]),
                    int(edge["next_page"]),
                    int(edge["next_index"]),
                )
            )

    return verified_table_edges


def _deduplicate_and_sort_edge_records(
    edge_records: list[EdgeVerdictRecord],
) -> tuple[list[EdgeVerdictRecord], list[dict[str, Any]]]:
    """Collapse exact duplicate edge records and reject conflicting duplicates.

    Verification is the source of truth for per-boundary candidate selection. Compile
    therefore must not re-rank competing records for the same page boundary. This
    helper only:

    1. Collapses exact duplicate records for the same boundary when their
        compile-relevant semantics are identical.
    2. Raises a ValueError if multiple *conflicting* records exist for the same
        boundary, because that indicates inconsistent upstream verification outputs.

    NB: After deduplication, every retained record must be adjacent
    (next_page_index == prev_page_index + 1). Non-adjacent edge records are invalid for
    page-pair continuity compilation and cause a ValueError.

    Parameters
    ----------
    edge_records
        List of edge verdict records to collapse and sort.

    Returns
    -------
    tuple[list[EdgeVerdictRecord], list[dict[str, Any]]]
        (sorted_edge_records, boundary_duplicate_resolutions)

    Raises
    ------
    ValueError
        If any boundary has conflicting duplicate records or if any retained edge
        record is non-adjacent.
    """

    boundary_map: dict[tuple[int, int], list[EdgeVerdictRecord]] = {}

    # Group records only by page boundary first. Compile intentionally treats
    # verification's selected boundary candidates as authoritative, so all records for
    # the same prev/next page pair must either be exact duplicates or an error.
    for record in edge_records:
        boundary = (int(record.prev_page_index), int(record.next_page_index))
        boundary_map.setdefault(boundary, []).append(record)

    boundary_duplicate_resolutions: list[dict[str, Any]] = []
    selected: list[EdgeVerdictRecord] = []

    for boundary, records in sorted(boundary_map.items()):
        # If a boundary has only one record, there is nothing to resolve.
        if len(records) == 1:
            selected.append(records[0])
            continue

        identity_groups: dict[tuple[Any, ...], list[EdgeVerdictRecord]] = {}

        # Build a compile-relevant identity key.
        for record in records:
            verdict = record.verdict

            #  If two records differ on any of these fields, they are not true
            #  duplicates -> they represent conflicting upstream outputs for the same
            #  boundary.
            identity_key = (
                int(record.prev_page_index),
                int(record.next_page_index),
                int(record.prev_item_index),
                int(record.next_item_index),
                bool(verdict.is_continuation),
                verdict.continuation_kind.value,
                float(verdict.confidence),
                verdict.set_next_table_repeats_header,
            )
            identity_groups.setdefault(identity_key, []).append(record)

        # More than one identity group means the same page boundary has multiple
        # non-identical records. Compile refuses to choose between them because that
        # would amount to re-ranking verification output.
        if len(identity_groups) > 1:
            conflict_summaries = [
                _brief_edge_record(group_records[0])
                for _, group_records in sorted(
                    identity_groups.items(), key=lambda p: p[0]
                )
            ]
            raise ValueError(
                f"Conflicting edge records detected for boundary "
                f"{boundary}. Compile treats verification-selected boundary records "
                f"as ground truth and will not re-rank them. Conflicting records: "
                f"{conflict_summaries}"
            )

        # At this point every record for the boundary is semantically identical, so we
        # keep one representative and record how many exact duplicates were collapsed.
        kept = records[0]
        duplicate_count = len(records) - 1

        if duplicate_count > 0:
            logger.warning(
                f"Exact duplicate edge records detected for boundary {boundary}; "
                f"keeping one record and discarding {duplicate_count} duplicate(s)."
            )
            boundary_duplicate_resolutions.append(
                {
                    "prev_page": boundary[0],
                    "next_page": boundary[1],
                    "selected": _brief_edge_record(kept),
                    "discarded_count": duplicate_count,
                    "reason": "collapsed_exact_duplicate_edge_records",
                }
            )

        selected.append(kept)

    # Once boundary-level duplicates are resolved, sort the retained records into the
    # deterministic order expected by downstream compile logic.
    sorted_edge_records = sorted(
        selected,
        key=lambda record: (
            int(record.prev_page_index),
            int(record.next_page_index),
            int(record.prev_item_index),
            int(record.next_item_index),
        ),
    )

    # Compile only supports edges between adjacent page pairs. This check runs after
    # deduplication so the final retained set is validated exactly as it will be used.
    non_adjacent = [
        record
        for record in sorted_edge_records
        if int(record.next_page_index) != int(record.prev_page_index) + 1
    ]

    if non_adjacent:
        details = ", ".join(
            (
                f"{record.prev_page_index}->{record.next_page_index} "
                f"(prev_index={record.prev_item_index}, next_index={record.next_item_index})"
            )
            for record in non_adjacent
        )
        raise ValueError(
            f"Non-adjacent edge record(s) detected during continuity compilation: "
            f"{details}. Continuity compilation only supports adjacent page pairs."
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
        A tuple containing the initialized bools dictionary and effective local codes
        dictionary.
    """

    bools: dict[tuple[int, int], list[bool]] = {}
    normalized_local_codes: dict[tuple[int, int], str | None] = {}

    for page_index in sorted(page_irs):
        for item_index, item in enumerate(page_irs[page_index].items):
            key = (page_index, item_index)
            fp, tn = _boundary_to_bools(item.boundary)
            bools[key] = [fp, tn]
            normalized_local_codes[key] = _normalize_local_code(
                getattr(item, "local_code", None)
            )

    return bools, normalized_local_codes


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
        "set_next_table_repeats_header": verdict.set_next_table_repeats_header,
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
    """Patch table.repeats_header in an invariant-safe order.

    NB: repeats_header is a visual repeated-header signal. A False patch therefore does
    not imply any required header_row_count value. This helper only performs the
    repeats_header mutation itself and returns a concise change summary.

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

    # Clearing repeats_header to None first avoids transient invariant violations
    # during in-place updates if assignment validation is enabled on the schema.
    if table.repeats_header is not None:
        table.repeats_header = None

    table.repeats_header = desired

    return {
        "page": page_index,
        "item_index": item_index,
        "before": before_repeats,
        "after": desired,
    }


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


def _reconcile_dirty_item_states(
    *,
    bools: dict[tuple[int, int], list[bool]],
    dirty_keys: set[tuple[int, int]],
    page_irs: dict[int, PageIR],
    repeats_header_patch: dict[tuple[int, int], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconcile updated boundaries and repeats_header states for touched items.

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
        A tuple containing the lists of boundary changes, repeats_header changes, and
        repeats_header review flags.
    """

    boundary_changes: list[dict[str, Any]] = []
    repeats_header_changes: list[dict[str, Any]] = []
    repeats_header_review_flags: list[dict[str, Any]] = []
    all_keys = dirty_keys | set(repeats_header_patch.keys())

    for page_index, item_index in sorted(all_keys):
        flags = bools[(page_index, item_index)]
        item = page_irs[page_index].items[item_index]
        b_change, h_change, h_review = _reconcile_item_state(
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
        if h_review:
            repeats_header_review_flags.append(h_review)

    return boundary_changes, repeats_header_changes, repeats_header_review_flags


def _reconcile_item_state(
    *,
    item: Block | Table,
    item_index: int,
    flags: list[bool],
    page_index: int,
    repeats_header_patch: dict[tuple[int, int], bool],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Update item boundary and table headers based on calculated flags.

    Explicit repeats_header patches are assumed to have already passed
    verification-time validation. This function applies them in an invariant-safe order
    while also clearing repeats_header when a table no longer has a continuation
    boundary. Any review notes are returned separately from actual header changes.

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
    tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]
        A tuple of (boundary_change, header_change, header_review_flag) summaries, or
        None values when no output is needed.
    """

    from_prev, to_next = flags
    boundary_change: dict[str, Any] | None = None
    header_change: dict[str, Any] | None = None
    header_review_flag: dict[str, Any] | None = None

    before_boundary = item.boundary
    after_boundary = _bools_to_boundary(from_prev=from_prev, to_next=to_next)

    is_table = item.kind == "table"
    table: Table | None = item if is_table else None
    before_repeats = table.repeats_header if table is not None else None

    # If a table is about to transition into a non-continuation boundary state, clear
    # repeats_header BEFORE updating the boundary. This avoids transient invariant
    # violations if validate_assignment is enabled on the schema.
    if (
        is_table
        and table is not None
        and after_boundary not in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
        and table.repeats_header is not None
    ):
        header_change = _patch_repeats_header(
            desired=None, item_index=item_index, page_index=page_index, table=table
        )

    if before_boundary != after_boundary:
        boundary_change = {
            "page": page_index,
            "item_index": item_index,
            "before": getattr(before_boundary, "value", None),
            "after": after_boundary.value,
        }
        item.boundary = after_boundary

    if not is_table or table is None:
        return boundary_change, header_change, header_review_flag

    # Connection broken -> repeats_header must be None. The only review case worth
    # flagging here is a downgraded continuation that previously had an
    # explicit/extracted False visual signal *and* zero extracted header rows, because
    # that may indicate a now-standalone table starting without any header row.
    if after_boundary not in {ItemBoundary.RESUMED, ItemBoundary.BOTH}:
        if (
            before_boundary in {ItemBoundary.RESUMED, ItemBoundary.BOTH}
            and before_repeats is False
            and table.header_row_count == 0
        ):
            logger.warning(
                f"Page {page_index} item {item_index}: boundary downgraded to "
                f"{after_boundary.value} after a non-repeated-header table state with "
                f"header_row_count=0. This table may need manual review."
            )
            header_review_flag = {
                "page": page_index,
                "item_index": item_index,
                "before_boundary": getattr(before_boundary, "value", None),
                "after_boundary": after_boundary.value,
                "before_repeats_header": before_repeats,
                "header_row_count": table.header_row_count,
                "reason": (
                    "boundary_downgraded_after_non_repeated_header_state_with_zero_header_rows"
                ),
            }

        return boundary_change, header_change, header_review_flag

    # Connection exists -> Apply explicit patch if present.
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

    return boundary_change, header_change, header_review_flag


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


def compile_continuity_from_edge_verdicts(
    *,
    edge_records: list[EdgeVerdictRecord],
    min_confidence_to_patch: float,
    min_confidence_to_select_positive: float,
    page_irs: dict[int, PageIR],
    verification_dirs: PageIRVerificationDirs,
) -> set[tuple[int, int, int, int]]:
    """Apply all continuity decisions in one pass as follows:

    1. Positive edge -> set prev.to_next and next.from_prev
    2. Negative edge -> clear ONLY that directional connection
    3. Then recompute ItemBoundary enums from bits and also enforce repeats_header
        consistency with boundary state.

    Parameters
    ----------
    edge_records
        List of edge verdict records.
    min_confidence_to_patch
        Minimum confidence threshold to apply edits.
    min_confidence_to_select_positive
        Verification-time selection threshold carried through for provenance in the
        compile report. Compile treats incoming edge_records as already
        verification-selected and does not re-rank per-boundary candidates.
    page_irs
        Mapping of page_index to PageIR objects.
    verification_dirs
        Directory paths for the verification run, used for writing the continuity
        compile report.

    Returns
    -------
    set[tuple[int, int, int, int]]
        The applied VERIFIED table-continuation edges as
        (prev_page, prev_index, next_page, next_index) tuples.
    """

    bools, effective_local_codes = _initialize_states(page_irs)
    sorted_edge_records, boundary_duplicate_resolutions = (
        _deduplicate_and_sort_edge_records(edge_records)
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

    (
        boundary_changes,
        repeats_header_changes,
        repeats_header_review_flags,
    ) = _reconcile_dirty_item_states(
        bools=bools,
        dirty_keys=dirty_keys,
        page_irs=page_irs,
        repeats_header_patch=repeats_header_patch,
    )

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

    compile_report = {
        "applied_edges": applied_edges,
        "boundary_duplicate_resolutions": boundary_duplicate_resolutions,
        "boundary_changes": boundary_changes,
        "selection_policy": {
            "min_confidence_to_patch": min_confidence_to_patch,
            "min_confidence_to_select_positive": min_confidence_to_select_positive,
        },
        "local_code_changes": local_code_changes,
        "local_code_patch_skips": local_code_patch_skips,
        "local_code_propagation_conflicts": local_code_propagation_conflicts,
        "local_code_conflicts": local_code_conflicts,
        "repeats_header_changes": repeats_header_changes,
        "repeats_header_review_flags": repeats_header_review_flags,
    }

    write_to_json(
        fp=verification_dirs.root / "continuity_compile_report.json",
        json_info=compile_report,
    )

    return _collect_verified_table_continuation_edges(applied_edges)


def run_compile_step(
    *,
    config: VerificationConfig,
    edge_records: list[EdgeVerdictRecord],
    page_irs: dict[int, PageIR],
    verification_dirs: PageIRVerificationDirs,
) -> tuple[bool, set[tuple[int, int, int, int]] | None]:
    """Execute the compile step, skipping if outputs already exist and overwrite=False.

    Parameters
    ----------
    config
        The verification configuration, including thresholds and overwrite flag.
    verification_dirs
        Directory paths for the verification run, used for checking existing outputs
        and writing the continuity compile report.
    edge_records
        List of edge verdict records to compile into continuity decisions.
    page_irs
        Mapping of page_index to PageIR objects to be mutated in-place based on
        compiled continuity decisions.

    Returns
    -------
    tuple[bool, set[tuple[int, int, int, int]] | None]
        A tuple of (did_run_compile, verified_table_edges), where did_run_compile
        indicates whether the compile step was executed, and verified_table_edges is a
        set of (prev_page, prev_index, next_page, next_index) tuples for VERIFIED
        table-continuation edges if compile was run, or None if compile was skipped.
    """

    compile_report_fp = verification_dirs.root / "continuity_compile_report.json"

    if not config.overwrite and compile_report_fp.exists():
        logger.warning(
            "Skipping continuity compile because continuity_compile_report.json "
            "already exists and overwrite=False."
        )
        return False, None

    verified_table_edges = compile_continuity_from_edge_verdicts(
        edge_records=edge_records,
        min_confidence_to_patch=config.min_confidence_to_patch,
        min_confidence_to_select_positive=config.min_confidence_to_select_positive,
        page_irs=page_irs,
        verification_dirs=verification_dirs,
    )
    return True, verified_table_edges
