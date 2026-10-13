<!-- STANDARDS
Artifact: ARCHITECTURE
Cycle: make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5
-->

# Technical Design: Make the Backend Test Suite Fast Again

Design mode: evolution. The work changes an existing test-running setup (one
`make test` target with a free-form `TEST_ARGS`, a serial CI command) and the
suite's contents. The main risk is the move from that setup to two fixed targets
and a parallel full-suite CI run without losing coverage or test isolation.

## Context

- Scope: `.standards/docs/scope/make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5.md`
  (`AC-001` through `AC-017`). Production code under `backend/src/` is out of
  bounds. The `--run-slow` switch in `backend/tests/pytest_slow.py` stays the
  only fast/full separator.
- Current state, from `.standards/CONTEXT.md` and the files themselves:
  - `backend/Makefile` defines `TEST_ARGS ?=` (line 27) and a `test` target that
    delegates to an unlisted `run-tests` recipe:
    `uv run pytest -n auto -rsPQ -m "not alembic" --cov-report term-missing --cov-config=./pyproject.toml --cov=. tests $(TEST_ARGS)`,
    after sourcing `tests/test.env`.
  - `.github/workflows/tests.yml` job `backend-tests` runs the same pytest
    command serially, without `--run-slow`, and maps exit code 5 to success.
  - `tests/test.env` sets only `LOGGING_LOG_LEVEL`. `BackendSettings`
    (`backend/src/kgfeg/config.py`) requires `PATHS_PROJECT_DIR` and validates
    `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION` as non-blank. CI supplies these plus
    `CHAT_ENV` and a fake `OPENAI_API_KEY` in its workflow `env`. Locally they
    come only from whatever the user's shell (direnv over the repo-root `.env`)
    provides, so local and CI runs can differ.
  - Branch suite: 3,855 default tests, 4,741 with `--run-slow`; all slow marks
    are in `tests/kgfeg/kgs/`; `tests/kgfeg/evals/lp_eval/` (816 tests) has none.
    No test carries the `alembic` mark.
  - Test modules import each other as fixture libraries (for example
    `test_lp_generation.py` is imported by 20 modules), and `build_snapshot` in
    `tests/fixtures/lp_eval/snapshot_fixtures.py` drives a full synthetic LP run
    for six lp_eval files.
  - pytest-randomly is auto-loaded. Whether every test passes under xdist with
    random order is not established; CI has only ever run serially.
  - `src/` does not use greenlet, although coverage is configured with
    `concurrency = ["greenlet", "thread"]`.

## Decision

1. **Measure once, serially, with pytest's durations report.** One run of the
   full suite, outside `make`, produces a raw log. Both rankings are derived from
   that one log, so they come from the same run by construction.
2. **Keep the rankings, baselines, deletion list, approval, and replacement map
   in Developer's cycle records.** Summaries and decisions go in the development
   plan; the full rankings go in data files beside it.
3. **Deletion is gated on an explicit user approval recorded before the first
   deletion.** Moving a default test into the slow set counts as a deletion for
   this purpose, so the default set cannot shrink without the same approval.
4. **Two fixed Makefile targets share one runner definition** and differ only in
   test selection. No free-form argument variable replaces `TEST_ARGS`.
5. **The Makefile targets set the test environment themselves.** `tests/test.env`
   carries the same non-secret test values CI uses, and the recipe sets
   `PATHS_PROJECT_DIR` to the repository root. Local and CI runs then see the
   same settings, whatever the developer's shell exports.
6. **CI calls `make test-all`.** Parallelism and coverage come from the target,
   so CI and developers run the same command.
7. **Speed-ups stay inside test code and runner settings** and must keep every
   surviving test's checked behavior, isolation, and pass status under random
   order and xdist. No new dependencies.

## Acceptance Coverage

- `AC-001`, `AC-002`: Measurement run and derived ranking files under
  **Interfaces and Contracts → Timing measurement** and **Cycle record layout**.
- `AC-003`: Baseline wall-clock protocol under **Wall-clock measurement**, taken
  on the unmodified tree before any other build step.
- `AC-004`: Deletion list and approval record under **Deletion contract**; the
  build plan orders approval before any deletion.
- `AC-005`: **Deletion contract** rules on exact removal, matrix reductions,
  re-marking as slow, and helper relocation.
- `AC-006`: Replacement map and replacement kinds under **Deletion contract**.
- `AC-007`, `AC-008`: **Wall-clock measurement** protocol for after-times, and
  the permitted and forbidden speed-up levers under **Speed-up rules**.
