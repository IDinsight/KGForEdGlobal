# Session Instructions for the Creation of Academic Standards KG Artifacts v0

## Purpose of this file

Use this file at the start of the coding session to establish the shared context quickly and begin implementation without re-reviewing the full curriculum mapping background.

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

Academic Standards KG creation is an **LLM extraction pipeline**. Python prepares source-faithful windows, adds profile and code hints, validates structured responses, merges duplicates, mints deterministic IDs, prepares source-grounded parent-candidate sets, resolves relationships with LLM assistance when needed, and exports artifacts. The LLM performs semantic extraction from each window into SFI candidates. After SFIs are finalized, the LLM also helps choose direct `hasChild` parents from source-grounded candidate parent sets derived from the finalized SFIs' provenance and DocumentIR context. Deterministic rules are guardrails, retrieval aids, validation checks, and helpers, not a replacement for LLM semantic judgment.

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
    - `create_kgs.py`: the main entry point for creating the KGs from the document IR and document profile JSON files
    - `create_extraction_windows.py`: contains functionalities for creating LLM-ready extraction windows from the DocumentIR and the document profile
    - `schemas.py`: schemas for the KG pipeline
    - `utils.py`: utility functions for the KG pipeline
    - `run_config_schemas.py`: contains the run config schemas (renamed to avoid conflict)
    - `schemas_document_ir.py`: schemas for stitched DocumentIR segments, including BlockSegment, TableSegment, provenance, table rows, rows_grid, grid_sources, row_provenance, and rows_filldown (renamed to avoid conflict)
2. JSON files:
    - `config_math_curriculum.json`: the runtime config parameters (note that some parameters for the KG pipeline are outdated/unused and can probably be removed if not useful)
    - `document_ir.json`: the stitched DocumentIR output for Ghana's math curriculum PDF
    - `document_profile_math.json`: the DocumentProfile for Ghana's math curriculum PDF
    - `kg_run_manifest.json`: output from the current KG prep stage for the Ghana math run; useful as the sanity-check baseline before implementing extraction windows

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

The KG creation layer takes a stitched `DocumentIR` plus a country/document-specific `DocumentProfile` and uses LLM-driven semantic extraction, followed by deterministic validation/merge/export, to turn the source material into validated KG-shaped artifacts.

For v0, this layer should focus on Academic Standards only:

```text
DocumentIR + DocumentProfile
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

The current v0 implementation now covers the prep and base extraction-window artifact stages:

```text
1. Load and validate the stitched DocumentIR and DocumentProfile.
2. Cross-check basic profile/document compatibility.
3. Build and persist kg_run_manifest.json.
4. Plan extraction windows from DocumentIR segments.
5. Persist extraction_window_plan.json.
6. Build base LLM-ready extraction windows.
7. Persist extraction_windows.jsonl.
```

Do **not** add a separate context-enrichment pass to `extraction_windows.jsonl` for v0. The extraction windows should stay source-faithful and include source text, block/table payloads, provenance, table helper views, profile instructions, code matches, and code-parent hints. Broader source context such as `section_path` should be recovered later from the DocumentIR after SFIs have been finalized and should be used during `hasChild` relationship resolution.

The CLI cross-checks that the KG run is using the same PDF bytes/doc_key as the extraction and stitching run, creates the KG output directory, persists `kg_run.json`, and then calls `build_kgs(...)`.

The next coding target is Step 4: run LLM extraction over the existing base extraction windows to produce validated SFI extraction results. After candidate merge/dedup and final SFI ID minting, recover section paths/source context from the DocumentIR and use those signals to resolve `hasChild` edges.

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

### 3. Profile-driven, not country-hardcoded

The `DocumentProfile` is the country/document instruction sheet. It should tell the pipeline:

```text
what counts as a grouping SFI
what counts as a normative expectation SFI
what counts as descriptor/guidance/activity/auxiliary material
which table signatures/sections are eligible for extraction
which table signatures/sections are excluded
what code patterns exist, if any
which fields should be used for no-code synthetic merge keys
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
profile extraction instructions
```

Do not require extraction windows to include additional `source_context_hints`, nearby heading packages, or a precomputed active context path. Relationship context should be recovered later from the stitched DocumentIR using the finalized SFI's source provenance.

The hasChild-resolution system instruction should describe the curriculum-specific hierarchy grammar:

```text
which finalized SFI roles are expected to be children of the StandardsFramework root
which finalized SFI roles are expected to be direct parents of other SFIs
how to interpret raw DocumentIR section_path as ordered heading history
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
provide profile-derived hints and constraints
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
interpret dense table cells using profile rules
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
plan_extraction_windows(*, document_ir, document_profile, save_fp) -> list[ExtractionWindowPlanItem]
```

Purpose:

```text
Walk `DocumentIR.segments` in source order and plan the source units that should become Academic Standards extraction windows.
```

Rules:

```text
Block segments:
- Plan one block extraction window for every block segment with extractable source text.
- Do not require profile block-selection rules.
- Do not make Python decide whether a block contains SFIs. The LLM extraction step decides whether the block yields SFI candidates, auxiliary records, or no candidates.

