"""This module contains prompt templates for canonical Intermediate Representation (IR)
creation.
"""

# Standard Library
import json

from textwrap import dedent
from typing import Any

# Package Library
from skg.canonical_ir.schemas import GroupingCanonicalizationKey
from skg.utils.constants import (
    OUTER_ANCHOR_ROLES,
    BlockType,
    FrontMatterHeadings,
    NodeRole,
    NonArtifacts,
    SegmentDecisionType,
    StatementRole,
)
from skg.utils.general import PromptPair

# Build a mapping of document-specific heading patterns to fixed role assignments, to
# be included in the heading level instructions for relevant documents. The prompt will
# instruct the model to apply these roles when the patterns are observed, but only when
# consistent with the global structural rules (e.g., if a pattern is observed but it
# appears in a section of the document that looks like front matter, the model should
# ignore the pattern and assign level 0).
HEADING_LEVEL_CONTEXT: dict[str, str] = {
    "senegal": dedent(
        """This document is a Senegal primary mathematics curriculum for the Deuxième étape (CE1–CE2). It contains bilingual Wolof/French text and many planning tables organized by weeks and by "palier" (checkpoint).

Document-specific patterns (use when consistent with the observed heading sequence):
- Standalone container headings like "MATHÉMATIQUES", "DEUXIÈME ÉTAPE (CE1–CE2)", "PLANIFICATION DES APPRENTISSAGES", and "APPRENTISSAGES PONCTUELS" are structural (non-zero).
  - Even if these appear on the cover/front matter, treat them as document-identity headings and assign level 1 when they represent instructional scope (subject + stage/grade span).
- Special case: **Document identity headings** (subject + stage/grade span) are structural.
  - If a heading clearly encodes instructional scope (e.g., "Mathematics — Grade 1–3", "Mathématiques — Deuxième étape (CE1–CE2)", "Science — Primary Cycle"), assign a non-zero level (usually 1), even if it resembles a cover/title line.
  - Only assign level 0 if it is purely administrative metadata (publisher/ministry/edition/approval/copyright) with no instructional scope.
- Treat any heading containing “tolluwaay” (especially bilingual “tolluwaay <n> / étape <n>”) as a stage identity container and assign it level 1, even if it appears repeatedly.
- Domain/strand containers include headings containing: "ACTIVITÉS NUMÉRIQUES", "ACTIVITÉS GÉOMÉTRIQUES", "MESURE", "RÉSOLUTION DE PROBLÈMES" (sometimes combined, and sometimes bilingual with a "/" + Wolof label). Treat these as structural STRAND headings at a consistent level (non-zero).
- Headings like "Paliers du niveau CE1" / "Paliers du niveau CE2" are structural containers under the current strand (non-zero), grouping multiple palier statements.
- IMPORTANT: lines starting with "Jéego <number>:" or "PALIER <number>:" usually contain the FULL competency/expectation statement (not just a label). These should be treated as curriculum CONTENT if they appear in the heading list:
  - Assign level 0 so the downstream pipeline can process them as expectation content.
  - Only treat "PALIER <number>" as structural (non-zero) when it is a short label without the statement text (rare).
- Continuations like "(suite)" / "(yeggale)" are continuations and MUST keep the same level as the base heading.
- "Tableau <number>" / "Tableau ... — ..." / "Tableau de ..." are table captions. Prefer level 0 for these even if formatted like headings.
- "Semaine <number>" is usually a table-row label. If it appears as a true heading, treat it as a WEEK container nested under the current planification/unit context.
"""
    )
}

# Roles that are valid for GroupingDecision.role/context_groupings/groupings. Excludes
# FRAMEWORK (root), UNRESOLVED (error bucket), and PROSE (document furniture).
GROUPING_ROLES: tuple[NodeRole, ...] = tuple(
    r
    for r in NodeRole
    if r not in (NodeRole.FRAMEWORK, NodeRole.UNRESOLVED, NodeRole.PROSE)
)

# For the outer-anchor requirement: build a string listing the roles that are
# considered valid outer anchors for emitted leaves.
OUTER_ANCHOR_ROLES_STR = ", ".join(sorted(r.value for r in OUTER_ANCHOR_ROLES))

