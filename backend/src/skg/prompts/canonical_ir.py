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
from skg.canonical_ir.schemas import GroupingCanonicalizationKey
from skg.utils.constants import (
    CONTEXT_GROUPINGS_ROLE_ORDER,
    BlockType,
    NodeRole,
    SegmentDecisionType,
    StatementRole,
)


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
  - If uncertain:
    - If you can still propose *candidate* groupings/leaves/rows (based only on visible evidence), choose decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" and include the best candidate outputs + explain ambiguity.
    - If you cannot propose any safe structure or leaves without guessing, choose decision_type="{SegmentDecisionType.UNRESOLVED.value}" and explain why.
  - Avoid over-classifying ambiguous prose as standards.
3. DO NOT TRANSLATE.
  - Preserve original language and casing.
  - Translation happens later.
4. Do NOT include institutional or governmental identifiers (e.g., country name, ministry name, publisher, approving authority) in context_groupings[]. Such text is document metadata, not curriculum hierarchy. If no curriculum hierarchy is present, use an empty context.
5. If decision_type="{SegmentDecisionType.IGNORE.value}" -> keep groupings/leaves/rows empty arrays.
6. If decision_type="{SegmentDecisionType.UNRESOLVED.value}" -> keep arrays empty and explain why in rationale.
7. If decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}":
  - You MUST output your best candidate interpretation using ONLY the evidence in the segment.
  - You MAY emit context_groupings[], groupings[], leaves[], and/or rows[].
  - You MUST emit at least one of: groupings[] OR leaves[] OR rows[] (do not leave everything empty).
  - The output WILL NOT be compiled into the final CanonicalIR tree; it is stored for human review.
  - Explain precisely what is ambiguous in rationale (e.g., unclear grade/subject boundary, table header drift, mixed furniture vs structure).
8. For table segments, prefer using rows[] over leaves[].
9. CHUNKING: The segment may represent a *slice* of a larger table.
  - If Segment includes a `chunking` object, ONLY decide on the rows provided in this segment payload.
  - NEVER assume missing rows exist outside this chunk.
10. If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}":
  - You MAY emit groupings[] and/or rows[] (row-level groupings)
  - You MUST NOT emit any leaves (top-level leaves[] must be empty AND all RowDecision.leaves[] must be empty)
11. If decision_type="{SegmentDecisionType.EMIT_LEAVES_ONLY.value}":
  - segment-level groupings[] MUST be empty
  - You MAY still use rows[] for tables, and RowDecision.groupings[] is allowed (row-local containers)
  - Note: "{SegmentDecisionType.EMIT_LEAVES_ONLY.value}" means no *segment-level* groupings; row-local groupings are still allowed for tables.
12. If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES.value}":
  - You MUST emit at least one grouping somewhere (segment-level groupings[] or RowDecision.groupings[])
  - You MUST emit at least one leaf somewhere (top-level leaves[] or RowDecision.leaves[])
13. IMPORTANT (tables): You MAY emit multiple RowDecision objects with the SAME row_index when a single source table row contains *multiple sibling values* (sibling fanout).
  - Example: one row includes two different Subjects/Strands/Topics that each need their own grouping path.
  - In that case, emit multiple RowDecisions with the same row_index (each with different groupings and/or leaves).
  - You MUST NOT emit exact duplicate RowDecision entries (same row_index + identical groupings + identical leaves).
14. If decision_type="{SegmentDecisionType.IGNORE.value}" or "{SegmentDecisionType.UNRESOLVED.value}" -> context_groupings[] MUST be empty.
15. If decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" -> context_groupings[] MAY be empty or non-empty.
  - Prefer empty context_groupings[] when the outer hierarchy is unclear, even if you can parse row-local leaves/groupings.
16. Preserve statement order as it appears in the segment; do not reorder leaves or row decisions.
17. Always include the field `context_groupings[]` in your output. It MAY be empty.
  - The deterministic compiler WILL NOT create hierarchy nodes from `segment.section_path[]`.
  - Therefore, you MUST explicitly provide the hierarchy context snapshot in `context_groupings[]` when there is clear curriculum structure evidence.
