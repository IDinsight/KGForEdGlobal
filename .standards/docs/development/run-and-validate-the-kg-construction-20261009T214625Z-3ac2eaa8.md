<!-- STANDARDS
Artifact: DEVELOPMENT
Cycle: run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8
-->

# Development Plan

`Cycle`: `run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8` `Mode`: `STEPWISE`
`User Style`: `tony` `User Style Locked`: `true`
`Status`: `IN_PROGRESS` `Verification Cadence`:
`INCREMENTAL` `Current Increment`: `1`

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

`Status`: `DONE` `Depends On`: `NONE`
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

Self-check run 2026-10-10 02:40 UTC by the user from the repo root on
`ec6df04` (config committed; `logs/` untracked); outputs reviewed by
Developer:

- S0 (`logs/kg_construction/tools/manifest.py create`):
  `logs/kg_construction/dev001/s0_manifest.json`, 705 files (401
  `extraction/`, 301 `verification/`, 3 `stitching/`), identical to the
  Developer trial hashes; `results/kg_for_ed/<doc_key>/` holds only
  `extraction/`, `verification/`, `stitching/` (no `kgs/`), so S0 predates
  every KG run.
- Preflight (`logs/kg_construction/dev001/preflight.py --out-dir
  logs/kg_construction/dev001`): `passed: True`, exit 0. Configs: CAPS
  `output_dir` `<repo>/results/kg_for_ed`, both page stages 35/135, loads as
  `RunConfig` (AC-001); `overwrite` false, `lc_max_failure_rate` 0.05,
  `max_rows_per_table_window` null; versus `da7ad46` only `kgs.as` changed, in
  exactly the 11 contract keys; the six other example configs are unchanged
  and load. Windows: 55 = 48 whole-table + 7 block, no input warnings; the
  largest window payload is about 65k characters (7.8k source text). Headings:
  152 sub-strands matched with none missing, duplicated or unexpected; 0
  unclassified lines; 924 expected Skills; both lead-ins found with 4
  fragments each (`Emergent reading skills`, Grade 1 Term 1; `Phonics`,
  Grade 3 Term 2). Per-table outline in `preflight_summary.txt`.

---

### DEV-002 — AS run and AS checks

`Status`: `DONE` `Depends On`: `DEV-001`
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

AS run 1 handed over 2026-10-10 on `ec6df04` plus the local Stop A edit:
in `build_kgs`, right after the step-10 `logger.success(...)` AS export count,
`sys.exit("Temporary Stop A: ...")`. Run from the repo root with output teed
to `logs/kg_construction/dev002/as_run_1.log`. Check scripts are written
against the actual run outputs after the run.

Interim review 2026-10-10 about 12:00 UTC, user-requested while AS run 1
was in hasChild (read-only; scratchpad `interim_as_review.py`; not the
formal self-check): run started 02:43 UTC; extraction (55 windows) took
about 2.5 h, dedup and finalization about 1.7 h. Extraction: 1220
candidates (48 Grade, 48 Term, 48 Skill Area, 152 Sub-strand, 924 Skill);
final: 4 Grade, 16 Term, 48 Skill Area, 152 Sub-strand, 924 Skill; merge
conflicts and needs-review empty. All 924 Skills equal a printed bullet
(whitespace-collapsed text) under the expected Grade, Term, Skill Area and
sub-strand in force, both lead-ins joined with their fragments; every
table's Sub-strand set equals the expected set, all with canonical values;
the `AC-029` example is two Skills (Emergent reading skills; Begins to make
meaning of written text). hasChild: 559 of 1144 resolved, all to the
structurally correct parent, none unresolved, checker passed every draft
unchanged; for all 1140 non-Grade children the single correct parent is in
the (truncated, max 24) candidate set. Log: one checker correction (Grade 2
Term 1 Reading and Phonics window, issues=2, final output still exact), one
integrity-check retry, three benign repeated-description warnings (the
`AC-029` pair). Pace about 32 s per hasChild request.

AS run 1 result (user-run, ended at Stop A as intended): started 02:43:55,
ended 14:56:50 UTC (12 h 13 min). hasChild: 1144 edges, 0 checker
corrections, 4 root edges (the Grades), 0 unresolved, 0 root fallbacks;
1044 of 1144 candidate sets truncated to 24, the correct parent kept in
all. Export: 1 framework, 1144 items, 1144 hasChild relationships;
`as_validation_report.json` `passed: true`, no errors, 0 unresolved
fallback relationships. Usage (`kg_run.json`): 3010 requests, 80.75M
input and 2.02M output tokens, no prompt caching; hasChild producer 1476
requests for 1144 children (output retries), checker 1144.

Developer review 2026-10-10 (read-only; `logs/kg_construction/dev002/as_checks.py`
trial output in the scratchpad, all 6 checks passing, plus ad hoc reads):
merges only fold the 12 Grade and 3 Term banner occurrences per node (no
Skill, Sub-strand or Skill Area merged); every Skill reads as a learner
statement (104 distinct opening words, all learner verbs such as Uses,
Reads, Listens; `Can`, `With help`, `Role plays` are printed that way); no
audit flags; confidence 0.85-0.97, the lowest on Reading and Phonics
sub-strands whose names recur in schedule lines and the ASSESSMENT band.
Observations for the user's review, matching the design rather than
defects: Learning Commons nodes carry the printed label as `description`
(for example a Phonics Sub-strand reads `Daily Phonic Activities of 15
minutes:`; Grade, Term and Skill Area read as the uppercase banner text),
while the canonical value sits in the item metadata; the two lead-in Skills
keep line breaks between the lead-in and its fragments.

Self-check run 2026-10-10 15:08 UTC by the user from the repo root on
`03aed4b` (clean tree, no stop in `create_kgs.py`; `kgs/` from AS run 1 as
reviewed above); outputs reviewed by Developer:

- `logs/kg_construction/dev002/as_checks.py --out-dir logs/kg_construction/dev002`:
  `passed: True`, exit 0, all 6 checks with 0 problems, details identical to
  the Developer trial. AC-002/AC-003: `document_ir_fp` is
  `<repo>/results/kg_for_ed/<doc_key>/stitching/document_ir.json`; the AS
  validation report passed with no errors. AC-007: 1144 edges, 4 root edges
  (exactly the Grades), 0 root fallbacks; unresolved edges, unresolved items,
  merge conflicts and needs-review all empty; final, hasChild-export and
  Learning Commons edge sets equal. AC-004/AC-025/AC-030: 4 Grade, 16 Term,
  48 Skill Area, 152 Sub-strand, 924 Skill; each item has one parent of the
  level above, Grade children are Term 1-4, Term children the expected Skill
  Areas, Skill Area children exactly the expected Sub-strands; groupings
  `Standard Grouping` and Skills `Standard` in records, items and nodes;
  every grouping has a canonical value; identity scopes equal the ancestor
  chain. AC-010: every item's local grade is its Grade ancestor and its
  Learning Commons grade the mapped value (Skills: K 312, 1 188, 2 213,
  3 211). AC-005/AC-026/AC-027/AC-029: 924 of 924 printed bullets matched by
  (Grade, Term, Skill Area, Sub-strand in force, whitespace-collapsed text),
  no extra Skill, no fragment-only Skill, both lead-ins joined, the `AC-029`
  pair under Begins to make meaning of written text and Emergent reading
  skills. AC-008/AC-028/AC-027: all 1220 candidate citations are in their own
  requirements table; Grade/Term/Skill Area cite banner rows printing their
  label, Sub-strands the row printing their heading, Skills the row printing
  their bullet, all before the ASSESSMENT row.
- `as_outline.md` (1265 lines, the export tree in printed order with PDF
  pages) is identical to the trial outline apart from its timestamp; it is
  the input to the user's DEV-003 review.
- Interim S0 compare after AS (`manifest.py compare`,
  `logs/kg_construction/dev002/s0_compare_after_as.json`): identical, 705
  files, none added, removed or changed.

---

### DEV-003 — User AS confirmation and S_AS

`Status`: `DONE` `Depends On`: `DEV-002`
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

**Implementation Notes**

