# Session Instructions: Academic Standards KG Artifacts v0

## Purpose

Use this file at the start of a coding session to re-establish the current Academic Standards KG v0 context.

The v0 pipeline is focused on Academic Standards only:

```text
stitched DocumentIR
  -> source-faithful extraction windows
  -> LLM-extracted SFI candidates
  -> global candidate registry
  -> bounded LLM-assisted dedup/merge
  -> deterministic final SFI records
  -> source-context recovery
  -> bounded LLM-assisted hasChild relationship resolution
  -> final StandardsFramework / StandardsFrameworkItem / Relationship(hasChild) bundle
  -> validation + write artifacts
```

LearningComponents and LearningProgressions are downstream phases. Do not implement them in this v0 Academic Standards slice.

## Core architecture

The pipeline has three layers:

1. **PageIR extraction and verification**: page/layout extraction only. It preserves what appears on the page and does not decide KG semantics.
2. **Stitched DocumentIR**: the source material for KG extraction. It contains block/table segments, source order, page provenance, section-path hints, table rows, and optional helper views such as `rows_grid`, `grid_sources`, `row_provenance`, and `rows_filldown`.
3. **KG creation**: LLM-assisted semantic extraction plus deterministic validation, dedup, final ID minting, relationship resolution, final export, and validation.

Python orchestrates, packages source-faithful inputs, validates, retrieves candidates, mints deterministic IDs, and exports artifacts. The LLM performs semantic SFI extraction and chooses direct `hasChild` parents from bounded source-grounded candidate parent sets.

## Main design principles

- Keep v0 focused on `StandardsFramework`, `StandardsFrameworkItem`, and `Relationship(hasChild)`.
- Do not extract LearningComponents during the SFI stage.
- Do not create `supports`, `buildsTowards`, or `relatesTo` in the Academic Standards v0 pipeline.
- Treat `RunConfig.kgs` / `CreateKGConfig` as the country/document instruction sheet. Avoid country-hardcoded logic.
- Use the LLM for semantic SFI extraction and direct parent selection. Use Python for guardrails, retrieval aids, validation, deterministic IDs, and export.
- Preserve original language and source provenance. Do not translate source wording.
- Support no-code curricula. No downstream step may assume `statement_code` or `normalized_statement_code` exists.
- Use deterministic UUIDv5 IDs only. Never use random UUIDs for stable KG entities or relationships.
- Treat `source_text` as an evidence quote, not the only provenance and not necessarily final KG text.
- Keep extraction, dedup, finalization, relationship resolution, and final export as separate concerns.

## Current implementation status

Steps 1-9 are implemented and should be treated as the completed historical path.

Implemented:

1. Load/validate stitched `DocumentIR` and `RunConfig.kgs` / `CreateKGConfig`.
2. Build and persist `kg_run_manifest.json`.
3. Plan and persist source-faithful extraction windows from `DocumentIR.segments`.
4. Run compact-prompt LLM SFI extraction over each extraction window.
5. Validate and persist `SFIExtractionResult` records incrementally.
6. Build and persist the global SFI candidate registry.
7. Build bounded dedup review sets, run LLM-assisted dedup review, and persist merge groups covering every registry candidate exactly once.
8. Mint deterministic final SFI records from eligible `merged` and `singleton` groups, excluding `conflict` and `needs_review` groups from automatic finalization.
9. Recover final SFI source contexts, build bounded parent-candidate sets, run LLM-assisted direct `hasChild` parent selection, validate the graph, and persist relationship-resolution artifacts.

Key implemented artifacts:

```text
kg_run_manifest.json
extraction_window_plan.json
extraction_windows.jsonl
sfi_extraction_results.jsonl
sfi_extraction_summary.json
sfi_candidate_registry.json
sfi_dedup_review_requests.jsonl
sfi_dedup_review_responses.jsonl
sfi_merge_report.json
sfi_merge_groups.json
sfi_merge_conflicts.json
sfi_merge_needs_review.json
sfi_final_records.json
sfi_final_summary.json
sfi_final_contexts.json
has_child_candidate_parent_sets.jsonl
has_child_resolution_requests.jsonl
has_child_resolution_responses.jsonl
has_child_edges_final.json
has_child_unresolved_edges.json
has_child_resolution_summary.json
```

