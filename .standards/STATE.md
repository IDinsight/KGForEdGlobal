# S.T.A.N.D.A.R.D.S. Workflow State

`WorkflowState`: `DEVELOPING` `CycleMode`: `STANDARD` `PendingCycleMode`: `UNSET`
`PendingCycleRequest`: `UNSET` `PendingCycleBlockedOn`: `NONE`

## Active Work

`CompletionPolicy`: `FULL_DELIVERABLE` `Id`:
`run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8` `Request`:
`Run the KG construction step of the pipeline (create_kgs: the AS, LC and LP knowledge graphs) on the Funda Wande South Africa CAPS English Home Language R-3 PDF; update the CAPS runtime config's kgs section (examples/funda_wande/config_english_curriculum.json) as necessary; check and update the KG pipeline code as necessary; and review the results. Basically the same thing the previous cycle (add-opt-in-curriculum-guidance-for-pdf-20261009T133726Z-b640e0a8, signed off) did for the page IR extraction, verification and stitching steps. Same branch (tz6/fw). INPUTS (user, 2026-10-09): page IR extraction, verification and stitching do not need to run again. Their full Grade R-3 outputs (0-based page_index 35-135, PDF 36-135), verified separately by the user, are in results/kg_for_ed/c939a3a9dcce92ed61d970f4599ced6f2ddc425d1220eeeab9828472b878d24d/ (extraction/, verification/, stitching/document_ir.json; local, git-ignored). CONTEXT FROM THE PREVIOUS CYCLE (facts, not new requirements): it authored the CAPS kgs block (South Africa / Department of Basic Education metadata; Grade > Term > Skill Area > Skill hierarchy; Grade R mapped to Learning Commons grade K; sfi_extraction_instructions say Grade, Term and skill area come from each table's header rows; tables selected by included_table_section_patterns matching requirements per term) but never ran create_kgs (a non-goal there), so that block has only been checked by config loading and review. The CAPS PDF is data/funda_wande/caps_english_hl_grade_3_fs.pdf (local, git-ignored). USER REWORK (2026-10-09): AS, LC and LP must capture their concepts the way the Learning Commons knowledge graph defines them (https://docs.learningcommons.org/knowledge-graph/understanding-knowledge-graph/introduction). Model the sub-headings CAPS prints inside each requirements table (for example Phonics, Shared Reading, Handwriting) as a Standard Grouping level between Skill Area and Skill (option B). Enforce LP Term order through the CAPS LP prompt instructions only, with no KG code change; a backward-Term buildsTowards edge means tightening the prompt and rerunning LP. Keep zero failed LC requests with lc_max_failure_rate left at 0.05. USER REWORK (2026-10-11): raise the LP candidate budgets (kgs.lp.candidate_policy.budgets) from 12 per Skill / 5000 total to 30 per Skill / 14,000 total for higher recall of progression pairs; an offline candidate count on the current AS+LC gives 12,892 pairs (about 25,800 LLM calls at 2 per pair), against 5,000 now and 385,629 uncapped. USER REWORK (2026-10-11): drop the design's LP checkpoint-scale criterion that the median save over the last 500 requests stays within 2x of the first 500 (whole-file usage rewrites make late saves about 2.5-3x early ones, about 0.4 s of checkpoint work per request against about 16 s of model time, so it does not affect the real run).`
`Scope`:
`.standards/docs/scope/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md` `Architecture`:
`.standards/docs/specs/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md` `Development`:
`.standards/docs/development/run-and-validate-the-kg-construction-20261009T214625Z-3ac2eaa8.md`
`PromotionReason`: `NONE` `AuditTarget`: `NONE` `BlockedOn`:
`Continue with DEV-005 (stop LP run 2, read-only load of its checkpoints with the fixed code, resume LP)?`
`PendingVerificationCadence`: `NONE`

`BaselineReconciliation`: `NONE`

<!-- When non-NONE, use one Markdown list entry per source cycle:
- `SourceCycle`: `<cancelled-cycle-id>`
  `Request`: `<source-cycle request summary>`
Preserve existing entries; see PROTOCOL.md, Baseline Reconciliation Format.
-->

## Handoff

`Kind`: `CHECKPOINT` `From`: `TESTING` `FailureType`: `NONE` `Reason`:
`Increment 1 verified: LP checkpoint throughput fix (DEV-007 to DEV-009) for AC-017, AC-020, AC-022, AC-023.`

## Recovery

`Active`: `false`

## Outstanding Obligations

`Active`: `false`
