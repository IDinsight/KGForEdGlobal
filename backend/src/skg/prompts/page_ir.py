"""This module contains prompt templates for extracting intermediate representation
information from page images.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Any, Optional

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
    """Generate the prompts for extracting PageIR from a page image.

    Parameters
    ----------
    country
        The country of origin for the document.
    image_height
        The height of the image in pixels.
    image_width
        The width of the image in pixels.
    languages
        List of expected languages (e.g., ['en', 'sw']).
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
        f"""You are a high-fidelity curriculum digitization agent. Convert the image {doc_context} into a strict PageIR JSON object.

## DOCUMENT CONTEXT
- **Expected Languages**: {lang_context}. Use BCP-47 codes. Use "und" if unknown; "mul" if mixed.

{text_layer_context}

## HARD RULES
1. **If text-layer hints contradict the image, trust the image.**
2. **KIND DISCRIMINATOR**: Every item in the `items` list MUST have a `"kind"` field set to either `"block"` or `"table"`.
3. **TABLE CELL STRUCTURE**: The `text` field inside a `TableCell` is an OBJECT (TextUnit).
   - Correct: `"text": {{"text": "content", "language": "en", "text_en": null}}`
   - Incorrect: `"text": "content"`
   - Empty: If a cell is blank, set `"text": null`.
4. **TABLE COLUMN COUNT (optional)**: If you can clearly infer the number of visual columns in the table grid (from ruling/grid and headers), set `CurriculumTable.n_cols` to that integer. If unsure, omit it or set it to null.
5. **TABLE HEADER ROWS**:
   - If the table has header rows at the top, set `header_row_count` to the number of header rows.
   - Keep header rows INSIDE `rows`; do not split headers into a separate field.
   - If unsure, leave it at 0 / omit it.
6. **READING ORDER**: Populate `items` in visual reading order (left-to-right columns, then down).
7. **COORDINATES**: Use pixel coordinates (px) relative to {image_width}x{image_height}.
8. **VERBATIM**: Extract text exactly as seen. Do not fix typos or complete truncated sentences.
9. **NO TRANSLATION**:
   - Do NOT translate, paraphrase, or “helpfully” rewrite any text.
   - For every `TextUnit`, `text_en` MUST be null or omitted. (A later translation pass fills it.)
10. **DO NOT POPULATE PYTHON-FILLED FIELDS**:
   - The following PageIR fields are filled/overwritten by the Python pipeline and MUST be null or omitted:
     - doc_key
     - pdf_name
     - page_index
     - dpi
     - image_width
     - image_height
   - You may omit `coord_space`; if you include it, it MUST be exactly "px".
11. **BBOX REQUIRED**: Every block/table MUST include a localized bbox [x0,y0,x1,y1]. Never omit it. BBoxes must be tight to the content. Never use a full-page bbox except for genuinely full-page images (rare).
12. **BBOX VALIDITY**: Every bbox must satisfy 0<=x0<x1<=image_width and 0<=y0<y1<=image_height.
13. Do not output empty tables; if you see a table, it must have at least one row.
14. Do a final scan of the bottom 10% of the page before finishing; do not stop early.
15. Do NOT create blocks that represent the whole page or background. Every block/table/figure must correspond to actual visible content. Full-page bbox is allowed ONLY when the page is dominated by a single full-page figure/diagram.
16. Do not emit any block that contains no content.
 - For block_type in {{paragraph, heading, caption}}: block must have non-empty `text`.
 - For block_type=list: block must have non-empty `list_items` and `text=null`.
 - For block_type=figure: block must have a non-null `figure` object and `text=null` and `list_items=null`.
17. If block_type != "figure", then `figure` MUST be null or omitted.
18. Do NOT output a full-page “artifact” or “background” block. Only output artifacts when you see actual header/footer/page-number text.
19. artifact = running header/footer/page number only. Certificates/ISBN/publisher blocks are NOT artifacts. Logos/seals/crests/graphics are NOT artifacts; treat them as figure if you include them at all.
20. LANGUAGE TAGGING (IMPORTANT):
   - Use the BEST-MATCH BCP-47 language for the visible text, even if it is NOT in Expected Languages.
   - Expected Languages are only hints (common in Tanzania PDFs to include tables with fr/zh/ar in addition to en/sw).
   - Use "und" only if you genuinely cannot tell.
   - Use "mul" only if multiple languages appear in the SAME TextUnit (e.g., bilingual French+English in one cell).
21. For any non-list block (including figure), list_items MUST be null or omitted (never []).
22. MIXED LANGUAGE IN ONE TEXTUNIT: If a single block/cell contains multiple languages (common in bilingual curriculum tables), set TextUnit.language="mul" and keep the full verbatim text as-is. Do not split into multiple cells unless the grid shows separate cells.
23. GRID-TRUE ROWS ONLY: Only create new TableRows when there is a visible row boundary in the table grid. Numbered lines inside one cell stay inside that cell’s text.

## BLOCK CLASSIFICATION

Valid block_type values: {allowed_block_types}

- **{BlockType.ARTIFACT.value}**: Headers, footers, page numbers;
- **{BlockType.CAPTION.value}**: Labels for tables/figures (e.g., "Table 1").
- **{BlockType.FIGURE.value}**: Diagrams/figures/illustrations/charts/flowcharts (use `figure`, set `text=null`, `list_items=null`).
- **{BlockType.HEADING.value}**: Section titles.
- **{BlockType.LIST.value}**: Bulleted/numbered items (use `list_items`, set `text=null`).
- **{BlockType.PARAGRAPH.value}**: Prose (use `text`, set `list_items=null`).

## FIGURES / DIAGRAMS
- If the page contains a diagram/figure/illustration/chart/flowchart that is NOT a table grid, emit a block with:
  - kind="block"
  - block_type="figure"
  - bbox tightly around the figure region (include axes/arrows/labels inside the region)
  - text=null
  - list_items=null
  - figure = {{
      "figure_kind": one of {allowed_figure_kinds},
      "contains_text": true/false/null,
      "alt_text": VERY short non-semantic description (<=200 chars), e.g. "flowchart with arrows", "pyramid diagram"
      "caption": null OR a TextUnit object ({{"language": "...", "text": "...", "text_en": null}}) ONLY if the caption is clearly inside the figure bbox
    }}
- Do NOT emit a figure for tiny decorative elements (small logos/ornaments) unless they are central content.

- Captions rule:
  - If you see a caption like "Figure 2: ..." near a figure, prefer to extract it as its own block_type="caption"
    item (with text=TextUnit) in reading order.
  - Only set figure.caption when the caption is unambiguously part of the figure region itself.

- Do NOT interpret the diagram meaning. Do NOT convert diagram content into prose. Just preserve the region + light hints.
- Do NOT misclassify tables as figures. If there is a ruled grid with cells, it must be a table.

## BOUNDARIES
- Item `boundary` is SEMANTIC continuity (not “missing borders”):
  - "resumed"  = this item is a continuation from the previous page
  - "truncated" = this item continues onto the next page
  - "both" = continuation from prev AND to next (middle slice of a long item)
  - "complete" = fully contained on this page
- DO NOT rely on whether table borders are drawn. Many PDFs repeat gridlines and headers on continuation pages.

- Page `boundary_state` is derived by Python from item boundaries. You may omit it.
- Focus on setting each item's `boundary` correctly (resumed/truncated/both/complete) based on visible continuation cues.
- For block_type="figure": default boundary="complete" unless the same figure is visibly cut off by the page edge.
        """
    )

    user_message = dedent(
        f"""Extract PageIR for the provided image (context: this is page_index={page_index} in the PDF).
IMPORTANT: Do NOT set the PageIR.page_index field; it must be null or omitted.

Requirements:
1. Identify blocks (including figure blocks) and tables from the {image_width}x{image_height} image.
2. Set "kind": "block" or "kind": "table" for every entry.
3. Do NOT translate. `TextUnit.text_en` must be null/omitted. Leave doc_key/pdf_name/page_index/dpi/image_* null/omitted.
4. Use the best-match BCP-47 language code for the visible text (not limited to Expected Languages). Use "und" if unknown.
5. Only set repeats_header when the table is resumed/both (i.e., continuing from previous page). Never set it for complete tables.
6. If you can confidently count the table's columns, set "n_cols".
7. If you see a diagram/figure (not a table), output a block_type="figure" block with a `figure` object and tight bbox.
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
        [ItemBoundary.TRUNCATED.value], ensure_ascii=False
    )
    next_boundary_allowed = json.dumps([ItemBoundary.RESUMED.value], ensure_ascii=False)

    system_message = dedent(
        f"""You are a strict PageIR continuity verifier.

You will be given:
- bottom crop of page N (IMAGE A)
- top crop of page N+1 (IMAGE B)
- excerpt of a candidate item near the bottom of page N (from PageIR JSON)
- excerpt of a candidate item near the top of page N+1 (from PageIR JSON)

IMPORTANT: Your decision must be about whether THESE TWO CANDIDATE ITEMS are a continuation across the boundary.
(You are not verifying the whole pages globally; you are verifying the chosen boundary-anchor items.)

Task:
1) Decide whether the candidate item at the bottom of page N continues onto the candidate item at the top of page N+1.
2) Propose MINIMAL continuity-metadata edits if (and only if) the existing metadata is wrong.

