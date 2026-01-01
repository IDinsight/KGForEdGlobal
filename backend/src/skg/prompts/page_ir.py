"""This module contains prompt templates for extracting Intermediate Representation
(IR) information from PDF pages.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Any, Optional

# Third Party Library
from dotmap import DotMap

# Package Library
from skg.utils.constants import (
    BlockType,
    FigureKind,
    ItemBoundary,
    PageContinuationKind,
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
    text_layer_context = (
        ""
        if not text_layer_hints
        else f"## TEXT LAYER HINTS\n{text_layer_hints}\nNB: Text layer hints are only hints and they may be incomplete."
    )

    system_message = dedent(
        f"""You are a high-fidelity curriculum digitization agent. Convert the single page image {doc_context} PDF into a strict PageIR JSON object.

{text_layer_context}

## HARD RULES
1. **If text-layer hints contradict the image, trust the image.**
2. **READING ORDER**: Populate `items` in visual reading order (left-to-right columns, then down).
3. **VERBATIM**: Extract text exactly as seen. Do not fix typos or complete truncated sentences.
4. **KIND DISCRIMINATOR**: Every item in the `items` list MUST have a `"kind"` field set to either `"block"` or `"table"`.
5. **NO HALLUCINATION**: Do not add rows/cells/text that are not visible. If a cell is blank, set text: null.
6. **Do a final scan of the bottom 10% of the page before finishing; do not stop early.**
7. **DO NOT POPULATE PYTHON-FILLED FIELDS**:
  - The following PageIR fields are filled/overwritten by the Python pipeline and MUST be null or omitted:
    - doc_key
    - pdf_name
    - page_index
    - dpi
    - image_width
    - image_height
  - You may omit `coord_space`; if you include it, it MUST be exactly "px".

## BOUNDING BOXES
1. **COORDINATES**: Use pixel coordinates (px) relative to {image_width}x{image_height}.
2. **BBOX REQUIRED**: Every block/table MUST include a localized bbox [x0,y0,x1,y1]. Never omit it. BBoxes must be tight to the content. Never use a full-page bbox except for genuinely full-page images (rare).
3. **BBOX VALIDITY**: Every bbox must satisfy 0<=x0<x1<=image_width and 0<=y0<y1<=image_height.
4. **NO PLACEHOLDER/REUSED BBOXES**: Never use the same bbox for multiple items. Never copy the page bbox into items. Each item bbox must tightly wrap only that item’s visible pixels. If unsure, err slightly smaller than too large—never full-page.
5. Full-page bbox is allowed ONLY when the output contains exactly ONE item and that item is kind="block", block_type="{BlockType.FIGURE}" (a true full-page figure page). Otherwise, full-page bbox is never allowed.

## BOUNDARIES
1. Item `boundary` is SEMANTIC continuity (not “missing borders”):
  - "{ItemBoundary.RESUMED}": This item is a continuation from the previous page
  - "{ItemBoundary.TRUNCATED}": This item continues onto the next page
  - "{ItemBoundary.BOTH}": Continuation from previous page AND to next page (middle slice of a long item)
  - "{ItemBoundary.COMPLETE}": Fully contained on this page
2. Focus on setting each item's `boundary` correctly ({ItemBoundary.RESUMED}/{ItemBoundary.TRUNCATED}/{ItemBoundary.BOTH}/{ItemBoundary.COMPLETE}) based on visible continuation cues.
3. DO NOT rely on whether table borders are drawn. Many PDFs repeat gridlines and headers on continuation pages.
4. Page `boundary_state` is derived by Python from item boundaries. You may omit it.
5. For block_type="{BlockType.FIGURE}": default boundary="{ItemBoundary.COMPLETE}" unless the same figure is visibly cut off by the page edge.

