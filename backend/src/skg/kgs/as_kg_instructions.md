# Session Instructions for the Creation of Academic Standards KG Artifacts v0

## Purpose of this file

Use this file at the start of the coding session to establish the shared context quickly and begin implementation without re-reviewing the full curriculum mapping background.

Revision note before Step 9: Steps 1-8 have produced finalized SFI records. Step 9 should now consume `sfi_final_records.json` / `sfi_final_summary.json`, recover source context from DocumentIR, and resolve `hasChild` relationships with explicit guardrails for audited same-code/different-content records, null-normalized source codes, stale section paths, and broad page provenance.

The immediate goal is **not** to build the full Learning Commons KG pipeline end-to-end. The immediate goal is to implement a focused v0 slice:

```text
stitched DocumentIR
  -> source-faithful LLM-ready extraction windows
  -> LLM-extracted SFI candidates
  -> merged/deduplicated final SFI records with source provenance
  -> post-dedup source-context recovery from DocumentIR
  -> LLM-assisted hasChild relationship resolution
  -> StandardsFramework
  -> StandardsFrameworkItem
  -> Relationship(hasChild)
  -> validation + Academic Standards KG artifacts
```

LearningComponents and LearningProgressions remain important downstream goals, but they should come after the Academic Standards hierarchy is stable. Treat them as separate downstream phases: LearningComponents add `LearningComponent` nodes plus `supports` relationships from LearningComponents to finalized StandardsFrameworkItems; LearningProgressions add `buildsTowards` and `relatesTo` relationships among finalized StandardsFrameworkItems.

Academic Standards KG creation is an **LLM extraction pipeline**. Python prepares source-faithful windows, adds KG config and code hints, validates structured responses, merges duplicates, mints deterministic IDs, prepares source-grounded parent-candidate sets, resolves relationships with LLM assistance when needed, and exports artifacts. The LLM performs semantic extraction from each window into SFI candidates. After SFIs are finalized, the LLM also helps choose direct `hasChild` parents from source-grounded candidate parent sets derived from the finalized SFIs' provenance and DocumentIR context. Deterministic rules are guardrails, retrieval aids, validation checks, and helpers, not a replacement for LLM semantic judgment.

---

## Big-picture objective

We are building a reusable pipeline that maps primary-school curriculum PDFs from any jurisdiction into the high-level Learning Commons Knowledge Graph shape.

The initial countries/documents include Zambia, Uganda, Tanzania, Ghana, Senegal Math, and Senegal Reading. Some have stable alphanumeric codes, such as Ghana and Zambia. Others, especially Senegal Math and Senegal Reading, rely more on section headings, paliers, weeks, table local codes, row order, and bilingual content than on stable standard codes.

The pipeline must preserve the Learning Commons KG ontology shape without forcing non-US documents into a US CASE import model.

For v0, the Academic Standards output should include:

```text
StandardsFramework
StandardsFrameworkItem
Relationship(hasChild)
```

The next stages can later add separate downstream artifact groups:

```text
Learning Components phase:
  LearningComponent
  Relationship(supports)
    LearningComponent -> StandardsFrameworkItem

Learning Progressions phase:
  Relationship(buildsTowards)
    StandardsFrameworkItem -> StandardsFrameworkItem
  Relationship(relatesTo)
    StandardsFrameworkItem -> StandardsFrameworkItem
```

Do not model `buildsTowards` or `relatesTo` as LearningComponent relationships. They are SFI-to-SFI progression relationships. The LearningComponent phase should create only `supports` edges back to finalized SFIs unless a later ontology revision explicitly changes this.

---

## Starting Files for This Session

Files available:
1. Python files:
    - `create_kgs.py`: the main entry point for creating KG artifacts from the stitched DocumentIR and the `kgs` section of the runtime config
    - `create_extraction_windows.py`: contains functionality for creating LLM-ready extraction windows from the DocumentIR and `CreateKGConfig` attributes
    - `schemas.py`: schemas for the KG pipeline
    - `utils.py`: utility functions for the KG pipeline
    - `run_config_schemas.py`: contains the run config schemas (renamed to avoid conflict)
    - `schemas_document_ir.py`: schemas for stitched DocumentIR segments, including BlockSegment, TableSegment, provenance, table rows, rows_grid, grid_sources, row_provenance, and rows_filldown (renamed to avoid conflict)
2. JSON files:
    - `config_reading_curriculum.json`: the runtime config; its `kgs` section contains both KG runtime controls and country/document-specific Academic Standards extraction/config attributes
    - `document_ir.json`: the stitched DocumentIR output for Senegal's curriculum PDF
    - `kg_run_manifest.json`: output from the KG prep stage for Senegal's curriculum

---

## Current architecture

The overall pipeline has three conceptual layers. Layers A and B are already implemented. Layer C is the next implementation target.

### Layer A: PageIR extraction

This is the page-level, layout-oriented extraction layer. It extracts what appears on the page: blocks, headings, paragraphs, lists, captions, figures, tables, rows, cells, coordinates, languages, and reading order.

The vision/LLM extraction layer should not decide KG semantics. It should not decide that something is a grade node, standard, objective, descriptor, or activity. Its job is to preserve page content faithfully.

### Layer B: stitched DocumentIR

This is the current upstream artifact for KG creation.

The DocumentIR is the raw material for KG extraction. It contains stitched readable segments: block segments and table segments, page provenance, bounding boxes, section path hints, table rows, and table helper views such as `rows_grid`, `grid_sources`, `row_provenance`, and `rows_filldown` when available.

Important: the DocumentIR is still layout/source material. It does **not** yet know which content is a standard, a grouping, an example, an activity, a descriptor, or a competency.

The document IR stitcher is intentionally layout-oriented. It loads verified PageIRs, normalizes items, links cross-page continuations, merges cross-page tables/text/list blocks, verifies that normalized items were consumed exactly once, and saves `document_ir.json` plus a stitch report. It does not infer semantic hierarchy, assign statement roles, create LC KG IDs, or export KG nodes/edges.

### Layer C: Knowledge Graph creation

This is the next layer to implement.

The KG creation layer takes a stitched `DocumentIR` plus country/document-specific Academic Standards attributes from `RunConfig.kgs` / `CreateKGConfig` and uses LLM-driven semantic extraction, followed by deterministic validation/merge/export, to turn the source material into validated KG-shaped artifacts.

For v0, this layer should focus on Academic Standards only:

```text
DocumentIR + RunConfig.kgs / CreateKGConfig
  -> source-faithful LLM-ready extraction windows
  -> LLM-extracted SFI candidates
  -> global registry
  -> merge/dedup
  -> deterministic final SFI IDs
  -> recover raw source context from DocumentIR for finalized SFIs
  -> LLM-assisted hasChild resolution from candidate parent sets
  -> final hasChild edges
  -> StandardsFramework / StandardsFrameworkItem / Relationship(hasChild)
```

---

## Current implementation state

The KG entry point is `create_kgs.py`.

The current v0 implementation now covers prep, source-faithful extraction-window creation, Step 4/5 SFI extraction, Step 6 registry construction, and Step 7 bounded LLM-assisted SFI merge/dedup:

```text
1. Load and validate the stitched DocumentIR and `RunConfig.kgs` / `CreateKGConfig`.
2. Cross-check basic KG config/document compatibility.
3. Build and persist kg_run_manifest.json.
4. Plan extraction windows from DocumentIR segments.
5. Persist extraction_window_plan.json.
6. Build base LLM-ready extraction windows.
7. Persist extraction_windows.jsonl.
8. Run compact-prompt LLM extraction over extraction_windows.jsonl.
9. Validate each SFIExtractionResult with schema validation and source-grounding quality checks for codes, `source_text`, `description`, source references, and table row/header indexes.
10. Persist sfi_extraction_results.jsonl incrementally after each successful window.
11. Refresh sfi_extraction_summary.json after each successful window.
12. Resume Step 4/5 from an existing valid JSONL prefix when overwrite is false, or skip only when all current windows are complete.
13. Build and persist sfi_candidate_registry.json from validated extraction results and extraction windows.
14. Validate result/window alignment, unique window-local candidate IDs, statement-type/code-pattern compatibility, and current extraction quality before registry flattening.
15. Compute source-context-aware text/code bucket keys, duplicate buckets, registry warnings, and registry summary counts.
16. Build bounded Step 7 dedup review components from duplicate buckets, warnings, and source-provenance overlap.
17. Run LLM-assisted dedup review over bounded review sets, validate closed-enum decisions and exact candidate coverage, and persist review request/response JSONL progress.
18. Convert reviewed, unresolved, and singleton candidates into complete merge groups that cover every registry candidate exactly once.
19. Persist sfi_merge_report.json, sfi_merge_groups.json, sfi_merge_conflicts.json, and sfi_merge_needs_review.json.
20. Reuse complete current Step 7 artifacts when overwrite is false, or resume from a valid review request/response prefix when a prior dedup run is incomplete.
21. Mint deterministic final SFI records from eligible Step 7 merge groups, preserving merge provenance, source provenance, audit flags, and same-code/different-content disambiguators.
22. Persist sfi_final_records.json and sfi_final_summary.json using JSON-mode serialization for UUID-bearing Pydantic models.
23. Recover Step 9 source context from DocumentIR and finalized SFI provenance, build source-grounded candidate parent sets, resolve hasChild relationships, and persist request/response/debug artifacts.
```

