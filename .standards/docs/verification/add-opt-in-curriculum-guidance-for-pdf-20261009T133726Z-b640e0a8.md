<!-- STANDARDS
Artifact: VERIFICATION
Cycle: add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8
-->

# Verification Report

`Cycle`: `add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8` `Mode`: `VERIFY` `Status`:
`COMPLETE` `User Style`: `NONE`
`Assessment Purpose`: `FULL` `Assessment Target`: `NONE`

## Assessed Inputs

- Scope `.standards/docs/scope/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  (last commit `43b8505`, blob `563261fc88`); current ACs: AC-001 to AC-006,
  AC-008, AC-009, AC-012 to AC-020, AC-022, AC-023, AC-025, AC-027 to AC-034.
  Retired (history only): AC-007, AC-010, AC-011, AC-021, AC-024, AC-026.
- Design `.standards/docs/specs/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  (`a5a1851`, blob `c2b47b19b7`); context `.standards/CONTEXT.md` (`a34c4e6`,
  blob `7d6abd01b3`); plan `.standards/docs/development/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  (`COMPLETE`, `AFTER_IMPLEMENTATION`, `Current Increment: NONE`; blob
  `59e8f5d045` at `31a20f6`).
- Implementation: `git diff d916660 31a20f6 -- backend examples` (9 backend
  source files, CAPS config). Source tree clean at `HEAD` `31a20f6`; CAPS config
  blob `c7022a5c3e` (AC-034 restore is committed in `31a20f6`). Dirty tree:
  Tester-owned test changes only, 8 files (blob prefixes):
  `page_ir_extraction/test_llm.py` ea59592bf9, `test_prompts.py` 9b7d77e130,
  `test_utils.py` f2fc06098c; `page_ir_verification/test_llm.py` 653109f22e,
  `test_prompts.py` e58cb91da6, `test_utils.py` 80946738f4,
  `test_verify_page_irs.py` 075d44f490; `tests/kgfeg/test_schemas.py`
  7deaee3b41. `.standards/user-styles/scoper/` untracked, unrelated.
- Environment: macOS, Python 3.13.9 (`backend/.venv`), run from `backend/` with
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo>`
  (fake key; no LLM calls).
- Local evidence (git-ignored): `data/funda_wande/caps_english_hl_grade_3_fs.pdf`;
  reruns `results/funda_wande_grade_{r,1,2,3}/`; trials
  `results/funda_wande_trial_p28_55/`, `results/funda_wande_trial_p110_135/`.
- Reason: first full verification after Developer's `FORWARD` handoff. Session
  limit: this conversation has no Developer implementation history; client
  metadata cannot certify a fresh session.

## Acceptance Evidence

