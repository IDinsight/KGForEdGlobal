# Session Instructions for the Creation of Academic Standards KG Artifacts v0

## Purpose of this file

Use this file at the start of the coding session to establish the shared context quickly and begin implementation without re-reviewing the full curriculum mapping background.

The immediate goal is **not** to build the full Learning Commons KG pipeline end-to-end. The immediate goal is to implement a focused v0 slice:

```text
stitched DocumentIR
  -> LLM-ready extraction windows
  -> LLM-extracted SFI candidates
  -> merged final SFI records
  -> StandardsFramework
  -> StandardsFrameworkItem
  -> Relationship(hasChild)
  -> validation + Academic Standards KG artifacts
```

LearningComponents and LearningProgressions remain important downstream goals, but they should come after the Academic Standards hierarchy is stable.

Academic Standards KG creation is an **LLM extraction pipeline**. Python prepares source-faithful windows, adds profile/context/code hints, validates structured responses, merges duplicates, mints deterministic IDs, resolves relationships, and exports artifacts. The LLM performs the semantic extraction from each window into SFI candidates and hierarchy/parent hints. Deterministic rules are guardrails and helpers, not a replacement for LLM extraction.

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

The next stages can later add:

```text
LearningComponent
Relationship(supports)
Relationship(buildsTowards)
Relationship(relatesTo)
```

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
    - `utils_document_ir.py`: utility functions from the DocumentIR pipeline; useful for source-text normalization, local-code comparison, provenance handling, and deterministic source-address logic (renamed to avoid conflict)
    - `stitch_segments.py`: reference for how stitched table segments and helper views are produced; useful when building LLM-ready table extraction windows
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
  -> LLM-ready extraction windows
  -> LLM-extracted SFI candidates
  -> global registry
  -> merge/dedup
  -> deterministic final SFI IDs
  -> final hasChild edges
  -> StandardsFramework / StandardsFrameworkItem / Relationship(hasChild)
