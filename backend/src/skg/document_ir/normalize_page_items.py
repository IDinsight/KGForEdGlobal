"""This module contains utility functions for normalizing page items for the document IR."""

# Standard Library
import re

from typing import Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.utils import normalize_local_code
from skg.page_ir_extraction.schemas import Block, PageIR, Table, TextUnit
from skg.page_ir_verification.utils import is_artifact
from skg.utils.constants import (
    BlockType,
    CaptionFigurePrefixes,
    CaptionTablePrefixes,
    ItemBoundary,
)

# Compiled regexes.
_FIGURE_PREFIX_RE = "|".join(re.escape(t) for t in CaptionFigurePrefixes)
_TABLE_PREFIX_RE = "|".join(re.escape(t) for t in CaptionTablePrefixes)
_FIGURE_CODE_RE = re.compile(
    rf"(?i)^\s*(?:{_FIGURE_PREFIX_RE})\s*(?:no\.?|n\.?|na\.)?\s*(?P<num>\d+(?:\.\d+)*)\s*(?:[:.\-–—]\s*)?"
)
_TABLE_CODE_RE = re.compile(
    rf"(?i)^\s*(?:{_TABLE_PREFIX_RE})\s*(?:no\.?|n\.?|na\.)?\s*(?P<num>\d+(?:\.\d+)*)\s*(?:[:.\-–—]\s*)?"
)
_TRAILING_SEP_RE = re.compile(r"[\s:.\-–—]+$")


def _classify_code_kind(code: str) -> Optional[str]:
    """Classify a local code as 'table', 'figure', or None.

    Uses the multilingual regex patterns (_TABLE_CODE_RE, _FIGURE_CODE_RE) to determine
    whether a code string refers to a table or figure, regardless of the language
    prefix. Unlike `_extract_table_or_figure_local_code`, this function does **not**
    canonicalize the code--it only classifies.

    Parameters
    ----------
    code
        The raw local code string.

    Returns
    -------
    Optional[str]
        `"table"`, `"figure"`, or `None`.
    """

    s = (code or "").strip()

    if not s:
        return None

    if _TABLE_CODE_RE.match(s) is not None:
        return "table"

    if _FIGURE_CODE_RE.match(s) is not None:
        return "figure"

    return None


def _extract_raw_table_or_figure_code(text: str) -> Optional[str]:
    """Extract a table/figure code from text, preserving the original prefix.

    Like `_extract_table_or_figure_local_code`, but returns the matched portion in its
    original form instead of canonicalizing (e.g., `"Tableau 4"` stays `"Tableau 4"`
    and not `"Table 4"`).

    Parameters
    ----------
    text
        The text to extract from.

    Returns
    -------
    Optional[str]
        The extracted code in its original form, or `None` if not found.
    """

    s = (text or "").strip()

    if not s:
        return None

    for regex in (_TABLE_CODE_RE, _FIGURE_CODE_RE):
        m = regex.match(s)

        if m is not None:
            # Strip trailing separators (:, ., -, —) that the regex may capture.
            return _TRAILING_SEP_RE.sub("", m.group(0)).strip()

    return None


def _extract_table_or_figure_local_code(text: str) -> Optional[str]:
    """Extract a canonical table/figure local_code (e.g., 'Table 4', 'Figure 2') from a
    label string.

    Supports multilingual caption prefixes via
    CaptionTablePrefixes/CaptionFigurePrefixes, and tolerates variants like
    'Table No. 4:'.

    Parameters
    ----------
    text
        The text to extract from.

    Returns
    -------
    Optional[str]
        The extracted local_code, or None if not found.
    """

    s = (text or "").strip()

    if not s:
        return None

    if (m := _TABLE_CODE_RE.match(s)) is not None:
        return f"Table {m.group('num')}"

    if (m := _FIGURE_CODE_RE.match(s)) is not None:
        return f"Figure {m.group('num')}"

    return None


