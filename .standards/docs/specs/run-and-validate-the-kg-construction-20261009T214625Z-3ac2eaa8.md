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
- LP keeps at most `min(max_total_candidates, N(N-1)/2,
  N*max_candidates_per_sfi//2)` candidate pairs (`kgs/lp_candidates.py`). Both
  budgets are strict positive integers with no upper limit (`schemas.py:2768-2780`).
- LP execution material includes a hash of the complete `kgs` config and
  `max_concurrent_requests` (`kgs/lp_generation.py:931-956`). Any config change
  after LP has started makes LP fail closed.
- `kgs.lp.evidence_limits.max_source_evidence_characters_per_sfi` serves three
  purposes in LP request building (`kgs/lp_requests.py:455-500`, `:770-840`):
  - it caps each descriptive text field;
  - it is the total source-excerpt budget per SFI;
  - it is a hard ceiling on each endpoint's and ancestor's complete audit
    context. Audit context covers the audit, merge and code fields and the
    canonicalization, and request building raises rather than truncate it.

  LP run 1 (2026-10-11) stopped at this check with the bound at 2000. No LP
  artifacts were written. On the confirmed AS, 81 Skills (max 2395 characters)
  and 6 Sub-strands (max 2155) exceed 2000. The text is mostly the dedup
  `merge_reason`, stored twice. Developer's offline request build, with no LLM
  calls and no writes, reproduces the error at 2000 and builds all 12,892
  requests at 2400 and at 3000 with no other bound failure. The median payload
  is about 43.1k characters at 2400 and 43.5k at 3000; the maximum is about 59k
  and 61k.
- Scope rework (2026-10-11) adds `AC-033`: LP budgets of 30 pairs per Skill and
  14,000 in total. LP had not run yet. The confirmed AS (S_AS) and LC run 2
  (924 Skills, zero failed requests) stand. An offline candidate build on that
  AS+LC bundle keeps about 12,892 pairs.
- `page_ir_verification` has no `output_dir` field; it reads and writes under
  `page_ir_extraction.output_dir` (`entries/verify_page_ir_continuity.py:209`,
  `:241`). Commit `7ccfb3a` set that to `results/kg_for_ed` and both stages to
  `start_page` 35 / `end_page` 135.
- The committed CAPS `kgs` block already holds the user's four instruction edits
  (lead-in rule and Grades 1-3 assessment labels).
- **LP checkpoint cost (D-LP-2, LP run 2, 2026-10-11).** Run 2 processed about
  17.6 requests an hour (168 of 12,892 after 9.5 h). Less than 10% of that time
  was model calls. The single coordinator does all checkpoint work while
  holding the dispatch gate. Each save costs time in proportion to the whole
  store and population, not to the change:
  - `LPGenerationCheckpoints._save` and `_commit`
    (`kgs/lp_checkpoints.py:192-208`, `:660-692`) run the execution check about
    10 times per save. Each check streams the 569 MB
    `lp_generation_requests.jsonl` and re-serializes all 12,892 requests
    (`LPRequestPopulationVerifier.verify`, `kgs/lp_requests.py:309-337`, `:669`),
    about 2.5 s each.
  - `_validate` re-hashes the request behind every stored row
    (`kgs/lp_checkpoints.py:1010-1074`).
  - `_payloads` re-proves every record each time it is called: in the save, in
    `_previous_hashes` and in `verify_bytes`.
  - Every save rewrites all checkpoint files and also journals the full next
    state.

  A request takes about 7 saves. Per-request overhead therefore grows with the
  number of completed requests, so total overhead grows with the square of the
  request count. At-rest checkpoints are ordinary files:
  - the three stage JSONL files grow only by appending (`promote`,
    `kgs/lp_checkpoints.py:1415-1424`);
  - `pending`, `failures` and `usage` (which holds the attempts and per-request
    states) and the receipt are small JSON files.

  `lp_execution_material` (`kgs/lp_generation.py:931-972`) hashes the config,
  `prompts.py`, the schemas, the model and the request manifest, but not the
  checkpoint code. A code-only fix therefore leaves run 2's completed requests
  reusable. Tests in `tests/kgfeg/kgs/` pin the current every-check streaming
  cadence: `test_lp_streaming_proofs.py`, `test_lp_population_verifier.py`,
  `test_lp_checkpoint_proofs.py` and the concurrency and recovery tests.

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

