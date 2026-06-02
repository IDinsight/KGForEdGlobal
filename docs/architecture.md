# Architecture

This page describes the pipeline that powers Senegal Knowledge Graph. The backend 
currently consists of five steps to transform a curriculum PDF into the knowledge graph 
artifacts for downstream applications to use. In addition, you can locally host the 
generated knowledge graph in an MCP server and interact with it using either Claude 
Desktop or Claude Code.

---

## Overview

Build a reusable pipeline that maps **any primary-school curriculum PDF** (often non-US countries like Zambia/Uganda/Tanzania/Senegal) into the high-level entity/relationship shapes used by the **Learning Commons knowledge graphs (LC KGs)**.

### Primary entities in scope (standards + skills)

- **Entities:** `StandardsFramework`, `StandardsFrameworkItem`, `LearningComponent`
- **Relationships:** `hasChild`, `supports`, `buildsTowards`, `relatesTo`

### Educational alignment (optional curriculum elements)

- **Relationship:** `hasEducationalAlignment` (curriculum element → standards item)
- Curriculum elements (`Course`, `LessonGrouping`, `Lesson`, `Activity`, `Assessment`, `Material`) are **optional** and should only be materialized when the PDF supports them clearly.

LC KGs published datasets are currently US-centric (e.g., standards via 1EdTech CASE; progressions via Student Achievement Partners’ Coherence Map), but the objective here is to **preserve the LC ontology “shape” for non-US PDFs**, not to force a 1:1 US CASE import. We still want to attach strong provenance and keep the ontology consistent.

### PDFs we’re working with (initial set)

1. **Ghana**
   - Highly structured standards with stable codes and clear roles (standards/indicators/exemplars)
   - Useful as a reference implementation and inference playground

2. **Senegal (Math Curriculum)**
   - Bilingual presentation is typically Wolof first, French second for the same competency/outcome.
   - Strong “framework-style” content up front: Compétence de cycle, Compétence de l’étape, then domain/strand base competencies (e.g., activités numériques / géométriques / mesure / résolution de problèmes).
   - The bulk is scope-and-sequence via tables organized by Semaine (week), with periodic paliers / activités d’intégration acting like checkpoints.
   - Progression evidence is mostly explicit sequencing by week (and sometimes by “palier”), rather than stable alphanumeric standard codes.

3. **Senegal (Reading Curriculum)**
   - Bilingual presentation is typically **Wolof first, French second**, often restating the same competency/outcome in both languages.
   - Organized with a clear document spine: **Schéma intégrateur**, **Tableau de planification des apprentissages**, then major activity sections such as **Oral**, **Lecture**, **Communication Écrite**, and **Production d’écrits**.
   - Progression is driven mostly by **Paliers** and **Semaine (week)** sequencing rather than stable alphanumeric standard codes.
   - Several sections use dense instructional tables where columns function like **learning objectives / specific objectives / content / duration**, while some written-language tables also embed repeated section headers and competency statements inside the table body.
   - The later written-production portion is structured as recurring components such as **Outils de langue**, **Produire des textes**, and **Copie**, repeated across paliers.
   - Expect bilingual extraction and normalization needs; store original text plus English where translation is required.

## Core Design Decisions

### Layer A — Extraction of Intermediate Representations (IR) (page-level, layout-oriented)

**Purpose:** Extract *what is on the page* using a vision model in repeatable JSON, preserving reading order, tables, and bounding boxes.

- Output: `PageIR` structure per page (ordered list of blocks and tables)
- Keep semantics light:
  - headings/paragraphs/lists/captions/tables
  - Avoid deep interpretation (don’t decide “this is a grade node” during extraction)

### Layer B — Canonical Curriculum IR (document-level, country-agnostic)

**Purpose:** Deterministically stitch a list of `PageIR` structures into a stable canonical representation with hierarchy and statement roles.

Canonical IR contains:

- Document metadata: country, ministry/publisher, year, languages, grade range, subjects/areas, title, etc.
- Hierarchy nodes: grade/stage --> subject/learning area --> theme/strand/topic --> unit/week/subtopic
- Statements with explicit roles:
  - `expectation` (normative outcome/competence/objective/standard)
  - `performance_descriptor` (expected standard/benchmark/indicator/assessment criteria)
  - `guidance` (activities/resources/teacher notes/exemplars)
- Strong provenance: pdf name, page number, section path, and optional bboxes

### Layer C — Canonical IR to LC KG Export (shape-preserving)

- One `StandardsFramework` per PDF (default)
- `StandardsFrameworkItem` hierarchy via `hasChild`
- `LearningComponent` generation per config + `supports`
- Progressions: explicit + inferred (with evidence/confidence)
- Optional: materialize curriculum elements and export `hasEducationalAlignment`

### Deterministic Global IDs

IDs must be deterministic across reruns and globally unique across all PDFs.

- Compute `doc_key` deterministically from PDF bytes (e.g., SHA-256)
- All KG IDs are minted via UUIDv5 with a **pinned** namespace UUID:
  - `namespace_uuid` is provided in `KnowledgeGraphConfig` and MUST never change once used.
- Entity identifiers (UUIDv5 seeds):
  - Framework `identifier`:
    - `uuid5(namespace_uuid, f"{doc_key}:entity:framework")`
  - SFI `identifier` (stable by CanonicalIR node id, not text):
    - `uuid5(namespace_uuid, f"{doc_key}:entity:sfi:{canonical_node_id}")`
  - LearningComponent `identifier` (stable by standard SFI identifier + split key):
    - `uuid5(namespace_uuid, f"{doc_key}:entity:lc:{standard_sfi_identifier}:{split_key}")`
- Relationship identifiers (UUIDv5 seeds):
  - `uuid5(namespace_uuid, f"{doc_key}:rel:{relationshipType}:{from_identifier}:{to_identifier}")`
- CASE identifiers (UUIDv5 seeds, separate from `identifier`):
  - Framework `caseIdentifierUUID`:
    - `uuid5(namespace_uuid, f"{doc_key}:case:framework")`
  - SFI `caseIdentifierUUID`:
    - `uuid5(namespace_uuid, f"{doc_key}:case:sfi:{canonical_node_id}")`
  - LearningComponent `caseIdentifierUUID`:
    - `uuid5(namespace_uuid, f"{doc_key}:case:lc:{standard_sfi_identifier}:{split_key}")`

NB: Never use random UUIDs as stable KG identifiers (`identifier`, `caseIdentifierUUID`, relationship `identifier`). Random UUIDs are fine for run IDs/logging.

### LearningComponent Creation (Configurable)

- LC source statement roles: default = most granular `expectation`
- Splitting policies:
  - `1_to_1`
  - `split_bullets`
- Always maintain at least one `supports` anchor per LC

### Progressions Policy (configurable)

A. Explicit progressions
- Extract when stated (prerequisite language, progression charts/tables).

B. Inferred progressions (pluggable modules)
- `grade_order`/`stage_order`
- `scope_sequence` (term/week/unit ordering)
- `code_pattern` (stable code progression)
- `semantic_similarity` (optional; bounded by level constraints)

C. All inferred edges must include:
- `inference_type`
- `confidence`
- `evidence` (source IDs + provenance pointers like page/section)
- Deduping: keep highest-confidence edge per pair/type

### Human-in-the-loop Mapping Wizard (for ambiguous PDFs)

Require a small repeatable set of inputs:

1. Framework scope: per-PDF (default) vs. per-subject export partition
2. Normative expectations fields: which columns/sections are standards/outcomes/competences/objectives?
3. Descriptor vs. guidance: which fields are performance descriptors vs instructional guidance?
4. LC policy: `1_to_1` vs. `split_bullets`
5. Progressions policy: explicit-only vs. explicit + inferred; enabled inference modules; confidence thresholds
6. Optional curriculum export: whether to materialize curriculum entities for `hasEducationalAlignment`

### Extraction Strategy and Division of Labor

#### Vision LLM responsibilities (extraction and verification layers only)

