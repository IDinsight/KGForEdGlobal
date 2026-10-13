<!-- STANDARDS
Artifact: DEVELOPMENT
Cycle: make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5
-->

# Development Plan

`Cycle`: `make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5`
`Mode`: `STEPWISE` `User Style`: `tony` `User Style Locked`: `true`
`Status`: `IN_PROGRESS` `Verification Cadence`: `AFTER_IMPLEMENTATION`
`Current Increment`: `NONE`

## Implementation Contract

- `Scope`:
  `.standards/docs/scope/make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5.md`
- `Architecture`:
  `.standards/docs/specs/make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5.md`
- `Request`: Measure and rank local backend test timings, delete the
  user-approved slowest tests while keeping their behavior under cheaper tests,
  speed up the rest, replace `TEST_ARGS` with fixed `make test` and
  `make test-all` targets, and have CI run `make test-all` in parallel.

## Build Steps

### DEV-001 — Record baseline wall-clock times on the unmodified tree

`Status`: `DONE` `Depends On`: `NONE` `Acceptance`: `AC-003`

**Goal**

Capture the "before" times that `AC-007` and `AC-008` compare against.

**Affected Area**

No project files. Results go in **Measurements** below.

**Expected Outcome**

The plan records, for `make test` and `make test TEST_ARGS=--run-slow` run from
`backend/` on the unmodified tree: command, date, commit, elapsed seconds from
`time`, and pytest's passed/failed/deselected counts. Both runs happen before any
file under `backend/` or `.github/workflows/` changes.

**Self-Check**

`git status --short backend .github` is empty before and after both runs; both
summary lines are captured from the run output.

Result (2026-10-13): `git status --short backend .github` empty before and
after; commit `944fed9`. Both logs end with an all-passed pytest summary and a
`real` time; results in **Measurements → Baseline**. The three `FAILED
test_example.py::test_slow_failure` lines in each log are inner pytester
output from `tests/test_slow_selection.py`, not suite failures. Logs checked:
no API key values present.

**Implementation Notes**

Today's `make test` sources only `LOGGING_LOG_LEVEL` from `tests/test.env`. The
caller supplies `CHAT_ENV`, `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION`,
`OPENAI_API_KEY=sk-fake`, and `PATHS_PROJECT_DIR` with the CI values, so the
baseline sees the same settings the reworked targets will set themselves
(DEV-004). Run with no other heavy work on the machine.

---

### DEV-002 — Run the serial timing measurement and derive both rankings

`Status`: `DONE` `Depends On`: `DEV-001` `Acceptance`: `AC-001, AC-002`

**Goal**

Replace the CI log estimates with one local serial measurement of the full
suite and rank tests and files from it.

**Affected Area**

`.standards/docs/development/<cycle>/durations.log`, `test-ranking.tsv`,
`file-ranking.tsv`; a derivation script kept in the session scratchpad (not
under `backend/`), with its source recorded in **Plan Notes**.

**Expected Outcome**

- `durations.log` holds the full console output of the design's measurement
  command (serial, `-p no:randomly`, `-p no:cacheprovider`, `--run-slow`,
  `--durations=0 --durations-min=0`, no coverage, no `-m`).
- `test-ranking.tsv` (`rank seconds nodeid`) has one row per collected node ID,
  per-test time = setup + call + teardown.
- `file-ranking.tsv` (`rank seconds tests path`) has one row per test file.
- Both sort by seconds descending, ties by node ID or path ascending, including
  zero-time entries.
- The plan records command, conditions, date, machine (CPU count, OS), commit,
  total time, outcome counts, any failures, a top-N summary of both rankings,
  and where fixture setup cost distorts the per-test ranking.

**Self-Check**

Row count of `test-ranking.tsv` equals the node IDs from
`pytest --collect-only -q --run-slow -p no:randomly tests` with the same
environment (4,741 expected); row count of `file-ranking.tsv` equals the
distinct file paths in that collection; sum of file seconds equals sum of test
seconds.

Result (2026-10-13): `collection-before.txt` lists 4,741 unique node IDs (the
user ran the collect-only command with the same environment). The derivation
script reported `collected=4741 timed_nodes=4741 unknown=0 untimed=0`,
`test_rows=4741 file_rows=69`, `sum_tests=1382.77 sum_files=1382.77`; 69
equals the distinct file paths in the collection. Every collected node had a
reported duration line.

---

### DEV-003 — Draft the deletion list and obtain the user's approval

`Status`: `DONE` `Depends On`: `DEV-002` `Acceptance`: `AC-004, AC-006`

**Goal**

Turn the ranking into a concrete, user-approved list of deletions and matrix
reductions, each with its planned replacement.

**Affected Area**

**Deletion List** and **Approval Record** sections of this plan. Read-only
inspection of the ranked test files and their importers.

**Expected Outcome**

A versioned deletion list where each item is a file path, a node ID, or a test
function plus the exact parameter IDs to drop, and shows measured time, share of
the full-suite total, covered behavior, and planned replacement (existing
faster test, kept representative case, or new cheaper test). Any proposed new
`slow` mark on a default test is listed as an item. The user's approval is
recorded with date, list version, and their words. No file is deleted or edited
before approval.

**Self-Check**

Every item's time matches `test-ranking.tsv` / `file-ranking.tsv`; every item
has a replacement entry; approval is recorded before DEV-005 starts.

