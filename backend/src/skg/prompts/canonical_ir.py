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
10. If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES.value}":
   - You MUST emit at least one grouping somewhere (segment-level groupings[] or RowDecision.groupings[])
   - You MUST emit at least one leaf somewhere (top-level leaves[] or RowDecision.leaves[])

## WHAT TO EXTRACT
1. A segment can yield:
  - groupings: hierarchy containers (e.g., subject/grade/strand/topic/theme/unit/week/stage/section)
  - leaves: atomic statements (expectations/descriptors/guidance)
  - rows: for tables, per-row groupings + leaves (preferred)

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

## ROLE GUIDANCE (how to label leaves)
{StatementRole.EXPECTATION.name}:
  - "Learners should be able to..."
  - "Pupils demonstrate..."
  - "Student can..."
  - "Specific competence:" statements
  - "Learning outcome:" statements
  - Clear measurable objective/standard

{StatementRole.DESCRIPTOR.name}:
  - "Expected standard / benchmark"
  - performance indicator/criteria
  - rubric-like achievement description
  - "Assessment criteria"

{StatementRole.GUIDANCE.name}:
  - activities/tasks: "Discuss...", "Do...", "Group work...", "Teacher guides..."
  - "Learning activities" column content
  - exemplars/resources/teaching notes

## GROUPING GUIDANCE (how to label hierarchy containers)
1. Use GroupingDecision for container nodes like:
  - grade/stage ("Standard I", "Grade 2", "P1")
  - subject/learning area ("Mathematics", "Literacy", "Science")
  - theme/sub-theme/week (Uganda thematic curriculum)
  - strand/main competence/topic/subtopic/unit/section
2. Prefer the most specific reasonable NodeRole.
3. If you are unsure, use role="{NodeRole.SECTION.value}" or omit groupings.
4. REQUIRED: Emit `context_groupings[]` for any decision that emits anything (any `emit_*` decision_type).
  - The deterministic compiler WILL NOT create hierarchy nodes from `segment.section_path[]`.
  - Therefore, YOU MUST explicitly provide the hierarchy context snapshot in `context_groupings[]`.
  - `segment.section_path` is provided as EVIDENCE ONLY (semantic-light headings).
  - `context_groupings[]` must be derived only from evidence in the segment payload:
    - section_path texts
    - caption_text (if present for tables)
    - table header phrases if they clearly imply context
  - If no usable context evidence exists, context_groupings may be [] (attach to framework root).
  - If uncertain about context, choose decision_type="unresolved".
5. **Role assignment guardrails (IMPORTANT):**
  - Use role=SUBJECT ONLY for actual learning areas/subjects (e.g., Mathematics, English, Science, Social Studies).
  - Do NOT label document titles, publishers, ministries, or high-level document headings as SUBJECT.
  - Examples of non-subject headings: “Curriculum”, “Syllabus”, “Framework”, “Ministry of Education”, “National Curriculum”, “Table of Contents”. Stage-band headings like “Lower Primary”, “Upper Primary”, “Primary Cycle” should be role=STAGE (not SUBJECT).
6. **Grade/Stage grouping normalization (IMPORTANT):**
  - role=GRADE_LEVEL should contain only the grade identifier/band (e.g., “Grade 1”, “P1”, “Standard I–II”, “Grades 1–3”).
  - role=STAGE should contain stage/cycle names (e.g., “Lower Primary”, “Upper Primary”, “Primary Cycle”).
  - If a heading mixes both, split into STAGE + GRADE_LEVEL (and optionally SECTION for the remaining phrase).

## IMPORTANT: Outer context vs. row-local context for tables
1. For TABLE segments (including chunked slices):
  - `context_groupings[]` should represent ONLY the stable OUTER context for this table/chunk:
      * derived from `section_path[]` and/or `caption_text` and/or table headers
      * e.g., Grade/Stage, Subject/Learning Area, Theme/Unit if clearly indicated
  - Row-specific context (topic/subtopic/strand/code/week/etc. that changes by row) MUST go inside `RowDecision.groupings[]`.
  - Do NOT promote per-row values from table cells into `context_groupings[]` unless they are clearly table-scoped and stable across the chunk (rare).

