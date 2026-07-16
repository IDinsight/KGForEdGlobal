"""This module contains functionalities for building source-faithful LLM extraction
windows for Academic Standards KG.

The windowing strategy is intentionally simple:

1. Walk stitched DocumentIR segments in source order.
2. Plan one block window for every block segment with extractable source text.
3. Plan table windows only for table segments selected by KG config table rules.
4. Add bounded preceding and following same-page heading context as context-only
    evidence when PDF reading order differs from visual layout.
5. Build LLM-ready payloads that preserve source text, table structure, optional table
    helper views, provenance, code hints, and the later SFI extraction contract.

Python does not decide whether block text is semantically relevant to the standards
hierarchy. The downstream LLM extraction step returns SFI candidates, auxiliary
records, or no candidates for each window.
"""

# Standard Library
import hashlib
import re
import unicodedata
import uuid

from pathlib import Path
from typing import Any, Literal, Optional, Sequence

# Third Party Library
from pydantic import BaseModel

# Package Library
from skg.document_ir.schemas import BlockSegment, DocumentIR, TableSegment
from skg.kgs.schemas import (
    CodeMatch,
    CodeParentHint,
    ExtractionWindow,
    ExtractionWindowContextEvidence,
    ExtractionWindowPlanArtifact,
    ExtractionWindowPlanItem,
    ExtractionWindowTablePayload,
)
from skg.kgs.utils import (
    ConfiguredCodeMatchSourceUnit,
    compile_code_patterns,
    extract_block_source_text,
    find_configured_code_matches_in_source_units,
    get_table_selection_reasons,
)
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, write_to_json

_MAX_NEIGHBOR_HEADING_CONTEXT_PER_DIRECTION = 2


def _build_block_windows(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    plan_item: ExtractionWindowPlanItem,
    segment: BlockSegment,
    window_start_index: int,
) -> list[ExtractionWindow]:
    """Build one extraction window for a planned block segment.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    kg_config
        Country/document-specific KG extraction configuration.
    plan_item
        Planned source unit for this block segment.
    segment
        Planned block segment.
    window_start_index
        Index to assign to the produced window.

    Returns
    -------
    list[ExtractionWindow]
        One block extraction window, or an empty list if the block source text is empty.
    """

    block_payload = segment.model_dump(mode="json")
    source_text = extract_block_source_text(block_payload)
    return (
        []
        if not source_text
        else [
            _build_extraction_window(
                block=block_payload,
                code_match_source_units=[
                    ConfiguredCodeMatchSourceUnit(start_char=0, text=source_text)
                ],
                document_ir=document_ir,
                kg_config=kg_config,
                plan_item=plan_item,
                row_range_label=None,
                segment_kind="block",
                source_provenance=_model_dump_list(segment.segment_provenance),
                source_section_path=_model_dump_list(segment.section_path),
                source_segment_ids=[segment.segment_id],
                source_text=source_text,
                table=None,
                window_index=window_start_index,
                window_notes=["block_window_full_segment_source"],
            )
        ]
    )


def _build_context_evidence_key(
    context_evidence: Sequence[ExtractionWindowContextEvidence],
) -> str:
    """Build a stable source-context key component from neighboring headings.

    Parameters
    ----------
    context_evidence
        Ordered neighboring context evidence records.

    Returns
    -------
    str
        Stable context string containing direction, source position, pages, segment ID,
        and normalized visible text.
    """

    key_parts: list[str] = []

    for context in context_evidence:
        normalized_text = unicodedata.normalize("NFKC", context.source_text).casefold()
        normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
        page_key = ",".join(
            str(page_index) for page_index in context.source_page_indexes
        )
        key_parts.append(
            f"{context.context_direction}:{context.document_segment_index}:"
            f"{page_key}:{context.source_segment_id}:{normalized_text}"
        )

    return ">".join(key_parts)


