# Output artifacts and integration contract

The pipeline persists many files because its intermediate decisions are intended to be
inspectable and resumable. Downstream applications usually need only a small subset of
those artifacts.

This page explains which outputs to consume, the graph and identifier semantics those
outputs preserve, and which implementation details should not become accidental
integration dependencies.

For how the artifacts are produced, see the [pipeline overview](../pipeline/index.md).
For operational recovery and artifact-first debugging, see
[Run, resume, and debug](../guides/running-and-debugging.md).

---

## Choose the output that matches your consumer

| Need                                                                                   | Recommended artifact                                      | Shape                                                                                                               |
|----------------------------------------------------------------------------------------|-----------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| Complete validated Academic Standards graph, including provenance and unresolved state | `kgs/as_kg_bundle.json`                                   | One structured JSON bundle using the pipeline's internal export models                                              |
| Academic Standards in the Learning Commons-shaped JSONL delivery format                | `kgs/as_nodes.jsonl` + `kgs/as_relationships.jsonl`       | Compact aliased JSONL records for `StandardsFramework`, `StandardsFrameworkItem`, and `hasChild`                    |
| Complete validated Academic Standards + Learning Components graph                      | `kgs/as_lc_kg_bundle.json`                                | One structured JSON bundle containing AS, LCs, `hasChild`, `supports`, provenance, unresolved state, and validation |
| Combined graph as flat records for a bulk graph loader                                 | `kgs/as_lc_nodes.jsonl` + `kgs/as_lc_relationships.jsonl` | Internal-schema JSONL projection with an `entity_type` discriminator on nodes                                       |

For most programmatic integrations, start with a bundle. Bundles are self-contained,
carry validation and unresolved-state information with the graph, and avoid requiring a
consumer to join several working artifacts correctly.

If a downstream system specifically expects the current Learning Commons-shaped
Academic Standards wire format, use `as_nodes.jsonl` and `as_relationships.jsonl`
instead.

!!! warning "The two JSONL projections are different contracts"
    `as_nodes.jsonl` / `as_relationships.jsonl` are the **Academic Standards
    Learning Commons-shaped wire projection**. `as_lc_nodes.jsonl` /
    `as_lc_relationships.jsonl` are a **combined flat internal-schema projection** for
    bulk loading. Do not parse the combined files with the AS Learning Commons JSONL
    schema, and do not assume their field naming or nesting is interchangeable.

---

## Validate before publishing or ingesting

A final artifact existing on disk does not by itself mean the run is suitable for
release. Final compilation writes validation information so failures remain inspectable.

### Academic Standards

Before consuming the standalone AS graph, require:

```text
as_kg_bundle.json -> validation_report.passed == true
as_kg_bundle.json -> validation_report.errors == []
```

The same report is also written separately as:

```text
as_validation_report.json
```

### Academic Standards + Learning Components

Before consuming the combined graph, require:

```text
as_lc_kg_bundle.json -> validation_report.passed == true
as_lc_kg_bundle.json -> validation_report.errors == []
```

The combined compiler first requires a passed, error-free Academic Standards bundle,
then validates the merged graph again. Its validation report checks the combined LC
endpoints, LC edge coverage, identifier collisions, provenance presence, and summary
alignment.

### A passed graph can still contain unresolved or excluded material

`passed == true` means the exported graph satisfies the pipeline's deterministic graph
and reconciliation contracts. It does **not** mean that every source-visible curriculum
item was automatically resolved.

Inspect:

```text
bundle.unresolved_items
bundle.summary
```

before defining your own publication policy. For example, the AS graph can preserve an
explicit unresolved root-fallback relationship, and the combined bundle can report LC
source exclusions or LC generation failures while still reconciling the final graph
correctly.

---

## Artifact tiers

A useful integration boundary is to treat the KG directory as three classes of output.

### 1. Consumer-facing graph outputs

These are the normal integration surfaces:

```text
as_kg_bundle.json
as_nodes.jsonl
as_relationships.jsonl
as_lc_kg_bundle.json
as_lc_nodes.jsonl
as_lc_relationships.jsonl
```

