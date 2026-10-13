<!-- STANDARDS
Artifact: REVIEW
Cycle: run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8
ReviewKind: FINAL_DELIVERABLE
-->

# Review Report

`Cycle`: `run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8` `ReviewKind`: `FINAL_DELIVERABLE`
`Status`: `COMPLETE` `User Style`: `NONE`

## Assessed Inputs and Scope

Final-deliverable review, 2026-10-13, entered by the `FORWARD` handoff
`DOCUMENTING -> REVIEWING_FINAL`. `CycleMode` `STANDARD`, `CompletionPolicy`
`FULL_DELIVERABLE`. Recovery and outstanding obligations inactive; `BlockedOn`,
`BaselineReconciliation` and `PendingVerificationCadence` `NONE`. No user style
selected.

- **Contract.** Scope (SHA-256 `d6cccac4f30dcc49...`, unchanged since `31c9f8f`):
  31 current conditions, `AC-001` to `AC-033` except retired `AC-006` and
  `AC-009`. Design (`abb2e926c037f47c...`, last changed `66a24ab`): Acceptance
  Coverage, LP checkpoint integrity and throughput contract (items 1-7),
  Technical Acceptance Criteria. Context `.standards/CONTEXT.md`
  (`202946c349224a96...`, `7ccfb3a`).
- **Range.** Baseline `d06ec5f` (previous cycle signed off); assessed HEAD
  `d6cad44`; working tree clean apart from this report. The implementation
  review assessed HEAD `a182f42`. Since then: `aa4f7cb` (state and the
  implementation report only) and `d6cad44` (the documentation commit:
  `docs/pipeline/learning-progressions.md`, `docs/guides/running-and-debugging.md`,
  `docs/guides/adding-a-curriculum.md`, the `lp_checkpoints.py` module docstring,
  the documentation record, state).
- **Deliverable content (git blob).** `lp_checkpoints.py` `e978f1d` (`32710db`
  at the run and the implementation review; the only difference is the module
  docstring), `lp_generation.py` `d0ed431`, `lp_requests.py` `0695aed`, the six
  LP test files as the implementation review lists them, CAPS config `8f43478`.
  Documentation: `learning-progressions.md` `26c3fb0`,
  `running-and-debugging.md` `78fd018`, `adding-a-curriculum.md` `b319c02`, equal
  to the documentation record's saved identities. Assessed and unchanged:
  `docs/reference/output-artifacts.md` `04cb5de`, `docs/pipeline/index.md`
  `2efdb18`, `docs/architecture.md` `ff2185f`.
- **Workflow records.** Plan `COMPLETE` (`cb041daefa0996e5...`); verification
  `COMPLETE`, `FULL` (`e9c8ec866e1292ff...`); implementation review `COMPLETE`,
  no findings (`9e608da41b634596...`); documentation record `COMPLETE`
  (`5723c19cbb5a1446...`). Evidence logs under `logs/kg_construction/` (dev001
  to dev009, `tester/`, `tester/full/`).
- **Run outputs** (local, git-ignored):
  `results/kg_for_ed/c939a3a9dcce92ed61d970f4599ced6f2ddc425d1220eeeab9828472b878d24d/`.
  Final export `kgs/as_lc_lp_nodes.jsonl` (`f83c4cfe6fb476e2...`),
  `as_lc_lp_relationships.jsonl` (`2a6564d6f6a76e38...`), `as_lc_lp_kg_bundle.json`
  (`5ba389f52b8141b2...`), `lp_validation_report.json` (`b2268c3bae9d2eae...`),
  `lp_candidate_summary.json` (`019cf90a48b592c9...`), checkpoint receipt
  (`0c51a2f7db3c1425...`); run log `logs/kg_construction/dev005/lp_run_3.log`
  (`c25e7009e5028776...`). All equal the implementation review's identities
  (FR3), and no transaction journal is present.
- **Excluded.** Paid LLM stages (no reruns); unrelated repository areas; the
  page IR stages (non-goal).
- **Session.** This conversation began at the Reviewer invocation and holds no
  scoping, design, implementation, testing or documentation authoring. The
  reviewing model is Claude Opus 5.5. The models that produced the work are
  not recorded, so model independence cannot be confirmed.

## Contract and Evidence Assessment

