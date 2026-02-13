"""This module contains utility functions for segment decisions."""

# Standard Library
import copy
import re

from pathlib import Path
from typing import Any, Optional

# Third Party Library
from loguru import logger
from pydantic import TypeAdapter

# Package Library
from skg.canonical_ir.llm import generate_segment_decision
from skg.canonical_ir.schemas import (
    CaptionBinding,
    SegmentDecision,
    SegmentDecisionSet,
    compute_decision_set_id,
)
from skg.canonical_ir.utils import CanonicalIRDirs, _extract_block_segment_text
from skg.document_ir.schemas import (
    BlockSegment,
    DocumentIR,
    SectionHeadingRef,
    Segment,
    TableSegment,
)
from skg.page_ir_extraction.schemas import TableCell, TableRow, TextUnit
from skg.schemas import CreateCanonicalConfig
from skg.utils.constants import (
    BlockType,
    CaptionFigurePrefixes,
    CaptionKind,
    CaptionTablePrefixes,
    FrontMatterHeadings,
    NonArtifacts,
    SegmentDecisionType,
)
from skg.utils.general import open_json_type, write_to_json


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

    stable_models = list(decision.context_groupings or [])

    seen: set[tuple[str, str]] = set()
    stable_hint: list[dict[str, Any]] = []

    for g in stable_models:
        role = g.role.value.strip().casefold()
        title = re.sub(r"\s+", " ", g.title).strip().casefold()
        key_fp = (role, title)

        if role and title and key_fp not in seen:
            seen.add(key_fp)
            stable_hint.append(g.model_dump(mode="json"))

    # Only trust context_groupings from decisions we intend to materialize.
    # EMIT_FLAGGED_UNRESOLVED explicitly means "store for review; not reliable enough
    # to compile", so it must NOT become the stable prior context for later chunks.
    usable = decision.decision_type in (
        SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES,
        SegmentDecisionType.EMIT_GROUPINGS_ONLY,
        SegmentDecisionType.EMIT_LEAVES_ONLY,
    ) and bool(stable_hint)

    if usable:
        return copy.deepcopy(stable_hint)

    msg = (
        f"Chunked table first-chunk produced no usable context_groupings; "
        f"decision_type={decision.decision_type}; "
        f"falling back to context_hint for segment_id={segment_id}, "
        f"row_range_start={chunk_range[0]}, row_range_end={chunk_range[1]}."
    )
    logger.warning(msg)
    warnings.append(msg)

    return copy.deepcopy(fallback_hint or [])


def _filter_section_path_for_llm(
    *,
    heading_levels: dict[str, int],
    max_items: int = 5,
    max_page_distance: int = 3,
    section_paths: list[SectionHeadingRef],
    segment_item_index: int | None,
    segment_page_index: int,
) -> list[dict[str, Any]]:
    """Reduce section_path to only the *relevant, nearby* headings for the LLM.

    NB:

    1. Never include headings that occur AFTER the segment (future context).
    2. Prefer headings on the same page or within the last N pages.
    3. Keep only the tail max_items after filtering.
    4. De-dupe consecutive identical headings.

    Parameters
    ----------
    heading_levels
        Mapping from normalized heading text to structural depth level.
    max_items
        The maximum number of section headings to keep.
    max_page_distance
        The maximum page distance to keep.
    section_paths
        A list of SectionHeadingRef objects.
    segment_item_index
        The segment item index.
    segment_page_index
        The segment page index.

    Returns
    -------
    list[dict[str, Any]]
        The filtered section_path.
    """

    if not section_paths:
        return []

    filtered: list[SectionHeadingRef] = []

    for section_path in section_paths:
        text_cf = " ".join(section_path.text.split()).casefold()

        if text_cf in {h.value for h in FrontMatterHeadings} or text_cf in NonArtifacts:
            continue

        # Drop headings that are after the segment or that are too far away.
        if (
            section_path.page_index > segment_page_index
            or (
                section_path.page_index == segment_page_index
                and segment_item_index is not None
                and section_path.item_index > segment_item_index
            )
            or (segment_page_index - section_path.page_index) > max_page_distance
        ):
            continue

        filtered.append(section_path)

    # If we filtered too aggressively, keep the closest prior heading as a fallback.
    if not filtered:
        # Get the first matching item in reverse, or keep list empty if none found.
        filtered = [
            sp for sp in reversed(section_paths) if sp.page_index <= segment_page_index
        ][:1]

    output = [sp.model_dump(mode="json") for sp in filtered]

    # Reconstruct proper heading stack using LLM-assigned levels.
    output = reconstruct_section_path(
        heading_levels=heading_levels, section_paths=output
    )

    # De-dupe consecutive identical heading texts.
    deduped: list[dict[str, Any]] = []

    prev_norm: str | None = None

    for item in output:
        norm = " ".join(item["text"].split()).casefold()

        if norm == prev_norm:
            continue

        prev_norm = norm
        deduped.append(item)

    return deduped[-max_items:]