Table segments:
- Apply profile-driven table selection.
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
build_llm_extraction_windows(*, document_ir, document_profile, plan_items, save_fp) -> list[ExtractionWindow]
```

Purpose:

```text
Convert planned DocumentIR source units into stable prompt payloads for LLM-based Academic Standards extraction.

The base window must preserve source text, block/table payloads, provenance, table helper views, code hints, profile instructions, and deterministic source-derived keys. It does not need a separate context-enrichment layer for v0; broader hierarchy context is recovered later from DocumentIR provenance after SFIs are finalized.
```

This step produces the inputs that will be sent to the LLM. It is not a semantic extraction step. Python packages the exact source text, table structure, optional helper views, provenance, code hints, profile instructions, and deterministic source-derived keys needed for reliable LLM extraction.

Step 2 is the LLM prompt-payload construction stage. Step 4 is the LLM call and structured-response validation stage.

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
profile extraction instructions
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
Keep extraction windows focused on source text, block/table payloads, provenance, table helper views, profile instructions, code matches, and code-parent hints. Defer hierarchy-context interpretation until after SFI extraction, merge/dedup, and final SFI ID minting.
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

### Step 4. Run LLM extraction to create SFI candidates — next target

Add a function such as:

```python
extract_sfi_candidates_with_llm(*, document_profile, windows) -> list[SFIExtractionResult]
```

Purpose:

```text
Use the LLM to convert each base extraction window into candidate StandardsFrameworkItems and auxiliary records, guided by the DocumentProfile and the window's source/provenance/table/code payload.
```

Use the LLM as the primary semantic extractor for Academic Standards v0. The LLM should return candidates that validate against the SFI extraction response schema. Deterministic code should not bypass this LLM candidate-extraction stage except for mechanical framework-root creation and later validation/export bookkeeping.

The LLM should identify:

```text
structural grouping SFI candidates
normative expectation SFI candidates
descriptor/guidance/activity/auxiliary records that should not become Standard SFIs by default
source-language text and language tags
statement codes when visible
source provenance sufficient to recover DocumentIR context after merge/dedup
uncertainties about candidate role, statement type, code, or auxiliary status
```

Use deterministic logic around the LLM call for:

```text
prompt construction
code-pattern hints
code parent-rule hints
table header/source-structure hints
schema validation of LLM responses
retry/repair when the response fails validation
post-extraction normalization and merge/ID support
```

The output is candidates, not final KG nodes.

The SFI candidate schema must support:

```text
candidate_id: deterministic window-local or source-local ID
statement_code: Optional[str]
source_text: required
in_language: required
statement_type/source role: required when known
normalized_statement_type: Standard | Standard Grouping | Other
candidate role: expectation | descriptor | guidance | grouping | auxiliary
source_window_ids and source_segment_ids: required for later DocumentIR context recovery
source row/table provenance: required when table-derived and available
source provenance: required
synthetic_context_key/no-code merge fields: required when statement_code is missing
confidence/rationale when LLM-derived
extraction_notes: optional notes about ambiguity in candidate boundaries, role, or source interpretation
```

### Step 5. Persist SFI extraction results

Write:

```text
sfi_extraction_results.jsonl
```

This should include one result per extraction window, even when the result contains zero candidates. Empty-window outputs are useful for coverage/debugging.

Also write an extraction summary if helpful:

```text
sfi_extraction_summary.json
```

Include counts such as:

```text
total windows
windows with candidates
candidate count by normalized statement type
candidate count by source role/table signature
LLM failure/retry counts if applicable
unresolved/ambiguous counts
```

### Step 6. Build the global candidate registry

Add a function such as:

```python
build_candidate_registry(sfi_extraction_results) -> SFICandidateRegistry
```

Purpose:

```text
Collect all candidates globally before merge and final ID minting.
```

The registry should normalize and index candidates by:

```text
source code when present
normalized source text hash
source-derived context/disambiguation key
statement/source role
language
source segment/table/row/cell provenance
source window IDs and source segment IDs needed for post-dedup context recovery
```

Write:

```text
sfi_candidate_registry.json
```

This registry is the first document-level view of what was extracted.

### Step 7. Merge duplicate candidates globally

Add a function such as:

```python
merge_candidates(candidate_registry, document_profile) -> SFIMergeResult
```

Purpose:

```text
Collapse repeated candidates into one final logical SFI record with merged provenance and enough source references to recover DocumentIR context for relationship resolution.
```

Expected duplicate sources:

```text
overlapping windows
rows_filldown repetition
same official code appearing in multiple windows
same no-code objective repeated in bilingual/section-local structures
continuation tables split across pages
same grouping/context surfaced in multiple heading or table windows
```

For coded curricula:

```text
same statement_code + compatible role/context -> merge
same statement_code + conflicting source text/context -> review or disambiguate
```

For no-code curricula:

```text
doc_key + subject + source-visible level/stage/week/palier/grouping context + source role + normalized source text -> likely merge key
```

Use bounded LLM duplicate review only when deterministic rules cannot decide.

Write:

```text
sfi_merge_report.json
duplicate_review_requests.jsonl
duplicate_review_responses.jsonl
```

### Step 8. Mint deterministic final SFI IDs

Add a function such as:

```python
mint_final_sfi_ids(merge_result, document_ir, document_profile) -> list[FinalSFIRecord]
```

Purpose:

```text
Assign stable final IDs only after deduplication.
```

For stable-code items, prefer:

```text
lc:curriculum:{doc_key}:sfi:{role}:{statement_code}:{disambiguating_context_if_needed}
```

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
source_text / description text
source and normalized statement type
statement_code, if any
language
source context/disambiguation key when available
metadata/provenance
merged source window IDs, source segment IDs, source page indexes, and source row/table provenance
raw/recoverable DocumentIR context references for hasChild resolution
```