Result (2026-10-13): `deletion-list-v1.tsv` was generated from
`test-ranking.tsv` (seconds copied per node ID), with 350 rows and no node ID in
two items. Every item in **Deletion List → Version 1** has a replacement
entry. Approval recorded in **Approval Record** before any test file changed
(`git status --short backend .github` empty).

**Implementation Notes**

This step pauses implementation with `Active Work.BlockedOn` set to the
deletion-list approval request. A later change to the list needs another
recorded approval.

---

### DEV-004 — Replace `TEST_ARGS` with `test` and `test-all` targets and commit `tests/test.env`

`Status`: `IN_PROGRESS` `Depends On`: `DEV-002` `Acceptance`: `AC-009, AC-010,
AC-011, AC-012, AC-013`

**Goal**

Give developers one fast default target and one complete target, both running
with the same committed test environment as CI.

**Affected Area**

`backend/Makefile` (helpers, test section, `.PHONY`, color variables),
`backend/tests/test.env`, and the root `.gitignore`.

**Expected Outcome**

- `TEST_ARGS` and the `run-tests` recipe are gone; no other free-form argument
  variable exists.
- One shared runner definition sources `tests/test.env`, exports
  `PATHS_PROJECT_DIR` as the absolute repository root, and runs
  `uv run pytest -n auto <report flags> --cov=. --cov-config=./pyproject.toml --cov-report term-missing <selection> tests`.
- `test` selects `-m "not alembic"` without `--run-slow`; `test-all` passes
  `--run-slow` and no `-m`. pytest-randomly stays on in both.
- Both are in `.PHONY` with `##` help text naming what each selects.
- `backend/tests/test.env` is tracked through a root `.gitignore` negation
  `!backend/tests/test.env` placed after the `*.env*` rule (not `git add -f`).
  It holds exactly `CHAT_ENV=testing`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09`,
  `LOGGING_LOG_LEVEL=INFO`, and `OPENAI_API_KEY=sk-fake` with
  `# pragma: allowlist secret`, under a header comment saying the file is
  committed, holds only fake or non-secret test values, and must never hold
  real credentials. `PATHS_PROJECT_DIR` stays out of it (set by the Makefile).
  A missing file is an error, as before.
- The color variables silence `tput` errors so the Makefile parses cleanly
  without `TERM` (for CI).

**Self-Check**

`grep TEST_ARGS backend/Makefile` finds nothing; `make help` shows both lines;
`make -n test` and `make -n test-all` print the expected commands;
`env -u TERM make help` prints no `tput` error; `git check-ignore -v
backend/tests/test.env` reports the negation and `git status` shows the file as
new (then `git ls-files` lists it once committed); the user runs a collect-only
check through each target's selection, with a deliberately different shell
value for one `test.env` variable, showing the expected counts and that the
file values win.

Partial result (2026-10-13, step not done):

- `backend/Makefile` edited: `TEST_ARGS` and `run-tests` removed; `REPO_ROOT`
  and a shared `run-pytest` definition added; `test` and `test-all` targets
  with help text; `test-all` in `.PHONY`; `tput` calls silenced with
  `2>/dev/null`.
- `backend/tests/test.env` edited on disk to add the three CI values, but the
  file is ignored by `.gitignore:5` (`*.env*`) and has never been committed
  (`git ls-files` and `git log --all` return nothing). The change cannot reach
  CI or other clones, so this part of the design does not hold. Routed as a
  `PROJECT_CONTEXT` failure; see **Plan Notes → Suspended Assignment 1**.
- Checks run from `backend/`: `grep -c TEST_ARGS Makefile` → 0; `make help`
  (colors stripped) lists `test` and `test-all` with the expected text;
  `env -u TERM make help` prints no `tput` error; `make -n test` and
  `make -n test-all` print
  `set -a && source ./tests/test.env && set +a && PATHS_PROJECT_DIR="<repo root>" uv run pytest -n auto -rsPQ --cov=. --cov-config=./pyproject.toml --cov-report term-missing`
  followed by `-m "not alembic" tests` and `--run-slow tests` respectively.
  The user has not yet run the collect-only checks through the targets.

Resumed after the revised plan was approved (2026-10-13):

- Root `.gitignore`: added `!backend/tests/test.env` after `!.template.env*`
  (below the `*.env*` rule). `git check-ignore -v --no-index
  backend/tests/test.env` → `.gitignore:8:!backend/tests/test.env`;
  `git status` shows `?? backend/tests/test.env` (to be committed by the user).
- `backend/tests/test.env`: header comment (committed on purpose, fake or
  non-secret values only, never real credentials, `PATHS_PROJECT_DIR` set by
  the Makefile) and exactly `LOGGING_LOG_LEVEL=INFO`, `CHAT_ENV=testing`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09`,
  `OPENAI_API_KEY=sk-fake  # pragma: allowlist secret`. Sourcing it under bash
  exports exactly those four values.
