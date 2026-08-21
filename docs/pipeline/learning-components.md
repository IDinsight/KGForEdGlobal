# Learning Components KG

Learning Components construction is the fifth stage of the pipeline. It starts from the
validated Academic Standards KG, decomposes eligible source standards into atomic
skills, reconciles duplicate skills, and connects the resulting `LearningComponent`
nodes back to the source standards with `supports` relationships.

For the broader workflow, see the [pipeline overview](index.md) and
[architecture](../architecture.md).

---

## Purpose

The Academic Standards graph preserves the source-facing curriculum hierarchy. The
Learning Components phase adds a finer-grained skill layer that can be shared across
standards when the generated skill meaning is equivalent.

This stage produces:

- `LearningComponent` nodes representing atomic skills; and
- `supports` relationships from each Learning Component to the source
  `StandardsFrameworkItem` (SFI) that claims it.

Learning Components are derived from the validated Academic Standards graph. The
original PDF is not independently reinterpreted at this stage.

---

## Entry point and configuration

Learning Components construction is the second phase of the shared KG command:

```text
backend/src/skg/entries/create_kgs.py
```

From the `backend/` directory, run:

```bash
python src/skg/entries/create_kgs.py <config.json>
```

The command reads the `kgs.lc` section of the shared `RunConfig`.

Important settings include:

| Setting | Purpose |
| --- | --- |
| `generation_instructions` | Curriculum-specific guidance for decomposing standards into atomic skills |
| `lc_source_statement_types` | Optional allowlist of source-facing SFI types eligible for LC generation |
| `lc_include_sibling_context` | Optionally include sibling SFIs as disambiguation-only context |
| `lc_request_batch_size` | Number of source SFIs included in each generation request |
| `lc_max_failure_rate` | Maximum tolerated fraction of source SFIs that fail decomposition |
| `lc_semantic_dedup` | Enable or disable semantic duplicate adjudication |
| `lc_dedup_scope` | Bound where equivalent skills are allowed to merge |
| `lc_manual_review_overrides` | Optional reviewed override for unresolved ancestor context |

---

## How Learning Components construction works

### 1. Gate on the Academic Standards KG

LC generation requires the Academic Standards validation report to have passed without
errors. The system will not generate Learning Components from an invalid standards KG.

Recorded Academic Standards gaps do not necessarily block the entire phase. By
default, LC generation proceeds over the resolved portion of the graph and excludes
source SFIs whose ancestor path passes through an unresolved root-fallback edge. Any
such gaps are recorded in the eligibility report and phase summary.

### 2. Select eligible source standards

Selection is deterministic.

If `lc_source_statement_types` is configured, only SFIs with those source-facing
statement types are eligible. If it is omitted, the default is to select leaf SFIs
whose normalized statement type is `Standard`.

Every excluded SFI is recorded with an explicit reason. A non-empty standards graph
that produces zero eligible source SFIs fails rather than generating an empty LC layer.

### 3. Build bounded generation requests

For each eligible SFI, Python builds a deterministic request containing:

- the authoritative source-standard description;
- framework metadata;
- direct parent information; and
- the resolved `hasChild` ancestor path needed to interpret curriculum and grade scope.

Sibling SFIs can optionally be included for disambiguation, but they are context only.
Source codes are not supplied as skill-generation content.

### 4. Generate atomic skills

A bounded LLM request decomposes each source SFI into one or more atomic skills. An
already atomic standard may therefore produce a single Learning Component candidate.

Responses must pass deterministic quality checks, including configured limits on skill
count and skill-text length. Invalid responses are retried through the structured LLM
validation flow.

Generation is sequential and resumable. Successful responses are persisted as they
complete. Failed requests are recorded separately and can be retried on a later run.
The phase fails only when the configured `lc_max_failure_rate` is exceeded.

Unlike the Academic Standards SFI extraction and hierarchy phases, LC generation does
not use a separate producer/checker LLM pair; bounded model output is constrained by
structured Python validation and retry logic.

### 5. Deduplicate generated skills

Generated skills are first grouped by normalized text within the configured
`lc_dedup_scope`.

When semantic deduplication is enabled, deterministic blocking nominates plausible
candidate pairs. A bounded LLM judge decides whether nominated skills are semantically
equivalent, and deterministic clustering assembles accepted matches while guarding
against contradictory merge chains.

The dedup scope can be configured as:

- `framework` — allow merging anywhere in the document;
- `top_ancestor` — merge only under a shared top-level ancestor;
- `parent` — merge only among sibling standards; or
- `none` — disable cross-SFI merging.

### 6. Mint Learning Components and `supports` relationships

Python deterministically mints each canonical Learning Component using a UUIDv5
identity derived from the document, deduplication scope, and canonical skill text.

A skill claimed by several eligible SFIs can therefore become one Learning Component
with multiple source claims rather than several duplicate nodes.

The system then emits one deterministic `supports` relationship for each unique
Learning Component / claiming-SFI pair:

```text
LearningComponent ──supports──> StandardsFrameworkItem
```

### 7. Validate and export the combined graph

Before export, Python validates LC-level invariants including:

- deterministic identifier recomputation;
- real relationship endpoints;
- one primary `supports` edge per LC/SFI pair;
- coverage of successfully decomposed eligible SFIs;
- provenance consistency; and
- summary-count reconciliation.

The final step merges the validated Academic Standards bundle with the LC layer and
validates the complete graph before writing the combined AS+LC artifacts.

---

## Output artifacts

Learning Components artifacts are written under:

```text
<output_dir>/<doc_key>/kgs/
```

Key artifacts include:

```text
kgs/
├── lc_eligible_sfis.json
├── lc_eligibility_report.json
├── lc_generation_requests.jsonl
├── lc_generation_responses.jsonl
├── lc_generation_failures.json
├── lc_dedup_candidate_pairs.jsonl
├── lc_dedup_verdicts.jsonl
├── lc_dedup_groups.json
├── learning_components.jsonl
├── lc_supports_edges.json
├── lc_generation_summary.json
├── lc_entity_provenance.json
├── as_lc_nodes.jsonl
├── as_lc_relationships.jsonl
└── as_lc_kg_bundle.json
```

These artifacts preserve eligibility decisions, generation results, deduplication
judgments, provenance, failures, and the final graph so the LC phase can be audited
without treating the final bundle as a black box.

---

## Stage boundary

Learning Components construction is currently the final stage of the current production
pipeline. It adds a generated atomic-skill layer to the source-grounded Academic
Standards hierarchy while preserving explicit provenance back to the standards and,
through them, to the original document evidence.

The `as_lc_kg_bundle.json` contains the Academic Standards framework and items,
Learning Components, `hasChild` relationships, `supports` relationships, unresolved
records, provenance, summaries, and validation results for the combined graph.
