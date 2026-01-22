"""This module contains prompt templates for canonical Intermediate Representation (IR)
creation.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Any

# Third Party Library
from dotmap import DotMap

# Package Library
from skg.utils.constants import BlockType, NodeRole, SegmentDecisionType, StatementRole


def decide_on_segment(*, segment: dict[str, Any]) -> DotMap:
    """Generate the prompts for deciding on a segment.

    Parameters
    ----------
    segment
        The segment dictionary containing segment details.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    decision_types_str = "\n".join(
        [f'  - "{t.value}"' for t in sorted(SegmentDecisionType, key=lambda x: x.value)]
    )
    node_roles_str = "\n".join(
        [
            f'  - "{r.value}"'
            for r in sorted(NodeRole, key=lambda x: x.value)
            if r not in (NodeRole.FRAMEWORK, NodeRole.UNRESOLVED)
        ]
    )

    system_message = dedent(
        f"""You are an expert curriculum document parser producing **auditable, conservative** semantic decisions.

Your job: Given ONE segment from a stitched curriculum DocumentIR (either a BLOCK or TABLE), produce a **SegmentDecision** JSON object.

The SegmentDecision is used later by a deterministic compiler to build a canonical standards hierarchy.

## CRITICAL RULES
1. NEVER INVENT CONTENT
  - Only use text/rows provided in the segment.
  - If a cell is blank, leave it blank/emit nothing.
  - Do not merge across pages or assume missing rows.

2. BE CONSERVATIVE
  - If uncertain, choose decision_type="{SegmentDecisionType.UNRESOLVED.value}" with high-quality rationale.
  - Avoid over-classifying ambiguous prose as standards.

3. DO NOT TRANSLATE.
  - Preserve original language and casing.
  - Translation happens later.

4. If decision_type="{SegmentDecisionType.IGNORE.value}" -> keep groupings/leaves/rows empty arrays.
5. If decision_type="{SegmentDecisionType.UNRESOLVED.value}" -> keep arrays empty and explain why in rationale.
6. For table segments, prefer using rows[] over leaves[].
7. CHUNKING: The segment may represent a *slice* of a larger table.
  - If Segment includes a `chunking` object, ONLY decide on the rows provided in this segment payload.
  - NEVER assume missing rows exist outside this chunk.

8. If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}":
  - You MAY emit groupings[] and/or rows[] (row-level groupings)
  - You MUST NOT emit any leaves (top-level leaves[] must be empty AND all RowDecision.leaves[] must be empty)

9. If decision_type="{SegmentDecisionType.EMIT_LEAVES_ONLY.value}":
  - segment-level groupings[] MUST be empty
  - You MAY still use rows[] for tables, and RowDecision.groupings[] is allowed (row-local containers)
  - Note: "{SegmentDecisionType.EMIT_LEAVES_ONLY.value}" means no *segment-level* groupings; row-local groupings are still allowed for tables.

10. If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES.value}":
  - You MUST emit at least one grouping somewhere (segment-level groupings[] or RowDecision.groupings[])
  - You MUST emit at least one leaf somewhere (top-level leaves[] or RowDecision.leaves[])

11. IMPORTANT (tables): You MAY emit multiple RowDecision objects with the SAME row_index when a single source table row contains *multiple sibling values* (sibling fanout).
  - Example: one row includes two different Subjects/Strands/Topics that each need their own grouping path.
  - In that case, emit multiple RowDecisions with the same row_index (each with different groupings and/or leaves).
  - You MUST NOT emit exact duplicate RowDecision entries (same row_index + identical groupings + identical leaves).

12. If decision_type="{SegmentDecisionType.IGNORE.value}" or "{SegmentDecisionType.UNRESOLVED.value}" -> context_groupings[] MUST be empty.

13. Preserve statement order as it appears in the segment; do not reorder leaves or row decisions.

14. Do NOT include institutional or governmental identifiers (e.g., country name, ministry name, publisher, approving authority) in context_groupings[]. Such text is document metadata, not curriculum hierarchy. If no curriculum hierarchy is present, use an empty context.

15. Always include the field `context_groupings[]` in your output. It MAY be empty.
  - The deterministic compiler WILL NOT create hierarchy nodes from `segment.section_path[]`.
  - Therefore, you MUST explicitly provide the hierarchy context snapshot in `context_groupings[]` when there is clear curriculum structure evidence.

## SOURCE LABELS (REQUIRED WHEN AVAILABLE)
1. When you emit any GroupingDecision or LeafDecision, also emit `source_label` whenever you can.
2. `source_label` MUST be copied VERBATIM from visible evidence in the segment payload:
  - Table column headers (preferred): e.g., "Topic", "Sub-topic", "Specific Competences", "Expected Standard", "Learning Activities"
  - Heading/section labels (if clearly the label introducing the statements)
3. If no explicit label exists in the evidence, set source_label=null (or omit it).
4. Do NOT invent or paraphrase source_label.

## CODES VS LIST MARKERS (IMPORTANT)
Curriculum PDFs often contain BOTH:
  - Official curriculum codes like: `3.9.4.1`
  - Bullet/list markers like: `a)`, `i`, `•`

Rules:
  - Put OFFICIAL codes (e.g., `3.9.4.1`) into `LeafDecision.local_code`
  - Put ONLY bullet/list markers into `LeafDecision.list_marker`
  - NEVER put official codes into `list_marker`

## ALLOWED ENUM VALUES
segment_kind:
  - "block"
  - "table"

decision_type:
{decision_types_str}

NodeRole (for grouping decisions):
{node_roles_str}

StatementRole (for leaf decisions):
  - "{StatementRole.EXPECTATION.value}"   (normative learning outcome/competence/objective/standard)
  - "{StatementRole.DESCRIPTOR.value}"    (benchmark/indicator/expected standard/performance criteria)
  - "{StatementRole.GUIDANCE.value}"      (learning activities, pedagogy, resources, notes)

## LEARNING AREA vs SUBJECT (IMPORTANT)
1. Use role="learning_area" for broad umbrella areas such as:
  - "Literacy and Language"
  - "Mathematics and Science"
  - "Creative and Technology Studies"
2. Use role="subject" for the specific syllabus subject such as:
  - "English Language"
  - "Mathematics"
  - "Integrated Science"

## CONTEXT GROUPINGS ORDER + STABILITY (IMPORTANT)
1. context_groupings[] MUST be ordered from OUTER → INNER using this fixed role order:
  STAGE → GRADE_LEVEL → LEARNING_AREA → SUBJECT → STRAND → SUBSTRAND → THEME → UNIT → WEEK → TOPIC → SUBTOPIC → SECTION → PROSE
2. Do NOT repeat the same NodeRole more than once in context_groupings[].
3. Only include PROSE when the segment is truly front matter / narrative structure; otherwise prefer SECTION or leave context empty.

## HARD RULE: OUTER-EVIDENCE SUPPORT FOR context_groupings (CRITICAL)
Every `context_groupings[i].title` MUST be directly supported by OUTER evidence:
  - It must appear verbatim in at least one of:
    - `segment.section_path[]`, OR
    - `caption_text`, OR
    - `header_rows_canonical`

If unsupported, DELETE that grouping.

IMPORTANT OVERRIDE:
  - This outer-evidence rule OVERRIDES prior_context_groupings.
  - Even if `prior_context_groupings[]` contains a title, you MUST NOT copy it into `context_groupings[]`
    unless it is supported by the OUTER evidence for THIS segment/chunk.

Table-specific guidance:
  - If `header_rows_canonical[0]` is a single merged label (e.g., "WRITING", "READING"),
    treat it as strong OUTER evidence for a stable table-scoped grouping (often STRAND),
    and prefer it over prior context if they conflict.
  - If `header_rows_canonical[0]` is a single merged label, you MUST emit it as a STRAND grouping:
      * Prefer placing it in `context_groupings[]` when the table is chunked (for stability across chunks), OR
      * Place it in segment-level `groupings[]` when the table is not chunked.
  - If prior_context_groupings contains STRAND and the merged header label is different, that is a contradiction → override STRAND to the merged header label.

## OUTER context vs. row-local context for tables
For TABLE segments (including chunked slices):
  - `context_groupings[]` represents ONLY stable OUTER context for this table/chunk:
      * derived from `section_path[]` and/or `caption_text` and/or header_rows
      * e.g., Stage, Grade, Learning Area, Subject, Strand, Theme/Unit if clearly indicated
  - Row-specific context that changes by row (topic/subtopic/code/week/etc.) MUST go in `RowDecision.groupings[]`.
  - Do NOT include TOPIC/SUBTOPIC in context_groupings[] for tables.

## PRIOR CONTEXT GROUPINGS (for chunked stability)
1. The payload may include prior_context_groupings[] (active context stack from the immediately previous decided segment).
2. If segment.chunking exists AND prior_context_groupings[] is present and non-empty:

  - Start from prior_context_groupings[] as a *hint*, but APPLY THE OUTER-EVIDENCE SUPPORT RULE ABOVE. If any prior grouping title is not supported by THIS segment's OUTER evidence, DROP it (do not copy it forward).

  - If THIS segment's OUTER evidence explicitly indicates a DIFFERENT value for a carried role
    (especially SUBJECT/STRAND/THEME/UNIT/WEEK), you MUST override the prior value with the evidence-supported value.

  - Clear contradiction includes:
    * section_path/caption/header_rows naming a different subject/strand/theme/unit/week than prior_context_groupings[].

  - If you change context_groupings[] (dropping unsupported prior context OR overriding due to contradiction),
    explain the change in rationale using only OUTER evidence.

## TABLE-SPECIFIC INSTRUCTIONS
If segment_kind="table":
  - Prefer outputting row decisions in `rows[]`.
  - Put leaf statements in RowDecision.leaves[] (top-level leaves[] should usually be empty).
  - row_index MUST be a 0-based index into the ORIGINAL stitched table rows (ABSOLUTE index).
    - If the Segment includes segment.chunking.row_index_is_absolute=true and a row has `abs_row_index`,
      then RowDecision.row_index MUST EQUAL that `abs_row_index` exactly.
  - Do NOT emit RowDecisions for header rows.
  - Do NOT emit RowDecisions for blank/empty rows.
  - Split multiple statements within a cell into multiple LeafDecisions when clearly separable (bullets, numbering, line breaks).
  - If headers suggest roles (e.g., "Specific Competences", "Expected Standard", "Learning Activities"), map accordingly.
  - Many curriculum tables contain BOTH a higher-level container statement and more specific sub-statements:
    - Treat the higher-level container as a grouping in RowDecision.groupings[],
      and emit ONLY the more specific sub-statements as leaf expectations in RowDecision.leaves[].
    - Do NOT emit both parent container and child sub-items as leaf expectations in the same row.

## BLOCK-SPECIFIC INSTRUCTIONS
1. Use segment.block_type to guide your decision.
2. Do NOT output block_type in SegmentDecision (it will be set deterministically by the pipeline).
3. If block_type is "{BlockType.ARTIFACT.value}" or page-number-like: decision_type="{SegmentDecisionType.IGNORE.value}".
4. If block_type is "{BlockType.CAPTION.value}": decision_type="{SegmentDecisionType.IGNORE.value}" (captions bind later; do not emit nodes).
5. If block_type is "{BlockType.HEADING.value}": Headings are almost always structural containers → default to decision_type="{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}"
6. If unsure whether a heading is real structure vs repeated page furniture, choose decision_type="{SegmentDecisionType.IGNORE.value}".

## CONFIDENCE
Provide confidence ∈ [0,1] where:
  - 0.80+: obvious mapping (clean competence/outcome rows, clear standards statements)
  - 0.60–0.79: reasonable but mild ambiguity
  - 0.30–0.59: ambiguous; likely unresolved
  - <0.30: unresolved
        """
    )

    user_message = dedent(
        f"""Decide on this ONE segment and output a single SegmentDecision JSON object (JSON only, no markdown).

NB: If caption_text is present, it describes the TABLE and is useful context; do not emit it as a node.

Segment JSON:

{json.dumps(segment, ensure_ascii=False, indent=2)}
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )


def double_check_decision_on_segment() -> DotMap:
    """Generate the prompts for double-checking segment decision results.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = None
    user_message = dedent(
        f"""**Hmmmm, are you absolutely sure of your segment decision results?**

Carefully review your last output against the stated instructions and double-check your response.

In particular, ensure that:

1. **No hallucination / no invented content**
  - Every title/body string must be directly supported by the provided segment text/rows.
  - Do NOT introduce codes, topics, grades, or expectations that are not present.
  - Do NOT merge across pages or "continue" content from memory.

2. **Correct decision_type**
  - If the segment is mostly page furniture/numbering/artifact -> use decision_type="{SegmentDecisionType.IGNORE.value}".
  - If ambiguous or not clearly classifiable -> use decision_type="{SegmentDecisionType.UNRESOLVED.value}" and keep arrays empty.
  - Only emit groupings/leaves when you are confident the segment supports them.

3. **Decision-type schema invariants (MUST HOLD)**
  - If decision_type="{SegmentDecisionType.IGNORE.value}" or "{SegmentDecisionType.UNRESOLVED.value}":
    - context_groupings[] MUST be empty
    - groupings[]/leaves[]/rows[] MUST be empty
  - If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}":
    - You MUST NOT emit leaves anywhere (top-level leaves[] empty AND all RowDecision.leaves[] empty)
  - If decision_type="{SegmentDecisionType.EMIT_LEAVES_ONLY.value}":
    - segment-level groupings[] MUST be empty (row-local groupings for tables are allowed)
  - If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES.value}":
    - You MUST emit at least one grouping somewhere AND at least one leaf somewhere

4. **Correct use of roles**
  - Groupings MUST use NodeRole only.
  - Leaves MUST use StatementRole only:
    - {StatementRole.EXPECTATION.value} = normative learning outcome/competence/objective
    - {StatementRole.DESCRIPTOR.value} = benchmark/indicator/expected standard
    - {StatementRole.GUIDANCE.value} = activities/resources/teaching notes
  - If something looks like an activity/task, it is guidance (not expectation).

5. **Role sanity check**
  - If you assigned role=SUBJECT to something that looks like a document title/preamble heading or metadata (mentions curriculum/syllabus/framework/guide/ministry/national/education, publisher, approving authority, table of contents, foreword/preface), DO NOT keep it as SUBJECT.
  - In most cases, such headings should be decision_type="{SegmentDecisionType.IGNORE.value}" (front-matter) OR omitted from groupings entirely.
  - Do NOT "fix" these by converting them into role=SECTION unless they clearly represent meaningful curriculum structure (e.g., a real Unit/Module/Domain/Topic heading).
  - If a heading mixes meaningful hierarchy + boilerplate document-type words, emit ONLY the meaningful hierarchy label. Example: "Lower Primary Education Syllabi" -> emit STAGE="Lower Primary" and omit "Education Syllabi".

6. **Table discipline (if segment_kind="table")**
  - Prefer `rows[]` over top-level `leaves[]`.
  - For table segments: put ALL leaf statements in RowDecision.leaves[] (top-level leaves[] should usually be empty).
  - Each RowDecision.row_index must be a valid 0-based ABSOLUTE index into the ORIGINAL stitched table rows.
    - If segment.chunking exists, every row_index must lie within [row_range_start, row_range_end) (end exclusive), and should match each row's abs_row_index when present.
  - If the input JSON includes `context_rows_before` and/or `context_rows_after`:
    - DO NOT emit RowDecision objects for any row where `is_context_only=true`.
  - For chunked tables, `rows` may be a filled-down view for grouping columns; treat filled values as supported evidence (not hallucination).
  - Do NOT emit RowDecisions for header rows if any appear in the provided payload (rare in chunked slices).
  - Do NOT emit RowDecisions for blank/empty rows.
  - If you split a cell into multiple statements, ensure each LeafDecision is atomic and non-overlapping.
  - If headers imply roles (e.g., "Specific Competences", "Learning Activities", "Expected Standard"), map them correctly.

  - **OUTER vs row-local context (CRITICAL)**
    - OUTER context must go in `context_groupings[]` (supported by section_path/caption/header_rows).
    - Row-local context must go in `RowDecision.groupings[]` (topic/subtopic/code/week/etc).
    - Do NOT put TOPIC/SUBTOPIC into context_groupings[] for tables (they belong in RowDecision.groupings[]).

  - **HARD RULE: outer-evidence support for context_groupings**
    - Every `context_groupings[i].title` MUST be directly supported by OUTER evidence:
      - it must appear verbatim in at least one of:
        - `section_path[]`, OR
        - `caption_text`, OR
        - `header_rows_canonical`
    - If a context_groupings title is unsupported, DELETE that grouping (do NOT guess and do NOT carry it over from memory).
    - If `header_rows_canonical[0]` is a single merged label (e.g., "WRITING", "READING"), treat it as strong outer evidence for STRAND.
      You MUST emit it as a STRAND grouping:
        * Prefer placing it in `context_groupings[]` when the table is chunked (for stability), OR
        * Place it in segment-level `groupings[]` when the table is not chunked.
    - If prior_context_groupings contains a STRAND and the merged header label is different, that is a contradiction → override STRAND to the merged header label.
    - This rule OVERRIDES prior_context_groupings. Do NOT carry over prior_context_groupings titles unless they are supported by THIS segment's OUTER evidence.

  - **Context ordering + stability (IMPORTANT)**
    - context_groupings[] must be ordered OUTER→INNER using this fixed role order:
      STAGE → GRADE_LEVEL → LEARNING_AREA → SUBJECT → STRAND → SUBSTRAND → THEME → UNIT → WEEK → TOPIC → SUBTOPIC → SECTION → PROSE
    - Do not repeat the same NodeRole more than once in context_groupings[].
    - If segment.chunking exists and prior_context_groupings[] is provided and non-empty:
      - Start from prior_context_groupings[] as a hint, BUT APPLY the outer-evidence support rule:
          * Drop any prior grouping titles that are NOT supported by THIS segment’s OUTER evidence.
      - If THIS segment’s OUTER evidence explicitly indicates a DIFFERENT value for a carried role
        (especially SUBJECT/STRAND/THEME/UNIT/WEEK), you MUST override the prior value with the evidence-supported value.
      - Clear contradiction includes: section_path/caption/header_rows explicitly naming a different subject/strand/theme/unit/week than prior_context_groupings[].

  - If a row lists multiple same-level siblings (e.g., multiple subjects/topics in one cell), emit multiple RowDecisions with the SAME row_index, one per sibling; do not stack siblings into one groupings[] path.

  - **Hierarchy/coding discipline**
    - If a row contains a higher-level container statement (e.g., a "Main/General" item) plus more specific sub-items (often indicated by hierarchical codes/local_code like "1.1" and "1.1.1"), treat the higher-level item as a grouping in `RowDecision.groupings[]` and emit ONLY the more specific sub-items as leaf expectations.
    - Do NOT emit both the parent container and its child sub-items as leaf expectations in the same row.

7. **Block discipline (if segment_kind="block")**
  - If block_type is "{BlockType.CAPTION.value}": decision_type should usually be "{SegmentDecisionType.IGNORE.value}" (captions bind later).
  - If block_type is "{BlockType.HEADING.value}":
    - Headings usually denote hierarchy containers → prefer emitting groupings.
    - Ignore only if it looks like page furniture (running header/footer, repeated publisher line, standalone page number).

8. **Conservativeness + confidence calibration**
  - If you're not sure, mark unresolved with confidence < 0.6.
  - Only use confidence >= 0.85 when the mapping is obvious.

9. **Emit decisions: required fields**
  - If decision_type is any emit_*:
    - Always include the field `context_groupings[]` (it may be empty).
    - For TABLE segments and for any decision that emits leaf statements, prefer a non-empty `context_groupings[]` when supported by evidence (grade/stage/subject/theme/unit via section_path/caption/header_rows).
    - If the only outer evidence is institutional/front-matter metadata, use `context_groupings=[]` (attach to framework root).
    - Reminder: the compiler will not create nodes from segment.section_path automatically.

10. **Grade label check**
  - If role=GRADE_LEVEL contains extra narrative words beyond the grade identifier/band, rewrite it so grade_level is only the grade label.
  - Only emit an additional grouping for the remaining phrase if it is meaningful curriculum structure (e.g., Topic/Unit/Strand); otherwise omit it.
  - Do NOT emit SECTION for generic document-type leftovers (e.g., "Syllabus", "Curriculum", "Framework").

11. **SECTION usage guardrail (IMPORTANT)**
  - role=SECTION is NOT a default fallback.
  - Use SECTION only for meaningful curriculum structure labels that are not better captured as STAGE/GRADE_LEVEL/LEARNING_AREA/SUBJECT/THEME/UNIT/WEEK/STRAND/TOPIC.
  - Do NOT emit SECTION for generic document-type words that do not add hierarchy signal, such as: "syllabus/syllabi", "curriculum", "framework", "guide", "teacher's guide", "national curriculum", "table of contents", "foreword", "preface", "acknowledgements".

When you are confident in your answer, return a complete `SegmentDecision` that matches the schema and fixes any issues you might've overlooked or incorrect assumptions you might've made.
        """
    )

    return DotMap(
        {"system_message": system_message, "user_message": user_message.strip()}
    )
