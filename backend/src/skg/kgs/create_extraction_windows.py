"""This module contains functionalities for creating LLM-ready extraction windows for
the Academic Standards (AS) KG:

1. Select source DocumentIR segments eligible for AS extraction.
2. Build source-faithful, LLM-ready extraction-window payloads.
3. Persist extraction windows as JSONL for prompt/debug review before any LLM call.

The implementation is intentionally profile-driven and conservative. It packages source
text, table structure, optional table helper views, code hints, context hints, and the
later SFI extraction contract without trying to replace the LLM semantic extraction
step.
"""

# Standard Library
import hashlib
import re
import uuid

from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

# Package Library
from skg.document_ir.schemas import (
    BlockSegment,
    DocumentIR,
    SectionHeadingRef,
    Segment,
    TableSegment,
)
from skg.kgs.schemas import (
    CodeMatch,
    CodeParentHint,
    DocumentProfile,
    ExtractionWindow,
    ExtractionWindowTablePayload,
    SelectedExtractionSegment,
    SelectedExtractionSegmentsArtifact,
    StructuredContextItem,
    unique_clean_strings,
)
from skg.utils.general import make_dir, write_to_json


def _build_block_source_text(block_payload: dict[str, Any]) -> str:
    """Build a source text string from a block payload.

    Parameters
    ----------
    block_payload
        JSON-serializable block payload.

    Returns
    -------
    str
        Source text for the block window.
    """

    if text := block_payload.get("combined_text"):
        return str(text).strip()

    text_unit = block_payload.get("text")

    if isinstance(text_unit, dict) and text_unit.get("text"):
        return str(text_unit["text"]).strip()

    return _extract_list_or_figure_text(
        block_payload=block_payload, list_items=block_payload.get("list_items") or []
    )


def _build_block_windows(
    *,
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    segment: BlockSegment,
    selected_segment: SelectedExtractionSegment,
    window_start_index: int,
) -> list[ExtractionWindow]:
    """Build a single block extraction window.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
    segment
        Selected block segment.
    selected_segment
        Selection metadata for the block segment.
    window_start_index
        Index to assign to the first produced window.

    Returns
    -------
    list[ExtractionWindow]
        One block extraction window, or an empty list when the block has no source text.
    """

    block_payload = segment.model_dump(mode="json")
    source_text = _build_block_source_text(block_payload)
    return (
        []
        if not source_text
        else [
            _build_extraction_window(
                block=block_payload,
                document_ir=document_ir,
                document_profile=document_profile,
                nearby_headings=_nearby_headings_for_segment(
                    document_profile=document_profile, section_path=segment.section_path
                ),
                row_range_label=None,
                section_path=_model_dump_list(segment.section_path),
                segment_kind="block",
                selected_segment=selected_segment,
                source_provenance=_model_dump_list(segment.segment_provenance),
                source_segment_ids=[segment.segment_id],
                source_text=source_text,
                structured_context=_derive_structured_context(
                    document_profile=document_profile, heading_refs=segment.section_path
                ),
                table=None,
                window_id=_deterministic_uuid(
                    f"lc:curriculum:{document_ir.doc_key}:extraction_window:block:{segment.segment_id}"
                ),
                window_index=window_start_index,
                window_notes=["block_window_selected_by_profile_policy"],
            )
        ]
    )


