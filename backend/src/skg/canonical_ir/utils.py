"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import hashlib
import re
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    CanonicalIR,
    LeafParsingConfig,
    LeafStatement,
    NormalizedRow,
    ParserConfig,
)
from skg.config import Settings
from skg.document_ir.schemas import TableSegment, TextUnit
from skg.schemas import RunCtx
from skg.utils.constants import BlockType, StatementRole
from skg.utils.general import compute_sha256_hex, make_dir, write_to_json


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path


def _as_textunit(
    *, default_language: str = "und", value: TextUnit | dict | str | None
) -> TextUnit | None:
    """Coerce various input formats into a TextUnit object.

    Parameters
    ----------
    default_language
        The language code to use if the input is a plain string or missing language.
    value
        The input value to convert. Can be a raw string, a dict, or existing TextUnit.

    Returns
    -------
    TextUnit | None
        The valid TextUnit or None if the input was empty/null.
    """

    # Delegate parsing logic to the centralized utility (returns a dict or None).
    data = _coerce_text_to_textunit_like(value)

    if data is None:
        return None

    # Handle language override.
    if default_language != "und" and data.get("language") == "und":
        data["language"] = default_language

    # Convert the dict to the TextUnit object expected by CanonicalNode.
    return TextUnit.model_validate(data)


def _block_text_for_matching(*, bt_str: str, seg: Any) -> str:
    """Extract full text content from a block segment for regex matching.

    Parameters
    ----------
    bt_str
        The block type string (e.g., "list", "paragraph").
    seg
        The document segment object (DocumentIR schema).

    Returns
    -------
    str
        The extracted text content.
    """

    if bt_str == BlockType.LIST.value:
        items = getattr(seg, "list_items", None) or []
        parts: list[str] = []
        for li in items:
            tu = (
                getattr(li, "text", None)
                if not isinstance(li, dict)
                else li.get("text")
            )
            parts.append(_normalize_space(_coerce_text_to_str(tu)))
        return "\n".join([p for p in parts if p])

    return _normalize_space(
        _coerce_text_to_str(getattr(seg, "text", None))
        or getattr(seg, "combined_text", "")
        or ""
    )


def _calculate_drop_count(
    *, sl: Any, slice_index: int, seg_header_sigs: list[Any], seg_hrc: int
) -> int:
    """Determine how many rows to drop from a slice based on header repetition.

    Parameters
    ----------
    sl
        The TableSlice-like object.
    slice_index
        The index of the slice within the segment.
    seg_header_sigs
        The segment's header row signatures.
    seg_hrc
        The segment's header_row_count.

    Returns
    -------
    int
        The number of rows to drop from the slice.
    """

    # First slice (index 0) never drops rows
    if slice_index == 0:
        return 0

    # repeats_header=True --> drop known header count.
    if sl.repeats_header is True:
        return int(getattr(sl, "header_row_count", 0) or 0)

    # repeats_header=False --> drop nothing.
    if sl.repeats_header is False:
        return 0

    # repeats_header=None --> infer conservatively via header match.
    sl_hrc = int(getattr(sl, "header_row_count", 0) or 0)
    k = min(
        seg_hrc,
        sl_hrc,
        len(getattr(sl, "rows", []) or []),
        len(seg_header_sigs),
    )

    if k > 0:
        sl_first_sigs = [_row_sig(r) for r in (getattr(sl, "rows", []) or [])[:k]]
        if sl_first_sigs == seg_header_sigs[:k]:
            return sl_hrc

    return 0


def _cell_at(nr: Any, col: int | None) -> TextUnit | None:
    """Safely extract a TextUnit from a normalized row at a specific column.

    Parameters
    ----------
    nr
        The normalized row object.

    Returns
    -------
    TextUnit | None
        The TextUnit at the specified column, or None if out of bounds.
    """

    if col is None:
        return None

    cells = getattr(nr, "cells", []) or []
    if col < 0 or col >= len(cells):
        return None

    return _as_textunit(default_language="und", value=cells[col])


def _coerce_text_to_str(value: Any) -> str:
    """Coerce TableCell.text (TextUnit | dict | str | None) into a plain string.

    Parameters
    ----------
    value
        The value to coerce.

    Returns
    -------
    str
        The coerced string.
    """

    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        return value.get("text") or value.get("text_en") or ""

    # Pydantic model (e.g., TextUnit).
    if hasattr(value, "text") or hasattr(value, "text_en"):
        return getattr(value, "text", None) or getattr(value, "text_en", None) or ""

    return str(value)


