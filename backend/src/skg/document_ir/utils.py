"""This module contains utility functions for document Intermediate Representations."""

# Standard Library
import re
import uuid

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Union

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.schemas import (
    BlockSegment,
    BlockSlice,
    Segment,
    SegmentProvenance,
    TableSegment,
    TableSlice,
)
from skg.page_ir_extraction.schemas import (
    Block,
    ListItem,
    PageIR,
    Table,
    TableRow,
    TextUnit,
)
from skg.schemas import RunCtx
from skg.utils.constants import BlockType, ItemBoundary
from skg.utils.general import (
    compute_sha256_hex,
    make_dir,
    open_json_type,
    write_to_json,
)

ItemKey = tuple[int, int]
ChainItem = tuple[int, int, Union[Table, Block]]


@dataclass(frozen=True)
class DocumentIRDirs:
    """Dataclass for document IR directories."""

    root: Path


def _append_rejected_warnings(
    *,
    is_prev: bool,
    items: list,
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

    for ridx in rejected_indices:
        orig_idx, item = items[ridx]
        warnings.append(
            f"Skipped stitching candidate on {'previous' if is_prev else 'next'} page "
            f"because it is {reason} by non-artifact content (would reorder content): "
            f"page={page_ir.page_index} item_index={orig_idx} "
            f"kind={item.kind} boundary={item.boundary.value}"
        )


def _append_unmatched_warnings(
    *,
    current_page_ir: PageIR,
    next_candidates: list[int],
    next_items: list,
    next_page_ir: PageIR,
    prev_candidates: list[int],
    prev_items: list,
    warnings: list[str],
) -> None:
    """Append warnings when valid candidates exist on one side but not the other.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
    next_candidates
        A list of indices of valid next-page candidates.
    next_items
        The next page's normalized items list.
    next_page_ir
        The next PageIR.
    prev_candidates
        A list of indices of valid previous-page candidates.
    prev_items
        The previous page's normalized items list.
    warnings
        A list to append warning messages to.
    """

    if prev_candidates and not next_candidates:
        for pidx in prev_candidates:
            prev_orig_idx, prev_item = prev_items[pidx]
            warnings.append(
                f"Unmatched continuation on previous page (TRUNCATED/BOTH) "
                f"- no eligible next-page candidate: "
                f"page={current_page_ir.page_index} item_index={prev_orig_idx} "
                f"kind={prev_item.kind} boundary={prev_item.boundary.value}"
            )

    if next_candidates and not prev_candidates:
        for nidx in next_candidates:
            next_orig_idx, next_item = next_items[nidx]
            warnings.append(
                f"Unmatched continuation on next page (RESUMED/BOTH) - no "
                f"eligible previous-page candidate: "
                f"page={next_page_ir.page_index} item_index={next_orig_idx} "
                f"kind={next_item.kind} boundary={next_item.boundary.value}"
            )


def _drop_repeated_header(
    *,
    base_header_rows: list[TableRow],
    header_row_count: int,
    next_table: Table,
) -> list[TableRow]:
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
    list[TableRow]
        Rows to add from next_table with repeated header removed if needed.
    """

    rows_to_add = list(next_table.rows)
    if header_row_count <= 0:
        return rows_to_add

    if next_table.repeats_header is True:
        return rows_to_add[header_row_count:]

    if next_table.repeats_header is False:
        return rows_to_add

    # Unknown: detect by exact header-row match to base.
    maybe_header = rows_to_add[:header_row_count]
    if base_header_rows and _rows_match(base_header_rows, maybe_header):
        return rows_to_add[header_row_count:]

    return rows_to_add


def _find_next_candidates(items: list[Any]) -> tuple[list[int], list[int]]:
    """Find items on the next page eligible for stitching.

    Parameters
    ----------
    items
        The next page's normalized items list.

    Returns
    -------
    tuple[list[int], list[int]]
        A tuple containing:
            - A list of indices of valid next-page candidates.
            - A list of indices of rejected next-page candidates.
    """

    rejected, valid = [], []
    for idx, (_, item) in enumerate(items):
        if item.boundary not in (ItemBoundary.RESUMED, ItemBoundary.BOTH):
            continue

        if _next_candidate_is_safe_to_stitch(
            next_item=item, next_item_idx=idx, next_page_items=items
        ):
            valid.append(idx)
        else:
            rejected.append(idx)

    return valid, rejected


def _find_prev_candidates(items: list[Any]) -> tuple[list[int], list[int]]:
    """Find items on the previous page eligible for stitching.

    Parameters
    ----------
    items
        The previous page's normalized items list.

    Returns
    -------
    tuple[list[int], list[int]]
        A tuple containing:
            - A list of indices of valid previous-page candidates.
            - A list of indices of rejected previous-page candidates.
    """

    rejected, valid = [], []
    for idx, (_, item) in enumerate(items):
        if item.boundary not in (ItemBoundary.TRUNCATED, ItemBoundary.BOTH):
            continue

        if _prev_candidate_is_safe_to_stitch(
            prev_item=item, prev_item_idx=idx, prev_page_items=items
        ):
            valid.append(idx)
        else:
            rejected.append(idx)

    return valid, rejected


def _is_artifact_block(item: Union[Table, Block]) -> bool:
    """Return True if the item is an artifact block.

    Parameters
    ----------
    item
        The item to check.

    Returns
    -------
    bool
        True if the item is an artifact block.
    """

    return isinstance(item, Block) and item.block_type == BlockType.ARTIFACT


def _is_safe_interstitial_block(*, next_item: object, prior: Block) -> bool:
    """Determine if it's safe to ignore 'prior' when stitching 'next_item'.

    Parameters
    ----------
    next_item
        The next item.
    prior
        The prior block.

    Returns
    -------
    bool
        True if 'prior' can be ignored for the purpose of deciding whether it's safe to
        stitch 'next_item' to the previous page without causing meaningful reordering.
    """

    _continued_re = re.compile(r"\bcontinued\b", re.IGNORECASE)
    _table_figure_re = re.compile(r"^(table|figure)\s+\d+\b", re.IGNORECASE)

    # Allow captions immediately before tables.
    if prior.block_type == BlockType.CAPTION and isinstance(next_item, Table):
        return True

    # Allow obvious "continued" headings before a continued table/figure.
    if prior.block_type == BlockType.HEADING and isinstance(next_item, Table):
        txt = (prior.text.text if prior.text else "").strip()

        if _continued_re.search(txt):
            return True
        if _table_figure_re.match(txt):
            return True

        # Allow if local_codes match.
        if getattr(prior, "local_code", None) and getattr(
            next_item, "local_code", None
        ):
            if prior.local_code.strip() == next_item.local_code.strip():
                return True

    return False


def _match_candidates(
    *,
    current_page_ir: PageIR,
    next_candidates: list[int],
    next_page_ir: PageIR,
    next_page_items: list,
    prev_candidates: list[int],
    prev_page_items: list,
) -> dict[tuple[int, int], tuple[int, int]]:
    """Sort candidates by proximity and find the best matches.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
    next_candidates
        A list of indices of valid next-page candidates.
    next_page_ir
        The next PageIR.
    next_page_items
        The next page's normalized items list.
    prev_candidates
        A list of indices of valid previous-page candidates.
    prev_page_items
        The previous page's normalized items list.

    Returns
    -------
    dict[tuple[int, int], tuple[int, int]]
        Forward links for items that continue across the page break.
    """

    # Sort bottom of previous page by highest y-coordinate.
    prev_candidates.sort(
        key=lambda idx: float(prev_page_items[idx][1].bbox[3]),  # pylint: disable=W0640
        reverse=True,
    )
    # Sort top of next page by lowest y-coordinate.
    next_candidates.sort(
        key=lambda idx: float(next_page_items[idx][1].bbox[1])  # pylint: disable=W0640
    )

    page_pair_links: dict[tuple[int, int], tuple[int, int]] = {}
    used_next_indices: set[int] = set()

    for pidx in prev_candidates:
        prev_orig_idx, prev_item = prev_page_items[pidx]
        best: Optional[tuple[int, int]] = None  # (score, nidx)

        # Find first compatible next candidate not used.
        for nidx in next_candidates:
            if nidx in used_next_indices:
                continue

            next_item = next_page_items[nidx][1]

            if not compatible_kinds_for_stitch(
                next_item=next_item, prev_item=prev_item
            ):
                continue

            score = _match_score(next_item=next_item, prev_item=prev_item)

            if best is None or score > best[0]:  # pylint: disable=E1136
                best = (score, nidx)

        if best:
            best_nidx = best[1]

            # Retrieve the ORIGINAL index of the matched next item.
            match_orig_idx = next_page_items[best_nidx][0]

            # Store page pair link: (Page A, Orig Index A) -> (Page B, Orig Index B).
            page_pair_links[(current_page_ir.page_index, prev_orig_idx)] = (
                next_page_ir.page_index,
                match_orig_idx,
            )
            used_next_indices.add(best_nidx)

    return page_pair_links


def _match_score(
    *,
    next_item: Union[Table, Block],
    prev_item: Union[Table, Block],
) -> int:
    """Score a potential continuation match (higher is better).

    Parameters
    ----------
    next_item
        The next item.
    prev_item
        The previous item.

    Returns
    -------
    int
        The match score.
    """

    if isinstance(prev_item, Table) and isinstance(next_item, Table):
        score = 0
        if (
            prev_item.local_code
            and next_item.local_code
            and prev_item.local_code.strip() == next_item.local_code.strip()
        ):
            score += 5
        if table_schema_fingerprint(prev_item) == table_schema_fingerprint(next_item):
            score += 4

        # Same header_row_count is a mild positive.
        if prev_item.header_row_count == next_item.header_row_count:
            score += 1
        return score

    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        score = 0
        if prev_item.block_type == next_item.block_type:
            score += 2
        if (
            prev_item.local_code
            and next_item.local_code
            and prev_item.local_code.strip() == next_item.local_code.strip()
        ):
            score += 1
        return score

    return -999


def _next_candidate_is_safe_to_stitch(
    *,
    next_item: Union[Table, Block],
    next_item_idx: int,
    next_page_items: list[tuple[int, Union[Table, Block]]],
) -> bool:
    """Determine if it's safe to stitch to the next-page candidate. Only stitch to a
    next-page candidate if nothing non-artifact precedes it. Otherwise stitching would
    reorder content (because the stitched segment is anchored on the previous page).
    Exception: allow CAPTION blocks before tables (common: "Table X (continued)").

    Parameters
    ----------
    next_item
        The next item.
    next_item_idx
        The index of the next item in the next page's normalized items list.
    next_page_items
        The next page's normalized items list.

    Returns
    -------
    bool
        True if it's safe to stitch to the next-page candidate.
    """

    for _, prior in next_page_items[:next_item_idx]:
        if _is_artifact_block(prior):
            continue

        if isinstance(prior, Block) and _is_safe_interstitial_block(
            next_item=next_item, prior=prior
        ):
            continue

        return False

    return True


def _normalize_text(text: Optional[str]) -> str:
    """Normalize text for comparisons.

    Parameters
    ----------
    text
        The text to normalize.

    Returns
    -------
    str
        The normalized text.
    """

    if text is None:
        return ""

    # Collapse whitespace and strip.
    return re.sub(r"\s+", " ", text).strip().lower()


def _prev_candidate_is_safe_to_stitch(
    *,
    prev_item: Union[Table, Block],
    prev_item_idx: int,
    prev_page_items: list[tuple[int, Union[Table, Block]]],
) -> bool:
    """Determine if it's safe to stitch from the previous-page candidate. Only stitch
    from a previous-page candidate if nothing non-artifact follows it. Exception: allow
    CAPTION blocks after tables (e.g., Source/Note lines).

    Parameters
    ----------
    prev_item
        The previous item.
    prev_item_idx
        The index of the previous item in the previous page's normalized items list.
    prev_page_items
        The previous page's normalized items list.

    Returns
    -------
    bool
        True if it's safe to stitch from the previous-page candidate.
    """

    for _, later in prev_page_items[prev_item_idx + 1 :]:
        if _is_artifact_block(later):
            continue

        if (
            isinstance(prev_item, Table)
            and isinstance(later, Block)
            and later.block_type == BlockType.CAPTION
        ):
            continue

        return False

    return True


def _process_page_pair(
    *,
    current_page_ir: PageIR,
    next_page_ir: PageIR,
    next_page_items: list,
    prev_page_items: list,
    warnings: list[str],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Orchestrate candidate finding, warning logging, and linking for a single pair of
    pages.

    The process is as follows:

    1. Identify candidates (valid vs rejected).
    2. Append warnings for unsafe candidates (rejected).
    3. Append warnings for scenarios where no candidates exist.
    4. Compute links between valid candidates.

    Parameters
    ----------
    current_page_ir
        The current PageIR.
    next_page_ir
        The next PageIR.
    next_page_items
        The next page's normalized items list.
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
    prev_candidates, prev_rejected = _find_prev_candidates(prev_page_items)
    next_candidates, next_rejected = _find_next_candidates(next_page_items)

    # 2.
    _append_rejected_warnings(
        is_prev=True,
        items=prev_page_items,
        page_ir=current_page_ir,
        rejected_indices=prev_rejected,
        warnings=warnings,
    )
    _append_rejected_warnings(
        is_prev=False,
        items=next_page_items,
        page_ir=next_page_ir,
        rejected_indices=next_rejected,
        warnings=warnings,
    )

    # 3.
    if not prev_candidates or not next_candidates:
        _append_unmatched_warnings(
            current_page_ir=current_page_ir,
            next_candidates=next_candidates,
            next_items=next_page_items,
            next_page_ir=next_page_ir,
            prev_candidates=prev_candidates,
            prev_items=prev_page_items,
            warnings=warnings,
        )
        return {}

    # 4.
    return _match_candidates(
        current_page_ir=current_page_ir,
        next_page_ir=next_page_ir,
        prev_candidates=prev_candidates,
        next_candidates=next_candidates,
        prev_page_items=prev_page_items,
        next_page_items=next_page_items,
    )


def _rows_match(a: Sequence[TableRow], b: Sequence[TableRow]) -> bool:
    """Return True if two sequences of table rows match in content.

    Parameters
    ----------
    a
        The first sequence of table rows.
    b
        The second sequence of table rows.

    Returns
    -------
    bool
        True if the two sequences of table rows match in content.
    """

    if len(a) != len(b):
        return False
    return all(_row_signature(ra) == _row_signature(rb) for ra, rb in zip(a, b))


def _row_signature(row: TableRow) -> tuple[str, ...]:
    """Create a stable signature for a table row based on cell texts.

    Parameters
    ----------
    row
        The table row.

    Returns
    -------
    tuple[str, ...]
        The row signature.
    """

    sig: list[str] = []
    for cell in row.cells:
        # TableCell.text is a TextUnit or None.
        if cell.text is None:
            sig.append("")
        else:
            sig.append(_normalize_text(cell.text.text))
    return tuple(sig)


def assert_page_items_consumed_exactly_once(
    *,
    items_with_idx: dict[int, list[tuple[int, Union[Table, Block]]]],
    segments: list[Segment],
    strict: bool = True,
    warnings: list[str],
) -> None:
    """Validate that every normalized PageIR item is consumed exactly once by segments.
    Expected universe of segments is derived from `items_with_idx` (i.e.,
    post-normalization, with artifacts filtered if keep_artifacts=False upstream).

    Parameters
    ----------
    items_with_idx
        Mapping of page_index to list of (item_index, item) tuples after normalization.
    segments
        The list of segments to validate.
    strict
        If True, raise ValueError on integrity violations; else log warnings.
    warnings
        A list to append warning messages to (used if strict=False).

    Raises
    ------
    ValueError
        If strict=True and any of:
            - Missing items (expected but not present in any segment.provenance).
            - Extra items (present in segment.provenance but not expected).
            - Duplicate consumption (same (page_index,item_index) appears in > 1
                segment.provenance).
    """

    expected: set[ItemKey] = {
        (page_idx, orig_item_idx)
        for page_idx, items in items_with_idx.items()
        for (orig_item_idx, _item) in items
    }

    used_by: dict[ItemKey, list[str]] = defaultdict(list)
    for seg in segments:
        for prov in seg.provenance:
            k: ItemKey = (prov.page_index, prov.item_index)
            used_by[k].append(seg.segment_key)

    seen = set(used_by.keys())

    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    dupes = sorted([k for k, seg_keys in used_by.items() if len(seg_keys) > 1])

    if not (missing or extra or dupes):
        return

    # Build a readable error message (show a sample, not everything).
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
        lines = []
        for k in dupes[:10]:
            lines.append(f"  {k} -> {used_by[k]}")
        if len(dupes) > 10:
            lines.append(f"  ... (+{len(dupes) - 10} more)")
        dupe_details = "\nDuplicate details (page,item -> segment_keys):\n" + "\n".join(
            lines
        )

    msg = (
        "Integrity check failed: normalized PageIR items were not consumed exactly once.\n"
        f"Missing (expected but not consumed): {_fmt_keys(missing)}\n"
        f"Extra (consumed but not expected): {_fmt_keys(extra)}\n"
        f"Duplicates (consumed >1 time): {_fmt_keys(dupes)}"
        f"{dupe_details}"
    )

    if strict:
        raise ValueError(msg)

    # Non-strict mode: record a warning instead of failing.
    warnings.append(msg)


def block_segment_key(block: Block) -> str:
    """Deterministic key for a block segment. For headings/captions, key still exists,
    but these should not be stitched by design.

    Parameters
    ----------
    block
        The curriculum block.

    Returns
    -------
    str
        The segment key.
    """

    base = f"{block.block_type.value}|{_normalize_text(block.local_code)}"
    txt = ""

    if block.text is not None:
        txt = _normalize_text(block.text.text)[:400]
    elif block.list_items:
        txt = _normalize_text(
            " ".join((li.text.text if li.text else "") for li in block.list_items)
        )[:400]

    return f"block:{block.block_type.value}:{compute_sha256_hex(s=base + '|' + txt)}"


def build_continuation_chain(
    *,
    items_lookup: dict[int, dict[int, Union[Table, Block]]],
    links: dict[ItemKey, ItemKey],
    start_item: Union[Table, Block],
    start_key: ItemKey,
) -> tuple[list[ChainItem], list[str]]:
    """Follow links to build a list of items belonging to one logical segment.

    Parameters
    ----------
    items_lookup
        A mapping of page_index to item_index to Item. This allows O(1) lookup of items
        by their original index, even if intermediate artifacts were filtered out.
    links
        A mapping of (page_index, item_index) to (next_page_index, next_item_index) for
        items that continue across page breaks.
    start_item
        The starting item of the chain.
    start_key
        The (page_index, item_index) of the starting item.

    Returns
    -------
    tuple[list[ChainItem], list[str]]
        A tuple containing:
          - A list of (page_index, item_index, item) tuples representing the chain of
            continuation items.
          - A list of warning messages encountered during chain building.
    """

    chain: list[ChainItem] = []
    warnings: list[str] = []

    current_page_idx, current_item_idx = start_key
    current_item = start_item

    while True:
        chain.append((current_page_idx, current_item_idx, current_item))
        fwd = links.get((current_page_idx, current_item_idx))

        if not fwd:
            break

        next_page_idx, next_item_idx = fwd
        next_page_map = items_lookup.get(next_page_idx)

        # Validation: Broken link (page missing).
        if next_page_map is None:
            warnings.append(
                f"Broken link from {(current_page_idx, current_item_idx)} -> {fwd}: "
                f"Page {next_page_idx} not found in lookup."
            )
            break

        # Look up the next item by original index.
        next_item = next_page_map.get(next_item_idx)

        # Validation: Broken link (item missing on page).
        if next_item is None:
            warnings.append(
                f"Broken link from {(current_page_idx, current_item_idx)} -> {fwd}: "
                f"Item {next_item_idx} not found on page {next_page_idx}."
            )
            break

        # Validation: incompatible kinds.
        if not compatible_kinds_for_stitch(next_item=next_item, prev_item=current_item):
            warnings.append(
                f"Incompatible continuation kinds at {(current_page_idx, current_item_idx)} -> {fwd}"
            )
            break

        # Advance to next item.
        current_page_idx, current_item_idx = next_page_idx, next_item_idx
        current_item = next_item

    return chain, warnings


def compatible_kinds_for_stitch(
    *,
    next_item: Union[Table, Block],
    prev_item: Union[Table, Block],
) -> bool:
    """Return True if two items are stitch-compatible.

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

    if isinstance(prev_item, Table) and isinstance(next_item, Table):
        return True

    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        # Headings/captions should never be part of a stitched continuation, but we
        # keep this conservative check anyway.
        if prev_item.block_type in (BlockType.HEADING, BlockType.CAPTION):
            return False
        if next_item.block_type in (BlockType.HEADING, BlockType.CAPTION):
            return False

        # Restrict stitching to the same block_type.
        return prev_item.block_type == next_item.block_type

    return False


def compute_page_break_links(
    *, keep_artifacts: bool = True, page_irs: list[PageIR], warnings: list[str]
) -> dict[tuple[int, int], tuple[int, int]]:
    """Compute a mapping of (page_i, item_idx) --> (page_i+1, item_idx) links for
    continuations.

    This uses only the already-verified item boundaries:
      - prev item boundary must continue to next (TRUNCATED or BOTH)
      - next item boundary must continue from prev (RESUMED or BOTH)

    We choose candidates nearest the bottom/top of each page to resolve ambiguity.

    Parameters
    ----------
    keep_artifacts
        If True, keep artifact blocks (page numbers, running headers/footers) when
        normalizing page items for stitching.
    page_irs
        The list of PageIRs for the document.
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[tuple[int, int], tuple[int, int]]
        Forward links for items that continue across a page break.
    """

    # Normalize pages to item lists (artifact-filtered)
    normalized_pages: list[list[tuple[int, Union[Table, Block]]]] = [
        normalize_page_items(keep_artifacts=keep_artifacts, page_ir=page_ir)
        for page_ir in page_irs
    ]
    links: dict[tuple[int, int], tuple[int, int]] = {}

    for i in range(len(page_irs) - 1):
        # Process one pair of pages at a time.
        page_pair_links = _process_page_pair(
            current_page_ir=page_irs[i],
            next_page_ir=page_irs[i + 1],
            next_page_items=normalized_pages[i + 1],
            prev_page_items=normalized_pages[i],
            warnings=warnings,
        )
        links.update(page_pair_links)

    return links


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


def join_text_units(*, repair_hyphenation: bool = True, units: list[TextUnit]) -> str:
    """Join a list of TextUnit objects into a single combined_text. If
    repair_hyphenation=True, applies a conservative fix for line-end hyphens when the
    next segment begins with a lowercase letter.

    Parameters
    ----------
    repair_hyphenation
        If True, repair hyphenation at line breaks.
    units
        The list of TextUnit objects.

    Returns
    -------
    str
        The combined text.
    """

    texts = [u.text for u in units if u.text is not None]

    if not texts:
        return ""

    if not repair_hyphenation or len(texts) == 1:
        return "\n".join(texts)

    out: list[str] = [texts[0]]
    for nxt in texts[1:]:
        prev = out[-1]
        prev_strip = prev.rstrip()
        if prev_strip.endswith("-") and nxt and nxt[0].islower():
            # Remove trailing hyphen and join directly.
            out[-1] = prev_strip[:-1] + nxt.lstrip()
        else:
            out.append(nxt)
    return "\n".join(out)


def load_page_irs_from_verification(
    *, expected_doc_key: str, verified_page_irs_dir: Path
) -> list[PageIR]:
    """Load and validate all verified page IR JSONs from the verification output
    directory.

    Parameters
    ----------
    expected_doc_key
        The expected document key for all page IRs.
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
    if expected_doc_key is None:
        raise ValueError(
            "verification_run.json is missing extra.doc_key (expected_doc_key)."
        )

    doc_keys = {p.doc_key for p in page_irs if p.doc_key}
    pdf_names = {p.pdf_name for p in page_irs if p.pdf_name}

    if not doc_keys:
        raise ValueError(
            "All verified PageIRs are missing doc_key. "
            "Ensure step 1/2 populates PageIR.doc_key for every page."
        )
    if len(doc_keys) > 1 or len(pdf_names) > 1:
        raise ValueError(
            "Inconsistent pdf_name or doc_key across pages:\n"
            f"{sorted(doc_keys)}\n{sorted(pdf_names)}"
        )

    only_doc_key = next(iter(doc_keys))
    if only_doc_key != expected_doc_key:
        raise ValueError(f"Expected doc_key '{expected_doc_key}', got '{only_doc_key}'")

    # Validate coord space + dimensions + dpi consistency/presence.
    coord_spaces = {p.coord_space for p in page_irs if p.coord_space is not None}
    dpis = {p.dpi for p in page_irs if p.dpi is not None}
    heights = {p.image_height for p in page_irs if p.image_height is not None}
    widths = {p.image_width for p in page_irs if p.image_width is not None}

    if len(coord_spaces) > 1 or len(dpis) > 1 or len(widths) > 1 or len(heights) > 1:
        raise ValueError(
            "Inconsistent coordinate space, page dimensions, or dpi across pages:\n"
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


def materialize_segment(
    *,
    chain: list[ChainItem],
    item_index: int,
    page_index: int,
    repair_hyphenation: bool,
    warnings: list[str],
) -> Segment:
    """Dispatches the chain to the correct merging logic based on item type.

    Parameters
    ----------
    chain
        A list of (page_index, item_index, item) tuples representing a chain of
        continuation items.
    item_index
        The starting item index of the chain.
    page_index
        The starting page index of the chain.
    repair_hyphenation
        If True, repair hyphenation at line breaks when combining text units.
    warnings
        A list to append warning messages to.

    Returns
    -------
    Segment
        The merged Segment (BlockSegment or TableSegment).
    """

    first_item = chain[0][2]

    if isinstance(first_item, Table):
        table_chain = [(pi, ii, it) for pi, ii, it in chain if isinstance(it, Table)]
        if len(table_chain) != len(chain):
            warnings.append(
                f"Mixed-kind chain starting at {(page_index, item_index)}; kept as standalone."
            )
            # fallback: treat first item standalone
            table_chain = [(page_index, item_index, first_item)]
        return stitch_table_chain(chain=table_chain, warnings=warnings)

    block_chain = [(pi, ii, it) for pi, ii, it in chain if isinstance(it, Block)]
    if len(block_chain) != len(chain):
        warnings.append(
            f"Mixed-kind chain starting at {(page_index, item_index)}; kept as standalone."
        )
        block_chain = [(page_index, item_index, first_item)]
    return stitch_block_chain(chain=block_chain, repair_hyphenation=repair_hyphenation)


def normalize_page_items(
    *, keep_artifacts: bool, page_ir: PageIR
) -> list[tuple[int, Union[Table, Block]]]:
    """Normalize a PageIR items list for stitching.

    Parameters
    ----------
    keep_artifacts
        If True, keep artifact blocks (page numbers, running headers/footers).
    page_ir
        Source PageIR.

    Returns
    -------
    list[tuple[int, Union[Table, Block]]]
        List of (item_index, item) tuples after normalization.
    """

    items_with_idx = []
    for idx, item in enumerate(page_ir.items):
        if keep_artifacts or not _is_artifact_block(item):
            items_with_idx.append((idx, item))

    return items_with_idx


def persist_stitching_run(
    *, output_dir: Path, **kwargs: Any
) -> tuple[DocumentIRDirs, RunCtx]:
    """Persist stitching run metadata.

    Parameters
    ----------
    output_dir
        The output directory for the document IR JSON.
    kwargs
        Additional stitching run configuration parameters.

    Returns
    -------
    tuple[DocumentIRDirs, RunCtx]
        The created document IR directories and persisted stitching run metadata.
    """

    extra = kwargs.get("extra", {})
    extra.pop("status", None)
    stitching_dirs = create_document_ir_dirs(output_dir=output_dir)
    stitching_run = RunCtx(
        extra=extra, run_id=str(uuid.uuid4()), started_at=datetime.now(timezone.utc)
    )
    write_to_json(fp=output_dir / "stitching_run.json", json_info=stitching_run)
    logger.info(f"Stitching directory: {output_dir}")

    return stitching_dirs, stitching_run


def stitch_block_chain(
    *, chain: list[tuple[int, int, Block]], repair_hyphenation: bool = True
) -> BlockSegment:
    """Stitch a chain of block slices.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Block) tuples representing the
        slices to stitch.
    repair_hyphenation
        If True, repair hyphenation at line breaks when combining text units.

    Returns
    -------
    BlockSegment
        The stitched BlockSegment.
    """

    first_pi, first_ii, first = chain[0]  # pylint: disable=W0612
    seg_key = block_segment_key(first)

    slices: list[BlockSlice] = []
    provenance: list[SegmentProvenance] = []
    text_units: list[TextUnit] = []
    list_items: list[ListItem] = []
    figure_payload: Optional[dict[str, Any]] = None

    for pi, ii, b in chain:
        slices.append(
            BlockSlice(
                bbox=list(b.bbox),
                block_type=b.block_type,
                boundary=b.boundary,
                figure=(
                    b.figure.model_dump(mode="json") if b.figure is not None else None
                ),
                item_index=ii,
                list_items=list(b.list_items) if b.list_items else None,
                local_code=b.local_code,
                page_index=pi,
                text=b.text,
            )
        )
        provenance.append(
            SegmentProvenance(
                bbox=list(b.bbox),
                boundary=b.boundary,
                item_index=ii,
                kind="block",
                local_code=b.local_code,
                page_index=pi,
                repeats_header=None,
            )
        )

        if b.text is not None:
            text_units.append(b.text)
        if b.list_items:
            list_items.extend(b.list_items)
        if b.figure is not None:
            figure_payload = b.figure.model_dump(mode="json")

    combined_text: Optional[str] = None
    stitched_text: Optional[TextUnit] = first.text

    if text_units:
        combined_text = join_text_units(
            repair_hyphenation=repair_hyphenation, units=text_units
        )

        # If slice languages disagree, mark the stitched segment as mixed-language.
        langs = {tu.language for tu in text_units}
        if len(langs) > 1:
            # NB: Do NOT mutate any slice TextUnit --> create a new one instead.
            stitched_text = TextUnit(language="mul", text=combined_text, text_en=None)
        else:
            # Single language: avoid None if first slice lacked text.
            stitched_text = first.text or text_units[0]

    return BlockSegment(
        block_type=first.block_type,
        combined_text=combined_text,
        figure=figure_payload,
        list_items=list_items or (first.list_items if first.list_items else None),
        local_code=first.local_code,
        provenance=provenance,
        segment_key=seg_key,
        slices=slices,
        text=stitched_text,
    )


def stitch_table_chain(
    *, chain: list[tuple[int, int, Table]], warnings: list[str]
) -> TableSegment:
    """Stitch a chain of table slices.

    For local_code determination, the process is as follows:

    1. If first.local_code is None but a later slice has one, promote the first
        non-null code that we encounter.
    2. Once a code is known, carry it forward to later slices that are missing it.
    3. After the loop, if local_code was discovered mid-chain:
        a. Backfill slices[0].local_code and provenance[0].local_code if missing.
        b. Upgrade seg_key to table:{local_code} (so it prefers the human table label).

    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the
        slices to stitch.
    warnings
        A list to append warning messages to.

    Returns
    -------
    TableSegment
        The stitched TableSegment.
    """

    def _norm_code(x: str | None) -> str | None:
        """Normalize a local_code by stripping whitespace; return None if blank.

        Parameters
        ----------
        x
            The local_code to normalize.

        Returns
        -------
        str | None
            The normalized local_code or None.
        """

        return (x or "").strip() or None

    first_pi, first_ii, first = chain[0]
    first_code = _norm_code(first.local_code)

    # Promote local_code from later slices if the first slice is missing/blank it.
    local_code = first_code
    if local_code is None:
        for _pi, _ii, t in chain[1:]:
            t_code = _norm_code(t.local_code)
            if t_code:
                local_code = t_code
                break

    # Compute segment_key *after* local_code promotion.
    seg_key = (
        f"table:{local_code}"
        if local_code
        else f"table:schema:{table_schema_fingerprint(first)}"
    )

    header_row_count = int(first.header_row_count or 0)
    stitched_rows: list[TableRow] = list(first.rows)
    header_rows = stitched_rows[:header_row_count] if header_row_count > 0 else []

    slices: list[TableSlice] = [
        TableSlice(
            bbox=list(first.bbox),
            boundary=first.boundary,
            header_row_count=header_row_count,
            item_index=first_ii,
            local_code=local_code,  # ok to stamp with promoted code
            page_index=first_pi,
            repeats_header=first.repeats_header,
            rows=list(first.rows),
        )
    ]
    provenance: list[SegmentProvenance] = [
        SegmentProvenance(
            bbox=list(first.bbox),
            boundary=first.boundary,
            item_index=first_ii,
            local_code=local_code,
            kind="table",
            page_index=first_pi,
            repeats_header=first.repeats_header,
        )
    ]

    for pi, ii, t in chain[1:]:
        t_code = _norm_code(t.local_code)

        # Warn if codes conflict inside a stitched chain.
        if t_code and local_code and t_code != local_code:
            warnings.append(
                f"Conflicting local_code in table chain {seg_key}: "
                f"{local_code!r} vs {t_code!r} (page={pi}, item_index={ii}). "
                f"Keeping {local_code!r}."
            )

        # Carry local_code forward deterministically if missing in later slices.
        slice_local_code = t_code or local_code

        # Determine k for header dropping.
        next_hrc = int(t.header_row_count or 0)
        if next_hrc != header_row_count:
            warnings.append(
                f"header_row_count mismatch in table chain {seg_key}: "
                f"first={header_row_count} vs next={next_hrc} "
                f"(page={pi}, item_index={ii}). Using k=min(...) for safe header-drop."
            )
        k = min(header_row_count, next_hrc)

        slices.append(
            TableSlice(
                bbox=list(t.bbox),
                boundary=t.boundary,
                header_row_count=next_hrc,
                item_index=ii,
                local_code=slice_local_code,
                page_index=pi,
                repeats_header=t.repeats_header,
                rows=list(t.rows),
            )
        )
        provenance.append(
            SegmentProvenance(
                bbox=list(t.bbox),
                boundary=t.boundary,
                item_index=ii,
                kind="table",
                local_code=slice_local_code,
                page_index=pi,
                repeats_header=t.repeats_header,
            )
        )

        rows_to_add = _drop_repeated_header(
            base_header_rows=header_rows[:k], header_row_count=k, next_table=t
        )
        stitched_rows.extend(rows_to_add)

    # Ensure seg_key prefers table code if known.
    if local_code:
        seg_key = f"table:{local_code}"

    n_cols = max((len(r.cells) for r in stitched_rows), default=0)

    return TableSegment(
        header_row_count=header_row_count,
        header_rows=list(header_rows),
        local_code=local_code,
        n_cols=n_cols,
        provenance=provenance,
        rows=stitched_rows,
        segment_key=seg_key,
        slices=slices,
    )


def table_schema_fingerprint(table: Table) -> str:
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
    header_rows = list(table.rows[:hrc]) if hrc > 0 else []

    # Fall back to first row if header_count is 0.
    if not header_rows and table.rows:
        header_rows = [table.rows[0]]

    sig_rows = [",".join(_row_signature(r)) for r in header_rows]
    n_cols = max((len(r.cells) for r in table.rows), default=0)
    base = f"hrc={hrc}|ncols={n_cols}|rows={'||'.join(sig_rows)}"

    return compute_sha256_hex(n_hex=24, s=base)


def uniquify_segment_keys(*, segments: list[Segment]) -> list[Segment]:
    """Ensure segment_key is unique within a single DocumentIR.

    Segment keys are designed to be deterministic and content-based. In rare cases
    (e.g., repeated boilerplate text blocks or tables lacking local_code), collisions
    can occur. When collisions occur, suffix the key with provenance of the first slice
    (page_index/item_index) to disambiguate deterministically.

    NB: Segment keys only need to be unique within a *single* document IR. If there are
    no collisions, adding suffixes doesn't do anything.
    """

    counts = Counter(s.segment_key for s in segments)

    if all(c == 1 for c in counts.values()):
        return segments

    logger.warning(
        f"Segment key collisions detected: {counts}.\n\n"
        f"Segment keys: {[s.segment_key for s in segments]}"
    )

    used: set[str] = set()
    out: list[Segment] = []

    for s in segments:
        base = s.segment_key

        if counts[base] == 1 and base not in used:
            out.append(s)
            used.add(base)
            continue

        # Collision: add deterministic provenance suffix.
        if getattr(s, "slices", None):
            first = s.slices[0]
            suffix = f"#p{first.page_index:04d}i{first.item_index:04d}"
        else:
            suffix = "#unknown"

        candidate = f"{base}{suffix}"
        k = candidate
        n = 1

        while k in used:
            n += 1
            k = f"{candidate}#{n}"

        out.append(s.model_copy(update={"segment_key": k}))
        used.add(k)

    return out