Choose one representation for a given consumer rather than joining equivalent
representations together.

### 2. Release, provenance, and unresolved-state artifacts

These help a release process decide whether and why the graph is acceptable:

```text
as_validation_report.json
as_unresolved_items.json
as_entity_provenance.json
lc_entity_provenance.json
lc_generation_summary.json
lc_generation_failures.json
kg_run.json
kg_run_manifest.json
```

Most of this information is also embedded or summarized in the final bundles, but the
standalone files are convenient for review and operations.

### 3. Working and audit artifacts

Files such as extraction windows, producer/checker responses, candidate registries,
dedup review sets, merge groups, parent-candidate sets, and LC dedup verdicts exist to
make the pipeline auditable and resumable.

Examples include:

```text
sfi_extraction_*
sfi_candidate_registry.json
sfi_dedup_*
sfi_merge_*
has_child_*
lc_eligibility_report.json
lc_generation_*.jsonl
lc_dedup_*
learning_components.jsonl
lc_supports_edges.json
```

These are valuable diagnostic contracts *inside a pinned pipeline version*, but a
downstream application should not depend on them merely because they are available.
Their schemas can evolve as the implementation gains new evidence, validation, or
resume behavior.

If a consumer intentionally integrates with a working artifact, pin the repository
revision and add a fixture/schema test for that dependency.

---

## `as_kg_bundle.json`

The Academic Standards bundle is the complete validated output of Stage 4. Its top-level
shape is:

```json
{
  "entity_provenance": {},
  "framework": {},
  "items": [],
  "relationships_has_child": [],
  "summary": {},
  "unresolved_items": {},
  "validation_report": {}
}
```

It contains:

- exactly one `StandardsFramework` for the source document;
- finalized `StandardsFrameworkItem` nodes, including both curriculum groupings and
  normative learning statements;
- resolved `hasChild` relationships;
- entity-level provenance;
- unresolved/finalization reporting;
- aggregate counts; and
- final validation state and input fingerprints.

This bundle is also the authoritative input to Learning Components construction. The LC
stage does not rebuild Academic Standards from a separate representation.

---

## `as_nodes.jsonl` and `as_relationships.jsonl`

These are compact, Learning Commons-shaped delivery records for the **Academic
Standards layer only**.

### Node record

Each line has the outer shape:

```json
{
  "identifier": "...",
  "labels": ["StandardsFrameworkItem"],
  "properties": {
    "caseIdentifierUUID": "...",
    "description": "..."
  },
  "type": "node"
}
```

Important wire-format behavior:

- property aliases use the Learning Commons-style names such as
  `caseIdentifierUUID`, `academicSubject`, and `statementType`;
- `null` properties are omitted;
- `isCurrent` is serialized as the string `"true"` or `"false"`;
- mapped `gradeLevel`, when present, is serialized as a JSON-array **string** such as
  `"[\"2\"]"`, not as a nested JSON array; and
- the outer `identifier` must equal `properties.identifier`.

### Relationship record

Each line has the outer shape:

```json
{
  "identifier": "...",
  "label": "hasChild",
  "properties": {
    "relationshipType": "hasChild",
    "sourceEntityValue": "...",
    "targetEntityValue": "..."
  },
  "source_identifier": "...",
  "source_labels": ["StandardsFrameworkItem"],
  "target_identifier": "...",
  "target_labels": ["StandardsFrameworkItem"],
  "type": "relationship"
}
```

This projection intentionally omits the richer pipeline-internal metadata present in
`as_kg_bundle.json`.

---

## `as_lc_kg_bundle.json`

The combined bundle is the complete validated output of Stage 5. It composes the
validated Academic Standards content with the Learning Components layer.

Its top-level shape is:

```json
{
  "entity_provenance": {},
  "framework": {},
  "items": [],
  "learning_components": [],
  "relationships_has_child": [],
  "relationships_supports": [],
  "summary": {},
  "unresolved_items": {},
  "validation_report": {}
}
```

The existing Academic Standards framework, items, and `hasChild` relationships are
preserved when the LC layer is added. The bundle then adds:

- canonical `LearningComponent` nodes;
- primary `supports` relationships;
- LC provenance under `entity_provenance.learning_components`;
- the LC generation summary;
- LC failure/exclusion reporting under `unresolved_items.learning_components`; and
- a new merged-graph validation report.

For a consumer that needs both standards and skills, this is normally the safest single
artifact to ingest.

---

## `as_lc_nodes.jsonl` and `as_lc_relationships.jsonl`

These files flatten the combined bundle for graph loaders. They do **not** use the
Learning Commons wire models used by `as_nodes.jsonl` and `as_relationships.jsonl`.

### Combined node records

Each line contains one internal node record plus an injected discriminator:

```json
{
  "entity_type": "LearningComponent",
  "identifier": "...",
  "description": "...",
  "metadata": {}
}
```

`entity_type` is one of:

```text
StandardsFramework
StandardsFrameworkItem
LearningComponent
```

The remainder of each object uses the corresponding pipeline model's normal JSON field
names, including snake_case names such as `academic_subject`, `case_identifier_uuid`,
and `in_language`.

### Combined relationship records

Each line is an internal `Relationship` record. Both `hasChild` and `supports` are
present in the same file, and pipeline metadata is retained.

A loader should dispatch relationships using `relationship_type` and should resolve
endpoints using `source_entity_key` / `source_entity_value` and
`target_entity_key` / `target_entity_value`. Do not assume all node types share one
identifier field convention.

---

## Graph semantics

### Node types

| Entity                   | Meaning                                                       | Primary graph identifier used by current relationships |
|--------------------------|---------------------------------------------------------------|--------------------------------------------------------|
| `StandardsFramework`     | Root container for one source framework                       | `case_identifier_uuid`                                 |
| `StandardsFrameworkItem` | Source-facing grouping or standards item                      | `case_identifier_uuid`                                 |
| `LearningComponent`      | Canonical atomic skill derived from one or more eligible SFIs | `identifier`                                           |

For the current Academic Standards export, `identifier` and `case_identifier_uuid` are
minted to the same UUID for the framework and SFIs. Consumers should still follow each
relationship's explicit endpoint key rather than depending on that equality.

### `hasChild`

Direction is always:

```text
parent --hasChild--> child
```

The parent can be either the `StandardsFramework` root or another
`StandardsFrameworkItem`. The child is always a `StandardsFrameworkItem`.

Current internal endpoint keys are:

```text
source_entity_key = case_identifier_uuid
target_entity_key = case_identifier_uuid
```

The hierarchy can be a DAG rather than a strict single-parent tree when the source
supports multiple direct memberships.

An unresolved root-fallback relationship is still a real `hasChild` edge. In the
Learning Commons-shaped relationship projection it receives
`resolutionStatus = "unresolvedRootFallback"`; the internal bundle also retains the
richer unresolved metadata/reporting.

### `supports`

Direction is always:

```text
LearningComponent --supports--> StandardsFrameworkItem
```

Current internal endpoint keys are:

```text
source_entity_key = identifier
target_entity_key = case_identifier_uuid
```

There is one primary `supports` edge per unique `(LearningComponent, claiming SFI)`
pair. A single LC can therefore support several standards after deduplication.

The relationship metadata contains the generation request IDs and a
`support_confidence`. When several generated claims from the same SFI collapse into the
same LC, that edge uses the minimum contributing confidence.

---

## Identifier stability

Identifiers are deterministic, but deterministic does not mean immutable under every
source or policy change.

### Document key

`doc_key` is the SHA-256 digest of the source PDF bytes. A byte-identical source PDF
produces the same document key. Changing the PDF bytes changes the document namespace
used throughout the run.

### Framework

The framework UUID is UUIDv5-minted from the canonical namespace and the document key.

### Standards Framework Items

Final SFI UUIDs are UUIDv5-minted from a resolved SFI identity key. That identity is
document-scoped and can depend on curriculum policy such as code handling, identity
scope, and duplicate reconciliation.

Do **not** use `statement_code`, `description`, source row number, or list ordinal as a
substitute primary key. They may be absent, repeated, corrected, or non-unique in the
source.

### Learning Components