def _build_extraction_window(
    *,
    block: Optional[dict[str, Any]],
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    nearby_headings: list[dict[str, Any]],
    row_range_label: Optional[str],
    section_path: list[dict[str, Any]],
    segment_kind: Literal["block", "table"],
    selected_segment: SelectedExtractionSegment,
    source_provenance: list[dict[str, Any]],
    source_segment_ids: list[str],
    source_text: str,
    structured_context: list[StructuredContextItem],
    table: Optional[ExtractionWindowTablePayload],
    window_id: str,
    window_index: int,
    window_notes: list[str],
) -> ExtractionWindow:
    """Assemble and validate a shared extraction-window payload.

    Parameters
    ----------
    block
        Optional block payload.
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
    nearby_headings
        Raw nearby heading references.
    row_range_label
        Optional table row-range label for deterministic hints.
    section_path
        Raw section path for the window.
    segment_kind
        Source segment kind for the window.
    selected_segment
        Selected segment metadata.
    source_provenance
        Source provenance records.
    source_segment_ids
        Source DocumentIR segment IDs in the window.
    source_text
        Human-readable source text for code matching and debugging.
    structured_context
        Profile-derived structured context.
    table
        Optional table payload.
    window_id
        Deterministic window identifier.
    window_index
        0-based window index.
    window_notes
        Debug notes.

    Returns
    -------
    ExtractionWindow
        The validated extraction window.
    """

    code_matches = _collect_code_matches(
        document_profile=document_profile, source_text=source_text
    )
    code_parent_hints = _collect_code_parent_hints(
        code_matches=code_matches, document_profile=document_profile
    )
    context_path_text = " > ".join(
        str(ref.get("text") or "").strip()
        for ref in section_path
        if str(ref.get("text") or "").strip()
    )
    canonical_context = "|".join(
        [
            document_ir.doc_key,
            selected_segment.segment_kind,
            selected_segment.segment_id,
            row_range_label or "",
            _normalize_key_text(context_path_text),
            _normalize_key_text(source_text),
        ]
    )
    return ExtractionWindow(
        block=block,
        code_matches=code_matches,
        code_parent_hints=code_parent_hints,
        context_path_text=context_path_text,
        deterministic_hints={
            "bilingual_pair_policy": document_profile.bilingual_pair_policy,
            "code_parent_rules": document_profile.code_parent_rules,
            "code_patterns": document_profile.code_patterns,
            "code_statement_types": document_profile.code_statement_types,
            "country": document_profile.country,
            "has_stable_codes": document_profile.has_stable_codes,
            "no_code_policy": (
                "statement_code is optional. When no official code is visible, later "
                "candidate merge/ID steps must use source-derived context/text keys, "
                "not LLM paraphrases."
            ),
            "repeated_statement_policy": document_profile.repeated_statement_policy,
            "selected_segment_selection_reasons": selected_segment.selection_reasons,
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
            "filldown rows, and structured context as hints, not final KG nodes. Preserve "
            "source-language text, language tags, and provenance. statement_code is "
            "optional; when no official source code is visible, use source-derived context "
            "and normalized source text for synthetic merge-key fields. Separate normative "
            "standards/groupings from descriptors, guidance, activities, examples, and other "
            "auxiliary material according to the DocumentProfile instructions. Return "
            "parent/context hints when visible, but do not invent missing hierarchy."
        ),
        nearby_headings=nearby_headings,
        pdf_name=document_ir.pdf_name,
        primary_language=document_profile.primary_language,
        profile_extraction_instructions=document_profile.sfi_extraction_instructions,
        section_path=section_path,
        segment_kind=segment_kind,
        source_provenance=source_provenance,
        source_segment_ids=source_segment_ids,
        source_text=source_text,
        structured_context=structured_context,
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
        Human-readable table source text for code matching/debugging.
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
    segment: Any,
    selected_segment: SelectedExtractionSegment,
    window_index: int,
) -> ExtractionWindow:
    """Build one table extraction window for a selected body-row range.

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
    segment
        Selected table segment.
    selected_segment
        Selection metadata for the table segment.
    window_index
        0-based window index.

    Returns
    -------
    ExtractionWindow
        The validated table extraction window.
    """

    row_indexes = list(range(body_row_start_index, body_row_end_index_exclusive))
    rows = _model_dump_by_indexes(indexes=row_indexes, values=segment.rows)
    rows_grid = _optional_model_dump_by_indexes(
        indexes=row_indexes, values=getattr(segment, "rows_grid", None)
    )
    rows_filldown = _optional_model_dump_by_indexes(
        indexes=row_indexes, values=getattr(segment, "rows_filldown", None)
    )
    row_provenance = _optional_model_dump_by_indexes(
        indexes=row_indexes, values=getattr(segment, "row_provenance", None)
    )
    grid_sources = _optional_list_by_indexes(
        indexes=row_indexes, values=getattr(segment, "grid_sources", None)
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
        table_window_mode=document_profile.table_window_mode,
    )
    source_text = _build_table_source_text(rows=rows, table_payload=table_payload)
    section_path = _model_dump_list(segment.section_path)
    nearby_headings = _nearby_headings_for_segment(
        document_profile=document_profile, section_path=segment.section_path
    )
    structured_context = _derive_structured_context(
        document_profile=document_profile, heading_refs=segment.section_path
    )
    row_range_label = f"rows:{body_row_start_index}:{body_row_end_index_exclusive}"
    window_id = _deterministic_uuid(
        f"lc:curriculum:{document_ir.doc_key}:extraction_window:table:{segment.segment_id}:{row_range_label}"
    )
    return _build_extraction_window(
        block=None,
        document_ir=document_ir,
        document_profile=document_profile,
        nearby_headings=nearby_headings,
        row_range_label=row_range_label,
        section_path=section_path,
        segment_kind="table",
        selected_segment=selected_segment,
        source_provenance=_model_dump_list(segment.segment_provenance),
        source_segment_ids=[segment.segment_id],
        source_text=source_text,
        structured_context=structured_context,
        table=table_payload,
        window_id=window_id,
        window_index=window_index,
        window_notes=["table_window_uses_optional_helpers_when_present"],
    )


