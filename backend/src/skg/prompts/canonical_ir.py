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

## JSON OUTPUT STRUCTURE (CRITICAL)
You must strictly adhere to these field names:

1. **SegmentDecision** (Top Level):
   - `decision_type`: Enum value (see below).
   - `rationale`: String explaining your logic.
   - `confidence`: Float [0.0 - 1.0].
   - `context_groupings`: List of GroupingDecision objects (outer context).
   - `groupings`: List of GroupingDecision objects (segment-level).
   - `leaves`: List of LeafDecision objects (segment-level).
   - `rows`: List of RowDecision objects (table-level).

2. **GroupingDecision** (for containers/headers):
   - `role`: NodeRole Enum.
   - `title`: String (the heading text).
   - `list_id`: String (optional code, e.g., "1.2", "Unit A").

3. **LeafDecision** (for atomic standards):
   - `role`: StatementRole Enum.
   - `body`: String (the expectation text).
   - `list_id`: String (optional code/bullet, e.g., "a)", "3.1.1").

4. **RowDecision** (for table rows):
   - `row_index`: Integer (must match input `abs_row_index` exactly).
   - `groupings`: List of GroupingDecision objects (row-local context).
   - `leaves`: List of LeafDecision objects (row-specific content).

## CRITICAL RULES
1. **NEVER INVENT CONTENT**: Use only provided text. If blank, emit nothing.
2. **BE CONSERVATIVE**: If uncertain, use decision_type="{SegmentDecisionType.UNRESOLVED.value}".
3. **DO NOT TRANSLATE**: Preserve original language and casing.
4. **MUTUALLY EXCLUSIVE OUTPUTS** (Schema Enforcement):
   - If using `rows[]` (tables), `leaves[]` MUST be empty. Never mix top-level leaves with rows.
   - If decision_type="{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}", ALL `leaves` arrays must be empty.
   - If decision_type="{SegmentDecisionType.EMIT_LEAVES_ONLY.value}", top-level `groupings` must be empty (use row-level `groupings` instead).
5. **CONTEXT & HIERARCHY**:
   - `context_groupings[]` is REQUIRED for all non-ignore decisions.
   - It represents the **stable outer context** (Snapshot) derived from `section_path`, `caption_text`, or headers.
   - Do NOT put row-varying data here.

## DECISION TYPES
{decision_types_str}

## ALLOWED ROLES
**NodeRole (Groupings):**
{node_roles_str}
*Use `role="{NodeRole.SECTION.value}"` if specific role is unclear.*

**StatementRole (Leaves):**
  - "{StatementRole.EXPECTATION.value}": Normative outcomes ("Student can...", "Learners should...").
  - "{StatementRole.DESCRIPTOR.value}": Assessment criteria/benchmarks.
  - "{StatementRole.GUIDANCE.value}": Activities, teacher notes, resources.

## TABLE PARSING INSTRUCTIONS
1. **Header Rows**: Do NOT emit RowDecisions for headers. Use header text ONLY to infer roles for body cells.
2. **Row Index**: You must use the `abs_row_index` provided in the input as your `RowDecision.row_index`.
3. **Multi-valued Cells**: If a cell contains multiple distinct items (e.g., a list of topics), emit multiple `RowDecision` objects with the SAME `row_index` (one for each item).
4. **Hierarchy in Tables**:
   - If a row has a generic container (e.g., "Competence 1") AND specific items (e.g., "1.1 ...", "1.2 ..."):
   - Put the container in `RowDecision.groupings[]`.
   - Put the specific items in `RowDecision.leaves[]`.

## CAPTIONS & BLOCK TYPES
1. **Captions**: Use `caption_text` (if present) to inform `context_groupings`, but DO NOT emit the caption text itself as a node.
2. **Block Types**:
   - If block_type="{BlockType.HEADING.value}": Usually `EMIT_GROUPINGS_ONLY`.
   - If block_type="{BlockType.ARTIFACT.value}" or page numbers: `IGNORE`.

## CONFIDENCE
  - 0.80+: Obvious mapping.
  - 0.60–0.79: Reasonable.
  - <0.60: Unresolved.
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
