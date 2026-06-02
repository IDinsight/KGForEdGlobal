"""This module contains utility functions for stitching the document IR."""

# Standard Library
import re
import uuid

from collections import defaultdict
from typing import Any, Optional, cast

# Third Party Library
from loguru import logger

# Package Library
from skg.document_ir.schemas import (
    BlockSegment,
    BlockSlice,
    SectionHeadingRef,
    Segment,
    SegmentProvenance,
    TableRowProvenance,
    TableSegment,
    TableSlice,
)
from skg.document_ir.utils import (
    ItemKey,
    canonicalize_local_code_for_compare,
    compatible_kinds_for_stitch,
    normalize_text,
    row_signature,
)
from skg.page_ir_extraction.schemas import (
    Block,
    FigureUnit,
    ListItem,
    PageIR,
    Table,
    TableCell,
    TableRow,
    TextUnit,
)
from skg.regexes import ALPHA_RE, DIGIT_RE
from skg.schemas import StitchingConfig
from skg.utils.constants import BlockType

_ChainItem = tuple[int, int, Block | Table]


def _are_items_compatible_for_segment_stitching(
    *, next_item: Block | Table, prev_item: Block | Table
) -> bool:
    """Return whether two linked items can be materialized into one stitched segment.

    This is intentionally stricter than page-break candidate compatibility. The
    page-break linker may allow some fallback block matches (for example, Paragraph
    <-> List) to support uncertain continuation evidence, but BlockSegment
    materialization requires all block slices to share the same `block_type`.
    Therefore, block links are only segment-stitchable when both slices are Blocks and
    their `block_type` values match exactly.

    Parameters
    ----------
    next_item
        The candidate continuation item.
    prev_item
        The current item in the chain.

    Returns
    -------
    bool
        True if the link is safe for segment materialization, False otherwise.
    """

    if not compatible_kinds_for_stitch(next_item=next_item, prev_item=prev_item):
        return False

    if isinstance(prev_item, Table) and isinstance(next_item, Table):
        return True

    if isinstance(prev_item, Block) and isinstance(next_item, Block):
        return prev_item.block_type == next_item.block_type

    return False


def _build_continuation_chain(
    *,
    items_lookup: dict[int, dict[int, Block | Table]],
    links: dict[ItemKey, ItemKey],
    start_item: Block | Table,
    start_key: ItemKey,
    warnings: list[str],
) -> list[_ChainItem]:
    """Follow page-break links to build one logical continuation chain.

    The walker is intentionally defensive:

    1. Broken destination pages/items terminate the chain with a warning.
    2. Cycles raise immediately instead of risking an infinite loop.
    3. Cross-kind hops raise immediately because the upstream linker should have
        filtered them out already.

    This function starts from the current item and repeatedly follows
    links[current_key] until there is no next link. While walking, it:
        - Appends each item to the chain
        - Guards against cycles
        - Warns and stops on broken destination lookups
        - Raises if a hop is not safe for segment stitching

    So, for a paragraph that flows across three pages, the output is a list like:

        [
          (0, 9, paragraph_block_page0),
          (1, 0, paragraph_block_page1),
          (2, 1, paragraph_block_page2),
        ]

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
    list[_ChainItem]
        A list of (page_index, item_index, item) tuples representing the chain of
        continuation items.

    Raises
    ------
    ValueError
        If the link graph contains a cycle or an incompatible continuation hop for
        segment materialization.
    """

    chain: list[_ChainItem] = []
    current_page_index, current_item_index = start_key
    current_item = start_item
    seen_keys: set[ItemKey] = set()

    while True:
        current_key = (current_page_index, current_item_index)
        chain.append((current_page_index, current_item_index, current_item))
        seen_keys.add(current_key)

        next_link = links.get(current_key, None)

        if next_link is None:
            break

        if next_link in seen_keys:
            raise ValueError(
                f"Cycle detected while building continuation chain: "
                f"start={start_key}, current={current_key}, next={next_link}, "
                f"chain={_summarize_chain_items(chain)}"
            )

        next_page_index, next_item_index = next_link
        next_page_map = items_lookup.get(next_page_index, None)

        # Broken link (page missing).
        if next_page_map is None:
            msg = (
                f"Broken link from {current_key}->{next_link}: "
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
                f"Broken link from {current_key}->{next_link}: "
                f"Item {next_item_index} not found on page {next_page_index}."
            )
            logger.warning(msg)
            warnings.append(msg)
            break

        if not _are_items_compatible_for_segment_stitching(
            next_item=next_item, prev_item=current_item
        ):
            raise ValueError(
                f"Incompatible page-break link while building continuation chain: "
                f"current={current_key}, next={next_link}, "
                f"current_type={type(current_item).__name__}, "
                f"next_type={type(next_item).__name__}, "
                f"current_block_type={getattr(current_item, 'block_type', None)!r}, "
                f"next_block_type={getattr(next_item, 'block_type', None)!r}"
            )

        # Advance to next item.
        current_item = next_item
        current_item_index = next_item_index
        current_page_index = next_page_index

    return chain