Do **not** add a separate context-enrichment pass to `extraction_windows.jsonl` for v0. The persisted extraction windows should stay source-faithful and include source text, block/table payloads, provenance, table helper views, KG extraction instructions, code matches, and code-parent hints. Broader source context such as `section_path` should be recovered later from the DocumentIR after SFIs have been finalized and should be used during `hasChild` relationship resolution.

The persisted `ExtractionWindow` remains the complete source-faithful artifact and the validation source of truth. Step 4 may derive a compact prompt payload from each `ExtractionWindow` to reduce token cost and model distraction. The compact prompt should preserve only the fields needed for SFI extraction: window identity, segment kind, block/table source text, compact table rows, compact table header rows, source row indexes, source header indexes, code matches, and KG extraction instructions from `RunConfig.kgs` / `CreateKGConfig`. Code-parent hints should not be included in the compact prompt; they remain in the persisted `ExtractionWindow` and are reserved for later `hasChild` relationship resolution. Validators should still compare LLM outputs against the full `ExtractionWindow` artifact.

In SFI extraction results, `candidate.source_text` is a source-visible evidence quote for validation and review. It is not the final canonical KG statement text and must not be treated as the only downstream provenance. `candidate.description` is the candidate-level semantic statement text, but it must still be source-supported: validators should reject descriptions that cannot be found directly in, or conservatively assembled from, source-visible text in the persisted `ExtractionWindow`. For split or continued table statements, the extraction result should preserve complete semantic text in `description` when the visible statement can be assembled from adjacent source rows/cells, and it must include all contributing `table_row_indexes` and/or `table_header_indexes`. Later registry, merge, and export stages should recover full source context from the persisted `ExtractionWindow` and stitched DocumentIR rather than relying on `source_text` alone.

The CLI cross-checks that the KG run is using the same PDF bytes/doc_key as the extraction and stitching run, creates the KG output directory, persists `kg_run.json`, and then calls `build_kgs(...)`.

Step 6 builds a lean global candidate registry from `sfi_extraction_results.jsonl` and `extraction_windows.jsonl`. It validates result/window alignment, flattens window-local candidates into document-level registry records, computes source-context-aware text/code bucket keys, emits possible duplicate buckets and warnings, and writes a registry summary. It does not merge candidates, mint final SFI IDs, infer hierarchy, or perform full source-context recovery.

Step 7 now performs bounded LLM-assisted merge/dedup over registry review sets. It treats Step 6 duplicate buckets, warnings, and source-provenance overlap as review signals; validates every LLM decision for exact candidate coverage and hard guardrails; converts accepted decisions into merge groups; preserves conflict and needs-review groups; creates singleton groups for unreviewed candidates; and writes the merge report artifacts. It does not mint final SFI IDs, choose final canonical KG text, recover full DocumentIR context, infer `hasChild`, or compile final KG objects.

The next coding target is Step 9: recover source context for finalized SFI records and resolve direct `hasChild` edges. Step 9 should use the completed Step 8 final records as the only SFI node universe; it must not return to temporary extraction candidates or merge-group-only identities except as provenance/evidence.

---

## Key design principles for v0

### 1. Keep v0 small

The first complete product should be an Academic Standards KG artifact bundle. Do not implement LearningComponents or LearningProgressions until the SFI hierarchy is working on at least Ghana, Zambia, and Senegal Math and Senegal Reading curricula. When those phases are added, keep their relationship semantics separate: LearningComponents support SFIs; LearningProgressions connect SFIs to other SFIs.

### 2. Keep downstream phases semantically separate

LearningComponents and LearningProgressions are both downstream of the Academic Standards hierarchy, but they are not the same artifact group.

```text
LearningComponent -> supports -> StandardsFrameworkItem
StandardsFrameworkItem -> buildsTowards -> StandardsFrameworkItem
StandardsFrameworkItem -> relatesTo -> StandardsFrameworkItem
```

The LearningComponent phase should consume finalized Standard SFIs, decompose them into atomic teachable skills or concepts, deduplicate those components, mint deterministic LearningComponent IDs, and create `supports` edges back to the source SFIs.

The LearningProgressions phase should consume finalized SFIs and create progression or relatedness edges among SFIs. It should not require LearningComponent nodes, and it should not attach `buildsTowards` or `relatesTo` to LearningComponents.

### 2a. Keep Learning Commons ontology boundaries explicit

A `StandardsFrameworkItem` may be either:

```text
source-visible organizational/structural element within a standards framework
  examples: grade band, domain, strand, cluster, topic, sub-topic, palier, week grouping

source-visible normative learning expectation
  examples: standard, content standard, indicator, objective, specific competence
```

A `LearningComponent` is not extracted during the SFI stage. It is a smaller teachable skill or concept that supports one or more finalized `StandardsFrameworkItem` records and is produced only in a later LearningComponent phase.

During Academic Standards v0 extraction:

```text
organizational curriculum structure -> SFI candidate with normalized_statement_type = Standard Grouping
normative learning expectation      -> SFI candidate with normalized_statement_type = Standard
descriptor/guidance/activity text   -> auxiliary candidate, metadata, dropped text, or Other only if KG config policy says so
atomic skill/concept decomposition  -> not Step 4; defer to the later LearningComponent phase
```

Do not ask the SFI extractor to decompose standards into subskills, prerequisite skills, teachable concepts, or lesson-level components. That decomposition belongs to the downstream LearningComponent phase and should consume finalized Standard SFIs, not temporary extraction-window candidates.

### 3. Config-driven, not country-hardcoded

The `kgs` section of the runtime config is the country/document instruction sheet. Its `CreateKGConfig` attributes should tell the pipeline:

```text
what counts as a grouping SFI
what counts as a normative expectation SFI
what counts as descriptor/guidance/activity/auxiliary material
which table signatures/sections are eligible for extraction
which table signatures/sections are excluded
what code patterns exist, if any
which fields should be used for no-code synthetic merge keys
how SFI deduplication should be handled by the Step 7 dedup LLM
how direct SFI hasChild hierarchy should be resolved by the Step 9 relationship LLM
how bilingual pairs should be treated
```

Country-specific examples:

```text
Ghana:
  Basic grade nodes -> grouping SFIs
  strands/sub-strands -> grouping SFIs
  content standards and indicators -> SFIs
  exemplars/core competencies -> auxiliary, not standalone SFIs for v0 unless configured

Zambia:
  grades/subjects/topics/sub-topics -> grouping SFIs
  Specific Competences -> Standard SFIs
  Expected Standard -> descriptor/metadata attached to the competence
  Learning Activities -> guidance, not SFI for v0

Senegal Math:
  domain/activity area/palier/week context -> grouping or sequencing metadata
  Objectif specifique -> Standard SFI
  Contenus -> descriptor/metadata
  Duree -> metadata only

Senegal Reading:
  Oral/Lecture/Communication Ecrite/Production d'ecrits -> grouping SFIs
  Palier/Jeego/Niveau/Semaine -> grouping or sequencing metadata
  Objectifs specifiques -> Standard SFIs
  Contenus/Outils de langue/Durees -> descriptor/guidance/metadata depending section
```

For Step 7, use a single curriculum-specific deduplication instruction field:

```text
sfi_deduplication_instructions
```

This field should tell the dedup LLM when candidate records should merge, stay separate, be marked as conflicts, or remain unresolved for review. It replaces separate duplicate-review and repeated-statement policy fields for v0. Keep `bilingual_pair_policy` separate because it is narrower, optional, and especially relevant for bilingual source-local pairing. Do not maintain legacy duplicate-policy fields or backward-compatible aliases in v0.

For Step 9, use a single curriculum-specific hasChild instruction field:

```text
sfi_has_child_instructions
```

This field should tell the relationship-resolution LLM the intended direct hierarchy grammar for finalized SFIs in this curriculum. It should identify which finalized SFI roles can be children of the StandardsFramework root, which finalized SFI roles can be direct parents of other finalized SFIs, how source table/header/section/grade context should be interpreted, how configured code-parent rules should be used as evidence, and which source-visible roles are broader ancestors rather than direct parents. Step 9 must include this field in every hasChild parent-selection prompt. Do not maintain legacy hasChild instruction fields or backward-compatible aliases in v0.

### 4. Separate extraction from post-dedup relationship resolution

The SFI-extraction system instruction should describe the curriculum-specific extraction grammar:

```text
what counts as an SFI
what counts as a grouping SFI versus a normative expectation SFI
which materials are auxiliary rather than StandardsFrameworkItems
how table cells, codes, repeated statements, bilingual pairs, and descriptors should be interpreted
```

The extraction window should provide the source evidence for the specific segment being inspected:

