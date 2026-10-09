<!-- STANDARDS
Artifact: DEVELOPMENT
Cycle: add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8
-->

# Development Plan

`Cycle`: `add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8` `Mode`: `STEPWISE`
`User Style`: `tony` `User Style Locked`: `true`
`Status`: `IN_PROGRESS` `Verification Cadence`:
`AFTER_IMPLEMENTATION` `Current Increment`: `NONE`

## Implementation Contract

- `Scope`: `.standards/docs/scope/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
- `Architecture`: `.standards/docs/specs/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
- `Request`: Add four opt-in, curriculum-neutral instruction fields (one per
  page IR agent) and a hints-gated guard against checker corrections that add
  words not on the page; fix the verified page IR loader docs; apply all CAPS
  behavior through `examples/funda_wande/config_english_curriculum.json` only;
  validate with reruns over the trial ranges plus PDF 60-66.

## Build Steps

### DEV-001 — Four optional instruction fields on the stage configs

`Status`: `DONE` `Depends On`: `NONE`
`Acceptance`: `AC-001, AC-004`

**Goal**

Add `extraction_instructions` and `validation_instructions` to
`ExtractionConfig`, and `verification_instructions` and
`validation_instructions` to `VerificationConfig`, each
`Optional[str] = Field(default=None, ...)` with curriculum-neutral
descriptions and a validator that strips the value and maps blank to `None`
(Architecture Decision 1).

**Affected Area**

`backend/src/kgfeg/schemas.py` (`ExtractionConfig`, `VerificationConfig`).

**Expected Outcome**

Both stage configs accept the new keys, default them to `None`, turn `""` or
whitespace into `None`, and still reject unknown keys. All seven example
configs still load through `RunConfig.model_validate`.

**Self-Check**

Existing `tests/kgfeg/test_schemas.py` with the CI-equivalent env; a scratchpad
script that loads every `examples/**/config*.json` and checks the new fields
are `None`; mypy on `schemas.py`.

Run 2026-10-09 from `backend/`, CI-equivalent env, on `c2e8a4b` plus the
uncommitted `schemas.py` change (98 lines added):

- Scratchpad `check_dev001.py`: all seven example configs load via
  `RunConfig.model_validate` with all four fields `None`; `"  rule text \n"`
  -> `"rule text"`, `""`/`"   "`/`None` -> `None` for each field; a non-string
  value and an unknown key each raise `ValidationError`. Limitation: `pdf_fp`
  and `output_dir` were replaced with local paths because the Nigeria config
  embeds another machine's absolute `pdf_fp` (pre-existing, unrelated).
- `pytest -n auto tests/kgfeg/page_ir_extraction tests/kgfeg/page_ir_verification tests/kgfeg/document_ir tests/kgfeg/test_schemas.py`:
  663 passed (matches baseline).
- `mypy`, `pylint` (10.00/10), `ruff`, `black --check`, `isort --check-only`,
  `interrogate` on `src/kgfeg/schemas.py`: all clean.

**Implementation Notes**

The blank-to-`None` validator passes non-strings through so pydantic's
`Optional[str]` check rejects them as a field-located `ValidationError`, unlike
the older `_strip_optional_strings` copies that raise a bare `TypeError`.

---

### DEV-002 — Extraction prompts take and receive curriculum instructions

`Status`: `DONE` `Depends On`: `DEV-001`
`Acceptance`: `AC-002, AC-003`

**Goal**

Give `extract_page_ir_from_pdf_page` and `validate_page_ir_extraction` a
`curriculum_instructions: str | None = None` keyword. When set, append a
`## RUNTIME CURRICULUM INSTRUCTIONS` section at the end of the system message
that frames the text as authoritative and overriding the generic rules except
the output contract (schema and the structural rules the Python quality
validators enforce). Thread `extraction_instructions` and
`validation_instructions` through `extract_page_ir` and `_run_validation_agent`
from `entries/extract_page_ir.py` (Decisions 2 and 3).

**Affected Area**

`backend/src/kgfeg/page_ir_extraction/prompts.py`, `page_ir_extraction/llm.py`,
`entries/extract_page_ir.py`.

**Expected Outcome**

With `None`, both prompts are byte-identical to commit `d916660`. With a value,
the text appears only in its own agent's system message, after every generic
section. The entry point passes the two config values.

**Self-Check**

Existing `tests/kgfeg/page_ir_extraction`; a scratchpad script comparing both
`PromptPair`s against `git show d916660:...prompts.py` output for
representative inputs (with and without text/table hints), and confirming
set-path placement and isolation.

Run 2026-10-09 from `backend/`, CI-equivalent env, on the DEV-001 tree plus
uncommitted changes to `page_ir_extraction/prompts.py`, `page_ir_extraction/llm.py`
and `entries/extract_page_ir.py`:

- Scratchpad `check_dev002.py`: for three extraction inputs (no hints; text and
  table hints; text hint only) and one checker input, both builders with
  `curriculum_instructions` omitted or `None` return a `PromptPair` equal to
  `d916660`'s `prompts.py`. With a value set, the user message is unchanged,
  the system message equals the old one plus `\n\n## RUNTIME CURRICULUM
  INSTRUCTIONS\n...` ending in the tagged text, and the text appears once.
  Driving `extract_page_ir` with stub agents routes `extraction_instructions`
  only to the extraction builder and `validation_instructions` only to the
  checker builder.
- Entry point passes `config.extraction_instructions` and
  `config.validation_instructions` (diff inspection).
- `mypy`, `pylint` (10.00/10), `ruff`, `black`, `isort`, `interrogate` on the
  three files: clean.
- `pytest -n auto tests/kgfeg/page_ir_extraction tests/kgfeg/page_ir_verification tests/kgfeg/document_ir tests/kgfeg/test_schemas.py`:
  659 passed, 4 failed. All four are in
  `tests/kgfeg/page_ir_extraction/test_llm.py`
  (`test__run_validation_agent_invokes_agent_and_tracks_usage`,
  `test_extract_page_ir_returns_corrected_page_ir_when_validation_fails`,
  `test_extract_page_ir_passes_pdf_hints_into_prompt_builder`,
  `test_extract_page_ir_returns_extraction_page_ir_when_validation_passes`) and
  fail with `TypeError: ... unexpected keyword argument
  'curriculum_instructions'` or `'validation_instructions'`: their stand-in
  functions declare an exact keyword-only signature, and the orchestration now
  always passes the new keyword (as `None` when unset). Behavior under test is
  unchanged. The stubs are Tester-owned; see Plan Notes.

**Implementation Notes**

One private helper, `_append_curriculum_instructions`, builds the section for
both builders; each passes its own one-sentence directive (the checker's says
an extraction breaking the instructions is an error-severity issue and any
`corrected_page_ir` must follow them).

---

### DEV-003 — Verification prompts take and receive curriculum instructions

`Status`: `DONE` `Depends On`: `DEV-001`
`Acceptance`: `AC-005, AC-006`

**Goal**

