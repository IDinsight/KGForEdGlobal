"""This module contains prompt templates for the knowledge graph pipeline."""

# Standard Library
from textwrap import dedent
from typing import Any, Optional

# Package Library
from skg.kgs.schemas import ExtractionWindow
from skg.schemas import CreateKGConfig
from skg.utils.general import PromptPair, json_dumps


def _build_compact_block_payload(
    extraction_window: ExtractionWindow,
) -> Optional[dict[str, Any]]:
    """Build a compact prompt-facing block payload for SFI extraction.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to compact for the prompt.

    Returns
    -------
    Optional[dict[str, Any]]
        A compact block payload for block windows, or `None` for table windows.
    """

    return (
        None
        if extraction_window.block is None
        else {
            "block_type": extraction_window.block.get("block_type"),
            "language": extraction_window.primary_language,
            "local_code": extraction_window.block.get("local_code"),
            "source_text": extraction_window.source_text,
        }
    )


def _build_compact_extraction_window_payload(
    extraction_window: ExtractionWindow,
) -> dict[str, Any]:
    """Build the compact prompt payload sent to the SFI extraction LLM.

    The persisted `ExtractionWindow` remains the complete source-faithful artifact.
    This prompt payload keeps only the fields the model needs to decide which candidate
    SFIs and auxiliary records are visible in one window.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to compact for the prompt.

    Returns
    -------
    dict[str, Any]
        Compact JSON-serializable source payload for the extraction prompt.
    """

    payload: dict[str, Any] = {
        "code_matches": [
            code_match.model_dump(mode="json")
            for code_match in extraction_window.code_matches
        ],
        "segment_kind": extraction_window.segment_kind,
        "window_id": extraction_window.window_id,
        "window_index": extraction_window.window_index,
        "window_source_segment_ids": extraction_window.source_segment_ids,
    }

    if block_payload := _build_compact_block_payload(extraction_window):
        payload["block"] = block_payload

    if table_payload := _build_compact_table_payload(extraction_window):
        payload["table"] = table_payload

    return payload


def _build_compact_filldown_context_row_payload(
    *,
    filldown_row: dict[str, Any],
    header_labels: list[str],
    raw_row: dict[str, Any],
    row_index: int,
) -> Optional[dict[str, Any]]:
    """Build helper-only filldown context cells for one table row.

    Parameters
    ----------
    filldown_row
        Filldown helper-view row aligned to the raw source row.
    header_labels
        Column labels from the final canonical header row, when available.
    raw_row
        Raw source row aligned to the filldown helper row.
    row_index
        Source table row index for this row.

    Returns
    -------
    Optional[dict[str, Any]]
        Compact helper-context row payload, or `None` when the filldown row adds no
        helper-only context beyond the raw source row.
    """

    cells: list[dict[str, Any]] = []
    raw_cells = raw_row.get("cells") or []

    for column_index, cell in enumerate(filldown_row.get("cells") or []):
        text_unit = cell.get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        if not text:
            continue

        raw_cell = raw_cells[column_index] if column_index < len(raw_cells) else {}
        raw_text_unit = raw_cell.get("text") or {}
        raw_text = str(raw_text_unit.get("text") or "").strip()
        helper_only = bool(cell.get("synthetic") or cell.get("rowspan_placeholder"))

        if text == raw_text and not helper_only:
            continue

        payload: dict[str, Any] = {
            "column_index": column_index,
            "header": (
                header_labels[column_index]
                if column_index < len(header_labels)
                else None
            ),
            "language": text_unit.get("language"),
            "source_visibility": "helper_context_only",
            "text": text,
        }

        if cell.get("rowspan_placeholder"):
            payload["rowspan_placeholder"] = True

        if cell.get("synthetic"):
            payload["synthetic"] = True

        cells.append(payload)

    if not cells:
        return None

    return {"cells": cells, "row_index": row_index}


