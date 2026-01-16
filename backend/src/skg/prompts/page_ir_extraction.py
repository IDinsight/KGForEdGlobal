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
        """**Hmmmm, are you absolutely sure of your extraction results?**

It's a good idea to carefully review your last output against the stated instructions and double-check your response.

In particular, ensure that:

1. Extracted items at the top and bottom of the page are not cut off or missing.
2. Extracted items have the correct `Block`/`Figure` kinds (don't mistaken a paragraph for an artifact or vice versa!).
3. Extracted tables have **ALL** of their structure (`rows`, `columns`, `header_row_count`, `local_code`, etc.) correctly identified and no data is missing.
  - Spend some time thinking about this one, as tables are often tricky!
4. Extracted bounding boxes are tight to the content and do not overlap significantly with other items.
5. Extracted text is verbatim and does not contain hallucinated or invented content.
6. All items are in correct visual reading order (left-to-right, top-to-bottom).

When you are confident in your answer, return a complete `PageIR` that matches the schema and fixes any issues you might've overlooked or incorrect assumptions you might've made.
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
        else f"## TEXT LAYER HINTS\n{text_layer_hints}\nNB: Text layer hints are only hints and they may be incomplete. If TEXT LAYER HINTS contain multiple lines of non-header text (paragraph-like), treat the page as a text page unless the image clearly shows the text layer is wrong (e.g., OCR garbage not matching the visible page)."
    )

    system_message = dedent(
        f"""You are a high-fidelity curriculum digitization agent. Convert the single page image {doc_context} PDF into a strict PageIR JSON object.

{text_layer_context}

## HARD RULES
1. **If text-layer hints contradict the image, trust the image.**
2. **READING ORDER**: Populate `items` in visual reading order: left-to-right columns, then down. For multi-column pages: read top-to-bottom within the left column first, then move to the next column on the right.
3. **VERBATIM**: Extract text exactly as seen. Do not fix typos or complete truncated sentences.
4. **KIND DISCRIMINATOR**: Every item in the `items` list MUST have a `"kind"` field set to either `"block"` or `"table"`.
5. **NO HALLUCINATION**: Do not add rows/cells/text that are not visible. If a cell is blank, set text: null.
  - **NO HALLUCINATED LIST MARKERS**: Never add bullets/numbers/letters that are not visible. For markerless TOC/outline lines, set marker=null.
6. **Do a final scan of the bottom 10% of the page before finishing; do not stop early.**
7. If you see an explicit curriculum code/section code/table number associated with a block or table, put it in `local_code` verbatim.
  - Preserve punctuation exactly (dots/hyphens/slashes). Dot sequences matter.
  - Examples: "3.9.4.1", "B5.2.1.1", "Table 1.2", "P1-THEME 3.2".
8. If you set local_code, do not include that code in `text`, **UNLESS** the block consists *only* of that code. In that case, keep the code in `text` (so the block is not empty) and also put it in `local_code`.
9. **ANTI-FULL-PAGE FAILSAFE (CRITICAL)**:
  - If the page contains substantial readable text (e.g., more than ~2 lines of body text, a TOC/list of entries, paragraphs like "Acknowledgements", or any multi-line section content), you MUST NOT output a single full-page FIGURE item.
  - In those cases, extract the text into "{BlockType.HEADING.value}"/"{BlockType.PARAGRAPH.value}"/"{BlockType.LIST.value}" blocks (and TABLE items if there is a grid).
  - The “single full-page FIGURE page” exception is ONLY for pages that are visually dominated by a non-table graphic or scanned image with at most ~1–2 short text lines total (typical: cover image, certificate scan, photo page).
10. Ignore thin separator rules/lines used to visually separate sections (including the horizontal line above footnotes). Do not emit blocks for those lines.
11. **DO NOT POPULATE PYTHON-FILLED FIELDS**:
  - The following PageIR fields are filled/overwritten by the Python pipeline:
    - boundary_state MUST be omitted (do not include it at all).
    - doc_key, pdf_name, page_index, dpi, image_width, image_height MUST be null or omitted.
  - You may omit `coord_space`; if you include it, it MUST be exactly "px".