def _compute_segment_id(
    *, doc_key: str, item_index: int, kind: str, page_index: int
) -> str:
    """Compute a deterministic UUIDv5 for a stitched segment. Segment IDs must be
    stable across reruns and (ideally) globally unique across PDFs, so we include the
    PDF doc_key plus the first source item pointer.

    Example:

    If a table starts at (page=4, item=6), the segment ID will be based on that exact
    origin, even if the table continues for 3 more pages.

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


def _create_item_addr(*, item_index: int, page_index: int) -> str:
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


def _dfs(
    *,
    links: dict[ItemKey, ItemKey],
    node_key: ItemKey,
    path: list[ItemKey],
    visit_state: dict[ItemKey, int],
) -> None:
    """Depth-first search to detect cycles in the link graph. This function implements
    the 3-color DFS algorithm to detect cycles.

    Example: A Valid Linear Path (with no cycles)

    # A points to B, B points to C, C points to D.
    good_links = {
        'A': 'B',
        'B': 'C',
        'C': 'D'
    }

    The function visits A, marks it as visiting (state 1), and moves to B. It continues
    to C and D. Since D has no next key, it resolves and is marked as fully visited
    (state 2). The function then traces back, marking C, B, and A as state 2. No node
    is ever encountered while it is in state 1.

    Example: A Simple Circular Cycle

    # A points to B, B points to C, C points back to A.
    simple_cycle = {
        'A': 'B',
        'B': 'C',
        'C': 'A'
    }

    The function visits A, B, and C (all currently state 1). When it looks at C's
    target, it sees A. Because A is already in state 1 (currently being visited in the
    current path stack), it knows it has hit a cycle and stops execution.

    Parameters
    ----------
    links
        Mapping of source item key to destination item key.
    node_key
        The current node key being visited.
    path
        The path of node keys taken to reach the current node, used for cycle reporting.
    visit_state
        Dictionary tracking the visit status of nodes (1 = visiting, 2 = visited).

    Raises
    ------
    ValueError
        If a cycle is detected in the graph.
    """

    state = visit_state.get(node_key, 0)

    if state == 1:
        cycle_start = path.index(node_key) if node_key in path else 0
        cycle_path = path[cycle_start:] + [node_key]
        raise ValueError(f"Cycle detected in page-break link graph: {cycle_path}.")

    if state == 2:
        return

    visit_state[node_key] = 1
    next_key = links.get(node_key)

    if next_key is not None:
        _dfs(
            links=links,
            node_key=next_key,
            path=path + [node_key],
            visit_state=visit_state,
        )

    visit_state[node_key] = 2


def _drop_repeated_header(
    *, base_header_rows: list[TableRow], header_row_count: int, next_table: Table
) -> tuple[list[TableRow], int]:
    """Return next_table.rows with repeated header removed if warranted.

    This function compares the top k rows of the continuation slice against the base
    header rows using row_signature(). It is intentionally conservative:
        - If full header match is found, drop those rows
        - If full match fails but repeats_header=True, it may try a smaller partial
            match
        - If the header rows do not actually match, it does not drop them just because
            a hint said repeats_header=True

    This is a good guard against losing real rows like checkpoint rows or section rows
    at the top of continuation pages.

    NB: Never drop "header" rows solely because the verifier says `repeats_header=True`
    if the continuation slice does not itself contain header rows (or if the would-be
    header rows do not match the base header). This avoids losing real content when a
    page begins with a checkpoint/section row inside the table grid (common in many
    curricula).

    Example 1: repeated header really matches

    Base header row from first slice:

    | Topic | Specific Competence | Expected Standard |

    Next page begins with the exact same row.

    Then:
        - dropped_header_rows = 1
        - rows_to_add = next_item.rows[1:]

    So the repeated header is removed from the stitched table.

    Example 2: extractor says repeated header, but it actually is a checkpoint row

    Suppose next page starts with:

    | Palier 2 Assessment | | |

    and repeats_header=True.

    Because that row does not match the base header, nothing is dropped. The code warns
    and keeps all rows to avoid content loss.

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