def _coerce_text_to_textunit_like(value: Any) -> Any:
    """Return a TextUnit-like object (TextUnit instance or dict), or None if empty.

    NB: We avoid importing/constructing TextUnit directly here; Pydantic will validate
    dicts into TextUnit when NormalizedRow is created.

    Parameters
    ----------
    value
        The value to coerce.

    Returns
    -------
    Any
        The coerced TextUnit-like object or None.
    """

    if value is None:
        return None

    if isinstance(value, dict):
        # Ensure minimal shape; keep any extra fields.
        language = value.get("language") or "und"
        text = value.get("text")
        text_en = value.get("text_en")
        return {**value, "language": language, "text": text, "text_en": text_en}

    if isinstance(value, str):
        return {"language": "und", "text": value, "text_en": None}

    # If it's a Pydantic-ish object with text fields, convert to a dict so Canonical
    # TextUnit validation is consistent (avoid cross-module BaseModel instances).
    if hasattr(value, "model_dump"):
        d = value.model_dump(mode="python")
        language = d.get("language") or "und"
        text = d.get("text")
        text_en = d.get("text_en")
        return {**d, "language": language, "text": text, "text_en": text_en}

    if hasattr(value, "text") or hasattr(value, "text_en"):
        return {
            "language": getattr(value, "language", None) or "und",
            "text": getattr(value, "text", None),
            "text_en": getattr(value, "text_en", None),
        }

    # Fallback: stringify into text.
    return {"language": "und", "text": str(value), "text_en": None}


def _construct_normalized_rows(
    *,
    grid: list[list[dict[str, Any]]],
    key: str,
    n_cols: int,
    n_rows: int,
    row_prov: list[dict[str, Any]],
) -> list[NormalizedRow]:
    """Transform the grid into NormalizedRow objects with provenance.

    Parameters
    ----------
    grid
        The populated grid.
    key
        The TableSegment key for error reporting.
    n_cols
        The number of columns in the table.
    n_rows
        The number of rows in the table.
    row_prov
        The per-row provenance information.

    Returns
    -------
    list[NormalizedRow]
        The list of normalized rows.

    Raises
    ------
    ValueError
        If provenance information is missing.
    """

    output: list[NormalizedRow] = []

    for r_idx in range(n_rows):
        prov = row_prov[r_idx]
        bbox = prov.get("row_bbox") or prov.get("bbox")
        page_index = prov.get("page_index")
        slice_index = prov.get("slice_index")
        slice_row_index = prov.get("slice_row_index")

        if bbox is None:
            raise ValueError(
                f"Missing provenance_bbox for normalized row {r_idx} "
                f"in TableSegment '{key}'."
            )

        if page_index is None or slice_index is None:
            raise ValueError(
                f"Missing provenance page/slice for normalized row {r_idx} "
                f"in TableSegment '{key}'."
            )

        cells: list[Any] = []
        for c in range(n_cols):
            v = grid[r_idx][c]["text"]
            if _coerce_text_to_str(v).strip() == "":
                cells.append(None)
            else:
                cells.append(_coerce_text_to_textunit_like(v))

        output.append(
            NormalizedRow(
                cells=cells,
                original_row_index=(
                    int(slice_row_index) if slice_row_index is not None else r_idx
                ),
                provenance_bbox=bbox,
                provenance_page_index=page_index,
                provenance_slice_index=slice_index,
                row_index=r_idx,
            )
        )
    return output


def _create_leaf_statement(
    *,
    bullet_re: re.Pattern | None,
    cur_id: str | None,
    cur_lines: list[str],
    drop_empty: bool,
) -> LeafStatement | None:
    """Create a LeafStatement from buffered lines, cleaning bullets if needed.

    Parameters
    ----------
    bullet_re
        The compiled regex pattern to strip bullet markers, if any.
    cur_id
        The list ID associated with the current statement, if any.
    cur_lines
        The list of buffered lines for the current statement.
    drop_empty
        Whether to drop empty statements.

    Returns
    -------
    LeafStatement | None
        The created LeafStatement or None if dropped.
    """

    if not cur_lines:
        return None

    body = _normalize_space("\n".join(cur_lines))

    # Strip a single leading bullet marker if present.
    if bullet_re:
        body = bullet_re.sub("", body, count=1).strip()

    if drop_empty and not body:
        return None

    return LeafStatement(body=body, list_id=cur_id)