- Env precedence probe from `backend/`: the `make -n test` recipe with pytest
  swapped for `env`, run with `CHAT_ENV=bogus`, `OPENAI_API_KEY=bogus-key`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=1999-01-01`,
  `PATHS_PROJECT_DIR=/tmp/bogus` in the caller's environment, printed the file
  values and `PATHS_PROJECT_DIR=<repo root>`; the same probe on `test-all`
  printed `CHAT_ENV=testing`.
- `pre-commit run --files backend/tests/test.env .gitignore backend/Makefile`
  from the repo root: detect-secrets, end-of-file, trailing-whitespace passed;
  `env-template-sync` first failed only because the sandbox blocked uv's cache,
  then passed when re-run outside the sandbox.
- Remaining: the user runs the two collect-only commands through the targets'
  exact recipes (expected 3,855 and 4,741 tests before deletions).

---

### DEV-005 — Apply the approved deletions, replacements, and helper moves

`Status`: `PENDING` `Depends On`: `DEV-003` `Acceptance`: `AC-005, AC-006`

**Goal**

Remove exactly the approved items while keeping their behavior covered.

**Affected Area**

Approved files and functions under `backend/tests/kgfeg/`; importers of any
deleted file; new non-`test_` helper modules under
`backend/tests/fixtures/<area>/` when a deleted file is imported elsewhere.

**Expected Outcome**

- Every approved item is gone; nothing else is removed, skipped, `xfail`-marked,
  or slow-marked.
- Helpers imported from a wholly deleted file live in
  `tests/fixtures/<area>/` and importers are updated; helpers in a partly
  deleted file stay put.
- Each approved replacement exists (kept representative case or new cheaper
  test), and the **Replacement Map** table lists deleted item, replacement node
  IDs, kind, and covered behavior.

**Self-Check**

Diff of `pytest --collect-only -q --run-slow -p no:randomly tests` against the
DEV-002 collection shows only approved node IDs removed and replacement node
IDs added; the replacement node IDs pass in a focused pytest run; the moved
helpers' importers collect without error.

---

### DEV-006 — Speed up the remaining slowest tests

`Status`: `PENDING` `Depends On`: `DEV-004, DEV-005` `Acceptance`: `AC-007,
AC-008`

**Goal**

Cut runtime beyond deletion using only the design's permitted levers.

**Affected Area**

Remaining top files in the ranking, expected to include
`tests/fixtures/lp_eval/snapshot_fixtures.py` (`build_snapshot` reuse),
`tests/kgfeg/evals/lp_eval/test_automatic_recovery.py` (repeated
`prepare_evaluation`), `test_independent_evaluator.py` (real `asyncio.sleep`
retry seams), heavy `tests/kgfeg/kgs/test_lp_*` fixtures, the Makefile runner
options (`--dist`, report flags), and `[tool.coverage.run] concurrency` in
`backend/pyproject.toml`.

**Expected Outcome**

Each applied speed-up keeps the test's assertions and isolation: cached
immutable inputs are copied per test before mutation, stubs replace real waits
only where elapsed time is not asserted, no change under `backend/src/`, no new
dependencies, no `-p no:randomly` or fixed seed. Each lever's measured effect
(focused before/after time) is recorded in **Measurements**; levers with no
measurable gain are dropped.

**Self-Check**

Focused pytest runs of each changed file, before and after, with random order
and `-n auto`; `git diff --stat backend/src` is empty.

---

### DEV-007 — Make both targets pass and record after-times

`Status`: `PENDING` `Depends On`: `DEV-006` `Acceptance`: `AC-007, AC-008,
AC-013`

**Goal**

Show the reworked suite passes under both targets and is faster than baseline.

**Affected Area**

Test code only, for any shared-state or fixed-path collisions that xdist with
random order exposes (fixed with `tmp_path` / `tmp_path_factory`).

**Expected Outcome**

`make test` and `make test-all` both finish with every selected test passing.
The plan records after-times for both (same run count and conditions as
DEV-001), compared to the DEV-001 baselines, with both lower. Any failure only
fixable in `src/` is recorded and raised with the user, not worked around.

**Self-Check**

`time make test` and `time make test-all` from `backend/`, with the pytest
summary lines captured; at least two runs of each target to catch
order-dependent failures under different random seeds.

---

### DEV-008 — Switch CI to `make test-all` and get a passing run

`Status`: `PENDING` `Depends On`: `DEV-004, DEV-007` `Acceptance`: `AC-014,
AC-015, AC-016`

**Goal**

Have pull-request CI run the same complete command developers use.

**Affected Area**

`.github/workflows/tests.yml`: the workflow-level `env` block and the
`backend-tests` test step.

**Expected Outcome**

The test step runs `make test-all` from `backend/` after `uv sync --extra dev`,
with no direct pytest command and no exit-code-5 special case. The
workflow-level `env` block is removed, so the workflow defines no test
environment values of its own (they come from the committed `tests/test.env`
and the Makefile's `PATHS_PROJECT_DIR`). Triggers, path filters, and the
`changes` job are unchanged. A CI run
on `tz6/fw` passes the backend test job and its log shows the `kgfeg`
term-missing coverage table.

**Self-Check**

YAML validated by prettier/pre-commit; CI run ID, result, and the coverage-table
excerpt recorded here.

**Implementation Notes**

Pushing the branch is outward-facing. Developer asks the user before pushing,
or the user pushes and Developer reads the run with `gh`.

---

### DEV-009 — Pass linters and pre-commit on every changed file

`Status`: `PENDING` `Depends On`: `DEV-005, DEV-006, DEV-007, DEV-008`
`Acceptance`: `AC-017`

**Goal**

Keep all changed test code, the Makefile, and the workflow within project
standards.

**Affected Area**

All files changed under `backend/tests/` (including `tests/test.env`), plus
`backend/Makefile`, `.github/workflows/tests.yml`, and `.gitignore`.

**Expected Outcome**

black, isort, ruff, interrogate, mypy, pylint, prettier, and detect-secrets all
pass on the changed files.

**Self-Check**

`pre-commit run --files <changed files>` from the repo root, and `make lint`
from `backend/`, both clean.

---

## Measurements

Filled in by DEV-001, DEV-002, DEV-006, and DEV-007.

### Baseline (`AC-003`)

- Date: 2026-10-13. Commit: `944fed9` (clean `backend/` and `.github/`).
  Machine: user's Mac, 12 CPUs (`hw.ncpu`), macOS 26.6.2. Run by the user, one
  run each, sequentially, no other heavy work.
- Environment: the user's shell (direnv loads the repo `.envrc` and
  `backend/.env.local`, exporting `ANTHROPIC_API_KEY` and `LLM_*` settings),
  with `CHAT_ENV=testing`, `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09`,
  `OPENAI_API_KEY=sk-fake`, and `PATHS_PROJECT_DIR=<repo root>` prefixed on the
  command. After-runs (DEV-007) should use the same shell setup.
- Timer: `/usr/bin/time -p` around the whole `make` invocation from `backend/`.
- Logs: `make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5/baseline-make-test.log`
  and `.../baseline-make-test-run-slow.log`.

| Command | Elapsed (`real`, s) | pytest time (s) | Passed | Failed | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `make test` | 355.08 | 347.33 | 3,855 | 0 | 91 warnings; 886 slow tests excluded (not printed as "deselected" by `pytest_slow.py`) |
| `make test TEST_ARGS=--run-slow` | 714.49 | 705.74 | 4,741 | 0 | 97 warnings |

Both runs: `-n auto` (12 workers), coverage on, random order, `kgfeg`
coverage TOTAL 61%.

### Timing measurement (`AC-001`, `AC-002`)

- Date: 2026-10-13. Commit: `944fed9`, unmodified `backend/` and `.github/`.
  Machine: as in **Baseline**. Run by the user from `backend/`, in the
  foreground, same shell setup as the baseline.
- Command:
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR="$(cd .. && pwd)" /usr/bin/time -p .venv/bin/python -m pytest -p no:randomly -p no:cacheprovider --run-slow --durations=0 --durations-min=0 -q tests`,
  output tee'd to `durations.log`.
