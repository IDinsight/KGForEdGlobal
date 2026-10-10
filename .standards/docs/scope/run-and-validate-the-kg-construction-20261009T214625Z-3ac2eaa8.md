<!-- STANDARDS
Artifact: SCOPE
Cycle: run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8
-->

# KG Construction on the CAPS English Home Language R-3 PDF

## Goal

The previous cycle
(`add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8`) produced
a DocumentIR for the South Africa CAPS English Home Language Grades R-3 PDF
(Funda Wande) covering page_index 35-134 (PDF 36-135), which the user has
verified. It also wrote the CAPS `kgs` config block, but never ran
`create_kgs`, so that block has only been checked by config loading and
review.

This cycle runs KG construction on that DocumentIR: the Academic Standards (AS),
Learning Components (LC) and Learning Progressions (LP) graphs. Where the run or
its review shows a defect, the CAPS config or the generic KG pipeline code is
fixed and the affected stages are rerun. The result is a CAPS KG whose AS
hierarchy, LCs and LP relationships match the curriculum, plus a recorded
review of what it contains.

The KG must capture these concepts the way the Learning Commons knowledge graph
defines them (user, 2026-10-09). In those terms, an AS item is either a
statement (Standard) or a structural element of the framework (Standard
Grouping); an LC is a single, well-defined skill or concept that supports a
standard; and buildsTowards and relatesTo link standards into progressions. For
CAPS this means keeping the sub-headings printed inside each table, such as
Phonics or Shared Reading, as their own grouping level.

## Constraints

- Inputs are the user-verified outputs in `results/kg_for_ed/<doc_key>/`
  (`extraction/`, `verification/`, `stitching/document_ir.json`). Page IR
  extraction, verification and stitching are not rerun (user, 2026-10-09).
- The user changed the committed CAPS config so both page IR stages use
  `output_dir` `results/kg_for_ed` with `start_page` 35 and `end_page` 135,
  matching the runs that produced those outputs (user, 2026-10-09; committed
  in `7ccfb3a`).
- Curriculum details belong in runtime config. Backend source and test code
  added or changed in this cycle must not name CAPS, Funda Wande, South Africa,
  any other curriculum, or curriculum-specific text. This carries over the
  previous cycle's constraint and the project's design principle.
- CAPS-specific config changes go only in
  `examples/funda_wande/config_english_curriculum.json`.
- Review is staged. The user reviews the AS results before LC and LP run, and
  stops the run between AS and LC with a temporary local `exit()`. That stop is
  not a permanent code change and is never committed (user, 2026-10-09).
- No cost limit applies (user, 2026-10-09). KG runs on the CAPS DocumentIR,
  including reruns after fixes, call paid LLM APIs and are in scope.
- AS, LC and LP follow the Learning Commons knowledge graph definitions
  (https://docs.learningcommons.org/knowledge-graph/understanding-knowledge-graph/introduction
  and the schema reference pages it links) (user, 2026-10-09).
- LP Term order is enforced only through the CAPS config's LP prompt
  instructions; KG pipeline code is not changed for it. A backward-Term
  buildsTowards edge is fixed by tightening those instructions and rerunning LP
  (user decisions, 2026-10-09).
- `kgs.lc.lc_max_failure_rate` stays at its default of 0.05. Zero failed LC
  requests (`AC-015`) is reached by rerunning the failed requests (user
  decision, 2026-10-09).
- Run configs keep rejecting unknown keys (`BaseSchema`, `extra="forbid"`).
- KG outputs, the PDF and the DocumentIR stay local and git-ignored. They are
  not committed.

## Non-goals

- Rerunning or changing page IR extraction, page IR verification or Document IR
  stitching, including their code.
- Editing the Ghana, India, Nigeria or Rwanda example configs, or running KG
  construction on them.
- Running the LC and LP evaluation CLIs (`evaluate_lcs.py`, `evaluate_lps.py`).
  They are outside the pipeline and were not requested.
- Adding a permanent stop point or stage selector to `create_kgs`.
- Committing KG outputs.

## Work

### 1. KG run inputs

**Intent:** KG construction must read the user-verified DocumentIR, not an
earlier trial output, and must not redo the page stages.

**Done when:**

- `AC-001`: The committed CAPS config sets `output_dir` to
  `results/kg_for_ed` and `start_page` 35 / `end_page` 135 in both the page IR
  extraction and page IR verification stages, and the full config loads as a
  valid run config.
- `AC-002`: The KG runs read
  `results/kg_for_ed/<doc_key>/stitching/document_ir.json`, and the
  `extraction/`, `verification/` and `stitching/` outputs under that directory
  are unchanged by them.

**Depends on:** None.

### 2. Academic Standards graph

**Intent:** Build the CAPS AS graph as Grade > Term > Skill Area > Sub-strand >
Skill, with one Skill per bulleted learner statement in the REQUIREMENTS PER
TERM tables. A sub-strand is a heading CAPS prints inside a table to group its
bullets, such as Phonics or Shared Reading. CAPS divides its requirements this
way itself (PDF 16), and Learning Commons models such structural elements as
Standard Groupings (user decision, 2026-10-09). AS is the base for LC and LP.
The conditions below are strict: a miss is fixed and AS rerun, not just
reported (user decision, 2026-10-09).