def _is_structural_heading(*, heading_levels: dict[str, int], segment: Segment) -> bool:
    """Check if a block segment is a structural heading that should be auto-ignored.

    Structural headings (heading_level > 0) exist solely to establish section hierarchy
    for downstream segments. Their structural role is already captured in the
    section_path of subsequent segments, so sending them to the LLM for a segment
    decision is wasted compute and produces validator failures (the heading's own text
    is not "outer evidence" for itself).

    Parameters
    ----------
    heading_levels
        Mapping from normalized heading text to structural depth level.
    segment
        The Segment to check.

    Returns
    -------
    bool
        True if the segment is a structural heading that should be auto-ignored.
    """

    if segment.kind != "block" or segment.block_type != BlockType.HEADING:
        return False

    text = _extract_block_segment_text(segment)
    norm = " ".join(text.split()).casefold()

    if not norm:
        return False

    level = heading_levels.get(norm)

    # Level > 0 means structural. Level 0 = non-structural (front matter etc.). Level
    # None = heading not in map (shouldn't happen, but don't auto-ignore).
    return level is not None and level > 0


def _make_auto_ignore_decision(
    *, doc_key: str, reason: str, segment: Segment
) -> SegmentDecision:
    """Create a deterministic IGNORE SegmentDecision without calling the LLM.

    Parameters
    ----------
    doc_key
        The document key.
    reason
        A human-readable rationale for the auto-ignore.
    segment
        The Segment being ignored.

    Returns
    -------
    SegmentDecision
        A minimal IGNORE decision matching the schema.
    """

    return SegmentDecision(
        block_type=segment.block_type if segment.kind == "block" else None,
        confidence=1.0,
        context_groupings=[],
        decision_id=f"segment_decision:{doc_key}:{segment.segment_id}",
        decision_type=SegmentDecisionType.IGNORE,
        groupings=[],
        leaves=[],
        rationale=reason,
        row_range_end=None,
        row_range_start=None,
        rows=[],
        segment_id=segment.segment_id,
        segment_kind=segment.kind,
    )


