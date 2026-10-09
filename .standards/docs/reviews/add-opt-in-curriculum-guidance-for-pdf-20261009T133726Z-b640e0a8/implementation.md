<!-- STANDARDS
Artifact: REVIEW
Cycle: add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8
ReviewKind: IMPLEMENTATION
-->

# Review Report

`Cycle`: `add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8` `ReviewKind`: `IMPLEMENTATION`
`Status`: `COMPLETE` `User Style`: `NONE`

## Assessed Inputs and Scope

- Cycle mode `STANDARD`, `CompletionPolicy: FULL_DELIVERABLE` (no closure
  assessment needed). Entry: `RESUME` from `TESTING` after the Tester pylint
  C1803 correction; recovery and outstanding obligations inactive.
- Contract (blob prefixes at `HEAD` `40ec02e`): scope
  `.standards/docs/scope/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  `563261fc88`; design
  `.standards/docs/specs/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  `c2b47b19b7`; context `.standards/CONTEXT.md` `7d6abd01b3`; plan
  `.standards/docs/development/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  `59e8f5d045` (`COMPLETE`, `AFTER_IMPLEMENTATION`, `Current Increment: NONE`);
  verification
  `.standards/docs/verification/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
  `451aa97a84` (`COMPLETE`, `FULL`, `NONE`).
- Current ACs: AC-001 to AC-006, AC-008, AC-009, AC-012 to AC-020, AC-022,
  AC-023, AC-025, AC-027 to AC-034. Retired (history only): AC-007, AC-010,
  AC-011, AC-021, AC-024, AC-026.
- Comparison range: `d916660..40ec02e` for `backend/` and `examples/`. Basis:
  `a34c4e6` started the cycle and committed the baseline fix-5 loader line and
  the then-untracked CAPS config; `git diff a34c4e6 d916660 -- backend examples`
  is empty, and the design names `d916660` as the pre-cycle prompt reference.
  Working tree clean apart from this report (`git status --short`).