Allowed edits (metadata only):
- set_prev_item_boundary: one of {prev_boundary_allowed} (or null)
- set_next_item_boundary: one of {next_boundary_allowed} (or null)
- set_next_table_repeats_header:
    - Only when NEXT candidate is a table AND it is the SAME table continuing.
    - Set to true if the header rows are visibly repeated on page N+1.
    - Set to false ONLY if the excerpt explicitly shows repeats_header=true but IMAGE B clearly does NOT repeat headers. Otherwise leave it null.
    - Null means “leave as-is”.

Rules:
- DO NOT rewrite, move, merge, or complete text/table cells across pages.
- DO NOT invent missing content.
- Do NOT set repeats_header=true unless it is the SAME table continuing across the boundary.
- Only change continuity metadata fields. If everything is already correct, leave all set_* fields null.
- Return ONLY a JSON object matching the required schema. No prose.
- Always include a short rationale string.
- If uncertain, set is_continuation=false, continuation_kind="none", low confidence, and leave all set_* null.

Decision guidance:
- Use the IMAGES as source of truth. Excerpts may be wrong/incomplete.
- TABLE continuation signals: same table grid continues, row text cut off at bottom then resumes at top, repeated header row (often same column labels).
- TEXT continuation signals (STRONG EVIDENCE REQUIRED):
  Only choose continuation_kind="text" when you can see strong truncation in IMAGE A and a clear resumption in IMAGE B.
  Strong truncation cues include at least one of:
   - The last visible line ends with a dangling comma/semicolon/colon/ellipsis (",", ";", ":", "…")
   - A word is visibly cut with a hyphen/dash at the end of the line ("-", "–", "—")
   - Unmatched open bracket/paren/quote in the visible text near the end
   - A list item/numbering clearly continues (e.g., 1., 2., 3. or bullets) and IMAGE B continues the same list
   - The top of IMAGE B starts mid-sentence (lowercase continuation, no new heading/caption) and matches the tail of IMAGE A
  Hard negatives (DO NOT mark text continuation even if topic is related):
   - "Table N shows/illustrates/indicates ..." at end of page N followed by a new page starting with "Table N:" or a table/caption. This is a layout handoff to a table/caption, NOT a text continuation.
   - "Figure N shows ..." at end of page N followed by "Figure N:" or a figure/caption at top of page N+1.
   - If the bottom text clearly ends a complete sentence (ends with ".", "!" or "?"), do NOT mark as text continuation.
   - If the next candidate begins with a table/figure caption label ("Table", "Figure"), do NOT mark as text continuation.
