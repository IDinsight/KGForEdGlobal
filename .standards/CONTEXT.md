# Project Context

## Project Baseline

- KGForEdGlobal is a config-driven Python pipeline that turns curriculum PDFs
  into Learning Commons-shaped knowledge graphs: Page IR extraction -> Page IR
  continuity verification -> Document IR stitching -> KG construction (Academic
  Standards, Learning Components, Learning Progressions (LP)). CLI entry points
  live in `backend/src/kgfeg/entries/`; `evaluate_lcs.py` and `evaluate_lps.py`
  are LLM-judge evaluation CLIs outside the pipeline.
- This cycle concerns only the backend test suite and how it is run: the tests
  under `backend/tests/`, the test targets in `backend/Makefile`, the pytest and
  coverage settings in `backend/pyproject.toml`, and
  `.github/workflows/tests.yml`. Production code under `backend/src/` is out of
  the audit target.
- Branch `tz6/fw` is 80 commits ahead of `main` and 0 behind. Its merge base is
  `be5c6a5` (the LP merge, the last commit on `main`), so CI run 36025148809 on
  `main` measured the merge-base suite, not this branch. Since the merge base,
  the branch changed 14 test files (+2,469/-127 lines), including the new
  `tests/kgfeg/kgs/test_lp_checkpoint_cadence.py`. It did not change
  `backend/Makefile`, `backend/pyproject.toml`, or `.github/`.

## Stack and Tooling

- Python `>=3.13,<3.14` (local venv `backend/.venv` is 3.13.9), managed with
  uv. Hashed requirements live in `cicd/requirements/`. `uv.lock` is in
  `backend/`.
- Pinned test dependencies (`backend/pyproject.toml` dev extra, matching the
  local venv): `pytest==8.2.0`, `pytest-xdist==3.8.0`, `pytest-randomly==4.0.1`,
  `pytest-cov==7.0.0` (coverage 7.13.0), `pytest-asyncio==1.2.0`,
  `pytest-mock==3.15.1`, `pytest-order==1.3.0`, `pytest-bdd==8.1.0`,
  `pytest-alembic>=0.12.1`.
- pytest-randomly is auto-loaded, so test order is shuffled on every run unless
  `-p no:randomly` is passed. It also reseeds random generators per test.
- The local machine reports 12 CPUs (`os.cpu_count()`), so `-n auto` starts 12
  xdist workers locally.

## Structure and Boundaries

- `backend/tests/conftest.py` — root conftest. It adds `backend/src` and
  `backend/tests` to `sys.path`, loads the slow-test plugin through
  `pytest_plugins = ["tests.pytest_slow"]`, and defines shared fixtures. One
  autouse fixture disables Logfire/OpenTelemetry instrumentation for every test.
- `backend/tests/pytest_slow.py` — adds `--run-slow` and registers the `slow`
  marker. Without `--run-slow` it deselects every item with a `slow` mark,
  including inherited class and parameter marks.
- `backend/tests/test_slow_selection.py` — runs pytester subprocesses to check
  `pytest_slow.py`: default exclusion, opt-in, `-m` interaction, explicit node
  IDs, and exit codes.
- `backend/tests/fixtures/` — `lp/` (six curriculum regression JSON fixtures,
  plus `loader.py` and `delivery.py`), `lp_eval/snapshot_fixtures.py`
  (`build_snapshot`), and `utils/` (PDF, image, and job files).
- `backend/tests/kgfeg/` — per-package test areas: `kgs/` (34 test files,
  ~32k lines, almost all `test_lp_*`), `evals/lp_eval/` (9 files, ~8.8k lines),
  `page_ir_verification/` (8), `page_ir_extraction/` (5), `document_ir/` (4),
  `utils/` (4), and four top-level files (`test_schemas.py`, `test_regexes.py`,
  and two `test_learning_progressions_config_*.py`).
- Test modules import each other as fixture libraries, so deleting a test file
  can break other files' imports. Most-imported modules (importer counts):
  - `tests/kgfeg/kgs/test_lp_generation.py` — 20 kgs test modules.
  - `test_lp_orchestration.py` — 15.
  - `test_lp_finalization.py` — 10 kgs modules plus
    `fixtures/lp_eval/snapshot_fixtures.py`.
  - `test_lp_concurrency.py` — 5.
  - `tests/kgfeg/evals/lp_eval/test_independent_evaluator.py` — 5 lp_eval
    modules (`test_automatic_recovery`, `test_critique_citation_remediation`,
    `test_grounding_remediation`, `test_snapshot_reader_matrix`,
    `test_structured_output_remediation`).
  - `test_lp_checker.py`, `test_lp_export.py`, `test_lp_reuse.py`, and
    `test_create_kgs_lp.py` — 4 each.
  - `test_lp_artifacts`, `test_lp_candidates`, `test_lp_dispatch_concurrency`,
    and `test_lp_selection` — 3 each.
  - Also imported: `test_lp_checkpoint_formats` (by `test_lp_checkpoint_cadence`
    and `evals/lp_eval/test_current_input_contract`),
    `test_lp_checkpoint_cadence`, `test_lp_evidence` (by `snapshot_fixtures`),
    `test_lp_population_verifier`, `test_lp_projections`,
    `test_lp_relationships`, `test_lp_validation`, and lp_eval's
    `test_current_input_contract`, `test_grounding_remediation`, and
    `test_projection_compatibility`.
