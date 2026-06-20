"""This module contains functionalities for building source-faithful LLM extraction
windows for Academic Standards KG.

The windowing strategy is intentionally simple:

1. Walk stitched DocumentIR segments in source order.
2. Plan one block window for every block segment with extractable source text.
3. Plan table windows only for table segments selected by DocumentProfile table rules.
4. Build LLM-ready payloads that preserve source text, table structure, optional table
    helper views, provenance, code hints, and the later SFI extraction contract.

Python does not decide whether block text is semantically relevant to the standards
hierarchy. The downstream LLM extraction step returns SFI candidates, auxiliary
records, or no candidates for each window.
"""

# Standard Library
import hashlib
import re
import uuid

from pathlib import Path
from typing import Any, Optional, Sequence

# Package Library
from skg.document_ir.schemas import BlockSegment, DocumentIR, TableSegment
from skg.kgs.schemas import (
    CodeMatch,
    CodeParentHint,
    DocumentProfile,
    ExtractionWindow,
    ExtractionWindowPlanArtifact,
    ExtractionWindowPlanItem,
    ExtractionWindowTablePayload,
    unique_clean_strings,
)
from skg.utils.general import make_dir, write_to_json


def _build_block_source_text(block_payload: dict[str, Any]) -> str:
    """Build source text for a block extraction window.

    Parameters
    ----------
    block_payload
        JSON-serializable block segment payload.

    Returns
    -------
    str
        Source text for the block window, or an empty string when the block has no
        useful prompt text.
    """

    if text := block_payload.get("combined_text"):
        return str(text).strip()

    text_unit = block_payload.get("text")

    if isinstance(text_unit, dict) and text_unit.get("text"):
        return str(text_unit["text"]).strip()

    return _extract_list_items_text(
        block_payload.get("list_items") or []
    ) or _extract_figure_text(block_payload)