```text
source text
source order/page range
source block or table payload
source table/row/column provenance
code matches and code parent hints
KG extraction instructions from `RunConfig.kgs` / `CreateKGConfig`
```

Do not require extraction windows to include additional `source_context_hints`, nearby heading packages, or a precomputed active context path. Relationship context should be recovered later from the stitched DocumentIR using the finalized SFI's source provenance.

The hasChild-resolution system instruction must include `config.academic_standards.sfi_has_child_instructions`. That field should describe the curriculum-specific hierarchy grammar:

```text
which finalized SFI roles are expected to be children of the StandardsFramework root
which finalized SFI roles are expected to be direct parents of other SFIs
how to interpret raw DocumentIR section_path as ordered heading history
how source table/header/row context, section context, grade/context labels, and configured code-parent rules should be used as evidence
which source roles are direct parents versus broader ancestor context
```

This distinction is important. The extraction LLM identifies candidate SFIs. The relationship-resolution LLM chooses direct parents only after candidates have been merged into finalized SFIs and after Python has recovered source-grounded candidate parent sets from provenance, `section_path`, code rules, source order, and table context.

Python may recover and package source-visible context evidence, but it must not treat that evidence as final semantic hierarchy. For relationship resolution, the LLM should choose from provided finalized parent candidates or mark the child unresolved; it should not invent new parent nodes.

### 5. Academic Standards extraction is LLM-driven by design

The Academic Standards KG should be created through an LLM extraction stage, not by trying to make Python fully understand every curriculum layout semantically.

Python should do the deterministic orchestration work:

```text
plan extraction windows from source segments
build stable LLM prompt windows
provide config-derived hints and constraints
preserve raw source text/provenance
validate the LLM response schema
merge duplicates globally
mint deterministic IDs
resolve/validate hasChild edges
compile schema-valid KG artifacts
```

The LLM should do the semantic extraction work inside each window:

```text
identify grouping SFI candidates
identify normative expectation SFI candidates
separate expectations from descriptors/guidance/activities/examples
interpret dense table cells using KG config rules
handle no-code and bilingual/native-language curricula
preserve source text, source role, statement code, language, and provenance needed for later merge/dedup and relationship resolution
```

Deterministic code patterns, table header mappings, and code parent rules are still useful, but in v0 they should be treated mainly as prompt inputs, hints, validators, and merge/ID aids. They should not replace the LLM extraction step.

### 6. No-code support is mandatory

No downstream candidate, merge, ID, or relationship step may assume `statement_code` exists.

For coded curricula, stable source codes are valuable and should be used for merge and ID stability. For no-code curricula, use deterministic synthetic keys based on document context and source text.

No-code synthetic keys should be based on source-derived material, not LLM paraphrases.

### 7. Preserve original language and provenance

For v0, translation is not required. Preserve original text, language tags, source page/cell/row provenance, and optional `text_en` when already available.

For non-English or bilingual curricula, keep source-language content. Use `mul` for mixed-language content when appropriate.

### 8. Treat table helper views as helpful but optional

For table-heavy curricula, extraction windows should include both source-fidelity and interpretation-friendly views when available:

```text
rows              -> source-fidelity row/cell text
rows_grid         -> grid-normalized cells
rows_filldown     -> interpretation-friendly rows with group context filled down
grid_sources      -> debugging/provenance for normalized cells
row_provenance    -> row/cell provenance
```

However, the KG stage should not fail just because `rows_grid` or `rows_filldown` is missing. Use them when present; fall back to raw rows, headers, and provenance when not present.

### 9. Use candidates before final KG nodes

The LLM extraction stage should emit candidates, not final KG nodes. Deterministic code may add hints or pre/post-validation, but final KG nodes should not be minted directly from a single window response.

This prevents duplicate KG nodes from overlapping extraction windows, filldown repetition, repeated codes, or bilingual duplicates.

Final SFI IDs should be minted only after global merge/dedup.

### 10. Deterministic IDs only

Never use random UUIDs for stable KG entities or relationships.

Use deterministic canonical strings and UUIDv5. The PDF byte hash `doc_key` should be part of the canonical namespace.

Recommended canonical strings:

```text
lc:curriculum:{doc_key}:framework
lc:curriculum:{doc_key}:sfi:{role}:{statement_code}:{context_disambiguator}
lc:curriculum:{doc_key}:sfi:{role}:{normalized_context_path}:{normalized_text_hash}
lc:curriculum:{doc_key}:rel:hasChild:{source_uuid}:{target_uuid}
```

For stable-code items, avoid overdepending on text hashes. Use the stable source code plus context when possible. For no-code items, use normalized source context plus normalized source-text hash.

### 11. Relationship endpoint rules

For Academic Standards v0, only `hasChild` is required.

Use KG relationship helper functions so every relationship follows the schema constraints:

```text
StandardsFramework       --hasChild--> StandardsFrameworkItem
StandardsFrameworkItem   --hasChild--> StandardsFrameworkItem
```

The relationship schema expects `hasChild` endpoints to use `case_identifier_uuid` for both source and target when applicable. For simplicity in v0, set each exported framework/item's `identifier` equal to `case_identifier_uuid` unless there is a strong reason not to.

### 12. Validation is part of the product

Do not treat validation as a later cleanup step. The v0 product should include schema validation, relationship endpoint validation, coverage checks, and an explicit validation report.

---

## Academic Standards v0 implementation sequence

The prep/manifest and extraction-window stages now exist. The remaining implementation sequence should continue from the persisted `extraction_window_plan.json` and `extraction_windows.jsonl` artifacts.

### Step 1. Plan extraction windows from DocumentIR segments — implemented

Implemented by a function such as:

```python
plan_extraction_windows(*, config: CreateKGConfig, document_ir, save_fp) -> list[ExtractionWindowPlanItem]
```

Purpose:

```text
Walk `DocumentIR.segments` in source order and plan the source units that should become Academic Standards extraction windows.
```

Rules:

```text
Block segments:
- Plan one block extraction window for every block segment with extractable source text.
- Do not require KG config block-selection rules.
- Do not make Python decide whether a block contains SFIs. The LLM extraction step decides whether the block yields SFI candidates, auxiliary records, or no candidates.

Table segments:
- Apply KG config-driven table selection.
- Exclude tables matching excluded_table_columns_signatures.
- Exclude tables matching excluded_table_section_patterns.
- Include tables matching included_table_columns_signatures.
- Include tables matching included_table_section_patterns.
- Skip non-selected tables.

Run behavior:
- Preserve DocumentIR source order.
- Persist a deterministic plan item for every planned block/table source unit.
- Fail by default if no plan items are produced.
```

Output artifact:

```text
extraction_window_plan.json
```

### Step 2. Build base LLM-ready extraction windows — implemented

Implemented by a function such as:

```python
build_llm_extraction_windows(*, config: CreateKGConfig, document_ir, plan_items, save_fp) -> list[ExtractionWindow]
```

Purpose:

```text
Convert planned DocumentIR source units into stable prompt payloads for LLM-based Academic Standards extraction.

The base window must preserve source text, block/table payloads, provenance, table helper views, code hints, KG extraction instructions, and deterministic source-derived keys. It does not need a separate context-enrichment layer for v0; broader hierarchy context is recovered later from DocumentIR provenance after SFIs are finalized.
```

This step produces the inputs that will be sent to the LLM. It is not a semantic extraction step. Python packages the exact source text, table structure, optional helper views, provenance, code hints, KG extraction instructions, and deterministic source-derived keys needed for reliable LLM extraction.

Step 2 builds the complete persisted source-faithful `ExtractionWindow` artifact. Step 4 may derive a compact prompt-facing view from this artifact before the LLM call, then validates the structured response against the full persisted `ExtractionWindow`.

Each window should include:

```text
window_id
window_index
source segment_id(s)
source segment kind
source text
block payload, for block windows
table payload, for table windows
table metadata and column signature, if table-based
headers, if table-based
raw rows/cells, if table-based
rows_grid when available
rows_filldown when available
grid_sources when available
row_provenance/page provenance
code matches from configured code_patterns
code parent hints from configured code_parent_rules
deterministic source-derived hints
KG extraction instructions from `RunConfig.kgs` / `CreateKGConfig`
LLM task instructions that ask for SFI candidates and auxiliary candidates while preserving source provenance needed for later merge/dedup and hasChild resolution
```

The extraction-window stage should not infer final parentage. It also does not need to add a separate `source_context_hints` payload before SFI extraction. For relationship resolution, recover raw `section_path`, source order, page range, table/row provenance, and nearby finalized grouping SFIs from the DocumentIR after SFI merge/dedup.

For tables, windowing is controlled by `max_rows_per_table_window`:

```text
max_rows_per_table_window = null -> one whole-table window
max_rows_per_table_window = integer -> row chunks of at most that many body rows
row_overlap -> overlapping body rows between adjacent chunks when row chunking is enabled
```

