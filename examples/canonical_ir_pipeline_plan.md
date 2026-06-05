# Canonical IR Pipeline Plan

## Purpose

Build a reusable canonicalization pipeline that converts a stitched `DocumentIR` into a clean, auditable `CanonicalCurriculumIR`, which can then be exported into Learning Commons KG shapes:

- `StandardsFramework`
- `StandardsFrameworkItem`
- `LearningComponent`
- `LearningProgressions`
- relationships such as: `hasChild`, `supports`, `buildsTowards`, and `relatesTo`

The main design idea is:

```text
DocumentIR → DocumentProfile → SourceUnits → LLM local interpretation → LLM reconciliation → Python assembly + validation → CanonicalCurriculumIR
```

The LLM helps interpret curriculum content, but it does **not** create final KG nodes, final graph IDs, or final edges. Python owns canonical assembly, deterministic IDs, provenance, deduplication, validation, and KG export.

---

## Core Principles

1. **DocumentIR remains layout-oriented**
   The `DocumentIR` is the source of stitched layout content: blocks, tables, rows, cells, captions, provenance, page numbers, and bounding boxes. It should not be treated as already-semantic.

2. **Canonical IR is a semantic intermediate layer**
   The canonical IR should contain curriculum-level entities and relationships, but it is still upstream of KG export.

3. **The LLM proposes; Python disposes**
   The LLM may classify, split, translate, normalize, and propose relationships. Python must compile, dedupe, validate, and decide whether to materialize or mark unresolved.

4. **No assumption that curricula are linear or non-repeating**
   Curricula can repeat expectations (e.g., in overview tables, planning tables, detailed standards tables, assessment sections, appendices, or restatement sections). The pipeline must merge, dedupe, attach, or flag repetitions.

5. **Country/document differences are policy, not pipeline forks**
   Ghana, Senegal, Zambia, Uganda, Tanzania, etc. can use different document profiles and role policies while sharing the same canonicalization machinery.

6. **Provenance is mandatory**
   Every canonical statement, grouping, guidance item, descriptor, and unresolved item must point back to source units and ultimately to `DocumentIR` segments/pages/rows/cells. However, we should not overcomplicate provenance by trying to track every single cell in every table. The right level of provenance without undue complexity (especially for v0) is an open question.

7. **Stable source anchors, flexible interpretation**
   LLM interpretation may vary across runs. That is acceptable. But source unit IDs, source provenance, canonical ID generation, validation rules, and serialization should be deterministic for a given set of LLM outputs.

---

# High-Level Pipeline

```text
1. Load DocumentIR
2. Create DocumentProfile
3. Create SourceUnitSet with Python coarse unitization
4. Run local LLM unit interpretation
5. Run section/table/window-level LLM reconciliation
6. Assemble CanonicalCurriculumIR in Python
7. Validate and repair or mark unresolved
8. Possible additional LLM review step before moving onto KG export pipeline (downstream)
```

---

# Main Artifacts

## 1. `DocumentProfile`

A small policy object that describes how the curriculum should be interpreted.

Purpose:

- Identify country, subject, grade/stage, languages, and curriculum family.
- Define what counts as a standards-like item.
- Define which units are guidance, descriptors, examples, activities, or checkpoints.
- Provide code patterns when official codes exist.
- Provide parentage and sequence policies.
- Provide prompt context for LLM calls.

Example Ghana profile:

```json
{
  "curriculum_family": "structured_code_standards",
  "country": "Ghana",
  "subject": "Mathematics",
  "grades_or_stages": ["Basic 4", "Basic 5", "Basic 6"],
  "languages": ["en"],
  "has_stable_codes": true,
  "code_patterns": {
    "content_standard": "B[456]\\.\\d+\\.\\d+\\.\\d+",
    "indicator": "B[456]\\.\\d+\\.\\d+\\.\\d+\\.\\d+"
  },
  "role_policy": {
    "content_standard": "sfi_parent",
    "indicator": "sfi_leaf",
    "exemplar": "guidance",
    "core_competency": "guidance"
  },
  "parentage_policy": "code_prefix"
}
```

Example Senegal Reading profile:

```json
{
  "curriculum_family": "bilingual_sequence_curriculum",
  "country": "Senegal",
  "subject": "Langue et Communication",
  "grades_or_stages": ["CE1", "Étape 2"],
  "languages": ["wo", "fr"],
  "has_stable_codes": false,
  "hierarchy_markers": {
    "substage": ["Palier", "Sumb", "Jéego"],
    "week": ["Semaine", "Bés"],
    "learning_objective": ["Objectif d’apprentissage", "Nisaru njàng"],
    "specific_objective": ["Objectif spécifique", "Nisaru jukki"]
  },
  "role_policy": {
    "competence": "sfi_parent",
    "objectif_apprentissage": "sfi_parent",
    "objectif_specifique": "sfi_leaf",
    "contenus": "guidance",
    "duree": "descriptor",
    "integration": "checkpoint",
    "evaluation": "checkpoint"
  },
  "parentage_policy": "nearest_structural_context"
}
```

