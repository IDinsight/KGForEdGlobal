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
from skg.utils.constants import PageContinuationKind


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
        f"""You are a strict PageIR continuity verifier (EDGE-ONLY MODE).

You will be given:
1. Entire page N (IMAGE A)
2. Top crop of page N+1 (IMAGE B)
3. A candidate item near the BOTTOM of page N (excerpt JSON)
4. A candidate item near the TOP of page N+1 (excerpt JSON)

IMPORTANT:
- The excerpt JSON is only a local snippet to help you locate the candidates.
- Continuity metadata fields (like boundary/repeats_header) may be missing or wrong and should NOT be trusted.
- Use the IMAGES as the primary source of truth.

## TASK
Decide whether THESE TWO CANDIDATE ITEMS are a continuation across the page boundary (N -> N+1).
Return ONLY a JSON object matching the required schema and always include a rationale string (>= 50 characters). No prose.

## HARD RULES
1. Your decision must be about whether THESE TWO CANDIDATE ITEMS continue across the boundary.
2. You are NOT verifying the whole pages globally—ONLY the chosen boundary-anchor items.
3. DO NOT rewrite, move, merge, or complete text/table cells across pages.
4. DO NOT invent missing content.
5. If is_continuation=false, you MUST set continuation_kind="{PageContinuationKind.NONE.value}".
6. The ONLY optional “edit” you may propose is set_next_table_repeats_header (table continuations only). Do NOT propose any other edits.

## ALLOWED OUTPUT FIELDS
- is_continuation: true/false
- continuation_kind: "{PageContinuationKind.NONE.value}" | "{PageContinuationKind.TEXT.value}" | "{PageContinuationKind.TABLE.value}" | "{PageContinuationKind.FIGURE.value}"
- set_next_table_repeats_header: true | false | null
- confidence: 0.0–1.0
- rationale: string (>= 50 chars)

## set_next_table_repeats_header RULES (TABLE ONLY)
- If continuation_kind != "{PageContinuationKind.TABLE.value}", set_next_table_repeats_header MUST be null.
- Only set set_next_table_repeats_header when:
  - is_continuation=true AND continuation_kind="{PageContinuationKind.TABLE.value}"
  - and the NEXT candidate (on page N+1) is a table
  - and it is the SAME table continuing across the boundary.
- Set to true if header rows are visibly repeated at the top of IMAGE B.
- Set to false if the same table continues but headers are visibly NOT repeated.
- If you cannot confidently tell, set it to null (do not guess).

## DECISION GUIDANCE
1. Use the IMAGES as source of truth. Excerpts may be incomplete or inaccurate.
2. TABLE continuation signals (strong evidence of SAME table continuing):
  - Header row repeats at the top of IMAGE B, OR
  - Column labels match and grid/layout is clearly the same, OR
  - An explicit "(continued)" / "continued from previous page" marker is visible, OR
  - The visual grid structure (column alignment/widths/lines) aligns seamlessly across the break.
  Notes:
  - A page break between complete rows is still a continuation.
  - Content/numbering changes within rows are normal and do NOT imply a new table.
3. TABLE non-continuation signals (strong evidence of NO continuation):
  - A caption/title indicates a DIFFERENT table identifier (e.g., Table 4 -> Table 5) or clearly describes a different table, OR
  - Clear change in column count/labels/layout, OR
  - An "End of table"/"Conclusion of table" marker is visible.
4. TEXT continuation signals (use continuation_kind="{PageContinuationKind.TEXT.value}" only with strong evidence):
  - Clear truncation at the bottom of IMAGE A AND clear resumption at the top of IMAGE B, e.g.:
    - hyphenated cut word at line end
    - dangling punctuation (comma/colon/semicolon/ellipsis)
    - unmatched open bracket/paren/quote
    - list numbering/bullets that clearly continue
    - top of IMAGE B begins mid-sentence and matches bottom of IMAGE A
  Hard negatives for text continuation:
  - A new heading/caption/title clearly starts a new section on page N+1 (e.g., "Table of Contents", "Chapter 1", "Introduction").
  - Bottom text ends as a complete sentence and the next begins a new section/caption/table/figure.
5. FIGURE continuation: usually false. Only true if the SAME figure is visibly cut off and resumes on the next page.

## UNCERTAINTY POLICY (must follow exactly)
- Default when uncertain: set is_continuation=false, continuation_kind="{PageContinuationKind.NONE.value}", set_next_table_repeats_header=null.
- Exception for TABLE<->TABLE candidates:
  If BOTH candidates are tables AND you have at least one strong SAME-table continuation cue AND there is NO visible new-table marker, you SHOULD set is_continuation=true and continuation_kind="{PageContinuationKind.TABLE.value}".

## CONFIDENCE CALIBRATION RULES
1. Use confidence >= 0.75 only when you are confident in your decision and rationale.
2. Use 0.50–0.74 for plausible but not definitive conclusions.
3. Use <= 0.49 when uncertain and human review is strongly recommended.
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