Do not implement a separate logical-row assembly layer yet; use raw rows plus optional `rows_grid`, `rows_filldown`, `grid_sources`, and `row_provenance` when available, then rely on downstream candidate dedup/merge.

```text
Keep extraction windows focused on source text, block/table payloads, provenance, table helper views, KG extraction instructions, code matches, and code-parent hints. Defer hierarchy-context interpretation until after SFI extraction, merge/dedup, and final SFI ID minting.
```

Rationale:

```text
The same source segment can yield multiple candidate SFIs.
Duplicate candidates from repeated windows/tables may merge into one final SFI.
Parentage should be resolved against finalized SFIs, not temporary extraction-window candidates.
Raw DocumentIR section_path can include stale sibling headings and should be interpreted as ordered heading history during hasChild resolution, not treated as a clean ancestor path during extraction.
```

After final SFIs are minted, recover source context from the DocumentIR for each finalized SFI by using its merged source provenance:

```text
source window IDs
source segment IDs
source page indexes
source row indexes / table provenance when available
raw DocumentIR section_path for each source segment
source order and neighboring finalized grouping SFIs
code matches and code-parent hints
same table / same row / same filldown context when available
```

This recovered context is input to Step 9 relationship resolution. It is not part of the extraction-window artifact.

### Step 3. Persist extraction artifacts — implemented

Write:

```text
extraction_window_plan.json
extraction_windows.jsonl
```

Each extraction-window JSONL record should validate against the intermediate `ExtractionWindow` schema.

These artifacts should be inspectable without running the LLM. They are the primary debugging surface for, "What did we ask the LLM extractor to inspect?" and "What source text, table structure, provenance, and deterministic hints did we provide?"

### Step 4. Run LLM extraction to create SFI candidates — implemented

Implemented by functions such as:

```python
extract_sfi_candidates_from_windows(
    *,
    config: CreateKGConfig,
    extraction_windows: Sequence[ExtractionWindow],
    overwrite: bool,
    save_fp: Path,
    summary_fp: Path,
    usage_tracker: KGUsageTracker,
) -> list[SFIExtractionResult]
```

Purpose:

```text
Use the LLM to convert each persisted source-faithful extraction window into candidate StandardsFrameworkItems and auxiliary records, guided by `RunConfig.kgs` / `CreateKGConfig` and a compact prompt-facing source payload derived from the full ExtractionWindow.
```

Use the LLM as the primary semantic extractor for Academic Standards v0. The LLM returns one `SFIExtractionResult` per extraction window, and that result must validate against the extraction response schema plus source-grounding quality checks for candidate codes, `source_text`, `description`, source references, and table row/header indexes. Deterministic code should not bypass this LLM candidate-extraction stage except for mechanical framework-root creation and later validation/export bookkeeping.

The persisted `ExtractionWindow` is the source of truth. The prompt sent to the LLM may be compacted, but it must preserve enough evidence for extraction:

```text
window_id, window_index, and source_segment_ids
segment kind and source text
compact block payload or compact table row/header payload
table row indexes for row-derived table candidates
table header indexes for header-derived table candidates
code matches
KG extraction instructions from `RunConfig.kgs` / `CreateKGConfig`
```

Do not include code-parent hints in the compact prompt. SFI extraction identifies candidates and their own source-visible codes; it must not infer parentage. Code-parent hints stay in the persisted `ExtractionWindow` and are reserved for later `hasChild` relationship resolution.

The LLM should identify:

```text
structural grouping SFI candidates
normative expectation SFI candidates
auxiliary records for descriptor/guidance/activity/example/competency text that should not become Standard SFIs by default
source-language text and language tags
statement codes when visible
source row indexes for row-derived table candidates
source header indexes for header-derived table candidates
complete descriptions for source-visible statements that continue across adjacent rows/cells
uncertainties about candidate role, statement type, code, or auxiliary status
```

The LLM must not extract LearningComponents in this step. Do not decompose a standard, objective, competence, or indicator into smaller teachable skills, concepts, subskills, prerequisite skills, or lesson-level components. LearningComponents are a later downstream phase that consumes finalized Standard SFIs.

Use deterministic logic around the LLM call for:

```text
compact prompt construction
code-pattern hints and prompt-visible code matches
table header/source-structure hints
schema validation of LLM responses
source-grounding checks for codes, `source_text`, `description`, references, `table_row_indexes`, and `table_header_indexes`
retry/repair when the response fails validation
usage tracking
incremental persistence and resumability
post-extraction normalization and merge/ID support
```

The output is candidates, not final KG nodes.

The SFI extraction result should include:

```text
one result per extraction window, even when zero candidates are found
sfi_candidates for StandardsFrameworkItem candidates only
auxiliary_candidates for examples, exemplars, guidance, activities, competencies, descriptors, resources, or other visible source text that helps explain non-SFI decisions
window_id, window_index, and window_source_segment_ids copied exactly
candidate_id local to the window
statement_code when source-visible, otherwise null
source_text copied or faithfully excerpted from visible source text as a validation evidence quote, not as the final canonical KG statement or sole provenance
description in source language, including visible continuation fragments needed to express the complete official statement
language
normalized_statement_type: Standard | Standard Grouping | Other
statement_type as the source-facing role label when known
table_row_indexes for row-derived table candidates
table_header_indexes for header-derived table candidates
parent_references and ancestor_context_references only when source-visible
confidence and extraction_notes for uncertainty
```

### Step 5. Persist SFI extraction results — implemented

Write:

```text
sfi_extraction_results.jsonl
```

This includes one result per extraction window, even when the result contains zero candidates. Empty-window outputs are useful for coverage/debugging.

Step 5 should persist incrementally:

```text
append one validated SFIExtractionResult to sfi_extraction_results.jsonl after each successful window
rebuild and write sfi_extraction_summary.json after each successful window
when overwrite is false, load existing JSONL results as a completed prefix of the current extraction-window list
when overwrite is false and the existing JSONL contains one valid result for every current extraction window, rebuild/refresh the summary and skip new LLM calls
when overwrite is false and the existing JSONL is partial, rebuild/refresh the summary and resume from the first missing window
when overwrite is true, delete or replace existing Step 4/5 artifacts and start from the first extraction window
when resuming from partial artifacts, treat sfi_extraction_results.jsonl as the source of truth and validate that completed window identities form a prefix of the current extraction window sequence
```

Also write an extraction summary:

```text
sfi_extraction_summary.json
```

Include counts such as:

```text
total windows processed
windows with candidates
windows with auxiliary candidates
windows without SFI candidates
candidate count by normalized statement type
candidate count by source role/table signature when available
auxiliary candidate count
LLM usage counts when available
LLM failure/retry counts if tracked
unresolved/ambiguous counts when available
```

### Step 6. Build a lean global candidate registry — implemented

Add a function such as:

```python
build_candidate_registry(
    *,
    extraction_windows: Sequence[ExtractionWindow],
    kg_config: CreateKGConfig,
    save_fp: Path,
    sfi_extraction_results: Sequence[SFIExtractionResult],
) -> SFIRegistryArtifact
```

Purpose:

```text
Create a simple document-level inventory of extracted SFI candidates before merge and final ID minting.
```

For v0, keep this step intentionally small. The registry is a flatten-and-bucket artifact, not a source-context recovery stage and not a merge stage. It should help the next step find likely duplicates while preserving enough references to return to the persisted `ExtractionWindow` artifact later.

Step 6 should do only the following:

```text
1. Validate that each extraction result aligns with the corresponding extraction window.
2. Flatten window-local SFI candidates into globally addressable registry candidate records.
3. Preserve the original candidate fields and minimal source references.
4. Compute lightweight normalized text/code keys for later duplicate bucketing.
5. Emit possible duplicate buckets and a small registry summary.
```

Validation should check:

```text
result.window_id == window.window_id
result.window_index == window.window_index
result.window_source_segment_ids == window.source_segment_ids
window-local candidate_id values are unique within each extraction result
table_row_indexes and table_header_indexes, when present, refer to rows/headers in that window
existing SFI extraction quality checks still pass when practical
```

Each registry candidate record should preserve the original candidate payload and add only a small wrapper, for example:

```text
registry_candidate_id
window_id
window_index
source_segment_ids
source_window_candidate_id
statement_type
normalized_statement_type
description
source_text
language
confidence
statement_code
table_row_indexes
table_header_indexes
```

`registry_candidate_id` is only a temporary candidate-level handle for review and merge reporting. It is not a final StandardsFrameworkItem ID. A simple deterministic value such as `w0012:sfi_3` or a deterministic UUID from `doc_key + window_id + candidate_id` is sufficient.

Compute only lightweight source-context-aware keys, such as:

```text
normalized_description
normalized_source_text
normalized_statement_code, when statement_code exists and matches configured code policy
source_context_key, derived from source segment/window/table/header/row/block context
code_bucket_key = statement_type + normalized_statement_code, when an accepted normalized code exists
text_bucket_key = statement_type + normalized_description for coded candidates
text_bucket_key = statement_type + source_context_key + normalized_description for no-code candidates
source_text_bucket_key = statement_type + normalized_source_text for coded candidates
source_text_bucket_key = statement_type + source_context_key + normalized_source_text for no-code candidates
```

