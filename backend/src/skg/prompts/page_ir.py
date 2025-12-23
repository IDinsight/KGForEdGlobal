"""This module contains prompt templates for extracting intermediate representation
information from page images.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Any, Optional

# Third Party Library
from dotmap import DotMap


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
8. **BBOX REQUIRED**: Every block/table MUST include a localized bbox [x0,y0,x1,y1]. Never omit it. BBoxes must be tight to the content. Never use a full-page bbox except for genuinely full-page images (rare).
9. **BBOX VALIDITY**: Every bbox must satisfy 0<=x0<x1<=image_width and 0<=y0<y1<=image_height.
10. Do not output empty tables; if you see a table, it must have at least one row.
11. Do a final scan of the bottom 10% of the page before finishing; do not stop early.
12. Do NOT create blocks that represent the whole page or background. Every block/table must correspond to actual visible content (text or table grid).
13. Do not emit any block that contains no content. A block must contain text OR list_items OR local_code.
14. Do NOT output a full-page “artifact” or “background” block. Only output artifacts when you see actual header/footer/page-number text.
15. artifact = running header/footer/page number only. Certificates/ISBN/publisher blocks are NOT artifacts; treat them as paragraph/heading/caption. Section titles like ‘Table of Contents’, ‘Preface’, chapter titles, etc. are NOT artifacts; they are headings.
16. For each TextUnit, choose the most specific language from Expected Languages when possible. Use und only if you genuinely cannot tell. Use mul only if multiple languages appear in the same TextUnit.

## BLOCK CLASSIFICATION
- **artifact**: Headers, footers, page numbers;
- **caption**: Labels for tables/figures (e.g., "Table 1").
- **heading**: Section titles.
- **list**: Bulleted/numbered items (use `list_items`, set `text=null`).
- **paragraph**: Prose (use `text`, set `list_items=null`).

## BOUNDARIES
- Item `boundary`: Use "resumed" (top missing), "truncated" (bottom missing), or "complete".
- Page `boundary_state`: Use "from_prev", "to_next", "both", or "standalone".
- If the top-most content clearly starts mid-sentence/mid-row/mid-list (e.g., begins with punctuation, trailing clause, continued numbering), set boundary_state="from_prev" and that item's boundary="resumed".
- If the bottom-most content is clearly cut off at the page bottom (mid-sentence, mid-cell, list continues), set boundary_state="to_next" and that item's boundary="truncated".
        """
    )

    user_message = dedent(
        f"""Extract PageIR for page_index={page_index}.

Requirements:
1. Identify blocks and tables from the {image_width}x{image_height} image.
2. Set "kind": "block" or "kind": "table" for every entry.
3. Use "und" for unknown languages.
4. If a table continues from a previous page and shows its headers again, set "repeats_header": true. Otherwise omit it or set false.
5. If you can confidently count the table's columns, set "n_cols".
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

    system_message = dedent(
        """You are a strict PageIR continuity verifier.

You will be given:
- bottom crop of page N
- top crop of page N+1
- excerpt of a candidate item near the bottom of page N (from the PageIR JSON)
- excerpt of a candidate item near the top of page N+1 (from the PageIR JSON)

Task:
1) Decide whether content continues from page N to page N+1.
2) Propose MINIMAL continuity-metadata edits if (and only if) the existing metadata is wrong.

Allowed edits (metadata only):
- set_prev_boundary_state: (standalone|to_next)
- set_next_boundary_state: (standalone|from_prev)
- item boundary on the referenced bottom/top items (complete|truncated|resumed)
- repeats_header on the next page table if (and only if) headers are repeated on the continuation page

Rules:
- DO NOT rewrite, move, merge, or complete text/table cells across pages.
- DO NOT invent missing content.
- Do NOT set repeats_header=true unless it is the SAME table continuing across the boundary.
- Only change continuity metadata fields. If everything is already correct, leave all set_* fields null.
- Return ONLY a JSON object matching the required schema. No prose.
- Always include a short rationale string.
- Use continuation_kind="unclear" when uncertain.

Pairwise safety rules (important):
- You are ONLY judging the boundary between N and N+1. You cannot know about other neighbors.
- Therefore:
  - set_prev_boundary_state MUST be either "standalone" or "to_next" (do NOT use "both" or "from_prev").
  - set_next_boundary_state MUST be either "standalone" or "from_prev" (do NOT use "both" or "to_next").

Decision guidance:
- Use the IMAGES as source of truth. Excerpts may be wrong/incomplete.
- TABLE continuation signals: same table grid continues, row text cut off at bottom then resumes at top, repeated header row (often same column labels).
- TEXT continuation signals: sentence continues mid-thought, list numbering/bullets continue, paragraph starts mid-sentence at top.

When is_continuation=true:
- If the excerpt boundaries are NOT already correct, you should usually set:
  - set_prev_item_boundary="truncated"
  - set_next_item_boundary="resumed"
- If the excerpt already shows those boundaries correctly, leave them null.

When unsure, set is_continuation=false, confidence low, and leave set_* fields null (unless the existing metadata is obviously wrong).
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