## Remaining implementation work

Only the final step remains.

### Step 10: Compile, validate, and write final Academic Standards KG artifacts

Combine the former compile and validation steps into a single final deterministic pipeline step.

Purpose:

```text
Compile Step 8 finalized SFI records and Step 9 validated hasChild edges into final Learning Commons-shaped Academic Standards KG artifacts, validate the complete exported graph, and write all final export files.
```

Core principle:

```text
Step 10 is a compile/export/validation step. It must not perform new semantic extraction, deduplication, finalization, hierarchy inference, or LLM resolution.
```

Required inputs:

```text
CreateKGConfig
DocumentIR
KGDirs
sfi_final_records.json / Sequence[SFIFinalRecord]
sfi_final_summary.json / SFIFinalSummary
has_child_edges_final.json / Sequence[SFIHasChildEdge]
has_child_unresolved_edges.json / Sequence[SFIHasChildEdge]
has_child_resolution_summary.json / SFIHasChildResolutionSummary
kg_run_manifest.json / dict[str, Any]
```

Optional audit inputs:

```text
sfi_merge_report.json / SFIMergeReport
sfi_merge_conflicts.json
sfi_merge_needs_review.json
```

Use optional audit inputs only when `unresolved_items.json` must include detailed excluded conflict/needs-review merge groups. Without these optional inputs, Step 10 can report finalization exclusion counts from `sfi_final_summary.json`, but cannot reconstruct the omitted merge-group details.

Inputs not required for core compile/export:

```text
sfi_final_contexts.json
has_child_candidate_parent_sets.jsonl
has_child_resolution_requests.jsonl
has_child_resolution_responses.jsonl
extraction_windows.jsonl
sfi_candidate_registry.json
```

These artifacts may be useful for debugging or audit, but Step 10 should not require them to compile the final framework, item, and relationship bundle.

Objects:

```text
StandardsFramework
StandardsFrameworkItem
Relationship(hasChild)
```

Compile rules:

- Create exactly one `StandardsFramework` per source PDF/framework.
- The deterministic `StandardsFramework` UUID must match the root UUID used by Step 9 root edges.
- Use config and document metadata for framework name/title, subject, jurisdiction, provider, language, license, author, attribution, adoption status, document key, PDF name, and available notes.
- Do not require a framework description field unless the runtime config/schema actually provides one.
- Export every `SFIFinalRecord` as exactly one `StandardsFrameworkItem`.
- Preserve final SFI identifiers exactly: `final_sfi_uuid`, `identifier`, `case_identifier_uuid`, and `case_identifier_uri`.
- Export only `Relationship(hasChild)` relationships.
- Compile exactly one final `Relationship(hasChild)` for every `SFIHasChildEdge` in `has_child_edges_final.json`.
- Preserve provenance, source references, merge evidence, relationship evidence, unresolved-root-fallback status, relationship metadata, and audit flags in metadata/provenance fields.
- Do not re-deduplicate, re-mint SFI IDs, infer new relationships, finalize excluded merge groups, or create LearningComponents.
- Do not silently drop final SFIs or relationship edges.

Relationship endpoint mapping:

```text
For each SFIHasChildEdge, compile one Relationship(hasChild):

identifier = edge.relationship_id
relationship_type = "hasChild"
source_entity = edge.source_entity
source_entity_key = "case_identifier_uuid"
source_entity_value = edge.source_entity_uuid
target_entity = "StandardsFrameworkItem"
target_entity_key = "case_identifier_uuid"
target_entity_value = edge.target_sfi_uuid
```

Also preserve these relationship fields in metadata/provenance when available:

