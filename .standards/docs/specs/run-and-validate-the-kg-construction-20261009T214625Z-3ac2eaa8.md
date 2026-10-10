<!-- STANDARDS
Artifact: ARCHITECTURE
Cycle: run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8
-->

# Technical Design: KG Construction on the CAPS English HL R-3 DocumentIR

Design mode: Feature.

## Context

The scope runs `create_kgs` (AS, LC, LP) on the user-verified CAPS DocumentIR in
`results/kg_for_ed/<doc_key>/`, adds a sub-strand level between Skill Area and
Skill, aligns AS, LC and LP with the Learning Commons definitions, enforces LP
Term order through prompt text only, and records a staged AS review and a final
results review. Curriculum details stay in
`examples/funda_wande/config_english_curriculum.json`; backend code must stay
curriculum-neutral.

Repository facts that shape the design (paths under `backend/src/kgfeg/`):

- An identity-scope dimension must be a Standard Grouping type with
  `controlled_values` (`schemas.py:2104-2162`). Aliases must be unique within a
  statement type (`schemas.py:393-444`); the same text may appear in two types.
- The extraction checker rejects `identity_scope_values` whose keys differ from
  the configured dimensions or whose values are not a canonical value or alias
  (`kgs/validators.py:1967-2022`). The prompt tells the LLM to output canonical
  values (`kgs/prompts.py:1134-1135`).
- A candidate's own `canonical_statement_value` is set only when its
  `description` or `source_text` normalizes exactly
  (`normalize_controlled_value_key`, `schemas.py:140`) to a canonical value or
  alias (`kgs/sfi_registry.py:143-176`).
- hasChild retrieval marks a parent as an indispensable candidate when its
  canonical value and every ancestor scope value match the child's identity
  scope (`kgs/sfi_relationships.py:166-222`, `:579-694`, `:1358-1433`).
- Dedup refuses merge groups whose candidates have different identity scopes
  (`kgs/sfi_dedup.py:2623-2741`). Final IDs hash normalized text plus identity
  scope, so two same-worded Skills need different identity scopes.
- Resume with `kgs.overwrite=false`: extraction and LC resume keys ignore
  instruction text; hasChild requests embed both hasChild instruction texts and
  restart when they change (`kgs/sfi_relationships.py:2860-2955`, `:4848-4897`);
  AS and AS+LC exports are reused only when payloads or input fingerprints
  match. LP fails closed on any stale or changed material, including an existing
  final LP bundle (`kgs/lp_export.py:743-790`, `kgs/lp_generation.py:855-924`).
  `kgs.overwrite=true` restarts AS, LC and LP together; there is no LP-only or
  LC-only overwrite (`docs/guides/running-and-debugging.md`).
- AS artifacts are flat files in `<output_dir>/<doc_key>/kgs/`. LC files start
  with `lc_`, plus `learning_components.jsonl` and `as_lc_*`. LP files start with
  `lp_` (including `lp_generation_history/`), plus `as_lc_lp_*` and
  `.lp_generation.lock`.
- `kgs/lp_candidates.py` records `total_candidate_pairs_dropped_by_per_sfi_budget`
  and `..._by_total_budget` in `lp_candidate_summary.json`.
- `page_ir_verification` has no `output_dir` field; it reads and writes under
  `page_ir_extraction.output_dir` (`entries/verify_page_ir_continuity.py:209`,
  `:241`). Commit `7ccfb3a` set that to `results/kg_for_ed` and both stages to
  `start_page` 35 / `end_page` 135.
- The committed CAPS `kgs` block already holds the user's four instruction edits
  (lead-in rule and Grades 1-3 assessment labels).

DocumentIR evidence for the sub-strand level: each of the 48 REQUIREMENTS PER
TERM tables has 2-8 body rows, so no table is split at the current
`max_rows_per_table_window` of 10. Sub-strand headings are non-bullet lines
inside the body cells. Printed variants include `Daily Phonemic Awareness/
Phonic Activities of 15 minutes`, `Daily Phonic Activities of 15 minutes` and
`Phonic Activities three times a week for 15 minutes` (Phonics); `Paired/
Independent Reading` with or without `(three times a week)` or `(twice a week in
Language focus time)`; `Emergent reading` (Grade R Term 2); `Emergent reading
skills (taught in Shared and Group Guided Reading lessons)` (Grade 1 Term 1);
and `Handwriting` for Grade R Term 2's Emergent Handwriting. The same names
(Shared Reading, Group Guided Reading, Paired/Independent Reading, Handwriting,
Writing, `Phonics:`) also head lists inside the ASSESSMENT band, which can span
the last two rows of a table. About 930 bullet lines lie outside the ASSESSMENT
band.