18.CONTEXT SNAPSHOT CONSERVATISM
  A) Table of Contents suppression:
  - If any outer heading indicates "Table of Contents"/"Contents":
    - decision_type MUST be "{SegmentDecisionType.IGNORE.value}"
    - context_groupings[] MUST be []
    - groupings/leaves/rows MUST be empty
  B) Nearest-headings-only rule:
  - Prefer the nearest headings in segment.section_path[] as evidence for context_groupings[].
  - Default: use the LAST 3 items.
  - Exception (allowed): you MAY use up to the LAST 8 items **only if needed** to recover stable curriculum anchors (e.g., Grade/Subject) that are missing from the last 3.
  - If you use any heading beyond the last 3, you MUST mention this explicitly in `rationale`.
19. TABLE-WIDE OUTER CONTEXT (IMPORTANT):
  - If the segment is a TABLE and you see table-wide anchors (...) that apply to the WHOLE table AND they are supported by NEAREST outer evidence (not TOC/front-matter), you MUST place them in context_groupings[].
  - If outer context is ambiguous/noisy, prefer context_groupings=[] and parse row-local groupings/leaves only.
  - For CHUNKED tables (chunking.is_chunked == true)
    - You MUST place table-wide anchors in `context_groupings[]` and MUST NOT place them in segment-level `groupings[]`.
    - On the FIRST chunk: decide the full, stable `context_groupings[]` for the whole table.
    - On ALL later chunks: repeat `context_groupings[]` EXACTLY as `prior_context_groupings[]` (do not override or modify it).
    - If evidence in a later chunk appears to contradict the established context:
      - Prefer decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" so you can still emit candidate row parsing, but do NOT commit to changing context mid-table.
      - Use decision_type="{SegmentDecisionType.UNRESOLVED.value}" only if you cannot safely parse anything without guessing.
  - For NON-CHUNKED tables: prefer placing table-wide anchors in `context_groupings[]`, but placing a merged-header anchor in segment-level `groupings[]` is allowed.
20. If caption_text is present, it describes the TABLE and is useful context; do not emit it as a node.
21. Every `context_groupings[i].title` MUST be directly supported by OUTER evidence OR safe carry-forward evidence:
  A) OUTER EVIDENCE SUPPORT (default rule)
  - The title must appear verbatim in at least one of:
    - `segment.section_path[]`, OR
    - `caption_text`, OR
    - `header_rows_canonical`
  B) SAFE CARRY-FORWARD SUPPORT (allowed for stable outer roles)
  - If `prior_context_groupings[]` contains the SAME role/title, you MAY keep that role/title in `context_groupings[]`
    even if it does not reappear verbatim in OUTER evidence, as long as ALL are true:
      1) The role is one of these stable carry roles: STAGE, GRADE_LEVEL, LEARNING_AREA, SUBJECT, THEME, UNIT, WEEK, TERM
      2) There is NO clear contradiction in this segment’s OUTER evidence (section_path/caption/header_rows explicitly naming a different value for that role).
      3) You are NOT introducing governmental/institutional metadata (country/ministry/publisher).
      4) You are not adding TOPIC/SUBTOPIC via carry-forward.
  - If unsupported by (A) or (B), DELETE that grouping.
  - If you use carry-forward support (B), you MUST mention it explicitly in `rationale`.

## SOURCE LABELS (REQUIRED WHEN AVAILABLE)
1. When you emit any GroupingDecision or LeafDecision, also emit `source_label` whenever you can.
2. `source_label` MUST be copied VERBATIM from visible evidence in the segment payload:
  - Table column headers (preferred): e.g., "Topic", "Sub-topic", "Specific Competences", "Expected Standard", "Learning Activities"
  - Heading/section labels (if clearly the label introducing the statements)
3. If no explicit label exists in the evidence, set source_label=null (or omit it).
4. Do NOT invent or paraphrase source_label.

## CODES vs. LIST MARKERS (IMPORTANT)
1. Curriculum PDFs often contain BOTH:
  - Official curriculum codes like: `3.9.4.1`
  - Bullet/list markers like: `a)`, `i`, `•`
2. Rules:
  - Put OFFICIAL codes (e.g., `3.9.4.1`) into `LeafDecision.local_code`
  - Put ONLY bullet/list markers into `LeafDecision.list_marker`
  - NEVER put official codes into `list_marker`

