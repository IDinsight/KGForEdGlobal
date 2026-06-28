# Session Instructions: Academic Standards KG Artifacts v0

## Purpose

Use this file at the start of a coding session to re-establish the current Academic Standards KG v0 context.

The immediate goal is a focused Academic Standards pipeline:

```text
stitched DocumentIR
  -> source-faithful extraction windows
  -> LLM-extracted SFI candidates
  -> global candidate registry
  -> bounded LLM-assisted dedup/merge
  -> deterministic final SFI records
  -> post-dedup source-context recovery
  -> LLM-assisted hasChild relationship resolution
  -> StandardsFramework / StandardsFrameworkItem / Relationship(hasChild)
  -> validation + KG artifacts
```

LearningComponents and LearningProgressions are downstream phases. Do not implement them in this v0 Academic Standards slice.

## Core architecture

The pipeline has three layers:

1. **PageIR extraction and verification**: page/layout extraction only. It preserves what appears on the page and does not decide KG semantics.
2. **Stitched DocumentIR**: the source material for KG extraction. It contains block/table segments, source order, page provenance, section-path hints, table rows, and optional helper views such as `rows_grid`, `grid_sources`, `row_provenance`, and `rows_filldown`.
3. **KG creation**: LLM-assisted semantic extraction plus deterministic validation, dedup, final ID minting, relationship resolution, export, and validation.

Python orchestrates, packages source-faithful inputs, validates, retrieves candidates, mints deterministic IDs, and exports artifacts. The LLM performs semantic extraction and later chooses direct `hasChild` parents from source-grounded candidate parent sets.

## Main design principles

- Keep v0 focused on Academic Standards only: `StandardsFramework`, `StandardsFrameworkItem`, and `Relationship(hasChild)`.
- Do not extract LearningComponents during the SFI stage.
- Do not create `supports`, `buildsTowards`, or `relatesTo` in the Academic Standards v0 pipeline.
- Treat the `kgs` / `CreateKGConfig` section as the country/document instruction sheet. Avoid country-hardcoded logic.
- Use the LLM for semantic SFI extraction and Step 9 parent selection. Deterministic Python rules are guardrails, retrieval aids, validators, and ID helpers.
- Preserve original language and source provenance. Do not translate or normalize source wording into final text unless a later policy explicitly requires it.
- Support no-code curricula. No downstream step may assume `statement_code` or `normalized_statement_code` exists.
- Use deterministic UUIDv5 IDs only. Never use random UUIDs for stable KG entities or relationships.
- Treat `source_text` as an evidence quote, not the only provenance and not necessarily final canonical KG text.
- Keep extraction separate from relationship resolution. Do not infer parentage during SFI extraction.

## Current implementation status

Steps 1-8 are implemented and should be treated as the completed historical path.

Implemented:

1. Load/validate stitched `DocumentIR` and `RunConfig.kgs` / `CreateKGConfig`.
2. Build and persist `kg_run_manifest.json`.
3. Plan extraction windows from `DocumentIR.segments`.
4. Persist `extraction_window_plan.json`.
5. Build source-faithful `ExtractionWindow` records.
6. Persist `extraction_windows.jsonl`.
7. Run compact-prompt LLM SFI extraction.
8. Validate `SFIExtractionResult` records for schema correctness, window identity, source-visible codes, source-supported `source_text` and `description`, and valid table row/header indexes.
9. Persist `sfi_extraction_results.jsonl` incrementally and refresh `sfi_extraction_summary.json`.
10. Resume Step 4/5 from a valid JSONL prefix when `overwrite=False`.
11. Build and persist `sfi_candidate_registry.json`.
12. Validate result/window alignment and unique window-local candidate IDs before registry flattening.
13. Compute normalized text/code/context keys, duplicate buckets, registry warnings, and registry summary counts.
14. Build bounded Step 7 dedup review components from duplicate buckets, warnings, repeated source text, and source-provenance overlap.
15. Run LLM-assisted dedup review over bounded review sets.
16. Validate dedup review responses for exact candidate coverage, closed-enum decisions, non-empty reasons, and hard merge guardrails.
17. Convert reviewed, unresolved, and singleton candidates into merge groups covering every registry candidate exactly once.
18. Persist `sfi_dedup_review_requests.jsonl`, `sfi_dedup_review_responses.jsonl`, `sfi_merge_report.json`, `sfi_merge_groups.json`, `sfi_merge_conflicts.json`, and `sfi_merge_needs_review.json`.
19. Reuse complete Step 7 artifacts only after content-current validation; otherwise resume from a valid request/response prefix.
20. Mint deterministic final SFI records from eligible `merged` and `singleton` groups.
21. Preserve merge provenance, source provenance, audit flags, and same-code/different-content disambiguators.
22. Persist `sfi_final_records.json` and `sfi_final_summary.json` using JSON-mode serialization for UUID-bearing models.