def _extract_table_header_texts(segment: Any) -> list[str]:
    """Extract header strings for each column from a table segment. Prioritizes
    `header_rows` if available; otherwise falls back to slicing the first
    `header_row_count` rows from the table body.

    Parameters
    ----------
    segment
        The table segment object (DocumentIR schema).

    Returns
    -------
    list[str]
        A list of header strings, one per column.
    """

    header_rows = getattr(segment, "header_rows", None)
    if not header_rows:
        hrc = int(getattr(segment, "header_row_count", 0) or 0)
        rows = getattr(segment, "rows", []) or []
        header_rows = rows[:hrc] if hrc > 0 else []

    n_cols = int(getattr(segment, "n_cols", 0) or 0)

    # If upstream IR didn't set n_cols, infer it from header rows using col_spans.
    if n_cols <= 0:
        inferred = 0
        for r in header_rows or []:
            cells = getattr(r, "cells", None) or []
            c_idx = 0
            for cell in cells:
                cs = int(getattr(cell, "col_span", 1) or 1)
                c_idx += max(1, cs)
            inferred = max(inferred, c_idx)
        n_cols = inferred

    cols: list[list[str]] = [[] for _ in range(max(0, n_cols))]

    for r in header_rows or []:
        cells = getattr(r, "cells", None) or []
        c_idx = 0
        for cell in cells:
            t = _normalize_space(_coerce_text_to_str(getattr(cell, "text", None)))
            cs = int(getattr(cell, "col_span", 1) or 1)
            if t:
                for j in range(cs):
                    if (c_idx + j) < len(cols):
                        cols[c_idx + j].append(t)
            c_idx += cs

    return [" | ".join(parts).strip() for parts in cols]


def _fill_span_area(
    *,
    c_span: int,
    c_start: int,
    grid: list[list[dict[str, Any]]],
    key: str,
    r_span: int,
    r_start: int,
    value: Any,
) -> None:
    """Fill a specific rectangular area of the grid.

    Parameters
    ----------
    c_span
        The column span.
    c_start
        The starting column index.
    grid
        The grid to populate.
    key
        The TableSegment key for error reporting.
    r_span
        The row span.
    r_start
        The starting row index.
    value
        The TextUnit-like value to fill in the spanned area.

    Raises
    ------
    ValueError
        If overlapping spans are detected.
    """

    for rr in range(r_start, r_start + r_span):
        for cc in range(c_start, c_start + c_span):
            if grid[rr][cc]["source_row"] != -1:
                raise ValueError(
                    f"Overlapping spans detected at (row={rr}, col={cc}) "
                    f"in TableSegment '{key}'."
                )
            grid[rr][cc] = {"text": value, "source_row": r_start}


def _forward_fill_columns(
    *,
    cols_to_fill: list[int],
    grid: list[list[dict[str, Any]]],
    key: str,
    n_cols: int,
    n_rows: int,
    start_row: int,
) -> None:
    """Apply forward-fill logic to specific columns.

    Parameters
    ----------
    cols_to_fill
        The list of column indices to forward-fill.
    grid
        The grid to populate.
    key
        The TableSegment key for error reporting.
    n_cols
        The number of columns in the table.
    n_rows
        The number of rows in the table.
    start_row
        The row index to start forward-filling from.

    Raises
    ------
    ValueError
        If cols_to_fill contains invalid column indices.
    """

    for col in cols_to_fill:
        if col < 0 or col >= n_cols:
            raise ValueError(
                f"forward_fill_cols contains invalid col={col} for n_cols={n_cols} "
                f"in TableSegment '{key}'."
            )

        last_value: Any = None
        last_source_row: int = -1

        for r in range(start_row, n_rows):
            cell_data = grid[r][col]
            cur_text = _coerce_text_to_str(cell_data["text"]).strip()

            if cell_data["text"] is None or cur_text == "":
                if last_value is not None:
                    grid[r][col] = {"text": last_value, "source_row": last_source_row}

            else:
                last_value = cell_data["text"]
                last_source_row = cell_data["source_row"]


