"""This module contains prompt templates for the knowledge graph pipeline."""

# Standard Library
from textwrap import dedent
from typing import Any, Optional

# Package Library
from skg.kgs.schemas import DocumentProfile, ExtractionWindow
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

    if extraction_window.block is None:
        return None

    return {
        "block_type": extraction_window.block.get("block_type"),
        "language": extraction_window.primary_language,
        "local_code": extraction_window.block.get("local_code"),
        "source_text": extraction_window.source_text,
    }


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
        "primary_language": extraction_window.primary_language,
        "segment_kind": extraction_window.segment_kind,
        # Omit geometry-heavy fields by mapping only required keys.
        "source_provenance": [
            {
                "boundary": record.get("boundary"),
                "item_addr": record.get("item_addr"),
                "item_index": record.get("item_index"),
                "local_code": record.get("local_code"),
                "page_index": record.get("page_index"),
                "repeats_header": record.get("repeats_header"),
            }
            for record in extraction_window.source_provenance
        ],
        "window_id": extraction_window.window_id,
        "window_index": extraction_window.window_index,
        "window_source_segment_ids": extraction_window.source_segment_ids,
    }

    if block_payload := _build_compact_block_payload(extraction_window):
        payload["block"] = block_payload

    if table_payload := _build_compact_table_payload(extraction_window):
        payload["table"] = table_payload

    return payload


def _build_compact_document_profile_context(
    document_profile: DocumentProfile,
) -> dict[str, Any]:
    """Build the compact document profile context for the extraction prompt.

    Parameters
    ----------
    document_profile
        Country/document-specific extraction profile.

    Returns
    -------
    dict[str, Any]
        Compact document profile facts and extraction instructions needed by the LLM.
    """

    profile_context: dict[str, Any] = {
        "country": document_profile.country,
        "grades_or_stages": document_profile.grades_or_stages,
        "primary_language": document_profile.primary_language,
        "sfi_extraction_instructions": document_profile.sfi_extraction_instructions,
        "subject": document_profile.subject,
    }

    if document_profile.bilingual_pair_policy:
        profile_context["bilingual_pair_policy"] = (
            document_profile.bilingual_pair_policy
        )

    return profile_context


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

    cells = []

    for column_index, cell in enumerate(row.get("cells") or []):
        text_unit = cell.get("text") or {}
        text = str(text_unit.get("text") or "").strip()

        # Skip cells that don't contain any text.
        if not text:
            continue

        # Build the base payload for the text-bearing cell.
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

        # Optionally add boolean flags if they are present and truthy.
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

    # Select preferred table row view: filldown -> grid -> raw rows.
    if table.rows_filldown is not None:
        row_view_name, row_view = "rows_filldown", table.rows_filldown
    elif table.rows_grid is not None:
        row_view_name, row_view = "rows_grid", table.rows_grid
    else:
        row_view_name, row_view = "rows", table.rows

    return {
        "columns_signature": table.columns_signature,
        "header_rows_canonical": table.header_rows_canonical,
        "local_code": table.local_code,
        "row_view": row_view_name,
        "rows": [
            _build_compact_row_payload(
                header_labels=(
                    table.header_rows_canonical[-1]
                    if table.header_rows_canonical
                    else []
                ),
                row=row,
                row_index=row_index,
            )
            for row_index, row in zip(table.row_indexes, row_view)
        ],
    }


def extract_sfi_candidates_from_window(
    *, document_profile: DocumentProfile, extraction_window: ExtractionWindow
) -> PromptPair:
    """Generate the prompts for extracting candidate SFIs from one extraction window.

    Parameters
    ----------
    document_profile
        Country/document-specific extraction profile.
    extraction_window
        Source-faithful LLM-ready extraction window.

    Returns
    -------
    PromptPair
        A PromptPair containing the system and user messages for the SFI extraction
        agent.
    """

    profile_context = _build_compact_document_profile_context(document_profile)
    user_payload = _build_compact_extraction_window_payload(extraction_window)

    system_message = dedent(
        f"""You are an Academic Standards extraction agent. Inspect exactly one compact source window and return structured SFI candidate records.

## Scope
- Extract candidate StandardsFrameworkItems only from the provided compact source window.
- Return zero SFI candidates when the window contains front matter, examples only, teacher guidance only, activities only, resources only, or unrelated content.
- Do not create final IDs, final hasChild edges, LearningComponents, LearningProgressions, buildsTowards, relatesTo, or supports relationships.
- Parent/context references are optional source-grounded hints only. They are not final graph edges.
- Do not invent missing hierarchy. If parent/context text is not visible in the compact source window, omit it or add an extraction note.

## Curriculum-specific extraction profile
{json_dumps(profile_context)}

## Candidate type policy
- Use normalized_statement_type="Standard Grouping" for grouping/organizing curriculum items that should become SFI grouping nodes.
- Use normalized_statement_type="Standard" for normative expectations learners should know, understand, or demonstrate.
- Use normalized_statement_type="Other" only when profile policy says the item may be retained as an SFI but it is neither a grouping nor a normative expectation.
- Treat examples, exemplars, competencies, activities, assessment suggestions, resources, pedagogical notes, duration, and teacher guidance as auxiliary unless the profile explicitly says otherwise.
- Return auxiliary candidates only when useful for explaining why visible source text is not an SFI; do not exhaustively list every example, activity, or competency.

## Source fidelity rules
- Preserve source-language text. Do not translate.
- statement_code is optional. Use null when no official/source-visible code is present.
- For every candidate and auxiliary record, source_text must be a verbatim quote or faithful source-visible excerpt from block.source_text or table row cell text.
- For table-derived candidates, table_row_indexes must use the visible table.rows[].row_index values.
- Use code_matches, code_parent_hints, table headers, and selected table row text as evidence, not as final KG nodes.

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