- Implementation (blob prefixes): `schemas.py` `20bdef2ae5`,
  `entries/extract_page_ir.py` `5442d13d4e`, `page_ir_extraction/llm.py`
  `6da7baf480`, `page_ir_extraction/prompts.py` `631f2e90cd`,
  `page_ir_extraction/utils.py` `2e514befc1`, `page_ir_verification/llm.py`
  `a35853cb21`, `page_ir_verification/prompts.py` `9be7721b73`,
  `page_ir_verification/utils.py` `b7277ac481`,
  `page_ir_verification/verify_page_pairs.py` `73cba3c31b`;
  `examples/funda_wande/config_english_curriculum.json` `c7022a5c3e`
  (identical to `97bee9a`'s config). `backend/src` unchanged since `9cd9cdd`,
  so all four grade reruns ran on the current source.
- Tests (blob prefixes, match the verification report's recorded identities):
  extraction `test_llm.py` `ea59592bf9`, `test_prompts.py` `9b7d77e130`,
  `test_utils.py` `f2fc06098c`; verification `test_llm.py` `653109f22e`,
  `test_prompts.py` `e58cb91da6`, `test_utils.py` `80946738f4`,
  `test_verify_page_irs.py` `075d44f490`; `tests/kgfeg/test_schemas.py`
  `7deaee3b41`.
- Local evidence (git-ignored): `data/funda_wande/caps_english_hl_grade_3_fs.pdf`;
  reruns `results/funda_wande_grade_{r,1,2,3}/`; trial
  `results/funda_wande_trial_p28_55/`.
- Environment: macOS, `backend/.venv` Python 3.13, run from `backend/` with
  `CHAT_ENV=testing LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake PATHS_PROJECT_DIR=<repo>`
  (fake key; no LLM calls).
- Included: every changed backend file and its callers
  (`extract_page_by_page`, `_execute_verification_attempts`,
  `document_ir/utils.py` loader caller), the PageIR schema fields the guard
  scans, the text-hint quality gate, loguru sink setup, the CAPS config, and the
  rerun outputs. Excluded: KG step behavior (non-goal; not run) and unrelated
  modules.
- Session: this conversation began with the Reviewer invocation and holds no
  scope, design, implementation, test or documentation authoring history.
  Client metadata cannot certify session freshness. Reviewer model is Opus 5.5;
  the authoring models are not recorded, so model independence is unknown.

## Contract and Evidence Assessment

| Obligation / criterion | Assessed evidence | Current disposition |
| --- | --- | --- |
| AC-001, AC-004 (Decision 1) | `schemas.py` fields and blank-to-None validators; Tester E1 schema tests; R1, R3 | Supported |
| AC-002 (Decisions 2-3) | Diff of extraction `prompts.py`/`llm.py`/entry point; Tester routing tests; R2 set-path checks | Supported |
| AC-003, AC-006 (TAC: byte identity) | R2: 9 builder comparisons vs `d916660` (omitted and explicit `None`), 0 differ; Tester E3 | Supported |
| AC-005 (TAC: override names table rule) | R2: verifier framing names section B procedure and the UNCERTAINTY POLICY TABLE<->TABLE exception; checker framing names section B (its prompt has no uncertainty policy); `verify_page_pairs.py:370-371` forwards config values; Tester routing tests | Supported |
| AC-029, AC-030 (Decision 4 count rule) | `utils.py:483-555` matches `C > L and C > E`, `L` as `Counter \| Counter` of raw and hyphen-joined layer; Tester unit tests and mutation results (E2); R4 replay | Supported |
| AC-008 | Casefold, quote map, punctuation split, ARTIFACT skip; Tester tests | Supported |
| AC-009 | `text_hint None` and <50% distinct-word coverage both accept; `_extract_text_hint` returns raw, untruncated text; Tester tests | Supported |
| AC-027 | `llm.py:352` gates on `pdf_page is not None`; entry point passes `pdf_page` only when `use_extracted_hints`; prompts built before the guard; Tester hints-off test | Supported |
| AC-032 | R4: replay per design contract rejected with the 8 words; Tester E4 | Supported |
| AC-012, AC-013 | Loader logic, NB note, Raises entry and message inspected; Tester `TestLoadPageIrsFromVerification` | Supported |
| AC-014, AC-015, AC-028 | R3 load; `kgs` inspection (South Africa/DBE metadata, Grade > Term > Skill Area > Skill, no Ghana/NaCCA/Strand/B-code content, `grade_level_mapping["Grade R"] == ["K"]`, `sfi_extraction_instructions` names header rows) | Supported |
| AC-016, AC-017 | Both extraction texts carry rules 1-3; both verification texts carry the banner rule and "48 pages, PDF 36-133"; R5 confirms 48 banner pages PDF 36-133 in the text layer | Supported |
| AC-034 | Committed config: 35/59 in both page stages, `output_dir` `results/funda_wande_grade_r` (not a trial dir) | Supported |
| AC-018 | R6: 0 curriculum-term matches in lines added under `backend/src` and `backend/tests`; Tester E7 | Supported |
| AC-019 | R6: non-CAPS example dirs unchanged since `a34c4e6`; R3: all six load with four fields `None`; R2 + Tester E3 per-config prompt identity | Supported |
| AC-020 | R7 lint on all 17 changed files; R8 full suite; Tester E8 | Supported |
| AC-033 | Plan DEV-008 before/after table; Tester E6; R5 independent rerun analysis with run ranges confirmed from run metadata | Supported |
| AC-022 | R5: 48 breaks into a banner or resources page, 0 marked continuation (incl. PDF 110->111, 123->124) | Supported |
| AC-023 | R5: 48 banner-free continuation pages open with a `resumed`/`both` table; each shares a stitched segment with the previous page | Supported |
| AC-031 | R5: every saved correction (PDF 46, 59, 73, 85, 91, 97, 114, 119) replays as accepted with no added words; warning path covered by Tester orchestration test; user reported no guard warnings | Supported, with limitation L1 |
| AC-025 | R5: Grade 3, all content segments carry the Grade 3 heading; max `section_path` length 2 | Supported |
| Design TAC: guard never raises; constants 0.50 / 10 | `evaluate_page_ir_correction` has no raising path for schema-valid PageIRs; named module constants | Supported |

Developer self-checks (plan DEV-001 to DEV-008), Tester formal evidence (E1-E8)
and Reviewer diagnostics (R1-R8) are distinguished above. Reviewer diagnostics
confirm Tester evidence; they do not replace it.

## Checks and Results

All from `backend/` with the env above, 2026-10-09, on `HEAD` `40ec02e` with a
clean tree. Scripts are session-local in the scratchpad.

- R1: `.venv/bin/python -m pytest -n auto -q -p no:cacheprovider tests/kgfeg/page_ir_extraction tests/kgfeg/page_ir_verification tests/kgfeg/document_ir tests/kgfeg/test_schemas.py`:
  691 passed, exit 0.
- R2: `review_checks.py` (prompt identity): `git show d916660:` copies of both
  prompt modules vs current builders. Extraction: 5 comparisons, 0 differ.
  Verification: 4 comparisons, 0 differ. With a tagged value set, user
  messages are unchanged, the section is appended after the old system
  message, and the text appears once.
- R3: same script, every `examples/**/config*.json` loaded through
  `RunConfig.model_validate` (`pdf_fp`/`output_dir` redirected to a temp dir):
  7 load. CAPS has all four fields set; the six others have all four `None`.
- R4: same script, PDF 43 replay per the design's AC-032 contract
  (`0042.val00.attempt00.parsed.json`, the only attempt; `page_irs/0042.json`;
  `_extract_text_hint` on page_index 42): `accepted=False`, added words
  `assessment, for, informal, observation, or, oral, practical, suggestions`.
- R5: `review_runs.py`, independent of `logs/dev008/` and Tester scripts.
  Ground truth from the PDF text layer: 48 banner pages PDF 36-133; resources
  pages PDF 59, 84, 109, 135. Run ranges and `use_extracted_hints` confirmed
  from `extraction_run.json`/`verification_run.json`; Grades 1-3 instruction
  texts equal the `HEAD` config, Grade R's predate wording revision 1. Per
  grade (new-table breaks wrong / continuation breaks wrong / loose pages /
  unjoined / content segments without grade heading / guard rejects on saved
  corrections): R 0 of 12 / 0 of 11 / 0 / 0 / 0 / 0 of 2; G1 0 of 12 / 0 of
  12 / 0 / 0 / 0 / 0 of 1; G2 0 of 12 / 0 of 12 / 0 / 0 / 0 / 0 of 3; G3 0 of
  12 / 0 of 13 / 0 / 0 / 0 / 0 of 2. Max `section_path` length 1-2.
- R6: `git diff --quiet a34c4e6 HEAD -- examples/ghana examples/india examples/nigeria examples/rwanda`
  (unchanged); case-insensitive whole-word scan of added lines in
  `git diff d916660 HEAD -- backend/src backend/tests` for caps, funda, wande,
  south africa, ghana, ghanaian, nigeria, rwanda, india, madhi, pratham, nacca,
  requirements per term, foundation phase, home language, grade r: 0. `kgs`
  block scan for Ghana-era terms: only false positives ("Twice",
  "primary_language").
- R7: check-only lint on the 17 changed files: `isort --check-only` clean,
  `black --check` 17 unchanged, `ruff check` passed, `interrogate` 100%,
  `mypy` no issues, `pylint` 10.00/10. Tree unchanged afterwards (badge file
  rewritten with identical content).
- R8: full suite `.venv/bin/python -m pytest -n auto -q -p no:cacheprovider -m "not alembic" tests`:
  3847 passed, 0 failed, exit 0 (matches Tester E8's count). Slow tests not
  included (CI default).
- Inspection: guard scans exactly `Block.text`, `ListItem.text`,
  `FigureUnit.caption`, `TableCell.text`, skipping ARTIFACT blocks; synthetic
  and placeholder cells are post-process only, so they cannot inflate `C` at
  guard time. Guard warning uses loguru, whose configured sink is stderr
  (`utils/logging_.py:207-240`), satisfying terminal-only output.

## Findings

No material findings.

## Questions, Limitations, and Later Dependencies

- L1 (non-material): checker verdicts are not persisted, so a blocked
  correction leaves no file trace; AC-031's "reported as a warning" half rests
  on the unit-tested warning path plus the user's report of no warnings. The
  "no accepted correction adds words" half is fully evidenced by R5. Terminal-
  only output is the user's recorded decision.
- L2 (non-material): Grade R ran with the CAPS wording before revision 1, so
  the committed config (revision 1) was never run on Grade R. Revision 1 only
  adds an exception for the "RECOMMENDED TEXTS/RESOURCES" box, which Grade R's
  PDF 58->59 break already handled correctly, and Grades 1-3 exercised it. The
  user accepted this (plan DEV-008).
- L3 (non-material): the KG step was not run (non-goal), so the CAPS `kgs`
  block (AC-014, AC-015, AC-028) rests on loading and inspection, as scoped.
- Observations, not findings: the plan's `Implementation Contract.Request`
  line still says "trial ranges plus PDF 60-66", and the design's Context says
  "AC-001 to AC-025". Both are stale summary lines; the authoritative scope,
  design decisions and DEV-008 body are current, and no evidence depends on
  them.
- Later dependencies: NONE. No blocking user question.

## Progress and Conclusion

- Completed: every current AC and design TAC assessed against current content;
  R1-R8 run. Immediately before completion, `HEAD` was still `40ec02e` with a
  clean tree apart from this report, so assessed inputs are current.
- Gate: passes. No material findings, no material assessment gap, no later
  dependency, no blocking question, and no obligation owned by this state.
  Developer and Tester full gates hold for the current implementation (plan
  `COMPLETE`; verification `COMPLETE`, `FULL`, `NONE`).
- Plain-language summary: the work can move forward. The four new optional
  settings change nothing for the other countries' configs, the guard blocks
  the PDF 43 bad correction and nothing else seen in the reruns, and all four
  grade reruns now split and join the CAPS tables correctly, with the grade
  kept on every Grade 3 segment. Not validated: the KG step on the new CAPS
  `kgs` block (out of scope), and a live blocked-correction warning (none
  occurred; the warning path is unit-tested). Passing this review is not the
  end of the cycle: documentation, final review and synchronization remain.
- Handoff: `FORWARD` to `DOCUMENTING` (Documenter), `FULL_DELIVERABLE`.