def _norm(s: str) -> str:
    """Normalize a string for robust comparison.

    Parameters
    ----------
    s
        The string to normalize.

    Returns
    -------
    str
        The normalized string.
    """

    return " ".join((s or "").split()).strip().lower()


def _normalize_space(s: str) -> str:
    """Collapse internal whitespace and strip padding.

    Parameters
    ----------
    s
        The input string.

    Returns
    -------
    str
        The normalized string.
    """

    return re.sub(r"\s+", " ", (s or "")).strip()


def _normalize_space_keep_newlines(s: str) -> str:
    """Normalize whitespace within lines but preserve newline structure. This is
    critical for leaf splitting, which depends on splitlines() to detect
    bullets/blank lines as statement boundaries.

    Parameters
    ----------
    s
        The input string.

    Returns
    -------
    str
        The normalized string with preserved newlines.
    """

    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []

    for ln in s.split("\n"):
        # Preserve blank lines exactly (so split_on_blank_lines can work).
        lines.append(_normalize_space(ln) if ln.strip() else "")

    return "\n".join(lines).strip()


def _pick_heading_role_and_level(
    *, cfg: ParserConfig, text: str
) -> tuple[StatementRole, int, bool, bool]:
    """Determine the role and hierarchy level for a heading string. Iterates through
    `cfg.heading_rules`. If no rule matches, defaults to StatementRole.UNRESOLVED with
    a high probability of uniqueness enforcement.

    Parameters
    ----------
    cfg
        The parser configuration containing rules.
    text
        The heading text.

    Returns
    -------
    tuple[StatementRole, int, bool, bool]
        (Role, Level, UniquePerOccurrence, MatchedRule)
    """

    for rule in cfg.heading_rules:
        if rule.matches(text):
            role = rule.role
            level = (
                rule.level if rule.level is not None else cfg.role_levels.get(role, 50)
            )
            return role, level, rule.unique_per_occurrence, True

    # Fallback: do not invent semantics; mark as UNRESOLVED. Keep generic SECTION level
    # so the stack still behaves like a heading boundary.
    role = StatementRole.UNRESOLVED
    level = cfg.role_levels.get(StatementRole.SECTION, 50)
    return role, level, False, False


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

    for r_idx, row in enumerate(segment.rows):
        cursor = 0

        for cell in row.cells:
            # Calculate spans safely.
            r_span = max(1, int(getattr(cell, "row_span", 1) or 1))
            c_span = max(1, int(getattr(cell, "col_span", 1) or 1))
            raw = getattr(cell, "text", None)

            # Treat truly-empty cells as None, otherwise preserve full TextUnit-like
            # payload.
            value = (
                None
                if _coerce_text_to_str(raw).strip() == ""
                else _coerce_text_to_textunit_like(raw)
            )

            # Padding cell = extractor emitted an explicit blank cell in a column that
            # may already be occupied by a row-span from a previous row.
            is_padding = value is None and r_span == 1 and c_span == 1

            # If this is padding and the current slot is already occupied, consume
            # exactly one column and move on. This prevents right-shifting that can
            # overflow n_cols.
            if is_padding:
                if cursor < n_cols and grid[r_idx][cursor]["source_row"] != -1:
                    cursor += 1
                    continue

                # If we're already past the edge due to earlier padding, ignore
                # trailing padding.
                if cursor >= n_cols:
                    continue

            # Advance cursor to next empty slot (normal behavior for real cells).
            while cursor < n_cols and grid[r_idx][cursor]["source_row"] != -1:
                cursor += 1

            # Sanity check: ensure span fits.
            if cursor >= n_cols:
                # If the only thing left is padding, ignore it; otherwise this is a
                # real error.
                if is_padding:
                    continue

                raise ValueError(
                    f"Row {r_idx} exceeds declared n_cols={n_cols} "
                    f"in TableSegment '{segment.segment_key}'."
                )

            # Validate spans.
            _validate_span_bounds(
                c_span=c_span,
                cursor=cursor,
                n_cols=n_cols,
                n_rows=n_rows,
                r_idx=r_idx,
                r_span=r_span,
                segment_key=segment.segment_key,
            )

            # Fill the spanned area.
            _fill_span_area(
                c_span=c_span,
                c_start=cursor,
                grid=grid,
                key=segment.segment_key,
                r_span=r_span,
                r_start=r_idx,
                value=value,
            )

            cursor += c_span


