"""This module contains functionalities related to validating CanonicalIR information."""

# Standard Library
import re
import unicodedata

from typing import Any, Optional

# Package Library
from skg.canonical_ir.schemas import SegmentDecision
from skg.document_ir.schemas import Segment
from skg.page_ir_extraction.validators import QualityError
from skg.utils.constants import BlockType, NonArtifacts, SegmentDecisionType

_DASH_RE = re.compile(r"[‐-‒–—−]")  # common unicode dash characters


def _build_row_text_map(rows: list[dict[str, Any]]) -> dict[int, str]:
    """Parse payload rows to build a map of {abs_row_index: normalized_text_blob}.

    Parameters
    ----------
    rows
        The list of row dictionaries from the segment payload.

    Returns
    -------
    dict[int, str]
        A mapping from absolute row index to the concatenated, normalized text.
    """

    row_text_by_abs_index: dict[int, str] = {}

    for r in rows:
        abs_i = r.get("abs_row_index")

        if abs_i is None:
            continue

        # Extract text from all cells in the row.
        cells = r.get("cells") or []
        parts = [
            str(c.get("text", {}).get("text", "") or "")
            for c in cells
            if isinstance(c.get("text"), dict)
        ]

        row_text_by_abs_index[int(abs_i)] = _normalize_text(" \n ".join(parts))

    return row_text_by_abs_index


def _normalize_text(text: Optional[str]) -> str:
    """Normalize text for comparisons (grounding + substring checks).

    Parameters
    ----------
    text
        The input text to normalize.

    Returns
    -------
    str
        The normalized text.
    """

    if not text:
        return ""

    # Normalize unicode forms (compatibility normalization).
    text = unicodedata.normalize("NFKC", text)

    # Normalize dash variants to ASCII hyphen.
    text = _DASH_RE.sub("-", text)

    # Collapse whitespace and casefold.
    return re.sub(r"\s+", " ", text).strip().casefold()


def validate_context_groupings_required_for_emit(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
) -> None:
    """If this decision emits anything and *meaningful* context evidence exists, then
    context_groupings[] must be non-empty. "Meaningful context evidence" is
    intentionally conservative:
        - section_path contains at least 1 non-artifact heading, OR
        - caption_text is present (table payload)

    NB: We ignore common document furniture headings (Table of Contents, References,
        etc.) via `NonArtifacts`.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    payload = segment_payload or {}

    # Determine whether section_path is meaningful.
    section_path = payload.get("section_path") or []
    meaningful_heading_texts: list[str] = []

    for h in section_path:
        t = ""

        if isinstance(h, dict):
            t = h.get("text", "") or ""
        elif isinstance(h, str):
            t = h  # Defensive: some payloads may serialize as strings

        tn = _normalize_text(t)

        if not tn or tn in NonArtifacts:
            continue

        meaningful_heading_texts.append(t)

    has_meaningful_section_path = bool(meaningful_heading_texts)
    has_caption = bool((payload.get("caption_text") or "").strip())

    if (
        has_meaningful_section_path or has_caption
    ) and not segment_decision.context_groupings:
        raise QualityError(
            f"Emitting decision must include non-empty context_groupings[] when "
            f"meaningful section_path or caption_text evidence exists.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  has_meaningful_section_path: {has_meaningful_section_path}\n"
            f"  has_caption_text: {has_caption}\n"
            f"  section_path_headings: {meaningful_heading_texts}"
        )


def validate_context_groupings_supported_by_outer_evidence(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
) -> None:
    """`context_groupings[]` must be supported by OUTER evidence:
        - section_path texts
        - caption_text
        - header_rows_canonical

    This discourages the model from promoting row-local values into outer context.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if (
        segment_decision.decision_type
        in (
            SegmentDecisionType.IGNORE,
            SegmentDecisionType.UNRESOLVED,
        )
        or not segment_decision.context_groupings
    ):
        return

    payload = segment_payload or {}
    section_path = payload.get("section_path") or []
    headings: list[str] = []

    for h in section_path:
        if isinstance(h, dict):
            t = h.get("text", "") or ""
        elif isinstance(h, str):
            t = h
        else:
            continue

        tn = _normalize_text(t)

        if not tn or tn in NonArtifacts:
            continue

        headings.append(t)

    caption = payload.get("caption_text") or ""
    header_rows = payload.get("header_rows_canonical") or []
    header_strings = []

    for r in header_rows:
        for c in r:
            if isinstance(c, str) and c.strip():
                header_strings.append(c)

    evidence_blob = _normalize_text(" \n ".join([*headings, caption, *header_strings]))

    # If there is NO outer evidence at all, we can't enforce this strictly.
    if not evidence_blob.strip():
        return

    for g in segment_decision.context_groupings:
        title = _normalize_text(g.title)
        if not title:
            raise QualityError(
                f"context_groupings contains an empty title.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}"
            )
        if title not in evidence_blob:
            raise QualityError(
                f"context_groupings title not supported by OUTER evidence "
                f"(section_path/caption/headers). It may be a row-local value.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  unsupported_title: {g.title}\n"
                f"  section_path_headings: {headings}\n"
                f"  has_caption_text: {bool((caption or '').strip())}"
            )


