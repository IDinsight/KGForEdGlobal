# Learning Progressions Decision Ledger

This note records decisions resolved in the Learning Progressions explainer discussion before the Step 0 governance edit.

It is not the canonical engineering specification. The engineering brief remains a decision draft until a governance-edit task records all D1-D14 decisions consistently as `SETTLED` and the user approves the updated brief for implementation.

## D1 — Progression grain and statement-type pair policy

**Resolved option:** Option B — relation-specific statement-type pair matrices.

### Global interpretation

1. Each curriculum has separate closed-world matrices for `buildsTowards` and `relatesTo`.
2. A type pair is permitted only when explicitly listed for that relationship. Every omitted pair is excluded.
3. All current `Standard Grouping` types are excluded from both relationships.
4. All cross-type pairs are excluded initially, including cross-grain pairs between two normalized `Standard` types.
5. Any future statement type or type pair is excluded until it is deliberately added to the applicable curriculum matrix.
6. An allowed pair only makes two distinct StandardsFrameworkItem nodes eligible for candidate consideration. It does not publish or semantically approve an edge.
7. `buildsTowards` pairs below are directional source type to target type. Because every initial pair is same-type, actual earlier/later endpoint orientation follows D2 and D4.
8. `relatesTo` pairs below are semantically unordered. D5 will separately decide their serialized representation.

### Madhi mathematics

`buildsTowards`:

- `Content` -> `Content`

`relatesTo`:

- `{Content, Content}`

Excluded types:

- `Curricular Goal`
- `Competency`
- `Class`

### Nigeria mathematics

`buildsTowards`:

- `Performance Objective` -> `Performance Objective`

`relatesTo`:

- `{Performance Objective, Performance Objective}`

Excluded types:

- `Grade`
- `Theme`
- `Sub-Theme`
- `Topic`

### Pratham science

`buildsTowards`:

- `NCERT Learning Outcome` -> `NCERT Learning Outcome`
- `Content Domain Specific Learning Outcome` -> `Content Domain Specific Learning Outcome`
- `Indicator` -> `Indicator`

`relatesTo`:

- `{NCERT Learning Outcome, NCERT Learning Outcome}`
- `{Content Domain Specific Learning Outcome, Content Domain Specific Learning Outcome}`
- `{Indicator, Indicator}`

Excluded types:

- `Class`
- `Content Domain`
- `Chapter`

### Rwanda mathematics

`buildsTowards`:

- `Grade Key Competence` -> `Grade Key Competence`
- `Key Unit Competence` -> `Key Unit Competence`
- `Knowledge Objective` -> `Knowledge Objective`
- `Skills Objective` -> `Skills Objective`
- `Attitudes and Values Objective` -> `Attitudes and Values Objective`

`relatesTo`:

- `{Grade Key Competence, Grade Key Competence}`
- `{Key Unit Competence, Key Unit Competence}`
- `{Knowledge Objective, Knowledge Objective}`
- `{Skills Objective, Skills Objective}`
- `{Attitudes and Values Objective, Attitudes and Values Objective}`

Excluded types:

- `Grade`
- `Topic Area`
- `Sub-Topic Area`
- `Unit`

### Ghana mathematics

`buildsTowards`:

- `Content Standard` -> `Content Standard`
- `Indicator` -> `Indicator`

`relatesTo`:

- `{Content Standard, Content Standard}`
- `{Indicator, Indicator}`

Excluded types:

- `Grade`
- `Strand`
- `Sub-Strand`

### Ghana English

`buildsTowards`:

- `Content Standard` -> `Content Standard`
- `Indicator` -> `Indicator`

`relatesTo`:

- `{Content Standard, Content Standard}`
- `{Indicator, Indicator}`

Excluded types:

- `Grade`
- `Strand`
- `Sub-Strand`

### D1 accepted limitation

The configured matrices are semantic recall boundaries. Omitted grains and type pairs are not considered unless the policy is deliberately revised.

## D2 — Local developmental-order model

**Resolved option:** Option A — one explicit primary ordered dimension.

### Coordinate resolution policy for all six curricula

