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


def validate_page_ir_continuity_verdict(
    *,
    min_confidence_to_patch: float,
    min_confidence_to_select_positive: float,
    min_confidence_to_stop_negative_search: float,
    next_item_excerpt: dict[str, Any],
    next_page_index: int,
    prev_item_excerpt: dict[str, Any],
    prev_page_index: int,
    verdict_json: str,
) -> PromptPair:
    """Generate prompts for the validation agent that checks a continuity verdict.

    Parameters
    ----------
    min_confidence_to_patch
        Positive verdicts at or above this threshold may be patched into PageIR state.
    min_confidence_to_select_positive
        Positive verdicts at or above this threshold may outrank negatives during
        candidate-pair selection.
    min_confidence_to_stop_negative_search
        Same-family primary-primary negative verdicts at or above this threshold may
        stop alternate candidate-pair search.
    next_item_excerpt
        The excerpt JSON of the candidate item near top of page N+1.
    next_page_index
        The 0-based page index of the next page (N+1).
    prev_item_excerpt
        The excerpt JSON of the candidate item near bottom of page N.
    prev_page_index
        The 0-based page index of the previous page (N).
    verdict_json
        The JSON string of the PageIRContinuityVerdict to validate.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        f"""You are a strict PageIR continuity validation agent (CHECKER MODE).

You will be given:
1. IMAGE A: Entire page N
2. IMAGE B: A top-crop of page N+1
3. Candidate excerpt JSON for the bottom-anchor item on page N
4. Candidate excerpt JSON for the top-anchor item on page N+1
5. A `PageIRContinuityVerdict` produced by the verification agent

## TASK
Evaluate whether the verification agent's verdict is CORRECT by cross-checking it
against the source images and candidate excerpts. Return a `ContinuityValidationVerdict`.

## OUTPUT FIELDS
1. passed: true if the verdict is accurate; false if corrections are needed.
2. issues: list of ContinuityValidationIssue objects describing any problems found.
3. rationale: string (>= 50 chars) explaining the overall assessment.
4. corrected_verdict: a corrected PageIRContinuityVerdict (required when passed=false; null when passed=true).

## WHAT TO CHECK

### 1. Is the continuation decision correct?
- Use the IMAGES as source of truth. Do NOT trust metadata fields in the excerpt JSON.
- Does `is_continuation` accurately reflect whether the two candidate items continue across the page break?

### 2. Is the continuation_kind correct?
- If is_continuation=true, does continuation_kind match what you see? (table/text/figure)

### 3. Schema invariants (must hold):
- is_continuation=false -> continuation_kind="{PageContinuationKind.NONE.value}", set_next_table_repeats_header=null.
- is_continuation=true -> continuation_kind!="{PageContinuationKind.NONE.value}", confidence >= 0.50.
- continuation_kind!="{PageContinuationKind.TABLE.value}" -> set_next_table_repeats_header=null.

### 4. Is the confidence calibrated?
- Does the evidence support the stated confidence level?
- >= 0.50: clear or plausible visual evidence supports the decision.
- <= 0.49: uncertain -> MUST have is_continuation=false.
- Positive verdicts with confidence >= {min_confidence_to_patch:.2f} may be patched into PageIR state, so reserve that range for decisions with genuinely strong visual support.
- Positive verdicts with confidence >= {min_confidence_to_select_positive:.2f} may outrank negatives during candidate-pair selection.
- A same-family primary-primary negative verdict with confidence >= {min_confidence_to_stop_negative_search:.2f} may stop alternate candidate-pair search, so reserve that range for negatives with genuinely strong visual evidence.

### 5. Table-only patch: set_next_table_repeats_header
- Only applies for table continuations.
- true = headers visibly repeated at top of IMAGE B; false = repeated table header visibly not repeated; null = uncertain.
- Judge true/false from the image only. Do NOT require the excerpt JSON to agree on header_row_count; extraction may miss repeated header rows or still count header-like/section rows.

### 6. Is the rationale adequate?
- >= 50 chars, references specific visual evidence.

## DECISION GUIDANCE (same rules as verification)