def _process_block_segment(
    *,
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    heading_levels: dict[str, int],
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
    heading_levels
        Mapping from normalized heading text to structural depth level.
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

    # Structural headings (heading_level > 0) exist to create section hierarchy for
    # downstream segments via section_path. Their role is already captured there, so
    # calling the LLM is wasted compute and triggers validator failures (the heading's
    # own text is not "outer evidence" for itself).
    if _is_structural_heading(heading_levels=heading_levels, segment=segment):
        text_preview = _extract_block_segment_text(segment)[:120]
        norm = " ".join(text_preview.split()).casefold()
        level = heading_levels.get(norm, "?")

        logger.warning(
            f"Auto-ignoring structural heading (level={level}): "
            f'"{text_preview}" [segment_id={segment.segment_id}]'
        )

        segment_decision = _make_auto_ignore_decision(
            doc_key=doc_key,
            reason=(
                f"Auto-ignored: structural heading at level {level}. "
                f"Section hierarchy is captured in section_path for downstream segments."
            ),
            segment=segment,
        )
        decision_set.decisions.append(segment_decision)
        existing_keys.add(key)

        return save_segment_decision_set(
            decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
        )

    # Filter payload evidence that is not helpful to the LLM.
    segment_payload = segment.model_dump(
        exclude={"segment_id", "segment_provenance", "slices"}, mode="json"
    )

    # Add additional payload evidence that is helpful to the LLM.
    assert (
        segment.slices
    ), f"Segment {segment.segment_id} has no slices; cannot determine page context."
    segment_payload["section_path"] = _filter_section_path_for_llm(
        heading_levels=heading_levels,
        section_paths=segment.section_path,
        segment_item_index=segment.slices[0].item_index,
        segment_page_index=segment.slices[0].page_index,
    )
    segment_payload["prior_context_groupings"] = [dict(x) for x in (context_hint or [])]
    segment_payload["prev_segment_hint"] = prev_segment_hint
    segment_payload["next_segment_hint"] = next_segment_hint

    segment_decision = generate_segment_decision(
        always_double_check_first_attempt=config.always_double_check_first_attempt,
        doc_key=doc_key,
        heading_role_hints=config.heading_role_hints,
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
    caption_binding: CaptionBinding | None,
    chunks: list[tuple[int | None, int | None]],
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, int | None, int | None]],
    heading_levels: dict[str, int],
    next_segment_hint: dict[str, Any] | None,
    prev_segment_hint: dict[str, Any] | None,
    segment: Segment,
    segment_decisions_fp: Path,
    warnings: list[str],
) -> SegmentDecisionSet:
    """Process chunked table segments.

    Parameters
    ----------
    caption_binding
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
    heading_levels
        Mapping from normalized heading text to structural depth level.
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

    for i, (start, end) in enumerate(chunks_sorted):
        key = (segment.segment_id, start, end)
        assert key not in existing_keys, f"Duplicate chunk key found: {key}"
        table_payload = make_table_chunk_payload(
            end=end, heading_levels=heading_levels, segment=segment, start=start
        )

        # Mark first chunk for validation.
        table_payload.setdefault("chunking", {})
        table_payload["chunking"]["is_first_chunk"] = i == 0

        table_payload = apply_caption_binding_to_table_payload(
            caption_binding=caption_binding, table_payload=table_payload
        )

        # Chunked table == N decisions.
        #
        # For chunked tables we want a stable "prior_context_groupings" across ALL
        # chunks of the SAME table segment. The best anchor is the context_groupings
        # decided for the FIRST chunk of that table.
        #
        # This avoids drift where chunk 1 gets a rich context (e.g. Learning Area +
        # Subject) but chunk 2+ gets a smaller/different context depending on what
        # segment preceded the table.
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
            heading_role_hints=config.heading_role_hints,
            model=config.model,
            row_range_end=end,
            row_range_start=start,
            segment=segment,
            segment_payload=table_payload,
        )
        segment_decision = attach_caption_binding_to_segment_decision(
            caption_binding=caption_binding, segment_decision=segment_decision
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
    heading_levels: dict[str, int],
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
    heading_levels
        Mapping from normalized heading text to structural depth level.
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

    # caption_bindings is keyed by TABLE segment_id, and some tables may not have
    # captions.
    caption_binding = caption_bindings.get(segment.segment_id)

    # Determine table chunks.
    chunks = table_chunks_for_segment(
        max_body_rows=config.max_table_rows_per_decision, segment=segment
    )

    if len(chunks) == 1 and chunks[0] == (None, None):
        return _process_unchunked_table(
            caption_binding=caption_binding,
            config=config,
            context_hint=context_hint,
            decision_set=decision_set,
            doc_key=doc_key,
            existing_keys=existing_keys,
            heading_levels=heading_levels,
            next_segment_hint=next_segment_hint,
            prev_segment_hint=prev_segment_hint,
            segment=segment,
            segment_decisions_fp=segment_decisions_fp,
        )

    return _process_chunked_table(
        caption_binding=caption_binding,
        chunks=chunks,
        config=config,
        context_hint=context_hint,
        decision_set=decision_set,
        doc_key=doc_key,
        existing_keys=existing_keys,
        heading_levels=heading_levels,
        next_segment_hint=next_segment_hint,
        prev_segment_hint=prev_segment_hint,
        segment=segment,
        segment_decisions_fp=segment_decisions_fp,
        warnings=warnings,
    )


def _process_unchunked_table(
    *,
    caption_binding: CaptionBinding | None,
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, int | None, int | None]],
    heading_levels: dict[str, int],
    next_segment_hint: dict[str, Any] | None,
    prev_segment_hint: dict[str, Any] | None,
    segment: Segment,
    segment_decisions_fp: Path,
) -> SegmentDecisionSet:
    """Process unchunked table segments.

    Parameters
    ----------
    caption_binding
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
    heading_levels
        Mapping from normalized heading text to structural depth level.
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

    unchunked_key = (segment.segment_id, None, None)
    assert (
        unchunked_key not in existing_keys
    ), f"Duplicate unchunked key: {unchunked_key}"

    # Build table payload.
    table_payload = make_table_full_payload(
        heading_levels=heading_levels, segment=segment
    )
    table_payload = apply_caption_binding_to_table_payload(
        caption_binding=caption_binding, table_payload=table_payload
    )
    table_payload["prior_context_groupings"] = [dict(x) for x in (context_hint or [])]
    table_payload["prev_segment_hint"] = prev_segment_hint
    table_payload["next_segment_hint"] = next_segment_hint

    # Generate segment decision.
    segment_decision = generate_segment_decision(
        always_double_check_first_attempt=config.always_double_check_first_attempt,
        doc_key=doc_key,
        heading_role_hints=config.heading_role_hints,
        model=config.model,
        segment=segment,
        segment_payload=table_payload,
    )
    segment_decision = attach_caption_binding_to_segment_decision(
        caption_binding=caption_binding, segment_decision=segment_decision
    )

    decision_set.decisions.append(segment_decision)
    existing_keys.add(unchunked_key)

    return save_segment_decision_set(
        decision_set=decision_set, segment_decisions_fp=segment_decisions_fp
    )


