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
    *, header_labels: list[str], row: dict[str, Any], row_index: int
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

    # Select preferred table row view: filldown -> grid -> raw rows. The selected view
    # name is intentionally omitted from the prompt because it is debug metadata;
    # cell-level synthetic flags carry the only helper-view signal needed by the LLM.
    if table.rows_filldown is not None:
        row_view = table.rows_filldown
    elif table.rows_grid is not None:
        row_view = table.rows_grid
    else:
        row_view = table.rows

    header_labels = (
        table.header_rows_canonical[-1] if table.header_rows_canonical else []
    )

    return {
        "header_rows_canonical": table.header_rows_canonical,
        "local_code": table.local_code,
        "rows": [
            _build_compact_row_payload(
                header_labels=header_labels, row=row, row_index=row_index
            )
            for row_index, row in zip(table.row_indexes, row_view)
        ],
    }


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
- For every candidate and auxiliary record, source_text must be a verbatim quote or faithful source-visible excerpt from block.source_text or table row cell text.
- For table-derived candidates, table_row_indexes must use the visible table.rows[].row_index values.
- Use code_matches, code_parent_hints, table headers, and selected table row text as evidence, not as final KG nodes.
- Use code_parent_hints only as deterministic evidence for likely hierarchy. Emit a parent_reference from a code-parent hint only when the parent code or parent text is also visible in the actual block/table source text in this compact window. Do not use code-parent hints alone as source_text.
- Prefer source-visible row text over synthetic/filldown helper text for source_text when both are available. Helper text may be used as context when it is visible in the compact source window.

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