- `build_snapshot` (`tests/fixtures/lp_eval/snapshot_fixtures.py:81`) builds a
  complete synthetic LP run. It drives `test_lp_finalization._Harness` and
  imports from `test_lp_evidence` and `test_lp_selection`. Six lp_eval test
  files use it.

## Commands

Run from `backend/`.

- `make test` — the only test target. It runs the undocumented `run-tests`
  recipe:
  `set -a && source ./tests/test.env && set +a && uv run pytest -n auto -rsPQ -m "not alembic" --cov-report term-missing --cov-config=./pyproject.toml --cov=. tests $(TEST_ARGS)`.
  `TEST_ARGS ?=` is defined at `backend/Makefile:27`. The help text reads "Run
  focused tests; TEST_ARGS=--run-slow includes all integration matrices".
  `run-tests` is not listed in `.PHONY`.
- CI (`.github/workflows/tests.yml`, job `backend-tests`) runs from `backend/`
  after `uv sync --extra dev`:
  `uv run pytest -rsPQ -m "not alembic" --cov-report term-missing --cov-config=./pyproject.toml --cov=. tests`.
  It runs serially (no `-n`), without `--run-slow`, and treats exit code 5 (no
  tests collected) as success.
- Focused local runs with CI-equivalent environment variables:
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo root> .venv/bin/python -m pytest <args> <paths>`.
  The fake key placeholder is the literal test value CI uses.
- Collection only, verified during this audit (about 1 s each, with
  `-p no:cacheprovider -p no:randomly -m "not alembic"`):
  `... pytest --collect-only -q tests` and the same with `--run-slow`.

## Conventions and Constraints

- Test files follow `tests/kgfeg/<package area>/test_*.py`. Shared helpers live
  in `tests/constants.py`, `tests/types_.py`, and `tests/fixtures/`.
- Markers: `pyproject.toml` registers only `costs-money`. `pytest_slow.py`
  registers `slow` at runtime. `alembic` comes from the pytest-alembic plugin.
  No test in the tree uses `costs-money` or `alembic`; `-m "not alembic"`
  currently deselects nothing.
- Commit messages follow Conventional Commits. Pre-commit runs detect-secrets,
  black, isort, ruff, interrogate (docstring coverage over `src/` and
  `tests/`), mypy, pylint, and prettier; `make lint` runs the same Python
  linters over `src/` and `tests/`. New or changed test code must satisfy these
  linters, including docstrings.
- The fake API key in `tests.yml` has a `# pragma: allowlist secret` comment
  for detect-secrets.

## External Systems and Data

- Tests are offline. LP and lp_eval tests use stub models and fake judge
  transports. Several modules (for example `test_automatic_recovery.py:122-134`)
  use an autouse fixture that patches `socket.socket.connect`,
  `socket.socket.connect_ex`, and `socket.create_connection` to reject network
  access.
- `Settings` reads `.env` relative to the working directory
  (`src/kgfeg/config.py:67`). `backend/` has `.env.local` and `.envrc` but no
  `.env`; the repo root has `.env`. Values were not inspected.
  `tests/test.env` defines only `LOGGING_LOG_LEVEL`. CI sets `LOGGING_LEVEL`,
  which is not the setting name `config.py:56` reads.

## Testing and Verification Baseline

- Local collection on this branch, verified during this audit:
  - Default (`-m "not alembic"`, no `--run-slow`): 3,855 tests collected, 886
    deselected as slow.
  - With `--run-slow`: 4,741 tests.
  - The Navigator's CI figures (3,819 tests and 928 slow-deselected tests on
    `main`) describe the merge base, not this branch.
- Default-run test counts by area: `kgs/` 1,893; `evals/lp_eval/` 816;
  `page_ir_verification/` 351; `test_learning_progressions_config_schema.py`
  198; `page_ir_extraction/` 169; `document_ir/` 145; `test_regexes.py` 95;
  `test_learning_progressions_config_wiring.py` 86; `utils/` 70;
  `test_schemas.py` 26; `test_slow_selection.py` 6.