def _row_to_text(*, max_cols: int = 6, row: TableRow) -> list[str]:
    """Convert a table row dictionary into a list of cell texts.

    Parameters
    ----------
    max_cols
        Maximum number of columns to process, by default 6.
    row
        The TableRow dictionary to process.

    Returns
    -------
    list[str]
        A list of strings representing cell content, with trailing empty cells removed.
    """

    texts: list[str] = []

    for cell in row.cells[:max_cols]:
        assert isinstance(cell, TableCell), f"{row = } {cell = }"
        text_unit_or_none = cell.text
        text = (
            (text_unit_or_none.text or "").strip()
            if isinstance(text_unit_or_none, TextUnit)
            else ""
        )
        texts.append(text[:500])

    # Trim trailing empties for compactness.
    while texts and not texts[-1]:
        texts.pop()

    return texts


def _section_path_texts(
    *,
    heading_levels: dict[str, int],
    k: int = 6,
    segment: Segment,
    segment_item_index: int,
    segment_page_index: int,
) -> list[str]:
    """Extract recent section headings from the segment path.

    Parameters
    ----------
    heading_levels
        Mapping from normalized heading text to structural depth level.
    k
        The number of recent sections to include, by default 6.
    segment
        The Segment to extract from.
    segment_item_index
        The segment item index.
    segment_page_index
        The segment page index.

    Returns
    -------
    list[str]
        A list of section heading strings.
    """

    # SectionHeadingRef has .text; keep only recent ones.
    section_paths = _filter_section_path_for_llm(
        heading_levels=heading_levels,
        section_paths=segment.section_path,
        segment_item_index=segment_item_index,
        segment_page_index=segment_page_index,
    )
    texts: list[str] = []

    for sp in section_paths[-k:]:
        text = sp["text"].strip()
        assert text, f"{section_paths = }"
        texts.append(text)

    return texts


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


def collect_unique_headings(document_ir: DocumentIR) -> list[dict[str, Any]]:
    """Collect all unique heading texts from every segment's section_path. Returns a
    list of {"text": ..., "page_index": ..., "item_index": ...} preserving
    first-encounter order.

    Parameters
    ----------
    document_ir
        The stitched DocumentIR.

    Returns
    -------
    list[dict[str, Any]]
        Unique headings in document order.
    """

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    for segment in document_ir.segments:
        for sp in segment.section_path:
            text = sp.text.strip()
            norm = " ".join(text.split()).casefold()

            if norm and norm not in seen:
                seen.add(norm)
                ordered.append(
                    {
                        "text": text,
                        "page_index": sp.page_index,
                        "item_index": sp.item_index,
                    }
                )

    return ordered