- `AC-009` through `AC-012`: **Makefile test targets** contract.
- `AC-013`: **Makefile test targets** environment contract and the isolation
  requirements in **Speed-up rules**; pass status is established by later
  verification.
- `AC-014`, `AC-015`: **CI workflow** contract.
- `AC-016`: No architectural impact beyond the **CI workflow** contract. It is
  satisfied by an actual CI run on this branch during implementation and
  verification.
- `AC-017`: No architectural impact. It depends on the repository's established
  linters and pre-commit hooks (black, isort, ruff, interrogate, mypy, pylint,
  prettier, detect-secrets) applied during implementation and verification.

## Components

- `backend/Makefile` — owns the two test entry points and the shared runner
  options.
- `backend/tests/test.env` — owns the non-secret test environment values sourced
  by both targets.
- `backend/tests/pytest_slow.py` and `backend/tests/test_slow_selection.py` —
  unchanged. They remain the slow/default selection contract and its tests.
- `backend/tests/**` test modules and `backend/tests/fixtures/**` — where
  deletions, replacements, relocated helpers, and test-level speed-ups land.
- `backend/pyproject.toml` `[tool.pytest.ini_options]` and
  `[tool.coverage.*]` — runner and coverage settings; only the changes allowed in
  **Speed-up rules** may be made.
- `.github/workflows/tests.yml` job `backend-tests` — calls the Makefile.
- Developer cycle records:
  `.standards/docs/development/<Active Work.Id>.md` and the folder
  `.standards/docs/development/<Active Work.Id>/`.

## Interfaces and Contracts

### Timing measurement (`AC-001`, `AC-002`)

- Run from `backend/`, on the user's machine, before any change to tests or
  test configuration (it may follow the `AC-003` baseline runs):

  ```sh
  CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 \
  OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR="$(cd .. && pwd)" \
  .venv/bin/python -m pytest -p no:randomly -p no:cacheprovider --run-slow \
    --durations=0 --durations-min=0 -q tests
  ```

  No `-n`, no `--cov`, no `-m` expression. With no `alembic`-marked tests, this
  selects the same 4,741-test set as `make test TEST_ARGS=--run-slow`. The fake
  key is the literal placeholder CI already uses.
- Save the complete console output as the raw log. Failures or errors in this
  run do not invalidate timings; record them in the plan.
- Per-test time is the sum of the `setup`, `call`, and `teardown` durations
  reported for the same node ID. Per-file time is the sum of per-test times for
  every node ID whose path (the part before the first `::`) is that file.
- Both rankings sort by time descending, ties broken by node ID or path
  ascending, and include every collected test or file, including zero-time
  entries.
- Known attribution effect: a module- or session-scoped fixture's setup cost is
  charged to the first test that uses it. The per-file ranking captures it
  correctly; the plan should flag this where it distorts the per-test ranking.

### Cycle record layout (`AC-001` to `AC-008`)

- `.standards/docs/development/<Active Work.Id>/durations.log` — raw log from the
  measurement run.
- `.standards/docs/development/<Active Work.Id>/test-ranking.tsv` — header
  `rank	seconds	nodeid`, one row per test.
- `.standards/docs/development/<Active Work.Id>/file-ranking.tsv` — header
  `rank	seconds	tests	path`, one row per test file.
- The development plan records: the exact measurement command, conditions
  (serial, `-p no:randomly`, no coverage, `--run-slow`), date, machine
  description (CPU count, OS), commit; the run's total time and outcome counts;
  a short top-N summary of both rankings; every wall-clock measurement; the
  deletion list; the user's approval; and the replacement map.
- The derivation script, if one is written, is not committed under `backend/`.
  Its command or source is recorded in the plan so the TSVs can be regenerated
  from the log.

### Wall-clock measurement (`AC-003`, `AC-007`, `AC-008`)

- Elapsed wall-clock time of the whole `make` invocation from `backend/`,
  measured with `time` on the user's machine.
- Baseline (`AC-003`) on the unmodified tree: `make test` and
  `make test TEST_ARGS=--run-slow`. After (`AC-007`, `AC-008`): `make test` and
  `make test-all`.
- Each measurement records command, date, commit, elapsed seconds, and pytest's
  summary counts (passed, failed, deselected). Take the same number of runs
  before and after (at least one each); with more than one, compare medians.
  Runs use the same machine with no other heavy work running.