Evidence key: T = Tester formal evidence (verification report F1-F7, Increment 1
executions 1-9); D = Developer self-checks of record; IR = implementation
review R1-R13; Doc = documentation record C1-C7; FR = this review's checks
(Checks and Results).

| Obligation / criterion | Assessed evidence | Current disposition |
| ---------------------- | ----------------- | ------------------- |
| `AC-001` config output dir and pages 35/135 in both page stages; loads | T F4; IR R7; FR4 | Supported |
| `AC-002` KG runs read `stitching/document_ir.json`; upstream unchanged | T F4; IR R5/R6; FR1 (S0 705/705, none added or removed); FR3 (`document_ir_fp`) | Supported |
| `AC-003` AS completes; `as_validation_report.json` passes | T F4/F6; FR3 | Supported |
| `AC-004` 4 Grades, 4 Terms each, expected 3 Skill Areas per Term | T F4; IR R4; FR2 | Supported |
| `AC-025` exactly the listed Sub-strands, 152 | T F4/F6 (per-table names, preflight 152/0); IR R4/R7; FR2 | Supported |
| `AC-005` one Skill per printed bullet, full text | T F4; D DEV-002/DEV-006; IR R12; FR1 (S_AS unchanged) | Supported |
| `AC-026` lead-ins joined | T F4; IR R12 | Supported |
| `AC-027` Skill under its printed Sub-strand and table placement | T F4/F6; IR R4; FR2 | Supported |
| `AC-007` no root fallback, no unresolved hasChild | T F4/F6; IR R4; FR2; FR3 (unresolved edges, merge conflicts, needs-review empty) | Supported |
| `AC-008`, `AC-028` nothing from assessment rows, schedules, notes, headings | T F4/F6 | Supported |
| `AC-029` same-worded Skills stay separate | T F4; IR R12 | Supported |
| `AC-010` Grade R to K, Grades 1-3; every Skill's grade | T F4; IR R4; FR2 | Supported |
| `AC-030` groupings `Standard Grouping`, Skills `Standard` | T F4; IR R4; FR2 | Supported |
| `AC-011` AS confirmation recorded with S_AS | Plan DEV-003; T F4; FR1 | Supported |
| `AC-012` LC and LP built on confirmed AS | T F4/F6; IR R5/R9; FR1 | Supported |
| `AC-013` no committed stop; one invocation runs AS, LC, LP | T F4; IR R8/R9; FR5 | Supported |
| `AC-014` LC completes; `as_lc_validation_report.json` passes | T F4/F6; FR3 | Supported |
| `AC-015` zero failed LC requests; every Skill has an LC | T F4; FR2; FR3 | Supported |
| `AC-016` LCs are learner skills supported by Skill text | T F7; D DEV-004; IR R13 | Supported by sampled review (design's method) |
| `AC-031` single skill; supports to every Skill served | T F4/F6/F7; IR R4/R13; FR2 | Supported by check and sampled review |
| `AC-017` LP completes; merged export; LP validation passes | T F4/F6; IR R4/R6; FR2; FR3 | Supported |
| `AC-017` evidence bound 3000; preflight builds every request | T F4; D DEV-005; FR4 | Supported |
| `AC-017` checkpoint scale (synthetic) | T Increment 1 executions 2-5; IR R1/R2; FR6 | Supported |
| `AC-017` real run: reuse | T F5; IR R11; FR3 | Supported |
| `AC-017` real run: per-window throughput | T F5; D DEV-005; IR R10; FR3 (log unchanged) | Supported |
| `AC-018` no backward buildsTowards | T F4; IR R4; FR2 | Supported |
| `AC-032` buildsTowards and relatesTo meanings | T F7; D DEV-005; IR R13; FR2 | Supported by sampled review; limit below |
| `AC-033` budgets 30 / 14,000 in config and final summary | T F4; IR R6/R7; FR3; FR4 | Supported |
| `AC-019` results review recorded | Plan DEV-006; IR R4/R6; FR2; FR3 | Supported |
| `AC-020` no curriculum names in backend code changed | T F4; D DEV-006; IR R8; FR5 | Supported |
| `AC-021` other example configs unchanged and loading | T F4; IR R7; FR4 | Supported |
| `AC-022` tests show the checkpoint fix | T Increment 1; IR R1/R2; FR6 | Supported |
| `AC-023` suite and lint pass | T F1-F3 (before `d6cad44`); FR6, FR7 (HEAD `d6cad44`) | Supported |
| `AC-024` KG pipeline docs describe the checkpoint change | Doc C1-C7; FR8-FR10 | Supported; resolves the implementation review's later dependency |

Retired `AC-006` and `AC-009` are historical only.

**`AC-024` and its technical criterion.** Both named pages describe the three
required topics. The full-verification points: the LP page's list and the
guide's first new paragraph. The guards between them: the LP page's two
bullets and the guide. The incremental, journaled commits with unchanged
at-rest files: the LP page's save and journal paragraphs and the guide's second
new paragraph. FR8 checked each statement against the code. The implementation
review asked for two limits to be stated without overclaiming, and the LP page
states both: a coarse-timestamp edit is caught at the next full verification,
and a torn append fails closed and means LP regeneration. The other three edits
are accurate too: the add-a-curriculum tip, the "every example profile" wording
and the module docstring (FR9).

**Prior conclusions rechecked.** The implementation review found the run's code
equal to HEAD (R8). `d6cad44` has since changed `lp_checkpoints.py`, but only
its module docstring. No code reads that docstring. `lp_execution_material`
hashes `prompts.py` only, and finalization hashes only its own source, so
neither the LP execution identity nor the finalization material changed. The
full suite and lint pass on HEAD (FR6, FR7). The implementation review's other
conclusions rest on outputs and records that are unchanged (FR1-FR3).

## Checks and Results

All run 2026-10-13 on HEAD `d6cad44` (clean tree apart from this report).
Scripts and logs are in this session's scratch directory, outside the
repository. Nothing under `results/`, `logs/` or `backend/` was written, and
`git status` stayed clean apart from this report after every check.

- **FR1** Snapshots (`fdr_checks.py`, read-only).
  - S0: every file under `extraction/`, `verification/` and `stitching/` was
    rehashed against `logs/kg_construction/dev001/s0_manifest.json`. 705 files,
    0 changed, none added or removed.
  - S_AS: the 13 files equal `dev003/s_as_manifest.json`, and their combined
    digest equals DEV-003's `deb631a3...`.
- **FR2** Independent recount from the merged export.
  - Nodes: 1 framework, 1,144 items and 912 LCs.
  - Items: Grade 4, Term 16, Skill Area 48, Sub-strand 152 (all
    `Standard Grouping`), Skill 924 (`Standard`).
  - Relationships: hasChild 1,144, supports 1,204, buildsTowards 2,879,
    relatesTo 6,708.
  - Structure: no item has two parents. Every Skill's chain is Skill >
    Sub-strand > Skill Area > Term > Grade > framework, with 0 grade
    mismatches.
  - LP: buildsTowards has 0 backward edges by (Grade, Term): 491 cross-Grade,
    1,622 cross-Term and 766 within one Term. There is no cycle, and every LP
    endpoint is a Skill.
  - Supports: only LC to Skill. 0 Skills without an LC and 0 LCs without a
    Skill.
- **FR3** Reports and identities.
  - Validation: `as_validation_report.json`, `as_lc_validation_report.json` and
    `lp_validation_report.json` passed with 0 errors (LP 0 warnings).
    `lc_generation_failures.json` is empty. `sfi_merge_conflicts.json`,
    `sfi_merge_needs_review.json` and `has_child_unresolved_edges.json` are
    empty lists.
  - Run records: receipt `completed`, run 3, stage counts 12,892 each, 0 failed
    pairs; `kg_run.json` `extra.status` `success`; `kg_run_manifest.json`
    `document_ir_fp` ends `<doc_key>/stitching/document_ir.json`.
  - Candidates: `lp_candidate_summary.json` records budgets 30 / 14,000, bound
    13,860 and 12,892 pairs, with 27,576 dropped by the per-Skill budget and 0
    by the total.
  - Usage: `lp_generation_usage.json` holds 25,794 attempts. 25,788 carry usage
    (706.0M input, 43.6M output tokens), 25,308 are from run 3, and all 8
    failures are resolved. This matches the `AC-019` review.
  - Identities: the seven export, report, receipt and log identities equal the
    implementation review's.
- **FR4** CAPS config values read from the committed file.
  - Page stages: `output_dir` `<repo>/results/kg_for_ed`, 35/135 in both.
  - `kgs.as`: hierarchy Grade > Term > Skill Area > Sub-strand > Skill, with
    the matching identity scopes, parent policy and dedup context.
    `max_rows_per_table_window` is null.
  - `kgs.lp` and the rest: budgets 30 / 14,000, evidence bound 3000,
    concurrency 4, `lc_max_failure_rate` 0.05, `overwrite` false.
  - Other configs: all seven example configs set `unresolved_participation` to
    `include_unresolved_with_warnings`.
- **FR5** Git checks.
  - `create_kgs.py` has no commit since `d06ec5f` and contains no `exit` call.
  - `git diff a182f42 HEAD -- backend examples` is the 5-line docstring change
    only, with 0 curriculum-term hits.
  - No code reads `__doc__`. Only `prompts.py` (execution material) and
    `lp_finalization.py` (its own finalization material) are hashed as source.
- **FR6** Full suite. Command, from `backend/` with `tests/test.env` and
  `CHAT_ENV=testing`, `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09`, a
  placeholder `OPENAI_API_KEY` and `PATHS_PROJECT_DIR=<repo root>`:
  `.venv/bin/python -m pytest -n auto -rsPQ -p no:cacheprovider -m "not alembic" tests`.
  This is `make test` without `uv run` or coverage.
  - Result: exit 0, 3,855 passed, 77 warnings, 283.7 s.
  - The `FAILED test_example.py::test_slow_failure` lines come from the inner
    pytester run of `tests/test_slow_selection.py` (lines 22-48). That test
    expects the failure to propagate.
- **FR7** Check-only lint on `src/` and `tests/`, same environment.
  - `isort --check-only` 0; `black --check` 0 (178 files unchanged); `ruff
    check` 0 on both trees.
  - `interrogate` 100%, with its badge redirected to scratch.
  - `mypy` reports no issues (92 and 86 files); `pylint` rates both 10.00/10.
  - `make lint` itself was not run because its isort and black targets rewrite
    files. `cloc` was skipped; it is statistics only.
- **FR8** Documentation claims checked against the code.
  - Full points: `LPRequestPopulationVerifier.__init__` and `_load` at start
    (`validate_lp_checkpoint_format` runs twice before construction); the
    completion boundary in `_save`; the `full=True` check after
    `_finish_lp_requests`; and the read-only loads (`validate_lp_checkpoint_format`
    before LP, export, final-bundle reuse and finalization; finalization's store
    built with current material).
  - Guards: `_check_checkpoint_files(full=False)` and `guard` on 5 stat fields,
    with the two quoted `INFO` strings. Per-request re-hashes happen at
    admission (`_admit_lp_requests`), at every transport call (`LPDispatchGate.check`
    → `_verify_active_material`) and at publication (published plus
    `in_flight`).
  - Saves: stage appends (`_append_bytes`); small files and the receipt
    replaced whole (`_SMALL_NAMES`); delta journal fields; apply, check,
    unlink order in `_commit_delta`; exact previous/next roll-forward in
    `_recover_delta`; full-payload journals in `_recover`.
  - Strings and names: every quoted error string and file name exists.
    `max_concurrent_requests` is in `lp_execution_material`.
- **FR9** The other edits.
  - The add-a-curriculum tip matches the run and the committed hierarchy.
  - The "every example profile" wording matches FR4.
  - The module docstring matches `_save` and `_recover_delta`.
  - Related passages agree with the new text: the LP page's historical-reuse
    section, `docs/reference/output-artifacts.md` (transaction file transient)
    and `docs/pipeline/index.md`.
- **FR10** `backend/.venv/bin/mkdocs build --strict -f mkdocs.yml -d $TMPDIR/fdr_site`
  from the repo root: exit 0. The anchor `how-checkpoints-are-checked-and-saved`
  appears once, and the guide links to it once.
- **Reused evidence.** Tester F1-F7 and Increment 1 executions, and
  implementation review R1-R13, stay valid for content that has not changed
  since: source other than the docstring, tests, config, run outputs and logs
  (FR1, FR3, FR5). FR6 and FR7 supersede F1-F3 for `AC-023` on the current
  tree.

## Findings

**No material findings.**

## Questions, Limitations, and Later Dependencies

- **Earlier dependency resolved.** `AC-024` was the implementation review's only
  later dependency, owned by Documenter. `d6cad44` resolves it, and FR8-FR10
  assessed it independently. No later dependency remains.
- **Sampled-review limit (`AC-016`, `AC-031`, `AC-032`).** These conditions
  judge LLM output. The design verifies them by sampled review with no numeric
  threshold. Three independent samples (Developer, Tester, implementation
  review) found no contradiction of the Learning Commons definitions or CAPS
  order. Some low-confidence edges are loose, and the export records
  `pedagogical_correctness_established: false`. This is not material: the
  contract's method is met, and the `AC-019` review discloses the limit.
- **Doc precision, read-only format check (not material).** The LP page says a
  full verification "rechecks the execution identity (configuration, prompts,
  schemas, and model)" and runs at every read-only load. The standalone
  `validate_lp_checkpoint_format` builds its store from the receipt's own
  material. It therefore proves the stored identity and population are
  consistent, but does not compare them with the current configuration.
  Finalization, which builds its store with current material, and generation
  resume both do compare. The pages also state elsewhere that resume and reuse
  reject stale configuration, prompts and model. No operator action depends on
  the format check alone, so this needs no correction for `AC-024`. A later
  documentation pass could say "against the recorded material" for that check.
