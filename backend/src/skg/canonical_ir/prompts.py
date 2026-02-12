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
    GROUPING_ROLES,
    BlockType,
    FrontMatterHeadings,
    NonArtifacts,
    SegmentDecisionType,
    StatementRole,
)

CONTEXT_GROUPINGS_ORDER_STR = " → ".join(r.name for r in CONTEXT_GROUPINGS_ROLE_ORDER)

# Build a de-duplicated, sorted list of document-structure words that should NOT become
# SECTION grouping nodes. Sourced from FrontMatterHeadings and NonArtifacts so the
# prompt stays in sync when either collection grows.
_SECTION_EXCLUSION_WORDS: list[str] = sorted(
    {h.value for h in FrontMatterHeadings}
    | NonArtifacts
    | {
        # Additional generic document words not covered by the enums above.
        "syllabus",
        "curriculum",
        "framework",
        "guide",
    }
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
        [f'  - "{r.value}"' for r in sorted(GROUPING_ROLES, key=lambda x: x.value)]
    )

    system_message = dedent(
        f"""You are an expert curriculum document parser producing **auditable, conservative** semantic decisions.

Your job: Given ONE segment from a stitched curriculum DocumentIR (either a BLOCK or TABLE), produce a **SegmentDecision** JSON object that a deterministic compiler will use to build a canonical standards hierarchy.

## 1. CORE PRINCIPLES
- NEVER INVENT CONTENT. Only use text/rows in the segment. Do not merge across pages or assume missing rows.
- DO NOT TRANSLATE. Preserve original language and casing.
- Preserve statement order as it appears in the segment.
- If uncertain, prefer "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" (with best-effort candidate outputs) over "{SegmentDecisionType.UNRESOLVED.value}" (empty, last resort).

## 2. DECISION-TYPE INVARIANTS
| decision_type | context_groupings | groupings/leaves/rows | notes |
|---|---|---|---|
| ignore | MUST be [] | MUST be [] | Page furniture, artifacts, captions, TOC content |
| unresolved | MUST be [] | MUST be [] | Cannot safely emit anything; explain in rationale |
| emit_flagged_unresolved | MAY be [] or non-empty | MUST emit ≥1 of groupings/leaves/rows | For human review; will NOT compile into final tree. Explain ambiguity in rationale. |
| emit_groupings_only | required (may be []) | groupings[] and/or rows[].groupings[] allowed; NO leaves anywhere | |
| emit_leaves_only | required (may be []) | segment-level groupings[] MUST be []; row-local RowDecision.groupings[] allowed | |
| emit_groupings_and_leaves | required (may be []) | MUST emit ≥1 grouping AND ≥1 leaf somewhere | |

## 3. ALLOWED ENUM VALUES
decision_type: {decision_types_str}

NodeRole (for groupings): {node_roles_str}
  - Do NOT use: "framework", "prose", "unresolved" as grouping roles.
  - role=SECTION is NOT a default fallback. Use only for meaningful curriculum labels not captured by other roles.
  - Do NOT emit SECTION for generic document words: {", ".join(f'"{w}"' for w in _SECTION_EXCLUSION_WORDS)}.

StatementRole (for leaves):
  - "{StatementRole.EXPECTATION.value}" — normative learning outcome/competence/objective/standard
  - "{StatementRole.DESCRIPTOR.value}" — benchmark/indicator/expected standard/performance criteria
  - "{StatementRole.GUIDANCE.value}" — learning activities, pedagogy, resources, notes. If it looks like an activity/task, it is guidance (not expectation).

LEARNING_AREA vs. SUBJECT:
  - LEARNING_AREA = broad umbrella (e.g., "Literacy and Language", "Mathematics and Science")
  - SUBJECT = specific syllabus subject (e.g., "English Language", "Mathematics")

## 4. CONTEXT GROUPINGS
The compiler WILL NOT create hierarchy from segment.section_path[]. You MUST provide the hierarchy context snapshot in context_groupings[] when supported by evidence.

**Ordering:** context_groupings[] MUST be ordered OUTER → INNER: {CONTEXT_GROUPINGS_ORDER_STR}
Do NOT repeat the same NodeRole. Do NOT include TOPIC/SUBTOPIC (those belong in RowDecision.groupings[] for tables).

**Evidence support:** Every context_groupings[].title MUST be supported by one of:
  A) OUTER EVIDENCE — appears in section_path[], caption_text, or header_rows_canonical.
  B) CARRY-FORWARD — prior_context_groupings[] contains the same role/title AND:
    1. Role is a stable outer role (STAGE, GRADE_LEVEL, LEARNING_AREA, SUBJECT, THEME, SUBTHEME, UNIT, WEEK, TERM, STRAND, SUBSTRAND)
    2. No contradiction in this segment's outer evidence
    3. Not governmental/institutional metadata
    If using carry-forward, mention it in rationale.
  If unsupported by (A) or (B), DELETE that grouping.

**Institutional metadata:** Do NOT include country name, ministry, publisher, or approving authority in context_groupings[]. Use empty context if no curriculum hierarchy is present.

**Outer anchor requirement:** If emitting any leaves (EXPECTATION/DESCRIPTOR/GUIDANCE), the decision MUST include ≥1 outer anchor (GRADE_LEVEL, STAGE, LEARNING_AREA, SUBJECT, THEME, SUBTHEME, UNIT, WEEK, TERM, STRAND, SUBSTRAND) in context_groupings[], groupings[], or rows[].groupings[]. If no anchor is supported by evidence, use "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}".

**Role depth ordering:** groupings[] are children under the context stack tip. Never emit a grouping whose role is OUTER than the deepest role in context_groupings[]. Fix by placing the outer role in context_groupings[] or emitting both in groupings[] in correct order.

**Grade label hygiene:** If role=GRADE_LEVEL title contains extra narrative words, trim it to just the grade label. Only emit an additional grouping for the remaining phrase if it is meaningful curriculum structure.

## 5. CHUNKED TABLES
If segment includes a `chunking` object:
  - ONLY decide on the rows in this chunk. Never assume rows outside this chunk.
  - Table-wide anchors MUST go in context_groupings[], NOT segment-level groupings[].
  - FIRST CHUNK: apply outer-evidence support strictly; DROP any unsupported prior grouping.
  - LATER CHUNKS: repeat context_groupings[] EXACTLY as prior_context_groupings[] unless this chunk's outer evidence explicitly contradicts it.
  - On contradiction in a later chunk: use "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" (emit candidate rows + explain contradiction). Do NOT change context mid-table.

## 6. TABLE-SPECIFIC GUIDANCE
- Prefer rows[] over top-level leaves[]. Put leaf statements in RowDecision.leaves[].
- row_index MUST be a 0-based ABSOLUTE index into the original stitched table. If abs_row_index is present, RowDecision.row_index MUST match it exactly.
- Do NOT emit RowDecisions for header rows, blank/empty rows, or rows where is_context_only=true.
- Split multiple statements within a cell into multiple LeafDecisions when clearly separable (bullets, numbering, line breaks).
- If headers suggest roles (e.g., "Specific Competences", "Expected Standard"), map accordingly.
- If a row contains a higher-level container + specific sub-statements: treat the container as RowDecision.groupings[] and emit ONLY sub-statements as RowDecision.leaves[]. Do NOT emit both as leaves.
- Sibling fanout: if a single row contains multiple sibling values (e.g., two Topics), emit multiple RowDecisions with the SAME row_index (one per sibling). No exact duplicate RowDecisions.
- If header_rows_canonical[0] is a single merged label (e.g., "WRITING"), treat it as strong outer evidence for a table-scoped grouping (often STRAND but may be SUBJECT/THEME). For chunked tables, place in context_groupings[]; for non-chunked, segment-level groupings[] is also allowed.
- For chunked tables, filled-down values in grouping columns are supported evidence (not hallucination).
- OUTER context (section_path/caption/header_rows-derived) → context_groupings[]. Row-local context (topic/subtopic/week) → RowDecision.groupings[].

## 7. BLOCK-SPECIFIC GUIDANCE
- "{BlockType.ARTIFACT.value}" or page-number-like → "{SegmentDecisionType.IGNORE.value}"
- "{BlockType.CAPTION.value}" → "{SegmentDecisionType.IGNORE.value}" (captions bind later)
- "{BlockType.HEADING.value}" → default to "{SegmentDecisionType.EMIT_GROUPINGS_ONLY.value}" (headings are structural containers). Ignore only if page furniture.
- If a heading mixes meaningful hierarchy + boilerplate document words, emit ONLY the meaningful label. Example: "Lower Primary Education Syllabi" → STAGE="Lower Primary", omit "Education Syllabi".
- Do NOT output block_type in SegmentDecision (set deterministically by pipeline).

## 8. PRIOR CONTEXT & CONTINUITY HINTS
- prior_context_groupings[]: use as starting hint for stable outer context (see carry-forward rules in §4).
- prev_segment_hint / next_segment_hint: use ONLY as continuity hints (does this segment continue or begin new context?). They are NOT outer evidence.
- Table of Contents suppression: if any heading in section_path indicates "Table of Contents"/"Contents", decision_type MUST be "{SegmentDecisionType.IGNORE.value}".

## 9. SOURCE LABELS
- Emit source_label on GroupingDecision/LeafDecision when possible.
- source_label MUST be copied VERBATIM from visible segment evidence (column headers preferred, or heading/section labels). Do NOT invent or paraphrase.
- If no explicit label exists, set source_label=null.

## 10. CODES vs. LIST MARKERS
- Official curriculum codes (e.g., "3.9.4.1") → LeafDecision.local_code
- Bullet/list markers (e.g., "a)", "•") → LeafDecision.list_marker
- NEVER put official codes into list_marker.

## 11. CONFIDENCE
- ≥0.75: obvious mapping (clean competence rows, clear standards)
- 0.50–0.74: mild ambiguity but likely resolvable
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
        f"""Review your last output against the instructions. Check these common failure modes:

1. **Hallucination check:** Is every title/body/code directly supported by the segment text? No invented content?
2. **Decision-type invariants:** Does your output satisfy the invariant table in §2? (e.g., ignore/unresolved → all arrays empty; emit_groupings_only → no leaves anywhere)
3. **Role sanity:** Did you assign SUBJECT/STRAND to something that is actually document metadata (mentions curriculum/syllabus/framework/ministry/publisher/TOC)? If so, change to ignore or omit.
4. **Outer anchor:** If emitting leaves, is there ≥1 outer anchor (GRADE_LEVEL/STAGE/SUBJECT/STRAND/etc.) in context_groupings[] or groupings[]? If not, use "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}".
5. **Context depth:** Are groupings[] all INNER relative to context_groupings[]? (No GRADE_LEVEL in groupings[] when SUBJECT is the deepest context role.)
6. **Table row_index:** If table, does every RowDecision.row_index match the row's abs_row_index? No RowDecisions for header/blank/context-only rows?
7. **Context evidence:** Is every context_groupings[].title supported by outer evidence (section_path/caption/header_rows) or valid carry-forward?

If any check fails, fix it and return the corrected SegmentDecision. Otherwise, return your original output unchanged.
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
- Map to an established canonical WHEN semantically equivalent AND the role matches exactly.
- Output title MUST match the canonical EXACTLY (string match).
- Only create NEW canonical nodes if the input cannot map to this list.

{formatted_keys}
        """
        )

    system_message = dedent(
        f"""You are canonicalizing curriculum grouping nodes globally for a single curriculum document.

Input: list of grouping candidates, each with role, title, optional local_code/source_label.
Output: GroupingCanonicalizationMap with items[] — EXACTLY one item per input, in the SAME ORDER.

## Allowed roles
{", ".join(allowed_roles)}

## Role precedence (outer → inner)
{precedence_str}

## Action semantics
| action | output | constraints |
|---|---|---|
| keep | [] (preferred) or [input] | No change needed |
| drop | [] | Remove this grouping entirely |
| replace | exactly 1 item | output[0].role MUST equal input.role; output MUST differ from input |
| split | ≥2 items | Ordered outer→inner per precedence; no duplicate outputs |

{context_str}

## Rules
1. Do NOT invent curriculum concepts not in the input.
2. Prefer minimal changes: whitespace/punctuation normalization, synonym folding. Avoid casing changes unless matching an established canonical.
3. REPLACE must not change role (validator enforces this). If output would be identical to input, use KEEP.
4. SPLIT only when the title clearly contains multiple groupings (e.g., "Grade 1 - Mathematics") AND each part is directly present as a substring.
5. If unsure, choose KEEP with lower confidence (0.6–0.8). Do NOT DROP uncertain items.
        """
    )

    user_message = dedent(
        f"""Canonicalize the following NEW grouping keys.

Input keys (JSON array):
```json
{json.dumps([k.model_dump(mode="json") for k in grouping_keys], ensure_ascii=False)}
```
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )


def heading_level_instructions(headings: list[dict[str, Any]]) -> DotMap:
    """Return the heading level instructions.

    Parameters
    ----------
    headings
        The unique headings to assign levels to.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        """You are a document structure analyst. You will be given a numbered list of section headings extracted (in order) from a curriculum document.

Your task: Assign each heading an integer **structural depth level** where:
- 1 = The broadest/highest-level container (e.g., "Chapter 1").
- Higher numbers = More deeply nested content (e.g., "1.1", "1.1.1").
- 0 = Front-matter, non-structural text, or long content erroneously detected as a heading.

Logic Rules:
1. SIBLINGS (same structural role, e.g., "Unit 1" and "Unit 2") MUST receive the SAME level.
2. CONTAINERS receive a LOWER (broader) level number than the headings they contain.
3. If a heading is actually just a sentence or paragraph content, mark it as level 0.
        """
    )

    lines: list[str] = []

    for i, h in enumerate(headings):
        # Collapse internal whitespace for readability.
        text = " ".join(h["text"].split())
        lines.append(f'{i}. "{text}"  (page {h["page_index"]})')

    user_message = "\n".join(lines)

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