Write:

```text
final_sfi_records.json
```

### Step 9. Resolve final hasChild edges after SFI finalization

Add a function such as:

```python
resolve_has_child_edges(final_sfi_records, document_ir, document_profile) -> HasChildResolutionResult
```

Purpose:

```text
Create final `hasChild` edges between the StandardsFramework and finalized SFIs, and among finalized SFIs themselves, after SFI extraction, merge/dedup, and deterministic final SFI ID minting.
```

Relationship resolution should operate on **finalized SFIs**, not temporary extraction candidates. Each final SFI should carry merged source provenance that lets Python recover its source context from the stitched DocumentIR.

Use these inputs in a generic and curriculum-agnostic resolution process:

```text
finalized SFI records and their stable IDs
source window IDs and source segment IDs for each finalized SFI
raw DocumentIR section_path for each source segment
source order, source page ranges, and neighboring finalized grouping SFIs
source table/row/filldown provenance when available
code parent hints from configured code_parent_rules
source codes/source labels/source roles that can be matched to finalized SFIs
DocumentProfile hierarchy instructions for the relationship-resolution LLM
StandardsFramework root as fallback parent
```

#### Raw section_path is ordered heading history, not a clean ancestor chain

A DocumentIR `section_path` may contain stale sibling headings or multiple repeated headings from earlier sections. Do not treat the full list as a final parent chain. Instead, provide the raw path to the relationship-resolution LLM as ordered source-visible heading evidence and instruct it to infer the active context at the SFI's source position.

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