def _row_provenance_by_stitched_index(*, segment: TableSegment) -> list[dict[str, Any]]:
    """Return a list of length len(segment.rows) where each entry contains the
    provenance info (at least bbox + page_index + slice_index) for the stitched row.
    This is computed deterministically from `segment.slices` using the same
    header-dropping logic as stitching.

    NB:

    1. repeats_header=True --> drop header_row_count rows on that slice
    2. repeats_header=False --> drop nothing
    3. repeats_header=None --> *infer* repeated header by matching the slice's first
        header rows against the segment's header rows (conservative).

    Parameters
    ----------
    segment
        The stitched TableSegment.

    Returns
    -------
    list[dict[str, Any]]
        List of provenance info per stitched row.

    Raises
    ------
    ValueError
        If the segment is invalid or mapping length mismatches.
    """

    if not segment.slices:
        raise ValueError(
            f"TableSegment '{segment.segment_key}' has no slices; cannot derive row "
            f"provenance."
        )

    seg_hrc = int(getattr(segment, "header_row_count", 0) or 0)
    seg_header_rows = (getattr(segment, "header_rows", None) or []) or (
        (getattr(segment, "rows", None) or [])[:seg_hrc] if seg_hrc > 0 else []
    )
    seg_header_sigs = [_row_sig(r) for r in (seg_header_rows or [])[:seg_hrc]]

    mapping: list[dict[str, Any]] = []

    for slice_index, sl in enumerate(segment.slices):
        bbox = sl.bbox
        if bbox is None or len(bbox) != 4:
            raise ValueError(
                f"Missing/invalid bbox for TableSlice (segment_key={segment.segment_key}, "
                f"slice_index={slice_index}, page_index={sl.page_index}): {bbox}"
            )

        drop = _calculate_drop_count(
            sl=sl,
            slice_index=slice_index,
            seg_header_sigs=seg_header_sigs,
            seg_hrc=seg_hrc,
        )

        # Never drop beyond available rows.
        drop = min(drop, len(getattr(sl, "rows", []) or []))

        effective_rows = sl.rows[drop:]

        # Approximate per-row bbox from the slice bbox by evenly splitting the slice
        # bbox vertically across the slice's visual rows. This is deterministic and
        # makes wizard/debug output much more actionable than a coarse slice bbox.
        total_rows_in_slice = len(getattr(sl, "rows", []) or [])
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
                {
                    "bbox": bbox,
                    "row_bbox": row_bbox,
                    "page_index": sl.page_index,
                    "slice_index": slice_index,
                    "slice_row_index": slice_row_index,
                    "slice_row_index_after_drop": i,
                    "slice_total_rows": total_rows_in_slice,
                    "dropped_header_rows": drop,
                }
            )

    if len(mapping) != len(segment.rows):
        raise ValueError(
            f"Row↔slice mapping length mismatch for TableSegment '{segment.segment_key}'. "
            f"Derived {len(mapping)} rows from slices, but segment.rows has {len(segment.rows)}."
        )

    return mapping


def _row_sig(row: Any) -> str:
    """Generate a stable signature for a row for header matching.

    Parameters
    ----------
    row
        The TableRow-like object.

    Returns
    -------
    str
        The row signature.
    """

    # Signature should be stable across reruns and resilient to spacing.
    parts: list[str] = []
    for cell in getattr(row, "cells", None) or []:
        txt = _norm(_coerce_text_to_str(getattr(cell, "text", None)))
        cs = int(getattr(cell, "col_span", 1) or 1)
        rs = int(getattr(cell, "row_span", 1) or 1)
        parts.append(f"{rs}x{cs}:{txt}")

    return "|".join(parts)


def _sanitize_path_part(s: str) -> str:
    """Make a path token safe for use in generate_global_id(). Prevent delimiter
    collisions caused by '/' in content.

    Parameters
    ----------
    s
        The input string.

    Returns
    -------
    str
        The sanitized string.
    """

    s = _normalize_space(s or "")

    # Remove control characters (optional but helps avoid invisible ID instability).
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)

    # Escape path delimiters so join('/') cannot create collisions.
    s = s.replace("/", "／").replace("\\", "／")  # Fullwidth slash

    # Avoid empty tokens.
    return s if s else "_"