- All slow-marked tests are in `tests/kgfeg/kgs/` (`--run-slow` adds 886 there;
  `test_lp_generation.py:67` builds conditional slow marks for the
  `ghana_english` and `rwanda_math` profiles but adds no slow-only items to its
  own collection). Files
  with slow cases (default -> with `--run-slow`):
  - `test_lp_orchestration` 46 -> 285
  - `test_lp_finalization` 6 -> 172
  - `test_lp_reuse` 17 -> 150
  - `test_lp_export` 23 -> 113
  - `test_lp_artifacts` 17 -> 102
  - `test_lp_release_policy` 45 -> 93
  - `test_lp_concurrency_recovery` 27 -> 57
  - `test_lp_validation` 33 -> 55
  - `test_kg_manifest_resume` 31 -> 51
  - `test_lp_concurrency` 37 -> 49
  - `test_lp_projections` 3 -> 34
  - `test_lp_concurrency_boundaries` 4 -> 12
  - `test_lp_relationships` 44 -> 46
- `tests/kgfeg/evals/lp_eval/` has no slow marks, so all 816 of its tests run
  by default and in CI.
- The lp_eval files have few test functions but heavy parametrization (for
  example `test_automatic_recovery.py` has 10 test functions).
  `test_automatic_recovery.py` builds snapshots with
  `build_snapshot(count=3, ...)` and calls `prepare_evaluation` repeatedly. Its
  retry sleeps are injected `_sleep` stubs. Some `test_independent_evaluator.py`
  cases pass the real `asyncio.sleep` as the retry seam (`:575`, `:967`, `:983`,
  `:1049`).
- Coverage configuration (`pyproject.toml` `[tool.coverage.run]`):
  `branch = true`, `concurrency = ["greenlet", "thread"]`,
  `source = ["kgfeg"]`, and `omit` for tests and `__init__.py`. Both `make test`
  and CI always pass `--cov=.`; neither has a coverage-free path.
- `[tool.pytest.ini_options]`: `asyncio_mode = "auto"`,
  `bdd_features_base_dir = "tests/features/"` (the directory does not exist),
  `markers = ["costs-money"]`, `pythonpath = [".", "src"]`, and
  `testpaths = ["tests"]`. No `addopts`, timeout, or durations setting exists.
- Branch-local test changes (14 files; see **Project Baseline**) have not been
  timed in CI.

## Relevant Existing Behavior

- `make test` runs the suite in parallel with coverage and sources
  `tests/test.env`. Passing `TEST_ARGS=--run-slow` adds the 886 slow tests;
  other values are appended verbatim as pytest arguments.
- `--run-slow` alone runs every collected test, because no test carries the
  `alembic` mark.
- CI runs the default (non-slow) set serially with coverage on pull requests
  that touch `**.py`, `backend/pyproject.toml`, `backend/uv.lock`,
  `backend/tests/fixtures/**`, or the workflow, and on pushes to `main`. A
  `changes` job gates the test job on `**.py` changes only.
- `test_slow_selection.py` asserts the `--run-slow` contract of
  `pytest_slow.py`. Changing or removing that switch affects these tests.

## Known Unknowns

- Local per-test and per-file durations are not established. No local timing
  was run during audit; CI timings came from log timestamps on the merge base.
  Measuring them is part of the requested work.
- The local wall-clock time of `make test`, and the effect of coverage
  (branch + greenlet/thread concurrency), xdist, and random ordering on these
  tests, are unmeasured.
- Whether local `make test` gets `CHAT_ENV`,
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION`, `OPENAI_API_KEY`, and
  `PATHS_PROJECT_DIR` from `backend/.envrc` (direnv) or the shell is not
  established. `tests/test.env` does not set them, and the previous cycle
  recorded that focused runs needed them supplied explicitly.
- Whether every test passes under xdist with random ordering is not
  established. CI runs serially.

## Evidence

- `backend/Makefile:27`, `:138-144` — `TEST_ARGS`, `test`, and `run-tests`.
- `backend/pyproject.toml:82-108`, `:152-175` — pytest, coverage, and test
  dependency pins.
- `.github/workflows/tests.yml` — CI triggers, environment, and pytest command.
- `backend/tests/conftest.py`, `backend/tests/pytest_slow.py`,
  `backend/tests/test_slow_selection.py` — plugin loading and the slow switch.
- `pytest --collect-only -q -p no:cacheprovider -p no:randomly -m "not alembic" [--run-slow] tests`
  with the CI environment variables — collected counts and per-file splits.
- `grep` for `from tests.` imports across `backend/tests` — the cross-module
  dependency map.
- `git merge-base main HEAD`, `git diff --stat main...HEAD -- backend/tests ...`
  — branch drift from the CI-measured commit.
- Previous project context, from git history before `22256f5` — project
  baseline and earlier test-environment notes.