## BLOCK CLASSIFICATIONS
1. Valid block_type values: {allowed_block_types}
  - **{BlockType.ARTIFACT.value}**: Headers, footers, page numbers;
  - **{BlockType.CAPTION.value}**: Labels for tables/figures (e.g., "Table 1").
  - **{BlockType.FIGURE.value}**: Diagrams/figures/illustrations/charts/flowcharts (use `figure`, set `text=null`, `list_items=null`).
  - **{BlockType.HEADING.value}**: Section titles.
  - **{BlockType.LIST.value}**: Bulleted/numbered items (use `list_items`, set `text=null`).
  - **{BlockType.PARAGRAPH.value}**: Prose (use `text`, set `list_items=null`).

## BLOCK TYPES
1. Do not emit any block that contains no content.
  - For block_type in ({BlockType.PARAGRAPH, BlockType.HEADING, BlockType.CAPTION}): block must have non-empty `text`.
  - For block_type={BlockType.LIST}: block must have non-empty `list_items` and `text=null`.
  - For block_type={BlockType.FIGURE}: block must have a non-null `figure` object and `text=null` and `list_items=null`.
  - For block_type="{BlockType.ARTIFACT}": must have non-empty text (page number/running header/footer), never full-page.
2. If block_type != "{BlockType.FIGURE}", then `figure` MUST be null or omitted.
3. Do NOT output a full-page “{BlockType.ARTIFACT}” block. Only output artifacts when you see actual header/footer/page-number text.
4. Ignore page border lines/decorative frames. Do not emit blocks/tables for borders or background. Page numbers must be a small ARTIFACT bbox around the digits/roman numerals only. Signatures/logos are FIGURE only if you include them, with tight bbox around the graphic only (not margins).
5. Artifacts means running header/footer/page number only. Certificates/ISBN/publisher blocks are NOT artifacts. Logos/seals/crests/graphics are NOT artifacts; treat them as figure if you include them at all.
6. For any non-list block (including figure), list_items MUST be null or omitted (never []).

## LANGUAGES
1. **Expected Languages**: {lang_context}.
2. Expected Languages are only hints (common in Tanzania PDFs to include tables with fr/zh/ar in addition to en/sw).
3. Use the BEST-MATCH BCP-47 language for the visible text, even if it is NOT in Expected Languages.
4. Use "und" if the language is unknown or if you genuinely cannot tell.
5. Use "mul" only if multiple languages appear in the SAME TextUnit (e.g., bilingual French+English in one cell).
6. **NO TRANSLATION**:
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
  - block_type="{BlockType.FIGURE}"
  - bbox tightly around the figure region (include axes/arrows/labels inside the region)
  - text=null
  - list_items=null
  - figure = {{
      "alt_text": VERY short (<=200 chars) non-semantic description (e.g. "flowchart with arrows", "pyramid diagram"),
      "caption": null OR a TextUnit object ({{"language": "...", "text": "...", "text_en": null}}) ONLY if the caption is clearly inside the figure bbox,
      "contains_text": true/false/null,
      "figure_kind": one of {allowed_figure_kinds},
    }}
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
5. Only set repeats_header when the table is {ItemBoundary.RESUMED}/{ItemBoundary.BOTH} (i.e., continuing from previous page). Never set it for {ItemBoundary.COMPLETE} tables.
6. If you can confidently count the table's columns, set "n_cols".
7. If you see a diagram/figure (not a table), output a block_type="{BlockType.FIGURE}" block with a `figure` object and tight bbox.

Final check before output: no item bbox may be full-page or reused across items (except the single full-page figure case).
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )


def verify_page_ir_pairs_from_extraction(
    *,
    next_item_excerpt: dict[str, Any],
    next_page_index: int,
    prev_item_excerpt: dict[str, Any],
    prev_page_index: int,
) -> DotMap:
    """Generate the prompts for verifying PageIR pairs from the extraction step.

    Parameters
    ----------
    next_item_excerpt
        Excerpt of a candidate item near the TOP of page N+1 JSON.
    next_page_index
        The 0-based page index of the next page (N+1).
    prev_item_excerpt
        Excerpt of a candidate item near the BOTTOM of page N JSON.
    prev_page_index
        The 0-based page index of the previous page (N).

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    prev_boundary_allowed = json.dumps(
        [ItemBoundary.TRUNCATED.value, ItemBoundary.COMPLETE.value], ensure_ascii=False
    )
    next_boundary_allowed = json.dumps(
        [ItemBoundary.RESUMED.value, ItemBoundary.COMPLETE.value], ensure_ascii=False
    )

    system_message = dedent(
        f"""You are a strict PageIR continuity verifier.

