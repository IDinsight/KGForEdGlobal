<!-- STANDARDS
Artifact: REVIEW
Cycle: add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8
ReviewKind: FINAL_DELIVERABLE
-->

# Review Report

`Cycle`: `add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8` `ReviewKind`: `FINAL_DELIVERABLE`
`Status`: `COMPLETE` `User Style`: `NONE`

## Assessed Inputs and Scope

- Cycle mode `STANDARD`, `CompletionPolicy: FULL_DELIVERABLE`. Entry: `FORWARD`
  from `DOCUMENTING`; recovery and outstanding obligations inactive;
  `BlockedOn: NONE`; `BaselineReconciliation: NONE`;
  `PendingVerificationCadence: NONE`.
- Assessed at `HEAD` `949d7f5` with a clean tree apart from this report. Content
  identities (blob prefixes, all equal to the identities recorded by the
  verification report, implementation review and documentation record):
  - Contract: scope
    `.standards/docs/scope/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `563261fc88`; design
    `.standards/docs/specs/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `c2b47b19b7`; context `.standards/CONTEXT.md` `7d6abd01b3`.
  - Owner records: plan
    `.standards/docs/development/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `59e8f5d045` (`COMPLETE`, `AFTER_IMPLEMENTATION`, `Current Increment: NONE`);
    verification
    `.standards/docs/verification/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `451aa97a84` (`COMPLETE`, `FULL`, `NONE`); implementation review
    `.standards/docs/reviews/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8/implementation.md`
    `e65f2db4f7` (`COMPLETE`, no findings); documentation record
    `.standards/docs/documentation/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `acda225cda` (`COMPLETE`).
  - Implementation: `schemas.py` `20bdef2ae5`, `entries/extract_page_ir.py`
    `5442d13d4e`, `page_ir_extraction/llm.py` `6da7baf480`,
    `page_ir_extraction/prompts.py` `631f2e90cd`, `page_ir_extraction/utils.py`
    `2e514befc1`, `page_ir_verification/llm.py` `a35853cb21`,
    `page_ir_verification/prompts.py` `9be7721b73`,
    `page_ir_verification/utils.py` `b7277ac481`,
    `page_ir_verification/verify_page_pairs.py` `73cba3c31b`; CAPS config
    `examples/funda_wande/config_english_curriculum.json` `c7022a5c3e`.
  - Tests: extraction `test_llm.py` `ea59592bf9`, `test_prompts.py`
    `9b7d77e130`, `test_utils.py` `f2fc06098c`; verification `test_llm.py`
    `653109f22e`, `test_prompts.py` `e58cb91da6`, `test_utils.py` `80946738f4`,
    `test_verify_page_irs.py` `075d44f490`; `tests/kgfeg/test_schemas.py`
    `7deaee3b41`.
  - Documentation (commit `949d7f5`): `docs/pipeline/page-ir-extraction.md`
    `aea9d64c26`, `docs/pipeline/page-ir-verification.md` `dcdc17bbe2`,
    `docs/guides/adding-a-curriculum.md` `2699263a1f`,
    `docs/guides/running-and-debugging.md` `31cda5f050`, `docs/architecture.md`
    `ff2185f3a5`.
- Comparison range: `d916660..949d7f5` for `backend/`, `examples/` and `docs/`
  (`a34c4e6` started the cycle and committed the baseline loader line and CAPS
  config; `git diff a34c4e6 d916660 -- backend examples` is empty, per the
  implementation review). Since the implementation review (`68e0b2c`) the only
  changes are `docs/` (5 files), the documentation record and `STATE.md`:
  `backend/` and `examples/` are byte-identical to what Tester and the
  implementation review assessed. Other cycle-range changes: `.secrets.baseline`
  (detect-secrets hook pruned entries for two files that no longer exist;
  unrelated housekeeping) and `.standards/user-styles/*/tony.md` (user-owned).
- Current ACs (28): AC-001 to AC-006, AC-008, AC-009, AC-012 to AC-020, AC-022,
  AC-023, AC-025, AC-027 to AC-034. Retired (history only): AC-007, AC-010,
  AC-011, AC-021, AC-024, AC-026.
- Local evidence (git-ignored): `data/funda_wande/caps_english_hl_grade_3_fs.pdf`;
  reruns `results/funda_wande_grade_{r,1,2,3}/` (newest files 12:42-14:06 local
  on 2026-10-09, before the implementation review commit at 17:21, so unchanged
  since R5/E6); trial `results/funda_wande_trial_p28_55/`.
- Environment: macOS, `backend/.venv` Python 3.13, from `backend/` with
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo>`
  (fake key; no LLM calls).
- Included: every documentation claim added this cycle, traced to code; other
  project docs for stale range or example-list statements; the implementation,
  CAPS config and rerun outputs re-examined against the implementation
  review's conclusions. Excluded: KG step behavior (non-goal; not run).
- Session: this conversation began with the Reviewer invocation and holds no
  scope, design, implementation, test or documentation authoring history.
  Client metadata cannot certify freshness. Reviewer model is Opus 5.5; the
  authoring models are not recorded, so model independence is unknown.

## Contract and Evidence Assessment

| Obligation / criterion | Assessed evidence | Current disposition |
| --- | --- | --- |
| AC-001, AC-004 (Decision 1) | Tester E1 schema tests; FR3 (blank and whitespace map to `None`, unknown key rejected, all configs load); docs settings rows | Supported |
| AC-002, AC-005 (Decisions 2-3, TAC placement and override framing) | Tester E1/E2 routing tests; FR2 (section appended last, user message unchanged, verifier framing names section B and the UNCERTAINTY POLICY exception, checker names section B only); docs subsections match the framing | Supported |
| AC-003, AC-006 (TAC byte identity) | FR1: 10 comparisons vs `d916660` builders, 0 differ; Tester E3; review R2 | Supported |
| AC-029, AC-030, AC-008, AC-009 (Decision 4) | Code inspection of `page_ir_extraction/utils.py` guard; Tester E1/E2 unit and mutation evidence; docs "Correction guard" subsection checked claim by claim | Supported |
| AC-027 | `llm.py` guard gated on `pdf_page is not None`; entry point passes `pdf_page` only when `use_extracted_hints`; Tester hints-off test; docs state the gating | Supported |
| AC-032 | FR4 replay per design contract: rejected, 8 words `assessment, for, informal, observation, or, oral, practical, suggestions`; Tester E4; review R4 | Supported |
| AC-012, AC-013 | Loader code, NB note, Raises entry and message inspected; Tester `TestLoadPageIrsFromVerification`; FR5; docs range text matches `load_page_irs_from_verification` and `cross_check_verification_run` | Supported |
| AC-014, AC-015, AC-028 | CAPS `kgs` inspection: South Africa/DBE metadata, Grade R-3, 0 Ghana/NaCCA/BASIC/Strand/B-code strings, `sfi_extraction_instructions` names header rows, `grade_level_mapping["Grade R"] == ["K"]`; FR3 load | Supported |
| AC-016, AC-017 | Both extraction texts carry rules 1-3; both verification texts carry the banner rule and "48 pages, PDF 36-133"; FR6 ground truth 48 banner pages PDF 36-133 | Supported |
| AC-034 | Committed config 35/59 in both page stages; `output_dir` `results/funda_wande_grade_r`, not a trial directory | Supported |
| AC-018 | Backend source and tests unchanged since Tester E7 and review R6 (0 curriculum terms in added lines); documentation commit touched no backend file | Supported (reused, inputs unchanged) |
| AC-019 | Non-CAPS example dirs unchanged since `d916660`; FR3 all six load with four fields `None`; FR1 builder identity; Tester E3 per-config identity | Supported |
| AC-020 | FR7 full suite on current content; Tester E8 and review R7 lint on unchanged source and tests | Supported (lint reused, inputs unchanged) |
| AC-033 | Plan DEV-008 before/after table; Tester E6; review R5; FR6 re-tally on unchanged outputs | Supported |
| AC-022 | FR6: 48 breaks into a banner or resources page, 0 marked continuation (includes page_index 109->110 and 122->123, PDF 110->111 and 123->124) | Supported |
| AC-023 | FR6: 48 breaks into banner-free continuation pages all `table` continuations; 0 such pages left out of the previous page's stitched segment | Supported |
| AC-031 | Saved corrections replay with no added words (Tester E6, review R5); user reported no guard warnings; terminal-only by decision | Supported, limitation L1 |
| AC-025 | FR6: Grade 3 content segments all carry "3.4 GRADE 3"; max `section_path` length 2 | Supported |
| Design TAC: guard never raises; constants 0.50 / 10 | Code inspection; documented in plain words on the extraction page | Supported |
| Documentation accuracy (Documenter gate, FULL_DELIVERABLE) | FR8-FR10 below: every new claim traced to code; strict build and anchors pass | Supported, observation O1 |

Developer self-checks, Tester formal evidence (E1-E8), implementation-review
diagnostics (R1-R8), Documenter checks (C1-C5) and these final-review
diagnostics (FR1-FR10) are kept distinct. FR results confirm owner evidence;
they do not replace it.

## Checks and Results

All on `HEAD` `949d7f5`, clean tree apart from this report, 2026-10-09.
Scripts are session-local in the scratchpad (`fr/fr_checks.py`,
`fr/fr_runs.py`) and described here so they can be rebuilt.

- FR1 prompt identity (`fr_checks.py`, from `backend/` with the env above):
  `git show d916660:` copies of both prompt modules vs current builders, two
  extraction inputs (no hints; text and table hints), one extraction-checker,
  one verifier and one continuity-checker input, each with
  `curriculum_instructions` omitted and `None`: 10 comparisons, 0 differ.
- FR2 set path (same script): for all four builders a tagged value leaves the
  user message unchanged, the system message equals the old one plus
  `\n\n## RUNTIME CURRICULUM INSTRUCTIONS\n...` ending in
  `</curriculum_instructions>`, the text appears once; verification framing
  names "DECISION PROCEDURE in section B (Steps 1-4)", and only the verifier's
  names the UNCERTAINTY POLICY. Result: ok.
- FR3 config (same script): all 7 `examples/**/config*.json` load via
  `RunConfig.model_validate` (`pdf_fp`/`output_dir` redirected to a temp dir);
  CAPS has all four fields set, the six others all `None`. On the CAPS config,
  `""` and whitespace for each of the four fields load as `None`; an unknown
  `page_ir_extraction` key is rejected.
- FR4 PDF 43 replay (same script; `0042.val00.attempt00.parsed.json`, the only
  attempt; `page_irs/0042.json`; `_extract_text_hint` on page_index 42):
  `accepted=False`, added words as in the table above. Exit 0.
- FR5 loader: `load_page_irs_from_verification` and
  `cross_check_verification_run` read in full; contiguity check uses the first
  index, duplicates and gaps raise.
- FR6 reruns (`fr_runs.py`, read-only, independent of `logs/dev008/` and earlier
  review scripts): ground truth from the PDF text layer, 48 banner pages PDF
  36-133; resources pages page_index 58, 83, 108, 134. Run ranges read from each
  `verification_run.json`: 35-59, 59-84, 84-109, 109-135. Per grade
  (banner/resources breaks wrong / continuation breaks wrong / continuation
  pages not sharing a segment with the previous page / content segments
  without the grade heading / max path): R 0 of 12 / 0 of 11 / 0 / 0 of 13 / 1;
  G1 0 of 12 / 0 of 12 / 0 / 0 of 13 / 2; G2 0 of 12 / 0 of 12 / 0 / 0 of 13 /
  2; G3 0 of 12 / 0 of 13 / 0 / 0 of 13 / 2.
- FR7 full suite: `.venv/bin/python -m pytest -n auto -q -p no:cacheprovider -m "not alembic" tests`
  from `backend/` with the env above and `TERM=dumb`: 3847 passed, 0 failed,
  79 warnings, 178 s, exit 0 (matches Tester E8 and review R8 counts). Slow
  tests not included (CI default). Lint was not rerun: no backend file changed
  since Tester E8 and review R7, which ran the lint suite on the same content.
- FR8 docs build: `backend/.venv/bin/mkdocs build --strict -f mkdocs.yml -d "$TMPDIR/fr_site"`
  from the repo root: exit 0. Anchors `#document-specific-instructions` (both
  stage pages), `#correction-guard` and `#page-ranges-and-calibration-runs`
  exist once each; the new links in `guides/adding-a-curriculum.html` and
  `pipeline/page-ir-extraction.html` resolve to them. `git diff --check
  d916660 HEAD -- docs`: clean.