```text
edge.parent_endpoint_id
edge.parent_final_sfi_uuid
edge.child_final_sfi_uuid
edge.llm_reason
edge.evidence_reasons
edge.unresolved_root_fallback
edge.is_root_edge
edge.metadata
edge.confidence
```

Validation requirements:

- All final KG objects are schema-valid.
- Exactly one framework object is present.
- Framework UUID matches Step 9 root-edge source UUID for root edges.
- Every `SFIFinalRecord` produces exactly one exported `StandardsFrameworkItem`.
- Every `SFIHasChildEdge` produces exactly one exported `Relationship(hasChild)`.
- Every `hasChild` source and target endpoint exists.
- Every final SFI has at least one incoming `hasChild` edge.
- Every final SFI is reachable from the `StandardsFramework` root.
- No self-loops.
- No cycles among SFI-to-SFI `hasChild` edges.
- No duplicate relationship IDs.
- No duplicate parent/child edge pairs.
- Every SFI has non-empty source-backed description text.
- Every SFI has deterministic `identifier`, `case_identifier_uuid`, and `case_identifier_uri`.
- Every SFI has provenance or a clear synthetic provenance explanation.
- No-code SFIs preserve stable source-context/text identity material.
- Relationship counts match `has_child_resolution_summary.json`.
- Final SFI counts match `sfi_final_summary.json`.
- Conflict and needs-review merge exclusions are reported at least as counts.
- Detailed conflict and needs-review exclusions are reported only when optional merge audit inputs are supplied.
- Unresolved/root-fallback relationship cases are reported from `has_child_unresolved_edges.json`.
- Zero-window, zero-final-SFI, or empty-final-KG output fails.

Expected final artifacts:

```text
academic_standards_kg_bundle.json
standards_framework.json
standards_framework_items.jsonl
relationships_has_child.jsonl
entity_provenance.json
validation_report.json
unresolved_items.json
```

`unresolved_items.json` contents:

```text
relationship_unresolved_edges:
  - records from has_child_unresolved_edges.json

finalization_exclusion_summary:
  excluded_conflict_group_count
  excluded_needs_review_group_count

finalization_excluded_groups:
  - detailed conflict/needs_review groups when optional merge audit inputs are supplied
  - otherwise empty or omitted with details_unavailable=true
```

Optional later artifact, when policy coverage is implemented:

```text
policy_coverage_report.json
```

Plan of action:

1. Define final export schemas for the compiled bundle, framework, SFI items, hasChild relationships, provenance, unresolved report, summary, and validation report.
2. Centralize or reuse the deterministic `StandardsFramework` UUID helper so Step 9 root edges and Step 10 framework export use the same root ID.
3. Load or receive the required Step 10 inputs; treat merge report/conflict/needs-review artifacts as optional audit inputs only.
4. Compile one framework object from `DocumentIR` and KG config metadata without requiring a non-existent description field.
5. Compile one `StandardsFrameworkItem` for every `SFIFinalRecord`, preserving final UUIDs, CASE identifiers, source provenance, merge/audit evidence, and identity metadata.
6. Compile one `Relationship(hasChild)` association for every validated `SFIHasChildEdge`, using the explicit endpoint mapping above and preserving relationship IDs, endpoints, LLM reason, evidence reasons, confidence, metadata, and unresolved-root-fallback status.
7. Build a complete KG bundle plus separate inspectable framework, item, relationship, provenance, unresolved, summary, and validation artifacts.
8. Run final graph validation over the compiled artifact: endpoint existence, incoming-edge coverage, root reachability, no duplicate edges/IDs, no self-loops, no SFI cycles, schema validity, and non-empty source-backed SFI descriptions.
9. Cross-check counts and summaries across Step 8, Step 9, and Step 10 artifacts.
10. Implement `overwrite=True` rebuild behavior.
11. Implement `overwrite=False` exact-payload reuse/rebuild behavior: reuse existing Step 10 artifacts only if all expected output artifacts exist, parse successfully, exactly match what would be compiled from the current inputs, and have a successful validation report for the same input fingerprints/counts. Otherwise rebuild all Step 10 outputs deterministically.
12. Do not implement resume-prefix logic for Step 10; this step has no LLM calls and no incremental JSONL review process.
13. Wire the final function into `create_kgs.py` as the last pipeline step and add focused tests for happy path, no-code curricula, same-code/different-content, root fallback, dangling edges, missing incoming edges, cycles, stale artifacts, optional audit inputs absent/present, and serialization round trip.