- Conditions: serial (no `-n`), fixed order (`-p no:randomly`), no coverage, no
  `-m`, `--run-slow`.
- Collection: the same environment and selection with `--collect-only -q`, saved
  as `collection-before.txt` (4,741 tests). This is also the "before"
  collection for the `AC-005` comparison.
- Outcome: 4,741 passed, 0 failed, 11 warnings in 1,389.89 s (`real` 1,392.29 s).
- Files (in `make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5/`):
  `durations.log`, `test-ranking.tsv`, `file-ranking.tsv`. Durations have
  0.01 s resolution; 2,033 tests round to 0.00 s.

Where the time goes (summed per-test total 1,382.77 s):

- `tests/kgfeg/evals/lp_eval/` 704.3 s (50.9%); `tests/kgfeg/kgs/` 677.0 s
  (49.0%); everything else about 1.6 s.
- Top 10 tests: 22.1%. Top 50: 44.1%. Top 100: 56.9%. Top 250: 67.8%.
- 52 tests take 5 s or more (44.8%); 156 take 1 s or more (62.4%).

Top 10 files:

| Rank | Seconds | Tests | File |
| ---: | ---: | ---: | --- |
| 1 | 216.07 | 41 | `tests/kgfeg/evals/lp_eval/test_automatic_recovery.py` |
| 2 | 149.49 | 84 | `tests/kgfeg/evals/lp_eval/test_critique_citation_remediation.py` |
| 3 | 137.32 | 52 | `tests/kgfeg/evals/lp_eval/test_presentation.py` |
| 4 | 101.32 | 150 | `tests/kgfeg/kgs/test_lp_reuse.py` |
| 5 | 78.01 | 172 | `tests/kgfeg/kgs/test_lp_finalization.py` |
| 6 | 74.76 | 102 | `tests/kgfeg/kgs/test_lp_artifacts.py` |
| 7 | 72.43 | 93 | `tests/kgfeg/kgs/test_lp_release_policy.py` |
| 8 | 64.37 | 113 | `tests/kgfeg/kgs/test_lp_export.py` |
| 9 | 60.79 | 285 | `tests/kgfeg/kgs/test_lp_orchestration.py` |
| 10 | 59.31 | 169 | `tests/kgfeg/evals/lp_eval/test_independent_evaluator.py` |

Top 10 tests:

| Rank | Seconds | Node ID |
| ---: | ---: | --- |
| 1 | 36.90 | `lp_eval/test_critique_citation_remediation.py::test_incompatible_material_preserves_ledger_and_exhausted_attempts[references]` |
| 2 | 36.28 | `...[prompt]` (same function) |
| 3 | 36.13 | `lp_eval/test_presentation.py::test_chart_memberships_and_raw_counts[2]` |
| 4 | 36.04 | `...test_incompatible_material_preserves_ledger_and_exhausted_attempts[prompts_source]` |
| 5 | 35.34 | `...test_incompatible_material_preserves_ledger_and_exhausted_attempts[sampling_source]` |
| 6 | 31.16 | `lp_eval/test_presentation.py::test_output_boundary_and_tamper_rejection[2]` |
| 7 | 30.18 | `lp_eval/test_automatic_recovery.py::test_selection_orders_incomplete_then_creation_time_then_schedule[False]` |
| 8 | 29.96 | `...test_selection_orders_incomplete_then_creation_time_then_schedule[True]` |
| 9 | 18.08 | `lp_eval/test_presentation.py::test_chart_memberships_and_raw_counts[1]` |
| 10 | 16.18 | `lp_eval/test_presentation.py::test_output_boundary_and_tamper_rejection[1]` |