def _build_compact_header_row_payload(
    *, header_row: dict[str, Any], header_row_index: int
) -> dict[str, Any]:
    """Build a compact prompt-facing table header row payload.

    Parameters
    ----------
    header_row
        Raw source table header row payload.
    header_row_index
        Source table header row index within the table header block.

    Returns
    -------
    dict[str, Any]
        Compact header row payload with text-bearing cells only.
    """

    cells: list[dict[str, Any]] = []

    for column_index, cell in enumerate(header_row.get("cells") or []):
        text_unit = cell.get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        if not text:
            continue

        payload: dict[str, Any] = {
            "column_index": column_index,
            "language": text_unit.get("language"),
            "source_visibility": "source_visible_header",
            "text": text,
        }

        if cell.get("col_span") is not None:
            payload["col_span"] = cell.get("col_span")

        if cell.get("row_span") is not None:
            payload["row_span"] = cell.get("row_span")

        cells.append(payload)

    return {"cells": cells, "header_row_index": header_row_index}


def _build_compact_kg_config_context(kg_config: CreateKGConfig) -> dict[str, Any]:
    """Build the compact KG config context for the extraction prompt.

    Parameters
    ----------
    kg_config
        Country/document-specific extraction configuration.

    Returns
    -------
    dict[str, Any]
        Compact KG config facts and extraction instructions needed by the LLM.
    """

    kg_config_context: dict[str, Any] = {
        "country": kg_config.country,
        "grades_or_stages": kg_config.grades_or_stages,
        "primary_language": kg_config.primary_language,
        "sfi_extraction_instructions": kg_config.as_sfi_extraction_instructions,
        "subject": kg_config.subject,
    }

    if kg_config.as_bilingual_pair_policy:
        kg_config_context["bilingual_pair_policy"] = kg_config.as_bilingual_pair_policy

    return kg_config_context


def _build_compact_row_payload(
    *,
    header_labels: list[str],
    row: dict[str, Any],
    row_index: int,
    source_visibility: str,
) -> dict[str, Any]:
    """Build a compact prompt-facing table row payload.

    Parameters
    ----------
    header_labels
        Column labels from the final canonical header row, when available.
    row
        Source or helper-view table row payload.
    row_index
        Source table row index for this row.
    source_visibility
        Visibility label for the row's cells, such as `source_visible` or
        `helper_context_only`.

    Returns
    -------
    dict[str, Any]
        Compact row payload with text-bearing cells only.
    """

    cells: list[dict[str, Any]] = []

    for column_index, cell in enumerate(row.get("cells") or []):
        text_unit = cell.get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        if not text:
            continue

        payload: dict[str, Any] = {
            "column_index": column_index,
            "header": (
                header_labels[column_index]
                if column_index < len(header_labels)
                else None
            ),
            "language": text_unit.get("language"),
            "source_visibility": source_visibility,
            "text": text,
        }

        if cell.get("rowspan_placeholder"):
            payload["rowspan_placeholder"] = True

        if cell.get("synthetic"):
            payload["synthetic"] = True

        cells.append(payload)

    return {"cells": cells, "row_index": row_index}


def _build_compact_table_payload(
    extraction_window: ExtractionWindow,
) -> Optional[dict[str, Any]]:
    """Build a compact prompt-facing table payload for SFI extraction.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to compact for the prompt.

    Returns
    -------
    Optional[dict[str, Any]]
        A compact table payload for table windows, or `None` for block windows.
    """

    table = extraction_window.table

    if table is None:
        return None

    header_labels = (
        table.header_rows_canonical[-1] if table.header_rows_canonical else []
    )
    payload: dict[str, Any] = {
        "header_rows": [
            _build_compact_header_row_payload(
                header_row=header_row, header_row_index=header_row_index
            )
            for header_row_index, header_row in enumerate(table.header_rows)
        ],
        "header_rows_canonical": table.header_rows_canonical,
        "local_code": table.local_code,
        "source_rows": [
            _build_compact_row_payload(
                header_labels=header_labels,
                row=row,
                row_index=row_index,
                source_visibility="source_visible_row",
            )
            for row_index, row in zip(table.row_indexes, table.rows)
        ],
        "table_source_policy": (
            "Quote source_text only from block.source_text, table.header_rows cell "
            "text, or table.source_rows cell text. Use header_rows_canonical to "
            "understand table structure, but prefer table.header_rows for verbatim "
            "header source_text. If an official table statement is split across "
            "adjacent source rows or cells, use all visible contributing fragments "
            "to build the candidate description and include all contributing "
            "table_row_indexes. Use source_text as a source-visible evidence quote, "
            "not as the only downstream provenance or final KG statement text. Use "
            "filldown_context_rows only to understand repeated row-span context; do "
            "not quote helper_context_only cells as source_text."
        ),
    }

    if table.rows_filldown is not None:
        filldown_context_rows = _build_filldown_context_rows(
            filldown_rows=table.rows_filldown,
            header_labels=header_labels,
            raw_rows=table.rows,
            row_indexes=table.row_indexes,
        )

        if filldown_context_rows:
            payload["filldown_context_rows"] = filldown_context_rows

    return payload


