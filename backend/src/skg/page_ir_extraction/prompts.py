"""This module contains prompt templates for **extracting** Intermediate Representation
(IR) information from PDF pages.
"""

# Standard Library
import json

from textwrap import dedent

# Package Library
from skg.utils.constants import BlockType, FigureKind, ItemBoundary
from skg.utils.general import PromptPair


def double_check_page_ir_extraction() -> PromptPair:
    """Generate the prompts for double-checking page IR extraction results.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    system_message = None
    user_message = dedent(
        """**Double-check your last PageIR against the image.** Fix anything that's off, then return a COMPLETE corrected PageIR JSON (and nothing else).

Checklist:
1. **Missing content**: Did you miss anything near the very top or bottom margins?
2. **Reading order**: Items are in correct visual reading order.
3. **Misclassification**:
   - Ruled grid/cells → TABLE (not FIGURE).
   - Section titles → HEADING (not ARTIFACT).
   - Running headers/footers/page numbers → ARTIFACT.
4. **Tables**:
   - Rows/cells captured faithfully; blank cells are `text:null`.
   - `header_row_count` reasonable; `n_cols` set only if clearly inferable.
   - row_span/col_span only for clearly visible merges.
   - `repeats_header` true/false only when clear; otherwise null.
5. **BBoxes**: Tight to content; inside page bounds; unique per item.
6. **Text fidelity**: Verbatim text; `text` field contains the complete visible text even
   when `local_code` is set.
7. **Figures** (if any): alt_text present; embedded_text present when contains_text=true.

