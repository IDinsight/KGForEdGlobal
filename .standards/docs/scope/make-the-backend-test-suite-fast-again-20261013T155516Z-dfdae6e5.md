<!-- STANDARDS
Artifact: SCOPE
Cycle: make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5
-->

# Make the Backend Test Suite Fast Again

## Goal

The backend suite grew from 828 tests in about 7 seconds to several thousand
tests that take over an hour in CI, most of it in LP generation and LP
evaluation tests. This cycle measures where the time goes on the user's machine,
removes the tests the user agrees are far too slow while keeping the behavior
they covered under cheaper tests, speeds up the rest of the run, and gives the
backend two clear test entry points: `make test` for the fast default set and
`make test-all` for everything. CI then runs `make test-all` in parallel.

## Constraints

- Timings are measured locally on the user's machine, not taken from CI logs.
- No test is deleted until the user has approved a specific deletion list built
  from the ranking.
- The `--run-slow` opt-in in `backend/tests/pytest_slow.py` stays the way the
  suite separates the fast default set from the full set.
- New or changed test code must satisfy the repository's existing linters and
  pre-commit checks.

## Non-goals

- Changing production code under `backend/src/`.
- A replacement for `TEST_ARGS`. Focused runs use pytest directly.
- Changing CI triggers, path filters, or the `changes` job.
- A fixed wall-clock budget for either target. Improvement is judged against
  the measured baseline.
- New test coverage beyond the cheaper replacements for deleted tests.

## Work

### 1. Measure and rank test timings

**Intent:** Replace the CI log-timestamp estimates with local measurements so
deletion and speed-up decisions rest on real numbers.

**Done when:**

- `AC-001`: The cycle records hold a per-test ranking, slowest to fastest, of
  every test in the full suite (default and `--run-slow` cases), measured on the
  user's machine in one serial run with fixed order (`-p no:randomly`) and no
  coverage. The record states the command, conditions, and date.
- `AC-002`: The cycle records hold a per-file ranking, slowest to fastest by
  total time, from the same measurement run as `AC-001`.
- `AC-003`: The cycle records hold baseline local wall-clock times, taken before
  any change, for the current `make test` and for the current full suite
  (`make test TEST_ARGS=--run-slow`).

**Depends on:** None.

### 2. Remove the tests that take far too long

**Intent:** Cut the slowest tests the user agrees are not worth their cost
without losing the behavior they protect.

**Done when:**

- `AC-004`: Before any test is deleted, the cycle records hold a deletion list
  of tests and test files drawn from the ranking, and the user's explicit
  approval of that list.
- `AC-005`: Every item on the approved list is gone from the suite, and no
  other test or test file has been removed. Reducing a parametrized matrix
  counts as a deletion and must be on the list.
- `AC-006`: For every approved deletion, the behavior it covered is still
  exercised by at least one faster test, such as a representative case kept
  from a reduced matrix. The cycle records map each deleted item to its
  replacement.

**Depends on:** Work item 1.

### 3. Speed up the remaining suite

**Intent:** Make the tests that remain run faster, beyond the gain from
deletion.

**Done when:**

- `AC-007`: The local wall-clock time of `make test` after the changes is lower
  than its `AC-003` baseline, measured on the same machine. Before and after
  times are recorded.
- `AC-008`: The local wall-clock time of `make test-all` after the changes is
  lower than the `AC-003` full-suite baseline, measured on the same machine.
  Before and after times are recorded.

**Depends on:** Work items 1, 2, and 4.

### 4. Rework the backend Makefile test targets

**Intent:** Give developers one fast default command and one complete command,
with no free-form argument variable.

**Done when:**

- `AC-009`: `backend/Makefile` no longer defines or reads `TEST_ARGS`.
- `AC-010`: `make test` runs every test except slow-marked and alembic-marked
  tests.
- `AC-011`: `make test-all` runs every test, including slow-marked and
  alembic-marked tests.
- `AC-012`: `make help` lists both `test` and `test-all` with descriptions that
  match what each runs.
- `AC-013`: `make test` and `make test-all` both finish with every selected
  test passing on the user's machine.

**Depends on:** Work item 2 for `AC-013`.

### 5. Run the full suite in CI through the Makefile

**Intent:** Have pull-request CI run the same complete command developers use,
in parallel.

**Done when:**

- `AC-014`: The backend test job in `.github/workflows/tests.yml` runs
  `make test-all` from `backend/` in place of its own pytest command, with tests
  running in parallel.
- `AC-015`: The CI test job output still includes the coverage report for
  `kgfeg` that it prints today.
- `AC-016`: A CI run of the updated workflow on this branch passes the backend
  test job.

**Depends on:** Work item 4.

### 6. Keep test code within project standards

**Intent:** Changed and replacement tests must meet the same bar as the rest of
the tree.

**Done when:**

- `AC-017`: All new or changed files under `backend/tests/`, plus
  `backend/Makefile` and `.github/workflows/tests.yml`, pass the repository's
  linters and pre-commit checks.

**Depends on:** Work items 2 through 5.

## Assumptions

- "When the PR is submitted" refers to the workflow's existing `pull_request`
  trigger. Pushes to `main` keep the same job and therefore also run
  `make test-all`.
- No test carries the `alembic` mark today, so `AC-010` and `AC-011` differ only
  in slow-marked tests unless alembic tests are added later.
- The full-suite baseline in `AC-003` uses today's `TEST_ARGS=--run-slow`
  route because `make test-all` does not exist yet.