User confirmation 2026-10-10 ("continue, AS confirmed"), after reviewing
`logs/kg_construction/dev002/as_outline.md` (generated 15:08 UTC by the
DEV-002 self-check from AS run 1, `kgs/` files last written 14:56 UTC). No
defects reported; no change requested to the printed labels in the node
descriptions (the observation raised before confirmation). S_AS covers the
Architecture's 13 files: `sfi_final_records.json`,
`sfi_final_contexts.json`, `has_child_edges_final.json`,
`has_child_unresolved_edges.json` and the nine `as_*` exports. A Developer
trial hash of the same files (scratchpad) was taken right after the
confirmation for comparison with the user's manifest.

Self-check run 2026-10-10 15:12:48 UTC by the user from the repo root on
`442beb4` (clean tree): `manifest.py create` wrote
`logs/kg_construction/dev003/s_as_manifest.json` (root
`<repo>/results/kg_for_ed/<doc_key>/kgs`, 13 files), identical to the
Developer trial hashes taken after the confirmation, so the files are the
ones reviewed. S_AS combined digest (SHA-256 of the sorted
`<path> <sha256>` lines): `deb631a3b76cea871f1b3ca4ebea4b45a7ff8f30d5ad36e4a8eb0a9fafb6e649`. Per-file SHA-256:

  - `as_entity_provenance.json`: `f12d141fd68154d2278bdfa9415355edbc2c42a8df37b3671e8f6584aa035fd3`
  - `as_kg_bundle.json`: `ad0b9d72197b9230190cb4b6ff3af8f451566884ad918d0d16faf9f40d6b2003`
  - `as_nodes.jsonl`: `25159132725dd464cebba394181ae6f4c5c2da7e717137306a41bed34ad54b52`
  - `as_relationships.jsonl`: `b2b30734fa8cf9730e988328f8179a1b74206acaba9ab761d38265aac35085e6`
  - `as_relationships_has_child.jsonl`: `a8d652ebf5c02665006a3b3675703c462febcea3b563312c88408cdfa3a6406d`
  - `as_standards_framework.json`: `9cf5a06dfa0c1a1b63bafd46b738a8d52ab57376ccd68982054f2f2c094913b8`
  - `as_standards_framework_items.jsonl`: `865f92f88ea7e317bac364c3568135331661cf1d8925da2a16da8c7b26ad1d50`
  - `as_unresolved_items.json`: `f6ffdd6b47267163ddd83fc9b3fa2207a03d603dfd20e9bbef66fb219c479fe2`
  - `as_validation_report.json`: `c262544abb019e2d142cf997ee9bec4ed017288862f1a62c18422b20d2421f25`
  - `has_child_edges_final.json`: `a15e51ba5405ddfb331814fa31699d5074eaf06563aab768fe7415541419c13c`
  - `has_child_unresolved_edges.json`: `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
  - `sfi_final_contexts.json`: `06e685f7d6faa0c9b0162a22e87b2c7e9752689b8ce67c8eecfdd4f0d2bc2d8a`
  - `sfi_final_records.json`: `a92329ca9953f98fbb0f49b00b6aa4311a14d8cd9537e58aee0004a71d1b54fd`

---

### DEV-004 — LC run with zero failed requests

`Status`: `DONE` `Depends On`: `DEV-003`
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

**Implementation Notes**

LC run instructions handed over 2026-10-10 on `442beb4` (clean tree, Stop A
already removed by the user, never committed) plus the local Stop B edit:
in `build_kgs`, after the "LP requires a passed, error-free AS+LC validation
report" check and before `if not config.overwrite:` /
`reuse_as_lc_lp_kg`, `sys.exit("Temporary Stop B: ...")`. Runs from the repo
root teed to `logs/kg_construction/dev004/lc_run_<n>.log`. The user reruns
the same command while the last `Decomposed LC-source SFIs` log line reports
`failed_requests` above 0 (retries only failed requests), then pauses for
review. AS must be reused without LLM calls (extraction resume, merge and
hasChild artifacts loaded); signs of AS regeneration mean stop the run and
report. LC check scripts are written against the actual outputs after the
run.

LC run 1 (user-run 2026-10-10 15:18-18:58 UTC, `lc_run_1.log`, ended at
Stop B as intended): AS reused with no AS LLM calls (extraction skipped
55/55, merge report and hasChild artifacts loaded, AS export loaded;
`sfi_final_records.json` rewritten byte-identical); a Developer trial
`manifest.py compare` against S_AS was identical (13/13). LC generation:
924 requests, 924 succeeded, 0 failed (`lc_generation_failures.json`
empty), 26 validator corrections, 1209 skill claims (splits 1: 675, 2: 219,
3: 24, 4: 6). Dedup: 987 unique texts, 4953 candidate pairs, 199 judge
requests, 515 judged same, 72 merges refused by the chaining guard; 698
LCs, 1208 supports edges; every Skill has at least one supports edge; every
LC's edges equal its claiming Skills; `as_lc_validation_report.json` passed
with no errors. LC texts read as learner skills; mentions of the teacher or
a pace ('per week') come from the Skills' own wording.

Defect D-LC-1 (`AC-016`, found in Developer review of LC run 1): a merged
LC carries the elected member's own examples, qualifiers and quantities
(canonical election is most-claimed text), so its text is not supported by
other Skills it supports. 146 of the 1208 supports links (96 LCs) add
content found in neither the Skill nor its own claim; clear cases:
'Recognises vowel digraphs such as 'oo' as in moon and 'ee' as in tree' on
Skills listing 'oy', 'oi', 'ay', 'ai'; 'Spells words correctly using phonic
knowledge in informal tests, in dictation and in all written work' on
'Spells words correctly using phonic knowledge'; 'Listens without
interrupting, showing respect for the speaker' on 'Listens without
interrupting'; Grade 3 Term 3 'Edits own story ... (at least 12
sentences)' where the Skill prints 'ten or more sentences'. Cause: the CAPS
`lc_dedup_instructions` treat scope-widening repeats as the same skill. A
smaller generation-side variant: about 39 single-Skill LCs reword the
Skill, a few adding scope (for example 'in own writing'). Proposed fix
(within this step: `kgs.lc` wording plus LC regeneration by move-aside):
merge only interchangeable skill texts, and tell the generator not to add
unstated qualifiers or ancestor scope. Awaiting the user's choice before
editing (2026-10-10).

D-LC-1 fix (user chose option 1, stricter merging, 2026-10-10): root cause
confirmed in the prompts. The generic LC dedup judge policy is already strict
(SAME only when action, object, direction and every scope qualifier match),
but the CAPS `lc_dedup_instructions`, which override it, said repeats that
widen scope "are the same skill when the stated action and object match".
Config edit (CAPS file only; `kgs.lc.lc_dedup_instructions` rewritten to
apply the generic policy strictly and judge non-interchangeable repeats
DISTINCT, with the D-LC-1 cases as examples; one paragraph added to
`kgs.lc.generation_instructions`: state only what the Skill prints, no
ancestor scope such as 'in own writing'). No other key changed; the config
loads as a `RunConfig` (`lc_max_failure_rate` 0.05, `overwrite` false).

LC regeneration handed over 2026-10-10 as the design's move-aside done in
two steps at the user's request: copy the whole `kgs/` (AS + LC run 1) to
`kgs_superseded/20261010T185810Z-lc/` (named for LC run 1's end), verify the
copy with `diff -rq`, then remove the 18 LC files from `kgs/` (`lc_*`,
`learning_components.jsonl`, `as_lc_*`; no `lp_*` files or LP lock exist).
The copy keeps LC run 1 as D-LC-1 evidence. Then LC run 2 with Stop B still
in place, teed to `logs/kg_construction/dev004/lc_run_2.log`.

LC run 2 (user-run 2026-10-10 19:30-22:38 UTC on `d249c7e`, the config fix
committed; `lc_run_2.log`; ended at Stop B): the backup
`kgs_superseded/20261010T185810Z-lc/` holds all 51 run-1 files (its
`lc_generation_summary.json` is run 1's: 698 LCs, 1208 edges). AS reused
with no AS LLM calls; a Developer trial S_AS compare was identical (13/13).
Generation: 924 of 924 succeeded, 0 failed, 22 validator corrections, 1204
claims. Dedup: 984 unique texts, 4859 candidate pairs, 195 judge requests,
89 judged same (run 1: 515), 1 chaining-guard conflict (run 1: 72); 912
LCs (run 1: 698), 1204 supports edges, 152 LCs shared by 2-8 Skills.
`as_lc_validation_report.json` passed with no errors.

D-LC-1 resolved: the merged-LC screen flags 16 links (13 LCs), down from
146 (96 LCs), and all 16 are rewordings (clarity/clarification,
tense/tenses, learned/learnt, sequence/string, 'phonic words'/'words from
phonics lessons'). Each run-1 case now has its own LC per distinct Skill
wording (the four vowel-digraph Skills, 'Listens without interrupting' with
and without 'showing respect for the speaker', 'ten or more' versus 'at
least 12 sentences', the two 'Spells words correctly' variants, the seven
word-bank variants, 'Begins to' versus 'Continues to build a sight
vocabulary'); merges remain only for identical or interchangeable wording.
No LC adds ancestor scope such as 'in own writing'; the single-Skill
differences are inflection and linking words. LCs naming the teacher or a
pace carry the Skill's own printed wording (for example 'Copies at least
two letters of joined script or cursive writing per week').

LC check script: `logs/kg_construction/dev004/lc_checks.py` (S_AS compare,
AS+LC validation, zero failures and `lc_max_failure_rate` 0.05, supports
coverage and claimant equality; review lists for merged-LC additions and
teacher/timing words; `lc_outline.md`, every Skill with its LCs). Developer
trial in the scratchpad: all 3 checks pass. The recorded self-check is the
user's run.

Self-check run 2026-10-11 00:16 UTC by the user from the repo root on
`d249c7e` plus local Stop B (`kgs/` from LC run 2): `lc_checks.py --out-dir
logs/kg_construction/dev004` `passed: True`, exit 0, identical to the
Developer trial. AC-012: `s_as_compare.json` identical, 13/13. AC-014/AC-015:
`as_lc_validation_report.json` passed, no errors (912 LCs, 1204 supports,
1144 hasChild); `lc_generation_failures.json` empty, 0 failed SFIs;
`lc_max_failure_rate` 0.05. AC-015/AC-031: 924 of 924 Skills have supports
edges (LCs per Skill 1: 679, 2: 215, 3: 25, 4: 5); every LC's edges equal its
claiming Skills; no duplicate or non-Skill targets. Review lists: 16 merged
links (the rewordings above), 41 LCs with teacher or timing words (Skill
wording such as 'with the teacher' or 'per week'). AC-016/AC-031 content
judgement: the Developer review above (D-LC-1 resolved; single learner
skills; no teacher guidance, routines, assessment, contact time or
scheduling labels).

Caps and limits in the AS and LC runs (user question, 2026-10-11): neither
log reports truncation or budget drops. Config caps off: whole-table
extraction windows, unsplit dedup review sets, no skills-per-Skill or
skill-length limits. Applied: hasChild candidate sets are bounded to 24
including the root (1044 of 1144 truncated; the exact-scope parent is
indispensable and was present in all, with 0 corrections and 0 unresolved);
LC dedup compares all pairs only within a Sub-strand of up to 12 skill
texts (26 of 152 Sub-strands are larger, where lexical rules alone nominate
pairs; this can only miss merges, never add one). Context-only limits: up
to 2 neighbouring headings per extraction window, evidence labels cut to
180 characters, at most 12 section-path labels (the DocumentIR has up to
7). Every LC and hasChild request carried the full Skill text (924/924,
1144/1144). Next, LP caps candidates at 12 per Skill and 5000 in total
(924 x 12 / 2 = 5544, so the total binds) and evidence at 8 LCs, 4 items
and 2000 characters per Skill; `AC-019` reports the dropped pairs.

---

### DEV-005 — LP run and no-stop final invocation

`Status`: `IN_PROGRESS` `Depends On`: `DEV-004, DEV-009`
`Acceptance`: `AC-012, AC-013, AC-017, AC-018, AC-032, AC-033`

**Goal**

Set the LP candidate budgets to 30 per Skill and 14,000 in total (`AC-033`)
and the LP evidence bound to 3000 (`AC-017`) before the next LP run, pass the
offline LP request-build preflight, then run `create_kgs` with no stop and an unmodified
`create_kgs.py`, pass the LP checks, and fix any Term-order or relationship
defect through the CAPS `kgs.lp` instructions plus LP regeneration. LP
continues past run 2 only with the LP checkpoint fix (DEV-007 to DEV-009)
committed and its self-checks passed: the user stops run 2, a read-only load of
run 2's checkpoint directory with the new code
(`validate_lp_checkpoint_format`) passes, and LP resumes from run 2's
checkpoints with the execution material unchanged (concurrency stays 4).

**Affected Area**

CAPS `kgs.lp.candidate_policy.budgets` (and `max_concurrent_requests` only as
the user decides before LP starts); user-run `create_kgs`; LP artifacts in
`kgs/`; local scripts in `logs/kg_construction/dev005/`; CAPS `kgs.lp`
producer/checker instructions only if a finding requires it.

**Expected Outcome**

S_AS reproduced; `lp_validation_report.json` and the AS+LC+LP bundle
validation pass with no errors and `as_lc_lp_kg_bundle.json` exists; no
buildsTowards edge goes to an earlier (Grade, Term); a sample of buildsTowards
and relatesTo edges checked against the Learning Commons meanings; the last
successful invocation ran with no stop and `git diff` shows no change to
`create_kgs.py`; `lp_candidate_summary.json` records `max_candidates_per_sfi`
30, `max_total_candidates` 14000 and `candidate_pair_bound` 13860, with
`total_candidate_pairs` within that bound. After the checkpoint fix, run 2's
completed requests are reused with no repeated model call for any request that
already has a response; per-request checkpoint time in the run log shows no
upward trend with the completed count; once 500 requests are complete,
throughput over any 2-hour window is at least 70% of the model-bound rate
(concurrency × 3600 ÷ median per-request model seconds).

**Self-Check**

Config load with the new budgets and evidence bound; LP preflight
(`logs/kg_construction/dev005/lp_preflight.py`: selection, candidates and the
full request population built offline with no bound error); read-only load of
run 2's checkpoints with the new code after run 2 stops; LP run-log throughput
and reuse script (checkpoint seconds by completed count, 2-hour windows, no
model call for a request that already had a response); LP Term-order script
(ranks from `ordered_values` and each Skill's Term ancestor); S_AS comparison;
sampled LP review recorded here; `git diff --stat -- backend`.

**Implementation Notes**

Budgets set 2026-10-11 in the CAPS config: `kgs.lp.candidate_policy.budgets`
`max_candidates_per_sfi` 30, `max_total_candidates` 14000 (JSON integers);
the only changed key versus `9554a7d`; the config loads as a `RunConfig`.
`kgs.lp.max_concurrent_requests` stays 4 (user decision, 2026-10-11, asked
before LP starts per the design; it is LP execution material). The offline
candidate build with these budgets keeps 12892 pairs (bound 13860).

LP run 1 handed over 2026-10-11: the user commits the budget change, confirms
`create_kgs.py` has no local edits (Stop B already removed, never
committed), and runs from the repo root under `caffeinate -i`, teed to
`logs/kg_construction/dev005/lp_run_1.log`. AS and LC must be reused (AS
signals as before; LC `Decomposed LC-source SFIs ... resumed=924` and
`Grouped duplicate skills ... (resumed=195)`). A stop with
`LPGenerationFailed` (a request out of retries) is rerun unchanged
(`lp_run_<n>.log`; resumes from checkpoints); any other failure, including
`LPFinalizationCycleError`, is kept as is and reported. LP check scripts are
written against the actual outputs after the run.

LP run 1 (user-run 2026-10-11 01:26 UTC on `a70996b`, clean tree,
`lp_run_1.log`): AS and LC reused (LC `resumed=924`, dedup `resumed=195`,
AS+LC bundle reused on matching fingerprints), then LP request building raised
`ValueError: LP SFI cdb52a2a-49ba-50ff-8857-d12b94b8f0b3 audit context exceeds
max_source_evidence_characters_per_sfi; increase the evidence bound.`
(`kgs/lp_requests.py:483-487`). No `lp_*` file, `as_lc_lp_*` file or LP lock
was written, so no LP material exists to move aside.

Defect D-LP-1 (design, found in LP run 1): `_context_sfi` requires each LP
endpoint's and ancestor's complete audit context (metadata and provenance
`audit*`/`merge*` fields, `normalized_statement_code`,
`statement_value_canonicalization`) to fit in
`max_source_evidence_characters_per_sfi` (2000, unchanged by the design). An
offline `build_lp_selection` measurement (scratchpad `lp_audit_sizes.py`)
finds 81 Skills (max 2395) and 6 Sub-strands (max 2155) over 2000; Grades
1515-1627, Terms 1361-1837, Skill Areas 741-783. The text is mostly the dedup
`merge_reason`, stored in both metadata and provenance; no item carries audit
flags or notes. The failing `cdb52a2a` is the Grade R Sub-strand 'Uses visual
and pictorial cues to make meaning' (2127), an ancestor of Skill endpoints.
The original 12 / 5000 budgets would have failed too: every Skill is an
endpoint at 12 per Skill. Offline `build_lp_generation_requests` (scratchpad
`lp_requests_try.py`, no LLM, no writes): bound 2000 reproduces the error;
2400 and 3000 build all 12892 requests with no other bound failure (payload
median 43144 / 43475 characters, max 58735 / 61183, total 563.9M / 568.5M).
The design keeps `evidence_limits` fixed, so this routes to Architect as an
`ARCHITECTURE` failure.

D-LP-1 fix (design `ec6dfe6`): `kgs.lp.evidence_limits.max_source_evidence_characters_per_sfi`
set to 3000 in the CAPS config (JSON integer; the only change versus
`ec6dfe6`; the other `evidence_limits` fields unchanged; the config loads).
LP preflight `logs/kg_construction/dev005/lp_preflight.py` (committed config
with the designed LP values; S_AS identical and no LP artifacts; offline
`build_lp_selection`, `build_lp_candidates` and `build_lp_generation_requests`
with no bound error and one request per retained pair; writes only into
`--out-dir`). Developer trial in the scratchpad before the commit: all passes
except the expected `config_not_committed`; 1144 selection SFIs, audit
context max 2395 (0 over 3000), candidate bound 13860, 12892 candidates and
12892 requests (median payload 43475, max 61183 characters), request build
74 s. Handed over 2026-10-11: commit, run the preflight, and only when it
passes start LP run 2 (`lp_run_2.log`) as described for LP run 1.

LP run 2 (user-run 2026-10-11 02:40 UTC on `14ad0e0`, clean tree, LP
preflight `passed: True`, `lp_run_2.log`): AS and LC reused (LC
`resumed=924`, dedup `resumed=195`, AS+LC bundle reused on matching
fingerprints); LP wrote its candidate and request artifacts (12892 requests)
and began dispatching at 02:45 UTC. Interim review at 12:14 UTC (user
question; read-only scratchpad `lp_review.py`, nothing written into `kgs/`):
168 requests through both stages, no failures, no retries, no warning or
error lines. Of 164 reconciled responses, 127 are relatesTo, 37 buildsTowards
and 0 no_relation; the checker corrected 14 drafts, all buildsTowards to
relatesTo on reprinted CAPS bullets; no buildsTowards edge goes to an earlier
(Grade, Term), so `AC-018` holds so far. Sampled buildsTowards edges extend an
earlier-Term Skill in a later Term (for example Grade 1 'Draws pictures to
convey a message', Term 1, to '... adding a label or caption', Term 2). The
first 168 requests are near-duplicate cross-Term pairs (mean SFI text Jaccard
0.83, against 0.07-0.21 over later request ranges); the population is 7546
same-Grade same-Term pairs, 4558 same-Grade cross-Term and 788 cross-Grade,
so the interim decision mix is not representative. Usage per request is about
58.3k input and 3.8k output tokens (`anthropic:claude-opus-5`, effort high,
adaptive thinking; no cache reads).

Defect D-LP-2 (design, found in LP run 2): throughput is about 17.6 requests
an hour, steady across the run, so 12892 requests take about 30 days; the
design expects a day or more at concurrency 4. Log timing per batch of 4
requests: draft dispatch checkpoints 4 x about 25 s in series
(`checkpoint_seconds` median 24.85, n 336), model calls in parallel (stage
median 31.5 s), draft retention 4 x about 53 s, verdict dispatch 4 x about
25 s, verdict retention 4 x about 77 s; about 13.7 minutes per 4 requests,
under 10% of it model time. Cause (code reading, consistent with those
timings): `LPGenerationCheckpoints._save` (`kgs/lp_checkpoints.py:660-692`)
calls `verify_execution_authority` twice and `_commit` (`:192-208`) calls it
once per written file and once more, so each save runs the execution check
about 10 times. Each check is `LPRequestPopulationVerifier.verify`
(`kgs/lp_requests.py:309-337`): `_population_material_hash` (`:669`)
re-serializes all 12892 candidates and requests, and the 569 MB request file
and the other inputs are stream-hashed; about 2.5 s per check. A request takes
7 saves (two dispatches, two attempt completions with promotion, one
reconciled append) plus about 7 further `_verify_execution_material` calls,
about 77 checks. The single coordinator holds the dispatch gate while it
saves, so `max_concurrent_requests` cannot raise throughput. Each save also
rewrites the whole checkpoint state twice (transaction journal and every
artifact) and re-encodes every record, so saves slow as rows accumulate
(state about 4.5 MB now, about 150 MB projected at completion; about 15 TB of
writes projected over a full run). A fix changes when and how the store
re-verifies inputs and checkpoint bytes (resume and fail-closed semantics),
outside the code-fix boundary, and the design's runtime premise needs
correcting, so this routes to Architect as an `ARCHITECTURE` failure (user
decision, 2026-10-11, after the interim review; not chosen: lowering the
`AC-033` budgets, still about 12 days at 12 / 5000 with LP restarted, or
letting run 2 finish unchanged). Run 2 keeps running meanwhile (user
decision). Its completed requests stay reusable after a code-only fix that
leaves the LP execution material (`lp_execution_material`: `kgs` config hash,
`prompts.py` hash, schemas, model, request manifest) and the checkpoint
format unchanged.

Run 2 restart (user, 2026-10-11, reported at the next Developer session): the
user stopped run 2's first invocation after 12:14 UTC and restarted it unchanged
on the pre-fix code at 12:40 UTC, teed to the same `lp_run_2.log`, which
replaced that invocation's log. Developer's ANSI-stripped capture of the first
invocation up to 12:14 UTC is kept as
`logs/kg_construction/dev005/lp_run_2_invocation1_until_0814.log` (local
times); about 26 minutes of log lines before the stop are lost. The checkpoint
store resumed as designed: receipt `run_number` 2, 176 responses by 13:01 UTC,
and the two run-1 draft attempts in flight at the stop recorded as `unknown`
and dispatched again in run 2. No acceptance evidence depends on the lost
lines: D-LP-2 evidence is recorded above, and the `AC-017` real-run criteria
apply to the post-fix invocation's own log.

---

### DEV-006 — Close-out checks and results review

`Status`: `PENDING` `Depends On`: `DEV-005`
`Acceptance`: `AC-001, AC-002, AC-012, AC-013, AC-019, AC-020, AC-021, AC-023, AC-033`

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
  `start_page` 35 / `end_page` 135, `output_dir` `results/kg_for_ed` and the
  committed LP budgets 30 / 14000.
- `make test` and `make lint` pass from `backend/`.
- Results review (`AC-019`) recorded here: counts by statement type and
  relationship type, LP dropped-pair counts by budget, the defect log with
  resolutions, and sampled Skills, LCs and LP relationships checked against
  the source.

**Self-Check**

S0/S_AS comparison scripts; config load script; `git diff` against `19f44f5`
for paths and curriculum terms; `make test`, `make lint`.

---

### DEV-007 — LP full-verification points and cheap guards

`Status`: `DONE` `Depends On`: `NONE`
`Acceptance`: `AC-017, AC-020`

**Goal**

Run the full LP input and checkpoint proofs only at the design's
full-verification points, with guards between them that fail closed in the same
cases at a cost that does not depend on the population or store size (LP
checkpoint contract items 1, 2 and 5).

**Affected Area**

`backend/src/kgfeg/kgs/lp_requests.py` (`LPRequestPopulationVerifier`);
`backend/src/kgfeg/kgs/lp_generation.py` (`_verify_execution_material`,
`_verify_lp_inputs`, the dispatch-gate material check and their call sites);
`backend/src/kgfeg/kgs/lp_checkpoints.py` (`verify_execution_authority`,
`verify_bytes`, `_save`, `_commit`, `_previous_hashes`).

**Expected Outcome**

- Full verification (stream every population byte, hash the whole in-memory
  population, execution-material equality, complete `_validate`, read back
  every checkpoint file against the receipt) runs at store construction in
  every invocation, before the save whose receipt status first becomes
  `completed`, in every read-only load, and when a guard trips.
- Before each dispatch, dispatch-gate transport check and publication, the
  guards compare population-artifact stat identity (`st_dev`, `st_ino`,
  `st_size`, `st_mtime_ns`, `st_ctime_ns`) with the last full verification (a
  difference runs a full population verification: identical bytes refresh and
  log the identity, different bytes fail closed); re-hash only the requests
  being dispatched or published against per-request digests from the last full
  verification; recompute execution-material equality; and compare checkpoint
  files' stat identity and length with the store's last write (a difference
  runs a full readback; a mismatch fails closed).
- `_commit` runs one authority check (guards plus ownership) before writing the
  journal and one before retiring it, not one per file; `_previous_hashes`
  uses recorded digests and stat identities instead of re-reading files.
- Between full points, no dispatch, transport check or publication reads
  `lp_generation_requests.jsonl` or re-serializes the whole population.

**Self-Check**

User-run from `backend/` with the test environment in Plan Notes: ruff, mypy
and pylint on the changed modules; the LP test files (`tests/kgfeg/kgs/test_lp_*.py`
and `test_create_kgs_lp.py`), every failure attributed to a test that pins the
old every-check cadence (listed here for Tester) and none to other behavior; a
scratch script counting population-artifact reads per save on a small synthetic
store.

**Implementation Notes**

Implemented 2026-10-11 (uncommitted, on `cb7d9dc`):

- `lp_requests.py`: `LPRequestPopulationVerifier` keeps per-request digests
  and each artifact's stat identity (`file_stat_identity`: `st_dev`, `st_ino`,
  `st_size`, `st_mtime_ns`, `st_ctime_ns`, captured unchanged across the
  verified read). `verify` stays the full proof and refreshes the identities;
  the new `guard` runs a full `verify` on any stat difference (identical bytes
  refresh and log, different bytes fail closed) and re-hashes only the named
  requests. `_population_material_digests` computes the whole hash and the
  request digests in one pass; `_population_material_hash` keeps its result.
- `lp_checkpoints.py`: `execution_check` is called with `full` and
  `request_indices`; `verify_execution_authority(full=True)` by default.
  `_check_checkpoint_files` reads a checkpoint file only when its stat identity
  differs from the store's last write or full readback (always on a full check)
  and requires the validated digest. `_save` takes the changed request indices
  (plus pending rows), runs one guard before writing the journal and `_commit`
  one before retiring it (no per-file checks), and runs the full input and
  checkpoint proofs before the save whose receipt status first becomes
  `completed`. `_previous_hashes` uses the validated digests after a stat
  guard. `_recover` runs one guard before applying a journal. `_load` reads
  every file back (`verify_bytes(full=True)`). `verify_bytes` keeps the
  in-memory payload recheck (removed by DEV-008) with a stat guard on files.
- `lp_generation.py`: admission and retention guard the dispatched or
  published request; the dispatch-gate transport check
  (`_verify_active_material`) guards every request in the bounded window,
  which the coordinator changes only under the same gate lock; the end of an
  invocation runs the full proofs.

Developer trial (Developer-run, not the self-check of record; from `backend/`
with the test environment): black and isort clean after `black` on the two
files; ruff, mypy and pylint (10.00/10) clean on the three modules; LP tests
(`tests/kgfeg/kgs/test_lp_*.py`, `test_create_kgs_lp.py`): 1847 passed, 1
failed: `test_lp_population_verifier.py::test_transaction_publication_rechecks_changed_population`,
which pins the per-file authority check inside `_commit` (a population edit
after the journal is written must stop the remaining file replacements); under
contract item 5 the replacements complete and the check before retiring the
journal fails closed, leaving the journal for recovery. Listed for Tester.
`tests/kgfeg/evals`: 816 passed. `logs/kg_construction/dev007/lp_guard_reads.py`
(real orchestration, stub model call that passes the real transport check):
6 standards, 15 requests, 107 saves, 351 guards; 12 standards, 66 requests,
464 saves, 1525 guards; full population verifications 3 in both (store
construction, completion boundary, end of invocation) and population bytes
read about 7.7 requests-file equivalents in both, so population reads no
longer grow with saves. Handed over for the user-run self-check.

Self-check of record (user-run 2026-10-11 13:25-13:29 UTC from the repo root,
uncommitted tree on `cb7d9dc`; git blob ids `lp_checkpoints.py` `0437a8a`,
`lp_generation.py` `34f4424`, `lp_requests.py` `0695aed`; outputs in
`logs/kg_construction/dev007/`):

- `lint.log`: black and isort report the three modules unchanged; ruff on
  `src/kgfeg/kgs/` passes; mypy finds no issues in the three modules; pylint
  10.00/10.
- `pytest_lp.log` (`tests/kgfeg/kgs/test_lp_*.py`, `test_create_kgs_lp.py`,
  `tests/kgfeg/evals`, `-n auto`): 2663 passed, 1 failed. The failure is the
  expected
  `test_lp_population_verifier.py::test_transaction_publication_rechecks_changed_population`
  (asserts the checkpoint files stay unreplaced after a post-journal population
  edit; the run did fail closed with the expected `artifact` error and made no
  model call). It pins the removed per-file check and is listed for Tester.
- `lp_guard_reads.json`: `passed: true`, counts as in the Developer trial (3
  full population verifications at 107 and at 464 saves; population bytes read
  7.75 and 7.65 requests-file equivalents); no guard-trip refresh was logged.

---

### DEV-008 — LP checkpoint record authority

`Status`: `DONE` `Depends On`: `DEV-007`
`Acceptance`: `AC-017, AC-020`

**Goal**

Make each checkpoint record's validated canonical bytes its authority, so a
save re-proves only the records it adds or changes (LP checkpoint contract item
3).

**Affected Area**

`backend/src/kgfeg/kgs/lp_checkpoints.py` (record creation and change in
`begin_run`, `record_dispatch`, `complete_attempt`, `append` and `promote`;
`_validate`, `_validate_cached_row`, `_payloads`, `_record_bytes`; the
`rows`, `pending`, `attempts` and `failures` accessors and `get_row`); their
read-only users in `kgs/lp_generation.py`, `kgs/lp_finalization.py` and
`evals/lp_eval/sampling.py`.

**Expected Outcome**

- A stage row, pending row, attempt or failure is validated when the store
  creates or changes it, and publication uses only its canonical bytes.
- Accessors return copies or immutable views: mutating a returned object
  changes neither persisted bytes nor store decisions.
- A save re-proves only the added or changed records plus the rows they depend
  on for the same request index, and rechecks the cross-record invariants the
  change can affect (stage length order, prefix contiguity, pending capacity,
  failure and attempt dispositions for that request).
- The complete `_validate` still runs at every full-verification point.
- Per-save record work does not grow with the completed count; callers behave
  as before.

**Self-Check**

As DEV-007, plus a scratch script that mutates returned records and confirms
persisted bytes and store decisions are unchanged, and that counts records
re-proved per save on a small synthetic store.

**Implementation Notes**

Implemented 2026-10-11 (uncommitted, on `61f6c81`):

- `lp_checkpoints.py`: stage rows, pending rows, attempts, failures and the
  execution material are private (`_rows`, `_pending`, `_attempts`,
  `_failures`, `_material`, a deep copy of the constructor's material). The
  public `rows`, `pending`, `attempts` and `failures` are read-only live views
  (`_RecordSequence`, `_PendingRecords`, `MappingProxyType`) whose items are
  deep copies; `get_row` returns a copy; `material` returns a deep copy; the new
  `retry_limits` returns a copy of the captured limits. Records are never
  mutated in place: `_store_result`, `_put_attempt` and `_set_failures`
  validate a new record and store its canonical bytes in `_record_encodings`,
  which `_payloads` joins without re-proving anything. Failures are rare, so a
  changed failure history is re-encoded whole.
- `_validate(indices=None)`: without indices it validates every record (load,
  read-only load, completion boundary); with indices it validates those
  requests' rows (`_validate_index_rows`, proofs keyed as before) and attempts
  (`_validate_index_attempts`), plus stage length order, pending capacity and
  boundaries, the whole (short) failure history and the dispatched count.
  `_save` validates its changed requests plus pending rows, or runs the
  complete validation at the completion boundary. The proof material digest is
  computed once per store.
- Usage `request_states` are cached per request (`_request_state_encodings`)
  and re-encoded only for the requests a save changes; the joined bytes equal
  the previous whole-list encoding.
- `verify_bytes` no longer re-serializes the in-memory store (records cannot
  change outside validated updates); it checks the files only. An update that
  raises after changing any record, encoding or file marks the store failed
  (`_updating`), and every later update or `verify_bytes` is refused; a rejected
  precondition changes nothing and does not.
- `lp_generation.py`: `_observe_lp_failures` reads `store.retry_limits`
  instead of indexing `store.material` (which is now a deep copy).

Developer trial (Developer-run; from `backend/` with the test environment):
black, isort, ruff, mypy (with `lp_finalization.py` and
`evals/lp_eval/sampling.py`) clean, pylint 10.00/10. LP tests: 1828 passed, 20
failed. All 20 pin replaced behavior and are listed for Tester: the DEV-007
`test_transaction_publication_rechecks_changed_population`, and 19 in
`test_lp_checkpoint_proofs.py` that mutate store records or material through
the public attributes and expect the store to notice, assign into the views,
or call `_validate_cached_row` with the removed `material` argument:
`test_cache_history_is_bounded_by_current_positions`,
`test_changed_nested_record_revalidates[draft|verdict|response]`,
`test_changed_request_and_execution_material_revalidate[producer|checker|model|retry|manifest]`
(the `request` case, a population edit, still passes),
`test_failed_validation_cannot_install_partial_proofs`,
`test_order_and_prerequisite_edits_fail_closed[missing_draft|missing_verdict|gap|duplicate|order|pending_overlap]`,
`test_resealed_disk_cannot_replace_last_validated_snapshot[True]` (the disk-only
`[False]` case passes) and
`test_unchanged_response_revalidates_changed_prerequisite[draft|verdict]`.
`tests/kgfeg/evals`: 816 passed. `logs/kg_construction/dev008/lp_record_authority.py`:
passed; incremental saves (106 at 15 requests, 463 at 66) average about 1
record encoding, 1.4 row proofs and 0.85 semantic row validations per save in
both halves of each run and at both sizes, with at most 3 row proofs in one
save (bound 3 × (capacity + 1) = 15); one complete validation per run (the
completion boundary); every caller-reachable record mutated with payload
bytes, files (full readback), retry limits and the dispatch decision
unchanged, and writes into the views rejected. `lp_guard_reads.py` still
passes (3 full population verifications at 107 and 464 saves). Handed over
for the user-run self-check.

Self-check of record (user-run 2026-10-11 from the repo root, uncommitted tree
on `61f6c81`; git blob ids `lp_checkpoints.py` `d76a824`, `lp_generation.py`
`194c220`, `lp_requests.py` unchanged since DEV-007; outputs in
`logs/kg_construction/dev008/`):

- `lint.log`: black and isort report the three modules unchanged; ruff on
  `src/kgfeg/kgs/` passes; mypy finds no issues in the five modules
  (including `lp_finalization.py` and `evals/lp_eval/sampling.py`); pylint
  10.00/10.
- `pytest_lp.log`: 2644 passed, 20 failed; the 20 are exactly the tests listed
  above (19 in `test_lp_checkpoint_proofs.py` plus the DEV-007
  `test_transaction_publication_rechecks_changed_population`), all pinning
  replaced behavior, for Tester.
- `lp_record_authority.json`: `passed: true`; per incremental save about 1
  encoding, 1.3-1.5 row proofs and 0.8-0.9 semantic row validations in both
  halves at 15 and 66 requests (at most 6 row proofs in one save, bound 15);
  one complete validation per run; caller mutation changed nothing.
- `lp_guard_reads.json`: `passed: true` (3 full population verifications at
  107 and 464 saves).

Commit-hook fix (2026-10-11): the pre-commit `interrogate` hook (100% docstring
coverage) rejected the commit because the two `_RecordSequence.__getitem__`
`@overload` stubs had no docstrings. Added one-line docstrings to both stubs,
no code change (`lp_checkpoints.py` blob `639486e`); Developer reran
interrogate on `src/kgfeg/kgs/` (100%), ruff, mypy and pylint (10.00/10) clean.

---

### DEV-009 — Incremental journaled LP checkpoint commits

`Status`: `DONE` `Depends On`: `DEV-008`
`Acceptance`: `AC-017, AC-020`

**Goal**

Write only what changed on each save while every at-rest checkpoint file and
the receipt stay byte-identical to the current full-rewrite writer, so run 2's
checkpoints resume as they are (LP checkpoint contract items 4 and 6).

**Affected Area**

`backend/src/kgfeg/kgs/lp_checkpoints.py` (`_save`, `_commit`, `_recover`,
`_read_transaction`, `_LPCheckpointTransaction`, `_payloads`, running
digests, `validate_lp_checkpoint_format`).

**Expected Outcome**

- Stage JSONL saves append exactly the new rows' bytes; `pending`, `failures`,
  `usage` and the receipt are rewritten whole; receipt `artifact_byte_hashes`
  stay full-file SHA-256, kept as running digests seeded by the full load.
- After every save, each checkpoint file and the receipt equal the pre-change
  writer's bytes for the same state.
- The journal holds, per stage file, its previous length, previous digest and
  appended bytes, and the full next bytes of each small file and the receipt.
  Commit order is unchanged (write and fsync the journal, apply, check against
  the journal's next state, retire it, sync the directory). Recovery rolls
  forward only when every file is exactly at its previous or next state
  (length and digest); anything else fails closed.
- A pre-change full-payload journal is still recovered, and
  `validate_lp_checkpoint_format` accepts both journal forms.
- Unchanged: `lp_execution_material`, the at-rest file set, formats and receipt
  fields, retry/attempt/usage/failure accounting, lock ownership, `overwrite`
  archiving, finalization, artifacts and export; no config field.

**Self-Check**

As DEV-007, plus scratch scripts in `logs/kg_construction/dev009/` (no model
calls, nothing written under `results/`):

- synthetic scale benchmark: 12,892 requests with about 11 kB of stage rows
  each, every save driven with stub results; the median per-save time over the
  last 500 requests is within 2× of the first 500, and no save, dispatch or
  transport check between full-verification points reads
  `lp_generation_requests.jsonl` or a whole checkpoint file;
- byte equivalence: dispatch, completion, promotion, retry, failure and
  recovery sequences through the new store and through the pre-change writer
  (the `HEAD` modules loaded from `git show`) give identical at-rest files and
  receipts;
- crash injection after each commit step of the new journal, and on a
  pre-change full-payload journal: reload recovers or fails closed as the
  design requires.

**Implementation Notes**

Implemented 2026-10-11 (uncommitted, on `00c466e`):

- `lp_checkpoints.py`: a save journals a `_LPCheckpointDeltaTransaction`:
  per stage file its previous length and digest, appended bytes, next length
  and digest; per small file (pending, failures, usage) and the receipt its
  previous digest and complete next bytes. `_commit_delta` extends stage files
  in place (`_append_bytes`, which refuses a file whose size is not the
  journaled previous length), replaces the small files and receipt atomically,
  checks stage files by length plus a readback of only the appended tail and
  small files by length, records each file's identity, runs the guard and
  retires the journal. Running SHA-256 digests (`_stage_hashers`) and persisted
  row counts are seeded by every full load and advanced only after a
  successful commit; `_previous_state` takes predecessor lengths and digests
  from the store's recorded identities after a stat guard. `_receipt_bytes`
  encodes the receipt from the material bytes encoded once per store; the
  bytes equal encoding the whole receipt object. `_recover` dispatches on the
  journal form: the full-payload journal path is unchanged, and
  `_recover_delta` rolls forward only when every stage file is exactly at its
  previous or next length and digest (absent counts as previous only for a
  fresh store) and every small file at its previous or next digest, after
  validating the complete next snapshot; anything else fails closed.
  `_read_transaction` reads and validates both forms, so
  `validate_lp_checkpoint_format` accepts both. Small files are rewritten
  whole on every save, as before.
- `lp_generation.py`: `_retain_lp_outcome` now guards the published request
  together with every request still in the bounded window (`in_flight`). The
  DEV-008 store guard re-hashed only the published request and pending rows,
  so a worker-side mutation of another admitted request could precede a
  publication (`test_changed_worker_evidence_closes_dispatch_and_publication[returning_call-4]`
  failed intermittently under DEV-009's faster saves); the transport check
  already covered the whole window.

Developer trial (Developer-run): black, isort, ruff, mypy, pylint (10.00/10)
and interrogate (100%) clean. LP tests plus `tests/kgfeg/evals`: 2636 passed,
28 failed: the 20 listed under DEV-008 and 8 that pin the full-payload journal
or the atomic replacement of stage files, for Tester:
`test_lp_checkpoint_formats.py::test_current_transactions_recover_every_authenticated_old_new_file_mixture[initialize|update]`
(asserts the journal carries every checkpoint file's full bytes) and
`test_lp_orchestration.py::test_pending_transaction_never_authorizes_arbitrary_corruption_repair[file_corruption|journal_invalid|journal_material|journal_partial|journal_payload|missing_file]`
(injects the crash through `_atomic_write` of the draft stage file, which is
now appended, and edits `next_payloads` of a full-payload journal). The
interruption tests in `test_lp_concurrency_recovery.py` that interrupt every
journal and small-file write in every phase pass against the delta journal.
Scripts in `logs/kg_construction/dev009/` (shared driver `lp_drive.py`, which
loads the pre-change writer from `cb7d9dc`):

- `lp_byte_equivalence.py`: passed; 93 operations (dispatch, success, failure
  and retry, exhausted failure resolved in a later run, checker correction,
  out-of-order promotion, cancelled and unknown outcomes, new runs, reload
  after an interrupted dispatch, through the completion boundary) leave every
  checkpoint file and the receipt byte-identical to the pre-change writer's,
  and each writer's class loads the other's directory.
- `lp_crash_recovery.py`: passed; 360 interruptions (before and after every
  journal write, stage append or creation, small-file write and final
  directory sync of 18 representative operations, including the completion
  boundary): 273 left a delta journal and all recovered to the uninterrupted
  run's exact files; 32 pre-change full-payload journals (interrupted after the
  journal and after each file replacement) recovered to the same files; a torn
  stage-file append and a small file matching neither state both fail closed on
  load with no file changed.
- `lp_scale_benchmark.py --limit 300` (real CAPS population of 12,892 requests,
  stub calls, rows about 11 kB per request): 7 saves per request, median save
  0.021 s, no population or whole-checkpoint-file read between
  full-verification points. A cProfile trial of 200 requests puts setup (two
  request-population builds and verifications) at about 4-5 minutes and
  checkpoint work at about 0.15-0.2 s per request against a model-bound
  15.75 s per request at concurrency 4. The full run is part of the user-run
  self-check.
- `lp_record_authority.py` and `lp_guard_reads.py` still pass.

Full scale benchmark dropped from the Developer self-check (user decision,
2026-10-11, after Developer's estimate below; non-material: no build step,
behavior or approach changes). The other self-checks stand. Developer
micro-measurement of the save work that grows with progress (usage file
rewritten whole and journaled with the 1 MB receipt, three 3.5 kB stage
appends, `_canonical_bytes` and `_atomic_write` as used by the store): 19.5 ms
per save with usage at 2.3 MB (about 1,000 attempts) and 54.9 ms at 12.1 MB
(about 25,800 attempts, the end of a 12,892-request run). With the observed
early median save of 0.021 s, late saves are expected near 0.055-0.06 s, about
2.5-3x early ones, so the `AC-017` checkpoint-scale criterion (last-500 median
within 2x of first-500) may not be met: the growth comes from the design's
whole-file usage rewrite, not from re-proving records or re-reading inputs.
Expected real-run effect: about 0.4 s of checkpoint work per request at the
end of the run against about 15.75 s of model time per request at concurrency
4 (run 2 before the fix: about 200 s per request), so throughput stays
model-bound. The `AC-017` checkpoint-scale criterion remains Architect's
technical criterion for Tester to verify in Increment 1;
`logs/kg_construction/dev009/lp_scale_benchmark.py` (about 1 hour, no model
calls) is available for that.

Self-check of record (user-run 2026-10-11 from the repo root, uncommitted tree
on `00c466e`; git blob ids `lp_checkpoints.py` `65c44a6`, `lp_generation.py`
`d0ed431`, `lp_requests.py` unchanged since DEV-007 (`0695aed`); outputs in
`logs/kg_construction/dev009/`):

- `lint.log`: black and isort report the three modules unchanged; ruff on
  `src/kgfeg/kgs/` passes; mypy finds no issues in the five modules; pylint
  10.00/10; interrogate 100% on `src/kgfeg/kgs/`.
- `pytest_lp.log`: 2636 passed, 28 failed; the 28 are exactly those listed
  above (19 in `test_lp_checkpoint_proofs.py`, 1 in
  `test_lp_population_verifier.py`, 2 in `test_lp_checkpoint_formats.py`, 6 in
  `test_lp_orchestration.py`), all pinning replaced behavior, for Tester.
- `lp_byte_equivalence.json`: `passed: true`, 93 operations.
- `lp_crash_recovery.json`: `passed: true`; 360 interruptions, 273 delta
  journals recovered, 32 pre-change full-payload journals recovered, torn
  append and unexplained small file fail closed unchanged.

---

## Verification Increments

### Increment 1

`Development Steps`: `DEV-007, DEV-008, DEV-009` `Acceptance`: `AC-017, AC-020, AC-022, AC-023`

**Ready Outcome**

The LP checkpoint store and verifiers implement the design's LP checkpoint
integrity and throughput contract (full-verification points with cheap guards
between them, record authority, incremental journaled commits with at-rest
files and receipt byte-identical to the pre-change writer, recovery of both
journal forms), with DEV-007 to DEV-009 self-checks passed. Ready for Tester's
`AC-022` tests (including crash injection at each commit step and the update of
existing tests that pin the old every-check cadence) and the suite and lint
run (`AC-023`) before DEV-005 stops run 2 and resumes LP on the fixed code.
The real-run part of `AC-017` stays with DEV-005.

**Readiness** (2026-10-11): DEV-007 to DEV-009 are `DONE` with their
self-checks of record (see each step). Assessed implementation: the commits
that land `lp_checkpoints.py` blob `65c44a6`, `lp_generation.py` blob
`d0ed431` and `lp_requests.py` blob `0695aed` (changed only by `8040c56`,
DEV-007). Claims for Tester to assess:

- Contract items 1, 2 and 5: full proofs only at store construction, the
  completion boundary, read-only loads and guard trips; stat-identity and
  per-request guards between them, now covering the published request and
  every in-flight request at publication; one authority check per side of the
  journal (DEV-007, and the DEV-009 `in_flight` note).
- Contract item 3: private records, read-only views of copies, encoding at
  creation or change, per-request incremental validation, fail-closed after a
  failed update (DEV-008).
- Contract items 4 and 6: delta journal, in-place appends, running digests,
  at-rest files and receipt byte-identical to the pre-change writer, recovery
  of both journal forms, unchanged execution material and file set (DEV-009).

Existing tests that pin replaced behavior (28, listed under DEV-007, DEV-008
and DEV-009) need updating to the new cadence and journal, not deleting, per
the design's `AC-022` list, which also asks for the crash-injection, byte
equivalence, guard and caller-mutation tests that the Developer scripts in
`logs/kg_construction/dev007/` to `dev009/` exercise informally. Known risk:
the `AC-017` checkpoint-scale ratio (last-500 median save within 2x of
first-500) is estimated at about 2.5-3x because of the design's whole-file
usage rewrite; the full benchmark was dropped from the Developer self-check by
user decision (see DEV-009). Not part of this increment: DEV-005 (stop run 2,
read-only load of its checkpoints with the new code, resume) and DEV-006;
`AC-024` documentation is Documenter's.

---

## Plan Notes

- The user runs every command (user decision, 2026-10-09): `create_kgs`
  invocations (from the repo root,
  `backend/.venv/bin/python backend/src/kgfeg/entries/create_kgs.py examples/funda_wande/config_english_curriculum.json`),
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
- The one planned KG code change is the LP checkpoint throughput fix
  (DEV-007 to DEV-009, design `cb7d9dc`, LP checkpoint contract). Developer
  does not edit tests: its formal tests (`AC-022`), including the
  crash-injection tests and the update of existing tests that pin the old
  every-check cadence, are Tester-owned, and its documentation (`AC-024`) is
  Documenter-owned. Any other generic pipeline defect needing a code fix is a
  material plan revision (new `DEV-NNN`, reapproval) within the code-fix
  boundary; anything beyond it routes to Architect.
- Code edits do not affect a running `create_kgs` process (its modules are
  already imported), but `lp_execution_material` re-reads `prompts.py` on every
  check, so `prompts.py` and the CAPS config stay untouched while run 2 is
  running.
- LP candidate budget analysis (user question, 2026-10-11; offline
  `build_lp_candidates` on the LC run 2 AS+LC bundle, no LLM calls, scratchpad
  scripts `lp_pool.py`, `lp_pool_dump.py`): uncapped (923 per Skill, 426426
  total) keeps 385629 of the 426426 Skill pairs (every pair with any evidence;
  about 771k LLM calls at 2 per pair). Recall proxy: 890 likely progression
  pairs (same sub-strand, 1-4 terms apart, at least 50% word overlap; or
  sharing an LC). Per-Skill budget k with total at least 924 x k / 2: k 12
  (total 5000, current) 5000-5058 pairs, 60% of the proxy, 79% of the 533
  shared-LC pairs; k 20 (9300) 8559, 73%, 89%; k 30 (14000) 12892, 82%, 95%;
  k 50 (23100) 21523, 87%, 100%. The structurally plausible share of
  candidates falls from 58% (k 12) to 38% (k 30). Raising the budgets changes
  the scope assumption and design choice of 12 per Skill / 5000 total, so it
  routes to Architect as user rework; config-only (`kgs.lp.candidate_policy.budgets`).
- Test commands run from `backend/` with
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo root>`.