1. **Config first, with one planned code change.** The sub-strand level,
   Learning Commons alignment and Term order all go into the CAPS config; the
   existing pipeline supports a fifth hierarchy level without code changes. The
   one planned code change is the generic LP checkpoint throughput fix
   (Decision 9).
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
9. **Make LP checkpoint cost per save independent of the number of completed
   requests.** Run full verification at the points where trust changes: start of
   each invocation, completion, read-only loads, and any guard trip. Between
   those points, use cheap guards that fail closed in the same cases. Write only
   what changed, while keeping every at-rest checkpoint byte identical to
   today's writer. Run 2's completed requests then resume as they are, and the
   LP run becomes bound by model calls, so concurrency helps again.

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
- `AC-017`: LP can start only if every endpoint's and ancestor's full audit
  context fits the LP evidence bound. The bound is set to 3000, and an offline
  request-build preflight confirms the full population builds before any paid
  LP call. LP can finish in a practical time only with the checkpoint
  throughput fix (Decision 9, LP checkpoint contract).
- `AC-017`, `AC-018`, `AC-032`: The LP coordinate blocks later-to-earlier Grade
  edges. Term order and relationship meaning come from the LP producer and
  checker instructions. A Term-order violation or relationship defect is fixed
  by changing instructions and regenerating LP.
- `AC-033`: The CAPS config sets `kgs.lp.candidate_policy.budgets` to 30 and
  14,000 before the first LP run. With 924 Skills, the per-Skill budget is the
  binding one, and the final `lp_candidate_summary.json` records the budgets.
- `AC-019`: The results review lives in the development plan. It draws on the
  export summaries, `lp_candidate_summary.json` dropped-pair counts, the run
  log and the sample checks.
- `AC-020`, `AC-022`: The one planned code change is the LP checkpoint
  throughput fix, in `kgs/lp_checkpoints.py`, the population verifier in
  `kgs/lp_requests.py`, and the verification call sites in
  `kgs/lp_generation.py`. It is curriculum-neutral and needs the tests listed
  under Technical Acceptance Criteria. Any other defect fix follows the
  code-fix boundary.
- `AC-021`: Only the CAPS config changes. The six other example configs stay
  unchanged and must still load: `examples/ghana/` (English and math),
  `examples/india/madhi/`, `examples/india/pratham/`, `examples/nigeria/` and
  `examples/rwanda/`.
- `AC-023`: The existing suite and `make lint` run with the cycle's changes in
  place.
- `AC-024`: The checkpoint fix changes documented LP resume and verification
  behavior, so Documenter updates `docs/pipeline/learning-progressions.md`
  ("Concurrency, checkpoints, and failure") and
  `docs/guides/running-and-debugging.md` (the LP resume section). No config
  field changes. Any other code fix that changes behavior is documented the
  same way.

## Components

- **CAPS runtime config** (`examples/funda_wande/config_english_curriculum.json`,
  `kgs` block): the only planned config change. It holds the hierarchy,
  vocabulary and instruction text.
- **`create_kgs` pipeline** (`entries/create_kgs.py`, `kgs/`): used as-is,
  except for the LP checkpoint store and verifiers. Local stops are added only
  for staged runs.
- **LP checkpoint store and verifiers**: `LPGenerationCheckpoints` in
  `kgs/lp_checkpoints.py`, `LPRequestPopulationVerifier` in
  `kgs/lp_requests.py`, and `_verify_execution_material`, `_verify_lp_inputs`
  and the dispatch-gate check in `kgs/lp_generation.py`. This is the only
  planned code change, under the LP checkpoint contract below. Finalization,
  artifacts, export and the at-rest checkpoint format stay unchanged.
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
- `kgs.lc`: changes only to fix an `AC-016` or `AC-031` finding. Any change
  requires LC regeneration.
- `kgs.lp.candidate_policy.budgets`: `max_candidates_per_sfi: 30`,
  `max_total_candidates: 14000`, both written as JSON integers (`AC-033`).
  - Set them before the first LP invocation. No LP artifacts exist yet, so this
    needs no move-aside.
  - With 924 eligible Skills, the retention bound is
    `min(14000, 426426, 924*30//2) = 13860`. The per-Skill budget binds and the
    total budget leaves headroom.