def _build_extraction_window(
    *,
    block: Optional[dict[str, Any]],
    code_match_source_units: Sequence[ConfiguredCodeMatchSourceUnit],
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    plan_item: ExtractionWindowPlanItem,
    row_range_label: Optional[str],
    segment_kind: str,
    source_provenance: list[dict[str, Any]],
    source_section_path: list[dict[str, Any]],
    source_segment_ids: list[str],
    source_text: str,
    table: Optional[ExtractionWindowTablePayload],
    window_index: int,
    window_notes: list[str],
) -> ExtractionWindow:
    """Assemble and validate a shared extraction-window payload.

    Parameters
    ----------
    block
        Optional block payload for block windows.
    code_match_source_units
        Atomic source-text units whose offsets reference `source_text`. Configured code
        matching is confined to each unit independently.
    document_ir
        Validated stitched DocumentIR.
    kg_config
        Country/document-specific KG extraction configuration.
    plan_item
        Planned source unit that produced this window.
    row_range_label
        Optional table row-range label used in deterministic keys.
    segment_kind
        Source segment kind for the window.
    source_provenance
        Source provenance records.
    source_section_path
        Source DocumentIR section-path references preserved in path order.
    source_segment_ids
        Source DocumentIR segment IDs included in the window.
    source_text
        Human-readable source text for prompt review.
    table
        Optional table payload for table windows.
    window_index
        0-based window index.
    window_notes
        Implementation/debug notes.

    Returns
    -------
    ExtractionWindow
        Validated extraction window.
    """

    code_matches = [
        CodeMatch(
            code_type=match.code_type,
            end_char=match.end_char,
            normalized_value=match.normalized_value,
            raw_value=match.raw_value,
            start_char=match.start_char,
        )
        for match in find_configured_code_matches_in_source_units(
            code_patterns=kg_config.academic_standards.code_patterns,
            source_units=code_match_source_units,
        )
    ]
    code_parent_hints = _collect_code_parent_hints(
        code_matches=code_matches, kg_config=kg_config
    )
    target_page_indexes = _resolve_window_source_page_indexes(
        source_provenance=source_provenance, table=table
    )
    source_context_before, source_context_after = (
        _build_neighbor_heading_context_evidence(
            document_ir=document_ir,
            source_segment_id=plan_item.segment_id,
            target_page_indexes=target_page_indexes,
        )
    )
    canonical_context = "|".join(
        [
            document_ir.doc_key,
            plan_item.segment_kind,
            plan_item.segment_id,
            _build_source_section_path_key(source_section_path),
            _build_context_evidence_key(source_context_before),
            _build_context_evidence_key(source_context_after),
            row_range_label or "",
            re.sub(r"\s+", " ", source_text or "").strip().casefold(),
        ]
    )
    window_id = _deterministic_uuid(
        f"lc:curriculum:{document_ir.doc_key}:extraction_window:{canonical_context}"
    )
    return ExtractionWindow(
        block=block,
        code_matches=code_matches,
        code_parent_hints=code_parent_hints,
        deterministic_hints={
            "bilingual_pair_policy": kg_config.academic_standards.bilingual_pair_policy,
            "code_parent_rules": kg_config.academic_standards.code_parent_rules,
            "code_patterns": kg_config.academic_standards.code_patterns,
            "country": kg_config.metadata.country,
            "no_code_policy": (
                "statement_code is optional. When no official code is visible, later "
                "candidate merge/ID steps must use source-derived keys and source text, "
                "not LLM paraphrases."
            ),
            "plan_reasons": plan_item.plan_reasons,
            "source_context_key": hashlib.sha256(
                canonical_context.encode("utf-8")
            ).hexdigest()[:32],
            "subject": kg_config.metadata.subject,
            "synthetic_merge_key_fields": kg_config.academic_standards.synthetic_merge_key_fields,
        },
        doc_key=document_ir.doc_key,
        framework_title=kg_config.metadata.framework_title,
        kg_extraction_instructions=kg_config.academic_standards.sfi_extraction_instructions,
        pdf_name=document_ir.pdf_name,
        primary_language=kg_config.metadata.primary_language,
        segment_kind=segment_kind,
        source_context_after=source_context_after,
        source_context_before=source_context_before,
        source_provenance=source_provenance,
        source_section_path=source_section_path,
        source_segment_ids=source_segment_ids,
        source_text=source_text,
        subject=kg_config.metadata.subject,
        table=table,
        window_id=window_id,
        window_index=window_index,
        window_notes=[
            *window_notes,
            *(
                ["same_page_neighbor_heading_context_included"]
                if source_context_before or source_context_after
                else []
            ),
        ],
    )