# Build a de-duplicated, sorted list of document-structure words that should NOT become
# SECTION grouping nodes. Sourced from FrontMatterHeadings and NonArtifacts so the
# prompt stays in sync when either collection grows.
SECTION_EXCLUSION_WORDS: list[str] = sorted(
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

# Build a mapping of document-specific SEGMENT DECISION hints to be included in the
# segment-decision instructions for relevant documents. These hints guide how to
# classify table cells into expectation/descriptor/guidance and how to refine context
# when headings embed grade or sequencing cues.
SEGMENT_DECISION_MAPPING: dict[str, str] = {
    "senegal": dedent(
        """This document is a Senegal primary mathematics curriculum with bilingual Wolof/French headings and many scope-and-sequence planning tables.

Segment-decision guidance (apply ONLY when supported by the current segment evidence):

A. Planning tables are often normative (Issue D):
- Tables labeled "Tableau de planification" and sections labeled "Apprentissages ponctuels" are usually describing WHAT learners should learn/do that week/palier.
- In these tables, prefer StatementRole=EXPECTATION for cells that describe learner outcomes/skills/knowledge (often terse competency phrases), even if the overall document feels like a planning guide.
- Use StatementRole=GUIDANCE only for true instructional actions or logistics:
  teacher actions/tasks (e.g., "faire...", "proposer...", "organiser...", "demander aux élèves...")
  materials/resources ("matériel", "supports"), duration/time ("durée"), pedagogy/method ("démarche"), evaluation instructions.
- Use StatementRole=DESCRIPTOR only for explicit performance criteria/benchmarks/checkpoints (rare in the week tables; more common near "paliers" summaries).

B. Senegal mixed-organization rule (IMPORTANT):
- SECTION anchoring from captions/headings (IMPORTANT for stability):
  - If caption_text or a nearby heading contains "Planification", set context_groupings.section="Planification des apprentissages".
  - If caption_text or a nearby heading contains "Apprentissages ponctuels", set context_groupings.section="Apprentissages ponctuels".
  - Do NOT use stage labels like "ÉTAPE (CE1-CE2)" as SECTION; they belong in role=STAGE.
- Wolof strand label rule:
  - If a standalone Wolof heading such as "caxu xayma" appears, treat it as STRAND reinforcement (Résolution de problèmes). Do NOT emit it as role=TOPIC or SUBTOPIC.

- For "Apprentissages ponctuels" tables (typically Tableaux 4–27): the document is STRAND-first. If the active section_path/headings/caption indicate a strand, ALWAYS include that STRAND as an OUTER context_groupings entry (stable for the table). Do not encode grade in the strand title (e.g., avoid "Activités numériques CE1/CE2" as a strand); keep grade_level separate and let paliers carry CE1 vs CE2 scope.
- For weekly "Planification" tables (typically Tableau 2–3): the document is GRADE-first. Treat the table as a grade-scoped UNIT (Planification CE1/CE2). Even if the caption contains multiple strands (e.g., "Activités numériques et Résolution de problèmes"), DO NOT set STRAND in context_groupings. Instead, represent strands row/column-locally using the column-header fanout pattern (STRICT — REQUIRED):
  - You MUST emit separate RowDecision entries per (row_index, strand column). That means:
    * RowDecision.col_index MUST be set to the strand column index (0-based in the ORIGINAL stitched table).
    * RowDecision.leaves MUST come ONLY from that column's cell for that row.
    * RowDecision.groupings MUST include exactly one strand grouping: {role:"strand", title:<canonical French column header>}.
  - DO NOT pack multiple strand leaves into one RowDecision with col_index=null and different source_label values. If you do not split by col_index, the decision is incorrect.
  - If a row has content under two strand columns, emit TWO RowDecision objects for the same row_index (different col_index), each with its own strand grouping.
  - If you cannot reliably map which cell belongs to which strand header, set decision_type="emit_unresolved" (or emit_flagged_unresolved if confidence is below threshold).
- When emitting STRAND in context_groupings, use the FRENCH form of the strand name
  (e.g., "Activités numériques"), not the bilingual heading form
  (e.g., "Activités numériques / Kenug xayma"). The bilingual form will be preserved
  in section_path provenance; context_groupings needs the canonical French form for
  spine correction alignment.

C. Column-header heuristics for Senegal planning tables:
- Headers implying EXPECTATION (normative content): "apprentissages", "objectifs", "compétence(s)", "habiletés", "capacités".
- NOTE: headers like "contenus" / "contenu" usually indicate DESCRIPTOR (coverage/topics) unless the cell is clearly phrased as a learner outcome (see §G).
- Headers implying GUIDANCE: "situations", "démarche", "méthode", "matériel", "durée", "évaluation", "ressources".
- IMPORTANT: strand-name column headers like "Activités numériques", "Activités géométriques", "Activités de mesure", "Activités Résolution de problèmes" are STRUCTURAL strand labels, NOT GUIDANCE indicators. Cells under these headers typically contain EXPECTATION statements (learning outcomes), not teacher activities. Do NOT classify them as GUIDANCE merely because the header contains the word "activités".
- Headers like "Semaine" / "Sem." and "Palier" are STRUCTURE, not leaves. Treat their values as row-local groupings (week/substage cues) rather than leaf statements.

D. Grade refinement from unit headings (Issue E):
- Prefer grade_level from a parent/outer heading like "Paliers du niveau CE1" / "... CE2".
- HOWEVER, if the current segment’s strongest visible evidence for grade is embedded in a UNIT heading/caption like "(niveau 1: CE1)" or "(niveau 2: CE2)", you SHOULD refine context_groupings.grade_level to that specific grade ("CE1" or "CE2") *while keeping the full UNIT title intact*.
  - Do NOT create a separate grade grouping from the inline "(niveau ...: CE...)" fragment.
  - If prior_context_groupings has a broader grade band (e.g., "CE1–CE2") and the unit heading clearly specifies CE1 or CE2, override/refine to the specific grade and note it in rationale.

E. Language markers are not hierarchy:
- "Mooñaale ci wolof" and similar language-of-instruction directives are prose labels, not structural groupings and not expectations.

F. Bilingual duplication rule for PALIER / JÉEGO (fixes duplicate expectations):
- "Jéego <number>" (Wolof) and "PALIER <number>" (French) are two language renderings of the SAME competency milestone.
- If BOTH languages appear within the SAME segment/table cell (e.g., separated by "/" or repeated lines), emit ONE EXPECTATION leaf only:
  - Prefer the French form as the primary phrasing, and include BOTH variants in the leaf.body with clear labels, e.g.:
    "Wolof: ...
French: ..."
  - Use a single substage grouping title "PALIER <number>".
- If a segment/cell appears to be Wolof-only "Jéego <number> ..." (no French wording present), do NOT emit it as an EXPECTATION by default. Emit it as DESCRIPTOR (translation text) under the SAME substage grouping title "PALIER <number>".
  - Exception: if the Wolof text is clearly the only available version in the current segment evidence (no nearby French version in the same cell/segment), you MAY emit it as EXPECTATION.

G. Column-specific guidance for apprentissages ponctuels tables (Tableaux 4–27):
- "Objectif d'apprentissage" column is a numeric learning-objective index (e.g., "4", "5") used as a stable local identifier for the rows that follow.
  - DO NOT create a grouping node (role=TOPIC) solely for this number.
  - Instead, propagate this identifier into LeafDecision.local_code for every emitted leaf tied to that objective group (use the printed number, optionally prefixed e.g., "OA-4" if helpful for clarity).
- "Objectif spécifique" column is the normative learner outcome for that objective group → EXPECTATION.
- "Contenus" column is usually a content/coverage list (topics, knowledge items, examples) → DESCRIPTOR by default.
  - Only treat "Contenus" as EXPECTATION if the cell is phrased as a learner capability/outcome (action verbs like "peut", "réaliser", "résoudre", "utiliser", or an explicit "L'élève ...").
- "Durée" column contains lesson/session logistics → GUIDANCE.
- If you emit both Objectif spécifique and Contenus for the same objective group, give them the SAME LeafDecision.local_code (the objective index) so they can be associated deterministically downstream.

H. Competency overview table (Tableau 1 — "Compétences de base par domaine d'activité"):
- This table has one column per strand with high-level competency descriptions. These are general competence statements repeated at finer granularity in the palier definitions.
- Treat as EXPECTATION if emitted. IGNORE is also acceptable since the palier-level tables provide the same content at finer granularity.
"""
    )
}


def decide_on_segment(
    *,
    country: str,
    context_groupings_role_dict: dict[NodeRole, int],
    heading_role_hints: list[dict[str, str]],
    outer_context_roles: list[Any] | None = None,
    segment: dict[str, Any],
    segment_decision_conf_threshold: float,
) -> PromptPair:
    """Generate the prompts for deciding on a segment.

    Parameters
    ----------
    country
        The country of the curriculum document, used to provide relevant
        context-specific hints.
    context_groupings_role_dict
        The current context groupings as a dict of NodeRole to index, used to provide
        hints about which roles are currently active in the context stack.
    heading_role_hints
        A list of dictionaries containing 'text' and 'role_hint' for each heading in
        the document, to be used as potential evidence for grouping roles.
    outer_context_roles
        A list of roles that are considered stable outer anchors for chunked tables,
        which must be placed in context_groupings[] rather than segment-level
        groupings[].
    segment
        The segment dictionary containing segment details.
    segment_decision_conf_threshold
        The confidence threshold for segment decisions, used to guide the model's
        choice between "obvious" and "ambiguous" outputs.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    decision_types_str = "\n".join(
        [f'  - "{t.value}"' for t in sorted(SegmentDecisionType, key=lambda x: x.value)]
    )
    hint_lines = "\n".join(
        f'  - {h.get("note") or h["pattern"]} → {h["role"]}' for h in heading_role_hints
    )
    node_roles_str = "\n".join(
        [f'  - "{r.value}"' for r in sorted(GROUPING_ROLES, key=lambda x: x.value)]
    )

    context_role_order = list(context_groupings_role_dict)
    context_order_str = (
        " → ".join(r.value for r in context_groupings_role_dict)
        if context_role_order
        else "(no precedence configured)"
    )
    ranked_roles_note = (
        ", ".join(r.value for r in context_role_order)
        if context_role_order
        else "(none; all roles treated as unranked)"
    )

    # For chunked tables: roles that MUST be expressed in context_groupings[] (not
    # segment-level groupings[]) so that all chunks share a stable outer context stack.
    # This list is policy/config-driven.
    outer_context_roles = outer_context_roles or []
    outer_context_roles_str = (
        ", ".join(
            sorted(
                [
                    getattr(r, "value", str(r))
                    for r in outer_context_roles
                    if r is not None
                ]
            )
        )
        if outer_context_roles
        else "(none configured)"
    )

    # Document-specific segment-decision hints (country-level).
    segment_guidance_block = ""
    segment_context = SEGMENT_DECISION_MAPPING.get(country.lower(), None)

    if segment_context and segment_context.strip():
        segment_guidance_block = dedent(
            f"""## 12. DOCUMENT-SPECIFIC SEGMENT DECISION HINTS
{segment_context.strip()}
"""
        ).strip()

    system_message = dedent(
        f"""You are an expert curriculum document parser producing **auditable, conservative** semantic decisions.

Your job: Given ONE segment from a stitched curriculum DocumentIR (either a BLOCK or TABLE), produce a **SegmentDecision** JSON object that a deterministic compiler will use to build a canonical standards hierarchy.

## 1. CORE PRINCIPLES
- NEVER INVENT CONTENT. Only use text/rows in the segment. Do not merge across pages or assume missing rows.
- DO NOT TRANSLATE. Preserve original language and casing.
- Preserve statement order as it appears in the segment.
- If uncertain but your confidence is ≥ the threshold value of {segment_decision_conf_threshold}, commit to the best proper emit type (emit_leaves_only/emit_groupings_only/emit_groupings_and_leaves). Note remaining uncertainties in rationale.
- Hard constraint: a proper emit_* decision that emits leaves MUST satisfy the outer-anchor rule in §4. If you cannot provide an outer anchor without inventing it, you MUST downgrade to "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" (set confidence < threshold) or "{SegmentDecisionType.UNRESOLVED.value}".
- Reserve "{SegmentDecisionType.UNRESOLVED.value}" for segments where you truly cannot extract anything useful.

## 2. DECISION-TYPE INVARIANTS
| decision_type | context_groupings | groupings/leaves/rows | notes |
|---|---|---|---|
| ignore | MUST be [] | MUST be [] | Page furniture, artifacts, captions, TOC content |
| unresolved | MUST be [] | MUST be [] | Cannot safely emit anything; explain in rationale |
| emit_flagged_unresolved | MAY be [] or non-empty | MUST emit ≥1 of groupings/leaves/rows | ONLY when confidence < {segment_decision_conf_threshold}. Use to surface audit-worthy ambiguity; keep rationale explicit. |
| emit_groupings_only | required (may be []) | groupings[] and/or rows[].groupings[] allowed; NO leaves anywhere | |
| emit_leaves_only | required (may be []) | segment-level groupings[] MUST be []; row-local RowDecision.groupings[] allowed | |
| emit_groupings_and_leaves | required (may be []) | MUST emit ≥1 grouping AND ≥1 leaf somewhere | |

## 3. ALLOWED ENUM VALUES
decision_type: {decision_types_str}

NodeRole (for groupings): {node_roles_str}
  - Do NOT use: "framework", "prose", "unresolved" as grouping roles.
  - role=SECTION is NOT a default fallback. Use only for meaningful curriculum labels not captured by other roles.
  - Do NOT emit SECTION for generic document words: {", ".join(f'"{w}"' for w in SECTION_EXCLUSION_WORDS)}.

StatementRole (for leaves):
  - "{StatementRole.EXPECTATION.value}" — normative learning outcome/competence/objective/standard
  - "{StatementRole.DESCRIPTOR.value}" — benchmark/indicator/expected standard/performance criteria
  - "{StatementRole.GUIDANCE.value}" — learning activities, pedagogy, resources, notes. If it looks like an activity/task, it is guidance (not expectation).

LEARNING_AREA vs. SUBJECT:
  - LEARNING_AREA = broad umbrella (e.g., "Literacy and Language", "Mathematics and Science")
  - SUBJECT = specific syllabus subject (e.g., "English Language", "Mathematics")

## 4. CONTEXT GROUPINGS
The compiler WILL NOT create hierarchy from segment.section_path[]. You MUST provide the hierarchy context snapshot in context_groupings[] when supported by evidence.

**Ordering:** context_groupings[] MUST be ordered OUTER → INNER for ranked roles: {context_order_str}
Only roles listed in the configured precedence are *ranked*: {ranked_roles_note}. Roles not listed are treated as unranked and will not be penalized by precedence-based validators.
Do NOT repeat the same NodeRole. Do NOT include TOPIC/SUBTOPIC (those belong in RowDecision.groupings[] for tables).

**Evidence support:** Every context_groupings[].title MUST be supported by one of:
  A) OUTER EVIDENCE — appears in section_path[], caption_text, or header_rows_canonical.
  B) CARRY-FORWARD — prior_context_groupings[] contains the same role/title AND:
    1. Role is a stable outer role ({OUTER_ANCHOR_ROLES_STR})
    2. No contradiction in this segment's outer evidence
    3. Not governmental/institutional metadata
    If using carry-forward, mention it in rationale.
  If unsupported by (A) or (B), DELETE that grouping.

