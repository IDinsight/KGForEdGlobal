"""This module contains prompt templates for **extracting** Intermediate Representation
(IR) information from PDF pages.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Optional

# Third Party Library
from dotmap import DotMap

# Package Library
from skg.utils.constants import BlockType, FigureKind, ItemBoundary


def double_check_page_ir_extraction() -> DotMap:
    """Generate the prompts for double-checking page IR extraction results.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = None
    user_message = dedent(
        """**Double-check your last PageIR against the image.** Fix anything that’s off, then return a COMPLETE corrected PageIR JSON (and nothing else).

Checklist:
1. **Missing content**: Did you miss anything near the very top or bottom margins?
2. **Misclassification**:
   - Any ruled grid/cells must be a TABLE (not a FIGURE).
   - Section titles are HEADING (not ARTIFACT).
   - Running headers/footers/page numbers are ARTIFACT (small bboxes).
3. **Tables**:
   - rows/cells captured faithfully; blank cells are `text:null` (no invented content).
   - `header_row_count` reasonable; `n_cols` set only if clearly inferable.
   - row_span/col_span only when merges are clearly visible (don’t hallucinate merges).
   - `repeats_header` true/false only when clear; otherwise null/omit.
4. **BBoxes**:
   - Tight to content; inside page bounds; not reused across multiple items.
   - No accidental full-page bbox unless the strict single full-page FIGURE exception truly applies.
5. **Text fidelity**:
   - Verbatim text; no invented bullets/numbers; marker=null when no explicit marker exists.
6. **Figures** (if any):
   - figure.alt_text present + non-empty.
   - If figure.contains_text=true then embedded_text present and verbatim.
7. **Reading order**: Items are in correct visual reading order.

Now return the corrected PageIR JSON only.
        """
    )

    return DotMap(
        {"system_message": system_message, "user_message": user_message.strip()}
    )


def extract_page_ir_from_pdf_page(
    *,
    country: str,
    image_height: int,
    image_width: int,
    languages: list[str],
    page_index: int,
    text_layer_hints: Optional[str] = None,
    year: Optional[int] = None,
) -> DotMap:
    """Generate the prompts for extracting page IRs from a page image.

    Parameters
    ----------
    country
        The country of origin for the document.
    image_height
        The height of the image in pixels.
    image_width
        The width of the image in pixels.
    languages
        List of expected languages in BCP-47 format (e.g., ['en', 'sw']).
    page_index
        The 0-based page index of the image being processed.
    text_layer_hints
        Optional text layer hint extracted from the PDF.
    year
        The document year, if known.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    allowed_block_types = json.dumps([bt.value for bt in BlockType], ensure_ascii=False)
    allowed_figure_kinds = json.dumps(
        [fk.value for fk in FigureKind], ensure_ascii=False
    )
    doc_context = f"from {country}" + (f" ({year})" if year else "")
    lang_context = ", ".join(languages)
    text_block_types = json.dumps(
        [
            BlockType.CAPTION.value,
            BlockType.FOOTNOTE.value,
            BlockType.HEADING.value,
            BlockType.PARAGRAPH.value,
        ],
        ensure_ascii=False,
    )
    text_layer_context = (
        ""
        if not text_layer_hints
        else f"## TEXT LAYER HINTS\n{text_layer_hints}\nNB: Text layer hints are only hints and they may be incomplete. If TEXT LAYER HINTS contain multiple lines of non-header text (paragraph-like), treat the page as a text page unless the image clearly shows the text layer is wrong (e.g., OCR garbage not matching the visible page). If LIKELY_MULTI_COLUMN_OR_TABLE=true OR the page visually shows a grid/columns, you MUST extract a TableItem and use the image to determine rows/columns. Use text-layer hints only to fill cell text accurately."
    )

    system_message = dedent(
        f"""You are a high-fidelity curriculum digitization agent. Convert the single page image {doc_context} PDF into a strict PageIR JSON object.

{text_layer_context}

## HARD RULES
1. **IMAGE IS SOURCE OF TRUTH**: If text-layer hints contradict the image, trust the image.
2. **READING ORDER**: Populate `items` in visual reading order: left-to-right across columns, then top-to-bottom. For multi-column pages: read top-to-bottom within the left column first, then the next column to the right.
3. **VERBATIM / NO HALLUCINATION**:
   - Extract text exactly as seen (including typos, punctuation, dot leaders, spacing). Do not complete cut-off sentences.
   - Do not add rows/cells/text not visible. If a table cell is blank, set `text: null`.
   - **Never invent list markers**. If no explicit marker is visible, set `marker: null`.