You will be given:
1. Bottom crop of page N (IMAGE A)
2. Top crop of page N+1 (IMAGE B)
3. Excerpt of a candidate item near the bottom of page N (from PageIR JSON)
4. Excerpt of a candidate item near the top of page N+1 (from PageIR JSON)

## TASK
1. Decide whether the candidate item at the bottom of page N continues onto the candidate item at the top of page N+1.
2. Propose MINIMAL continuity-metadata edits if (and only if) the existing metadata is wrong.

## HARD RULES
1. Your decision must be about whether THESE TWO CANDIDATE ITEMS are a continuation across the boundary.
2. You are not verifying the whole pages globally; you are verifying the chosen boundary-anchor items.
3. DO NOT rewrite, move, merge, or complete text/table cells across pages.
4. DO NOT invent missing content.
5. Do NOT set repeats_header=true unless it is the SAME table continuing across the boundary.
6. Only change continuity metadata fields. If everything is already correct, leave all set_* fields null.
7. Return ONLY a JSON object matching the required schema. No prose.
8. Always include a short rationale string.
9. If uncertain, set is_continuation=false, continuation_kind="{PageContinuationKind.NONE}", low confidence, and leave all set_* null.
10. If is_continuation=false, do not set boundaries to {ItemBoundary.RESUMED} or {ItemBoundary.TRUNCATED}. Only suggest {ItemBoundary.COMPLETE} (or null).

## ALLOWED EDITS (METADATA ONLY)
1. set_prev_item_boundary: one of {prev_boundary_allowed} (or null)
2. set_next_item_boundary: one of {next_boundary_allowed} (or null)
3. set_next_table_repeats_header:
  - Only set this when is_continuation=true AND continuation_kind="table" AND the NEXT candidate is a table AND it is the SAME table continuing.
  - Decide using IMAGE B (and IMAGE A), not the excerpt fields.
  - Set to true if the header rows are visibly repeated on page N+1.
  - Set to false if the same table continues but headers are visibly NOT repeated.
  - If you cannot confidently tell, set it to null (do not guess).
  - Null means “leave as-is/do not patch”.
  - If the JSON excerpt’s repeats_header already matches what you see in the images, leave it null.

## DECISION GUIDANCE
1. Use the IMAGES as source of truth. Excerpts may be wrong/incomplete.
2. TABLE continuation signals: same table grid continues, row text cut off at bottom then resumes at top, repeated header row (often same column labels).
3. TEXT continuation signals (STRONG EVIDENCE REQUIRED):
  - Only choose continuation_kind="text" when you can see strong truncation in IMAGE A and a clear resumption in IMAGE B.
  - Strong truncation cues include at least one of:
    - The last visible line ends with a dangling comma/semicolon/colon/ellipsis (",", ";", ":", "…")
    - A word is visibly cut with a hyphen/dash at the end of the line ("-", "–", "—")
    - Unmatched open bracket/paren/quote in the visible text near the end
    - A list item/numbering clearly continues (e.g., 1., 2., 3. or bullets) and IMAGE B continues the same list
    - The top of IMAGE B starts mid-sentence (lowercase continuation, no new heading/caption) and matches the tail of IMAGE A
  - Hard negatives (DO NOT mark text continuation even if topic is related):
    - "Table N shows/illustrates/indicates ..." at end of page N followed by a new page starting with "Table N:" or a table/caption. This is a layout handoff to a table/caption, NOT a text continuation.
    - "Figure N shows ..." at end of page N followed by "Figure N:" or a figure/caption at top of page N+1.
    - If the bottom text clearly ends a complete sentence (ends with ".", "!" or "?"), do NOT mark as text continuation.
    - If the next candidate begins with a table/figure caption label ("Table", "Figure"), do NOT mark as text continuation.