def _build_neighbor_heading_context_evidence(
    *,
    document_ir: DocumentIR,
    source_segment_id: str,
    target_page_indexes: Sequence[int],
) -> tuple[
    list[ExtractionWindowContextEvidence], list[ExtractionWindowContextEvidence]
]:
    """Build bounded preceding and following same-page heading context.

    The function exposes nearby source-visible headings without treating them as part
    of the target extraction source. Only block segments typed as headings are
    eligible. Context is restricted to headings sharing at least one source page with
    the target segment, and the nearest bounded headings are retained in document
    source order.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR containing the target and neighboring segments.
    source_segment_id
        Segment ID of the target extraction source unit.
    target_page_indexes
        Source pages represented by the specific extraction window. For table chunks,
        these should come from the selected rows rather than the whole stitched table.

    Returns
    -------
    tuple[list[ExtractionWindowContextEvidence], list[ExtractionWindowContextEvidence]]
        Preceding and following context evidence lists, each in document source order.

    Raises
    ------
    ValueError
        If the target segment ID is missing from DocumentIR or the target window has no
        source pages.
    """

    target_segment_index = next(
        (
            segment_index
            for segment_index, segment in enumerate(document_ir.segments)
            if segment.segment_id == source_segment_id
        ),
        None,
    )

    if target_segment_index is None:
        raise ValueError(
            f"Could not build neighboring context for unknown segment_id: "
            f"{source_segment_id!r}."
        )

    target_page_index_set = {int(page_index) for page_index in target_page_indexes}

    if not target_page_index_set:
        raise ValueError(
            f"Could not build neighboring context without target source pages for "
            f"segment_id={source_segment_id!r}."
        )

    def _collect_context(
        *,
        context_direction: Literal["following", "preceding"],
        segment_indexes: Sequence[int],
    ) -> list[ExtractionWindowContextEvidence]:
        """Collect one direction of eligible neighboring heading context.

        Parameters
        ----------
        context_direction
            Whether the scanned segments precede or follow the target.
        segment_indexes
            DocumentIR segment indexes to inspect in nearest-first order.

        Returns
        -------
        list[ExtractionWindowContextEvidence]
            Bounded context records in nearest-first scan order.
        """

        context_evidence: list[ExtractionWindowContextEvidence] = []

        for document_segment_index in segment_indexes:
            context_segment = document_ir.segments[document_segment_index]

            if context_segment.kind != "block":
                continue

            if context_segment.block_type.value != "heading":
                continue

            context_page_indexes = sorted(
                {
                    int(provenance.page_index)
                    for provenance in context_segment.segment_provenance
                }
            )

            if not target_page_index_set.intersection(context_page_indexes):
                continue

            context_source_text = extract_block_source_text(
                context_segment.model_dump(mode="json")
            )

            if not context_source_text:
                continue

            context_evidence.append(
                ExtractionWindowContextEvidence(
                    block_type=context_segment.block_type.value,
                    context_direction=context_direction,
                    document_segment_index=document_segment_index,
                    source_page_indexes=context_page_indexes,
                    source_segment_id=context_segment.segment_id,
                    source_text=context_source_text,
                )
            )

            if len(context_evidence) >= _MAX_NEIGHBOR_HEADING_CONTEXT_PER_DIRECTION:
                break

        return context_evidence

    preceding_context = _collect_context(
        context_direction="preceding",
        segment_indexes=range(target_segment_index - 1, -1, -1),
    )
    following_context = _collect_context(
        context_direction="following",
        segment_indexes=range(target_segment_index + 1, len(document_ir.segments)),
    )
    preceding_context.reverse()
    return preceding_context, following_context


def _build_source_section_path_key(
    source_section_path: Sequence[dict[str, Any]],
) -> str:
    """Build a stable source-context key component from section-path references.

    Parameters
    ----------
    source_section_path
        Serialized DocumentIR section-heading references in path order.

    Returns
    -------
    str
        Stable path string containing source positions and normalized visible text.
    """

    key_parts: list[str] = []

    for section_ref in source_section_path:
        item_index = section_ref.get("item_index", "")
        page_index = section_ref.get("page_index", "")
        text = unicodedata.normalize(
            "NFKC", str(section_ref.get("text") or "")
        ).casefold()
        text = re.sub(r"\s+", " ", text).strip()
        key_parts.append(f"{page_index}:{item_index}:{text}")

    return ">".join(key_parts)


def _build_table_source_text_and_code_match_units(
    *, rows: list[dict[str, Any]], table_payload: ExtractionWindowTablePayload
) -> tuple[str, list[ConfiguredCodeMatchSourceUnit]]:
    """Build table source text and cell-bounded configured-code matching units.

    Raw header and selected body cells are rendered exactly as before: tabs separate
    cells, newlines separate nonempty rows, and outer whitespace is stripped. Each
    nonempty raw cell is also recorded as one atomic matching unit with an offset into
    the rendered source text. Configured regexes can therefore match flexible
    whitespace inside a cell but cannot manufacture a code across cells or rows.

    Parameters
    ----------
    rows
        Selected raw source body rows represented as dictionaries.
    table_payload
        Table payload containing raw headers and selected body-row metadata.

    Returns
    -------
    tuple[str, list[ConfiguredCodeMatchSourceUnit]]
        Rendered source-visible table text and ordered cell-level matching units whose
        offsets reference that text.

    Raises
    ------
    ValueError
        If a calculated cell offset does not map back to the exact rendered cell text.
    """

    source_rows = [*table_payload.header_rows, *rows]
    raw_source_parts: list[str] = []
    raw_source_units: list[ConfiguredCodeMatchSourceUnit] = []
    raw_source_length = 0
    rendered_row_count = 0

    for row in source_rows:
        cell_texts = _extract_table_row_cell_texts(row)

        if not any(cell_texts):
            continue

        if rendered_row_count:
            raw_source_parts.append("\n")
            raw_source_length += 1

        for cell_index, cell_text in enumerate(cell_texts):
            if cell_index:
                raw_source_parts.append("\t")
                raw_source_length += 1

            if cell_text:
                raw_source_units.append(
                    ConfiguredCodeMatchSourceUnit(
                        start_char=raw_source_length, text=cell_text
                    )
                )

            raw_source_parts.append(cell_text)
            raw_source_length += len(cell_text)

        rendered_row_count += 1

    raw_source_text = "".join(raw_source_parts)
    leading_trim_length = len(raw_source_text) - len(raw_source_text.lstrip())
    source_text = raw_source_text.strip()
    source_units = [
        ConfiguredCodeMatchSourceUnit(
            start_char=source_unit.start_char - leading_trim_length,
            text=source_unit.text,
        )
        for source_unit in raw_source_units
    ]

    for source_unit in source_units:
        unit_end_char = source_unit.start_char + len(source_unit.text)

        if source_text[source_unit.start_char : unit_end_char] != source_unit.text:
            raise ValueError(
                "Table cell code-match unit does not align with rendered source_text."
            )

    return source_text, source_units