Architect ran a read-only pass over the DocumentIR with the controlled-value
table below. It assigned each table's allowed sub-strands and stopped at the
ASSESSMENT row. Exactly 152 sub-strands matched, and every unmatched non-bullet
line was a heading, schedule or note that the instruction contract excludes. The
preflight in Data and Control Flow repeats this check as Developer evidence.

## Decision

1. **Config only, no planned KG code change.** The sub-strand level, Learning
   Commons alignment and Term order all go into the CAPS config. The existing
   pipeline supports a fifth hierarchy level without code changes.
2. **New statement type `Sub-strand`** (normalized `Standard Grouping`) with a
   controlled vocabulary. It joins the Skill identity scope so same-worded
   Skills under different sub-strands get different identities (`AC-029`), and
   it lets hasChild find each Skill's parent by an exact scope match
   (`AC-027`, `AC-007`).
3. **Canonical values name the sub-strand, not the printed schedule.** Every
   printed variant is an alias. Grade R's Emergent Handwriting and Grades 1-3
   Handwriting share the canonical `Handwriting` (alias `Emergent
   Handwriting`). This is required because Grade R Term 2 prints `Handwriting`,
   an alias cannot map to two canonical values, and one canonical keeps Grade R
   consistent across terms. Identity stays separate per Grade, Term and Skill
   Area. Exports still show the printed label (`description`).
4. **One extraction window per requirements table.** Set
   `kgs.as.max_rows_per_table_window` to `null` so the sub-strand in force at a
   page break is always in the same window as the bullets under it.
5. **Staged runs with temporary local stops.** There are two stops, after AS
   (step 10) and after LC (step 19). Neither is ever committed. LP runs only on
   an LC layer with zero failed requests.
6. **Regenerate a phase by moving its artifacts aside, never by
   `kgs.overwrite=true`.** That flag would also regenerate the confirmed AS. The
   committed config keeps `kgs.overwrite: false`.
7. **Identity snapshots are SHA-256 manifests.** They prove the upstream outputs
   are unchanged (`AC-002`) and that LC and LP build on the confirmed AS
   (`AC-011`, `AC-012`).
8. **Checks are local and stay uncommitted.** Coverage and structure checks are
   CAPS-specific, so they live outside `backend/` and are never committed. Their
   method and results go in the development plan, as the previous cycle's run
   results did.

## Acceptance Coverage

- `AC-001`: Already present in the committed config (`7ccfb3a`). The
  verification stage derives its paths from `page_ir_extraction.output_dir`.
  This cycle keeps those values, and the edited config must still load as a
  `RunConfig`.
- `AC-002`: `create` reads `extraction_run.json` and `stitching/document_ir.json`
  and writes only under `kgs/` (moved-aside copies go to a sibling
  `kgs_superseded/`). Snapshot S0 compares the three upstream directories.
- `AC-003`: AS runs with the revised config. The AS check requires
  `as_validation_report.json` to pass with no errors.
- `AC-004`, `AC-025`, `AC-030`: Covered by the `Sub-strand` type, its controlled
  values, the parent policy, the hierarchy and the extraction instructions. The
  preflight heading check runs before any paid run, and the AS structure check
  runs after.
- `AC-005`, `AC-026`, `AC-008`, `AC-028`: Covered by the extraction and
  validation instruction contract and whole-table windows. The AS coverage
  check compares Skills with the DocumentIR bullets.
- `AC-027`, `AC-029`: The Skill identity scope includes `Sub-strand`, and the
  parent policy is Skill → Sub-strand. Dedup cannot merge across scopes, and
  final IDs include the scope.
- `AC-007`: An exact identity-scope match makes the correct parent an
  indispensable hasChild candidate. The hasChild instructions require a
  resolved parent. The AS check requires zero unresolved and zero root-fallback
  edges.
- `AC-010`: `grade_level_mapping` is unchanged. Each Skill's grade comes from its
  `Grade` scope value. The AS check compares each Skill's export grade with its
  Grade ancestor.
- `AC-011`: The user reviews AS while the stop after step 10 is in place. The
  confirmation is recorded with the S_AS manifest.