## Remaining implementation work

Steps 9-11 remain.

### Step 9: Resolve final hasChild edges

Implement `resolve_has_child_edges()`.

Inputs:

```text
CreateKGConfig
DocumentIR
KGDirs
sfi_final_records.json / Sequence[SFIFinalRecord]
sfi_final_summary.json / SFIFinalSummary | None
extraction_windows.jsonl, loaded from kg_dirs when code-parent hints are needed
```

Purpose:

```text
Create final hasChild edges between the StandardsFramework root and finalized SFIs, and among finalized SFIs themselves, after SFI extraction, merge/dedup, and deterministic final SFI ID minting.
```

Hard requirements:

- Operate on finalized SFIs only.
- Do not use temporary extraction candidates, registry candidate IDs, merge group IDs, source codes, headings, auxiliary candidates, or invented nodes as relationship endpoints.
- Require non-empty `config.academic_standards.sfi_has_child_instructions`.
- Recover source context from DocumentIR and final-record provenance after finalization.
- Build bounded, source-grounded candidate parent sets for each final SFI.
- Let the LLM select one or more direct parents from the provided candidate set, or mark the child unresolved.
- Allow a finalized SFI to have one or more incoming `hasChild` parents when source evidence supports multiple direct hierarchy memberships.
- Do not make single-parent tree assumptions in Python.
- Do not add a StandardsFramework root edge merely for reachability when semantic SFI parent(s) are selected.
- Use root fallback only when the StandardsFramework is the selected direct parent or when the child is unresolved.

Recommended Step 9 substeps:

1. Load and validate `sfi_final_records.json` and `sfi_final_summary.json`.
2. Validate that final UUIDs, identity keys, merge group IDs, and source candidate coverage are safe and unique.
3. Fail if conflict or needs-review groups were excluded unless an explicitly supported incomplete-universe policy exists in code.
4. Build lookup indexes by UUID, statement type, normalized code, raw code, source windows, source segments, source context keys, table references, and source order.
5. Recover source context for every final SFI.
6. Persist `sfi_final_contexts.json` as a debug artifact.
7. Build source-grounded candidate parent sets, always including the StandardsFramework root fallback.
8. Use code-parent matches as evidence only when normalized codes are present, compatible, and not contradicted by audit/source-context evidence.
9. Preserve same-code/different-content audited records as separate endpoints; never collapse or choose by code alone.
10. Treat raw `statement_code` with null `normalized_statement_code` as source-visible evidence only.
11. Batch children for LLM-assisted parent selection.
12. Validate each LLM response for exact child coverage, known parent IDs, no self-loops, no invented relationship types, no duplicate parent decisions for the same parent/child pair, non-empty reason/evidence, and at least one parent for every resolved child.
13. Assemble deterministic `hasChild` edges, preserving multiple-parent decisions and unresolved/root-fallback status.
14. Validate graph constraints: endpoint existence, no self-loops, no cycles among SFI-to-SFI edges, no duplicate parent/child edges, reachability from StandardsFramework, deterministic relationship IDs, and summary consistency.
15. Persist Step 9 artifacts.

