---
name: teacher-guide
description: Generate a Senegal CE1/Grade 3 reading teacher guide for a given semaine, following the team's source-of-truth hierarchy across edu-kg, the Wolof progression doc, and the prior teacher guide example. Use when the user asks to produce a teacher guide for a specific semaine.
argument-hint: [semaine-number]
arguments: [semaine]
allowed-tools: Read Glob Bash(python3 *) Bash(pip install python-docx) Bash(mkdir -p *)
---

Generate the complete teacher guide for **Semaine $semaine** as a DOCX, covering Jour 1 through Jour 5.

## Paths

All paths below are anchored to the skill directory via `${CLAUDE_SKILL_DIR}`, which the skill engine substitutes with the absolute path at load time. The `../../..` segments walk up to the project root (the directory containing `.claude/`) and resolve naturally when used as file paths. This works regardless of which directory you launched Claude Code from. Do **not** rewrite these as CWD-relative paths.

| File / directory | Path                                                                                                                                                      | Role                                                                   |
|---|-----------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| Week-specific instructions | `${CLAUDE_SKILL_DIR}/../../../examples/teacher_guide_generation_instructions/semaine<N>_teacher_guide_instructions_concise_v2.md` where `<N>` is $semaine | Spec for this week — read in full first                                |
| Progression document | `${CLAUDE_SKILL_DIR}/../../../examples/refs/reading_wolof_progression.docx`                                                                               | Exact L1 language-tool content using Wolof-native conjugation models   |
| Prior teacher guide example | `${CLAUDE_SKILL_DIR}/../../../examples/refs/reading_teacher_guide_grade_3_lesson_1_to_8.docx`                                                             | Format, tone, classroom-script style                                   |
| `edu-kg` MCP server | (connected)                                                                                                                                               | Palier framing, KG-governed sessions, learning-objective decomposition |
| Output | `${CLAUDE_SKILL_DIR}/../../../results/semaine<N>_teacher_guide.docx`                                                                                      | Generated DOCX (create the `results/` dir if needed)                   |

## Source-of-truth hierarchy

Follow §2 of the instructions doc strictly. Summary, in priority order:

1. **Session placement, order, duration, language scope** → embedded MEN timetable in the instructions doc.
2. **Palier framing + KG-governed sessions** (oral expression, comprehension, recitation, Production d'Écrits, Écriture, broad competencies) → `edu-kg`.
3. **Exact L1 language-tool content** for this week (vocab / grammar / conjugation / orthography) → progression doc. Its Wolof-native conjugation models (*nettali*, *dégtal*, *wonale*, etc., crossed with *sotti / sotteedi* aspect and *teew / weesu* time) are not in the KG.
4. **Exact L2 language-tool content** → progression doc for week assignment, `edu-kg` for objective decomposition.
5. **Format, tone, classroom-script layout** → prior teacher guide example.

When the KG and progression doc diverge for L1 language tools, prefer the progression doc and flag the divergence in the rationale (per §2b of the instructions doc — the linguistic-framework reason matters).

## Reading inputs

Install python-docx if not present (`pip install python-docx`). Use it to read both DOCX references.

Read the instructions doc in full first — it defines the content map, timetable, format rules, transfer requirements, concision targets, and QA checklist.

The example guide is large. Don't try to load it as a single text blob; sample 5–10 sessions across different session types (oral expression, comprehension, vocabulary, grammar, conjugation, poésie-récitation) to confirm the recurring layout, then reproduce. Inspect its named styles directly with python-docx (`Document(...).styles`) so the generated guide can reuse them rather than redefining its own.

## Workflow

1. **Plan all 22 sessions before writing.** Cross-reference §4 (content map) and §5 (timetable) of the instructions doc. For each of the 22 sessions, identify which source governs it per the hierarchy above. The Vocabulaire L2 row appears twice and must be treated as two distinct sessions.
2. **Query edu-kg for KG-governed sessions.** Pull session objectives via `get_learning_components_for_standard`. Validate week-grouping by checking that $semaine appears in the parent chain (some KG standards have parent nodes whose descriptions are week numbers). Use `get_progression` for `buildsFrom` / `buildsTowards` links when the Palier framing or transfer activities (§3, §10) need curriculum-grounded sequencing rationale.
3. **Disambiguate KG duplicates.** When multiple KG standards share the same or near-identical description (e.g., "Conjuguer au présent de l'indicatif" appears in both Palier 1 and Palier 2), prefer the instance whose parent chain includes a Palier 2 grouping or the week-$semaine grouping node.
4. **Generate the DOCX** at the output path above using python-docx. Match the example's structure:
   - Bilingual session headers (Wolof name / French name)
   - Paired teacher/student column tables — row symmetry is required
   - ARED 5-phase rows for content-rich sessions; compress Découverte/Structuration for procedural sessions (Poésie-Récitation, Écriture, Identification des Mots Fréquents, Fluidité, Remédiation CGP) per §6
   - Wolof text in the named character style used by the example (consistent dark blue)
   - Italics for teacher stage directions, regular text for teacher speech to pupils
   - Framed boxes for rules/patterns
   - 🔁 marker on every explicit transfer activity per §10
   - Open with the short timetable validation table covering all 22 sessions
5. **QA against §17** of the instructions doc before delivering. The checklist is the acceptance criteria.
6. **Print a brief rationale** to chat covering the four points required by §18: source hierarchy followed; KG/progression L1 divergence flagged with the linguistic-framework reason; model-week status (officially confirmed vs. "model-week-level" because it opens a palier); and any missing-source limitations.

## Concision targets

22–28 DOCX pages. Default to ~1 page per 30-minute session. Up to 2 pages allowed only for rule-heavy sessions or the 60-minute CGP remediation. A 50-page guide is a failure of concision — cut meta-commentary, repeated palier framing, "this phase is important because…" notes, and instructions restated as both a stage direction and a quoted teacher line.

## Output

- DOCX at the output path above.
- Rationale paragraph in chat (per §18 of the instructions doc).