| AC / technical criterion | Tests or checks | Disposition and evidence |
| --- | --- | --- |
| AC-001 | `tests/kgfeg/test_schemas.py::test_stage_config_instruction_fields_default_to_none_when_omitted[extraction]`, `..._are_stripped_and_blank_becomes_none[extraction]`, `test_stage_configs_still_reject_unknown_keys` | VERIFIED (E1) |
| AC-002 | `tests/kgfeg/page_ir_extraction/test_prompts.py::test_extraction_prompt_builders_append_curriculum_instructions_last_with_override_rule` (2 rows); `test_llm.py::test_extract_page_ir_routes_each_instruction_field_only_to_its_own_agent_prompt`; entry point `entries/extract_page_ir.py` passes `config.extraction_instructions`/`config.validation_instructions` (diff inspection) | VERIFIED (E1, E2, E7) |
| AC-003 | `test_extraction_prompt_builders_leave_prompts_unchanged_without_curriculum_instructions` (2 rows); scratch byte comparison vs `d916660` builders | VERIFIED (E1, E3) |
| AC-004 | `test_schemas.py` `[verification]` rows and unknown-key test | VERIFIED (E1) |
| AC-005 | `page_ir_verification/test_prompts.py::TestContinuityPromptCurriculumInstructions::test_verifier_appends_...`, `test_checker_appends_...`; `test_llm.py::test_verify_page_ir_pairs_routes_each_instruction_field_only_to_its_own_agent_prompt`; `test_verify_page_irs.py::test_passes_config_curriculum_instructions_to_verify_page_ir_pairs` | VERIFIED (E1, E2) |
| AC-006 | `TestContinuityPromptCurriculumInstructions::test_unset_instructions_leave_both_prompts_unchanged`; scratch byte comparison | VERIFIED (E1, E3) |
| AC-029 | `page_ir_extraction/test_utils.py::test_evaluate_page_ir_correction_rejects_correction_adding_word_not_on_page`, `..._rejects_on_page_text_repeated_more_often_than_page_has_it`; `test_llm.py::test_extract_page_ir_keeps_extraction_and_warns_when_guard_rejects_correction` (extraction kept, warning names page, count and words) ; `..._accepts_words_kept_from_extraction_but_missing_from_text_layer` | VERIFIED (E1, E2) |
| AC-008 | `test_evaluate_page_ir_correction_ignores_display_case_and_quote_style`, `..._ignores_added_running_header_artifacts` | VERIFIED (E1, E2) |
| AC-009 | `test_evaluate_page_ir_correction_skips_garbled_text_layer` (garbled layer) ; `..._accepts_correction_when_page_has_no_text_layer` | VERIFIED (E1, E2) |
| AC-030 | `..._ignores_display_case_and_quote_style` (no added words -> accepted); `test_llm.py::test_extract_page_ir_returns_correction_when_guard_accepts_it` ; `..._accepts_words_kept_from_extraction_but_missing_from_text_layer` | VERIFIED (E1, E2) |
| AC-027 | `test_llm.py::test_extract_page_ir_does_not_run_guard_when_hints_are_off`; prompts built before the guard call (inspection of `llm.py:296-370`) | VERIFIED (E1, E2) |
| AC-032 | Scratch replay `replay_pdf43.py` per the design's replay contract | VERIFIED (E4) |
| AC-012 | `page_ir_verification/test_utils.py::TestLoadPageIrsFromVerification` (3 scenarios); docstring/Raises/message inspected | VERIFIED (E1, E2) |
| AC-013 | Same tests: range starting at 35 loads; gap raises | VERIFIED (E1) |
| AC-014 | Scratch load of CAPS config as `RunConfig` (output_dir redirected); `kgs` inspection: South Africa / DBE metadata, Grade R-3, 0 occurrences of Ghana, NaCCA, BASIC, Basic 1, Strand, B1./B2./B3. | VERIFIED (E5) |
| AC-015 | `kgs.as.sfi_extraction_instructions` says "Grade, Term and Skill Area come from each table's header rows" | VERIFIED (E5, inspection) |
| AC-016 | Both extraction fields carry rules 1-3 (no-banner box continues as rows incl. ASSESSMENT openers; banner rows are header rows; keep parent column count) | VERIFIED (E5, inspection) |
| AC-017 | Both verification fields: banner table starts a new table even with matching columns; phrase on exactly 48 pages, PDF 36-133, nowhere else | VERIFIED (E5, inspection; 48/PDF 36-133 confirmed against the PDF text layer in E6) |
| AC-028 | `grade_level_mapping["Grade R"] == ["K"]` | VERIFIED (E5) |
| AC-034 | Committed config: `start_page`/`end_page` 35/59 in both page stages; `output_dir` `results/funda_wande_grade_r`, not a trial dir | VERIFIED (E5) |
| AC-018 | Case-insensitive whole-word scan of all lines added under `backend/src` and `backend/tests` since `d916660` (incl. Tester tests) | VERIFIED (E7) |
| AC-019 | Non-CAPS configs unchanged since `d916660`; 6 load with 4 fields `None`; all 4 prompts byte-identical to `d916660` builders | VERIFIED (E3) |
| AC-020 | `make test`; lint suite (check-only) | VERIFIED (E8) |
| AC-033 | Scratch `analyze_runs.py` over four grade reruns and both trials | VERIFIED (E6) |
| AC-022 | E6: 44 breaks into a banner page + 4 into resources boxes, 0 marked continuation; incl. PDF 110->111, 123->124 (trial merged both) | VERIFIED (E6) |
| AC-023 | E6: 48 banner-free continuation pages, 0 loose, all `resumed`/`both` tables, all joined by stitching to the previous page's table | VERIFIED (E6) |
| AC-031 | E6: every saved correction (8 pages) replayed through the guard adds no words; user reported no guard warnings in all four runs (terminal-only by design) | VERIFIED with limitation (E6) |
| AC-025 | E6: Grade 3 rerun, all 14 content segments carry "3.4 GRADE 3"; 9 segments touching PDF 122+ all carry it (trial: 8 of 96) | VERIFIED (E6) |
| Design TAC: guard never raises; coverage constants 0.50/10 | Inspection of `utils.py:483-555` | VERIFIED (inspection) |

## Scenario Budget

