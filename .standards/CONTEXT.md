# Project Context

## Project Baseline

- KGForEdGlobal is a config-driven Python pipeline that turns curriculum PDFs
  into Learning Commons-shaped knowledge graphs. Stages: Page IR extraction ->
  Page IR continuity verification -> Document IR stitching -> AS / LC / LP KG
  construction, run through four CLI entry points in
  `backend/src/kgfeg/entries/` (`extract_page_ir.py`,
  `verify_page_ir_continuity.py`, `stitch_document_ir.py`, `create_kgs.py`).
- Design principle (README, `docs/architecture.md`): curriculum-specific
  taxonomy and policy belong in runtime config ("document profiles"), not in
  source-specific backend branches. LLM judgments use producer/checker agent
  pairs, bounded inputs, and deterministic Python validators.
- Example runtime configs live in `examples/` (Ghana English and math, India
  Madhi math and Pratham science, Nigeria math, Rwanda math). The new
  `examples/funda_wande/config_english_curriculum.json` (South Africa CAPS
  English HL R-3) is untracked.
- Uncommitted baseline change (part of this cycle's request, fix 5):
  `backend/src/kgfeg/page_ir_verification/utils.py:541` now builds `expected`
  from `page_indexes[0]` rather than 0. Its docstring (`:494-496`) and Raises
  entry (`:516`) still say the sequence must start at 0.

## Stack and Tooling

- Python `>=3.13,<3.14` (`backend/pyproject.toml:130`), managed with uv
  (`backend/uv.lock`, hashed requirements under `cicd/requirements/`). Local
  venv at `backend/.venv`.
- LLM agents use pydantic-ai (`Agent`, `ModelRetry`, `BinaryContent`). Models
  come from environment settings, not run config:
  `LLM_PAGE_IR_EXTRACTION_MODEL` and `LLM_PAGE_IR_VERIFICATION_MODEL`
  (`backend/src/kgfeg/config.py:52-53`, via `Settings.llm_config(...)`), with
  per-provider settings in `backend/src/kgfeg/model_registry.py`.
- PDF access uses PyMuPDF (`fitz`). Logging uses loguru
  (`from loguru import logger`) everywhere; no stdlib `logging`, no named
  loggers.
- Prompts are `dedent(f"""...""")` f-strings returning
  `PromptPair(system_message, user_message)` (`backend/src/kgfeg/utils/general.py:94`).

## Structure and Boundaries

- `backend/src/kgfeg/schemas.py` — all run-config models. `BaseSchema`
  (`:252-255`) sets `extra="forbid"`, so every config model rejects unknown
  keys. `RunConfig` (`:3565`) has `page_ir_extraction: ExtractionConfig`
  (`:3308`), `page_ir_verification: VerificationConfig` (`:3449`),
  `document_ir: StitchingConfig` (`:3392`), and `kgs: Optional[CreateKGConfig]`
  (`:3152`, aliases `as`/`lc`/`lp`). Loaded with
  `RunConfig.model_validate(open_json_type(config_fp))` in each entry point.
- `backend/src/kgfeg/page_ir_extraction/` — per-page extraction.
  - `agents.py`: `create_page_ir_extraction_agent` (`:27`) and
    `create_page_ir_validation_agent` (`:162`, the checker, output
    `ExtractionValidationVerdict`). A new agent is built per page. Both output
    validators run `verify_page_ir_extraction_quality` (`llm.py:331-382`, 14
    ordered validators from `validators.py`) and raise `ModelRetry` on
    `QualityError`.
  - `prompts.py`: `extract_page_ir_from_pdf_page` (`:15`) and
    `validate_page_ir_extraction` (`:191`). The extraction user message appends
    optional text-layer and table-layer hint blocks only when not `None`
    (`:157-184`). The checker prompt has no optional blocks and never receives
    the text-layer hints.
  - `llm.py`: `extract_page_ir` runs the extraction agent, then
    `_run_validation_agent` (`:126-191`). On a failing verdict it returns
    `verdict.corrected_page_ir` wholesale, with no merge or further gating
    (`:313-328`).
  - `utils.py`: `extract_page_text_layer_hints` (`:248`) reads
    `page.get_text("text")` and `page.find_tables()`; text passes a quality gate
    (min 20 chars, printable ratio >= 0.90, U+FFFD ratio <= 0.02,
    `:24-26`, `:176-207`), and is otherwise raw. No quote normalization,
    running-header stripping, or OCR exists in this module.
  - Run config reaching extraction: only `languages`, `use_extracted_hints`, and
    `dpi` (`entries/extract_page_ir.py:110-119`). The text layer is read only
    when `use_extracted_hints` is true (`:115`).
- `backend/src/kgfeg/page_ir_verification/` — adjacent-page continuity.
  - `agents.py`: `create_continuity_verification_agent` (`:29`) and
    `create_continuity_validation_agent` (`:125`, the checker).
  - `prompts.py`: `verify_page_ir_pairs_from_extraction` (`:171`, verifier) and
    `validate_page_ir_continuity_verdict` (`:16`, checker). Both system prompts
    are one static f-string with no optional blocks. The verifier's TABLE
    decision procedure (Steps 1-4, `:258-286`) ends with "Content differences
    INSIDE the grid do NOT override this structural conclusion", explicitly
    including topic or skill-area shifts; its UNCERTAINTY POLICY makes Step 4
    override uncertainty. The checker repeats Steps 1-4 nearly verbatim
    (`:112-128`).
  - `llm.py`: `verify_page_ir_pairs` runs the verifier, then
    `_run_validation_agent` (`:114-199`); on failure it returns
    `validation_verdict.corrected_verdict` ungated (`:376-389`).
  - Only three config values reach the LLM layer, as keyword arguments:
    `min_confidence_to_patch`, `min_confidence_to_select_positive`,
    `min_confidence_to_stop_negative_search` (call path
    `verify_page_pairs.py:1359` -> `:290` -> `:351-370`). No config object
    reaches `llm.py` or `prompts.py`.
  - `utils.py`: `load_page_irs_from_verification` (`:488-595`), whose only
    caller is `cross_check_verification_run` (`document_ir/utils.py:341`,
    call at `:395`), invoked from `entries/stitch_document_ir.py:226`. The
    caller separately rejects negative/duplicate indexes and non-consecutive
    pairs (`document_ir/utils.py:404-439`). The loader globs every `*.json` in
    the directory (`:522`).
- `backend/src/kgfeg/document_ir/` — stitching.
  `compatible_kinds_for_stitch` (`utils.py:255`, rule at `:300-302`) only
  stitches Block-Block or Table-Table; a Table never links to a Block.
  `section_path` is built in `_update_section_stack`
  (`stitch_segments.py:2117-2190`) and truncated to the newest
  `config.max_section_path_length` headings (`:2190`; schema default 12,
  `schemas.py:3420-3423`).
- `backend/src/kgfeg/kgs/` — AS/LC/LP KG construction and prompts
  (`kgs/prompts.py`).

## Commands

Run from `backend/`.

- `make test` — `uv run pytest -n auto ... tests $(TEST_ARGS)` after sourcing
  `tests/test.env`; `TEST_ARGS=--run-slow` includes slow tests
  (`backend/Makefile:140-144`, `tests/pytest_slow.py`).
- Focused tests with CI-equivalent settings (CI env in
  `.github/workflows/tests.yml:23-28`; `tests/test.env` alone does not supply
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION` or `PATHS_PROJECT_DIR`, so
  `BackendSettings` fails to load without them):
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo root> .venv/bin/python -m pytest -n auto tests/kgfeg/<area>`.
- `make lint` — isort, black, ruff, interrogate, mypy, pylint, cloc.
- Pipeline stages: `python src/kgfeg/entries/<entry>.py <config.json>` (README
  Quick start). These call live LLMs and cost money.

## Conventions and Constraints

- Request constraint for this cycle: no code may name CAPS or any curriculum;
  curriculum details go only in runtime config. With new fields unset, every
  agent prompt must stay unchanged, and the existing example configs must behave
  as today. New unset fields appearing in run metadata is accepted.
- Established optional-instruction pattern: `kgs.lc.lc_dedup_instructions:
  Optional[str] = None` (`schemas.py:2495-2503`), injected in
  `build_lc_dedup_prompt` (`kgs/prompts.py:896-905`) as a
  "## Runtime curriculum instructions" section that says to follow it over the
  generic policy unless that would violate the output contract. Only `None`
  omits the section; an empty string passes validation and emits an empty
  section. `lc_generation_validation_instructions` (`schemas.py:2521`) is
  another `Optional`, default-`None` example.
- `kgs.as.sfi_extraction_instructions` (`schemas.py:1019`) is a required,
  stripped, non-empty `str` (validator `:1214-1238`). It reaches both the SFI
  extraction prompt (`kgs/prompts.py:396`, precedence wording at
  `:1115`, `:1152`) and the SFI checker (`:1733-1737`, `:1781-1787`). Other
  required AS instruction fields: `sfi_dedup_instructions`,
  `sfi_extraction_validation_instructions`, `sfi_has_child_instructions`,
  `sfi_has_child_validation_instructions`.
- Page ranges: `start_page` is 0-based inclusive, `end_page` 0-based exclusive
  (`None` = to the end), in both `ExtractionConfig` and `VerificationConfig`
  (`check_page_range` requires `end_page > start_page`). Verification writes
  only the configured range, so verified page indexes can start above 0.
- Commit messages follow Conventional Commits in recent history.
- Pre-commit runs detect-secrets, black, isort, ruff, interrogate, mypy, pylint
  (`.pre-commit-config.yaml`). Example configs embed absolute local paths for
  `pdf_fp` and `output_dir`.

## External Systems and Data

- LLM providers: Anthropic (default models) and OpenAI, via pydantic-ai; keys
  come from environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
- Run metadata: `extraction_run.json` (`persist_extraction_run`,
  `page_ir_extraction/utils.py:277-317`) and `verification_run.json`
  (`persist_verification_run`, `page_ir_verification/utils.py:648-684`) store
  the stage config's `model_dump` (minus `overwrite`) in `RunCtx.extra`
  (`schemas.py:3543`), so any new config field appears there automatically.
- Git-ignored local data: `data/`, `results/`, `caches/`, `logs/`, `secrets/`,
  `graveyard/`, `.env*`. `examples/` is not ignored.
- CAPS evidence (local, git-ignored):
  `data/funda_wande/caps_english_hl_grade_3_fs.pdf` has 142 pages and a usable
  PyMuPDF text layer on every page except PDF 142. A case-insensitive search
  for "REQUIREMENTS PER TERM" matches exactly 48 pages, PDF 36-133. Trial runs
  are under `results/funda_wande_trial_p13_15/`,
  `results/funda_wande_trial_p28_55/` (both nested under a config-hash
  directory), and `results/funda_wande_trial_p110_135/`
  (`extraction/`, `verification/`, `stitching/`).

## Testing and Verification Baseline

- Tests live in `backend/tests/kgfeg/<module>/` (pytest, xdist,
  `asyncio_mode=auto`, `pythonpath=[".","src"]`). Baseline on this checkout:
  `tests/kgfeg/page_ir_extraction`, `page_ir_verification`, `document_ir`, and
  `test_schemas.py` — 663 passed with the CI-equivalent env above.
- LLM calls are never made in tests. Extraction tests monkeypatch the agent
  factories with stub agents (`tests/kgfeg/page_ir_extraction/test_llm.py:65-268`)
  or patch `_run_validation_agent`; verification tests use
  `unittest.mock.patch` on `llm.create_continuity_verification_agent`,
  `_run_validation_agent`, and related functions with `MagicMock` agents.
  Prompt tests assert substrings and injected values, not full snapshots.
- Fixtures in `tests/conftest.py` include a small PDF
  (`tests/fixtures/utils/tanzania.pdf`), loguru capture/mocks, and an autouse
  logfire silencer.
- Untested today: entry modules, `persist_extraction_run`,
  `persist_verification_run`, `load_page_irs_from_verification`,
  `cross_check_verification_run`, and the agent factories' output validators.
- CI (`.github/workflows/tests.yml`) runs pytest on Python 3.13 without
  `--run-slow`; `linting.yml` runs isort/black/ruff/interrogate/mypy/pylint.

## Relevant Existing Behavior

- Extraction checker correction replaces the extracted PageIR wholesale once it
  passes the deterministic quality validators; nothing compares its text to the
  PDF text layer.
- Verification checker correction replaces the verifier verdict wholesale.
- The verifier's generic table rule treats any matching-column table at the top
  of page N+1 with no external heading and no redefined header as a
  continuation, regardless of in-grid topic or skill-area changes.
- Document IR never stitches a Table to a Block, so continuation content
  extracted as blocks breaks table continuity.
- The Funda Wande config's `kgs` block is byte-for-byte equal to Ghana English's
  (Ghana metadata, framework title, grades, `sfi_extraction_instructions`,
  `lc_dedup_instructions`). Its only CAPS-specific values are outside `kgs`:
  `page_ir_extraction.country` "South Africa", `year` 2011, `pdf_fp`,
  `output_dir`, `start_page`/`end_page` 27/55 in both page stages, and
  `document_ir.max_section_path_length` 60. It sets
  `use_extracted_hints: true`.

## Known Unknowns

- The docstrings of both page-IR modules say the checker uses higher reasoning
  effort, but Anthropic settings are identical for both agent types
  (`model_registry.py:85-89`, `:113-116`). Not material to this request unless
  downstream design relies on the distinction.

## Evidence

- `backend/src/kgfeg/page_ir_extraction/{agents,prompts,llm,utils,validators}.py`
  and `entries/extract_page_ir.py` — extraction flow and hints.
- `backend/src/kgfeg/page_ir_verification/{agents,prompts,llm,utils,verify_page_pairs}.py`
  and `entries/verify_page_ir_continuity.py` — verification flow.
- `backend/src/kgfeg/schemas.py`, `backend/src/kgfeg/kgs/prompts.py` — config
  models and instruction-field patterns.
- `backend/src/kgfeg/document_ir/{utils,stitch_segments}.py` — stitching and
  section path.
- `git diff backend/src/kgfeg/page_ir_verification/utils.py` — uncommitted
  fix-5 change.
- Python comparison of the Funda Wande and Ghana English `kgs` blocks — equal.
- PyMuPDF scan of the CAPS PDF — page count, text layer, banner pages.
- Focused pytest run — 663 passed.
