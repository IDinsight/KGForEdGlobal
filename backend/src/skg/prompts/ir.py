""" "This module contains prompt templates for extracting intermediate representation
information from page images.
"""

# Standard Library
from textwrap import dedent

# Third Party Library
from dotmap import DotMap


def extract_page_ir_info(*, page_index: int) -> DotMap:
    """Generate the system and user messages for extracting PageIR from a page image.

    Parameters
    ----------
    page_index
        The 0-based page index to include in the prompt context.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        f"""You are an expert curriculum digitization system.
Your task: Convert the page image into a VALID PageIR JSON object that matches the provided schema EXACTLY.

## 1. IDENTITY & REFS (CRITICAL)
- Generate SIMPLE, short local IDs. Do NOT include page numbers or long prefixes.
  - Tables: "t1", "t2"...
  - Nodes: "n1", "n2"...
  - Statements: "s1", "s2"...
  - Curriculum Elements: "e1", "e2"...
- IDs must be unique within this JSON response.
- `parent_ref`: If an item belongs to a container (e.g., a "Topic" node or "Table 1"), set this field.

## 2. PHYSICAL EXTRACTION (The "Container" Layer)
Analyze the page layout. Is it a GRID (Table) or a FLOW (Document)?

### A. If GRID / TABLE (e.g., columns for "Topic", "Outcomes", "Activities"):
1. Create a `TableIR` covering the full grid.
2. Extract EVERY cell into `TableIR.rows`.
   - `table_kind`: "data" (if it holds curriculum content) or "layout".
3. **Double Extraction**: For every semantic item found *inside* a cell, create a corresponding `StatementIR` or `CurriculumElementIR`.
   - Link these items back to the table using `provenance.table_ref="t1"`, `table_row=X`, `table_col=Y`.
   - This creates a bridge: Table (Physical) -> Statement (Semantic).

### B. If FLOW / DOCUMENT (e.g., Centered Headers, Paragraphs, Lists):
1. Extract Headers/Titles as `HierarchyNodeIR`.
   - Use `node_type` to classify (e.g., "theme", "topic", "grade", "subject").
2. Extract Lists/Paragraphs under those headers as `StatementIR` or `CurriculumElementIR`.
   - Set `parent_ref` to the nearest header's ID ("n1").

## 3. SEMANTIC CLASSIFICATION (The "Meaning" Layer)
Decide what each extracted text block represents using these definitions:

### A. `HierarchyNodeIR` (Grouping)
- Containers that structure the curriculum.
- Examples: Grade, Stage, Subject, Theme, Strand, Topic, Unit, Week.
- Action: Set `label` to the title.

### B. `StatementIR` (Normative / "Must Learn")
- The core requirements or standards.
- Roles (Choose ONE):
  - "expectation": The outcome, objective, competence, or standard.
  - "performance_descriptor": How to assess it (indicators, criteria, benchmarks).
  - "guidance": Pedagogical advice, teacher notes, prerequisites.

### C. `CurriculumElementIR` (Instructional / "How to Teach")
- Supporting materials, activities, or resources.
- Types (Choose ONE):
  - "activity": Learning activities, suggested tasks.
  - "resource": Materials, books, tools.
  - "assessment": Sample questions, tests.
  - "example": Illustrative examples.
  - "teacher_note": Tips for the teacher.

## 4. LANGUAGE & TRANSLATION
- **Original Text**: Extract the EXACT text from the page into `text` (for statements/elements) or `label` (for nodes). Preserve original language (e.g., Swahili, French).
- **Translation**: If the content is NOT in English:
  - Translate it into clear, standard English.
  - Populate `text_en` (for statements/elements) or `label_en` (for nodes) with the translation.
  - If the text is already English, leave `*_en` fields null.

## 5. PROVENANCE & FIDELITY
- `provenance`: REQUIRED for every item.
  - `page_index`: {page_index}
  - `bbox`: [x0, y0, x1, y1] (tightly bounding the text ink, in image pixels).
  - `doc_key` / `pdf_name`: Leave as "UNKNOWN" (post-processing fixes this).
- **Text**: Extract VERBATIM. Do not summarize. Preserve spelling/punctuation.
- **Nulls**: If a field is not present (e.g. `confidence`, `sequence`), omit it or use null. Do not guess.

Output strictly valid JSON matching the `PageIR` schema.
"""
    )

    user_message = dedent(
        f"""Extract PageIR for page_index={page_index}.

### Layout Hints for this Page:
- If you see columns like "Topic", "Sub-topic", "Competences" -> Extract as `TableIR` AND mapped `HierarchyNodeIR` / `StatementIR`.
- If you see headers like "Theme 1:", "Sub-theme 2:" -> Extract as `HierarchyNodeIR`.
- If you see lists of outcomes -> Extract as `StatementIR` (role="expectation").
- If you see "Suggested Activities" -> Extract as `CurriculumElementIR` (type="activity").
- **Language**: If the text is non-English, remember to populate `text` (original) and `text_en` (translation).

Warning: Do not invent content. If the text is cut off or unreadable, extract what you can and add a note to `warnings`.
"""
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