| Source file | Reused coverage | Active-change allocations | User additions |
| --- | --- | --- | --- |
| `kgfeg/schemas.py` | none | 5: default None (extraction, verification); strip + blank->None (extraction, verification); unknown keys still rejected | none |
| `page_ir_extraction/prompts.py` | existing prompt contract tests | 4: None path (2 rows), set path (2 rows) | none |
| `page_ir_verification/prompts.py` | existing prompt tests | 4: None path (verifier, checker), verifier set, checker set | none |
| `page_ir_extraction/llm.py` | existing orchestration tests | 4: guard reject, guard accept, hints off, instruction routing | none |
| `page_ir_verification/llm.py` | existing tests | 1: instruction routing | none |
| `page_ir_verification/verify_page_pairs.py` | existing attempt tests | 1: config fields forwarded | none |
| `page_ir_verification/utils.py` | none | 3: range above 0 loads; gap; duplicate | none |
| `page_ir_extraction/utils.py` | none | 7: missing word; over-count; case + quotes; ARTIFACT headers; garbled layer; no text layer; words kept from extraction | +2 (user-approved 2026-10-09, this file only, limit 7) |
| `entries/extract_page_ir.py` | none | 0 (2-line diff, inspection) | none |
| `examples/funda_wande/config_english_curriculum.json` | none | 0 suite scenarios (scratch load + inspection) | none |

Test corrections (no new allocation): stale stubs in
`page_ir_extraction/test_llm.py` (3 `_run_validation_agent` stand-ins and 2
prompt-builder stand-ins accept the new keyword) and
`page_ir_verification/test_verify_page_irs.py` (`VerificationConfigStub` gains
the two fields). Behavior under test unchanged.

User addition: the default ceiling left (a) AC-009 missing text layer and (b)
the AC-029/AC-030 case of words kept from the extraction but absent from the
text layer uncovered (seeded defects for both passed the first five). The user
approved +2 scenarios for `page_ir_extraction/utils.py` only (2026-10-09); both
were added and used.

## Execution Evidence

- E1: `.venv/bin/python -m pytest -n auto -q tests/kgfeg/page_ir_extraction tests/kgfeg/page_ir_verification tests/kgfeg/document_ir tests/kgfeg/test_schemas.py`
  (focused). Baseline before Tester changes: 654 passed, 9 failed (the 9
  stale stubs Developer reported). After stub corrections and new tests, each
  touched file passes (`test_llm.py` x2, `test_prompts.py` x2,
  `test_utils.py` x2, `test_verify_page_irs.py`, `test_schemas.py`); the
  full suite run is E8.
- E2: Test validity. `scratchpad/mutate.sh` copies `backend/src`+`tests` to a
  scratch dir, applies one textual mutation, runs the relevant file. Control
  (no change) passes; all 16 mutations fail at least one new test: guard
  `and`->`or`, no ARTIFACT skip, no casefold, no coverage check, set-based
  counts, guard ungated, reject keeps correction, extraction None adds `\n`,
  override sentence removed, extraction routing swapped, verifier uncertainty
  override removed, config field not forwarded, verification routing swapped,
  loader start-at-0, blank not mapped to None, verification None adds section.
  After the approved additions: guard without the extraction-count clause and
  guard treating a missing layer as empty are each caught (control passes).
  The first no-layer test data was masked by the coverage rule; it was
  corrected to an extraction under 10 distinct words before this result.
- E3: `scratchpad/check_prompt_identity.py` against `git show d916660:` copies
  of both prompts modules: 54 representative-input comparisons (4 builders,
  omitted and explicit `None`) and 24 per-config comparisons for the 6
  non-CAPS example configs (loaded as `RunConfig`, `pdf_fp`/`output_dir`
  redirected; all 4 fields `None`): 0 differ. `git diff --quiet d916660 HEAD --
  examples/{ghana,india,nigeria,rwanda}`: unchanged.
- E4: `scratchpad/replay_pdf43.py`: extraction `0042.val00.attempt00.parsed.json`
  (only attempt), correction `page_irs/0042.json`, text hint via
  `_extract_text_hint` on page_index 42 (usable). Result: rejected; added words
  exactly `assessment, for, informal, observation, or, oral, practical,
  suggestions` (correction/layer/extraction counts e.g. assessment 5/4/3).
- E5: CAPS config scratch load as `RunConfig` succeeds; `kgs` fields inspected
  (hierarchy Grade > Term > Skill Area > Skill, identity scopes, has-child
  policy, `included_table_section_patterns` `(?i)\brequirements\s+per\s+term\b`,
  empty code patterns, LP coordinate Grade R..Grade 3).
