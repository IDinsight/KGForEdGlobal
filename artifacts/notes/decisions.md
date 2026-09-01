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

## D8 — `buildsTowards` cycle policy

**Resolved option:** Option A — the published `buildsTowards` graph must be acyclic.

### Approved interpretation

1. Every finalized `buildsTowards` relationship participates in one directed graph, and that complete graph must be a DAG before LP or combined validation can pass.
2. Any directed cycle is a release-blocking validation failure. Diagnostic artifacts may remain inspectable, but `kg_run.json` and the combined validation state cannot report success.
3. D2 rank and direction rules remain independently mandatory. Cross-rank backward edges fail D2 even before cycle policy is considered; under the approved one-axis profiles, any otherwise admissible cycle would therefore be composed of same-rank edges.
4. D4 and D6 permit only one relationship direction for one unordered pair, so a reciprocal two-node `buildsTowards` cycle cannot be published. Cycle detection must still handle cycles of three or more SFIs and any future graph shape consistent with the approved pair contract.
5. `relatesTo` relationships are conceptually symmetric and are excluded from directed `buildsTowards` cycle validation. Meaningful mutual or recurring coherence should follow D7 and use `relatesTo` when developmental direction is not justified.
6. Producer/checker pair verification does not prove global acyclicity. Deterministic Python owns whole-graph cycle detection after final pair reconciliation.
7. Cycle diagnostics must identify every cyclic strongly connected component and every final SFI and `buildsTowards` relationship participating in those components, with stable identifiers and links to pair/request/producer/checker provenance.
8. For easy auditability, the validation report must include deterministic representative cycle paths for each cyclic component and reconcile their counts with the reported component, node, and relationship sets. It is not required to enumerate every possible simple cycle when their number is combinatorial.
9. The pipeline must not silently delete an arbitrary edge, convert it to `relatesTo`, or hand-edit a final artifact to break a cycle. The earliest incorrect policy, prompt, judgment, finalization behavior, or approved semantic input must be corrected and the affected stages rerun.
10. Any future manual semantic decision remains subject to this cycle validator and cannot waive acyclicity without reopening D8.

### Rationale

An acyclic graph provides the clearest developmental traversal contract and prevents circular progression explanations. The D5/D7 `relatesTo` path already represents substantive mutual or recurring coherence without forcing it into a directed loop. Component-, edge-, and provenance-level reporting makes any violation directly auditable while avoiding unbounded enumeration of redundant cycle paths.

## D9 — Transitive edge policy

**Resolved option:** Option A — publish only directly adjudicated `buildsTowards` edges, with no automatic transitive closure or reduction.

### Approved interpretation

1. A `buildsTowards` relationship is eligible for publication only when its exact endpoint pair was deterministically nominated, assigned to one D4 request, directly adjudicated by the producer/checker path, and accepted under D1-D8.
2. If `A -> B` and `B -> C` are published but the A/C pair was not independently accepted, Python does not create `A -> C` from reachability.
3. If `A -> C` was independently nominated and accepted, Python retains that direct relationship even when an alternate accepted path such as `A -> B -> C` already connects the endpoints.
4. Finalization performs neither transitive closure nor transitive reduction. Graph reachability is a consumer or graph-utility computation, not a relationship-publication rule.
5. Every published edge retains its own candidate ID, request ID, producer/checker outcome, evidence summary, config/input fingerprints, deterministic relationship identity, and source-framework provenance.
6. Multi-hop reachability does not count as a separate accepted judgment, published edge, or candidate-recall success. Candidate recall remains measured against the exact independently reviewed pair.
7. D8 acyclicity remains mandatory for the complete directly adjudicated graph. The presence of an alternate path neither excuses a cycle nor authorizes silent deletion of an edge.
8. `relatesTo` is unaffected and receives neither transitive closure nor transitive reduction.
9. Validation must reject any published `buildsTowards` edge without its own accepted pair-level provenance, any silently omitted accepted edge, and any count mismatch among accepted final claims, standalone relationships, combined bundle relationships, and projections.
10. Consumer documentation must distinguish a direct published assertion from multi-hop reachability and show how to compute upstream or downstream paths when needed.

### Learning Commons alignment note

Learning Commons documentation presents individual `buildsTowards` rows and demonstrates one-hop prerequisite/successor lookup while describing longer learning pathways as traversal. It does not prescribe transitive closure or reduction. This decision follows that direct-assertion model without claiming that Learning Commons formally mandates D9 Option A.

### D9 accepted limitation