def _build_table_windows(
    *,
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    segment: TableSegment,
    selected_segment: SelectedExtractionSegment,
    window_start_index: int,
) -> list[ExtractionWindow]:
    """Build table extraction windows according to document profile table-window mode.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
    segment
        Selected table segment.
    selected_segment
        Selection metadata for the table segment.
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

    if document_profile.table_window_mode == "whole_table":
        windows.append(
            _build_table_window_for_row_range(
                body_row_end_index_exclusive=body_end_index,
                body_row_start_index=body_start_index,
                document_ir=document_ir,
                document_profile=document_profile,
                segment=segment,
                selected_segment=selected_segment,
                window_index=window_start_index,
            )
        )
        return windows

    if document_profile.table_window_mode != "row_chunks":
        raise ValueError(
            f"Unsupported table_window_mode: {document_profile.table_window_mode!r}"
        )

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
                segment=segment,
                selected_segment=selected_segment,
                window_index=window_start_index + len(windows),
            )
        )

    return windows


def _collect_code_matches(
    *, document_profile: DocumentProfile, source_text: str
) -> list[CodeMatch]:
    """Collect document profile code regex matches from window source text.

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
                    statement_type=document_profile.code_statement_types.get(code_type),
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
    code_statement_types = document_profile.code_statement_types
    hints: list[CodeParentHint] = []

    for code_match in code_matches:
        for rule in document_profile.code_parent_rules:
            # 1. Skip if the rule does not apply to this code match type.
            child_code_type = rule.get("child")

            if code_match.code_type != child_code_type:
                continue

            method = rule.get("method")
            parent_code_type = rule.get("parent")
            parent_code: Optional[str] = None

            # 2. Derive the parent code.
            if method == "drop_last_dot_component":
                if "." in code_match.value:
                    parent_code = code_match.value.rsplit(".", 1)[0]
            elif method == "regex_substitution":
                regex = rule.get("regex")
                replacement = rule.get("replacement")

                if regex is not None and replacement is not None:
                    parent_code = re.sub(regex, replacement, code_match.value)

            # 3. Check if a valid parent code was successfully derived.
            if (
                not parent_code
                or parent_code == code_match.value
                or parent_code_type is None
            ):
                continue

            # 4. Validate against the parent pattern if one exists.
            parent_pattern = code_patterns.get(parent_code_type)

            if parent_pattern is not None and not re.fullmatch(
                parent_pattern, parent_code
            ):
                continue

            # 5. Rule matched and generated a valid parent; append the hint.
            hints.append(
                CodeParentHint(
                    child_code=code_match.value,
                    child_code_type=code_match.code_type,
                    method=str(method),
                    parent_code=parent_code,
                    parent_code_type=parent_code_type,
                    parent_statement_type=code_statement_types.get(parent_code_type),
                )
            )

    return _dedupe_code_parent_hints(hints)


def _collect_nearby_heading_keys_for_tables(
    *, document_profile: DocumentProfile, table_segments: Sequence[Any]
) -> set[tuple[int, int]]:
    """Collect source keys for table-adjacent context heading blocks.

    This function runs after independent table selection and before the final
    materialization pass. When `include_context_blocks_for_selected_tables` is true, it
    looks at each selected table's bounded nearby heading refs and returns the
    source-address keys for those headings. The final selection pass can then include
    matching BlockSegments as context windows without treating those context windows as
    direct block-extraction hits.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    table_segments
        Already selected table segments.

    Returns
    -------
    set[tuple[int, int]]
        Set of `(page_index, item_index)` heading keys.
    """

    if not document_profile.include_context_blocks_for_selected_tables:
        return set()

    keys: set[tuple[int, int]] = set()

    for table_segment in table_segments:
        for heading_ref in _limit_nearby_headings(
            document_profile=document_profile, section_path=table_segment.section_path
        ):
            keys.add((heading_ref.page_index, heading_ref.item_index))

    return keys


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