## Step-specific implementation notes

### Steps 1-3: extraction windows

- Plan one block window for every block segment with extractable source text.
- Select table windows using KG config table include/exclude rules.
- Preserve DocumentIR source order.
- Persist inspectable artifacts before any LLM call.
- Keep extraction windows source-faithful: source text, block/table payloads, provenance, helper views, KG instructions, code matches, and code-parent hints.
- Do not infer hierarchy or parentage in extraction windows.
- Step 4 may compact the prompt payload, but validators must compare LLM outputs against the full persisted `ExtractionWindow`.
- Do not include code-parent hints in the compact Step 4 prompt. Reserve them for Step 9.

### Steps 4-5: SFI extraction

- The LLM emits candidates, not final KG nodes.
- Emit one `SFIExtractionResult` per extraction window, even when zero candidates are found.
- Extract only SFI candidates and auxiliary records; do not extract LearningComponents.
- `description` must preserve source-language wording and be source-supported.
- For table-derived candidates, `description` and `source_text` must be supported by cited `table_row_indexes` and/or `table_header_indexes`.
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
- Split oversized components conservatively; unresolved residues become `needs_review` groups.
- LLM decisions are `merge`, `keep_separate`, `conflict`, or `needs_review`.
- Validate exact candidate coverage and hard merge guardrails.
- Convert reviewed, unresolved, and unreviewed singleton candidates into merge groups covering every registry candidate exactly once.
- Preserve same-code/different-content audit evidence for later steps.
- Do not mint final IDs, infer hierarchy, compile KG objects, or create relationships.

### Step 8: final SFI records

- Mint final SFI IDs only after global dedup/merge.
- Mint only eligible `merged` and `singleton` groups.
- Exclude `conflict` and `needs_review` groups from automatic final SFI records and report exclusions.
- Use deterministic UUIDv5 from canonical identity keys.
- For same-code/different-content groups, include deterministic source/text/provenance disambiguators and preserve audit flags/peer references.
- For no-code items, use source context plus source-text/description hash material.
- Preserve source candidate IDs, merge evidence, source windows, source segments, source page indexes, row/header provenance, audit flags, and candidate evidence.
- Validate UUID and identity-key uniqueness before writing.

### Step 9: hasChild relationship resolution

- Operate on finalized SFIs only.
- Do not use extraction candidates, registry IDs, merge group IDs, source codes, headings, auxiliary candidates, or invented nodes as relationship endpoints.
- Fail before relationship resolution if finalization excluded conflict or needs-review groups, unless an explicitly supported incomplete-universe policy exists.
- Recover source context from DocumentIR and final-record provenance.
- Build bounded source-grounded parent-candidate sets for each final SFI.
- Always include the `StandardsFramework` root fallback candidate.
- Candidate generation is retrieval only; Python must not auto-resolve direct parentage.
- Use evidence such as code-parent hints, canonical scope matches, active outline stack, section-path matches, source context keys, source segments/windows, table row/header context, source-order proximity, and statement-type compatibility.
- Use code-parent hints only with compatible local source evidence. Never choose by code alone.
- Preserve same-code/different-content audited records as separate endpoints.
- Let the LLM select one or more direct parents from the bounded set, or mark the child unresolved.
- Resolved records get selected semantic parent edges. Unresolved records get an explicit marked root-fallback edge.
- Validate endpoint existence, no duplicate edge pairs, no self-loops, no SFI-to-SFI cycles, reachability from root, statement-type policy, canonical-scope policy, and relationship ID uniqueness.