The published DAG may contain both a direct edge and an alternate multi-hop path between the same endpoints. Consumers seeking a minimal path structure or complete upstream/downstream reachability must compute that view without changing the released evidence-level relationships.

## D10 — Unresolved Academic Standards ancestry policy

**Resolved option:** Hybrid of Options A and B — each `kgs.lp` profile must explicitly select either exclusion of all unresolved SFIs or inclusion of all otherwise-eligible unresolved SFIs with warnings. All six initial profiles select inclusion with warnings.

### Required runtime-policy semantics

1. `kgs.lp` contains one required, profile-level, two-state unresolved-participation policy. The final field name and literal representation will be set during the Step 0 governance edit and schema design; no illustrative field name is authoritative.
2. The two permitted semantic states are:
   - exclude every SFI with unresolved self or ancestry; or
   - include every otherwise-eligible SFI with unresolved self or ancestry while propagating explicit warnings.
3. The policy has no silent default. Missing or unknown values are configuration errors.
4. The policy is all-or-nothing within one curriculum run. D10 does not support per-SFI reviewed exceptions or a sidecar exception mechanism.

### Exclusion-state behavior

1. Every SFI with unresolved self or ancestry is excluded from both `buildsTowards` and `relatesTo`.
2. D10 exclusion takes precedence over D2 missing-coordinate behavior, including D2's ordinary allowance for an otherwise-eligible coordinate-missing SFI to participate in `relatesTo`.
3. Excluded SFIs produce no candidate pairs or LLM requests.
4. The eligibility report records the exclusion reason and exact counts. This is a normal policy exclusion, not `no_relation`, `needs_review`, or processing failure.

### Inclusion-with-warning behavior

1. Every unresolved SFI may continue only when it is otherwise eligible under D1, D2, and all other settled policies.
2. Inclusion never nominates a pair, approves a relationship, or bypasses producer/checker adjudication and deterministic validation.
3. Framework-root fallback is never positive hierarchy, topology, domain, or curricular-placement evidence.
4. Permitted evidence may include the SFI's own text, valid canonical scope and coordinate, source code and audit flags, supporting Learning Components, bounded source-document evidence, and trustworthy non-fallback hierarchy paths.
5. An unrecognized, ambiguous, or conflicting developmental coordinate remains a D2 hard validation error and cannot publish an LP relationship.
6. The producer and checker receive the same explicit unresolved warning and the same bounded evidence. Neither may reinterpret fallback-root attachment as genuine curriculum structure.
7. Unresolved status remains visible in eligibility, candidates, requests, producer/checker judgments, final claims, relationship provenance, summaries, and validation.

### Initial six-curriculum profile matrix

| Curriculum          | Initial unresolved-participation state                       |
|---------------------|--------------------------------------------------------------|
| Madhi mathematics   | Include all otherwise-eligible unresolved SFIs with warnings |
| Nigeria mathematics | Include all otherwise-eligible unresolved SFIs with warnings |
| Pratham science     | Include all otherwise-eligible unresolved SFIs with warnings |
| Rwanda mathematics  | Include all otherwise-eligible unresolved SFIs with warnings |
| Ghana mathematics   | Include all otherwise-eligible unresolved SFIs with warnings |
| Ghana English       | Include all otherwise-eligible unresolved SFIs with warnings |

### Fingerprint, reuse, and validation policy

1. The selected unresolved-participation state is a material LP configuration input and participates in every relevant policy/config fingerprint.
2. Changing the state invalidates stale eligibility, candidate, request, response, final-claim, relationship, and combined-bundle reuse and requires the affected stages to rerun.
3. Validation reconciles unresolved eligible/excluded counts, confirms warning and provenance propagation for every included unresolved SFI and edge, and rejects any use of fallback-root placement as positive evidence.

### D10 accepted limitation

The profile-level policy cannot distinguish a strong individual unresolved case from a weak one. Inclusion may admit unresolved SFIs whose non-fallback evidence is insufficient, while exclusion may omit usable SFIs. Producer/checker adjudication and semantic evaluation mitigate but do not remove this all-or-nothing policy limitation.

## D11 — Attribution and ownership metadata for inferred LP relationships

### Status

**Resolved — Option B with approved source-license inheritance.**

### Selected direction

The user selected **Option B — dedicated LP relationship metadata in `kgs.lp`** and directed that LP inference authorship/provider identity be consistent with existing Learning Components.

The exact selected values are:

- `author`: `LLM generated`
- `provider`: `IDinsight`

The spelling and spacing above deliberately match the existing LC constants.

### Existing LC and relationship state confirmed during the decision

