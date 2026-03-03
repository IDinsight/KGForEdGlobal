"""This module contains prompt templates for **extracting** and **validating**
Intermediate Representation (IR) information from PDF pages.
"""

# Standard Library
import json

from textwrap import dedent

# Package Library
from skg.utils.constants import BlockType, FigureKind, ItemBoundary
from skg.utils.general import PromptPair


def extract_page_ir_from_pdf_page(
    *,
    image_height: int,
    image_width: int,
    languages: list[str],
    page_index: int,
    table_layer_hint: str | None = None,
    text_layer_hint: str | None = None,
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
    table_layer_hint
        Optional serialized table structure extracted from the PDF text layer by
        PyMuPDF. When provided, appended to the user message as a structural reference.
        May be None if no usable tables were found.
    text_layer_hint
        Optional plain-text content extracted from the PDF text layer by PyMuPDF. When
        provided, appended to the user message as a character-level spelling reference.
        May be None if the text layer failed quality checks.

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

## PDF TEXT LAYER HINTS (when provided)
A machine-extracted text layer and/or table structure from the PDF may be included in the user message. These are derived from the PDF's internal text encoding (NOT from OCR) and are therefore **character-accurate** for spelling, diacritics, and special characters.
  - **TEXT LAYER**: Use as the authoritative source for character-level spelling — especially for non-Latin scripts, diacritics, accented characters, and special letters (e.g., ɓ, ɗ, Ƴ, ŋ, ñ, é, ü). If you see a character in the image that could be either a plain Latin letter or a diacritical variant, **prefer the text layer's spelling**.
  - **TABLE LAYER**: Use as a structural reference for table cell contents and column layout. It may help confirm cell boundaries, merged cells, and column counts. However, the text layer's table detection may miss some tables or include spurious ones.
  - **IMAGE REMAINS AUTHORITATIVE** for: visual layout, reading order, block classification (heading vs paragraph vs table vs figure), bounding boxes, and structural decisions. The text/table layers are **hints only** — do not blindly copy them. If the text layer contains content not visible in the image, ignore it. If the image shows content not in the text layer (e.g., purely visual elements), extract it from the image.
  - **WHEN HINTS CONFLICT WITH IMAGE**: The image is ground truth for structure and content presence. The text layer is ground truth for spelling of text that IS visible in the image.
        """
    )

    user_message = dedent(
        f"""Extract PageIR for the provided image (context: page_index={page_index}).

Before returning, scan the bottom ~10% of the page for any missed content.

Return the PageIR JSON only.
        """
    )

    # Append PDF-derived hints when available.
    hint_parts: list[str] = []

    if text_layer_hint is not None:
        hint_parts.append(
            f"## PDF TEXT LAYER REFERENCE (character-accurate — use for spelling)\n"
            f"The following text was extracted directly from the PDF's internal text "
            f"encoding. Use it as the authoritative source for character-level "
            f"spelling, especially for diacritics and special characters. Do NOT "
            f"copy its structure or reading order — use the image for that.\n"
            f"<text_layer>\n"
            f"{text_layer_hint}\n"
            f"</text_layer>"
        )

    if table_layer_hint is not None:
        hint_parts.append(
            f"## PDF TABLE LAYER REFERENCE (structural hint for tables)\n"
            f"The following table structures were extracted from the PDF. Use them "
            f"to confirm cell contents, column counts, and merged cells. The image "
            f"remains authoritative for table boundaries and classification.\n"
            f"<table_layer>\n"
            f"{table_layer_hint}\n"
            f"</table_layer>"
        )

    if hint_parts:
        user_message = user_message.strip() + "\n\n" + "\n\n".join(hint_parts)

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def validate_page_ir_extraction(
    *, image_height: int, image_width: int, page_index: int, page_ir_json: str
) -> PromptPair:
    """Generate the prompts for validating an extracted PageIR against a source image.

    The validation agent runs in a separate conversation from the extraction agent. It
    receives the extracted PageIR JSON and the source page image, then returns a
    structured ValidationVerdict.

    Parameters
    ----------
    image_height
        The height of the source image in pixels.
    image_width
        The width of the source image in pixels.
    page_index
        The 0-based page index.
    page_ir_json
        The serialized PageIR JSON string to validate.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        f"""You are a quality assurance agent for document digitization. Your task is to compare an extracted PageIR JSON against the original page image and identify any discrepancies.

You will receive:
1. The original page image (rendered at {image_width}×{image_height} pixels).
2. The extracted PageIR JSON that was produced by a separate extraction agent.

Your job is to verify that the extraction faithfully represents the source image. You are NOT re-extracting the page — you are auditing someone else's work.

## VALIDATION CHECKLIST

Compare the extraction against the image for each of the following:

1. **Missing content**: Are there any visible content blocks, tables, or figures on the page that are NOT represented in the PageIR? Pay special attention to content near the very top and bottom margins.
2. **Spurious content**: Does the PageIR contain any items that do NOT appear on the page (hallucinated content)?
3. **Text fidelity**: Is the extracted text faithful to what is visible? Check for missing words, added words, or significant transcription errors. Minor whitespace differences are acceptable.
4. **Block classification**:
   - Ruled grids/cells → TABLE (not FIGURE).
   - Section titles → HEADING (not ARTIFACT).
   - Running headers/footers/page numbers → ARTIFACT (not HEADING).
   - Prose text → PARAGRAPH.
   - Bulleted/numbered items → LIST.
5. **Reading order**: Are items ordered top-to-bottom within each column (left column before right for multi-column layouts)?
6. **Table structure**:
   - Are rows and cells captured faithfully?
   - Are blank cells represented as `text: null` (not omitted or hallucinated)?
   - Is `header_row_count` reasonable given the visible table structure?
   - Are row_span/col_span values only used for clearly visible merges?
   - Has the table been collapsed (e.g., multi-column grid extracted as single-column rows)?
7. **Bounding boxes**: Are bboxes reasonably tight to content and within page bounds ({image_width}×{image_height})? Are there obvious duplicates or placeholders?
8. **Figures** (if any): Do figure blocks have alt_text? Is embedded_text present when the figure contains visible text?
9. **Boundary markers**: Are continuation markers (resumed/truncated/both/complete) consistent with visible content flow at page edges?

## SEVERITY GUIDE
- **error**: The extraction is materially incorrect — missing content, hallucinated content, wrong classification that changes meaning, collapsed table structure, grossly wrong reading order.
- **warning**: Minor quality concern — slightly loose bounding box, borderline classification choice, minor whitespace issue.

## RULES
- A verdict of passed=false MUST include at least one issue with severity="error".
- If all issues are only warnings, set passed=true (warnings are informational).
- Be specific in issue descriptions: reference item indices (e.g., "items[3]"), quote relevant text, and describe the discrepancy between what the image shows and what the JSON contains.
- Do NOT flag issues that are correct per the extraction schema (e.g., text_en=null is expected during extraction; Python-filled fields like doc_key/dpi being null is expected).
- Focus on **material correctness**, not stylistic preferences.
"""
    )

    user_message = dedent(
        f"""Validate the following PageIR extraction for page_index={page_index}.

## Extracted PageIR JSON
```json
{page_ir_json}
```

Compare this JSON carefully against the attached page image and return a ValidationVerdict."""
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