For coded curricula such as Ghana and Zambia, create possible duplicate buckets from code, description-text, and source-text keys. Same-code buckets are strong review signals, but they are not final identity decisions.

For no-code curricula such as Senegal Reading and Senegal Math, create possible duplicate buckets from source-context-aware description-text and source-text keys so repeated labels are not globally bucketed by text alone.

These buckets are review and merge inputs only. Step 6 must not collapse candidates, choose canonical text, infer parentage, or mint final IDs. Same-code buckets are usually strong merge evidence, but they can still expose conflicts and should remain possible duplicate buckets until Step 7. Same-text buckets in no-code curricula are weaker because repeated labels such as grade, section, palier, activity, topic, or weekly objective can occur under different source contexts.

Emit lightweight warnings, not hard failures, for cases such as:

```text
same statement_type + same statement_code + different normalized descriptions
same statement_code used across multiple statement_types
same statement_type + same normalized text appearing multiple times in one window
same statement_type + same normalized text appearing across many windows
code-like source text but statement_code is null
simple numeric statement_code on grouping candidates
language differs across near-duplicate text buckets
```

Auxiliary candidates may be copied into the registry or summarized by window, but Step 6 should not implement an auxiliary coverage analysis. Keep auxiliary handling minimal unless later merge/export stages need more detail.

Write:

```text
sfi_candidate_registry.json
```

The registry summary should include only basic counts, such as:

```text
candidate_count
auxiliary_candidate_count
candidate_count_by_statement_type
candidate_count_by_normalized_statement_type
candidate_count_by_language
candidates_with_statement_code
candidates_without_statement_code
possible_duplicate_bucket_count
largest_duplicate_buckets
warnings
```

Defer all of the following to later steps:

```text
actual candidate merge/dedup decisions
final SFI ID minting
full source-context recovery from DocumentIR
section_path interpretation
parent/child hints or hasChild inference
canonical final SFI text selection
rich provenance reconstruction
complex bilingual duplicate review
auxiliary policy coverage analysis
```

### Step 7. Merge duplicate candidates globally with bounded LLM dedup review — implemented

Implemented entry point:

```python
merge_sfi_candidates(
    *,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool,
    sfi_candidate_registry: SFIRegistryArtifact,
    usage_tracker: KGUsageTracker,
) -> SFIMergeReport
```

Purpose:

```text
Collapse repeated registry candidates into logical SFI merge groups. Step 7 decides which candidates represent the same curriculum item and preserves their merged source references. It does not mint final SFI IDs, create hasChild edges, recover full DocumentIR source context, choose final canonical KG text, or compile final KG objects.
```

Step 7 consumes the in-memory `SFIRegistryArtifact` produced by Step 6 and writes dedup/merge artifacts under `kg_dirs.root`. Treat Step 6 duplicate buckets and warnings as review-set construction inputs, not merge decisions.

The implemented Step 7 design is LLM-assisted by default, but bounded by deterministic Python retrieval, resumability, and validation:

```text
Python builds source-grounded dedup review edges from duplicate buckets, warnings, and provenance overlap.
Python merges overlapping review edges into connected components.
Python splits oversized connected components by conservative source-derived context before LLM review.
The dedup LLM decides merge / keep_separate / conflict / needs_review within each bounded supplied set.
Python validates every LLM response and converts accepted decisions into merge groups.
Candidates connected to oversized or unsafe components that cannot be reviewed safely become explicit needs_review groups.
Candidates outside any review set become singleton merge groups without an LLM call.
Python validates that final merge groups cover every registry candidate exactly once.
```

Do not send the whole registry to the dedup LLM. Do not iterate candidate-by-candidate. Iterate over bounded review sets built from duplicate buckets, warnings, and source-provenance overlap.

#### Dedup LLM instruction model

Use a general dedup system instruction plus curriculum-specific dedup instructions from `CreateKGConfig`:

```text
general SFI dedup system instruction
config.academic_standards.sfi_deduplication_instructions
config.academic_standards.bilingual_pair_policy, when present
```

`config.academic_standards.sfi_deduplication_instructions` is the single v0 field for curriculum-specific duplicate handling. It covers duplicate-review guidance and repeated-statement policy in one concise instruction. Keep `bilingual_pair_policy` separate because bilingual pairing is optional, narrower, and may apply only to source-local bilingual or mixed-language pair decisions.

The dedup prompt payload should not include broad registry/document metadata such as country, subject, framework title, or registry summary. Those fields are useful for logs and reports, but they do not materially improve bounded dedup decisions. The prompt should include only:

```text
review_set_id
review_reasons
curriculum-specific dedup instructions
optional bilingual_pair_policy
candidate records in the bounded review set
```

#### Build bounded review sets before calling the LLM

Initial review edges are built from these sources when available:

```text
same code duplicate buckets
same description-text duplicate buckets
same source-text duplicate buckets
registry warning groups, including same-code/different-description and repeated-text warnings
same source table row or same source table header provenance overlap among candidates with the same statement_type
```

Then overlapping review edges are merged into connected components before calling the LLM. For example:

```text
bucket A: c1, c2
bucket B: c2, c3
warning C: c3, c4

=> one dedup review component: c1, c2, c3, c4
```

This avoids contradictory decisions across multiple LLM calls.

For v0, review sets must remain small. Use `kg_config.academic_standards.max_dedup_review_set_candidates` when configured; when it is null, keep the current connected component unsplit by size while preserving all other safeguards. Oversized connected components are split by conservative source-derived context:

```text
statement_type
normalized_statement_code or code_bucket_key or source_context_key
source_segment_ids
coarse window_index band
```

If a split residue cannot become a meaningful bounded comparison set, mark it `needs_review` rather than asking the LLM to solve a broad clustering problem or silently treating it as an ordinary singleton.

#### Dedup review request payload

Each LLM review request includes a compact candidate view, not the full registry or full DocumentIR. Include fields needed for dedup only:

```text
registry_candidate_id
statement_type
normalized_statement_type
statement_code
normalized_statement_code
description
source_text
language
window_id
window_index
source_segment_ids
table_row_indexes
table_header_indexes
text_bucket_key
source_text_bucket_key
code_bucket_key
source_context_key
source_context_labels
review reasons for this set
```

Do not include auxiliary candidates unless they are directly needed to explain a duplicate decision. Do not include full extraction windows or full DocumentIR sections in v0 dedup prompts.

#### Dedup LLM output

The dedup LLM response must assign every input candidate to exactly one decision group using a closed enum:

```text
merge
keep_separate
conflict
needs_review
```

The response should include:

```text
review_set_id
decision groups
candidate IDs in each group
short reason
confidence, if present in the schema
```

A `merge` group means the candidates represent the same final source item. `keep_separate` means they are valid separate source items despite lexical/code/context similarity. `conflict` means the candidates appear to claim the same identity but contain materially incompatible text or source context. `needs_review` means the evidence is insufficient for a safe v0 decision.

Python rejects, retries, or marks the set unresolved if the LLM response:

```text
invents candidate IDs
omits any input candidate
assigns a candidate to more than one decision group
uses a decision outside the allowed enum
tries to merge candidates outside the supplied review set
returns an empty reason for a decision group
violates hard merge guardrails, such as merging different statement_type values or different normalized official codes
```

#### Merge rules and guardrails

For coded curricula such as Ghana and Zambia:

```text
Use statement_type + normalized_statement_code as the strongest review-set signal, not as a globally unique identity key.
The dedup LLM should usually merge same statement_type + same normalized_statement_code when text and source context are compatible.
Do not merge candidates with different official codes solely because normalized text is similar.
Do not merge same-code candidates when they have materially different source-visible statements or incompatible source references.
When same-code candidates appear to be distinct source-visible curriculum items, keep them separate as individual SFI candidates and preserve same-code/different-content audit evidence for manual review and Step 8 disambiguated final IDs.
Use conflict or needs_review for same-code candidates only when they appear to be competing/incompatible representations of the same source item, or when the bounded evidence is insufficient to decide whether they are distinct source items.
Treat descriptions that differ only because one includes the visible code and the other omits it as compatible when statement_type, normalized_statement_code, and source context match.
Use configured code_parent_rules later for hierarchy evidence, not for merging different candidate items in Step 7.
```

For no-code curricula such as Senegal Reading and Senegal Math:

```text
Use same statement_type + same normalized source/description text as review-set evidence, not as an automatic merge rule.
The dedup LLM should merge only when source context is compatible.
Context can include source_context_key, source_context_labels, source segment, table local_code, table row/header indexes, extraction window overlap, section/level/stage/domain/palier/week context when already available in candidate/window references, and nearby source order.
Do not globally merge repeated labels or similar no-code objectives by text alone.
Repeated labels such as CE level, palier, week, domain, grade, component, topic, or section headings may be distinct under different contexts.
```

For bilingual or mixed-language curricula:

```text
Follow config.academic_standards.bilingual_pair_policy when present.
```