LC UUIDs are UUIDv5-minted from:

- the source document key;
- the configured LC dedup scope key; and
- a hash of the canonical normalized skill text.

The displayed `description` is a deterministic representative surface form and is not
itself the complete identity contract. Several source claims can resolve to one LC.

### Relationships

`hasChild` and `supports` relationship IDs are also deterministic UUIDv5 values derived
from the document key and their resolved endpoints.

### Changes that can intentionally change IDs

Expect identifiers to change when identity-driving inputs change, including:

- source PDF bytes;
- `LC_CANONICAL_NAMESPACE_UUID`;
- SFI identity/code-scope policy or dedup outcomes;
- hierarchy endpoint resolution for relationship IDs;
- LC dedup scope or canonical skill identity; or
- implementation changes that intentionally revise the identity-key contract.

For reproducible downstream datasets, keep the source PDF, relevant configuration, and
canonical namespace under version control or otherwise record them with the release.

---

## Provenance contract

The final bundles preserve provenance separately from the slim delivery projection.

### Academic Standards provenance

`as_kg_bundle.json -> entity_provenance` contains entries for:

```text
framework
items
kg_run_manifest
relationships_has_child
```

SFI provenance connects finalized nodes to merge groups, registry candidates, source
windows, DocumentIR segments, page indexes, source references, and audit information.

### Learning Components provenance

The combined bundle adds:

```text
entity_provenance.learning_components
```

keyed by LC identifier. LC metadata and provenance retain the claiming SFI UUIDs,
source segments/pages/windows, generation request IDs, claim confidence, skill text,
ancestor context, tags, and the originating Academic Standards bundle fingerprint.

A downstream trace can therefore proceed roughly as:

```text
LearningComponent
    -> supports edge
    -> StandardsFrameworkItem
    -> SFI provenance
    -> DocumentIR segment / source window
    -> page provenance
```

For audit-sensitive applications, preserve the bundle rather than ingesting only the
slim JSONL projection.

---

## Schema versioning and compatibility

The environment must provide a non-empty:

```text
LEARNING_COMMONS_EXPORT_SCHEMA_VERSION
```

before the Learning Commons export is compiled. The configured value is recorded at:

```text
bundle.validation_report.learning_commons_export_schema_version
```

It is also part of the final AS export fingerprints.

Use this value when deciding whether a consumer supports the Learning Commons-shaped AS
export contract. It does **not** version every pipeline-internal working artifact or
replace pinning the repository revision when a consumer depends on internal bundle or
combined-projection fields.

For a production integration, a useful release record is therefore:

```text
source PDF digest / doc_key
repository revision
runtime config revision
LC_CANONICAL_NAMESPACE_UUID
LEARNING_COMMONS_EXPORT_SCHEMA_VERSION
final bundle validation report
```

---

## Ordering and serialization

Writers use deterministic ordering where the pipeline relies on stable output, but
consumers should treat identifiers and explicit graph fields as semantic and list/file
order as serialization detail unless an artifact documents otherwise.

In particular:

- do not infer hierarchy from node order;
- do not infer relationship direction from source order in the PDF;
- do not infer LC identity from the first claim or first surface form; and
- do not infer SFI identity from statement codes alone.

Use `hasChild`, `supports`, endpoint keys, identifiers, and provenance explicitly.

---

## Recommended ingestion checks

Before loading a release into another system, check at least:

1. the intended bundle's `validation_report.passed` is `true` and `errors` is empty;
2. the `learning_commons_export_schema_version` is supported by the consumer;
3. `doc_key` and the source/config release are the expected ones;
4. unresolved items satisfy your publication policy;
5. every relationship endpoint resolves using its declared entity key/value;
6. the consumer understands multi-parent `hasChild` topology;
7. the consumer preserves `LearningComponent -> supports -> StandardsFrameworkItem`
   direction; and
8. if provenance matters, the full bundle is retained even if a flat projection is
   loaded into the serving graph.

For diagnosing a failed check, use the
[operator/debugging guide](../guides/running-and-debugging.md) to trace the earliest
incorrect artifact rather than repairing the final export by hand.
