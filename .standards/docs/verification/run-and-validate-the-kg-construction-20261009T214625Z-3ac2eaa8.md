<!-- STANDARDS
Artifact: VERIFICATION
Cycle: run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8
-->

# Verification Report

`Cycle`: `run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8` `Mode`: `VERIFY` `Status`:
`IN_PROGRESS` `User Style`: `NONE`
`Assessment Purpose`: `INCREMENT` `Assessment Target`: `Increment 1`

## Assessed Inputs

- Scope `.standards/docs/scope/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md`
  at `cfbbf4d` (last changed `31c9f8f`, `AC-033`); current acceptance set
  `AC-001` to `AC-033` except retired `AC-006` and `AC-009`.
- Design `.standards/docs/specs/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md`
  at `cfbbf4d`: LP checkpoint integrity and throughput contract (D-LP-2,
  Decision 9); the `AC-017` checkpoint-scale save-time ratio and the real-run
  trend check were removed by user rework in `cfbbf4d`.
- Context `.standards/CONTEXT.md` (test and lint baseline: CI-equivalent pytest
  env, `make test`, `make lint`).
- Development plan `.standards/docs/development/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md`
  at `cfbbf4d`: cadence `INCREMENTAL`, `Current Increment` 1 (DEV-007 to
  DEV-009, `AC-017`, `AC-020`, `AC-022`, `AC-023`).
- Active change for Increment 1: `backend/src/kgfeg/kgs/lp_checkpoints.py`
  (HEAD blob `32710db`, commits `8040c56`, `64acbd2`, `b697e8e`),
  `lp_generation.py` (`d0ed431`), `lp_requests.py` (`0695aed`, `8040c56`
  only). Tree clean at `cfbbf4d` when this assessment started. The plan's
  Readiness names `lp_checkpoints.py` blob `65c44a6`, which is not in the
  object store; the committed blob `32710db` is what this assessment tests.
- Session: a Tester conversation with no Developer implementation history;
  client session metadata is not available, so freshness is not certified.

## Acceptance Evidence

| AC / technical criterion | Tests or checks | Disposition and evidence |
| ------------------------ | --------------- | ------------------------ |
| `AC-022`: at-rest bytes and receipt equal a full rewrite across dispatch, completion, promotion, retry, failure and recovery | `test_lp_checkpoint_cadence.py::test_every_commit_leaves_the_bytes_a_full_rewrite_would_write`; supplementary execution 4 (real pre-change writer) | Verified for Increment 1 (execution 2, 3, 4). |
| `AC-022`: crash recovery at each commit step of the new journal; recovery of a pre-change full-payload journal | Slow write-boundary tests (updated); formats mixture test rows `full-initialize`, `full-update`, `delta-update`; supplementary execution 4 (`lp_crash_recovery.py`) | Verified for Increment 1 (execution 2, 3, 4). |
| `AC-022`: unexplained file states fail closed | `test_pending_transaction_never_authorizes_arbitrary_corruption_repair` (6 rows, delta journal); `test_lp_concurrency_recovery.py::test_invalid_pending_or_usage_state_rejects_before_calls_and_preserves_bytes`; formats attacks | Verified for Increment 1 (execution 2). |
| `AC-022`: population content edit with mtime restored is detected (`st_ctime_ns`) | `test_lp_population_verifier.py::test_guard_rejects_same_size_edit_with_restored_mtime`; refresh case `test_guard_reads_nothing_until_an_identity_changes_then_refreshes_it` | Verified for Increment 1 (execution 2, 3). |
| `AC-022`: checkpoint-file edit between saves detected before the next dispatch or publication | `test_checkpoint_edit_between_saves_stops_the_next_dispatch` (dispatch); existing `test_lp_checkpoint_proofs.py::test_scheduler_rejects_resealed_checkpoint_edit_during_call` (publication) | Verified for Increment 1 (execution 2, 3). |
| `AC-022`: mutated dispatched request detected before dispatch or publication | Existing `test_lp_population_verifier.py::test_changed_worker_evidence_closes_dispatch_and_publication` (both boundaries, capacity 1 and 4, covers the DEV-009 `in_flight` guard) | Verified for Increment 1 (execution 2). |
| `AC-022`: caller mutation of a returned record cannot change persisted bytes or store decisions | Updated `test_lp_checkpoint_proofs.py` rows: `test_changed_nested_record_revalidates[*]`, `test_changed_request_and_execution_material_revalidate[*]`, `test_order_and_prerequisite_edits_fail_closed[*]`, `test_resealed_disk_cannot_replace_last_validated_snapshot[True]`, `test_cache_history_is_bounded_by_current_positions` | Verified for Increment 1 (execution 2). |
| `AC-022`: full verification still streams every population byte at construction, completion and read-only loads | `test_full_verification_runs_at_construction_completion_and_read_only_loads`; existing `test_lp_streaming_proofs.py::test_every_verification_streams_every_artifact_byte` | Verified for Increment 1 (execution 2, 3). |
| `AC-022`: per-save work (population bytes read, records re-proved) does not grow with completed count | `test_per_save_work_does_not_grow_with_completed_requests` | Verified for Increment 1 (execution 2, 3). |
| `AC-022`: a checkpoint directory written by the pre-change code loads and resumes | `test_directory_left_by_the_pre_change_writer_loads_and_resumes` (user-approved +1); supplementary execution 4 | Verified for Increment 1 (execution 2, 3, 4). |
| `AC-022`: existing tests pinning every-check streaming updated, not deleted | 28 fast and 60 slow tests updated (see Scenario Budget) | Verified (execution 1, 2). |
| `AC-022` (overall) | All of the above | Increment 1 code change (DEV-007 to DEV-009) covered. Any later code change in this cycle needs its own tests. |
| `AC-023` | `make test`, `make lint`, LP fast and slow suites | Verified for Increment 1's changes (executions 6, 8, 9). Rerun at full verification after DEV-005 and DEV-006. |
| `AC-020` | Execution 7 | Backend part verified for Increment 1 (no curriculum names in any added backend source or test line). The example-config part is `AC-021` at full verification. |
| `AC-017` (checkpoint scale, synthetic): no population or whole checkpoint read between full points; at-rest files equal the full-rewrite writer | Execution 5; `test_per_save_work_does_not_grow_with_completed_requests`; `test_every_commit_leaves_the_bytes_a_full_rewrite_would_write` | Verified (execution 5, 12,892 requests). |
| `AC-017` (evidence bound, preflight, LP completion, real run) | DEV-005 | `AWAITING_IMPLEMENTATION` (DEV-005). |
| `AC-001`, `AC-002`, `AC-003`, `AC-004`, `AC-005`, `AC-007`, `AC-008`, `AC-010`, `AC-011`, `AC-012`, `AC-014`, `AC-015`, `AC-016`, `AC-025`, `AC-026`, `AC-027`, `AC-028`, `AC-029`, `AC-030`, `AC-031` | Developer checks in DEV-001 to DEV-004 | Not in Increment 1; assessed at full verification (S0 and S_AS comparisons depend on DEV-006). |
| `AC-013`, `AC-018`, `AC-032`, `AC-033`, `AC-019`, `AC-021` | DEV-005, DEV-006 | `AWAITING_IMPLEMENTATION` (DEV-005, DEV-006). |
| `AC-024` | Documenter | Pending later role: Documenter updates `docs/pipeline/learning-progressions.md` and `docs/guides/running-and-debugging.md`. |