- E6: `scratchpad/analyze_runs.py` (independent of `logs/dev008/`). Ground
  truth from the PDF text layer: 48 banner pages PDF 36-133; resources pages
  PDF 59, 84, 109, 135. Run ranges confirmed from `extraction_run.json` and
  `verification_run.json`: 35-59, 59-84, 84-109, 109-135, hints on. Grade R ran
  without wording revision 1 (user-accepted); Grades 1-3 with it.
  Per run (rerun | trial where overlapping):
  - Continuation pages extracted as loose blocks: R 0/11 (trial PDF 36-55:
    5/9: 41, 43, 47, 49, 51); G1 0/12; G2 0/12; G3 0/13 (trial 12/13).
  - Breaks into a banner page marked continuation: 0 of 11 in each grade (G3
    trial: 110->111, 123->124); resources breaks: 0 of 4.
  - Breaks into a continuation page not marked table continuation: 0 in each
    grade (trial PDF 36-55: 5; G3 trial: 12).
  - Continuation not joined by stitching: 0 in each grade (trials: 5 and 12).
  - Content segments without the grade heading in `section_path`: 0 in each
    grade; max path length 1-2 (G3 trial: 87, max 60).
  - Saved checker corrections (final PageIR differs from raw extraction): R PDF
    46, 59; G1 73; G2 85, 91, 97; G3 114, 119. Replayed through the guard: no
    added words. Trial PDF 43 (only trial page flagged) adds 8 words.
  Limitation: the checker's verdicts are not persisted, so a correction the
  guard blocked leaves no file trace; AC-031's warning half rests on the
  user's report of no "correction guard rejected" lines (terminal-only by user
  decision). The Term finding has no direct pipeline output this cycle (KG step
  not run, non-goal); Term is present in every banner table's header rows.
- E7: AC-018 scan: `git diff d916660 -- backend/src backend/tests`, added lines
  (1805 incl. Tester tests), whole-word case-insensitive search for caps,
  funda, wande, south africa, ghana, ghanaian, nigeria, rwanda, india, madhi,
  pratham, nacca, tanzania, kenya, requirements per term, foundation phase,
  home language: 0 matches (a Tester reference to the `tanzania.pdf` fixture
  was replaced by a temp file before this result).
- E8: `make test` from `backend/` with the env above and `TERM=dumb`, run
  outside the sandbox (inside it, uv could not open `~/.cache/uv`; an earlier
  outside run was stopped before final formatting and is superseded). Final
  content: 3847 passed, 0 failed, 90 warnings, 424 s, exit 0
  (`logs/tester/test.txt`, git-ignored). `FAILED` lines in that log belong to
  inner pytest sessions of the slow-test plugin's own tests. Slow tests not
  included (CI default).
  Lint, check-only equivalents of `make lint` (no write-mode isort/black), log
  `logs/tester/lint.txt`: isort clean; black 177 files unchanged; ruff passed
  (src, tests); interrogate 100%; mypy no issues (92 src, 85 tests files);
  pylint 10.00/10 (src, tests). Black had first reformatted 5 Tester test
  files.
  Correction (user-reported, 2026-10-09, recovery frame 1): that pylint record
  was wrong. My filter regex did not match pylint's `[C1803` message format,
  and over the whole `tests/` tree 5 convention messages still round to
  10.00. Per-file pylint showed 5 x C1803 (`added_words == ()`) in
  `page_ir_extraction/test_utils.py` (9.85/10). Fixed to `assert not
  <decision>.added_words` (same assertion for a tuple). Rerun with the test
  env: pylint `--rcfile=backend/.pylintrc` on all 8 changed test files from
  the repo root 10.00/10 with no messages, and `pylint tests/` from `backend/`
  10.00/10 with no messages (grep on `[CRWEF]NNNN` codes). Black, ruff and
  mypy on `tests/` are clean; `test_utils.py` 30 passed. The full `make test`
  result above is reused: the only later change is this assertion-equivalent
  edit, and that file was re-run. Interrogate regenerated the tracked `interrogate_badge.svg` with no
  content change.

## Open Findings and Dependencies

- None blocking. No implementation, upstream, or verification defects found.
- Limitations carried to review: AC-031's warning half rests on the user's
  report of no guard warnings (terminal-only by user decision; checker
  verdicts are not persisted). Grade R ran before CAPS wording revision 1
  (user-accepted). The KG step was not run (non-goal), so AC-014/AC-015 rest on
  config loading and inspection, and the Term finding has no pipeline output.
- No later-role dependencies: every current AC has Tester evidence.

## Resume or Handoff

Full verification complete (`FULL`, `NONE`). Hand off to Reviewer, review
kind `IMPLEMENTATION`. Evidence: tests listed under Acceptance Evidence;
scratch scripts (`check_prompt_identity.py`, `replay_pdf43.py`,
`analyze_runs.py`, `mutate.sh`) were session-local and are described in E2-E6
with their inputs, so they can be rebuilt; logs in git-ignored `logs/tester/`.
No acceptance evidence remains pending downstream.