- FR9 claim tracing (inspection). Extraction page: field routing
  (`entries/extract_page_ir.py:110-121`, `llm.py`), section at the end of the
  system message with the output-contract exception (`prompts.py`
  `_append_curriculum_instructions`), blank equals unset (schema validators,
  FR3), existing PageIRs skipped unless `overwrite` (`extract_page_by_page`),
  guard gating, count rule, scanned and skipped fields, case and quote
  handling, pure numbers and single characters, usable-layer rule and its
  10-word/50% thresholds (`utils.py` `evaluate_page_ir_correction`), warning
  text, stderr-only sink (`utils/logging_.py`: file sink only with `log_fp`, no
  caller passes it; `logfire.configure` is commented out), verdicts not
  persisted. Verification page: routing and framing (`page_ir_verification/
  prompts.py`), the paraphrase of section B Steps 1-4 and the in-grid content
  sentence, pair reports reloaded unless `overwrite`
  (`entries/verify_page_ir_continuity.py:108-125`). Architecture page: Stage 1
  and Stage 2 paragraphs match the same code. Running guide and curriculum
  guide: stitcher reads `<output_dir>/<doc_key>/verification/page_irs_verified/`
  from `page_ir_extraction.output_dir` (`entries/stitch_document_ir.py:206-231`),
  loads every JSON there, and requires consecutive, duplicate-free indexes from
  any start (`document_ir/utils.py:341-440`); South Africa is the only example
  with page-stage instruction fields (FR3). All claims hold.