## Increment Assessments

| Increment | Outcome and acceptance scope | Assessed inputs / evidence | Disposition |
| --------- | ---------------------------- | -------------------------- | ----------- |
| 1 | LP checkpoint integrity and throughput fix, DEV-007 to DEV-009 (plan Increment 1) / `AC-017` (synthetic checkpoint scale), `AC-020` (backend part), `AC-022` (all items), `AC-023` | Source at `cfbbf4d` (`lp_checkpoints.py` `32710db`, `lp_generation.py` `d0ed431`, `lp_requests.py` `0695aed`) with the Tester test changes; design at `cfbbf4d`; executions 1-9 | VERIFIED |

Not part of Increment 1: the real-run parts of `AC-017` (DEV-005) and every
other AC (see Acceptance Evidence).

## Scenario Budget

Default ceiling: five new scenarios per changed source file. Updating an existing
test that pins replaced behavior to the new contract keeps its scenario and uses
no new allowance; parameter rows whose write boundary no longer exists were
re-pointed to the boundary that replaced it.

| Source file | Reused coverage | Active-change allocations | User additions |
| ----------- | --------------- | ------------------------- | -------------- |
| `kgs/lp_checkpoints.py` | LP suite; 19 rows in `test_lp_checkpoint_proofs.py`, 2 `full` rows of the formats mixture test, 6 rows of `test_pending_transaction_never_authorizes_arbitrary_corruption_repair`, the slow write-boundary tests (`test_journaled_transactions_resume_at_every_artifact_write_boundary`, `test_atomic_rename_interruptions_preserve_durable_draft_without_repeated_call`, `test_interrupted_pending_and_promotion_transactions_reuse_every_durable_stage`) updated to the new contract | 5 of 5: (1) `test_every_commit_leaves_the_bytes_a_full_rewrite_would_write`; (2) formats mixture row `delta-update`; (3) `test_checkpoint_edit_between_saves_stops_the_next_dispatch`; (4) `test_per_save_work_does_not_grow_with_completed_requests` (shared); (5) `test_full_verification_runs_at_construction_completion_and_read_only_loads` (shared) | +1 (user, 2026-10-11): the `AC-022` item that a checkpoint directory written by the pre-change code loads and resumes; limit one scenario on this file: `test_directory_left_by_the_pre_change_writer_loads_and_resumes` |
| `kgs/lp_requests.py` | Population verifier and streaming tests; `test_transaction_publication_rechecks_changed_population` updated | 4 of 5: `test_guard_rejects_same_size_edit_with_restored_mtime`; `test_guard_reads_nothing_until_an_identity_changes_then_refreshes_it`; shared (4) and (5) | None |
| `kgs/lp_generation.py` | `test_changed_worker_evidence_closes_dispatch_and_publication` (mutated dispatched or in-flight request, both boundaries, capacity 1 and 4) | 2 of 5: shared (4) and (5) | None |