Return the corrected PageIR JSON only.
        """
    )

    return PromptPair(system_message=system_message, user_message=user_message.strip())


def extract_page_ir_from_pdf_page(
    *,
    image_height: int,
    image_width: int,
    languages: list[str],
    page_index: int,
) -> PromptPair:
    """Generate the prompts for extracting page IRs from a page image.

    Parameters
    ----------
    image_height
        The height of the image in pixels.
    image_width
        The width of the image in pixels.
    languages
        List of expected languages in BCP-47 format (e.g., ['en', 'sw']).
    page_index
        The 0-based page index of the image being processed.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    allowed_block_types = json.dumps([bt.value for bt in BlockType], ensure_ascii=False)
    allowed_figure_kinds = json.dumps(
        [fk.value for fk in FigureKind], ensure_ascii=False
    )
    lang_context = ", ".join(languages)

    system_message = dedent(
        f"""You are a high-fidelity document digitization agent. Convert the single page image into a strict PageIR JSON object.

## HARD RULES
1. **IMAGE IS SOURCE OF TRUTH**: Extract exactly what is visible on the page.
2. **READING ORDER**: Populate `items` top-to-bottom within each column, left column before right. For single-column pages this is simply top-to-bottom.
3. **VERBATIM / NO HALLUCINATION**:
   - Extract text exactly as seen (including typos, punctuation, dot leaders, spacing). Do not complete cut-off sentences.
   - Do not add rows/cells/text not visible. If a table cell is blank, set `text: null`.
4. **ITEM KIND**: Every item MUST include `"kind"` — either `"block"` or `"table"`.
5. **BOTTOM SCAN**: Scan the full page including the bottom margin; do not stop early.
6. **LOCAL CODES (optional)**:
   - If you see an explicit section/table code tightly associated with a block/table, put it in `local_code` verbatim (punctuation matters).
   - **Always keep the full visible text verbatim in `text`**. Do NOT strip or remove any portion of the text to avoid duplication with `local_code`. The `local_code` field is an annotation, not a replacement.
7. **IGNORE DRAWN LINES**: Ignore thin separator rules/lines (including the line above footnotes). Do not emit items for drawn lines. If a separator is made of text characters (e.g., "***"), it may be extracted as a small "{BlockType.ARTIFACT.value}" block.

## PYTHON-FILLED FIELDS
Omit these fields (Python fills them after extraction): boundary_state, doc_key, pdf_name, page_index, dpi, image_width, image_height, coord_space.

## BOUNDING BOXES
1. **COORDINATES**: Pixel coordinates (px) relative to {image_width}x{image_height}.
2. **BBOX REQUIRED**: Every block/table MUST have a bbox [x0,y0,x1,y1] tight to visible content.
3. **BBOX VALIDITY**: 0 <= x0 < x1 <= {image_width} and 0 <= y0 < y1 <= {image_height}.
4. **NO PLACEHOLDER/REUSED BBOXES**: Each item gets a unique, localized bbox.
5. **NO FULL-PAGE BBOXES** unless the page contains exactly one non-table visual (diagram/illustration) with no readable body text. Such pages are rare.

## BOUNDARIES (item.boundary only)
1. Semantic continuity across pages (not missing borders):
   - "{ItemBoundary.RESUMED.value}": continues from previous page
   - "{ItemBoundary.TRUNCATED.value}": continues onto next page
   - "{ItemBoundary.BOTH.value}": both directions
   - "{ItemBoundary.COMPLETE.value}": fully contained on this page
2. Set boundary from visible continuation cues. Do NOT rely on whether borders are drawn.

## BLOCK CLASSIFICATIONS
Valid block_type values: {allowed_block_types}
  - "{BlockType.HEADING.value}": Section titles.
  - "{BlockType.PARAGRAPH.value}": Prose text.
  - "{BlockType.LIST.value}": Bulleted/numbered/outlined items (use `list_items`, `text=null`).
  - "{BlockType.CAPTION.value}": Labels for figures/tables (e.g., "Table 1: …").
  - "{BlockType.FOOTNOTE.value}": Bottom-of-page footnotes (not page numbers). Must be near the bottom of the page.
  - "{BlockType.ARTIFACT.value}": ONLY running headers/footers and page numbers. Never use for true section headings.
  - "{BlockType.FIGURE.value}": Diagrams/illustrations/charts that are NOT a ruled table grid.

## BLOCK CONTENT RULES
1. Do not emit blocks with no content.
2. Text-bearing blocks (heading/paragraph/caption/footnote/artifact): non-empty `text` required; `list_items` and `figure` must be null.
3. List blocks: non-empty `list_items` required; `text` and `figure` must be null.
4. Figure blocks: non-null `figure` required; `text` and `list_items` must be null.

## LANGUAGES
1. Expected languages (hints): {lang_context}.
2. Use ISO 639-1 codes where available (e.g., 'fr', 'wo', 'sw'); use ISO 639-3 for languages without two-letter codes; "und" if unknown; "mul" if multiple languages appear in the SAME TextUnit.
3. **NO TRANSLATION**: `text_en` MUST be null or omitted for every TextUnit.

## TABLES
1. **HEADER ROWS**: Set `header_row_count` if the top rows are headers (keep them inside `rows`). If unsure, leave 0.
2. **n_cols**: Set if the column count is clearly inferable. If set, at least one row's cells (counting col_spans) must add up to n_cols, and no row may exceed it. Omit if unsure.
3. **MERGED CELLS**: Use row_span/col_span only for clearly visible merges. If unsure, use 1/1. Spanned cells (row_span>1 or col_span>1) must have non-null text. Do not duplicate merged-cell text into covered cells.
4. **ROW BOUNDARIES**: Every row must have at least one cell. Rows may be separated by rules or by consistent spacing. Numbered/bulleted lines inside a cell stay in that cell's text.
5. **INSIDE vs OUTSIDE GRID**: Do not absorb outside-grid headings/paragraphs into the table. End the table when the grid ends.
6. **REPEATS HEADER**: Only set `repeats_header` on continuation tables (boundary is "{ItemBoundary.RESUMED.value}" or "{ItemBoundary.BOTH.value}"). Set true/false if clear; null if unsure.

## LISTS
For each list item:
  - If an explicit marker is visible ("•", "1.", "a)", "(i)"), set `marker` to the EXACT marker verbatim.
  - If no explicit marker exists, set `marker=null`. Do NOT invent markers.
  - List item text must be non-empty.
  - Keep dot leaders and page numbers inside `text` verbatim.

## FIGURES
If you emit a FIGURE block:
  - figure.alt_text MUST be present (not null) and non-empty (≤ ~500 chars). Describe what is visible.
  - Set figure.figure_kind to one of {allowed_figure_kinds}; keep conservative. If figure_kind is "equation", figure.contains_text must be true.
  - If figure.contains_text=true, populate figure.embedded_text (best-effort verbatim). Otherwise set contains_text=false (or null if unknown).
        """
    )

    user_message = dedent(
        f"""Extract PageIR for the provided image (context: page_index={page_index}).

Before returning, scan the bottom ~10% of the page for any missed content.

Return the PageIR JSON only.
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