### Deletion contract (`AC-004` to `AC-006`)

- The deletion list names each item as a test file path, a test node ID, or a
  test function plus the exact parameter IDs to remove (matrix reduction). Each
  item shows its measured time and share of the full-suite total from the
  ranking, the behavior it covers, and its proposed replacement.
- Adding a `slow` mark to a test that runs by default is a list item with the
  same approval requirement, even though the test stays in the suite.
- Approval is recorded in the plan before any deletion: date, the approved list
  version, and the user's words. Any change to the list after approval needs
  another recorded approval.
- After deletions, only approved items are gone. Comparing collection with
  `--run-slow` before and after must show exactly the approved node IDs missing,
  plus any replacement tests added.
- Every deleted item maps to at least one surviving or new node ID that
  exercises the same behavior. The replacement is one of: an existing faster
  test, a representative case kept from a reduced matrix, or a new cheaper test.
  The map is a table in the plan: deleted item, replacement node IDs, kind,
  covered behavior.
- When a deleted file is imported by surviving modules, move the helpers they
  import into a non-`test_` module under `backend/tests/fixtures/<area>/` and
  update the importers. Helpers in a partly deleted file stay where they are.

### Makefile test targets (`AC-009` to `AC-013`)

- Remove `TEST_ARGS` and the `run-tests` recipe. Do not add another free-form
  argument variable. Focused runs use pytest directly.
- One shared runner definition used by both targets:
  source `tests/test.env` (`set -a` / `set +a`, as today), export
  `PATHS_PROJECT_DIR` as the absolute repository root (the parent of
  `backend/`), then
  `uv run pytest -n auto <report flags> --cov=. --cov-config=./pyproject.toml --cov-report term-missing <selection> tests`.
- Selection:
  - `test`: `-m "not alembic"`, no `--run-slow`. Slow tests stay excluded by
    `pytest_slow.py`.
  - `test-all`: `--run-slow` and no `-m` expression, so slow- and
    alembic-marked tests are both included. Do not pass `--test-alembic`; the
    project has no alembic configuration.
- pytest-randomly stays active in both targets (no `-p no:randomly`).
- `test` and `test-all` are in `.PHONY` and carry `##` help comments that state
  what each selects (fast default set excluding slow- and alembic-marked tests;
  every test including both).
- `tests/test.env` adds `CHAT_ENV=testing`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09`, and
  `OPENAI_API_KEY=sk-fake` with a `# pragma: allowlist secret` comment. Because
  the file is sourced, these override shell values inside the targets.

### CI workflow (`AC-014`, `AC-015`)

- Replace the `backend-tests` pytest step with `make test-all`, still running
  from `backend/` after `uv sync --extra dev`. Remove the exit-code-5 special
  case; `make` reports any pytest failure as a non-zero exit.
- Parallelism comes from the target's `-n auto`. The coverage report comes from
  the target's `--cov-report term-missing`; pytest-cov combines worker data, so
  the job log still prints the `kgfeg` coverage table.
- Triggers, path filters, the `changes` job, and the workflow `env` block stay
  unchanged. Inside `make`, `tests/test.env` values take precedence over the
  workflow `env` for the three overlapping variables; the values are identical.
- The job must not fail because `tput` has no `TERM` when the Makefile is
  parsed. Developer chooses the fix (for example setting `TERM` for the step or
  guarding the color variables).

### Speed-up rules (`AC-007`, `AC-008`, `AC-013`)

Permitted:

- Reusing expensive immutable test inputs across tests by widening fixture
  scope or caching builders such as `build_snapshot`. A test that mutates a
  cached artifact must get its own copy (for example a per-test copy of a
  cached snapshot directory).
- Replacing real waits in tests (for example real `asyncio.sleep` passed as a
  retry seam in `test_independent_evaluator.py`) with stubs, where the test does
  not assert on real elapsed time.
- Removing repeated setup inside one test, such as redundant
  `prepare_evaluation` calls, when the assertions stay the same.
- Choosing an xdist distribution mode (`--dist`) and reducing report flags
  (`-rsPQ`), applied identically to both targets.
- Dropping `greenlet` from `[tool.coverage.run] concurrency`, since `src/` does
  not use it, if measurement shows a gain. `branch`, `source`, `omit`, and the
  report settings stay as they are.

Forbidden:

- Any change under `backend/src/`.
- Removing, skipping, `xfail`-marking, or slow-marking a test, or removing
  assertions from a surviving test, unless the item is on the approved list.
