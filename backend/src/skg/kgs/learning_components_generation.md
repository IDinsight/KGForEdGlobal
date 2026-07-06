# LearningComponents Generation Pipeline: Inputs, Uses, and Outline

## Purpose

This document describes the recommended downstream inputs and workflow for generating `LearningComponent` nodes from an already completed Academic Standards KG export.

The Academic Standards KG v0 pipeline is complete at Step 10 and produces:

```text
StandardsFramework
StandardsFrameworkItem
Relationship(hasChild)
```

LearningComponents are a downstream phase. They should be generated from finalized `StandardsFrameworkItem` records, and each generated LearningComponent should have at least one `supports` relationship to a target SFI in the Academic Standards KG.

## Core rule

Use the Step 10 export as the downstream source of truth.

Do not use extraction candidates, registry candidates, merge groups, intermediate parent-candidate sets, or raw DocumentIR section paths as LearningComponent endpoints.

The stable `supports` targets are finalized `StandardsFrameworkItem` entities from Step 10.

## Recommended source-of-truth input

Prefer loading the complete bundle:

```text
academic_standards_kg_bundle.json
```

This is the safest single input because it contains the framework, SFIs, `hasChild` relationships, provenance, unresolved report, summary, and validation report together.

For graph-native or streaming workflows, the same information may be loaded from the separate Step 10 artifacts listed below.

## Required Step 10 inputs

### 1. `validation_report.json`

Use as a hard gate before generating any LearningComponents.

Required checks:

```text
passed == true
errors == []
```

Purpose:

- Confirms the Academic Standards KG export is internally valid.
- Confirms framework, SFI, and relationship counts align.
- Confirms endpoints exist.
- Confirms every SFI has an incoming `hasChild` edge.
- Confirms every SFI is reachable from the framework root.
- Confirms no self-loops, no SFI cycles, no duplicate relationship IDs, and no duplicate parent/child edge pairs.
- Confirms SFI identifiers and provenance were preserved.

Do not proceed if validation failed.

### 2. `unresolved_items.json`

Use as a second go/no-go gate.

Purpose:

- Reports finalization exclusions from Step 8.
- Reports unresolved/root-fallback relationship cases from Step 9.

Recommended default policy:

```text
excluded_conflict_group_count == 0
excluded_needs_review_group_count == 0
relationship_unresolved_edges == []
```

If unresolved relationship edges are present, either block LC generation or generate only for the resolved subgraph unless the downstream pipeline explicitly supports root-fallback/unresolved hierarchy semantics.

### 3. `standards_framework.json`

Use for framework-level metadata.

Purpose:

- Provides the root framework UUID.
- Provides framework title/name.
- Provides jurisdiction/country, subject, language, provider, license, author, attribution, and document metadata.
- Supplies context that should be passed to prompts and preserved in generated LC metadata.

Typical fields to carry forward:

```text
case_identifier_uuid
case_identifier_uri
name
academic_subject
jurisdiction
in_language
provider
license
author
attribution_statement
metadata.doc_key
metadata.pdf_name
metadata.framework_title
metadata.grades_or_stages
```

### 4. `standards_framework_items.jsonl`

Use as the canonical StandardsFrameworkItem pool.

Purpose:

- Provides all finalized SFI nodes.
- Provides the target entities that LearningComponents may support.
- Provides source-backed SFI descriptions to decompose.
- Provides SFI statement type, normalized type, identifiers, language, and metadata.

Key fields:

```text
case_identifier_uuid
case_identifier_uri
identifier
description
statement_type
normalized_statement_type
statement_code
in_language
grade_level
metadata.identity_key
metadata.source_context_keys
metadata.source_page_indexes
metadata.source_segment_ids
metadata.source_registry_candidate_ids
metadata.candidate_source_refs
metadata.candidate_source_texts
metadata.merge_decision
metadata.merge_reason
```

Seed selection policy:

- Prefer generating LearningComponents from teachable/leaf SFIs.
- For the Nigeria Primary 1-3 Mathematics KG, use `Performance Objective` and `Content` as primary LC seeds.
- Treat `Grade`, `Theme`, `Sub-Theme`, and `Topic` primarily as context, not as LC generation targets, unless a downstream product explicitly wants LearningComponents for grouping nodes.

Generic seed selection rule:

```text
Include SFIs whose normalized_statement_type == "Standard"
and whose statement_type is configured as teachable/decomposable.

Exclude SFIs whose normalized_statement_type == "Standard Grouping"
unless explicitly configured otherwise.
```

### 5. `relationships_has_child.jsonl`

Use to reconstruct the Academic Standards hierarchy.

Purpose:

- Builds the parent/child graph from the framework root to each SFI.
- Recovers ancestor paths for prompt context.
- Recovers grade/theme/sub-theme/topic for lower-level SFIs.
- Enables sibling-context lookup.
- Enables roll-up from LearningComponents to higher-level curriculum organizers through SFI ancestry.

Do not rely on `grade_level` alone. In no-code or table-heavy curricula, lower-level SFIs may not carry explicit grade values directly. Derive grade and curriculum scope from the `hasChild` path.