- FIGURE candidates: most figures do NOT continue across pages. Only mark continuation if the SAME figure is clearly cut off and resumes on the next page.

continuation_kind rules:
- If is_continuation=false, set continuation_kind="none".
- If is_continuation=true and continuation_kind='table', ALWAYS set set_next_table_repeats_header to true/false (never null).
- Use continuation_kind="table" only for table continuations.
- Use continuation_kind="text" only for text/list continuations.
- Use continuation_kind="figure" only for figure/diagram continuations (same figure is cut off and resumes on next page).

Boundary rules:
- When is_continuation=true, the prev candidate must indicate it continues to next (TRUNCATED or BOTH),
  and the next candidate must indicate it continues from prev (RESUMED or BOTH).

PAIRWISE LIMITATION (CRITICAL, common in long tables):
- You only see the bottom of page N and the top of page N+1.
- Therefore, DO NOT try to decide TRUNCATED vs BOTH (or RESUMED vs BOTH).
  Only correct boundaries when they are clearly incompatible:
   * If continuation=true and prev is marked COMPLETE (or RESUMED), suggest set_prev_item_boundary="truncated".
   * If continuation=true and next is marked COMPLETE (or TRUNCATED), suggest set_next_item_boundary="resumed".
  Otherwise leave set_* null.

Candidate mismatch safety:
- If the images suggest there might be continuation somewhere across the boundary, but it is NOT clearly between these two candidate items,
  then set is_continuation=false, continuation_kind="none", confidence low, and leave all set_* fields null.
  (This avoids forcing wrong edits onto the wrong anchor items.)

When unsure:
- Set is_continuation=false, continuation_kind="none", confidence <= 0.49, and leave all set_* fields null.
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