## BOUNDING BOXES
1. **COORDINATES**: Use pixel coordinates (px) relative to {image_width}x{image_height}.
2. **BBOX REQUIRED**: Every block/table MUST include a localized bbox [x0,y0,x1,y1]. Never omit it. BBoxes must be tight to the content. Never use a full-page bbox except for genuinely full-page images (rare).
3. **BBOX VALIDITY**: Every bbox must satisfy 0<=x0<x1<=image_width and 0<=y0<y1<=image_height.
4. **NO PLACEHOLDER/REUSED BBOXES**: Never use the same bbox for multiple items. Never copy the page bbox into items. Each item bbox must tightly wrap only that item’s visible pixels. If unsure, err slightly smaller than too large—never full-page.
5. **FULL-PAGE BBOX EXCEPTION (EXTREMELY STRICT)**:
  - A full-page bbox is allowed ONLY if ALL are true:
    - The output contains exactly ONE item.
    - That item is kind="block" and block_type="{BlockType.FIGURE.value}".
    - The page is visually dominated by a non-table graphic or scanned image (NOT primarily text).
    - The page has no paragraph/list-like body text beyond ~1–2 short lines.
    - figure.figure_kind MUST NOT be "unknown". If you cannot classify the figure kind, you MUST NOT use the full-page exception.
  - If ANY condition is not met, you must split the page into multiple localized bboxes.

## BOUNDARIES
1. Item `boundary` is SEMANTIC continuity (not “missing borders”):
  - "{ItemBoundary.RESUMED.value}": This item is a continuation from the previous page
  - "{ItemBoundary.TRUNCATED.value}": This item continues onto the next page
  - "{ItemBoundary.BOTH.value}": Continuation from previous page AND to next page (middle slice of a long item)
  - "{ItemBoundary.COMPLETE.value}": Fully contained on this page
2. Focus on setting each item's `boundary` correctly ("{ItemBoundary.RESUMED.value}"/"{ItemBoundary.TRUNCATED.value}"/"{ItemBoundary.BOTH.value}"/"{ItemBoundary.COMPLETE.value}") based on visible continuation cues.
3. DO NOT rely on whether table borders are drawn. Many PDFs repeat gridlines and headers on continuation pages.
4. Page `boundary_state` MUST be omitted (it is derived by Python).
5. For block_type="{BlockType.FIGURE.value}": default boundary="{ItemBoundary.COMPLETE.value}" unless the same figure is visibly cut off by the page edge.

## BLOCK CLASSIFICATIONS
1. Valid block_type values: {allowed_block_types}
  - **"{BlockType.ARTIFACT.value}"**: ONLY running headers/footers, page numbers (arabic or roman numerals), or short decorative separators (e.g., "— — —", "***").
    - NEVER use artifact for section titles or content headings.
    - Examples that MUST be heading (NOT artifact): "Table of Contents", "List of Tables", "List of Figures", "Acknowledgements", "Preface", "Bibliography", "References", "Section One/Two/Three..."
  - **"{BlockType.FOOTNOTE.value}"**: Bottom-of-page footnotes/numbered notes (often separated by a thin horizontal rule).
    - Use when the page includes a real footnote body like "2 The curriculum does not..." at the bottom margin.
    - Footnotes are NOT artifacts. Page numbers/running headers remain "{BlockType.ARTIFACT.value}".
    - Extract the full footnote line(s) verbatim in `text` (including leading footnote number/superscript if visible).
  - **"{BlockType.CAPTION.value}"**: Labels for tables/figures (e.g., "Table 1").
  - **"{BlockType.FIGURE.value}"**: Diagrams/figures/illustrations/charts/flowcharts (use `figure`, set `text=null`, `list_items=null`).
  - **"{BlockType.HEADING.value}"**: Section titles.
  - **"{BlockType.LIST.value}"**: Bulleted/numbered items (use `list_items`, set `text=null`).
  - **"{BlockType.PARAGRAPH.value}"**: Prose (use `text`, set `list_items=null`).

## BLOCK TYPES
1. Do not emit any block that contains no content.
  - For block_type in {text_block_types}: block must have non-empty `text`. The `text` field MUST be a TextUnit object ({{"text": "...", "language": "...", "text_en": null}}), NOT a string.
  - For block_type="{BlockType.LIST.value}": block must have non-empty `list_items` and `text=null`.
  - For block_type="{BlockType.FIGURE.value}": block must have a non-null `figure` object and `text=null` and `list_items=null`.
  - For block_type="{BlockType.ARTIFACT.value}": must have non-empty text (page number/running header/footer), never full-page.