1. Existing `hasChild` relationships inherit `author`, `provider`, `license`, and `attribution_statement` from framework metadata.
2. Existing Learning Component nodes and `supports` relationships use `author = "LLM generated"` and `provider = "IDinsight"`.
3. Existing Learning Component nodes and `supports` relationships inherit `license` and `attribution_statement` from the source framework.
4. The engineering brief's D11 introduction must describe this actual mixed `supports` behavior rather than saying that `supports` inherits all framework metadata.

### License policy

Each LP relationship copies the validated source-framework `license` value verbatim for the current curriculum. There is no fallback or generic default. A missing or blank upstream source license is a validation error.

This is an explicit source-license inheritance policy approved as part of D11, not unresolved “inherit later” language. It does not cause the relationship to inherit source authorship or imply that the source publisher authored the inferred relationship.

### Attribution-statement template

The exact approved template is:

```text
Learning progression relationship generated by IDinsight using an LLM producer/checker workflow. Source framework attribution: {source_attribution_statement}. This inferred relationship was not stated or endorsed by the source publisher.
```

The single `{source_attribution_statement}` token is replaced with the validated source framework's `attribution_statement` verbatim. No other runtime substitutions are permitted. A missing or blank source attribution statement is a validation error.

The period immediately following `{source_attribution_statement}` in the approved template is literal and must remain in the rendered template in addition to whatever punctuation the substituted source attribution text contains.

### Approval

`IDinsight` is the organizational approver for the selected author, provider, source-license inheritance policy, attribution template, substitution rule, and required provenance.

### Required internal provenance

Every published LP relationship retains:

1. source-framework UUID and title;
2. source-framework author, provider, license, and attribution statement;
3. source and target SFI `case_identifier_uuid` values;
4. candidate ID and evidence summary;
5. producer and checker request, judgment, and outcome references;
6. upstream AS+LC bundle fingerprint;
7. LP configuration/policy, candidate/evidence, and request fingerprints; and
8. producer/checker prompt and model-settings fingerprints.

### Runtime, finalization, and validation consequences

1. All six initial curriculum profiles carry the D11 policy explicitly in required `kgs.lp` configuration. Final configuration field names remain a Step 3 schema-design detail and are not established by illustrative snippets.
2. Generic Python does not hard-code `LLM generated`, `IDinsight`, or curriculum-specific source metadata.
3. Deterministic finalization, not the LLM producer or checker, attaches the approved metadata.
4. Validation requires exact author/provider strings, verbatim equality with the validated source-framework license, exact template substitution, and complete provenance.
5. Standalone LP relationship artifacts, the combined bundle, and the combined relationship projection must agree exactly.

### D11 accepted limitation

The engineering brief and this decision are not legal advice. IDinsight remains responsible for confirming that copying each source-framework license to inferred LP relationship records is legally and organizationally appropriate.

## D12 — Semantic evaluation and release gate

### Status

**Resolved — Option D, non-blocking post-release human audit.**

### Selected direction

The user withdrew the prior Option A direction and selected **Option D — non-blocking post-release human audit** for v1.

“Human audit” means review and recommended remediation. It does not permit hand-editing generated LP graphs. Any correction follows the existing earliest-stage repair-and-rerun rule.

### V1 release policy

1. Independent human semantic review is not required before acceptance or release.
2. Release depends on deterministic structural validation, successful producer/checker reconciliation, and the settled D13 failed-request policy.
3. `needs_review` judgments remain visible in audit and summary artifacts, do not themselves block release, and never become published relationships.
4. No independently reviewed gold set, minimum human-review sample, review cadence, semantic metric, or numeric semantic threshold is required for v1 release.
5. Structural correctness, producer/checker agreement, and release under D13 must not be described as proof of pedagogical correctness.

### Post-release human audit

1. Human semantic review may occur after acceptance or release.
2. Any audit that is performed records its reviewed population or sampling method, reviewer identity, review time, findings, rationale, affected relationship/candidate IDs, and the exact released artifact and policy fingerprints.
3. Findings inform remediation or a subsequent release. They do not retroactively make human review a prerequisite for the original release.
4. Confirmed defects are corrected at the earliest incorrect configuration, candidate, prompt, judgment, finalization, or validation stage and the pipeline is rerun. Generated final graph files are never hand-edited.
5. `IDinsight` is the organizational authority for post-release audit findings and remediation/re-release decisions.

### Governance and build-order consequences

