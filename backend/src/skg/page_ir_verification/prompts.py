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


def double_check_page_ir_verification() -> DotMap:
    """Generate the prompts for double-checking page IR verification results.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = None
    user_message = dedent(
        f"""Re-check your most recent `PageIRContinuityVerdict` against the evidence.

Checklist (must satisfy ALL):
1. Use the IMAGES as source of truth; the candidate JSON excerpts are only for locating regions.
2. Decide ONLY whether the *two candidate items* continue across the page break (N -> N+1).

Schema invariants (must hold):
1. If `is_continuation` is false:
  - `continuation_kind` MUST be "{PageContinuationKind.NONE.value}"
  - `set_next_table_repeats_header` MUST be null
2. If `is_continuation` is true:
  - `continuation_kind` MUST NOT be "{PageContinuationKind.NONE.value}"
  - `confidence` MUST be >= 0.50
3. If `continuation_kind` is not "{PageContinuationKind.TABLE.value}":
  - `set_next_table_repeats_header` MUST be null

Table-only patch rule:
1. Only set `set_next_table_repeats_header` when you are confident it is the SAME table continuing.
2. Set true only if header rows are visibly repeated at the top of IMAGE B; false only if visibly not repeated; otherwise null.

If anything above is violated or your reasoning is weak, correct it now and return a complete `PageIRContinuityVerdict` (rationale >= 50 chars). Return ONLY the object.
        """
    )

    return DotMap(
        {"system_message": system_message, "user_message": user_message.strip()}
    )


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
1. IMAGE A: Entire page N
2. IMAGE B: A top-crop of page N+1 (may extend far enough to include the chosen candidate region)
3. Candidate excerpt JSON for the bottom-anchor item on page N
4. Candidate excerpt JSON for the top-anchor item on page N+1

IMPORTANT:
1. The excerpt JSON is ONLY to help you locate the candidates. It may be incomplete or inaccurate.
2. Do NOT trust continuity metadata fields (e.g., boundary/repeats_header) inside the excerpts.
3. Use the IMAGES as the primary source of truth.

## TASK
Decide whether THESE TWO CANDIDATE ITEMS are a continuation across the page boundary (N -> N+1).
Return ONLY a `PageIRContinuityVerdict` object that matches the schema (no extra keys, no prose).

## OUTPUT FIELDS (must be present)
1. is_continuation: true/false
2. continuation_kind: "{PageContinuationKind.NONE.value}" | "{PageContinuationKind.TEXT.value}" | "{PageContinuationKind.TABLE.value}" | "{PageContinuationKind.FIGURE.value}"
3. set_next_table_repeats_header: true | false | null
4. confidence: 0.0–1.0
5. rationale: string (>= 50 chars)

## SCHEMA INVARIANTS (must hold)
1. If is_continuation=false:
  - continuation_kind MUST be "{PageContinuationKind.NONE.value}"
  - set_next_table_repeats_header MUST be null
2. If is_continuation=true:
  - continuation_kind MUST NOT be "{PageContinuationKind.NONE.value}"
  - confidence MUST be >= 0.50
3. If continuation_kind != "{PageContinuationKind.TABLE.value}":
  - set_next_table_repeats_header MUST be null

## HARD RULES
1. Decide ONLY about continuity between the two provided candidate items (not whole-page continuity).
2. Do NOT rewrite, merge, move, or complete content across pages.
3. Do NOT invent missing text/cells.
4. The ONLY allowed “edit” suggestion is set_next_table_repeats_header (table continuations only). No other edits.

## TABLE-ONLY PATCH: set_next_table_repeats_header
1. Only consider this when:
  - is_continuation=true AND continuation_kind="{PageContinuationKind.TABLE.value}"
  - The page N+1 candidate is a table
  - You are confident it is the SAME table continuing

Then:
- true  = header rows are visibly repeated at the top of IMAGE B
- false = same table continues but headers are visibly NOT repeated
- null  = you cannot confidently tell (do not guess)

## DECISION GUIDANCE (use the IMAGES)
A. TABLE continuation cues (SAME table):
  - Identical column structure (count, widths, gridlines) continues across the break, AND
  - No visible “new table” title/numbering marker, AND/OR
  - Header repeats (common), OR
  - Row content clearly continues (e.g., same section/week sequence continues)

Notes:
- A page break between complete rows can still be a continuation.
- Minor content shifts do not imply a new table if structure is the same.
- A fully boxed table on BOTH pages can still be the SAME logical table (common in scope-and-sequence curricula).
  Treat as continuation when column structure is identical AND rows clearly continue an ordered sequence,
  e.g., "Semaine 3" -> "Semaine 4", "Palier" checkpoints continuing, repeated week numbering, or the same section label with uninterrupted sequencing,
  UNLESS a new section heading/caption explicitly indicates a new table.

B. TABLE non-continuation cues (NEW table):
  - A new table title/number (e.g., "Tableau 3", "Table 4") or a clearly different caption, OR
  - Clear change in column layout/labels/structure, OR
  - Explicit "end of table"/conclusion marker.

C. TEXT continuation cues (use "{PageContinuationKind.TEXT.value}" only with strong evidence):
  - Bottom of IMAGE A is visibly truncated and top of IMAGE B visibly resumes the same sentence/list:
    - hyphenated cut word, dangling punctuation, unmatched quote/paren/bracket,
    - list numbering/bullets clearly continue,
    - top begins mid-sentence matching the bottom.

Hard negatives:
- Bottom ends as a complete thought and next begins a new heading/section/table/figure.

D. FIGURE continuation (rare):
  - Only true if the SAME figure/diagram is clearly cut off on IMAGE A and resumes on IMAGE B.

E. BILINGUAL / MULTILINGUAL DOCUMENTS:
  - Documents may contain parallel text in two languages (e.g., rows alternating between languages, or bilingual headers).
  - A language switch between rows does NOT indicate a new table—it is normal bilingual formatting.
  - Unfamiliar scripts or languages are NOT evidence of truncation or corruption.

## UNCERTAINTY POLICY
1. Default when uncertain: is_continuation=false, continuation_kind="{PageContinuationKind.NONE.value}", set_next_table_repeats_header=null.
2. Exception for TABLE<->TABLE candidates:
  If BOTH candidates are tables, and you see at least one strong SAME-table cue, and there is NO visible new-table marker,
  you SHOULD set is_continuation=true and continuation_kind="{PageContinuationKind.TABLE.value}".

## CONFIDENCE CALIBRATION
- >= 0.75: clear visual evidence supports your decision.
- 0.50–0.74: plausible but not definitive (still allowed for true/false).
- <= 0.49: uncertain → MUST choose is_continuation=false (per policy above).
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
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
