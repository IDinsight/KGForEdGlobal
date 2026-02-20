"""This module contains prompt templates for **verifying** Intermediate Representation
(IR) information from PDF pages.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Any

# Package Library
from skg.utils.constants import PageContinuationKind
from skg.utils.general import PromptPair


def double_check_page_ir_verification() -> PromptPair:
    """Generate the prompts for double-checking page IR verification results.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
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

Border reminders:
1. Do NOT treat a reset in row numbering/labels (e.g., "1" restarting after "27") as proof of a NEW table.
  Many curricula restart sequences inside the SAME logical table (new unit/term/level/checkpoint) while keeping the schema identical.
2. Large content/topic shifts are also NOT proof of a new table if the column structure is unchanged.
3. A "checkpoint" row (e.g., a merged row naming a unit/term/assessment/checkpoint) can appear INSIDE the table grid.
  If the grid continues below/after it, the table may still be continuing across the page break.
  (closed boxes, ruled edges) do NOT indicate a table has ended. Judge table continuity by content: same column structure, continuing row sequence, and absence of a new table title or restructured header row.
4. A language switch between rows (e.g., Wolof → French, or any other bilingual alternation) is normal bilingual formatting and is NOT evidence of a new table or text discontinuity.

If anything above is violated or your reasoning is weak, correct it now and return a complete `PageIRContinuityVerdict` (rationale >= 50 chars). Return ONLY the object.
        """
    )

    return PromptPair(system_message=system_message, user_message=user_message.strip())


def verify_page_ir_pairs_from_extraction(
    *,
    next_item: dict[str, Any],
    next_page_index: int,
    prev_item: dict[str, Any],
    prev_page_index: int,
) -> PromptPair:
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
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
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
- STRONG cue (template match): the vertical gridlines/column boundaries align and the number of columns is the same.
  Treat this as strong evidence of SAME-table continuity even if the row labels restart or the topic changes.
- A checkpoint/section row inside the table (often a merged row spanning many columns) does NOT end the table by itself.
  If the table grid continues and the next page shows the same schema, prefer continuation.
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

CRITICAL — Visual borders are NOT evidence of table discontinuity:
- Do NOT treat distinct cell borders, box outlines, or ruled edges as signals that a table has ended or that a new table has begun.
- Tables in many document traditions draw full closed borders around EVERY page's portion of the same logical table.
  Two fully bordered table fragments on consecutive pages are often the SAME table.
- Instead, determine continuity by content: does the row/column structure remain identical, and does the row sequence logically continue (e.g., "Semaine 5" → "Semaine 6")?
- A table has truly ENDED only when there is concrete content evidence: a new table title/number, a structurally different header row starting a new table, an explicit concluding row (e.g., "EVALUATIONS FINALES"), or a shift to non-tabular content.

B. TABLE non-continuation cues (NEW table):
- IMPORTANT: The following are NOT sufficient by themselves to declare a new table:
  - Row numbering/labels restart (e.g., "Week 27" then "Week 1")
  - Topic/skill area changes sharply
  - The table is fully boxed on both pages
  These are common in scope-and-sequence documents that reuse a single table schema across sections/terms.
  - A new table title/number (e.g., "Tableau 3", "Table 4") or a clearly different caption, OR
  - Clear change in column layout/labels/structure, OR
  - Explicit "end of table"/conclusion marker.
  Note: Closed visual borders alone are NOT a non-continuation cue. You must see content-level evidence (new title, different column structure, or a header row that redefines the schema) to conclude a new table has begun.

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
2. TABLE<->TABLE candidate guidance (this is NOT an exception to rule 1; it clarifies when you are NOT uncertain):
  When BOTH candidates are tables and the column/grid schema matches visually (same number of columns; aligned vertical gridlines)
  and there is NO explicit new-table marker (no new caption/title/number, no restructured header row), this constitutes moderate
  positive evidence of continuation — you are not uncertain. In this case:
  - Set is_continuation=true, continuation_kind="{PageContinuationKind.TABLE.value}", with confidence ~0.60–0.80,
    even if row labels restart or content/topic shifts.
  - You SHOULD choose is_continuation=false ONLY when you see explicit new-table evidence (new caption/title/number) OR a clear
    column schema change. Row restarts, topic shifts, or closed visual borders alone are NOT such evidence.

## CONFIDENCE CALIBRATION
- >= 0.5: clear or plausible visual evidence supports your decision.
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

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