2. If block_type != "{BlockType.FIGURE.value}", then `figure` MUST be null or omitted.
3. Do NOT output a full-page “{BlockType.ARTIFACT.value}” block. Only output artifacts when you see actual header/footer/page-number text.
4. Ignore page border lines/decorative frames. Do not emit blocks/tables for borders or background. Page numbers must be a small ARTIFACT bbox around the digits/roman numerals only. Signatures/logos are FIGURE only if you include them, with tight bbox around the graphic only (not margins).
5. Artifacts means running header/footer/page number only. Certificates/ISBN/publisher blocks are NOT artifacts. Logos/seals/crests/graphics are NOT artifacts; treat them as figure if you include them at all.
  - If the text looks like a section label/title (not a running header/footer), classify as "{BlockType.HEADING.value}", not "{BlockType.ARTIFACT.value}".
6. For any non-list block (including figure), list_items MUST be null or omitted (never []).

## LANGUAGES
1. **Expected Languages**: {lang_context}.
2. Expected Languages are only hints (it's common for PDFs to include tables with multiple languages).
3. Use the BEST-MATCH BCP-47 language for the visible text, even if it is NOT in Expected Languages.
4. Prefer en, sw, etc. rather than regional subtags like en-TZ, etc.
5. Numeric-only page numbers should be und.
6. Use "und" if the language is unknown or if you genuinely cannot tell.
7. Use "mul" only if multiple languages appear in the SAME TextUnit (e.g., bilingual French+English in one cell).
8. **NO TRANSLATION**:
  - Do NOT translate, paraphrase, or “helpfully” rewrite any text.
  - For every `TextUnit`, `text_en` MUST be null or omitted. (A later translation pass fills it.)

## TABLES
1. **TABLE CELL STRUCTURE**: The `text` field inside a `TableCell` is an OBJECT (TextUnit).
  - Correct: `"text": {{"text": "content", "language": "en", "text_en": null}}`
  - Incorrect: `"text": "content"`
  - Empty: If a cell is blank, set `"text": null`.
2. **TABLE COLUMN COUNT (optional)**: If you can clearly infer the number of visual columns in the table grid (from ruling/grid and headers), set `Table.n_cols` to that integer. If unsure, omit it or set it to null.
3. **TABLE HEADER ROWS**:
  - If the table has header rows at the top, set `header_row_count` to the number of header rows.
  - Keep header rows INSIDE `rows`; do not split headers into a separate field.
  - If unsure, leave it at 0/omit it.
4. **MERGED CELLS (ROW/COL SPANS)**:
  - If a cell visually spans multiple rows, set `row_span` to the number of rows it spans.
  - If a cell visually spans multiple columns, set `col_span` to the number of columns it spans.
  - For a row-spanned cell, DO NOT duplicate the same text into the covered rows unless the table actually repeats it visibly.
  - Do NOT hallucinate merges. If you are unsure whether a merge exists, set `row_span=1` and `col_span=1` (and represent the table as best you can from the visible grid).
  - Default: `row_span=1`, `col_span=1`.
5. Keep all bullet/subheading lines inside the cell text
  - If text is inside a table cell, do not emit separate list/paragraph items for it. Preserve line breaks inside the cell.
6. Do not absorb outside-of-grid sections into the table
  - If the grid ends and a new heading/section begins (e.g., “Assessment Guidelines...”), end the Table and emit new items after it.
7. **GRID-TRUE ROWS ONLY**: Only create new TableRows when there is a visible row boundary in the table grid. Numbered lines inside one cell stay inside that cell’s text.
8. **MIXED LANGUAGE IN ONE TEXTUNIT**: If a single block/cell contains multiple languages (common in bilingual curriculum tables), set TextUnit.language="mul" and keep the full verbatim text as-is. Do not split into multiple cells unless the grid shows separate cells.
9. Do not output empty tables; if you see a table, it must have at least one row.
10. Do NOT misclassify tables as figures. If there is a ruled grid with cells, it must be a table.
11. **FIGURES INSIDE TABLE CELLS**:
  - If you see an image/diagram/illustration INSIDE a table cell (e.g., a clock picture, blocks, icons):
    - Always extract the table grid normally (cell text may be null if no text).
    - Only ALSO emit a separate FIGURE block for the embedded image if it is MEANINGFUL CONTENT, e.g.:
      - It contains visible text/labels/numbers/symbols that matter (set figure.contains_text=true and populate figure.embedded_text best-effort), OR
      - It is a diagram/chart/graph/table-like figure that changes meaning (e.g., number line, geometric pattern, labeled picture), OR
      - It is explicitly referenced in nearby text (e.g., “see figure…”, “as shown below”), OR
      - It is clearly required to answer/understand the exercise (e.g., a clock face for time, a shape pattern for counting).
    - Ignore tiny decorative/illustrative icons (pure decoration, repeated ornaments) and do NOT emit a FIGURE block for them (treat as no-op).
12. **REPEATED HEADERS**: If the table is a continuation from a previous page ("{ItemBoundary.RESUMED.value}" or "{ItemBoundary.BOTH.value}") AND the header rows are repeated visually, set `repeats_header=true`. Otherwise false.

## LIST
1. For each list item:
  - **Disambiguation**: If a numbered line is a standalone section title, treat it as a "{BlockType.HEADING.value}" (use `local_code`). Only use "{BlockType.LIST.value}" if it is part of a vertical sequence of items.
  - If there is an explicit bullet/number/letter/code marker visible (e.g., "•", "1.", "a)", "(i)", "3.9.4.1"), set `marker` to that EXACT marker (verbatim) and put the remaining content in `text`.
  - If the line is list-like but has NO explicit marker (common in Table of Contents/dot-leader entries/indented outlines), set `marker` to null.
    - DO NOT invent markers.
    - DO NOT use an empty string for marker; use null.
  - Keep dot leaders and page numbers inside `text` verbatim (e.g., "INTRODUCTION.................. 91").

## FIGURES/DIAGRAMS
1. If the page contains a diagram/figure/illustration/chart/flowchart that is NOT a table grid, emit a block with:
  - kind="block"
  - block_type="{BlockType.FIGURE.value}"
  - bbox tightly around the figure region (include axes/arrows/labels inside the region)
  - text=null
  - list_items=null
  - figure = {{
      "alt_text": VERY short (<=200 chars) non-semantic description (e.g. "flowchart with arrows", "pyramid diagram"),
      "caption": null OR a TextUnit object ({{"language": "...", "text": "...", "text_en": null}}) ONLY if the caption is clearly inside the figure bbox,
      "contains_text": true/false/null,
      "figure_kind": one of {allowed_figure_kinds},
    }}
  - If you set figure.contains_text=true, you MUST also populate figure.embedded_text with best-effort verbatim text.
  - If you are not extracting embedded text, set figure.contains_text=false.
2. Do NOT emit a figure for tiny decorative elements (small logos/ornaments) unless they are central content.
3. Do NOT interpret diagram meanings. Do NOT convert diagram content into prose. Just preserve the region and light hints.

## CAPTIONS
1. If you see a caption like "Figure 2: ..." near a figure, prefer to extract it as its own block_type="caption" item (with text=TextUnit) in reading order.
2. Only set figure.caption when the caption is unambiguously part of the figure region itself.
        """
    )

    user_message = dedent(
        f"""Extract PageIR for the provided image (context: this is page_index={page_index}).

## CONTEXTUAL OVERRIDES AND CHECKS
1. **Identify Content**: Identify all blocks and tables in the {image_width}x{image_height} image.
2. **Missing List Markers**: As per rules, if a list entry (like a TOC line) has no explicit bullet/number, set `marker=null` (do not invent one).
3. **Table Columns**: If visual columns are clear, explicitly set `n_cols`.
4. **Continuations**: Check the top of the page. If a table continues from the previous page, mark it as "{ItemBoundary.RESUMED.value}" (or "{ItemBoundary.BOTH.value}") and set `repeats_header` if applicable.

## FINAL SAFETY CHECK
Before outputting, verify:
1. **Anti-Hallucination**: Did you invent text for a blank cell? (Set text: null instead).
2. **Anti-Full-Page**: If you have exactly ONE item, is it strictly a full-page image/scan? If there is readable body text, you MUST split it into paragraph/heading blocks.
3. **BBox Validity**: Ensure no bboxes are overlapping significantly or identical (unless nested), and all are within [0, 0, {image_width}, {image_height}].
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