1. Resolve the configured coordinate from the participating SFI's canonical identity-scope value.
2. If an SFI is itself of the configured coordinate statement type, its own canonical value may be used.
3. Canonicalize recognized aliases through the curriculum's existing AS statement-type and controlled-value policy.
4. Do not infer order from lexical sorting, source order, hierarchy proximity, Learning Commons `grade_level` values, or an LLM.
5. If the coordinate is absent, exclude the SFI from `buildsTowards` but retain it for `relatesTo` when it is otherwise eligible. Record the missing-coordinate state and do not treat rank as positive evidence.
6. An unrecognized, ambiguous, or conflicting coordinate is a hard validation error and cannot publish any LP relationship.
7. D10 unresolved-SFI exclusions take precedence over this missing-coordinate policy.

### Relationship-level policy for all six curricula

1. Same-level `buildsTowards` is allowed.
2. For different resolved ranks, `buildsTowards` is allowed only from the lower configured rank to the higher configured rank.
3. There is no maximum forward-rank gap.
4. `relatesTo` is allowed at the same rank and across any configured rank gap.
5. `relatesTo` remains allowed when one or both otherwise-eligible endpoints have a missing coordinate.

### Curriculum coordinate profiles

#### Madhi mathematics

- Coordinate statement type: `Class`
- Canonical source: scope-only `Class` value in `Content` identity scope
- Ordered values: `Class-1 < Class-2 < Class-3 < Class-4 < Class-5`

#### Nigeria mathematics

- Coordinate statement type: `Grade`
- Canonical source: `Grade` identity scope
- Ordered values: `PRIMARY ONE < PRIMARY TWO < PRIMARY THREE`

#### Pratham science

- Coordinate statement type: `Class`
- Canonical source: `Class` identity scope
- Ordered values: `Class IX < Class X`

#### Rwanda mathematics

- Coordinate statement type: `Grade`
- Canonical source: `Grade` identity scope
- Ordered values: `P1 < P2 < P3`

#### Ghana mathematics

- Coordinate statement type: `Grade`
- Canonical source: `Grade` identity scope
- Ordered values: `BASIC 4 < BASIC 5 < BASIC 6`

#### Ghana English

- Coordinate statement type: `Grade`
- Canonical source: `Grade` identity scope
- Ordered values: `BASIC 1 < BASIC 2 < BASIC 3`

### D2 accepted limitation

A future curriculum with genuine multi-axis progression will require a schema extension rather than heuristic tuple inference.

## D3 — Candidate nomination technology

**Resolved option:** Option A — explainable deterministic multi-signal retrieval without embeddings in v1.

### Approved interpretation

1. Candidate nomination is the deterministic, bounded union of nominations from named, explainable signal strategies.
2. Each nominated pair records the signal or signals that nominated it and the concrete values that triggered each nomination.
3. D1 statement-type pair policy and D2 developmental-order policy remain hard admissibility boundaries. Candidate signals cannot bypass them.
4. Hierarchy context, local-rank proximity, Learning Component overlap, text similarity, code proximity, source order, or any other individual signal may nominate a pair but cannot publish a relationship.
5. Generic Python owns deterministic signal execution, union, deduplication, stable ranking and tie-breaking, budget enforcement, identities, and fingerprints. Curriculum-specific signal selection and policy remain runtime configuration concerns.
6. No embedding model, provider, vector index, cache, dependency, cost boundary, or fallback behavior is approved for v1.
7. The v1 implementation must provide a small named-signal strategy boundary, or an equivalently clear extension point, so a future approved embedding model can be added as another nomination signal without redesigning candidate-pair records, producer/checker judgment records, or final relationship schemas.
8. A future embedding add-on remains a separate governed change. It must supply the model/provider/version, multilingual scope, dependency/runtime boundary, vector/cache persistence and invalidation rules, batching/cost controls, fingerprint inputs, and unavailable-capability behavior before it may be implemented.
9. Candidate algorithm/version, enabled signals, signal-policy configuration, budgets, and other material retrieval inputs must be fingerprinted. Any future embedding signal would add its own material fingerprint inputs.

### D3 accepted limitation

Deterministic non-embedding retrieval creates a candidate-recall ceiling and may miss semantically related standards with weak lexical, hierarchy, code, or Learning Component overlap. D12 must measure candidate recall using independently selected positive pairs rather than evaluating only pairs already nominated by the pipeline.