def validate_heading_segments_emit_groupings(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure HEADING blocks don't become no-op context only. If the stitching step
    classified a segment as a HEADING block and the LLM decision emits anything (i.e.,
    is not IGNORE/UNRESOLVED), then we require it to emit at least one grouping node in
    `groupings[]`. Without this, the model can put all structure into
    `context_groupings[]` and skip emitting the actual heading node, which causes
    hierarchy/order drift. The decision can still be IGNORE/UNRESOLVED (e.g.,
    false-positive running headers).
    """

    if segment.kind != "block":
        return

    # Only enforce for true heading blocks from DocumentIR.
    if getattr(segment, "block_type", None) != BlockType.HEADING:
        return

    if segment_decision.decision_type in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ):
        return

    if not segment_decision.groupings:
        raise QualityError(
            f"Heading segment emitted a decision but did not emit any grouping nodes.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  decision_type: {segment_decision.decision_type.value}"
        )


def validate_row_groupings_supported_by_row_cells(
    *,
    segment: Segment,
    segment_decision: SegmentDecision,
    segment_payload: dict[str, Any] | None,
) -> None:
    """Row-local groupings must be grounded in the row's visible cell text. This
    prevents hallucinated Topic/Subtopic/Code values. Only enforced for TABLE segments
    where segment_payload includes rows.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.
    segment_payload
        The payload dictionary for the Segment being decided on.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if (
        segment.kind != "table"
        or (
            segment_decision.decision_type
            in (
                SegmentDecisionType.IGNORE,
                SegmentDecisionType.UNRESOLVED,
            )
        )
        or not segment_decision.rows
    ):
        return

    payload = segment_payload or {}
    row_text_map = _build_row_text_map(payload.get("rows") or [])

    # Validate each row decision grouping title appears in that row text.
    for rd in segment_decision.rows:
        if not rd.groupings:
            continue

        row_blob = row_text_map.get(rd.row_index)

        # If we can't find the row blob (e.g., unchunked payload without
        # abs_row_index), skip strict enforcement.
        if not row_blob:
            continue

        for g in rd.groupings:
            title = _normalize_text(g.title)

            if not title:
                raise QualityError(
                    f"RowDecision.groupings contains an empty title.\n"
                    f"  segment_id: {segment.segment_id}\n"
                    f"  decision_id: {segment_decision.decision_id}\n"
                    f"  row_index: {rd.row_index}"
                )
            if title not in row_blob:
                raise QualityError(
                    f"RowDecision grouping title not supported by visible row cell text.\n"
                    f"  segment_id: {segment.segment_id}\n"
                    f"  decision_id: {segment_decision.decision_id}\n"
                    f"  row_index: {rd.row_index}\n"
                    f"  unsupported_title: {g.title}"
                )


def validate_segment_kind_coherence(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure the decision structure matches the actual segment kind.

    1. Block segments must not include rows[]
    2. Table segments must not include block_type
    3. Block segments must have block_type equal to DocumentIR's segment.block_type
      (block_type is set deterministically by the pipeline)

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind == "block":
        if segment_decision.rows:
            raise QualityError(
                f"Block segment decision must not include rows[].\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}"
            )

        if segment_decision.block_type != segment.block_type:
            raise QualityError(
                f"Block segment decision has mismatched block_type.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  expected: {segment.block_type}\n"
                f"  got: {segment_decision.block_type}"
            )

    if segment.kind == "table":
        if segment_decision.block_type is not None:
            raise QualityError(
                f"Table segment decision must not include block_type.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  block_type: {segment_decision.block_type}"
            )


def validate_table_row_index(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Validate that RowDecision.row_index values are within range and unique. If this
    SegmentDecision represents a chunked slice of a table, also enforce that all
    RowDecision.row_index values lie within [row_range_start, row_range_end), where
    row_range_end is EXCLUSIVE.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    start = segment_decision.row_range_start
    end = segment_decision.row_range_end

    # If chunking is declared, it must be well-formed.
    if (start is None) != (end is None):
        raise QualityError(
            f"Chunked table decision must include both row_range_start and row_range_end (exclusive).\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  row_range_start: {start}\n"
            f"  row_range_end: {end}"
        )

    is_chunked = start is not None
    table_rows = segment.rows

    if is_chunked:
        if start < 0 or end < 0 or start >= end:
            raise QualityError(
                f"Invalid chunk boundaries for table decision. Expected start < end and both >= 0.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_range: [{start}, {end})"
            )

        if end > len(table_rows):
            raise QualityError(
                f"Chunk boundary row_range_end exceeds table length.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_range: [{start}, {end})\n"
                f"  table_rows: {len(table_rows)}"
            )


def validate_table_header_rows_not_emitted(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Ensure RowDecision.row_index does not point into header rows. This prevents the
    model from interpreting table headers as real curriculum rows, especially for
    unchunked table decisions where chunk boundaries aren't present.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any RowDecision.row_index points into header rows.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    header_n = segment.header_row_count or 0

    if header_n <= 0:
        return

    bad = sorted(
        {rd.row_index for rd in segment_decision.rows if rd.row_index < header_n}
    )
    if bad:
        raise QualityError(
            f"RowDecision.row_index includes header rows; header rows must not be emitted.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  header_row_count: {header_n}\n"
            f"  bad_row_indices: {bad}"
        )


def validate_table_split_explosion(
    *, max_leaves_per_row: int = 25, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Heuristic guardrail: prevent the LLM from hallucinating/splitting excessively.

    Parameters
    ----------
    max_leaves_per_row
        The maximum allowed number of LeafDecisions per RowDecision.
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any RowDecision contains more than max_leaves_per_row LeafDecisions.
    """

    if segment.kind != "table":
        return

    for rd in segment_decision.rows:
        if len(rd.leaves) > max_leaves_per_row:
            raise QualityError(
                f"RowDecision produced too many leaves (>{max_leaves_per_row}).\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  leaves_count: {len(rd.leaves)}"
            )


def validate_unique_table_rows(
    *, segment: Segment, segment_decision: SegmentDecision
) -> None:
    """Validate that all RowDecision.row_index values are unique.

    Parameters
    ----------
    segment
        The Segment being decided on.
    segment_decision
        The SegmentDecision to validate.

    Raises
    ------
    QualityError
        If any quality checks fail.
    """

    if segment.kind != "table" or not segment_decision.rows:
        return

    table_rows = segment.rows
    max_row_index = len(table_rows) - 1
    start = segment_decision.row_range_start
    end = segment_decision.row_range_end

    seen: set[int] = set()
    dupes: list[int] = []

    for rd in segment_decision.rows:
        # Global range check.
        if rd.row_index < 0 or rd.row_index > max_row_index:
            raise QualityError(
                f"RowDecision.row_index out of range.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  allowed: 0..{max_row_index}\n"
                f"  table_rows: {len(table_rows)}"
            )

        if start is not None and not start <= rd.row_index < end:
            raise QualityError(
                f"RowDecision.row_index outside decision chunk boundaries.\n"
                f"  segment_id: {segment.segment_id}\n"
                f"  decision_id: {segment_decision.decision_id}\n"
                f"  row_index: {rd.row_index}\n"
                f"  allowed_chunk: [{start}, {end})"
            )

        if rd.row_index in seen:
            dupes.append(rd.row_index)
        seen.add(rd.row_index)

    if dupes:
        raise QualityError(
            f"Duplicate RowDecision.row_index values in table decision.\n"
            f"  segment_id: {segment.segment_id}\n"
            f"  decision_id: {segment_decision.decision_id}\n"
            f"  duplicates: {sorted(set(dupes))}"
        )