def _derive_structured_context(
    *, document_profile: DocumentProfile, heading_refs: Sequence[SectionHeadingRef]
) -> list[StructuredContextItem]:
    """Derive structured context from section headings using profile rules.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    heading_refs
        Raw DocumentIR heading references.

    Returns
    -------
    list[StructuredContextItem]
        Profile-derived context items, ordered by profile role_order when available.
    """

    context_by_role: OrderedDict[str, StructuredContextItem] = OrderedDict()
    context_spine = document_profile.context_spine

    if not context_spine.heading_rules:
        return []

    for heading_ref in heading_refs:
        heading_candidates = _heading_match_candidates(
            document_profile=document_profile, heading_ref=heading_ref
        )

        if not heading_candidates:
            continue

        context_item = heading_candidates[0]

        for reset_rule in context_spine.reset_rules:
            if reset_rule.on_role == context_item.role:
                for role_to_reset in reset_rule.reset_roles:
                    context_by_role.pop(role_to_reset, None)

        context_by_role[context_item.role] = context_item

    if context_spine.role_order:
        ordered_items = [
            context_by_role[role]
            for role in context_spine.role_order
            if role in context_by_role
        ]
        ordered_items.extend(
            item
            for role, item in context_by_role.items()
            if role not in set(context_spine.role_order)
        )
        return ordered_items

    return list(context_by_role.values())


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


def _extract_list_or_figure_text(
    *, block_payload: dict[str, Any], list_items: list[Any]
) -> str:
    """Extract source text from list items or a figure payload.

    Parameters
    ----------
    block_payload
        JSON-serializable block payload containing the figure data.
    list_items
        The list items previously extracted from the block payload.

    Returns
    -------
    str
        Source text extracted from either the list items or the figure, or an empty
        string if neither yields valid text.
    """

    if isinstance(list_items, list):
        item_texts = []

        for item in list_items:
            if isinstance(item, dict):
                item_text = item.get("text")

                if isinstance(item_text, dict):
                    item_texts.append(str(item_text.get("text") or "").strip())
                elif item_text:
                    item_texts.append(str(item_text).strip())

        if item_texts:
            return "\n".join(item_text for item_text in item_texts if item_text)

    figure = block_payload.get("figure")

    if isinstance(figure, dict):
        embedded_text = figure.get("embedded_text")

        if isinstance(embedded_text, dict) and embedded_text.get("text"):
            return str(embedded_text["text"]).strip()

        if figure.get("alt_text"):
            return str(figure["alt_text"]).strip()

    return ""


def _format_context_label(
    *, label_template: Optional[str], match: re.Match[str]
) -> str:
    """Format a context label from a heading-rule match.

    Parameters
    ----------
    label_template
        Optional label template from the profile.
    match
        Regex match object.

    Returns
    -------
    str
        Formatted label.
    """

    if label_template is None:
        return match.group(0).strip()

    # Document profile docs use {1}, {2}, ... for capture groups. Prefix an empty
    # element so those templates work with Python's positional format syntax.
    groups = [match.group(0), *match.groups()]

    try:
        return label_template.format(*groups).strip()
    except (IndexError, KeyError):
        return match.group(0).strip()


def _get_block_context_selection_reasons(
    *,
    context_heading_keys: set[tuple[int, int]],
    document_profile: DocumentProfile,
    segment: BlockSegment,
) -> list[str]:
    """Return reasons for selecting a block only as context for selected tables.

    This is separate from direct block extraction. A block can be selected here when
    its first source provenance key is one of the nearby heading keys collected from
    already selected tables. Block exclusion policy still applies, so a noisy heading
    can be suppressed by block type, block text, or nearby section context.

    Parameters
    ----------
    context_heading_keys
        Source-address keys collected from selected tables' nearby heading refs.
    document_profile
        Country/document-specific KG extraction profile.
    segment
        Candidate block segment.

    Returns
    -------
    list[str]
        Context-block selection reasons, empty when the block is not table context.
    """

    if not document_profile.include_context_blocks_for_selected_tables:
        return []

    source_text = _build_block_source_text(segment.model_dump(mode="json"))

    if not source_text:
        return []

    section_text = _segment_selection_context_text(
        document_profile=document_profile, include_block_text=False, segment=segment
    )

    if _is_block_excluded(
        document_profile=document_profile,
        section_text=section_text,
        segment=segment,
        source_text=source_text,
    ):
        return []

    segment_heading_key = (
        segment.segment_provenance[0].page_index,
        segment.segment_provenance[0].item_index,
    )

    if segment_heading_key not in context_heading_keys:
        return []

    return ["block_is_context_heading_for_selected_table"]