## TABLE-SPECIFIC INSTRUCTIONS
1. If segment_kind="table":
  - Prefer outputting row decisions in `rows[]`.
  - For each emitted RowDecision:
    - row_index MUST be a 0-based index into the ORIGINAL stitched DocumentIR table rows (ABSOLUTE index).
      - If the Segment includes segment.chunking.row_index_is_absolute=true, each provided row includes `abs_row_index`. In that case: RowDecision.row_index MUST EQUAL the row's abs_row_index (copy it exactly).
    - Do NOT emit RowDecisions for header rows.
      - Header rows are row_index values < segment.header_row_count.
      - Only emit RowDecisions for body rows unless a header row clearly contains real standards/expectations (rare).
    - groupings[] should capture container-like cells (subject/topic/strand/stage/week etc.).
    - leaves[] should capture expectation/descriptor/guidance statements from that row.
  - Reminder: Put stable OUTER context in `context_groupings[]`, and row-varying context in `RowDecision.groupings[]`.
  - Split multiple statements within a cell into multiple LeafDecisions when clearly separable (bullets, numbering, semicolons, line breaks).
  - If headers suggest roles (e.g., "Specific Competences", "Expected Standard", "Learning Activities"), map accordingly.
  - If no rows contain anything meaningful (e.g., empty or purely formatting), use decision_type="{SegmentDecisionType.IGNORE.value}".
  - Multi-valued cells/sibling fanout rule (IMPORTANT):
    - If a single table row contains multiple same-level sibling items (e.g., a list of topics/subjects/units/skills in one cell), do NOT encode them as a single hierarchy path inside one RowDecision.groupings[].
    - Instead, emit multiple RowDecision entries with the SAME row_index, each containing the shared parent grouping(s) plus exactly one sibling item.
    - This preserves the intended structure: one parent → many children.
  - **Hierarchy/coding discipline (IMPORTANT):**
    - Many curriculum tables contain BOTH a higher-level container statement and more specific sub-statements. This may be expressed via headers (e.g., "Main/General competence" vs "Specific competence"), or via hierarchical codes/list_ids where a parent code is a prefix of child codes (e.g., "1.1" and "1.1.1").
    - In these cases, treat the higher-level container as a **grouping** in `RowDecision.groupings[]` (use role STRAND/TOPIC/SECTION as appropriate, and keep the code in list_id if present), and emit ONLY the more specific sub-statements as leaf expectations in `RowDecision.leaves[]`.
    - Do NOT emit both the parent container and its child sub-statements as leaf expectations in the same row.
2. CHUNKED TABLES:
  - If segment.chunking exists, the provided `segment.rows` is a slice of the original table.
  - ONLY emit RowDecisions for the provided rows in this payload.
  - Do not emit RowDecisions for rows you cannot see.
  - If a row cell is blank: emit nothing for that cell (do not hallucinate).
  - Example: if a provided row has abs_row_index=57, then RowDecision.row_index MUST be 57.

## CAPTION BINDING (IMPORTANT)
1. For table segments, the input JSON may include optional caption metadata fields:
  - caption_kind
  - caption_text
  - caption_segment_id
  - caption_page_index
  - caption_gap_segments
2. How to use caption_text:
  - Treat caption_text as table-scoped context (it describes what the table represents).
  - Use it to disambiguate subject/grade/theme/strand/week/stage when the table headers or section path are insufficient.
  - caption_text is NOT a curriculum statement and should NOT be emitted as a grouping node or a leaf statement by itself.
  - Do NOT copy caption_text verbatim into groupings or leaves.
  - If caption_text contains key structure (e.g., "Grade 2 Mathematics"), you may incorporate the IMPLIED structure by creating normal groupings (e.g., SUBJECT="Mathematics", GRADE_LEVEL="Grade 2"), but only if confident.
  - If caption_text conflicts with section_path_text, prefer section_path_text unless the caption is clearly more specific for this table.

## BLOCK-SPECIFIC INSTRUCTIONS
1. Use the input field segment.block_type to guide your decision.
2. Do NOT output block_type in SegmentDecision (it will be set deterministically by the pipeline).
3. If block_type is "{BlockType.ARTIFACT.value}" or page-number-like: decision_type="{SegmentDecisionType.IGNORE.value}".
4. If block_type is "{BlockType.CAPTION.value}": decision_type="{SegmentDecisionType.IGNORE.value}" (captions bind to tables later; do not emit nodes).
5. If block_type is "{BlockType.HEADING.value}":
6. Headings are almost always structural containers → default to decision_type="{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}"
7. Choose the most specific NodeRole you can infer (GRADE_LEVEL / SUBJECT / THEME / STRAND / TOPIC / UNIT / WEEK / STAGE / SECTION).
8. If unsure, STILL emit a grouping using role="{NodeRole.SECTION.value}" and title = the heading text.
9. Only use decision_type="{SegmentDecisionType.IGNORE.value}" if the heading is clearly page furniture (running header/footer, repeated publisher line, standalone page number).
10. If {BlockType.PARAGRAPH.value}/{BlockType.LIST.value} includes clear expectations: {SegmentDecisionType.EMIT_LEAVES_ONLY.value} (or {SegmentDecisionType.EMIT_GROUPINGS_AND_LEAVES.value} if it also contains a grouping label)
11. For heading blocks: put the heading itself in groupings[]; use context_groupings[] only for the outer context in section_path.

