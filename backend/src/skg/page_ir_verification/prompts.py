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
1. Use the IMAGES as source of truth; candidate JSON excerpts are only for locating regions.
2. Decide ONLY whether the *two candidate items* continue across the page break (N -> N+1).

Schema invariants (must hold):
1. is_continuation=false -> continuation_kind="{PageContinuationKind.NONE.value}", set_next_table_repeats_header=null.
2. is_continuation=true -> continuation_kind!="{PageContinuationKind.NONE.value}", confidence >= 0.50.
3. continuation_kind!="{PageContinuationKind.TABLE.value}" -> set_next_table_repeats_header=null.

Table-only patch rule:
- Only set set_next_table_repeats_header when you are confident it is the SAME table continuing.
- true = headers visibly repeated at top of IMAGE B; false = visibly not repeated; null = uncertain.

Border & continuity reminders:
- Visual borders (closed boxes, ruled edges) are NEVER evidence that a table has ended or begun.
- Row numbering restarts, topic shifts, and checkpoint rows inside a table grid do NOT end the table.
- A language switch between rows (e.g., Wolof -> French) is normal bilingual formatting, not a discontinuity.
- Judge table continuity by content: same column structure + continuing row sequence = same table.

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
- The excerpt JSON is ONLY to help you locate the candidates. It may be incomplete or inaccurate.
- Do NOT trust continuity metadata fields (e.g., boundary/repeats_header) inside the excerpts.
- Use the IMAGES as the primary source of truth.

## TASK
Decide whether THESE TWO CANDIDATE ITEMS are a continuation across the page boundary (N -> N+1).
Return ONLY a `PageIRContinuityVerdict` object (no extra keys, no prose).

## OUTPUT FIELDS
1. is_continuation: true/false
2. continuation_kind: "{PageContinuationKind.NONE.value}" | "{PageContinuationKind.TEXT.value}" | "{PageContinuationKind.TABLE.value}" | "{PageContinuationKind.FIGURE.value}"
3. set_next_table_repeats_header: true | false | null
4. confidence: 0.0–1.0
5. rationale: string (>= 50 chars)

## SCHEMA INVARIANTS
1. is_continuation=false -> continuation_kind="{PageContinuationKind.NONE.value}", set_next_table_repeats_header=null.
2. is_continuation=true -> continuation_kind!="{PageContinuationKind.NONE.value}", confidence >= 0.50.
3. continuation_kind!="{PageContinuationKind.TABLE.value}" -> set_next_table_repeats_header=null.

## HARD RULES
1. Decide ONLY about continuity between the two provided candidate items (not whole-page continuity).
2. Do NOT rewrite, merge, move, or complete content across pages.
3. Do NOT invent missing text/cells.
4. The ONLY allowed "edit" suggestion is set_next_table_repeats_header (table continuations only).

## TABLE-ONLY PATCH: set_next_table_repeats_header
Only applies when is_continuation=true AND continuation_kind="{PageContinuationKind.TABLE.value}" AND next candidate is a table you are confident is the SAME table:
- true  = header rows are visibly repeated at the top of IMAGE B
- false = same table continues but headers are visibly NOT repeated
- null  = cannot confidently tell (do not guess)

## DECISION GUIDANCE

### A. VISUAL BORDERS — CRITICAL RULE
Visual borders (closed boxes, ruled edges, cell outlines) are NEVER evidence of table discontinuity.
Many document traditions draw full closed borders around EVERY page's portion of the same logical table.
Judge continuity exclusively by content, not by visual framing.

### B. TABLE continuation (SAME table)
Strong positive cues:
- Column structure matches: same number of columns, aligned vertical gridlines/widths.
- Row sequence logically continues (e.g., "Semaine 3" -> "Semaine 4", week numbering, ordered sections).
- Header rows repeat (common in scope-and-sequence curricula).

Things that do NOT indicate a new table:
- Row numbering/labels restart (e.g., "Week 27" then "Week 1") — common when sections/terms change.
- Topic or skill-area shifts — common inside single scope-and-sequence tables.
- Checkpoint/section rows (merged rows naming a unit/term/assessment) appearing inside the grid.
- Fully boxed table fragments on consecutive pages (see rule A above).
- Language switches between rows (e.g., Wolof -> French) — normal bilingual formatting.

A table has truly ENDED only when you see explicit content evidence:
- A new table title/number/caption (e.g., "Tableau 3", "Table 4"), OR
- A structurally different header row redefining the column schema, OR
- An explicit concluding row (e.g., "EVALUATIONS FINALES") followed by non-tabular content.

### C. TEXT continuation
Use "{PageContinuationKind.TEXT.value}" only with strong visual evidence:
- Bottom of IMAGE A is visibly truncated and top of IMAGE B resumes the same sentence/list: hyphenated cut word, dangling punctuation, unmatched quote/paren/bracket, list numbering/bullets clearly continue, or top begins mid-sentence.
- Hard negative: bottom ends as a complete thought and next begins a new heading/section/table/figure.

### D. FIGURE continuation (rare)
Only true if the SAME figure/diagram is clearly cut off on IMAGE A and resumes on IMAGE B.

## UNCERTAINTY POLICY
- Default when uncertain: is_continuation=false, continuation_kind="{PageContinuationKind.NONE.value}", set_next_table_repeats_header=null.
- Exception — TABLE<->TABLE with matching schema: when BOTH candidates are tables and column structure matches visually (same column count + aligned gridlines) with NO new-table marker (no new caption/title, no restructured header row), you are NOT uncertain. Set is_continuation=true, continuation_kind="{PageContinuationKind.TABLE.value}", confidence ~0.60–0.80. Choose is_continuation=false ONLY with explicit new-table evidence (new caption/title/number OR clear column schema change).

## CONFIDENCE CALIBRATION
- >= 0.50: clear or plausible visual evidence supports your decision.
- <= 0.49: uncertain -> MUST choose is_continuation=false.
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