- **Doc precision, end-of-loop check (not material).** The full check after the
  request loop streams the population, rechecks material and reads back every
  checkpoint file, but does not rerun the complete record validation. Records
  change only through validated updates, and a completed run already passed
  `_validate()` at the completion boundary. The documented safety property
  holds.
- **Torn appends beyond crashes (not material).** A disk-full error during a
  stage append could also leave a file at neither journaled state. The journal
  is written first and is far larger than an append, so the disk would have to
  fill within the few kilobytes between the two writes. The result is the same
  documented fail-closed outcome, and the design chose fail-closed recovery
  (contract item 4).
- **Developer script after later commits (not material).**
  `dev005/lp_checks.py` compares `backend` and `examples` with `9697ac4`. Rerun
  now, its `AC-013` item would flag the test reordering (`a182f42`) and the
  docstring (`d6cad44`), which postdate the run. FR5 shows neither changes run
  behavior. The recorded run of that script (nine checks, all pass) stands.
- **Project context.** `.standards/CONTEXT.md` describes the pre-cycle baseline:
  the old CAPS `output_dir`, no CAPS test profile and the pre-fix checkpoint
  cost. Under the protocol, planned active-cycle work does not make context
  stale; the next cycle's audit reconciles it.
- **Stray tool directory (not material).** `kgs/.claude/.cc-writes/`, noted by
  the implementation review, remains in the git-ignored output directory. No
  artifact or check uses it. Remove it before sharing the output directory.