def _get_block_selection_reasons(
    *, document_profile: DocumentProfile, segment: BlockSegment
) -> list[str]:
    """Return direct block-selection reasons for Academic Standards extraction.

    Direct block selection is independent of table selection. It is enabled by
    `DocumentProfile.include_block_windows` and can select block segments using block
    type allow-lists, block text patterns, bounded section-context patterns, stable
    source-code matches, or profile context-heading rules. Table-adjacent context
    headings are handled separately by `_get_block_context_selection_reasons(...)`.

    Exclusion rules are applied first. A block is not selected if its block type,
    bounded section context, or own source text matches a configured block exclusion.
    Section-context pattern checks use only bounded nearby headings plus segment
    metadata, rather than the full raw `section_path`, to reduce stale-heading noise.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    segment
        Candidate block segment.

    Returns
    -------
    list[str]
        Direct block-selection reasons, empty when the block should not be selected.
    """

    if not document_profile.include_block_windows:
        return []

    source_text = _build_block_source_text(segment.model_dump(mode="json"))

    if not source_text:
        return []

    section_text = _segment_selection_context_text(
        document_profile=document_profile, include_block_text=False, segment=segment
    )

    if _is_block_excluded(
        document_profile=document_profile,
        section_text=section_text,
        segment=segment,
        source_text=source_text,
    ):
        return []

    block_type = segment.block_type.value
    reasons: list[str] = []

    if block_type is not None and block_type in document_profile.target_block_types:
        reasons.append("block_type_target_match")

    if _collect_code_matches(
        document_profile=document_profile, source_text=source_text
    ):
        reasons.append("block_contains_profile_code_match")

    if _heading_match_candidates(
        document_profile=document_profile,
        heading_ref=SectionHeadingRef(
            item_index=segment.segment_provenance[0].item_index,
            page_index=segment.segment_provenance[0].page_index,
            text=source_text,
        ),
    ):
        reasons.append("block_matches_context_heading_rule")

    if _matches_any_pattern(
        patterns=document_profile.target_block_section_patterns, text=section_text
    ):
        reasons.append("block_section_target_pattern_match")

    if _matches_any_pattern(
        patterns=document_profile.target_block_text_patterns, text=source_text
    ):
        reasons.append("block_text_target_pattern_match")

    return unique_clean_strings(reasons)


def _get_table_selection_reasons(
    *, document_profile: DocumentProfile, segment: TableSegment
) -> list[str]:
    """Return table-selection reasons for Academic Standards extraction.

    Table selection is independent of direct block selection. It is enabled by
    `DocumentProfile.include_table_windows`, applies table exclusions first, and then
    selects a table when its columns signature or bounded section context matches a
    configured table target rule. Section-pattern matching uses nearby heading context
    rather than the full raw `section_path`, which reduces over-selection from stale or
    overly broad heading trails.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    segment
        Candidate table segment.

    Returns
    -------
    list[str]
        Table-selection reasons, empty when the table should not be selected.
    """

    if not document_profile.include_table_windows:
        return []

    columns_signature = segment.columns_signature or "<missing>"
    section_text = _segment_selection_context_text(
        document_profile=document_profile, include_block_text=False, segment=segment
    )

    if columns_signature in document_profile.excluded_table_columns_signatures:
        return []

    if _matches_any_pattern(
        patterns=document_profile.excluded_table_section_patterns, text=section_text
    ):
        return []

    reasons: list[str] = []

    if columns_signature in document_profile.target_table_columns_signatures:
        reasons.append("table_columns_signature_target_match")

    if _matches_any_pattern(
        patterns=document_profile.target_table_section_patterns, text=section_text
    ):
        reasons.append("table_section_target_pattern_match")

    return unique_clean_strings(reasons)


def _heading_match_candidates(
    *, document_profile: DocumentProfile, heading_ref: SectionHeadingRef
) -> list[StructuredContextItem]:
    """Return context candidates from one heading reference.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    heading_ref
        Heading reference to inspect.

    Returns
    -------
    list[StructuredContextItem]
        Matched context candidates, highest priority first.
    """

    heading_text = heading_ref.text
    raw_fragments = [heading_text]

    if document_profile.context_spine.split_multiline_headings:
        raw_fragments.extend(line for line in heading_text.splitlines() if line.strip())

    fragments = unique_clean_strings(raw_fragments)

    candidates: list[tuple[int, int, StructuredContextItem]] = []

    for rule_index, rule in enumerate(document_profile.context_spine.heading_rules):
        compiled = re.compile(rule.pattern)

        for fragment in fragments:
            match = compiled.search(fragment)

            if match is None:
                continue

            candidates.append(
                (
                    -rule.priority,
                    rule_index,
                    StructuredContextItem(
                        label=_format_context_label(
                            label_template=rule.label_template, match=match
                        ),
                        metadata=rule.metadata,
                        normalized_statement_type=rule.normalized_statement_type,
                        role=rule.role,
                        rule_name=rule.name,
                        source_heading_item_index=heading_ref.item_index,
                        source_heading_page_index=heading_ref.page_index,
                        source_text=fragment.strip(),
                        statement_type=rule.statement_type,
                    ),
                )
            )

    candidates.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in candidates]