def _build_table_window_for_row_indexes(
    *,
    body_row_indexes: Sequence[int],
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    plan_item: ExtractionWindowPlanItem,
    segment: TableSegment,
    window_index: int,
) -> ExtractionWindow:
    """Build one table extraction window for selected source body rows.

    An empty `body_row_indexes` sequence creates a header-only extraction window. This
    preserves a planned table whose source-visible content exists only in its headers.

    Parameters
    ----------
    body_row_indexes
        Contiguous source table body-row indexes to include. May be empty only for a
        header-only table window.
    document_ir
        Validated stitched DocumentIR.
    kg_config
        Country/document-specific KG extraction configuration.
    plan_item
        Planned source unit for this table segment.
    segment
        Selected table segment.
    window_index
        0-based window index.

    Returns
    -------
    ExtractionWindow
        Validated table extraction window.
    """

    row_indexes = list(body_row_indexes)

    if row_indexes:
        body_row_start_index = row_indexes[0]
        body_row_end_index_exclusive = row_indexes[-1] + 1
        row_range_label = "rows:" + ",".join(str(index) for index in row_indexes)
        window_notes = ["table_window_uses_optional_helpers_when_present"]
    else:
        body_row_start_index = segment.header_row_count
        body_row_end_index_exclusive = segment.header_row_count
        row_range_label = "header-only"
        window_notes = [
            "table_window_header_only",
            "table_window_uses_optional_helpers_when_present",
        ]

    rows = _model_dump_by_indexes(indexes=row_indexes, values=segment.rows)
    rows_grid = _optional_model_dump_by_indexes(
        indexes=row_indexes, values=segment.rows_grid
    )
    rows_filldown = _optional_model_dump_by_indexes(
        indexes=row_indexes, values=segment.rows_filldown
    )
    row_provenance = _optional_model_dump_by_indexes(
        indexes=row_indexes, values=segment.row_provenance
    )
    grid_sources = _optional_list_by_indexes(
        indexes=row_indexes, values=segment.grid_sources
    )
    table_payload = ExtractionWindowTablePayload(
        body_row_end_index_exclusive=body_row_end_index_exclusive,
        body_row_start_index=body_row_start_index,
        columns_signature=segment.columns_signature,
        grid_sources=grid_sources,
        header_row_count=segment.header_row_count,
        header_rows=_model_dump_list(segment.header_rows),
        header_rows_canonical=segment.header_rows_canonical,
        local_code=segment.local_code,
        n_cols=segment.n_cols,
        row_indexes=row_indexes,
        row_provenance=row_provenance,
        rows=rows,
        rows_filldown=rows_filldown,
        rows_grid=rows_grid,
        source_table_row_count=len(segment.rows),
    )
    source_text, code_match_source_units = (
        _build_table_source_text_and_code_match_units(
            rows=rows, table_payload=table_payload
        )
    )
    return _build_extraction_window(
        block=None,
        code_match_source_units=code_match_source_units,
        document_ir=document_ir,
        kg_config=kg_config,
        plan_item=plan_item,
        row_range_label=row_range_label,
        segment_kind="table",
        source_provenance=_model_dump_list(segment.segment_provenance),
        source_section_path=_model_dump_list(segment.section_path),
        source_segment_ids=[segment.segment_id],
        source_text=source_text,
        table=table_payload,
        window_index=window_index,
        window_notes=window_notes,
    )