## ALLOWED ENUM VALUES
1. decision_type: {decision_types_str}
2. NodeRole (for grouping decisions): {node_roles_str}
3. StatementRole (for leaf decisions):
  - "{StatementRole.EXPECTATION.value}"   (normative learning outcome/competence/objective/standard)
  - "{StatementRole.DESCRIPTOR.value}"    (benchmark/indicator/expected standard/performance criteria)
  - "{StatementRole.GUIDANCE.value}"      (learning activities, pedagogy, resources, notes)

## LEARNING AREA vs. SUBJECT (IMPORTANT)
1. Use role="{NodeRole.LEARNING_AREA.value}" for broad umbrella areas such as:
  - "Literacy and Language"
  - "Mathematics and Science"
  - "Creative and Technology Studies"
  - etc.
2. Use role="{NodeRole.SUBJECT.value}" for the specific syllabus subject such as:
  - "English Language"
  - "Mathematics"
  - "Integrated Science"
  - etc.

## CONTEXT GROUPINGS ORDER + STABILITY (IMPORTANT)
1. context_groupings[] MUST be ordered from OUTER → INNER using this fixed role order: STAGE → GRADE_LEVEL → LEARNING_AREA → SUBJECT → STRAND → SUBSTRAND → THEME → UNIT → WEEK → TOPIC → SUBTOPIC → SECTION → PROSE
2. Do NOT repeat the same NodeRole more than once in context_groupings[].
3. Only include PROSE when the segment is truly front matter/narrative structure; otherwise prefer SECTION or leave context empty.

## IMPORTANT OVERRIDE
  - On the FIRST chunk of a chunked table, this outer-evidence rule OVERRIDES prior_context_groupings.
  - For later chunks, you MAY carry forward prior_context_groupings to keep table context stable, unless there is a clear contradiction in THIS chunk’s OUTER evidence.

## TABLE-SPECIFIC GUIDANCE
1. If `header_rows_canonical[0]` is a single merged label (e.g., "WRITING", "READING"), treat it as strong OUTER evidence for a stable table-scoped grouping (often STRAND), and prefer it over prior context if they conflict.
2. If `header_rows_canonical[0]` is a single merged label, you MUST emit it as a STRAND grouping:
  - Prefer placing it in `context_groupings[]` when the table is chunked (for stability across chunks) OR place it in segment-level `groupings[]` when the table is not chunked.
  - If prior_context_groupings contains STRAND and the merged header label is different, that is a contradiction → override STRAND to the merged header label.
3. If segment_kind="table":
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

## OUTER context vs. row-local context for tables
1. For TABLE segments (including chunked slices):
  - `context_groupings[]` represents ONLY stable OUTER context for this table/chunk:
    - derived from `section_path[]` and/or `caption_text` and/or header_rows (e.g., Stage, Grade, Learning Area, Subject, Strand, Theme/Unit if clearly indicated)
  - Row-specific context that changes by row (topic/subtopic/code/week/etc.) MUST go in `RowDecision.groupings[]`.
  - Do NOT include TOPIC/SUBTOPIC in context_groupings[] for tables.

## PRIOR CONTEXT GROUPINGS (for chunked stability)
1. The payload may include prior_context_groupings[] (active context stack from the immediately previous decided segment).
2. If prior_context_groupings[] is present and non-empty:
  - Start from prior_context_groupings[] as a hint for stable outer context.
  - You MAY carry-forward stable outer roles using Rule 20(B), even if not repeated verbatim in this segment.
  - If segment.chunking exists:
    - If chunking.is_first_chunk=true: apply the outer-evidence support rule and DROP any unsupported prior grouping.
    - If chunking.is_first_chunk=false: you SHOULD repeat prior_context_groupings[] EXACTLY unless there is a clear contradiction.
  - If THIS segment's OUTER evidence explicitly indicates a DIFFERENT value for a carried role:
    - Prefer decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}"
      so you can still emit candidate parsing but explain the contradiction.
    - Use decision_type="{SegmentDecisionType.UNRESOLVED.value}" only if you cannot safely emit candidate outputs.