def _segment_bbox_union(seg: Any) -> list[float] | None:
    """Compute the union bounding box of all slices in a segment.

    Parameters
    ----------
    seg
        The document segment.

    Returns
    -------
    list[float] | None
        [x0, y0, x1, y1] or None.
    """

    bbox: list[float] | None = None
    for sl in getattr(seg, "slices", []) or []:
        bbox = _union_bbox(bbox, getattr(sl, "bbox", None))
    return bbox


def _segment_page_indices(seg: Any) -> list[int]:
    """Extract distinct sorted page indices from a segment.

    Parameters
    ----------
    seg
        The document segment.

    Returns
    -------
    list[int]
        Sorted list of page numbers (0-indexed).
    """

    pages: set[int] = set()
    for sl in getattr(seg, "slices", []) or []:
        pi = getattr(sl, "page_index", None)
        if isinstance(pi, int):
            pages.add(pi)
    return sorted(pages)


def _split_leaf_statements(
    *, leaf_cfg: LeafParsingConfig, text: str
) -> list[LeafStatement]:
    """Split a text block into atomic statements based on config. Uses blank lines,
    code patterns, and bullet points as delimiters.

    Parameters
    ----------
    leaf_cfg
        The splitting rules.
    text
        The input text block.

    Returns
    -------
    list[LeafStatement]
        A list of extracted statements.
    """

    raw = (text or "").strip()
    if not raw:
        return []

    bullet_re = re.compile(leaf_cfg.bullet_regex) if leaf_cfg.bullet_regex else None
    code_re = re.compile(leaf_cfg.code_line_regex) if leaf_cfg.code_line_regex else None

    cur_id: str | None = None
    cur_lines: list[str] = []
    output: list[LeafStatement] = []

    def _flush() -> None:
        """Flush the current accumulated lines into an output LeafStatement."""

        nonlocal cur_lines, cur_id
        stmt = _create_leaf_statement(
            bullet_re=bullet_re,
            cur_id=cur_id,
            cur_lines=cur_lines,
            drop_empty=leaf_cfg.drop_empty,
        )
        if stmt:
            output.append(stmt)
        cur_lines = []
        cur_id = None

    for ln in raw.splitlines():
        if leaf_cfg.split_on_blank_lines and not ln.strip():
            _flush()
            continue

        stripped = ln.rstrip("\n")

        # Code line logic (starts a new item)
        if code_re:
            m = code_re.match(stripped.strip())
            if m:
                _flush()
                cur_id = (m.groupdict().get("list_id") or "").strip() or None
                body = (m.groupdict().get("body") or "").strip()
                cur_lines = [body] if body else []
                continue

        # Bullet logic (starts a new item, unless it's the very first line of a chunk)
        if leaf_cfg.split_on_bullets and bullet_re and bullet_re.match(stripped):
            if cur_lines:
                _flush()
            cur_lines.append(stripped)
            continue

        cur_lines.append(stripped)

    _flush()
    return output


def _stable_list_item_key(*, body: str, marker: str | None) -> str:
    """Generate a stable hash key for list items to handle duplicates.

    Parameters
    ----------
    body
        The body text of the list item.
    marker
        An optional marker (e.g., bullet) associated with the item.

    Returns
    -------
    str
        A stable 12-character hash string.
    """

    s = f"{marker or ''}|{body}".encode("utf-8")
    return hashlib.sha256(s).hexdigest()[:12]


def _stable_table_identity(
    *,
    caption_text: str,
    header_texts: list[str],
    local_code: str | None,
    segment_key: str | None,
) -> str:
    """Build a stable identifier for a table segment.

    Preference order:
      1. local_code (best, when present)
      2. header signature hash
      3. caption signature hash (last resort)
      4. 'unknown'

    Parameters
    ----------
    caption_text
        The text of the table caption, if any.
    header_texts
        The list of header strings for each column.
    local_code
        The local code of the table, if any.
    segment_key
        The segment key for logging context.

    Returns
    -------
    str
        A stable identifier string.
    """

    if local_code:
        lc = _normalize_space(local_code)

        # Avoid separators used elsewhere in deterministic seeds/paths.
        lc = lc.replace("|", "_").replace(">", "_")
        return f"lc:{lc}"

    header_norm = "|".join(
        _normalize_space(h).lower() for h in header_texts if _normalize_space(h)
    )
    if header_norm:
        h = hashlib.sha256(header_norm.encode("utf-8")).hexdigest()[:12]
        return f"hdr:{h}"

    cap_norm = _normalize_space(caption_text).lower()
    if cap_norm:
        h = hashlib.sha256(cap_norm.encode("utf-8")).hexdigest()[:12]
        return f"cap:{h}"

    # Deterministic fallback: never return 'unknown' because it can cause cross-table
    # ID collisions when multiple unlabeled tables exist in the same document.
    seg_norm = _normalize_space(segment_key or "").lower()
    h = hashlib.sha256(seg_norm.encode("utf-8")).hexdigest()[:12]
    return f"seg:{h}"