5. **ITEM KIND**: Every item MUST include `"kind"` and it must be either `"block"` or `"table"`.
6. **BOTTOM SCAN**: Do a final scan of the bottom ~10% of the page before finishing; do not stop early.
7. **LOCAL CODES (optional)**:
   - If you see an explicit curriculum/section/table code tightly associated with a block/table, put it in `local_code` verbatim (punctuation matters).
   - If the code appears as a clean leading prefix (e.g., "3.9.4.1 ..."), you MAY omit that prefix from the block/table text to avoid duplication. If separation is not clean, keep the text verbatim and still set `local_code` if you can.
8. **IGNORE DRAWN LINES**: Ignore thin drawn separator rules/lines used to visually separate sections (including the horizontal line above footnotes). Do not emit items for drawn lines.
   - If a separator is made of actual text characters (e.g., "***", "— — —") and is present as text, it may be extracted as a small "{BlockType.ARTIFACT.value}" block.

## DO NOT POPULATE PYTHON-FILLED FIELDS
1. PageIR fields filled/overwritten by Python must be omitted or left null:
  - boundary_state MUST be omitted (do not include it at all).
  - doc_key, pdf_name, page_index, dpi, image_width, image_height MUST be null or omitted.
2. You may omit `coord_space`; if you include it, it MUST be exactly "px".

## BOUNDING BOXES
1. **COORDINATES**: Use pixel coordinates (px) relative to {image_width}x{image_height}.
2. **BBOX REQUIRED**: Every block/table MUST include a bbox [x0,y0,x1,y1]. BBoxes must be tight to the visible content.
3. **BBOX VALIDITY**: 0 <= x0 < x1 <= image_width and 0 <= y0 < y1 <= image_height.
4. **NO PLACEHOLDER/REUSED BBOXES**: Do not reuse the same bbox for multiple items. Do not copy the page bbox into items.
5. **FULL-PAGE FIGURE EXCEPTION (EXTREMELY STRICT)**:
   - A full-page bbox is allowed ONLY if ALL are true:
     - The output contains exactly ONE item.
     - That item is kind="block" and block_type="{BlockType.FIGURE.value}".
     - The page is visually dominated by a non-table graphic or scanned image (NOT primarily text).
     - The page has no readable body text beyond ~1–2 short lines total.
     - figure.figure_kind MUST NOT be "unknown". If you cannot classify the figure kind, do NOT use the full-page exception.
   - If ANY condition is not met, split the page into multiple localized bboxes (headings/paragraphs/lists/tables as appropriate).

## BOUNDARIES (item.boundary only)
1. Item `boundary` is semantic continuity across pages (not missing borders):
  - "{ItemBoundary.RESUMED.value}": continuation from previous page
  - "{ItemBoundary.TRUNCATED.value}": continues onto next page
  - "{ItemBoundary.BOTH.value}": continues from previous AND to next page
  - "{ItemBoundary.COMPLETE.value}": fully contained on this page
2. Set each item’s boundary based on visible continuation cues. Do NOT rely on whether table borders are drawn.

## BLOCK CLASSIFICATIONS
1. Valid block_type values: {allowed_block_types}
  - "{BlockType.HEADING.value}": Section titles.
  - "{BlockType.PARAGRAPH.value}": Prose text.
  - "{BlockType.LIST.value}": Bulleted/numbered/outlined items (use `list_items`, set `text=null`).
  - "{BlockType.CAPTION.value}": Labels for figures/tables (e.g., "Table 1: ...", "Figure 2: ...").
  - "{BlockType.FOOTNOTE.value}": Bottom-of-page footnotes/numbered notes (not page numbers).
  - "{BlockType.ARTIFACT.value}": ONLY running headers/footers, page numbers (arabic/roman), or textual decorative separators ("***", "— — —"). Never use artifact for true section headings.
  - "{BlockType.FIGURE.value}": Diagrams/illustrations/charts/flowcharts that are NOT a ruled table grid (use `figure`, set `text=null`, `list_items=null`).

