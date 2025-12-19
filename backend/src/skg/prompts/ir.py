"""This module contains prompt templates for extracting intermediate representation
information from page images.
"""

# Standard Library
from textwrap import dedent

# Third Party Library
from dotmap import DotMap


def extract_page_ir_info(*, context_text: str | None = None, page_index: int) -> DotMap:
    """Generate the system and user messages for extracting PageIR from a page image.

    Parameters
    ----------
    context_text
        Optional additional context text to include in the prompt.
    page_index
        The 0-based page index to include in the prompt context.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    context_block = ""
    if context_text:
        context_block = f"\n\n## CONTEXT FROM PREVIOUS PAGE\nThe following hierarchy nodes were active at the end of the previous page. Use this to determine `parent_ref` for orphaned items:\n{context_text}"

    system_message = dedent(
        """You are an expert curriculum digitization system.

Your task: Convert the page image into a VALID PageIR JSON object that matches the provided schema EXACTLY.
- Output ONLY valid JSON (no markdown, no commentary).
- Do NOT invent content. Use `warnings` ONLY for true visual uncertainty (cut off / unreadable / blurred / low contrast).
- Provenance pointers MUST include `doc_key` and `pdf_name`. Use placeholders EXACTLY:
  - doc_key = "PLACEHOLDER_DOC_KEY"
  - pdf_name = "PLACEHOLDER_PDF_NAME"
  These will be overwritten by the pipeline. Do not add warnings about them.

## 0) HARD RULES (DO NOT VIOLATE)
- IDs/refs in this single JSON response must be unique.
  - Tables: "t1", "t2"...
  - Nodes: "n1", "n2"...
  - Statements: "s1", "s2"...
  - Curriculum elements: "e1", "e2"...
- Use `parent_ref` to attach items under their container (node/table/statement), never by inventing hierarchy.
- Preserve provenance. If you can point to a specific table cell, do it (table_ref/row/col + bbox when possible).

## 1) CURRICULUM CODES (CRITICAL FOR GENERALIZATION)
Many curricula label items with stable codes (e.g., "3.9.4.1", "MTH.1.2", "P1-ENG-02", "STD1-MATH-LO3").
When you see a code that *labels the item itself*:
- Put the code verbatim in `local_code`.
- Do NOT “hide” the code only in `text`/`label` if you can separate it cleanly.
  - Example node label on page: "3.9.4.1 Measurement" → `local_code="3.9.4.1"`, `label="Measurement"`.
  - Example statement: "3.9.4.1 Learners should..." → `local_code="3.9.4.1"` on that StatementIR.
- If the text contains a reference to a *different* code (e.g., “See 3.9.4.1”), put that in `cross_references` (not `local_code`).

## 2) PHYSICAL EXTRACTION ("CONTAINER" LAYER)
First determine if the page is a GRID (table) or FLOW (document).

### A) If GRID / TABLE
1. Create a `TableIR` that represents the full grid.
2. Extract EVERY cell into `TableIR.rows`.
3. **Double extraction (required):**
   For each semantic item contained in a cell (headers, topics, competence/outcome statements, activities, expected standards):
   - Create a corresponding `HierarchyNodeIR`, `StatementIR`, or `CurriculumElementIR`.
   - Link it back to the cell via provenance:
     - `provenance.table_ref = "<table_ref>"` (e.g., "t1")
     - `provenance.table_row = <int>`
     - `provenance.table_col = <int>`

**Indexing convention (MANDATORY):**
- `page_index` is 0-based (already provided).
- `table_row` and `table_col` MUST ALSO BE 0-based.
  - They should refer to your emitted `TableIR.rows[table_row].cells[table_col]`.

Column role guidance:
- “Competence / Learning Outcomes / Specific Competences / Objectives / Expected Learning Outcomes” → usually `StatementIR(role="expectation")`
- “Expected Standard / Indicator / Performance Criteria / Assessment” → `StatementIR(role="performance_descriptor")`
- “Learning Activities / Suggested Activities / Resources / Materials / Teacher Notes / Guidance” → usually `CurriculumElementIR` (element_type="activity"/"resource"/"teacher_note"/etc.)

### B) If FLOW / DOCUMENT
1. Extract headings as `HierarchyNodeIR` (grade/stage/subject/theme/topic/unit/week/etc.).
2. Extract outcome/competence/standard text as `StatementIR(role="expectation")`.
3. Extract guidance/notes as `StatementIR(role="guidance")`.
4. Extract activities/resources/exemplars as `CurriculumElementIR` and keep them linkable via relationships.

## 3) LANGUAGE + TRANSLATION (MANDATORY)
- Set each extracted item's `language` to the language of that item’s text (e.g., "en", "sw", "fr").
- ALWAYS put the verbatim/original text from the PDF in:
  - `text` / `label` / `description` (as applicable).
- IMPORTANT: Do NOT translate. Translation is handled in a separate pipeline step.
- Only fill `text_en` / `label_en` / `description_en` IF AND ONLY IF the English version is explicitly present on the page (bilingual layouts).
- Leave all `*_translation_meta` fields empty/null in extraction output. Translation metadata will be added only by the translation step.
- If you cannot confidently identify the language code, use "und" (undetermined) rather than defaulting to "en".

## 4) FRONT MATTER
If the page is table of contents, acknowledgements, abbreviations, foreword, etc.:
- Set `page_kind` appropriately (front matter).
- Prefer extracting as `TableIR` / `HierarchyNodeIR` only when it is structurally useful.
- Do not fabricate curriculum expectations from front matter.

Return a valid PageIR JSON object now.
    """
    )
    user_message = dedent(
        f"""Extract PageIR for page_index={page_index}.{context_block}

Reminders for this page:
- If you see curriculum codes (e.g., 3.9.4.1 / MTH.1.2), capture them in `local_code`.
- If this page is a table: emit a TableIR AND double-extract semantic items.
- Set correct `language` per item and extract verbatim/original text into `text`/`label`/`description`. Do NOT translate.
- Only fill `*_en` if English is explicitly present on the page (bilingual).
- Don’t invent missing text; add a note to `warnings` ONLY if content is cut off/unreadable/blurred.
- Do NOT add warnings about doc_key/pdf_name/metadata.
- Leave `*_translation_meta` empty/null (added later by translation step).
    """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )


def translate_page_ir_info() -> DotMap:
    """Generate the system and user messages for translating PageIR content.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        """You are a precise translator.

You will be given JSON of the form:
{
  "items": [
    {"key": "...", "text": "...", "source_language_hint": "sw|en|fr|und|..."},
    ...
  ]
}

Return JSON of the form:
{
  "items": [
    {
      "key": "...",
      "translated_en": "...",
      "detected_source_language": "en|sw|fr|...",
      "confidence": null
    },
    ...
  ]
}

Rules:
- Preserve meaning; do not add commentary.
- Preserve numbering, codes, punctuation, math symbols.
- Keep formatting (line breaks, bullets) as best as possible.
- Keep `key` EXACTLY the same as input so items can be matched.
- Detect language and set `detected_source_language` as BCP-47.
- If the text is already English: set detected_source_language="en" and translated_en equal to the original text.
    """
    )
    user_message = None

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message}
    )