def _expand_table_rows_to_rows_grid(
    segment: TableSegment,
) -> tuple[list[TableRow], list[list[dict[str, Any]]]]:
    """Expand a stitched table's ragged rows with row_span/col_span into a rectangular
    grid of shape (n_rows x n_cols). Output rows have exactly n_cols cells each, and
    every output TableCell has row_span=1, col_span=1.

    This function converts ragged rows with row_span/col_span into a rectangular grid
    where:
        - Every row has exactly n_cols cells
        - Every output cell has row_span=1 and col_span=1
        - Empty grid cells get TextUnit(language="und", text="")
        - A parallel grid_sources matrix records which stitched source row each grid
            cell came from

    NB: For downstream stability, visually empty grid cells are normalized to an
    explicit empty TextUnit (language='und', text='') rather than null. This ensures
    every TableCell in `rows_grid` has a `text` payload in JSON (even if blank).

    Example: rowspan expansion

    Suppose stitched rows contain:

    Row 0:
        - "Topic" col_span=1
        - "Competence" col_span=2

    Row 1:
        - "Numbers" row_span=2
        - "Count objects" col_span=1
        - "Expected Standard" col_span=1

    Row 2:
        - blank padding cell
        - "Recognize numerals"
        - "Reads numerals correctly"

    rows_grid becomes a clean rectangular 3-column table with the rowspan expanded
    across both affected rows, and grid_sources shows which original stitched row
    populated each cell.

    Parameters
    ----------
    segment
        The stitched TableSegment.

    Returns
    -------
    tuple[list[TableRow], list[list[dict[str, Any]]]]
        A tuple where the first element is the list of expanded TableRows, and the
        second element is the grid_sources mapping (per-cell source row index).

    Raises
    ------
    ValueError
        If the segment has invalid n_cols or if overlapping spans are detected during
        grid population.
    """

    n_cols = segment.n_cols
    n_rows = len(segment.rows)

    if n_cols <= 0:
        raise ValueError(
            f"Cannot expand spans: invalid n_cols={segment.n_cols} "
            f"(segment_id={segment.segment_id})"
        )

    # Initialize empty grid: grid[row][col] = {"text": TextUnit | None, "source_row": int}.
    grid: list[list[dict[str, Any]]] = [
        [{"text": None, "source_row": -1} for _ in range(n_cols)] for _ in range(n_rows)
    ]

    # Populate spans into grid.
    _populate_grid_spans(segment=segment, grid=grid, n_rows=n_rows, n_cols=n_cols)

    # Convert grid to TableRow list and aligned grid_sources.
    grid_sources: list[list[dict[str, Any]]] = []
    rows_grid: list[TableRow] = []

    for r in range(n_rows):
        out_cells: list[TableCell] = []
        src_row: list[dict[str, Any]] = []

        for c in range(n_cols):
            cell_text = grid[r][c]["text"]
            cell_text = cell_text or TextUnit(language="und", text="", text_en=None)
            out_cells.append(TableCell(col_span=1, row_span=1, text=cell_text))
            src_row.append({"source_row": grid[r][c]["source_row"]})

        rows_grid.append(TableRow(cells=out_cells))
        grid_sources.append(src_row)

    return rows_grid, grid_sources