For all curricula:

```text
The LLM must not invent new candidates.
The LLM must not merge candidates outside the supplied review set.
The LLM must not mint final IDs.
The LLM must not infer hasChild parentage.
The LLM may suggest representative wording only as review evidence; Step 8 remains responsible for deterministic final IDs and final source-backed record construction.
```

#### Merge outputs

Step 7 produces merge groups for every registry candidate. Candidates not included in any LLM review set become singleton groups automatically.

Each merge group preserves enough information for Step 8 and Step 9:

```text
merge_group_id, temporary and deterministic for the report
merged registry_candidate_ids
statement_type and normalized_statement_type, singular only when unambiguous
statement_code / normalized_statement_code, singular only when unambiguous
candidate descriptions and source_text evidence retained for audit
merged source window IDs, source segment IDs, table row indexes, table header indexes, source-context labels, and confidence range
merge_decision: merged | singleton | conflict | needs_review
merge_reason: short deterministic explanation or LLM review reason
```

For v0, ambiguous groups may remain `needs_review` or `conflict` instead of forcing a merge.

Write:

```text
sfi_dedup_review_requests.jsonl
sfi_dedup_review_responses.jsonl
sfi_merge_report.json
sfi_merge_groups.json
sfi_merge_conflicts.json
sfi_merge_needs_review.json
```

When `overwrite=False`, Step 7 may reuse a complete current merge report only after validating companion artifacts, planned review requests, completed review responses, and exact registry-candidate coverage. If a complete report is unavailable, it may resume from a valid review request/response prefix and rewrite JSONL progress to a clean completed prefix before continuing.

Do not do any of the following in Step 7:

```text
mint final StandardsFrameworkItem IDs
choose final canonical KG text from one candidate only
recover full DocumentIR source context
infer hasChild parentage
create StandardsFramework or Relationship objects
attach auxiliary candidates as final metadata except by preserving candidate/source references for later use
```

### Step 8. Mint deterministic final SFI IDs

Add a function such as:

```python
mint_final_sfi_ids(
    *,
    document_ir: DocumentIR,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    sfi_candidate_registry: SFIRegistryArtifact,
    sfi_merge_report: SFIMergeReport,
) -> list[FinalSFIRecord]
```

Purpose:

```text
Assign stable final IDs only after deduplication.
```

For stable-code items, prefer a code-first identity string that still includes deterministic disambiguating material whenever the same source code appears on more than one distinct source-visible item:

```text
lc:curriculum:{doc_key}:sfi:{role}:{statement_code}:{disambiguating_context_or_text_hash_if_needed}
```

Official source codes are strong identity material but are not guaranteed to be globally unique or correct in source PDFs. Step 8 must therefore validate that coded final IDs are collision-free. When Step 7 keeps same-code/different-content candidates separate, Step 8 should mint separate final SFIs with deterministic disambiguators from source-backed text hashes, source-context keys, row/header references, or other stable provenance. It should also preserve the same-code/different-content audit flag and peer references so manual review can inspect the source numbering anomaly.

For no-code items, prefer:

```text
lc:curriculum:{doc_key}:sfi:{role}:{source_context_key}:{normalized_source_text_hash}
```

Then convert the canonical string to UUIDv5.

Each final SFI record should include:

```text
final_sfi_uuid
case_identifier_uuid
case_identifier_uri
identifier
source candidate IDs
final source-backed description text, reconciled from candidate descriptions and recovered source rows/headers when needed
source_text evidence quotes retained for audit/review, not treated as the sole canonical statement text
source and normalized statement type
statement_code, if any
language
source context/disambiguation key when available
metadata/provenance
merged source window IDs, source segment IDs, source page indexes, and source row/header/table provenance
raw/recoverable DocumentIR context references for hasChild resolution
```

Write:

```text
sfi_final_records.json
sfi_final_summary.json
```

When writing final records or summaries that contain UUID fields, dump Pydantic models in JSON mode before passing them to the JSON writer. UUID-bearing records should persist UUIDs as strings, while the in-memory models may still use UUID-typed fields.

### Step 9. Resolve final hasChild edges after SFI finalization

Add a function such as:

```python
resolve_has_child_edges(
    *,
    config: CreateKGConfig,
    document_ir: DocumentIR,
    kg_dirs: KGDirs,
    sfi_final_records: Sequence[FinalSFIRecord],
    sfi_final_summary: FinalSFISummary | None = None,
) -> HasChildResolutionResult
```

Purpose:

```text
Create final `hasChild` edges between the StandardsFramework and finalized SFIs, and among finalized SFIs themselves, after SFI extraction, merge/dedup, and deterministic final SFI ID minting.
```

Relationship resolution must operate on **finalized SFIs only**. The final SFI records define the complete SFI node universe for Step 9. Do not create relationship endpoints from temporary extraction candidates, registry candidate IDs, merge group IDs, source codes, headings, auxiliary candidates, or newly invented parent nodes. Registry candidate IDs and merge group IDs may be used only as provenance/evidence to recover source context and explain decisions.

#### Step 9 preflight validation

Before building any parent candidates or LLM requests, validate the Step 8 outputs:

```text
sfi_final_records.json parses and validates as FinalSFIRecord records.
sfi_final_summary.json, if supplied, agrees with actual sfi_final_records counts.
Every final_sfi_uuid, case_identifier_uuid, identifier, and case_identifier_uri is present.
For v0, final_sfi_uuid == case_identifier_uuid == identifier, and case_identifier_uri == urn:uuid:{final_sfi_uuid}.
Every final_sfi_uuid is unique.
Every identity_key is unique.
Every merge_group_id is unique across final records.
Every source_registry_candidate_id appears in exactly one final record.
No final record has an empty description.
No final record is missing source_window_ids, source_segment_ids, candidate_source_refs, or source_registry_candidate_ids.
Excluded conflict and needs-review group counts are zero unless the config explicitly allows relationship resolution over an incomplete SFI universe.
```

If any of these checks fail, fail Step 9 before calling the LLM. Relationship generation over an incomplete or ambiguous final SFI universe is more harmful than producing no relationships.

Build explicit lookup indexes before retrieval:

```text
by final_sfi_uuid / case_identifier_uuid
by merge_group_id
by statement_type and normalized_statement_type
by normalized_statement_code when present
by raw statement_code when present
by source_window_id and source_window_index
by source_segment_id
by source_context_key
by source table row/header references when present
by source order position
```

The lookup indexes are retrieval aids only. They do not by themselves decide direct parents except where the configured rules are unambiguous and validation confirms the choice.

#### Recover Step 9 source context after finalization

Step 9 should create a recovered context package for every finalized SFI before parent selection. This can be an internal object or a persisted debug artifact.

For each final SFI, recover and retain:

```text
final_sfi_uuid / case_identifier_uuid
statement_type, normalized_statement_type, statement_code, normalized_statement_code
description and candidate_source_texts
merge decision, merge group ID, audit flags, and audit peer merge group IDs
source_window_ids and source_window_indexes
source_segment_ids
candidate_source_refs including table_row_indexes, table_header_indexes, and source_context_labels
raw DocumentIR section_path for each source segment
DocumentIR segment type and source order
page indexes / page ranges as coarse evidence only
nearby preceding and following finalized grouping SFIs in source order
same-table, same-row, same-header, filldown, and local-code evidence when available
configured code_parent_hints or code_parent_rules that match the normalized code, when safe
```

Write this debug/context artifact when useful:

```text
sfi_final_contexts.json
```

`source_page_indexes` may be broad for table-derived records because a source segment can span many pages. Do not use page overlap alone as direct parent evidence. Prefer source window order, source segment order, table row/header provenance, source_context_keys, source-visible labels, and recovered section-path evidence.

#### Raw section_path and source_context_labels are evidence, not a clean ancestor chain

A DocumentIR `section_path` may contain stale sibling headings, previous sections, or multiple repeated headings. Registry/final-record `source_context_labels` may also include inherited stale headings around layout transitions. Do not treat either list as a clean final parent chain.

Instead, provide these ordered labels to the relationship-resolution LLM as source-visible evidence and ask it to infer the active context at the SFI's source position. Earlier sibling headings should be considered superseded when a later heading of the same hierarchy level appears.

For example, if the raw section path contains old sub-strands followed by:

```text
BASIC 6
Strand 1: Number
Sub-Strand 1: Counting, Representation, Cardinality & Ordinality
Sub-Strand 2: Number Operations
Sub-Strand 3: Fractions
```

then a relationship-resolution prompt for an SFI sourced after `Sub-Strand 3: Fractions` should ask the LLM to infer the active context as:

```text
BASIC 6 -> Strand 1: Number -> Sub-Strand 3: Fractions
```

Python should not hard-code country-specific heading level grammar. The generic prompt can explain how to interpret ordered heading evidence, and `config.academic_standards.sfi_has_child_instructions` should supply document-specific direct hierarchy grammar such as Grade -> Strand -> Sub-Strand -> Content Standard -> Indicator when available.

#### Code evidence and audited code anomalies