- `kgs.lp.evidence_limits.max_source_evidence_characters_per_sfi`: `3000`, a
  JSON integer, set before the next LP invocation. No LP artifacts exist, so
  this needs no move-aside.
  - 3000 fits the largest mandatory audit context (2395) with about 25%
    headroom. The confirmed AS fixes the audit text, so headroom only guards
    against measurement error.
  - The cost is small: the source-excerpt budget per SFI grows from 2000 to
    3000 characters, adding under 1% to median request payloads.
  - The other `evidence_limits` fields and all other `kgs.lp` fields keep their
    values.
- `kgs.lp` producer and checker instructions: they already state the Term
  order, and change only to fix an `AC-018` or `AC-032` finding.
- Any `kgs.lp` change after LP has started requires LP regeneration. This
  includes the budgets and `max_concurrent_requests`, so the user fixes
  concurrency before the LP run starts.

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

### LP checkpoint integrity and throughput contract (D-LP-2)

The fail-closed guarantees keep their strength. What changes is where full
proofs run and how much each save writes.

1. **Full verification points.** Full verification means today's complete
   checks:
   - stream every population artifact byte;
   - hash the whole in-memory population;
   - check execution-material equality;
   - run a complete `_validate` of every record;
   - read back every checkpoint file against the receipt.

   It runs at store construction in every invocation (fresh, resume or
   recovery); at the completion boundary, before the save whose receipt status
   first becomes `completed`; in every read-only load (finalization,
   `validate_lp_checkpoint_format`); and whenever a guard in item 2 trips.
2. **Guards between full verifications.** They run before each dispatch, each
   dispatch-gate transport check and each publication, and do no work
   proportional to the population or the store:
   - **Population artifacts:** compare stat identity (`st_dev`, `st_ino`,
     `st_size`, `st_mtime_ns`, `st_ctime_ns`) with the values captured at the
     last full verification. Any difference triggers a full population
     verification: identical bytes refresh the identity and log it; differing
     bytes fail closed. `st_ctime_ns` catches a content edit followed by an
     `os.utime` restore.
   - **In-memory population:** re-hash the requests being dispatched or
     published and compare them with per-request digests captured at the last
     full verification. Recompute execution-material equality as today.
   - **Checkpoint files:** compare stat identity and length with the values
     recorded after the store's own last write. Any difference triggers a full
     readback against the receipt; a mismatch fails closed.
3. **Record authority.**
   - A record (stage row, pending row, attempt, failure) is validated when the
     store creates or changes it, and its canonical bytes become its authority.
   - The store publishes only from those bytes. No caller or worker holds an
     object whose mutation can change persisted bytes or store decisions;
     accessors return copies or immutable views.
   - Each save re-proves only the records it adds or changes, plus the rows
     those records depend on for the same request index.
   - It also rechecks the cross-record invariants the change can affect: stage
     length order, prefix contiguity, pending capacity, and failure and attempt
     dispositions for that request.
4. **Incremental commits with identical at-rest bytes.**
   - Stage JSONL files: a save appends exactly the new rows' bytes.
   - `pending`, `failures`, `usage` and the receipt: rewritten whole, as today.
   - Receipt `artifact_byte_hashes`: still full-file SHA-256, maintained as
     running digests seeded by the full load.
   - After every save, each checkpoint file and the receipt are byte-identical
     to what the current full-rewrite writer produces for the same state.
   - The transaction journal records, for each stage file, its previous length,
     its previous digest and the appended bytes, and the full next bytes of each
     small file and the receipt.
   - Commit order is unchanged: write the journal and fsync, apply, check
     against the journal's next state, retire the journal, sync the directory.
     Recovery rolls forward only when every file is exactly at its previous or
     next state (length and digest); anything else fails closed.
   - A full-payload journal left by the pre-change code is still recovered by
     the existing rules, because stopping run 2 can interrupt a commit.
     `validate_lp_checkpoint_format` accepts both journal forms.
5. **One authority check per side of the journal.** Run one check (the item 2
   guards plus ownership) before writing the journal and one before retiring it.
   This replaces the per-file checks in `_commit`. `_previous_hashes` uses the
   recorded digests and stat identities instead of re-reading files.
6. **Unchanged:** `lp_execution_material`; the at-rest file set, formats and
   receipt fields; retry, attempt, usage and failure accounting; lock
   ownership; `overwrite` archiving; finalization, artifacts and export. No
   config field is added.