def _table_is_contentful(*, cfg: ParserConfig, seg: Any) -> bool:
    """Determine if an unmatched table is 'real' enough for wizard diagnostics.

    Parameters
    ----------
    cfg
        Configuration defining minimum thresholds.
    seg
        The table segment.

    Returns
    -------
    bool
        True if the table exceeds the content thresholds.
    """

    preview = _table_preview_rows(max_cols=12, max_rows=8, seg=seg)
    nonempty_cells = 0
    total_chars = 0
    for row in preview:
        for c in row:
            if c:
                s = c.strip()
                if s:
                    nonempty_cells += 1
                    total_chars += len(s)
    return (
        nonempty_cells >= cfg.unmatched_table_min_nonempty_cells
        and total_chars >= cfg.unmatched_table_min_total_chars
    )


def _table_preview_rows(
    *, max_cols: int = 12, max_rows: int = 6, seg: Any
) -> list[list[str | None]]:
    """Generate a JSON-safe preview of the table content for debugging.

    Parameters
    ----------
    max_cols
        Maximum columns to include.
    max_rows
        Maximum rows to include.
    seg
        The table segment.

    Returns
    -------
    list[list[str | None]]
        A 2D grid of cell strings.
    """

    try:
        norm_rows = normalize_table_grid(forward_fill_cols=[], segment=seg)
    except Exception:  # pylint: disable=broad-except
        # Worst-case fallback: raw stitched rows, best-effort.
        output: list[list[str | None]] = []
        for r in (getattr(seg, "rows", []) or [])[:max_rows]:
            row_out: list[str | None] = []
            for cell in (getattr(r, "cells", None) or [])[:max_cols]:
                t = _normalize_space(_coerce_text_to_str(getattr(cell, "text", None)))
                row_out.append(t or None)
            output.append(row_out)
        return output

    output = []
    for nr in norm_rows[:max_rows]:
        row_out = []
        for c in (getattr(nr, "cells", []) or [])[:max_cols]:
            t = _normalize_space(_coerce_text_to_str(c))
            row_out.append(t or None)
        output.append(row_out)
    return output