- Output strict JSON that validates against the extraction schema
- Preserve reading order of `items`
- Extract tables explicitly:
  - `header_rows` and `body_rows`
  - Keep `row_span`/`col_span` where possible
  - Allow blank cells (don’t hallucinate content)
- Provide bounding boxes in a declared coordinate space
  - Preferred: pixel coords (`coord_space="px"`) in rendered PNG space
- Avoid heavy semantics; any region classification should be a hint plus confidence

Never:

- Invent missing content
- Merge tables across pages (use continuation hints)
- Create random/stable IDs

#### Python responsibilities (stitching and canonicalization)

- Carry forward heading context across pages
- Assign statement roles using table headers and config files
- Build hierarchy and ordering deterministically
- Generate deterministic IDs
- Store ambiguous content in an `unresolved` bucket rather than guessing

### Framework Scoping Policy

- Default: `framework_scope = per_pdf`
- Optional: `framework_scope = per_subject` implemented as export-time partitioning
- For per-PDF frameworks, create subject/learning-area grouping nodes under the root when applicable

## Knowledge Graphs

### Academic Standards

#### Entities

1. **StandardsFramework**
   - Root “document/framework” node (default: one per PDF).
2. **StandardsFrameworkItem**
   - Organizational groupings (grade, stage, subject, theme, strand, topic, unit, week)
   - Normative expectation statements (outcomes/competences/objectives/standards)

#### Relationships

1. `(:StandardsFramework)-[:hasChild]->(:StandardsFrameworkItem)`
2. `(:StandardsFrameworkItem)-[:hasChild]->(:StandardsFrameworkItem)`

#### Requirements

**Mandatory**

1. **Document scope**: Identifies the document so we can create a `StandardsFramework` root node.
2. **Normative learning expectations**: Standards/outcomes/competencies/objectives (not merely narrative). These become leaf `StandardsFrameworkItem` nodes (e.g., `normalizedStatementType="Standard"`).
3. **Grouping structure (hierarchy)**: Subject/grade/theme/topic/etc. These become grouping `StandardsFrameworkItem` nodes (e.g., `normalizedStatementType="Standard Grouping"`) connected via `hasChild`.

**Optional**

1. Grades/age bands/stage bands (more consistent groupings)
2. Subjects/learning areas (separate subtrees or per-subject export partitions)
3. Codes/identifiers (stable IDs and cross-references)
4. Performance indicators/expected standards (notes or child items, depending on policy)
5. Time structure (weeks/terms) for grouping + sequencing hints
6. Metadata (language, publisher/ministry, year, jurisdiction) for provenance/filtering
7. Assessment guidance/activities/pedagogy (store as guidance; optionally export as curriculum elements)

### Learning Components

#### Entity

1. **LearningComponent**
   - A granular skill/concept aligned to standards.

#### Relationship

1. `(:LearningComponent)-[:supports]->(:StandardsFrameworkItem)`

#### Requirements

**Mandatory**

1. Standards items exist (`StandardsFrameworkItem`) to anchor `supports`.
2. Component-like statements: explicit skills OR granular outcomes/objectives OR splittable bullets under standards.
3. Sufficient clarity to extract atomic skills (prefer actionable/measurable statements).
4. Alignment anchor for each LC: each LC must support at least one `StandardsFrameworkItem`.

**Optional**

1. Clear separation between “skill” vs “activity/resource”
2. Granularity cues (bullets/numbering/subskills)
3. Examples/indicators (helps splitting)
4. Cross-subject tags (reuse)
5. Terminology definitions (normalize synonyms)
6. Assessment rubrics (validate measurability)

### Learning Progressions

#### Relationships

1. `(:StandardsFrameworkItem)-[:buildsTowards]->(:StandardsFrameworkItem)` (directional)
2. `(:StandardsFrameworkItem)-[:relatesTo]->(:StandardsFrameworkItem)` (associative)

#### Requirements

**Mandatory**

1. Standards items exist (`StandardsFrameworkItem`).
2. Evidence of sequencing:
   - Explicit prereq/progression language (“builds on”, “prior knowledge”), OR
   - Structured order implying development (grade/stage ordering, scope-and-sequence).