**Institutional metadata:** Do NOT include country name, ministry, publisher, or approving authority in context_groupings[]. Use empty context if no curriculum hierarchy is present.

**Outer anchor requirement:** If emitting any leaves (EXPECTATION/DESCRIPTOR/GUIDANCE), the decision MUST include ≥1 outer anchor ({OUTER_ANCHOR_ROLES_STR}) in context_groupings[], groupings[], or rows[].groupings[]. If no anchor is directly in this segment's evidence, carry forward from prior_context_groupings[] (see carry-forward rules above).
If BOTH direct evidence and carry-forward are unavailable:
- NEVER emit leaves in a proper emit_* decision (it will be rejected for TABLE segments).
- Prefer "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" and set confidence < {segment_decision_conf_threshold:.2f} so the content is preserved for audit, and keep rationale explicit about what is missing/ambiguous.
- If you truly cannot emit anything without inventing an anchor (or the segment is pure noise), use "{SegmentDecisionType.UNRESOLVED.value}" or "{SegmentDecisionType.IGNORE.value}".

**Role depth ordering:** groupings[] are children under the context stack tip. Never emit a grouping whose role is OUTER than the deepest role in context_groupings[]. Fix by placing the outer role in context_groupings[] or emitting both in groupings[] in correct order.

**Grade label hygiene:** If role=GRADE_LEVEL title contains extra narrative words, trim it to just the grade label. Only emit an additional grouping for the remaining phrase if it is meaningful curriculum structure.