def _build_table_windows(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    plan_item: ExtractionWindowPlanItem,
    segment: TableSegment,
    window_start_index: int,
) -> list[ExtractionWindow]:
    """Build table windows from a planned table segment.

    The stitched `TableSegment.rows` sequence is authoritative. Header handling belongs
    to DocumentIR stitching, so this function chunks every row at or after
    `header_row_count` without applying a second repeated-header heuristic.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    kg_config
        Country/document-specific KG extraction configuration.
    plan_item
        Planned source unit for this table segment.
    segment
        Selected table segment.
    window_start_index
        Index to assign to the first produced window.

    Returns
    -------
    list[ExtractionWindow]
        Table extraction windows with exact body-row coverage, or one header-only
        window when the table has no body rows.
    """

    body_row_indexes = list(range(segment.header_row_count, len(segment.rows)))
    row_index_chunks = (
        _iter_row_index_chunks(
            max_rows_per_window=kg_config.academic_standards.max_rows_per_table_window,
            row_indexes=body_row_indexes,
            row_overlap=kg_config.academic_standards.row_overlap,
        )
        if body_row_indexes
        else [[]]
    )
    windows = [
        _build_table_window_for_row_indexes(
            body_row_indexes=row_chunk_indexes,
            document_ir=document_ir,
            kg_config=kg_config,
            plan_item=plan_item,
            segment=segment,
            window_index=window_start_index + chunk_index,
        )
        for chunk_index, row_chunk_indexes in enumerate(row_index_chunks)
    ]
    _validate_table_window_coverage(segment=segment, windows=windows)
    return windows


def _collect_code_parent_hints(
    *, code_matches: Sequence[CodeMatch], kg_config: CreateKGConfig
) -> list[CodeParentHint]:
    """Collect case-insensitive code-parent hints from substitution rules.

    Code patterns and parent-rule regexes follow the universal case-insensitive code
    interpretation used throughout KG preflight, extraction-window construction, and
    candidate-code resolution. Derived parent codes are retained only when they fully
    match the configured parent code pattern.

    Parameters
    ----------
    code_matches
        Code matches found in the window.
    kg_config
        Country/document-specific KG extraction configuration.

    Returns
    -------
    list[CodeParentHint]
        Ordered unique parent-code hints whose derived parent code validates against
        the configured parent code pattern.
    """

    compiled_patterns = compile_code_patterns(
        kg_config.academic_standards.code_patterns
    )
    hints: list[CodeParentHint] = []

    for code_match in code_matches:
        child_code = code_match.normalized_value

        for rule in kg_config.academic_standards.code_parent_rules:
            if code_match.code_type != rule["child"]:
                continue

            # Derive parent code via regex substitution.
            parent_code = re.sub(
                flags=re.IGNORECASE,
                pattern=rule["regex"],
                repl=rule["replacement"],
                string=child_code,
            ).strip()

            # Ensure the substitution produced a distinct, non-empty code.
            if not parent_code or parent_code == child_code:
                continue

            parent_code_type = rule["parent"]
            parent_pattern = compiled_patterns[parent_code_type]

            # Ensure the derived parent code matches the configured pattern.
            if parent_pattern.fullmatch(parent_code) is None:
                continue

            hints.append(
                CodeParentHint(
                    child_code=child_code,
                    child_code_type=code_match.code_type,
                    method=rule["method"],
                    parent_code=parent_code,
                    parent_code_type=parent_code_type,
                )
            )

    return _dedupe_code_parent_hints(hints)


def _dedupe_code_parent_hints(
    code_parent_hints: Sequence[CodeParentHint],
) -> list[CodeParentHint]:
    """Dedupe code-parent hints while preserving order.

    Parameters
    ----------
    code_parent_hints
        Code-parent hints.

    Returns
    -------
    list[CodeParentHint]
        Deduped code-parent hints.
    """

    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[CodeParentHint] = []

    for hint in code_parent_hints:
        key = (
            hint.child_code,
            hint.child_code_type,
            hint.parent_code,
            hint.parent_code_type,
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(hint)

    return deduped


def _deterministic_uuid(canonical_string: str) -> str:
    """Create a deterministic UUIDv5 string from a canonical string.

    Parameters
    ----------
    canonical_string
        Stable canonical identity string.

    Returns
    -------
    str
        UUIDv5 string.
    """

    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_string))


def _extract_table_row_cell_texts(row: dict[str, Any]) -> list[str]:
    """Extract visible cell text from a serialized table row.

    Parameters
    ----------
    row
        Serialized table row containing a `cells` list.

    Returns
    -------
    list[str]
        Cell texts in source order, preserving empty cells.
    """

    cell_texts: list[str] = []

    for cell in row.get("cells") or []:
        if not isinstance(cell, dict):
            cell_texts.append("")
            continue

        text_unit = cell.get("text")

        if isinstance(text_unit, dict):
            cell_text = str(text_unit.get("text") or "").strip()
        elif text_unit is not None:
            cell_text = str(text_unit).strip()
        else:
            cell_text = ""

        cell_texts.append(cell_text)

    return cell_texts