New tests live in `backend/tests/kgfeg/kgs/test_lp_checkpoint_cadence.py` and
`test_lp_population_verifier.py`. Each new scenario is a single row (no
parameters).

## Execution Evidence

All runs from `backend/` on HEAD `cfbbf4d` source (`lp_checkpoints.py`
`32710db`, `lp_generation.py` `d0ed431`, `lp_requests.py` `0695aed`) with the
uncommitted Tester test changes listed above, environment
`CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo root>`
(placeholder key, no network), Python 3.13.9, 2026-10-11. Logs in
`logs/kg_construction/tester/` (git-ignored).

1. Baseline, unchanged tests on HEAD: `.venv/bin/python -m pytest -n auto -q -p no:cacheprovider tests/kgfeg/kgs/test_lp_*.py tests/kgfeg/kgs/test_create_kgs_lp.py`
   → 28 failed, 1820 passed (`inc1_baseline_lp.log`); the 28 are exactly the
   plan's list. Same files with `--run-slow -m slow` → 60 failed, 848 passed
   (`inc1_baseline_lp_slow.log`). The Developer's runs did not include slow
   tests, so these 60 were not in the plan's list. All 60 inject their crash
   through whole-file `_atomic_write`/`os.replace` of stage files, which the
   delta writer now extends in place, so the injection never fires (`DID NOT
   RAISE`, or the run then halts on the scripted failure). They pin the
   replaced write mechanism; no implementation defect.
2. Updated and new tests, targeted runs: proofs file 30 passed; formats,
   verifier and pending-transaction tests 147 passed; slow write-boundary tests
   (`--run-slow`) 141 passed; new cadence file 4 passed; affected files
   together 204 passed. Lint on the six changed test files: isort, black, ruff,
   mypy, interrogate (100%) clean, pylint 10.00/10.