- FR10 stale-statement search over `docs/`, `README.md`, `mkdocs.yml`,
  `AGENTS.md` for start-at-0, cropped-PDF and example-list wording: one stale
  statement (O1); no remaining start-at-0 or cropped-PDF guidance.
- `node .standards/bin/check.mjs`: passed before review work.

## Findings

No material findings.

## Questions, Limitations, and Later Dependencies

- L1 (non-material, carried from verification and implementation review):
  checker verdicts are not persisted, so a blocked correction leaves no file
  trace; AC-031's warning half rests on the unit-tested warning path plus the
  user's report of no warnings in all four runs. Terminal-only output is the
  user's recorded decision, and the extraction page now tells operators to keep
  terminal output if they need a record.
- L2 (non-material): Grade R ran with the CAPS wording before revision 1;
  revision 1 only adds the resources-box exception, which Grades 1-3 exercised
  and Grade R handled anyway. User-accepted (plan DEV-008).
- L3 (non-material): the KG step was not run (non-goal), so the CAPS `kgs`
  block (AC-014, AC-015, AC-028) rests on loading and inspection, as scoped. The
  curriculum guide says so.
- O1 (observation, not a finding): `docs/architecture.md:9-12` still says the
  example configurations "cover curricula from Ghana, Nigeria, Rwanda, and
  India", while `docs/guides/adding-a-curriculum.md:28` now lists South Africa
  too. The sentence is incomplete, not wrong about behavior; the guide that
  profile authors use and `README.md` (which points to `examples/`) are
  accurate, and no AC or documented behavior depends on it. It does not block
  this gate; Documenter or the user may add "South Africa" in a later change.