- **Commands not run.** `make lint` (writes files; FR7 ran check-only
  equivalents) and `cloc`; no LLM or pipeline runs.
- No blocking question; `BlockedOn` is `NONE`.

## Progress and Conclusion

- Done: every current AC and relevant technical criterion has a current
  disposition, and the earlier `AC-024` dependency is resolved. FR1-FR10 ran.
  The inputs were rechecked before this report was marked `COMPLETE`: HEAD was
  still `d6cad44`, the tree was clean apart from this report, and the blob ids
  were unchanged. Nothing remains to assess for this gate.
- Gate: passes. There is no unresolved material finding, assessment gap,
  dependency or blocking question, and no obligation owned by this state. The
  Full Verification Boundary holds for the current implementation: plan
  `COMPLETE`, verification `COMPLETE` (`FULL`), and the only later change is a
  docstring, rechecked by FR6 and FR7.
- Plain-language summary: the work can move to the last check before sign-off.
  The finished graph, the code change, the saved settings and the new
  documentation match what was asked for. This review recounted the graph,
  confirmed the source files and the confirmed AS results are unchanged, and
  reran the full test suite (3,855 passed) and the code checks on the final
  code. It also checked each new documentation statement against the code and
  built the docs site. Nothing needs fixing. Two details in the new LP page
  could be stated more exactly, but nothing an operator does depends on them. LP link quality still rests on sampled reading, not proof.
  Passing this review does not finish the cycle: synchronization and the
  user's sign-off remain.
- Next: Synchronizer (`SYNCHRONIZING`).