Fixture-attribution distortions (setup of 2 s or more, 74.4 s total, charged to
the first test that uses the fixture):

- `test_presentation.py`: the module-scoped `_saved` fixture (params `1`, `2`)
  costs 36.13 s and 18.08 s of setup, charged to
  `test_chart_memberships_and_raw_counts[2]` and `[1]`. Those two tests' own
  call time is small; the cost belongs to the whole file.
- `test_independent_evaluator.py::test_all_requests_offline_execution_reporting_and_denominators`:
  10.08 s setup.
- `test_critique_citation_remediation.py`: 5.47 s setup on `...[references]`
  and 4.63 s on `test_classification_does_not_consult_critique_policy_references`.

## Deletion List

### Version 1 (2026-10-13, approved in part: Tier A and B5)

Exact node IDs for every item are in
`make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5/deletion-list-v1.tsv`
(`item seconds nodeid`, 350 rows, no node ID in two items). Seconds and shares
come from `test-ranking.tsv` (full serial total 1,382.77 s). "Default" items
run in `make test`; "full" items are slow-marked and run only in
`make test-all`. Serial seconds overstate the wall-clock gain under `-n auto`.

Source of the analysis: code reading of each test and of the `src/` code path
it reaches, with spot checks against `src/` for A1–A3, A4, A9, and the slow
marks of B5–B6. Before deleting any item in DEV-005, Developer re-checks that
its dropped cases reach the same check as a kept case; if a claim does not
hold, that item is withdrawn and brought back to the user rather than deleted
anyway.

**Tier A — dropped cases hit the same code path as a kept case.** No distinct
behavior is lost.