Earlier sibling headings should be considered superseded when a later heading of the same level appears. Python should not hard-code the level grammar; the DocumentProfile and LLM instructions should explain the curriculum's expected hierarchy.

#### Candidate parent retrieval

For a batch of children to place, Python should construct a candidate parent set for each child. Candidate retrieval should be mechanical and source-grounded, not a hard-coded curriculum hierarchy walker.

Include candidate parents from these generic sources when available:

```text
1. StandardsFramework root fallback.
2. Finalized SFIs whose statement_code matches a profile-derived code_parent_hint.
3. Finalized grouping SFIs whose source text/label matches components in the child's raw section_path.
4. Nearby preceding finalized grouping SFIs in source order, especially those whose page span or source context overlaps the child.
5. Finalized SFIs from the same source table, row, filldown group, or code family.
6. Finalized SFIs referenced by any source-visible label/code hints emitted during extraction, if present.
```

The candidate set may include broader ancestors as well as plausible direct parents. The LLM chooses the direct parent from the candidate set or marks the child unresolved. The LLM must not invent new parent nodes.

#### LLM-assisted parent selection batches

Process finalized SFIs in batches of children to place, for example 8-20 children per call. Each child may have a different candidate parent set pulled from the full finalized SFI registry.

Each relationship-resolution request should include:

```text
curriculum-specific hierarchy instructions from DocumentProfile
child SFIs to place
for each child: source text, statement type, normalized statement type, statement_code, source provenance, and raw section_path evidence
for each child: candidate parent SFIs with IDs, labels/text, statement types, codes, and evidence for why each candidate was retrieved
StandardsFramework root fallback candidate
instruction to choose exactly one direct parent when evidence supports it, otherwise mark unresolved
instruction to avoid transitive/ancestor edges when a more specific direct parent candidate exists
```

This differs from a pure sliding-window approach. The batch controls which children are resolved together; it does not restrict possible parents to the local batch. Parent candidates may come from anywhere in the finalized SFI registry as long as they are retrieved by source-grounded evidence.

For coded curricula, code parent rules may resolve high-confidence direct parents when configured, or may be passed to the LLM as high-confidence evidence:

```text
B4.1.1.1.1 -> parent B4.1.1.1
3.9.4.1 -> parent inferred by configured code hierarchy, when safe
```

For no-code curricula, rely on raw section_path evidence, source order, nearby finalized grouping SFIs, and LLM hierarchy-resolution instructions rather than Python hard-coded hierarchy logic.

Rules:

```text
Every emitted SFI must be reachable from the StandardsFramework root.
Start with a temporary root scaffold if helpful: StandardsFramework -> every finalized SFI.
Replace root edges with more specific direct parent edges only when candidate evidence resolves cleanly.
Create StandardsFramework -> SFI edges for top-level finalized grouping SFIs when the root is the direct parent.
Create SFI -> SFI edges only between finalized SFIs.
Do not create self-loops.
Do not create cycles.
Do not allow more than one direct hierarchy parent for an SFI unless explicitly allowed by profile policy.
Keep unresolved or conflicting parent choices in unresolved_edges.json rather than guessing.
Do not make descriptor/guidance/activity nodes into parents unless profile policy explicitly allows it.
Do not create buildTowards or relatesTo edges in this step.
```

Write:

```text
has_child_resolution_requests.jsonl
has_child_resolution_responses.jsonl
final_has_child_edges.json
unresolved_edges.json
```

### Step 10. Compile Academic Standards KG objects

Add a function such as:

