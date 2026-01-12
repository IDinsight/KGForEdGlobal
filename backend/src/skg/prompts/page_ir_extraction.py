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
        [BlockType.PARAGRAPH.value, BlockType.HEADING.value, BlockType.CAPTION.value],
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
6. **Do a final scan of the bottom 10% of the page before finishing; do not stop early.**
7. If you see an explicit curriculum code/section code/table number associated with a block or table, put it in `local_code` verbatim.
8. If you set local_code, do not include that code in text.
9. **ANTI-FULL-PAGE FAILSAFE (CRITICAL)**:
  - If the page contains substantial readable text (e.g., more than ~2 lines of body text, a TOC/list of entries, paragraphs like "Acknowledgements", or any multi-line section content), you MUST NOT output a single full-page FIGURE item.
  - In those cases, extract the text into HEADING / PARAGRAPH / LIST blocks (and TABLE items if there is a grid).
  - The “single full-page FIGURE page” exception is ONLY for pages that are visually dominated by a non-table graphic or scanned image with at most ~1–2 short text lines total (typical: cover image, certificate scan, photo page).
10. **DO NOT POPULATE PYTHON-FILLED FIELDS**:
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
    - That item is kind="block" and block_type="figure".
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
4. Page `boundary_state` is derived by Python from item boundaries. You may omit it.
5. For block_type="{BlockType.FIGURE.value}": default boundary="{ItemBoundary.COMPLETE.value}" unless the same figure is visibly cut off by the page edge.

## BLOCK CLASSIFICATIONS
1. Valid block_type values: {allowed_block_types}
  - **"{BlockType.ARTIFACT.value}"**: ONLY running headers/footers, page numbers (arabic or roman numerals), or short decorative separators (e.g., "— — —", "***").
    - NEVER use artifact for section titles or content headings.
    - Examples that MUST be heading (NOT artifact): "Table of Contents", "List of Tables", "List of Figures", "Acknowledgements", "Preface", "Bibliography", "References", "Section One/Two/Three..."
  - **"{BlockType.CAPTION.value}"**: Labels for tables/figures (e.g., "Table 1").
  - **"{BlockType.FIGURE.value}"**: Diagrams/figures/illustrations/charts/flowcharts (use `figure`, set `text=null`, `list_items=null`).
  - **"{BlockType.HEADING.value}"**: Section titles.
  - **"{BlockType.LIST.value}"**: Bulleted/numbered items (use `list_items`, set `text=null`).
  - **"{BlockType.PARAGRAPH.value}"**: Prose (use `text`, set `list_items=null`).

## BLOCK TYPES
1. Do not emit any block that contains no content.
  - For block_type in {text_block_types}: block must have non-empty `text`.
  - For block_type="{BlockType.LIST.value}": block must have non-empty `list_items` and `text=null`.
  - For block_type="{BlockType.FIGURE.value}": block must have a non-null `figure` object and `text=null` and `list_items=null`.
  - For block_type="{BlockType.ARTIFACT.value}": must have non-empty text (page number/running header/footer), never full-page.
2. If block_type != "{BlockType.FIGURE.value}", then `figure` MUST be null or omitted.
3. Do NOT output a full-page “{BlockType.ARTIFACT.value}” block. Only output artifacts when you see actual header/footer/page-number text.
4. Ignore page border lines/decorative frames. Do not emit blocks/tables for borders or background. Page numbers must be a small ARTIFACT bbox around the digits/roman numerals only. Signatures/logos are FIGURE only if you include them, with tight bbox around the graphic only (not margins).
5. Artifacts means running header/footer/page number only. Certificates/ISBN/publisher blocks are NOT artifacts. Logos/seals/crests/graphics are NOT artifacts; treat them as figure if you include them at all.
  - If the text looks like a section label/title (not a running header/footer), classify as HEADING, not ARTIFACT.
6. For any non-list block (including figure), list_items MUST be null or omitted (never []).

## LANGUAGES
1. **Expected Languages**: {lang_context}.
2. Expected Languages are only hints (common in Tanzania PDFs to include tables with fr/zh/ar in addition to en/sw).
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
2. **TABLE COLUMN COUNT (optional)**: If you can clearly infer the number of visual columns in the table grid (from ruling/grid and headers), set `CurriculumTable.n_cols` to that integer. If unsure, omit it or set it to null.
3. **TABLE HEADER ROWS**:
  - If the table has header rows at the top, set `header_row_count` to the number of header rows.
  - Keep header rows INSIDE `rows`; do not split headers into a separate field.
  - If unsure, leave it at 0/omit it.
4. **MERGED CELLS**: If a cell clearly spans multiple rows/columns in the visible grid, set row_span/col_span accordingly; otherwise keep them as 1.
5. **GRID-TRUE ROWS ONLY**: Only create new TableRows when there is a visible row boundary in the table grid. Numbered lines inside one cell stay inside that cell’s text.
6. **MIXED LANGUAGE IN ONE TEXTUNIT**: If a single block/cell contains multiple languages (common in bilingual curriculum tables), set TextUnit.language="mul" and keep the full verbatim text as-is. Do not split into multiple cells unless the grid shows separate cells.
7. Do not output empty tables; if you see a table, it must have at least one row.
8. Do NOT misclassify tables as figures. If there is a ruled grid with cells, it must be a table.

## LIST
1. For each list item, populate marker with the visible bullet/numbering (e.g., “•”, “1.”, “a)”) and put the remaining content in text.

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
        f"""Extract PageIR for the provided image (context: this is page_index={page_index} in the PDF).

Reminders:
1. Identify blocks (including figure blocks) and tables from the {image_width}x{image_height} image.
2. Set "kind": "block" or "kind": "table" for every entry.
3. Do NOT translate. `TextUnit.text_en` must be null/omitted.
4. Use the best-match BCP-47 language code for the visible text (not limited to Expected Languages). Use "und" if unknown. Use "mul" if mixed languages are present.
5. Only set repeats_header when the table is "{ItemBoundary.RESUMED.value}"/"{ItemBoundary.BOTH.value}" (i.e., continuing from previous page). Never set it for "{ItemBoundary.COMPLETE.value}" tables.
6. If you can confidently count the table's columns, set "n_cols".
7. If you see a diagram/figure (not a table), output a block_type="{BlockType.FIGURE.value}" block with a `figure` object and tight bbox.

Final check before output:
- If you are outputting exactly ONE item, stop and re-evaluate: is the page truly a full-page graphic/scan with ~no text?
  - If NO: split into multiple blocks/tables with tight bboxes.
- No item bbox may be full-page or reused across items (except the strict single full-page figure case above).
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