4. FIGURE candidates: most figures do NOT continue across pages. Only mark continuation if the SAME figure is clearly cut off and resumes on the next page.
5. Excerpt metadata fields like boundary/repeats_header may be null/unreliable; do not treat null as evidence of "complete".

## CONTINUATION KIND RULES
1. If is_continuation=false, set continuation_kind="{PageContinuationKind.NONE}".
2. If is_continuation=true and continuation_kind='{PageContinuationKind.TABLE}', set set_next_table_repeats_header to true/false ONLY when you can confidently see whether headers repeat; otherwise leave it null.
3. Use continuation_kind="{PageContinuationKind.TABLE}" only for table continuations.
4. Use continuation_kind="{PageContinuationKind.TEXT}" only for text/list continuations.
5. Use continuation_kind="{PageContinuationKind.FIGURE}" only for figure/diagram continuations (same figure is cut off and resumes on next page).
6. When is_continuation=true, the previous candidate should be compatible with continuing to next (TRUNCATED or BOTH), and the next candidate should be compatible with continuing from previous (RESUMED or BOTH). If incompatible, propose minimal boundary edits as allowed.
7. Candidate mismatch safety: If the images suggest there might be continuation somewhere across the boundary, but it is NOT clearly between these two candidate items, then set is_continuation=false, continuation_kind="{PageContinuationKind.NONE}", confidence low, and leave all set_* fields null. (This avoids forcing wrong edits onto the wrong anchor items.)
8. When unsure, set is_continuation=false, continuation_kind="{PageContinuationKind.NONE}", confidence <= 0.49, and leave all set_* fields null.

## PAIRWISE LIMITATION (CRITICAL, COMMON IN LONG TABLES)
1. You only see the bottom of page N and the top of page N+1.
  - Therefore, DO NOT try to decide TRUNCATED vs. BOTH (or RESUMED vs. BOTH).
  - Never set boundaries to "{ItemBoundary.BOTH}" in this step. Leave BOTH decisions to Python/global stitching.
  - If a candidate boundary is already "{ItemBoundary.BOTH}", that is compatible with continuation; do not change it.
  - Only correct boundaries when they are clearly incompatible:
    - If continuation=true and previous is marked {ItemBoundary.COMPLETE} (or {ItemBoundary.RESUMED}), suggest set_prev_item_boundary="{ItemBoundary.TRUNCATED}".
    - If continuation=true and next is marked {ItemBoundary.COMPLETE} (or {ItemBoundary.TRUNCATED}), suggest set_next_item_boundary="{ItemBoundary.RESUMED}".
2. If is_continuation=false and the previous candidate is marked {ItemBoundary.TRUNCATED}/{ItemBoundary.BOTH}, suggest set_prev_item_boundary="{ItemBoundary.COMPLETE}" only if the crop shows a clean end (sentence ends cleanly/table ends cleanly).
3. If is_continuation=false and the next candidate is marked {ItemBoundary.RESUMED}/{ItemBoundary.BOTH}, suggest set_next_item_boundary="{ItemBoundary.COMPLETE}" only if the crop shows a clean start (new heading/table start, etc.).

## CONFIDENCE CALIBRATION RULES
1. Use confidence ≥ 0.75 only when continuation is visually obvious (clear cut/resume).
2. Use 0.50–0.74 for plausible but not definitive.
3. Use ≤0.49 when uncertain/no continuation.
    """
    )

    user_message = json.dumps(
        {
            "prev_page_index": prev_page_index,
            "next_page_index": next_page_index,
            "prev_candidate_item_excerpt": prev_item_excerpt,
            "next_candidate_item_excerpt": next_item_excerpt,
        },
        ensure_ascii=False,
        indent=2,
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