```python
compile_academic_standards_kg(final_sfi_records, has_child_edges, document_profile, document_ir) -> AcademicStandardsKGArtifacts
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
Use document profile metadata for title, subject, jurisdiction, provider, language, license, author, attribution statement, adoption status, and framework description.
Include doc_key and source PDF metadata in metadata.
```

SFI policy:

```text
Grouping nodes -> normalized_statement_type = Standard Grouping
Normative expectation nodes -> normalized_statement_type = Standard
Descriptors/guidance/activity/auxiliary -> drop, attach to expectation metadata, or export as Other according to profile/config policy
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
Zero-window or zero-SFI output fails unless explicitly allowed by config/profile.
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
```

Recommended next intermediate schemas:

```text
SFIExtractionResult
SFICandidate
HasChildResolutionRequest
HasChildResolutionResponse
CandidateParentSet
SFICandidateRegistry
SFIMergeReport
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

Start from the current `extraction_windows.jsonl` artifact. Steps 1–3 now provide an inspectable prompt-input artifact before running the LLM. Do not add a separate extraction-window context-enrichment step for v0.

The next coding target is:

```text
extract_sfi_candidates_with_llm()
write sfi_extraction_results.jsonl
write sfi_extraction_summary.json
```

Begin with one relatively structured document, such as Ghana or Zambia, using the base source windows, deterministic code-pattern hints, code-parent hints, table headers, table helper views, source text, and provenance in the prompt. Validate every LLM response against the new intermediate SFI extraction schema before implementing the registry, merge, ID, and hasChild machinery.

After SFI extraction and dedup are working, implement:

```text
merge_sfi_candidates()
mint_final_sfi_ids()
recover_final_sfi_source_context_from_document_ir()
build_has_child_candidate_parent_sets()
resolve_has_child_edges_with_llm()
validate_has_child_graph()
```

The SFI extraction prompt should focus on extracting SFI and auxiliary candidates. The hasChild-resolution prompt should run after final SFI records exist; it should receive each child's recovered raw `section_path`, source provenance, and candidate parent SFIs from the finalized registry, then choose the direct parent or mark unresolved.

Do not begin with LearningComponents or LearningProgressions. Those should be behind later flags and should consume final SFIs, not candidates. The LearningComponent phase should decompose finalized Standard SFIs into atomic LearningComponent nodes and create `supports` edges from each LearningComponent to the relevant finalized SFI. A separate LearningProgressions phase may later create `buildsTowards` and `relatesTo` edges among finalized SFIs; those relationships should not be emitted by the LearningComponent phase.

---

## Known risk areas to keep in mind

### Raw section_path can be incomplete, stale, or misleading

Do not treat raw DocumentIR `section_path` as a clean final ancestor chain. It may include stale sibling headings or prior sections. In the hasChild resolver, provide the raw ordered `section_path` as evidence and instruct the LLM to infer the active context at the source position, such as latest applicable grade/basic, strand, and sub-strand. The resolver should keep ambiguous or conflicting parent choices in review artifacts rather than guessing.

### Optional table helpers can be missing

The KG stage should tolerate missing `rows_grid`, `grid_sources`, `row_provenance`, or `rows_filldown` fields. Use them when present, but keep raw rows/provenance as the fallback.

### Bilingual duplicate policy should be conservative

For Senegal, do not globally merge Wolof and French statements just because they look similar. For v0, prefer same-row or same-table-local pairing only when profile policy allows it. Otherwise preserve both and mark possible bilingual duplicates.

### Descriptor/guidance leakage

Do not let activities, exemplars, duration, resources, or teacher guidance become `Standard` SFIs unless the profile explicitly says they are in scope. Default behavior should be conservative:

```text
expectation -> SFI Standard
structural context -> SFI Standard Grouping
descriptor/guidance/activity -> attach/drop/Other according to policy
```

### ID churn

Avoid hashing LLM-cleaned or translated text as the primary identity source. Hash normalized source text from the DocumentIR when text hashing is needed.

### Zero-output failures

A run that produces zero extraction windows or zero final SFIs should fail by default. Silent empty KG artifacts are worse than a hard error during v0.
