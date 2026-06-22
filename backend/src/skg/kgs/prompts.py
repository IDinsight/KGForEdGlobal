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
        "code_parent_hints": [
            code_parent_hint.model_dump(mode="json")
            for code_parent_hint in extraction_window.code_parent_hints
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


def _build_compact_kg_config_context(
    kg_config: CreateKGConfig,
) -> dict[str, Any]:
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
        "header_rows_canonical": table.header_rows_canonical,
        "local_code": table.local_code,
        "source_rows": [
            _build_compact_row_payload(
                header_labels=header_labels,
                row=row,
                row_index=row_index,
                source_visibility="source_visible",
            )
            for row_index, row in zip(table.row_indexes, table.rows)
        ],
        "table_source_policy": (
            "Quote source_text only from header_rows_canonical or source_rows cells. "
            "Use filldown_context_rows only to understand repeated row-span context; "
            "do not quote helper_context_only cells as source_text."
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
- Parent/context references are optional source-grounded hints only. They are not final graph edges.
- Do not invent missing hierarchy. If parent/context text is not visible in the compact source window, omit it or add an extraction note.
- Use the curriculum-specific extraction KG config below to adapt the generic ontology rules to this document.

## Curriculum-specific KG extraction config
{json_dumps(kg_config_context)}

## Candidate classification policy
- Use normalized_statement_type="Standard Grouping" for source-visible organizational groupings that should become SFI grouping nodes.
- Use normalized_statement_type="Standard" for source-visible learning expectations that should become SFI standard nodes.
- Use normalized_statement_type="Other" rarely, only when the KG config explicitly says a visible framework item should be retained as an SFI but it is neither a grouping nor a learning expectation.
- Do not classify examples, exemplars, competencies lists that describe cross-cutting skills, activities, assessment suggestions, resources, pedagogical notes, durations, teacher guidance, or learning-material content as SFIs unless the KG config explicitly says they are standards-framework items.
- Return auxiliary candidates only when useful for explaining why visible source text is not an SFI; do not exhaustively list every example, activity, competency, or guidance note.

## Candidate field policy
- candidate_id must be unique within this window, such as sfi_1, sfi_2, etc.
- description should preserve the source-language wording of the SFI. For learning expectations, use the official statement text. For groupings, use the grouping label or heading text.
- statement_type should be the source-facing role when visible, such as Grade, Stage, Strand, Domain, Cluster, Content Standard, Indicator, Competency, Objective, or Outcome. If no source-facing role is visible, use a concise best-effort role consistent with the source window and KG config.
- statement_code should be the official/source-visible code when present. Use null when no code is visible.
- language should use the source language tag when visible; otherwise use the KG config primary language.
- confidence should reflect how clearly the source window supports the candidate.

## Source fidelity rules
- Preserve source-language text. Do not translate.
- For every candidate and auxiliary record, source_text must be a verbatim quote or faithful source-visible excerpt from block.source_text, table.header_rows_canonical, or table.source_rows cell text.
- For table-derived SFI candidates, table_row_indexes must be non-empty and must use the visible table.source_rows[].row_index values that support the candidate.
- Use code_matches, code_parent_hints, table headers, and table.source_rows text as evidence, not as final KG nodes.
- Use code_parent_hints only as deterministic evidence for likely hierarchy. Emit a parent_reference from a code-parent hint only when the parent code or parent text is also visible in block.source_text, table.header_rows_canonical, or table.source_rows. Do not use code-parent hints alone as source_text.
- Treat table.filldown_context_rows as helper context only. These cells repeat row-span context for interpretation, but they are not source-visible evidence. Do not quote helper_context_only cells as candidate source_text, auxiliary source_text, or parent/context reference source_text unless the same text is also visible in block.source_text, table.header_rows_canonical, or table.source_rows.

## Output contract
Return one SFIExtractionResult object as structured JSON only. Copy window_id, window_index, and window_source_segment_ids exactly from the compact source window.
Each SFI candidate must include candidate_id, confidence, description, language, metadata, normalized_statement_type, parent_references, ancestor_context_references, source_text, statement_code, statement_type, and table_row_indexes.
Each auxiliary candidate must include auxiliary_id, auxiliary_type, language, rationale, related_candidate_ids, and source_text.
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