## D4 — Candidate-pair orientation and adjudication shape

**Resolved option:** Option A — one canonical unordered logical pair with one unified relation/direction judgment.

### Approved interpretation

1. Each logical pair of distinct eligible SFI endpoints is represented once for candidate identity, request coverage, adjudication, failure accounting, and pair-level provenance.
2. Pair identity is invariant to the order in which the two endpoints were encountered. Any deterministic canonical endpoint ordering used in the candidate record is technical and does not assert semantic direction.
3. Before prompting, deterministic Python derives the pair's admissible judgments from D1 statement-type matrices, D2 developmental-order policy, and all other settled structural rules. Disallowed relationship types and `buildsTowards` directions are not accepted.
4. The unified judgment considers every admissible interpretation together: either permitted `buildsTowards` direction, `relatesTo`, `no_relation`, or `needs_review`. The final same-pair relationship cardinality remains governed by D6 and is not settled by D4.
5. A pair appears exactly once in the deterministic request set. The producer returns one complete pair-level judgment, and the checker receives the same bounded evidence plus the producer draft and either accepts it or returns one complete corrected judgment.
6. Producer/checker verification does not make an outcome authoritative by itself. Python still verifies exact pair identity and coverage, endpoint containment, allowed relation/direction, schema integrity, and request/response identity before finalization.
7. The LLM cannot introduce a different endpoint or create an additional unrequested pair.
8. D5 will separately determine how an accepted conceptually symmetric `relatesTo` judgment is serialized. Canonical candidate-pair ordering must not be mistaken for a semantic `relatesTo` direction.
9. D6 will separately determine whether the unified pair judgment may publish both relationship types. D7-D9 will separately govern recurring-practice mapping, cycles, and transitive edges.
10. The unified pair design is the stable pair/judgment contract for all D3 nomination strategies, including the extension point reserved for a separately approved future embedding signal.

### Rationale

One pair-level judgment lets the producer and checker compare directional progression, non-directional conceptual coherence, a confident negative, and genuine ambiguity from the same evidence. It avoids duplicate directional adjudication, duplicate evaluation of symmetric `relatesTo`, and late reconciliation of independently produced contradictory judgments.

## D5 — `relatesTo` serialization

**Resolved option:** Option A — store one canonical `relatesTo` relationship per unordered pair.

### Approved interpretation

1. `relatesTo` remains conceptually symmetric and non-directional: it connects two SFIs through substantive conceptual or skill coherence without asserting sequence or dependency.
2. One accepted `relatesTo` judgment for an unordered D4 pair produces exactly one final relationship row, not reciprocal rows.
3. Python canonicalizes the final endpoints by their `case_identifier_uuid` values: the lower canonical UUID is stored as source and the higher canonical UUID as target.
4. The stored source/target ordering is a technical identity and serialization convention only. It does not express developmental order, hierarchy order, source-document order, or semantic direction.
5. The deterministic relationship UUID uses the document key, `relatesTo` relationship type, and canonicalized endpoints. The LLM does not choose endpoint ordering or relationship identity.
6. The relationship appears exactly once in the standalone `relatesTo` relationship artifact, combined bundle, combined relationship JSONL, summary counts, and relationship provenance.
7. Consumers must treat `relatesTo` as an undirected-neighbor relation: lookup for one SFI checks both endpoint positions and returns the opposite endpoint, or uses an API/graph abstraction that provides equivalent symmetric traversal.
8. Validation rejects non-canonical endpoint ordering, reverse duplicates, multiple identifiers for the same unordered pair, missing provenance, and count disagreement.
9. D5 does not alter D1 pair eligibility, D2 rank policy, D3 nomination, or D4 one-pair adjudication.

### Learning Commons alignment note

Learning Commons documentation describes `relatesTo` as non-sequential and depicts it with a bidirectional semantic arrow, while its schema example uses one ordinary source/target relationship row. The documentation does not prescribe reciprocal physical rows or min/max UUID canonicalization. This decision is therefore compatible with the documented ontology, but the canonical endpoint rule is a project-specific deterministic convention rather than a claimed Learning Commons mandate.

### D5 accepted limitation

Raw relationship consumers that query only outgoing or only incoming edges will miss some `relatesTo` neighbors. Combined-graph documentation, ingestion checks, and examples must explicitly require symmetric lookup across both endpoint positions.