**Sub-strands per table**, from the PDF. Printed wording varies; for example,
Phonics appears under four different phonic-activity headings.

- Grade R, Listening and Speaking (Oral), every term: Daily activities in all
  areas of Language and other subjects; Uses language to develop concepts;
  Uses language to think and reason; Uses language to investigate and explore;
  Processes information; Uses visual and pictorial cues to make meaning.
- Grade R, Emergent Reading: Emergent reading skills, Shared Reading,
  Independent Reading and Phonological/Phonemic Awareness every term; Begins to
  make meaning of written text in Term 1; Relates sounds to letters and words
  in Terms 1-3.
- Grade R, Emergent Writing, every term: Emergent Handwriting (printed as
  Handwriting in Term 2); Emergent Writing; Works with words.
- Grades 1-3, Listening and Speaking (Oral), every term: Daily / Weekly
  activities in all areas of Language and other subjects; Twice weekly
  focussed listening and speaking activities.
- Grades 1-3, Reading and Phonics, every term: Phonics; Shared Reading; Group
  Guided Reading; Paired/Independent Reading. Grade 1 Term 1 has Emergent
  reading skills instead of Paired/Independent Reading.
- Grades 1-3, Writing, every term: Handwriting; Shared, Group and Independent
  Writing.

That is 152 sub-strands across the 48 tables.

**Done when:**

- `AC-003`: The AS phase completes on the CAPS DocumentIR with the
  committed config, and `as_validation_report.json` passes with no errors.
- `AC-004`: The AS graph has exactly four Grade items (Grade R, Grade 1,
  Grade 2, Grade 3), each a direct child of the framework root. Each Grade has
  exactly four Term children (Term 1 to Term 4). Each Term has exactly three
  Skill Area children: Listening and Speaking (Oral), Emergent Reading and
  Emergent Writing for Grade R; Listening and Speaking (Oral), Reading and
  Phonics, and Writing for Grades 1-3.
- `AC-025`: Each Skill Area has exactly the sub-strands listed above for its
  Grade and Term as its direct children, 152 in all.
- `AC-005`: Every bulleted learner statement in the
  CONTENT/CONCEPTS/SKILLS body rows of the 48 REQUIREMENTS PER TERM tables,
  outside ASSESSMENT rows, is represented by a Skill whose text keeps the full
  statement, including its examples and bracketed elaborations. No printed
  statement yields more than one Skill.
- `AC-026`: Each of the two lead-in bullets, 'Develops basic concepts of
  print including:' (Grade 1 Term 1, Reading and Phonics) and the bullet ending
  'different spelling choices such as' (Grade 3 Term 2, Reading and Phonics),
  forms one Skill together with the fragment bullets that complete it. Those
  fragments are not separate Skills.
- `AC-027`: Every Skill is a direct child of the sub-strand printed above
  it in its source table, under the Skill Area, Term and Grade given by that
  table's header rows. Continuation rows keep the header rows of the table they
  continue and the sub-strand in force at the page break.
- `AC-007`: No AS item attaches to the framework root by fallback,
  and no hasChild resolution in the final AS results is unresolved.
- `AC-008`: No Skill comes from ASSESSMENT rows, contact-time text,
  column labels, week or scheduling labels, list-introducing sub-labels that
  are not learner statements, teacher guidance, running headers, the
  resources boxes, or section headings.
- `AC-028`: Headings nested inside a sub-strand (week ranges such as
  'Weeks 1 - 5', and Handwriting sub-headings such as 'Development of letter
  formation in formal handwriting lessons', 'Maintenance of the print script'
  and 'Transition to a joined script or cursive writing'), headings that only
  group sub-strands ('Reading' in Grade R, 'Daily Reading Activities' in Grades
  1-3), schedules and teacher notes do not become AS items.
- `AC-029`: Skills with the same wording under different Grades, Terms,
  Skill Areas or sub-strands stay separate Skills. 'Recognises own name and
  names of at least five other children in the class', printed under both
  Emergent reading skills and Begins to make meaning of written text in Grade R
  Term 1, is two Skills.
- `AC-010`: The AS export maps Grade R to Learning Commons grade K and
  Grades 1, 2 and 3 to grades 1, 2 and 3, and every Skill in the export
  carries the grade of its Grade.
- `AC-030`: Grade, Term, Skill Area and sub-strand items have the Learning
  Commons normalized statement type Standard Grouping, and Skills have
  Standard.

**Depends on:** Work item 1.

### 3. Staged user review of the AS results

**Intent:** The user checks AS before LC and LP spend LLM calls building on it.

**Done when:**

- `AC-011`: Before LC and LP run, the user has reviewed the AS results
  and confirmed them, and that confirmation is recorded together with the
  identity of the AS results reviewed. Defects the user finds are corrected,
  and AS rerun, before the confirmation.
- `AC-012`: LC and LP build on the AS results the user confirmed: the
  same SFI identities, hasChild edges and AS export content.
