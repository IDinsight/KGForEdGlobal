<!-- STANDARDS
Artifact: DEVELOPMENT
Cycle: run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8
-->

# Development Plan

`Cycle`: `run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8` `Mode`: `STEPWISE`
`User Style`: `tony` `User Style Locked`: `true`
`Status`: `IN_PROGRESS` `Verification Cadence`:
`AFTER_IMPLEMENTATION` `Current Increment`: `NONE`

## Implementation Contract

- `Scope`: `.standards/docs/scope/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md`
- `Architecture`: `.standards/docs/specs/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md`
- `Request`: Run `create_kgs` (AS, LC, LP) on the user-verified CAPS English HL
  R-3 DocumentIR in `results/kg_for_ed/<doc_key>/`; add a config-only
  Sub-strand (Standard Grouping) level between Skill Area and Skill; align AS,
  LC and LP with the Learning Commons definitions; keep LP Term order in
  prompt text only; stage the run with temporary local stops and a user AS
  confirmation; record a results review. CAPS changes go only in
  `examples/funda_wande/config_english_curriculum.json`.

## Build Steps

### DEV-001 — Sub-strand config and preflight

`Status`: `IN_PROGRESS` `Depends On`: `NONE`
`Acceptance`: `AC-001, AC-002, AC-004, AC-025, AC-030, AC-005, AC-026, AC-027, AC-008, AC-028, AC-029, AC-010`

**Goal**

Take snapshot S0, then edit the CAPS `kgs.as` block to the Architecture's
`kgs.as` config contract and instruction contract, and pass the no-LLM
preflight before any paid run.

**Affected Area**

`examples/funda_wande/config_english_curriculum.json` (`kgs.as` only; tab
indentation and key order preserved). Local, uncommitted scripts in
git-ignored `logs/kg_construction/dev001/`.

**Expected Outcome**

- S0 manifest (path + SHA-256 of every file under `extraction/`,
  `verification/`, `stitching/`) saved as
  `logs/kg_construction/dev001/s0_manifest.json`; no `kgs/` directory exists
  yet.
- New `Sub-strand` statement type (`Standard Grouping`) with the 21 canonical
  values and every printed variant found in the 48 tables as an alias;
  type aliases are spellings of the type name only.
- `identity_scope_statement_types`, `sfi_has_child_statement_type_hierarchy`,
  `sfi_has_child_parent_policy`, `sfi_dedup_context_statement_types` and the
  `Skill` description updated per the contract; `max_rows_per_table_window`
  `null`.
- The five AS instruction fields rewritten per the instruction contract
  (hierarchy, per-Skill-Area sub-strand lists with term exceptions, carry-over
  across rows and page breaks, the non-sub-strand list, the Skill Area vs
  Sub-strand label clash, lead-in rule and other exclusions kept).
- `kgs.lc`, `kgs.lp`, `kgs.metadata`, `overwrite`, page stages unchanged.

**Self-Check**

From `backend/`: load the CAPS config and the six other example configs as
`RunConfig` (with `output_dir` redirected so loading creates no directory);
diff the config against `HEAD` to show only `kgs.as` changed; run AS steps 3-4
with `save_fp` under `logs/` and expect 48 whole-table windows plus 7 heading
windows; run the heading check (per table, non-bullet lines before the
ASSESSMENT row yield exactly the scope's expected sub-strands, each
normalizing via `normalize_controlled_value_key` to a `Sub-strand` canonical
value or alias): 152 matched, zero unmatched printed sub-strand headings.

**Implementation Notes**

Config edited 2026-10-09 by scratchpad `edit_caps_kgs_as.py` (tab-indented
JSON round-trips byte for byte; only the 11 contract keys of `kgs.as`
change). The 21 canonical values carry the printed variants with distinct
normalized keys as aliases (`Uses language to develop concepts in all
subjects`, `Emergent reading`, `Emergent reading skills (taught in Shared and
Group Guided Reading lessons)`, `Phonological/ Phonemic Awareness`, `Emergent
Handwriting`, the three Phonics headings, and the two bracketed
Paired/Independent Reading variants); colon, period and case variants share a
key. Type aliases: `SUB-STRAND`, `Substrand`, `SUBSTRAND`.