| Item | Test (file) | Removed | Cases | Seconds | Share | Suite | Why redundant | Replacement (kind) |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |
| A1 | `test_incompatible_material_preserves_ledger_and_exhausted_attempts` (`lp_eval/test_critique_citation_remediation.py`) | `[prompts_source]`, `[sampling_source]` | 2 | 71.38 | 5.16% | default | The evaluator reads no source files (`implementation_fingerprints` is always `()`), so patching source bytes changes nothing. | Kept `[references]`, `[prompt]` (same test); `test_fresh_material_collects_no_source_digests` asserts no `.py` read (existing faster test) |
| A2 | 13 tests using the `_saved` fixture (`lp_eval/test_presentation.py`) | every `[1]` / `[1-…]` case (fixture `params=[1, 2]` → `[2]`) | 18 | 45.10 | 3.26% | default | `presentation.py` has no branch on curriculum count; the 2-curriculum case covers everything the 1-curriculum case does, plus per-curriculum separation. | Same tests' `[2]` cases (kept representative) |
| A3 | `test_projection_implementation_changes_preserve_saved_schedule` (`lp_eval/test_projection_compatibility.py`) | all but `[sampling.py]` | 4 | 18.04 | 1.30% | default | Same as A1: no source file is read, so every file param runs the same code. | `[sampling.py]` (kept representative) |
| A4 | `test_explicit_reruns_preserve_partial_cycles_and_lifetime_usage` (`lp_eval/test_automatic_recovery.py`) | `[rate_limit]`, `[server_error]`, `[invalid_output]`, `[transport]`, `[configuration]`, `[invalid_input]`, `[interrupted]` | 7 | 43.07 | 3.11% | default | `judge._retryable_event` has two classes: retryable (timeout/rate_limit/server_error/invalid_output) and non-retryable; HTTP 400 and citation are the only special paths. | `[timeout]`, `[authentication]`, `[http_400]`, `[citation]` (kept representatives) |
| A5 | `test_execution_boundary_tampering_fails_before_dispatch` (same file) | `[event_count]`, `[event_head]`, `[execution_number]` | 3 | 7.00 | 0.51% | default | These rewrite the payload without resealing its hash, so they fail on the same digest check as `[history_hash]`. | `[history_hash]` (kept) |
| A6 | `test_failed_current_preflight_cannot_close_unknown_history` (same file) | `[configuration]` | 1 | 2.40 | 0.17% | default | Same preflight-raises path as `[authentication]`. | `[authentication]` (kept) |
| A7 | `test_incompatible_store_is_rejected_without_evidence_writes` (`lp_eval/test_independent_evaluator.py`) | `[sdk]`, `[schema]` | 2 | 25.74 | 1.86% | default | All three hit one whole-string comparison of the output contract. | `[mode]` (kept); `test_structured_output_remediation.py::test_incompatible_output_binding_rejects_before_http[*]` covers each mutation in 0.01 s (existing faster test) |
| A8 | `test_all_cells_resume_rejects_material_changes_before_dispatch` (`lp_eval/test_snapshot_reader_matrix.py`) | `[source_format-journal_bearing-wire]` | 1 | 2.07 | 0.15% | default | Rewriting projection bytes fails the same fingerprint check as `[source_bytes-…]`. | `[source_bytes-journal_bearing-wire]` (kept) |
| A9 | `test_checkpoint_contract_rejects_before_effects` (`lp_eval/test_current_input_contract.py`) | every `[internal-…]` and `[converted-…]` case | 60 | 18.36 | 1.33% | default | Execution evidence is rejected (`sampling._snapshot_execution`) before projections are read (`_snapshot_graphs`), so the projection axis never matters. | The 30 `[wire-…]` cases (kept) |
| A10 | `test_unsupported_formats_reject_across_all_entry_and_export_paths_without_effects` (`kgs/test_lp_checkpoint_formats.py`) | 20 attacks: `compatibility_*` (3), `material_capacity_{null,string}`, `usage_capacity_{null,string,missing}`, `usage_attempts_missing`, `missing_both_journals`, `prefix_{empty,partial,stale_material}`, `unauthenticated_{pending,usage}`, `upgraded_with_native_attempts`, `transaction_{next_failure_zero,next_missing_capacity,next_missing_journal,partial_predecessor}` | 20 | 11.13 | 0.80% | default | Each shares a reader branch (`_read_receipt`, `_read_usage`, the capacity check, `_read_transaction`) with a kept attack. | 26 kept attacks, one or more per branch (kept representatives) |
| A11 | `test_invalid_upstream_delivery_rejects_before_publication` (`kgs/test_lp_delivery_contract.py`) | 35 of 44 cases | 35 | 10.20 | 0.74% | default | Two rejection sites: one read-error wrapper and one canonical-JSON equality check. | 9 kept cases covering both sites, both files, both ops |
| A12 | `test_corrupt_checkpoint_populations_fail_at_public_artifact_boundary` (`kgs/test_lp_finalization.py`) | every `[True-…]` case | 33 | 9.35 | 0.68% | full | finalize and validate run the same preflight and claim loading before they diverge. | The 33 `[False-…]` cases (kept) |
| A13 | `test_missing_or_corrupt_bound_artifact_blocks_existing_release` (`kgs/test_lp_reuse.py`) | 30 of 36 cases | 30 | 14.63 | 1.06% | full | Files fail either in one wrapped preflight read or in `read_lp_artifacts`. | 6 kept cases spanning both sites and both attacks |
| A14 | `test_projection_persistence_failures_propagate_through_public_compiler` (`kgs/test_lp_projections.py`) | `drop`, `duplicate`, `metadata`, `reorder` on both files; `missing`, `read_error` on relationships | 10 | 10.41 | 0.75% | full | drop/duplicate/metadata/reorder/truncate fail one byte-equality round trip. | nodes × {write_error, missing, read_error, truncate}; relationships × {write_error, truncate} (kept) |
| A15 | `test_changed_current_material_rejects_before_any_reuse_write` (`kgs/test_lp_reuse.py`) | every `[*-compile]` case except `[model-compile]` | 11 | 5.93 | 0.43% | full | compile and reuse reject in the same shared preflight. | All 12 `[*-reuse]` cases and `[model-compile]` (kept) |
| A16 | `test_corrupt_checkpoint_authority_blocks_compilation` (`kgs/test_lp_export.py`) | all but `[lp_generation_responses.jsonl-gap]` | 14 | 10.58 | 0.77% | full | Its attacks are a subset of `test_lp_reuse.py::test_checkpoint_prefix_corruption_rejects_even_with_resealed_receipts[*]`, same preflight. | Kept case; that reuse test (existing faster test) |
| A17 | `test_audit_cannot_authorize_generated_bundle_edits` (`kgs/test_lp_release_policy.py`) | 14 of 16 cases | 14 | 8.57 | 0.62% | full | The audit file is never read in `src/`; only two rejection paths exist. | `[combined-reuse-direction]`, `[standalone-compile-insert]` (kept) |
| A18 | `test_missing_malformed_and_noncanonical_standalone_artifacts_fail_closed` (`kgs/test_lp_export.py`) | 18 of 21 cases | 18 | 8.31 | 0.60% | full | Same 7 files and attacks as `test_lp_artifacts.py::test_missing_malformed_or_noncanonical_files_are_rejected[*]`; only `lp_generation_failures.json` takes a different (preflight) path. | 3 kept cases incl. the failures file; that artifacts test (existing test) |

Tier A total: 283 cases, 322.27 s (23.3% of the serial suite): 254.49 s
default, 67.78 s full-only.

**Tier B — a representative case stays; a named scenario is lost or a test is
rewritten.**