- `AC-012`: After confirmation, `kgs.as` and `kgs.metadata` do not change, and
  later invocations use `overwrite=false`, so the AS sub-stages reuse their
  artifacts. Every later invocation must reproduce the S_AS manifest.
- `AC-013`: Stops are local edits to `entries/create_kgs.py` and are never
  committed. The last run is one invocation with no stop that runs AS, LC and LP
  in order.
- `AC-014`, `AC-015`: LC runs with the stop after step 19. Failed requests are
  retried with `overwrite=false` until `lc_generation_failures.json` is empty.
  `lc_max_failure_rate` stays 0.05. The LC check requires
  `as_lc_validation_report.json` to pass and at least one supports edge per
  Skill.
- `AC-016`, `AC-031`: The existing LC generation and dedup instructions apply.
  Review findings are fixed in LC instruction text and LC is regenerated.
- `AC-017`, `AC-018`, `AC-032`: The LP coordinate blocks later-to-earlier Grade
  edges. Term order and relationship meaning come from the LP producer and
  checker instructions. A Term-order violation or relationship defect is fixed
  by changing instructions and regenerating LP.
- `AC-019`: The results review lives in the development plan. It draws on the
  export summaries, `lp_candidate_summary.json` dropped-pair counts, the run
  log and the sample checks.
- `AC-020`, `AC-022`: No KG code change is planned. A defect fix must be
  generic and needs tests (see Interfaces, code-fix boundary).
- `AC-021`: Only the CAPS config changes. The six other example configs stay
  unchanged and must still load: `examples/ghana/` (English and math),
  `examples/india/madhi/`, `examples/india/pratham/`, `examples/nigeria/` and
  `examples/rwanda/`.
- `AC-023`: The existing suite and `make lint` run with the cycle's changes in
  place.
- `AC-024`: No architectural impact unless a code fix changes pipeline
  behavior or config fields. Documenter then updates `docs/pipeline/` or
  `docs/guides/`.

## Components

- **CAPS runtime config** (`examples/funda_wande/config_english_curriculum.json`,
  `kgs` block): the only planned change. It holds the hierarchy, vocabulary and
  instruction text.
- **`create_kgs` pipeline** (`entries/create_kgs.py`, `kgs/`): used as-is. Local
  stops are added only for staged runs.
- **Local check scripts** (uncommitted, outside `backend/`, for example in a
  scratch directory or under the git-ignored `results/` tree): the preflight
  heading check, AS structure and coverage checks, LC checks, the LP Term-order
  check, and SHA-256 manifests. They read the DocumentIR and KG artifacts and
  never write into `extraction/`, `verification/`, `stitching/` or `kgs/`.
- **Development plan**: holds the run log, the AS confirmation with S_AS, the
  defect log, check results and the `AC-019` results review.

## Interfaces and Contracts

### `kgs.as` config contract