def _is_block_excluded(
    *,
    document_profile: DocumentProfile,
    section_text: str,
    segment: BlockSegment,
    source_text: str,
) -> bool:
    """Return whether a block is removed by block exclusion policy.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    section_text
        Bounded nearby section-context text for the segment.
    segment
        Candidate block segment.
    source_text
        The block's own source text.

    Returns
    -------
    bool
        True when the block should be excluded from block/context selection.
    """

    block_type = segment.block_type.value

    if block_type is not None and block_type in document_profile.excluded_block_types:
        return True

    if _matches_any_pattern(
        patterns=document_profile.excluded_block_section_patterns, text=section_text
    ):
        return True

    return _matches_any_pattern(
        patterns=document_profile.excluded_block_text_patterns, text=source_text
    )


def _iter_row_chunks(
    *, end_index: int, max_rows_per_window: int, overlap: int, start_index: int
) -> list[tuple[int, int]]:
    """Return deterministic overlapping body-row chunks.

    Parameters
    ----------
    end_index
        Exclusive end row index.
    max_rows_per_window
        Maximum number of body rows per window.
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
        If row windowing parameters are invalid.
    """

    if max_rows_per_window <= 0:
        raise ValueError("max_rows_per_window must be positive.")

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


def _limit_nearby_headings(
    *, document_profile: DocumentProfile, section_path: Sequence[SectionHeadingRef]
) -> list[SectionHeadingRef]:
    """Limit heading refs using the document profile context-spine `max_nearby_headings`
    setting.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    section_path
        Full raw section path.

    Returns
    -------
    list[SectionHeadingRef]
        Nearby heading refs.
    """

    max_nearby_headings = document_profile.context_spine.max_nearby_headings

    if max_nearby_headings <= 0:
        return []

    return list(section_path[-max_nearby_headings:])


def _matches_any_pattern(*, patterns: Sequence[str], text: str) -> bool:
    """Return whether any regex pattern matches the given text.

    Every configured section pattern is treated as a regex, matched case-insensitively
    against the entire `section_text` string. If `patterns` is empty, it returns False.

    NB: The DocumentProfile schema should validate that section patterns are valid
    regexes.

    Parameters
    ----------
    patterns
        Regex patterns to test.
    text
        Text to inspect.

    Returns
    -------
    bool
        True if any pattern matches.
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
    """Serialize one Pydantic model/dict-like value.

    Parameters
    ----------
    value
        Value to serialize.

    Returns
    -------
    dict[str, Any]
        JSON-serializable dictionary.
    """

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    if isinstance(value, dict):
        return dict(value)

    raise TypeError(f"Expected a Pydantic model or dict-like value, got {type(value)}")


def _nearby_headings_for_segment(
    *, document_profile: DocumentProfile, section_path: Sequence[SectionHeadingRef]
) -> list[dict[str, Any]]:
    """Return raw nearby headings for an extraction window.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    section_path
        Full raw section path.

    Returns
    -------
    list[dict[str, Any]]
        Raw nearby heading dictionaries.
    """

    return (
        []
        if not document_profile.context_spine.include_nearby_headings
        else _model_dump_list(
            _limit_nearby_headings(
                document_profile=document_profile, section_path=section_path
            )
        )
    )


def _normalize_key_text(text: str) -> str:
    """Normalize source text for source-derived keys.

    Parameters
    ----------
    text
        Source text.

    Returns
    -------
    str
        Whitespace-normalized lowercase text.
    """

    return re.sub(r"\s+", " ", text or "").strip().casefold()


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
        Selected values, or None when the source helper view is missing.
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
        Serialized selected values, or None when the helper view is missing.
    """

    if values is None:
        return None

    if max(indexes, default=-1) >= len(values):
        return None

    return _model_dump_by_indexes(indexes=indexes, values=values)


def _segment_section_text(segment: Segment) -> str:
    """Build full raw section text for debug use.

    This helper preserves the earlier full-section-path behavior for diagnostics. New
    selection policy should prefer `_segment_selection_context_text(...)`, which uses
    bounded nearby headings and avoids stale section-path noise.

    Parameters
    ----------
    segment
        DocumentIR segment.

    Returns
    -------
    str
        Full section-path text plus local code, columns signature, and block text when
        present.
    """

    parts = [heading.text for heading in segment.section_path]

    if segment.local_code:
        parts.append(str(segment.local_code))

    if getattr(segment, "columns_signature", None):
        parts.append(str(segment.columns_signature))

    if segment.kind == "block":
        parts.append(_build_block_source_text(segment.model_dump(mode="json")))

    return "\n".join(part for part in parts if part)


