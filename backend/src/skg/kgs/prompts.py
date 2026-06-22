"""This module contains prompt templates for the knowledge graph pipeline."""

# Standard Library
from textwrap import dedent

# Package Library
from skg.kgs.schemas import DocumentProfile, ExtractionWindow
from skg.utils.general import PromptPair, json_dumps


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

    output_schema_guidance = {
        "auxiliary_candidates": [
            {
                "auxiliary_id": "aux_1",
                "auxiliary_type": "example|exemplar|guidance|activity|descriptor|core_competency|other",
                "language": document_profile.primary_language,
                "rationale": "why this is auxiliary and not a StandardsFrameworkItem",
                "related_candidate_ids": ["sfi_1"],
                "source_text": "verbatim source quote",
            }
        ],
        "extraction_notes": [
            "brief notes, especially when no SFI candidates are found"
        ],
        "sfi_candidates": [
            {
                "ancestor_context_references": [
                    {
                        "confidence": 0.5,
                        "normalized_statement_type": "Standard Grouping",
                        "rationale": "source-grounded explanation",
                        "reference_kind": "ancestor_context_hint",
                        "source_text": "verbatim source-visible context text",
                        "statement_code": None,
                        "statement_type": "source-facing role, if visible",
                    }
                ],
                "candidate_id": "sfi_1",
                "confidence": 0.5,
                "description": "candidate SFI description in source language",
                "language": document_profile.primary_language,
                "metadata": {},
                "normalized_statement_type": "Standard|Standard Grouping|Other",
                "parent_references": [],
                "source_text": "verbatim source quote",
                "statement_code": None,
                "statement_type": "source-facing role/type label",
                "table_row_indexes": [],
            }
        ],
        "window_id": extraction_window.window_id,
        "window_index": extraction_window.window_index,
        "window_source_segment_ids": extraction_window.source_segment_ids,
    }

    profile_context = {
        "bilingual_pair_policy": document_profile.bilingual_pair_policy,
        "code_parent_rules": document_profile.code_parent_rules,
        "code_patterns": document_profile.code_patterns,
        "country": document_profile.country,
        "framework_title": document_profile.framework_title,
        "grades_or_stages": document_profile.grades_or_stages,
        "primary_language": document_profile.primary_language,
        "repeated_statement_policy": document_profile.repeated_statement_policy,
        "sfi_extraction_instructions": document_profile.sfi_extraction_instructions,
        "subject": document_profile.subject,
        "synthetic_merge_key_fields": document_profile.synthetic_merge_key_fields,
    }

    system_message = dedent(
        f"""You are an Academic Standards extraction agent. Inspect exactly one source-faithful extraction window and return structured SFI candidate records.

## Scope
- Extract candidate StandardsFrameworkItems only from the provided extraction window.
- Return zero SFI candidates when the window contains front matter, examples only, teacher guidance only, activities only, resources only, or unrelated content.
- Do not create final IDs, final hasChild edges, LearningComponents, LearningProgressions, buildsTowards, relatesTo, or supports relationships.
- Parent/context references are optional source-grounded hints only. They are not final graph edges.
- Do not invent missing hierarchy. If parent/context text is not visible in the window, omit it or add an extraction note.

## Curriculum-specific extraction profile
{json_dumps(profile_context)}

## Candidate type policy
- Use normalized_statement_type="Standard Grouping" for grouping/organizing curriculum items that should become SFI grouping nodes.
- Use normalized_statement_type="Standard" for normative expectations learners should know, understand, or demonstrate.
- Use normalized_statement_type="Other" only when profile policy says the item may be retained as an SFI but it is neither a grouping nor a normative expectation.
- Treat examples, exemplars, competencies, activities, assessment suggestions, resources, pedagogical notes, duration, and teacher guidance as auxiliary unless the profile explicitly says otherwise.

## Source fidelity rules
- Preserve source-language text. Do not translate.
- statement_code is optional. Use null when no official/source-visible code is present.
- For every candidate, source_text must be a verbatim quote or faithful source-visible cell/block excerpt from the extraction window.
- For table-derived candidates, include the relevant table_row_indexes from the window payload when available.
- Use code_matches, code_parent_hints, table headers, rows, rows_grid, rows_filldown, row_provenance, and deterministic_hints as evidence, not as final KG nodes.

## Output contract
Return one object matching the schema shape below. Copy window_id, window_index, and window_source_segment_ids exactly.

{json_dumps(output_schema_guidance)}
        """
    )

    user_payload = extraction_window.model_dump(mode="json")
    user_message = dedent(
        f"""Extract candidate SFIs from this extraction window.

Return structured JSON only. Do not include markdown.

## ExtractionWindow JSON
{json_dumps(user_payload)}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