- `statement_type_policy`: insert `Sub-strand` between `Skill Area` and `Skill`.
  - `normalized_statement_type`: `Standard Grouping`.
  - Statement-type aliases: spellings of the type name only (for example
    `SUB-STRAND`, `Substrand`). Never use a sub-strand value as a type alias,
    because typed-label evidence prefix-matches type aliases
    (`kgs/sfi_relationships.py:2367-2408`).
  - `description`: a heading printed inside the CONTENT/CONCEPTS/SKILLS body of
    a REQUIREMENTS PER TERM table that groups the bullets after it, scoped by the
    table's Grade, Term and Skill Area.
  - `controlled_values` (canonical values; aliases are every printed variant in
    the 48 tables, not only those listed here):

    | Used in                     | Canonical value                                                         | Known printed variants (aliases)                                                                                                                                                                                          |
    | --------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
    | Grade R Oral                | `Daily activities in all areas of Language and other subjects`         | trailing period                                                                                                                                                                                                           |
    | Grade R Oral                | `Uses language to develop concepts`                                     | `Uses language to develop concepts in all subjects`                                                                                                                                                                       |
    | Grade R Oral                | `Uses language to think and reason`                                     | trailing colon                                                                                                                                                                                                            |
    | Grade R Oral                | `Uses language to investigate and explore`                              |                                                                                                                                                                                                                           |
    | Grade R Oral                | `Processes information`                                                 | trailing colon                                                                                                                                                                                                            |
    | Grade R Oral                | `Uses visual and pictorial cues to make meaning`                        |                                                                                                                                                                                                                           |
    | Grade R Reading; Gr 1 T1    | `Emergent reading skills`                                               | `Emergent reading`; `Emergent reading skills (taught in Shared and Group Guided Reading lessons)`                                                                                                                         |
    | Grade R Reading             | `Begins to make meaning of written text`                                |                                                                                                                                                                                                                           |
    | Grade R Reading; Gr 1-3 R&P | `Shared Reading`                                                        |                                                                                                                                                                                                                           |
    | Grade R Reading             | `Independent Reading`                                                   |                                                                                                                                                                                                                           |
    | Grade R Reading             | `Phonological/Phonemic Awareness`                                       | `Phonological/ Phonemic Awareness`                                                                                                                                                                                        |
    | Grade R Reading             | `Relates sounds to letters and words`                                   |                                                                                                                                                                                                                           |
    | Grade R Writing; Gr 1-3     | `Handwriting`                                                           | `Emergent Handwriting`                                                                                                                                                                                                    |
    | Grade R Writing             | `Emergent Writing`                                                      |                                                                                                                                                                                                                           |
    | Grade R Writing             | `Works with words`                                                      |                                                                                                                                                                                                                           |
    | Gr 1-3 Oral                 | `Daily / Weekly activities in all areas of Language and other subjects` |                                                                                                                                                                                                                           |
    | Gr 1-3 Oral                 | `Twice weekly focussed listening and speaking activities`               |                                                                                                                                                                                                                           |
    | Gr 1-3 R&P                  | `Phonics`                                                               | `Daily Phonemic Awareness/ Phonic Activities of 15 minutes`; `Daily Phonic Activities of 15 minutes`; `Phonic Activities three times a week for 15 minutes`                                                               |
    | Gr 1-3 R&P                  | `Group Guided Reading`                                                  |                                                                                                                                                                                                                           |
    | Gr 1-3 R&P                  | `Paired/Independent Reading`                                            | `Paired/Independent Reading (three times a week)`; `Paired/Independent Reading (twice a week in Language focus time)`                                                                                                     |
    | Gr 1-3 Writing              | `Shared, Group and Independent Writing`                                 |                                                                                                                                                                                                                           |

    Variants that differ only in case or punctuation share a normalized key and
    need no separate alias. Schedule lines such as `Group Guided Reading (two
    groups per day) and 2 - 3 Shared Reading sessions per week.` are not
    aliases.
- `Skill` statement type: its description now scopes it under Grade, Term,
  Skill Area and Sub-strand. The `Grade`, `Term` and `Skill Area` entries are
  unchanged.
- `identity_scope_statement_types`: `Sub-strand: [Grade, Term, Skill Area]`;
  `Skill: [Grade, Term, Skill Area, Sub-strand]`. `Term` and `Skill Area` are
  unchanged.
- `sfi_has_child_statement_type_hierarchy`:
  `[Grade, Term, Skill Area, Sub-strand, Skill]`.
- `sfi_has_child_parent_policy`: `Sub-strand` → `Skill Area` (min 1, max 1);
  `Skill` → `Sub-strand` (min 1, max 1). Other entries are unchanged.
- `sfi_dedup_context_statement_types`:
  `[Grade, Term, Skill Area, Sub-strand]`.
- `max_rows_per_table_window`: `null`. `row_overlap` stays 1 and is unused.
- `grade_level_*`, `synthetic_merge_key_fields`, table selection and
  `max_has_child_*` are unchanged. A Skill has at most about six same-row
  sub-strand candidates plus its exact match, well under 23 indispensable
  candidates.

### Instruction contract (wording is Developer's)

