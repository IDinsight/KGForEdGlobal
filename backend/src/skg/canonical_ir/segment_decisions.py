"""This module contains utility functions for segment decisions."""

# Standard Library
import re

from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger

# Package Library
from skg.canonical_ir.llm import generate_segment_decision
from skg.canonical_ir.schemas import (
    SegmentDecision,
    SegmentDecisionSet,
    compute_decision_set_id,
)
from skg.canonical_ir.utils import (
    CanonicalIRDirs,
    CaptionBinding,
    _extract_block_segment_text,
    _normalize_text,
)
from skg.document_ir.schemas import BlockSegment, DocumentIR, Segment, TableSegment
from skg.schemas import CreateCanonicalConfig
from skg.utils.constants import (
    BlockType,
    CaptionFigurePrefixes,
    CaptionKind,
    CaptionTablePrefixes,
    NodeRole,
    NonArtifacts,
    SegmentDecisionType,
)
from skg.utils.general import write_to_json


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


def _clip(s: str | None, n: int) -> str:
    """Truncate a string to the first n characters.

    Parameters
    ----------
    s
        The input string. Handles None by converting to empty string.
    n
        The maximum number of characters to return.

    Returns
    -------
    str
        The clipped string.
    """

    s = s or ""

    return s[:n]


def _determine_stable_context(
    *,
    chunk_range: tuple[int, int],
    decision: SegmentDecision,
    fallback_hint: list[dict[str, Any]] | None,
    segment_id: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Extract stable context roles from the first chunk decision.

    Parameters
    ----------
    chunk_range
        The (start, end) row range for logging.
    decision
        The SegmentDecision to extract context from.
    fallback_hint
        The fallback context hint to use if no stable context is found.
    segment_id
        The segment ID for logging.
    warnings
        The list of warnings to append to.

    Returns
    -------
    list[dict[str, Any]]
        The stable context groupings.
    """

    STABLE_CONTEXT_ROLES = {
        NodeRole.GRADE_LEVEL,
        NodeRole.STAGE,
        NodeRole.LEARNING_AREA,
        NodeRole.SUBJECT,
        NodeRole.THEME,
        NodeRole.UNIT,
        NodeRole.TERM,
        NodeRole.WEEK,
        NodeRole.STRAND,
        NodeRole.SUBSTRAND,
        NodeRole.SECTION,
    }

    stable_models = list(decision.context_groupings or [])
    stable_models.extend(
        [g for g in (decision.groupings or []) if g.role in STABLE_CONTEXT_ROLES]
    )

    seen: set[tuple[str, str]] = set()
    stable_hint: list[dict[str, Any]] = []

    for g in stable_models:
        role = str(getattr(g, "role", "")).strip().lower()
        title = str(getattr(g, "title", "")).strip().lower()
        key_fp = (role, title)

        if role and title and key_fp not in seen:
            seen.add(key_fp)
            stable_hint.append(g.model_dump(mode="json"))

    usable = decision.decision_type not in (
        SegmentDecisionType.IGNORE,
        SegmentDecisionType.UNRESOLVED,
    ) and bool(stable_hint)

    if usable:
        return [dict(x) for x in stable_hint]

    msg = (
        f"Chunked table first-chunk produced no usable context_groupings; "
        f"falling back to context_hint for segment_id={segment_id}, "
        f"row_range_start={chunk_range[0]}, row_range_end={chunk_range[1]}."
    )
    logger.warning(msg)
    warnings.append(msg)

    return [dict(x) for x in (fallback_hint or [])]


def _filter_section_path_for_llm(
    section_path: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Remove front-matter/non-artifact headings from the section_path evidence shown
    to the LLM.

    Parameters
    ----------
    section_path
        The section_path to filter.

    Returns
    -------
    list[dict[str, Any]]
        The filtered section_path.
    """

    if not section_path:
        return []

    output: list[dict[str, Any]] = []

    for h in section_path:
        txt = (h.get("text") or "").strip()

        if not txt:
            continue

        norm = _normalize_text(text=txt)

        if norm in NonArtifacts:
            continue

        output.append(h)

    return output


def _list_preview(seg: dict[str, Any], n: int = 3) -> list[str] | None:
    """Generate a preview of the first few list items.

    Parameters
    ----------
    seg
        The segment dictionary.
    n
        The number of list items to sample, by default 3.

    Returns
    -------
    list[str] | None
        A list of clipped item strings, or None if no items exist.
    """

    items = seg.get("list_items") or []
    if not items:
        return None

    output: list[str] = []

    for it in items[:n]:
        if isinstance(it, dict):
            # ListItem likely has text: TextUnit; but be defensive.
            tu = it.get("text") or {}
            body = (tu.get("text") or it.get("body") or "").strip()
            if body:
                output.append(_clip(body, 140))

    return output or None


def _page_span(seg: dict[str, Any]) -> list[int] | None:
    """Calculate the min and max page indices for the segment.

    Parameters
    ----------
    seg
        The segment dictionary.

    Returns
    -------
    list[int] | None
        A list containing [min_page, max_page], or None if no provenance exists.
    """

    prov = seg.get("segment_provenance") or []
    pages: list[int] = []

    for p in prov:
        if isinstance(p, dict) and isinstance(p.get("page_index"), int):
            pages.append(p["page_index"])

    if not pages:
        return None

    return [min(pages), max(pages)]


def _process_block_segment(
    *,
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    next_segment_hint: dict[str, Any] | None = None,
    prev_segment_hint: dict[str, Any] | None = None,
    segment: Segment,
    segment_decisions_fp: Path,
) -> SegmentDecisionSet:
    """Helper to process block segments.

    Parameters
    ----------
    config
        The CreateCanonicalConfig to use.
    context_hint
        The context hint to apply.
    decision_set
        The SegmentDecisionSet to update.
    doc_key
        The document key.
    existing_keys
        The set of existing decision keys.
    next_segment_hint
        The next segment hint to apply.
    prev_segment_hint
        The previous segment hint to apply.
    segment
        The Segment to process.
    segment_decisions_fp
        The file path to save segment decisions to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    key: tuple[str, int | None, int | None] = (segment.segment_id, None, None)
    assert key not in existing_keys, f"Duplicate segment block key found: {key}"

    # Filter the outer evidence shown to the LLM so it matches validator policy.
    segment_payload = segment.model_dump(mode="json")
    segment_payload["section_path"] = _filter_section_path_for_llm(
        segment_payload.get("section_path")
    )
    segment_payload["prior_context_groupings"] = [dict(x) for x in (context_hint or [])]
    segment_payload["prev_segment_hint"] = prev_segment_hint
    segment_payload["next_segment_hint"] = next_segment_hint

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


def _process_chunked_table(
    *,
    binding: CaptionBinding | None,
    chunks: list[tuple[int | None, int | None]],
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, int | None, int | None]],
    next_segment_hint: dict[str, Any] | None,
    prev_segment_hint: dict[str, Any] | None,
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Process chunked table segments.

    Parameters
    ----------
    binding
        The CaptionBinding to apply, or None to skip.
    chunks
        The list of (start, end) row index tuples for each chunk.
    config
        The CreateCanonicalConfig to use.
    context_hint
        The context hint to apply.
    decision_set
        The SegmentDecisionSet to update.
    doc_key
        The document key.
    existing_keys
        The set of existing decision keys.
    next_segment_hint
        The next segment hint to apply.
    prev_segment_hint
        The previous segment hint to apply.
    segment
        The Segment to process.
    segment_decisions_fp
        The file path to save segment decisions to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    unchunked_key = (segment.segment_id, None, None)
    assert (
        unchunked_key not in existing_keys
    ), f"Duplicate unchunked key: {unchunked_key}"

    stable_table_prior_context: list[dict[str, Any]] | None = None

    # Ensure deterministic order.
    chunks_sorted = sorted(
        [(s, e) for (s, e) in chunks if s is not None and e is not None],
        key=lambda x: (x[0], x[1]),
    )

    for start, end in chunks_sorted:
        key = (segment.segment_id, start, end)
        table_payload = make_table_chunk_payload(end=end, segment=segment, start=start)

        # Mark first chunk for validation.
        table_payload.setdefault("chunking", {})
        table_payload["chunking"]["is_first_chunk"] = start == chunks_sorted[0][0]

        table_payload = apply_caption_binding_to_table_payload(
            caption_binding=binding, table_payload=table_payload
        )

        # Chunked table == N decisions.
        #
        # For chunked tables we want a stable "prior_context_groupings" across ALL chunks
        # of the SAME table segment. The best anchor is the context_groupings decided for
        # the FIRST chunk of that table.
        #
        # This avoids drift where chunk 1 gets a rich context (e.g. Learning Area + Subject)
        # but chunk 2+ gets a smaller/different context depending on what segment preceded
        # the table.
        prior = (
            stable_table_prior_context
            if stable_table_prior_context is not None
            else (context_hint or [])
        )
        table_payload["prior_context_groupings"] = [dict(x) for x in prior]
        table_payload["prev_segment_hint"] = prev_segment_hint
        table_payload["next_segment_hint"] = next_segment_hint

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

        # Update stable context if this was the first chunk.
        if stable_table_prior_context is None:
            stable_table_prior_context = _determine_stable_context(
                chunk_range=(start, end),
                decision=segment_decision,
                fallback_hint=context_hint,
                segment_id=segment.segment_id,
                warnings=warnings,
            )

        decision_set.decisions.append(segment_decision)
        existing_keys.add(key)
        decision_set = save_segment_decision_set(
            decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
        )

    return decision_set


def _process_table_segment(
    *,
    caption_bindings: dict[str, CaptionBinding | None],
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    next_segment_hint: dict[str, Any] | None = None,
    prev_segment_hint: dict[str, Any] | None = None,
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Helper to process table segments, handling both chunked and unchunked logic.

    Parameters
    ----------
    caption_bindings
        The mapping of segment_id to CaptionBinding.
    config
        The CreateCanonicalConfig to use.
    context_hint
        The context hint to apply.
    decision_set
        The SegmentDecisionSet to update.
    doc_key
        The document key.
    existing_keys
        The set of existing decision keys.
    next_segment_hint
        The next segment hint to apply.
    prev_segment_hint
        The previous segment hint to apply.
    segment
        The Segment to process.
    segment_decisions_fp
        The file path to save segment decisions to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    # Caption bindings dict is keyed by TABLE segment_id, and many tables have NO
    # caption -> Use .get().
    binding: CaptionBinding | None = caption_bindings.get(segment.segment_id)

    # Determine table chunks.
    chunks = table_chunks_for_segment(
        max_body_rows=config.max_table_rows_per_decision, segment=segment
    )

    if len(chunks) == 1 and chunks[0] == (None, None):
        return _process_unchunked_table(
            binding=binding,
            config=config,
            context_hint=context_hint,
            decision_set=decision_set,
            doc_key=doc_key,
            existing_keys=existing_keys,
            next_segment_hint=next_segment_hint,
            prev_segment_hint=prev_segment_hint,
            segment=segment,
            segment_decisions_fp=segment_decisions_fp,
            warnings=warnings,
        )

    return _process_chunked_table(
        binding=binding,
        chunks=chunks,
        config=config,
        context_hint=context_hint,
        decision_set=decision_set,
        doc_key=doc_key,
        existing_keys=existing_keys,
        next_segment_hint=next_segment_hint,
        prev_segment_hint=prev_segment_hint,
        segment=segment,
        segment_decisions_fp=segment_decisions_fp,
        warnings=warnings,
    )


def _process_unchunked_table(
    *,
    binding: CaptionBinding | None,
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, int | None, int | None]],
    next_segment_hint: dict[str, Any] | None,
    prev_segment_hint: dict[str, Any] | None,
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Process unchunked table segments.

    Parameters
    ----------
    binding
        The CaptionBinding to apply, or None to skip.
    config
        The CreateCanonicalConfig to use.
    context_hint
        The context hint to apply.
    decision_set
        The SegmentDecisionSet to update.
    doc_key
        The document key.
    existing_keys
        The set of existing decision keys.
    next_segment_hint
        The next segment hint to apply.
    prev_segment_hint
        The previous segment hint to apply.
    segment
        The Segment to process.
    segment_decisions_fp
        The file path to save segment decisions to.
    warnings
        The list of warnings to append to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    unchunked_key = (segment.segment_id, None, None)
    assert (
        unchunked_key not in existing_keys
    ), f"Duplicate unchunked key: {unchunked_key}"

    # Do NOT create unchunked decision if chunked ones already exist.
    existing_chunked_for_segment = any(
        sid == segment.segment_id and row_start is not None
        for (sid, row_start, _row_end) in existing_keys
    )
    if existing_chunked_for_segment:
        msg = (
            f"Skipping unchunked decision for {segment.segment_id} because chunked "
            f"decisions already exist."
        )
        logger.warning(msg)
        warnings.append(msg)
        return decision_set

    # Build table payload.
    table_payload = make_table_full_payload(segment=segment)
    table_payload = apply_caption_binding_to_table_payload(
        caption_binding=binding, table_payload=table_payload
    )
    table_payload["prior_context_groupings"] = [dict(x) for x in (context_hint or [])]
    table_payload["prev_segment_hint"] = prev_segment_hint
    table_payload["next_segment_hint"] = next_segment_hint

    # Generate segment decision.
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
    existing_keys.add(unchunked_key)

    return save_segment_decision_set(
        decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
    )


def _row_to_text(row: dict[str, Any], max_cols: int = 6) -> list[str]:
    """Convert a table row dictionary into a list of cell texts.

    Parameters
    ----------
    row
        The row dictionary containing cells.
    max_cols
        Maximum number of columns to process, by default 6.

    Returns
    -------
    list[str]
        A list of strings representing cell content, with trailing empty cells removed.
    """

    cells = row.get("cells") or []
    txts: list[str] = []

    for c in cells[:max_cols]:
        if not isinstance(c, dict):
            continue

        tu = c.get("text") or {}
        t = (tu.get("text") or "").strip()
        txts.append(_clip(t, 120))

    # Trim trailing empties for compactness.
    while txts and not txts[-1]:
        txts.pop()
    return txts


def _section_path_texts(seg: dict[str, Any], k: int = 6) -> list[str]:
    """Extract recent section headings from the segment path.

    Parameters
    ----------
    seg
        The segment dictionary.
    k
        The number of recent sections to include, by default 6.

    Returns
    -------
    list[str]
        A list of section heading strings.
    """

    # SectionHeadingRef has .text; keep only recent ones.
    sp = _filter_section_path_for_llm(seg.get("section_path")) or []
    texts: list[str] = []

    for ref in sp[-k:]:
        if isinstance(ref, dict):
            t = (ref.get("text") or "").strip()
            if t:
                texts.append(t)

    return texts


def _table_sample(seg: dict[str, Any]) -> dict[str, Any]:
    """Generate a structural summary and data sample for a table segment.

    Parameters
    ----------
    seg
        The segment dictionary representing a table.

    Returns
    -------
    dict[str, Any]
        A dictionary containing metadata (cols, rows) and body samples.
    """

    header_rows_canonical = seg.get("header_rows_canonical") or []
    header_row_count = int(seg.get("header_row_count") or 0)
    n_cols = seg.get("n_cols")

    # Prefer filldown sample for “topic/subtopic/strand” signals if present.
    rows = seg.get("rows_filldown") or seg.get("rows") or []
    row_count = len(seg.get("rows") or [])

    # Sample 2 body rows immediately after headers.
    body_samples: list[list[str]] = []
    start = min(header_row_count, len(rows))
    for r in rows[start : start + 2]:
        if isinstance(r, dict):
            body_samples.append(_row_to_text(r, max_cols=6))

    return {
        "columns_signature": seg.get("columns_signature"),
        "header_rows_canonical": header_rows_canonical[:2],
        "header_row_count": header_row_count,
        "n_cols": n_cols,
        "row_count": row_count,
        "body_row_samples": body_samples,
        "has_rows_grid": bool(seg.get("rows_grid")),
        "has_rows_filldown": bool(seg.get("rows_filldown")),
    }


def _tail(s: str | None, n: int) -> str:
    """Return the suffix of a string.

    Parameters
    ----------
    s
        The input string. Handles None by converting to empty string.
    n
        The number of characters to return from the end.

    Returns
    -------
    str
        The last n characters, or the whole string if length < n.
    """

    s = s or ""

    return s[-n:] if len(s) > n else s


def _text_preview_for_block(seg: dict[str, Any]) -> dict[str, str]:
    """Generate a preview of text content (head and tail).

    Parameters
    ----------
    seg
        The segment dictionary.

    Returns
    -------
    dict[str, str]
        A dictionary with 'text_head' and 'text_tail' keys.
    """

    # Prefer combined_text if present (stitched blocks), otherwise TextUnit.text.
    combined = (seg.get("combined_text") or "").strip()
    text_unit = seg.get("text") or {}
    raw = (text_unit.get("text") or "").strip()
    src = combined if combined else raw

    return {
        "text_head": _clip(src, 260),
        "text_tail": _tail(src, 120),
    }


def apply_caption_binding_to_table_payload(
    *, caption_binding: CaptionBinding | None, table_payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply caption binding information to a table segment payload.

    Parameters
    ----------
    caption_binding
        The CaptionBinding to apply, or None to skip.
    table_payload
        The table segment payload to update.

    Returns
    -------
    dict[str, Any]
        The updated table segment payload.
    """

    if not caption_binding:
        return table_payload

    table_payload["caption_gap_segments"] = caption_binding.gap_segments
    table_payload["caption_kind"] = caption_binding.caption_kind
    table_payload["caption_page_index"] = caption_binding.caption_page_index
    table_payload["caption_segment_id"] = caption_binding.caption_segment_id
    table_payload["caption_text"] = caption_binding.caption_text

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
    creation_dirs: CanonicalIRDirs,
    document_ir: DocumentIR,
    max_gap_segments: int = 2,
    max_page_distance: int = 1,
) -> dict[str, CaptionBinding]:
    """Build deterministic caption→table bindings *before* LLM interpretation.

    Many curriculum PDFs place a short caption/label block immediately before a table.
    That caption is usually not curriculum content itself, but it often contains
    critical context (grade, subject, theme/unit, table meaning) needed to interpret
    the table.

    This function:

    1. Scans DocumentIR.segments[] in order and one-shot binds each CAPTION block to
        the *next* table segment (within configured gap/page limits).
    2. Produces a stable mapping: table_segment_id -> CaptionBinding(...).
    3. Emits warnings for captions that cannot be bound (e.g., dangling captions).

    We call this function before calling the LLM so that we can:

    1. Improve the LLM accuracy by injecting caption context into table payloads,
        helping it choose correct context_groupings[] and statement roles.
    2. Avoid asking the LLM to infer cross-segment relationships, keeping behavior
        deterministic and replayable.
    3. Enforce the policy that captions are provenance-only: captions provide evidence
        but never become canonical nodes.
    4. Stabilize chunked-table processing by ensuring all chunks of a table receive the
        same caption metadata.

    The resulting bindings are applied when constructing LLM inputs for table segments
    and are stored as provenance/audit context (or attached to unresolved items).

    Parameters
    ----------
    bind_unknown_caption
        Whether to bind captions of unknown kind.
    creation_dirs
        The canonical IR creation directories.
    document_ir
        The DocumentIR to process.
    max_gap_segments
        The maximum number of non-table segments allowed between caption and table.
    max_page_distance
        The maximum page distance allowed between caption and table.

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings: dict[str, CaptionBinding] = {}
    warnings: list[str] = []

    # (caption_segment, caption_text, caption_kind, caption_page, caption_index)
    pending_caption: tuple[BlockSegment, str, CaptionKind, int, int] | None = None

    for index, segment in enumerate(document_ir.segments):
        page_index = segment.slices[0].page_index
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

    if pending_caption is not None:
        cap_seg, *_ = pending_caption
        msg = f"Dangling caption dropped: caption={cap_seg.segment_id} end_of_document"
        logger.warning(msg)
        warnings.append(msg)

    warnings_fp = creation_dirs.root / "caption_binding_warnings.json"
    write_to_json(fp=warnings_fp, json_info={"warnings": warnings})

    return caption_bindings


def make_table_chunk_payload(
    *,
    context_rows_after: int = 2,
    context_rows_before: int = 2,
    end: int,
    segment: TableSegment,
    start: int,
) -> dict[str, Any]:
    """Build a table chunk payload for the LLM as follows:

    1. Keep table metadata + headers
    2. Replace `rows` with ONLY the rows in [start,end)
    3. Adds abs_row_index to each provided row
    4. Adds a `chunking` object so prompts can instruct absolute indexing
    5. Adds `context_rows_before` containing up to N rows immediately preceding `start`
       (context-only; the LLM MUST NOT emit RowDecision for these rows).
    6. Adds `context_rows_after` containing up to M rows immediately following `end`
       (context-only; the LLM MUST NOT emit RowDecision for these rows).
    7. If `rows_filldown` exists in the segment, uses it to produce a fill-down view of
       ONLY the decision rows. The filled rows become the main `rows` payload, and the
       raw visual decision rows are preserved under `rows_original`.

    Parameters
    ----------
    context_rows_after
        The number of context rows to include after the chunk end.
    context_rows_before
        The number of context rows to include before the chunk start.
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
    seg["section_path"] = _filter_section_path_for_llm(seg.get("section_path"))

    # NB: Chunk payload should not include full-table derived views that can leak
    # information outside the chunk. NB: We intentionally KEEP rows_filldown here (if
    # present) but slice it down to the decision-row window so it does not expose the
    # entire table.
    for k in ("rows_grid", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    full_rows_raw = seg.get("rows") or []
    full_rows_filldown: list[dict[str, Any]] | None = seg.get("rows_filldown")

    # Context windows (before/after).
    ctx_before_start = max(0, start - max(0, int(context_rows_before or 0)))
    ctx_after_end = min(len(full_rows_raw), end + max(0, int(context_rows_after or 0)))

    context_rows_before_payload: list[dict[str, Any]] = []
    context_rows_after_payload: list[dict[str, Any]] = []

    for abs_i in range(ctx_before_start, start):
        row = dict(full_rows_raw[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_before_payload.append(row)

    for abs_i in range(end, ctx_after_end):
        row = dict(full_rows_raw[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_after_payload.append(row)

    # Decision rows: raw visual + optional fill-down view.
    decision_rows_raw: list[dict[str, Any]] = []
    decision_rows_payload: list[dict[str, Any]] = []

    # Prefer fill-down view (if available) for primary `rows`, because validators
    # ground row-local groupings against visible row text.
    use_filldown = (full_rows_filldown is not None) and len(full_rows_filldown) == len(
        full_rows_raw
    )

    for abs_i in range(start, end):
        raw_row = dict(full_rows_raw[abs_i])
        raw_row["abs_row_index"] = abs_i
        raw_row["is_context_only"] = False
        decision_rows_raw.append(raw_row)

        if use_filldown:
            assert full_rows_filldown is not None
            fd_row = dict(full_rows_filldown[abs_i])
            fd_row["abs_row_index"] = abs_i
            fd_row["is_context_only"] = False
            decision_rows_payload.append(fd_row)
        else:
            decision_rows_payload.append(raw_row)

    # Primary decision rows (potentially fill-down adjusted)/
    seg["rows"] = decision_rows_payload

    # Preserve raw visual decision rows for audit/debug.
    seg["rows_original"] = decision_rows_raw

    # Preserve context rows separately (raw visual).
    seg["context_rows_before"] = context_rows_before_payload
    seg["context_rows_after"] = context_rows_after_payload

    # Keep ONLY the decision-row slice of rows_filldown for explicitness/debugging.
    if use_filldown:
        seg["rows_filldown"] = [dict(r) for r in decision_rows_payload]
    else:
        seg.pop("rows_filldown", None)

    seg["chunking"] = {
        "is_chunked": True,
        "row_range_start": start,
        "row_range_end": end,
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
        "context_rows_before_start": ctx_before_start,
        "context_rows_before_end": start,
        "context_rows_before_count": len(context_rows_before_payload),
        "context_rows_after_start": end,
        "context_rows_after_end": ctx_after_end,
        "context_rows_after_count": len(context_rows_after_payload),
        "rows_are_filldown_view": use_filldown,
        "rows_original_preserved": True,
    }

    return seg


def make_table_full_payload(*, segment: TableSegment) -> dict[str, Any]:
    """Build a FULL (unchunked) table payload for the LLM.

    This mirrors `make_table_chunk_payload` but includes ALL rows. Critically, it:

    1. Prefers `rows_filldown` (if available) so row-level groupings are grounded
        in-row (raw visual rows are preserved under `rows_original`).
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
    seg["section_path"] = _filter_section_path_for_llm(seg.get("section_path"))

    # Prefer fill-down view if it exists.
    rows_raw = seg.get("rows") or []
    rows_filldown = seg.get("rows_filldown")

    use_filldown = (
        isinstance(rows_filldown, list)
        and len(rows_filldown) == len(rows_raw)
        and len(rows_raw) > 0
    )

    if use_filldown:
        seg["rows_original"] = rows_raw
        seg["rows"] = rows_filldown  # Store rows_filldown here before removing
        seg["rows_original_preserved"] = True
    else:
        seg["rows_original_preserved"] = False

    # NB: Remove derived structures that bloat the prompt. We intentionally keep the
    # filldown effect by swapping seg["rows"] above.
    for k in ("rows_grid", "rows_filldown", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    rows = seg.get("rows") or []

    # Add abs_row_index to every row (headers included).
    for abs_i, row in enumerate(rows):
        if isinstance(row, dict):
            row["abs_row_index"] = abs_i

    seg["rows"] = rows
    seg["chunking"] = {
        "is_chunked": False,
        "row_range_start": 0,
        "row_range_end": len(rows),
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
    }

    return seg


def process_segment_decisions(
    *,
    caption_bindings: dict[str, CaptionBinding | None],
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None = None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    prev_segment_hint: dict[str, Any] | None = None,
    next_segment_hint: dict[str, Any] | None = None,
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Process a single segment to generate and persist decisions.

    Parameters
    ----------
    caption_bindings
        The caption bindings to apply to table segments.
    config
        The canonical IR creation run configuration.
    context_hint
        The context hint to include in the segment decision payload.
    decision_set
        The current SegmentDecisionSet to update.
    doc_key
        The expected document key for all page IRs.
    existing_keys
        The set of existing decision keys to avoid duplicates.
    prev_segment_hint
        The previous segment hint to include in the segment decision payload.
    next_segment_hint
        The next segment hint to include in the segment decision payload.
    segment
        The Segment to process.
    segment_decisions_fp
        The output file path for the SegmentDecisionSet JSON.
    warnings
        A list to append warning messages to.

    Returns
    -------
    SegmentDecisionSet
        The updated SegmentDecisionSet.
    """

    assert segment.kind in (
        "block",
        "table",
    ), f"Unexpected segment kind: {segment.kind}"

    if segment.kind == "block":
        return _process_block_segment(
            config=config,
            context_hint=context_hint,
            decision_set=decision_set,
            doc_key=doc_key,
            existing_keys=existing_keys,
            next_segment_hint=next_segment_hint,
            prev_segment_hint=prev_segment_hint,
            segment=segment,
            segment_decisions_fp=segment_decisions_fp,
        )

    return _process_table_segment(
        caption_bindings=caption_bindings,
        config=config,
        context_hint=context_hint,
        decision_set=decision_set,
        doc_key=doc_key,
        existing_keys=existing_keys,
        next_segment_hint=next_segment_hint,
        prev_segment_hint=prev_segment_hint,
        segment=segment,
        segment_decisions_fp=segment_decisions_fp,
        warnings=warnings,
    )


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


def segment_hint(segment: dict[str, Any]) -> dict[str, Any]:
    """Generate a compact hint dictionary for a segment.

    NB: Keep this SMALL (only things that help context).

    Parameters
    ----------
    segment
        The segment dictionary.

    Returns
    -------
    dict[str, Any]
        The compact hint dictionary.
    """

    kind = segment.get("kind")
    assert kind in ("block", "table")

    hint: dict[str, Any] = {
        "segment_id": segment.get("segment_id"),
        "kind": kind,
        "local_code": segment.get("local_code"),
        "page_span": _page_span(segment),
        "section_path_texts": _section_path_texts(segment, k=6),
    }

    if kind == "block":
        hint["block_type"] = segment.get("block_type")
        hint.update(_text_preview_for_block(segment))

        lp = _list_preview(segment, n=3)
        if lp:
            hint["list_item_samples"] = lp

        if segment.get("figure") is not None:
            hint["has_figure"] = True
    else:
        hint["table"] = _table_sample(segment)

    return hint


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