```

---

## Current implementation state

The KG entry point is `create_kgs.py`.

Its current v0 behavior is prep/validation only:

```text
1. Load and validate the stitched DocumentIR and DocumentProfile.
2. Cross-check basic profile/document compatibility.
3. Build and persist kg_run_manifest.json.
```

The CLI also cross-checks that the KG run is using the same PDF bytes/doc_key as the extraction and stitching run, creates the KG output directory, persists `kg_run.json`, and then calls `build_kgs(...)`.

The next coding work should extend `build_kgs(...)` after the manifest prep remains successful.

---

## Key design principles for v0

### 1. Keep v0 small

The first complete product should be an Academic Standards KG artifact bundle. Do not implement LearningComponents or LearningProgressions until the SFI hierarchy is working on at least Ghana, Zambia, and Senegal Math and Senegal Reading curricula.

### 2. Profile-driven, not country-hardcoded

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

### 3. Academic Standards extraction is LLM-driven by design

The Academic Standards KG should be created through an LLM extraction stage, not by trying to make Python fully understand every curriculum layout semantically.

Python should do the deterministic orchestration work:

```text
select eligible source segments
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
return parent/context hints when the hierarchy is visible in the window
```

Deterministic code patterns, table header mappings, and code parent rules are still useful, but in v0 they should be treated mainly as prompt inputs, hints, validators, and merge/ID aids. They should not replace the LLM extraction step.

### 4. No-code support is mandatory

No downstream candidate, merge, ID, or relationship step may assume `statement_code` exists.

For coded curricula, stable source codes are valuable and should be used for merge and ID stability. For no-code curricula, use deterministic synthetic keys based on document context and source text.

No-code synthetic keys should be based on source-derived material, not LLM paraphrases.

### 5. Preserve original language and provenance

For v0, translation is not required. Preserve original text, language tags, source page/cell/row provenance, and optional `text_en` when already available.

For non-English or bilingual curricula, keep source-language content. Use `mul` for mixed-language content when appropriate.

### 6. Treat table helper views as helpful but optional

For table-heavy curricula, extraction windows should include both source-fidelity and interpretation-friendly views when available:

```text
rows              -> source-fidelity row/cell text
rows_grid         -> grid-normalized cells
rows_filldown     -> interpretation-friendly rows with group context filled down
grid_sources      -> debugging/provenance for normalized cells
row_provenance    -> row/cell provenance
```

However, the KG stage should not fail just because `rows_grid` or `rows_filldown` is missing. Use them when present; fall back to raw rows, headers, section path, and provenance when not present.

### 7. Use candidates before final KG nodes

The LLM extraction stage should emit candidates, not final KG nodes. Deterministic code may add hints or pre/post-validation, but final KG nodes should not be minted directly from a single window response.

This prevents duplicate KG nodes from overlapping extraction windows, filldown repetition, repeated codes, or bilingual duplicates.

Final SFI IDs should be minted only after global merge/dedup.

### 8. Deterministic IDs only

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

### 9. Relationship endpoint rules

For Academic Standards v0, only `hasChild` is required.

Use KG relationship helper functions so every relationship follows the schema constraints:

```text
StandardsFramework       --hasChild--> StandardsFrameworkItem
StandardsFrameworkItem   --hasChild--> StandardsFrameworkItem
```

The relationship schema expects `hasChild` endpoints to use `case_identifier_uuid` for both source and target when applicable. For simplicity in v0, set each exported framework/item's `identifier` equal to `case_identifier_uuid` unless there is a strong reason not to.

### 10. Validation is part of the product

Do not treat validation as a later cleanup step. The v0 product should include schema validation, relationship endpoint validation, coverage checks, and an explicit validation report.

---

## Academic Standards v0 implementation sequence

The current prep/manifest code already exists. The following 11 steps are the recommended next implementation sequence inside or below `build_kgs(...)`, after `kg_run_manifest.json` is successfully created.

### Step 1. Select extraction segments

Add a function such as:

```python
select_extraction_segments(document_ir, document_profile) -> SelectedExtractionSegments
```

Purpose:

```text
Choose the DocumentIR segments eligible for Academic Standards extraction.
```

Inputs:

```text
DocumentIR.segments
DocumentProfile target/excluded table signatures
DocumentProfile target/excluded section patterns
DocumentProfile table/window mode
```

Rules:

```text
Prefer profile-driven table selection.
Ignore front matter, panel-member tables, pure examples, and non-standards tables.
Include block segments only when the profile says block text can contain extractable standards/groupings.
Add a hard guard: if zero extraction segments/windows are selected, raise unless the profile explicitly allows zero windows.
```

Output artifact candidate:

```text
selected_extraction_segments.json
```

### Step 2. Build LLM-ready extraction windows

Add a function such as:

```python
build_llm_extraction_windows(selected_segments, document_profile) -> list[ExtractionWindow]
```

Purpose:

```text
Cut selected DocumentIR content into stable prompt-sized windows for LLM-based Academic Standards extraction.
```

This step should produce the inputs that will be sent to the LLM. It is not merely a chunking utility; it is where Python packages the exact source text, table structure, context spine, profile instructions, and provenance needed for reliable LLM extraction.

Step 2 is the LLM prompt-payload construction stage; Step 4 is the LLM call and structured-response validation stage.

Each window should include:

```text
window_id
source segment_id(s)
section path / heading context
structured context from profile rules, if available
table metadata and column signature, if table-based
headers
raw rows/cells
rows_grid when available
rows_filldown when available
grid_sources when available
row_provenance/page provenance
profile extraction instructions
LLM task instructions that ask for SFI candidates, auxiliary candidates, and parent/context hints
```

Each window should be treated as an LLM prompt payload. Python may precompute deterministic hints such as code matches, candidate parent-code suggestions, table-header role hints, and normalized context strings, but the window's purpose is to give the LLM enough source material to extract Academic Standards candidates faithfully.

For tables, v0 can use row chunks or whole-table windows based on the profile. Do not implement a separate logical-row assembly layer yet; use `rows_filldown` plus downstream dedup/merge.

### Step 3. Persist extraction windows

Write:

```text
extraction_windows.jsonl
```

Each line should validate against an intermediate `ExtractionWindow` schema once that schema is added.

This artifact should be inspectable without running the LLM. It is the primary debugging surface for, "What did we ask the LLM extractor to inspect?"

### Step 4. Run LLM extraction to create SFI candidates

Add a function such as:

```python
extract_sfi_candidates_with_llm(windows, document_profile) -> list[SFIExtractionResult]
```

Purpose:

```text
Use the LLM to convert each extraction window into candidate StandardsFrameworkItems and auxiliary records, guided by the DocumentProfile and the window's source/provenance payload.
```

Use the LLM as the primary semantic extractor for Academic Standards v0. The LLM should return candidates that validate against the SFI extraction response schema. Deterministic code should not bypass this LLM candidate-extraction stage except for mechanical framework-root creation and later validation/export bookkeeping.

The LLM should identify:

```text
structural grouping SFI candidates
normative expectation SFI candidates
descriptor/guidance/activity/auxiliary records that should not become Standard SFIs by default
source-language text and language tags
statement codes when visible
parent/context hints visible in the window
uncertainties or unresolved cases
```

Use deterministic logic around the LLM call for:

```text
prompt construction
stable code-pattern hints
code-pattern hints
code parent-rule hints
table header role hints
section/context spine hints
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
parent hints: temporary candidate IDs, code parent hints, context path hints, or none
source provenance: required
synthetic_context_key/no-code merge fields: required when statement_code is missing
confidence/rationale when LLM-derived
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
normalized context path
statement/source role
language
source segment/table/row/cell provenance
candidate parent hints
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
Collapse repeated candidates into one final logical SFI record with merged provenance.
```

Expected duplicate sources:

```text
overlapping windows
rows_filldown repetition
same official code appearing in multiple windows
same no-code objective repeated in bilingual/section-local structures
continuation tables split across pages
```

For coded curricula:

```text
same statement_code + compatible role/context -> merge
same statement_code + conflicting source text/context -> review or disambiguate
```

For no-code curricula:

```text
doc_key + subject + level/stage/week/palier context + source role + normalized source text -> likely merge key
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
lc:curriculum:{doc_key}:sfi:{role}:{normalized_context_path}:{normalized_source_text_hash}
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
context path / canonical path key
metadata/provenance
```

Write:

```text
final_sfi_records.json
```

### Step 9. Remap and resolve parent-child edges

Add a function such as:

```python
resolve_has_child_edges(final_sfi_records, merge_result, document_profile) -> HasChildResolutionResult
```

Purpose:

```text
Convert temporary parent hints into final hasChild edges.
```

For coded curricula, use code parent rules where configured:

```text
B4.1.1.1.1 -> parent B4.1.1.1
3.9.4.1 -> parent inferred by configured code hierarchy, when safe
```

For no-code curricula, use structural/context hierarchy:

```text
Framework -> subject/domain/activity section -> stage/level/palier/week -> expectation
```

Rules:

```text
Every emitted SFI must be reachable from the StandardsFramework root.
Do not create self-loops.
Do not create cycles.
Keep unresolved parent hints in unresolved_edges.json rather than guessing.
Do not make descriptor/guidance/activity nodes into parents unless profile policy explicitly allows it.
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

Recommended first intermediate schemas:

```text
SelectedExtractionSegment
ExtractionWindow
SFIExtractionResult
SFICandidate
SFICandidateRegistry
SFIMergeReport
FinalSFIRecord
HasChildEdgeCandidate / FinalHasChildEdge
AcademicStandardsKGArtifacts
AcademicStandardsValidationReport
```

All LLM outputs should validate against schemas. Do not pass unvalidated ad hoc dictionaries between implemented stages.

---

## Practical coding notes for the next session

Start by extending `build_kgs(...)` in `create_kgs.py` after `kg_run_manifest.json` is written.

A practical first coding target is:

```text
select_extraction_segments()
build_llm_extraction_windows()
write extraction_windows.jsonl
```

This gives an inspectable artifact before running the LLM and makes prompt/debug review possible.

A good second coding target is LLM-based SFI candidate extraction for one relatively structured document, such as Ghana or Zambia, using deterministic code/table/header hints in the prompt and validating the LLM response against the new intermediate schemas. That will test the registry, merge, ID, and hasChild machinery before the more difficult Senegal no-code/bilingual path.

Do not begin with LearningComponents or LearningProgressions. Those should be behind later flags and should consume final SFIs, not candidates.

---

## Known risk areas to keep in mind

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