def _segment_selection_context_text(
    *, document_profile: DocumentProfile, include_block_text: bool, segment: Segment
) -> str:
    """Build bounded context text for segment selection rules.

    Selection-time section pattern matching intentionally uses the same bounded nearby
    heading view that extraction windows expose for debug/fallback context. This avoids
    matching every stale heading in a long raw `section_path`. The returned text also
    includes segment-level metadata that is useful for profile rules, such as local
    codes and table column signatures. Block source text is only included when the
    caller explicitly asks for it; block text patterns should normally inspect source
    text directly instead.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    include_block_text
        Whether to append a block segment's own source text.
    segment
        DocumentIR segment.

    Returns
    -------
    str
        Bounded selection context text.
    """

    parts = [
        heading_ref.text
        for heading_ref in _limit_nearby_headings(
            document_profile=document_profile, section_path=segment.section_path
        )
    ]

    if segment.local_code:
        parts.append(str(segment.local_code))

    if getattr(segment, "columns_signature", None):
        parts.append(str(segment.columns_signature))

    if include_block_text and segment.kind == "block":
        parts.append(_build_block_source_text(segment.model_dump(mode="json")))

    return "\n".join(part for part in parts if part)


def build_llm_extraction_windows(
    *,
    document_ir: DocumentIR,
    document_profile: DocumentProfile,
    save_fp: Path,
    selected_segments: Sequence[SelectedExtractionSegment],
) -> list[ExtractionWindow]:
    """Build LLM-ready Academic Standards extraction windows.

    The windows are prompt payloads, not final KG objects. They include source-fidelity
    rows/text/provenance, optional table helper views when available, profile context,
    code hints, no-code merge-key hints, and the expected later SFI extraction response
    contract. Missing optional table helper fields are tolerated.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
    save_fp
        Filepath for saving the extraction windows.
    selected_segments
        Ordered selected segments from `select_extraction_segments()`.

    Returns
    -------
    list[ExtractionWindow]
        LLM-ready extraction windows in deterministic document/window order.

    Raises
    ------
    ValueError
        If no windows are produced and the document profile does not explicitly allow
        zero windows via `metadata.allow_zero_extraction_windows`.
    """

    segment_by_id = {segment.segment_id: segment for segment in document_ir.segments}
    extraction_windows: list[ExtractionWindow] = []

    for selected_segment in selected_segments:
        segment = segment_by_id.get(selected_segment.segment_id)

        if segment is None:
            raise ValueError(
                f"Selected segment_id not found in DocumentIR: {selected_segment.segment_id}"
            )

        if segment.kind == "block":
            extraction_windows.extend(
                _build_block_windows(
                    document_ir=document_ir,
                    document_profile=document_profile,
                    segment=segment,
                    selected_segment=selected_segment,
                    window_start_index=len(extraction_windows),
                )
            )
        elif segment.kind == "table":
            extraction_windows.extend(
                _build_table_windows(
                    document_ir=document_ir,
                    document_profile=document_profile,
                    segment=segment,
                    selected_segment=selected_segment,
                    window_start_index=len(extraction_windows),
                )
            )
        else:
            raise ValueError(f"Unrecognized selected segment kind: {segment.kind}")

    if not extraction_windows and not bool(
        document_profile.metadata.get("allow_zero_extraction_windows")
    ):
        raise ValueError(
            "No extraction windows were produced. This is a hard failure unless "
            "DocumentProfile.metadata.allow_zero_extraction_windows is true."
        )

    write_extraction_windows(extraction_windows=extraction_windows, save_fp=save_fp)
    return extraction_windows