## 5. CHUNKED TABLES
If segment includes a `chunking` object:
  - ONLY decide on the rows in this chunk. Never assume rows outside this chunk.
  - For chunked tables, any grouping whose role is in the configured OUTER CONTEXT ROLES MUST be placed in context_groupings[] (NOT segment-level groupings[]) so all chunks share an identical context stack.
    OUTER CONTEXT ROLES for this run: {outer_context_roles_str}
  - FIRST CHUNK: apply outer-evidence support strictly; DROP any unsupported prior grouping.
  - FIRST CHUNK: if you cannot establish ANY outer anchor for emitted leaves without inventing it, use "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" (set confidence < threshold) rather than a proper emit_* decision.
  - LATER CHUNKS: repeat context_groupings[] EXACTLY as prior_context_groupings[] unless this chunk's outer evidence explicitly contradicts it.
  - On contradiction in a later chunk: use the context supported by THIS chunk's evidence (section_path, caption, header_rows), note the contradiction in rationale, and emit using the appropriate proper emit type. Do NOT change context mid-table without evidence.

## 6. TABLE-SPECIFIC GUIDANCE
- Prefer rows[] over top-level leaves[]. Put leaf statements in RowDecision.leaves[].
- row_index MUST be a 0-based ABSOLUTE index into the original stitched table. If abs_row_index is present, RowDecision.row_index MUST match it exactly.
- **Column-anchored fanout (multi-column rows):** If a single data row contains multiple independent statements in different columns (e.g., one strand per column), you have two safe options:
  A) **Preferred (structural):** emit multiple RowDecisions with the SAME row_index, **one per column**, and set `RowDecision.col_index` to the 0-based column index. In this case:
    - RowDecision.groupings[] MAY include a grouping derived from the **column header** (often role=STRAND/TOPIC/SECTION).
    - That grouping is grounded in `header_rows_canonical[*][col_index]`.
    - All RowDecision.leaves[] MUST come from that column’s cell text.
  B) **Minimal (no extra grouping):** emit a single RowDecision for the row with multiple leaves, and set each LeafDecision.source_label to the corresponding column header text.