Codes are strong evidence but not absolute truth. Source PDFs may reuse a code for different content, omit separators, use visible codes that do not match configured patterns, or attach a content-standard-shaped code to an indicator row.

Rules for code use in Step 9:

```text
Use normalized_statement_code for code hierarchy retrieval only when it is present and accepted by configured code patterns.
If statement_code is present but normalized_statement_code is null, treat the raw statement_code as source-visible text evidence only; do not parse or normalize it ad hoc in Step 9.
A code-derived direct parent is safe only when the configured rule points to exactly one finalized parent candidate of a compatible role and no source-context evidence contradicts it.
If multiple finalized SFIs share the same normalized code, do not choose among them by code alone.
If the child or candidate parent has same_code_different_content audit flags, require source-context/table/order evidence or LLM selection from explicit candidates; never collapse or shortcut by normalized code alone.
If source code and recovered source context disagree, include both signals in the LLM request and prefer unresolved/review over guessing.
```

Same-code/different-content final SFIs are valid, separate nodes. Step 9 must preserve them as separate possible relationship endpoints. When one is a plausible parent candidate, include audit flags, audit notes, peer IDs, source text, and source context evidence so the LLM can distinguish the intended node.

#### Candidate parent retrieval

For each child final SFI, Python should construct a bounded, source-grounded candidate parent set. Retrieval should be mechanical and auditable, not a hard-coded curriculum hierarchy walker.

Always include:

```text
1. StandardsFramework root fallback candidate.
2. Any directly matched configured code-parent candidate that is unique and validation-safe.
3. Finalized grouping SFIs whose source text/description matches active labels in the child's recovered section_path or source_context_labels.
4. Nearby preceding finalized grouping SFIs in source order, especially those whose source window/segment context overlaps or immediately precedes the child.
5. Finalized SFIs from the same source table, row/header group, filldown group, local_code, or code family when available.
6. Finalized SFIs referenced by source-visible labels/codes or extraction-time code-parent hints, if those resolve to finalized SFI records.
7. Audit peer records when the child or a candidate is same_code_different_content, so the LLM can explicitly distinguish them.
```

Candidate sets may include broader ancestors as well as plausible direct parents. The LLM chooses the direct parent from the candidate set or marks the child unresolved. The LLM must not invent new parent nodes.

Bound candidate sets to keep the LLM task reliable. Prefer strong source-evidence candidates first:

```text
unique code-parent candidate with compatible source context
active recovered Grade / Strand / Sub-Strand labels that match finalized grouping SFIs
same table / same local code / same source segment candidates
nearby preceding grouping candidates by source order
root fallback
```

If the candidate set is still too large, split or rank by evidence strength, but never omit the root fallback.

#### LLM-assisted parent selection batches

Process finalized SFIs in batches of children to place, for example 8-20 children per call. Each child may have a different candidate parent set pulled from the full finalized SFI registry.

Each relationship-resolution request should include:

```text
`config.academic_standards.sfi_has_child_instructions`
child SFIs to place
for each child: final_sfi_uuid, description, statement type, normalized statement type, statement_code, normalized_statement_code, audit flags, source provenance, recovered source context, and raw section_path evidence
for each child: candidate parent SFIs with IDs, labels/text, statement types, codes, audit flags, source context, and evidence for why each candidate was retrieved
StandardsFramework root fallback candidate
closed-enum instruction to choose exactly one direct parent when evidence supports it, otherwise mark unresolved
instruction to avoid transitive/ancestor edges when a more specific direct parent candidate exists
instruction to choose among same-code audited peers by source context, not by code alone
instruction not to create, rename, merge, or delete any SFI
```

This differs from a pure sliding-window approach. The batch controls which children are resolved together; it does not restrict possible parents to the local batch. Parent candidates may come from anywhere in the finalized SFI registry as long as they are retrieved by source-grounded evidence.

The `sfi_has_child_instructions` field is required in v0. If it is missing or blank, fail Step 9 preflight before building parent-selection requests.

The LLM response should be schema-validated and closed-enum, for example:

```text
child_final_sfi_uuid
selected_parent_kind = StandardsFramework | StandardsFrameworkItem | unresolved
selected_parent_uuid, null only when unresolved
relationship_type = hasChild
confidence
reason
evidence_used
unresolved_reason, if any
```

Validate every response before accepting it:

```text
Every child in the request appears exactly once in the response.
No unknown child IDs.
No unknown selected parent IDs.
No selected parent equals child.
Selected parent is either the StandardsFramework root or one of the provided candidate parents.
The response does not invent relationship types.
The response does not introduce multiple direct parents for one child.
The reason/evidence is non-empty.
```

With `overwrite=False`, Step 9 may resume from a valid request/response prefix only after validating that the current final SFI universe, request identities, candidate parent sets, and accepted responses still match the current inputs exactly. Otherwise, rebuild Step 9 artifacts from scratch.

#### Edge assembly and unresolved handling

After validating responses, assemble one direct parent decision per final SFI.

Rules:

```text
Every finalized SFI must have at most one direct hierarchy parent.
Top-level grouping SFIs should normally attach to the StandardsFramework root.
A StandardsFrameworkItem may be the parent of another StandardsFrameworkItem.
Create StandardsFramework -> SFI edges only when the framework is the direct parent or explicit root fallback.
Create SFI -> SFI edges only between finalized SFIs.
Do not create self-loops.
Do not create cycles.
Do not create buildTowards, relatesTo, supports, descriptor, guidance, activity, or auxiliary relationships in this step.
Do not make descriptor/guidance/activity/auxiliary content into parents unless KG config policy explicitly exports those as SFIs.
Do not silently drop a finalized SFI from the relationship universe.
```

For unresolved children, use an explicit policy rather than guessing:

```text
If the config requires a fully reachable v0 KG, emit a StandardsFramework root fallback edge marked with resolution_status = unresolved_root_fallback and also write the unresolved decision to unresolved_edges.json.
If the config requires strict hierarchy only, do not emit a fallback edge and fail validation until the unresolved item is reviewed.
In either mode, unresolved decisions must be counted and inspectable.
```

For v0 Academic Standards KG export, a fully reachable graph is usually preferable, but unresolved root fallbacks must remain visible in debug/validation artifacts so they are not mistaken for high-confidence semantic hierarchy.

Deterministic relationship identity:

```text
lc:curriculum:{doc_key}:rel:hasChild:{source_case_identifier_uuid}:{target_case_identifier_uuid}
```

where `source_case_identifier_uuid` is the StandardsFramework UUID or parent SFI `case_identifier_uuid`, and `target_case_identifier_uuid` is the child SFI `case_identifier_uuid`.

Write:

```text
sfi_final_contexts.json
has_child_candidate_parent_sets.jsonl
has_child_resolution_requests.jsonl
has_child_resolution_responses.jsonl
final_has_child_edges.json
unresolved_edges.json
has_child_resolution_summary.json
```

#### Step 9 validation checklist

Before Step 10 compilation, validate:

```text
Every final SFI appears exactly once as a child decision.
Every accepted edge source exists: StandardsFramework root or finalized SFI.
Every accepted edge target exists: finalized SFI.
No self-loops.
No directed cycles among SFI -> SFI edges.
No child has more than one direct parent.
Every final SFI is reachable from StandardsFramework, unless strict mode intentionally fails on unresolved items.
Every unresolved/root-fallback edge is explicitly marked and listed in unresolved_edges.json.
Every same-code/different-content audited final SFI remains a separate node and is not collapsed by code.
No relationship endpoint uses registry_candidate_id, merge_group_id, statement_code, source_segment_id, or any non-final UUID as an entity key.
All relationship IDs are deterministic and collision-free.
Summary counts agree with edge records and unresolved records.
```

### Step 10. Compile Academic Standards KG objects

Add a function such as:

```python
compile_academic_standards_kg(config: CreateKGConfig, document_ir, sfi_final_records, has_child_edges) -> AcademicStandardsKGArtifacts
```

Purpose:

```text
Create actual KG schema objects.
```

Objects:

```text
StandardsFramework
StandardsFrameworkItem
Relationship(hasChild)
```

Framework policy:

```text
Default: one StandardsFramework per PDF.
Use `RunConfig.kgs` / `CreateKGConfig` metadata for title, subject, jurisdiction, provider, language, license, author, attribution statement, adoption status, and framework description.
Include doc_key and source PDF metadata in metadata.
```

SFI policy:

```text
Grouping nodes -> normalized_statement_type = Standard Grouping
Normative expectation nodes -> normalized_statement_type = Standard
Descriptors/guidance/activity/auxiliary -> drop, attach to expectation metadata, or export as Other according to KG config policy
Preserve source statement_type and statement_code where available
Preserve provenance in metadata/entity_provenance
```

Relationship policy:

```text
Framework/SFI -> SFI only
relationship_type = hasChild
source_entity_key = case_identifier_uuid
target_entity_key = case_identifier_uuid
deterministic relationship identifier
```

### Step 11. Validate and write Academic Standards artifacts