def _build_filldown_context_rows(
    *,
    filldown_rows: list[dict[str, Any]],
    header_labels: list[str],
    raw_rows: list[dict[str, Any]],
    row_indexes: list[int],
) -> list[dict[str, Any]]:
    """Build compact filldown context rows aligned to source table rows.

    Parameters
    ----------
    filldown_rows
        Filldown helper-view rows aligned to `row_indexes`.
    header_labels
        Column labels from the final canonical header row, when available.
    raw_rows
        Raw source rows aligned to `row_indexes`.
    row_indexes
        Source table row indexes included in the window.

    Returns
    -------
    list[dict[str, Any]]
        Helper-only filldown context rows. These rows are context for interpreting
        row spans and should not be quoted as source-visible evidence.
    """

    context_rows: list[dict[str, Any]] = []

    for row_index, filldown_row, raw_row in zip(row_indexes, filldown_rows, raw_rows):
        context_row = _build_compact_filldown_context_row_payload(
            filldown_row=filldown_row,
            header_labels=header_labels,
            raw_row=raw_row,
            row_index=row_index,
        )

        if context_row is not None:
            context_rows.append(context_row)

    return context_rows


def extract_sfi_candidates_from_window(
    *, extraction_window: ExtractionWindow, kg_config: CreateKGConfig
) -> PromptPair:
    """Generate the prompts for extracting candidate SFIs from one extraction window.

    Parameters
    ----------
    extraction_window
        Source-faithful LLM-ready extraction window.
    kg_config
        Country/document-specific KG extraction configuration.

    Returns
    -------
    PromptPair
        A PromptPair containing the system and user messages for the SFI extraction
        agent.
    """

    kg_config_context = _build_compact_kg_config_context(kg_config)
    user_payload = _build_compact_extraction_window_payload(extraction_window)

    system_message = dedent(
        f"""You are an Academic Standards extraction agent for a Learning Commons-shaped Knowledge Graph. Inspect exactly one compact source window and return candidate StandardsFrameworkItem records.

## Learning Commons ontology target
- A StandardsFrameworkItem (SFI) is an individual statement or structural element inside an academic standards framework.
- Extract an SFI when the source text is an official standards-framework item: either a learning expectation or an organizational grouping.
- Learning expectations are normative statements that define what learners should know, understand, demonstrate, or be able to do. Examples include standards, competencies, objectives, outcomes, content standards, performance expectations, benchmarks, and indicators.
- Organizational groupings are source-visible structural items that organize learning expectations. Examples include grades/stages, domains, strands, substrands, clusters, topics, units, themes, paliers, and similar headings when they structure the official standards hierarchy.
- Do not extract LearningComponents in this step. A LearningComponent is a granular teachable skill or concept that breaks down a broader SFI for instruction, activities, assessment items, or lesson planning. LearningComponents are created later and may support SFIs.
- Do not extract final relationships. Final hasChild, supports, buildsTowards, relatesTo, hasEducationalAlignment, and other edges are resolved in later stages.

## Scope
- Extract candidate SFIs only from the provided compact source window.
- Return zero SFI candidates when the window contains front matter, examples only, teacher guidance only, activities only, resources only, assessment suggestions only, or unrelated content.
- Do not infer hierarchy or relationships in this step. Extract only SFI candidates directly visible in this compact source window; final hasChild relationships are resolved later from finalized SFIs and source provenance.
- Extract grouping SFIs only when the grouping label itself is visible in this window. Do not add absent grade, strand, sub-strand, or parent context.
- Use the curriculum-specific extraction KG config below to adapt the generic ontology rules to this document.
- If the generic instructions and the curriculum-specific runtime config conflict, follow the curriculum-specific runtime config. The runtime config is authoritative for document-specific extraction policy.

## Curriculum-specific KG extraction config
{json_dumps(kg_config_context)}

## Candidate classification policy
- Use normalized_statement_type="Standard Grouping" for source-visible organizational groupings that should become SFI grouping nodes.
- Use normalized_statement_type="Standard" for source-visible learning expectations that should become SFI standard nodes.
- Use normalized_statement_type="Other" rarely, only when the KG config explicitly says a visible framework item should be retained as an SFI but it is neither a grouping nor a learning expectation.
- Do not classify examples, exemplars, competencies lists that describe cross-cutting skills, activities, assessment suggestions, resources, pedagogical notes, durations, teacher guidance, or learning-material content as SFIs unless the KG config explicitly says they are standards-framework items.

## Candidate field policy
- candidate_id must be unique within this window, such as sfi_1, sfi_2, etc.
- description should preserve the source-language wording of the SFI. For learning expectations, use the official statement text. For groupings, use the grouping label or heading text.
- statement_type should be the source-facing role when visible, such as Grade, Stage, Strand, Domain, Cluster, Content Standard, Indicator, Competency, Objective, or Outcome. If no source-facing role is visible, use a concise best-effort role consistent with the source window and KG config.
- statement_code should be the official/source-visible code when present. Use null when no code is visible.
- language should use the source language tag when visible; otherwise use the KG config primary language.
- confidence should reflect how clearly the source window supports the candidate.
- table_header_indexes should be populated only for candidates whose evidence comes from table.header_rows.
- table_row_indexes should be populated only for candidates whose evidence comes from table.source_rows.
- description should contain the complete source-visible SFI statement or grouping label, including visible continuation fragments when an official statement is split across adjacent table rows or cells.
- source_text is a source-visible evidence quote for validation. It is not the final canonical KG statement text and it is not the only downstream provenance.
- Keep source_text concise but sufficient. For coded table statements, quote only official code and statement text, not examples, exemplars, teacher guidance, activities, or competencies. When a statement is split across multiple visible rows/cells, quote the complete visible statement only if the contributing fragments can be represented as a source-visible excerpt; otherwise quote the strongest exact visible fragment and rely on table_row_indexes/table_header_indexes for downstream source recovery.

## Source fidelity rules
- Preserve source-language text. Do not translate.
- For every candidate and auxiliary record, source_text must be a verbatim source-visible excerpt from block.source_text, table.header_rows cell text, or table.source_rows cell text.
- If a table statement visibly continues across adjacent source rows or cells, include every contributing table_row_index and assemble the complete official statement in description from those visible fragments. Do not truncate description at the first row/cell.
- The final KG-building stages recover full source provenance from window_id, window_source_segment_ids, table_row_indexes, table_header_indexes, and the persisted ExtractionWindow/DocumentIR. Do not use source_text to carry hidden context, parentage, or non-visible text.
- Use code_matches as evidence, not as final KG nodes.
- Table headers are source-visible structural evidence. When the curriculum-specific KG config says a table-header label is an official grouping SFI, extract it as a Standard Grouping candidate.
- For table-row-derived SFI candidates, table_row_indexes must be non-empty and must use the visible table.source_rows[].row_index values that support the candidate.
- For table-header-derived SFI candidates, table_header_indexes must be non-empty and must use the visible table.header_rows[].header_row_index values that support the candidate; table_row_indexes may be empty for these candidates.
- If a table candidate is supported by both a header and body rows, include both table_header_indexes and table_row_indexes.
- Treat table.filldown_context_rows as helper context only. These cells repeat row-span context for interpretation, but they are not source-visible evidence. Do not quote helper_context_only cells as candidate source_text or auxiliary source_text unless the same text is also visible in block.source_text, table.header_rows, or table.source_rows.

## Output contract
Return exactly one SFIExtractionResult object as structured JSON only. Follow the schema exactly and do not include extra fields.
Copy window_id, window_index, and window_source_segment_ids exactly from the compact source window.
Keep extraction_notes short; use them only for window-level extraction issues, not to summarize examples, competencies, or activities.
Return auxiliary candidates only when they clarify why prominent source-visible text was not extracted as an SFI; do not list ordinary examples, activities, competencies, or guidance notes.
Do not emit auxiliary candidates for routine front matter, ordinary examples, or repeated core-competency lists unless they are unusually ambiguous or likely to be mistaken for an SFI.
        """
    )

    user_message = dedent(
        f"""Extract candidate SFIs from this compact source window.

Return structured JSON only. Do not include markdown.

## Compact source window JSON
{json_dumps(user_payload)}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
