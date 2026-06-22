# Session Instructions for the Creation of Academic Standards KG Artifacts v0

## Purpose of this file

Use this file at the start of the coding session to establish the shared context quickly and begin implementation without re-reviewing the full curriculum mapping background.

The immediate goal is **not** to build the full Learning Commons KG pipeline end-to-end. The immediate goal is to implement a focused v0 slice:

```text
stitched DocumentIR
  -> source-faithful, source-context-enriched LLM-ready extraction windows
  -> LLM-extracted SFI candidates with parent/context hints
  -> merged final SFI records
  -> StandardsFramework
  -> StandardsFrameworkItem
  -> Relationship(hasChild)
  -> validation + Academic Standards KG artifacts
```

LearningComponents and LearningProgressions remain important downstream goals, but they should come after the Academic Standards hierarchy is stable. Treat them as separate downstream phases: LearningComponents add `LearningComponent` nodes plus `supports` relationships from LearningComponents to finalized StandardsFrameworkItems; LearningProgressions add `buildsTowards` and `relatesTo` relationships among finalized StandardsFrameworkItems.

Academic Standards KG creation is an **LLM extraction pipeline**. Python prepares source-faithful windows, adds profile and code hints, validates structured responses, merges duplicates, mints deterministic IDs, resolves relationships, and exports artifacts. The LLM performs the semantic extraction from each window into SFI candidates and hierarchy/parent hints. Deterministic rules are guardrails and helpers, not a replacement for LLM extraction.

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
  -> source-faithful, source-context-enriched LLM-ready extraction windows
  -> LLM-extracted SFI candidates with parent/context hints
  -> global registry
  -> merge/dedup
  -> deterministic final SFI IDs
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

Before running SFI extraction at scale, enhance the extraction-window payloads so they expose source-visible hierarchy/context evidence to the LLM. This enhancement should remain source-faithful and curriculum-agnostic: Python packages nearby headings, section paths, source order, and structured context hints when available, but it does not decide final hierarchy or parentage.

The CLI cross-checks that the KG run is using the same PDF bytes/doc_key as the extraction and stitching run, creates the KG output directory, persists `kg_run.json`, and then calls `build_kgs(...)`.

The next coding target is Step 2b, then Step 4: enrich `extraction_windows.jsonl` with source-visible context hints, then run LLM extraction over the enriched windows to produce validated SFI extraction results.

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

### 4. Separate curriculum grammar from source-window evidence

The SFI-extraction system instruction should describe the curriculum-specific grammar:

```text
what counts as an SFI
what the expected hierarchy/context shape is
which source roles are direct parents versus broader ancestor context
which materials are auxiliary rather than StandardsFrameworkItems
```

The extraction window should provide the source-visible evidence for the specific segment being inspected:

```text
visible headings and section path hints
nearby headings before/after the source unit
source order/page range
source table/row/column provenance
code matches and code parent hints
structured context hints derived from profile-configured context_spine rules, when available
```

This distinction is important. The system instruction tells the LLM the curriculum grammar; the extraction window tells the LLM which grade, strand, sub-strand, palier, week, table section, code context, or source neighborhood is visible for the current source unit.

Python may package source-visible context hints, but it must not treat those hints as final semantic hierarchy. The LLM may emit candidate direct parents and ancestor context references during SFI extraction; Python later aggregates, resolves, validates, and compiles those hints into final `hasChild` edges.

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
return candidate direct parent references and broader ancestor/context hints when the source evidence is visible in the window
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