### Suspended Assignment 1

`Recovery Frame`: `1` `Recovery Reason`: `User rework: raise the LP candidate budgets to 30 per Skill and 14,000 total (kgs.lp.candidate_policy.budgets).` `Purpose`: `DEVELOPMENT`
`Target`: `NONE` `Assessed Inputs`: `DEV-001 to DEV-004 DONE; CAPS config at d249c7e (LP budgets 12 per Skill, 5000 total); AS equal to S_AS; LC run 2 outputs checked 2026-10-11 00:16 UTC; local uncommitted Stop B in create_kgs.py`
`Next Action`: `Reconcile DEV-005 with the corrected scope and design (set kgs.lp.candidate_policy.budgets as decided there), then hand over the no-stop LP run with Stop B removed.`

### Recovery Reconciliation 1

Frame 1 (user rework: LP candidate budgets) closed 2026-10-11 (scope
`31c9f8f` adds `AC-033`; design `9554a7d`). Restored Suspended Assignment 1.
DEV-001 to DEV-004 unaffected: AS stays equal to S_AS and LC run 2 stands
(design: no LP artifacts exist yet, so no move-aside). DEV-005 gains `AC-033`
and an in-step config change the user requested (budgets 30 / 14000 before
the first LP run, recorded in `lp_candidate_summary.json`); DEV-006 checks
the committed budgets. Traceability and an in-step config setting under
unchanged approved intent; no reapproval required. Next: DEV-005 (user
decides `max_concurrent_requests` before LP starts, per the design).