7. **Operation.**
   - Stop any running LP invocation, then resume with the fixed code and
     `overwrite=false`.
   - Run 2's completed requests are reused only while config, prompts, schemas
     and model stay unchanged, including `max_concurrent_requests` 4.
   - Choosing a different concurrency changes the execution material, so it
     means LP regeneration (move-aside). That discards run 2's completed
     requests, about 45 minutes of model time at concurrency 4.

### Code-fix boundary

- If a run exposes a generic pipeline defect that needs no new config field, no
  stage-contract change and no resume-semantics change, Developer fixes it in
  curriculum-neutral code, with tests (`AC-020`, `AC-022`) and documentation
  through Documenter (`AC-024`).
- Anything that adds or changes a config field, a stage contract or resume
  behavior, adds stage selection, or enforces Term order in code comes back to
  Architect. The LP checkpoint contract above is such a change, designed here;
  any departure from it also comes back to Architect.

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
   - LP preflight, with no LLM calls and no writes under `kgs/`: build the LP
     selection, candidates and the full request population from the current
     AS+LC bundle and the committed config. It must build every candidate's
     request with no bound error.
   - The LP checkpoint fix must be in place before LP continues past run 2:
     code committed, self-checks passed, and a read-only load of run 2's real
     checkpoint directory with the new code (`validate_lp_checkpoint_format`)
     passed. Any running LP invocation is stopped first.
   - AS and LC are reused; check S_AS. LP resumes from run 2's checkpoints when
     the execution material is unchanged.
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
- `AC-017`: The committed CAPS config has
  `kgs.lp.evidence_limits.max_source_evidence_characters_per_sfi` 3000. The LP
  preflight builds one request per retained candidate pair with no bound error.
  `lp_validation_report.json` and the AS+LC+LP bundle validation pass with no
  errors, and `as_lc_lp_kg_bundle.json` exists.
- `AC-017` (checkpoint scale): on a synthetic store of 12,892 requests with
  payloads the size of run 2's (about 11 kB of stage rows per request), driving
  every save with stub calls and no model calls:
  - the median per-save time over the last 500 requests is within 2× of the
    median over the first 500;
  - between full verification points, no save, dispatch or transport check
    reads `lp_generation_requests.jsonl` or a whole checkpoint file;
  - the at-rest files and receipt equal those of the full-rewrite writer.
- `AC-017` (real run): run 2's completed requests are reused, with no repeated
  model call for any request that already has a response.
  - Per-request checkpoint time in the run log shows no upward trend with the
    number of completed requests.
  - Once 500 requests are complete, throughput over any 2-hour window is at
    least 70% of the model-bound rate. That rate is concurrency × 3600 ÷ the
    median per-request model seconds.
- `AC-022` (checkpoint fix): tests show each of the following:
  - at-rest bytes and receipt identical to the full-rewrite writer across
    dispatch, completion, promotion, retry, failure and recovery sequences;
  - crash recovery at each commit step of the new journal, and recovery of a
    pre-change full-payload journal;
  - unexplained file states fail closed;
  - a population-artifact content edit with mtime restored is detected
    (`st_ctime_ns`);
  - a checkpoint-file edit between saves is detected before the next dispatch
    or publication;
  - a mutated dispatched request is detected before dispatch or publication;
  - a caller's mutation of a returned record cannot change persisted bytes or
    store decisions;
  - full verification still streams every population byte at construction,
    at completion and in read-only loads;
  - per-save work (population bytes read and records re-proved) does not grow
    with the completed count;
  - a checkpoint directory written by the pre-change code loads and resumes.

  Existing tests that pin every-check streaming are updated to this cadence,
  not deleted.
- `AC-024`: `docs/pipeline/learning-progressions.md` and
  `docs/guides/running-and-debugging.md` describe the full-verification points,
  the guards between them, and the incremental, journaled commits with
  unchanged at-rest files.
- `AC-018`: For every buildsTowards edge, the source's (Grade rank, Term rank)
  is not later than the target's. Rank comes from `ordered_values` and the Term
  ancestor's canonical value.
- `AC-033`: The committed CAPS config has
  `kgs.lp.candidate_policy.budgets.max_candidates_per_sfi` 30 and
  `max_total_candidates` 14000, and loads as a `RunConfig`. The
  `lp_candidate_summary.json` from the final successful LP run records
  `max_candidates_per_sfi` 30, `max_total_candidates` 14000 and a
  `candidate_pair_bound` of 13860 for 924 eligible Skills, with
  `total_candidate_pairs` no greater than that bound.