- If you derive any row-local grouping from a column header, you MUST set RowDecision.col_index; otherwise the decision will be rejected as ungrounded.
- Do NOT emit RowDecisions for header rows, blank/empty rows, or rows where is_context_only=true.
- Split multiple statements within a cell into multiple LeafDecisions when clearly separable (bullets, numbering, line breaks).
- If headers suggest roles (e.g., "Specific Competences", "Expected Standard"), map accordingly.
- If a row contains a higher-level container + specific sub-statements: treat the container as RowDecision.groupings[] and emit ONLY sub-statements as RowDecision.leaves[]. Do NOT emit both as leaves.
- Sibling fanout: if a single row contains multiple sibling values (e.g., two Topics), emit multiple RowDecisions with the SAME row_index (one per sibling). No exact duplicate RowDecisions.
- If header_rows_canonical[0] is a single merged label (e.g., "WRITING"), treat it as strong outer evidence for a table-scoped grouping (often STRAND but may be SUBJECT/THEME). For chunked tables, place in context_groupings[]; for non-chunked, segment-level groupings[] is also allowed.
- Filled-down values in grouping columns are supported evidence (not hallucination), whether the table is chunked or not. The pipeline pre-fills repeated grouping cells so you can rely on them.
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
  Contents: kind, page_span, and either block_type + short text_preview (for blocks) or columns_signature + header_rows_canonical + n_cols + row_count (for tables). No section_path or row data.
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
This run’s low-confidence cutoff is: {segment_decision_conf_threshold:.2f}
- "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" is ONLY for decisions where your confidence is < {segment_decision_conf_threshold:.2f}. This is the SOLE trigger for emit_flagged_unresolved.
- If your confidence is >= {segment_decision_conf_threshold:.2f}, you MUST use a proper emit type (emit_leaves_only, emit_groupings_only, emit_groupings_and_leaves) or ignore/unresolved. NEVER use emit_flagged_unresolved when confidence >= {segment_decision_conf_threshold:.2f}.
- If the segment is clearly noise/furniture, use "{SegmentDecisionType.IGNORE.value}" even with high confidence.
- If you have semantic concerns (ambiguous structure, possible row wrapping, context contradiction) but your confidence is above threshold, commit to your best interpretation and explain concerns in rationale.

