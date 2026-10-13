<!-- STANDARDS
Artifact: REVIEW
Cycle: run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8
ReviewKind: IMPLEMENTATION
-->

# Review Report

`Cycle`: `run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8` `ReviewKind`: `IMPLEMENTATION`
`Status`: `COMPLETE` `User Style`: `NONE`

## Assessed Inputs and Scope

Implementation review, 2026-10-13, entered by the `FORWARD` handoff
`TESTING -> REVIEWING_IMPLEMENTATION`. `CycleMode` `STANDARD`,
`CompletionPolicy` `FULL_DELIVERABLE` (no closure assessment applies). Recovery
and outstanding obligations inactive; `BlockedOn`, `BaselineReconciliation` and
`PendingVerificationCadence` `NONE`. No user style selected.

- **Contract.** Scope (SHA-256 `d6cccac4f30dcc49...`, unchanged since `31c9f8f`):
  current acceptance set `AC-001` to `AC-033` except retired `AC-006` and
  `AC-009`, 31 conditions. Design (`abb2e926c037f47c...`, last changed `66a24ab`):
  Acceptance Coverage, LP checkpoint integrity and throughput contract (items
  1-7), Technical Acceptance Criteria. Context `.standards/CONTEXT.md`
  (`202946c349224a96...`).
- **Range.** Baseline `d06ec5f` (previous cycle signed off); assessed HEAD
  `a182f42`; working tree clean apart from this report. Outside `.standards/`
  the range changes 10 files: `backend/src/kgfeg/kgs/lp_checkpoints.py` (blob
  `32710db`), `lp_generation.py` (`d0ed431`), `lp_requests.py` (`0695aed`), six
  LP test files (`test_lp_checkpoint_cadence.py` `ebb5d18` new;
  `test_lp_checkpoint_formats.py` `357eaad`, `test_lp_checkpoint_proofs.py`
  `30cf107`, `test_lp_concurrency_recovery.py` `546425f`,
  `test_lp_orchestration.py` `e6bbb42`, `test_lp_population_verifier.py`
  `d63d2e1`) and `examples/funda_wande/config_english_curriculum.json`
  (`8f43478`). `create_kgs.py` and every other example config are unchanged.
  Affected unchanged boundaries inspected: `lp_dispatch.LPDispatchGate`, the
  read-only store loads in `lp_finalization.py` and
  `evals/lp_eval/sampling.py`, `generate_learning_progressions` wiring and
  `lp_execution_material`.
- **Development and verification.** Plan `COMPLETE`, DEV-001 to DEV-009 `DONE`
  (`cb041daefa0996e5...`); verification report `COMPLETE`, `FULL`
  (`e9c8ec866e1292ff...`), with logs under `logs/kg_construction/tester/` and
  `tester/full/`, and Developer evidence under `logs/kg_construction/dev001/`
  to `dev009/`.
- **Run outputs** (local, git-ignored):
  `results/kg_for_ed/c939a3a9dcce92ed61d970f4599ced6f2ddc425d1220eeeab9828472b878d24d/`.
  Final export `kgs/as_lc_lp_nodes.jsonl` (`f83c4cfe6fb476e2...`),
  `as_lc_lp_relationships.jsonl` (`2a6564d6f6a76e38...`),
  `as_lc_lp_kg_bundle.json` (`5ba389f52b8141b2...`), `lp_validation_report.json`
  (`b2268c3bae9d2eae...`), `lp_candidate_summary.json` (`019cf90a48b592c9...`),
  checkpoint receipt (`0c51a2f7db3c1425...`, the receipt the LP validation
  report names); final run log `logs/kg_construction/dev005/lp_run_3.log`
  (`c25e7009e5028776...`).
- **Excluded.** Unrelated repository areas; rerunning paid LLM stages; a fresh
  full `make test`/`make lint` (reuse justified under Checks and Results).
- **Session.** This conversation began at the Reviewer invocation and holds no
  scoping, design, implementation, testing or documentation authoring. The
  reviewing model is Claude Opus 5.5; the models that produced the work are not
  recorded, so model independence cannot be confirmed.

## Contract and Evidence Assessment

Evidence key: T = Tester formal evidence (verification report, F1-F7 and
Increment 1 executions 1-9); D = Developer self-checks; R = this review's
diagnostics (Checks and Results R1-R13).