Expected Step 9 artifacts:

```text
sfi_final_contexts.json
has_child_candidate_parent_sets.jsonl
has_child_resolution_requests.jsonl
has_child_resolution_responses.jsonl
has_child_edges_final.json
has_child_unresolved_edges.json
has_child_resolution_summary.json
```

### Step 10: Compile Academic Standards KG objects

Implement `compile_academic_standards_kg()`.

Objects:

```text
StandardsFramework
StandardsFrameworkItem
Relationship(hasChild)
```

Rules:

- One `StandardsFramework` per PDF.
- Use config metadata for framework title, subject, jurisdiction, provider, language, license, author, attribution, adoption status, and description.
- Export finalized SFI records as `StandardsFrameworkItem` objects.
- Export only `hasChild` relationships.
- Use `case_identifier_uuid` for relationship endpoints.
- Preserve provenance and audit evidence in metadata/provenance fields.

### Step 11: Validate and write Academic Standards artifacts

Implement `validate_academic_standards_kg()`.

Validate:

- All KG objects are schema-valid.
- Every `hasChild` source and target exists.
- Every SFI is reachable from the StandardsFramework root.
- No self-loops.
- No cycles among SFI-to-SFI `hasChild` edges.
- No duplicate relationship IDs or duplicate parent/child edges.
- Every SFI has non-empty description text.
- Every SFI has deterministic identifier / `case_identifier_uuid` / `case_identifier_uri`.
- Every SFI has provenance or a clear synthetic provenance explanation.
- No-code SFIs have stable synthetic context/text keys.
- Dropped or attached auxiliary content is accounted for in a policy coverage report when implemented.
- Zero-window or zero-final-SFI output fails.

Expected final artifacts:

```text
academic_standards_kg_bundle.json
standards_framework.json
standards_framework_items.jsonl
relationships_has_child.jsonl
entity_provenance.json
validation_report.json
policy_coverage_report.json
unresolved_items.json
```

## Step-specific implementation notes

### Steps 1-3: extraction windows

- Plan one block window for every block segment with extractable source text.
- Select table windows using KG config table include/exclude rules.
- Preserve DocumentIR source order.
- Persist inspectable artifacts before any LLM call.
- Do not add a separate context-enrichment pass to `extraction_windows.jsonl`.
- Keep extraction windows source-faithful: source text, block/table payloads, provenance, helper views, KG instructions, code matches, and code-parent hints.
- Do not infer hierarchy or parentage in extraction windows.
- Step 4 may compact the prompt payload, but validators must still compare LLM outputs against the full persisted `ExtractionWindow`.
- Do not include code-parent hints in the compact Step 4 prompt. Reserve them for Step 9.

### Steps 4-5: SFI extraction

- The LLM emits candidates, not final KG nodes.
- Emit one `SFIExtractionResult` per extraction window, even when zero candidates are found.
- Extract only SFI candidates and auxiliary records; do not extract LearningComponents.
- `description` must preserve source-language wording and be source-supported.
- For table-derived candidates, `description` must be supported by the cited `table_row_indexes` and/or `table_header_indexes`, not merely by text elsewhere in the same table window.
- `source_text` must be a visible source evidence quote.
- The model must not clean, translate, paraphrase, infer parent context, or complete descriptions from uncited rows/headers.
- Validate and retry/repair failed structured outputs.
- Persist incrementally and support resume from a valid prefix.

### Step 6: registry

- Flatten validated extraction results into globally addressable registry candidates.
- Preserve candidate payloads and minimal source references.
- Compute normalized description/source text/code/context keys.
- For coded curricula, same-code buckets are strong review signals but not final identity decisions.
- For no-code curricula, use source-context-aware text buckets to avoid globally merging repeated labels.
- Emit warnings, not hard failures, for likely duplicate or code anomalies.
- Do not merge candidates, choose canonical text, infer parentage, recover full context, or mint final IDs.

### Step 7: dedup/merge