{segment_guidance_block}

## 13. DOCUMENT-SPECIFIC HEADING ROLE CONSTRAINTS
The following heading patterns have FIXED role assignments for this document.
When any section_path heading or caption matches a pattern below, you MUST use
the specified role in context_groupings[]. Do NOT assign a different role.

{hint_lines}
        """
    )

    segment_json = json.dumps(
        segment,
        ensure_ascii=False,
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    user_message = dedent(
        f"""Decide on this ONE segment and output a single SegmentDecision JSON object (JSON only, no markdown).

Segment JSON:

{segment_json}
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def double_check_decision_on_segment() -> PromptPair:
    """Generate the prompts for double-checking segment decision results.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    system_message = None
    user_message = dedent(
        f"""Review your last output against the instructions. Check these common failure modes:

1. **Hallucination check:** Is every title/body/code directly supported by the segment text? No invented content?
2. **Decision-type invariants:** Does your output satisfy the invariant table in §2? (e.g., ignore/unresolved → all arrays empty; emit_groupings_only → no leaves anywhere)
3. **Role sanity:** Did you assign SUBJECT/STRAND to something that is actually document metadata (mentions curriculum/syllabus/framework/ministry/publisher/TOC)? If so, change to ignore or omit.
4. **Outer anchor:** If emitting leaves, is there ≥1 outer anchor ({OUTER_ANCHOR_ROLES_STR}) in context_groupings[] or groupings[]? or rows[].groupings[]? If not, carry forward from prior_context_groupings[]. If you still cannot establish an outer anchor without inventing it, switch to "{SegmentDecisionType.EMIT_FLAGGED_UNRESOLVED.value}" (set confidence < threshold) or "{SegmentDecisionType.UNRESOLVED.value}" — do NOT emit leaves in a proper emit_* decision.
5. **Context depth:** Are groupings[] all INNER relative to context_groupings[]? (No GRADE_LEVEL in groupings[] when SUBJECT is the deepest context role.)
6. **Table row_index:** If table, does every RowDecision.row_index match the row's abs_row_index? If any RowDecision uses col_index, is it a valid 0-based table column and are its leaves grounded in that column’s cell text? No RowDecisions for header/blank/context-only rows?
7. **Context evidence:** Is every context_groupings[].title supported by outer evidence (section_path/caption/header_rows) or valid carry-forward?
8. **Planning-table role sanity:** If the segment is a scope-and-sequence or planning table, did you incorrectly label *all* (or nearly all) row leaves as GUIDANCE? Re-evaluate: cells describing learner outcomes, skills, or knowledge are EXPECTATION even in planning tables; reserve GUIDANCE for teacher actions, materials, duration/logistics, and pedagogy. Also check: are strand-name column headers (e.g., "Activités numériques") being confused for activity/guidance indicators? Cells under strand-label headers typically contain EXPECTATION content, not teacher activities.
9. **Grade refinement sanity:** If a UNIT heading or caption contains an embedded grade indicator (e.g., "(Grade 3)", "(Year 5)", "(niveau 1: CE1)") and the current grade_level context is missing or broader, refine context_groupings.grade_level to the specific grade while keeping the UNIT title intact (do not create a separate grade node from the inline fragment).

