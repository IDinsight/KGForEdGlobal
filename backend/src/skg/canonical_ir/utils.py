"""This module contains utility functions for canonical Intermediate Representations."""

# Standard Library
import re
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.llm import generate_segment_decision
from skg.canonical_ir.schemas import (
    CanonicalIR,
    SegmentDecision,
    SegmentDecisionSet,
    compute_decision_set_id,
)
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.page_ir_extraction.schemas import TextUnit
from skg.schemas import CreateCanonicalConfig, RunCtx
from skg.utils.constants import (
    BlockType,
    CaptionFigurePrefixes,
    CaptionKind,
    CaptionTablePrefixes,
)
from skg.utils.general import make_dir, open_json_type, write_to_json


@dataclass(frozen=True)
class CanonicalIRDirs:
    """Dataclass for canonical IR directories."""

    root: Path


@dataclass(frozen=True)
class CaptionBinding:
    """Dataclass for caption-to-table bindings."""

    caption_kind: CaptionKind
    caption_page_index: int | None
    caption_segment_id: str
    caption_text: str
    gap_segments: int
    table_page_index: int | None
    table_segment_id: str


def _classify_caption_kind(text: str) -> CaptionKind:
    """Classify caption kind based on text prefixes.

    Parameters
    ----------
    text
        The caption text to classify.

    Returns
    -------
    CaptionKind
        The classified caption kind.
    """

    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)

    for p in CaptionTablePrefixes:
        if t.startswith(p):
            return "table"

    for p in CaptionFigurePrefixes:
        if t.startswith(p):
            return "figure"

    # Regex fallback for common patterns.
    if re.match(r"^(table|tab\.?|tbl\.?|jedwali|tableau)\s*\d+", t):
        return "table"

    if re.match(r"^(figure|fig\.?)\s*\d+", t):
        return "figure"

    return "unknown"


def _extract_block_segment_text(segment: BlockSegment) -> str | None:
    """Extract text from a BlockSegment.

    Parameters
    ----------
    segment
        The BlockSegment to extract text from.

    Returns
    -------
    str | None
        The extracted text, or None if not found.
    """

    if segment.combined_text and segment.combined_text.strip():
        return segment.combined_text.strip()

    if isinstance(segment.text, TextUnit) and segment.text.text.strip():
        return segment.text.text.strip()

    if segment.list_items:
        parts: list[str] = []

        for list_item in segment.list_items:
            text_unit = list_item.text

            if text_unit.text.strip():
                parts.append(text_unit.text.strip())

        if parts:
            return "\n".join(parts)

    return None


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