- Treat duplicate buckets, registry warnings, exact source-text repeats, and provenance overlap as review-set construction inputs only.
- Build bounded review sets; do not send the whole registry to the LLM.
- Merge overlapping review edges into connected components before review.
- Split oversized components conservatively; unresolved residues become `needs_review` groups.
- LLM decisions are `merge`, `keep_separate`, `conflict`, or `needs_review`.
- Validate exact candidate coverage and hard merge guardrails.
- Convert reviewed, unresolved, and unreviewed singleton candidates into merge groups covering every registry candidate exactly once.
- Preserve same-code/different-content audit evidence for Step 8 and Step 9.
- Do not mint final IDs, infer hierarchy, compile KG objects, or create relationships in Step 7.

### Step 8: final SFI records

- Mint final SFI IDs only after global dedup/merge.
- Mint only eligible `merged` and `singleton` groups.
- Exclude `conflict` and `needs_review` groups from automatic final SFI records and report exclusions.
- Use deterministic UUIDv5 from canonical identity keys.
- For same-code/different-content groups, include a deterministic source/text/provenance disambiguator and preserve audit flags/peer references.
- For no-code items, use source context plus source-text hash material.
- Preserve source candidate IDs, merge evidence, source windows, source segments, source page indexes, row/header provenance, audit flags, and candidate evidence.
- Validate UUID and identity-key uniqueness before writing.

## Main pitfalls

### Do not treat raw section_path as a clean ancestor chain

`DocumentIR.section_path` can be incomplete, stale, or include superseded sibling headings. In Step 9, pass it to the LLM as ordered evidence and ask the model to infer active context at the source position.

### Do not make Step 9 a Python-only hierarchy walker

Python should retrieve and package candidate parent evidence. The LLM should choose direct parent(s) or mark unresolved. Avoid country-hardcoded hierarchy logic.

### Do not assume one parent per SFI

A finalized SFI may have one or more direct `hasChild` parents when the source framework genuinely represents multiple direct hierarchy memberships. Validate against duplicate parent/child edges, not against multiple parents.

### Do not choose parents by code alone

Codes are strong evidence but not absolute truth. Same-code/different-content records are valid separate nodes. If multiple finalized SFIs share a normalized code, use source context and LLM selection; never collapse or shortcut by code alone.

### Do not parse raw codes ad hoc in Step 9

Use `normalized_statement_code` only when present and accepted by configured code patterns. If raw `statement_code` exists but normalized code is null, treat it as source-visible evidence only.

### Do not rely on page overlap alone

`source_page_indexes` can be broad, especially for table-derived records. Prefer source windows, source segments, table row/header provenance, source context keys, source order, and recovered section evidence.

### Do not silently omit ambiguous final records

Every finalized SFI must be represented in Step 9 resolution. Resolved records get one or more semantic parent edges. Unresolved records get an explicit marked root-fallback edge and must be listed in unresolved artifacts.

### Do not leak descriptor/guidance/activity content into Standard SFIs

Activities, exemplars, duration, resources, teacher guidance, and descriptors should not become `Standard` SFIs unless KG config explicitly says they are in scope. Default policy:

```text
expectation -> SFI Standard
structural context -> SFI Standard Grouping
descriptor/guidance/activity -> auxiliary/drop/metadata/Other according to policy
```

### Do not hash LLM-cleaned text as primary identity material

Use source-derived normalized text, source context, stable source codes, and deterministic provenance material for identity keys.

### Do not produce silent empty outputs

Zero extraction windows or zero final SFIs should fail by default. Silent empty KG artifacts are worse than hard errors during v0.

## Recommended next-session start

Start from these validated Step 8 artifacts:

```text
sfi_final_records.json
sfi_final_summary.json
sfi_merge_report.json
sfi_merge_groups.json
sfi_candidate_registry.json
extraction_windows.jsonl
document_ir.json
kg_run_manifest.json
```

Then implement:

```text
resolve_has_child_edges()
compile_academic_standards_kg()
validate_academic_standards_kg()
```