If any check fails, fix it and return the corrected SegmentDecision. Otherwise, return your original output unchanged.
        """
    )

    return PromptPair(system_message=system_message, user_message=user_message.strip())


def grouping_canonicalization_instructions(
    *,
    context_groupings_role_dict: dict[NodeRole, int],
    grouping_keys: list[GroupingCanonicalizationKey],
    known_canonical_keys: list[dict[str, str]] | None = None,
) -> PromptPair:
    """Return the grouping canonicalization instructions.

    Parameters
    ----------
    context_groupings_role_dict
        The current context groupings as a dict of NodeRole to index, used to determine
        allowed roles and precedence for canonicalization.
    grouping_keys
        The list of GroupingCanonicalizationKey objects to be canonicalized.
    known_canonical_keys
        Optional list of {'role': str, 'title': str} representing canonical
        standards established in previous batches.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    # Build allowed roles + precedence strings.
    ranked_roles = [r.value for r in context_groupings_role_dict]

    # Allowed roles are all grouping container roles.
    allowed_roles = [r.value for r in sorted(GROUPING_ROLES, key=lambda x: x.value)]

    # Precedence is only defined for the configured ranked roles.
    precedence_str = (
        " > ".join(ranked_roles) if ranked_roles else "(no precedence configured)"
    )

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
2. Prefer minimal changes, but DO normalize superficial formatting when meaning is unchanged: whitespace, punctuation, and minor accent/case variants. For ALL-CAPS titles, normalize to sentence case (capitalize only the first word and proper nouns), not Title Case — this is correct for French, Wolof, and most non-English languages.
3. For bilingual titles separated by '/', treat variants as equivalent when they share a clear common French (or English) substring. Prefer the most informative form already present in inputs (often the bilingual form) as the canonical, and REPLACE monolingual variants to that form.
4. REPLACE must not change role (validator enforces this). If output would be identical to input, use KEEP.
5. SPLIT only when the title clearly contains multiple groupings (e.g., "Grade 1 - Mathematics") AND each part is directly present as a substring.
6. Do NOT SPLIT a title when the resulting sub-grouping already exists as a separate key in this batch with the same role. For example, if section:"Apprentissages ponctuels — Activités numériques" is a key AND strand:"Activités numériques" is also a separate key, prefer KEEP over SPLIT.
7. If unsure, choose KEEP with lower confidence (0.6–0.8). Do NOT DROP uncertain items. However, do NOT use KEEP merely because of casing, accents, or bilingual ordering differences—use REPLACE when semantically equivalent and role matches.
        """
    )

    input_keys = json.dumps(
        [k.model_dump(mode="json") for k in grouping_keys],
        ensure_ascii=False,
        separators=(",", ":"),  # Remove spaces after commas/colons
    )

    user_message = dedent(
        f"""Canonicalize the following NEW grouping keys.