- `sfi_extraction_instructions` must:
  - state the hierarchy Grade → Term → Skill Area → Sub-strand → Skill;
  - list the expected sub-strands for each Skill Area, with the term
    exceptions, as in the scope's per-table list;
  - emit each sub-strand heading once per table as a `Sub-strand` candidate
    whose `description` is the printed heading text;
  - give each Skill the `Sub-strand` scope value of the heading in force above
    its bullet, carried across rows and page breaks within the table;
  - name what is not a sub-strand: `Reading`/`Reading:` (Grade R) and `Daily
    Reading Activities`; week ranges; Handwriting sub-headings (`Development of
    letter formation...`, `Maintenance of the print script`, `Transition to a
    joined script or cursive writing`, `Activities to strengthen fine
    muscles...`); schedule and time lines (`Daily 15 minute activities`, `Formal
    Lessons ... of 15 minutes`, `Group Guided Reading (two groups per day)...`,
    `Whole class lessons...`, `Daily reading related activities...`); teacher
    notes; `Daily activities in all areas of Language and other subjects` when it
    appears under Emergent Handwriting in Grade R Emergent Writing (it is a
    sub-strand only in Grade R Oral); and every heading in or after the
    ASSESSMENT row;
  - remove the items that are now sub-strands from the old exclusion list:
    `Daily / Weekly activities`, `Twice weekly focussed listening and speaking
    activities`, `Group Guided Reading`, `Processes information:`, `Uses visual
    and pictorial cues to make meaning`;
  - keep the lead-in rule and the other exclusions;
  - point out that `Emergent Writing` (and `Emergent reading`) can be both a
    Skill Area label in the header rows and a Sub-strand heading in the body.
- `sfi_extraction_validation_instructions`: the checker side of the same rules,
  including the expected sub-strand set per Skill Area and the assessment-band
  exclusion.
- `sfi_dedup_instructions`: Sub-strands are scoped by Grade, Term and Skill Area
  and merge only repeated occurrences within one table scope. Skills now include
  Sub-strand in their identity, so Skills under different sub-strands are never
  merged (the `AC-029` example).
- `sfi_has_child_instructions` and `sfi_has_child_validation_instructions`:
  hierarchy StandardsFramework → Grade → Term → Skill Area → Sub-strand → Skill.
  A Sub-strand attaches to the Skill Area of the same Grade and Term. A Skill
  attaches to the Sub-strand matching its identity scope, which is the heading
  above it in its table. The existing root, unresolved and exclusion rules stay.
- `kgs.lc`, `kgs.lp`: no planned change. The LP producer and checker
  instructions already state the Term order. They change only to fix an
  `AC-016`, `AC-031`, `AC-018` or `AC-032` finding, and changing them requires
  regenerating that phase.

### Run and regeneration contract

- Committed config keeps `kgs.overwrite: false`. Never run `create_kgs` with
  `kgs.overwrite: true` after the AS confirmation.
- Temporary stops are local edits in `build_kgs`: Stop A after step 10 (AS
  export), and Stop B after the AS+LC validation check (before
  `reuse_as_lc_lp_kg`). They are never committed (`AC-013`). Per the scope, the
  user inserts and removes Stop A.
- Moving aside is the only regeneration mechanism. Do it only when no
  `create_kgs` process is running. Move the files into
  `<output_dir>/<doc_key>/kgs_superseded/<UTC timestamp>-<phase>/` and never
  delete or edit them:
  - **AS regeneration**: move the whole `kgs/` directory. This is required for
    any change to `kgs.as` or `kgs.metadata`, because extraction resume ignores
    instruction text. One exception: when only the two hasChild instruction
    fields change, rerun in place with `overwrite=false`, because hasChild
    restarts on its own.
  - **LC regeneration**: move all `lc_*` files, `learning_components.jsonl`,
    `as_lc_*`, all `lp_*` entries and `as_lc_lp_*`. Required for any `kgs.lc`
    change, because LC resume ignores instruction text.
  - **LP regeneration**: move all `lp_*` entries, including
    `lp_generation_history/`, and `as_lc_lp_*`. Leave `.lp_generation.lock` in
    place. Required for any `kgs.lp` change, or when an LP run consumed an LC
    layer that has since changed.
  - If a run after a move-aside still fails closed on stale LP or LC material,
    stop and investigate. Never delete the lock or edit evidence.
- Retrying failed LC requests needs no move-aside: rerun with `overwrite=false`
  and Stop B in place.
- If AS has to change after the confirmation, the user reconfirms a new S_AS,
  and LC and LP are regenerated.

### Identity snapshots

- **S0**: path and SHA-256 for every file under `extraction/`,
  `verification/` and `stitching/`, taken before the first KG run and compared
  after the final run (`AC-002`).