def _iter_row_index_chunks(
    *,
    max_rows_per_window: Optional[int],
    row_indexes: Sequence[int],
    row_overlap: int,
) -> list[list[int]]:
    """Return body-row index chunks for table extraction windows.

    Parameters
    ----------
    max_rows_per_window
        Maximum number of body rows per window. If None, emit one whole-table chunk.
    row_indexes
        Ordered source table body-row indexes eligible for extraction.
    row_overlap
        Number of overlapping body rows between adjacent chunks.

    Returns
    -------
    list[list[int]]
        Ordered chunks of source table row indexes.

    Raises
    ------
    ValueError
        If row-windowing parameters are invalid.
    """

    row_indexes_list = list(row_indexes)

    if not row_indexes_list:
        return []

    if max_rows_per_window is None:
        return [row_indexes_list]

    if max_rows_per_window <= 0:
        raise ValueError("max_rows_per_window must be positive or None.")

    if row_overlap < 0:
        raise ValueError("overlap must be non-negative.")

    if row_overlap >= max_rows_per_window:
        raise ValueError("overlap must be smaller than max_rows_per_window.")

    chunks: list[list[int]] = []
    current_start_position = 0

    while current_start_position < len(row_indexes_list):
        current_end_position = min(
            current_start_position + max_rows_per_window, len(row_indexes_list)
        )
        chunks.append(row_indexes_list[current_start_position:current_end_position])

        if current_end_position >= len(row_indexes_list):
            break

        current_start_position = current_end_position - row_overlap

    return chunks


def _model_dump_by_indexes(
    *, indexes: Sequence[int], values: Sequence[Any]
) -> list[dict[str, Any]]:
    """Serialize selected values by index.

    Parameters
    ----------
    indexes
        Indexes to select.
    values
        Sequence of values.

    Returns
    -------
    list[dict[str, Any]]
        Serialized selected values.
    """

    return [values[index].model_dump(mode="json") for index in indexes]


def _model_dump_list(values: Sequence[BaseModel]) -> list[dict[str, Any]]:
    """Serialize a sequence of Pydantic models.

    Parameters
    ----------
    values
        Values to serialize.

    Returns
    -------
    list[dict[str, Any]]
        Serialized dictionaries.
    """

    return [value.model_dump(mode="json") for value in values]


def _optional_list_by_indexes(
    *, indexes: Sequence[int], values: Optional[Sequence[Any]]
) -> Optional[list[Any]]:
    """Select optional list values by index.

    Parameters
    ----------
    indexes
        Indexes to select.
    values
        Optional sequence of values.

    Returns
    -------
    Optional[list[Any]]
        Selected values, or None when the source helper view is missing or unaligned.
    """

    if values is None or max(indexes, default=-1) >= len(values):
        return None

    return [values[index] for index in indexes]


def _optional_model_dump_by_indexes(
    *, indexes: Sequence[int], values: Optional[Sequence[Any]]
) -> Optional[list[dict[str, Any]]]:
    """Serialize optional values by index.

    Parameters
    ----------
    indexes
        Indexes to select.
    values
        Optional sequence of values.

    Returns
    -------
    Optional[list[dict[str, Any]]]
        Serialized selected values, or None when the helper view is missing or
        unaligned.
    """

    if values is None or max(indexes, default=-1) >= len(values):
        return None

    return _model_dump_by_indexes(indexes=indexes, values=values)


def _resolve_window_source_page_indexes(
    *,
    source_provenance: Sequence[dict[str, Any]],
    table: Optional[ExtractionWindowTablePayload],
) -> list[int]:
    """Resolve source pages represented by one extraction window.

    Table segments may span several pages while one extraction window contains only a
    subset of body rows. When aligned row provenance is available, the selected rows'
    pages are authoritative for neighboring context selection. Block windows and table
    windows without aligned row provenance fall back to segment-level provenance.

    Parameters
    ----------
    source_provenance
        Serialized segment-level provenance records.
    table
        Optional table payload for the current extraction window.

    Returns
    -------
    list[int]
        Sorted unique 0-based source page indexes represented by the window.
    """

    row_page_indexes: set[int] = set()

    if table is not None:
        row_page_indexes = {
            int(row_provenance["page_index"])
            for row_provenance in (table.row_provenance or [])
            if "page_index" in row_provenance
        }

    if row_page_indexes:
        return sorted(row_page_indexes)

    return sorted(
        {
            int(provenance["page_index"])
            for provenance in source_provenance
            if "page_index" in provenance
        }
    )