### Suspended Assignment 2

`Recovery Frame`: `1` `Recovery Reason`: `LP request build fails: the 2000-character LP evidence bound (kgs.lp.evidence_limits.max_source_evidence_characters_per_sfi) is below the CAPS SFI audit context of 81 Skills and 6 Sub-strands (max 2395 characters).` `Purpose`: `DEVELOPMENT`
`Target`: `NONE` `Assessed Inputs`: `DEV-001 to DEV-004 DONE; CAPS config at a70996b (LP budgets 30 / 14000, concurrency 4, evidence bound 2000); AS equal to S_AS; LC run 2; LP run 1 failed before writing LP artifacts`
`Next Action`: `Reconcile DEV-005 with the corrected design (evidence bound and any related config), confirm the full LP request population builds offline, then hand over LP run 2 with no stops.`

### Recovery Reconciliation 2

Frame 1 (D-LP-1, LP evidence bound) closed 2026-10-11 (design `ec6dfe6`:
`kgs.lp.evidence_limits.max_source_evidence_characters_per_sfi` 3000 and an
offline LP request-build preflight before any paid LP call). Restored
Suspended Assignment 2. DEV-001 to DEV-004 unaffected (no LP artifacts exist;
AS equal to S_AS; LC run 2 stands). DEV-005 gains the 3000 bound and the
preflight as in-step config and self-check work under unchanged approved
intent; no reapproval required. Next: set the bound, write and hand over the
preflight, then LP run 2.