| Item | Test (file) | Removed | Cases | Seconds | Share | Suite | What is lost | Replacement (kind) |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |
| B1 | `test_unfinished_start_is_closed_before_new_execution` (`lp_eval/test_automatic_recovery.py`) | `[2]`–`[5]`, `[7]`–`[10]` | 8 | 26.01 | 1.88% | default | Intermediate prior-attempt counts. | `[1]` (no prior failures), `[6]` (middle), `[11]` (cycle boundary) (kept) |
| B2 | `test_automatic_resume_reuses_successes_and_ignores_new_discovery` (same file) | whole test | 1 | 10.88 | 0.79% | default | The combination "automatic selection + symlinked results root + a new snapshot appearing". Each part is covered separately. | `test_snapshot_reader_matrix.py::test_all_cells_complete_lifecycle_and_cached_resume[journal_bearing-wire]` (resume and cached no-op) plus `test_discovery_all_layouts_alias_cycles_and_active_exclusion` (aliases) (existing tests) |
| B3 | `test_schedule_runs_without_git_or_source_hash_gates` (`lp_eval/test_independent_evaluator.py`) | whole test | 1 | 5.60 | 0.40% | default | A standalone "no Git call" check on the 778-request schedule. | Git guard in the lifecycle test above; `test_fresh_material_collects_no_source_digests` (existing tests) |
| B4 | `test_selection_orders_incomplete_then_creation_time_then_schedule` (same recovery file) | `[False]` and `[True]` replaced by one unparametrized test | 2 → 1 | 29.96 (net) | 2.17% | default | Nothing, if the merge is right: the new test runs the untied sequence, then re-ties `created_at` and asserts the schedule-hash tie-break. It is a rewrite, so it carries rewrite risk. | One merged test (new cheaper test) |
| B5 | 10 six-curriculum profile matrices in `kgs/` (release_policy, artifacts, reuse, finalization, concurrency_boundaries, kg_manifest_resume, projections, orchestration, export, relationships) | every `rwanda_math` case | 17 | 114.31 | 8.27% | full | End-to-end runs over Rwanda's shape (flat hierarchy, 5 statement types, one shared LC, 100 pairs at the candidate budget). No assertion in these tests is specific to it. | Four small profiles keep every profile-specific assertion; `test_lp_generation.py::test_six_fixture_round_trips_reconcile_material_and_preserve_upstream[*]` still round-trips expanded Rwanda by default (kept representatives, existing test) |
| B6 | same 10 matrices | every `ghana_english` case | 17 | 64.13 | 4.64% | full | End-to-end runs over Ghana English's cross-grade recurrence. If B5 and B6 are both taken, no large profile runs end to end. | Same as B5 |
| B7 | `test_long_graph_traversal_exceeds_recursive_depth_without_losing_edges` (`kgs/test_lp_finalization.py`) | `[False]`, `[True]` | 2 | 11.99 | 0.87% | full | Nothing, if the replacement is right: the time is a 300-item harness run, not the traversal. | New unit test calling the `lp_finalization` component and cycle-diagnostic traversal on a synthetic 300-node path and cycle under a lowered recursion limit (new cheaper test) |
| B8 | `test_corrupt_checkpoint_populations_fail_at_public_artifact_boundary` (`kgs/test_lp_finalization.py`) | `[False-…]` draft and verdict cases except `-prerequisite` | 20 | 5.69 | 0.41% | full | Per-stage repeats of the same attacks; `test_lp_orchestration.py::test_resealed_success_prefix_attacks_reach_structural_validation[*]` covers most of them per stage. | `[False-lp_generation_responses.jsonl-*]` (11) and both `-prerequisite` cases (kept) |

Tier B total: 67 cases, 268.57 s (19.4%): 72.45 s default, 196.12 s
full-only.

**Not proposed for deletion** (left to DEV-006 speed-ups, since every case is
distinct or the cost is shared setup): `test_presentation.py` `_saved` setup
cost itself, `test_all_requests_offline_execution_reporting_and_denominators`
(its 10 s is module-fixture setup that would move to the next test),
`test_all_cells_complete_lifecycle_and_cached_resume`,
`test_more_than_six_current_snapshots_and_capacities_remain_framework_isolated`,
`test_fresh_material_collects_no_source_digests`,
`test_bad_matching_store_blocks_without_fresh_discovery`, and
`test_journaled_transactions_resume_at_every_artifact_write_boundary`. No test
gains a `slow` mark in this list.

## Approval Record

- Development plan approved by the user on 2026-10-13 ("i approve"), in
  `STEPWISE` mode with user style `tony` (now locked).
- Revised plan (DEV-004 and DEV-008 after Frame 1) approved by the user on
  2026-10-13 ("aprove").
- Deletion-list approval: on 2026-10-13 the user approved **version 1, Tier A
  (A1–A18) plus B5**, in their words: "approve Tier A plus B5". B1–B4 and
  B6–B8 are not approved and stay in the suite. The approved node IDs (300
  cases, 436.58 s serial) are in
  `make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5/deletion-list-approved.tsv`,
  filtered from `deletion-list-v1.tsv` with
  `awk -F'\t' 'NR==1 || $1 ~ /^A/ || $1=="B5"'`. Any change to this set needs
  another recorded approval.

## Replacement Map

Filled in by DEV-005.

## Plan Notes

### Suspended Assignment 1

`Recovery Frame`: `1` `Recovery Reason`: `backend/tests/test.env is gitignored (.gitignore:5 *.env*) and untracked, so the design's shared test env cannot reach CI or fresh clones.` `Purpose`: `DEVELOPMENT`
`Target`: `NONE` `Assessed Inputs`: `backend/Makefile (working tree, DEV-004
edits), backend/tests/test.env (untracked, local edit), deletion-list-approved.tsv,
CONTEXT.md and the technical design as of commit 944fed9` `Next Action`:
`Reconcile DEV-004 with the corrected context and design (how the test env
values reach CI and fresh clones), finish DEV-004 and its collect-only
self-checks, then pause before DEV-005.`

DEV-001 to DEV-003 are done and unaffected. DEV-004 is in progress; its
Makefile changes stay in the working tree.