## CONFIDENCE
1. Provide confidence ∈ [0,1] where:
  - 0.80+: obvious mapping (clean competence/outcome rows, clear standards statements)
  - 0.60–0.79: reasonable but mild ambiguity
  - 0.30–0.59: ambiguous; likely unresolved
  - <0.30: unresolved
        """
    )

    # Keep the user message small and stable: include the segment JSON verbatim.
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

It's a good idea to carefully review your last output against the stated instructions and double-check your response.

In particular, ensure that:

1. **No hallucination/no invented content**
  - Every title/body string must be directly supported by the provided segment text/rows.
  - Do NOT introduce codes, topics, grades, or expectations that are not present.
  - Do NOT merge across pages or "continue" content from memory.
2. **Correct decision_type**
  - If the segment is mostly page furniture/numbering/artifact -> use decision_type="{SegmentDecisionType.IGNORE.value}".
  - If ambiguous or not clearly classifiable -> use decision_type="{SegmentDecisionType.UNRESOLVED.value}" and keep arrays empty.
  - Only emit groupings/leaves when you are confident the segment supports them.
3. **Correct use of roles**
  - Groupings MUST use NodeRole only.
  - Leaves MUST use StatementRole only:
    - {StatementRole.EXPECTATION.value} = normative learning outcome/competence/objective
    - {StatementRole.DESCRIPTOR.value} = benchmark/indicator/expected standard
    - {StatementRole.GUIDANCE.value} = activities/resources/teaching notes
  - If something looks like an activity/task, it is guidance (not expectation).
4. **Role sanity check:**
  - If you assigned role=SUBJECT to something that looks like a document title/preamble heading (mentions curriculum/syllabus/framework/ministry/national/education or is very long/all-caps), correct it to role=SECTION.
5. **Table discipline (if segment_kind="table")**
  - Prefer `rows[]` over top-level `leaves[]`.
  - Each RowDecision.row_index must be a valid 0-based ABSOLUTE index into the ORIGINAL stitched table rows.
    - If segment.chunking exists, every row_index must lie within [row_range_start, row_range_end) (end exclusive), and should match each row's abs_row_index when present.
  - Do NOT emit RowDecisions for header rows (row_index < segment.header_row_count).
  - Do NOT emit RowDecisions for blank/empty rows.
  - If you split a cell into multiple statements, ensure each LeafDecision is atomic and non-overlapping.
  - If headers imply roles (e.g., "Specific Competences", "Learning Activities", "Expected Standard"), map them correctly.
  - Ensure table OUTER context is in `context_groupings[]` (section_path/caption/headers), and row-local context is in `RowDecision.groupings[]` (topic/subtopic/code/week/etc).
  - If a row lists multiple same-level siblings (e.g., multiple subjects/topics in one cell), emit multiple RowDecisions with the SAME row_index, one per sibling; do not stack siblings into one groupings[] path.
  - **Hierarchy/coding discipline:**
    - If a row contains a higher-level container statement (e.g., a "Main/General" item) plus more specific sub-items (often indicated by hierarchical codes/list_ids like "1.1" and "1.1.1"), treat the higher-level item as a grouping in `RowDecision.groupings[]` and emit ONLY the more specific sub-items as leaf expectations.
    - Do NOT emit both the parent container and its child sub-items as leaf expectations in the same row.
6. **Block discipline (if segment_kind="block")**
  - If block_type is "{BlockType.CAPTION.value}": decision_type should usually be "{SegmentDecisionType.IGNORE.value}" (captions bind later).
  - If block_type is "{BlockType.HEADING.value}": emit groupings only if it clearly denotes a hierarchy container
     (subject/grade/theme/section). Otherwise ignore.
7. **Conservativeness + confidence calibration**
  - If you're not sure, mark unresolved with confidence < 0.6.
  - Only use confidence >= 0.85 when the mapping is obvious.
8. If decision_type is any emit_*:
  - `context_groupings[]` must be present and reflect the current hierarchy context based on evidence in the segment payload (especially section_path and caption_text).
  - The compiler will not create nodes from segment.section_path automatically.
9. **Grade label check:**
  - If role=GRADE_LEVEL contains extra narrative words beyond the grade/stage identifier, rewrite it so grade_level is only the grade/stage label and move the remaining phrase to role=SECTION/TOPIC.

When you are confident in your answer, return a complete `SegmentDecision` that matches the schema and fixes any issues you might've overlooked or incorrect assumptions you might've made.
        """
    )

    return DotMap(
        {"system_message": system_message, "user_message": user_message.strip()}
    )