- **S_AS**: SHA-256 of `sfi_final_records.json`, `sfi_final_contexts.json`,
  `has_child_edges_final.json`, `has_child_unresolved_edges.json` and every
  `as_*` export file (`as_kg_bundle.json`, `as_standards_framework.json`,
  `as_standards_framework_items.jsonl`, `as_relationships_has_child.jsonl`,
  `as_nodes.jsonl`, `as_relationships.jsonl`, `as_validation_report.json`,
  `as_entity_provenance.json`, `as_unresolved_items.json`). Taken at the user's
  AS confirmation and required byte-identical after every later invocation
  (`AC-011`, `AC-012`). These payloads are deterministic; AS export reuse
  requires exact payload equality.

### Code-fix boundary

- If a run exposes a generic pipeline defect that needs no new config field, no
  stage-contract change and no resume-semantics change, Developer fixes it in
  curriculum-neutral code, with tests (`AC-020`, `AC-022`) and documentation
  through Documenter (`AC-024`).
- Anything that adds or changes a config field, a stage contract or resume
  behavior, adds stage selection, or enforces Term order in code comes back to
  Architect.

## Data and Control Flow

1. **Preflight (no LLM calls).**
   - Take S0.
   - Load the edited CAPS config and the six other example configs.
   - Run AS steps 3-4 with `save_fp` outside the repository. Expect 48 whole-table
     windows plus 7 heading windows.
   - Run the heading check. For each of the 48 tables, the non-bullet heading
     lines before the ASSESSMENT row must yield exactly the scope's expected
     sub-strands, and each printed heading must normalize to a `Sub-strand`
     canonical value or alias. That gives 152 in all.
2. **AS run** (Stop A): `create_kgs` runs steps 1-10.
   - Run the AS checks.
   - Each defect gets a config fix, AS regeneration and a fresh check. Log it.
   - When the checks pass, the user reviews and confirms. Take S_AS and record
     both.
3. **LC run** (Stop A removed, Stop B in place).
   - AS is reused and must match S_AS.
   - Run the LC checks, and retry failed requests until there are none.
   - LC content defects get an LC instruction fix and LC regeneration.
4. **LP run** (no stops; one invocation running AS, LC and LP in order).
   - AS and LC are reused; check S_AS.
   - Run the LP checks. LP defects get an instruction fix and LP regeneration,
     followed by another no-stop invocation.
   - The last invocation must succeed with no stop and an unmodified
     `create_kgs.py`.
5. **Close-out.** Compare S0, record the results review (`AC-019`), and run the
   test suite and lint.

## Technical Acceptance Criteria

- `AC-001`: The committed CAPS config has `page_ir_extraction.output_dir`
  `.../results/kg_for_ed`, `start_page` 35 and `end_page` 135 in both page
  stages, and loads as a `RunConfig`.
- `AC-002`: The S0 manifest is identical before the first KG run and after the
  final run. Every KG run resolves `document_ir_fp` to
  `results/kg_for_ed/<doc_key>/stitching/document_ir.json`.
- `AC-025`: The preflight heading check reports all 152 expected sub-strands
  with zero unmatched printed headings. In the AS export, each Sub-strand item
  has a non-empty canonical value, so `canonical_statement_value` is set.
- `AC-004`, `AC-025`, `AC-030`, `AC-010`: The AS structure check confirms:
  - 4 Grades under the root, 4 Terms per Grade and the expected 3 Skill Areas
    per Term;
  - exactly the expected Sub-strand set under each Skill Area;
  - Grade, Term, Skill Area and Sub-strand items are `Standard Grouping`, and
    Skills are `Standard`;
  - every Skill's grade is the mapped grade of its Grade ancestor (`K`, 1, 2, 3).
- `AC-005`, `AC-026`, `AC-027`, `AC-029`: The AS coverage check builds expected
  Skills from the DocumentIR:
  - inputs are the bullet lines before the ASSESSMENT row of each banner table;
  - the two lead-ins are joined with their fragments;
  - each expected Skill is keyed by Grade, Term, Skill Area, sub-strand in force
    and normalized text.

  It finds exactly one exported Skill per expected Skill, under the matching
  ancestor chain, with no fragment-only Skill. Every mismatch is decided against
  the source and logged.
- `AC-008`, `AC-028`: The coverage check finds no exported Skill or grouping
  outside the expected sets. The 7 heading-block windows and the 4 resources
  tables contribute no AS items.
- `AC-007`: `has_child_unresolved_edges.json` is empty, and no final hasChild
  edge is a root fallback. `sfi_merge_conflicts.json` and
  `sfi_merge_needs_review.json` drop no expected item.