## BLOCK CONTENT REQUIREMENTS
1. Do not emit any block that contains no content.
  - For block_type in {text_block_types}: block must have non-empty `text` as a TextUnit object ({{"text": "...", "language": "...", "text_en": null}}).
  - For block_type="{BlockType.LIST.value}": block must have non-empty `list_items` and `text=null`.
  - For block_type="{BlockType.FIGURE.value}": block must have non-null `figure` and `text=null` and `list_items=null`.
2. If block_type != "{BlockType.FIGURE.value}", then `figure` MUST be null or omitted.
3. For any non-list block, `list_items` MUST be null or omitted (never []).

## LANGUAGES
1. Expected Languages (hints): {lang_context}.
2. Use the best-match BCP-47 language for the visible text; use "und" if unknown. Numeric-only page numbers should be "und".
3. Use "mul" only if multiple languages appear in the SAME TextUnit (e.g., bilingual sentence in one cell).
4. **NO TRANSLATION**: Do NOT translate or paraphrase. For every TextUnit, `text_en` MUST be null or omitted.

## TABLES
1. **TABLE VS FIGURE**: If there is a ruled grid with cells, it MUST be a table (not a figure).
2. **CELL TEXT TYPE**: `TableCell.text` is either null OR a TextUnit object.
3. **HEADER ROWS**: If the table has header rows at the top, set `header_row_count` accordingly (keep headers inside `rows`). If unsure, leave 0 or omit.
4. **n_cols (optional)**: If the number of visual columns is clearly inferable, set `n_cols`. If unsure, omit or null.
5. **MERGED CELLS (spans)**:
   - Use `row_span`/`col_span` only when a merge is clearly visible.
   - If unsure, do not merge (use row_span=1, col_span=1).
   - Do not duplicate merged-cell text into covered cells unless it is visibly repeated.
6. **ROW BOUNDARIES**:
   - Table rows may be separated by ruling lines OR by consistent horizontal spacing/alignment that clearly forms rows.
   - Numbered/bulleted lines inside a single cell stay inside that cell’s text (do not create extra rows/items).
7. **INSIDE vs OUTSIDE GRID**: Do not absorb outside-of-grid headings/paragraphs into the table. End the table when the grid ends.
8. **REPEATS HEADER**: If the table continues from a previous page and header rows are visibly repeated, set `repeats_header=true`. If clearly not repeated, set false. If unsure, set null/omit.
9. Do not output empty tables; if you emit a table, it must have at least one row.

## LISTS
1. For each list item:
  - If an explicit marker is visible (e.g., "•", "1.", "a)", "(i)", "3.9.4.1"), set `marker` to that EXACT marker verbatim.
  - If list-like but has NO explicit marker (common in TOC/dot-leader entries/indented outlines), set `marker=null`. Do NOT invent markers.
  - Keep dot leaders and page numbers inside `text` verbatim.

## FIGURES
1. If you emit a FIGURE block:
  - figure.alt_text MUST be present and non-empty (<= ~200 chars). Describe only what is visible (type + key elements). Do not interpret meaning.
  - Set figure.figure_kind to one of {allowed_figure_kinds}; keep conservative (avoid over-specific).
  - If figure.contains_text=true, you MUST populate figure.embedded_text (best-effort verbatim). Otherwise set contains_text=false (or null if unknown).
        """
    )

    user_message = dedent(
        f"""Extract PageIR for the provided image (context: page_index={page_index}).

## QUICK CHECKS (before you finalize)
1. **Reading order**: Are items ordered left-to-right then top-to-bottom (column-by-column if multi-column)?
2. **Tables**: If the page shows a grid/columns, emit a TABLE item and capture rows/cells faithfully (blank cells => text:null). If columns are clear, set `n_cols`.
3. **Continuations**: If the page begins mid-table or mid-paragraph, set boundary="{ItemBoundary.RESUMED.value}" (or "{ItemBoundary.BOTH.value}"). If it continues off the page, use "{ItemBoundary.TRUNCATED.value}" (or "{ItemBoundary.BOTH.value}").
4. **Full-page figure exception**: Only use a single full-page FIGURE when the page is truly dominated by a non-table image/scan with at most ~1–2 short text lines.

Return the PageIR JSON only.
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