def _build_block_windows(
    *,
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    plan_item: ExtractionWindowPlanItem,
    segment: BlockSegment,
    window_start_index: int,
) -> list[ExtractionWindow]:
    """Build one extraction window for a planned block segment.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
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
    source_text = _build_block_source_text(block_payload)

    if not source_text:
        return []

    return [
        _build_extraction_window(
            block=block_payload,
            document_ir=document_ir,
            document_profile=document_profile,
            plan_item=plan_item,
            row_range_label=None,
            segment_kind="block",
            source_provenance=_model_dump_list(segment.segment_provenance),
            source_segment_ids=[segment.segment_id],
            source_text=source_text,
            table=None,
            window_id=_deterministic_uuid(
                f"lc:curriculum:{document_ir.doc_key}:extraction_window:block:{segment.segment_id}"
            ),
            window_index=window_start_index,
            window_notes=["block_window_full_segment_source"],
        )
    ]


def _build_extraction_window(
    *,
    block: Optional[dict[str, Any]],
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    plan_item: ExtractionWindowPlanItem,
    row_range_label: Optional[str],
    segment_kind: str,
    source_provenance: list[dict[str, Any]],
    source_segment_ids: list[str],
    source_text: str,
    table: Optional[ExtractionWindowTablePayload],
    window_id: str,
    window_index: int,
    window_notes: list[str],
) -> ExtractionWindow:
    """Assemble and validate a shared extraction-window payload.

    Parameters
    ----------
    block
        Optional block payload for block windows.
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
    plan_item
        Planned source unit that produced this window.
    row_range_label
        Optional table row-range label used in deterministic keys.
    segment_kind
        Source segment kind for the window.
    source_provenance
        Source provenance records.
    source_segment_ids
        Source DocumentIR segment IDs included in the window.
    source_text
        Human-readable source text for prompt review and code matching.
    table
        Optional table payload for table windows.
    window_id
        Deterministic window identifier.
    window_index
        0-based window index.
    window_notes
        Implementation/debug notes.

    Returns
    -------
    ExtractionWindow
        Validated extraction window.
    """

    code_matches = _collect_code_matches(
        document_profile=document_profile,
        source_text=source_text,
    )
    code_parent_hints = _collect_code_parent_hints(
        code_matches=code_matches,
        document_profile=document_profile,
    )
    canonical_context = "|".join(
        [
            document_ir.doc_key,
            plan_item.segment_kind,
            plan_item.segment_id,
            row_range_label or "",
            re.sub(r"\s+", " ", source_text or "").strip().casefold(),
        ]
    )
    return ExtractionWindow(
        block=block,
        code_matches=code_matches,
        code_parent_hints=code_parent_hints,
        deterministic_hints={
            "bilingual_pair_policy": document_profile.bilingual_pair_policy,
            "code_parent_rules": document_profile.code_parent_rules,
            "code_patterns": document_profile.code_patterns,
            "country": document_profile.country,
            "no_code_policy": (
                "statement_code is optional. When no official code is visible, later "
                "candidate merge/ID steps must use source-derived keys and source text, "
                "not LLM paraphrases."
            ),
            "plan_reasons": plan_item.plan_reasons,
            "repeated_statement_policy": document_profile.repeated_statement_policy,
            "source_context_key": hashlib.sha256(
                canonical_context.encode("utf-8")
            ).hexdigest()[:32],
            "subject": document_profile.subject,
            "synthetic_merge_key_fields": document_profile.synthetic_merge_key_fields,
        },
        doc_key=document_ir.doc_key,
        framework_title=document_profile.framework_title,
        llm_task_instructions=(
            "Inspect only the source material in this extraction window. Return candidate "
            "StandardsFrameworkItems and auxiliary candidates using the expected output "
            "schema. Treat Python-provided code matches, code-parent hints, table headers, "
            "filldown rows, and source provenance as hints, not final KG nodes. Preserve "
            "source-language text, language tags, and provenance. statement_code is "
            "optional; when no official source code is visible, use source-derived keys "
            "and normalized source text for synthetic merge-key fields. Separate normative "
            "standards/groupings from descriptors, guidance, activities, examples, and other "
            "auxiliary material according to the DocumentProfile instructions. Return "
            "parent/context hints only when visible in the window; do not invent missing "
            "hierarchy."
        ),
        pdf_name=document_ir.pdf_name,
        primary_language=document_profile.primary_language,
        profile_extraction_instructions=document_profile.sfi_extraction_instructions,
        segment_kind=segment_kind,
        source_provenance=source_provenance,
        source_segment_ids=source_segment_ids,
        source_text=source_text,
        subject=document_profile.subject,
        table=table,
        window_id=window_id,
        window_index=window_index,
        window_notes=window_notes,
    )


def _build_table_source_text(
    *, rows: list[dict[str, Any]], table_payload: ExtractionWindowTablePayload
) -> str:
    """Build compact source text from selected table rows.

    Parameters
    ----------
    rows
        Selected raw source rows represented as dictionaries.
    table_payload
        Table payload containing headers and row indexes.

    Returns
    -------
    str
        Human-readable table source text for code matching and debugging.
    """

    lines: list[str] = []

    if table_payload.header_rows_canonical:
        header_lines = [" | ".join(row) for row in table_payload.header_rows_canonical]
        lines.append("Headers: " + " ||| ".join(header_lines))

    for row_index, row in zip(table_payload.row_indexes, rows):
        cell_texts: list[str] = []

        for cell in row.get("cells") or []:
            cell_text = ""

            if isinstance(cell, dict):
                text_unit = cell.get("text")

                if isinstance(text_unit, dict):
                    cell_text = str(text_unit.get("text") or "").strip()
                elif text_unit is not None:
                    cell_text = str(text_unit).strip()

            cell_texts.append(cell_text)

        lines.append(f"Row {row_index}: " + " | ".join(cell_texts))

    return "\n".join(lines).strip()


def _build_table_window_for_row_range(
    *,
    body_row_end_index_exclusive: int,
    body_row_start_index: int,
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    plan_item: ExtractionWindowPlanItem,
    segment: TableSegment,
    window_index: int,
) -> ExtractionWindow:
    """Build one table extraction window for a selected source row range.

    Parameters
    ----------
    body_row_end_index_exclusive
        Exclusive end index in the source table rows.
    body_row_start_index
        Inclusive start index in the source table rows.
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
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

    row_indexes = list(range(body_row_start_index, body_row_end_index_exclusive))
    rows = _model_dump_by_indexes(indexes=row_indexes, values=segment.rows)
    rows_grid = _optional_model_dump_by_indexes(
        indexes=row_indexes,
        values=getattr(segment, "rows_grid", None),
    )
    rows_filldown = _optional_model_dump_by_indexes(
        indexes=row_indexes,
        values=getattr(segment, "rows_filldown", None),
    )
    row_provenance = _optional_model_dump_by_indexes(
        indexes=row_indexes,
        values=getattr(segment, "row_provenance", None),
    )
    grid_sources = _optional_list_by_indexes(
        indexes=row_indexes,
        values=getattr(segment, "grid_sources", None),
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
    source_text = _build_table_source_text(
        rows=rows,
        table_payload=table_payload,
    )
    row_range_label = f"rows:{body_row_start_index}:{body_row_end_index_exclusive}"
    window_id = _deterministic_uuid(
        f"lc:curriculum:{document_ir.doc_key}:extraction_window:table:{segment.segment_id}:{row_range_label}"
    )
    return _build_extraction_window(
        block=None,
        document_ir=document_ir,
        document_profile=document_profile,
        plan_item=plan_item,
        row_range_label=row_range_label,
        segment_kind="table",
        source_provenance=_model_dump_list(segment.segment_provenance),
        source_segment_ids=[segment.segment_id],
        source_text=source_text,
        table=table_payload,
        window_id=window_id,
        window_index=window_index,
        window_notes=["table_window_uses_optional_helpers_when_present"],
    )


def _build_table_windows(
    *,
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    plan_item: ExtractionWindowPlanItem,
    segment: TableSegment,
    window_start_index: int,
) -> list[ExtractionWindow]:
    """Build table windows from a planned table segment.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
    plan_item
        Planned source unit for this table segment.
    segment
        Selected table segment.
    window_start_index
        Index to assign to the first produced window.

    Returns
    -------
    list[ExtractionWindow]
        Table extraction windows.
    """

    body_start_index = min(segment.header_row_count, len(segment.rows))
    body_end_index = len(segment.rows)

    if body_start_index >= body_end_index:
        return []

    windows: list[ExtractionWindow] = []

    for start_index, end_index in _iter_row_chunks(
        end_index=body_end_index,
        max_rows_per_window=document_profile.max_rows_per_table_window,
        overlap=document_profile.row_overlap,
        start_index=body_start_index,
    ):
        windows.append(
            _build_table_window_for_row_range(
                body_row_end_index_exclusive=end_index,
                body_row_start_index=start_index,
                document_ir=document_ir,
                document_profile=document_profile,
                plan_item=plan_item,
                segment=segment,
                window_index=window_start_index + len(windows),
            )
        )

    return windows


def _collect_code_matches(
    *, document_profile: DocumentProfile, source_text: str
) -> list[CodeMatch]:
    """Collect document-profile code regex matches from window source text.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    source_text
        Window source text.

    Returns
    -------
    list[CodeMatch]
        Ordered code matches.
    """

    code_matches: list[CodeMatch] = []

    for code_type, pattern in document_profile.code_patterns.items():
        for match in re.finditer(pattern, source_text):
            code_matches.append(
                CodeMatch(
                    code_type=code_type,
                    end_char=match.end(),
                    start_char=match.start(),
                    value=match.group(0),
                )
            )

    code_matches.sort(key=lambda item: (item.start_char, item.end_char, item.code_type))

    seen: set[tuple[str, int, int, str]] = set()
    deduped: list[CodeMatch] = []

    for code_match in code_matches:
        key = (
            code_match.code_type,
            code_match.start_char,
            code_match.end_char,
            code_match.value,
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(code_match)

    return deduped


def _collect_code_parent_hints(
    *, code_matches: Sequence[CodeMatch], document_profile: DocumentProfile
) -> list[CodeParentHint]:
    """Collect deterministic code-parent hints from profile rules.

    Parameters
    ----------
    code_matches
        Code matches found in the window.
    document_profile
        Country/document-specific KG extraction profile.

    Returns
    -------
    list[CodeParentHint]
        Ordered unique parent-code hints.
    """

    code_patterns = document_profile.code_patterns
    hints: list[CodeParentHint] = []

    for code_match in code_matches:
        for rule in document_profile.code_parent_rules:
            child_code_type = rule.get("child")

            if code_match.code_type != child_code_type:
                continue

            method = rule.get("method")
            parent_code_type = rule.get("parent")
            parent_code: Optional[str] = None

            if method == "drop_last_dot_component":
                if "." in code_match.value:
                    parent_code = code_match.value.rsplit(".", 1)[0]
            elif method == "regex_substitution":
                regex = rule.get("regex")
                replacement = rule.get("replacement")

                if regex is not None and replacement is not None:
                    parent_code = re.sub(regex, replacement, code_match.value)

            if (
                not parent_code
                or parent_code == code_match.value
                or parent_code_type is None
            ):
                continue

            parent_pattern = code_patterns.get(parent_code_type)

            if parent_pattern is not None and not re.fullmatch(
                parent_pattern,
                parent_code,
            ):
                continue

            hints.append(
                CodeParentHint(
                    child_code=code_match.value,
                    child_code_type=code_match.code_type,
                    method=str(method),
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


def _extract_figure_text(block_payload: dict[str, Any]) -> str:
    """Extract source text from a block payload's figure fields.

    Parameters
    ----------
    block_payload
        JSON-serializable block payload containing optional figure data.

    Returns
    -------
    str
        Source text drawn from embedded text, caption, or alt text, or an empty string
        when no useful figure text is present.
    """

    figure = block_payload.get("figure")

    if not isinstance(figure, dict):
        return ""

    embedded_text = figure.get("embedded_text")

    if isinstance(embedded_text, dict) and embedded_text.get("text"):
        return str(embedded_text["text"]).strip()

    caption = figure.get("caption")

    if isinstance(caption, dict) and caption.get("text"):
        return str(caption["text"]).strip()

    if isinstance(caption, str) and caption.strip():
        return caption.strip()

    if figure.get("contains_text") and figure.get("alt_text"):
        return str(figure["alt_text"]).strip()

    return ""


def _extract_list_items_text(list_items: list[Any]) -> str:
    """Join the text fields of list items into a single newline-delimited string.

    Parameters
    ----------
    list_items
        List items extracted from a block payload.

    Returns
    -------
    str
        Newline-joined item text, or an empty string when no item text is present.
    """

    if not isinstance(list_items, list):
        return ""

    item_texts: list[str] = []

    for item in list_items:
        if not isinstance(item, dict):
            continue

        item_text = item.get("text")

        if isinstance(item_text, dict):
            item_texts.append(str(item_text.get("text") or "").strip())
        elif item_text:
            item_texts.append(str(item_text).strip())

    return "\n".join(item_text for item_text in item_texts if item_text)


def _get_table_plan_reasons(
    *, document_profile: DocumentProfile, segment: TableSegment
) -> list[str]:
    """Return reasons for planning a table segment for extraction.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    segment
        Candidate table segment.

    Returns
    -------
    list[str]
        Table plan reasons, empty when the table should be skipped.
    """

    columns_signature = segment.columns_signature or "<missing>"
    section_text = _table_section_text(segment)

    if columns_signature in document_profile.excluded_table_columns_signatures:
        return []

    if _matches_any_pattern(
        patterns=document_profile.excluded_table_section_patterns, text=section_text
    ):
        return []

    reasons: list[str] = []

    if columns_signature in document_profile.included_table_columns_signatures:
        reasons.append("table_columns_signature_included_match")

    if _matches_any_pattern(
        patterns=document_profile.included_table_section_patterns, text=section_text
    ):
        reasons.append("table_section_included_pattern_match")

    return unique_clean_strings(reasons)


def _iter_row_chunks(
    *,
    end_index: int,
    max_rows_per_window: Optional[int],
    overlap: int,
    start_index: int,
) -> list[tuple[int, int]]:
    """Return deterministic body-row chunks for a table window.

    Parameters
    ----------
    end_index
        Exclusive end row index.
    max_rows_per_window
        Maximum number of body rows per window. If None, emit one whole-table range.
    overlap
        Number of overlapping body rows between adjacent chunks.
    start_index
        Inclusive start row index.

    Returns
    -------
    list[tuple[int, int]]
        (start, end) row ranges.

    Raises
    ------
    ValueError
        If row-windowing parameters are invalid.
    """

    if max_rows_per_window is None:
        return [(start_index, end_index)]

    if max_rows_per_window <= 0:
        raise ValueError("max_rows_per_window must be positive or None.")

    if overlap < 0:
        raise ValueError("overlap must be non-negative.")

    if overlap >= max_rows_per_window:
        raise ValueError("overlap must be smaller than max_rows_per_window.")

    chunks: list[tuple[int, int]] = []
    current_start_index = start_index

    while current_start_index < end_index:
        current_end_index = min(current_start_index + max_rows_per_window, end_index)
        chunks.append((current_start_index, current_end_index))

        if current_end_index >= end_index:
            break

        current_start_index = current_end_index - overlap

    return chunks


def _matches_any_pattern(*, patterns: Sequence[str], text: str) -> bool:
    """Return whether any configured regex pattern matches text.

    Parameters
    ----------
    patterns
        Regex patterns to test.
    text
        Text to inspect.

    Returns
    -------
    bool
        True if any pattern matches; otherwise False.
    """

    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _model_dump_by_indexes(
    *, indexes: Sequence[int], values: Sequence[Any]
) -> list[dict[str, Any]]:
    """Serialize selected model/list values by index.

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

    return [_model_to_dict(values[index]) for index in indexes]


def _model_dump_list(values: Sequence[Any]) -> list[dict[str, Any]]:
    """Serialize a sequence of Pydantic models or dictionaries.

    Parameters
    ----------
    values
        Values to serialize.

    Returns
    -------
    list[dict[str, Any]]
        Serialized dictionaries.
    """

    return [_model_to_dict(value) for value in values]


def _model_to_dict(value: Any) -> dict[str, Any]:
    """Serialize one Pydantic model or dictionary value.

    Parameters
    ----------
    value
        Value to serialize.

    Returns
    -------
    dict[str, Any]
        JSON-serializable dictionary.

    Raises
    ------
    TypeError
        If the value is neither a Pydantic model nor a dictionary.
    """

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return dict(value)

    raise TypeError(f"Expected a Pydantic model or dict-like value, got {type(value)}")


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

    if values is None:
        return None

    if max(indexes, default=-1) >= len(values):
        return None

    return [values[index] for index in indexes]


def _optional_model_dump_by_indexes(
    *, indexes: Sequence[int], values: Optional[Sequence[Any]]
) -> Optional[list[dict[str, Any]]]:
    """Serialize optional model/list values by index.

    Parameters
    ----------
    indexes
        Indexes to select.
    values
        Optional sequence of values.

    Returns
    -------
    Optional[list[dict[str, Any]]]
        Serialized selected values, or None when the helper view is missing or unaligned.
    """

    if values is None:
        return None

    if max(indexes, default=-1) >= len(values):
        return None

    return _model_dump_by_indexes(indexes=indexes, values=values)


def _table_section_text(segment: TableSegment) -> str:
    """Build text used only for table section-pattern selection rules.

    Parameters
    ----------
    segment
        Candidate table segment.

    Returns
    -------
    str
        Source-adjacent heading text plus table metadata for table selection.
    """

    parts = [heading_ref.text for heading_ref in segment.section_path]

    if segment.local_code:
        parts.append(str(segment.local_code))

    if segment.columns_signature:
        parts.append(str(segment.columns_signature))

    return "\n".join(part for part in parts if part)


def build_llm_extraction_windows(
    *,
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    plan_items: Sequence[ExtractionWindowPlanItem],
    save_fp: Path,
) -> list[ExtractionWindow]:
    """Build LLM-ready Academic Standards extraction windows.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
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
        If a planned segment is missing or no windows are produced without an explicit
        zero-window override.
    """

    segment_by_id = {segment.segment_id: segment for segment in document_ir.segments}
    extraction_windows: list[ExtractionWindow] = []

    for plan_item in plan_items:
        segment = segment_by_id.get(plan_item.segment_id)

        if segment is None:
            raise ValueError(
                f"Planned segment_id not found in DocumentIR: {plan_item.segment_id}"
            )

        if segment.kind == "block":
            extraction_windows.extend(
                _build_block_windows(
                    document_ir=document_ir,
                    document_profile=document_profile,
                    plan_item=plan_item,
                    segment=segment,
                    window_start_index=len(extraction_windows),
                )
            )
        elif segment.kind == "table":
            extraction_windows.extend(
                _build_table_windows(
                    document_ir=document_ir,
                    document_profile=document_profile,
                    plan_item=plan_item,
                    segment=segment,
                    window_start_index=len(extraction_windows),
                )
            )
        else:
            raise ValueError(f"Unrecognized planned segment kind: {segment.kind}")

    if not extraction_windows:
        raise ValueError("No extraction windows were produced.")

    write_extraction_windows(
        extraction_windows=extraction_windows,
        save_fp=save_fp,
    )
    return extraction_windows


def plan_extraction_windows(
    *, document_ir: DocumentIR, document_profile: DocumentProfile, save_fp: Path
) -> list[ExtractionWindowPlanItem]:
    """Plan DocumentIR source units that should become extraction windows.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
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
            source_text = _build_block_source_text(segment.model_dump(mode="json"))

            if not source_text:
                continue

            plan_reasons = ["block_has_extractable_source_text"]
        elif segment.kind == "table":
            plan_reasons = _get_table_plan_reasons(
                document_profile=document_profile, segment=segment
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
