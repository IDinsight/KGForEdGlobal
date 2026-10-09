<!-- STANDARDS
Artifact: SYNCHRONIZATION
Cycle: add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8
-->

# Synchronization Record

`Cycle`: `add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8` `Status`: `COMPLETE`
`User Style`: `NONE`

## Assessed Inputs

- Workflow context: `STANDARD`, `CompletionPolicy: FULL_DELIVERABLE`,
  `ProjectMode: BROWNFIELD`. Entry: `FORWARD` from `REVIEWING_FINAL`. Recovery
  and outstanding obligations inactive; `BlockedOn`, `BaselineReconciliation`,
  `PromotionReason` and `PendingVerificationCadence` all `NONE`. No user style
  was selected for this role.
- Checked at `HEAD` `aae4a60` with a clean tree before this record was
  created. Content identities below are `git hash-object` prefixes of the
  current files. Every one equals the identity recorded by the final review,
  and, where they recorded it, the verification report, implementation review
  and documentation record.
  - Contract and context: scope
    `.standards/docs/scope/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `563261fc88`; design
    `.standards/docs/specs/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `c2b47b19b7`; `.standards/CONTEXT.md` `7d6abd01b3`.
  - Owner records, each with matching `SCOPE`/`ARCHITECTURE`/`DEVELOPMENT`/
    `VERIFICATION`/`REVIEW`/`DOCUMENTATION` provenance for this cycle and, for
    reviews, a `ReviewKind` matching its path: plan
    `.standards/docs/development/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `59e8f5d045` (`COMPLETE`, `AFTER_IMPLEMENTATION`, `Current Increment:
    NONE`, DEV-001 to DEV-008 `DONE`); verification
    `.standards/docs/verification/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `451aa97a84` (`COMPLETE`, `FULL`, `NONE`); implementation review
    `.standards/docs/reviews/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8/implementation.md`
    `e65f2db4f7` (`COMPLETE`, `IMPLEMENTATION`); documentation record
    `.standards/docs/documentation/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
    `acda225cda` (`COMPLETE`); final review
    `.standards/docs/reviews/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8/final-deliverable.md`
    `a89b68b5cf` (`COMPLETE`, `FINAL_DELIVERABLE`, committed in `aae4a60`).
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
  - Documentation: `docs/pipeline/page-ir-extraction.md` `aea9d64c26`,
    `docs/pipeline/page-ir-verification.md` `dcdc17bbe2`,
    `docs/guides/adding-a-curriculum.md` `2699263a1f`,
    `docs/guides/running-and-debugging.md` `31cda5f050`, `docs/architecture.md`
    `ff2185f3a5`.
- Comparison range: `d916660..aae4a60`. Basis: `a34c4e6` started the cycle and
  committed the pre-cycle loader line and the then-untracked CAPS config;
  `git diff --quiet a34c4e6 d916660 -- backend examples` succeeds (no change),
  and the design names `d916660` as the pre-cycle prompt reference. Files
  changed in the range outside `.standards/docs/`: the 9 backend source files,
  8 test files and 5 docs above, the CAPS config, `.standards/STATE.md`,
  `.secrets.baseline` and `.standards/user-styles/scoper/tony.md`. No deleted
  or moved files. Since the final review's assessed `HEAD` `949d7f5`, only the
  final-review report and `STATE.md` changed (`git diff --stat 949d7f5 HEAD`).
- Unrelated or non-deliverable changes, kept out of the assessment:
  `.standards/user-styles/scoper/tony.md` (user-owned style file).
  `.secrets.baseline` (detect-secrets baseline): commit `9cd9cdd` removed the
  one entry for the CAPS config, a "Base64 High Entropy String" on the old
  `output_dir` path line (the trial-run path), after DEV-006 changed that
  value; `43b8505` only shifted that entry's line number and added a filter
  entry. A false positive on a path, not a credential; it follows from the
  AC-034 config change and affects no acceptance condition.
- Dependencies and configuration: `backend/pyproject.toml`, `backend/uv.lock`,
  `cicd/` and `mkdocs.yml` are unchanged since `d916660`, so the owners'
  execution assumptions (Python 3.13 `backend/.venv`, test env, MkDocs build)
  still hold.
- Local evidence (git-ignored): reruns `results/funda_wande_grade_{r,1,2,3}/`
  exist; their newest files date from 12:42 to 14:06 local on 2026-10-09,
  before the implementation and final review commits, so the outputs the
  reviewers analyzed are unchanged. Trial
  `results/funda_wande_trial_p28_55/` and `results/funda_wande_trial_p110_135/`
  and the CAPS PDF are referenced through owner evidence and were not
  re-analyzed here.
- This record and the `STATE.md` transition are Synchronizer writes and legal
  coordination, not deliverable inputs.

## Completion and Evidence References

Current ACs (28): AC-001 to AC-006, AC-008, AC-009, AC-012 to AC-020, AC-022,
AC-023, AC-025, AC-027 to AC-034. Retired (history only): AC-007, AC-010,
AC-011, AC-021, AC-024, AC-026. The inventory is the same in the scope, the
verification report, both reviews and the documentation record. Every current
AC has a design disposition under the design's Acceptance Coverage (AC-013 and
AC-020 are explicit no-architectural-impact dispositions).

All evidence below applies because its inputs are byte-identical to the
current files (identities above).

- AC-001, AC-004 (Decision 1): verification E1;
  implementation review R1, R3; final review FR3; documentation settings rows
  (`docs/pipeline/page-ir-extraction.md`, `docs/pipeline/page-ir-verification.md`).
- AC-002, AC-005 (Decisions 2-3; TAC on placement and routing): E1, E2;
  R2; FR2; documentation subsections, checked claim by claim in FR9.
- AC-003, AC-006, AC-019 (TAC: byte identity with `d916660` builders): E3; R2,
  R3, R6; FR1, FR3. Non-CAPS example directories unchanged since `d916660`
  (rechecked here).
- AC-029, AC-030, AC-008, AC-009, AC-027 (Decision 4 and its TAC): E1, E2
  (unit and mutation evidence); R4 and inspection; FR4, FR9; documentation
  "Correction guard" subsection.
- AC-032 (replay contract): E4; R4; FR4. All three replays report the same 8
  words.
- AC-012, AC-013 (Decision 5; TAC): E1, E2; implementation review inspection;
  FR5; documentation range text in `docs/guides/running-and-debugging.md` and
  `docs/guides/adding-a-curriculum.md`.
- AC-014, AC-015, AC-016, AC-017, AC-028, AC-034 (Decision 6; CAPS contracts;
  TACs): E5, E6 (48 banner pages PDF 36-133 from the text layer); R3, R5, R6;
  FR3, FR6. Rechecked here on the committed config: range 35/59 in both page
  stages, `output_dir` ends in `funda_wande_grade_r`, all four fields set,
  `grade_level_mapping["Grade R"] == ["K"]`.
- AC-018: E7; R6; final review (reused, backend unchanged). Rechecked here:
  0 matches.
- AC-020: E8 (suite and lint); R7, R8; FR7. Lint not rerun after E8/R7
  because no backend file changed afterwards.
- AC-033, AC-022, AC-023, AC-025, AC-031 (validation reruns): plan DEV-008
  before/after table; E6; R5; FR6. AC-031's warning half carries limitation L1
  (below).
- Final review applicability: it assessed `949d7f5` with the identities above.
  The only later change is its own report and `STATE.md`. Its conclusion (no
  material findings, no later dependency, gate passed) applies to the current
  deliverable. Implementation review (`40ec02e`) and verification remain
  applicable: `backend/` and `examples/` are byte-identical to what they
  assessed, and the final review rechecked those conclusions on the assembled
  work.
- Documentation evidence (Synchronization Gate interface): the documentation
  record names the five documents with saved identities that match current
  content, its checks C1-C5 and results, and the ACs each document covers, with
  no-change dispositions for the rest. FR8-FR10 independently confirm it.
- Later-role dependencies: none were recorded by Tester or either Reviewer.
  Nothing pending to resolve.

Reconciliation checks performed for this record, 2026-10-09, macOS:

- S1: `node .standards/bin/check.mjs` from the repo root: "STANDARDS check
  passed.", exit 0 (before work).
- S2: `git status --short` (clean) and `git hash-object` on every file listed
  under Assessed Inputs: all identities match the owner records.
- S3: `git diff --quiet a34c4e6 d916660 -- backend examples`,
  `git diff --quiet d916660 HEAD -- examples/{ghana,india,nigeria,rwanda}` and
  the same for dependency and MkDocs config: all unchanged.
- S4: `.secrets.baseline` history (`git log` and `git show` of `43b8505` and
  `9cd9cdd`) and the key name on the flagged config line at each commit (key
  names only, no values printed): `output_dir`. A scan of the CAPS config for
  credential-like key names found only tuning parameters.
- S5: Python read of the committed CAPS config (AC-014 to AC-017, AC-028,
  AC-034 facts above). Extraction texts carry the banner phrase; both
  verification texts also carry "48" and "PDF 36-133".
- S6: AC-018 whole-word, case-insensitive scan of added lines in
  `git diff -U0 d916660 HEAD -- backend/src backend/tests` for curriculum
  names and "requirements per term", "foundation phase", "home language": 0.
- S7: from `backend/`, with `CHAT_ENV=testing
  LEARNING_COMMONS_EXPORT_SCHEMA_VERSION=2026-07-09 OPENAI_API_KEY=sk-fake
  PATHS_PROJECT_DIR=<repo> TERM=dumb` (fake key, no LLM calls):
  `.venv/bin/python -m pytest -n auto -q -p no:cacheprovider tests/kgfeg/page_ir_extraction tests/kgfeg/page_ir_verification tests/kgfeg/document_ir tests/kgfeg/test_schemas.py`:
  691 passed, exit 0 (equal to R1). This confirms the environment, not new
  formal verification.
- S8: rerun output directories exist; newest file times precede both review
  commits.

Not rerun here (owner evidence reused because inputs are unchanged): full
suite, lint, MkDocs strict build, prompt-identity comparisons, PDF 43 replay
and the rerun analysis.

## Discrepancies and Dispositions

NONE. No inconsistency was found between the deliverable, the completion
claims and the evidence.

## Limitations and Remaining Work

Remaining work: NONE. Blocking question: NONE.

Non-blocking limits, each already recorded by its owner or a reviewer:

- L1: checker verdicts are not persisted, so a blocked correction leaves no
  file trace. AC-031's warning half rests on the unit-tested warning path and
  the user's report of no warnings in all four runs. Terminal-only output is
  the user's recorded decision (scope Non-goals, AC-029).
- L2: Grade R ran with the CAPS wording before revision 1; the user accepted
  this (plan DEV-008). Grades 1-3 exercised revision 1.
- L3: the KG step was not run (scope non-goal), so the CAPS `kgs` block
  (AC-014, AC-015, AC-028) rests on loading and inspection, as scoped.
- Stale wording that no evidence depends on, so it does not affect whether the
  evidence applies: the plan's `Implementation Contract.Request` line ("trial
  ranges plus PDF 60-66") and the design's Context ("AC-001 to AC-025"), noted
  by both reviews; `docs/architecture.md:9-12` lists Ghana, Nigeria, Rwanda and
  India as the example curricula but not South Africa (final review O1). The
  sentence is incomplete, not wrong, and no AC covers it. The user may add
  South Africa in a later change.
- `.standards/CONTEXT.md` still says the CAPS config is untracked and the
  loader line uncommitted. That was true at audit; `a34c4e6` committed the same
  content (scope Assumptions). Active-cycle work does not make context stale,
  and the next Auditor refresh covers it.
- The final review describes the `.secrets.baseline` change as pruning entries
  "for two files that no longer exist". In the comparison range it removed one
  entry, for a string in the CAPS config that no longer exists (S4). The
  review's conclusion is unaffected: the change carries no deliverable or
  acceptance impact.
- Session and model independence of the reviews cannot be certified by client
  metadata (stated in both review reports).

## Resume and Synchronization Conclusion

The work can proceed to user sign-off. The current deliverable is the content
that Tester, the implementation review, Documenter and the final review
assessed, byte for byte. Every current AC and design TAC has current evidence,
no discrepancy or dependency is open, and recovery and obligations are
inactive. The full Synchronizer gate passed, and the Standard Cycle Completion
requirements for `FULL_DELIVERABLE` hold. No recovery return applies.

Next action: none for Synchronizer. The user decides to sign off, rework, or
cancel. On resume or at sign-off, recompute the identities above. Any change to
`backend/`, `examples/`, `docs/` or the contract records needs reassessment by
its owners. Readiness is not user acceptance.