**Mandatory for fine-grained progressions**

1. Comparable adjacent levels (e.g., Grade 1/2/3) or clearly separable per-level items (not only banded stages like “III–VI”).

**Optional (strongly recommended)**

1. Explicit progression charts/tables
2. Stable coding scheme encoding grade/strand
3. Scope-and-sequence (weeks/terms)
4. Examples of increasing complexity
5. Teacher guidance about prior/next learning

### Curriculum Elements and `hasEducationalAlignment` (Optional)

#### Why this is optional

Many non-US curriculum PDFs are standards/syllabi with some guidance, not full lesson materials. Only create curriculum entities when the PDF structure supports them clearly.

#### Allowed alignment forms

- `(:Course|:LessonGrouping|:Lesson|:Activity|:Assessment|:Material)-[:hasEducationalAlignment]->(:StandardsFrameworkItem)`

#### Practical mapping guidance (when we do create curriculum elements)

- **Course**: the highest-level instructional container (often Grade+Subject, or a year-long thematic course)
- **LessonGrouping**: unit/module/theme
- **Lesson**: week/sub-theme/lesson-sized chunk (if present)
- **Activity**: discrete task in “Learning Activities” fields
- Always mark curriculum entities derived from standards PDFs as **synthetic/derived** and attach provenance.

### Step 1: Structural per-page intermediate representation (IR) extraction from PDF

We begin by extracting structural information from each page of the PDF document using 
a suitable vision LLM. This includes artifacts such as text blocks, figures, images, 
tables, their respective positions on the page, and so on. The extracted data is stored 
in a per-page Intermediate Representation (IR) JSON format.

### Step 2: Verifying continuity of extracted page IRs

After extracting the per-page IRs, we need to ensure that the extracted data is 
continuous and coherent across pages. This step involves verifying that elements that
span multiple pages are correctly identified and linked, and that there are no missing
or misidentified elements. This verification is crucial to maintain the integrity of
the data before stitching it into a single document IR JSON (next step).

This step also leverages a vision LLM to analyze the sequence of per-page IR JSONs and 
verify their continuity.

### Step 3: Stitching single document IR JSON from (Verified) per-page IR JSONs

Once we have verified the continuity of the per-page IR JSONs, we can stitch them
together to form a single document IR JSON. This involves merging the individual page
IR JSONs into a cohesive representation of the entire document, ensuring that all
elements are correctly positioned and linked.

This step is deterministically done in Python and does not require any special models. 

### Step 4: Creating canonical IR from document IR

After obtaining the single document IR JSON, we need to convert it into a canonical IR
format that aligns with the Learning Commons ontology. This step involves mapping the 
elements from the document IR JSON to the corresponding concepts and relationships 
defined in the Learning Commons ontology. This canonical IR serves as a standardized 
format that can be used for further processing and knowledge graph construction.

This step currently requires a custom Curriculum Skeleton file for each PDF document to
guide the conversion process. The configuration file specifies how to map the elements
from the document IR JSON to the Learning Commons ontology.

In `examples/senegal/curriculum_skeleton_reading.json`, we provide such an example 
configuration file for Senegal's reading curriculum that can be used as a starting 
point for creating custom configurations for other PDF documents.

NB: This config file was actually created by an LLM with human-in-the-loop verification 
and editing. Thus, although it seems to contain a lot of hard-coded values, it can be 
automated to a large extent using a suitable system prompt, the raw PDF document, the 
document IR JSON, and an LLM. Furthermore, the config is purposely verbose to 
demonstrate most of the available options---in reality, many of the options can be 
omitted for simplicity and sensible defaults are set in the code. In addition, we are
actively working on improving the canonical IR pipeline to reduce its complexity and 
the amount of manual configuration needed.

### Step 5: Creating knowledge graphs from canonical IR

Finally, we construct the Learning Commons knowledge graphs from the canonical IR. This
involves creating nodes and edges in the knowledge graphs that correspond to the
concepts and relationships defined in the Learning Commons ontology. The resulting
knowledge graphs can then be used for various applications, such as curriculum
analysis, recommendation systems, and educational content generation.
