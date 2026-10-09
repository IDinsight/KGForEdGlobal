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

`Status`: `PENDING` `Depends On`: `DEV-002`
`Acceptance`: `AC-026, AC-008, AC-009, AC-010, AC-027`

**Goal**

Add pure functions to `page_ir_extraction/utils.py`, next to the existing
text-hint quality gate, for
normalization (NFKC, soft-hyphen removal, curly-to-straight quotes and primes,
casefold, line-break hyphen joins counted both ways), content-word collection
from transcribed fields only (skip `ARTIFACT` blocks, `alt_text`,
`embedded_text`, `local_code`, list markers, `text_en`), text-layer usability
(the existing gated `text_hint`, plus 50% coverage when the extraction has at
least 10 distinct content words), and the accept-or-reject decision. In
`extract_page_ir`, run it only when `pdf_page` was supplied and the checker
failed: on rejection, log a `logger.warning` with the 1-based page, word count
and sorted words, and return the extraction agent's PageIR (Decision 4).

**Affected Area**

`backend/src/kgfeg/page_ir_extraction/utils.py`, `page_ir_extraction/llm.py`.
No new module.

**Expected Outcome**

Hints on, usable layer, added unsupported words -> extraction PageIR kept and
terminal warning. Any other case -> correction returned as today. Hints off ->
guard not invoked. No prompt changes. The guard never raises.

**Self-Check**

Existing `tests/kgfeg/page_ir_extraction`; a scratchpad script driving the
decision function over each Decision 4 branch (case, quotes, artifact header,
garbled layer, `None` layer, extraction-kept words, genuinely added words) and
replaying the PDF 43 trial correction from `results/` against the real PDF
text layer.

---

### DEV-005 — Loader documentation for ranges not starting at 0

`Status`: `PENDING` `Depends On`: `NONE`
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

**Implementation Notes**

AC-013's automated tests are Tester-owned and are not written here.

---

### DEV-006 — CAPS runtime config

`Status`: `PENDING` `Depends On`: `DEV-001`
`Acceptance`: `AC-014, AC-015, AC-016, AC-017, AC-028`

**Goal**

In `examples/funda_wande/config_english_curriculum.json` only: set the four
page-stage instruction fields per **CAPS page-stage instructions**, and replace
the Ghana-copy `kgs` block per **CAPS `kgs` block** (metadata, Grade > Term >
Skill Area > Skill hierarchy, policies, `grade_level_mapping` with Grade R ->
`["K"]`, no codes, `included_table_section_patterns` on "requirements per
term", `sfi_extraction_instructions` saying Grade, Term and Skill Area come
from table header rows, and CAPS-specific AS/LC/LP instruction fields). Leave
`start_page`/`end_page` at 27/55.

**Affected Area**

`examples/funda_wande/config_english_curriculum.json`.

**Expected Outcome**

The config loads as a `RunConfig`, has no Ghana, NaCCA or BASIC values, and
carries the required CAPS rules in all four page-stage fields.

**Self-Check**

`RunConfig.model_validate` on the file; grep for Ghana-specific strings;
review against the architecture contract.

---

### DEV-007 — Cycle-wide compatibility and lint self-check

`Status`: `PENDING` `Depends On`: `DEV-001, DEV-002, DEV-003, DEV-004, DEV-005, DEV-006`
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

---

### DEV-008 — Validation reruns on the CAPS PDF (user-run, Developer-checked)

`Status`: `PENDING` `Depends On`: `DEV-007`
`Acceptance`: `AC-021, AC-022, AC-023, AC-024, AC-025`

**Goal**

Developer does not run the paid pipeline. Developer prepares git-ignored
configs derived from the CAPS config that change only `start_page`, `end_page`
and `output_dir` (a fresh `results/` directory per range) for ranges 12-15,
27-55, 109-135 and 59-66, then gives the user the exact extraction,
verification and stitching commands and what to keep from the terminal (guard
warnings). The user runs them and returns. Developer then analyzes the outputs
against the three trial runs for each Goal finding, reports PDF 60-66 on its
own, and records the comparison here (Build Plan step 5).

**Affected Area**

Derived configs and rerun outputs under git-ignored `results/`; possibly CAPS
config wording.

**Expected Outcome**

A recorded before/after comparison in this plan showing new-banner breaks
split (including 110->111, 123->124), continuation and ASSESSMENT pages kept as
stitched table rows, no accepted correction adding off-page words (blocked ones
seen as warnings in the user's terminal output), and the Grade 3 heading in
`section_path` throughout Grade 3. If a miss traces to config wording,
Developer revises the CAPS text and asks the user to rerun only the affected
range. Misses not fixable by config wording are routed to their owner.

**Self-Check**

Scratchpad analysis scripts over the user's rerun outputs and the trial
outputs, plus the guard warning lines the user pastes back.

**Implementation Notes**

The step has two pauses: after handing over the run instructions
(`BlockedOn` waits for the user's runs), and after the analysis, under STEPWISE.

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