## D6 — Same-pair relationship-type publication policy

**Resolved option:** Option A — relationship types are mutually exclusive for one logical pair, with `buildsTowards` precedence.

### Approved interpretation

1. One D4 logical pair may publish at most one final LP relationship: one permitted `buildsTowards` direction, one canonical D5 `relatesTo`, or no relationship.
2. When the reviewed pair evidence supports a permitted directional developmental relationship, the judgment publishes `buildsTowards` and does not also publish `relatesTo` for that unordered pair.
3. `relatesTo` is the published alternative when the evidence supports substantive conceptual or skill coherence but does not justify a permitted developmental direction.
4. `buildsTowards` precedence is a reconciliation rule between semantically supported relationship interpretations. It does not allow local rank, hierarchy, Learning Component overlap, text similarity, code proximity, source order, or any other nomination signal to create an edge automatically.
5. `no_relation` remains the confident negative outcome, and `needs_review` remains the materially ambiguous or contradictory outcome. Neither publishes an edge.
6. The D4 producer returns one complete single-valued pair judgment, and the checker evaluates the competing permitted interpretations together before accepting it or returning one complete corrected judgment.
7. Python enforces relation exclusivity, D1 type-pair permission, D2 direction/rank permission, D5 canonical `relatesTo` serialization, endpoint containment, and exact pair/request identity.
8. Consumers seeking a broad conceptual neighborhood must query the union of `buildsTowards` and `relatesTo` when they want progression pairs included; the graph does not redundantly add `relatesTo` to every progression pair.
9. D7 will determine how developmental extension, meaningful recurrence, and generic repetition map into the mutually exclusive relationship outcomes.

### Rationale

`buildsTowards` is the more specific assertion when directional developmental support is justified. Publishing an additional `relatesTo` row would usually repeat the broader fact that the pair is meaningfully connected, increase graph density and provenance counts, and weaken relation-choice evaluation without adding a distinct instructional claim.

## D7 — Recurring-practice and repeated-skill mapping

**Resolved option:** Option C — classify recurrence by substantive change.

### Approved interpretation

1. A recurring or repeated capability does not receive one universal relationship solely because it appears more than once or at a later local rank.
2. When the later standard substantively deepens, extends, combines, broadens, or increases the complexity of the earlier capability, publish the permitted earlier-to-later `buildsTowards` relationship.
3. When the pair represents meaningful recurrence or reinforcement without a clearly justified developmental dependency, publish one canonical D5 `relatesTo` relationship.
4. When the apparent recurrence is only generic wording, broad reusable Learning Component overlap, or another non-pair-specific similarity without useful instructional coherence, return `no_relation` and publish no edge.
5. When the bounded evidence is materially ambiguous or contradictory and does not support a confident classification, return `needs_review` and publish no edge.
6. Text equality or similarity, shared Learning Components, later rank, source order, code proximity, or hierarchy proximity may nominate and contextualize a pair but cannot determine its recurrence category or publish an edge automatically.
7. The D4 producer classifies the pair from bounded evidence, and the checker independently verifies both the recurrence interpretation and the resulting mutually exclusive D6 relationship outcome before Python accepts it.
8. Generic Python enforces schema integrity, D1 type-pair permission, D2 direction/rank policy, D5 serialization, D6 exclusivity, endpoint containment, and exact request coverage. It does not hard-code curriculum-specific examples of substantive change.
9. Curriculum-specific producer/checker instructions must supply reviewed examples and counterexamples for developmental extension, meaningful recurrence, generic repetition, and ambiguity where those distinctions are material.
10. `recurring_practice` may be retained as an internal evidence or rationale category, but it does not become a third published relationship type.
11. `no_relation` is a normal adjudication outcome counted in the LP summary. `needs_review` is unresolved and is routed to LP unresolved artifacts.

### Rationale

The three-way policy preserves the distinct meanings of `buildsTowards` and `relatesTo` while preventing generic repetition from becoming graph density. It supports genuine developmental extension in spiral curricula, meaningful reinforcement without overstated dependency, and explicit rejection of noisy recurring language or Learning Component reuse.

## Next unresolved decision

D8 — `buildsTowards` cycle policy.