1. D12 is no longer a pre-release semantic evaluation gate.
2. Existing Step 24–26 gold-set, threshold-enforcement, targeted semantic-evaluation, and evidence-gated tuning requirements must be revised or explicitly deferred as non-blocking future/post-release work.
3. Step 27 must not require a D12 semantic threshold or human-review pass, while retaining all structural, provenance, count, collision, D13 failure, and six-curriculum validation requirements.
4. Step 29 verifies that the absence of a pre-release independent semantic gate is accurately disclosed and does not claim semantic correctness from structural validation or producer/checker agreement.
5. Repository-level and role-specific instructions that currently make D12 evaluation a pre-release gate must be synchronized before the engineering brief can truthfully mark D12 settled.

### D12 accepted limitation

**LIMIT — V1 may release semantically incorrect or incomplete progression relationships because no independent human or gold-set semantic evaluation is required before release.** Producer/checker separation and deterministic validation reduce structural and process risk but do not establish pedagogical correctness. This limitation must be visible in release documentation and final review.

## D13 — Failed-request tolerance and release policy

### Status

**Resolved — Option A, any failed pair halts the LP run, with prefix-safe local checkpointing and resume.**

### Failure and halt policy

1. A valid `buildsTowards`, `relatesTo`, `no_relation`, or `needs_review` judgment is not a processing failure.
2. A failure is a timeout, malformed response, exhausted retry policy, missing or extra pair coverage, endpoint leakage, illegal relation/direction, or another producer/checker integrity violation that cannot be resolved by the permitted deterministic validation/retry path.
3. After any candidate pair becomes failed, the LP phase halts. There is no tolerated failed-pair count or rate for exploratory or release runs.
4. The failure and all completed checkpoints remain inspectable, but the interrupted run cannot produce a successful LP or combined release status.
5. D12's non-blocking `needs_review` policy does not weaken D13: `needs_review` is a valid abstention, whereas a malformed, missing, or otherwise invalid judgment is a failure.

### Pre-LLM materialization

Before the first external LP LLM call:

1. the complete deterministic candidate set is validated and written to `lp_candidate_pairs.jsonl` with its summary and material fingerprints;
2. the complete deterministic bounded request sequence is validated and written to `lp_generation_requests.jsonl`; and
3. candidate/request counts, IDs, order, coverage, and fingerprints reconcile exactly.

No LLM execution begins from an in-memory-only or partially materialized candidate/request population.

### Incremental successful-response checkpoints

1. Successful producer drafts are appended to `lp_generation_draft_responses.jsonl` only after schema, request-ID, pair-coverage, endpoint-containment, and fingerprint validation.
2. Successful checker verdicts are appended to `lp_generation_validation_verdicts.jsonl` only after the corresponding producer draft and the complete checker response validate.
3. Successful reconciled final judgments are appended to `lp_generation_responses.jsonl` only after the complete request has one valid final judgment per pair.
4. Failure details are written to `lp_generation_failures.json`; failed or partial responses are never appended as successful checkpoints.
5. JSONL writes follow deterministic request order and represent valid contiguous prefixes. Concurrent or out-of-order completion must be buffered or otherwise handled without creating a reusable prefix gap.

### `overwrite=false` resume behavior

1. On restart with `overwrite = false`, the pipeline reloads and validates the saved candidate set, request sequence, successful draft/verdict/final JSONL prefixes, failure record, and every material upstream, policy, prompt, model-settings, and input fingerprint.
2. Fully validated successful prefixes are reused without repeating their completed external LLM calls.
3. Resume begins at the earliest unfinished producer/checker stage for the first incomplete request. For example, a valid saved producer draft may be reused when the checker failed, so the checker stage—not the producer call—is retried.
4. Gaps, duplicates, out-of-order records, truncated/invalid JSONL, request misalignment, or stale fingerprints are rejected rather than silently reused.
5. A changed candidate population, request sequence, relevant configuration/policy, prompt, model settings, or upstream AS+LC fingerprint invalidates affected progress and requires the appropriate deterministic stage to be regenerated before calls resume.
6. The previous failure record remains audit evidence; the resumed run records whether and how the failed stage later succeeded.

### Consequences for all six curricula

The same zero-tolerance failure policy and prefix-safe resume contract apply to Madhi mathematics, Nigeria mathematics, Pratham science, Rwanda mathematics, Ghana mathematics, and Ghana English. No curriculum-specific count/rate thresholds are required in `kgs.lp`.

### D13 rationale

Option A gives every successful run complete producer/checker coverage while local deterministic checkpoints prevent one late transient failure from forcing already validated LLM work to be purchased and executed again.

## Next unresolved decision

D14 — Manual semantic edge overrides. The future Step 0 coding-agent session must also complete the deferred D12 governance synchronization and D11 template normalization in the engineering brief before Step 0 closes.
