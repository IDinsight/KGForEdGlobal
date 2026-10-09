# S.T.A.N.D.A.R.D.S. Workflow State

`WorkflowState`: `DEVELOPING` `CycleMode`: `STANDARD` `PendingCycleMode`: `UNSET`
`PendingCycleRequest`: `UNSET` `PendingCycleBlockedOn`: `NONE`

## Active Work

`CompletionPolicy`: `FULL_DELIVERABLE` `Id`:
`add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8` `Request`:
`Add generic, opt-in support for curriculum-specific guidance in PDF page
extraction and page-continuity verification, plus a generic guard against
hallucinated extraction corrections; then apply it to the South Africa CAPS
English Home Language R-3 PDF (Funda Wande) through that curriculum's runtime
config only. CONSTRAINTS: no code may name CAPS or any curriculum (curriculum
details live only in runtime config); with the new fields unset every agent
prompt stays the same and the Ghana, India, Nigeria and Rwanda example configs
behave exactly as today (run metadata files such as extraction.json and
verification_run.json saving the whole config, so new unset fields appear in
them, is acceptable). FIXES: (1) page_ir_extraction: optional
curriculum-instruction fields injected into both the extraction agent and its
validation (checker) agent, with an explicit rule that they override the
generic rules where they conflict; follow the kgs.lc.lc_dedup_instructions
pattern (backend/src/kgfeg/schemas.py:2495, backend/src/kgfeg/kgs/prompts.py:901);
the checker needs them because its corrected PageIR replaces the extraction
(page_ir_extraction/llm.py:328). (2) page_ir_verification: same kind of fields
for the continuity verifier and its checker (the checker's corrected verdict
replaces the verifier's, page_ir_verification/llm.py:389); they must be able to
override the generic table-to-table rule (page_ir_verification/prompts.py:273-286)
that treats skill-area changes inside a grid as continuations. (3)
page_ir_extraction generic guard: do not accept a checker correction that adds
content words missing from the page's PDF text layer when a usable text layer
exists; tolerate missing or garbled text layers, display-case differences,
curly vs straight quotes, and running headers; block the bad correction and log
a warning. (4) KG step, config only: in the CAPS config use
kgs.as.sfi_extraction_instructions to say Term and skill area come from each
table's header rows. (5) page_ir_verification: keep the uncommitted change in
load_page_irs_from_verification (backend/src/kgfeg/page_ir_verification/utils.py:541-542)
that accepts a gap-free page range not starting at 0; update its docstring and
error message; add tests that a range starting above 0 passes and a range with
a gap fails. (6) Author the CAPS kgs block in this cycle (still a copy of
Ghana's; fix 4 depends on it). Each agent gets its own instruction field. CAPS
CONFIG CONTENT (examples/funda_wande/config_english_curriculum.json):
extraction - a bordered box without the REQUIREMENTS PER TERM banner continues
the previous page's table as rows, including pages that open with an ASSESSMENT
band; the banner rows (Grade, Term, skill area) are header rows; keep the
parent table's column count. Verification - a table whose top rows contain
REQUIREMENTS PER TERM starts a new table even if the columns match; that phrase
appears on exactly the 48 banner pages, PDF 36-133, and nowhere else. EVIDENCE
(local, git-ignored): PDF data/funda_wande/caps_english_hl_grade_3_fs.pdf; trial
runs results/funda_wande_trial_p13_15/, results/funda_wande_trial_p28_55/,
results/funda_wande_trial_p110_135/; findings - 12 of 13 Grade 3 continuation
pages and all 7 pages opening with ASSESSMENT were extracted as loose blocks and
stitching never links a table to a block (document_ir/utils.py:302); 2 of 8
table-to-table breaks into a new banner table were wrongly merged (PDF 110->111,
123->124); the checker's correction on PDF 43 added 8 words not on the page; no
content segment has a Term in section_path; in Grade 3, 3.4 GRADE 3 drops out of
section_path at PDF 122 (only the newest 60 headings are kept) while in Grade R,
where continuation content stayed as table rows, the Grade stayed on all 44
segments. BASELINE NOTES: utils.py has the uncommitted fix-5 change;
examples/funda_wande/ is untracked; no .standards/CONTEXT.md existed.
VALIDATION after the fixes: rerun the three trial ranges plus PDF 60-66
(start_page 59, end_page 66) and compare the results. USER REWORK (2026-10-09): (a) the fix-3 guard runs only when the stage config sets use_extracted_hints true; with it false the guard does not run (replaces the always-on decision behind AC-011); (b) the guard blocks only content words the correction adds, i.e. missing from the usable text layer and absent from the extraction agent's PageIR (align AC-007 wording with AC-010); (c) blocked-correction warnings go to the terminal only; validation needs no log file; (d) keep CAPS Grade R mapped to Learning Commons grade K. USER DECISION (2026-10-09, after the PDF 43 scoping failure): the guard also blocks a correction when a content word occurs more times than in both the text layer and the extraction agent's PageIR (PDF 43 repeats an on-page ASSESSMENT band).`
`Scope`:
`.standards/docs/scope/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
`Architecture`:
`.standards/docs/specs/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md` `Development`:
`.standards/docs/development/add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8.md`
`PromotionReason`: `NONE` `AuditTarget`: `NONE` `BlockedOn`:
`Continue with DEV-005 (loader documentation for ranges not starting at 0)?`
`PendingVerificationCadence`: `NONE`

`BaselineReconciliation`: `NONE`

<!-- When non-NONE, use one Markdown list entry per source cycle:
- `SourceCycle`: `<cancelled-cycle-id>`
  `Request`: `<source-cycle request summary>`
Preserve existing entries; see PROTOCOL.md, Baseline Reconciliation Format.
-->

## Handoff

`Kind`: `RESUME` `From`: `ARCHITECTING` `FailureType`: `NONE` `Reason`:
`Design rerun for occurrence-count guard (AC-029 to AC-032) complete; recovery frame closed, resuming development.`

## Recovery

`Active`: `false`

## Outstanding Obligations

`Active`: `false`