For each LC seed SFI, compute:

```text
framework root
ancestor Grade
ancestor Theme
ancestor Sub-Theme
ancestor Topic
sibling Performance Objectives and Content under the same Topic
child SFIs if the seed itself is a grouping node
```

### 6. `entity_provenance.json`

Use for source traceability and optional prompt grounding.

Purpose:

- Provides source page indexes.
- Provides source segment IDs.
- Provides source window IDs and indexes.
- Provides source registry candidate IDs.
- Provides candidate source texts.
- Provides merge reason and audit information.
- Provides source references that can be used for debugging and citation.

Use provenance to explain where a generated LearningComponent came from.

Do not treat raw `source_context_labels` as the authoritative hierarchy. These labels can be cumulative or noisy. Use `relationships_has_child.jsonl` for hierarchy.

## Optional but useful inputs

### 1. `academic_standards_kg_bundle.json`

Use instead of individual files when possible. It contains all required Step 10 export objects and reports in one file.

### 2. `document_ir.json`

Usually not required for initial LearningComponent generation if the Step 10 export is complete.

Use only when the LC generator needs to recover richer local source text, table rows, or page-level evidence beyond what is available in item metadata and entity provenance.

Do not use DocumentIR section paths as authoritative hierarchy.

### 3. Runtime LC generation config

Recommended separate config for LearningComponents:

```text
which SFI statement types are LC seeds
maximum LearningComponents per SFI
minimum/maximum LC text length
whether to generate from Content, Performance Objective, or both
whether sibling SFI context is allowed
whether secondary supports edges are allowed
whether grouping-node supports edges are allowed
country/document-specific LC instructions
language policy
identity namespace UUID
```

Keep this separate from Academic Standards KG extraction config unless intentionally shared.

## Recommended normalized input object per SFI seed

For each target SFI selected for decomposition, construct an input object similar to:

```json
{
  "framework": {
    "case_identifier_uuid": "...",
    "name": "9-Year Basic Education Mathematics Curriculum for Primary 1-3",
    "jurisdiction": "Nigeria",
    "academic_subject": "Mathematics",
    "in_language": "en",
    "provider": "IDinsight"
  },
  "target_sfi": {
    "case_identifier_uuid": "...",
    "case_identifier_uri": "urn:uuid:...",
    "identifier": "...",
    "description": "...",
    "statement_type": "Performance Objective",
    "normalized_statement_type": "Standard",
    "statement_code": null,
    "in_language": "en"
  },
  "ancestor_path": [
    {
      "statement_type": "Grade",
      "description": "PRIMARY ONE",
      "case_identifier_uuid": "..."
    },
    {
      "statement_type": "Theme",
      "description": "THEME: NUMBER AND NUMERATION",
      "case_identifier_uuid": "..."
    },
    {
      "statement_type": "Sub-Theme",
      "description": "Whole Number",
      "case_identifier_uuid": "..."
    },
    {
      "statement_type": "Topic",
      "description": "Whole numbers 1-5",
      "case_identifier_uuid": "..."
    }
  ],
  "sibling_sfis_under_same_topic": [
    {
      "statement_type": "Content",
      "description": "Sorting and classifying objects leading to idea of 1-5",
      "case_identifier_uuid": "..."
    }
  ],
  "source_provenance": {
    "source_page_indexes": [0, 1, 2],
    "source_segment_ids": ["..."],
    "source_window_ids": ["..."],
    "candidate_source_texts": ["..."]
  },
  "generation_policy": {
    "primary_support_target_uuid": "...",
    "allow_secondary_supports": false,
    "language": "en"
  }
}
```

## LearningComponent generation principles

Each LearningComponent should be:

- Smaller and more teachable than the source SFI.
- Directly supported by the source SFI text and ancestor context.
- Source-language preserving unless translation is explicitly configured.
- Free of invented curriculum scope.
- Free of activities/resources/evaluation prompts unless the source SFI itself is in scope for LC decomposition.
- Stable enough for deterministic identity minting.

Recommended generated LC fields:

```text
learning_component_uuid / identifier
description or name
language
source_framework_uuid
source_sfi_uuid
source_sfi_description
ancestor_path_summary
generation_reason
confidence
metadata.identity_key
metadata.source_page_indexes
metadata.source_segment_ids
metadata.source_window_ids
metadata.generated_from_step10_bundle_fingerprint
```

## Deterministic LC identity

Mint deterministic IDs. Do not use random UUIDs.

Recommended identity material:

```text
LC namespace UUID
framework doc_key
target_sfi.case_identifier_uuid
normalized LearningComponent description
optional component type / ordinal only if needed after text normalization
```

Avoid using only list position as identity material. If the LLM output order changes, identities should remain stable when the LC text and source SFI are unchanged.

## `supports` relationship generation

Every LearningComponent must have at least one `supports` relationship.

Default relationship:

```text
LearningComponent -> supports -> source/decomposed StandardsFrameworkItem
```

