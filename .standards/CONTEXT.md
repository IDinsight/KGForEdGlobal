# Project Context

## Project Baseline

- KGForEdGlobal is a config-driven Python pipeline that turns curriculum PDFs
  into Learning Commons-shaped knowledge graphs. Stages: Page IR extraction ->
  Page IR continuity verification -> Document IR stitching -> KG construction
  (Academic Standards (AS), Learning Components (LC), Learning Progressions
  (LP)). Four CLI entry points in `backend/src/kgfeg/entries/`
  (`extract_page_ir.py`, `verify_page_ir_continuity.py`,
  `stitch_document_ir.py`, `create_kgs.py`); `evaluate_lcs.py` and
  `evaluate_lps.py` are LLM-judge evaluation CLIs outside the pipeline.
- Design principle (README, `docs/architecture.md`): curriculum-specific
  taxonomy and policy belong in runtime config ("document profiles"), not in
  source-specific backend branches. LLM judgments use producer/checker agent
  pairs, bounded inputs, and deterministic Python validators.
- Example runtime configs live in `examples/` (Ghana English and math, India
  Madhi math and Pratham science, Nigeria math, Rwanda math, and the committed
  South Africa CAPS English HL R-3 profile
  `examples/funda_wande/config_english_curriculum.json`).
- The signed-off previous cycle
  (`add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8`) added
  optional `extraction_instructions` / `validation_instructions` (extraction)
  and `verification_instructions` / `validation_instructions` (verification),
  a text-layer correction guard for extraction-checker corrections, and
  gap-free page ranges starting above 0. It changed no `kgs/` code; it authored
  the CAPS `kgs` block but never ran `create_kgs`.
- `backend/src/kgfeg/kgs/` was last changed on `main` by `be5c6a5` (LP
  generation, 2026-09-24); the branch `tz6/fw` has no `kgs/` changes.

## Stack and Tooling

- Python `>=3.13,<3.14` (`backend/pyproject.toml`), managed with uv
  (`backend/uv.lock`, hashed requirements under `cicd/requirements/`). Local
  venv at `backend/.venv`.
- LLM agents use pydantic-ai. Every KG agent (AS, LC, LP producers and
  checkers) builds its model from `Settings.llm_config("kgs")`, i.e. env var
  `LLM_KG_MODEL` (`backend/src/kgfeg/config.py:46`, `:129-131`); there is no
  per-stage KG model setting. Page stages use `LLM_PAGE_IR_EXTRACTION_MODEL` /
  `LLM_PAGE_IR_VERIFICATION_MODEL`. Settings load from `.env`
  (`config.py` `SettingsConfigDict(env_file=".env")`); the repo-root `.env`
  defines `LLM_KG_MODEL`, `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION`, provider
  keys and `PATHS_PROJECT_DIR` (values not recorded).
- PDF access uses PyMuPDF. Logging uses loguru everywhere. Prompts are
  `dedent(f"""...""")` f-strings returning `PromptPair`.

## Structure and Boundaries

- `backend/src/kgfeg/schemas.py` — all run-config models. `BaseSchema` (`:252`)
  sets `extra="forbid"`. `RunConfig` (`:3663`) holds `page_ir_extraction`
  (`ExtractionConfig`, `:3308`), `page_ir_verification` (`VerificationConfig`,
  `:3497`), `document_ir` (`StitchingConfig`, `:3440`) and
  `kgs: Optional[CreateKGConfig]` (`:3152`; namespaces `as`/`lc`/`lp` map to
  `_CreateKGAcademicStandardsConfig` `:892`, `_CreateKGLearningComponentsConfig`
  `:2482`, `_CreateKGLearningProgressionsConfig` `:2956`; `metadata` is
  `_CreateKGMetadata` `:2990`). `CreateKGConfig` cross-validates LP against AS:
  LP statement types must be canonical AS types, the developmental coordinate
  type must be in `grade_level_statement_types`, its `ordered_values` must equal
  that type's canonical controlled values, and every buildsTowards type must
  carry the coordinate in `identity_scope_statement_types`.
  `grade_level_mapping` keys must be canonical grade controlled values.