- Observations carried from the implementation review (stale summary lines in
  the plan's `Implementation Contract.Request` and the design's Context AC
  range) remain workflow-record wording only; no evidence depends on them.
- `.standards/CONTEXT.md` still describes the pre-cycle baseline (untracked CAPS
  config, uncommitted loader line). That is expected for active-cycle work and
  is Synchronizer/Auditor reconciliation context, not a final-review defect.
- Later dependencies: NONE. No blocking user question.

## Progress and Conclusion

- Completed: every current AC, design TAC and documentation claim assessed
  against current content; FR1-FR10 run. Prior implementation-review
  conclusions rechecked against unchanged inputs and reconfirmed.
- Immediately before completion, `HEAD` was still `949d7f5` with a clean tree
  apart from this report, so assessed inputs are current.
- Gate: passes. No material findings, no material assessment gap, no later
  dependency, no blocking question, and no obligation owned by this state.
  Every current AC and relevant technical criterion has sufficient current
  evidence. Developer, Tester, implementation Reviewer and Documenter full
  gates hold for the current content (plan `COMPLETE`; verification
  `COMPLETE`, `FULL`, `NONE`; implementation review `COMPLETE`; documentation
  record `COMPLETE`, with its saved identities matching `HEAD`).
- Plain-language summary: the work can move forward. The finished deliverable
  holds together: the code still does what was reviewed, the new docs describe
  it correctly (every claim was checked against the code), and the docs site
  builds with working links. The test suite passes, the PDF 43 bad correction
  is still blocked, and all four grade reruns still show tables split and
  joined correctly with the grade kept on every segment. One small gap: the
  architecture overview's list of example countries leaves out South Africa;
  it is harmless and can be fixed later. Not validated: the KG step on the new
  CAPS settings (out of scope) and a live blocked-correction warning (none
  occurred). Passing this review does not finish the cycle: synchronization and
  your sign-off remain.
- Handoff: `FORWARD` to `SYNCHRONIZING` (Synchronizer), `FULL_DELIVERABLE`.