Example Senegal Math profile (still needs to be filled out):

```json

```

Example Zambia literacy and languages profile (still needs to be filled out):

```json

```

---

## 2. `SourceUnitSet`

A deterministic, provenance-safe set of coarse units produced from the `DocumentIR`.

Python creates source units without trying to fully understand the curriculum.

Possible unit kinds (but open to iteration):

- `block_heading`
- `block_paragraph`
- `caption`
- `code_chunk`
- `list_item`
- `row_window`
- `table_cell`
- `table_row`

Suggested schema (but open to iteration):

```python
class SourceUnit:
    bbox_refs: list[dict]
    cells: list[dict] | None
    col_index: int | None
    detected_patterns: dict
    page_indices: list[int]
    row_index: int | None
    segment_id: str
    structural_context: dict
    table_headers: list[str]
    text: str | None
    unit_id: str
    unit_kind: str
```

Important: unitization should be simple for v0. It should package evidence for the LLM, not solve curriculum semantics in Python.
SCHEMAS SHOULD NOT INCLUDE MORE ATTRIBUTES THAN IS ABSOLUTELY NECESSARY FOR V0.

---

## 3. `InterpretedUnitSet`

Output of local LLM interpretation.

The LLM receives one `SourceUnit` or a small group of source units and returns one or more interpreted subunits.

Purpose:

- Split dense rows/cells into meaningful curriculum subunits.
- Identify likely role, language, code, and normative status.
- Provide parent/attachment hints.
- Preserve confidence and evidence.

Suggested schema (but open to iteration):

```python
class InterpretedSubUnit:
    attachment_hints: list[dict]
    code: str | None
    confidence: float
    curriculum_specific_role: str | None
    evidence: str
    is_normative: bool
    is_sfi_candidate: bool
    language: str | None
    local_role: Literal[
        "activity",
        "assessment",
        "checkpoint",
        "descriptor",
        "duration",
        "example",
        "framework_title",
        "guidance",
        "hierarchy_grouping",
        "ignore",
        "normative_expectation",
        "unresolved"
    ]
    normalized_text: str
    original_text: str
    parent_hint: dict | None
    source_spans: list[dict]
    source_unit_id: str
    subunit_id: str
    text_en: str | None
```

Local interpretation should **not** create final canonical nodes, final IDs, `hasChild` edges, learning components, learning progressions, or progression edges.
SCHEMAS SHOULD NOT INCLUDE MORE ATTRIBUTES THAN IS ABSOLUTELY NECESSARY FOR V0.

---

## 4. `ReconciliationDecisionSet`

Output of section/table/window-level LLM reconciliation.

The LLM receives a bounded group of interpreted units, such as:

- one standards table
- one activity table
- one palier section
- one grade/strand/sub-strand section
- one code-family group
- one table plus immediately preceding headings

Purpose:

- Decide final canonical roles for interpreted units.
- Attach guidance/examples/descriptors to the right SFI candidate.
- Resolve parent/child hints within the window.
- Identify repeated headers, checkpoint rows, duplicates, and unresolved units.
- Emit sequence hints.

Suggested schema (but open to iteration):

```python
class CanonicalRoleDecision:
    canonical_role: Literal[
        "activity",
        "assessment",
        "checkpoint",
        "descriptor",
        "example",
        "guidance",
        "hierarchy_grouping",
        "ignore",
        "sfi_leaf",
        "sfi_parent",
        "unresolved"
    ]
    canonical_text: str
    code: str | None
    confidence: float
    curriculum_specific_role: str | None
    interpreted_unit_id: str
    node_role: str | None
    rationale: str
    statement_role: str | None

class ParentageDecision:
    child_unit_id: str
    confidence: float
    parent_code: str | None
    parent_unit_id: str | None
    rationale: str
    relationship: Literal[
        "hasChild",
    ]

class ReconciliationDecisionSet:
    confidence: float
    duplicate_decisions: list[dict]
    group_id: str
    parentage_decisions: list[ParentageDecision]
    role_decisions: list[CanonicalRoleDecision]
    sequence_decisions: list[dict]
    summary: str
    unresolved_unit_ids: list[str]
    warnings: list[str]
```