def _validate_extraction_window_coverage(
    *,
    extraction_windows: Sequence[ExtractionWindow],
    plan_items: Sequence[ExtractionWindowPlanItem],
) -> None:
    """Validate planned-segment coverage and contiguous global window indexes.

    Parameters
    ----------
    extraction_windows
        Built extraction windows in persisted order.
    plan_items
        Planned source units expected to produce one or more windows.

    Raises
    ------
    ValueError
        If window indexes are non-contiguous, a planned segment is uncovered, or a
        window references an unplanned segment.
    """

    expected_window_indexes = list(range(len(extraction_windows)))
    actual_window_indexes = [window.window_index for window in extraction_windows]

    if actual_window_indexes != expected_window_indexes:
        raise ValueError(
            "Extraction window indexes must be contiguous and match persisted order."
        )

    planned_segment_ids = [plan_item.segment_id for plan_item in plan_items]
    planned_segment_id_set = set(planned_segment_ids)

    if len(planned_segment_id_set) != len(planned_segment_ids):
        raise ValueError("Extraction window plan contains duplicate segment_id values.")

    covered_segment_ids = {
        segment_id
        for window in extraction_windows
        for segment_id in window.source_segment_ids
    }
    missing_segment_ids = sorted(planned_segment_id_set - covered_segment_ids)
    unexpected_segment_ids = sorted(covered_segment_ids - planned_segment_id_set)

    if missing_segment_ids or unexpected_segment_ids:
        raise ValueError(
            f"Extraction window planned-segment coverage mismatch: "
            f"missing={missing_segment_ids}; unexpected={unexpected_segment_ids}."
        )


def _validate_table_window_coverage(
    *, segment: TableSegment, windows: Sequence[ExtractionWindow]
) -> None:
    """Validate exact authoritative body-row coverage for one table segment.

    Overlap between adjacent windows is allowed. Every stitched body row must appear in
    at least one window, no header or out-of-range row may appear, and a header-only
    table must still produce one inspectable window.

    Parameters
    ----------
    segment
        Source TableSegment whose stitched rows are authoritative.
    windows
        Extraction windows built for the table segment.

    Raises
    ------
    ValueError
        If no window is produced or body-row coverage is incomplete or invalid.
    """

    if not windows:
        raise ValueError(
            f"Planned table segment produced no extraction windows: {segment.segment_id}."
        )

    expected_row_indexes = set(range(segment.header_row_count, len(segment.rows)))
    covered_row_indexes: set[int] = set()

    for window in windows:
        if window.segment_kind != "table" or window.table is None:
            raise ValueError(
                f"Table segment {segment.segment_id} produced a non-table window."
            )

        if window.source_segment_ids != [segment.segment_id]:
            raise ValueError(
                f"Table window source_segment_ids must equal [{segment.segment_id!r}]."
            )

        window_row_indexes = window.table.row_indexes

        if expected_row_indexes and not window_row_indexes:
            raise ValueError(
                f"Table segment {segment.segment_id} produced an empty body-row "
                f"window despite having authoritative body rows."
            )

        unexpected_row_indexes = sorted(set(window_row_indexes) - expected_row_indexes)

        if unexpected_row_indexes:
            raise ValueError(
                f"Table window for segment {segment.segment_id} references header or "
                f"out-of-range rows: {unexpected_row_indexes}."
            )

        covered_row_indexes.update(window_row_indexes)

    missing_row_indexes = sorted(expected_row_indexes - covered_row_indexes)

    if missing_row_indexes:
        raise ValueError(
            f"Table windows for segment {segment.segment_id} do not cover body rows: "
            f"{missing_row_indexes}."
        )

    if not expected_row_indexes and (
        len(windows) != 1 or windows[0].table is None or windows[0].table.row_indexes
    ):
        raise ValueError(
            f"Header-only table segment {segment.segment_id} must produce exactly one "
            f"window with no body row indexes."
        )