### Suspended Assignment 3

`Recovery Frame`: `1` `Recovery Reason`: `LP run 2 completes about 17.6 requests an hour (168 of 12,892 after 9.5 h; about 30 days in all) against the design's day or more: every LP checkpoint save re-verifies the full 569 MB request population about 10 times (about 77 times per request, about 2.5 s each) on the single coordinator thread, so concurrency cannot help; a fix changes checkpoint verification semantics, outside the Developer code-fix boundary.` `Purpose`: `DEVELOPMENT`
`Target`: `NONE` `Assessed Inputs`: `DEV-001 to DEV-004 DONE; CAPS config at 9478365 (LP budgets 30 / 14000, concurrency 4, evidence bound 3000); AS equal to S_AS (LP preflight); LC run 2; LP run 2 still running on 14ad0e0 (168 of 12,892 requests at 2026-10-11 12:14 UTC, no failures)`
`Next Action`: `Reconcile the plan with the corrected design (a new DEV step with reapproval if the LP checkpoint throughput fix is assigned to Developer), then hand over stopping LP run 2 and resuming LP from its checkpoints with no stops.`

### Recovery Reconciliation 3

Frame 1 (D-LP-2, LP checkpoint throughput) closed 2026-10-11 (design
`cb7d9dc`: LP checkpoint integrity and throughput contract, Decision 9).
Restored Suspended Assignment 3. DEV-001 to DEV-004 unaffected (AS equal to
S_AS, LC run 2 stands; the fix changes no AS or LC code). The design assigns a
generic code change to Developer, which no existing step represents, so the
plan adds DEV-007 (full-verification points and guards), DEV-008 (record
authority) and DEV-009 (incremental journaled commits) in the design's order,
and DEV-005 now depends on DEV-009 and adds the stop, read-only load and
resume of run 2 plus the real-run throughput and reuse criteria. This is a
material revision: plan returned to `PROPOSED` for approval. Run 2 keeps
running on the pre-fix code until DEV-005 resumes (user restart at 12:40 UTC
recorded under DEV-005).

Plan approved by the user 2026-10-11 with a switch to `INCREMENTAL`
verification (explicit user choice at approval, applied at this safe
Developer boundary: no recovery active, no Tester assignment open).
Increment 1 is DEV-007 to DEV-009; DEV-005 and DEV-006 follow it, then full
verification.