3. Test validity (scratch copies of HEAD in `$TMPDIR`, one change each; the
   repository's source is untouched): `file_stat_identity` without
   `st_ctime_ns` fails the restored-mtime guard test and the checkpoint-edit
   test; `_save` validating every record fails the per-save test; `_save`
   running the full input proof fails the per-save and full-points tests; a
   stage row written with a stray space fails the full-rewrite test; a guard
   that ignores stat changes fails both guard tests.
4. Supplementary, Tester-run Developer scripts against the real pre-change
   writer (`cb7d9dc`, loaded from git): `logs/kg_construction/dev009/lp_byte_equivalence.py --out-dir logs/kg_construction/tester`
   → passed, 93 operations, identical at-rest files and receipts, and each
   writer's class loads the other's directory; `lp_crash_recovery.py` → passed,
   360 interruptions, 273 delta journals recovered, pre-change full-payload
   journals recovered, torn append and unexplained small file fail closed.
   Execution evidence only; not committed tests.
5. Full-scale check: `PYTHONPATH=.:src .venv/bin/python ../logs/kg_construction/tester/inc1_scale_check.py --out-dir ../logs/kg_construction/tester --work-dir $TMPDIR/inc1_scale_work`
   (reuses the DEV-009 benchmark harness; real orchestration on the confirmed
   CAPS AS+LC bundle and committed config, 12,892 requests, padded stub calls,
   about 11 kB of stage rows per request; reads only `as_lc_kg_bundle.json`
   and the config, writes nothing under `results/`; 11:14-12:48 UTC
   2026-10-11, 5,644 s) → exit 0, `passed: true` (`inc1_scale_check.json`):
   12,892 requests completed in 90,246 saves; zero reads of any population
   file or whole checkpoint file between full-verification points (every
   population read was at one of 7 full points; every whole checkpoint read
   was a full point or this check's own comparison); the at-rest files and
   receipt equal an independent full canonical rewrite at every 2,000th
   commit (45 checks) and at the end; the real pre-change writer (`cb7d9dc`)
   loads the final directory read-only, which it accepts only if the bytes
   equal its own full re-encoding. For information only (criterion removed by
   the 2026-10-11 rework): median save 0.0247 s over the first 500 requests
   and 0.0691 s over the last 500 (2.8x), matching the design's expected
   2.5-3x.
6. `make test`: plain `make test` failed before collecting tests, first on a
   sandbox denial of uv's cache (`~/.cache/uv`), then (with `UV_CACHE_DIR` in
   scratch, `UV_OFFLINE=1`) on missing settings (`PATHS_PROJECT_DIR`), as
   `CONTEXT.md` notes for `tests/test.env` alone; environment limits, not
   defects. Rerun with the CI-equivalent environment plus
   `UV_CACHE_DIR=$TMPDIR/uv-cache UV_OFFLINE=1`: exit 0, 3854 passed
   (`inc1_make_test.log`). Collected before the last new test
   (`test_directory_left_by_the_pre_change_writer_loads_and_resumes`) was
   added; that test file is covered by execution 8, and the only other change
   since is that test's import, which no other test uses.
7. `AC-020`: `git diff 19f44f5 -- backend` added lines and the new test file
   contain no curriculum names or curriculum-specific text (grep for CAPS,
   Funda Wande, South Africa, the other example countries and programs, and
   CAPS sub-strand terms): none found.
8. Final LP suites with every test change in place: `.venv/bin/python -m pytest -n auto -q -p no:cacheprovider --run-slow tests/kgfeg/kgs/test_lp_*.py tests/kgfeg/kgs/test_create_kgs_lp.py`
   → exit 0, 2722 passed, 0 failed (`inc1_final_lp_all.log`).
9. `make lint` (isort, black, ruff, interrogate, mypy, pylint, cloc on `src/`
   and `tests/`) with the same environment: exit 0; isort and black left all
   178 files unchanged, ruff clean, interrogate 100%, mypy no issues, pylint
   10.00/10 (`inc1_make_lint.log`). `git status` shows only the Tester test
   files and this report.

## Open Findings and Dependencies

- `AC-024` documentation: Documenter, later phase.
- Non-blocking Developer record discrepancies, for Developer to correct when
  reconciling this checkpoint: the Increment 1 Readiness names
  `lp_checkpoints.py` blob `65c44a6`, which is not in the object store (the
  committed blob is `32710db`; this assessment tested the committed content);
  and DEV-009 says the `test_lp_concurrency_recovery.py` interruption tests
  pass against the delta journal, but its slow-marked rows were not run and
  24 of them failed on HEAD (execution 1). Both are test-mechanism or record
  issues; no implementation defect was found.

## Resume or Handoff

### Suspended Assignment 1

`Recovery Frame`: `1` `Recovery Reason`: `User rework: remove the design's LP checkpoint-scale criterion (median save over the last 500 requests within 2x of the first 500); late saves are expected near 2.5-3x early ones from the whole-file usage rewrite, with negligible real-run effect.` `Purpose`: `INCREMENT`
`Target`: `Increment 1` `Assessed Inputs`: `HEAD d54b8d4; lp_checkpoints.py 32710db, lp_generation.py d0ed431, lp_requests.py 0695aed; design at d54b8d4 (with the AC-017 2x save-time ratio); plan Increment 1 Readiness; no Tester work started`
`Next Action`: `Reconcile Increment 1 with the reworked design, then write the AC-022 tests, update the 28 tests pinning replaced behavior, and run the LP tests, the full suite and lint (AC-023).`

Recorded retroactively on resume (2026-10-11): the `USER_REWORK` from
`TESTING` (`abcb867`) happened before Tester had opened Increment 1, so no
report existed to hold the assignment then.

### Recovery Reconciliation 1

Frame 1 closed by Architect in `cfbbf4d` with no rerun (`RESUME` to
`TESTING`). Restored Suspended Assignment 1. The design change only removes
the `AC-017` checkpoint-scale save-time ratio and the real-run trend check;
no code, test or plan content changed, and no Tester evidence existed, so
nothing is invalidated. Increment 1 continues in `VERIFY` mode against the
design at `cfbbf4d`.

### Increment 1 Result

Increment 1 verified (2026-10-11). Report stays `IN_PROGRESS`; returned to
Developer as a `CHECKPOINT`.

Remaining acceptance work for full verification: DEV-005 (stop LP run 2,
read-only load of its checkpoints with the fixed code, resume; the real-run
`AC-017` criteria; `AC-018`, `AC-032`, `AC-033`), DEV-006 (`AC-001`, `AC-002`,
`AC-013`, `AC-019`, `AC-021`, the S0 and S_AS comparisons), the full
reconciliation of the DEV-001 to DEV-004 ACs, a fresh `make test` and
`make lint` on the final implementation, and `AC-022` tests for any further
code change. `AC-024` belongs to Documenter.

Next action (Tester, on the next assignment): reload state and inputs,
reconcile any change since `cfbbf4d`, then assess the assigned increment or
full verification.
