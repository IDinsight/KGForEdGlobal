"""This module contains prompt templates for **verifying** Intermediate Representation
(IR) information from PDF pages.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Any

# Third Party Library
from dotmap import DotMap

# Package Library
from skg.utils.constants import ItemBoundary, PageContinuationKind


def verify_page_ir_pairs_from_extraction(
    *,
    next_item: dict[str, Any],
    next_page_index: int,
    prev_item: dict[str, Any],
    prev_page_index: int,
) -> DotMap:
    """Generate the prompts for verifying PageIR pairs from the extraction step.

    Parameters
    ----------
    next_item
        The candidate item near the TOP of page N+1 JSON.
    next_page_index
        The 0-based page index of the next page (N+1).
    prev_item
        The candidate item near the BOTTOM of page N JSON.
    prev_page_index
        The 0-based page index of the previous page (N).

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        f"""You are a strict PageIR continuity verifier.

You will be given:
1. Entire page N (IMAGE A)
2. Top crop of page N+1 (IMAGE B)
3. Candidate item near the BOTTOM of page N
4. Candidate item near the TOP of page N+1

## TASK
1. Decide whether the candidate item at the BOTTOM of page N continues onto the candidate item at the TOP of page N+1.
2. If (and only if) there is a continuation AND the existing continuity metadata is missing/incompatible, propose MINIMAL additive continuity-metadata edits.
3. Return ONLY a JSON object matching the required schema and always include a short rationale string. No prose.

## HARD RULES
1. Your decision must be about whether THESE TWO CANDIDATE ITEMS are a continuation across the boundary.
2. You are not verifying the whole pages globally; you are verifying ONLY the chosen boundary-anchor items.
3. DO NOT rewrite, move, merge, or complete text/table cells across pages.
4. DO NOT invent missing content.
5. Only propose additive continuity edits in the positive case:
   - If is_continuation=true, you may set set_prev_item_boundary="{ItemBoundary.TRUNCATED.value}" and/or set_next_item_boundary="{ItemBoundary.RESUMED.value}" (or leave them null).
   - If is_continuation=false:
     - You MUST set set_prev_item_boundary="{ItemBoundary.COMPLETE.value}" if the previous item is currently marked as TRUNCATED/BOTH.
     - You MUST set set_next_item_boundary="{ItemBoundary.COMPLETE.value}" if the next item is currently marked as RESUMED/BOTH.
     - Otherwise leave them null."
6. For TABLE candidates, “continuation” is decided at the TABLE level (same grid + same header schema).
  - DO NOT require that the last row on page N continues into the first row on page N+1.
  - A change in table content/numbering is NORMAL within the SAME long table and is NOT evidence of a new table.
7. Do NOT set repeats_header=true unless it is the SAME table continuing across the boundary.
8. HEADING/CAPTION SAFETY (TEXT ONLY):
  - NEVER treat a HEADING or CAPTION as part of a TEXT continuation.
  - If you would otherwise choose continuation_kind="text" but either candidate is a HEADING/CAPTION, then set is_continuation=false and continuation_kind="none".
  - In the negative case, close dangling TRUNCATED/RESUMED boundaries by setting them to "complete" when required.
9. CAPTION-AS-ANCHOR FOR TABLE CONTINUATION:
  - This safety rule does NOT block TABLE continuation.
  - If the previous candidate is a TABLE and the next candidate is a CAPTION/HEADING/PARAGRAPH that clearly labels the SAME table continuing on page N+1 (e.g., "Table X (continued)" or similar), you MAY set is_continuation=true and continuation_kind="table".
  - In that case, set_next_table_repeats_header MUST be null (because the next candidate is not a Table).
10. ORDERING SAFETY (TEXT/LIST ONLY):
  - If continuation_kind="{PageContinuationKind.TEXT.value}" (including lists) AND IMAGE B starts with a non-artifact HEADING/CAPTION/TITLE above the NEXT candidate block (e.g., "Table of Contents", "List of Figures", "Chapter 1", "Introduction"), then set: is_continuation=false, continuation_kind="{PageContinuationKind.NONE.value}", and leave all set_* null.
  - Exception: This rule does not apply to TABLE continuations (tables may have captions like "Table X (continued)" above them).
11. UNCERTAINTY POLICY (must follow exactly):
  - Default when uncertain: set is_continuation=false, continuation_kind="{PageContinuationKind.NONE.value}", and leave all set_* null.
  - Exception for TABLE <-> TABLE candidates: If BOTH candidates are tables AND you have at least one STRONG continuation cue (per TABLE continuation signals below) AND there is NO visible new-table marker/caption/title at the boundary, you SHOULD set is_continuation=true, and continuation_kind="{PageContinuationKind.TABLE.value}".

## ALLOWED EDITS (METADATA ONLY)
1. set_prev_item_boundary: {ItemBoundary.TRUNCATED.value} or null
2. set_next_item_boundary: {ItemBoundary.RESUMED.value} or null
3. set_next_table_repeats_header:
  - If the NEXT candidate is not a table, set_next_table_repeats_header MUST be null.
  - Only set this when is_continuation=true AND continuation_kind="{PageContinuationKind.TABLE.value}" AND the NEXT candidate is a table AND it is the SAME table continuing.
  - Decide using IMAGE A and IMAGE B, not the excerpt fields.
  - Set to true if the header rows are visibly repeated on page N+1.
  - Set to false if the same table continues but headers are visibly NOT repeated.
  - If you cannot confidently tell, set it to null (do not guess). Null means “leave as-is/do not patch”.
  - If the JSON excerpt’s repeats_header already matches what you see in the images, leave it null.
  - IMPORTANT CONSISTENCY: If you set set_next_table_repeats_header to true/false (not null), then the next candidate table must be marked as continuing from previous. Therefore, if the next candidate item's boundary is not already "{ItemBoundary.RESUMED.value}" or "{ItemBoundary.BOTH.value}", you MUST also set set_next_item_boundary="{ItemBoundary.RESUMED.value}".

## DECISION GUIDANCE
1. Use the IMAGES as source of truth. Excerpts may be wrong/incomplete.
2. TABLE continuation signals:
  - Treat TABLE <-> TABLE as continuation ONLY when you have at least one STRONG continuation cue:
    - Header row repeats at the top of IMAGE B, OR
    - Column labels match exactly and grid/layout is clearly the same, OR
    - An explicit "(continued)" marker is visible.
    - The visual grid structure (column vertical alignment, relative widths, and justification) aligns exactly between the bottom of IMAGE A and top of IMAGE B, implying a headless seamless break.
  - Continuation DOES NOT require a row/cell to be cut off mid-text. A page break BETWEEN complete rows is still a continuation.
  - New table signals (Strong evidence to set is_continuation=false):
    - A caption/title indicates a DIFFERENT table identifier than the previous one (e.g., Table 4 -> Table 5), OR the caption content clearly describes a different table.
    - A clear change in column count/labels/layout.
    - A visible boundary marker that clearly ends the table (see markers below).
  - Same-table continuation signals (support is_continuation=true):
    - The SAME table identifier/title repeats at the top of IMAGE B (often with "(continued)") — this is still the SAME table.
    - A full-width title row appears that matches the prior table’s title/identifier (not a new table).
  - Explicit markers:
    - "End of table"/"End"/"Conclusion of table" --> strong signal of NO continuation.
    - "(continued)"/"continued on next page"/"continued from previous page" --> strong signal of continuation (SAME table).
  - If BOTH candidates are tables AND the column header schema appears the same (same labels/order/column count) AND the grid/layout is clearly the same, THEN set is_continuation=true, and continuation_kind="table", UNLESS you can see an explicit NEW-table marker at the boundary (different table caption/identifier, different header schema, or a clear “End of table”). NOTE: Content changes, numbering jumps, or starting a new subject block are NOT new-table markers.
  - IMPORTANT: Changes in row content/numbering are NORMAL within a long table and do NOT imply a new table.
3. TEXT continuation signals:
  - Only choose continuation_kind="{PageContinuationKind.TEXT.value}" when you can see strong truncation in IMAGE A and a clear resumption in IMAGE B.
  - Strong truncation cues include at least one of:
    - The last visible line ends with a dangling comma/semicolon/colon/ellipsis (",", ";", ":", "...")
    - A word is visibly cut with a hyphen/dash at the end of the line ("-", "–", "—")
    - Unmatched open bracket/paren/quote in the visible text near the end
    - A list item/numbering clearly continues (e.g., 1., 2., 3. or bullets) and IMAGE B continues the same list
    - The top of IMAGE B starts mid-sentence (lowercase continuation, no new heading/caption) and matches the bottom of IMAGE A
  - Hard negatives (DO NOT mark text continuation even if topic is related):
    - SECTION BOUNDARIES: If Page N ends a list (e.g., "List of Tables") and Page N+1 starts with a DIFFERENT structural section title (e.g., "Table of Contents", "List of Figures", "Index", "Bibliography"), this is a hard negative.
    - "Table N shows/illustrates/indicates ..." at end of page N followed by a new page starting with "Table N:" or a table/caption. This is a layout handoff to a table/caption, NOT a text continuation.
    - "Figure N shows ..." at end of page N followed by "Figure N:" or a figure/caption at top of page N+1.
    - If the bottom text clearly ends a complete sentence (ends with ".", "!" or "?"), do NOT mark as text continuation.
    - If the next candidate begins with a table/figure caption label ("Table", "Figure"), do NOT mark as text continuation.
    - If the previous list entries appear complete (each entry is self-contained and ends cleanly, often with a right-aligned page number) and IMAGE B begins a new section under a new heading, do NOT mark continuation even if both are lists.
4. FIGURE candidates: most figures do NOT continue across pages. Only mark continuation if the SAME figure is clearly cut off and resumes on the next page.
5. Excerpt metadata fields like boundary/repeats_header may be null/unreliable; do not treat null as evidence of "complete".

## CONTINUATION KIND RULES
1. Use continuation_kind="{PageContinuationKind.TABLE.value}" only for table continuations.
2. Use continuation_kind="{PageContinuationKind.TEXT.value}" only for text/list continuations.
3. Use continuation_kind="{PageContinuationKind.FIGURE.value}" only for figure/diagram continuations (same figure is cut off and resumes on next page).
4. If is_continuation=false, set continuation_kind="{PageContinuationKind.NONE.value}".
5. If is_continuation=true AND continuation_kind="{PageContinuationKind.TABLE.value}", set set_next_table_repeats_header to true/false ONLY when you can confidently see whether headers repeat; otherwise leave it null.
6. If is_continuation=true, the previous candidate should be compatible with continuing to next ("{ItemBoundary.TRUNCATED.value}" or "{ItemBoundary.BOTH.value}"), and the next candidate should be compatible with continuing from previous ("{ItemBoundary.RESUMED.value}" or "{ItemBoundary.BOTH.value}"). If incompatible, propose minimal boundary edits as allowed.
7. Candidate mismatch safety (this rule does NOT apply when BOTH candidates are tables): If the images suggest there might be continuation somewhere across the boundary, but it is NOT clearly between these two candidate items, then set is_continuation=false, continuation_kind="{PageContinuationKind.NONE.value}", and leave all set_* fields null.
8. When uncertain, follow the UNCERTAINTY POLICY above.

## PAIRWISE LIMITATION (CRITICAL, COMMON IN LONG TABLES)
1. You see the entirety page N but only the TOP of page N+1.
  - Therefore, DO NOT propose set_* boundaries of "{ItemBoundary.BOTH.value}" in this step.
  - Only propose set_prev_item_boundary="{ItemBoundary.TRUNCATED.value}" (or null) and set_next_item_boundary="{ItemBoundary.RESUMED.value}" (or null).
  - Note: the Python pipeline MAY end up with item.boundary="{ItemBoundary.BOTH.value}" if the item already had the opposite boundary (e.g., extractor marked "{ItemBoundary.RESUMED.value}" and verification adds "{ItemBoundary.TRUNCATED.value}"). That upgrade is handled in Python.
  - If a candidate boundary is already "{ItemBoundary.BOTH.value}", that is compatible with continuation; do not change it.

## CONFIDENCE CALIBRATION RULES
1. Use confidence >= 0.75 only when you are confident in your decision and rationale.
2. Use 0.50–0.74 for plausible but not definitive conclusions.
3. Use <= 0.49 when uncertain in your decision and human review is strongly recommended.
            """
    )

    user_message = json.dumps(
        {
            "prev_page_index": prev_page_index,
            "next_page_index": next_page_index,
            "prev_candidate_item": prev_item,
            "next_candidate_item": next_item,
        },
        ensure_ascii=False,
        indent=2,
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