---

## 5. `CanonicalCurriculumIR`

Python-assembled semantic IR.

Purpose:

- Represent document metadata, hierarchy, standards candidates, guidance, descriptors, sequence evidence, unresolved content, and validation results.
- Serve as the stable input to KG export (downstream).

Suggested schema (but open to iteration):

```python
class CanonicalCurriculumIR:
    assembly_warnings: list[str]
    descriptor_candidates: list[DescriptorCandidate]
    document: DocumentMetadata
    guidance_candidates: list[GuidanceCandidate]
    has_child_edges: list[CanonicalEdge]
    hierarchy_nodes: list[HierarchyNodeCandidate]
    sequence_evidence: list[SequenceEvidence]
    statement_candidates: list[StatementCandidate]
    unresolved_items: list[UnresolvedItem]
    validation_report: ValidationReport
```

---

# v0 Build — Minimal POC

## v0 Goal

Get a working end-to-end canonical IR pipeline that can process at least:

1. Ghana Mathematics Basic 4–6
2. Senegal Reading CE1 bilingual curriculum
3. Senegal Math CE1-CE2 bilingual curriculum
4. Zambia Literacy and Languages curriculum

The v0 build should prove that the same pipeline can support:

- code-driven standards documents, like Ghana
- bilingual sequence/palier-driven curricula, like Senegal Reading and Math
- XXX, like Zambia

v0 should produce a valid `CanonicalCurriculumIR` that can support basic downstream Academic Standards, Learning Components, and Learning Progressions KG export.

---

## v0 Non-Goals

Do not spend v0 effort on:

- translating non-English text --> we can just leave text fields as is
- sophisticated fuzzy dedupe
- LearningProgression generation (downstream responsibility)
- LearningComponent atomic splitting (downstream responsibility)
- curriculum element materialization such as `Lesson`, `Activity`, `Assessment`, or `Material` (v1 responsibility)
- manual review UI
- complex prompt optimization

---

## v0 Pipeline

```text
DocumentIR
  → manually or semi-automatically created DocumentProfile
  → simple Python SourceUnitSet
  → local LLM interpretation
  → table/section-level LLM reconciliation
  → Python assembly
  → Python validation
  → LLM final validation
  → CanonicalCurriculumIR
```

---

## v0 Step 1 — DocumentProfile

For v0, profiles can be manually authored JSON files.

Suggested fields (but open to iteration):

```python
class DocumentProfile:
    code_patterns: dict[str, str]
    country: str
    curriculum_family: str
    doc_key: str
    has_stable_codes: bool
    hierarchy_markers: dict[str, list[str]]
    grades_or_stages: list[str]
    languages: list[str]
    negative_rules: list[str]
    parentage_policy: str
    primary_language: str
    role_policy: dict[str, str]
    sequence_policy: str | None
    subject: str
    table_header_role_hints: dict[str, str]
```

v0 should include the following profiles:

- `ghana_math_basic_4_6_profile.json`
- `senegal_reading_ce1_profile.json`
- `senegal_math_ce1_ce2_profile.json`
- `zambia_literacy_and_language_profile.json`

---

## v0 Step 2 — SourceUnitSet

Implement simple deterministic unitization.

For block segments:

- one source unit per heading/paragraph/caption/list item

For table segments:

- one source unit per table row
- include cells, headers, row index, segment ID, page provenance, and section path
- optionally create code chunks for Ghana when official code patterns are found

v0 unitization should not try to perfectly classify content. It only creates stable, provenance-rich objects for the LLM.

---

## v0 Step 3 — Local LLM Unit Interpretation

For each source unit, call the LLM with:

- document profile
- local source unit
- table headers and cells, if applicable
- nearby context
- detected codes
- required Pydantic output schema

The LLM returns an `InterpretedUnitSet`.

v0 prompt requirements:

- return strict JSON only
- do not create final KG nodes
- do not invent missing text
- classify examples/activities/durations/guidance carefully
- provide confidence and evidence
- preserve original text
- translate only if configured

---

## v0 Step 4 — Section/Table-Level Reconciliation

For v0, use only table-level or small section-level reconciliation windows.

Suggested v0 windows:

### Ghana

- one standards table under active grade/strand/sub-strand context
- or one row group if table is too large

### Senegal Reading

- one activity table, e.g. one `Tableau 1.4.x` or `Tableau 1.6.x`
- one overview table for competency sections

### Senegal Math

TBD

### Zambia Literacy and Language

TBD

The reconciliation LLM should output:

- final canonical role per interpreted unit
- parentage hints
- guidance/example/descriptor attachment hints
- duplicate hints
- sequence hints
- unresolved units

---

## v0 Step 5 — Python Canonical Assembly

Python assembles the canonical IR from reconciliation outputs.

Required v0 behavior:

1. Create one document-level framework record.
2. Create hierarchy nodes from profile and recognized grouping units.
3. Create SFI candidates from `sfi_parent` and `sfi_leaf` role decisions.
4. Create `hasChild` edges.
5. Attach guidance/examples/descriptors to SFIs when possible.
6. Merge obvious duplicates.
7. Emit unresolved items when parentage or classification is unsafe.
8. Generate deterministic IDs.

v0 ID policy:

```text
framework:
  lc:curriculum:{doc_key}:framework

hierarchy node:
  lc:curriculum:{doc_key}:grouping:{role}:{path_fp}:{title_hash}

SFI with code:
  lc:curriculum:{doc_key}:sfi:{role}:{path_fp}:{code}:{text_hash}

SFI without code:
  lc:curriculum:{doc_key}:sfi:{role}:{path_fp}:{source_unit_id}:{text_hash}

auxiliary guidance/descriptor:
  lc:curriculum:{doc_key}:aux:{kind}:{source_unit_id}:{text_hash}
```

---

## v0 Step 6 — Python Validation

v0 validation should be strict enough to catch major failures, but not so strict that one bad row kills the entire document.

Required v0 validation checks:

### Universal checks

- every SFI has provenance
- every source unit referenced by canonical output exists
- every `hasChild` edge references existing canonical nodes
- no cycles in `hasChild`
- framework root is not a child
- every `sfi_leaf` has a parent or is marked unresolved
- every guidance/descriptor attachment target exists or is marked unresolved
- duplicate source spans are intentional or reported

### Ghana checks

- content-standard code pattern maps to `sfi_parent`
- indicator code pattern maps to `sfi_leaf`
- indicator parent should be recoverable by code prefix
- exemplars beginning with `E.g.` should not become SFIs
- core competency text should not become SFIs
- same official code with conflicting text should become a warning/error

### Senegal Reading checks

- `Durée` should not become SFI
- `Contenus` should usually not become SFI
- `Palier`, `Sumb`, `Jéego`, `Semaine`, and `Bés` should be grouping/sequence evidence, not ordinary SFI leaves
- `INTÉGRATION` and `EVALUATION` rows should not become ordinary SFIs
- bilingual original text should be preserved when present

### Senegal Math checks

TBD

### Zambia Literacy and Languages check

TBD

---

## v0 Step 7 — LLM Final Validation

TBD

## v0 Step 8 - Final CanonicalCurriculumIR export

TBD

---

# Final Recommended Implementation Order

## v0 Implementation Order

1. Define new Pydantic schemas:
   - `DocumentProfile`
   - `SourceUnit`
   - `InterpretedSubUnit`
   - `ReconciliationDecisionSet`
   - `CanonicalCurriculumIR`
   - `ValidationReport`

2. Implement source unitization:
   - block units
   - table row units
   - optional Ghana code chunks

3. Write the following manual profiles:
   - Ghana Mathematics Basic 4–6
   - Senegal Reading CE1
   - Senegal Math CE1-CE2
   - Zambia Literacy and Languages

4. Implement local LLM interpretation runner.

5. Implement table/section reconciliation runner.

6. Implement Python canonical assembly:
   - hierarchy registry
   - SFI registry
   - guidance/descriptor registry
   - parentage resolver
   - duplicate merger
   - unresolved bucket

7. Implement validation:
   - universal checks
   - Ghana-specific checks
   - Senegal-specific checks
   - Zambia-specific checks

---

# Key Risks and Mitigations

## Risk 1: LLM over-materializes standards

Mitigation:

- negative role rules in `DocumentProfile`
- local confidence/evidence requirements
- reconciliation review
- Python role-policy validation

## Risk 2: local decisions are globally inconsistent

Mitigation:

- reconciliation windows
- Python registries
- duplicate detection
- parentage repair rules

## Risk 3: repeated standards create duplicate nodes

Mitigation:

- SFI registry by code, context, text hash, and source span
- merge provenance for compatible duplicates
- conflict warnings for incompatible duplicates

## Risk 4: document profile is wrong

Mitigation:

- profile confidence/evidence
- manual profile override in v0
- profile validation and review in v1

---

# Final Position

The new canonical IR pipeline should be an **LLM-assisted semantic compiler**:

```text
Python creates stable source units.
LLM interprets and reconciles bounded curriculum content.
Python assembles, deduplicates, validates, and exports.
```
