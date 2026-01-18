"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.schemas import (
    CanonicalIR,
    SegmentDecision,
    SegmentDecisionSet,
    compute_decision_set_id,
)
from skg.document_ir.schemas import DocumentIR, TableSegment
from skg.schemas import CreateCanonicalConfig, RunCtx
from skg.utils.general import make_dir, open_json_type, write_to_json


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path


def _load_segment_decision_set(
    *, expected_doc_key: str, pdf_name: str, segment_decisions_fp: Path
) -> SegmentDecisionSet:
    """Load SegmentDecisionSet JSON and normalize formats.

    Parameters
    ----------
    expected_doc_key
        The expected document key for the SegmentDecisionSet.
    pdf_name
        The expected PDF name for the SegmentDecisionSet.
    segment_decisions_fp
        The file path to the SegmentDecisionSet JSON.

    Returns
    -------
    SegmentDecisionSet
        The loaded SegmentDecisionSet.

    Raises
    ------
    ValueError
        If the SegmentDecisionSet.doc_key does not match expected_doc_key, or if the
        SegmentDecisionSet.pdf_name does not match pdf_name.
    """

    raw = open_json_type(segment_decisions_fp)

    # Allow "raw list" format.
    if isinstance(raw, list):
        decisions = [SegmentDecision.model_validate(d) for d in raw]
        raw = {
            "pdf_name": pdf_name,
            "doc_key": expected_doc_key,
            "decision_set_id": compute_decision_set_id(decisions=decisions),
            "decisions": decisions,
        }

    # Ensure decision_set_id exists for wrapper format.
    if isinstance(raw, dict):
        if raw.get("decisions") is None:
            raise ValueError(
                f"SegmentDecisionSet file missing `decisions` key: {segment_decisions_fp}"
            )

        decisions = [SegmentDecision.model_validate(d) for d in raw["decisions"]]
        raw["decisions"] = decisions

        if raw.get("decision_set_id") in (None, ""):
            raw["decision_set_id"] = compute_decision_set_id(decisions=decisions)

    decision_set = SegmentDecisionSet.model_validate(raw)

    if decision_set.doc_key != expected_doc_key:
        raise ValueError(
            f"SegmentDecisionSet.doc_key mismatch.\n"
            f"  Expected: {expected_doc_key}\n"
            f"  Got:      {decision_set.doc_key}\n"
            f"  File:     {segment_decisions_fp}"
        )

    if decision_set.pdf_name != pdf_name:
        raise ValueError(
            f"SegmentDecisionSet.pdf_name mismatch.\n"
            f"  DocumentIR: {pdf_name}\n"
            f"  Decisions:  {decision_set.pdf_name}"
        )

    return decision_set


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


def decision_key(
    segment_decision: SegmentDecision,
) -> tuple[str, int | None, int | None]:
    """Compute a unique key for a SegmentDecision based on segment_id and row range.

    Parameters
    ----------
    segment_decision
        The SegmentDecision to compute the key for.

    Returns
    -------
    tuple[str, int | None, int | None]
        The unique key as (segment_id, row_range_start, row_range_end).
    """

    return (
        segment_decision.segment_id or "",
        segment_decision.row_range_start,
        segment_decision.row_range_end,
    )


def load_or_initialize_segment_decision_set(
    *,
    creation_dirs: CanonicalIRDirs,
    doc_key: str,
    document_ir: DocumentIR,
    segment_decisions_fp: Path | None,
) -> tuple[SegmentDecisionSet, Path]:
    """Load decision set if present, else initialize an empty one.

    NB: We intentionally do NOT return a simple `existing_segment_ids` set since
    canonical IR creation may create multiple decisions per table segment when chunking
    is enabled. Callers should compute coverage based on (segment_id, row_range_start,
    row_range_end).

    Parameters
    ----------
    creation_dirs
        The canonical IR creation directories.
    doc_key
        The expected document key for the SegmentDecisionSet.
    document_ir
        The DocumentIR to reference for segment existence.
    segment_decisions_fp
        The file path to the SegmentDecisionSet JSON.

    Returns
    -------
    tuple[SegmentDecisionSet, Path]
        The loaded or initialized SegmentDecisionSet and the file path to the
        SegmentDecisionSet JSON.

    Raises
    ------
    ValueError
        If the SegmentDecisionSet refers to missing segment IDs.
    """

    segment_decisions_fp = (
        segment_decisions_fp or creation_dirs.root / "segment_decisions.json"
    )
    segment_decisions_fp = Path(segment_decisions_fp)

    decision_set = (
        _load_segment_decision_set(
            expected_doc_key=doc_key,
            pdf_name=document_ir.pdf_name,
            segment_decisions_fp=segment_decisions_fp,
        )
        if segment_decisions_fp.exists()
        else SegmentDecisionSet.model_validate(
            {
                "pdf_name": document_ir.pdf_name,
                "doc_key": doc_key,
                "decision_set_id": compute_decision_set_id(decisions=[]),
                "decisions": [],
            }
        )
    )

    # Ensure any existing decisions still refer to real segments.
    existing_segment_ids: set[str] = {d.segment_id for d in decision_set.decisions}
    assert all(seg_id for seg_id in existing_segment_ids), f"{existing_segment_ids = }"
    segments_by_id = {s.segment_id: s for s in document_ir.segments}
    missing = [sid for sid in existing_segment_ids if sid not in segments_by_id]

    if missing:
        raise ValueError(f"Decision set refers to missing segment_ids: {missing[:10]}")

    return decision_set, segment_decisions_fp