- `AC-003`: `as_validation_report.json` has `passed: true` and no errors.
- `AC-011`, `AC-012`: The development plan records the user's AS confirmation
  with the S_AS manifest. Every later invocation reproduces S_AS byte for byte.
- `AC-013`: `git diff` and the committed `entries/create_kgs.py` contain no stop.
  The final successful invocation ran with no local edits to `create_kgs.py`.
- `AC-014`, `AC-015`: `as_lc_validation_report.json` passes with no errors.
  `lc_generation_failures.json` holds no unresolved failure. Each Skill has at
  least one supports edge. `lc_max_failure_rate` is 0.05 in the committed
  config.
- `AC-017`: `lp_validation_report.json` and the AS+LC+LP bundle validation pass
  with no errors, and `as_lc_lp_kg_bundle.json` exists.
- `AC-018`: For every buildsTowards edge, the source's (Grade rank, Term rank)
  is not later than the target's. Rank comes from `ordered_values` and the Term
  ancestor's canonical value.
- `AC-020`, `AC-021`: The cycle's diff touches no example config other than
  CAPS, and each of the six other example configs loads as a `RunConfig`. Any
  backend diff contains no curriculum names or curriculum-specific text.

## Build Plan

1. Take S0. Edit the CAPS `kgs.as` block to the contract above. Validate config
   loading and run the preflight.
2. Run AS with Stop A and run the AS checks. Iterate fixes, using AS
   regeneration, until they pass. Record the user's confirmation with S_AS.
3. Run LC with Stop B. Retry failures, run the LC checks and fix as needed.
4. Run the full no-stop invocation and the LP checks. Fix with LP regeneration
   as needed, ending on a successful no-stop invocation.
5. Compare S0 and S_AS, write the `AC-019` review, and run the test suite, lint
   and the other-config load check.

## Risks and Follow-up

- **Alias gaps:** a printed heading missing from the aliases can exhaust
  extraction retries and abort the run, or can leave a Sub-strand without a
  canonical value and weaken hasChild evidence. The preflight heading check
  runs before any paid call.
- **Same label in two types:** `Emergent Writing` and `Emergent reading` are
  both Skill Area labels and sub-strand headings. The scope keeps them apart,
  but the instructions must call this out.
- **Assessment-band look-alikes:** headings in the ASSESSMENT band reuse
  sub-strand names. A miss shows up as extra Sub-strands in the structure check.
- **buildsTowards cycles:** with Term order kept, a cycle can only form among
  Skills of one Grade and Term, and it raises `LPFinalizationCycleError`. Fix
  it first in the prompt, then regenerate LP. A code-level cycle policy comes
  back to Architect.
- **LP candidate cap:** about 924 Skills makes `6N` larger than 5000, so the
  total budget binds. The dropped counts are reported (`AC-019`) and are not a
  defect in themselves.
- **Runtime:** hasChild makes about 1,100 sequential producer/checker requests,
  and LP makes about 5,000 pairs × 2 calls at concurrency 4. Expect
  multi-hour runs.
- **Large windows:** whole-table windows produce up to about 60 candidates each.
  If output limits force splitting, the sub-strand context guarantee breaks, so
  that change comes back to Architect.
- **Follow-up (outside scope):** a supported LP-only or LC-only regeneration
  option would replace the manual move-aside. The
  `docs/pipeline/academic-standards.md` wording on root fallback ("when
  permitted by the hierarchy policy") does not match the code.

## Alternatives

- **Sub-strand without controlled values, or kept out of Skill identity:**
  rejected. Identity-scope types need controlled values, and without the
  sub-strand two same-worded Skills collide in final IDs (`AC-029`).
- **Separate `Emergent Handwriting` canonical value:** rejected. Grade R Term 2
  prints `Handwriting`, which cannot be an alias of two canonical values, and
  Grade R would get a different canonical value in Term 2 than in Terms 1, 3
  and 4.
- **`kgs.overwrite=true` for reruns:** rejected. It regenerates the confirmed
  AS (`AC-012`).
- **An LP-only overwrite field, or temporary edits that pass `overwrite=True`
  into LP functions:** rejected. The first is a code change and close to the
  stage selector the scope rules out. The second needs three edits, including
  skipping fail-closed reuse. Moving files aside uses the fresh-start path the
  first LP run already uses.
- **Running LP without Stop B:** rejected. An LC layer with failures up to 5%
  would reach LP, and fixing it would discard a full LP run.