def apply_caption_binding_to_table_payload(
    *, caption_bindings: CaptionBinding | None, table_payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply caption binding information to a table segment payload.

    Parameters
    ----------
    caption_bindings
        The CaptionBinding to apply, or None to skip.
    table_payload
        The table segment payload to update.

    Returns
    -------
    dict[str, Any]
        The updated table segment payload.
    """

    if not caption_bindings:
        return table_payload

    table_payload["caption_gap_segments"] = caption_bindings.gap_segments
    table_payload["caption_kind"] = caption_bindings.caption_kind
    table_payload["caption_page_index"] = caption_bindings.caption_page_index
    table_payload["caption_segment_id"] = caption_bindings.caption_segment_id
    table_payload["caption_text"] = caption_bindings.caption_text

    return table_payload


def attach_caption_binding_to_segment_decision(
    *, caption_binding: CaptionBinding | None, segment_decision: SegmentDecision
) -> SegmentDecision:
    """Persist caption binding provenance on the SegmentDecision.

    Parameters
    ----------
    caption_binding
        The CaptionBinding to apply, or None to skip.
    segment_decision
        The SegmentDecision to update.

    Returns
    -------
    SegmentDecision
        The updated SegmentDecision.
    """

    if not caption_binding:
        return segment_decision

    segment_decision.caption_gap_segments = caption_binding.gap_segments
    segment_decision.caption_kind = caption_binding.caption_kind
    segment_decision.caption_page_index = caption_binding.caption_page_index
    segment_decision.caption_segment_id = caption_binding.caption_segment_id
    segment_decision.caption_text = caption_binding.caption_text

    return segment_decision


def build_caption_bindings(
    *,
    bind_unknown_caption: bool = True,
    document_ir: DocumentIR,
    max_gap_segments: int = 2,
    max_page_distance: int = 1,
    warnings: list[str],
) -> dict[str, CaptionBinding]:
    """Bind Caption block to next Table segment (within limits).

    Parameters
    ----------
    bind_unknown_caption
        Whether to bind captions of unknown kind.
    document_ir
        The DocumentIR to process.
    max_gap_segments
        The maximum number of non-table segments allowed between caption and table.
    max_page_distance
        The maximum page distance allowed between caption and table.
    warnings
        A list to append warning messages to.

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings: dict[str, CaptionBinding] = {}

    # (caption_segment, caption_text, caption_kind, caption_page, caption_index)
    pending_caption: tuple[BlockSegment, str, CaptionKind, int, int] | None = None

    for index, segment in enumerate(document_ir.segments):
        page_index = (
            segment.slices[0].page_index
            if segment.slices
            else segment.segment_provenance[0].page_index
        )
        assert isinstance(page_index, int) and page_index >= 0

        # Explicit caption candidate.
        if segment.kind == "block":
            caption_text = _extract_block_segment_text(segment)

            if segment.block_type == BlockType.CAPTION and caption_text:
                kind = _classify_caption_kind(caption_text)

                # Don't bind figure captions to tables.
                if kind == "figure" or (kind == "unknown" and not bind_unknown_caption):
                    continue

                pending_caption = (
                    segment,
                    caption_text,
                    kind,
                    page_index,
                    index,
                )

                continue

        # Bind to next table if eligible.
        if segment.kind == "table" and pending_caption is not None:
            cap_seg, cap_text, cap_kind, cap_page, cap_index = pending_caption
            gap = max(0, index - cap_index - 1)
            page_dist = abs(page_index - cap_page)

            if gap <= max_gap_segments and page_dist <= max_page_distance:
                caption_bindings[segment.segment_id] = CaptionBinding(
                    caption_kind=cap_kind,
                    caption_page_index=cap_page,
                    caption_segment_id=cap_seg.segment_id,
                    caption_text=cap_text,
                    gap_segments=gap,
                    table_page_index=page_index,
                    table_segment_id=segment.segment_id,
                )
            else:
                msg = (
                    f"Dangling caption dropped: "
                    f"caption={cap_seg.segment_id} gap={gap} page_dist={page_dist}"
                )
                logger.warning(msg)
                warnings.append(msg)
                # input(1)

            pending_caption = None

            continue

        # Expire pending caption if too far.
        if pending_caption is not None:
            cap_seg, _cap_text, _cap_kind, _cap_page, cap_index = pending_caption
            gap = max(0, index - cap_index - 1)
            if gap > max_gap_segments:
                msg = (
                    f"Dangling caption dropped: "
                    f"caption={cap_seg.segment_id} gap_exceeded={gap}"
                )
                logger.warning(msg)
                warnings.append(msg)
                pending_caption = None
                # input(1)

    if pending_caption is not None:
        cap_seg, *_ = pending_caption
        msg = f"Dangling caption dropped: caption={cap_seg.segment_id} end_of_document"
        logger.warning(msg)
        warnings.append(msg)
        # input(1)

    return caption_bindings


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

    # NB: Chunk payload should not include full-table derived views that can leak
    # information outside the chunk.
    for k in ("rows_grid", "rows_filldown", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    full_rows = seg.get("rows") or []
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


def make_table_full_payload(*, segment: TableSegment) -> dict[str, Any]:
    """Build a FULL (unchunked) table payload for the LLM.

    This mirrors `make_table_chunk_payload` but includes ALL rows. Critically, it:

    1. Removes derived full-table views (rows_grid/rows_filldown/...)
    2. Adds `abs_row_index` to every row so validators can enforce grounding
    3. Adds a lightweight `chunking` object indicating absolute indices

    Parameters
    ----------
    segment
        The TableSegment to process.

    Returns
    -------
    dict[str, Any]
        The full table payload.
    """

    seg = segment.model_dump(mode="json")

    # NB: Full payload should not include derived structures that can bloat the prompt
    # and leak cross-row info.
    for k in ("rows_grid", "rows_filldown", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    rows = seg.get("rows") or []

    # Add abs_row_index to every row (headers included).
    for abs_i, row in enumerate(rows):
        if isinstance(row, dict):
            row["abs_row_index"] = abs_i

    seg["rows"] = rows
    seg["chunking"] = {
        "row_range_start": 0,
        "row_range_end": len(rows),
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


def process_segment_decisions(
    *,
    caption_bindings: dict[str, CaptionBinding | None],
    config: CreateCanonicalConfig,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    segment: Segment,
    segment_decisions_fp: Path,
) -> SegmentDecisionSet:
    """Process a single segment to generate and persist decisions.

    Parameters
    ----------
    caption_bindings
        The caption bindings to apply to table segments.
    config
        The canonical IR creation run configuration.
    decision_set
        The current SegmentDecisionSet to update.
    doc_key
        The expected document key for all page IRs.
    existing_keys
        The set of existing decision keys to avoid duplicates.
    segment
        The Segment to process.
    segment_decisions_fp
        The output file path for the SegmentDecisionSet JSON.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    # Block segments are always 1 decision (unchunked).
    if segment.kind == "block":
        key: tuple[str, int | None, int | None] = (segment.segment_id, None, None)

        if key in existing_keys:
            return decision_set

        # NB: Never apply caption bindings to block segments.
        segment_payload = segment.model_dump(mode="json")

        segment_decision = generate_segment_decision(
            always_double_check_first_attempt=config.always_double_check_first_attempt,
            doc_key=doc_key,
            model=config.model,
            segment=segment,
            segment_payload=segment_payload,
        )

        decision_set.decisions.append(segment_decision)
        existing_keys.add(key)

        return save_segment_decision_set(
            decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
        )

    assert segment.kind == "table"

    # Caption bindings dict is keyed by TABLE segment_id, and many tables have NO
    # caption -> Use .get().
    binding: CaptionBinding | None = caption_bindings.get(segment.segment_id)

    # Table segments: chunk only if needed. If an unchunked table decision already
    # exists, do NOT mix chunked + unchunked.
    unchunked_key = (segment.segment_id, None, None)

    if unchunked_key in existing_keys:
        return decision_set

    # Determine table chunks.
    chunks = table_chunks_for_segment(
        max_body_rows=config.max_table_rows_per_decision, segment=segment
    )

    # Unchunked table == 1 decision.
    if len(chunks) == 1 and chunks[0] == (None, None):
        key = unchunked_key

        # Do NOT create an unchunked decision if ANY chunked decisions already exist
        # for this segment (else we would mix chunked + unchunked representations).
        existing_chunked_for_segment = any(
            sid == segment.segment_id and row_start is not None
            for (sid, row_start, _row_end) in existing_keys
        )
        if existing_chunked_for_segment:
            logger.warning(
                f"Skipping unchunked decision for table segment {segment.segment_id} "
                f"because chunked decisions already exist (avoid mixing chunked + unchunked)."
            )
            return decision_set

        if key not in existing_keys:
            # Apply caption binding and pass the payload even for UNCHUNKED tables so
            # the LLM sees caption_text/caption_kind etc.
            table_payload = make_table_full_payload(segment=segment)
            table_payload = apply_caption_binding_to_table_payload(
                caption_bindings=binding, table_payload=table_payload
            )
            segment_decision = generate_segment_decision(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                doc_key=doc_key,
                model=config.model,
                segment=segment,
                segment_payload=table_payload,
            )
            segment_decision = attach_caption_binding_to_segment_decision(
                caption_binding=binding, segment_decision=segment_decision
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(key)

            decision_set = save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )
    # Chunked table == N decisions.
    else:
        for start, end in chunks:
            key = (segment.segment_id, start, end)

            if start is None or end is None or key in existing_keys:
                continue

            table_payload = make_table_chunk_payload(
                end=end, segment=segment, start=start
            )

            # Use binding (may be None), do not index caption_bindings[].
            table_payload = apply_caption_binding_to_table_payload(
                caption_bindings=binding, table_payload=table_payload
            )

            segment_decision = generate_segment_decision(
                always_double_check_first_attempt=config.always_double_check_first_attempt,
                doc_key=doc_key,
                model=config.model,
                row_range_end=end,
                row_range_start=start,
                segment=segment,
                segment_payload=table_payload,
            )
            segment_decision = attach_caption_binding_to_segment_decision(
                caption_binding=binding, segment_decision=segment_decision
            )

            decision_set.decisions.append(segment_decision)
            existing_keys.add(key)

            decision_set = save_segment_decision_set(
                decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
            )

    return decision_set


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