The base window must preserve source text, block/table payloads, provenance, table helper views, code hints, profile instructions, and deterministic source-derived keys. Step 2b adds source-visible context hints used by the LLM to propose parent/context references.
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
source_context_hints from DocumentIR/profile context evidence, when available
deterministic source-derived hints
profile extraction instructions
LLM task instructions that ask for SFI candidates, auxiliary candidates, candidate direct parents, broader ancestor/context hints, and unresolved parentage notes visible in the window
```

The extraction-window stage should expose `section_path`, `nearby_headings`, `context_spine`, or similar upstream/profile-derived context as source-visible evidence when available, but it should not treat them as finalized semantic hierarchy. These fields are prompt evidence for the LLM, not Python-derived parentage decisions. If context is missing or ambiguous, preserve the ambiguity and let the SFI extraction result include unresolved parentage notes rather than guessing.

For tables, windowing is controlled by `max_rows_per_table_window`:

```text
max_rows_per_table_window = null -> one whole-table window
max_rows_per_table_window = integer -> row chunks of at most that many body rows
row_overlap -> overlapping body rows between adjacent chunks when row chunking is enabled
```

Do not implement a separate logical-row assembly layer yet; use raw rows plus optional `rows_grid`, `rows_filldown`, `grid_sources`, and `row_provenance` when available, then rely on downstream candidate dedup/merge.

### Step 2b. Enrich extraction windows with source-visible hierarchy/context evidence — next target before Step 4

Add a source-faithful context-enrichment layer to the extraction-window payloads before running the SFI extraction LLM.

Purpose:

```text
Provide the LLM with the local source evidence needed to extract candidate parent references and observed hierarchy/context paths, without making Python responsible for curriculum-specific hierarchy interpretation.
```

This step may derive compact context hints from DocumentIR and DocumentProfile fields such as:

```text
segment source order
source page range
section_path hints from DocumentIR, if present
nearest preceding and following headings
active source heading stack, when recoverable from source order
profile context_spine rules, when configured
table title/caption/section text, when present
table row/column provenance and same-row code context
```

Each enriched window should include a compact `source_context_hints` payload such as:

```text
section_path: source-visible heading/path strings with provenance when available
nearby_headings: closest heading/list/caption context before or around the source unit
source_order: segment index/window index/page range
structured_context: profile-derived context items, when available
context_confidence: high | medium | low
context_notes: source-faithful notes about ambiguity, resets, continuation, or missing headings
```

Rules:

```text
Do not infer final parents in Python.
Do not hard-code curriculum-specific hierarchy walkers in Python.
Do not require source_context_hints to be complete for every window.
Do not silently convert broad context into direct parentage.
Preserve provenance for context hints whenever possible.
If context hints are ambiguous, pass the ambiguity to the LLM and require unresolved notes in the extraction output.
```

This enrichment is especially important for grouping relationships such as:

```text
StandardsFramework -> grade/course grouping
Grade/course grouping -> strand/domain grouping
Strand/domain grouping -> sub-strand/topic/palier/week grouping
Sub-strand/topic/palier/week grouping -> normative expectation
```

For coded table-local relationships, such as Ghana content standard to indicator, existing code matches and code parent hints may be enough. For broader grouping hierarchy, source-visible context hints should be included so the LLM can emit grounded candidate parent references.

### Step 3. Persist extraction artifacts — implemented

Write:

```text
extraction_window_plan.json
extraction_windows.jsonl
```

Each extraction-window JSONL record should validate against the intermediate `ExtractionWindow` schema.

These artifacts should be inspectable without running the LLM. They are the primary debugging surface for, "What did we ask the LLM extractor to inspect?" and "What source-visible context did we provide for parent/context hinting?"

### Step 4. Run LLM extraction to create SFI candidates — next target

Add a function such as:

```python
extract_sfi_candidates_with_llm(*, document_profile, windows) -> list[SFIExtractionResult]
```

Purpose:

```text
Use the LLM to convert each source-context-enriched extraction window into candidate StandardsFrameworkItems, auxiliary records, and source-grounded parent/context references, guided by the DocumentProfile and the window's source/provenance/context payload.
```

Use the LLM as the primary semantic extractor for Academic Standards v0. The LLM should return candidates that validate against the SFI extraction response schema. Deterministic code should not bypass this LLM candidate-extraction stage except for mechanical framework-root creation and later validation/export bookkeeping.

The LLM should identify:

```text
structural grouping SFI candidates
normative expectation SFI candidates
descriptor/guidance/activity/auxiliary records that should not become Standard SFIs by default
source-language text and language tags
statement codes when visible
candidate direct parent references visible or strongly supported by the window/context payload
broader ancestor/context references visible in the window/context payload
observed hierarchy/context paths, with evidence
uncertainties or unresolved parentage/context cases
```

Use deterministic logic around the LLM call for:

```text
prompt construction
code-pattern hints
code parent-rule hints
table header/source-structure hints
source_context_hints from Step 2b
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
parent candidates: candidate direct parent references using temporary candidate IDs, source codes, source labels, source roles, or unresolved placeholders
ancestor context candidates: broader grade/course/strand/domain/topic/palier/week context references that should not automatically become direct parents
observed_context_path: source-visible path strings/items when visible or supported by source_context_hints
source_context_group_key: deterministic batching key derived from observed_context_path; prefer deriving this in Python by normalizing/lowercasing context path strings rather than asking the LLM to invent it
parent evidence: required for every non-empty parent/context candidate
source provenance: required
synthetic_context_key/no-code merge fields: required when statement_code is missing
confidence/rationale when LLM-derived
unresolved_parentage_notes: required when the expected parent cannot be identified from the window/context evidence
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
candidate parent hints and ancestor context candidates
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
Collapse repeated candidates into one final logical SFI record with merged provenance, merged source_context_hints, and merged parent/context candidates.
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
source context/disambiguation key
metadata/provenance
merged observed_context_paths and source_context_hints
merged candidate parent/context references for hasChild resolution
```

Write:

```text
final_sfi_records.json
```

### Step 9. Resolve candidate parent hints into final hasChild edges

Add a function such as:

```python
resolve_has_child_edges(final_sfi_records, merge_result, document_profile) -> HasChildResolutionResult
```

Purpose:

```text
Convert merged candidate direct-parent references and ancestor/context hints into final `hasChild` edges, using the StandardsFramework root as the fallback parent only when no stronger direct parent is resolved.
```

Use these inputs, in a generic and curriculum-agnostic resolution process:

```text
LLM-emitted candidate direct parents from SFI extraction
LLM-emitted ancestor/context candidates from SFI extraction
observed_context_paths and source_context_hints aggregated during candidate merge
code parent hints from configured code_parent_rules
source codes/source labels/source roles that can be matched to finalized SFIs
StandardsFramework root as fallback parent
```

For coded curricula, code parent rules may resolve high-confidence direct parents when configured:

```text
B4.1.1.1.1 -> parent B4.1.1.1
3.9.4.1 -> parent inferred by configured code hierarchy, when safe
```

For no-code curricula, rely on LLM-emitted candidate parents and observed context paths rather than Python hard-coded hierarchy logic:

```text
Framework -> subject/domain/activity section -> stage/level/palier/week -> expectation
```

For LLM-assisted hierarchy resolution, batching should remain generic and mechanical. Python may derive a `source_context_group_key` from each finalized SFI's merged `observed_context_path` by lowercasing, normalizing whitespace/punctuation, and joining path components. This key is only a batching/retrieval aid; it is not semantic hierarchy inference and does not decide parentage.

Use `source_context_group_key` to group unresolved SFIs with nearby/plausible candidate parents before calling the hierarchy-resolution LLM. Each batch should contain children to place, candidate parents from the same or adjacent context groups, direct parent references already emitted during SFI extraction, and the StandardsFramework root fallback. The LLM should choose from provided candidates or mark unresolved; it should not invent new parent nodes.

Rules:

```text
Every emitted SFI must be reachable from the StandardsFramework root.
Start with a temporary root scaffold if helpful: StandardsFramework -> every finalized SFI.
Replace root edges with more specific direct parent edges only when candidate evidence resolves cleanly.
Do not create self-loops.
Do not create cycles.
Do not allow more than one direct hierarchy parent for an SFI unless explicitly allowed by profile policy.
Keep unresolved or conflicting parent hints in unresolved_edges.json rather than guessing.
Do not make descriptor/guidance/activity nodes into parents unless profile policy explicitly allows it.
Do not create buildTowards or relatesTo edges in this step.
```

Write:

```text
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
SourceContextHint
ObservedContextPath
SourceContextGroupKey
SFIExtractionResult
SFICandidate
SFICandidateParentReference
SFICandidateRegistry
SFIMergeReport
FinalSFIRecord
HasChildEdgeCandidate / FinalHasChildEdge
AcademicStandardsKGArtifacts
AcademicStandardsValidationReport
```

`SourceContextGroupKey` is a Python-derived helper rather than an LLM output schema. It should be computed deterministically from `ObservedContextPath` when possible, used only for batching/retrieval, and never treated as an asserted curriculum hierarchy edge.

All LLM outputs should validate against schemas. Do not pass unvalidated ad hoc dictionaries between implemented stages.

---

## Practical coding notes for the next session

Start from the current `extraction_windows.jsonl` artifact. Steps 1–3 now provide an inspectable prompt-input artifact before running the LLM, but before SFI extraction the windows should be enriched with source-visible context hints as described in Step 2b.

The next coding target is:

```text
enrich_extraction_windows_with_source_context()
extract_sfi_candidates_with_llm()
write sfi_extraction_results.jsonl
write sfi_extraction_summary.json
```

Begin with one relatively structured document, such as Ghana or Zambia, using the enriched source windows, deterministic code-pattern hints, code-parent hints, source_context_hints, table headers, and table helper views in the prompt. Validate every LLM response against the new intermediate SFI extraction schema before implementing the registry, merge, ID, and hasChild machinery.

The SFI extraction prompt should ask the LLM to emit candidate direct parents and broader ancestor/context candidates at the same time it emits SFI candidates. These are not final hasChild edges. They are source-grounded hints that Python will aggregate, resolve against finalized SFI IDs, validate, and either accept or send to review.

Do not begin with LearningComponents or LearningProgressions. Those should be behind later flags and should consume final SFIs, not candidates. The LearningComponent phase should decompose finalized Standard SFIs into atomic LearningComponent nodes and create `supports` edges from each LearningComponent to the relevant finalized SFI. A separate LearningProgressions phase may later create `buildsTowards` and `relatesTo` edges among finalized SFIs; those relationships should not be emitted by the LearningComponent phase.

---

## Known risk areas to keep in mind

### Source context hints can be incomplete or misleading

Do not treat `source_context_hints`, `section_path`, `nearby_headings`, or `context_spine` output as final hierarchy. They are prompt evidence for the LLM and later relationship resolution. The SFI extraction schema should allow unresolved parentage notes, and the hasChild resolver should keep ambiguous or conflicting parent candidates in review artifacts rather than guessing.

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
