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

## Next unresolved decision

D4 — Candidate-pair orientation and adjudication shape.
