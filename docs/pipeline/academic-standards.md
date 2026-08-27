# Academic Standards KG

Academic Standards KG construction is the fourth stage of the pipeline. It consumes
`DocumentIR` and is the first stage that assigns curriculum semantics to the
source-faithful document representation.

For the broader workflow, see the [pipeline overview](index.md) and
[architecture](../architecture.md).

---

## Purpose

The goal of this stage is to identify the source-facing curriculum statements in a
`DocumentIR`, reconcile repeated or ambiguous occurrences, assign stable identities,
and resolve the direct `hasChild` hierarchy needed for the Academic Standards graph.

The primary entities are:

- one `StandardsFramework` representing the source framework;
- `StandardsFrameworkItem` (SFI) nodes representing source-facing curriculum items; and
- `hasChild` relationships representing the resolved direct hierarchy.

This stage is source-grounded. It does not generate Learning Components or infer
progression relationships.

---

## Entry point and configuration

Academic Standards construction is the first phase of the shared KG command:

```text
backend/src/kgfeg/entries/create_kgs.py
```

From the `backend/` directory, run:

```bash
python src/kgfeg/entries/create_kgs.py <config.json>
```

The command reads the `kgs.as` section of the shared `RunConfig`.
Important settings include:

| Setting                                               | Purpose                                                               |
|-------------------------------------------------------|-----------------------------------------------------------------------|
| `statement_type_policy`                               | Allowed source-facing statement types and their normalized roles      |
| `code_patterns` / `code_parent_rules`                 | Source-code recognition and configured code-parent hints              |
| `identity_scope_statement_types`                      | Curriculum dimensions that participate in SFI identity                |
| `included_table_*` / `excluded_table_*`               | Rules controlling which DocumentIR tables are eligible for extraction |
| `max_rows_per_table_window` / `row_overlap`           | Bounded table extraction-window size                                  |
| `sfi_dedup_*`                                         | Curriculum-specific deduplication context and instructions            |
| `sfi_has_child_parent_policy`                         | Allowed direct parent types and required parent cardinality           |
| `grade_level_statement_types` / `grade_level_mapping` | Local grade extraction and Learning Commons grade mapping             |

The configuration is curriculum-specific, but the construction workflow is shared
across frameworks.

---

## How Academic Standards construction works

The stage performs the following major steps.

### 1. Plan source extraction windows

Python walks the ordered `DocumentIR` and creates bounded source units for SFI
extraction.

- Extractable block segments become block windows.
- Table segments are included only when they match the configured table-selection
  rules.
- Large table bodies can be divided into bounded, overlapping row windows.
- Nearby heading context, source provenance, visible codes, and deterministic hints are
  carried into the extraction request.

The windowing step does not itself claim that a source unit is an Academic Standard.
It only determines what bounded source evidence the extraction agent may inspect.

### 2. Extract source-grounded SFI candidates

For each extraction window, an LLM producer identifies candidate curriculum items using
only the configured statement types and the supplied source evidence.

An independent checker then reviews the producer output. Accepted or corrected results
must pass deterministic Python validation before they become extraction results.

Candidates preserve source anchors back to the DocumentIR segments, table rows or
cells, page provenance, visible source text, codes, and configured identity scope.

### 3. Build the global candidate registry

Window-local candidates are flattened into one document-level registry. Python
normalizes codes and text, canonicalizes configured controlled values, constructs
source-context and identity-scope keys, and identifies possible duplicate groups.

This global step is important because the same curriculum item may appear in more than
one extraction window or source location.

### 4. Reconcile duplicate candidates

Python builds bounded review sets from deterministic duplicate signals such as repeated
codes, normalized text, controlled values, source overlap, and registry warnings.

An LLM producer reviews each bounded set and an independent checker validates the
result. Each group is classified for downstream use rather than being merged merely
because two candidates look similar.

Groups resolved as `singleton` or `merged` can proceed to finalization. Conflict or
needs-review groups remain visible in the audit artifacts and are excluded from
automatic final SFI minting.

### 5. Mint deterministic SFI identities

Python converts eligible merge groups into final SFI records and assigns deterministic
UUIDv5 identities derived from the document and resolved curriculum identity.

Final records preserve the evidence used to construct them, including candidate source
references, source pages and segments, codes, statement types, identity scope,
merge decisions, confidence information, and audit flags.

The LLM does not invent the final SFI identifiers.

### 6. Resolve `hasChild` relationships

Python builds a bounded direct-parent candidate set for each finalized SFI using the
configured parent policy and source-derived evidence such as:

- source-local ordering and grouping context;
- identity-scope agreement;
- visible code-parent hints;
- section-path evidence; and
- table-row and table-context evidence.

A producer LLM chooses the source-supported direct parent or records that the parent
cannot be safely resolved. An independent checker reviews that decision. Python then
enforces the configured parent cardinality and graph constraints.

An unresolved item can be preserved through an explicit root-fallback relationship
instead of assigning an unsupported source parent, when permitted by the hierarchy
policy.

### 7. Compile and validate the Academic Standards KG

The final compilation step builds the `StandardsFramework`, final SFI nodes, and
`hasChild` relationships, then validates both the internal graph and the
Learning Commons-shaped delivery graph.

Validation includes checks for:

- unique and consistent identities;
- relationship endpoint integrity;
- required parent cardinality;
- graph cycles and reachability;
- unresolved-item consistency;
- local-to-Learning-Commons grade mapping; and
- agreement between internal and delivery artifacts.

A failed final validation prevents the Academic Standards graph from being treated as a
successful downstream input.

---

## Output artifacts

Academic Standards artifacts are written under:

```text
<output_dir>/<doc_key>/kgs/
```

Key artifacts include:

```text
kgs/
├── kg_run_manifest.json
├── sfi_extraction_window_plan.json
├── sfi_extraction_windows.jsonl
├── sfi_extraction_results.jsonl
├── sfi_candidate_registry.json
├── sfi_merge_report.json
├── sfi_final_records.json
├── has_child_edges_final.json
├── as_standards_framework.json
├── as_standards_framework_items.jsonl
├── as_relationships_has_child.jsonl
├── as_unresolved_items.json
├── as_validation_report.json
├── as_entity_provenance.json
├── as_nodes.jsonl
├── as_relationships.jsonl
└── as_kg_bundle.json
```

The intermediate extraction, deduplication, and hierarchy artifacts are intentionally
persisted so a run can be inspected and, where supported, resumed without treating the
final graph as a black box.

`as_kg_bundle.json` is the complete validated Academic Standards bundle used by the next
phase of KG creation.

---

For downstream consumption and the distinction between bundle, Learning Commons
wire, and flat-loader projections, see the
[output artifacts and integration contract](../reference/output-artifacts.md).

## Stage boundary

Academic Standards construction converts the source-oriented `DocumentIR` into a
validated curriculum hierarchy. It establishes what the source says and how the
source-facing items are directly organized; it does not yet decompose those standards
into atomic skills.

The next stage, **Learning Components KG construction**, selects eligible finalized
SFIs from this validated graph, decomposes them into Learning Components, deduplicates
those skills, and connects them back to the source standards with `supports`
relationships.

---

## Next

[Learning Components KG →](learning-components.md)