## BLOCK-SPECIFIC GUIDANCE
1. Use segment.block_type to guide your decision.
2. Do NOT output block_type in SegmentDecision (it will be set deterministically by the pipeline).
3. If block_type is "{BlockType.ARTIFACT.value}" or page-number-like: decision_type="{SegmentDecisionType.IGNORE.value}".
4. If block_type is "{BlockType.CAPTION.value}": decision_type="{SegmentDecisionType.IGNORE.value}" (captions bind later; do not emit nodes).
5. If block_type is "{BlockType.HEADING.value}": Headings are almost always structural containers → default to decision_type="{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}"
6. If unsure whether a heading is real structure vs repeated page furniture, choose decision_type="{SegmentDecisionType.IGNORE.value}".

## CONFIDENCE
Provide confidence ∈ [0,1] where:
  - 0.75+: reasonable or obvious mapping (clean competence/outcome rows, clear standards statements)
  - 0.50–0.74: Mild ambiguity but likely resolvable
  - <0.50: ambiguous, unresolvable without guessing
        """
    )

    user_message = dedent(
        f"""Decide on this ONE segment and output a single SegmentDecision JSON object (JSON only, no markdown).

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

1. **No hallucination/no invented content**
  - Every title/body string must be directly supported by the provided segment text/rows.
  - Do NOT introduce codes, topics, grades, or expectations that are not present.
  - Do NOT merge across pages or "continue" content from memory.
2. **Correct decision_type**
  - If the segment is mostly page furniture/numbering/artifact -> use decision_type="{SegmentDecisionType.IGNORE.value}".
  - If ambiguous or not clearly classifiable:
    - If you can still propose *candidate* groupings/leaves/rows based only on the visible segment evidence, use decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" and include those candidate outputs.
    - If you cannot safely propose any outputs without guessing, use decision_type="{SegmentDecisionType.UNRESOLVED.value}" and keep arrays empty.
  - Only emit non-flagged emit_* decisions when you are confident the segment supports them.
3. **Decision-type schema invariants (MUST HOLD)**
  - If decision_type="{SegmentDecisionType.IGNORE.value}" or "{SegmentDecisionType.UNRESOLVED.value}":
    - context_groupings[] MUST be empty
    - groupings[]/leaves[]/rows[] MUST be empty
  - If decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}":
    - context_groupings[] MAY be empty or non-empty
    - You MUST emit at least one of: groupings[] OR leaves[] OR rows[] (do not leave everything empty)
    - This decision is for human review and WILL NOT be materialized into the CanonicalIR tree
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
    - CHUNKED TABLE RULE: If chunking.is_chunked == true, table-wide anchors (Grade/Stage/Learning Area/Subject/Theme/Unit/Term/Week/Strand/Substrand) MUST be in context_groupings[] and MUST NOT be emitted in segment-level groupings[].
  - **HARD RULE: outer-evidence support for context_groupings**
    - Every `context_groupings[i].title` MUST be directly supported by OUTER evidence:
      - it must appear verbatim in at least one of:
        - `section_path[]`, OR
        - `caption_text`, OR
        - `header_rows_canonical`
    - If a context_groupings title is unsupported, DELETE that grouping (do NOT guess and do NOT carry it over from memory).
    - If header_rows_canonical[0] is a single merged label (e.g., "WRITING", "READING"): treat it as strong OUTER evidence for a stable table-scoped grouping. It is often STRAND, but may sometimes be SUBJECT or THEME — choose the best-fitting OUTER anchor role.
    - If prior_context_groupings contains a STRAND and the merged header label is different:
      - If this is the FIRST chunk: use the merged header label (evidence wins) and explain in rationale.
      - If this is NOT the first chunk: do NOT override mid-table. Prefer decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" (so you can still emit candidate row parsing), and cite the contradiction. Use "{SegmentDecisionType.UNRESOLVED.value}" only if you cannot safely emit any candidate outputs.
    - For chunked tables: apply strict outer-evidence support on the FIRST chunk.
      - For later chunks: repeat context_groupings[] EXACTLY as prior_context_groupings[].
      - If outer evidence in a later chunk contradicts the prior context: Prefer decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" so you can still emit candidate row parsing, but DO NOT change context mid-table.
      - Use "{SegmentDecisionType.UNRESOLVED.value}" only if you cannot safely emit candidate outputs.
  - **Context ordering + stability (IMPORTANT)**
    - context_groupings[] must be ordered OUTER→INNER using this fixed role order:
      STAGE → GRADE_LEVEL → LEARNING_AREA → SUBJECT → STRAND → SUBSTRAND → THEME → UNIT → WEEK → TOPIC → SUBTOPIC → SECTION → PROSE
    - Do not repeat the same NodeRole more than once in context_groupings[].
    - If segment.chunking exists and prior_context_groupings[] is provided and non-empty:
      - If chunking.is_first_chunk=true:
        * Apply the outer-evidence support rule strictly; DROP unsupported prior groupings.
      - If chunking.is_first_chunk=false:
        * Keep prior_context_groupings roles+titles unchanged for stability UNLESS this chunk’s OUTER evidence explicitly contradicts it.
     - If contradicted by this chunk’s OUTER evidence:
       - If this is the FIRST chunk: apply outer evidence and adjust context_groupings accordingly (explain in rationale).
       - If this is NOT the first chunk: do NOT override mid-table. Prefer decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" (emit candidate row parsing + cite contradiction).
       - Use "{SegmentDecisionType.UNRESOLVED.value}" only if you cannot safely emit any candidate outputs.
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
  - If you're not sure, mark unresolved with confidence < 0.50.
  - Only use confidence >= 0.75 when the mapping is obvious.