Input keys (JSON array):
```json
{input_keys}
```
        """
    )

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )


def heading_level_instructions(
    *,
    country: str,
    headings: list[dict[str, Any]],
    include_neighbor_context: bool = True,
) -> PromptPair:
    """Return the heading level instructions.

    Parameters
    ----------
    country
        The country of the curriculum document, used to provide relevant
        context-specific hints.
    headings
        The unique headings to assign levels to.
    include_neighbor_context
        Whether to include prev/next heading text in each line to reduce reset mistakes.

    Returns
    -------
    PromptPair
        A PromptPair containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        """You are a document structure analyst. You will be given a numbered list of headings extracted IN ORDER from a document (with page numbers).

Task: assign each heading an integer structural depth level:
- 0 = front matter/document furniture OR curriculum-content mistakenly detected as a heading that we want processed downstream as CONTENT (not hierarchy)
- 1 = broadest/highest-level structural container in the body
- 2+ = deeper nesting

Hard rules:
1. Siblings (same structural role/pattern) MUST have the same level.
2. Containers MUST have a lower level number than the headings they contain.
3. Avoid artificial resets: do NOT jump to a broader level (smaller number) unless the heading clearly starts a new major section.
4. Continuations: headings containing continuation markers like "(suite)", "(continued)", "(part ...)" must match the base heading’s level.
5. Level 0 is ONLY for non-structural text OR curriculum-content misdetected as a heading that we want processed downstream as CONTENT (not hierarchy).
   Examples: long competency/objective sentences mistakenly extracted as headings, table/figure captions, language-of-instruction directives, or document metadata like edition/publisher lines.
   Do NOT assign 0 merely because a heading is long; assign 0 only when it is clearly not a structural container/label.
6. If a heading appears between two clearly structural headings, it MUST receive a non-zero level consistent with that neighborhood
   EXCEPT when it is clearly (a) a table/figure caption (e.g., "Tableau 3 — ...", "Table 3:", "Figure 2:"), or (b) a competency/objective sentence (rule 5). In those cases, assign level 0.
7. Important: the heading list may be DEDUPED; [prev]/[next] neighbors are only weak hints. Do not overfit to them if they conflict with global consistency rules.
        """
    )

    heading_level_context = HEADING_LEVEL_CONTEXT.get(country.lower(), None)

    if heading_level_context and heading_level_context.strip():
        system_message += (
            "\n\n"
            + dedent(
                f"""Document-specific heading level hints (optional):

{heading_level_context.strip()}

Use these hints only when consistent with the hard rules above.
If a hint conflicts with the document’s observed structure, ignore the hint.
            """
            ).strip()
        )

    lines: list[str] = []

    for i, h in enumerate(headings):
        text = " ".join(h["text"].split())

        if include_neighbor_context:
            prev_text = " ".join(h.get("prev_text", "").split()) or (
                " ".join(headings[i - 1]["text"].split()) if i > 0 else ""
            )
            next_text = " ".join(h.get("next_text", "").split()) or (
                " ".join(headings[i + 1]["text"].split())
                if i + 1 < len(headings)
                else ""
            )
            lines.append(
                f'{i}. "{text}" (page {h["page_index"]}) '
                f'[prev: "{prev_text}"] [next: "{next_text}"]'
            )
        else:
            lines.append(f'{i}. "{text}"  (page {h["page_index"]})')

    user_message = "\n".join(lines)

    return PromptPair(
        system_message=system_message.strip(), user_message=user_message.strip()
    )
