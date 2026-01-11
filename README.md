# SenegalKG

<!-- Badges -->
<p style="text-align: center;">
  <a href="https://github.com/econchick/interrogate">
    <img src="./interrogate_badge.svg" alt="Docstring coverage: interrogate">
  </a>
  &nbsp;
  <a href="https://github.com/pylint-dev/pylint">
    <img src="https://img.shields.io/badge/linting-pylint-yellowgreen" alt="Linting: pylint">
  </a>
</p>

## Table of Contents

- [Setup Instructions](#setup-instructions)
- [Local Startup Instructions](#local-startup-instructions)
- [Local Clean up Instructions](#local-clean-up-instructions)
- [Overview](#overview)
- [Core Design Decisions](#core-design-decisions)
- [Knowledge Graphs](#knowledge-graphs)
- [The Pipeline](#the-pipeline)
- [References](#references)

## Setup Instructions

1. Install [direnv](https://direnv.net/docs/installation.html).
2. If you are using `zsh`, then add `eval "$(direnv hook zsh)` to the end of your `~/.zshrc` file. If you are using `bash`, then add `eval "$(direnv hook bash)"` to the end of your `~/.bashrc` (or `~/.bash_profile`) file. Ensure you reload the file by running `source ~/.zshrc` or `source ~/.bashrc` (or `source ~/.bash_profile`).
3. Install the latest version of [uv](https://docs.astral.sh/uv/) using: `curl -LsSf https://astral.sh/uv/install.sh | sh`
4. Run `git clone git@github.com:IDinsight/SenegalKG.git` and cd into the root directory of the repo.
5. In the root `.envrc` file, ensure `PROJECT_ENV` is set to `local`.
6. Copy the **root** `.template.env` to `.env` and update the following environment variables in `.env`:
    1. `OPENAI_API_KEY`: Your OpenAI API key.
    2. `PATHS_PROJECT_DIR`: The absolute path to the root directory of the project.
7. Copy the **root** `.template.env.local` to `.env.local`.
8. Allow `direnv` to load the root environment variables by running `direnv allow`.
9. Create a `data` folder in the root directory. This is where you should place the curriculum PDF files you want to process.
10. Create a `results` folder in the root directory. This is where the output files for each step in the pipeline will be saved.
11. cd into the backend directory of the repo and:
    1. Copy `.template.env.local` to `.env.local`.
    2. Allow `direnv` to load the backend environment variables by running `direnv allow`.

## Local Startup Instructions

1. cd into the `backend` directory of the repo and:
    1. Run `make fresh-env`. This will create a new virtual environment for the backend and install all dependencies.
    2. Run `source .venv/bin/activate`: This will activate the virtual environment created by `make fresh-env`.
2. See [The Pipeline](#the-pipeline) section for instructions on how to run each step of the pipeline.

## Local Clean up Instructions

1. In the backend directory, run `deactivate`. This will exit out of the virtual environment created by `uv`.

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

1. **Zambia**: *LOWER PRIMARY EDUCATION SYLLABI GRADE 1–3 ... (2024)*
   - Table-like pages with columns such as **Topic / Sub-topic / Specific Competences / Learning Activities / Expected Standard**
   - Often includes hierarchical codes (e.g., `3.9.4.1`)
   - Expectation statements are strong; progressions mostly implied by grade ordering / code patterns

2. **Uganda**: *Primary 1 (P1) Curriculum*
   - Thematic curriculum: **12 themes**, **36 sub-themes**
   - Sub-theme ~ weekly; includes expected learning outcomes and competence statements across strands/learning areas
   - Progressions mostly implied by theme/week sequencing

3. **Tanzania**: *Curriculum for Primary Education (Standard I–VI)*
   - Higher-level framework; organized by stages (Std I–II vs Std III–VI), learning areas/subjects
   - “Main competences” + “Specific competences” in tables
   - Upper stage often banded rather than grade-by-grade
   - May include non-English content; preserve original text and language tags in CanonicalIR/metadata.

4. **Ghana**
   - Highly structured standards with stable codes and clear roles (standards/indicators/exemplars)
   - Useful as a reference implementation and inference playground

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

## The Pipeline

The SenegalKG pipeline currently converts a raw curriculum PDF document from non-U.S.
countries into a knowledge graph that follows the [Learning Commons ontology](https://docs.learningcommons.org/knowledge-graph/v1-2-0/understanding-knowledge-graph/about-knowledge-graph).

At the moment, we only create the following knowledge graphs from the curriculum PDF:

- Academic Standards
- Learning Components
- Learning Progressions

Each step can be executed from the `backend` directory using their specified commands.

### Step 1: Structural Per-Page IR Data Extraction From PDF

```bash
python src/skg/entries/extract_page_ir.py ../data/tanzania/tanzania.pdf -c Tanzania -y 2023 -l en -l sw -l fr -l zh-Hans -l ar -o ../results
```

We need to extract structural information from each page of the PDF document. This
includes text blocks, figures, images, tables, their respective positions on the page,
and so on. The extracted data is stored in a per-page Intermediate Representation (IR)
JSON format.

This step uses a vision LLM to analyze each page of the PDF document and extract the
required structural information. The LLM is prompted with a carefully designed prompt
that guides it to identify and extract the relevant elements from the page.

### Step 2: Verifying Continuity of Extracted Page IR JSONs

```bash
python src/skg/entries/verify_page_ir_continuity.py ../data/tanzania/tanzania.pdf /path/to/extraction_run_results_dir
```

After extracting the per-page IR JSONs, we need to ensure that the extracted data is
continuous and coherent across pages. This step involves verifying that elements that
span multiple pages are correctly identified and linked, and that there are no missing
or misidentified elements. This verification is crucial to maintain the integrity of
the data before stitching it into a single document IR JSON.

This step uses a vision LLM to analyze the sequence of per-page IR JSONs and verify
their continuity. The LLM is prompted with a verification prompt that guides it to
check for continuity and coherence across the pages.

### Step 3: Stitching Single Document IR JSON From (Verified) Per-Page IR JSONs

```bash
python src/skg/entries/stitch_document_ir.py ../data/tanzania/tanzania.pdf /path/to/verification_run_results_dir
```

Once we have verified the continuity of the per-page IR JSONs, we can stitch them
together to form a single document IR JSON. This involves merging the individual page
IR JSONs into a cohesive representation of the entire document, ensuring that all
elements are correctly positioned and linked.

This step is deterministically done in Python and does not require LLMs. The stitching
process involves iterating through the verified per-page IR JSONs and combining them
into a single JSON structure that represents the entire document.

### Step 4: Creating Canonical IR from Document IR

```bash
python src/skg/entries/create_canonical_ir.py ../examples/tanzania/parser_config.json ../data/tanzania/tanzania.pdf /path/to/stitching_run_results_dir
```

After obtaining the single document IR JSON, we need to convert it into a canonical
intermediate representation (IR) that aligns with the Learning Commons ontology. This
step involves mapping the elements from the document IR JSON to the corresponding
concepts and relationships defined in the Learning Commons ontology. This canonical IR
serves as a standardized format that can be used for further processing and knowledge
graph construction.

This step currently requires a custom `parser_config.json` for each PDF document to
guide the conversion process. The configuration file specifies how to map the elements
from the document IR JSON to the Learning Commons ontology.

In `examples/tanzania/parser_config.json`, we provide an example configuration file
(for Tanzania curriculum PDF) that can be used as a starting point for creating custom
configurations for other PDF documents. NB: This config file was actually created by
an LLM with human-in-the-loop verification and editing. Thus, although it seems to
contain a lot of hard-coded values, it can be automated to a large extent using a
suitable system prompt the raw PDF document, the document IR JSON, and an LLM.
Furthermore, the config is purposely verbose to demonstrate most of the available
options---in reality, many of the options can be omitted for simplicity and sensible
defaults are set in the code.

### Step 5: Creating Knowledge Graphs from Canonical IR

```bash
python src/skg/entries/create_knowledge_graphs.py ../examples/examples/kg_config.json /path/to/canonical_ir_run_results_dir
```

Finally, we construct the Learning Commons knowledge graphs from the canonical IR. This
involves creating nodes and edges in the knowledge graphs that correspond to the
concepts and relationships defined in the Learning Commons ontology. The resulting
knowledge graphs can then be used for various applications, such as curriculum
analysis, recommendation systems, and educational content generation.

This step currently requires a custom `kg_config.json` for each PDF document to guide
the knowledge graph construction process. The configuration file specifies how to
create the nodes and edges in the knowledge graphs based on the canonical IR and the
Learning Commons ontology. NB: This config file was also created by an LLM with
human-in-the-loop verification and editing. Thus, although it seems to contain a lot of
hard-coded values, it can be automated to a large extent using a suitable system prompt
the canonical IR, the Learning Commons ontology, and an LLM. Furthermore, the config
is purposely verbose to demonstrate most of the available options---in reality, many of
the options can be omitted for simplicity and sensible defaults are set in the code.

## References

1. LC KG overview: https://docs.learningcommons.org/knowledge-graph/understanding-knowledge-graph/about-knowledge-graph
2. Academic standards: https://docs.learningcommons.org/knowledge-graph/entity-and-relationship-reference/academic-standards
3. Learning components: https://docs.learningcommons.org/knowledge-graph/entity-and-relationship-reference/learning-components
4. Learning progressions: https://docs.learningcommons.org/knowledge-graph/v1-2-0/entity-and-relationship-reference/learning-progressions
5. Curriculum (Beta): https://docs.learningcommons.org/knowledge-graph/entity-and-relationship-reference/curriculum
6. 1EdTech CASE background: https://www.imsglobal.org/spec/CASE/v1p1/