Recommended endpoint mapping:

```text
relationship_type = "supports"
source_entity = "LearningComponent"
source_entity_key = "identifier" or "case_identifier_uuid" depending on LC schema
source_entity_value = learning_component.identifier / learning_component_uuid
target_entity = "StandardsFrameworkItem"
target_entity_key = "case_identifier_uuid"
target_entity_value = target_sfi.case_identifier_uuid
```

Preserve in relationship metadata:

```text
source_framework_uuid
target_sfi_statement_type
target_sfi_description
support_reason
support_confidence
generated_from_sfi_uuid
generated_from_sfi_identity_key
ancestor_path_uuids
ancestor_path_descriptions
source_page_indexes
source_segment_ids
```

### Secondary supports edges

Secondary `supports` edges may be useful when one LearningComponent clearly supports more than one SFI.

Use a conservative policy:

- Always support the decomposed target SFI.
- Add secondary supports only when the LC text is explicitly supported by another SFI in the Academic Standards KG.
- Prefer secondary supports to sibling Performance Objective or Content SFIs under the same Topic.
- Avoid automatic supports edges to Grade, Theme, Sub-Theme, or Topic grouping nodes. Roll-up can be inferred through `hasChild` ancestry.
- Do not add secondary supports solely because two SFIs have similar wording.

## General pipeline outline

1. Load `academic_standards_kg_bundle.json` or the separate Step 10 artifacts.
2. Require `validation_report.passed == true` and `validation_report.errors == []`.
3. Require no finalization exclusions and no unresolved relationship edges unless explicitly supported.
4. Build an SFI index by `case_identifier_uuid`.
5. Build a directed `hasChild` graph from `relationships_has_child.jsonl`.
6. Validate the graph locally for endpoint existence, reachability, and acyclic SFI hierarchy before LC generation.
7. Select LC seed SFIs using config-driven statement-type policy.
8. For each seed SFI:
   - recover full ancestor path from the framework root;
   - recover grade/theme/sub-theme/topic context;
   - collect sibling Performance Objective and Content SFIs under the same Topic;
   - attach source provenance from `entity_provenance.json`;
   - construct a compact, source-grounded LC generation request.
9. Generate LearningComponents with structured output.
10. Validate each generated LearningComponent:
    - non-empty description;
    - smaller/more granular than source SFI;
    - language policy respected;
    - no hallucinated grade/topic/context;
    - no duplicate LC identity under the same SFI;
    - source SFI support is explicit.
11. Mint deterministic LC IDs.
12. Create one primary `supports` relationship from each LC to the decomposed SFI.
13. Optionally create secondary `supports` relationships only under conservative evidence rules.
14. Validate supports graph:
    - every LC has at least one supports edge;
    - every supports target exists in Step 10 SFIs;
    - every supports source exists in generated LCs;
    - no duplicate supports relationship IDs;
    - no duplicate source/target supports pairs unless multi-evidence edges are explicitly allowed.
15. Write inspectable LC artifacts and validation reports.

## Recommended LC artifacts

```text
learning_component_generation_manifest.json
learning_component_generation_requests.jsonl
learning_component_generation_responses.jsonl
learning_components.jsonl
relationships_supports.jsonl
learning_component_entity_provenance.json
learning_component_validation_report.json
learning_component_kg_bundle.json
```

## Nigeria-specific recommendation

For the Nigeria Primary 1-3 Mathematics KG:

- Use `Performance Objective` and `Content` SFIs as primary LC seeds.
- Use Grade, Theme, Sub-Theme, and Topic nodes as context.
- Derive grade and topic context from the `hasChild` graph, not from `grade_level` alone.
- Treat all SFIs as no-code SFIs; do not infer curriculum codes from topic numbers or list numbers.
- Preserve English source wording.
- Use source table row/context metadata as grounding, but use Step 10 relationships as the authoritative hierarchy.

## Main pitfalls

### Do not generate from invalid or unresolved Academic Standards KGs

Block or restrict generation when Step 10 validation failed, finalization exclusions exist, or unresolved root-fallback relationships exist.

### Do not use intermediate candidates as supports targets

Only finalized Step 10 `StandardsFrameworkItem` UUIDs are valid Academic Standards supports targets.

### Do not rely on `grade_level` alone

Lower-level SFIs may not carry direct `grade_level` values. Recover grade from the `hasChild` ancestor path.

### Do not parse provenance labels as hierarchy

Use `relationships_has_child.jsonl` as authoritative hierarchy. Provenance labels are for traceability and can be noisy.

### Do not create automatic supports edges to grouping nodes

Prefer supports edges to the decomposed teachable SFI. Let grouping roll-up happen through `hasChild` ancestry unless explicitly configured otherwise.

### Do not over-decompose

A LearningComponent should be teachable and granular, but still grounded in the SFI. Do not invent prerequisite skills or classroom activities unless the LC pipeline explicitly supports those outputs separately.

### Do not translate or rewrite source language by default

Generate in the configured language. Preserve source-language meaning and avoid adding unsupported terminology.
