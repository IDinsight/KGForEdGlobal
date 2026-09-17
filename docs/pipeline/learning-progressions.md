# Learning Progressions

Learning Progressions (LP) adds developmental and conceptual relationships between
Standards Framework Items (SFIs) in one validated curriculum framework. It is the third
KG phase in `create_kgs.py`, after Academic Standards (AS) and Learning Components (LC).
It consumes the validated `as_lc_kg_bundle.json`; it does not extract the PDF again or
change the upstream nodes, hierarchy, skills, or `supports` edges.

This page describes implemented behavior. It does not certify completed six-curriculum
execution or final evaluator approval. See [evaluation evidence status](../guides/evaluating-learning-progressions.md#execution-evidence-and-project-completion).

## Running and configuration

From the configured backend environment:

```bash
python src/kgfeg/entries/create_kgs.py --help
# This executes the KG pipeline and may make live model calls:
python src/kgfeg/entries/create_kgs.py <config.json>
```

`kgs` is optional in the global configuration. When supplied, it requires `as`, `lc`,
`lp`, and `metadata`; there is no LP-disable switch. Production AS, LC, and LP use
`LLM_KG_MODEL`. The separate evaluator has its own model setting.

The following fields live under `kgs.lp`. Except for concurrency, these values must
be supplied explicitly; examples are curriculum policy, not universal defaults.

| Field | Meaning |
| --- | --- |
| `builds_towards.allowed_statement_type_pairs` | Directional type permissions, each with `source_statement_type` and `target_statement_type` |
| `relates_to.allowed_statement_type_pairs` | Unordered type permissions, each with `first_statement_type` and `second_statement_type` |
| `developmental_coordinate.statement_type`, `ordered_values` | One local progression axis and its exact canonical order |
| `candidate_policy.budgets.max_candidates_per_sfi`, `max_total_candidates` | Positive bounds applied before model calls |
| `producer_instructions`, `checker_instructions` | Curriculum-specific interpretation and validation guidance |
| `evidence_limits` | Positive bounds on ancestor path depth/count, supporting LC count, and source-evidence item/character counts |
| `request_batch_size` | Positive number of candidate pairs per request; independent of concurrency |
| `max_concurrent_requests` | Optional positive integer, default **4** admitted request batches per run; **1** is serial |
| `retry.producer_max_retries`, `checker_max_retries` | Nonnegative retry counts after each stage's initial attempt, per production invocation |
| `unresolved_participation` | Required `exclude_unresolved` or `include_unresolved_with_warnings` |
| `relationship_metadata` | Explicit `author`, `provider`, `approved_by`, and `attribution_statement_template` |

The exact evidence-limit keys are `max_ancestor_path_depth`,
`max_ancestor_paths_per_sfi`, `max_learning_components_per_sfi`,
`max_source_evidence_characters_per_sfi`, and `max_source_evidence_items_per_sfi`.
Unknown fields and attempts to configure universal candidate algorithms, rank rules,
checkpoint formats, failure tolerances, or fingerprint selectors are rejected.
Type permissions and local order are cross-validated against the AS policy.

## Relationship meaning and consumer queries

LP creates no new node type. Both endpoints are `StandardsFrameworkItem` nodes keyed
by `case_identifier_uuid`; Learning Components provide evidence, never LP endpoints.

| Relationship | Meaning | Consumer behavior |
| --- | --- | --- |
| `buildsTowards` | Proficiency in the source supports the likelihood of success in the target | Follow the stored direction; this is not a mandatory prerequisite or compulsory teaching order |
| `relatesTo` | Substantive conceptual or skill coherence without asserted dependency | Query both endpoint positions; storage order has no semantic direction |

One logical pair publishes at most one LP edge. A supported developmental relationship
takes precedence over a nondirectional association. Meaningful recurrence can produce
`relatesTo`; substantive extension can produce `buildsTowards`; generic repetition or
word overlap alone does not justify either. `hasChild` means organization or
decomposition, not progression.

`relatesTo` is stored once, with the lower canonical CASE UUID as source. An undirected
lookup against an internal combined bundle can therefore be written as:

```python
neighbors = set()
for edge in bundle["relationships_relates_to"]:
    source = edge["source_entity_value"]
    target = edge["target_entity_value"]
    if source == sfi_uuid:
        neighbors.add(target)
    elif target == sfi_uuid:
        neighbors.add(source)
```

`bundle` is the parsed JSON bundle and `sfi_uuid` is a CASE UUID string. Querying only
outgoing rows loses neighbors. Do not insert reciprocal rows into the released data.

The complete `buildsTowards` graph must be acyclic, including same-rank edges and cycles
spanning requests. Cycle diagnostics fail validation; the pipeline does not silently
drop or reverse edges. Only directly adjudicated edges are published: `A → B → C`
does not create `A → C`, and an independently accepted `A → C` is retained. Applications
may calculate reachability or a minimal display separately from the released rows.

## Eligibility, order, and unresolved context

LP eligibility is separate from LC eligibility, normalized `Standard` type, and leafness.
Only explicitly allowed statement-type pairs participate. Canonical identity scope
supplies the configured local coordinate, including scope-only grades/classes with no
corresponding graph node. Labels are never lexically sorted to infer development.

- A missing coordinate prevents `buildsTowards` participation but can still permit
  `relatesTo`.
- An invalid, ambiguous, or conflicting coordinate is a validation error.
- Same-rank `buildsTowards` is permitted. Across ranks, only lower-to-higher directions
  are permitted, with no maximum forward gap.
- `relatesTo` can cross any rank gap or involve missing coordinates.

Hierarchy indexing preserves all direct parents and relevant bounded paths in a DAG.
Under `exclude_unresolved`, an SFI with unresolved self or ancestry is excluded from
both relations. Under `include_unresolved_with_warnings`, otherwise eligible SFIs remain
eligible, with warnings carried through requests, judgments, provenance, and reports.
The six example profiles select inclusion with warnings. Framework-root fallback never
counts as positive hierarchy or topical evidence. Trustworthy text, scope, LCs, source
evidence, and non-fallback paths can still be used. There are no per-SFI exceptions.

## From candidates to validated graph

1. Validate upstream AS+LC material, index it, and record eligibility and exclusions.
2. Use deterministic, non-embedding nomination with explicit per-SFI and total budgets.
   Record nomination facts; no single similarity, LC overlap, hierarchy, or rank signal
   publishes an edge.
3. Materialize and validate the complete candidate and bounded request populations,
   including IDs, order, coverage, and content hashes, before any LP model call.
4. Generate one structured judgment per requested pair. A separate checker sees the
   same bounded evidence plus the draft, then accepts it or supplies a complete correction.
5. Validate exact coverage, endpoints, allowed decisions/directions, and material identity.
   Reconcile claims, mint deterministic UUIDv5 relationships, and validate the whole graph.
6. Write standalone LP evidence and the additive AS+LC+LP bundle and projections.

| Outcome | Published edge | Operational effect |
| --- | --- | --- |
| Accepted `buildsTowards` or `relatesTo` | One, subject to final validation | Counted and linked to candidate/request/judgment provenance |
| `no_relation` | None | Valid negative judgment, counted in the summary |
| `needs_review` | None | Valid ambiguity, retained in `lp_unresolved_items.json`; nonblocking |
| Processing failure | None | Any unresolved failed pair blocks LP/combined success after permitted retries |

Confidence is audit data, not an acceptance threshold. Invalid or missing responses
are failures, not ambiguous judgments. Production success checks structure and process;
it does not read evaluator scores or require a human gold set.

## Concurrency, checkpoints, and failure

Capacity counts admitted requests, including retry waits, with at most one active
producer/checker call per request. A batch occupies one slot regardless of pair count.
It is a per-run bound, not a provider-wide quota. The checker waits for its own validated
producer draft. Scheduling does not change candidates, prompts, or relation semantics.

One writer owns durable checkpoints and attempt/usage accounting. Successful draft,
verdict, and final-response JSONL files remain contiguous deterministic prefixes.
Validated out-of-order completions survive in a separate pending journal and are promoted
when the gap closes. With unchanged material and `kgs.overwrite=false`, resume starts
at the earliest unfinished stage and reuses durable valid work, including a saved draft
whose checker has not finished. Attempt and failure history is retained, but each new
production invocation gives unfinished stages a new configured retry allowance. A
restart can therefore make new calls after earlier exhaustion or an unknown outcome;
inspect that history before authorizing a resume. Durable successful stages are reused.
This differs from the [evaluator's resume restrictions](../guides/evaluating-learning-progressions.md#model-and-bounded-execution).

After the first observed exhausted failure, no new producer, checker, or retry API call
starts. Already active calls may finish; their valid results and observed usage are saved.
Local reconciliation can finish, but the failed run cannot become successful while a
failed pair remains unresolved. Unknown remote outcomes are recorded as unknown.

Resume and final-bundle reuse reject stale upstream/configuration, prompts, model
settings, candidate/request populations, or checkpoint material. Gaps, duplicates,
truncation, and misaligned stage dependencies also fail closed. Exact final-bundle reuse
can rewrite the two projections from the validated bundle without repeating LP calls.

The `.lp_generation.lock` file is retained after success. Its presence alone does not
mean a writer is active; ownership is an operating-system lock. Do not delete it to
force reuse: retaining the same inode protects competing readers/writers.

### Historical graphs versus production reuse

Completed historical graphs remain available for downstream reading and compatible
read-only validation. This does not authorize the current production command to resume
or reuse their unsupported checkpoints, even when a final bundle reports success.

Current production requires authenticated successful prefixes, failure evidence,
`lp_generation_pending_completions.json`, `lp_generation_usage.json`, and their checkpoint
manifest. Empty journals must exist explicitly. Prefix-only stores, incomplete evidence,
and formats containing `legacy_stage_counts` or `legacy_failure_count` are rejected,
including zero-valued counters. `overwrite=true` cannot bypass this check: rejection
precedes calls, archiving, recovery, projection writes, and run-record replacement.
Complete current-format interrupted transactions remain recoverable; the transaction
file may be absent after a committed update. Never retrofit journals or edit historical
hashes to make old evidence appear current.

## Outputs and provenance

The additive delivery is `as_lc_lp_kg_bundle.json`, `as_lc_lp_nodes.jsonl`, and
`as_lc_lp_relationships.jsonl`. Node membership equals AS+LC; relationships contain
`hasChild`, `supports`, `buildsTowards`, and `relatesTo`. Existing AS and AS+LC outputs
retain their contracts. The LP combined JSONL uses internal snake_case records, unlike
the AS/AS+LC delivery wire format. See the [artifact reference](../reference/output-artifacts.md#learning-progressions-artifacts).

LP edges identify `LLM generated` as author and `IDinsight` as provider and approving
organization. They inherit the source-framework license and disclose that the inferred
relationship was not stated or endorsed by its publisher. Provenance retains source
framework metadata, endpoints, candidate/evidence references, producer/checker lineage,
and actual upstream/config/request/prompt/model content identities.

## Accepted limitations

- Edges remain within one framework. There are no LC-to-LC progression edges or named
  pathway entities, and no claim of empirical prerequisite truth.
- Closed-world type matrices and bounded non-embedding nomination omit possible
  relationships. Sampled judge-positive nomination coverage does not establish
  curriculum-wide recall.
- One ordered local axis cannot represent genuine multi-axis development. Acyclicity
  can represent spiral or mutually reinforcing learning less richly.
- Upstream extraction, hierarchy, scope, and LC errors can influence LP. The profile-wide
  unresolved policy cannot select only the strongest individual unresolved cases.
- The reviewed examples are English-language; multilingual progression judgment and
  retrieval have not been validated by those examples.
- Symmetric lookup is required for `relatesTo`; direct edges may coexist with alternate
  multi-hop paths. Neither storage convention supplies an instructional sequence.
- Structural/process validity, producer/checker agreement, and fallible judge assessments
  do not prove pedagogical correctness. Sampling, bounded evidence, and correlated model
  errors remain limitations even with favorable controls or stable replicates.
- There are no forced semantic edge overrides. Confirmed errors require an authorized
  repair at the earliest incorrect stage and affected reruns, potentially further model
  calls and iterations; never hand-edit final graphs.
- License inheritance records organizational policy, not legal clearance. IDinsight
  remains responsible for confirming its appropriateness for each source.

## Related documentation

- [Evaluate Learning Progressions](../guides/evaluating-learning-progressions.md)
- [Run, resume, and debug](../guides/running-and-debugging.md)
- [Add a curriculum](../guides/adding-a-curriculum.md)
- [Output artifacts](../reference/output-artifacts.md)
