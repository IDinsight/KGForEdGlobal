# Learning Components KG

Learning Components construction is the fifth stage of the pipeline. It starts from the
validated Academic Standards KG, decomposes eligible source standards into atomic
skills, independently validates those decompositions, reconciles duplicate skills, and
connects the resulting `LearningComponent` nodes back to the source standards with
`supports` relationships.

For the broader workflow, see the [pipeline overview](index.md) and
[architecture](../architecture.md).

---

## Purpose

The Academic Standards graph preserves the source-facing curriculum hierarchy. The
Learning Components phase adds a finer-grained skill layer that can be shared across
standards when the generated skill meaning is equivalent within the configured merge
scope.

This stage produces:

- `LearningComponent` nodes representing atomic teachable skills; and
- `supports` relationships from each Learning Component to every source
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
| `generation_instructions` | Required curriculum-specific decomposition policy supplied to the LC producer and validator |
| `lc_generation_validation_instructions` | Optional curriculum-specific audit policy for the independent LC generation validator |
| `lc_source_statement_types` | Optional allowlist of source-facing SFI types eligible for LC generation |
| `lc_include_sibling_context` | Include sibling SFIs as disambiguation/overlap-avoidance context only |
| `lc_request_batch_size` | Number of eligible source SFIs included in each generation request; default `1` |
| `lc_min_skill_text_length` | Optional minimum character length for one generated skill |
| `lc_max_skill_text_length` | Optional maximum character length for one generated skill |
| `lc_max_skills_per_sfi` | Optional hard ceiling on generated skills per source SFI |
| `lc_max_failure_rate` | Maximum tolerated fraction of eligible source SFIs that fail decomposition |
| `lc_semantic_dedup` | Enable or disable semantic duplicate adjudication; exact normalized duplicates are still grouped |
| `lc_dedup_scope` | Bound where equivalent skills are allowed to merge |
| `lc_dedup_batch_size` | Candidate pairs per semantic dedup adjudication request |
| `lc_dedup_blocking` | Deterministic candidate-nomination thresholds for token, containment, trigram, tag, and neighborhood rules |
| `lc_dedup_instructions` | Optional curriculum-specific semantic duplicate policy for the dedup judge |
| `lc_dedup_language_pack` | Optional profile-defined stopwords and affix-folding rules used only for dedup candidate nomination |
| `lc_manual_review_overrides` | Optional manual-review record; `allow_unresolved_ancestor_context` is the flag used by LC seed selection |

`lc_manual_review_overrides` is persisted in the LC summary. The current selection logic
uses `allow_unresolved_ancestor_context` to decide whether unresolved ancestor paths may
be admitted; any additional review metadata in the record is retained for audit but is
not used to classify eligibility.

---

## How Learning Components construction works

### 1. Gate on the Academic Standards KG

LC generation requires the Academic Standards validation report to have passed without
errors. The system will not generate Learning Components from an invalid standards KG.

Recorded Academic Standards gaps do not necessarily block the entire phase. By
default, LC generation proceeds over the resolved portion of the graph and excludes
source SFIs whose ancestor path passes through an unresolved root-fallback edge. Such
gaps are recorded in the eligibility report and phase summary.

A reviewed configuration can set
`lc_manual_review_overrides.allow_unresolved_ancestor_context=true` to admit those seeds.
When that happens, the resulting request marks the path as unresolved and the generation
policy narrows accordingly: the seed text becomes the sole authority for curriculum
scope.

### 2. Select eligible source standards

Selection is deterministic.

If `lc_source_statement_types` is configured, only SFIs with those exact source-facing
statement types are eligible. If it is omitted, the default is to select leaf SFIs whose
normalized statement type is `Standard`.

An SFI can be excluded because it is outside the allowlist, is a non-leaf/grouping node,
has empty source text, or has an unresolved ancestor path that has not been explicitly
admitted. Every exclusion receives one deterministic reason in
`lc_eligibility_report.json`.

A non-empty finalized standards graph that produces zero eligible source SFIs fails
rather than silently generating an empty LC layer.

### 3. Build deterministic, hierarchy-aware generation requests

Eligible seeds are preserved in selection order and chunked according to
`lc_request_batch_size`. The default batch size is `1`, which scopes retries and resume
to one source SFI, but larger batches are supported.

Each request contains framework context plus, for every seed:

- the authoritative SFI description;
- source language and source-facing statement type;
- every direct `hasChild` parent UUID;
- the resolved ancestor graph needed to disambiguate curriculum/grade scope; and
- optional sibling context when `lc_include_sibling_context=true`.

The hierarchy model is not assumed to be a single-parent tree. A seed or ancestor can
have several `hasChild` parents. Request construction walks every branch to the
framework root, carries the union of all ancestors, and preserves each context entry's
own `parent_uuids` so the original topology remains reconstructible. Co-equal ancestors
at the same hierarchy depth have no semantic ordering preference.

`ancestor_path_status` records whether the hierarchy context is fully resolved. When it
is `unresolved_ancestor_path`, the producer and validator must treat the seed text as
the sole scope authority and may not add grade, strand, topic, or unit scope that is not
present in that seed.

Sibling SFIs, when included, are disambiguation and overlap-avoidance context only. The
prompts explicitly prohibit deriving new skill content from siblings. Source statement
codes are intentionally omitted as decomposition input.

### 4. Produce and independently validate atomic-skill decompositions

Each bounded request goes through an LC **producer/checker** flow.

The producer decomposes every requested SFI into one or more atomic teachable skills.
Python then checks universal response constraints such as request/SFI coverage,
non-empty skills, and any configured skill-count or text-length bounds. Invalid model
output is retried through the structured agent validation mechanism.

The generic decomposition contract includes several important semantic rules:

- an already atomic SFI can correctly yield one skill;
- a skill must be independently teachable and large enough to support lesson, activity,
  or assessment intent rather than being a fragment;
- activities, resources, assessment prompts, teacher instructions, and unstated
  prerequisites are not Learning Components;
- split only on distinctions the seed actually states;
- when a seed names several actions over several objects/cases, split on one axis only,
  normally the actions, never on both axes at once;
- qualifiers stay attached to the skill they qualify;
- a split replaces the whole seed, so the model must not also emit a summary skill that
  restates the complete standard; and
- generated skills stay in the source SFI's language unless runtime curriculum policy
  explicitly says otherwise.

After the producer succeeds, an independent LC generation validator receives the
original bounded request and the complete producer draft. The validator is instructed
to re-decompose each seed independently, then audit the draft for over-splitting,
under-splitting, unsupported scope/content, bad split-axis choices, fragments,
sibling leakage, language/register problems, and curriculum-specific violations.

If the draft is semantically correct, the validator accepts it. If a material correction
is required, the validator must return a complete corrected `LCGenerationResponse`, not
a patch. Python verifies that the selected final response still covers exactly the
requested SFIs and satisfies the configured skill bounds.

The generation phase persists all three semantic states:

```text
lc_generation_draft_responses.jsonl
lc_generation_validation_verdicts.jsonl
lc_generation_responses.jsonl
```

The final responses artifact therefore contains either accepted producer output or the
validator's complete corrected response.

Generation is sequential and resumable. A saved request is reusable only when its
draft, validator verdict, and final response remain mutually consistent with the
current deterministic request and still pass quality checks. Stale or inconsistent
saved state is not trusted.

Failed requests are isolated in `lc_generation_failures.json`; processing continues and
the run raises only after processing if the fraction of affected source SFIs exceeds
`lc_max_failure_rate`. Re-running without overwrite retries failed requests while
reusing valid completed request triples.

LLM usage for the producer and validator is tracked separately in `kg_run.json` under
`lc_generation` and `lc_generation_validation`.

### 5. Reconcile exact and semantic duplicate skills

Deduplication deliberately separates **identity normalization** from **candidate
nomination**.

For identity/grouping, generated skill text is normalized by lowercasing, collapsing
whitespace, and stripping a trailing period. This normalization is intentionally
language-independent and never applies stemming or language-specific morphology.

Exact normalized duplicates collapse within the configured `lc_dedup_scope` even when
`lc_semantic_dedup=false`.

When semantic deduplication is enabled, Python nominates plausible pairs within each
scope using deterministic rules. The language-independent core can use:

- token identity/Jaccard similarity;
- token containment overlap;
- character-trigram Jaccard similarity;
- generated-tag Jaccard similarity;
- corpus-frequency stopword suppression; and
- all-pairs review inside sufficiently small shared-direct-parent neighborhoods.

An optional `lc_dedup_language_pack` adds profile-defined stopwords and affix folding to
**candidate nomination only**. Those transformations never alter canonical LC identity.

The configured dedup scopes are:

- `framework` — one merge scope across the document;
- `top_ancestor` — merge only when seeds resolve to the same complete set of root-level
  SFI ancestors;
- `parent` — merge only when seeds have the same complete set of direct parent UUIDs;
- `none` — each source SFI receives its own scope, disabling cross-SFI merging.

For `top_ancestor` and `parent`, a seed with an empty or unresolved ancestor path falls
back to its own UUID as the scope key. Unreliable hierarchy therefore cannot enable a
cross-seed merge. Multi-parent seeds key on the complete relevant parent/root set rather
than allowing one branch to decide the scope.

Nominated pairs are batched by `lc_dedup_batch_size` and sent to a bounded semantic
judge. `lc_dedup_instructions` can append curriculum-specific equivalence rules. This
dedup adjudication is a single semantic judge plus deterministic output checking/retry;
it is not the independent producer/checker flow used for LC generation.

Accepted SAME links are clustered deterministically. Explicit DISTINCT verdicts act as
a chaining guard: if a transitive merge would place a judge-declared distinct pair in
the same cluster, that merge link is dropped and recorded as a conflict.

Canonical normalized text is elected deterministically: highest claim count wins, then
shorter text, then lexicographic order.

### 6. Mint Learning Components and `supports` relationships

Python mints one `LearningComponent` per canonical skill within its dedup scope. The
identifier is a deterministic UUIDv5 derived from a content-addressed identity key that
includes the source document key, scope key, and a hash of the canonical normalized
skill text.

The displayed `description` is chosen deterministically from the original surface forms
that correspond to the canonical text; identity remains based on normalized canonical
content.

Each LC retains per-claim provenance including the claiming SFI, generation request,
normalized skill text, confidence, ancestor UUIDs, statement type, and tags. LC metadata
also aggregates source/framework provenance and fingerprints the Academic Standards
bundle from which the LC layer was derived.

Attribution fields inherited from claiming SFIs must agree. A mismatch is treated as an
input inconsistency and fails rather than selecting one attribution arbitrarily.

A semantically shared skill can therefore become one Learning Component with several
source claims. Python emits one deterministic primary `supports` relationship for every
unique LC/claiming-SFI pair:

```text
LearningComponent ──supports──> StandardsFrameworkItem
```

If the same SFI reaches one canonical LC through several merged wordings, the edge's
`support_confidence` is the **minimum** of that SFI's contributing claim confidences so
the relationship does not overstate support.

### 7. Validate and export the LC layer and combined graph

Before the merged export, Python validates LC-level invariants including:

- deterministic LC identifier recomputation;
- unique LC and relationship identifiers;
- real `supports` source and target endpoints;
- one primary `supports` edge per unique LC/SFI pair;
- at least one primary `supports` edge for every LC; and
- exact eligibility reconciliation: every eligible SFI must be either claimed by at
  least one LC **or** recorded as failed, never both and never neither.

The final merge then combines the Academic Standards bundle, Learning Components,
`hasChild`, `supports`, unresolved records, summaries, and entity provenance. The merged
graph is validated again for AS gate status, endpoint existence, LC edge coverage,
claiming-SFI/support-count alignment, identifier collisions, LC provenance presence,
and summary-count consistency.

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
├── lc_generation_draft_responses.jsonl
├── lc_generation_validation_verdicts.jsonl
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

These artifacts preserve eligibility decisions, producer drafts, independent validation
verdicts, accepted/corrected generation results, deduplication judgments, provenance,
failures, and the final graph so the LC phase can be audited without treating the final
bundle as a black box.

For generation debugging, inspect the request, producer draft, validation verdict, and
final response together. For identity issues, inspect the dedup candidate pairs,
verdicts, groups, LC metadata, and `supports` edges rather than inferring a merge from
the final description alone.

---

For downstream consumption and the distinction between bundle, Learning Commons
wire, and flat-loader projections, see the
[output artifacts and integration contract](../reference/output-artifacts.md).

## Stage boundary

Learning Components construction is currently the final stage of the production
pipeline. It adds a generated atomic-skill layer to the source-grounded Academic
Standards hierarchy while preserving explicit provenance back to the standards and,
through them, to the original document evidence.

The `as_lc_kg_bundle.json` contains the Academic Standards framework and items,
Learning Components, `hasChild` relationships, `supports` relationships, unresolved
records, provenance, summaries, and validation results for the combined graph.

---

## Next

[Pipeline Overview ←](index.md)