9. **Emit decisions: required fields**
  - If decision_type is any emit_*:
    - Always include the field `context_groupings[]` (it may be empty).
    - For TABLE segments and for any decision that emits leaf statements, prefer a non-empty `context_groupings[]` when supported by evidence (grade/stage/subject/theme/unit via section_path/caption/header_rows).
    - If the only outer evidence is institutional/front-matter metadata, use `context_groupings=[]` (attach to framework root).
    - Reminder: the compiler will not create nodes from segment.section_path automatically.
  - You may use prior_context_groupings, prev_segment_hint, next_segment_hint ONLY as continuity hints
    (to decide whether this segment continues the prior context or begins a new context).
  - Do NOT treat prev_segment_hint / next_segment_hint as OUTER evidence. Only use groupings that are supported by THIS segment’s OUTER evidence:
      - segment.section_path
      - caption_text
      - header_rows_canonical
    (and for chunked tables after the first chunk, prior_context_groupings may be carried forward unless contradicted).
10 .**Outer anchor requirement (IMPORTANT):**
  - If you emit any EXPECTATION/DESCRIPTOR/GUIDANCE leaves (block leaves or table row leaves), your decision MUST include at least ONE “outer anchor” grouping somewhere in:
    - `context_groupings[]` OR
    - emitted `groupings[]` OR
    - `rows[].groupings[]`
  - Outer anchors include: `GRADE_LEVEL`, `STAGE`, `LEARNING_AREA`, `SUBJECT`, `THEME`, `UNIT`, `WEEK`.
  - If no outer anchor is supported by evidence:
    - Prefer decision_type="{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" if you can still emit candidate leaves/groupings, and explain that anchoring is ambiguous.
    - Use "{SegmentDecisionType.UNRESOLVED.value}" only if you cannot safely emit any candidate outputs.
11. **No outer-than-context groupings:**
  - `groupings[]` are *children under the context stack tip.*
  - Therefore, never emit a grouping whose role is OUTER than the deepest role in `context_groupings[]`.
  - Example (bad): `context_groupings=[SUBJECT]` and `groupings=[GRADE_LEVEL]`.
  - Fix: either include grade in `context_groupings`, or emit both in `groupings[]` in correct outer→inner order.
12. **Grade label check**
  - If role=GRADE_LEVEL contains extra narrative words beyond the grade identifier/band, rewrite it so grade_level is only the grade label.
  - Only emit an additional grouping for the remaining phrase if it is meaningful curriculum structure (e.g., Topic/Unit/Strand); otherwise omit it.
  - Do NOT emit SECTION for generic document-type leftovers (e.g., "Syllabus", "Curriculum", "Framework").