Give `verify_page_ir_pairs_from_extraction` and
`validate_page_ir_continuity_verdict` the same optional keyword and appended
section. The framing must also name, as overridable, the TABLE continuation
decision procedure (section B, Steps 1-4, including the "content differences
inside the grid" sentence) and, in the verifier only, the TABLE<->TABLE
exception in the uncertainty policy. Thread `verification_instructions` and
`validation_instructions` from `VerificationConfig` through
`_execute_verification_attempts` -> `verify_page_ir_pairs` ->
`_run_validation_agent` (Decisions 2 and 3).

**Affected Area**

`backend/src/kgfeg/page_ir_verification/prompts.py`,
`page_ir_verification/llm.py`, `page_ir_verification/verify_page_pairs.py`.

**Expected Outcome**

With `None`, both prompts are byte-identical to commit `d916660`. With a value,
the text appears only in its own agent's system message under the override
heading, and the framing names the table rule as overridable.

**Self-Check**

Existing `tests/kgfeg/page_ir_verification`; scratchpad prompt-equality and
isolation script as in DEV-002.

Run 2026-10-09 from `backend/`, CI-equivalent env, on the DEV-002 tree plus
uncommitted changes to `page_ir_verification/prompts.py`,
`page_ir_verification/llm.py` and `page_ir_verification/verify_page_pairs.py`:

- Scratchpad `check_dev003.py`: for two threshold sets, both builders with
  `curriculum_instructions` omitted or `None` return a `PromptPair` equal to
  `d916660`'s `prompts.py`. With a value set, the user message is unchanged,
  the section is appended last in the system message, the text appears once,
  both framings name "the TABLE continuation DECISION PROCEDURE in section B
  (Steps 1-4)" and the in-grid content rule as overridable, and only the
  verifier's names the UNCERTAINTY POLICY TABLE<->TABLE exception. Driving
  `verify_page_ir_pairs` with stub agents routes `verification_instructions`
  only to the verifier builder and `validation_instructions` only to the
  checker builder.
- `_execute_verification_attempts` passes `config.validation_instructions`
  and `config.verification_instructions` (diff inspection).
- `mypy`, `pylint` (10.00/10), `ruff`, `black`, `isort`, `interrogate` on the
  three files: clean.
- Focused suites: 654 passed, 9 failed. Four are the DEV-002 `test_llm.py`
  stubs. Five are in `tests/kgfeg/page_ir_verification/test_verify_page_irs.py`
  (`test_raises_runtime_error_when_all_attempts_fail`,
  `test_records_errors_then_selects_the_later_successful_attempt`,
  `test_uses_pair_priority_key_to_select_the_best_successful_attempt`,
  `test_stops_early_for_primary_primary_patchable_positive`,
  `test_stops_early_for_primary_primary_same_family_high_confidence_negative`):
  each attempt fails with `'VerificationConfigStub' object has no attribute
  'validation_instructions'`, because the test's stand-in config class lacks
  the two new attributes. Tester-owned stub update under the option A
  decision in Plan Notes.

**Implementation Notes**

A private `_append_curriculum_instructions` in this module (separate from the
extraction one, since the framings differ) adds an `overridable_rules` clause:
when the instructions say a table starts a new table or continues the previous
one, their conclusion wins over the decision procedure's; the SCHEMA
INVARIANTS still apply.

---

### DEV-004 — Hints-gated correction guard

`Status`: `DONE` `Depends On`: `DEV-002`
`Acceptance`: `AC-029, AC-008, AC-009, AC-030, AC-027, AC-032`

**Goal**

Add pure functions to `page_ir_extraction/utils.py`, next to the existing
text-hint quality gate, for
normalization (NFKC, soft-hyphen removal, curly-to-straight quotes and primes,
casefold, line-break hyphen joins counted both ways), content-word collection
from transcribed fields only (skip `ARTIFACT` blocks, `alt_text`,
`embedded_text`, `local_code`, list markers, `text_en`), text-layer usability
(the existing gated `text_hint`, plus 50% distinct-word coverage when the
extraction has at least 10 distinct content words), and the accept-or-reject
decision using occurrence counts: a word is added when it occurs more times in
the correction than in the text layer and than in the extraction. In
`extract_page_ir`, run it only when `pdf_page` was supplied and the checker
failed: on rejection, log a `logger.warning` with the 1-based page, word count
and sorted words, and return the extraction agent's PageIR (Decision 4).

**Affected Area**

`backend/src/kgfeg/page_ir_extraction/utils.py`, `page_ir_extraction/llm.py`.
No new module.

**Expected Outcome**

Hints on, usable layer, added words (including on-page text repeated more often
than the page has it) -> extraction PageIR kept and terminal warning. The
PDF 43 trial replay is rejected with the 8 over-counted words. Any other case -> correction returned as today. Hints off ->
guard not invoked. No prompt changes. The guard never raises.

**Self-Check**

Existing `tests/kgfeg/page_ir_extraction`; a scratchpad script driving the
decision function over each Decision 4 branch (case, quotes, artifact header,
garbled layer, `None` layer, extraction-kept words, genuinely added words) and
replaying the PDF 43 trial correction from `results/` against the real PDF
text layer.

Run 2026-10-09 from `backend/`, CI-equivalent env, on the DEV-003 tree plus
uncommitted changes to `page_ir_extraction/utils.py` (guard functions,
`PageIRCorrectionDecision`, `evaluate_page_ir_correction`) and
`page_ir_extraction/llm.py` (guard call on the `pdf_page is not None` path):

- Scratchpad `check_dev004.py`: case/curly-quote/line-break-hyphen differences,
  an added ARTIFACT header, words kept from the extraction, and added pure
  numbers or single characters are all accepted; genuinely new words
  (`drills`, `phonics`, `zebras`) are rejected; `text_hint=None` and a garbled
  layer (coverage below 50% of 12 extracted words) are accepted. Through
  `extract_page_ir` with stub agents: hints off returns the correction without
  invoking the guard; hints on with added words returns the extraction PageIR.
- Trial replay against the real CAPS text layer (raw extraction
  `page_irs_raw/*.val00.attempt00.parsed.json` vs saved `page_irs/*.json`):
  all 12 corrected trial pages (PDF 29, 33, 36, 39, 43, 50, 52, 54, 112, 113,
  124, 131) are accepted with no added words, **including PDF 43**.
- PDF 43 investigation (`inspect_p43*.py`): the correction (16 items vs the
  extraction's 10) repeats the ASSESSMENT band (items 9-13 duplicate items
  1-5). Every content word is in both the text layer and the extraction; the 8
  distinct words `assessment, suggestions, for, informal, oral, or,
  practical, observation` each occur once more than in the text layer
  (correction/layer/extraction counts: assessment 5/4/3, the others 2/1/1 or
  3/2/2). A per-word occurrence-count comparison flags PDF 43 alone among the
  12 corrected trial pages (`inspect_dupes.py`).
- `mypy`, `pylint` (10.00/10), `ruff`, `black`, `isort`, `interrogate` on
  `utils.py` and `llm.py`: clean. `doctest.testmod` on `utils.py`: 3 attempted,
  0 failed. Focused suites: 654 passed, 9 failed (the same Tester-owned stub
  failures as DEV-003; no new failures).

**Implementation Notes**

The implementation matches Architecture Decision 4 and AC-026 as written, but
that contract cannot block the PDF 43 correction the scope cites as the
motivating case. Routed as a `SCOPING` failure; see Suspended Assignment 1.

Rework run 2026-10-09 after Recovery Reconciliation 1, from `backend/`,
CI-equivalent env, on `56b91f8` plus uncommitted changes to
`page_ir_extraction/utils.py` (set helpers replaced by `_count_content_words`,
`_count_page_ir_content_words`, `_count_text_layer_content_words`;
`evaluate_page_ir_correction` uses occurrence counts, set-based coverage kept)
and `page_ir_extraction/llm.py` (docstrings and warning text). This supersedes
the earlier set-rule evidence above:

- Scratchpad `check_dev004.py`: all earlier cases still behave as before; new
  count cases: a correction that repeats an on-page paragraph with no new
  vocabulary is rejected (`big, books, learner, of, the`), and a repeat that
  stays within `max(L, E)` (word twice in the layer) is accepted.
- AC-032 PDF 43 replay (extraction = highest-numbered
  `page_irs_raw/0042.val00.attempt*.parsed.json`, which is `attempt00`;
  correction = `page_irs/0042.json`; text hint = page index 42 through
  `_extract_text_hint`): rejected, 8 added words `assessment, for, informal,
  observation, or, oral, practical, suggestions`.
- All 12 corrected trial pages (PDF 29, 33, 36, 39, 43, 50, 52, 54 in
  `funda_wande_trial_p28_55`; PDF 112, 113, 124, 131 in
  `funda_wande_trial_p110_135`): only PDF 43 is rejected. Note: the design's
  evidence says the other trial ranges have no saved extraction outputs;
  `funda_wande_trial_p110_135` does have them and they are covered here.
- `mypy`, `pylint` (10.00/10), `ruff`, `black`, `isort`, `interrogate` on both
  files: clean. `doctest.testmod` on `utils.py`: 3 attempted, 0 failed.
  Focused suites: 654 passed, 9 failed (the same Tester-owned stub failures
  recorded for DEV-002 and DEV-003).

---

### DEV-005 — Loader documentation for ranges not starting at 0

`Status`: `DONE` `Depends On`: `NONE`
`Acceptance`: `AC-012`

**Goal**

Keep the committed contiguity logic in `load_page_irs_from_verification` and
update its NB note, Raises entry and error message to describe a gap-free
`page_index` sequence that may start at any index (Decision 5).

**Affected Area**

`backend/src/kgfeg/page_ir_verification/utils.py` (`:488-595`).

**Expected Outcome**

No text in the function claims the sequence must start at 0; a gap or
duplicate raises `ValueError` about contiguity.

**Self-Check**

Existing `tests/kgfeg/page_ir_verification`; a scratchpad call with a temp
directory for a range starting above 0 and a range with a gap.

Run 2026-10-09 from `backend/`, CI-equivalent env, on the DEV-004 tree plus the
uncommitted `page_ir_verification/utils.py` change (NB note, Raises entry,
error message; logic untouched):

- Scratchpad `check_dev005.py` on the real trial output
  `results/funda_wande_trial_p28_55/*/verification/page_irs_verified` with its
  `extraction_run.json` doc_key: loads 28 pages, page_index 27..54. Copies with
  a gap (27, 28, 29, 31, 32) and a duplicate (27, 28, 29, 29) each raise
  `ValueError: Non-contiguous page_index sequence: expected a gap-free sequence
  with no duplicates starting at 27. Got [...]`.
- No remaining "start at 0" wording in the loader.
- `mypy`, `pylint` (10.00/10), `ruff`, `black`, `isort`, `interrogate`: clean.
  Focused suites: 654 passed, 9 failed (same Tester-owned stub failures).

**Implementation Notes**

AC-013's automated tests are Tester-owned and are not written here.

---

### DEV-006 — CAPS runtime config

`Status`: `DONE` `Depends On`: `DEV-001`
`Acceptance`: `AC-014, AC-015, AC-016, AC-017, AC-028, AC-034`

**Goal**

In `examples/funda_wande/config_english_curriculum.json` only: set the four
page-stage instruction fields per **CAPS page-stage instructions**, and replace
the Ghana-copy `kgs` block per **CAPS `kgs` block** (metadata, Grade > Term >
Skill Area > Skill hierarchy, policies, `grade_level_mapping` with Grade R ->
`["K"]`, no codes, `included_table_section_patterns` on "requirements per
term", `sfi_extraction_instructions` saying Grade, Term and Skill Area come
from table header rows, and CAPS-specific AS/LC/LP instruction fields). Set
`start_page`/`end_page` to the Grade R range 35/59 in both page stages and
point `page_ir_extraction.output_dir` at a fresh Grade R rerun directory under
`results/` (AC-034, Decision 6).

**Affected Area**

`examples/funda_wande/config_english_curriculum.json`.

**Expected Outcome**

The config loads as a `RunConfig`, has no Ghana, NaCCA or BASIC values, and
carries the required CAPS rules in all four page-stage fields.

**Self-Check**

`RunConfig.model_validate` on the file; grep for Ghana-specific strings;
review against the architecture contract.

Run 2026-10-09 from `backend/`, CI-equivalent env, on the DEV-005 tree plus the
uncommitted `examples/funda_wande/config_english_curriculum.json` change
(generated by scratchpad `build_caps_config.py`; tab-indented JSON verified to
round-trip byte-for-byte before editing):

- Scratchpad `check_dev006.py`: `RunConfig.model_validate` succeeds; the `kgs`
  block contains none of `Ghana, NaCCA, BASIC, Basic 1, Strand, B1., B2., B3.`;
  every value outside the four new page-stage fields and the rewritten `kgs`
  content equals `HEAD` (including `start_page`/`end_page` 27/55 and
  `document_ir`); generic numeric `as`/`lc`/`lp` settings unchanged;
  `grade_level_mapping["Grade R"] == ["K"]`; all four page-stage texts carry
  their contract phrases; building each prompt with its field puts the text
  only at the end of that agent's system message.
- Source grounding: a PyMuPDF scan of the 48 banner pages finds exactly the
  configured Grade (R, 1-3), Term (1-4) and Skill Area values (Grade R:
  Listening and Speaking (Oral), Emergent Reading, Emergent Writing; Grades
  1-3: Listening and Speaking (Oral), Reading and Phonics, Writing); title and
  imprint pages give the framework title and "© 2011 Department of Basic
  Education".
- `included_table_section_patterns` `(?i)\brequirements\s+per\s+term\b`
  against trial stitched `columns_signature`s: 21 of 22 banner-range tables
  match; the miss is PDF 110-111, where the trial split the banner off into
  its own table (`term 1|`), the defect extraction rule 1 now addresses.
  Non-matches otherwise are front matter (PDF 13-15, 28-35) and PDF 135.
- KG step not run (non-goal); the `kgs` block is checked by loading and review
  only.

**Implementation Notes**

The extraction checker text adds one line beyond the three required rules:
fixing structure must not add, repeat, or reword text, which targets the
PDF 43 duplication alongside the AC-029 guard.

Reopened run 2026-10-09 (Recovery Reconciliation 2, AC-034), on `a5a1851`
plus the uncommitted config change: scratchpad `check_dev006b.py` loads the
config via `RunConfig.model_validate` (with `output_dir` redirected so the
validator does not create the real directory); `start_page`/`end_page` are
35/59 in both page stages; `page_ir_extraction.output_dir` is
`<repo>/results/funda_wande_grade_r`, which is not a trial directory and does
not exist yet; the only values changed versus `HEAD` are those five.

---

### DEV-007 — Cycle-wide compatibility and lint self-check

`Status`: `DONE` `Depends On`: `DEV-001, DEV-002, DEV-003, DEV-004, DEV-005, DEV-006`
`Acceptance`: `AC-018, AC-019, AC-020`

**Goal**

Confirm the backend change is curriculum-neutral and existing pipelines are
unchanged, and fix any lint or test fallout in Developer-owned code.

**Affected Area**

All backend files changed in DEV-001 to DEV-005.

**Expected Outcome**

No changed backend source names a curriculum or curriculum text; the Ghana,
India, Nigeria and Rwanda configs are untouched, load, and yield prompts
identical to `d916660` for all four agents; `make lint` and `make test` pass.

**Self-Check**

`git diff d916660 -- backend` grep for curriculum terms; scratchpad prompt
comparison per non-CAPS config; `make lint` and `make test` from `backend/`.

**Implementation Notes**

User-run step (user request, 2026-10-09): the user runs the commands and
Developer reviews the saved output. The check script and outputs live in
git-ignored `logs/dev007/`: `check_non_caps_prompts.py` (loads every non-CAPS
example config with `pdf_fp`/`output_dir` redirected, confirms the file is
unchanged since `d916660` and all four instruction fields are `None`, and
compares all four agents' prompts with the `d916660` prompt builders),
`curriculum_terms.txt`, `non_caps_prompts.txt`, `lint.txt` and `test.txt`.
Expected `make test` result: only the 9 Tester-owned stub failures recorded
under DEV-002 and DEV-003.

Run 2026-10-09 by the user from `backend/` on `9cd9cdd` (working tree: only
`.standards/` records modified); output reviewed by Developer:

- AC-018 (`logs/dev007/curriculum_terms.txt`): `git diff d916660 -- src tests`
  adds 667 lines across 9 files; a case-insensitive whole-word search of the
  added lines for `caps, funda wande, wande, south africa, ghana, nigeria,
  rwanda, india, madhi, pratham, nacca, requirements per term` finds 0 matches
  (file empty; count rechecked by Developer).
- AC-019 (`logs/dev007/non_caps_prompts.txt`): all 6 non-CAPS example configs
  (Ghana English and math, India Madhi and Pratham, Nigeria, Rwanda) are
  unchanged since `d916660`, load as `RunConfig` with all four instruction
  fields `None`, and give extraction, extraction-checker, continuity-verifier
  and continuity-checker prompts identical to the `d916660` builders. 6
  checked, 0 failed.
- AC-020 lint (`logs/dev007/lint.txt`, `make lint`): isort and black left all
  177 files unchanged; ruff "All checks passed" (src and tests); interrogate
  100.0% (minimum 100.0%); mypy no issues in 92 and 85 source files; pylint
  10.00/10 for src and tests; cloc ran.
- AC-020 tests (`logs/dev007/test.txt`, `make test`): 3810 passed, 9 failed in
  510.95s. The 9 are exactly the Tester-owned stub failures recorded under
  DEV-002 and DEV-003 (4 `test_llm.py` `TypeError`s on
  `curriculum_instructions`/`validation_instructions`; 5
  `test_verify_page_irs.py` failures from `VerificationConfigStub` lacking
  `validation_instructions`). Other `FAILED`/`assert False` lines in the log
  belong to inner pytest sessions run by the slow-test plugin's own tests, not
  to the outer run. Per the option A decision, these stub updates are left to
  Tester; no Developer-owned fallout remains.

---

### DEV-008 — Validation reruns on the CAPS PDF (user-run, Developer-checked)

`Status`: `IN_PROGRESS` `Depends On`: `DEV-007`
`Acceptance`: `AC-033, AC-022, AC-023, AC-025, AC-031, AC-032`

**Goal**

Developer does not run the paid pipeline. The validation is four grade runs
(AC-033; page_index start inclusive, end exclusive): Grade R 35-59 from the
committed config as is, then Grade 1 59-84, Grade 2 84-109 and Grade 3
109-135, for which the user sets `start_page`/`end_page` in both page stages
and a fresh `page_ir_extraction.output_dir` per grade in the local config
only (not committed). Developer gives the user the exact extraction,
verification and stitching commands and what to keep from the terminal (guard
warnings). The user runs each grade and returns. Developer analyzes the
outputs against the trial runs where pages overlap (Grade R vs
`trial_p28_55` PDF 36-55; Grade 3 vs `trial_p110_135`), reports pages with no
baseline (PDF 56-109) on their own, reruns the PDF 43 replay (AC-032), and
records the comparison here (Build Plan step 5).

**Affected Area**

Rerun outputs under git-ignored `results/`; the user's local per-grade config
edits; possibly CAPS config wording.

**Expected Outcome**

A recorded before/after comparison in this plan showing new-banner breaks
split (including 110->111, 123->124), continuation and ASSESSMENT pages kept as
stitched table rows, no accepted correction adding content words as defined in
AC-029 (blocked ones seen as warnings in the user's terminal output), and the Grade 3 heading in
`section_path` throughout Grade 3. If a miss traces to config wording,
Developer revises the CAPS text and asks the user to rerun only the affected
grade, into a fresh output directory or with `overwrite` set. Misses not fixable by config wording are routed to their owner.

**Self-Check**

Scratchpad analysis scripts over the user's rerun outputs and the trial
outputs, plus the guard warning lines the user pastes back.

**Implementation Notes**

The step has two pauses: after handing over the run instructions
(`BlockedOn` waits for the user's runs), and after the analysis, under STEPWISE.

Progress: Grade R run instructions handed over 2026-10-09 (committed config at
`9cd9cdd`: page_index 35-59, `output_dir` `results/funda_wande_grade_r`,
which did not exist beforehand). Entry points are run from `backend/` as
`.venv/bin/python src/kgfeg/entries/<entry>.py ../examples/funda_wande/config_english_curriculum.json`
for `extract_page_ir`, `verify_page_ir_continuity`, `stitch_document_ir`.
Guard evidence (AC-031) comes from the user's terminal: lines containing
"correction guard rejected", pasted back (no log file, per scope).

**Grade R run (page_index 35-59, PDF 36-59)** — run by the user 2026-10-09
into `results/funda_wande_grade_r/` with the committed config at `9cd9cdd`;
analyzed by Developer with scratchpad scripts `grade_extraction.py`,
`grade_verification.py`, `grade_stitching.py`, `grade_corrections.py`,
`replay_pdf43.py`. User reported no guard warnings in the terminal.

- Extraction (vs `trial_p28_55` for PDF 36-55): all 12 banner pages give one
  1-column table with `header_row_count` 4 (trial: 3 on 6 of them); all 10
  banner-free pages (PDF 37, 39, 41, 43, 45, 47, 49, 51, 54, 56 plus 58) are
  extracted as `resumed` tables. The ASSESSMENT-opening pages PDF 41, 43, 47,
  49, 58 and the continuation page PDF 51, which the trial extracted as loose
  heading/paragraph/list blocks (PDF 41, 43, 47, 49, 51), are now table rows.
  PDF 59 ("RECOMMENDED TEXTS/RESOURCES FOR THE YEAR", no banner) is its own
  `complete` table.
- Verification (AC-022): all 12 page breaks into a banner page are
  `is_continuation=false` (confidence 0.95-0.96); all 10 breaks into a
  banner-free continuation page are table continuations (0.85-0.93). The trial
  had 5 of those as new tables (PDF 40->41, 42->43, 46->47, 48->49, 50->51).
  PDF 58->59 (resources box) is correctly a new table (0.93).
- Stitching (AC-023): 14 segments: the "3.1 GRADE R" heading, 12 banner tables
  each joined to its continuation page ([36,37], [38,39], [40,41], [42,43],
  [44,45], [46,47], [48,49], [50,51], [52], [53,54], [55,56], [57,58]; PDF 52
  is correctly alone, PDF 53 being a banner page) and the PDF 59 resources
  table. All 13 content segments carry the Grade R heading in `section_path`
  (path length 1).
- Corrections (AC-031): the checker's correction was saved on 2 of 24 pages
  (PDF 46, 59); replayed through the guard, both are accepted with no added
  words, consistent with no terminal warnings. Text-layer content words not in
  the final PageIR are 5 or 10 per page (the running headers, kept as
  ARTIFACT blocks), plus "cupboard" on PDF 51, an artifact of the hyphen-join
  counting of the printed syllable example "cup-board", which is transcribed
  verbatim.
- AC-032 replay (trial PDF 43, `0042.val00.attempt00.parsed.json` vs
  `page_irs/0042.json`): rejected, added words `assessment, for, informal,
  observation, or, oral, practical, suggestions`.
- Risk for later grades: each grade ends with a banner-free "RECOMMENDED
  TEXTS/RESOURCES FOR THE YEAR" box (PDF 59, 84, 109, 135). The CAPS rule
  "a banner-free box at the top of a page continues the previous table"
  (extraction rule 2, verification rule 3) is literally wrong for these boxes;
  Grade R handled PDF 59 correctly anyway.

**CAPS wording revision 1** (user-approved 2026-10-09, before the Grade 1 run;
the user accepted that Grade R ran with the earlier wording): one exception
sentence appended to rule 2 of both extraction texts and rule 3 of both
verification texts: a box/table whose top row is a title such as "RECOMMENDED
TEXTS/RESOURCES FOR THE YEAR" starts its own table, not a continuation, and
appears at the end of each grade (PDF 59, 84, 109, 135). Self-check
(scratchpad `check_wording.py`): config loads as `RunConfig`; only those four
values differ from `HEAD`; each text has the exception exactly once; AC-016
and AC-017 phrases still present; range and `output_dir` unchanged (35/59,
`funda_wande_grade_r`).

Grade 1 run handed over: the user sets, locally and uncommitted,
`start_page` 59 and `end_page` 84 in both page stages and
`page_ir_extraction.output_dir` to `<repo>/results/funda_wande_grade_1`, then
runs the same three entry points. No trial baseline covers PDF 60-84.

**Grade 1 run (page_index 59-84, PDF 60-84)** — run by the user 2026-10-09
into `results/funda_wande_grade_1/` with `97bee9a` (wording revision 1) plus
the local range edits (`extraction_run.json` confirms 59/84 and the new
wording). No trial baseline; reported on its own. User reported no guard
warnings.

- Extraction: all 12 banner pages give one table with 3 or 4 header rows
  holding the complete banner (Grade line and REQUIREMENTS PER TERM are one
  printed row on some pages); all 13 banner-free pages, including the
  ASSESSMENT openers PDF 65, 72, 75, are tables (`resumed`, or `both` for PDF
  62, 69, 75 that continue onto a third page). PDF 84 (resources) is a heading
  plus its own `complete` table.
- Column counts: Grade 1 banner tables have `n_cols` 2 only because the
  skill-area row splits into skill area and contact time; every body row spans
  both columns (`col_span` 2). Continuation pages are full-width and come out
  `n_cols` 1. Stitching widens those rows (21 `table_colspan_repair` warnings,
  no text change), which matches the printed layout; not treated as a miss of
  the "keep the parent's column count" rule.
- Verification (AC-022): all 11 breaks into a banner page are new tables
  (0.95-0.96); all 12 breaks into a banner-free continuation are table
  continuations (0.85-0.90); PDF 83->84 (resources box) is a new table (0.95),
  the first case covered by wording revision 1.
- Stitching (AC-023): 15 segments: the "3.2 GRADE 1" heading, 12 banner tables
  with their continuations ([60], [61-63], [64,65], [66,67], [68-70], [71,72],
  [73], [74-76], [77,78], [79], [80,81], [82,83]; [60], [73], [79] are
  correctly alone, the next page being a banner page), and the PDF 84
  resources heading and table. All 13 content segments carry the Grade 1
  heading in `section_path`. One further warning: stitching inferred
  `header_row_count` 3 for the PDF 84 resources table; it has no
  REQUIREMENTS PER TERM signature, so it is outside KG table selection.
- Corrections (AC-031): the checker's correction was saved on 1 of 25 pages
  (PDF 73); replayed through the guard it is accepted with no added words.
  Text-layer content words missing from the final PageIRs are only the
  running headers (5 or 10 per page).

Grade 2 run handed over: local, uncommitted `start_page` 84 / `end_page` 109
in both page stages and `output_dir` `<repo>/results/funda_wande_grade_2`.
No trial baseline covers PDF 85-109.

---

## Plan Notes

- Formal tests named in the Architecture Build Plan (prompt `None`/set paths,
  guard branches, loader ranges for AC-013) are Tester-owned. Developer
  self-checks use the existing suite plus scratchpad scripts, which are not
  committed.
- Test commands run from `backend/` with
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo root>`.
- Test stubs with exact signatures (DEV-002, DEV-003): existing `test_llm.py`
  stand-ins for the prompt builders and `_run_validation_agent` reject the new
  keyword arguments, and `test_verify_page_irs.py`'s `VerificationConfigStub`
  lacks the two new verification attributes. Developer does not edit Tester-owned tests and does not bend
  production code (for example, passing the keyword only when set) around them.
  The Architecture's note that existing tests keep working unchanged does not
  hold for these stubs. User decision (2026-10-09, option A): leave the stub
  updates to Tester; DEV-007 reports any remaining failures of this kind as
  known Tester-owned stub updates rather than routing an architecture failure.

### Suspended Assignment 1

`Recovery Frame`: `1` `Recovery Reason`: `AC-026 cannot stop the PDF 43 case: that correction duplicates an existing ASSESSMENT band (8 distinct words each one occurrence over the text layer), it adds no word missing from the text layer.` `Purpose`: `DEVELOPMENT`
`Target`: `NONE` `Assessed Inputs`: `DEV-001 to DEV-003 committed or staged by the user; DEV-004 uncommitted changes to backend/src/kgfeg/page_ir_extraction/utils.py and llm.py implementing Architecture Decision 4 as of 2026-10-09`
`Next Action`: `Reconcile DEV-004 against the corrected scope and design (reopen or revise it under the approval rules), finish its self-check record, then continue STEPWISE with DEV-005.`

### Recovery Reconciliation 1

Frame 1 closed 2026-10-09 (scope `dcc09dc`, design `56b91f8`). Restored
Suspended Assignment 1. Reconciliation: DEV-001 to DEV-003 unaffected. DEV-004
keeps its goal, files and dependencies; its rule follows the corrected
Decision 4 (occurrence counts; `L` is the element-wise max of raw and
hyphen-joined text-layer counts; coverage stays set-based), and its acceptance
mapping moves from retired AC-026/AC-010 to AC-029/AC-030 plus AC-032 (PDF 43
replay, run as part of its self-check and again in DEV-008). DEV-008 maps
AC-031 in place of retired AC-024. Traceability and in-step rule updates under
unchanged approved intent; no reapproval required.

### Suspended Assignment 2

`Recovery Frame`: `1` `Recovery Reason`: `User rework: validation reruns become four grade ranges (page_index 35-59, 59-84, 84-109, 109-135) replacing AC-021's trial ranges plus 59-66, and the committed CAPS config starts at the Grade R range.` `Purpose`: `DEVELOPMENT`
`Target`: `NONE` `Assessed Inputs`: `DEV-001 to DEV-006 DONE; DEV-006 CAPS config change as of 2026-10-09 with start_page/end_page still 27/55`
`Next Action`: `Reconcile DEV-006 and DEV-008 against the corrected scope and design (Grade R range in the committed config; four grade-range validation runs, user-run and Developer-checked), then continue STEPWISE with DEV-007.`

### Recovery Reconciliation 2

Frame 1 (user rework 2) closed 2026-10-09 (scope `43b8505`, design `a5a1851`).
Restored Suspended Assignment 2. DEV-001 to DEV-005 unaffected. DEV-006
reopened to set the committed Grade R range (35/59 in both page stages) and a
fresh Grade R `output_dir` (AC-034). DEV-008 now covers the four grade runs
(AC-033 replaces retired AC-021) plus the AC-032 replay. Bookkeeping and an
in-step config correction under unchanged approved intent; no reapproval
required. Next after DEV-006: DEV-007.