### A. VISUAL BORDERS — CRITICAL RULE
Visual borders (closed boxes, ruled edges) are NEVER evidence of table discontinuity.
Judge continuity exclusively by content, not by visual framing.

### B. TABLE continuation (SAME table)
Strong positive cues: matching column structure, continuing row sequence, repeated headers.
Things that do NOT indicate a new table: row numbering restarts, topic shifts, checkpoint rows, fully boxed fragments, language switches between rows.
A table has truly ENDED only with: a new table title/caption, a structurally different header row, or an explicit concluding row followed by non-tabular content.

### C. TEXT continuation
Only with strong visual evidence: truncated bottom of IMAGE A and resumed top of IMAGE B (hyphenated word, dangling punctuation, mid-sentence start).

### D. FIGURE continuation (rare)
Only if the SAME figure is clearly cut off and resumes.

## SEVERITY GUIDE
- error: wrong is_continuation, wrong continuation_kind, schema invariant violation, wrong set_next_table_repeats_header when clearly determinable.
- warning: slightly miscalibrated confidence, weak rationale, borderline decisions.

## RULES FOR corrected_verdict
When passed=false, you MUST provide a corrected_verdict that:
1. Fixes all error-severity issues.
2. Satisfies all schema invariants.
3. Has rationale >= 50 chars referencing visual evidence.
4. Does NOT invent content or merge items across pages.
        """
    )

    user_message = json.dumps(
        {
            "prev_page_index": prev_page_index,
            "next_page_index": next_page_index,
            "thresholds": {
                "min_confidence_to_patch": min_confidence_to_patch,
                "min_confidence_to_select_positive": min_confidence_to_select_positive,
                "min_confidence_to_stop_negative_search": min_confidence_to_stop_negative_search,
            },
            "prev_candidate_item": prev_item_excerpt,
            "next_candidate_item": next_item_excerpt,
            "verification_verdict": json.loads(verdict_json),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def verify_page_ir_pairs_from_extraction(
    *,
    min_confidence_to_patch: float,
    min_confidence_to_select_positive: float,
    min_confidence_to_stop_negative_search: float,
    next_item: dict[str, Any],
    next_page_index: int,
    prev_item: dict[str, Any],
    prev_page_index: int,
) -> PromptPair:
    """Generate the prompts for verifying PageIR pairs from the extraction step.

    Parameters
    ----------
    min_confidence_to_patch
        Positive verdicts at or above this threshold may be patched into PageIR state.
    min_confidence_to_select_positive
        Positive verdicts at or above this threshold may outrank negatives during
        candidate-pair selection.
    min_confidence_to_stop_negative_search
        Same-family primary-primary negative verdicts at or above this threshold may
        stop alternate candidate-pair search.
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
- false = same table continues but the repeated table header is visibly NOT repeated
- null  = cannot confidently tell (do not guess)
- Judge true/false from the image only. Do NOT require the excerpt JSON to agree on header_row_count; extraction may miss repeated header rows or still count header-like/section rows.

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
- A header-like or merged row at the top of IMAGE B does not by itself mean the repeated table header is present. Distinguish repeated column headers from section/checkpoint rows.
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
- Positive verdicts with confidence >= {min_confidence_to_patch:.2f} may be patched into PageIR state, so reserve that range for decisions with genuinely strong visual support.
- Positive verdicts with confidence >= {min_confidence_to_select_positive:.2f} may outrank negatives during candidate-pair selection.
- A same-family primary-primary negative verdict with confidence >= {min_confidence_to_stop_negative_search:.2f} may stop alternate candidate-pair search, so reserve that range for negatives with genuinely strong visual evidence.
        """
    )

    user_message = json.dumps(
        {
            "prev_page_index": prev_page_index,
            "next_page_index": next_page_index,
            "thresholds": {
                "min_confidence_to_patch": min_confidence_to_patch,
                "min_confidence_to_select_positive": min_confidence_to_select_positive,
                "min_confidence_to_stop_negative_search": min_confidence_to_stop_negative_search,
            },
            "prev_candidate_item": prev_item,
            "next_candidate_item": next_item,
        },
        ensure_ascii=False,
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