def load_or_build_caption_bindings(
    *,
    bind_unknown_caption: bool = True,
    creation_dirs: CanonicalIRDirs,
    document_ir: DocumentIR,
    max_gap_segments: int = 2,
    max_page_distance: int = 1,
    overwrite: bool,
) -> dict[str, CaptionBinding]:
    """Load existing caption-to-table bindings or build deterministic caption-to-table
    bindings *before* LLM interpretation.

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
    overwrite
        Whether to overwrite existing caption bindings.

    Returns
    -------
    dict[str, CaptionBinding]
        The computed caption bindings, keyed by table segment ID.
    """

    caption_bindings_fp = creation_dirs.canonical_ir / "caption_bindings.json"
    warnings_fp = creation_dirs.segment_decisions / "caption_binding_warnings.json"

    if not overwrite and caption_bindings_fp.exists() and warnings_fp.exists():
        logger.warning(
            f"Caption bindings already exists at: {caption_bindings_fp}. "
            f"If you wish to overwrite, pass the --overwrite flag."
        )
        adapter = TypeAdapter(dict[str, CaptionBinding])
        caption_bindings_json = open_json_type(caption_bindings_fp)
        return adapter.validate_python(caption_bindings_json)

    caption_bindings: dict[str, CaptionBinding] = {}
    warnings: list[str] = []

    # (caption_segment, caption_text, caption_kind, caption_page, caption_index)
    pending_caption: tuple[BlockSegment, str, CaptionKind, int, int] | None = None

    for index, segment in enumerate(document_ir.segments):
        assert (
            segment.slices
        ), f"Segment {segment.segment_id} has no slices; cannot determine page index."
        page_index = segment.slices[0].page_index
        assert isinstance(page_index, int) and page_index >= 0

        # Explicit caption candidate.
        if segment.kind == "block":
            caption_text = _extract_block_segment_text(segment)

            # NB: Sometimes, headings contain the actual caption text for the table.
            if (
                segment.block_type in (BlockType.CAPTION, BlockType.HEADING)
                and caption_text
            ):
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
                    f"Dangling caption dropped:\n"
                    f"caption={cap_seg.segment_id}\n"
                    f"gap={gap}\n"
                    f"page_index={page_index}\n"
                    f"segment_index={index}"
                )
                logger.warning(msg)
                warnings.append(msg)

            pending_caption = None

            continue

        # Expire pending caption if too far. NB: pending_caption[4] is cap_index.
        if (
            pending_caption is not None
            and max(0, index - pending_caption[4] - 1) > max_gap_segments
        ):
            cap_seg, _, _, _, cap_index = pending_caption
            msg = (
                f"Dangling caption dropped:\n"
                f"caption={cap_seg.segment_id} gap_exceeded={max(0, index - cap_index - 1)}\n"
                f"page_index={page_index}\n"
                f"segment_index={index}"
            )
            logger.warning(msg)
            warnings.append(msg)
            pending_caption = None

    if pending_caption is not None:
        cap_seg, *_ = pending_caption
        msg = f"Dangling caption dropped: caption={cap_seg.segment_id} end_of_document"
        logger.warning(msg)
        warnings.append(msg)

    write_to_json(
        fp=caption_bindings_fp,
        json_info={k: v.model_dump() for k, v in caption_bindings.items()},
    )
    write_to_json(fp=warnings_fp, json_info={"warnings": warnings})

    return caption_bindings