Add a function such as:

```python
validate_and_write_academic_standards(artifacts, kg_dirs) -> Path
```

Purpose:

```text
Ensure the Academic Standards KG bundle is schema-valid, internally consistent, and debuggable.
```

Validation checks:

```text
All objects validate against schemas.py.
Every hasChild source/target exists.
Every StandardsFrameworkItem is reachable from the StandardsFramework root.
No hasChild self-loops.
No hasChild cycles.
Every SFI has non-empty description text.
Every SFI has deterministic identifier/case_identifier_uuid/case_identifier_uri.
Every SFI has provenance or a clear synthetic provenance explanation.
No-code SFIs have stable synthetic context/text keys.
Dropped/attached auxiliary content is accounted for in a policy coverage report.
Zero-window or zero-SFI output fails unless explicitly allowed by KG config.
```

Write the initial Academic Standards artifact bundle:

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

The final success condition for v0a:

```text
A user can run create_kgs.py on a stitched DocumentIR and receive a validated Academic Standards KG artifact bundle created from LLM-extracted SFI candidates, containing one StandardsFramework, many StandardsFrameworkItems, and valid hasChild relationships, with deterministic IDs and provenance.
```

---

## Suggested implementation boundaries

Do not define every possible intermediate schema up front. Define schemas as each boundary is implemented.

Implemented intermediate schemas now include:

```text
ExtractionWindowPlanItem
ExtractionWindowPlanArtifact
ExtractionWindow
ExtractionWindowTablePayload
CodeMatch
CodeParentHint
SFIExtractionResult
SFIExtractionSummary
SFICandidate
SFIAuxiliaryCandidate
SFICandidateParentReference
```

Recommended next intermediate schemas:

```text
SFIDedupReviewRequest
SFIDedupReviewResponse
SFICandidateRegistry
SFIMergeReport
HasChildResolutionRequest
HasChildResolutionResponse
CandidateParentSet
FinalSFIRecord
FinalSFIRecoveredSourceContext
SourceContextGroupKey
FinalHasChildEdge
AcademicStandardsKGArtifacts
AcademicStandardsValidationReport
```

`SourceContextGroupKey` is an optional Python-derived helper used only for relationship-resolution batching/retrieval. It may be computed deterministically from recovered raw `section_path` or other source-context evidence after final SFI records are minted. It must never be treated as an asserted curriculum hierarchy edge.

All LLM outputs should validate against schemas. Do not pass unvalidated ad hoc dictionaries between implemented stages.

---

## Practical coding notes for the next session

Start from the validated Step 8 artifacts:

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

The next coding target is Step 9:

```text
resolve_has_child_edges()
```

Step 9 should consume finalized SFI records and produce direct `hasChild` decisions. It should not mint new SFI IDs, revisit candidate extraction, redo dedup, compile final KG schema objects, create LearningComponents, create LearningProgressions, or emit non-hasChild relationships.

Implement Step 9 as:

```text
1. Load and validate sfi_final_records.json and sfi_final_summary.json.
2. Validate that `config.academic_standards.sfi_has_child_instructions` is present and non-empty.
3. Validate that final records are UUID/identity/candidate-coverage safe and that no conflict or needs_review groups were silently included.
4. Build lookup indexes over finalized SFIs by final UUID, statement type, normalized code, raw code, source windows, source segments, source context keys, and source order.
5. Recover source context for each final SFI from DocumentIR and final-record provenance.
6. Persist sfi_final_contexts.json as a debug artifact if useful.
7. Build bounded, source-grounded candidate parent sets for every final SFI using generic evidence retrieval plus `sfi_has_child_instructions` for direct hierarchy grammar.
8. Treat code-parent matches as evidence only when normalized codes are present, unique, compatible, and not contradicted by audit/source-context evidence.
9. Treat same-code/different-content audited records as separate final nodes; never collapse or select by code alone.
10. Treat raw statement_code values with null normalized_statement_code as source-visible evidence only.
11. Run LLM-assisted parent selection over batches of children, with each child receiving its own candidate parent set, the StandardsFramework root fallback, and `sfi_has_child_instructions`.
12. Validate every response for exact child coverage, known parent IDs, no self-loops, no invented relationship types, and one direct parent decision per child.
13. Assemble deterministic hasChild edges, preserving unresolved/root-fallback status where needed.
14. Validate graph-level constraints: every finalized SFI has one parent decision, every endpoint exists, no cycles, no duplicate direct parents, and all relationship IDs are deterministic.
15. Persist final_has_child_edges.json, unresolved_edges.json, request/response JSONL, candidate parent sets, and has_child_resolution_summary.json.
```

Recommended Step 9 function shape:

```python
resolve_has_child_edges(
    *,
    config: CreateKGConfig,
    document_ir: DocumentIR,
    kg_dirs: KGDirs,
    sfi_final_records: Sequence[FinalSFIRecord],
    sfi_final_summary: FinalSFISummary | None = None,
) -> HasChildResolutionResult
```

Step 9 should preserve the distinction between source evidence and final graph assertions:

```text
sfi_final_records define the only valid SFI relationship endpoints.
source_registry_candidate_ids, merge_group_id, source_window_ids, source_segment_ids, and table row/header references are provenance/evidence, not entity IDs.
raw section_path and source_context_labels are ordered evidence, not a clean ancestor chain.
source_page_indexes are coarse evidence and may be too broad for direct parent selection.
normalized_statement_code is useful only when present and configured; raw statement_code alone should not be parsed ad hoc.
audit_flags must be passed through candidate parent sets and prompts so audited same-code records remain distinguishable.
```

Lessons from reviewed Ghana Step 8 artifacts:

```text
The final SFI universe is complete and suitable for Step 9: 306 final records from 317 registry candidates.
Same-code/different-content records are intentionally separate and must stay separate during relationship generation.
Several organizer records have raw numeric statement_code values but null normalized_statement_code; this is expected and should not block context-based hierarchy resolution.
At least one source-visible code-like value is intentionally preserved without normalized_statement_code; relationship resolution must not require normalized codes.
Some table-derived source_page_indexes are broad; use source windows, source segments, table rows, and recovered context before page overlap.
Raw section_path/source_context_labels can be stale around transitions; give them to the LLM as ordered evidence rather than direct parent chains.
```

After Step 9 hasChild resolution is implemented, continue with:

```text
compile_academic_standards_kg()
validate_and_write_academic_standards()
```

The SFI extraction prompt should focus on extracting SFI and auxiliary candidates. The persisted full `ExtractionWindow` remains the source-faithful validation artifact, while Step 4 may send a compact prompt payload to reduce token cost and model distraction. Runtime config instructions are authoritative for document-specific extraction policy when they conflict with generic prompt text. Candidate `source_text` is a source-visible validation quote, not final KG statement text or sole provenance; final SFI records should recover or retain complete source-backed text/provenance from merged source references. The hasChild-resolution prompt should run after final SFI records exist; it should receive each child's recovered raw `section_path`, source provenance, and candidate parent SFIs from the finalized registry, then choose the direct parent or mark unresolved.

Do not begin with LearningComponents or LearningProgressions. Those should be behind later flags and should consume final SFIs, not candidates. The LearningComponent phase should decompose finalized Standard SFIs into atomic LearningComponent nodes and create `supports` edges from each LearningComponent to the relevant finalized SFI. A separate LearningProgressions phase may later create `buildsTowards` and `relatesTo` edges among finalized SFIs; those relationships should not be emitted by the LearningComponent phase.

---

## Known risk areas to keep in mind

### Raw section_path can be incomplete, stale, or misleading

Do not treat raw DocumentIR `section_path` as a clean final ancestor chain. It may include stale sibling headings or prior sections. In the hasChild resolver, provide the raw ordered `section_path` as evidence and instruct the LLM to infer the active context at the source position, such as latest applicable grade/basic, strand, and sub-strand. The resolver should keep ambiguous or conflicting parent choices in review artifacts rather than guessing.

### Optional table helpers can be missing

The KG stage should tolerate missing `rows_grid`, `grid_sources`, `row_provenance`, or `rows_filldown` fields. Use them when present, but keep raw rows/provenance as the fallback.

### Bilingual duplicate policy should be conservative

For Senegal, do not globally merge Wolof and French statements just because they look similar. For v0, prefer same-row or same-table-local pairing only when KG config policy allows it. Otherwise preserve both and mark possible bilingual duplicates.

### Descriptor/guidance leakage

Do not let activities, exemplars, duration, resources, or teacher guidance become `Standard` SFIs unless the KG config explicitly says they are in scope. Default behavior should be conservative:

```text
expectation -> SFI Standard
structural context -> SFI Standard Grouping
descriptor/guidance/activity -> attach/drop/Other according to policy
```

### ID churn

Avoid hashing LLM-cleaned or translated text as the primary identity source. Hash normalized source text from the DocumentIR when text hashing is needed.

### Zero-output failures

A run that produces zero extraction windows or zero final SFIs should fail by default. Silent empty KG artifacts are worse than a hard error during v0.