- `backend/src/kgfeg/entries/create_kgs.py` — `create` resolves upstream
  inputs, then `build_kgs` runs 24 ordered steps (AS 1-10, LC 11-19, LP 20-24).
  KG artifacts live in `<page_ir_extraction.output_dir>/<doc_key>/kgs/`.
- `backend/src/kgfeg/kgs/` — KG construction (about 54k lines, 33 modules):
  - Shared: `utils.py` (input validation, table selection, manifest, `KGDirs`),
    `llm.py` (agent runners, `KGUsageTracker`), `agents.py`, `prompts.py`,
    `validators.py`, `schemas.py`.
  - AS: `sfi_extraction_windows.py` (plan + build windows),
    `sfi_extraction.py`, `sfi_registry.py`, `sfi_dedup.py`,
    `sfi_finalization.py`, `sfi_relationships.py` (hasChild),
    `sfi_source_anchors.py`, `sfi_export.py`.
  - LC: `lc_selection.py`, `lc_generation.py`, `lc_dedup.py`,
    `lc_finalization.py`, `lc_export.py`.
  - LP: `lp_generation.py`, `lp_candidates.py`, `lp_evidence.py`,
    `lp_coordinates.py`, `lp_admissibility.py`, `lp_selection.py`,
    `lp_requests.py`, `lp_dispatch.py`, `lp_checkpoints.py`, `lp_index.py`,
    `lp_finalization.py`, `lp_validation.py`, `lp_artifacts.py`,
    `lp_export.py`.
- Page-IR and Document IR modules (`page_ir_extraction/`,
  `page_ir_verification/`, `document_ir/`) are upstream of this cycle; the
  request says their CAPS outputs need not be rerun.

## Commands

Run from `backend/`.

- `make test` — pytest with xdist after sourcing `tests/test.env`;
  `TEST_ARGS=--run-slow` adds slow tests (`backend/Makefile`).