Tools (run from the repo root so settings load the root `.env`):
`logs/kg_construction/tools/manifest.py` (`create` / `compare` SHA-256
manifests; never overwrites a manifest) and
`logs/kg_construction/dev001/preflight.py` (config contract and loads, AS
steps 3-4, heading check; writes only into `--out-dir`). Developer trial runs
wrote to the scratchpad only: preflight passed (55 windows = 48 whole tables
+ 7 blocks; 152 sub-strands; 924 expected Skills after joining the two
lead-ins; every other non-bullet line is a column label, grouping heading,
week range, Handwriting sub-heading, schedule, teacher note or the Grade R
Emergent Handwriting `Daily activities ...` line; no bullet without a
sub-strand or under a grouping-only heading); S0 trial hashed 705 files and
compared identical. The recorded self-check is the user's run.

---

### DEV-002 — AS run and AS checks

`Status`: `PENDING` `Depends On`: `DEV-001`
`Acceptance`: `AC-002, AC-003, AC-004, AC-025, AC-005, AC-026, AC-027, AC-007, AC-008, AC-028, AC-029, AC-010, AC-030`

**Goal**

Run AS (steps 1-10) with Stop A and pass the AS structure and coverage checks,
fixing defects by config and AS regeneration.

**Affected Area**

User-run `create_kgs` with a local Stop A in `entries/create_kgs.py` (never
committed); `results/kg_for_ed/<doc_key>/kgs/`; local check scripts in
`logs/kg_construction/dev002/`; CAPS `kgs.as` wording if a defect traces to it.

**Expected Outcome**

`as_validation_report.json` passes with no errors, and the AS checks pass:

- structure: 4 Grades under the root, 4 Terms each, the expected 3 Skill
  Areas per Term, exactly the expected Sub-strand set per Skill Area (152),
  statement types `Standard Grouping` / `Standard`, each Skill's grade equal
  to its Grade ancestor's mapping (`K`, 1, 2, 3);
- coverage: one exported Skill per expected DocumentIR bullet (lead-ins joined
  with their fragments), keyed by Grade, Term, Skill Area, sub-strand in force
  and normalized text, under the matching ancestor chain; no extra Skill or
  grouping; nothing from heading windows, resources tables or ASSESSMENT rows;
- `has_child_unresolved_edges.json` empty, no root-fallback edge, and no
  expected item dropped by `sfi_merge_conflicts.json` or
  `sfi_merge_needs_review.json`.

Each defect, its cause, fix and the AS regeneration (move `kgs/` to
`kgs_superseded/<UTC>-as/`, or in-place rerun for hasChild-only text changes)
is logged in this step.

**Self-Check**

The AS check scripts over the run outputs; every mismatch decided against the
DocumentIR row text and logged.

**Implementation Notes**

Two pauses: after handing over the run instructions (Stop A edit and command),
and after the analysis.

---

### DEV-003 — User AS confirmation and S_AS

`Status`: `PENDING` `Depends On`: `DEV-002`
`Acceptance`: `AC-011`

**Goal**

The user reviews the checked AS results; defects they find are fixed as in
DEV-002 and AS is regenerated and rechecked before confirmation.

**Affected Area**

`results/kg_for_ed/<doc_key>/kgs/` AS artifacts; this plan.

**Expected Outcome**

The user's confirmation is recorded here with the S_AS manifest (SHA-256 of
the Architecture's S_AS file list) of the AS results reviewed.

**Self-Check**

S_AS recomputed from the files on disk at confirmation and saved as
`logs/kg_construction/dev003/s_as_manifest.json`.

---

### DEV-004 — LC run with zero failed requests

`Status`: `PENDING` `Depends On`: `DEV-003`
`Acceptance`: `AC-012, AC-014, AC-015, AC-016, AC-031`

**Goal**

Run AS+LC with Stop A removed and Stop B in place, retry failed LC requests
until none remain, and pass the LC checks.

**Affected Area**

User-run `create_kgs` with a local Stop B (never committed); LC artifacts in
`kgs/`; local scripts in `logs/kg_construction/dev004/`; CAPS `kgs.lc` wording
only if an `AC-016`/`AC-031` finding requires it (then LC regeneration by
move-aside).

**Expected Outcome**