- `AC-013`: Committed code contains no stop or exit added for the staged
  review, and `create_kgs` still runs AS, LC and LP in order in one invocation.

**Depends on:** Work item 2.

### 4. Learning Components

**Intent:** Break each CAPS Skill into atomic learner skills (LCs) supported
by the Skill's own text.

**Done when:**

- `AC-014`: The LC phase completes, and `as_lc_validation_report.json`
  passes with no errors.
- `AC-015`: The final LC results have no failed generation requests,
  and every Skill is supported by at least one LC (user decision, 2026-10-09).
- `AC-016`: Each LC states a learner skill supported by the text of
  the Skills it supports. No LC is teacher guidance, a classroom routine, an
  assessment suggestion, contact time or a scheduling label.
- `AC-031`: Each LC is a single, well-defined skill or concept, as Learning
  Commons defines a Learning Component, and has a supports relationship to
  every Skill it serves.

**Depends on:** Work item 3.

### 5. Learning Progressions

**Intent:** Relate CAPS Skills across the Grade R to Grade 3 sequence, with
Term 1 to Term 4 as the order within a grade.

**Done when:**

- `AC-017`: The LP phase completes, writes the merged AS+LC+LP export, and
  `lp_validation_report.json` passes with no errors.
- `AC-018`: No buildsTowards edge goes from a later Grade to an earlier
  Grade, or from a later Term to an earlier Term of the same Grade (user
  decision, 2026-10-09).
- `AC-032`: buildsTowards edges appear only where proficiency in the source
  Skill supports success in the target Skill, and relatesTo edges only where
  two Skills share a meaningful conceptual or skill link without sequence or
  dependency, as Learning Commons defines these relationships.

**Depends on:** Work item 4.

### 6. Results review

**Intent:** The request asks for the results to be reviewed. A written record
shows the user and later roles what the KG contains and what was fixed to get
there.

**Done when:**

- `AC-019`: A review of the final AS, LC and LP results is recorded. It
  gives counts by statement type and by relationship type, the number of LP
  candidate pairs each candidate budget dropped, each defect found during the
  cycle with its resolution, and checks of a sample of Skills, LCs and LP
  relationships against the source text.

**Depends on:** Work items 2, 4 and 5.

### 7. Code standards and compatibility

**Intent:** Keep any KG pipeline fix generic and leave other pipelines working.

**Done when:**

- `AC-020`: No backend source or test code added or changed in this cycle
  names CAPS, Funda Wande, South Africa, any other curriculum, or
  curriculum-specific text.
- `AC-021`: The Ghana, India, Nigeria and Rwanda example configs are
  unchanged and still load as valid run configs.
- `AC-022`: Each KG pipeline code change made in this cycle has automated
  tests that show the changed behavior.
- `AC-023`: The existing automated test suite and the repository's lint
  checks pass with this cycle's changes in place.

**Depends on:** Any code changes from work items 2-5.

### 8. Documentation

**Intent:** Keep the pipeline documentation true to the code.

**Done when:**

- `AC-024`: Where this cycle changes KG pipeline behavior or config fields,
  the KG pipeline documentation (`docs/pipeline/`, `docs/guides/`) describes
  the change.

**Depends on:** Work item 7.

## Retired Acceptance Identifiers

- `AC-006`: Replaced by `AC-027`. Skills now attach to their sub-strand
  instead of directly to the Skill Area (user rework, 2026-10-09).
- `AC-009`: Replaced by `AC-029`, which adds sub-strands to what keeps
  same-worded Skills apart (user rework, 2026-10-09).

## Assumptions

- `.standards/CONTEXT.md` still records the CAPS config's earlier `output_dir`
  (`results/funda_wande_grade_r`) and Grade R page range. The user's change in
  `AC-001` is active-cycle work, not a stale baseline.
- `AC-005`, `AC-027` and `AC-008` are judged against the DocumentIR table
  rows, which the user verified against the PDF. How coverage is checked
  (script, sample or both) is for Architect and Tester.
- The sub-strand statement type's name, its canonical names and aliases, and
  how the config tells sub-strand headings apart from schedules and teacher
  notes are design decisions for Architect. "Sub-strand" is this scope's
  descriptive term.
- Every content bullet sits under a listed sub-strand heading within its table,
  so the sub-strand level is expected to need config changes only; Architect
  confirms.
- The user is applying four instruction edits to the CAPS config before
  Architect starts: the lead-in bullet rule (`AC-026`) and the Grades 1-3
  assessment labels, in `sfi_extraction_instructions` and
  `sfi_extraction_validation_instructions` (2026-10-09).
- Candidate pairs dropped by the LP budgets (12 per Skill, 5000 total) are a
  design choice, not a defect by themselves; `AC-019` reports them.
- Conditions that judge LLM output (the AS structure and content, LC content
  and LP relationships) are not waived when a run misses them; the miss is
  routed to its owner (config wording or pipeline code).
- The scope does not fix who runs `create_kgs`; the user inserts and removes
  the temporary stop between AS and LC.