- New or upgraded dependencies, or changes to `pytest_slow.py`.
- `-p no:randomly` or a fixed seed in either target.

Every surviving test must pass under both targets with random order and
`-n auto`. Tests must not write to shared fixed paths that parallel workers
could collide on; use `tmp_path` or `tmp_path_factory`. A failure that can be
fixed only in `src/` is out of scope; record it and raise it with the user rather
than working around it.

## Technical Acceptance Criteria

- `AC-001`, `AC-002`: `test-ranking.tsv` has one row per node ID collected by
  the measurement command, and `file-ranking.tsv` one row per test file in that
  collection; both are derived from the same `durations.log`.
- `AC-003`: Both baseline measurements are taken and recorded before any file
  under `backend/` or `.github/workflows/` changes in this cycle.
- `AC-005`: Collection with `--run-slow` after the change differs from the
  measurement collection only by the approved node IDs (removed) and replacement
  node IDs (added). No default test gained a `slow` mark without approval.
- `AC-009`: `grep TEST_ARGS backend/Makefile` finds nothing.
- `AC-010`: `make test` collects the default set (`-m "not alembic"`, no
  `--run-slow`).
- `AC-011`: `make test-all` passes `--run-slow` and no `-m` expression.
- `AC-012`: `make help` prints `test` and `test-all` lines whose descriptions
  match the selections above.
- `AC-013`: Both targets run with the environment from `tests/test.env` plus
  `PATHS_PROJECT_DIR`, independent of the caller's shell values for those
  variables.
- `AC-014`: The `backend-tests` job's test step is `make test-all`, run from
  `backend/`, with no direct pytest command.
- `AC-015`: The CI log of that step contains the `term-missing` coverage table
  for `kgfeg`.

## Build Plan

1. On the unmodified tree, record both `AC-003` baselines.
2. Run the serial measurement, save `durations.log`, and derive both rankings
   (`AC-001`, `AC-002`).
3. Draft the deletion list with replacements from the rankings. Present it to
   the user and record approval (`AC-004`). Nothing is deleted before this.
4. Rework `backend/Makefile` and `tests/test.env` (`AC-009` to `AC-012`).
5. Apply the approved deletions, replacements, and helper moves (`AC-005`,
   `AC-006`).
6. Apply speed-ups to the remaining slowest files, re-measuring as needed.
7. Run `make test` and `make test-all` until both pass, then record after-times
   (`AC-007`, `AC-008`, `AC-013`).
8. Switch `.github/workflows/tests.yml` to `make test-all` and get a passing CI
   run on the branch (`AC-014` to `AC-016`).
9. Run linters and pre-commit over all changed files (`AC-017`).

## Risks and Follow-up

- The serial `--run-slow` measurement may take hours (CI took about 86 minutes
  for the smaller default set with coverage). Run it in the background, and do
  not interrupt it, since a partial log does not satisfy `AC-001`.
- CI will now run 886 slow tests it skipped before, on a 4-vCPU runner. CI time
  may rise or fall depending on deletions and speed-ups; the scope sets no
  budget.
- First parallel CI run: tests never run under xdist in CI may expose shared
  state or path collisions. These are fixed in test code under **Speed-up
  rules**.
- Changes that touch only `backend/Makefile` or `tests/test.env` do not trigger
  the workflow's path filters. Changing that is a scope non-goal; this branch
  triggers it through its `.py` and workflow changes.
- After this change, the workflow `env` values for `CHAT_ENV`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION`, and `OPENAI_API_KEY` no longer
  affect the test step. A later cleanup could remove them from the workflow.
- Documentation that mentions `TEST_ARGS` or only `make test` is Documenter's
  work in this cycle.

## Alternatives

- **JUnit XML for timings.** Rejected: its `classname` plus `name` does not
  reliably reconstruct parametrized node IDs. The durations report prints node
  IDs directly.
- **Full rankings inside the plan Markdown.** Rejected: thousands of rows make
  the plan hard to read and review. Data files beside it keep it short.
- **Dropping coverage from `make test` for speed.** Rejected: it would change
  what developers see from the established target without a scope decision, and
  `test-all` must keep coverage for CI anyway.
- **A replacement `PYTEST_ARGS` variable.** Rejected by scope non-goal.
- **Relying on the developer's shell for test environment values.** Rejected:
  local runs could pick up real keys or a different `CHAT_ENV` and diverge from
  CI.
