"""This module contains utility functions for document Intermediate Representations."""

# Standard Library
import re
import unicodedata
import uuid

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger
from pydantic import BaseModel

# Package Library
from skg.document_ir.schemas import (
    BlockSegment,
    BlockSlice,
    DocumentIR,
    DocumentPageMeta,
    SectionHeadingRef,
    Segment,
    SegmentProvenance,
    TableRowProvenance,
    TableSegment,
    TableSlice,
)
from skg.page_ir_extraction.schemas import (
    Block,
    ListItem,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.page_ir_verification.utils import (
    EdgeVerdictRecord,
    load_page_irs_from_verification,
    load_verification_verdicts,
)
from skg.schemas import ExtractionConfig, RunCtx, StitchingConfig
from skg.utils.constants import BlockType, CaptionFigurePrefixes, CaptionTablePrefixes
from skg.utils.general import make_dir, write_to_json

ItemKey = tuple[int, int]
ChainItem = tuple[int, int, Block | Table]

# Compiled regexes.
_ALPHA_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_DIGIT_RE = re.compile(r"\d")
_FIGURE_PREFIX_RE = "|".join(re.escape(t) for t in CaptionFigurePrefixes)
_TABLE_PREFIX_RE = "|".join(re.escape(t) for t in CaptionTablePrefixes)
_FIGURE_CODE_RE = re.compile(
    rf"(?i)^\s*(?:{_FIGURE_PREFIX_RE})\s*(?:no\.?|n\.?|na\.)?\s*(?P<num>\d+(?:\.\d+)*)\s*(?:[:.\-–—]\s*)?"
)
_LOCAL_CODE_RE = re.compile(r"\s+")
_TABLE_CODE_RE = re.compile(
    rf"(?i)^\s*(?:{_TABLE_PREFIX_RE})\s*(?:no\.?|n\.?|na\.)?\s*(?P<num>\d+(?:\.\d+)*)\s*(?:[:.\-–—]\s*)?"
)


@dataclass(frozen=True)
class DocumentIRDirs:
    """Dataclass for document IR directories."""

    root: Path


def _drop_repeated_header(
    *, base_header_rows: list[TableRow], header_row_count: int, next_table: Table
) -> tuple[list[TableRow], int]:
    """Return next_table.rows with repeated header removed if warranted.

    NB: Never drop "header" rows solely because the verifier says `repeats_header=True`
    if the continuation slice does not itself contain header rows (or if the would-be
    header rows do not match the base header). This avoids losing real content when a
    page begins with a checkpoint/section row inside the table grid (common in many
    curricula).

    Parameters
    ----------
    base_header_rows
        Header rows from the first slice.
    header_row_count
        Number of header rows (from the base slice).
    next_table
        The next table slice.

    Returns
    -------
    tuple[list[TableRow], int]
        (rows_to_add, num_dropped_header_rows) where num_dropped_header_rows is how
        many rows were removed from the start due to repeated header detection.
    """

    rows = next_table.rows
    dropped_count = 0

    def _base_matches_first_k(k: int) -> bool:
        """Check if the first k rows of the next table match the base header rows.

        Parameters
        ----------
        k
            The number of rows to check for a match.

        Returns
        -------
        bool
            True if the first k rows of the next table match the base header rows,
            False otherwise.
        """

        if k <= 0:
            return False

        maybe_header = rows[:k]

        if not base_header_rows or (len(base_header_rows) < k or len(maybe_header) < k):
            return False

        return all(
            row_signature(ra) == row_signature(rb)
            for ra, rb in zip(base_header_rows[:k], maybe_header)
        )

    # Determine how many rows to drop.
    if header_row_count > 0:
        # Does the full base header match exactly? Applies if repeats_header is True OR
        # Unknown (None). We skip this only if repeats_header is explicitly False.
        if next_table.repeats_header is not False and _base_matches_first_k(
            header_row_count
        ):
            dropped_count = header_row_count

        # Fallback check: partial match for explicit repeats. Only runs if
        # repeats_header is True AND the full match above failed.
        elif next_table.repeats_header is True:
            k = int(getattr(next_table, "header_row_count", 0) or 0)
            k = min(k, header_row_count)

            if k > 0 and _base_matches_first_k(k):
                dropped_count = k

    # Ensure we don't drop more rows than exist.
    dropped_count = min(dropped_count, len(rows))

    return rows[dropped_count:], dropped_count


def _expand_header_row_to_n_cols(
    *,
    local_code: str | None,
    n_cols: int,
    row: TableRow,
    segment_id: str,
    warnings: list[str],
) -> list[str]:
    """Expand a header row's cells based on col_span to match n_cols.

    Parameters
    ----------
    local_code
        The table's local code (for logging).
    n_cols
        The target number of columns to expand to.
    row
        The TableRow to expand.
    segment_id
        The TableSegment ID (for logging).
    warnings
        A list to append warning messages to.

    Returns
    -------
    list[str]
        The expanded header row as a list of cell texts, with length exactly n_cols.
    """

    expanded: list[str] = []
    cells = getattr(row, "cells", None) or []

    for cell in cells:
        text_or_none = cell.text
        text = (
            normalize_text(text_or_none.text)
            if isinstance(text_or_none, TextUnit)
            else ""
        )

        span = int(getattr(cell, "col_span", 1) or 1)
        span = max(span, 1)
        expanded.append(text)

        # Fill spanned columns with empty strings.
        if span > 1:
            expanded.extend([""] * (span - 1))

    # Pad or truncate to match n_cols.
    current_len = len(expanded)

    if current_len < n_cols:
        expanded.extend([""] * (n_cols - current_len))
    elif current_len > n_cols:
        msg = (
            f"header row expanded wider than n_cols: expanded={current_len} "
            f"> n_cols={n_cols}. Truncating. segment_id={segment_id} "
            f"local_code={local_code!r}"
        )
        logger.warning(msg)
        warnings.append(msg)
        expanded = expanded[:n_cols]

    return expanded


def _fill_span_area(
    *,
    col_span: int,
    col_start: int,
    grid: list[list[dict[str, Any]]],
    row_span: int,
    row_start: int,
    segment_id: str,
    value: Any,
) -> None:
    """Fill a specific rectangular area of the grid.

    Parameters
    ----------
    col_span
        The column span.
    col_start
        The starting column index.
    grid
        The grid to populate.
    row_span
        The row span.
    row_start
        The starting row index.
    segment_id
        The TableSegment ID (for error messages).
    value
        The TextUnit-like value to fill in the spanned area.

    Raises
    ------
    ValueError
        If overlapping spans are detected.
    """

    for rr in range(row_start, row_start + row_span):
        for cc in range(col_start, col_start + col_span):
            if grid[rr][cc]["source_row"] != -1:
                raise ValueError(
                    f"Overlapping spans detected at (row={rr}, col={cc}) "
                    f"in TableSegment: {segment_id}."
                )

            grid[rr][cc] = {"text": value, "source_row": row_start}


def _finalize_table_structure(
    *,
    chain: list[tuple[int, int, Table]],
    header_rows: list[TableRow],
    local_code: Optional[str],
    segment_id: str,
    stitched_rows: list[TableRow],
    warnings: list[str],
) -> tuple[int, Optional[str], list[list[str]]]:
    """Compute final n_cols and header signatures.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the slices to
        stitch.
    header_rows
        The final list of header rows.
    local_code
        The table's local code.
    segment_id
        The TableSegment ID.
    stitched_rows
        The final list of stitched table rows.
    warnings
        A list to append warning messages to.

    Returns
    -------
    tuple[int, Optional[str], list[list[str]]]
        The final n_cols, columns signature, and canonical header rows.
    """

    columns_signature: str | None = None
    header_rows_canonical: list[list[str]] = []
    declared_n_cols = max((table.n_cols or 0 for _, _, table in chain), default=0)

    # Calculate computed columns based on the stitched rows.
    computed_n_cols = 0

    if stitched_rows:
        computed_n_cols = max(
            (sum(cell.col_span for cell in row.cells) for row in stitched_rows),
            default=0,
        )

    n_cols = max(declared_n_cols, computed_n_cols)

    # Canonicalize header rows to a fixed width of n_cols by expanding col_spans.
    if header_rows and n_cols > 0:
        header_rows_canonical = [
            _expand_header_row_to_n_cols(
                local_code=local_code,
                n_cols=n_cols,
                row=hr,
                segment_id=segment_id,
                warnings=warnings,
            )
            for hr in header_rows
        ]
        columns_signature = "||".join(
            "|".join((c if c else "__BLANK__") for c in hrc)
            for hrc in header_rows_canonical
        )

    if 0 < declared_n_cols < computed_n_cols:
        msg = (
            f"n_cols inflation detected: computed_n_cols={computed_n_cols} > "
            f"declared_n_cols={declared_n_cols}. Using n_cols={n_cols}. "
            f"segment_id={segment_id} local_code={local_code!r}"
        )
        logger.warning(msg)
        warnings.append(msg)

    return n_cols, columns_signature, header_rows_canonical


def _join_text_unit_texts(
    *, repair_hyphenation: bool = True, text_units: list[TextUnit]
) -> str:
    """Join a list of TextUnit objects into a single combined string.

    If repair_hyphenation=False, then join all chunks using newline separators.

    If repair_hyphenation=True, apply deterministic joining rules:
        1. If previous chunk ends with '-' and next begins with lowercase: remove the
            hyphen and join with no space ("soft-" + "ware" -> "software").
        2. If previous chunk ends with '-' and next begins with non-lowercase: keep the
            hyphen and join with no space ("Non-" + "Profit" -> "Non-Profit").
        3. If previous chunk ends with strong sentence terminator (. ! ? ... …): start
            a new output chunk (paragraph/list boundary) and preserve it via newline.
        4. Otherwise, join with a single space (treat as flowing/wrapped text).

    Parameters
    ----------
    repair_hyphenation
        If True, repair hyphenation at line breaks.
    text_units
        The list of TextUnit objects.

    Returns
    -------
    str
        The combined text.
    """

    texts = [text_unit.text for text_unit in text_units if text_unit.text is not None]

    if not texts:
        return ""

    if len(texts) == 1:
        return texts[0]

    # If we're not repairing hyphenation, then we just join using newlines.
    if not repair_hyphenation:
        return "\n".join(texts)

    # Terminators that force a hard carriage return. We explicitly EXCLUDE commas,
    # colons, and semicolons, as they usually imply continuation of the current
    # thought/sentence.
    sentence_terminators = (".", "!", "?", "...", "…")

    output: list[str] = [texts[0]]

    for next_raw_text in texts[1:]:
        prev_raw_text = output[-1]
        prev_strip = prev_raw_text.rstrip()
        next_strip = (next_raw_text or "").lstrip()

        if not prev_strip:
            output[-1] = next_strip

            continue

        if not next_strip:
            # Keep previous as-is; skip empty continuation chunk.
            output[-1] = prev_strip

            continue

        prev_last = prev_strip[-1]
        next_first = next_strip[0]

        # Hyphenation handling.
        if prev_last == "-":
            # Case A: Standard word break (soft- \n ware) -> "software".
            if next_first.islower():
                output[-1] = prev_strip[:-1] + next_strip

            # Case B: Compound word break (Non- \n Profit) -> "Non-Profit". We keep the
            # hyphen, but strictly DO NOT insert a space.
            else:
                output[-1] = prev_strip + next_strip

            continue

        # Sentence terminators. If the previous line ended with a period, bang, or
        # question mark, we assume the next chunk is a new paragraph or list item.
        if prev_strip.endswith(sentence_terminators):
            output[-1] = prev_strip
            output.append(next_strip)

            continue

        # Default join (flowing text). If no terminator and no hyphen, we assume it's a
        # wrapped line. This covers:
        #   - "comma," + "next"
        #   - "no punct" + "continuation"
        #   - "proper noun" + "John"
        output[-1] = prev_strip + " " + next_strip

    return "\n".join(output)


def _populate_grid_spans(
    *, segment: TableSegment, grid: list[list[dict[str, Any]]], n_rows: int, n_cols: int
) -> None:
    """Handle cell parsing, cursor placement, and span explosion.

    Parameters
    ----------
    grid
        The grid to populate.
    n_cols
        The number of columns in the table.
    n_rows
        The number of rows in the table.
    segment
        The TableSegment to process.

    Raises
    ------
    ValueError
        If spans exceed table bounds or overlap.
    """

    for row_index, row in enumerate(segment.rows):
        cursor = 0

        for cell in row.cells:
            row_span, col_span = cell.row_span, cell.col_span

            # Treat truly empty cells as None, otherwise preserve full TextUnit-like
            # payload.
            raw_text = cell.text.text if isinstance(cell.text, TextUnit) else ""
            value = None if not raw_text.strip() else cell.text

            # Padding cell = extractor emitted an explicit blank cell in a column that
            # may already be occupied by a row-span from a previous row.
            is_padding = value is None and row_span == 1 and col_span == 1

            # If this is padding and the current slot is already occupied, consume
            # exactly one column and move on. This prevents right-shifting that can
            # overflow n_cols.
            if is_padding:
                if cursor < n_cols and grid[row_index][cursor]["source_row"] != -1:
                    cursor += 1
                    continue

                # If we're already past the edge due to earlier padding, ignore
                # trailing padding.
                if cursor >= n_cols:
                    continue

            # Advance cursor to next empty slot (normal behavior for real cells).
            while cursor < n_cols and grid[row_index][cursor]["source_row"] != -1:
                cursor += 1

            # Sanity check: ensure span fits.
            if cursor >= n_cols:
                # If the only thing left is padding, ignore it; otherwise this is a
                # real error.
                if is_padding:
                    continue

                raise ValueError(
                    f"Row {row_index} exceeds declared n_cols={n_cols} "
                    f"in TableSegment '{segment.segment_id}'."
                )

            # Validate spans.
            _validate_span_bounds(
                col_span=col_span,
                cursor=cursor,
                n_cols=n_cols,
                n_rows=n_rows,
                row_index=row_index,
                row_span=row_span,
                segment_id=segment.segment_id,
            )

            # Fill the spanned area.
            _fill_span_area(
                col_span=col_span,
                col_start=cursor,
                grid=grid,
                row_span=row_span,
                row_start=row_index,
                segment_id=segment.segment_id,
                value=value,
            )

            cursor += col_span


def _process_next_table_slice(
    *,
    current_local_code: Optional[str],
    next_item: Table,
    next_item_index: int,
    next_page_index: int,
    segment_header_row_count: int,
    segment_header_rows: list[TableRow],
    segment_id: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Process a subsequent table slice: resolve headers, code, and rows to append.

    The process is as follows:

    1. Resolve local code for display but compare using normalized form.
    2. Determine rows to drop/add.
    3. Create provenance.

    Parameters
    ----------
    current_local_code
        The current local code for the segment.
    next_item
        The next Table slice to process.
    next_item_index
        The item index of the next Table slice.
    next_page_index
        The page index of the next Table slice.
    segment_header_row_count
        The segment-level header row count.
    segment_header_rows
        The segment-level header rows.
    segment_id
        The TableSegment ID.
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[str, Any]
        A dict containing:
            - "slice": the new TableSlice to add to the segment.
            - "provenance": the SegmentProvenance for the new slice.
            - "rows_to_add": the list of TableRow objects to add from this slice after
                dropping repeated headers.
            - "local_code": the resolved local code for this slice (may be None).
    """

    # 1.
    next_local_code = _strip_local_code(next_item.local_code)

    if next_local_code and current_local_code:
        if normalize_local_code(next_local_code) != normalize_local_code(
            current_local_code
        ):
            msg = (
                f"Conflicting local_code in table chain {segment_id}: "
                f"{current_local_code!r} vs. {next_local_code!r} "
                f"(page={next_page_index}, item_index={next_item_index}). "
                f"Keeping {current_local_code!r}."
            )
            logger.warning(msg)
            warnings.append(msg)

    # Carry forward the segment code; only adopt the next code if missing.
    slice_local_code = current_local_code or next_local_code

    # 2.
    next_hrc = int(next_item.header_row_count or 0)

    # Determine how many header rows we should attempt to match/drop. We ALWAYS require
    # a match against the base header before dropping anything. This prevents losing
    # real content when a continuation page begins with a checkpoint/section row inside
    # the table grid (common in many curricula), even if the verifier marked
    # repeats_header=True.
    match_k = segment_header_row_count
    if 0 < next_hrc < segment_header_row_count:
        # If next slice has *fewer* headers declared than the segment, use the smaller
        # number.
        match_k = next_hrc
    elif next_hrc > 0 and next_hrc != segment_header_row_count:
        match_k = min(segment_header_row_count, next_hrc)
        msg = (
            f"header_row_count mismatch: seg={segment_header_row_count} vs next={next_hrc}. "
            f"Using match_k={match_k}."
        )
        logger.warning(msg)
        warnings.append(msg)

    rows_to_add, dropped_header_rows = _drop_repeated_header(
        base_header_rows=segment_header_rows[:match_k],
        header_row_count=match_k,
        next_table=next_item,
    )

    # If the verifier/extractor explicitly claimed a repeated header but we could not
    # confirm it by matching the base header rows, keep all rows and warn.
    if next_item.repeats_header is True and match_k > 0 and dropped_header_rows == 0:
        msg = (
            f"Table continuation marked repeats_header=True but top rows did not match the base header; "
            f"kept all rows to avoid content loss. segment_id={segment_id}, page={next_page_index}."
        )
        logger.warning(msg)
        warnings.append(msg)

    # Normalize repeats_header + header_row_count for downstream consumers. For
    # continuation slices, header_row_count reflects *effective/confirmed* repeated
    # headers (i.e., rows actually dropped), not the extractor's guess.
    repeats_header_norm = next_item.repeats_header
    next_hrc_effective = dropped_header_rows

    # If we dropped header rows, we have confirmed repetition regardless of the
    # extractor/verifier hint. Normalize repeats_header accordingly (and warn if it
    # contradicts an explicit False).
    if dropped_header_rows > 0:
        if repeats_header_norm is False:
            msg = (
                f"Table continuation had repeats_header=False but we dropped {dropped_header_rows} "
                f"repeated header rows by matching the base header; normalizing repeats_header to True. "
                f"segment_id={segment_id}, page={next_page_index}, item_index={next_item_index}."
            )
            logger.warning(msg)
            warnings.append(msg)

        repeats_header_norm = True

        # If the slice declared 0 header rows but we dropped some, record that
        # inference.
        if next_hrc == 0:
            msg = (
                f"Inferred header_row_count={dropped_header_rows} for continuation slice (was 0) "
                f"because we dropped repeated headers. segment_id={segment_id}, page={next_page_index}."
            )
            logger.warning(msg)
            warnings.append(msg)

    # If nothing was dropped, repeats_header=True is misleading; normalize away.
    if repeats_header_norm is True and next_hrc_effective == 0:
        repeats_header_norm = None
        msg = (
            f"Normalized repeats_header from True to None because effective header_row_count==0 "
            f"and no repeated header rows were dropped. "
            f"segment_id={segment_id}, page={next_page_index}, item_index={next_item_index}."
        )
        logger.warning(msg)
        warnings.append(msg)

    # 3.
    new_provenance = SegmentProvenance(
        bbox=next_item.bbox,
        boundary=next_item.boundary,
        item_addr=create_item_addr(
            item_index=next_item_index, page_index=next_page_index
        ),
        item_index=next_item_index,
        kind=next_item.kind,
        local_code=slice_local_code,
        page_index=next_page_index,
        repeats_header=repeats_header_norm,
    )
    new_slice = TableSlice(
        bbox=next_item.bbox,
        boundary=next_item.boundary,
        dropped_header_rows=dropped_header_rows,
        header_row_count=next_hrc_effective,
        item_index=next_item_index,
        local_code=slice_local_code,
        page_index=next_page_index,
        repeats_header=repeats_header_norm,
        rows=next_item.rows,
    )

    return {
        "slice": new_slice,
        "provenance": new_provenance,
        "rows_to_add": rows_to_add,
        "local_code": slice_local_code,
    }


def _repair_short_rows_missing_trailing_cols_as_colspan(
    *,
    header_row_count: int,
    n_cols: int,
    rows: list[TableRow],
    segment_id: str,
    warnings: list[str],
) -> list[TableRow]:
    """If a non-header row is "short" (<=2 cells) and the last cell has text, and the
    row's col_span total is < n_cols, treat the missing columns as a colspan on the
    last cell.

    Parameters
    ----------
    header_row_count
        The number of header rows at the top of the table (which should be exempt from
        this repair).
    n_cols
        The target number of columns in the table.
    rows
        The list of TableRow objects to process.
    segment_id
        The TableSegment ID (for logging).
    warnings
        A list to append warning messages to.

    Returns
    -------
    list[TableRow]
        The repaired rows.
    """

    out: list[TableRow] = []

    for r_idx, row in enumerate(rows):
        # Never touch headers.
        if r_idx < header_row_count:
            out.append(row)
            continue

        cells = list(row.cells)

        if not cells or len(cells) > 2:
            out.append(row)
            continue

        # Avoid interacting with true row-spans.
        if any(c.row_span != 1 for c in cells):
            out.append(row)
            continue

        last = cells[-1]
        last_text = last.text.text if isinstance(last.text, TextUnit) else ""

        if not last_text.strip():
            out.append(row)
            continue

        colsum = sum(c.col_span for c in cells)

        if colsum >= n_cols:
            out.append(row)
            continue

        missing = n_cols - colsum
        new_last = last.model_copy(update={"col_span": last.col_span + missing})
        new_row = TableRow(cells=cells[:-1] + [new_last])

        warnings.append(
            f"[table_colspan_repair] segment_id={segment_id} row={r_idx}: "
            f"extended last cell col_span by +{missing} to fill n_cols={n_cols}."
        )
        out.append(new_row)

    return out


def _resolve_header_row_count(
    *, first_item: Table, item_index: int, page_index: int, warnings: list[str]
) -> int:
    """Determine header row count, using inference if extractor provided none.

    Parameters
    ----------
    first_item
        The first Table item in the chain.
    item_index
        The item index of the first Table item in the chain.
    page_index
        The page index of the first Table item in the chain.
    warnings
        A list to append warning messages to.

    Returns
    -------
    int
        The resolved header row count.
    """

    header_row_count = first_item.header_row_count

    if header_row_count <= 0:
        inferred_hrc, confidence = infer_header_row_count_from_rows(
            max_header_rows=3, rows=first_item.rows
        )
        if inferred_hrc > 0 and confidence >= 0.65:
            msg = (
                f"Inferred header_row_count={inferred_hrc} (confidence={confidence:.2f}) "
                f"for table chain starting at (page={page_index}, item_index={item_index})."
            )
            logger.warning(msg)
            warnings.append(msg)
            return inferred_hrc

    return header_row_count


def _resolve_initial_local_code(chain: list[tuple[int, int, Table]]) -> Optional[str]:
    """Return the first non-null local code found in the chain.

    NB: Returns the raw (stripped) local code as extracted — no canonicalization
    (e.g., "Tableau 4" stays "Tableau 4"). Canonicalization is deferred to
    post-stitching.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the slices to
        stitch.

    Returns
    -------
    Optional[str]
        The resolved local code, or None if all slices lack it.
    """

    _, _, first_item = chain[0]
    first_code = _strip_local_code(first_item.local_code)

    return first_code or next(
        (c for *_, item in chain[1:] if (c := _strip_local_code(item.local_code))),
        None,
    )


def _strip_local_code(local_code: Optional[str]) -> Optional[str]:
    """Strip whitespace from a local code, returning None if empty.

    Parameters
    ----------
    local_code
        The raw local code from extraction.

    Returns
    -------
    Optional[str]
        The stripped local code, or None if input is None/whitespace-only.
    """

    if not local_code:
        return None

    s = local_code.strip()

    return s if s else None


def _summarize_chain_items(chain: list[ChainItem]) -> str:
    """Create a compact, human-readable summaries for a stitched chain (for
    warnings/debug).

    Parameters
    ----------
    chain
        The list of (page_index, item_index, item) tuples representing the chain.

    Returns
    -------
    str
        A compact summary string.
    """

    parts: list[str] = []

    for p_i, item_i, item in chain:
        kind = "Table" if isinstance(item, Table) else "Block"
        boundary = getattr(item, "boundary", None)

        if boundary is not None and hasattr(boundary, "value"):
            boundary_val = boundary.value
        else:
            boundary_val = str(boundary)

        local_code = (getattr(item, "local_code", None) or "").strip()
        snippet = ""

        if isinstance(item, Block) and isinstance(item.text, TextUnit):
            snippet = re.sub(r"\s+", " ", (item.text.text or "").strip())[:80]
        elif isinstance(item, Table):
            cap = getattr(item, "caption", None)

            if isinstance(cap, TextUnit):
                snippet = re.sub(r"\s+", " ", (cap.text or "").strip())[:80]

        parts.append(
            f"(page={p_i}, item={item_i}, kind={kind}, boundary={boundary_val}, code={local_code!r}, snip={snippet!r})"
        )

    return "[" + ", ".join(parts) + "]"


def _validate_span_bounds(
    *,
    col_span: int,
    cursor: int,
    n_cols: int,
    n_rows: int,
    row_index: int,
    row_span: int,
    segment_id: str,
) -> None:
    """Validate that row and column spans fit within table limits.

    Parameters
    ----------
    col_span
        The column span.
    cursor
        The current column cursor.
    n_cols
        The number of columns in the table.
    n_rows
        The number of rows in the table.
    row_index
        The current row index.
    row_span
        The row span.
    segment_id
        The TableSegment ID for error reporting.

    Raises
    ------
    ValueError
        If spans exceed table bounds.
    """

    if row_index + row_span > n_rows:
        raise ValueError(
            f"row_span out of bounds (row={row_index}, row_span={row_span}, n_rows={n_rows}) "
            f"in TableSegment '{segment_id}'."
        )
    if cursor + col_span > n_cols:
        raise ValueError(
            f"col_span out of bounds (row={row_index}, col={cursor}, col_span={col_span}, n_cols={n_cols}) "
            f"in TableSegment '{segment_id}'."
        )


def assert_page_items_consumed_exactly_once(
    *,
    items_mapping: dict[int, list[tuple[int, Block | Table]]],
    segments: list[Segment],
) -> None:
    """Validate that every normalized PageIR item is consumed exactly once by segments.
    Expected universe of segments is derived from `items_mapping` (i.e.,
    post-normalization, with artifacts filtered if keep_artifacts=False upstream).

    Parameters
    ----------
    items_mapping
        Mapping of page_index to list of (item_index, item) tuples after normalization.
    segments
        The list of segments to validate.

    Raises
    ------
    ValueError
        Any of:
            - Missing items (expected but not present in any
                segment.segment_provenance).
            - Extra items (present in segment.segment_provenance but not expected).
            - Duplicate consumption (same (page_index,item_index) appears in > 1
                segment.segment_provenance).
    """

    expected: set[ItemKey] = {
        (page_index, orig_item_index)
        for page_index, items in items_mapping.items()
        for (orig_item_index, _) in items
    }
    used_by: dict[ItemKey, list[str]] = defaultdict(list)

    for segment in segments:
        for provenance in segment.segment_provenance:
            k: ItemKey = (provenance.page_index, provenance.item_index)
            used_by[k].append(segment.segment_id)

    seen = set(used_by.keys())
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    dupes = sorted([k for k, seg_keys in used_by.items() if len(seg_keys) > 1])

    if not (missing or extra or dupes):
        return

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
        lines = [f"  {k} -> {used_by[k]}" for k in dupes[:10]]
        remaining = len(dupes) - 10

        if remaining > 0:
            lines.append(f"  ... (+{remaining} more)")

        joined_lines = "\n".join(lines)
        dupe_details = f"\nDuplicate details ...\n{joined_lines}"

    raise ValueError(
        f"Integrity check failed: normalized PageIR items were not consumed exactly once.\n"
        f"Missing (expected but not consumed): {_fmt_keys(missing)}\n"
        f"Extra (consumed but not expected): {_fmt_keys(extra)}\n"
        f"Duplicates (consumed >1 time): {_fmt_keys(dupes)}"
        f"{dupe_details}"
    )


def build_continuation_chain(
    *,
    items_lookup: dict[int, dict[int, Block | Table]],
    links: dict[ItemKey, ItemKey],
    start_item: Block | Table,
    start_key: ItemKey,
    warnings: list[str],
) -> list[ChainItem]:
    """Follow links to build a list of items belonging to one logical segment.

    Parameters
    ----------
    items_lookup
        A mapping of page_index to item_index to Item. This allows lookup of items by
        their original index, even if intermediate artifacts were filtered out.
    links
        A mapping of (page_index, item_index) to (next_page_index, next_item_index) for
        items that continue across page breaks.
    start_item
        The starting item of the chain.
    start_key
        The (page_index, item_index) of the starting item.
    warnings
        A list of warnings associated with this chain.

    Returns
    -------
    list[ChainItem]
        A list of (page_index, item_index, item) tuples representing the chain of
        continuation items.
    """

    chain: list[ChainItem] = []
    current_page_index, current_item_index = start_key
    current_item = start_item

    while True:
        chain.append((current_page_index, current_item_index, current_item))
        next_link = links.get((current_page_index, current_item_index), None)

        if not next_link:
            break

        next_page_index, next_item_index = next_link
        next_page_map = items_lookup.get(next_page_index, None)

        # Broken link (page missing).
        if next_page_map is None:
            msg = (
                f"Broken link from {(current_page_index, current_item_index)}->{next_link}: "
                f"Page {next_page_index} not found in lookup."
            )
            logger.warning(msg)
            warnings.append(msg)
            break

        # Look up the next item by original index.
        next_item = next_page_map.get(next_item_index)

        # Broken link (item missing on page).
        if next_item is None:
            msg = (
                f"Broken link from {(current_page_index, current_item_index)}->{next_link}: "
                f"Item {next_item_index} not found on page {next_page_index}."
            )
            logger.warning(msg)
            warnings.append(msg)
            break

        # Assert compatible kinds. NB: This is strictly a sanity check at this point
        # compatible kinds should have been checked in match_candidates (which is
        # upstream of this function call). However, it's cheap and worth checking again
        # to ensure nothing broke.
        assert compatible_kinds_for_stitch(next_item=next_item, prev_item=current_item)

        # Advance to next item.
        current_page_index, current_item_index = next_page_index, next_item_index
        current_item = next_item

    return chain


def compatible_kinds_for_stitch(
    *, next_item: Block | Table, prev_item: Block | Table
) -> bool:
    """Return True if two items are stitch-compatible.

    NB: This purpose of this function is to act as a gate that determines whether two
    items are stitchable. For example:

    1. Table <-> Table: stitchable
    2. Paragraph <-> Paragraph: stitchable
    3. Caption <-> Caption: stitchable
    4. Heading: unstitchable

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

    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        # Allow CAPTION <-> CAPTION *only* when strongly anchored.
        if (
            prev_item.block_type == BlockType.CAPTION
            and next_item.block_type == BlockType.CAPTION
        ):
            # local_code match.
            prev_code = normalize_local_code(prev_item.local_code)
            next_code = normalize_local_code(next_item.local_code)
            if prev_code and next_code and prev_code == next_code:
                return True

            # Caption text begins with same "Table X"/"Figure Y".
            prev_text = (
                (prev_item.text.text or "").strip()
                if isinstance(prev_item.text, TextUnit)
                else ""
            )
            next_text = (
                (next_item.text.text or "").strip()
                if isinstance(next_item.text, TextUnit)
                else ""
            )

            prev_code = extract_table_or_figure_local_code(prev_text)
            next_code = extract_table_or_figure_local_code(next_text)

            if (
                prev_code
                and next_code
                and normalize_local_code(prev_code) == normalize_local_code(next_code)
            ):
                return True

            # Otherwise, too risky.
            return False

        # Headings should still never stitch.
        if (
            prev_item.block_type == BlockType.HEADING
            or next_item.block_type == BlockType.HEADING
        ):
            return False

        # For all other blocks:
        #   - Allow exact block_type matches (e.g., FIGURE<->FIGURE).
        #   - Allow "text-like" continuation between PARAGRAPH and LIST (extractor can
        #       flip across pages).
        textlike = {BlockType.FOOTNOTE, BlockType.LIST, BlockType.PARAGRAPH}
        is_textlike_continuation = (
            prev_item.block_type in textlike and next_item.block_type in textlike
        )
        return is_textlike_continuation or (
            prev_item.block_type == next_item.block_type
        )

    # Table <-> Table allowed at this point but all others are not.
    return isinstance(prev_item, Table) and isinstance(next_item, Table)


def compute_segment_id(
    *, doc_key: str, item_index: int, kind: str, page_index: int
) -> str:
    """Compute a deterministic UUIDv5 for a stitched segment. Segment IDs must be
    stable across reruns and (ideally) globally unique across PDFs, so we include the
    PDF doc_key plus the first source item pointer.

    Parameters
    ----------
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    item_index
        0-based original item index within PageIR.items for the first slice.
    kind
        Segment kind ('block' or 'table').
    page_index
        0-based page index of the segment's first slice.

    Returns
    -------
    str
        UUIDv5 string.
    """

    name = f"{doc_key}:segment:{kind}:p{page_index:04d}:i{item_index:04d}"

    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


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


def create_item_addr(*, item_index: int, page_index: int) -> str:
    """Create a stable, reversible address for a raw PageIR item.

    Parameters
    ----------
    item_index
        The 0-based original item index within PageIR.items.
    page_index
        The 0-based page index.

    Returns
    -------
    str
        The item address.
    """

    return f"p{page_index}:raw{item_index}"


def cross_check_verification_run(
    *,
    computed_doc_key: str,
    expected_doc_key: str,
    extraction_config: ExtractionConfig,
    verified_page_irs_dir: Path,
) -> tuple[dict[tuple[int, int], EdgeVerdictRecord], list[PageIR]]:
    """Cross-check that the verification run matches expected parameters and load
    verified page IRs and their verdicts.

    Parameters
    ----------
    computed_doc_key
        The document key computed from the source PDF bytes by the caller.
    expected_doc_key
        The expected document key (hex string) from the extraction run metadata.
    extraction_config
        The extraction configuration used for the run.
    verified_page_irs_dir
        The directory where verified page IRs are stored.

    Returns
    -------
    tuple[dict[tuple[int, int], EdgeVerdictRecord], list[PageIR]]
        The loaded verdicts and verified page IRs.

    Raises
    ------
    ValueError
        If the computed document key does not match the expected key.
    """

    if computed_doc_key != expected_doc_key:
        raise ValueError(
            f"PDF doc_key mismatch.\n"
            f"  PDF provided to verify():   {extraction_config.pdf_fp}\n"
            f"  computed doc_key:           {computed_doc_key}\n"
            f"  extraction_run.json key:    {expected_doc_key}\n"
            f"You are likely stitching against a different PDF than the one used for "
            f"verification. Pass the same PDF used in the verification step or re-run "
            f"verification."
        )

    verdict_dir = (
        extraction_config.output_dir
        / computed_doc_key
        / "verification"
        / "page_irs_pair_reports"
    )

    # Load and validate verified PageIR JSONs from the verification output directory.
    verified_page_irs = load_page_irs_from_verification(
        doc_key=expected_doc_key, verified_page_irs_dir=verified_page_irs_dir
    )

    # Load verification verdicts for debugging and linking purposes.
    verdicts = load_verification_verdicts(verdict_dir=verdict_dir)

    return verdicts, verified_page_irs


def expand_table_rows_to_rows_grid(
    *, segment: TableSegment
) -> tuple[list[TableRow], list[list[dict[str, Any]]]]:
    """Expand a stitched table's ragged rows with row_span/col_span into a rectangular
    grid of shape (n_rows x n_cols). Output rows have exactly n_cols cells each, and
    every output TableCell has row_span=1, col_span=1.

    NB: For downstream stability, visually empty grid cells are normalized to an
    explicit empty TextUnit (language='und', text='') rather than null. This ensures
    every TableCell in `rows_grid` has a `text` payload in JSON (even if blank).

    Parameters
    ----------
    segment
        The stitched TableSegment.

    Returns
    -------
    tuple[list[TableRow], list[list[dict[str, Any]]]]
        A tuple where the first element is the list of expanded TableRows, and the
        second element is the grid_sources mapping (per-cell source row index).
    """

    n_cols = segment.n_cols
    n_rows = len(segment.rows)

    assert (
        n_cols > 0
    ), f"Cannot expand spans: invalid n_cols={segment.n_cols} (segment_id={segment.segment_id})"

    # Initialize empty grid: grid[row][col] = {"text": TextUnit | None, "source_row": int}
    grid: list[list[dict[str, Any]]] = [
        [{"text": None, "source_row": -1} for _ in range(n_cols)] for _ in range(n_rows)
    ]

    # Populate spans into grid.
    _populate_grid_spans(segment=segment, grid=grid, n_rows=n_rows, n_cols=n_cols)

    # Convert grid to TableRow list and aligned grid_sources.
    grid_sources: list[list[dict[str, Any]]] = []
    out_rows: list[TableRow] = []

    for r in range(n_rows):
        out_cells: list[TableCell] = []
        src_row: list[dict[str, Any]] = []

        for c in range(n_cols):
            cell_text = grid[r][c]["text"]
            cell_text = cell_text or TextUnit(language="und", text="", text_en=None)
            out_cells.append(TableCell(col_span=1, row_span=1, text=cell_text))
            src_row.append({"source_row": grid[r][c]["source_row"]})

        out_rows.append(TableRow(cells=out_cells))
        grid_sources.append(src_row)

    return out_rows, grid_sources


def extract_table_or_figure_local_code(text: str) -> Optional[str]:
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


def fill_down_table_rows(
    *, header_row_count: int, rows: list[TableRow], table_filldown_group_cols_max: int
) -> list[TableRow]:
    """Fill down visually empty cells in the first `filldown_group_cols_max` columns.
    This reconstructs the implicit semantics of merged cells/rowspans often used in
    curriculum tables (e.g., Topic/Sub-topic cells left blank on subsequent rows).

    The process is as follows:

    1. Header rows are NOT filled down.
    2. Only fills if the target cell is empty (None or whitespace).
    3. Never overwrites non-empty cells.
    4. Returns a deep-copied row list (does not mutate input).

    Parameters
    ----------
    header_row_count
        The number of header rows at the top of the table.
    rows
        The list of table rows to fill.
    table_filldown_group_cols_max
        The maximum number of leading group columns to fill down.

    Returns
    -------
    list[TableRow]
        The filled-down table rows.
    """

    if table_filldown_group_cols_max <= 0:
        return [row.model_copy(deep=True) for row in rows]

    # Don't mutate input rows.
    output_rows: list[TableRow] = [row.model_copy(deep=True) for row in rows]

    # Track last non-empty TextUnit per leading group column.
    last_non_empty: list[Optional[TextUnit]] = [None] * table_filldown_group_cols_max

    for row_index, row in enumerate(output_rows):
        # Never fill header rows.
        if row_index < max(0, int(header_row_count)):
            continue

        # Only fill the first `group_cols_max` columns.
        max_ci = min(table_filldown_group_cols_max, len(row.cells))

        for ci in range(max_ci):
            cell = row.cells[ci]

            # Determine emptiness (structural): visually empty cell means None or blank
            # text. In other words, only fill empty cells.
            is_empty = cell.text is None or (
                cell.text.text is None or cell.text.text.strip() == ""
            )

            if is_empty:
                last_non_empty_text_unit = last_non_empty[ci]
                if isinstance(last_non_empty_text_unit, BaseModel):
                    cell.text = last_non_empty_text_unit.model_copy(deep=True)
            else:
                last_non_empty[ci] = cell.text

    return output_rows


def infer_header_row_count_from_rows(
    *, max_header_rows: int = 3, rows: list[TableRow]
) -> tuple[int, float]:
    """Infer header_row_count from table rows using a deterministic heuristic. This
    function is intended ONLY as a fallback when the extractor gave
    header_row_count == 0.

    Parameters
    ----------
    max_header_rows
        The maximum number of header rows to consider.
    rows
        The list of table rows.

    Returns
    -------
    tuple[int, float]
        Tuple containing (header_row_count, confidence).
    """

    if not rows:
        return 0, 0.0

    def row_header_score(row: TableRow) -> float:
        """Compute a heuristic "header-likeness" score for a table row. Higher scores
        indicate more header-like rows.

        Parameters
        ----------
        row
            The table row.

        Returns
        -------
        float
            The header-likeness score.
        """

        parts: list[str] = []
        filled_cells = 0

        for cell in row.cells:
            if cell.text and cell.text.text and cell.text.text.strip():
                parts.append(cell.text.text.strip())
                filled_cells += 1

        text = " ".join(parts)

        if not text:
            return 0.0

        compact = re.sub(r"\s+", "", text)
        total = max(1, len(compact))

        # Calculate content ratios.
        alpha = len(_ALPHA_RE.findall(text))
        digits = len(_DIGIT_RE.findall(text))

        alpha_ratio = alpha / total
        digit_ratio = digits / total

        filled_ratio = filled_cells / max(1, len(row.cells))

        # Weighted: headers tend to be word-heavy, number-light, and spread across
        # columns.
        return (2.0 * alpha_ratio) + (1.0 * filled_ratio) - (1.5 * digit_ratio)

    # Walk the first N rows and count consecutive "header-like" rows from the top.
    count = 0
    scores: list[float] = []

    for row_index in range(min(max_header_rows, len(rows))):
        score = row_header_score(rows[row_index])
        scores.append(score)

        # Conservative threshold: first rows must look clearly "header-ish"
        # (word-heavy + reasonably filled).
        if score >= 1.15:
            count += 1
        else:
            break

    if count == 0:
        return 0, 0.0

    # Confidence increases with count and score strength.
    avg = sum(scores[:count]) / max(1, count)
    confidence = min(1.0, 0.55 + 0.15 * (count - 1) + 0.20 * max(0.0, avg - 1.15))

    return count, confidence


def materialize_segment(
    *,
    chain: list[ChainItem],
    doc_key: str,
    item_index: int,
    page_index: int,
    repair_hyphenation: bool,
    section_path: list[SectionHeadingRef],
    table_filldown_enabled: bool,
    table_filldown_group_cols_max: int,
    warnings: list[str],
) -> Segment:
    """Dispatch the continuation chain to the correct merging logic based on item type.

    Parameters
    ----------
    chain
        A list of (page_index, item_index, item) tuples representing a chain of
        continuation items.
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    item_index
        The starting item index of the chain.
    page_index
        The starting page index of the chain.
    repair_hyphenation
        If True, repair hyphenation at line breaks when combining text units.
    section_path
        The section path for the segment.
    table_filldown_enabled
        If True, apply fill-down logic to group columns in tables.
    table_filldown_group_cols_max
        The maximum number of leading group columns to fill down in tables.
    warnings
        A list to append warning messages to.

    Returns
    -------
    Segment
        The merged Segment (BlockSegment or TableSegment).
    """

    first_chain_item = chain[0][2]

    if isinstance(first_chain_item, Table):
        table_chain = [
            (page_index, item_index, item)
            for page_index, item_index, item in chain
            if isinstance(item, Table)
        ]

        if len(table_chain) != len(chain):
            msg = (
                f"Mixed-kind chain starting at {(page_index, item_index)}; kept as standalone. "
                f"chain={_summarize_chain_items(chain)}"
            )
            logger.warning(msg)
            warnings.append(msg)

            # Fallback: treat first item standalone.
            table_chain = [(page_index, item_index, first_chain_item)]

        return stitch_table_chain(
            chain=table_chain,
            doc_key=doc_key,
            section_path=section_path,
            table_filldown_enabled=table_filldown_enabled,
            table_filldown_group_cols_max=table_filldown_group_cols_max,
            warnings=warnings,
        )

    block_chain = [
        (page_index, item_index, item)
        for page_index, item_index, item in chain
        if isinstance(item, Block)
    ]

    if len(block_chain) != len(chain):
        msg = (
            f"Mixed-kind chain starting at {(page_index, item_index)}; kept as standalone. "
            f"chain={_summarize_chain_items(chain)}"
        )
        logger.warning(msg)
        warnings.append(msg)

        # Fallback: treat first item standalone.
        block_chain = [(page_index, item_index, first_chain_item)]

    return stitch_block_chain(
        chain=block_chain,
        doc_key=doc_key,
        repair_hyphenation=repair_hyphenation,
        section_path=section_path,
    )


def normalize_local_code(local_code: Optional[str]) -> Optional[str]:
    """Normalize a local code for comparison.

    Parameters
    ----------
    local_code
        The local code.

    Returns
    -------
    Optional[str]
        The normalized local code, or None if empty.
    """

    normalized_local_code = (
        local_code.strip() if local_code and local_code.strip() else None
    )

    if not normalized_local_code:
        return None

    # Collapse internal whitespace then case-fold.
    normalized_local_code = _LOCAL_CODE_RE.sub(" ", normalized_local_code.strip())
    return normalized_local_code.casefold()


def normalize_text(text: Optional[str]) -> str:
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

    # Normalize unicode characters (e.g., standardize accents). NFKC form is usually
    # best for compatibility comparisons.
    text = unicodedata.normalize("NFKC", text)

    # Collapse whitespace, strip, and lowercase.
    return re.sub(r"\s+", " ", text).strip().lower()


def persist_stitching_run(
    *, config: StitchingConfig, output_dir: Path
) -> tuple[DocumentIRDirs, RunCtx]:
    """Persist stitching run metadata.

    Parameters
    ----------
    config
        The stitching run configuration.
    output_dir
        The output directory for the stitching run results.

    Returns
    -------
    tuple[DocumentIRDirs, RunCtx]
        The created stitching directories and persisted stitching run metadata.
    """

    stitching_dirs = create_document_ir_dirs(output_dir=output_dir)
    exclude_keys = {"model", "overwrite"}
    stitching_run = RunCtx(
        extra={
            k: v
            for k, v in config.model_dump(mode="json").items()
            if k not in exclude_keys
        },
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "stitching_run.json", json_info=stitching_run)
    logger.info(f"Saving stitching results to: {stitching_dirs.root}")

    return stitching_dirs, stitching_run


def row_provenance_by_stitched_index(
    *, segment: TableSegment
) -> list[TableRowProvenance]:
    """Return a list of length len(segment.rows) where each entry contains the
    provenance info (at least bbox + page_index + slice_index) for the stitched row.
    This is computed deterministically from `segment.slices` using the same
    header-dropping logic as stitching.

    NB:

    1. repeats_header=True -> drop header_row_count rows on that slice
    2. repeats_header=False -> drop nothing
    3. repeats_header=None -> *infer* repeated header by matching the slice's first
        header rows against the segment's header rows (conservative).

    Parameters
    ----------
    segment
        The stitched TableSegment.

    Returns
    -------
    list[TableRowProvenance]
        The per-stitched-row provenance mapping.

    Raises
    ------
    ValueError
        If the mapping length mismatches.
    """

    assert (
        segment.slices
    ), f"TableSegment {segment.segment_id} has no slices; cannot derive row provenance."

    mapping: list[TableRowProvenance] = []

    for slice_index, sl in enumerate(segment.slices):
        bbox = sl.bbox

        assert isinstance(bbox, list) and len(bbox) == 4, (
            f"Missing/invalid bbox for TableSlice (segment_id={segment.segment_id}, "
            f"slice_index={slice_index}, page_index={sl.page_index}): {bbox}"
        )

        # Use the actual number of header rows dropped during stitching. This avoids
        # provenance drift when slice.header_row_count is missing/0 but the stitcher
        # still drops repeated headers via canonical matching.
        drop = 0 if slice_index == 0 else sl.dropped_header_rows

        # Never drop beyond available rows.
        drop = min(drop, len(sl.rows))
        effective_rows = sl.rows[drop:]

        # Approximate per-row bbox from the slice bbox by evenly splitting the slice
        # bbox vertically across the slice's visual rows. This is deterministic and
        # makes debug output much more actionable than a coarse slice bbox.
        total_rows_in_slice = len(sl.rows)
        x0, y0, x1, y1 = bbox
        row_h = ((y1 - y0) / total_rows_in_slice) if total_rows_in_slice > 0 else 0.0

        for i, _ in enumerate(effective_rows):
            slice_row_index = drop + i
            row_bbox = [
                x0,
                y0 + row_h * slice_row_index,
                x1,
                y0 + row_h * (slice_row_index + 1),
            ]
            mapping.append(
                TableRowProvenance(
                    bbox=bbox,
                    dropped_header_rows=drop,
                    page_index=sl.page_index,
                    row_bbox=row_bbox,
                    slice_index=slice_index,
                    slice_row_index=slice_row_index,
                    slice_row_index_after_drop=i,
                    slice_total_rows=total_rows_in_slice,
                )
            )

    if len(mapping) != len(segment.rows):
        raise ValueError(
            f"Row <-> slice mapping length mismatch for TableSegment {segment.segment_id}. "
            f"Derived {len(mapping)} rows from slices, but segment.rows has {len(segment.rows)}."
        )

    return mapping


def row_signature(row: TableRow) -> tuple[str, ...]:
    """Create a stable signature for a table row based on normalized cell texts.

    Parameters
    ----------
    row
        The table row.

    Returns
    -------
    tuple[str, ...]
        The row signature.
    """

    row_sig: list[str] = []

    for cell in getattr(row, "cells", None) or []:
        text_or_none = cell.text
        text = (
            normalize_text(text_or_none.text)
            if isinstance(text_or_none, TextUnit)
            else ""
        )
        row_sig.append(text)

    return tuple(row_sig)


def save_document_ir(
    *,
    doc_key: str,
    document_ir_fp: Path,
    link_debug: list[dict[str, Any]],
    links: dict[tuple[int, int], tuple[int, int]],
    page_irs: list[PageIR],
    page_pair_debug: list[dict[str, Any]],
    pdf_name: str,
    segments: list[Segment],
    stitching_dirs: DocumentIRDirs,
    warnings: list[str],
) -> None:
    """Persist the final DocumentIR and stitch report.

    Parameters
    ----------
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    document_ir_fp
        The output file path for the DocumentIR JSON.
    link_debug
        List of per-link debug info.
    links
        Forward links for items that continue across page breaks.
    page_irs
        The list of PageIRs.
    page_pair_debug
        List of per-page-pair debug info.
    pdf_name
        The PDF file name.
    segments
        The list of stitched segments.
    stitching_dirs
        The stitching directories.
    warnings
        A list of warning messages.
    """

    # Write DocumentIR to file.
    first_page = page_irs[0]
    pages_meta: list[DocumentPageMeta] = []

    for p in page_irs:
        assert isinstance(p.page_index, int) and p.page_index >= 0, f"{p = }"
        pages_meta.append(
            DocumentPageMeta(
                coord_space=p.coord_space,
                dpi=p.dpi,
                image_height=p.image_height,
                image_width=p.image_width,
                is_blank=(len(p.items) == 0),
                page_index=p.page_index,
            )
        )

    # Warn if pages have heterogeneous dimensions (consumers should use pages[i]
    # rather than top-level image_height/image_width).
    unique_dims = {(pm.image_width, pm.image_height) for pm in pages_meta}

    if len(unique_dims) > 1:
        warnings.append(
            f"Heterogeneous page dimensions detected ({len(unique_dims)} distinct sizes): "
            f"{sorted(unique_dims)}. Use pages[i].image_width / pages[i].image_height "
            f"for per-page bbox interpretation; top-level image_width/image_height are "
            f"from the first page only."
        )

    # Check for page index gaps before constructing DocumentIR (so warnings are
    # included in the serialized output).
    page_indices = sorted({p.page_index for p in page_irs if p.page_index is not None})

    if page_indices:
        expected = set(range(page_indices[0], page_indices[-1] + 1))
        missing = sorted(expected - set(page_indices))
        if missing:
            warnings.append(
                f"PageIR coverage has gaps: missing page_index values {missing}. "
                f"This may indicate omitted blank pages or extraction failures."
            )

    document_ir = DocumentIR(
        coord_space=first_page.coord_space,
        doc_key=doc_key,
        dpi=first_page.dpi,
        page_count=len(page_irs),
        pages=pages_meta,
        pdf_name=pdf_name,
        segments=segments,
        warnings=warnings,
    )

    write_to_json(fp=document_ir_fp, json_info=document_ir)

    # Write a stitch report JSON artifact.
    stitch_report_fp = stitching_dirs.root / "stitch_report.json"
    table_segments_summary: list[dict[str, Any]] = []

    for segment in segments:
        if segment.kind != "table":
            continue

        pages = [sl.page_index for sl in segment.slices]
        table_segments_summary.append(
            {
                "segment_id": segment.segment_id,
                "local_code": segment.local_code,
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "slice_count": len(segment.slices),
                "header_row_count": segment.header_row_count,
                "slices": [
                    {
                        "page_index": sl.page_index,
                        "item_index": sl.item_index,
                        "boundary": sl.boundary.value,
                        "repeats_header": sl.repeats_header,
                        "dropped_header_rows": sl.dropped_header_rows,
                    }
                    for sl in segment.slices
                ],
            }
        )

    table_segments_summary.sort(
        key=lambda x: (
            x["page_start"] if x["page_start"] is not None else 10**9,
            x["segment_id"],
        )
    )
    stitch_report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "block_type_counts": dict(
            Counter(
                [
                    s.block_type.value
                    for s in segments
                    if getattr(s, "kind", None) == "block"
                ]
            )
        ),
        "doc_key": doc_key,
        "link_count": len(links),
        "link_debug": link_debug,
        "page_count": len(page_irs),
        "page_pair_debug": page_pair_debug,
        "pdf_name": pdf_name,
        "segment_kind_counts": dict(Counter([s.kind for s in segments])),
        "table_segments": table_segments_summary,
        "warnings": warnings,
    }

    write_to_json(fp=stitch_report_fp, json_info=stitch_report)


def stitch_block_chain(
    *,
    chain: list[tuple[int, int, Block]],
    doc_key: str,
    repair_hyphenation: bool,
    section_path: list[SectionHeadingRef],
) -> BlockSegment:
    """Stitch a chain of block slices.

    NB: Block continuation chains can carry different payload types. We treat them
    differently:

    1. Text (`Block.text`/`TextUnit`) is additive across slices: a paragraph can be
        split across pages (truncated/resumed). We therefore collect all slice
        TextUnits and join them deterministically into a single segment-level
        `combined_text`/`text`.
    2. Lists (`Block.list_items`) are also additive: list items may continue on the
        next page. We therefore concatenate list items from all slices into one
        segment-level list.
    3. Figures (`Block.figure`) are NOT merged across slices. Figure payloads are
        structured objects and merging arbitrary dicts is not reliable or
        deterministic. We keep the first non-null figure payload as the segment-level
        representative (`BlockSegment.figure`) and preserve per-slice figure payloads
        in `slices[].figure` for full fidelity.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Block) tuples representing the slices to
        stitch.
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    repair_hyphenation
        If True, repair hyphenation at line breaks when combining text units.
    section_path
        The section path for the segment.

    Returns
    -------
    BlockSegment
        The stitched BlockSegment.
    """

    first_chain_page_index, first_chain_item_index, first_chain_item = chain[0]
    segment_id = compute_segment_id(
        doc_key=doc_key,
        item_index=first_chain_item_index,
        kind="block",
        page_index=first_chain_page_index,
    )

    figure_payload: Optional[dict[str, Any]] = None
    list_items: list[ListItem] = []
    resolved_local_code: Optional[str] = None
    segment_provenance: list[SegmentProvenance] = []
    slices: list[BlockSlice] = []
    text_units: list[TextUnit] = []

    for page_index, item_index, block in chain:
        # Promote local_code across the stitched chain: take the first non-empty code
        # encountered in any slice. NB: preserves original form (no canonicalization).
        if resolved_local_code is None:
            if lc := _strip_local_code(block.local_code):
                resolved_local_code = lc

        block_figure = (
            block.figure.model_dump(mode="json") if block.figure is not None else None
        )
        block_list_items = block.list_items if block.list_items else None
        slices.append(
            BlockSlice(
                bbox=block.bbox,
                block_type=block.block_type,
                boundary=block.boundary,
                figure=block_figure,
                item_index=item_index,
                list_items=block_list_items,
                local_code=block.local_code,
                page_index=page_index,
                text=block.text,
            )
        )
        segment_provenance.append(
            SegmentProvenance(
                bbox=block.bbox,
                boundary=block.boundary,
                item_addr=create_item_addr(
                    item_index=item_index, page_index=page_index
                ),
                item_index=item_index,
                kind=block.kind,
                local_code=block.local_code,
                page_index=page_index,
                repeats_header=None,
            )
        )

        if block.text is not None:
            text_units.append(block.text)
        if block_list_items:
            list_items.extend(block_list_items)
        if figure_payload is None and block_figure:
            figure_payload = block_figure

    combined_text: Optional[str] = None
    stitched_text: Optional[TextUnit] = first_chain_item.text

    if text_units:
        combined_text = _join_text_unit_texts(
            repair_hyphenation=repair_hyphenation, text_units=text_units
        )

        # If slice languages disagree, mark the stitched segment as mixed-language.
        languages = {text_unit.language for text_unit in text_units}

        if len(languages) > 1:
            # NB: Do NOT mutate any slice TextUnit -> create a new one instead.
            stitched_text = TextUnit(language="mul", text=combined_text, text_en=None)
        else:
            lang = languages.pop()  # Single language
            stitched_text = TextUnit(language=lang, text=combined_text, text_en=None)

    return BlockSegment(
        block_type=first_chain_item.block_type,
        combined_text=combined_text,
        figure=figure_payload,
        list_items=list_items
        or (first_chain_item.list_items if first_chain_item.list_items else None),
        local_code=resolved_local_code,
        section_path=section_path,
        segment_id=segment_id,
        segment_provenance=segment_provenance,
        slices=slices,
        text=stitched_text,
    )


def stitch_table_chain(
    *,
    chain: list[tuple[int, int, Table]],
    doc_key: str,
    section_path: list[SectionHeadingRef],
    table_filldown_enabled: bool,
    table_filldown_group_cols_max: int,
    warnings: list[str],
) -> TableSegment:
    """Stitch a chain of table slices.

    For local_code determination, the process is as follows:

    1. If first.local_code is None but a later slice has one, promote the first
        non-null code that we encounter.
    2. Once a code is known, carry it forward to later slices that are missing it.
    3. After the loop, if local_code was discovered mid-chain, then backfill
        slices[0].local_code and provenance[0].local_code if missing.


    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the
        slices to stitch.
    doc_key
        Deterministic hash key of the PDF bytes (SHA-256 hex).
    section_path
        The section path for the segment.
    table_filldown_enabled
        If True, apply fill-down logic to group columns in tables.
    table_filldown_group_cols_max
        The maximum number of leading group columns to fill down in tables.
    warnings
        A list to append warning messages to.

    Returns
    -------
    TableSegment
        The stitched TableSegment.
    """

    first_page_index, first_item_index, first_item = chain[0]

    # 1.
    segment_id = compute_segment_id(
        doc_key=doc_key,
        item_index=first_item_index,
        kind="table",
        page_index=first_page_index,
    )

    # Resolve local code (look ahead if missing in first slice).
    local_code = _resolve_initial_local_code(chain)

    # Resolve header row count (inference if missing).
    header_row_count = _resolve_header_row_count(
        first_item=first_item,
        item_index=first_item_index,
        page_index=first_page_index,
        warnings=warnings,
    )

    # 2.
    stitched_rows: list[TableRow] = list(first_item.rows)
    header_rows = stitched_rows[:header_row_count] if header_row_count > 0 else []

    # Create first slice and provenance.
    slices: list[TableSlice] = [
        TableSlice(
            bbox=first_item.bbox,
            boundary=first_item.boundary,
            dropped_header_rows=0,
            header_row_count=header_row_count,
            item_index=first_item_index,
            local_code=local_code,
            page_index=first_page_index,
            repeats_header=first_item.repeats_header,
            rows=first_item.rows,
        )
    ]
    segment_provenance: list[SegmentProvenance] = [
        SegmentProvenance(
            bbox=first_item.bbox,
            boundary=first_item.boundary,
            item_addr=create_item_addr(
                item_index=first_item_index, page_index=first_page_index
            ),
            item_index=first_item_index,
            kind=first_item.kind,
            local_code=local_code,
            page_index=first_page_index,
            repeats_header=first_item.repeats_header,
        )
    ]

    # 3.
    for next_page, next_item_idx, next_item in chain[1:]:
        slice_result = _process_next_table_slice(
            current_local_code=local_code,
            next_item=next_item,
            next_item_index=next_item_idx,
            next_page_index=next_page,
            segment_header_row_count=header_row_count,
            segment_header_rows=header_rows,
            segment_id=segment_id,
            warnings=warnings,
        )

        # Update state.
        local_code = slice_result["local_code"]  # Carry forward potentially new code
        slices.append(slice_result["slice"])
        segment_provenance.append(slice_result["provenance"])
        stitched_rows.extend(slice_result["rows_to_add"])

    # Finalize columns.
    n_cols, columns_signature, header_rows_canonical = _finalize_table_structure(
        chain=chain,
        stitched_rows=stitched_rows,
        header_rows=header_rows,
        segment_id=segment_id,
        local_code=local_code,
        warnings=warnings,
    )

    # Structural repair: short continuation rows often represent a colspan label (e.g.,
    # "Intégration" spanning remaining columns). Repair before building TableSegment.
    stitched_rows_for_segment = [r.model_copy(deep=True) for r in stitched_rows]
    stitched_rows_for_segment = _repair_short_rows_missing_trailing_cols_as_colspan(
        header_row_count=header_row_count,
        n_cols=n_cols,
        rows=stitched_rows_for_segment,
        segment_id=segment_id,
        warnings=warnings,
    )

    # Keep header_rows consistent with the repaired row objects
    header_rows = (
        stitched_rows_for_segment[:header_row_count] if header_row_count > 0 else []
    )

    # 5. Build objects.
    table_segment = TableSegment(
        columns_signature=columns_signature,
        header_row_count=header_row_count,
        header_rows=header_rows,
        header_rows_canonical=header_rows_canonical,
        kind="table",
        local_code=local_code,
        n_cols=n_cols,
        rows=stitched_rows_for_segment,
        section_path=section_path,
        segment_id=segment_id,
        segment_provenance=segment_provenance,
        slices=slices,
    )

    # 6. Post-processing (grid and provenance).
    rows_grid, grid_sources = expand_table_rows_to_rows_grid(segment=table_segment)
    row_provenance = row_provenance_by_stitched_index(segment=table_segment)

    rows_filldown = None
    if table_filldown_enabled:
        # Prefer grid if available, else raw rows.
        target_rows = rows_grid if rows_grid is not None else stitched_rows
        rows_filldown = fill_down_table_rows(
            table_filldown_group_cols_max=table_filldown_group_cols_max,
            header_row_count=header_row_count,
            rows=target_rows,
        )

    return table_segment.model_copy(
        update={
            "grid_sources": grid_sources,
            "row_provenance": row_provenance,
            "rows_filldown": rows_filldown,
            "rows_grid": rows_grid,
        }
    )


def update_section_stack(
    *,
    chain: list[tuple[int, int, Block | Table]],
    max_len: int,
    section_path_stack: list[SectionHeadingRef],
    warnings: list[str],
) -> list[SectionHeadingRef]:
    """Update the section path stack if the current chain represents a heading.

    Parameters
    ----------
    chain
        A list of (page_index, item_index, item) tuples representing a chain of
        continuation items.
    max_len
        The maximum length of the section path stack.
    section_path_stack
        The current section path stack.
    warnings
        A list to append warning messages to.

    Returns
    -------
    list[SectionHeadingRef]
        The updated section path stack.
    """

    # Use the first item in the chain (heading segments are standalone).
    first_chain_item = chain[0][2]

    if not (
        isinstance(first_chain_item, Block)
        and first_chain_item.block_type == BlockType.HEADING
    ):
        return section_path_stack

    text_or_none = first_chain_item.text
    heading_text = (
        (text_or_none.text or "").strip() if isinstance(text_or_none, TextUnit) else ""
    )
    local_code = (first_chain_item.local_code or "").strip()

    if not heading_text and not local_code:
        msg = (
            f"Heading block missing text and local_code; not added to section_path: "
            f"page_index={chain[0][0]}, item_index={chain[0][1]}"
        )
        logger.warning(msg)
        warnings.append(msg)
        return section_path_stack

    new_heading_text = (heading_text or local_code).strip()

    if section_path_stack:
        prev_heading_norm = re.sub(
            r"\s+", " ", section_path_stack[-1].text.strip()
        ).casefold()
        new_heading_norm = re.sub(r"\s+", " ", new_heading_text).casefold()

        if prev_heading_norm == new_heading_norm:
            # De-dupe consecutive identical headings (common with running headers).
            return section_path_stack

    section_path_stack.append(
        SectionHeadingRef(
            item_index=chain[0][1],
            page_index=chain[0][0],
            text=new_heading_text,
        )
    )

    return section_path_stack[-max_len:]