| Obligation / criterion | Assessed evidence | Current disposition |
| ---------------------- | ----------------- | ------------------- |
| `AC-001` config output dir and pages 35/135 in both page stages; loads | T F4; R7 (values read; loads as `RunConfig`) | Supported |
| `AC-002` KG runs read `stitching/document_ir.json`; upstream unchanged (S0) | T F4; R5 (705 files rehashed, 0 changed, none added or removed); R6 (`document_ir_fp`) | Supported |
| `AC-003` AS completes; `as_validation_report.json` passes, no errors | T F4/F6; R6 | Supported |
| `AC-004` 4 Grades under root, 4 Terms each, expected 3 Skill Areas per Term | T F4; R4 (tree, levels, counts) | Supported |
| `AC-025` exactly the listed Sub-strands per Skill Area, 152 | T F4/F6 (per-table names, preflight 152/0); R4 (152, every Sub-strand under a Skill Area); R7 (21 canonical values and aliases match the design table) | Supported |
| `AC-005` one Skill per printed bullet, full text | T F4 (932 bullets to 924 Skills + 8 fragments); D DEV-002, DEV-006; R12 (every Skill line in the DocumentIR, no duplicate under one parent) | Supported |
| `AC-026` two lead-ins joined with fragments | T F4; R12 (exactly 2 multi-line Skills) | Supported |
| `AC-027` each Skill under the Sub-strand printed above it, table's Grade/Term/Skill Area | T F4/F6; R4 (every Skill's parent is a Sub-strand, chain intact) | Supported |
| `AC-007` no root fallback, no unresolved hasChild | T F4/F6; R4 (one parent per item, Grades the only root children) | Supported |
| `AC-008`, `AC-028` nothing from ASSESSMENT rows, schedules, labels, notes, resources, headings | T F4 (no Skill outside printed bullets; per-type counts exact), F6 citations | Supported |
| `AC-029` same-worded Skills stay separate | T F4; R12 (no duplicate text under one parent) | Supported |
| `AC-010` Grade R to K, Grades 1-3 to 1-3, every Skill carries its grade | T F4; R4 (0 `gradeLevel` mismatches over 1,144 items) | Supported |
| `AC-030` groupings `Standard Grouping`, Skills `Standard` | T F4; R4 | Supported |
| `AC-011` user AS confirmation recorded with S_AS | Plan DEV-003 (confirmation and per-file hashes); T F4; R5 (13/13 equal) | Supported |
| `AC-012` LC and LP built on confirmed AS | T F4; R5; R9 (AS and LC reused in the final invocation) | Supported |
| `AC-013` no committed stop; one invocation runs AS, LC, LP | T F4; R8 (`create_kgs.py` unchanged since `d06ec5f`); R9 (completion line, no traceback) | Supported |
| `AC-014` LC completes; `as_lc_validation_report.json` passes | T F4/F6; R6 | Supported |
| `AC-015` zero failed LC requests; every Skill has an LC | T F4; R6 (`lc_generation_failures.json` empty); R4 (0 Skills without supports) | Supported |
| `AC-016` LCs are learner skills supported by Skill text | T F7 (15 LCs); D DEV-004 (D-LC-1 fixed); R13 (10 LCs) | Supported by sampled review (design's method; no numeric threshold) |
| `AC-031` each LC a single skill with supports to every Skill it serves | T F4/F6/F7; R4 (supports LC to Skill only, no orphan LC); R13 | Supported by check and sampled review |
| `AC-017` LP completes; merged export; `lp_validation_report.json` passes | T F4/F6; R6 (passed, no errors or warnings, 12,892 claims); R4 | Supported |
| `AC-017` evidence bound 3000; preflight builds one request per pair | T F4; D DEV-005 preflight; R7 | Supported |
| `AC-017` checkpoint scale (synthetic, no reads between full points; bytes equal full rewrite) | T Increment 1 executions 2-5 (source unchanged since); R1; R2 | Supported |
| `AC-017` real run: reuse | T F5; R11 (484 stage results at the stop, none attempted again; no stage succeeded twice) | Supported |
| `AC-017` real run: per-window throughput (design `66a24ab`) | T F5; D DEV-005; R10 (23,471 windows, 0 below floor, 0 tolerated; lowest 78.7%) | Supported |
| `AC-018` no backward buildsTowards in Grade or Term | T F4; R4 (0 of 2,879, ranks from the printed Grade and Term banners) | Supported |
| `AC-032` buildsTowards and relatesTo match Learning Commons meanings | T F7 (24 edges); D DEV-005 (30-edge sample, interim reviews); R13 (12 edges); R4 (no cycle, Skill endpoints only) | Supported by sampled review; limit below |
| `AC-033` budgets 30 / 14,000 in config and final summary | T F4; R6 (summary 30, 14000, bound 13,860, 12,892 pairs); R7 | Supported |
| `AC-019` results review recorded | Plan DEV-006 results review; R4/R6 recount (nodes 2,057; relationships 11,935 by type; dropped 27,576 / 0; defect log D-LC-1, D-LP-1 to D-LP-3; samples) | Supported |
| `AC-020` no curriculum names in backend code changed | T F4; D DEV-006; R8 (0 hits in 2,899 added backend lines) | Supported |
| `AC-021` other example configs unchanged and loading | T F4; R7 | Supported |
| `AC-022` tests show the checkpoint fix (ten items) | T Increment 1 (tests in `b5a325c`, validity mutations in execution 3); R1 (each item mapped to a test); R2 | Supported |
| `AC-023` existing suite and lint pass | T F1 (3,855 passed), F2 (886 slow), F3 (`make lint` clean); R2; R3 | Supported |
| `AC-024` KG pipeline docs describe the checkpoint change | Design assigns it to Documenter | Permitted later dependency (below) |

Retired `AC-006` and `AC-009` are historical only.

Design conformance (R1): the LP checkpoint code matches contract items 1-7.
Full proofs run in the population verifier's constructor in every invocation,
in the store's load whenever checkpoints exist (fresh, resume or recovery),
before the first `completed` receipt (`_save`), at the end of the invocation,
and in every read-only load. Guards compare population and
checkpoint stat identities including `st_ctime_ns`, re-hash only the named
requests and recompute execution material. Records are private, validated on
creation or change, and exposed only as copies. Saves journal stage appends
plus whole small files and check the applied tail, then retire the journal.
Recovery accepts both journal forms and only exact previous or next states.
The dispatch gate's claim that the coordinator changes the active window only
under the gate lock holds: every `active` change happens inside
`gate.coordinate()`, and draining starts only after `gate.close()`.

## Checks and Results

All commands ran 2026-10-13 on HEAD `a182f42` (clean tree). Scratch scripts
and logs are in this session's scratch directory, outside the repository.
Nothing under `results/` or `backend/` was written.

- **R1** Code inspection of the `d06ec5f..a182f42` diff of the three LP modules
  and their callers (see Design conformance), and of the six test files: no
  test function removed; the replaced-behavior tests were rewritten to the new
  contract. `AC-022` items map to `test_every_commit_leaves_the_bytes_a_full_rewrite_would_write`,
  the formats `delta`/`full` mixtures and the slow write-boundary tests,
  `test_pending_transaction_never_authorizes_arbitrary_corruption_repair`,
  `test_guard_rejects_same_size_edit_with_restored_mtime`,
  `test_checkpoint_edit_between_saves_stops_the_next_dispatch`,
  `test_changed_worker_evidence_closes_dispatch_and_publication`, the
  `test_lp_checkpoint_proofs.py` copy and seal tests,
  `test_full_verification_runs_at_construction_completion_and_read_only_loads`,
  `test_per_save_work_does_not_grow_with_completed_requests` and
  `test_directory_left_by_the_pre_change_writer_loads_and_resumes`.
- **R2** From `backend/`, with `CHAT_ENV=testing`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09`, a placeholder
  `OPENAI_API_KEY` and `PATHS_PROJECT_DIR=<repo root>`:
  `.venv/bin/python -m pytest -n auto -q -p no:cacheprovider --run-slow tests/kgfeg/kgs/test_lp_*.py tests/kgfeg/kgs/test_create_kgs_lp.py`
  gave exit 0, 2,722 passed, 85 warnings, 100.8 s. This matches Tester
  execution 8.
- **R3** Non-modifying lint on the nine changed backend files (`isort
  --check-only`, `black --check`, `ruff check`, `mypy`) is clean. `interrogate`
  gives 100% and `pylint` 10.00/10 on the three modules. `make lint` itself was
  not run because its isort and black targets rewrite files. `interrogate`
  regenerated the tracked `interrogate_badge.svg` byte for byte, and
  `git status` stayed clean.
- **R4** Independent export check (`review_checks.py`, read-only) over the
  final nodes and relationships.
  - Nodes: 1 framework, 1,144 items (Grade 4, Term 16, Skill Area 48,
    Sub-strand 152, all `Standard Grouping`; Skill 924 `Standard`), 912 LCs.
  - Relationships: hasChild 1,144, supports 1,204, buildsTowards 2,879,
    relatesTo 6,708. Every item has one parent of the level above.
  - Grades: R, 1, 2, 3 with 4 Terms each, 3 Skill Areas per Term; 0
    `gradeLevel` mismatches.
  - LP: endpoints only Skills; buildsTowards 0 backward (491 cross-Grade,
    1,622 cross-Term, 766 same Grade and Term); no buildsTowards cycle;
    relatesTo has no self or duplicate pairs.
  - Supports: only LC to Skill; 0 Skills without an LC and 0 LCs without a
    Skill; all 924 Skills appear in LP edges.
- **R5** Snapshots.
  - S0: rehashing the 705 files in `logs/kg_construction/dev001/s0_manifest.json`
    gives 0 changed, none added, none removed.
  - S_AS: the 13 files equal the per-file SHA-256 values recorded in DEV-003.
- **R6** Reports and summaries.
  - `as_validation_report.json` and `as_lc_validation_report.json` passed with
    no errors. `lc_generation_failures.json` is `[]`; the one LC summary
    warning is the default leaf (Skill) LC source.
  - `lp_validation_report.json` passed with no errors or warnings: 12,892
    claims, 3,305 `no_relation`, 0 unresolved; receipt hash equals the current
    receipt.
  - `lp_candidate_summary.json`: budgets 30 / 14000, bound 13,860, 12,892
    pairs, dropped 27,576 by the per-Skill budget and 0 by the total budget.
  - `kg_run.json` status `success`; `kg_run_manifest.json` `document_ir_fp` is
    `<doc_key>/stitching/document_ir.json`.
- **R7** CAPS config values match the design.
  - `kgs.as`: five statement types, Sub-strand with 21 canonical values and the
    design's aliases, identity scopes, hierarchy, parent policy, dedup context,
    `max_rows_per_table_window` null.
  - Other values: LP budgets 30 / 14000, evidence bound 3000, concurrency 4,
    `lc_max_failure_rate` 0.05, `overwrite` false; page stages
    `results/kg_for_ed`, 35/135.
  - All seven example configs load as `RunConfig`. For the six other configs
    `output_dir` was redirected to scratch, and Nigeria's `pdf_fp` too because
    that PDF is not on this machine.
  - `git diff --stat d06ec5f HEAD -- examples` lists only the CAPS file.
- **R8** Git checks.
  - `create_kgs.py` is unchanged since `d06ec5f` and has no exit or stop.
  - `git diff --name-only 9697ac4 HEAD -- backend/src examples` is empty, so
    the code and config of the final run are what HEAD holds.
  - The `a182f42` test change is a pure line permutation: sorted-line hashes
    are equal for the three files.
  - The 2,899 added backend lines give 0 hits for curriculum names, Sub-strand
    or Skill Area labels and CAPS terms.
- **R9** The final log `lp_run_3.log` shows AS and LC reused (LC
  `resumed=924`, dedup `resumed=195`, AS+LC bundle reused), ends with
  `KG creation completed successfully`, and has 0 tracebacks and 0 guard
  refresh lines. The only error outcomes are the 8 retried model errors.
- **R10** Independent throughput check (`throughput_check.py`) on
  `lp_run_3.log`: request model time is the sum of its stage times. Windows of
  2 hours start or end at each completion from the 500th; floor is
  0.7 × 2 × 4 × 3600 ÷ the window's median model time. Result: 23,471 windows,
  0 failing, 0 within one request; lowest 78.7% of its own model-bound rate
  (window from 2026-10-12 00:33:47, 549 against a floor of 488.6). This agrees
  with Tester F5 (78.6%) and Developer (78.5%) within window-boundary
  differences.
- **R11** Reuse from `lp_generation_usage.json` and
  `logs/kg_construction/dev005/lp_stop_state.json`.
  - Attempts: 25,794 (run 1: 344 succeeded and 2 unknown; run 2: 140
    succeeded; run 3: 25,300 succeeded and 8 failed).
  - No run-3 attempt repeats any of the 484 stage results present at the stop,
    and none of the 240 completed requests was attempted again. No stage has
    two successes.
- **R12** All 924 Skills' text lines appear in the DocumentIR text (whitespace
  and quote normalized). No two Skills share a parent and text. Exactly 2
  Skills are multi-line, the `AC-026` lead-ins.
- **R13** Reviewer sample read against the endpoints' printed wording and
  placement.
  - LCs (seed 20261013, 5 multi-Skill and 5 single-Skill): all are learner
    skills drawn from the supported Skills' wording, including splits of
    compound Skills. None is teacher guidance, timing or assessment. Merges
    occur only across interchangeable wording.
  - LP (seed 7, 2 per relationship type and pair kind, 12 edges): every
    buildsTowards runs forward and reads as plausible support, including a
    verbatim cross-Grade sight-vocabulary repeat. relatesTo pairs share a skill
    link, two of them loose.
  - This agrees with the Tester and Developer samples.
- **Reused Tester evidence.** F1 `make test` (3,855 passed), F2 slow KG tests
  (886 passed) and F3 `make lint` were run on HEAD `4499743` plus the
  test-definition reordering that `a182f42` committed. R8 confirms that commit
  is a pure reordering, and no source, config or other test changed since. The
  assessed content is identical, so the results still apply.

## Findings

**No material findings.**

## Questions, Limitations, and Later Dependencies

- **Later dependency `AC-024` (Documenter).** Owner: Documenter. Required
  evidence: `docs/pipeline/learning-progressions.md` ("Concurrency, checkpoints,
  and failure") and `docs/guides/running-and-debugging.md` (LP resume) describe
  three things: the full-verification points, the guards between them, and the
  incremental, journaled commits with unchanged at-rest files. Final Reviewer
  and Synchronizer assess it. The design assigns this to Documenter, so it is
  not present-phase work.
- **Sampled-review limit (`AC-016`, `AC-031`, `AC-032`).** These judge LLM
  output. The design verifies them by sampled review with no numeric threshold.
  Three independent samples (Developer, Tester, this review) found no
  contradiction of the Learning Commons definitions or of CAPS order. Some
  low-confidence edges are loose, and the export records
  `pedagogical_correctness_established: false`. Not material: the contract's
  method is satisfied, and the limit is disclosed in the `AC-019` review.
- **Torn append fails closed (design trade-off, not material).** Stage files
  are now extended in place. Before, every file was replaced atomically. An OS
  crash or power loss during an append can leave a stage file at neither its
  previous nor its next length. The design and code then fail closed
  (`_recover_delta`, "unexplained material"), and recovery means LP
  regeneration, losing completed LP work. The journal records the previous
  length and digest, which could prove a safe truncate-and-reapply, but the
  design chose to fail closed (contract item 4). No silent corruption is
  possible, the window is small on the journaling and copy-on-write file
  systems in use, and the run is complete. Recorded so that Documenter states
  the resume behavior accurately.
- **Guard timestamp granularity (not material).** The design's risk note says
  coarse timestamps "can only cause extra full verifications, never a missed
  change". On a file system with coarse (for example 1-second) timestamps, a
  same-size external edit made within one timestamp tick of the store's own
  write would leave the stat identity unchanged. The next full point
  (completion boundary, read-only load, next invocation) still catches it and
  fails closed. In-memory records, not disk bytes, feed publication between
  full points, so no corrupted output can be published. APFS and ext4 keep
  nanosecond timestamps. Documentation should not claim more than "detected
  no later than the next full verification".
- **Stray tool directory (not material).** `kgs/.claude/.cc-writes/` (empty,
  created 2026-10-11) sits in the git-ignored KG output directory. No artifact,
  check or snapshot uses it. Remove it before sharing the output directory.
- **Commands not run.** Full `make test` (reused, see Checks and Results) and
  `make lint` (writes files; R3 used check-only equivalents).
- No blocking question; `BlockedOn` is `NONE`.

## Progress and Conclusion

- Done: every current AC and relevant technical criterion has a current
  disposition. R1-R13 ran, the inputs were rechecked before marking this
  report `COMPLETE` (HEAD `a182f42`, tree clean apart from this report, blob
  ids unchanged), and nothing remains to assess for this gate.
- Gate: passes. No material finding or assessment gap. The only open item,
  `AC-024`, is a permitted later dependency owned by Documenter, with its
  required evidence named. The Full Verification Boundary holds: plan
  `COMPLETE`, verification `COMPLETE` (`FULL`) for the current implementation.
- Plain-language summary: the work can move to documentation. The code change,
  the saved settings and the finished graph match what was asked for and what
  the tests and checks claim. This review reran the LP tests, recounted the
  graph, rechecked the snapshots, the Term order, the reuse of earlier LP
  results and the speed measure, and read a fresh sample of LCs and LP links;
  nothing needs fixing. Still unchecked: the documentation update (`AC-024`).
  The quality of LP links rests on sampled reading, not proof. Passing this
  review does not finish the cycle: documentation, final review and
  synchronization remain.
- Next: Documenter (`DOCUMENTING`), then final Reviewer (`FINAL_DELIVERABLE`)
  and Synchronizer.