def make_table_chunk_payload(
    *,
    context_rows_after: int = 2,
    context_rows_before: int = 2,
    end: int,
    heading_levels: dict[str, int],
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
    heading_levels
        Mapping from normalized heading text to structural depth level.
    segment
        The TableSegment to chunk.
    start
        The inclusive start row index for the chunk.

    Returns
    -------
    dict[str, Any]
        The table chunk payload.
    """

    seg = segment.model_dump(
        exclude={"segment_id", "segment_provenance", "slices"}, mode="json"
    )
    assert (
        segment.slices
    ), f"Segment {segment.segment_id} has no slices; cannot determine page context."
    seg["section_path"] = _filter_section_path_for_llm(
        heading_levels=heading_levels,
        section_paths=segment.section_path,
        segment_item_index=segment.slices[0].item_index,
        segment_page_index=segment.slices[0].page_index,
    )

    # NB: Chunk payload should not include full-table derived views that can leak
    # information outside the chunk. NB: We intentionally KEEP rows_filldown here (if
    # present) but slice it down to the decision-row window so it does not expose the
    # entire table.
    for k in ("rows_grid", "grid_sources", "row_provenance"):
        seg.pop(k, None)

    full_rows_raw = seg.get("rows") or []
    full_rows_filldown: list[dict[str, Any]] | None = seg.get("rows_filldown")

    # Context windows (before/after). Clamp ctx_before_start to at least
    # header_row_count so the first chunk does not redundantly include header rows
    # (already visible in header_rows_canonical) as context-only rows.
    header_n = segment.header_row_count or 0
    ctx_before_start = max(header_n, start - max(0, int(context_rows_before or 0)))
    ctx_after_end = min(len(full_rows_raw), end + max(0, int(context_rows_after or 0)))

    context_rows_before_payload: list[dict[str, Any]] = []
    context_rows_after_payload: list[dict[str, Any]] = []

    # Prefer fill-down view (if available) for primary `rows`, because validators
    # ground row-local groupings against visible row text. Computed early so context
    # rows also use the fill-down view for consistency with decision rows.
    use_filldown = (full_rows_filldown is not None) and len(full_rows_filldown) == len(
        full_rows_raw
    )

    # Context rows use fill-down when available so the LLM sees the same filled values
    # as in the decision rows, avoiding misleading blank cells at chunk boundaries.
    ctx_source = full_rows_filldown if use_filldown else full_rows_raw
    assert ctx_source is not None

    for abs_i in range(ctx_before_start, start):
        row = dict(ctx_source[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_before_payload.append(row)

    for abs_i in range(end, ctx_after_end):
        row = dict(ctx_source[abs_i])
        row["abs_row_index"] = abs_i
        row["is_context_only"] = True
        context_rows_after_payload.append(row)

    # Decision rows: raw visual + optional fill-down view.
    decision_rows_raw: list[dict[str, Any]] = []
    decision_rows_payload: list[dict[str, Any]] = []

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


def make_table_full_payload(
    *, heading_levels: dict[str, int], segment: TableSegment
) -> dict[str, Any]:
    """Build a FULL (unchunked) table payload for the LLM.

    This mirrors `make_table_chunk_payload` but includes ALL rows. Critically, it:

    1. Prefers `rows_filldown` (if available) so row-level groupings are grounded
        in-row (raw visual rows are preserved under `rows_original`).
    2. Adds `abs_row_index` to every row so validators can enforce grounding
    3. Adds a lightweight `chunking` object indicating absolute indices

    Parameters
    ----------
    heading_levels
        Mapping from normalized heading text to structural depth level.
    segment
        The TableSegment to process.

    Returns
    -------
    dict[str, Any]
        The full table payload.
    """

    table_payload = segment.model_dump(
        exclude={"segment_id", "segment_provenance", "slices"}, mode="json"
    )
    assert (
        segment.slices
    ), f"Segment {segment.segment_id} has no slices; cannot determine page context."
    table_payload["section_path"] = _filter_section_path_for_llm(
        heading_levels=heading_levels,
        section_paths=segment.section_path,
        segment_item_index=segment.slices[0].item_index,
        segment_page_index=segment.slices[0].page_index,
    )

    # Prefer fill-down view if it exists.
    rows = segment.rows
    assert rows, f"{segment = }"
    rows_filldown = segment.rows_filldown

    use_filldown = isinstance(rows_filldown, list) and len(rows_filldown) == len(rows)

    if use_filldown:
        table_payload["rows_original"] = table_payload["rows"]
        table_payload["rows_original_preserved"] = True

        # Store rows_filldown here before removing.
        table_payload["rows"] = table_payload["rows_filldown"]
    else:
        table_payload["rows_original_preserved"] = False

    # NB: Remove derived structures that bloat the prompt. We intentionally keep the
    # filldown effect by swapping seg["rows"] above.
    for k in ("rows_grid", "rows_filldown", "grid_sources", "row_provenance"):
        table_payload.pop(k, None)

    rows = table_payload.get("rows") or []

    # Add abs_row_index to every row (headers included).
    for abs_i, row in enumerate(rows):
        row["abs_row_index"] = abs_i

    table_payload["rows"] = rows
    table_payload["chunking"] = {
        "is_chunked": False,
        "row_range_start": 0,
        "row_range_end": len(rows),
        "row_range_end_is_exclusive": True,
        "row_index_is_absolute": True,
    }

    return table_payload


def process_segment_decisions(
    *,
    caption_bindings: dict[str, CaptionBinding | None],
    config: CreateCanonicalConfig,
    context_hint: list[dict[str, Any]] | None = None,
    decision_set: SegmentDecisionSet,
    doc_key: str,
    existing_keys: set[tuple[str, Optional[int], Optional[int]]],
    heading_levels: dict[str, int],
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
    heading_levels
        Mapping from normalized heading text to structural depth level.
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
            heading_levels=heading_levels,
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
        heading_levels=heading_levels,
        next_segment_hint=next_segment_hint,
        prev_segment_hint=prev_segment_hint,
        segment=segment,
        segment_decisions_fp=segment_decisions_fp,
        warnings=warnings,
    )


def reconstruct_section_path(
    *, heading_levels: dict[str, int], section_paths: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replay section_path entries as a proper heading stack using assigned levels.

    Algorithm follows a standard HTML heading model:
      - Level 0 -> skip (non-structural).
      - Level N -> pop everything on the stack at level >= N, then push.

    Parameters
    ----------
    heading_levels
        Mapping from normalized heading text to structural depth level.
    section_paths
        A list of section_path dicts (with at least a "text" key).

    Returns
    -------
    list[dict[str, Any]]
        The reconstructed ancestor chain.
    """

    stack: list[tuple[int, dict[str, Any]]] = []  # (level, item)

    for item in section_paths:
        text = item.get("text", "").strip()
        norm = " ".join(text.split()).casefold()
        level = heading_levels.get(norm)

        # Unknown heading (not in map) — keep it to avoid losing data.
        # Level 0 — non-structural; skip.
        if level is None:
            # Treat as deepest level so it doesn't pop anything.
            stack.append((999, item))
            continue

        if level == 0:
            continue

        # Pop everything at same or deeper level.
        while stack and stack[-1][0] >= level:
            stack.pop()

        stack.append((level, item))

    return [item for _, item in stack]


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


def segment_hint(*, heading_levels: dict[str, int], segment: Segment) -> dict[str, Any]:
    """Generate a compact hint dictionary for a segment.

    NB: Keep this SMALL (only things that help context).

    Parameters
    ----------
    heading_levels
        Mapping from normalized heading text to structural depth level.
    segment
        The Segment to generate a hint for.

    Returns
    -------
    dict[str, Any]
        The compact hint dictionary.
    """

    kind = segment.kind
    assert kind in ("block", "table")
    assert (
        segment.slices
    ), f"Segment {segment.segment_id} has no slices; cannot build hint."

    pages: list[int] = [p.page_index for p in segment.segment_provenance]
    page_span = [min(pages), max(pages)] if pages else None

    hint: dict[str, Any] = {
        "kind": kind,
        "local_code": segment.local_code,
        "page_span": page_span,
        "section_path_texts": _section_path_texts(
            heading_levels=heading_levels,
            k=6,
            segment=segment,
            segment_item_index=segment.slices[0].item_index,
            segment_page_index=segment.slices[0].page_index,
        ),
    }

    if kind == "block":
        hint["block_type"] = segment.block_type.value

        # Prefer combined_text if present (stitched blocks), otherwise TextUnit.text.
        combined_text = (segment.combined_text or "").strip()
        text_unit_or_none = segment.text
        text = (
            (text_unit_or_none.text or "").strip()
            if isinstance(text_unit_or_none, TextUnit)
            else ""
        )
        hint["text_preview"] = (combined_text or text)[:500]

        list_items = segment.list_items
        hint["list_item_samples"] = (
            [list_item.text.text[:500] for list_item in list_items[:5]]
            if list_items
            else None
        )

        hint["figure_content"] = segment.figure
    else:
        header_rows_canonical = segment.header_rows_canonical
        header_row_count = segment.header_row_count
        n_cols = segment.n_cols

        # Prefer filldown sample for “topic/subtopic/strand” signals if present.
        rows = segment.rows_filldown or segment.rows
        row_count = len(segment.rows)
        assert rows and row_count, f"{segment = }"

        # Sample 2 body rows immediately after headers.
        body_samples: list[list[str]] = []
        start = min(header_row_count, len(rows))
        for row in rows[start : start + 2]:
            assert isinstance(row, TableRow), f"{rows = }\n{row = }"
            body_samples.append(_row_to_text(max_cols=6, row=row))

        hint["table"] = {
            "columns_signature": segment.columns_signature,
            "header_rows_canonical": header_rows_canonical[:2],
            "header_row_count": header_row_count,
            "n_cols": n_cols,
            "row_count": row_count,
            "body_row_samples": body_samples,
            "has_rows_grid": bool(segment.rows_grid),
            "has_rows_filldown": bool(segment.rows_filldown),
        }

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

    header_n = segment.header_row_count
    total_rows = len(segment.rows)
    body_rows = max(0, total_rows - header_n)

    if body_rows <= max_body_rows:
        return [(None, None)]

    chunks = []
    start = header_n  # Chunk only body rows (skip header rows)

    while start < total_rows:
        end = min(total_rows, start + max_body_rows)
        chunks.append((start, end))
        start = end

    return chunks
