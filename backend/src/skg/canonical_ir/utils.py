"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import CanonicalIR, NormalizedRow
from skg.config import Settings
from skg.document_ir.schemas import TableSegment
from skg.schemas import RunCtx
from skg.utils.constants import StatementRole
from skg.utils.general import compute_sha256_hex, make_dir, write_to_json


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path


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
        bbox = row_prov[r_idx].get("bbox")
        page_index = row_prov[r_idx].get("page_index")
        slice_index = row_prov[r_idx].get("slice_index")

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
                original_row_index=r_idx,
                provenance_bbox=bbox,
                provenance_page_index=page_index,
                provenance_slice_index=slice_index,
                row_index=r_idx,
            )
        )
    return output


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
            # Advance cursor to next empty slot.
            while cursor < n_cols and grid[r_idx][cursor]["source_row"] != -1:
                cursor += 1

            if cursor >= n_cols:
                raise ValueError(
                    f"Row {r_idx} exceeds declared n_cols={n_cols} "
                    f"in TableSegment '{segment.segment_key}'."
                )

            # Calculate spans safely.
            rs = max(1, int(getattr(cell, "row_span", 1) or 1))
            cs = max(1, int(getattr(cell, "col_span", 1) or 1))

            if r_idx + rs > n_rows:
                raise ValueError(
                    f"row_span out of bounds (row={r_idx}, row_span={rs}, n_rows={n_rows}) "
                    f"in TableSegment '{segment.segment_key}'."
                )
            if cursor + cs > n_cols:
                raise ValueError(
                    f"col_span out of bounds (row={r_idx}, col={cursor}, col_span={cs}, n_cols={n_cols}) "
                    f"in TableSegment '{segment.segment_key}'."
                )

            raw = getattr(cell, "text", None) if hasattr(cell, "text") else None

            # Treat truly-empty cells as None, otherwise preserve full TextUnit-like
            # payload.
            value = (
                None
                if _coerce_text_to_str(raw).strip() == ""
                else _coerce_text_to_textunit_like(raw)
            )

            # Fill the spanned area.
            _fill_span_area(
                c_span=cs,
                c_start=cursor,
                grid=grid,
                key=segment.segment_key,
                r_span=rs,
                r_start=r_idx,
                value=value,
            )

            cursor += cs


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

        drop = 0
        if slice_index > 0:
            if sl.repeats_header is True:
                drop = int(getattr(sl, "header_row_count", 0) or 0)
            elif sl.repeats_header is False:
                drop = 0
            else:
                # repeats_header is None --> infer conservatively via header match.
                sl_hrc = int(getattr(sl, "header_row_count", 0) or 0)
                k = min(
                    seg_hrc,
                    sl_hrc,
                    len(getattr(sl, "rows", []) or []),
                    len(seg_header_sigs),
                )
                if k > 0:
                    sl_first_sigs = [
                        _row_sig(r) for r in (getattr(sl, "rows", []) or [])[:k]
                    ]
                    if sl_first_sigs == seg_header_sigs[:k]:
                        drop = sl_hrc

        # Never drop beyond available rows.
        drop = min(drop, len(getattr(sl, "rows", []) or []))

        effective_rows = sl.rows[drop:]
        mapping.extend(
            [
                {
                    "bbox": bbox,
                    "page_index": sl.page_index,
                    "slice_index": slice_index,
                }
                for _ in effective_rows
            ]
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

    # Create a stable seed string.
    code_str = code or "nocode"
    path_str = "/".join(path)
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