def make_table_chunk_payload(
    *, end: int, segment: TableSegment, start: int
) -> dict[str, Any]:
    """Build a table chunk payload for the LLM as follows:

    1. Keep table metadata + headers
    2. Replace `rows` with ONLY the rows in [start,end)
    3. Adds abs_row_index to each provided row
    4. Adds a `chunking` object so prompts can instruct absolute indexing

    Parameters
    ----------
    end
        The exclusive end row index for the chunk.
    segment
        The TableSegment to chunk.
    start
        The inclusive start row index for the chunk.

    Returns
    -------
    dict[str, Any]
        The table chunk payload.
    """

    seg = segment.model_dump(mode="json")
    full_rows = seg["rows"]
    chunk_rows: list[dict[str, Any]] = []

    for abs_i in range(start, end):
        row = dict(full_rows[abs_i])
        row["abs_row_index"] = abs_i
        chunk_rows.append(row)

    seg["rows"] = chunk_rows
    seg["chunking"] = {
        "row_range_start": start,
        "row_range_end": end,
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
    }

    return seg


def persist_canonical_run(
    *, config: CreateCanonicalConfig, output_dir: Path
) -> tuple[CanonicalIRDirs, RunCtx]:
    """Persist canonical IR creation run metadata.

    Parameters
    ----------
    config
        The canonical IR creation run configuration.
    output_dir
        The output directory for the canonical IR creation run results.

    Returns
    -------
    tuple[CanonicalIRDirs, RunCtx]
        The created canonical IR directories and persisted canonical IR creation run
        metadata.
    """

    creation_dirs = create_canonical_ir_dirs(output_dir=output_dir)
    creation_run = RunCtx(
        extra={},
        models=[config.model],
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "creation_run.json", json_info=creation_run)
    logger.info(f"Saving canonical IR creation results to: {creation_dirs}")

    return creation_dirs, creation_run


def save_canonical_ir(*, canonical_ir: CanonicalIR, canonical_ir_fp: Path) -> None:
    """Export the canonical IR to a JSON file.

    Parameters
    ----------
    canonical_ir
        The CanonicalIR to serialize.
    canonical_ir_fp
        The output file path for the CanonicalIR JSON.
    """

    write_to_json(fp=canonical_ir_fp, json_info=canonical_ir)


def save_segment_decision_set(
    *, decision_set: SegmentDecisionSet, segment_decisions_fp: Path
) -> SegmentDecisionSet:
    """Write a SegmentDecisionSet with an updated stable decision_set_id.

    Parameters
    ----------
    decision_set
        The SegmentDecisionSet to serialize.
    segment_decisions_fp
        The output file path for the SegmentDecisionSet JSON.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet with recomputed decision_set_id.
    """

    # Recompute stable ID every write and keep the in-memory object consistent.
    new_id = compute_decision_set_id(decisions=decision_set.decisions)
    decision_set.decision_set_id = new_id

    write_to_json(fp=segment_decisions_fp, json_info=decision_set)

    return decision_set


def table_chunks_for_segment(
    *, max_body_rows: int | None, segment: TableSegment
) -> list[tuple[int | None, int | None]]:
    """Compute table row chunks for a TableSegment based on max_body_rows.

    Parameters
    ----------
    max_body_rows
        The maximum number of body rows per chunk. If None or <= 0, no chunk splitting
        is performed.
    segment
        The TableSegment to compute chunks for.

    Returns
    -------
    list[tuple[int | None, int | None]]
        A list of (start, end) row index tuples for each chunk. If no chunk splitting
        is needed, returns [(None, None)].
    """

    if not max_body_rows or max_body_rows <= 0:
        return [(None, None)]

    header_n = segment.header_row_count or 0
    total_rows = len(segment.rows)
    body_rows = max(0, total_rows - header_n)

    if body_rows <= max_body_rows:
        return [(None, None)]

    chunks = []
    start = header_n  # Chunk only body rows (skip headers)

    while start < total_rows:
        end = min(total_rows, start + max_body_rows)
        chunks.append((start, end))
        start = end

    return chunks