def select_extraction_segments(
    *, document_ir: DocumentIR, document_profile: DocumentProfile, save_fp: Path
) -> list[SelectedExtractionSegment]:
    """Select DocumentIR segments eligible for Academic Standards extraction.

    This is a conservative source-selection stage, not an extraction stage. It does not
    decide final StandardsFrameworkItems, call the LLM, or build extraction windows. It
    only decides which stitched DocumentIR segments should be packaged into later
    LLM-ready extraction windows.

    Table selection, direct block selection, and table-context block selection are
    separate policies:

    1. Select table segments independently with `_get_table_selection_reasons(...)`.
    2. Collect nearby heading keys from the selected tables, if configured.
    3. Select block segments independently with `_get_block_selection_reasons(...)`.
    4. Select additional block segments as table context with
        `_get_block_context_selection_reasons(...)`.
    5. Materialize selected tables and blocks in original DocumentIR order so that
        selected-segment order and downstream extraction-window order remain stable.

    Parameters
    ----------
    document_ir
        Validated stitched DocumentIR.
    document_profile
        Country/document-specific KG extraction profile.
    save_fp
        Filepath for saving the selected extraction segments.

    Returns
    -------
    list[SelectedExtractionSegment]
        Ordered selected segments.

    Raises
    ------
    ValueError
        If no segments are selected and the profile does not explicitly allow zero
        windows via `metadata.allow_zero_extraction_windows` or
        `metadata.allow_zero_extraction_segments`.
    """

    selected_table_reasons: dict[str, list[str]] = {}
    selected_table_segments: list[TableSegment] = []

    for segment in document_ir.segments:
        if segment.kind != "table":
            continue

        table_reasons = _get_table_selection_reasons(
            document_profile=document_profile, segment=segment
        )

        if not table_reasons:
            continue

        selected_table_reasons[segment.segment_id] = table_reasons
        selected_table_segments.append(segment)

    context_heading_keys = _collect_nearby_heading_keys_for_tables(
        document_profile=document_profile, table_segments=selected_table_segments
    )
    selected_block_reasons: dict[str, list[str]] = {}

    for segment in document_ir.segments:
        if segment.kind != "block":
            continue

        block_reasons = _get_block_selection_reasons(
            document_profile=document_profile, segment=segment
        )
        context_reasons = _get_block_context_selection_reasons(
            context_heading_keys=context_heading_keys,
            document_profile=document_profile,
            segment=segment,
        )
        selection_reasons = unique_clean_strings([*block_reasons, *context_reasons])

        if selection_reasons:
            selected_block_reasons[segment.segment_id] = selection_reasons

    selected_segments: list[SelectedExtractionSegment] = []

    for segment in document_ir.segments:
        selection_reasons = []

        if segment.kind == "block":
            selection_reasons = selected_block_reasons.get(segment.segment_id, [])
        elif segment.kind == "table":
            selection_reasons = selected_table_reasons.get(segment.segment_id, [])

        if not selection_reasons:
            continue

        block_type = segment.block_type.value if segment.kind == "block" else None
        selected_segments.append(
            SelectedExtractionSegment(
                block_type=block_type,
                columns_signature=getattr(segment, "columns_signature", None),
                local_code=getattr(segment, "local_code", None),
                row_count=len(segment.rows) if segment.kind == "table" else None,
                section_path=_model_dump_list(segment.section_path),
                segment_id=segment.segment_id,
                segment_kind=segment.kind,
                selection_id=_deterministic_uuid(
                    f"lc:curriculum:{document_ir.doc_key}:selected_extraction_segment:{segment.kind}:{segment.segment_id}"
                ),
                selection_index=len(selected_segments),
                selection_reasons=selection_reasons,
                source_page_indexes=sorted(
                    {
                        int(provenance.page_index)
                        for provenance in segment.segment_provenance
                    }
                ),
            )
        )

    if not selected_segments and not (
        document_profile.metadata.get("allow_zero_extraction_segments")
        or document_profile.metadata.get("allow_zero_extraction_windows")
    ):
        raise ValueError(
            "No extraction segments were selected. This is a hard failure unless "
            "DocumentProfile.metadata.allow_zero_extraction_segments or "
            "allow_zero_extraction_windows is true."
        )

    write_selected_extraction_segments(
        save_fp=save_fp, selected_segments=selected_segments
    )
    return selected_segments


def write_extraction_windows(
    *, extraction_windows: Sequence[ExtractionWindow], save_fp: Path
) -> Path:
    """Write extraction windows to a JSONL artifact.

    Each output line is a validated `ExtractionWindow` serialized via Pydantic. The
    artifact is intended to be reviewed before any LLM extraction call is run.

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


def write_selected_extraction_segments(
    *, save_fp: Path, selected_segments: Sequence[SelectedExtractionSegment]
) -> None:
    """Write selected extraction segments to a JSON artifact.

    Parameters
    ----------
    save_fp
        Destination JSON path.
    selected_segments
        Ordered selected extraction segments.
    """

    counts_by_reason: dict[str, int] = {}
    counts_by_segment_kind: dict[str, int] = {}

    for segment in selected_segments:
        counts_by_segment_kind[segment.segment_kind] = (
            counts_by_segment_kind.get(segment.segment_kind, 0) + 1
        )

        for reason in segment.selection_reasons:
            counts_by_reason[reason] = counts_by_reason.get(reason, 0) + 1

    artifact = SelectedExtractionSegmentsArtifact(
        counts_by_reason=dict(sorted(counts_by_reason.items())),
        counts_by_segment_kind=dict(sorted(counts_by_segment_kind.items())),
        selected_segments=list(selected_segments),
        total_selected_segments=len(selected_segments),
    )
    write_to_json(fp=save_fp, json_info=artifact)
