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

`Status`: `PENDING` `Depends On`: `DEV-001` `Acceptance`: `AC-001, AC-002`

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

---

### DEV-003 — Draft the deletion list and obtain the user's approval

`Status`: `PENDING` `Depends On`: `DEV-002` `Acceptance`: `AC-004, AC-006`

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

**Implementation Notes**

This step pauses implementation with `Active Work.BlockedOn` set to the
deletion-list approval request. A later change to the list needs another
recorded approval.

---

### DEV-004 — Replace `TEST_ARGS` with `test` and `test-all` targets

`Status`: `PENDING` `Depends On`: `DEV-002` `Acceptance`: `AC-009, AC-010,
AC-011, AC-012, AC-013`

**Goal**

Give developers one fast default target and one complete target, both running
with the same test environment as CI.

**Affected Area**

`backend/Makefile` (helpers, test section, `.PHONY`, color variables) and
`backend/tests/test.env`.

**Expected Outcome**

- `TEST_ARGS` and the `run-tests` recipe are gone; no other free-form argument
  variable exists.
- One shared runner definition sources `tests/test.env`, exports
  `PATHS_PROJECT_DIR` as the absolute repository root, and runs
  `uv run pytest -n auto <report flags> --cov=. --cov-config=./pyproject.toml --cov-report term-missing <selection> tests`.
- `test` selects `-m "not alembic"` without `--run-slow`; `test-all` passes
  `--run-slow` and no `-m`. pytest-randomly stays on in both.
- Both are in `.PHONY` with `##` help text naming what each selects.
- `tests/test.env` adds `CHAT_ENV=testing`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09`, and
  `OPENAI_API_KEY=sk-fake` with `# pragma: allowlist secret`.
- The color variables silence `tput` errors so the Makefile parses cleanly
  without `TERM` (for CI).

**Self-Check**

`grep TEST_ARGS backend/Makefile` finds nothing; `make help` shows both lines;
`make -n test` and `make -n test-all` print the expected commands;
`env -u TERM make help` prints no `tput` error; a collect-only run through each
target's selection (with a deliberately different shell value for one of the
`test.env` variables) shows the expected counts and that the file values win.

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

`.github/workflows/tests.yml`, job `backend-tests`, test step only.

**Expected Outcome**

The test step runs `make test-all` from `backend/` after `uv sync --extra dev`,
with no direct pytest command and no exit-code-5 special case. Triggers, path
filters, the `changes` job, and the workflow `env` block are unchanged. A CI run
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

All files changed under `backend/tests/`, plus `backend/Makefile` and
`.github/workflows/tests.yml`.

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

## Deletion List

Filled in by DEV-003.

## Approval Record

- Development plan approved by the user on 2026-10-13 ("i approve"), in
  `STEPWISE` mode with user style `tony` (now locked).
- Deletion-list approval: filled in by DEV-003.

## Replacement Map

Filled in by DEV-005.

## Plan Notes

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
- The DEV-002 serial run runs in the foreground in the user's terminal and may
  take hours. Do not interrupt it; a partial log does not satisfy `AC-001`.