def _find_next_non_artifact(
    *, items: list[tuple[int, Block | Table]], start_index: int
) -> tuple[int, int, Block | Table] | None:
    """Find the next item in the list that is not an ARTIFACT block.

    Parameters
    ----------
    items
        The list of items to search.
    start_index
        The index to start searching from.

    Returns
    -------
    tuple[int, int, Block | Table] | None
        A tuple containing (current_list_index, original_index, item) if found,
        otherwise None.
    """

    for j in range(start_index, len(items)):
        orig_idx, cand = items[j]

        if isinstance(cand, Block) and cand.block_type == BlockType.ARTIFACT:
            continue

        return j, orig_idx, cand

    return None


def _resolve_label_code(item: Block) -> Optional[str]:
    """Resolve a table/figure code from a label-like Block.

    Checks `local_code` first, then falls back to the block text.

    Parameters
    ----------
    item
        The Block to resolve the code from.

    Returns
    -------
    Optional[str]
        The resolved code in its original form, or `None` if not found.
    """

    if isinstance(item.local_code, str) and item.local_code.strip():
        if _classify_code_kind(item.local_code) is not None:
            return item.local_code.strip()

    if item.text is not None and isinstance(item.text, TextUnit):
        return _extract_raw_table_or_figure_code(item.text.text)

    return None


def _try_assign_immediate(
    *,
    code: str,
    label_info: tuple[int, Block],
    target_info: tuple[int, Block | Table],
    page_index: int,
    warnings: list[str],
) -> bool:
    """Attempt to assign the label code to the immediately following item. Check if the
    immediate next item matches the label type (Table code -> Table item,
    Figure code -> Figure item).

    NB: The `code` written to the target item is the **raw** form from the caption
    (e.g., `"Tableau 4"`), not a canonicalized English form. Canonical comparison (for
    conflict detection) uses `_extract_table_or_figure_local_code` internally.

    Parameters
    ----------
    code
        The resolved code to assign (raw form).
    label_info
        A tuple of (original item index, Block) for the label.
    target_info
        A tuple of (original item index, Block or Table) for the immediate next item.
    page_index
        The page index for logging context.
    warnings
        A list to append warning messages to.

    Returns
    -------
    bool
        True if the code was successfully assigned to the next item, False otherwise.
    """

    label_orig_index, label_item = label_info
    next_orig_index, next_item = target_info
    assigned = False
    did_write_code = False
    conflict_msg: Optional[str] = None

    code_kind = _classify_code_kind(code)

    if code_kind == "table" and isinstance(next_item, Table):
        # Use canonical forms for comparison only (never stored).
        existing_canon = _extract_table_or_figure_local_code(next_item.local_code)
        code_canon = _extract_table_or_figure_local_code(code)

        if not existing_canon:
            next_item.local_code = code
            assigned = True
            did_write_code = True
        elif existing_canon == code_canon:
            # Already consistent--treat as success so we DO NOT fallback-scan.
            assigned = True
        else:
            # Conflict--do NOT overwrite; warn; treat as success to avoid
            # mis-propagating elsewhere.
            conflict_msg = (
                f"Caption/table code conflict on page {page_index}: "
                f"caption='{code}' label_raw_index={label_orig_index}({label_item.block_type.value})->"
                f"target_raw_index={next_orig_index}({next_item.kind}) existing='{next_item.local_code}'."
            )
            assigned = True
    elif (
        code_kind == "figure"
        and isinstance(next_item, Block)
        and next_item.block_type == BlockType.FIGURE
    ):
        existing_canon = _extract_table_or_figure_local_code(next_item.local_code)
        code_canon = _extract_table_or_figure_local_code(code)

        if not existing_canon:
            next_item.local_code = code
            assigned = True
            did_write_code = True
        elif existing_canon == code_canon:
            assigned = True
        else:
            conflict_msg = (
                f"Caption/figure code conflict on page {page_index}: "
                f"caption='{code}' label_raw_index={label_orig_index}({label_item.block_type.value})->"
                f"target_raw_index={next_orig_index}({next_item.kind}) existing='{next_item.local_code}'."
            )
            assigned = True

    # Only log "propagated" when we actually wrote a code.
    if did_write_code:
        msg = (
            f"Propagated label code '{code}' on page {page_index}: "
            f"label_raw_index={label_orig_index}({label_item.block_type.value})->"
            f"target_raw_index={next_orig_index}({next_item.kind})."
        )
        logger.warning(msg)
        warnings.append(msg)
        return True

    # Log conflicts, but still return True to prevent fallback scan.
    if conflict_msg:
        logger.warning(conflict_msg)
        warnings.append(conflict_msg)
        return True

    # If already consistent, assigned=True and we return True silently.
    return assigned