S_AS reproduced byte for byte; `as_lc_validation_report.json` passes with no
errors; `lc_generation_failures.json` holds no unresolved failure; every Skill
has at least one supports edge; a sample of LCs checked against their Skills'
text shows single learner skills with no teacher guidance, routines,
assessment suggestions, contact time or scheduling labels.
`lc_max_failure_rate` stays 0.05.

**Self-Check**

S_AS comparison script; LC check script over the LC artifacts; sampled LC
review recorded here.

---

### DEV-005 — LP run and no-stop final invocation

`Status`: `PENDING` `Depends On`: `DEV-004`
`Acceptance`: `AC-012, AC-013, AC-017, AC-018, AC-032`

**Goal**

Run `create_kgs` with no stop and an unmodified `create_kgs.py`, pass the LP
checks, and fix any Term-order or relationship defect through the CAPS
`kgs.lp` instructions plus LP regeneration.

**Affected Area**

User-run `create_kgs`; LP artifacts in `kgs/`; local scripts in
`logs/kg_construction/dev005/`; CAPS `kgs.lp` producer/checker instructions
only if a finding requires it.

**Expected Outcome**

S_AS reproduced; `lp_validation_report.json` and the AS+LC+LP bundle
validation pass with no errors and `as_lc_lp_kg_bundle.json` exists; no
buildsTowards edge goes to an earlier (Grade, Term); a sample of buildsTowards
and relatesTo edges checked against the Learning Commons meanings; the last
successful invocation ran with no stop and `git diff` shows no change to
`create_kgs.py`.

**Self-Check**

LP Term-order script (ranks from `ordered_values` and each Skill's Term
ancestor); S_AS comparison; sampled LP review recorded here;
`git diff --stat -- backend`.

---

### DEV-006 — Close-out checks and results review

`Status`: `PENDING` `Depends On`: `DEV-005`
`Acceptance`: `AC-001, AC-002, AC-012, AC-013, AC-019, AC-020, AC-021, AC-023`

**Goal**

Confirm the upstream outputs and confirmed AS are unchanged, the diff stays
within bounds, and the existing suite and lint pass; write the results review.

**Affected Area**

This plan (results review); `logs/kg_construction/dev006/`.

**Expected Outcome**

- S0 identical to the pre-run snapshot; S_AS identical to the confirmed one.
- The cycle diff touches no example config other than CAPS and no backend
  file (or, if a code fix was approved, no curriculum names in it); the six
  other example configs load as `RunConfig`; the CAPS config loads with
  `start_page` 35 / `end_page` 135 and `output_dir` `results/kg_for_ed`.
- `make test` and `make lint` pass from `backend/`.
- Results review (`AC-019`) recorded here: counts by statement type and
  relationship type, LP dropped-pair counts by budget, the defect log with
  resolutions, and sampled Skills, LCs and LP relationships checked against
  the source.

**Self-Check**

S0/S_AS comparison scripts; config load script; `git diff` against `19f44f5`
for paths and curriculum terms; `make test`, `make lint`.

---

## Plan Notes

- The user runs every command (user decision, 2026-10-09): `create_kgs`
  invocations (from `backend/`,
  `.venv/bin/python src/kgfeg/entries/create_kgs.py ../examples/funda_wande/config_english_curriculum.json`),
  Stop A / Stop B edits and their removal, move-asides, snapshots, check
  scripts, `make test` and `make lint`. Developer edits the CAPS config,
  writes the scripts, hands over exact instructions, then pauses; the user
  runs them and returns, and Developer reviews the saved outputs. Scripts
  write their results to files in `logs/kg_construction/devNNN/` so the
  review does not depend on terminal output. Stops are never committed
  (`AC-013`).
- Move-aside regeneration follows the Architecture's run and regeneration
  contract, run by the user only when no `create_kgs` process is running;
  moved files and the LP lock are never deleted or edited. `kgs.overwrite`
  stays `false`.
- Check scripts and their outputs live in git-ignored
  `logs/kg_construction/devNNN/` so Tester and Reviewer can rerun them; they
  never write into `extraction/`, `verification/`, `stitching/` or `kgs/`.
- No KG code change is planned. A generic pipeline defect needing a code fix
  is a material plan revision (new `DEV-NNN`, reapproval) within the
  Architecture's code-fix boundary; its formal tests (`AC-022`) are
  Tester-owned. Anything beyond that boundary routes to Architect.
- Test commands run from `backend/` with
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo root>`.