13. **SECTION usage guardrail (IMPORTANT)**
  - role=SECTION is NOT a default fallback.
  - Use SECTION only for meaningful curriculum structure labels that are not better captured as STAGE/GRADE_LEVEL/LEARNING_AREA/SUBJECT/THEME/UNIT/WEEK/STRAND/TOPIC.
  - Do NOT emit SECTION for generic document-type words that do not add hierarchy signal, such as: "syllabus/syllabi", "curriculum", "framework", "guide", "teacher's guide", "national curriculum", "table of contents", "foreword", "preface", "acknowledgements".

When you are confident in your answer, return a complete `SegmentDecision` that matches the schema and fixes any issues you might've overlooked or incorrect assumptions you might've made.
        """
    )

    return DotMap(
        {"system_message": system_message, "user_message": user_message.strip()}
    )


def grouping_canonicalization_instructions(
    *,
    grouping_keys: list[GroupingCanonicalizationKey],
    known_canonical_keys: list[dict[str, str]] | None = None,
) -> DotMap:
    """Return the grouping canonicalization instructions.

    Parameters
    ----------
    grouping_keys
        The list of GroupingCanonicalizationKey objects to be canonicalized.
    known_canonical_keys
        Optional list of {'role': str, 'title': str} representing canonical
        standards established in previous batches.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    # Build allowed roles/precedence strings.
    allowed_roles = [r.value for r in CONTEXT_GROUPINGS_ROLE_ORDER]
    precedence_str = " > ".join(allowed_roles)

    context_str = ""

    if known_canonical_keys:
        formatted_keys = "\n".join(
            [f"- [{k['role']}] {k['title']}" for k in known_canonical_keys]
        )
        context_str = dedent(
            f"""## ESTABLISHED CANONICALS
The following grouping nodes have ALREADY been established in this document.

- You MUST map to an established canonical WHEN semantically equivalent AND the ROLE matches exactly.
- When mapping to an established canonical, the output title MUST match EXACTLY (string match).
- Only create NEW canonical nodes if the input cannot map to this list.

{formatted_keys}
        """
        )

    system_message = dedent(
        f"""You are canonicalizing curriculum grouping nodes globally for a single curriculum document.

You will receive a list of grouping candidates, each with:
- role (NodeRole enum)
- title
- optional local_code / source_label

Return a GroupingCanonicalizationMap with:
- items: list[GroupingCanonicalizationItem]
- EXACTLY one item per input grouping key
- items MUST be in the SAME ORDER as the input grouping keys

## Allowed roles
Roles MUST be one of:
{", ".join(allowed_roles)}

## Role precedence (outer → inner)
For action=SPLIT, the output groupings MUST be ordered using this precedence:
{precedence_str}

Each GroupingCanonicalizationItem must contain:
- action: keep | replace | split | drop
- confidence: float in [0,1]
- input: the original key (verbatim-ish)
- output: list of GroupingCanonicalizationKey (empty for keep/drop)
- rationale: short string (optional)

## Action semantics (must satisfy validator rules)
- keep: output MUST be [] (preferred) OR output=[input]
- drop: output MUST be []
- replace: output MUST have length 1 and output[0].role MUST equal input.role
- split: output MUST have length >= 2 (roles may differ but must be allowed roles)

{context_str}

Rules:
1. Do NOT invent new curriculum concepts not present in the input.
2. Prefer minimal changes: whitespace/punctuation normalization and synonym folding. Avoid casing changes unless required to match an established canonical exactly.
3. REPLACE must not change role (validator enforces this).
4. SPLIT only when the title clearly contains multiple groupings (e.g., "Grade 1 - Mathematics", "Theme 2: Plants") AND each split part is directly present as a substring. Do not paraphrase or infer.
5. For SPLIT:
   - output MUST be ordered outer→inner using the precedence list above.
   - do NOT emit duplicate output groupings.
6. If unsure, choose KEEP with lower confidence (e.g., 0.6–0.8). Do NOT DROP uncertain items.
7. Mapping to ESTABLISHED CANONICALS:
   - Only map if role matches exactly
   - Canonical title must match exactly
        """
    )

    user_message = dedent(
        f"""Canonicalize the following NEW grouping keys.

Input keys (JSON array):
```json
{json.dumps([k.model_dump(mode="json") for k in grouping_keys], ensure_ascii=False)}
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