def build_llm_extraction_windows(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    plan_items: Sequence[ExtractionWindowPlanItem],
    save_fp: Path,
) -> list[ExtractionWindow]:
    """Build LLM-ready Academic Standards extraction windows.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    kg_config
        Country/document-specific KG extraction configuration.
    plan_items
        Ordered planned source units from `plan_extraction_windows()`.
    save_fp
        Filepath for saving extraction windows.

    Returns
    -------
    list[ExtractionWindow]
        LLM-ready extraction windows in deterministic document/window order.

    Raises
    ------
    ValueError
        If a planned segment is missing, a planned source unit produces no window, or
        extraction-window coverage is inconsistent.
    """

    segments_by_id = {segment.segment_id: segment for segment in document_ir.segments}
    extraction_windows: list[ExtractionWindow] = []

    for plan_item in plan_items:
        segment = segments_by_id.get(plan_item.segment_id)

        if segment is None:
            raise ValueError(
                f"Planned segment_id not found in DocumentIR: {plan_item.segment_id}"
            )

        if segment.kind == "block":
            planned_windows = _build_block_windows(
                document_ir=document_ir,
                kg_config=kg_config,
                plan_item=plan_item,
                segment=segment,
                window_start_index=len(extraction_windows),
            )
        elif segment.kind == "table":
            planned_windows = _build_table_windows(
                document_ir=document_ir,
                kg_config=kg_config,
                plan_item=plan_item,
                segment=segment,
                window_start_index=len(extraction_windows),
            )
        else:
            raise ValueError(f"Unrecognized planned segment kind: {segment.kind}")

        if not planned_windows:
            raise ValueError(
                f"Planned source unit produced no extraction windows: "
                f"segment_id={plan_item.segment_id!r}."
            )

        extraction_windows.extend(planned_windows)

    if not extraction_windows:
        raise ValueError("No extraction windows were produced.")

    _validate_extraction_window_coverage(
        extraction_windows=extraction_windows, plan_items=plan_items
    )
    write_extraction_windows(extraction_windows=extraction_windows, save_fp=save_fp)
    return extraction_windows


def plan_extraction_windows(
    *, document_ir: DocumentIR, kg_config: CreateKGConfig, save_fp: Path
) -> list[ExtractionWindowPlanItem]:
    """Plan DocumentIR source units that should become extraction windows.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    kg_config
        Country/document-specific KG extraction configuration.
    save_fp
        Filepath for saving the extraction-window plan artifact.

    Returns
    -------
    list[ExtractionWindowPlanItem]
        Ordered planned source units.

    Raises
    ------
    ValueError
        If no source units are planned.
    """

    plan_items: list[ExtractionWindowPlanItem] = []

    for segment in document_ir.segments:
        if segment.kind == "block":
            source_text = extract_block_source_text(segment.model_dump(mode="json"))

            if not source_text:
                continue

            plan_reasons = ["block_has_extractable_source_text"]
        elif segment.kind == "table":
            plan_reasons = get_table_selection_reasons(
                kg_config=kg_config, segment=segment
            )

            if not plan_reasons:
                continue
        else:
            continue

        plan_items.append(
            ExtractionWindowPlanItem(
                block_type=(
                    segment.block_type.value if segment.kind == "block" else None
                ),
                columns_signature=getattr(segment, "columns_signature", None),
                local_code=segment.local_code,
                plan_id=_deterministic_uuid(
                    f"lc:curriculum:{document_ir.doc_key}:extraction_window_plan:{segment.kind}:{segment.segment_id}"
                ),
                plan_index=len(plan_items),
                plan_reasons=plan_reasons,
                row_count=len(segment.rows) if segment.kind == "table" else None,
                segment_id=segment.segment_id,
                segment_kind=segment.kind,
                source_page_indexes=sorted(
                    {
                        int(provenance.page_index)
                        for provenance in segment.segment_provenance
                    }
                ),
            )
        )

    if not plan_items:
        raise ValueError("No extraction window source units were planned.")

    write_extraction_window_plan(plan_items=plan_items, save_fp=save_fp)
    return plan_items


def write_extraction_window_plan(
    *, plan_items: Sequence[ExtractionWindowPlanItem], save_fp: Path
) -> Path:
    """Write the extraction-window source plan to a JSON artifact.

    Parameters
    ----------
    plan_items
        Ordered planned source units.
    save_fp
        Destination JSON path.

    Returns
    -------
    Path
        The written JSON path.
    """

    counts_by_reason: dict[str, int] = {}
    counts_by_segment_kind: dict[str, int] = {}

    for plan_item in plan_items:
        counts_by_segment_kind[plan_item.segment_kind] = (
            counts_by_segment_kind.get(plan_item.segment_kind, 0) + 1
        )

        for reason in plan_item.plan_reasons:
            counts_by_reason[reason] = counts_by_reason.get(reason, 0) + 1

    artifact = ExtractionWindowPlanArtifact(
        counts_by_reason=dict(sorted(counts_by_reason.items())),
        counts_by_segment_kind=dict(sorted(counts_by_segment_kind.items())),
        plan_items=list(plan_items),
        total_plan_items=len(plan_items),
    )
    make_dir(save_fp.parent)
    write_to_json(fp=save_fp, json_info=artifact)
    return save_fp


def write_extraction_windows(
    *, extraction_windows: Sequence[ExtractionWindow], save_fp: Path
) -> Path:
    """Write extraction windows to a JSONL artifact.

    Parameters
    ----------
    extraction_windows
        Extraction windows to persist.
    save_fp
        Destination JSONL path.

    Returns
    -------
    Path
        The written JSONL path.
    """

    make_dir(save_fp.parent)
    write_to_json(fp=save_fp, json_info=list(extraction_windows))
    return save_fp