- `AC-020`, `AC-021`: The cycle's diff touches no example config other than
  CAPS, and each of the six other example configs loads as a `RunConfig`. Any
  backend diff contains no curriculum names or curriculum-specific text.

## Build Plan

1. Take S0. Edit the CAPS `kgs.as` block to the contract above. Validate config
   loading and run the preflight.
2. Run AS with Stop A and run the AS checks. Iterate fixes, using AS
   regeneration, until they pass. Record the user's confirmation with S_AS.
3. Run LC with Stop B. Retry failures, run the LC checks and fix as needed.
4. Set the LP budgets (`AC-033`) and the LP evidence bound (3000). Confirm the
   config loads and run the LP preflight. Then run the full no-stop invocation
   and the LP checks. Fix with LP regeneration as needed, ending on a
   successful no-stop invocation.
   - Before LP continues past run 2, implement the LP checkpoint contract in
     this order: the guards and full-verification points, then record
     authority, then incremental commits.
   - Self-checks: the synthetic scale benchmark, byte equivalence with the
     full-rewrite writer, and a read-only load of run 2's checkpoints.
   - Then stop run 2 and resume LP with the fixed code.
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
  Skills of one Grade and Term, and it raises `LPFinalizationCycleError`. The
  larger budgets judge more same-Grade, same-Term pairs, which raises this risk.
  Fix it first in the prompt, then regenerate LP. A code-level cycle policy
  comes back to Architect.
- **LP candidate cap:** at 30 per Skill the per-Skill budget binds (bound
  13,860). The offline build keeps about 12,892 pairs. A smaller share of them
  is structurally plausible (38%, against 58% at 12 per Skill), so more
  `no_relation` outcomes are expected. The dropped counts are reported
  (`AC-019`) and are not a defect in themselves.
- **Runtime:** LP makes about 12,900 pairs × 2 calls (about 25,800 calls).
  - The original "a day or more" assumed model-bound throughput, which the
    pre-fix checkpoint store cannot reach.
  - After the fix, the run is bound by model calls: about 12,892 × 63 s ÷
    concurrency, which is about 56 hours at concurrency 4. The 63 s is two
    stages at a median of about 31.5 s each.
  - Raising `kgs.lp.max_concurrent_requests` shortens this in proportion,
    within provider rate limits. It changes the execution material, so it
    means LP regeneration and losing run 2's completed requests.
  - Interrupted runs resume with `overwrite=false` when the config is
    unchanged.
- **Checkpoint integrity change:** moving full proofs to defined points could
  hide a change made between them. The stat-identity and per-request guards,
  the full checks at completion and in every read-only load, the record-copy
  rule and the `AC-022` tests cover this. Coarse file timestamps can only cause
  extra full verifications, never a missed change, because any stat difference
  triggers one.
- **Recovery code risk:** the new delta journal and the retained pre-change
  journal path are the riskiest code. Crash-injection tests at every commit
  step are required before resuming the real run.
- **LP request size:** requests carry each SFI's full audit context, mostly
  dedup merge reasons, for the endpoints and their ancestors. Payloads run about
  43k characters at the median and up to about 61k. That fits model context
  windows but dominates LP input cost, which is within the no-cost-limit
  constraint. Trimming the audit context would mean regenerating AS or changing
  code, so it comes back to Architect.
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
- **Letting LP run 2 finish unchanged, or lowering the `AC-033` budgets:**
  rejected by the user (2026-10-11). Overhead grows with the square of the
  request count, so 30 days was a lower bound. Even 12 per Skill and 5000 in
  total would take about 12 days.
- **Only cutting the number of verifications per save:** rejected. Every save
  would still re-prove every record, re-hash the request behind each row, and
  rewrite the whole store plus a full-state journal. Late in the run that is
  about 150 MB written twice per save, roughly 15 TB in all, and per-save cost
  still grows with progress.
- **A new append-only checkpoint format (v2):** rejected. It would change the
  at-rest files, the finalization, artifact and format-validation consumers,
  and the tests, and it would discard run 2's progress or need a migration.
  The byte-identical incremental writer scales the same way without that.
- **Dropping per-save checks entirely:** rejected. It gives up the fail-closed
  design instead of moving full proofs to defined points.