def _fill_down_table_rows(
    *, header_row_count: int, rows: list[TableRow], table_filldown_group_cols_max: int
) -> list[TableRow]:
    """Fill down visually empty cells in the first `filldown_group_cols_max` columns.
    This reconstructs the implicit semantics of merged cells/rowspans often used in
    curriculum tables (e.g., Topic/Sub-topic cells left blank on subsequent rows).

    NB: The caller passes the span-expanded `rows_grid` (from
    `_expand_table_rows_to_rows_grid`), **not** the raw stitched `rows`. Every cell in
    the input therefore has `row_span=1` and `col_span=1`, and every row has exactly
    `n_cols` cells. This means the fill-down logic only needs to check for empty text;
    it does not need to account for row/col spans.

    This function only fills empty cells in the first table_filldown_group_cols_max
    columns, never header rows, and never overwrites non-empty cells. It is meant to
    reconstruct implicit grouping semantics common in curriculum tables.

    The process is as follows:

    1. Header rows are NOT filled down.
    2. Only fills if the target cell is empty (None or whitespace).
    3. Never overwrites non-empty cells.
    4. Returns a deep-copied row list (does not mutate input).

    Example:

    Suppose the span-expanded rows_grid is (all spans already resolved to 1×1 cells):

    Topic   Sub-topic   Competence
    Numbers Counting    Count objects
    ""      Numerals    Read numerals
    Shapes  Circles     Identify circles
    ""      Squares     Identify squares

    If filldown is enabled for the first 2 columns, the returned rows_filldown becomes:

    Topic   Sub-topic   Competence
    Numbers Counting    Count objects
    Numbers Numerals    Read numerals
    Shapes  Circles     Identify circles
    Shapes  Squares     Identify squares

    The input rows_grid remains unchanged; the output is a convenience normalization.

    Parameters
    ----------
    header_row_count
        The number of header rows at the top of the table.
    rows
        The span-expanded rectangular table rows (from `rows_grid`). Every row must
        have the same number of cells, each with `row_span=1` and `col_span=1`.
    table_filldown_group_cols_max
        The maximum number of leading group columns to fill down.

    Returns
    -------
    list[TableRow]
        The filled-down table rows.
    """

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
                if isinstance(last_non_empty_text_unit, TextUnit):
                    cell.text = last_non_empty_text_unit.model_copy(deep=True)
            else:
                last_non_empty[ci] = cell.text

    return output_rows


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

    Example:

    If slices declare n_cols=3, but one stitched row actually sums to 4 because of a
    wide continuation row, final n_cols = 4 and a warning is emitted.

    Example:

    Header row:

    | Specific Competence | Expected Standard |

    with col_spans [1,2] and n_cols=3

    might canonicalize to something like:

    ["specific competence", "expected standard", ""]

    then columns signature becomes a normalized joined string

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
        columns_signature = TableSegment._build_columns_signature(
            header_rows_canonical=header_rows_canonical
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


def _infer_header_row_count_from_rows(
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
        alpha = len(ALPHA_RE.findall(text))
        digits = len(DIGIT_RE.findall(text))

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


def _materialize_segment(
    *,
    chain: list[_ChainItem],
    doc_key: str,
    item_index: int,
    page_index: int,
    repair_hyphenation: bool,
    section_path: list[SectionHeadingRef],
    table_filldown_enabled: bool,
    table_filldown_group_cols_max: int,
    warnings: list[str],
) -> Segment:
    """Materialize one homogeneous continuation chain as a stitched segment.

    The chain is expected to be segment-stitchable because page-break linking should
    already have enforced compatible continuation edges. If a mixed block/table chain
    or a mixed block-type chain reaches this function, that indicates an upstream
    invariant failure and we raise immediately rather than silently dropping already
    visited items.

    This function inspects the first item in the chain and chooses a path:
        - If the first item is a Table, the whole chain must be tables, and it calls
            _stitch_table_chain().
        - Otherwise the whole chain must be Blocks, and all blocks must share the same
            block_type, then it calls _stitch_block_chain().

    If a mixed chain somehow reaches this point, it raises immediately because that
    indicates an upstream invariant failure.

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

    Raises
    ------
    ValueError
        If the chain contains a mix of Block and Table items.
    """

    first_chain_item = chain[0][2]

    if isinstance(first_chain_item, Table):
        if not all(isinstance(item, Table) for _, _, item in chain):
            raise ValueError(
                f"Mixed-kind chain reached _materialize_segment for table start: "
                f"start={(page_index, item_index)}, chain={_summarize_chain_items(chain)}"
            )

        table_chain = cast(list[tuple[int, int, Table]], chain)

        return _stitch_table_chain(
            chain=table_chain,
            doc_key=doc_key,
            section_path=section_path,
            table_filldown_enabled=table_filldown_enabled,
            table_filldown_group_cols_max=table_filldown_group_cols_max,
            warnings=warnings,
        )

    if not all(isinstance(item, Block) for _, _, item in chain):
        raise ValueError(
            f"Mixed-kind chain reached _materialize_segment for block start: "
            f"start={(page_index, item_index)}, chain={_summarize_chain_items(chain)}"
        )

    block_chain = cast(list[tuple[int, int, Block]], chain)
    first_block_type = block_chain[0][2].block_type

    if any(block.block_type != first_block_type for _, _, block in block_chain):
        raise ValueError(
            f"Mixed block_type chain reached _materialize_segment: "
            f"start={(page_index, item_index)}, "
            f"expected_block_type={first_block_type!r}, "
            f"chain={_summarize_chain_items(chain)}"
        )

    return _stitch_block_chain(
        chain=block_chain,
        doc_key=doc_key,
        repair_hyphenation=repair_hyphenation,
        section_path=section_path,
    )


def _populate_grid_spans(
    *, segment: TableSegment, grid: list[list[dict[str, Any]]], n_rows: int, n_cols: int
) -> None:
    """Handle cell parsing, cursor placement, and span explosion.

    This function walks the stitched rows, places cells into the first available slots,
    respects row/col spans, and prevents overlap. It treats truly blank padding cells
    specially so they do not incorrectly right-shift later cells.

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
        current_code_key = canonicalize_local_code_for_compare(current_local_code)
        next_code_key = canonicalize_local_code_for_compare(next_local_code)

        if current_code_key and next_code_key and current_code_key != next_code_key:
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
    # NB: Determine how many header rows we should attempt to match/drop. We ALWAYS
    # require a match against the base header before dropping anything. This prevents
    # losing real content when a continuation page begins with a checkpoint/section row
    # inside the table grid (common in many curricula), even if the verifier marked
    # repeats_header=True.
    next_hrc = int(next_item.header_row_count or 0)
    match_k = segment_header_row_count

    if next_hrc > 0 and next_hrc != segment_header_row_count:
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
        item_addr=_create_item_addr(
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

    This function only repairs a narrow case:
        - Not a header row
        - Row has 1 or 2 cells
        - All row spans are 1
        - Last cell has non-blank text
        - Total col_span is still less than n_cols

    Then it expands the last cell to fill the missing trailing columns.

    Example:

    Suppose n_cols = 4, and we have a body row:

    | Palier 1 |

    extracted as one cell with col_span = 1.

    The repair turns it into:

    | Palier 1 | (spans remaining 3 columns) |

    by increasing the last cell’s col_span from 1 to 4.

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
        new_last_payload = last.model_dump(mode="python")
        new_last_payload["col_span"] = last.col_span + missing
        new_last = TableCell.model_validate(obj=new_last_payload)
        new_row = TableRow(cells=cells[:-1] + [new_last])

        msg = (
            f"[table_colspan_repair] segment_id={segment_id} row={r_idx}: "
            f"extended last cell col_span by +{missing} to fill n_cols={n_cols}."
        )
        logger.warning(msg)
        warnings.append(msg)
        out.append(new_row)

    return out


def _resolve_header_row_count(
    *, first_item: Table, item_index: int, page_index: int, warnings: list[str]
) -> int:
    """Determine header row count, using inference if extractor provided none.

    This function starts with first_item.header_row_count. If that is <= 0, it runs a
    deterministic fallback heuristic over the first few rows and only adopts the
    inferred value if confidence is at least 0.65. So the first slice defines the
    segment’s header model.

    Example: extractor left header row count at 0

    Suppose the first row is clearly word-heavy and header-like:

    | Topic | Sub-topic | Expected Standard |
    | numbers | ... | ... |

    The heuristic may infer header_row_count=1 and warn that it was inferred.

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
        inferred_hrc, confidence = _infer_header_row_count_from_rows(
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

    NB: Returns the raw (stripped) local code as extracted--no canonicalization (e.g.,
    "Tableau 4" stays "Tableau 4"). Canonicalization is deferred to post-stitching.

    This matters because same-page caption propagation may already have written a code
    onto one table slice during normalization.

    Example:

    If page 4 table has local_code=None and page 5 table has local_code="Table 4", the
    segment adopts "Table 4" immediately.

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


def _row_provenance_by_stitched_index(
    segment: TableSegment,
) -> list[TableRowProvenance]:
    """Derive stitched-row provenance deterministically from `segment.slices`.

    The mapping replays the already-decided slice-level header dropping recorded on
    each `TableSlice` via `dropped_header_rows`; it does not re-infer repeated headers
    from `repeats_header`. Each stitched row receives the parent slice bbox plus an
    approximate `row_bbox` produced by evenly splitting the slice bbox over the slice's
    visual rows.

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
        If the segment has no slices, a slice bbox is invalid, or the mapping length
        mismatches.
    """

    if not segment.slices:
        raise ValueError(
            f"TableSegment {segment.segment_id} has no slices; cannot derive row provenance."
        )

    mapping: list[TableRowProvenance] = []

    for slice_index, sl in enumerate(segment.slices):
        bbox = sl.bbox

        if not (isinstance(bbox, list) and len(bbox) == 4):
            raise ValueError(
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


def _stitch_block_chain(
    *,
    chain: list[tuple[int, int, Block]],
    doc_key: str,
    repair_hyphenation: bool,
    section_path: list[SectionHeadingRef],
) -> BlockSegment:
    """Stitch a chain of block slices.

    Block continuation chains can carry different payload types, so the stitcher
    handles each one deliberately:

    1. Text (`Block.text`/`TextUnit`) is additive across slices, so all slice text
        units are joined deterministically into one segment-level `combined_text` and
        `text`.
    2. Lists (`Block.list_items`) are additive across slices, so list items are
        concatenated into one segment-level list.
    3. Figures (`Block.figure`) remain typed `FigureUnit` payloads. They are not merged
        across slices; the first non-null figure is preserved at the segment level and
        each slice keeps its own figure payload for provenance fidelity.

    In other words, this function:
        - Computes a deterministic segment ID from doc_key + first slice pointer
        - Accumulates BlockSlice objects, one per source slice
        - Accumulates SegmentProvenance, one per source slice
        - Promotes the first non-empty local_code across the chain
        - Concatenates list items across slices
        - Preserves the first non-null figure payload
        - Combines all TextUnits into one segment-level text object

    If multiple slice languages appear, the stitched segment text gets language "mul";
    otherwise it keeps the one language.

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
    segment_id = _compute_segment_id(
        doc_key=doc_key,
        item_index=first_chain_item_index,
        kind="block",
        page_index=first_chain_page_index,
    )

    figure_payload: FigureUnit | None = None
    list_items: list[ListItem] = []
    resolved_local_code: str | None = None
    segment_provenance: list[SegmentProvenance] = []
    slices: list[BlockSlice] = []
    text_units: list[TextUnit] = []

    for page_index, item_index, block in chain:
        # Promote local_code across the stitched chain: take the first non-empty code
        # encountered in any slice. NB: preserves original form (no canonicalization).
        if resolved_local_code is None and (lc := _strip_local_code(block.local_code)):
            resolved_local_code = lc

        block_figure = (
            block.figure.model_copy(deep=True) if block.figure is not None else None
        )
        block_list_items = (
            [li.model_copy(deep=True) for li in block.list_items]
            if block.list_items
            else None
        )
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
                item_addr=_create_item_addr(
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
        if figure_payload is None and block_figure is not None:
            figure_payload = block_figure

    combined_text: str | None = None
    stitched_text: TextUnit | None = first_chain_item.text

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
        list_items=list_items or None,
        local_code=resolved_local_code,
        section_path=section_path,
        segment_id=segment_id,
        segment_provenance=segment_provenance,
        slices=slices,
        text=stitched_text,
    )


def _stitch_table_chain(
    *,
    chain: list[tuple[int, int, Table]],
    doc_key: str,
    section_path: list[SectionHeadingRef],
    table_filldown_enabled: bool,
    table_filldown_group_cols_max: int,
    warnings: list[str],
) -> TableSegment:
    """Stitch a chain of table slices.

    This function is where a linked chain of page-level Table items turns into one
    stitched TableSegment, with both raw stitched rows and derived structural views. It
    does not decide the links itself. By the time this runs, the chain is already known
    to be a homogeneous table chain and segment-stitchable. When this function is
    called, _materialize_segment() has already confirmed the chain is all tables, not
    mixed block/table content.

    The table stitcher resolves the segment state up front and then appends each later
    slice deterministically:

    1. Compute a stable segment ID from the first slice pointer.
    2. Resolve the initial segment `local_code` as the first non-empty code anywhere in
       the chain, before building slices/provenance.
    3. Resolve the segment header row count from the first slice, with deterministic
       inference only when the extractor left it at zero.
    4. Start stitched_rows from the first slice and create the first-slice TableSlice +
       SegmentProvenance.
    5. Process each later slice via `_process_next_table_slice()`, which preserves the
       segment `local_code`, records any dropped repeated-header rows, and contributes
       only the rows that should survive into the stitched table.
    6. Finalize n_cols, canonical header rows, and columns signature.
    7. Repair short continuation rows into trailing colspans.
    8. Build the TableSegment.
    9. Derive rows_grid, grid_sources, row_provenance, and optional rows_filldown.

    So the returned table segment is richer than the block segment: it includes both
    the stitched visual rows and normalized structural views of the table.

    Parameters
    ----------
    chain
        List of (page_index, item_index, Table) tuples representing the slices to
        stitch.
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
    segment_id = _compute_segment_id(
        doc_key=doc_key,
        item_index=first_item_index,
        kind="table",
        page_index=first_page_index,
    )

    # 2.
    local_code = _resolve_initial_local_code(chain)

    # 3.
    header_row_count = _resolve_header_row_count(
        first_item=first_item,
        item_index=first_item_index,
        page_index=first_page_index,
        warnings=warnings,
    )

    # 4.
    stitched_rows: list[TableRow] = list(first_item.rows)
    header_rows = stitched_rows[:header_row_count] if header_row_count > 0 else []
    slices: list[TableSlice] = [
        TableSlice(
            bbox=first_item.bbox,
            boundary=first_item.boundary,
            dropped_header_rows=0,
            header_row_count=header_row_count,
            item_index=first_item_index,
            local_code=local_code,  # Potentially resolved local code for the segment
            page_index=first_page_index,
            repeats_header=first_item.repeats_header,
            rows=first_item.rows,
        )
    ]
    segment_provenance: list[SegmentProvenance] = [
        SegmentProvenance(
            bbox=first_item.bbox,
            boundary=first_item.boundary,
            item_addr=_create_item_addr(
                item_index=first_item_index, page_index=first_page_index
            ),
            item_index=first_item_index,
            kind=first_item.kind,
            local_code=local_code,  # Potentially resolved local code for the segment
            page_index=first_page_index,
            repeats_header=first_item.repeats_header,
        )
    ]

    # 5.
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

        # Update state. Also carry forward potentially new local code for subsequent
        # table slices.
        local_code = slice_result["local_code"]
        slices.append(slice_result["slice"])
        segment_provenance.append(slice_result["provenance"])
        stitched_rows.extend(slice_result["rows_to_add"])

    # 6.
    n_cols, columns_signature, header_rows_canonical = _finalize_table_structure(
        chain=chain,
        stitched_rows=stitched_rows,
        header_rows=header_rows,
        segment_id=segment_id,
        local_code=local_code,
        warnings=warnings,
    )

    # 7.
    stitched_rows_for_segment = [r.model_copy(deep=True) for r in stitched_rows]
    stitched_rows_for_segment = _repair_short_rows_missing_trailing_cols_as_colspan(
        header_row_count=header_row_count,
        n_cols=n_cols,
        rows=stitched_rows_for_segment,
        segment_id=segment_id,
        warnings=warnings,
    )

    # 8.
    table_segment = TableSegment(
        columns_signature=columns_signature,
        header_row_count=header_row_count,
        header_rows=(
            stitched_rows_for_segment[:header_row_count] if header_row_count > 0 else []
        ),
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

    # 9.
    rows_grid, grid_sources = _expand_table_rows_to_rows_grid(table_segment)
    row_provenance = _row_provenance_by_stitched_index(table_segment)
    rows_filldown = None

    if table_filldown_enabled:
        # Fill down on the span-expanded rectangular grid.
        rows_filldown = _fill_down_table_rows(
            header_row_count=header_row_count,
            rows=rows_grid,
            table_filldown_group_cols_max=table_filldown_group_cols_max,
        )

    table_segment_payload = table_segment.model_dump(mode="python")
    table_segment_payload.update(
        {
            "grid_sources": grid_sources,
            "row_provenance": row_provenance,
            "rows_filldown": rows_filldown,
            "rows_grid": rows_grid,
        }
    )
    return TableSegment.model_validate(table_segment_payload)


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


def _summarize_chain_items(chain: list[_ChainItem]) -> str:
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


def _update_section_stack(
    *,
    chain: list[tuple[int, int, Block | Table]],
    max_len: int,
    section_path_stack: list[SectionHeadingRef],
    warnings: list[str],
) -> list[SectionHeadingRef]:
    """Update the section path stack if the current chain represents a heading.

    This function only changes the stack if the chain’s first item is a HEADING block.
    If so, it extracts heading text (or falls back to local_code), de-dupes consecutive
    identical headings, appends a new SectionHeadingRef, and truncates the stack to
    max_section_path_length. If the chain is not a heading, the stack is left unchanged.

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
            item_index=chain[0][1], page_index=chain[0][0], text=new_heading_text
        )
    )

    return section_path_stack[-max_len:]


def _validate_link_graph(
    *,
    items_lookup: dict[int, dict[int, Block | Table]],
    links: dict[ItemKey, ItemKey],
) -> None:
    """Validate the cross-page link graph before segment stitching begins.

    The stitcher expects a functional acyclic graph over existing items:

    1. Every source key must exist in `items_lookup`.
    2. Every destination key must exist in `items_lookup`.
    3. Every destination must have in-degree exactly 1.
    4. Every link must be safe for segment materialization.
    5. The graph must be acyclic.

    This function checks that every link source exists, every destination exists, each
    destination has in-degree 1, every link is safe for segment materialization, and the
    graph is acyclic. If any of that fails, stitching stops before any segments are
    built.

    Parameters
    ----------
    items_lookup
        Pre-built mapping of page_index to {item_index: item} dicts.
    links
        Mapping of source item key to destination item key.

    Raises
    ------
    ValueError
        If the link graph is broken, ambiguous, incompatible, or cyclic.
    """

    indegree_by_dest: defaultdict[ItemKey, int] = defaultdict(int)

    for src_key, dst_key in links.items():
        src_page_index, src_item_index = src_key
        dst_page_index, dst_item_index = dst_key
        src_page_items = items_lookup.get(src_page_index)
        dst_page_items = items_lookup.get(dst_page_index)

        if src_page_items is None or src_item_index not in src_page_items:
            raise ValueError(
                f"Invalid page-break link source: src={src_key} was not found in items_mapping."
            )
        if dst_page_items is None or dst_item_index not in dst_page_items:
            raise ValueError(
                f"Invalid page-break link destination: dst={dst_key} was not found in items_mapping."
            )

        src_item = src_page_items[src_item_index]
        dst_item = dst_page_items[dst_item_index]

        if not _are_items_compatible_for_segment_stitching(
            next_item=dst_item, prev_item=src_item
        ):
            raise ValueError(
                f"Page-break link is not segment-stitchable: src={src_key}, dst={dst_key}, "
                f"src_type={type(src_item).__name__}, dst_type={type(dst_item).__name__}, "
                f"src_block_type={getattr(src_item, 'block_type', None)!r}, "
                f"dst_block_type={getattr(dst_item, 'block_type', None)!r}."
            )

        indegree_by_dest[dst_key] += 1

        if indegree_by_dest[dst_key] > 1:
            raise ValueError(
                f"Page-break link graph is not functional: destination {dst_key} has "
                f"indegree={indegree_by_dest[dst_key]}."
            )

    visit_state: dict[ItemKey, int] = {}

    for src_key in links:
        if visit_state.get(src_key, 0) == 0:
            _dfs(links=links, node_key=src_key, path=[], visit_state=visit_state)


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


def build_stitched_segments(
    *,
    config: StitchingConfig,
    doc_key: str,
    items_mapping: dict[int, list[tuple[int, Block | Table]]],
    links: dict[ItemKey, ItemKey],
    page_irs: list[PageIR],
    warnings: list[str],
) -> list[Segment]:
    """Build stitched document segments from normalized page items and cross-page links.

    Iterates through the document in reading order, resolves cross-page continuation
    chains using the provided links, and materializes them into fully stitched
    segments. Maintains a semantic-light heading context to preserve the section path.

    This function is called from stitch_document_ir() after two earlier stages have
    already happened:

    1. Each page’s items were normalized into items_mapping, and
    2. compute_page_break_links() produced a sparse forward link map like
        (page_i, item_j) -> (page_i + 1, item_k).

    So build_stitched_segments() is not deciding whether two items continue across
    pages; it is consuming a link graph that has already been decided and turning it
    into final stitched segments.

    Parameters
    ----------
    config
        The stitching run configuration containing parameters for segment
        materialization.
    doc_key
        The expected document key for all page IRs.
    items_mapping
        A mapping of page indices to a list of tuples containing the original item
        index and the item itself (Block or Table).
    links
        A mapping of source item keys to destination item keys representing cross-page
        links.
    page_irs
        Validated PageIR list in page order.
    warnings
        A list to collect any warnings generated during the stitching process.

    Returns
    -------
    list[Segment]
        A list of materialized segments representing the fully stitched document IR.

    Raises
    ------
    ValueError
        If the page-break link graph is invalid for segment stitching.
    """

    # items_lookup is a page-index -> item-index -> item lookup so the chain walker can
    # jump directly to linked destinations. Built once and shared with validation.
    items_lookup: dict[int, dict[int, Block | Table]] = {
        page_index: dict(items) for page_index, items in items_mapping.items()
    }

    _validate_link_graph(items_lookup=items_lookup, links=links)

    # Set of destination keys to identify items that are continuations.
    continuations = set(links.values())

    # Reverse map: destination -> list of sources that point to it (for debugging).
    reverse_links: dict[ItemKey, list[ItemKey]] = defaultdict(list)

    for src, dst in links.items():
        reverse_links[dst].append(src)

    # section_path_stack holds the current heading breadcrumbs as we iterate through
    # the document. When we encounter a heading block, we push it onto the stack, and
    # we snapshot the current stack as the section path for any segments we stitch from
    # that point until the next heading. This allows us to maintain a semantic-light
    # section context without needing an explicit section parser or a separate heading
    # detection pass.
    section_path_stack: list[SectionHeadingRef] = []

    segments: list[Segment] = []
    visited: set[ItemKey] = set()  # Ensure an item is only consumed once

    # Iterate in document reading order: page order, then item order.
    for page_ir in page_irs:
        current_page_index = page_ir.page_index
        current_page_items = items_mapping.get(current_page_index, [])

        logger.info(f"Stitching page {current_page_index}...\n")

        for orig_item_index, item in current_page_items:
            key = (current_page_index, orig_item_index)

            if key in visited:  # Skip if already processed
                continue

            # If this item is a continuation destination but wasn't actually consumed
            # by a previous chain, treat it as an "orphan continuation" and process it
            # as a standalone chain start (with a warning).
            if key in continuations:
                text = (
                    f"Orphan continuation destination encountered; "
                    f"it was pointed-to by a prior page-break link but not consumed in "
                    f"any chain. dest={key}, sources={reverse_links.get(key, [])}. "
                    f"Processing as standalone."
                )
                logger.warning(text)
                warnings.append(text)

            # Build the continuation chains.
            chain = _build_continuation_chain(
                items_lookup=items_lookup,
                links=links,
                start_item=item,
                start_key=key,
                warnings=warnings,
            )

            # Mark all items in chain as visited.
            for chain_page_index, chain_item_index, _ in chain:
                visited.add((chain_page_index, chain_item_index))

            # Snapshot section_path *before* materializing this segment. The current
            # item should not appear in its own section path.
            section_path_snapshot = list(section_path_stack)

            # Materialize a stitched segment from the chain.
            segments.append(
                _materialize_segment(
                    chain=chain,
                    doc_key=doc_key,
                    item_index=orig_item_index,
                    page_index=current_page_index,
                    repair_hyphenation=config.repair_hyphenation,
                    section_path=section_path_snapshot,
                    table_filldown_enabled=config.table_filldown_enabled,
                    table_filldown_group_cols_max=config.table_filldown_group_cols_max,
                    warnings=warnings,
                )
            )

            # Update section heading stack *after* processing a heading block. We use
            # the first item in the chain (heading segments are standalone).
            section_path_stack = _update_section_stack(
                chain=chain,
                max_len=config.max_section_path_length,
                section_path_stack=section_path_stack,
                warnings=warnings,
            )

    logger.success("Successfully stitched page IRs!")

    return segments