### Step 10: final KG compile/export/validation

- Compile final KG objects only from Step 8 final SFIs and Step 9 validated edges.
- Required core compile inputs are `CreateKGConfig`, `DocumentIR`, `KGDirs`, `sfi_final_records.json`, `sfi_final_summary.json`, `has_child_edges_final.json`, `has_child_unresolved_edges.json`, `has_child_resolution_summary.json`, and `kg_run_manifest.json`.
- Treat `sfi_merge_report.json`, `sfi_merge_conflicts.json`, and `sfi_merge_needs_review.json` as optional audit inputs for detailed excluded-group reporting only.
- Preserve IDs exactly; do not re-mint SFI or relationship UUIDs.
- Ensure the exported `StandardsFramework` UUID matches the Step 9 root-edge UUID.
- Compile one `Relationship(hasChild)` per `SFIHasChildEdge` using `case_identifier_uuid` endpoints.
- Preserve finalization and relationship provenance in export metadata.
- Validate the complete exported graph, not just individual artifacts.
- Report unresolved relationship cases from `has_child_unresolved_edges.json` and finalization exclusion counts from `sfi_final_summary.json`.
- Write final JSON/JSONL artifacts and a validation report.

## Main pitfalls

### Do not treat `section_path` as a clean ancestor chain

`DocumentIR.section_path` can be incomplete, stale, or include superseded sibling headings. In Step 9, pass it as evidence; do not treat it as deterministic parentage.

### Do not make Step 9 a Python-only hierarchy walker

Python retrieves and packages candidate parent evidence. The LLM chooses direct parent(s) or marks unresolved.

### Do not assume one parent per SFI

A finalized SFI may have multiple direct `hasChild` parents when source evidence supports multiple direct hierarchy memberships.

### Do not choose parents by code alone

Codes are strong evidence but not absolute truth. Same-code/different-content records are valid separate nodes.

### Do not parse raw codes ad hoc in Step 9

Use `normalized_statement_code` only when present and accepted by configured code patterns. Raw `statement_code` with null normalized code is source-visible evidence only.

### Do not rely on page overlap alone

Prefer source windows, source segments, table row/header provenance, source context keys, source order, and recovered section evidence.

### Do not silently omit ambiguous final records

Every finalized SFI must be represented in relationship resolution and final export. Unresolved records need explicit marked root-fallback edges and unresolved reporting.

### Do not leak descriptor/guidance/activity content into Standard SFIs

Activities, exemplars, duration, resources, teacher guidance, and descriptors should not become `Standard` SFIs unless KG config explicitly says they are in scope.

### Do not hash LLM-cleaned text as primary identity material

Use source-derived normalized text, source context, stable source codes, and deterministic provenance material for identity keys.

### Do not produce silent empty outputs

Zero extraction windows, zero final SFIs, or empty final KG artifacts should fail by default.

## Recommended next-session start

Start from these required Step 10 inputs:

```text
config / CreateKGConfig
document_ir.json
kg_run_manifest.json
sfi_final_records.json
sfi_final_summary.json
has_child_edges_final.json
has_child_unresolved_edges.json
has_child_resolution_summary.json
KGDirs / output directory
```

Optional audit inputs for detailed excluded conflict/needs-review reporting:

```text
sfi_merge_report.json
sfi_merge_conflicts.json
sfi_merge_needs_review.json
```

The following prior-stage artifacts are useful for debugging, but are not required for core Step 10 compile/export:

```text
sfi_final_contexts.json
has_child_candidate_parent_sets.jsonl
has_child_resolution_requests.jsonl
has_child_resolution_responses.jsonl
sfi_candidate_registry.json
extraction_windows.jsonl
```

Then implement the single remaining final step:

```text
compile_validate_and_write_academic_standards_kg()
```

or an equivalent Step 10 orchestration function that compiles, validates, and writes the final Academic Standards KG bundle.