Reconciliation after Frame 1 closed (2026-10-13): the Auditor recorded the
untracked `tests/test.env` in `CONTEXT.md` (commit `da41730`) and the
Architect revised the design at the user's direction (commit `c7588bb`): the
file is committed through a `.gitignore` negation with exactly four values and
a header comment, and the CI workflow drops its `env` block. This changes the
approved outcome of DEV-004 (adds `.gitignore` and the committed file) and
DEV-008 (removes the `env` block instead of leaving it), so the plan returned
to `PROPOSED` for approval. The Makefile part of DEV-004 (commit `b99c29e`)
already matches the revised design and is kept. DEV-001 to DEV-003, DEV-005 to
DEV-007, and DEV-009's checks are otherwise unchanged. The suspended
assignment resumes as its `Next Action` says once the plan is approved.

- Order follows the design's build plan: baselines (DEV-001) and the serial
  measurement (DEV-002) run before any change under `backend/` or
  `.github/workflows/`. DEV-004 can proceed while DEV-003 waits on approval.
- In this cycle the test suite is the deliverable. Developer edits test code
  only as the design directs (deletions, approved replacements, helper moves,
  speed-ups, isolation fixes). Tester still owns formal verification of every
  AC. Replacements prefer existing faster tests and kept representative cases;
  new cheaper tests are added only where the approved list says so.
- Test commands: the user runs every pytest and `make test` / `make test-all`
  command on their machine, from commands Developer gives. Developer then
  pauses, reviews the output the user shares (or the saved log), and records
  the results. Developer may still run read-only checks itself (`grep`,
  `git status`/`diff`, `make -n`, `make help`), the derivation script, and
  linters.
- DEV-002 derivation command, run from the repository root:
  `python3 -I rank_durations.py <cycle dir>/durations.log <cycle dir>/collection-before.txt <cycle dir>`.
  The script lives only in the session scratchpad; its full source is below so
  the TSVs can be regenerated from the log.

  <details><summary><code>rank_durations.py</code></summary>

  ```python
  """Derive per-test and per-file timing rankings from a pytest durations log.

  Usage: python rank_durations.py <durations.log> <collection.txt> <out_dir>

  Per-test time is the sum of the setup, call, and teardown durations reported
  for one node ID. Per-file time is the sum of per-test times for every node ID
  whose path (the text before the first "::") is that file. Every collected node
  ID appears in the test ranking, including ones with no reported duration
  (recorded as 0). Both rankings sort by seconds descending, then by node ID or
  path ascending.
  """

  import re
  import sys
  from collections import defaultdict
  from pathlib import Path

  # Matches a durations-report line such as
  # "36.28s call     tests/a/test_x.py::test_y[param]".
  _DURATION_LINE = re.compile(r"^(\d+(?:\.\d+)?)s (setup|call|teardown)\s+(\S.*?)\s*$")


  def _read_collection(path: Path) -> list[str]:
      """Return node IDs from `pytest --collect-only -q` output."""
      return [
          line.strip()
          for line in path.read_text().splitlines()
          if line.startswith("tests/") and "::" in line
      ]


  def _read_durations(path: Path) -> dict[str, float]:
      """Sum setup, call, and teardown seconds per node ID in the durations section."""
      totals: dict[str, float] = defaultdict(float)
      in_section = False
      for line in path.read_text().splitlines():
          if "slowest durations" in line:
              in_section = True
              continue
          if not in_section:
              continue
          match = _DURATION_LINE.match(line)
          if match:
              totals[match.group(3)] += float(match.group(1))
      return totals


  def main() -> None:
      """Write test-ranking.tsv and file-ranking.tsv and print consistency checks."""
      log_path, collection_path, out_dir = (Path(arg) for arg in sys.argv[1:4])
      collected = _read_collection(collection_path)
      durations = _read_durations(log_path)

      unknown = sorted(set(durations) - set(collected))
      missing = sorted(set(collected) - set(durations))

      tests = sorted(
          ((round(durations.get(node, 0.0), 2), node) for node in collected),
          key=lambda row: (-row[0], row[1]),
      )
      with (out_dir / "test-ranking.tsv").open("w") as handle:
          handle.write("rank\tseconds\tnodeid\n")
          for rank, (seconds, node) in enumerate(tests, start=1):
              handle.write(f"{rank}\t{seconds:.2f}\t{node}\n")

      file_seconds: dict[str, float] = defaultdict(float)
      file_counts: dict[str, int] = defaultdict(int)
      for seconds, node in tests:
          file_path = node.split("::", 1)[0]
          file_seconds[file_path] += seconds
          file_counts[file_path] += 1
      files = sorted(
          ((round(total, 2), path) for path, total in file_seconds.items()),
          key=lambda row: (-row[0], row[1]),
      )
      with (out_dir / "file-ranking.tsv").open("w") as handle:
          handle.write("rank\tseconds\ttests\tpath\n")
          for rank, (seconds, path) in enumerate(files, start=1):
              handle.write(f"{rank}\t{seconds:.2f}\t{file_counts[path]}\t{path}\n")

      print(f"collected={len(collected)} unique={len(set(collected))}")
      print(f"timed_nodes={len(durations)} unknown={len(unknown)} untimed={len(missing)}")
      print(f"test_rows={len(tests)} file_rows={len(files)}")
      print(f"sum_tests={sum(s for s, _ in tests):.2f} sum_files={sum(s for s, _ in files):.2f}")
      for node in unknown[:5]:
          print(f"unknown: {node}")


  if __name__ == "__main__":
      main()
  ```

  </details>

- The DEV-002 serial run runs in the foreground in the user's terminal and may
  take hours. Do not interrupt it; a partial log does not satisfy `AC-001`.