- Focused tests with CI-equivalent env (`tests/test.env` alone lacks
  `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION` and `PATHS_PROJECT_DIR`):
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo root> .venv/bin/python -m pytest -n auto <paths>`.
- `make lint` — isort, black, ruff, interrogate, mypy, pylint, cloc.
- `python src/kgfeg/entries/create_kgs.py <config.json>` — runs AS, LC and LP
  end to end with live LLM calls (cost).
- AS steps 3-4 (`plan_extraction_windows`, `build_llm_extraction_windows`) are
  deterministic and were run without LLM calls on the CAPS DocumentIR with
  `save_fp` outside the repo.

## Conventions and Constraints

- Request constraint carried from the previous cycle's work: curriculum
  details belong in runtime config; no backend code names CAPS or any
  curriculum.
- Established optional-instruction pattern: `Optional[str] = None` fields
  injected as a "Runtime curriculum instructions" section only when not `None`
  (for example `kgs.lc.lc_dedup_instructions`, `schemas.py:2495`, injected at
  `kgs/prompts.py:896-905`). `kgs.as.sfi_extraction_instructions`
  (`schemas.py:1019`) is required and non-empty.
- `RunConfig` validation creates `page_ir_extraction.output_dir` if missing
  (`ExtractionConfig.ensure_output_dir_exists`, `schemas.py:3390-3405`), so
  merely loading a config has a filesystem side effect.
- Page ranges: `start_page` 0-based inclusive, `end_page` 0-based exclusive.
- Commit messages follow Conventional Commits. Pre-commit runs detect-secrets,
  black, isort, ruff, interrogate, mypy, pylint. Example configs embed absolute
  local paths for `pdf_fp` and `output_dir`.

## External Systems and Data

- LLM providers: Anthropic and OpenAI via pydantic-ai; keys from environment
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
- `LEARNING_COMMONS_EXPORT_SCHEMA_VERSION` must be non-empty or AS export
  raises (`kgs/sfi_export.py:596-619`); it is recorded in validation reports
  and fingerprints only.
- Git-ignored local data: `data/`, `results/`, `caches/`, `logs/`, `secrets/`,
  `graveyard/`, `.env*`. Current `results/` holds `kg_for_ed/` and
  `lp_evals/`; the earlier CAPS trial directories are gone.
- CAPS PDF: `data/funda_wande/caps_english_hl_grade_3_fs.pdf`, doc key
  `c939a3a9dcce92ed61d970f4599ced6f2ddc425d1220eeeab9828472b878d24d`.
- User-verified upstream outputs for this cycle:
  `results/kg_for_ed/<doc_key>/` with `extraction/` (`extraction_run.json`:
  `output_dir` `results/kg_for_ed`, `start_page` 35, `end_page` 135, 100 page
  IRs = page_index 35-134 = PDF 36-135), `verification/` (100 verified page
  IRs) and `stitching/document_ir.json`. No `kgs/` directory exists yet.
- CAPS DocumentIR shape (59 segments): 7 heading blocks ("3.1 GRADE R",
  "3.2 GRADE 1", "3.3 GRADE 2", "3.4 GRADE 3", and three "RECOMMENDED
  TEXTS/RESOURCES FOR THE YEAR" headings on page_index 83, 108, 134) and 52
  tables. 48 are banner tables, 12 per grade (4 terms x 3 skill areas), each
  with `header_row_count` 3 or 4: the GRADE/REQUIREMENTS PER TERM lines (one
  or two rows), the TERM label, and the skill-area label (Grades 1-3 put
  contact time in column 2 of the skill-area row; the first Grade R table
  has a separate contact-time header row). One table (page_index 103-105) also counts the
  `CONTENT/CONCEPTS/SKILLS` row as a header. Grade R tables have `n_cols` 1,
  Grades 1-3 `n_cols` 2. The 4 remaining tables are the per-grade resources
  boxes (page_index 58, 83, 108, 134). Body rows are single full-width cells
  of 700-2300 characters holding many bullets; 144 body rows in the banner
  tables hold about 1,083 bullet lines outside ASSESSMENT rows and about 545
  inside them (rough regex count).
- `section_path` in this DocumentIR accumulates and never pops: later tables
  carry every earlier grade heading and resources heading (up to 7 entries).

## Testing and Verification Baseline

- Tests live in `backend/tests/kgfeg/<area>/` (pytest, xdist,
  `asyncio_mode=auto`). KG-related baseline on this checkout:
  `tests/kgfeg/kgs`, both `test_learning_progressions_config_*.py`,
  `tests/kgfeg/evals`, `test_schemas.py` — 3011 passed with the CI-equivalent
  env.
- `tests/kgfeg/kgs/` covers LP (about 31 files, stub models, sockets patched
  to reject network) plus `test_create_kgs_lp.py` (mocks AS/LC steps to check
  phase order), `test_kg_manifest_resume.py`, `test_llm.py`. No unit tests
  exist for SFI windows, extraction, registry, dedup, finalization, hasChild,
  source anchors, or any LC module; real AS/AS+LC export runs only on
  synthetic data in `tests/fixtures/lp_eval/snapshot_fixtures.py`. Test
  profiles are Ghana, Nigeria, Rwanda, MADHI and Pratham; none is CAPS.
- CI (`.github/workflows/tests.yml`) runs pytest on Python 3.13 without
  `--run-slow`; `linting.yml` runs the linters.

## Relevant Existing Behavior

### Inputs, resume and overwrite

- `create` requires `<page_ir_extraction.output_dir>/<doc_key>/extraction/extraction_run.json`
  with a matching `doc_key`, then reads `<...>/stitching/document_ir.json`
  (`kgs/utils.py:1288-1367`). The committed CAPS config sets
  `output_dir` to `results/funda_wande_grade_r` and page stages to
  `start_page` 35 / `end_page` 59, so it does not point at the
  `results/kg_for_ed` outputs.
- `load_and_validate_inputs` (`kgs/utils.py:1503-1582`) checks doc key, pages,
  segments, unique segment IDs; it raises when table inclusion rules are set
  but select no table, and only warns on language mismatch.
- One `kgs.overwrite` flag governs all of AS, LC and LP. With
  `overwrite=false`, `kg_run_manifest.json` must equal the freshly built prep
  manifest (which includes the table-selection policy, counts and warnings)
  or the run raises (`kgs/utils.py:1679-1752`). Sub-stages resume from
  aligned prefixes; resume keys for SFI extraction windows and LC requests do
  not include instruction text, so changed instructions with
  `overwrite=false` reuse earlier LLM results (also stated in
  `docs/guides/running-and-debugging.md`). LP resume fails closed on any
  changed config, prompt, model or population, and
  `validate_lp_checkpoint_format` runs before overwrite archiving.
- `kg_run.json` records the `kgs` config (minus `overwrite`), status, error
  and usage.

### AS (steps 3-10)

- Table selection matches `included_table_section_patterns` against text
  built from `section_path` headings within `table_section_pattern_page_lookback`
  pages of the table start (else the last `section_path` entry), plus the
  table's `local_code` and `columns_signature` (`kgs/utils.py:211-265`,
  `:1452-1500`); header and body cells are not consulted. Field docs mention
  only nearby heading text. Exclusions win; included column signatures are
  exact matches.
- Every non-empty block segment becomes its own extraction window, unfiltered
  (`kgs/sfi_extraction_windows.py:1636-1641`). Tables are split by
  `max_rows_per_table_window` / `row_overlap`, repeating `header_rows` in each
  window. The prompt payload sends raw `rows` cells (internal newlines kept),
  the full un-truncated `section_path` (recent first), up to 2 same-page
  headings on each side, and `scope_context_candidates` drawn only from
  neighbor headings and `section_path`.
- Dry run on the CAPS DocumentIR with the committed config: 55 windows = 48
  banner tables (selected via `columns_signature`, which contains "requirements
  per term"; the pattern never appears in `section_path`) + 7 heading blocks.
  The 4 resources tables are excluded. No table splits (2-8 body rows each).
  `scope_context_candidates` carry only Grade values, and late tables list all
  earlier grades. Prompts are about 30k system + 11-13k user characters per
  table window.
- SFI extraction runs windows sequentially: producer then checker (a failing
  verdict's correction replaces the draft), then deterministic integrity
  checks. Checks include: canonical `statement_type` (an alias is an error,
  not normalized); `identity_scope_values` keys exactly equal to the
  configured dimensions (so Grade candidates return `{}`) with controlled
  values or aliases; exact anchors; `source_text` inside the cited rows. Each
  agent has 3 output retries; exhausting them aborts the run.
- Registry: identity scope comes only from the LLM-returned
  `identity_scope_values`, canonicalized via controlled-value aliases with
  `normalize_controlled_value_key` (NFKC, casefold, punctuation collapsed;
  `schemas.py:140`); unknown or missing values raise
  (`kgs/sfi_registry.py:891-984`). A candidate's own label canonicalizes only
  by exact normalized match, else `canonical_statement_value` is `None`.
- Dedup sends every multi-candidate component (edges from same controlled
  value, same normalized text within identity scope, shared anchors, registry
  buckets, same row) to producer/checker; `max_dedup_review_set_candidates:
  null` leaves components unsplit. Only `merged`/`singleton` groups are minted;
  `conflict`/`needs_review` groups are dropped and counted.
- Final SFI IDs for uncoded items are UUIDv5 over `synthetic_merge_key_fields`
  (normalized text plus identity scope, etc.); an identity collision raises
  (`kgs/sfi_finalization.py:788-874`, `:1957-1995`). Finalization has no
  resume and always rewrites its files.
- hasChild issues one producer/checker request per final SFI, including
  Grades. Candidate parents are bounded to 24 including the root; more than
  23 "indispensable" candidates raises (`kgs/sfi_relationships.py:579-694`).
  Section-path evidence is a substring test against up to 12 labels.
  Unresolved children always get a root-fallback edge
  (`unresolved_root_fallback=True`) with no config gate, although
  `docs/pipeline/academic-standards.md` says "when permitted by the hierarchy
  policy".
- AS export (`kgs/sfi_export.py`) raises on any validation error. Local grades
  come from the record's canonical value or its Grade scope value (no
  inheritance through hasChild), and an observed grade missing from
  `grade_level_mapping` is an error. `metadata.grades_or_stages` is never
  checked against the mapping.

### LC (steps 11-19)

- LC requires a passed, error-free AS validation report. With
  `lc_source_statement_types: null`, selection uses the leaf default: leaf
  SFIs whose `normalized_statement_type == "Standard"` (CAPS Skills); seeds
  under a root-fallback path are excluded (`kgs/lc_selection.py:64-76`,
  `:242-268`). Zero eligible seeds raises.
- Generation is sequential, one producer/checker per request (batch size 1).
  Per-request failures are recorded. The run raises only when failed/total
  exceeds `lc_max_failure_rate`, after artifacts are written
  (`kgs/lc_generation.py:749-760`). Rerunning without overwrite retries only
  failed requests.
- Dedup with `lc_dedup_scope: framework` compares all skill texts in one scope.
  Identical normalized text across grades/terms becomes one LC with one
  supports edge per claiming SFI. Semantic pairs are adjudicated by a single
  judge (no checker) in batches of 25. The dedup judge prompt carries
  math-flavored examples (`kgs/prompts.py:912-913`).

### LP (steps 20-24)

- The coordinate is read from each Skill's own Grade `identity_scope_values`,
  ranked by `ordered_values`. Term order exists only in instruction text.
  buildsTowards is allowed when `source_rank <= target_rank`, so same-grade
  pairs are judged in both directions (`kgs/lp_coordinates.py:137-143`).
- Candidates are capped at `min(max_total_candidates, N(N-1)/2,
  N*max_candidates_per_sfi//2)` and over-budget pairs are dropped silently
  (`kgs/lp_candidates.py:264-281`). With this config that is
  `min(5000, 6N)`, so the 5000 cap binds at about 834 Skills. Each request
  makes one producer and one checker call, with up to 3 attempts each per
  invocation; concurrency is `max_concurrent_requests`.
- Any exhausted request fails the stage (`LPGenerationFailed`). Any
  buildsTowards cycle raises `LPFinalizationCycleError` after writing
  `lp_final_claims.json` (`kgs/lp_finalization.py:930-935`).
  `relationship_metadata` must match fixed approved literals.

## Known Unknowns

- Actual numbers of Skill SFIs, LC requests and LP candidates for CAPS;
  the bullet count above is a rough proxy. It matters for LLM cost and
  whether LP's 5000-candidate cap binds.
- How the KG model labels Grade/Term/Skill Area candidates whose banner cell
  combines lines (for example `"GRADE R HOME LANGUAGE ENGLISH\n\nREQUIREMENTS
  PER TERM"`). An unmapped Grade label would fail AS export, and a description
  that does not exactly normalize to a controlled value or alias leaves
  `canonical_statement_value` empty.
- The request describes the verified range as "page_index 35-135"; run
  metadata shows `end_page` 135 exclusive, i.e. page_index 35-134 (PDF
  36-135). Both agree on PDF 36-135.
- Real buildsTowards cycle frequency among same-grade CAPS Skill pairs.

## Evidence

- `backend/src/kgfeg/entries/create_kgs.py` — step order, input resolution,
  failure points.
- `backend/src/kgfeg/kgs/{utils,sfi_extraction_windows,sfi_extraction,sfi_registry,sfi_dedup,sfi_finalization,sfi_relationships,sfi_export}.py`
  — AS behavior.
- `backend/src/kgfeg/kgs/{lc_selection,lc_generation,lc_dedup,lc_finalization,lc_export}.py`
  — LC behavior.
- `backend/src/kgfeg/kgs/{lp_coordinates,lp_candidates,lp_generation,lp_checkpoints,lp_finalization,lp_validation,lp_export}.py`
  — LP behavior.
- `backend/src/kgfeg/schemas.py`, `backend/src/kgfeg/config.py` — config
  models, cross-validation, model settings.
- `results/kg_for_ed/<doc_key>/{extraction/extraction_run.json,stitching/document_ir.json}`
  — upstream run metadata and DocumentIR shape.
- Non-LLM dry run of AS steps 3-4 on the CAPS DocumentIR (outputs outside
  the repo) — window counts and selection reasons.
- Config load script — doc key, `output_dir` resolution.
- Focused pytest run — 3011 passed.
- `docs/pipeline/academic-standards.md`, `docs/guides/running-and-debugging.md`
  — documented stage behavior and resume caveats.
