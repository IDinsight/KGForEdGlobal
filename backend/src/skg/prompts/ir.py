"""This module contains prompt templates for extracting intermediate representation
information from page images.
"""

# Standard Library
from textwrap import dedent
from typing import Optional

# Third Party Library
from dotmap import DotMap


def stage1_extraction_prompts(
    *,
    country: str,
    languages: list[str],
    page_index: int,
    year: Optional[int] = None,
) -> DotMap:
    """Generate the prompts for Stage 1: Atomic Page Extraction.

    Parameters
    ----------
    country
        The country of origin for the document.
    languages
        List of expected languages (e.g., ['en', 'sw']).
    page_index
        The 0-based page index of the image being processed.
    year
        The document year, if known.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    doc_context = f"from {country}"
    if year:
        doc_context += f" ({year})"
    lang_context = ", ".join(languages)

    system_message = dedent(
        f"""You are a high-fidelity curriculum digitization agent. Your goal is to convert a single curriculum page image {doc_context} into a strict PageIR JSON object.

## DOCUMENT CONTEXT
- **Origin**: {country}
- **Expected Languages**: {lang_context}. You may encounter mixed-language content.
- **Task**: Provide raw visual evidence of the page's structure and contents. Do not translate. Extract text exactly as it appears in the image.

## HARD RULES (DO NOT VIOLATE)
1. NO HALLUCINATION: Extract only what is visually present. Do not complete sentences that are cut off at the page edge.
2. READING ORDER: Populate the `items` list in strict visual reading order (multi-column: left-to-right by column, then down).
3. VERBATIM TEXT: Do not fix typos. Do not translate.
4. COORDINATE SPACE: Use `coord_space="px"`.
5. If you can’t confidently provide a bbox, set bbox=null.
6. Output ONLY valid JSON (no markdown, no code fences).

## STRUCTURAL BOUNDARY DETECTION
You must explicitly identify items that "bleed" across page boundaries:
- TABLE BORDERS: Set `boundary="resumed"` if top border missing; `boundary="truncated"` if bottom border missing.
- TEXT CONTINUITY: Set `boundary="truncated"` if a block ends mid-sentence or is visibly cut off.
- PAGE STATE: Set `boundary_state` to reflect continuity for the overall page (from_prev / to_next / both / standalone).

## MULTI-PAGE CONTINUATION MARKERS (IMPORTANT)
Some documents mark continued content with labels like "(Continued)", "CONT'D", "Table 3 (continued)", or "Continued from previous page".
- Extract these labels VERBATIM as separate CurriculumBlock items in reading order.
- Use `block_type="caption"` if the label clearly refers to a nearby table/figure.
- Use `block_type="artifact"` if the label is part of a running header/footer or repeated page furniture.
- Do NOT insert continuation labels into table cells unless the text is visibly inside the table grid.

## BLOCK TYPE CLASSIFICATION (REQUIRED FOR EVERY CurriculumBlock)
For every CurriculumBlock, you MUST set `block_type` to exactly one of:
- **artifact**: page numbers, running headers/footers, repeating document titles, copyright lines, ministry footers.
- **caption**: a short label that clearly describes a nearby table/figure (e.g., “Table 2: …”, “Figure 1: …”).
- **heading**: section titles, topic headers, visually prominent headings (often bold/uppercase/larger).
- **key_value**: explicit “Label: Value” style metadata lines (e.g., “Subject: Mathematics”).
- **list**: bulleted/numbered outlines. Use `list_items` and set `text=null`.
- **paragraph**: standard prose blocks. Use `text` and set `list_items=null`.

IMPORTANT:
- If the content is a running header/footer/page number, classify it as **artifact** even if it looks like a heading.
- Do NOT mix `text` and `list_items`: for lists use only `list_items`; for non-lists use only `text`.
- For ANY non-list CurriculumBlock (artifact/caption/heading/key_value/paragraph), set `text` and set `list_items=null`.
- If a list item has no explicit marker, set marker="" (empty string). Do not invent numbering.
- `role_hint` is optional. Leave it null unless it is obvious.
- If `role_hint` is null, `role_confidence` must be null.
- Ignore crop marks/page frame lines.
- Do not output CurriculumBlocks whose text is empty/whitespace.

## LAYOUT-SPECIFIC INSTRUCTIONS
- TABLES: Preserve the exact grid structure. Count the header rows explicitly in `header_row_count`. Allow blank cells (use null text).
- STYLES: Note BOLD / ITALIC / UPPERCASE in `TextUnit.styles` when visually present.
- CODES: Extract explicit curriculum/section numbering (e.g., “3.9.4.1”) into `local_code` when it is visibly present.
    """
    )

    user_message = dedent(
        f"""Extract PageIR for page_index={page_index}.

Specific requirements:
1. DETECT LANGUAGES: Look for {lang_context}. Tag `TextUnit.language` accordingly; use "unk" if unclear.
2. BLOCK TYPES: Every CurriculumBlock MUST have a `block_type`. Tag running headers/footers/page numbers as `artifact`.
3. HEADER ROWS: For every table, explicitly count how many top rows serve as headers (`header_row_count`).
4. BOUNDARIES: Determine if content at the top/bottom is continuous with previous/next pages (`boundary` + `boundary_state`).
5. CONTINUATION LABELS: If you see "(Continued)" / "CONT'D" / "Table X (continued)"-style labels, extract them as separate blocks (caption or artifact). Do NOT put them inside table cells unless they are visibly inside the grid.
    """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