def _try_fallback_scan(
    *,
    code: str,
    items: list[tuple[int, Block | Table]],
    label_orig_index: int,
    page_index: int,
    start_index: int,
    warnings: list[str],
) -> None:
    """Scan forward from a specific index to find the nearest unassigned Table. Used as
    a fallback when a Caption is not immediately followed by its Table. Stops scanning
    if another Caption or Label is encountered.

    NB: Writes the **raw** code form (e.g., `"Tableau 4"`) to the target table's
    `local_code`.

    Parameters
    ----------
    code
        The code to assign to the next Table (raw form).
    items
        The list of (original item index, item) tuples for the page.
    label_orig_index
        The original item index of the label for logging context.
    page_index
        The page index for logging context.
    start_index
        The index to start scanning from (immediately after the label).
    warnings
        A list to append warning messages to.
    """

    eligible_stop_types = {BlockType.CAPTION, BlockType.HEADING, BlockType.PARAGRAPH}

    for k in range(start_index, len(items)):
        k_orig_index, k_item = items[k]

        if isinstance(k_item, Block):
            if k_item.block_type == BlockType.ARTIFACT:
                continue

            # Stop scanning if we hit another caption or potential label.
            if k_item.block_type == BlockType.CAPTION:
                break

            if (
                k_item.block_type in eligible_stop_types
                and _resolve_label_code(k_item) is not None
            ):
                break

        if isinstance(k_item, Table):
            if not normalize_local_code(k_item.local_code):
                k_item.local_code = code
                msg = (
                    f"Propagated caption code '{code}' to nearest following table on page {page_index}: "
                    f"caption_raw_index={label_orig_index}->table_raw_index={k_orig_index}."
                )
                logger.warning(msg)
                warnings.append(msg)

            break


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


def propagate_caption_table_local_codes(
    *, items: list[tuple[int, Block | Table]], page_index: int, warnings: list[str]
) -> None:
    """Propagate Table/Figure codes from label blocks to the appropriate content item
    on the same page.

    NB: This function **mutates** `local_code` on target items in-place. The propagated
    code preserves the original form from the caption (e.g., `"Tableau 4"` stays
    `"Tableau 4"`; it is NOT canonicalized to `"Table 4"`).

    Parameters
    ----------
    items
        The page's normalized items list.
    page_index
        The page index.
    warnings
        A list to append warning messages to.
    """

    eligible_label_types = {BlockType.CAPTION, BlockType.HEADING, BlockType.PARAGRAPH}

    for i, (label_orig_index, label_item) in enumerate(items):
        if (
            not isinstance(label_item, Block)
            or label_item.block_type not in eligible_label_types
        ):
            continue

        code = _resolve_label_code(label_item)

        if not code:
            continue

        # Normalize label itself.
        if not (label_item.local_code or "").strip():
            label_item.local_code = code

        # Find immediate next content.
        next_data = _find_next_non_artifact(items=items, start_index=i + 1)
        if not next_data:
            continue

        next_idx, next_orig_index, next_item = next_data

        # Try immediate assignment (Table or Figure).
        was_assigned = _try_assign_immediate(
            code=code,
            label_info=(label_orig_index, label_item),
            target_info=(next_orig_index, next_item),
            page_index=page_index,
            warnings=warnings,
        )

        if was_assigned:
            continue

        # Fallback: scan forward for tables (only for Captions with table codes).
        if (
            label_item.block_type == BlockType.CAPTION
            and _classify_code_kind(code) == "table"
        ):
            _try_fallback_scan(
                code=code,
                items=items,
                label_orig_index=label_orig_index,
                page_index=page_index,
                start_index=next_idx + 1,
                warnings=warnings,
            )