def _union_bbox(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    """Compute the union of two bounding boxes [x0, y0, x1, y1].

    Parameters
    ----------
    a
        The first bounding box.
    b
        The second bounding box.

    Returns
    -------
    list[float] | None
        The union bounding box, or None if both inputs are None.
    """

    if a is None:
        return b
    if b is None:
        return a
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _validate_span_bounds(
    *,
    c_span: int,
    cursor: int,
    n_cols: int,
    n_rows: int,
    r_idx: int,
    r_span: int,
    segment_key: str,
) -> None:
    """Validate that row and column spans fit within table limits.

    Parameters
    ----------
    c_span
        The column span.
    cursor
        The current column cursor.
    n_cols
        The number of columns in the table.
    n_rows
        The number of rows in the table.
    r_idx
        The current row index.
    r_span
        The row span.
    segment_key
        The TableSegment key for error reporting.

    Raises
    ------
    ValueError
        If spans exceed table bounds.
    """

    if r_idx + r_span > n_rows:
        raise ValueError(
            f"row_span out of bounds (row={r_idx}, row_span={r_span}, n_rows={n_rows}) "
            f"in TableSegment '{segment_key}'."
        )
    if cursor + c_span > n_cols:
        raise ValueError(
            f"col_span out of bounds (row={r_idx}, col={cursor}, col_span={c_span}, n_cols={n_cols}) "
            f"in TableSegment '{segment_key}'."
        )


def create_canonical_ir_dirs(*, output_dir: Path) -> CanonicalIRDirs:
    """Create canonical IR directories for a given creation run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    CanonicalDocumentIRDirs
        The created canonical document IR directories.
    """

    root = output_dir

    for p in [root]:
        make_dir(p)

    return CanonicalIRDirs(root=root)


def export_canonical_ir(*, canonical_ir: CanonicalIR, output_path: Path) -> None:
    """Export the canonical IR to a JSON file.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to serialize.
    output_path
        The file path to write the JSON output to.
    """

    data = canonical_ir.model_dump(mode="json")
    write_to_json(fp=output_path, json_info=data)
    logger.success(f"Canonical IR exported to: {output_path}")


def generate_global_id(
    *,
    code: Optional[str],
    doc_key: str,
    path: list[str],
    role: StatementRole,
    text: Optional[str],
) -> str:
    """Generate a deterministic UUIDv5 based on document identity and semantic content.

    Parameters
    ----------
    code
        The alphanumeric code (e.g., '3.1.1') associated with the node, if any.
    doc_key
        The unique document identifier.
    path
        The hierarchical path to the node within the document.
    role
        The semantic role of the node.
    text
        The full normative text of the node, if any.

    Returns
    -------
    str
        A deterministic UUIDv5 string.
    """

    code_str = code or "nocode"
    safe_path = [_sanitize_path_part(p) for p in path]
    path_str = "/".join(safe_path)
    text_hash = compute_sha256_hex(s=text or "")[:32]
    seed = f"{doc_key}|{role.value}|{path_str}|{code_str}|{text_hash}"

    return str(uuid.uuid5(Settings.PROJECT_NAMESPACE, seed))


def normalize_table_grid(
    *, forward_fill_cols: list[int], segment: TableSegment
) -> list[NormalizedRow]:
    """Explode row/col spans and forward-fill specified structural columns.

    The process is as follows:

    1. Expand row_span/col_span into an explicit n_rows × n_cols grid.
    2. Forward-fill structural columns (down the table).
    3. Attach REQUIRED provenance_bbox per normalized row (derived from the source
        slice bbox).

    Parameters
    ----------
    forward_fill_cols
        List of column indices to fill down (e.g., [0, 1] for Subject).
    segment
        The stitched TableSegment.

    Returns
    -------
    list[NormalizedRow]
        The normalized table rows.

    Raises
    ------
    ValueError
        If the table segment is invalid.
    """

    n_cols = segment.n_cols
    n_rows = len(segment.rows)

    if n_cols <= 0:
        raise ValueError(
            f"Invalid n_cols={segment.n_cols} for TableSegment '{segment.segment_key}'."
        )

    # Initialize empty grid: grid[row][col] = {"text": Optional[str], "source_row": int}
    grid: list[list[dict[str, Any]]] = [
        [{"text": None, "source_row": -1} for _ in range(n_cols)] for _ in range(n_rows)
    ]

    # 1.
    _populate_grid_spans(grid=grid, n_cols=n_cols, n_rows=n_rows, segment=segment)

    # 2.
    _forward_fill_columns(
        cols_to_fill=forward_fill_cols,
        grid=grid,
        key=segment.segment_key,
        n_cols=n_cols,
        n_rows=n_rows,
        start_row=max(0, int(segment.header_row_count or 0)),
    )

    # 3.
    row_prov = _row_provenance_by_stitched_index(segment=segment)
    return _construct_normalized_rows(
        grid=grid,
        key=segment.segment_key,
        n_rows=n_rows,
        n_cols=n_cols,
        row_prov=row_prov,
    )


def persist_creation_run(
    *, output_dir: Path, **kwargs: Any
) -> tuple[CanonicalIRDirs, RunCtx]:
    """Persist creation run metadata.

    Parameters
    ----------
    output_dir
        The output directory for the canonical IR JSON.
    kwargs
        Additional creation run configuration parameters.

    Returns
    -------
    tuple[CanonicalIRDirs, RunCtx]
        The created canonical IR directories and persisted creation run metadata.
    """

    extra = kwargs.get("extra", {})
    extra.pop("status", None)
    creation_dirs = create_canonical_ir_dirs(output_dir=output_dir)
    creation_run = RunCtx(
        extra=extra, run_id=str(uuid.uuid4()), started_at=datetime.now(timezone.utc)
    )
    write_to_json(fp=output_dir / "creation_run.json", json_info=creation_run)
    logger.info(f"Creation directory: {output_dir}")

    return creation_dirs, creation_run
