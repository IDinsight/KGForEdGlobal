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
from skg.utils.constants import (
    BlockType,
    FigureKind,
    ItemBoundary,
    PageBoundaryState,
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
   - Correct: `"text": {{"text": "content", "language": "en"}}`
   - Incorrect: `"text": "content"`
   - Empty: If a cell is blank, set `"text": null`.
4. **TABLE COLUMN COUNT (optional)**: If you can clearly infer the number of visual columns in the table grid (from ruling/grid and headers), set `CurriculumTable.n_cols` to that integer. If unsure, omit it or set it to null.
5. **READING ORDER**: Populate `items` in visual reading order (left-to-right columns, then down).
6. **COORDINATES**: Use pixel coordinates (px) relative to {image_width}x{image_height}.
7. **VERBATIM**: Extract text exactly as seen. Do not fix typos or complete truncated sentences.
8. **NO TRANSLATION**:
   - Do NOT translate, paraphrase, or “helpfully” rewrite any text.
   - For every `TextUnit`, `text_en` MUST be null or omitted. (A later translation pass fills it.)
9. **DO NOT POPULATE PYTHON-FILLED FIELDS**:
   - The following PageIR fields are filled/overwritten by the Python pipeline and MUST be null or omitted:
     - doc_key
     - pdf_name
     - page_index
     - dpi
     - image_width
     - image_height
   - You may omit `coord_space`; if you include it, it MUST be exactly "px".
10. **BBOX REQUIRED**: Every block/table MUST include a localized bbox [x0,y0,x1,y1]. Never omit it. BBoxes must be tight to the content. Never use a full-page bbox except for genuinely full-page images (rare).
11. **BBOX VALIDITY**: Every bbox must satisfy 0<=x0<x1<=image_width and 0<=y0<y1<=image_height.
12. Do not output empty tables; if you see a table, it must have at least one row.
13. Do a final scan of the bottom 10% of the page before finishing; do not stop early.
14. Do NOT create blocks that represent the whole page or background. Every block/table/figure must correspond to actual visible content. Full-page bbox is allowed ONLY when the page is dominated by a single full-page figure/diagram.
15. Do not emit any block that contains no content.
 - For block_type in {{paragraph, heading, caption}}: block must have non-empty `text`.
 - For block_type=list: block must have non-empty `list_items` and `text=null`.
 - For block_type=figure: block must have a non-null `figure` object and `text=null` and `list_items=null`.
16. If block_type != "figure", then `figure` MUST be null or omitted.
17. Do NOT output a full-page “artifact” or “background” block. Only output artifacts when you see actual header/footer/page-number text.
18. artifact = running header/footer/page number only. Certificates/ISBN/publisher blocks are NOT artifacts. Logos/seals/crests/graphics are NOT artifacts; treat them as figure if you include them at all.
19. For each TextUnit, choose the most specific language from Expected Languages when possible. Use und only if you genuinely cannot tell. Use mul only if multiple languages appear in the same TextUnit.

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
- Item `boundary`: Use "resumed" (top missing), "truncated" (bottom missing), or "complete".
- Page `boundary_state`: Use "from_prev", "to_next", "both", or "standalone".
- If the top-most content clearly starts mid-sentence/mid-row/mid-list (e.g., begins with punctuation, trailing clause, continued numbering), set boundary_state="from_prev" and that item's boundary="resumed".
- If the bottom-most content is clearly cut off at the page bottom (mid-sentence, mid-cell, list continues), set boundary_state="to_next" and that item's boundary="truncated".
- For block_type="figure": default boundary="complete" unless the figure is visibly cut off by the page edge.
        """
    )

    user_message = dedent(
        f"""Extract PageIR for page_index={page_index}.

Requirements:
1. Identify blocks (including figure blocks) and tables from the {image_width}x{image_height} image.
2. Set "kind": "block" or "kind": "table" for every entry.
3. Do NOT translate. `TextUnit.text_en` must be null/omitted. Leave doc_key/pdf_name/page_index/dpi/image_* null/omitted.
4. Use "und" for unknown languages.
5. If a table continues from a previous page and shows its headers again, set "repeats_header": true. Otherwise omit it or set null.
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

    cont_true_allowed = json.dumps(
        [
            PageContinuationKind.TABLE.value,
            PageContinuationKind.TEXT.value,
            PageContinuationKind.FIGURE.value,
        ],
        ensure_ascii=False,
    )
    item_boundary_allowed = json.dumps(
        [b.value for b in ItemBoundary], ensure_ascii=False
    )
    next_boundary_allowed = json.dumps(
        [
            PageBoundaryState.STANDALONE.value,
            PageBoundaryState.CONTINUES_FROM_PREV.value,
        ],
        ensure_ascii=False,
    )
    prev_boundary_allowed = json.dumps(
        [PageBoundaryState.STANDALONE.value, PageBoundaryState.CONTINUES_TO_NEXT.value],
        ensure_ascii=False,
    )

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
- set_prev_boundary_state: one of {prev_boundary_allowed}
- set_next_boundary_state: one of {next_boundary_allowed}
- set_prev_item_boundary / set_next_item_boundary: one of {item_boundary_allowed}
- set_next_table_repeats_header: only if the NEXT candidate item is a table AND it is the same table continuing AND the header is repeated

Rules:
- DO NOT rewrite, move, merge, or complete text/table cells across pages.
- DO NOT invent missing content.
- Do NOT set repeats_header=true unless it is the SAME table continuing across the boundary.
- Only change continuity metadata fields. If everything is already correct, leave all set_* fields null.
- Return ONLY a JSON object matching the required schema. No prose.
- Always include a short rationale string.
- If uncertain, set is_continuation=false, continuation_kind="unclear", low confidence, and leave all set_* null.

Pairwise safety rules (important):
- You are ONLY judging the boundary between N and N+1. You cannot know about other neighbors.
- Therefore:
  - set_prev_boundary_state MUST be one of {prev_boundary_allowed}.
  - set_next_boundary_state MUST be one of {next_boundary_allowed}.

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
- If is_continuation=false and you are confident, set continuation_kind="none".
- If is_continuation=false and you are uncertain, set continuation_kind="unclear".
- If is_continuation=true, continuation_kind MUST be one of: {cont_true_allowed} (never "none" or "unclear").
- If is_continuation=false, continuation_kind MUST be one of: ["none","unclear"].
- Use continuation_kind="table" only for table continuations.
- Use continuation_kind="text" only for text/list continuations.
- Use continuation_kind="figure" only for figure/diagram continuations (same figure is cut off and resumes on next page).

Boundary rules:
- When is_continuation=true, the prev candidate should be truncated and the next candidate should be resumed.
- When is_continuation=true, NEVER set set_prev_item_boundary or set_next_item_boundary to "complete".
- When is_continuation=false, do not suggest truncated/resumed; you may suggest "complete" only if the candidate item is incorrectly marked.

Candidate mismatch safety:
- If the images suggest there might be continuation somewhere across the boundary, but it is NOT clearly between these two candidate items,
  then set is_continuation=false, continuation_kind="unclear", confidence low, and leave all set_* fields null.
  (This avoids forcing wrong edits onto the wrong anchor items.)

When unsure:
- Prefer is_continuation=false, continuation_kind="unclear", low confidence, and leave set_* fields null (unless existing metadata is obviously wrong on the candidates).
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
