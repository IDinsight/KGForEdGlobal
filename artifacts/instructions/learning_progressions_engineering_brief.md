# Engineering Brief: Learning Progressions KG Construction

**Status:** Governance amendment approved for implementation by the user on 2026-09-11, after review of the synchronized amendment and both evidence-condition corrections. D12-S/J/R/B/A are settled: CLI-configurable S1/R1 defaults; dedicated `LLM_LP_EVAL_JUDGE_MODEL=anthropic:claude-opus-5`; no evaluation budget ceilings; the recorded finite execution settings; and A1 concern disposition. The approval record is in Section 3.3. Proceed in the amended build order, beginning with separate testing-role Step 24–25 reassessment and reviewer reapproval. Harness implementation begins only after Step 26 reviewer approval. Live calls/full runs require separate execution authorization; Git mutations and changes to active runs are not authorized by this approval.

**Prior approval and amendment impact:** The supplied former reviewer-approved frontier is Step 25 at `540ea950378ce54b54da1c7a93491b525609b574`; Steps 22–25 were revalidated there. On 2026-09-11 the observed root was `/Users/tzz/Projects/private/idi/KGForEdGlobal`, branch `tz6/lp-kg-build-step-25`, HEAD exactly that SHA, working tree clean, and no later commits. These observations are not a new reviewer approval. Adoption on 2026-09-11 reopens **K=24 through F=25**, retaining that SHA as the cross-step review base: Step 24's former repository-wide prohibition on required semantic samples/metrics in tests, scripts, and documentation is narrowed to the production pipeline, while required downstream evaluation is introduced. That is an approval-affecting change to the earlier release-policy conformance scope, not a purely prospective renumbering. Steps 24–25 need independent testing reassessment and reviewer reapproval before Step 26. Earlier production obligations, including Step 18's structural-only validation report and Steps 22–23 orchestration/reuse, remain unchanged; no evaluation field or prerequisite is added to those artifacts. If reassessment finds an earlier affected contract, stop and explicitly reopen that earliest owner rather than silently extending this range.

**Historical implementation approval (superseded only where the 2026-09-11 amendment changes it):** D1–D14 were fully recorded as **SETTLED** in that prior version. The user approved the prior canonical brief—including its accepted LIMITs—for Step 1+ implementation on 2026-09-01, approved the first D3 amendment on 2026-09-02, and explicitly approved this governance simplification amendment on 2026-09-02. The approved amendment removes internal candidate-policy versioning and runtime candidate ranking/tie-breaking configuration, and moves universal single-valued coordinate, candidate-policy, provenance, checkpoint, resume, license-inheritance, and stale-input behavior into code-owned invariants. At that time, the reviewer-approved Step 1 state remained the predecessor because Step 1 did not implement or consume these LP configuration fields. That historical Step 2 handoff used `345bb517957d90e9bb68793947ec4428bd9f0997`; it is not the review base for this amendment.

**Repository area:** `backend/src/kgfeg/`

**Primary entry point:** `backend/src/kgfeg/entries/create_kgs.py`

This brief is based on a review of:

- the current KG pipeline source, Pydantic schemas, prompts, agents, validators, exporters, runtime configuration, and documentation;
- all six example curriculum profiles and their completed KG artifacts: Madhi mathematics, Nigeria mathematics, Pratham science, Rwanda mathematics, Ghana mathematics, and Ghana English;
- the current Learning Commons documentation for [Academic Standards](https://docs.learningcommons.org/knowledge-graph/schema-reference/standards), [Learning Components](https://docs.learningcommons.org/knowledge-graph/schema-reference/learning-components), [Learning Progressions](https://docs.learningcommons.org/knowledge-graph/schema-reference/learning-progressions), [common relationship properties](https://docs.learningcommons.org/knowledge-graph/schema-reference/common-relationship-properties), and the [Math Coherence Map](https://docs.learningcommons.org/knowledge-graph/datasets/math-coherence-map).

---

# 1. What we are building

## 1.1 Objective

**SETTLED — Add Learning Progressions as the third phase of the existing KG step.**

The shared KG command will construct, in order:

```text
DocumentIR
   |
   v
Academic Standards KG
  StandardsFramework
  StandardsFrameworkItem
  hasChild
   |
   v
Learning Components KG
  LearningComponent
  supports
   |
   v
Learning Progressions KG
  buildsTowards
  relatesTo
   |
   v
Combined AS + LC + LP bundle and flat projections
```

The Learning Progressions phase begins only after the Academic Standards and Learning Components phases have produced a validated `AcademicStandardsLCKGBundle`.

In the current code, `build_kgs()` calls `compile_as_lc_kg()` at its final step and discards the returned bundle. The insertion point is therefore explicit:

```python
as_lc_bundle = compile_as_lc_kg()

# New downstream phase.
learning_progressions = build_learning_progressions(
    as_lc_bundle=as_lc_bundle
)
```

The LP phase may use bounded source evidence already preserved in final SFI provenance, but it will not restart PDF extraction or rebuild Academic Standards.

## 1.2 Ontology output

**SETTLED — Learning Progressions adds relationships, not nodes.**

The published LP layer will contain only these two Learning Commons relationship types:

```text
StandardsFrameworkItem
    --buildsTowards-->
StandardsFrameworkItem

StandardsFrameworkItem
    --relatesTo-->
StandardsFrameworkItem
```

No `LearningProgression` node will be introduced.

**SETTLED — Learning Components are evidence, not progression endpoints.**

The pipeline may use the LCs supporting either standard to nominate, explain, or validate a progression relationship, but it will not publish:

```text
LearningComponent --buildsTowards--> LearningComponent
LearningComponent --relatesTo--> LearningComponent
```

That keeps the output inside the Learning Commons ontology, prevents a second unversioned progression ontology from emerging, and avoids making the primary graph dependent on the current LC decomposition/deduplication policy.

## 1.3 Runtime configuration

**SETTLED — `kgs.lp` is required whenever `kgs` is present.**

`RunConfig.kgs` may remain optional so that a run can still skip the entire KG step. However, once `kgs` is supplied, `CreateKGConfig` will require all four namespaces:

```text
kgs.as
kgs.lc
kgs.lp
kgs.metadata
```

Conceptually:

```python
class CreateKGConfig(BaseSchema):
    academic_standards: _CreateKGAcademicStandardsConfig = Field(alias="as")
    learning_components: _CreateKGLearningComponentsConfig = Field(alias="lc")
    learning_progressions: _CreateKGLearningProgressionsConfig = Field(alias="lp")
    metadata: _CreateKGMetadata
```

All six example configs will be updated, and all six curricula will be rerun through AS, LC, and LP.

**SETTLED — Curriculum-specific semantics belong in `kgs.lp`; universal integrity belongs in Python.**

Examples of configuration-owned policy:

- participating local statement types and allowed statement-type pairings;
- the curriculum's local grade/class/stage progression order;
- relation-specific inclusion and exclusion rules;
- curriculum-specific producer and checker instructions;
- the required two-state unresolved-context participation policy;
- candidate budgets and request batching.

Examples of code-owned behavior:

- strict Pydantic validation and cross-field checks;
- DAG-safe graph indexing;
- canonical coordinate lookup plus the settled missing-coordinate, same-rank, and rank-gap behavior;
- deterministic candidate and request identities;
- the single built-in deterministic non-embedding candidate policy;
- bounded, explainable candidate generation using configured budgets plus code-owned ranking and tie-breaking;
- producer/checker orchestration, deterministic-prefix checkpointing, fail-closed resume, and stale-input rejection;
- source-license inheritance and the required relationship-provenance categories;
- endpoint, relation-shape, duplicate, cycle, provenance, and count validation;
- deterministic UUIDv5 relationship identities;
- artifact writing, content hashing, and combined graph compilation.

## 1.4 LLM model

**SETTLED — LP uses the existing `LLM_KG_MODEL`.**

No additional model environment variable is required for production LP. The model registry already accepts the `"learning_progressions"` model-settings type. Production LP adds its own agents, prompts, retry behavior, and usage buckets while using the same configured KG model as AS and LC. The separate Step 27 evaluator uses `LLM_LP_EVAL_JUDGE_MODEL` under D12-J; that variable must not become a requirement for production startup or AS/LC/LP execution.

## 1.5 Schemas

**SETTLED — Replace the dormant LP schemas rather than preserving them.**

The currently unused `ProgressionEdge` and `ProgressionEdgesResponse` models in `kgs/schemas.py` are not compatibility constraints. They do not adequately represent both relationship types, an explicit no-relation judgment, checker correction, candidate identity, evidence references, or unresolved/manual-review states.

They should be removed or replaced with schemas aligned to the current pipeline architecture.

## 1.6 Output artifacts

**SETTLED — LP receives its own audit, provenance, unresolved-state, summary, and validation artifacts.**

The exact internal class names may change during implementation, but the intended artifact contract is:

```text
lp_eligible_sfis.json
lp_eligibility_report.json
lp_candidate_pairs.jsonl
lp_candidate_summary.json
lp_generation_requests.jsonl
lp_generation_draft_responses.jsonl
lp_generation_validation_verdicts.jsonl
lp_generation_responses.jsonl
lp_generation_failures.json
lp_final_claims.json
lp_relationships_builds_towards.jsonl
lp_relationships_relates_to.jsonl
lp_relationship_provenance.json
lp_unresolved_items.json
lp_generation_summary.json
lp_validation_report.json
```

The primary consumer-facing outputs added by this project are:

```text
as_lc_lp_kg_bundle.json
as_lc_lp_nodes.jsonl
as_lc_lp_relationships.jsonl
```

Their logical contents will be:

```text
as_lc_lp_nodes.jsonl
  = 1 StandardsFramework
  + all StandardsFrameworkItems
  + all LearningComponents

as_lc_lp_relationships.jsonl
  = all hasChild
  + all supports
  + all buildsTowards
  + all relatesTo
```

Because LP adds no nodes, `as_lc_lp_nodes.jsonl` will normally have the same logical node set as `as_lc_nodes.jsonl`. It is still written so the triplet graph can be loaded as an independent, self-contained release.

**SETTLED — Existing outputs remain intact.**

The project will not silently change the meaning of:

```text
as_kg_bundle.json
as_nodes.jsonl
as_relationships.jsonl
as_lc_kg_bundle.json
as_lc_nodes.jsonl
as_lc_relationships.jsonl
```

The AS-only and AS+LC artifacts remain valid integration boundaries. The new AS+LC+LP artifacts are additive.

**SETTLED — `as_lc_lp_*.jsonl` follows the existing combined internal projection, not the AS Learning Commons wire projection.**

Like `as_lc_nodes.jsonl` and `as_lc_relationships.jsonl`, the new combined files will use snake_case internal models, retain internal relationship metadata, and include `entity_type` on node rows. They are not interchangeable with the slim Learning Commons-shaped `as_nodes.jsonl` / `as_relationships.jsonl` contract.

### Separate evaluation artifacts (Step 27)

The evaluator reads fixed, validated production snapshots and writes only to an explicitly selected evaluation directory disjoint from all production input/output directories. It does not add fields to `kgs.lp`, `kg_run.json`, LP validation reports, bundles, projections, or production provenance. Their structural-only meaning remains intact even when a separate evaluation exists.

Required evaluation artifacts are `lp_eval_manifest.json`, `lp_eval_population.json`, `lp_eval_sample.jsonl`, `lp_eval_requests.jsonl`, `lp_eval_judgments.jsonl`, `lp_eval_failures.jsonl`, `lp_eval_usage.json`, `lp_eval_report.json`, and `lp_eval_report.md`. They retain source paths and byte/material hashes, exact producer candidate SHA and evaluator candidate SHA, effective production and evaluator configs, population membership/counts, seeds/selection procedures, conditions/order/replicates, full request identities, prompt/schema/model settings hashes, validated outputs, attempt/failure disposition, denominators, and report-generation inputs. Independent-cohort artifacts bind the common upstream evidence-constructor settings and material payload hashes frozen before the production-metadata join. Reports retain that join and the common-condition numerator/denominator membership; overlapping production/independent selections retain distinct required judgment identities. Exact serialized field names are owned by Step 27 coding under the settled policies.

Full evaluation outputs are immutable review evidence, not committed curriculum fixtures. The handoff records their directory, manifest/checksums, commands, authorization, actual model/settings, usage/cost accounting and any unknown usage. Automated test fixtures remain reduced and synthetic. See D12 for scoring and execution integrity.

## 1.7 Success criteria

The implementation is complete when:

1. all six example configs validate with required `kgs.lp` sections;
2. all six full runs produce the LP audit artifacts and the three final AS+LC+LP outputs;
3. existing AS and AS+LC outputs continue to validate and retain their existing schemas;
4. every accepted progression relationship has valid SFI endpoints, deterministic identity, complete audit provenance, and an accounted-for producer/checker path;
5. the combined bundle's counts and projections reconcile exactly;
6. repeat/resume behavior follows existing KG conventions;
7. structural tests cover tree, DAG, unresolved hierarchy, scope-only grade, multiple Standard grains, sparse LC reuse, noisy LC reuse, and recurring-practice cases;
8. production release behavior remains structural/process-only under D12/D13: `needs_review` remains visible, never publishes, and does not block production success; required downstream Step 27 evaluation execution/reporting is a separate build-order completion obligation, with no automatic semantic score threshold or human gold-set prerequisite;
9. every candidate/request pair is processed successfully under D13's zero-tolerance failure policy, with validated deterministic-prefix checkpoint and resume behavior; and
10. all six fixed Step 26 input snapshots receive the required Step 27 evaluation and reports, independently validated and reviewed; Step 28 documentation and Step 29 review disclose evaluator failures, ambiguity, quality concerns, sampling denominators, and accepted D12/D14 limitations.

## 1.8 Non-goals for this project

**LIMIT — This project does not create cross-framework progression edges.**

Each `create_kgs` run currently owns one source PDF and one `StandardsFramework`. LP will infer relationships inside that framework only. Connecting Ghana standards to Nigeria standards, or a local framework to CCSS, would require a separate multi-framework indexing and identity project.

**LIMIT — This project does not claim empirical prerequisite truth.**

The graph will infer curriculum-grounded developmental and conceptual relationships from standards, hierarchy, LCs, and source evidence. It is not based on longitudinal learner-performance data, controlled learning-science studies, or an official progression map unless the source curriculum itself supplies such evidence.

**LIMIT — This project does not add LC-to-LC progression or named pathway entities.**

Those may be useful future extensions, but they are outside the current Learning Commons LP ontology and would require separate semantics, versioning, validation, and product requirements.

---

# 2. The core model

## 2.1 The three graph layers

The combined graph should be understood as three different kinds of structure over shared standards nodes:

```text
DECLARED CURRICULUM STRUCTURE
parent --hasChild--> child

SKILL DECOMPOSITION / ALIGNMENT
LearningComponent --supports--> StandardsFrameworkItem

DEVELOPMENTAL AND CONCEPTUAL COHERENCE
StandardsFrameworkItem --buildsTowards--> StandardsFrameworkItem
StandardsFrameworkItem --relatesTo--> StandardsFrameworkItem
```

**SETTLED — `hasChild` is not progression evidence by itself.**

A parent/child relationship can connect two normalized `Standard` nodes, as it does in Pratham science and both Ghana curricula. That means only that one source item directly organizes or decomposes another. It does not mean the parent is learned before the child.

```text
Content Standard --hasChild--> Indicator

is not automatically

Content Standard --buildsTowards--> Indicator
```

If a pair is allowed by LP policy, it still requires independent progression evidence.

## 2.2 Relationship semantics

### `buildsTowards`

**SETTLED — Use the Learning Commons meaning, not a strict prerequisite meaning.**

```text
A --buildsTowards--> B
```

means:

> Proficiency in A supports the likelihood of success in B.

It is directional, but it does not assert that A is a mandatory prerequisite, that B may never be taught first, or that the edge defines one compulsory instructional sequence.

The existing fallback description in `Relationship._fill_missing_description()` is too strong because it says “prerequisite progression.” It should be changed to the Learning Commons-compatible wording.

### `relatesTo`

**SETTLED — `relatesTo` means substantive conceptual or skill coherence without dependency.**

A useful mental model is:

> These standards belong in the same instructionally meaningful conceptual neighborhood, but neither one is asserted to be upstream of the other.

Good reasons can include:

- complementary skills;
- different representations of the same idea;
- cross-domain application;
- shared problem-solving structure;
- parallel or reinforcing concepts;
- a recurring capability whose later occurrence does not clearly depend on the earlier occurrence.

It is not enough that two standards:

- are in the same subject;
- share a parent;
- are in the same grade;
- contain a few overlapping words; or
- are broadly “about numbers,” “reading,” or “science.”

Under settled D5, one accepted `relatesTo` judgment is serialized as one canonical row per unordered pair. The lower canonical `case_identifier_uuid` is stored as source and the higher as target; that ordering is technical, not semantic, and consumers must traverse both endpoint positions.

### Explicit negative and unresolved judgments

**SETTLED — The LLM response must distinguish a confident negative from ambiguity.**

The replacement response schema must support at least these outcomes:

```text
accepted buildsTowards
accepted relatesTo
no_relation
needs_review
```

- `no_relation` means the available evidence supports publishing neither relationship.
- `needs_review` means the evidence is materially ambiguous or contradictory. It produces no edge and is reported under LP unresolved items.
- a malformed, missing, or unprocessable response is a generation failure, not `needs_review`.

This distinction is necessary for auditability and for measuring pipeline quality.

## 2.3 Final endpoints and identifiers

**SETTLED — Both progression relationship types use SFI CASE UUID endpoints.**

Each final relationship is an internal `Relationship` with:

```text
source_entity      = StandardsFrameworkItem
source_entity_key  = case_identifier_uuid
target_entity      = StandardsFrameworkItem
target_entity_key  = case_identifier_uuid
```

The LLM never invents final relationship UUIDs. Python mints them deterministically from:

```text
doc_key
relationship_type
resolved source case_identifier_uuid
resolved target case_identifier_uuid
```

using the repository's existing canonical UUID namespace and relationship identity pattern.

For `relatesTo`, the identity inputs depend on the canonicalization decision in **D5**.

## 2.4 The LP processing model

The intended phase is:

```text
validated as_lc_kg_bundle
        |
        v
1. Build graph indexes and upstream quality flags
        |
        v
2. Select LP-eligible SFIs using kgs.lp
        |
        v
3. Generate deterministic bounded candidate pairs
        |
        v
4. Build evidence-rich, bounded LLM requests
        |
        v
5. Producer judges each requested pair
        |
        v
6. Independent checker accepts or corrects
        |
        v
7. Python reconciles decisions and mints final relationships
        |
        v
8. Validate LP graph, provenance, counts, and policy
        |
        v
9. Compile AS + LC + LP bundle and projections
```

**SETTLED — There is no all-pairs LLM pass.**

The six reviewed curricula demonstrate why:

| Candidate population               | Eligible/normalized Standard count | Unordered pairs before blocking |
|------------------------------------|------------------------------------|---------------------------------|
| Madhi Content                      | 220                                | 24,090                          |
| Nigeria Performance Objective      | 155                                | 11,935                          |
| Pratham all normalized Standards   | 831                                | 344,865                         |
| Pratham Indicators only            | 554                                | 153,181                         |
| Rwanda normalized Standards        | 542                                | 146,611                         |
| Ghana math normalized Standards    | 256                                | 32,640                          |
| Ghana English normalized Standards | 319                                | 50,721                          |

Candidate nomination is therefore its own deterministic subsystem. The LLM decides among plausible, evidence-rich pairs; it does not search the complete graph.

## 2.5 Cross-curriculum requirements proven by the six runs

| Curriculum          | Important AS shape                                                               | Local progression coordinate                                   | LP design pressure                                                            |
|---------------------|----------------------------------------------------------------------------------|----------------------------------------------------------------|-------------------------------------------------------------------------------|
| Madhi mathematics   | Tree; one normative type (`Content`)                                             | `Class` exists as identity scope rather than final Class nodes | Coordinate extraction cannot require a grade node                             |
| Nigeria mathematics | Tree; `Grade -> Theme -> Sub-Theme -> Topic -> Performance Objective`            | Grade node plus descendant scope                               | Simple tree must not become the hidden universal assumption                   |
| Pratham science     | DAG; 235 SFIs have two parents; three normalized Standard grains                 | Class node plus scope                                          | Context must preserve all parents/ancestor paths; LP grain must be configured |
| Rwanda mathematics  | Five normalized Standard types; LC sources are only three of them                | Grade node plus scope                                          | LP eligibility cannot reuse LC eligibility; generic LC overlap can be noisy   |
| Ghana mathematics   | Content Standard and Indicator are both Standards; 13 unresolved hierarchy edges | Grade node plus scope                                          | A passed AS bundle can still contain unresolved fallback ancestry             |
| Ghana English       | Two Standard grains; two unresolved Indicators; repeated cross-grade skills      | Grade node plus scope                                          | Recurring practice must be distinguished from developmental progression       |

These are not exceptional branches to hard-code. They define the generic LP contract.

## 2.6 Graph indexes and context

**SETTLED — LP graph utilities must be DAG-safe.**

The phase must build indexes for:

- SFI by `case_identifier_uuid`;
- parent and child adjacency from `hasChild`;
- all direct parents, not one chosen parent;
- bounded ancestor paths and nearest ancestors by local statement type;
- unresolved root-fallback relationships and affected ancestry;
- LCs by supporting SFI;
- supporting SFIs by LC;
- SFI source/audit metadata;
- local progression coordinate and rank.

The producer/checker context must not flatten Pratham's two-parent structure into one invented “canonical path.” A bounded context record can list `parent_uuids` and the relevant ancestor subgraph.

## 2.7 Local developmental order

**SETTLED — Local canonical curriculum values are authoritative; Learning Commons US grade enums are not.**

The LP phase will not infer sequence by sorting display strings or by relying only on `StandardsFrameworkItem.grade_level`.

For example:

```text
Class-1 < Class-2 < Class-3 < Class-4 < Class-5
PRIMARY ONE < PRIMARY TWO < PRIMARY THREE
P1 < P2 < P3
BASIC 4 < BASIC 5 < BASIC 6
Class IX < Class X
```

Those orders must come from reviewed curriculum configuration.

Settled D2 uses one explicit primary ordered dimension for v1. Runtime configuration supplies only the local coordinate statement type and ordered canonical values. Python owns coordinate resolution plus the settled missing/invalid-value, same-rank, direction, and rank-gap behavior in Section 3 D2.

## 2.8 Eligibility and progression grain

**SETTLED — LP eligibility is independent of normalized type, leafness, and LC eligibility.**

The following shortcuts are invalid:

```python
lp_eligible = normalized_statement_type == "Standard"
lp_eligible = is_leaf
lp_eligible = lc_eligible
```

They happen to work for some curricula and fail for others.

- Pratham has 831 normalized Standards but only 554 Indicator LC seeds.
- Rwanda has 542 normalized Standards but only 475 LC seeds.
- Ghana Content Standards and Indicators are both normalized Standards.
- Rwanda Grade Key Competences can be leaf Standards without being LC sources.

The participating statement types and closed-world relation-specific pairings must be explicit in `kgs.lp` and must reproduce the complete six-profile matrices in Section 3 D1. Omitted types and pairs are excluded.

## 2.9 Candidate evidence

A candidate pair should carry explainable evidence rather than one opaque similarity score. Potential evidence families include:

- local progression-rank compatibility;
- allowed statement-type pairing;
- shared or nearby hierarchy context across all parent paths;
- same/different local domain, strand, topic, unit, competency, or chapter;
- source codes and code-prefix relationships, with AS audit flags preserved;
- source-document order as a weak signal only;
- exact shared Learning Components;
- related LC text/tags;
- SFI text similarity;
- common action, object, representation, or concept language; and
- other generic named evidence values owned by the built-in policy.

**SETTLED — No single evidence signal automatically creates an edge.**

In particular:

```text
shared LC             != buildsTowards
same parent           != relatesTo
adjacent grade        != buildsTowards
similar wording       != relatesTo
code prefix           != progression
```

Every published edge must pass semantic producer/checker adjudication and deterministic validation.

Settled D3 uses one built-in deterministic non-embedding candidate policy. The implementation may combine multiple named, explainable evidence signals internally, but runtime configuration does not select algorithms, strategies, enabled-signal lists, ranking inputs or precedence, tie-breaking rules, fingerprint groups, or implementation versions. The policy records every nomination reason and triggering value, applies explicit configured budgets, and ranks and tie-breaks candidates through one stable code-owned procedure. A future candidate-policy replacement is a separately governed build step that removes or replaces the prior implementation; the user deletes and regenerates affected candidate and downstream artifacts rather than maintaining a runtime compatibility/version layer.

## 2.10 Producer/checker contract

**SETTLED — LP follows the repository's producer/checker architecture.**

For every bounded request:

1. the producer returns a complete structured judgment for every requested candidate pair;
2. an independent checker receives the same bounded evidence, the producer result, generic rules, and curriculum-specific validation instructions;
3. the checker either accepts the result or returns a complete corrected result;
4. Python verifies exact pair coverage, endpoint membership, allowed directions, schema integrity, and request/response identity;
5. only the accepted or checker-corrected result proceeds.

The model may not introduce an endpoint that was absent from the candidate request.

The request schema supports batching through a required positive request-batch setting, with a correctness-first default of 1. Batch size changes throughput, not D4's one-judgment-per-logical-pair semantics. Step 3 determines the final field name.

## 2.11 Proposed internal records

The final field names should be finalized after Section 3 decisions, but the implementation needs these concepts:

| Record                          | Purpose                                                                                                                                             |
|---------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| `LPEligibleSFI`                 | Final SFI plus statement type, coordinate, hierarchy status, and eligibility reason                                                                 |
| `LPCandidatePair`               | Deterministic pair identity, allowed decisions/directions, nomination signals, and bounded evidence references                                      |
| `LPGenerationRequest`           | One or more exact candidate pairs plus SFI/LC/hierarchy context and material config/input content hashes                                            |
| `LPPairJudgment`                | One structured accepted, negative, or unresolved judgment for one pair                                                                              |
| `LPGenerationResponse`          | Exact complete judgment set for one request                                                                                                         |
| `LPGenerationValidationVerdict` | Checker pass/fail, issues, and optional corrected complete response                                                                                 |
| `LPGenerationFailure`           | Request/pair failures after retries                                                                                                                 |
| `LPFinalClaim`                  | Reconciled semantic claim before conversion to `Relationship`                                                                                       |
| `LPGenerationSummary`           | Eligibility, candidate, decision, edge, failure, and distribution counts                                                                            |
| `LPUnresolvedItems`             | `needs_review` judgments; normal eligibility exclusions remain in the eligibility report and D13 processing failures remain in the failure artifact |
| `LPValidationReport`            | Standalone LP graph checks and material input/artifact content hashes                                                                               |
| `AcademicStandardsLCLPKGBundle` | Complete triplet graph and merged validation state                                                                                                  |

## 2.12 Proposed combined bundle

**SETTLED — The combined bundle is additive and self-contained.**

Conceptually:

```json
{
  "entity_provenance": {
    "relationships_builds_towards": {},
    "relationships_relates_to": {}
  },
  "framework": {},
  "items": [],
  "learning_components": [],
  "relationships_has_child": [],
  "relationships_supports": [],
  "relationships_builds_towards": [],
  "relationships_relates_to": [],
  "summary": {
    "academic_standards": {},
    "learning_components": {},
    "learning_progressions": {},
    "total_node_count": 0,
    "total_relationship_count": 0
  },
  "unresolved_items": {
    "academic_standards": {},
    "learning_components": {},
    "learning_progressions": {}
  },
  "validation_report": {}
}
```

The placeholder entry above is explanatory, not a literal schema field. The compiler must copy the complete `as_lc_bundle.entity_provenance` mapping without deletion or reshaping, then add non-colliding LP relationship-provenance entries. It must likewise preserve the complete upstream framework, SFI, LC, `hasChild`, `supports`, summary, and unresolved content before adding LP fields.

A `no_relation` judgment is a normal adjudication outcome and is counted in the LP summary; it is not an unresolved item. Normal policy-based eligibility exclusions are accounted for in `lp_eligibility_report.json`, not mislabeled as unresolved judgments. `needs_review` judgments are unresolved, appear in `lp_unresolved_items.json`, never publish an edge, and do not block release. D13 processing failures remain distinct in `lp_generation_failures.json`, halt the LP phase after permitted retries/recovery, and prevent successful LP or combined release status.

## 2.13 Settled `kgs.lp` configuration semantics

This section fixes configuration meaning without inventing final schema field names or literal encodings. Step 2 defines intrinsic models and Step 3 chooses the concrete `kgs.lp` field names while preserving these semantics exactly.

Every profile must explicitly provide only the curriculum-specific or operationally variable inputs:

- curriculum-specific producer and checker instructions;
- the closed-world D1 `buildsTowards` and `relatesTo` statement-type pair matrices;
- the one-axis D2 coordinate statement type and exact ordered canonical values;
- one required D10 unresolved-participation state with no default; all six initial profiles select inclusion of all otherwise-eligible unresolved SFIs with warnings;
- D3 candidate budgets;
- positive request batch size and bounded evidence limits;
- exact D11 author, provider, attribution template, and approving identity; and
- D13 producer and checker retry counts. D13 permits no failed-pair rate/count tolerance and therefore requires no per-profile tolerance threshold.

The config must be cross-validated against `kgs.as.statement_type_policy`, controlled values, identity scope, and the final local coordinate vocabulary. Unknown, missing, contradictory, or silently defaulted variable policy must fail at config load time.

Python owns the universal settled behavior: canonical coordinate lookup through identity scope; missing/invalid-coordinate, same-rank, direction, and rank-gap rules; the single candidate implementation and its evidence handling, ranking, tie-breaking, and budget-application order; exact source-license inheritance; required relationship provenance; deterministic-prefix checkpointing; earliest-unfinished-stage resume; and rejection of stale or misaligned progress. `kgs.lp` must reject fields that attempt to restate or override those invariants, including `algorithm_version`, `strategies`, candidate technology markers, enabled-signal or ranking/tie-breaking selectors, `fingerprint_inputs`, candidate-policy versions, coordinate-source/rank-gap/missing-coordinate selectors, `license_source`, retained-provenance switches, checkpoint policy, resume policy, or fingerprint-mismatch policy. Content hashes are computed from the actual material inputs and artifacts needed for integrity; they are not configured field lists or manually maintained policy versions. Illustrative names in this brief are not final API names.

## 2.14 Core limitations

**LIMIT — Candidate blocking creates an unmeasured v1 recall ceiling.**

The adjudicator can only accept pairs that candidate generation nominates. Strong producer/checker agreement cannot recover a relationship that deterministic retrieval never surfaced. D12 requires independently sampled LLM-judge diagnostics, but sampled judge-positive nomination coverage is not curriculum-wide candidate recall. Omitted policy populations, unsampled pairs, evidence limits, and judge error leave the recall ceiling unmeasured; missing plausible pairs may remain undetected.

**LIMIT — Upstream AS and LC errors can influence LP.**

LP endpoints are stable SFIs, but hierarchy errors, missing scope values, faulty unresolved flags, or weak LC decomposition can change candidate evidence. LP provenance must retain the upstream bundle content hash so these dependencies are visible.

**LIMIT — The six reviewed examples are English-language artifacts.**

The normalization code is Unicode-aware, but candidate retrieval and progression prompts have not yet been validated on non-English or multilingual progression judgments. A lexical-only first implementation may require language-specific tuning later.

**LIMIT — Required LLM evaluation does not establish pedagogical correctness.**

Pydantic and graph checks establish structural/process properties. The independent judge workflow supplies fallible, sample- and evidence-conditioned assessments, potentially correlated with the production model. It has no human gold-set prerequisite or automatic semantic score threshold. Evaluation must execute and report, but favorable scores, stable replicates, and synthetic-control performance do not prove instructional soundness or completeness. Quality concerns require explicit disposition under D12; confirmed defects follow D14 earliest-stage remediation and rerun.

---

# 3. Decisions and amendment policy

## 3.1 Marker contract

The following markers are load-bearing. D1–D11, D13, and D14 production semantics remain **SETTLED**. D12-S/J/R/B/A record the user's settled amendment decisions and complete payloads. The user separately approved this amendment for implementation on 2026-09-11; ordinary role, build-order, reviewer and live-execution gates still apply.

### **SETTLED**

Implement it. Do not reopen it unless a major issue would prevent the project from moving forward.

### **DECIDE**

A real choice with consequences. No affected code should be written until the choice is made. Once decided:

1. update this file first;
2. replace the relevant **DECIDE** text with **SETTLED**;
3. record the chosen option, rationale, and every required concrete value in the decision table or an adjacent approved policy table;
4. remove placeholders such as `TBD`, `...`, or angle-bracket values from implementation-governing fields;
5. obtain an implementation OK;
6. only then write the affected code.

An option letter alone does **not** settle a future decision when the selected option requires concrete policy payloads. The D12 amendment records all five complete settled payloads, including the separately confirmed operational settings. The user's explicit implementation approval of the synchronized amendment is recorded in Section 3.3.

### **LIMIT**

A known weakness or scope boundary we are accepting. It must be documented where a future reader will encounter it. It must not be silently hidden by code or prompts.

## 3.2 Decision log

| ID  | Decision                                                             | Settled option                                                                                                     | Status      |
|-----|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|-------------|
| D1  | How LP progression grain and statement-type pairings are configured  | B — closed-world relation-specific pair matrices                                                                   | **SETTLED** |
| D2  | How local developmental order is represented                         | A — one explicit primary ordered dimension for v1                                                                  | **SETTLED** |
| D3  | Candidate nomination technology                                      | A — one built-in explainable deterministic non-embedding candidate policy                                          | **SETTLED** |
| D4  | Candidate-pair orientation and adjudication shape                    | A — one canonical unordered pair and one unified relation/direction judgment                                       | **SETTLED** |
| D5  | How conceptually symmetric `relatesTo` is serialized                 | A — one UUID-canonicalized relationship per unordered pair                                                         | **SETTLED** |
| D6  | Whether one pair may publish both relation types                     | A — mutually exclusive, with `buildsTowards` precedence                                                            | **SETTLED** |
| D7  | How recurring practice is mapped                                     | C — extension → `buildsTowards`; meaningful recurrence → `relatesTo`; generic repetition → `no_relation`           | **SETTLED** |
| D8  | `buildsTowards` cycle policy                                         | A — the complete published graph must be acyclic with deterministic diagnostics                                    | **SETTLED** |
| D9  | Transitive edge policy                                               | A — publish every directly accepted edge; perform neither transitive closure nor reduction                         | **SETTLED** |
| D10 | How unresolved AS ancestry affects LP                                | Required two-state profile policy; all six initial profiles include every otherwise-eligible unresolved SFI warned | **SETTLED** |
| D11 | Attribution/ownership metadata for inferred LP edges                 | B — exact LP metadata, source-license inheritance, attribution template, and provenance                            | **SETTLED** |
| D12 | Semantic evaluation and release policy | Required downstream LLM-judge execution/reporting; configurable S1/R1 defaults; dedicated evaluator model; no budget ceilings or automatic semantic threshold; A1 concern disposition | **SETTLED** |
| D13 | Failed-request tolerance and release gate                            | A — any failed pair halts LP, with deterministic-prefix checkpoint and resume                                      | **SETTLED** |
| D14 | Manual semantic edge overrides in v1                                 | A — no forced semantic include/exclude/relation/direction overrides                                                | **SETTLED** |

The selected policy text in each subsection is implementation-governing. Any option analysis retained below is explicitly historical and rejected; it cannot govern implementation unless a later user-approved governance change reopens the settled decision.

---

## D1. Progression grain and statement-type pair policy

**SETTLED — Option B: each curriculum declares separate closed-world statement-type pair matrices for `buildsTowards` and `relatesTo`.**

An omitted pair is excluded. Every current `Standard Grouping`, every cross-type pair, and every future type/pair not deliberately added is excluded. An allowed pair only permits two distinct SFIs to enter candidate consideration; it never publishes an edge. `buildsTowards` pairs are directional type permissions whose endpoint direction still follows D2/D4. `relatesTo` pairs are semantically unordered and serialize under D5.

The exact initial matrices are:

| Curriculum          | Allowed `buildsTowards` type pairs                                                                                                                                                                                                                          | Allowed unordered `relatesTo` type pairs                                                                                                                                                                                                               | Excluded statement types                        |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------|
| Madhi mathematics   | `Content` → `Content`                                                                                                                                                                                                                                       | `{Content, Content}`                                                                                                                                                                                                                                   | `Curricular Goal`, `Competency`, `Class`        |
| Nigeria mathematics | `Performance Objective` → `Performance Objective`                                                                                                                                                                                                           | `{Performance Objective, Performance Objective}`                                                                                                                                                                                                       | `Grade`, `Theme`, `Sub-Theme`, `Topic`          |
| Pratham science     | `NCERT Learning Outcome` → `NCERT Learning Outcome`; `Content Domain Specific Learning Outcome` → `Content Domain Specific Learning Outcome`; `Indicator` → `Indicator`                                                                                     | `{NCERT Learning Outcome, NCERT Learning Outcome}`; `{Content Domain Specific Learning Outcome, Content Domain Specific Learning Outcome}`; `{Indicator, Indicator}`                                                                                   | `Class`, `Content Domain`, `Chapter`            |
| Rwanda mathematics  | `Grade Key Competence` → `Grade Key Competence`; `Key Unit Competence` → `Key Unit Competence`; `Knowledge Objective` → `Knowledge Objective`; `Skills Objective` → `Skills Objective`; `Attitudes and Values Objective` → `Attitudes and Values Objective` | `{Grade Key Competence, Grade Key Competence}`; `{Key Unit Competence, Key Unit Competence}`; `{Knowledge Objective, Knowledge Objective}`; `{Skills Objective, Skills Objective}`; `{Attitudes and Values Objective, Attitudes and Values Objective}` | `Grade`, `Topic Area`, `Sub-Topic Area`, `Unit` |
| Ghana mathematics   | `Content Standard` → `Content Standard`; `Indicator` → `Indicator`                                                                                                                                                                                          | `{Content Standard, Content Standard}`; `{Indicator, Indicator}`                                                                                                                                                                                       | `Grade`, `Strand`, `Sub-Strand`                 |
| Ghana English       | `Content Standard` → `Content Standard`; `Indicator` → `Indicator`                                                                                                                                                                                          | `{Content Standard, Content Standard}`; `{Indicator, Indicator}`                                                                                                                                                                                       | `Grade`, `Strand`, `Sub-Strand`                 |

**LIMIT — These closed-world matrices are semantic recall boundaries.** Omitted grains and type pairs are not considered until policy is deliberately revised.

### Why this matters

Several frameworks contain multiple kinds of normalized `Standard`:

```text
Pratham:
  NCERT Learning Outcome
  Content Domain Specific Learning Outcome
  Indicator

Rwanda:
  Grade Key Competence
  Key Unit Competence
  Knowledge Objective
  Skills Objective
  Attitudes and Values Objective

Ghana:
  Content Standard
  Indicator
```

Blindly comparing every normalized Standard mixes broad expectations, decomposed outcomes, and granular indicators.

### Rejected alternative — Option A: one flat LP statement-type allowlist

```json
{
  "lp_source_statement_types": ["Indicator", "Content Standard"]
}
```

Every selected type can be paired with every other selected type.

**ELI5:** Put all allowed toys in one box; any toy may be matched with any other toy.

**Pros**

- Small config.
- Easy selection logic.
- Similar to the LC allowlist.

**Cons**

- Cannot say “Indicator-to-Indicator is valid, but Indicator-to-Content Standard is not.”
- Creates cross-grain nonsense candidates.
- Makes relation-specific rules difficult.

### Selected design background — Option B: relation-specific statement-type pair matrices

```json
{
  "builds_towards": {
    "allowed_statement_type_pairs": [
      ["Indicator", "Indicator"],
      ["Content Standard", "Content Standard"]
    ]
  },
  "relates_to": {
    "allowed_statement_type_pairs": [
      ["Indicator", "Indicator"],
      ["Indicator", "Content Standard"]
    ]
  }
}
```

For `buildsTowards`, pair order can be meaningful. For `relatesTo`, pairs can be treated as unordered after D5.

**ELI5:** Use a seating chart that says exactly which kinds of students may sit together for each activity.

**Pros**

- Precise.
- Handles multiple curricular grains.
- Allows `buildsTowards` and `relatesTo` to have different policies.
- Cross-field validators can reject unknown or contradictory local types.

**Cons**

- More verbose.
- Every curriculum requires deliberate review.
- A missing pair can reduce candidate recall.

### Rejected alternative — Option C: default all normalized Standards, with exclusions

```json
{
  "exclude_statement_types": ["Attitudes and Values Objective"]
}
```

**ELI5:** Invite everyone by default, then maintain a “do not invite” list.

**Pros**

- Least config for simple curricula.
- New Standard types automatically participate.

**Cons**

- New or misclassified types silently change graph behavior.
- Unsafe for Pratham, Rwanda, and Ghana.
- Harder to audit than an explicit positive policy.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option B, which is now settled by the complete matrix above.

A conservative first profile could begin with same-grain pairs, then deliberately add broader or cross-grain pairs:

| Curriculum      | Conservative first pairing hypothesis                                                                    |
|-----------------|----------------------------------------------------------------------------------------------------------|
| Madhi math      | `Content` ↔ `Content`                                                                                    |
| Nigeria math    | `Performance Objective` ↔ `Performance Objective`                                                        |
| Pratham science | `Indicator` ↔ `Indicator`; separately decide whether to add same-grain NCERT LO and CDSLO pairs          |
| Rwanda math     | Decide separately for Knowledge, Skills, Attitudes/Values, Key Unit Competence, and Grade Key Competence |
| Ghana math      | `Indicator` ↔ `Indicator`; optionally add `Content Standard` ↔ `Content Standard` later                  |
| Ghana English   | `Indicator` ↔ `Indicator`; optionally add `Content Standard` ↔ `Content Standard` later                  |

**LIMIT if Option B is chosen:** The configured matrix is a semantic recall boundary. Omitted grains and pair types will not be considered.

The complete required D1 payload is recorded in the settled matrix above. Step 3 must encode and cross-validate those exact policies without inventing cross-type permissions or silent defaults.

---

## D2. Local developmental-order model

**SETTLED — Option A: v1 uses one explicit primary ordered dimension.**

For all six curricula, resolve the configured coordinate from the participating SFI's canonical identity-scope value; when an SFI is itself the coordinate statement type, its own canonical value may be used. Canonicalize recognized aliases through the existing AS statement-type and controlled-value policy. Never infer order from lexical sorting, source order, hierarchy proximity, Learning Commons `grade_level`, or an LLM.

Missing, invalid, and relationship behavior is uniform:

- an absent coordinate excludes the SFI from `buildsTowards` but retains it for `relatesTo` when otherwise eligible; record the absence and do not use rank as positive evidence;
- an unrecognized, ambiguous, or conflicting coordinate is a hard validation error and cannot publish any LP relationship;
- D10 exclusion, when selected by a future profile, takes precedence over the ordinary missing-coordinate allowance;
- same-rank `buildsTowards` is allowed;
- different-rank `buildsTowards` is allowed only from lower configured rank to higher configured rank;
- there is no maximum forward-rank gap; and
- `relatesTo` is allowed at the same rank, across any rank gap, and when one or both otherwise-eligible endpoints lack a coordinate.

These uniform rules are Python invariants, not repeated profile settings. Runtime `developmental_coordinate` configuration supplies only `statement_type` and `ordered_values`; fields that restate canonical-source, missing-coordinate, same-rank, direction, or gap behavior are rejected.

The exact initial coordinate profiles are:

| Curriculum          | Coordinate statement type | Canonical source                                     | Exact ordered values                              |
|---------------------|---------------------------|------------------------------------------------------|---------------------------------------------------|
| Madhi mathematics   | `Class`                   | scope-only `Class` value in `Content` identity scope | `Class-1 < Class-2 < Class-3 < Class-4 < Class-5` |
| Nigeria mathematics | `Grade`                   | `Grade` identity scope                               | `PRIMARY ONE < PRIMARY TWO < PRIMARY THREE`       |
| Pratham science     | `Class`                   | `Class` identity scope                               | `Class IX < Class X`                              |
| Rwanda mathematics  | `Grade`                   | `Grade` identity scope                               | `P1 < P2 < P3`                                    |
| Ghana mathematics   | `Grade`                   | `Grade` identity scope                               | `BASIC 4 < BASIC 5 < BASIC 6`                     |
| Ghana English       | `Grade`                   | `Grade` identity scope                               | `BASIC 1 < BASIC 2 < BASIC 3`                     |

**LIMIT — A future curriculum with genuine multi-axis progression requires a schema extension rather than heuristic tuple inference.**

### Selected design background — Option A: one explicit primary ordered dimension

```json
{
  "developmental_dimension": {
    "statement_type": "Class",
    "ordered_values": ["Class-1", "Class-2", "Class-3", "Class-4", "Class-5"]
  }
}
```

Generic code resolves the value from the SFI's canonical identity scope or the SFI itself.

**ELI5:** Every standard gets one floor number in a building. The floor names are local, but the elevator order is explicit.

**Pros**

- Fits all six reviewed curricula.
- Easy to validate and explain.
- Prevents lexical sorting mistakes.
- Supports scope-only Madhi Class values and explicit Grade/Class nodes.

**Cons**

- Cannot naturally model two independent axes such as grade and proficiency band.
- A framework without one clear axis needs a workaround.

### Rejected alternative — Option B: multiple ordered dimensions

```json
{
  "developmental_dimensions": [
    {"name": "Grade", "ordered_values": ["P1", "P2", "P3"]},
    {"name": "Phase", "ordered_values": ["Emerging", "Developing", "Secure"]}
  ]
}
```

**ELI5:** Each standard gets both a floor number and a room-level difficulty number.

**Pros**

- More future-proof.
- Can model frameworks with stage + phase, grade + band, or parallel tracks.

**Cons**

- Requires rules for comparing coordinate tuples.
- Makes direction, cycle, and candidate-gap validation much more complex.
- No reviewed curriculum currently proves the need.

### Rejected alternative — Infer order from Learning Commons `grade_level`, metadata text, or the LLM

This is documented for contrast, but it is **not selectable** under the current brief. It conflicts with the **SETTLED** rule that reviewed local curriculum values are authoritative and with the invariant forbidding heuristic/lexical order inference. Selecting it would first require reopening those settled rules.

- US grade mappings are not authoritative for international curricula.
- Some local values intentionally map to nothing or several grades.
- Madhi's Class is scope-only.
- LLM-inferred sequence is difficult to reproduce and validate.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option A, which is now settled by the complete coordinate policy above.

The selected canonical-source, same-level, direction, rank-gap, and missing-coordinate behavior is code-owned. A future policy change requires governance and implementation changes rather than profile-specific selector fields.

**LIMIT if Option A is chosen:** Frameworks with genuine multi-axis progression will require a later schema extension rather than silent heuristic inference.

The complete required D2 payload is recorded above. Step 3 must encode and cross-validate it exactly.

---

## D3. Candidate nomination technology

**SETTLED — Option A: v1 uses one built-in explainable deterministic non-embedding candidate policy.**

Candidate nomination is performed by one code-owned deterministic policy. The policy may combine multiple named evidence rules internally, but those rules are implementation components rather than runtime-selectable algorithms or strategies. Every candidate records all nominating reasons and the concrete triggering values. D1 and D2 are hard admissibility boundaries; no evidence rule bypasses them or publishes an edge. Generic Python owns evidence extraction, bounded nomination, union, deduplication, stable ranking/tie-breaking, budget-application order, identities, and content hashing. Required runtime configuration owns only the explicit per-SFI and total candidate budgets.

`_CreateKGLearningProgressionsCandidatePolicy` therefore contains only the configured budgets. It has no runtime `ranking`, ranking-input list, tie-breaking rule list, `algorithm_version`, `strategies`, technology marker, enabled-signal list, `fingerprint_inputs` selector, strategy-specific policy version, or open-ended candidate-algorithm parameter bag. No embedding model, provider, vector index, cache, dependency, cost boundary, or fallback is approved for v1.

**SETTLED — Candidate policy has no separately maintained implementation identifier or version.** Integrity hashes are derived from the actual material runtime inputs and serialized artifacts; they do not include a manually maintained candidate-policy version or a configurable list of fingerprint groups.

A future candidate-policy replacement requires a separately approved build step. Changing the evidence rules, ranking precedence or directions, missing-value handling, total tie-breaking procedure, or budget-application order counts as replacing this code-owned policy. That step removes or replaces the prior implementation rather than registering a second runtime-selectable strategy or retaining both policies side by side. The user will delete and regenerate every affected candidate and downstream artifact after the replacement; reuse compatibility across candidate-policy implementations is not supported or inferred. Any future embedding-based replacement defines its required runtime, dependency, persistence, cost, and unavailable-capability behavior in that future governance step rather than predeclaring version fields in v1.

### Selected design background — Option A: one built-in explainable deterministic non-embedding policy

The built-in policy can combine bounded evidence rules such as:

```text
same/related local hierarchy context
adjacent or allowed progression ranks
shared exact LC
related LC tokens/tags
SFI token or character-ngram similarity
reviewed code-prefix signal
```

Each rule records why it nominated the pair. The fixed code-owned policy bounds nomination work, deduplicates the union, applies one stable ranking and total tie-breaking procedure, and enforces the configured per-SFI and total candidate budgets before request construction or any LLM call.

**ELI5:** One transparent scouting process uses several visible clues, explains every suggestion, and produces a bounded ranked list for the judge.

**Pros**

- Auditable.
- Reuses current repository patterns from LC dedup and hasChild candidate generation.
- No new model/service/dependency.
- Deterministic and easy to fixture-test.

**Cons**

- May miss conceptually related standards with little wording or hierarchy overlap.
- Multilingual recall may be weak.
- Replacing the evidence-policy implementation requires a governed code change and artifact regeneration rather than a runtime strategy toggle.

### Rejected alternative for v1 — Option B: add semantic embeddings/ANN

**ELI5:** Add a scout that finds ideas that “mean similar things” even when they use different words.

**Pros**

- Better semantic recall for paraphrases and cross-domain links.
- Useful for `relatesTo` and progression pairs with low lexical overlap.

**Cons**

- Introduces an embedding model, dependency, cache, cost, and reproducibility contract.
- Requires multilingual evaluation.
- Similarity still does not prove progression direction.
- The user-set requirement currently names one shared KG LLM, not an embedding stack.

### Rejected alternative — Let an LLM nominate from large allowed cohorts

For example, show one source standard and all allowed standards in the next grade/domain, then ask for likely candidates. This is **not selectable** under the current brief because candidate generation is already **SETTLED** as deterministic, explainable, and bounded before any LLM call. Selecting this approach would first require reopening that architecture boundary.

Additional drawbacks are large cohorts, higher token cost, positional bias, weaker complete-consumption guarantees, and a less auditable candidate-discovery path.

### Historical recommendation — superseded by the settled policy above

The draft recommended explainable deterministic non-embedding retrieval. The amended settled policy keeps that technology boundary but fixes one built-in policy instead of a runtime strategy boundary.

**LIMIT — Deterministic non-embedding retrieval creates an unmeasured v1 candidate-recall ceiling.** It may miss semantically related standards with weak lexical, hierarchy, code, or LC overlap. D12 requires sample-conditioned nomination diagnostics, which do not establish curriculum-wide candidate recall; this limitation must remain disclosed.

Embedding remains outside v1. A future replacement must be governed on its own terms and does not create v1 configuration or version fields.

---

## D4. Candidate-pair orientation and adjudication shape

**SETTLED — Option A: one canonical unordered logical pair receives one unified relation/direction judgment.**

Each pair of distinct eligible endpoints appears once for candidate identity, request coverage, adjudication, failure accounting, and provenance. Encounter order cannot change pair identity, and canonical record ordering asserts no semantic direction. Before prompting, Python derives the admissible outcomes from D1, D2, and all other settled structural rules. The one judgment chooses among permitted `buildsTowards` directions, `relatesTo`, `no_relation`, and `needs_review`. Each pair appears in exactly one deterministic request; the producer returns one complete judgment and the checker accepts it or returns one complete corrected judgment from the same bounded evidence plus the draft. Python still enforces identity, coverage, endpoint containment, schema integrity, relation/direction permissions, and D6 cardinality.

This pair/judgment contract also remains stable for any separately approved future D3 candidate-policy replacement.

### Selected design background — Option A: one canonical unordered pair and one unified judgment

The candidate artifact contains one pair:

```text
{A, B}
```

The request declares which outputs are allowed. The response chooses one:

```text
A buildsTowards B
B buildsTowards A
A relatesTo B
no_relation
needs_review
```

Disallowed directions are removed before prompting and enforced by Python.

**ELI5:** Put two cards on the table once and ask, “Does A lead to B, does B lead to A, are they just meaningfully related, or neither?”

**Pros**

- Prevents duplicate pair adjudication.
- Makes relation exclusivity straightforward.
- Lets the checker compare directional and non-directional interpretations together.
- Produces one stable pair audit trail.

**Cons**

- Response schema is slightly more complex.
- Batching must preserve exact pair identity and allowed decisions.

### Rejected alternative — Option B: directed candidate records

```text
A -> B
B -> A
```

Each row is independently judged as `buildsTowards`, `relatesTo`, or none.

**Pros**

- Simple directed relationship schema.
- Similar to ordinary edge classification.

**Cons**

- Duplicates work.
- Can produce contradictory judgments.
- `relatesTo` gets evaluated twice.
- Requires a later conflict reconciler.

### Rejected alternative — Option C: separate relationship pipelines

**Pros**

- Prompts can be narrowly specialized.
- Candidate retrieval can be relation-specific.

**Cons**

- Same pair may receive inconsistent outputs.
- More artifacts, agents, retries, and cost.
- Pair exclusivity becomes a late conflict-resolution problem.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option A; settled D4 now requires one semantic decision per logical pair while retaining relation-specific nomination reasons.

---

## D5. `relatesTo` serialization

**SETTLED — Option A: store one canonical `relatesTo` row per unordered pair.**

The lower canonical endpoint `case_identifier_uuid` is serialized as source and the higher as target. This ordering is a technical identity convention, not developmental, hierarchy, document, or semantic direction. The deterministic relationship UUID uses `doc_key`, relationship type, and those canonicalized endpoints. The row appears exactly once in the standalone artifact, combined bundle, combined relationship projection, counts, and provenance. Consumers must provide undirected-neighbor lookup across both endpoint positions. Validation rejects non-canonical ordering, reverse duplicates, multiple IDs for one unordered pair, missing provenance, and count disagreement.

**LIMIT — Raw consumers that query only outgoing or only incoming rows will miss some `relatesTo` neighbors.** Consumer documentation and ingestion checks must require symmetric lookup.

### Selected design background — Option A: one canonical relationship per unordered pair

```text
canonical_pair(A, B)
  source = min(case UUID)
  target = max(case UUID)
```

Downstream code treats `relatesTo` as traversable in either direction.

**ELI5:** Friendship is written once in the address book, even though either friend can look up the other.

**Pros**

- No duplicate knowledge.
- Stable identity.
- Half the storage and counts of reciprocal rows.
- Simple pair-level audit.

**Cons**

- Consumers must know to query both source and target sides.
- The stored direction is technical, not semantic.

### Rejected alternative — Option B: emit reciprocal relationships

```text
A --relatesTo--> B
B --relatesTo--> A
```

**ELI5:** Write the same friendship in both people's address books.

**Pros**

- One-direction graph traversals find neighbors naturally.
- No special query convention.

**Cons**

- Doubles counts and storage.
- Creates two identifiers for one semantic assertion.
- Requires perfect reciprocal consistency.
- Provenance is duplicated.

### Rejected alternative — Treat `relatesTo` as semantically directed

This is **not selectable** under the current brief. The semantic relationship is already **SETTLED** as conceptually symmetric and non-directional; only its row-level serialization remains open. Treating it as semantically directed would first require reopening Section 2.2. Its stored direction would otherwise be arbitrary or prompt-dependent.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option A, which is now settled by D5 above.

**LIMIT if Option A is chosen:** Combined-graph documentation and ingestion checks must explicitly state that `relatesTo` is stored once but semantically symmetric.

---

## D6. Can one pair publish both `buildsTowards` and `relatesTo`?

**SETTLED — Option A: relationship types are mutually exclusive for one logical pair, with `buildsTowards` precedence.**

One D4 pair publishes at most one LP relationship. When evidence supports a permitted directional developmental relationship, publish `buildsTowards` only. Publish canonical `relatesTo` when evidence supports meaningful conceptual or skill coherence without a justified developmental direction. `no_relation` and `needs_review` publish nothing. Precedence reconciles semantically supported interpretations; it never lets rank, hierarchy, LC overlap, text, code, proximity, or another nomination signal create an edge. Python enforces exclusivity and every D1/D2/D5 identity and permission rule.

### Selected design background — Option A: mutually exclusive, with `buildsTowards` precedence

```text
If A meaningfully builds toward B:
    publish A --buildsTowards--> B
    do not also publish relatesTo for {A, B}
```

**ELI5:** If the relationship is “step 1 helps with step 2,” that already tells us the two steps are related; a second “they are related” label adds little.

**Pros**

- Cleaner semantics.
- Lower density.
- One pair, one audit decision.
- Easier downstream use.

**Cons**

- Loses an explicit lateral label for a pair that is both sequential and conceptually close.

### Rejected alternative — Option B: allow both

```text
A --buildsTowards--> B
A --relatesTo------> B
```

**Pros**

- Preserves every asserted interpretation.
- Matches a fully multi-relational graph philosophy.

**Cons**

- Often redundant.
- Makes counts and product behavior harder to explain.
- Requires clearer query precedence.

### Process note — Separate requests are not a D6 answer

Judging the two relationships in separate requests is an adjudication-shape choice governed by D4. It does not answer D6's publication-policy question: the final graph must still either prohibit or permit both relationship types for one logical pair. Therefore D6 has two selectable options: A or B.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option A, which is now settled by D6 above.

The unified pair response then has one published relationship at most.

---

## D7. Recurring practice and repeated skills

**SETTLED — Option C: classify recurrence by substantive change.**

A later standard that substantively deepens, extends, combines, broadens, or increases the complexity of an earlier capability may publish permitted earlier-to-later `buildsTowards`. Meaningful recurrence or reinforcement without justified developmental dependency publishes one canonical `relatesTo`. Generic wording, broad reusable LC overlap, or other non-pair-specific similarity without useful instructional coherence returns `no_relation`. Material ambiguity or contradiction returns `needs_review`. No signal decides the category automatically. Producer and checker adjudicate from the same bounded evidence; Python enforces D1–D6 and exact coverage. Curriculum instructions provide reviewed examples/counterexamples where material. `recurring_practice` may remain an internal rationale category but is not a public relationship type.

The reviewed outputs show three distinct patterns.

### Pattern 1 — Developmental extension

```text
Basic 2: decode unknown words using structural analysis
Basic 3: use prefixes, suffixes, compounds, and roots to decode unknown words
```

The later standard adds specificity or complexity.

### Pattern 2 — Meaningful recurrence/reinforcement

```text
Basic 2: demonstrate turn-taking in conversation
Basic 3: demonstrate turn-taking across different topics
```

The capability is revisited in a broader context, but dependency may be weak.

### Pattern 3 — Generic repetition

A broad LC such as “show orderliness in daily life” can support many unrelated Rwanda objectives.

### Rejected alternative — Option A: always map later recurrence to `buildsTowards`

**Pros**

- Produces rich vertical pathways.
- Learning Commons does not require strict prerequisites.

**Cons**

- Overstates simple repetition.
- Generic LCs can create false progressions.

### Rejected alternative — Option B: always map recurrence to `relatesTo`

**Pros**

- Avoids claiming dependency.
- Captures curriculum continuity.

**Cons**

- A genuine developmental extension may be under-described.
- Could make `relatesTo` a dumping ground for repeated text.

### Selected design background — Option C: classify by substantive change

```text
deepens / extends / combines / increases complexity
    -> buildsTowards

meaningful recurrence without clear dependency
    -> relatesTo

same generic capability with no useful pair-specific coherence
    -> no_relation
```

**Pros**

- Best semantic fidelity.
- Handles Ghana English and Rwanda differently.
- Keeps both relation types meaningful.

**Cons**

- Harder prompt and checker rubric.
- Requires curriculum-specific examples.
- More human evaluation work.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option C, which is now settled by D7 above.

`recurring_practice` may be retained as an internal reason/category, but it will not become a third published relationship type.

---

## D8. `buildsTowards` cycle policy

**SETTLED — Option A: the complete published `buildsTowards` graph must be acyclic.**

Any directed cycle is a release-blocking validation failure, including cycles spanning requests or batches. D2 direction/rank rules remain independently mandatory; with the initial profiles, an otherwise admissible cycle can only use same-rank edges. D4/D6 prohibit reciprocal publication for one pair, but cycle detection must handle three or more SFIs and future compatible shapes. `relatesTo` is excluded from this directed cycle check.

Deterministic Python performs whole-graph cycle detection after final reconciliation. Diagnostics identify every cyclic strongly connected component and every participating SFI/edge with stable links to pair/request/producer/checker provenance, plus deterministic representative cycle paths and reconciled component/node/edge counts. Every possible simple cycle need not be enumerated. The pipeline never silently drops, converts, or hand-edits an edge to break a cycle; it repairs the earliest incorrect stage and reruns.

### Selected design background — Option A: forbid every directed cycle

```text
A -> B -> C -> A   # invalid
```

Mutual or circular reinforcement should use `relatesTo` or one clearly justified direction.

**ELI5:** A staircase cannot lead upward and eventually return to the same step.

**Pros**

- Easy to interpret as developmental structure.
- Prevents accidental reciprocal edges.
- Supports topological ordering and prerequisite traversal.
- Strong validation rule.

**Cons**

- Some spiral or co-developed skills may not fit cleanly.
- A legitimate same-level feedback loop must be simplified.

### Rejected alternative — Option B: allow same-level cycles

**Pros**

- Represents same-grade mutual development.
- Maintains forward grade progression.

**Cons**

- Same-level cycle semantics are difficult to distinguish from `relatesTo`.
- Topological use becomes conditional.

### Rejected alternative — Option C: allow cycles and report them

**Pros**

- Maximum expressiveness.

**Cons**

- Downstream “prerequisite” traversal can loop.
- Harder to explain and validate.
- May hide direction errors.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option A, which is now settled by D8 above.

**LIMIT if Option A is chosen:** Spiral curricula may be represented less richly. Conceptual mutuality should be captured through `relatesTo` rather than cyclic `buildsTowards` edges.

---

## D9. Transitive edge policy

**SETTLED — Option A: publish only directly adjudicated `buildsTowards` edges, with no automatic transitive closure or reduction.**

An edge is publishable only when its exact pair was nominated, assigned to one D4 request, directly producer/checker-adjudicated, and accepted under D1–D8. Reachability never creates another edge. An independently accepted direct edge remains even when an alternate accepted multi-hop path connects the same endpoints. Each published edge keeps its own candidate/request/judgment/evidence/content-hash/identity/source provenance. Multi-hop reachability is neither a separate accepted judgment nor a published edge, and v1 does not measure it as candidate recall under D12. D8 applies to the complete direct graph, while `relatesTo` receives neither closure nor reduction. Validation rejects provenance-free generated edges, silently omitted accepted edges, and count mismatches. Consumer documentation distinguishes direct assertions from computed reachability.

**LIMIT — The DAG may contain both a direct edge and an alternate multi-hop path.** Consumers compute minimal or reachability views without changing the released evidence-level rows.

Suppose the graph contains:

```text
A --buildsTowards--> B --buildsTowards--> C
```

### Selected design background — Option A: publish only directly adjudicated edges

Do not automatically add `A -> C`, and do not automatically remove an independently supported `A -> C`.

**ELI5:** Record the roads the surveyor actually confirmed. A route planner can calculate multi-road journeys later.

**Pros**

- Preserves evidence-level assertions.
- Avoids graph explosion.
- Does not confuse inferred reachability with directly reviewed relationships.
- Runtime queries can compute transitive closure when needed.

**Cons**

- Consumers must traverse more than one hop.
- Similar graphs can contain both A→B→C and A→C if all are directly supported.

### Rejected alternative — Option B: materialize transitive closure

Automatically add every reachable `A -> C`.

**Pros**

- Fast ancestor/prerequisite queries.

**Cons**

- Dense graph.
- Generated edges have weaker provenance.
- “A helps B” and “B helps C” do not always prove that A directly helps C enough to publish.

### Rejected alternative — Option C: apply transitive reduction

Remove `A -> C` whenever A can already reach C through other nodes.

**Pros**

- Sparse graph.
- Highlights a minimal path structure.

**Cons**

- Can delete a meaningful direct relationship.
- Reduction may not be unique when cycles are allowed.

### Historical recommendation — superseded by the settled policy above

The draft recommended Option A, which is now settled by D9 above.

---

## D10. Unresolved Academic Standards ancestry

**SETTLED — Every `kgs.lp` profile must explicitly select one of two unresolved-participation states; all six initial profiles include every otherwise-eligible unresolved SFI with warnings.**

The required profile policy has no silent default. Missing or unknown values are configuration errors, and the selected state is all-or-nothing within one curriculum run. D10 supports no per-SFI UUID exception and no sidecar exception mechanism.

The two permitted states are:

1. exclude every SFI with unresolved self or ancestry; or
2. include every otherwise-eligible SFI with unresolved self or ancestry while propagating explicit warnings.

Under exclusion, affected SFIs cannot participate in either relationship, generate no candidate/request, and are counted in the eligibility report as policy exclusions. Exclusion takes precedence over D2's otherwise-permitted coordinate-missing `relatesTo` participation.

Under inclusion with warnings, an unresolved SFI proceeds only when otherwise eligible under D1, D2, and every other settled rule. Inclusion never nominates or approves an edge. Framework-root fallback is never positive hierarchy, topology, domain, or placement evidence. Permitted evidence is limited to trustworthy non-fallback context such as the SFI's text, valid scope/coordinate, source code/audit flags, bounded source evidence, supporting LCs, and trustworthy hierarchy paths. Invalid/ambiguous/conflicting coordinates still fail under D2. Producer and checker receive the same explicit warning and bounded evidence. Unresolved status remains visible through eligibility, candidates, requests, judgments, final claims, relationship provenance, summaries, and validation.

The initial profile matrix is:

| Curriculum          | Unresolved-participation state                               |
|---------------------|--------------------------------------------------------------|
| Madhi mathematics   | Include all otherwise-eligible unresolved SFIs with warnings |
| Nigeria mathematics | Include all otherwise-eligible unresolved SFIs with warnings |
| Pratham science     | Include all otherwise-eligible unresolved SFIs with warnings |
| Rwanda mathematics  | Include all otherwise-eligible unresolved SFIs with warnings |
| Ghana mathematics   | Include all otherwise-eligible unresolved SFIs with warnings |
| Ghana English       | Include all otherwise-eligible unresolved SFIs with warnings |

The selected state is part of the effective-config content hash. A change invalidates stale eligibility, candidate, request, response, final-claim, relationship, and combined-bundle reuse. Validation reconciles unresolved eligible/excluded counts, verifies warning/provenance propagation, and rejects fallback-root placement as positive evidence.

**LIMIT — The profile-level state cannot distinguish a strong individual unresolved case from a weak one.** Inclusion may admit unresolved SFIs with insufficient non-fallback evidence, while exclusion may omit usable SFIs. Producer/checker adjudication reduces but does not remove this all-or-nothing weakness, and D12's fallible downstream judge diagnostics do not remove it or impose an automatic semantic score gate.

Ghana math contains 13 unresolved root-fallback hierarchy edges; Ghana English contains 2. A passed AS validation report therefore does not mean that every SFI has trustworthy curricular placement.

### Permitted runtime state — exclude every SFI with unresolved self or ancestry

**Pros**

- Conservative.
- No progression inference uses compromised hierarchy context.

**Cons**

- Can omit standards whose text, grade, code, and LCs are still sufficient.
- Ghana coverage decreases.

### Permitted runtime state — include otherwise-eligible unresolved SFIs with warnings

**Pros**

- Better coverage.
- Other signals may compensate.

**Cons**

- LLM may overtrust a misleading root placement.
- Harder to explain why some uncertain standards received edges.

### Rejected historical design — per-SFI reviewed eligibility exceptions

Under this rejected design, a reviewed exception would identify the SFI, reviewer, review time, rationale, and permitted evidence. This design is not part of v1.

The rejected design considered two possible storage locations:

| Sub-option | Representation                                                                                                    | ELI5                                                              | Advantages                                                                                        | Costs                                                                                                                |
|------------|-------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| **C1**     | Inline `kgs.lp` list keyed by final SFI `case_identifier_uuid`, with reviewer/time/rationale/evidence permissions | Keep each signed exception card inside the curriculum's LP policy | Smallest v1 surface; one effective-config content hash; straightforward deployment and validation | Mixes general policy with a small number of instance decisions; UUIDs must be revisited if upstream identity changes |
| **C2**     | Separate reviewed sidecar referenced by `kgs.lp` and covered by its material-content hash                         | Keep exception cards in a separate audited binder                 | Cleaner policy/data separation; scales better if exceptions grow                                  | Adds a file lifecycle, path/schema validation, deployment, and review surface                                        |

**Pros**

- Safe default with a controlled escape hatch.
- Mirrors the current LC unresolved-context philosophy.
- Audit-friendly.

**Cons**

- Requires manual review for exceptions.
- Exception selectors must be stable and included in the material effective-config content hash.

### Historical recommendation — rejected by the settled two-state profile policy

The earlier C1 recommendation is rejected. V1 has no inline UUID exception list and no exception sidecar.

Under the settled policy, eligibility and candidate artifacts always retain unresolved status for every included unresolved SFI, and the profile-wide state participates in the effective-config content hash.

No D10 exception payload exists. Step 3 implements the required two-state profile policy and the six initial inclusion-with-warning selections above.

---

## D11. Attribution and ownership for inferred progression edges

**SETTLED — Option B with exact pipeline authorship/provider values, explicit source-license inheritance, and an inference-disclosing attribution template.**

Every `Relationship` requires `author`, `provider`, `license`, and `attribution_statement`. Current `hasChild` edges inherit all four values from framework metadata. Current `supports` edges, like their Learning Component source nodes, instead use the pipeline constants `author = "LLM generated"` and `provider = "IDinsight"` while inheriting the framework `license` and `attribution_statement`. LP edges are different from explicit source-framework relationships: they may be inferred by this pipeline rather than explicitly authored by the ministry or curriculum publisher.

### Rejected alternative — Option A: inherit all framework metadata unchanged

**Pros**

- Simple.
- Consistent with current relationships.

**Cons**

- Can misleadingly imply that the source authority authored the inferred progression.
- Makes it difficult to distinguish source content from pipeline analysis.

### Selected option — Option B: configured LP attribution/ownership with code-owned integrity

Every one of the six initial curriculum profiles explicitly supplies the author, provider, attribution template, and approving identity in required `kgs.lp` configuration. Python universally owns source-license inheritance, template-substitution validation, provenance capture, and validation. The final variable field names and schema layout are implementation details for Step 3; the following values and behavior are authoritative:

| Policy element                              | Settled value or behavior                                                                                                                                                                                                                                   |
|---------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Configured `author`                         | Exact string `LLM generated`, matching existing Learning Components.                                                                                                                                                                                        |
| Configured `provider`                       | Exact string `IDinsight`, matching existing Learning Components.                                                                                                                                                                                            |
| Code-owned `license` inheritance            | Copy the validated source-framework `license` value verbatim for the current curriculum. There is no fallback or generic default; a missing or blank upstream value is a validation error.                                                                  |
| Configured `attribution_statement` template | Exact text: `Learning progression relationship generated by IDinsight using an LLM producer/checker workflow. Source framework attribution: {source_attribution_statement}. This inferred relationship was not stated or endorsed by the source publisher.` |
| Code-owned template substitution            | Replace the single `{source_attribution_statement}` token with the validated source framework's `attribution_statement` verbatim. No other runtime substitutions are permitted. A missing or blank source attribution statement is a validation error.      |
| Configured approving identity/role          | `IDinsight` as the organizational approver for the author, provider, source-license inheritance policy, attribution template, and required provenance.                                                                                                      |

Source-license inheritance and the required provenance set are explicit code-owned invariants rather than runtime choices. `kgs.lp` has no `license_source`, retained-provenance switch, or fingerprint-category selector. Option B remains distinct from Option A and Option C because the configured LP relationship author, provider, and attribution statement identify and disclose the inference instead of presenting the relationship as a source-publisher assertion.

Internal provenance for every published LP relationship retains all of the following:

- source-framework UUID and title;
- source-framework author, provider, license, and attribution statement;
- source and target SFI `case_identifier_uuid` values;
- candidate ID and evidence summary;
- producer and checker request, judgment, and outcome references;
- upstream AS+LC bundle content hash;
- effective LP configuration, candidate/evidence artifact, and request content hashes; and
- producer/checker prompt content hashes and actual model-settings identifiers.

Deterministic finalization, not the producer or checker, attaches these values. Validation requires exact author/provider strings, exact source-license equality, exact template expansion, complete provenance, and consistency across the standalone LP relationships, combined bundle, and relationship projection.

**Pros**

- Honest distinction between source and inference.
- Matches the Learning Commons pattern where the progression author and graph provider can differ.
- Clear release/legal boundary.

**Cons**

- Requires explicit organizational and licensing decisions.
- Retains explicit attribution/ownership configuration even though the six initial profiles share the same values.

### Rejected alternative — Option C: mixed source-author/pipeline-provider metadata

For example, source authority as `author`, pipeline as `provider`, and inference disclosure only in attribution/metadata.

**Pros**

- Preserves source ownership prominence.

**Cons**

- `author` remains ambiguous for an inferred assertion.
- Different consumers may interpret it differently.

### Settled decision

Use the selected Option B policy exactly as specified above. Changing the author, provider, source-license inheritance rule, attribution wording or substitution rule, organizational approval, or required provenance set reopens D11.

**LIMIT — This brief is not legal advice.** IDinsight's organizational approval is recorded above; IDinsight remains responsible for confirming that copying each source-framework license to inferred LP relationship records is legally and organizationally appropriate.

---

## D12. LLM evaluation and release policy

**SETTLED — Required independent LLM-as-judge evaluation execution and reporting, initially without an automatic semantic score release threshold.** The user selected S1 and R1 as CLI-configurable defaults, a dedicated evaluator model variable, removal of evaluation budget ceilings, the explicit execution settings below, and A1. These policy decisions were followed by the user's separate implementation approval on 2026-09-11, recorded in Section 3.3.

Production completion remains governed by D1–D11 and D13: structural/process validation, producer/checker reconciliation, and zero unresolved processing failures. `needs_review` stays visible, nonpublishing, and nonblocking. The production pipeline does not read evaluation outputs or wait for a semantic score. Separately, the numbered project cannot complete Step 27 or advance through Steps 28–29 without the required evaluation execution, accountable reports, and independent review. This is an evaluation-process requirement, not a claim that scores prove pedagogical correctness.

No human gold-set creation, human labeling sample, or pre-release human semantic examination is a prerequisite. The existing optional post-release human audit remains available and nonblocking for the original release. Such audits retain population/sampling, reviewer/time, findings/rationale, affected IDs, and released artifact/config content hashes; IDinsight retains its existing organizational audit/remediation authority. The user selected A1: IDinsight, acting through the project user, dispositions evaluator concern groups under D12-A.

### D12.1 Architecture and ownership

Use `backend/src/kgfeg/entries/evaluate_lcs.py` and `backend/src/kgfeg/evals/lc_eval/` as architectural references only. The inspected LC implementation separates CLI orchestration, typed records, sampling, prompts, judge calls/cache, and scoring. LP must not copy LC assertions-as-`is_true` labels, real distractors-as-negatives assumptions, precision/recall names computed against production assertions, or item/replicate-only cache reuse without complete material-input validation.

The coding role authors the executable evaluation support and its evaluation-only configuration surface:

```text
backend/src/kgfeg/entries/evaluate_lps.py
backend/src/kgfeg/evals/lp_eval/
  __init__.py
  schemas.py
  sampling.py
  prompts.py
  judge.py
  scoring.py
```

The entry point orchestrates explicit snapshot selection, preparation, judging/resume, and reporting. The five modules own typed contracts; population/strata/sampling; blind/critique/control prompt rendering; validated bounded calls and caching; and deterministic scoring/reporting, respectively. Existing reusable utilities may be called without changing production behavior. Step 27 adds the evaluator-only `LLM_LP_EVAL_JUDGE_MODEL` setting and `lp_eval_judge` model binding; production continues to use `LLM_KG_MODEL`. No `kgs.lp` selector, candidate-policy modification, or production prompt/config tuning is part of this amendment.

A separate testing-role task independently derives the oracle, authors deterministic tests and synthetic fixtures, red-teams the harness, and executes the approved evaluation only after deterministic validation succeeds and the user separately authorizes live calls. For this step, the user establishes the exact candidate commit after deterministic testing and before live execution so the required evidence binds to that SHA/tree; the final reviewer gate still follows complete execution/reporting. Coding cannot author those tests; testing cannot repair executable harness source, prompts, or evaluator configuration. The read-only reviewer evaluates both work products and execution evidence. Harness authors may run existing tests but may not supply the independent testing verdict.

### D12.2 Three complementary components

1. **Production-pair assessment.** Sample published `buildsTowards`, published `relatesTo`, final `no_relation`, and final `needs_review` pairs, retaining checker accept/correct and producer-to-final changes as audit strata. First make a blind independent classification in a fresh context: permitted `buildsTowards` direction, `relatesTo`, `no_relation`, or evaluator `ambiguous`, with specific evidence references and explanation. Use the blind-classification evidence view defined in D12.3: hide production decision, rationale, confidence, correction, nomination recommendations/rankings, publication status, and sampling/control labels, while retaining permitted factual evidence and policy permissions. Freeze this response before a separate call critiques the operative production rationale using the original-production critique view, not the redacted blind view. The critique cannot revise the blind judgment or be fed back to it, and the blind judge's answer is not supplied to the critic. Assess relationship support separately from rationale grounding; a plausible relationship may have an unsupported explanation and a well-grounded explanation may remain semantically ambiguous. For a corrected pair, critique the operative corrected rationale and retain the original draft for correction diagnostics; any extra critique calls must be explicit in the materialized evaluation schedule.
2. **Independent upstream-pair assessment.** Construct a reproducible population of distinct, same-framework pairs from the fixed eligible AS+LC population using D1/D2/D10 admissibility. Do not restrict selection to candidates, published edges, production negatives, or judge-positive discoveries. Freeze selection and the common bounded-upstream evidence payloads before joining candidate/adjudication/publication metadata. Every independently sampled pair, nominated or not, receives the same `reconstructed_bounded_upstream` base condition under D12.3; production evidence availability must not select its condition or alter its payload. Include both a uniform probability sample and an upstream-feature-stratified diagnostic sample so low-signal pairs have a sampling route. Use bounded sampling/indexing rather than an all-pairs LLM pass. Afterwards identify judge-positive pairs never nominated, nominated but finalized as `no_relation` or `needs_review`, or published with differing relation/direction. Preserve overlapping cohort membership; do not exclude nominated pairs merely to inflate misses. Policy-excluded pairs are outside this estimand, not negatives.
3. **Evaluator checks.** Include clearly constructed synthetic controls, endpoint/evidence presentation-order changes, repeated fresh judgments, limited deterministic baselines, and paired evidence-sensitivity comparisons. Controls target an explicit synthetic truth or planted rationale defect; they never turn an unasserted real pair into a known negative. Include synthetic developmental extension, substantive nondirectional coherence, unrelated concepts, insufficient/contradictory evidence, and invented rationale evidence. Keep control expectations hidden from the judge and separate from real-population summaries. Baselines are evaluation-only comparisons (constant `no_relation` and a bounded lexical ordering diagnostic), never graph publishers or semantic truth. A weak control or unstable judge result is a reported quality concern, not an execution failure unless the request/output contract itself failed.

All curricula must appear in each applicable component. Sampling must include same/cross/missing-rank cases; repeated or highly overlapping text; absent, shared, nonshared, and broadly reused LC evidence; DAG paths and unresolved ancestry; checker corrections; and truncated/bounded production evidence. Counts must expose absent strata, exhausted strata, and unsupported comparisons. Zero available cases is not evidence of good performance and must not be filled with invented real examples.

### D12.3 Evidence conditions and interpretation

**Exact production evidence** is the immutable original bounded production request material and relevant production policy/instructions for the assessed pair, with its original request/batch context, factual nomination evidence, limits, warnings and omissions preserved. It is not the redacted blind payload. Never silently reconstruct it from a newer upstream graph or supplement it with omitted source content. Missing or mismatched original material is an input/execution failure.

For production-pair assessment, two distinct views are required:

- **Blind-classification view:** derive this from the original bounded evidence, hiding production conclusions/rationale/confidence/corrections, publication status, sampling/control labels, and nomination recommendations/rankings. Preserve permitted factual evidence even when it is stored inside nomination context: for example, overlap counts, observed similarities, retained supporting references and their original aggregate-scope/omission warnings. Present those values as evidence, not as recommendations or proof of a relationship. Record the field-level retained/redacted mapping and hash the actual rendered payload. Do not discard an entire nomination record merely because it combines facts with recommendations.
- **Original-production critique view:** after the blind classification is validated and frozen, supply the original bounded production evidence and operative production rationale to a fresh critic context. Preserve factual nomination values and the original request boundary so the critic can assess what production actually received. Do not use the redacted blind payload as a substitute. Assess grounding separately from relationship support; a nomination recommendation cannot itself justify a relationship. The critic does not receive the independent classifier's answer, and its output cannot change that answer.

Use distinct component/view/request identities, rendered-payload hashes, response schemas and cache records for classification and critique. Reports identify which view supports each assessment. A factual claim supported by the original bounded request must not be marked unsupported merely because classification redacted its surrounding nomination metadata. Conversely, a claim requiring omitted evidence cannot be rescued with expanded upstream evidence when scoring original-production grounding.

**Expanded upstream evidence** means separately bounded retrieval from the same fixed AS+LC/provenance snapshot, with explicit selection order, limits, included references and omissions. It may expose additional trustworthy paths, supporting LCs and preserved source snippets, but cannot rerun PDF extraction or invent evidence. Root fallback never becomes positive hierarchy evidence. Evaluate this in a separate fresh context and identity, blind to both production and earlier judge responses.

**Common independent-sample evidence:** every independently sampled pair uses `reconstructed_bounded_upstream` as its base condition, whether nominated, rejected, unresolved, or published. Construct all such payloads from the fixed eligible AS+LC/provenance snapshot and policy using one deterministic evidence-selection procedure, the fixed production evidence limits, canonical stable ordering, and the same field/omission rules within each curriculum. The constructor must not read nomination/adjudication/publication status, original production requests, or nomination records. Any factual summaries included are recomputed from upstream evidence by the same rules for all pairs. Freeze payloads and their material hashes before joining production metadata for reporting; the join cannot change evidence, prompts, or judgments. A never-nominated pair has no exact production request, and this common reconstructed condition must never be called exact production evidence even when a nominated pair has an original request.

Independent-cohort diagnostic variants use that same common base and the same variant-construction rules regardless of nomination status. Exact-production assessment remains in the separate production component or an explicitly scheduled paired comparison; it cannot replace an independent-cohort base judgment. If a pair belongs to both cohorts, retain both component memberships and their separately identified evidence-condition judgments rather than deduplicating away either obligation. Evidence removal comparisons (LC removed and trustworthy hierarchy context removed, with warnings retained) are separate blind-classification conditions, not edits to the production snapshot or substitutes for original-production critique. Remove the selected evidence family from every representation in that comparison, including derived nomination facts, overlap counts and supporting references; do not leave the supposedly removed signal in another field. Retain warnings about uncertainty/omission without reintroducing the removed factual evidence.

Report these distinctions explicitly:

- Pipeline assertions and producer/checker results are comparisons, not ground-truth labels.
- Agreement, relation/direction disagreement, and judge-supported assertion rates are not semantic precision/recall.
- For each independent cohort and replicate, base nomination coverage is the number of valid judge-positive pairs that were nominated divided by all valid judge-positive pairs judged under the common `reconstructed_bounded_upstream` condition. Join nomination/publication outcomes only to these frozen assessments. Separately report published matching relation/direction coverage and the nominated-but-rejected/unresolved categories against that same judge-positive denominator. Additional variant coverage may be reported only within its own preselected cohort/condition/replicate using the same evidence-construction rules for nominated and never-nominated pairs. Never derive overall nomination coverage by separating evidence conditions according to nomination status or pooling original-production judgments with reconstructed judgments. These are sample-conditioned diagnostics, not automatically curriculum-wide candidate recall.
- Semantic ambiguity is a valid outcome, distinct from `no_relation`, a production `needs_review` label, and a malformed/missing evaluator response.
- Relationship support and rationale grounding have separate counts and denominators; no composite quality score hides either.
- Evidence-condition differences may indicate production evidence limits, upstream error, or judge variability. They do not by themselves prove the earliest defect or authorize a production change.

Retain each replicate and the full outcome distribution, not only a majority/representative answer. Report raw numerators/denominators per curriculum, cohort, stratum, relation/direction, evidence condition, and correction status; planned/attempted/valid/failed/ambiguous/excluded/unavailable counts; shortfalls and overlap; all disagreement records; and missing evidence. Do not pool diagnostic oversamples into population estimates without valid inclusion probabilities and a separately justified estimator. Repeated judgments are not independent curriculum samples. Zero denominator produces an explicit unavailable value and reason, not 0% or 100%. Any uncertainty estimate must state its sampling unit, method, assumptions, and limits; no unapproved semantic passing score is inferred.

### D12.4 Input, call, cache, and reporting integrity

Before the first evaluator call, freeze and validate all six input manifests, eligibility/pair populations, sample plan, conditions, prompt/schema/config/model settings, and full bounded request schedule. Link snapshots to Step 26 reviewed source/config/code/artifact hashes. Use explicit paths, not mutable latest-run discovery. Copying or content-addressed references must detect source changes before use; never read a live growing production run as a frozen snapshot. Reject aliased output paths that could overwrite inputs.

Cache identity must bind actual snapshot/evidence/request material, endpoint identity, component/condition, rendered prompt and response schema, effective judge provider/model/settings, replicate, presentation order, and the original production request/policy and operative production rationale supplied to its critique, plus the explicit classification-versus-critique view identity. Critique execution depends on completion of the associated blind classification, but its prompt does not contain that classifier's answer. Seed or model-name-only keys are insufficient. Validate cache records against the current exact schedule and content; reject stale, truncated, duplicated, extra, or mismatched records rather than silently accepting the last row. Reuse only fully validated successes. Changing scoring alone may regenerate reports from identical valid judgments, with the new scorer source hash recorded.

Validate exact request IDs, one complete output per scheduled pair/task, no missing/extra/duplicate endpoints or fields, allowed relation/direction, bounded enum/range values, expected condition/replicate/order, and evidence-reference containment. No partial or malformed response enters successful judgments. Output-format retry/repair calls count as attempts and usage. Prior successes survive an interruption, and resume does not repeat them. Failures and their later dispositions remain separate append-only audit evidence.

**Execution failure** includes missing/stale inputs, invalid request/output, transport errors after the configured retry limit, unaccounted scheduled judgments, or unreproducible/missing reports. Required evaluation cannot be marked complete with an unresolved execution failure; partial diagnostic reports remain clearly incomplete. This status is separate from production D13 and never rewrites `kg_run.json` or production validation. Honest exhausted/empty-stratum accounting under the selected sampling plan is not a transport failure or a fabricated success.

**Semantic ambiguity** is a valid judge assessment of insufficient or contradictory evidence. Retain and report it without converting it to failure or negative.

**Reported quality concern** includes production/judge disagreement, unsupported rationale, missed sampled judge-positive pairs, poor synthetic-control behavior, presentation sensitivity, and replicate/evidence-condition instability. These do not automatically fail a semantic threshold. Record their disposition under D12-A; deterministic harness defects still fail independent engineering review.

Usage accounting includes every attempt, valid/failed response, retry, component, curriculum, condition and model; input/output and available reasoning/cache token counts; observed cost when available; and unknown usage explicitly. The user removed evaluation budget ceilings: do not implement dollar, total-token, or total-attempt caps, cost-reservation machinery, or a required cost-estimate approval gate. Missing pricing or usage is recorded as unknown rather than zero and does not itself block evaluation completion. Sampling, repetitions, evidence limits and finite retry behavior still bound the scheduled work. No automatic model fallback, sample shrinkage, quota substitution, or unrequested repeated full evaluation is allowed.

### D12.5 SETTLED — Concrete policy packet

The user selected the following policies on 2026-09-11 and separately confirmed the operational settings. Earlier alternatives are superseded, not selectable presets. Sampling and repetition counts are operational evaluation controls, not semantic release thresholds. CLI overrides within the rules below are authorized configuration choices; changing a selection algorithm, label meaning, required component, or interpretation contract still requires governance.

**D12-S — S1 sampling defaults, configurable through the CLI.**

Per curriculum, the default samples up to 15 pairs uniformly without replacement within each of the four production outcomes (60 base). Supplement to at least 2 examples for each of the 12 tags below, adding at most 24 distinct pairs. Independently select 36 uniform admissible upstream pairs plus 3 per each of the 12 upstream tags (at most 72 draws before deduplication).

The CLI must expose the following settings; their final flag spellings are delegated to Step 27 and must be documented in CLI help and Step 28 documentation:

| CLI setting | Default | Contract |
|---|---:|---|
| Production pairs per outcome | 15 | Positive integer target for each of the four outcomes in each curriculum |
| Production examples per diagnostic tag | 2 | Positive integer minimum, including already-selected examples |
| Independent uniform pairs | 36 | Positive integer target per curriculum |
| Independent pairs per upstream tag | 3 | Positive integer target per tag per curriculum |
| Sampling seed | 20260911 | Explicit integer used for all deterministic selection and ordering |

These settings apply uniformly to the curricula selected for the invocation. Missing settings use the documented S1 defaults; CLI values override those defaults and appear in the effective evaluator configuration, schedule and reports. Required Step 27 evidence covers all six curricula, even if invocations are separated. Invalid values fail before calls. Empty/exhausted populations produce counted shortfalls, not fabricated samples. There is no CLI switch that marks a skipped component as completed.

Selection uses canonical UUID sorting before seeded selection, without-replacement sampling within each cell, stable listed tag order for supplements, no reallocation of exhausted quotas, and deduplication with all selection routes retained. A supplement adds only the number needed to reach its configured target. Its maximum additional population is the number of tags multiplied by that target.

The four base outcomes are published `buildsTowards`, published `relatesTo`, final `no_relation`, and final `needs_review`. The 12 production tags are: checker correction; same rank; different valid ranks; missing coordinate; equal normalized SFI text; unequal SFI text with token Jaccard at least 0.5; at least one endpoint without supporting LCs; shared exact LC; both with LCs but no shared LC; a supporting LC linked to at least 10 eligible SFIs; unresolved self/ancestry; and production evidence truncation. Upstream tags substitute multi-parent DAG context for checker correction and reconstructed-evidence truncation for production truncation.

Normalization is Unicode NFKC, casefold, whitespace collapse; token sets are Unicode alphanumeric runs with no stopword removal, and empty-set similarity is zero. These are sampling tags, not pedagogical labels or production policy changes. Independent tag selection reads only upstream material and fixed policy, not nomination/results. Exact probability calculations are required for the uniform cohort; diagnostic cohorts report selection routes and counts without population estimates.

**D12-J — Dedicated LP evaluation judge.**

Step 27 adds the exact evaluator-only assignment below to the repository-root `.template.env` and local `.env`, using standard environment assignment syntax:

```dotenv
LLM_LP_EVAL_JUDGE_MODEL=anthropic:claude-opus-5
```

This governance task records the requirement; it does not edit either environment file. The later coding task must preserve unrelated environment entries and secrets, never print the full `.env`, and never stage or commit secrets. The local `.env` assignment is local configuration, not a tracked review artifact.

Add the corresponding settings field and `lp_eval_judge` binding in `backend/src/kgfeg/config.py`, following the LC evaluator architecture. Resolve the judge through `Settings.llm_config("lp_eval_judge")` and existing `kgs_settings("learning_progressions")`. The user approved reusing the existing shared model-registry settings: inherit the configured shared output-token limit and provider-specific effort/thinking settings, rather than inventing a second set of LP evaluator knobs. Those are per-request model settings, not aggregate evaluation budget ceilings.

Do not make the evaluator variable required by production startup or by AS/LC/LP execution. An unset/blank evaluator variable is an error when the evaluator resolves its judge; production must continue to resolve `LLM_KG_MODEL` independently. Do not change the LC evaluator binding or production registry behavior. Include compatibility tests proving that absence or change of the evaluator setting cannot alter production model selection.

Record the actual provider/model and complete effective non-secret model settings in the frozen run manifest and material cache identities. The requested model identifier is a user choice, not evidence of provider availability or SDK compatibility. Verify compatibility during Step 27 preflight; if unsupported or unavailable, report the configuration/execution failure without silently substituting another model. Any model change requires explicit user direction and a fresh material-bound schedule/cache identity.

Expanded evidence doubles each positive production count/text/depth limit, retains mandatory warnings and references, and uses canonical stable ordering for additional paths/LCs/snippets. Any unchanged or unavailable evidence is recorded. Fresh contexts and blind prompts remain required even when production and judge models differ.

**D12-R — R1 defaults, configurable through the CLI.**

The default makes one base blind judgment per selected real pair in each component's required base condition and one separate operative-rationale critique per selected production pair. A pair selected in both components receives both the production-evidence blind judgment and the common reconstructed-upstream judgment, with separate identities; pair deduplication does not collapse these distinct conditions. Per curriculum, select up to 12 production and 12 independent pairs by seeded uniform selection within each cohort, without using judge results, for diagnostic repetition and evidence comparisons.

Each diagnostic pair receives two additional identical-presentation base judgments (three total by default), one endpoint-swapped presentation, one reversed evidence-list presentation, one expanded-evidence judgment, one LC-removed judgment, and one trustworthy-hierarchy-removed judgment. All variants are separate fresh calls; remap displayed endpoints to canonical identities before comparison.

The CLI must expose these settings with the documented R1 defaults; final flag spellings are delegated to Step 27:

| CLI setting | Default | Contract |
|---|---:|---|
| Base blind replicates | 1 | Positive integer per selected real pair |
| Operative-rationale critique replicates | 1 | Positive integer per selected production pair |
| Diagnostic pairs per cohort | 12 | Positive integer target in each cohort/curriculum |
| Additional identical-presentation diagnostic replicates | 2 | Nonnegative integer; base plus additional must be at least 2 |
| Replicates per presentation/evidence variant | 1 | Positive integer for each of the five variants |
| Synthetic cases per control family | 5 | Positive integer target per family/curriculum |
| Replicates per synthetic control | 3 | Positive integer |
| Lexical baseline top-k | 10 | Positive integer, capped by the available sampled population |

Resolve overrides before schedule materialization; record every effective value in config, manifest and reports. Counts may change, but required conditions, control families, blind-before-critique separation and honest reporting cannot be disabled. Changed schedules cannot silently reuse records with mismatched material identities; genuinely unchanged requests may reuse fully validated successes. Repeated judgments need not be odd because no majority erases disagreement.

The five D12.2 control families produce 25 cases per curriculum at default settings. Rationale-defect controls use the critique schema and the other controls use classification. Coding specifies explicit synthetic expectations as harness control definitions; testing independently challenges those definitions and authors separate regression fixtures. Score constant `no_relation` on all real sampled pairs and lexical top-k pair rankings by SFI token Jaccard within each cohort, with canonical UUID tie-breaking, without additional LLM calls. Lexical ranking is a comparison with judge outcomes, never an asserted relationship/direction. No automatic control-detection threshold applies.

Rationale grounding is reported separately as grounded (all material claims supported by cited shown evidence), partially_grounded (some but not all material claims supported), unsupported (no material claim supported or the central justification contradicted), or ambiguous (shown evidence cannot resolve grounding). Relationship classification remains the independent blind assessment; these categories are not combined into a pass/fail score. Exact enum spellings are an implementation detail; meaning and per-category reporting are policy.

**D12-B — No aggregate evaluation budget ceilings; finite operational execution.**

The user explicitly removed dollar, total-token, and total-API-attempt budgets. Do not add mandatory budget configuration, budget-based termination, cost reservations, spending-ceiling approvals, or a required pricing/estimated-cost preflight gate. Usage and available cost reporting remain required; unavailable accounting must be explicit.

The user separately approved these execution settings: concurrency 4; at most 2 retries after the initial attempt per scheduled judgment; 180-second timeout per attempt; retry waits of 5 then 20 seconds. Provider/SDK and output-validation retries count within that same per-judgment limit rather than multiplying it. Retries are permitted for timeout, HTTP 429/5xx, or invalid structured output; invalid inputs, auth/config failures, and stale cache fail immediately. The evaluator remains bounded by its explicit sample/condition/replicate schedule, evidence limits and finite retries, without an aggregate spending budget.

Live evaluation still needs explicit user authorization for the fixed-input execution and effective settings; recording policy or approving implementation is not itself authorization to call an external LLM. Do not reintroduce budget approval as a condition of that authorization.

**D12-A — A1: project-user disposition of quality concerns.**

IDinsight, acting through the project user, records `acknowledged`, `investigate`, or `remediation_requested` for reported concern groups before Step 27 completion. The reviewer checks completeness, integrity, and faithful disclosure; the user need not label pairs or endorse semantic correctness.

Group dispositions retain affected pair/condition IDs, authority, timestamp, rationale and report hashes. No score automatically mandates remediation or release rejection, no execution failure can be waived as a quality concern, and confirmed specification defects still trigger earliest-owner remediation/review. A future semantic threshold or gold-set gate requires another approved governance amendment.

### D12.6 Build order, remediation, and limits

Step 24 is production D12 release-policy conformance, reassessed under this amendment to distinguish the independent Step 27 obligation. Step 25 remains the structural/process matrix and is revalidated with Step 24. Former Step 27 full runs move to Step 26 without losing any source/config/provenance/count/collision/checkpoint/reuse/D13 checks. New Step 27 implements, independently tests, and executes this harness on the six reviewed Step 26 snapshots. Step 28 documents both kinds of evidence; Step 29 reviews them separately.

Evaluation never edits graphs or tunes the production candidate policy, prompts, or configurations. A concern is not automatically a confirmed production defect. Once independently confirmed, D14 requires an authorized earliest-stage repair, affected production rerun, new fixed evaluation inputs and evaluation rerun; old reports remain bound to old snapshots. Candidate-policy replacement still follows D3 governance and deletion/regeneration rules. No active run may be stopped or restarted by this amendment.

**LIMIT — Required evaluator execution is not independent empirical truth.** The judge may share model biases with production, misclassify pairs, overvalue plausible rationales or synthetic controls, and respond to presentation/evidence changes. Independent prompting is not guaranteed independence of model errors.

**LIMIT — Sampling and bounded evidence leave curriculum-wide quality and candidate recall unproven.** Uniform samples can contain few positives; feature strata are selective; synthetic-control expectations apply only to their constructed cases. Report small/empty denominators, disagreements, ambiguity and evidence omissions. No human gold set or automatic semantic score threshold is introduced, and semantically incorrect or incomplete LP graphs can still satisfy this process.

---

## D13. Failed-request tolerance and release policy

**SETTLED — Option A: any failed pair halts the LP phase after permitted retries/recovery, with prefix-safe local checkpointing and resume.**

A valid `buildsTowards`, `relatesTo`, `no_relation`, or `needs_review` judgment is not a processing failure. A timeout, malformed response, exhausted retry path, missing/extra pair coverage, endpoint leakage, illegal relation/direction, or other unresolved producer/checker integrity violation is a failure. Once any pair fails, LP halts. There is no failed-pair count/rate tolerance, exploratory exception, or per-profile threshold. Checkpoints and failure evidence remain inspectable, but the run cannot report successful LP or combined release status.

The producer/checker retry counts are runtime configuration because they are operational choices. Checkpoint shape, validation-before-write, separate failure storage, stale-input rejection, prefix reuse, and earliest-unfinished-stage resume are universal Python invariants. `kgs.lp` has no checkpoint, resume-mode, reuse-prefix, fingerprint-mismatch, or fingerprint-selection fields.

Before the first external LP LLM call, the pipeline validates and writes the complete deterministic candidate population to `lp_candidate_pairs.jsonl`, its summary/material content hashes, and the complete bounded request sequence to `lp_generation_requests.jsonl`. Candidate/request counts, IDs, order, coverage, and content hashes must reconcile exactly; execution never begins from an in-memory-only or partially materialized population.

Successful checkpoints are written in deterministic request order as validated contiguous prefixes:

- producer drafts enter `lp_generation_draft_responses.jsonl` only after schema, request-ID, pair-coverage, endpoint, and material-input validation;
- checker verdicts enter `lp_generation_validation_verdicts.jsonl` only after the corresponding draft and complete verdict validate;
- reconciled judgments enter `lp_generation_responses.jsonl` only after the complete request has one valid final judgment per pair; and
- failures are written separately to `lp_generation_failures.json`; partial or failed responses never enter successful checkpoint files.

Concurrent/out-of-order completions must be buffered or otherwise serialized without reusable prefix gaps.

With `overwrite=false`, the pipeline reloads and validates candidates, requests, every successful checkpoint prefix, the failure record, and hashes/identifiers derived from the actual material upstream bundle, effective config, prompts, model settings, requests, and stored artifacts. It reuses fully validated prefixes without repeating completed calls and resumes at the earliest unfinished producer/checker stage for the first incomplete request. A valid saved producer draft may therefore be reused when the checker failed. Gaps, duplicates, out-of-order rows, truncation/invalid JSONL, misalignment, or stale material inputs fail closed. Material changes regenerate the affected deterministic stages before calls resume. Prior failure records remain audit evidence and the resumed run records later disposition. Candidate-policy replacement is not a resume case: affected artifacts are deleted and regenerated under D3.

This contract applies identically to all six curricula.

A valid `no_relation` or `needs_review` response is not a request failure. Failures are timeouts, malformed outputs, exhausted retries, missing pair coverage, or integrity violations.

### Selected design background — Option A: any failed pair fails the LP run

**Pros**

- Complete processing guarantee.
- Simple release meaning.

**Cons**

- Brittle for large runs.
- One unresolved transient model failure blocks successful LP/combined release status, while validated checkpoints and diagnostics remain available for resume and audit.

### Rejected alternative — Option B: configurable dual guard

Support both:

```text
maximum failed-pair rate
maximum absolute failed-pair count
```

The phase writes failures and continues while inside both limits. The six example release runs must still finish with zero failed pairs.

**Pros**

- Resilient during development.
- Prevents a low rate from hiding hundreds of failures in a large graph.
- Release policy can be stricter than runtime tolerance.

**Cons**

- A technically passing exploratory graph may be incomplete.
- Requires clear publication checks.

### Rejected alternative — Option C: always continue and report failures

**Pros**

- Maximum robustness.

**Cons**

- Completeness can degrade silently.
- Weak release contract.

### Historical recommendation — withdrawn and superseded by settled Option A

The earlier Option B recommendation is rejected. V1 has no failure-tolerance rate/count fields or per-profile thresholds.

Confidence should be stored for audit, but a numeric confidence threshold must not bypass the checker. Acceptance is based on a valid final judgment and policy, not confidence alone.

The complete D13 payload is the zero-tolerance halt, pre-call materialization, checkpoint, failure-artifact, and resume contract above.

---

## D14. Manual semantic edge overrides

**SETTLED — Option A: v1 supports no forced semantic include, exclude, relation-type, or direction override.**

A human review, D12 evaluator concern independently confirmed as a defect, or post-release audit finding may lead to an approved change at the earliest incorrect curriculum configuration, candidate policy, producer/checker instruction, prompt, or universally valid generic-code stage, followed by a complete affected rerun. The producer and checker adjudicate the pair again under updated material inputs; a human finding never directly manufactures, changes, or deletes a published edge. Generated graphs are never hand-edited.

D10's profile-level unresolved-participation state cannot force a relationship, and a manual finding cannot conceal or replace a D13 processing failure. Every rerun remains subject to all settled pair, direction, recurrence, exclusivity, cycle, transitivity, attribution, provenance, structural-validation, checkpoint, and stale-reuse rules.

**LIMIT — Known semantic false positives or false negatives cannot be patched directly in v1.** They require earliest-stage remediation and a new run, may require additional LLM execution, and may still require further iteration.

This decision is separate from D10's profile-level eligibility policy, which has no per-SFI exception.

### Selected design background — Option A: no forced semantic edges in v1

Operators can adjust approved curriculum config or prompts and rerun, but cannot force a particular `buildsTowards` or `relatesTo` edge into the production graph. Replacing the code-owned candidate policy requires the separately governed replacement workflow in D3.

**Pros**

- Smaller scope.
- Avoids mixing hand-authored and generated semantics before the automated path is stable.
- Keeps one inference contract.

**Cons**

- Known false negatives cannot be patched directly.
- Repeated tuning may be expensive.

### Rejected alternative — Option B: runtime-config semantic overrides

**Pros**

- Easy deployment with the curriculum profile.

**Cons**

- Large configs become data patches.
- Policy and reviewed instance decisions are mixed.
- UUID-based overrides can be hard to maintain across identity-policy changes.

### Rejected alternative — Option C: semantic-override sidecar

Each row records pair identity, chosen decision, reviewer, timestamp, notes, and upstream bundle content hash.

**Pros**

- Strong auditability.
- Clean separation of policy and instance-level review.
- Can support include, exclude, or needs-review resolution.

**Cons**

- Additional artifact and lifecycle.
- More implementation/test work.

### Historical recommendation — superseded by the settled policy above

Option A is settled. D10 has no narrow per-SFI eligibility exception, D12 has no v1 gold-set workflow, and semantic overrides remain unsupported.

The settled D14 LIMIT is recorded above and covers both false positives and false negatives.

The payload requirements for rejected override options are historical only and create no v1 fields or artifacts.

---

## 3.3 Decision and approval sequence

D12-S/J/R/B/A are settled: S1 and R1 are documented CLI-configurable defaults; D12-J names the dedicated environment setting and shared model-settings binding; D12-B removes aggregate budgets and records the separately approved operational settings; A1 assigns concern disposition to the user for IDinsight. No implementation-governing decision payload remains open.

**Implementation approval recorded — 2026-09-11.** After reviewing the final synchronized governance amendment, including the original-production rationale-critique correction and common independent-sample evidence correction, the user explicitly stated: "ok i approve." This approves the amendment for implementation, including the K=24/F=25 revalidation scope and unchanged former-frontier review base `540ea950378ce54b54da1c7a93491b525609b574`. Approval applies to the reviewed governance changes in the working tree; HEAD remains the former frontier and is not a commit containing this amendment. This is user specification approval, not independent reviewer reapproval of Steps 24–25 or approval of an implemented harness. It does not authorize live evaluation/full pipeline calls, Git staging/commits, or changes to active runs. No budget approval is required.

Next required handoff: testing-primary cross-step reassessment of Step 24 release-policy conformance and affected Step 25 matrix against `540ea950378ce54b54da1c7a93491b525609b574`, followed by a user-created candidate commit when repository content changes and independent reviewer reapproval. If only execution/oracle evidence changes against an already reviewed identical tree, reuse the exact candidate SHA with immutable evidence; do not invent an empty commit. Do not implement Step 27 while revalidating Steps 24–25.

After that gate, testing owns Step 26 full-run execution/validation with explicit live authorization. After Step 26 reviewer approval, coding owns Step 27 harness authorship, a separate testing task owns tests and authorized execution, and the reviewer gates the exact candidate plus immutable evaluation evidence. Step 28 follows Step 27 approval; Step 29 uses the original pre-Step-1 baseline through the exact Step 28 approved candidate and reviews all production and evaluation evidence. Each handoff must resolve its actual review-base/candidate SHA at that time; future SHAs must be observed rather than invented.

---

# 4. Invariants

Invariants 1–57 remain production code-level contracts. Invariant 58 preserves structural-only production success while acknowledging the separate amended completion obligation. New 59–65 govern only the Step 27 evaluator under the user-approved amendment; the D12 payloads are settled, and Step 27 still follows Step 26 reviewer approval.

## 4.1 Configuration and phase boundary

1. **SETTLED — Required config:** when `RunConfig.kgs` is non-null, `kgs.as`, `kgs.lc`, `kgs.lp`, and `kgs.metadata` are all required; unknown fields remain forbidden.
2. **SETTLED — Cross-validation:** every LP statement type and local developmental value must be valid under the same curriculum's AS policy and controlled values.
3. **SETTLED — Upstream validation gate:** LP refuses to publish from an AS+LC bundle whose validation report failed or contains errors.
4. **SETTLED — Same framework:** all LP endpoints belong to the one framework/doc_key owned by the current run.
5. **SETTLED — No PDF reinterpretation:** final AS+LC nodes and edges are authoritative; LP may use bounded existing provenance/source snippets but does not rerun PageIR/SFI extraction.
6. **SETTLED — Curriculum neutrality:** no country, organization, subject, grade label, statement type, or hierarchy shape is hard-coded in LP Python.

## 4.2 Graph indexing and eligibility

7. **SETTLED — DAG-safe hierarchy:** all direct parents and relevant ancestor paths are preserved; code never assumes one parent.
8. **SETTLED — Root fallback is not a real curriculum parent:** unresolved framework-root fallback edges are flagged and never used as positive topical/hierarchy evidence.
9. **SETTLED — Local order is explicit:** code never determines developmental order through lexical sorting of labels.
10. **SETTLED — LP selection is independent:** normalized type, leafness, and LC eligibility are available evidence but never implicit LP eligibility rules.
11. **SETTLED — D1 closed-world matrices:** only the exact relation-specific statement-type pairs recorded in D1 may become candidates or final edges; omitted, cross-type, grouping, and future unconfigured pairs are excluded.
12. **SETTLED — D2 one-axis coordinate:** runtime configuration supplies only the coordinate statement type and exact ordered values; Python owns canonical identity-scope resolution, missing/invalid behavior, same-rank permission, lower-to-higher direction, unlimited forward gap, and `relatesTo` gap rules recorded in D2.
13. **SETTLED — D10 two-state unresolved policy:** every profile explicitly selects exclude-all or include-all-otherwise-eligible-with-warnings; no per-SFI exceptions/sidecars exist, the six initial profiles select inclusion with warnings, and status remains visible through all affected artifacts/provenance.

## 4.3 Candidate generation

14. **SETTLED — No self-pairs.**
15. **SETTLED — Deterministic pair identity:** the same doc_key, endpoints, and approved pair-orientation policy produce the same candidate ID.
16. **SETTLED — Exact uniqueness:** a logical pair is represented once under the approved D4 policy.
17. **SETTLED — Bounded retrieval:** candidate budgets are applied before any LLM call.
18. **SETTLED — Explainable nomination:** every pair records one or more named nomination reasons and the values that triggered them.
19. **SETTLED — No automatic semantic edge:** hierarchy, LC overlap, text similarity, code proximity, rank proximity, or source order can nominate but cannot publish a relationship.
20. **SETTLED — Audit flags propagate:** known AS code, merge, and unresolved anomalies remain available to producer/checker and final provenance.
21. **SETTLED — D3 built-in non-embedding policy:** candidate retrieval uses one code-owned deterministic non-embedding policy in v1. Runtime configuration supplies only explicit per-SFI and total budgets; Python owns evidence handling, ranking, tie-breaking, and budget-application order. No runtime algorithm/version, strategy list, technology marker, enabled-signal list, ranking/tie-breaking selector, fingerprint selector, or implementation identifier exists. Integrity hashes use actual material inputs and serialized artifacts, not a separately maintained policy version. Any future replacement is a separately governed build step that replaces the implementation and requires affected artifacts to be deleted and regenerated.

## 4.4 LLM adjudication

22. **SETTLED — Exact request coverage:** every candidate assigned to a request appears exactly once in that request set.
23. **SETTLED — Exact response coverage:** every successful response contains exactly one judgment per requested pair; no missing or extra pair IDs.
24. **SETTLED — Endpoint containment:** the producer/checker cannot introduce SFIs outside the request.
25. **SETTLED — Allowed decisions only:** Python rejects a relationship type or direction not allowed for that pair.
26. **SETTLED — Same evidence for checker:** the checker receives the same bounded evidence plus the producer draft; it cannot rely on hidden global context.
27. **SETTLED — Complete correction:** a failing checker returns a complete corrected response, not an incremental patch.
28. **SETTLED — Negative vs ambiguity:** `no_relation`, `needs_review`, and processing failure remain distinct states.
29. **SETTLED — Confidence is audit data:** confidence does not by itself accept an edge or bypass the checker.
30. **SETTLED — D7 recurrence mapping:** substantive extension maps to permitted `buildsTowards`, meaningful reinforcement without dependency maps to canonical `relatesTo`, generic repetition maps to `no_relation`, and material ambiguity maps to `needs_review`.
31. **SETTLED — D13 zero tolerance:** after permitted retries/recovery, any failed pair halts LP and prevents successful LP/combined release; no count/rate tolerance or per-profile threshold exists.

## 4.5 Final relationships

32. **SETTLED — Ontology shape:** only SFI → SFI `buildsTowards` and SFI → SFI `relatesTo` are emitted.
33. **SETTLED — Endpoint keys:** both endpoints use `case_identifier_uuid`.
34. **SETTLED — Endpoint existence:** every source and target resolves to a final SFI in the upstream bundle.
35. **SETTLED — No self-loops.**
36. **SETTLED — Deterministic relationship UUID:** UUIDv5 identity is derived from doc_key, relationship type, and finalized endpoints; the LLM never chooses it.
37. **SETTLED — Correct description:** `buildsTowards` does not claim a strict prerequisite; `relatesTo` does not imply sequence/dependency.
38. **SETTLED — Complete provenance:** every edge resolves to candidate ID, request ID, producer/checker outcome, evidence summary, material config/input content hashes, and source framework. The required provenance set is code-owned and cannot be selected or disabled in runtime configuration.
39. **SETTLED — D4 unified pair judgment:** each unordered logical pair appears once and receives one complete judgment among its deterministically admissible directions/relations, `no_relation`, and `needs_review`.
40. **SETTLED — D5 canonical `relatesTo`:** one row is stored per accepted unordered pair with lower canonical endpoint UUID as source and higher as target; consumers traverse both endpoint positions.
41. **SETTLED — D6 exclusivity:** one logical pair publishes at most one LP edge, and semantically supported `buildsTowards` takes precedence over `relatesTo`.
42. **SETTLED — D8 acyclicity:** the complete finalized `buildsTowards` graph is a DAG; violations fail release and produce deterministic SCC/edge/node/provenance diagnostics without silent repair.
43. **SETTLED — D9 direct edges only:** finalization performs neither transitive closure nor reduction, retains every independently accepted direct edge, and never publishes reachability as an edge.
44. **SETTLED — LP inference ownership:** every published LP relationship uses configured exact `author = "LLM generated"`, `provider = "IDinsight"`, attribution template, and approving identity. Python copies the validated source-framework license verbatim, expands only the approved source-attribution token, and retains the complete D11 provenance set. Runtime configuration cannot override the inheritance/substitution mechanics or choose provenance categories, and the LLM cannot author or alter these fields.
45. **SETTLED — D14 no semantic overrides:** v1 has no forced include/exclude/relation/direction mechanism; findings cause earliest-stage remediation, producer/checker re-adjudication, and rerun rather than generated-graph edits.

## 4.6 Artifacts, resume, and combined export

46. **SETTLED — Pre-call population materialization:** the complete validated candidate and request populations, IDs, order, coverage, counts, and material content hashes are written and reconciled before any external LP LLM call.
47. **SETTLED — Stage-specific prefix-safe checkpoints:** validated producer drafts, checker verdicts, and reconciled responses are written in deterministic contiguous prefixes; failures are separate, and `overwrite=false` resumes at the earliest unfinished stage without repeating valid completed calls. This behavior is code-owned and has no runtime checkpoint/resume policy fields.
48. **SETTLED — Prefix validation fails closed:** gaps, duplicates, out-of-order records, truncation, invalid JSONL, request misalignment, and hashes or identifiers that show stale material inputs are rejected. Runtime configuration cannot select fingerprint inputs or mismatch behavior.
49. **SETTLED — Stale final bundle rejection:** `overwrite=false` reuses `as_lc_lp_kg_bundle.json` only when hashes/identifiers derived from the actual material LP and upstream inputs and artifacts match. Candidate-policy replacements do not use a version compatibility check; affected artifacts are deleted and regenerated.
50. **SETTLED — Failed validation remains inspectable:** final artifacts may be written for diagnosis, but `kg_run.json` cannot report success when LP or combined validation fails.
51. **SETTLED — Count reconciliation:** summary counts equal actual list and JSONL counts for eligible SFIs, candidates, requests, judgments, failures, final relationships, nodes, and all four relationship types.
52. **SETTLED — Identifier collision absence:** framework, SFI, LC, `hasChild`, `supports`, `buildsTowards`, and `relatesTo` identifiers are unique in the combined graph.
53. **SETTLED — Existing artifacts are not mutated:** standalone AS and AS+LC bundle/projection schemas remain unchanged.
54. **SETTLED — Upstream combined content is preserved:** the AS+LC framework, SFIs, LCs, `hasChild`, `supports`, summaries, unresolved data, and complete `entity_provenance` mapping are copied without deletion or reshaping before additive LP fields/provenance are introduced.
55. **SETTLED — Combined node parity:** `as_lc_lp_nodes.jsonl` contains exactly the framework, SFI, and LC nodes in the combined bundle.
56. **SETTLED — Combined relationship completeness:** `as_lc_lp_relationships.jsonl` contains exactly all `hasChild`, `supports`, `buildsTowards`, and `relatesTo` relationships in the combined bundle.
57. **SETTLED — Ordering is serialization detail:** deterministic ordering is used for stable files, but downstream semantics rely on IDs, endpoint keys, and relationship types rather than line order.
58. **SETTLED — D12 production non-gate and disclosure:** production LP/combined success does not consume an evaluation score, human gold set, or semantic approval; `needs_review` stays visible/nonpublishing/nonblocking. Separate required Step 27 execution/reporting gates numbered project completion; neither process results nor judge assessments prove pedagogical correctness.

## 4.7 Independent evaluation (new Step 27 only)

59. **SETTLED — Read-only production boundary:** evaluation uses fixed validated inputs, never mutates production graphs/config/prompts/candidate policy or their success reports, and writes distinct evaluation artifacts.
60. **SETTLED — Blindness and evidence separation:** independent classification uses a redacted view retaining permitted facts; production conclusions/rationale and control labels cannot leak into it. After classification is frozen, a separate critic receives original bounded production evidence and the operative rationale, without the classifier's answer. Classification and critique have distinct request/cache identities. Exact production, reconstructed bounded upstream, expanded upstream, and evidence-removal conditions remain separate; removed evidence cannot survive in derived fields or be used to rescore original-production grounding. Every independently sampled pair receives the same bounded-upstream construction rules without consulting nomination status; freeze those payloads before joining production outcomes. Nomination coverage uses their common-condition judge-positive denominator, never nomination-dependent evidence conditions.
61. **SETTLED — Reproducible independent sampling:** both production outcome strata and independently selected admissible upstream populations are required; seeds, memberships, shortfalls, selection routes and denominators are retained. Unasserted real pairs are never assumed negative.
62. **SETTLED — Evaluator integrity:** exact schedule/output/endpoint/evidence validation, material-content-bound cache reuse, distinct replicate/order identities, separate failure records, bounded calls and usage accounting are mandatory.
63. **SETTLED — Honest interpretation:** relationship support, rationale grounding, production agreement, sample-conditioned nomination coverage, controls and judge stability are separate reported assessments. They are not semantic precision/recall or proof of pedagogical correctness.
64. **SETTLED — Completion versus quality:** unresolved execution failures prevent evaluation completion; ambiguity is a valid response; quality concerns and all disagreements retain accountable disposition without an automatic semantic score threshold. D12-A assigns concern disposition to IDinsight through the project user.
65. **SETTLED — Independent validation and remediation:** coding authors executable harness support; separate testing authors tests and validates/executes; reviewer gates completion. Confirmed production defects follow D14 earliest-stage remediation and rerun, not evaluator graph edits.

---

# 5. Build order

No implementation starts at Step 1 until Step 0 is complete.

| Step | One reviewable implementation aspect                                                             | Primary files/outputs                                                                                                                                                   | Review and test boundary                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
|------|--------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 0    | Record D1–D14 and later governance amendments consistently, then obtain implementation approval  | This brief plus synchronized repository/role governance instructions                                                                                                    | Every concrete payload is present; dependent model/config/invariant/build/test language agrees; no implementation-governing placeholder or unresolved decision remains; user implementation OK recorded                                                                                                                                                                                                                                                                                                        |
| 1    | Establish six-curriculum LP regression fixtures                                                  | New reduced fixtures under the repository's test area                                                                                                                   | Fixture loader verifies current AS+LC bundle shapes, counts, DAG parents, unresolved flags, and LC alignments without changing production code                                                                                                                                                                                                                                                                                                                                                                 |
| 2    | Define the intrinsic `kgs.lp` Pydantic models and standalone field validators                    | `backend/src/kgfeg/schemas.py`                                                                                                                                          | Tests cover relation-specific pair policies; D2 coordinate type/order; D3 per-SFI/total budgets; D10's required two-state policy; D11 author/provider/template/approver; positive batch/evidence bounds; and D13 retry counts. Reject runtime candidate algorithm/version/strategy/technology/signal/ranking/tie-breaking/fingerprint selectors, D2 invariant selectors, license-source/provenance switches, checkpoint/resume policies, and failure-tolerance thresholds; cross-profile wiring remains Step 3 |
| 3    | Wire `kgs.lp` as required, add AS-policy cross-validation, and update all six configs atomically | `backend/src/kgfeg/schemas.py`; `examples/**/config*.json`                                                                                                              | Tests prove all six profiles reproduce the exact D1 matrices, D2 coordinate types/orders, D3 per-SFI/total budgets, D10 inclusion-with-warning state, D11 configured values/template, retry counts, and curriculum instructions, with no repeated D2/D3/D11/D13 invariant fields. Cross-validation rejects unknown values, omitted variable policy, invariant-override fields, and copy/paste errors; later owning steps test the code-owned runtime behavior                                                  |
| 4    | Replace dormant LP schemas with the approved intrinsic pair/evidence/judgment record schemas     | `backend/src/kgfeg/kgs/schemas.py`                                                                                                                                      | Schema tests cover valid accepted/negative/review records and reject intrinsic defects such as malformed UUIDs, self-pairs, illegal enum combinations, and invalid confidence/rationale fields; request-relative endpoint/coverage checks remain owned by Steps 12 and 14                                                                                                                                                                                                                                      |
| 5    | Add LP usage buckets and model-settings plumbing using `LLM_KG_MODEL`                            | `kgs/llm.py`, existing model registry calls                                                                                                                             | Usage serialization includes producer/checker buckets and `kgs_settings("learning_progressions")` is exercised; no prompt or agent factory is implemented before Steps 13–14, and no new model environment setting is introduced                                                                                                                                                                                                                                                                               |
| 6    | Build an AS+LC graph index that is safe for trees and DAGs                                       | New `kgs/lp_index.py` or equivalent                                                                                                                                     | Tests verify Pratham multi-parent ancestry, framework-root handling, LC-by-SFI indexes, and deterministic traversal order                                                                                                                                                                                                                                                                                                                                                                                      |
| 7    | Implement local developmental-coordinate resolution                                              | New LP index/selection utility                                                                                                                                          | Tests cover every exact D2 order, Madhi scope-only Class, explicit Grade/Class nodes, alias canonicalization, missing-coordinate `buildsTowards` exclusion and `relatesTo` retention, invalid/ambiguous/conflicting hard failure, same-rank permission, forward-only direction, and unlimited gaps                                                                                                                                                                                                             |
| 8    | Implement LP SFI eligibility and unresolved-policy reporting                                     | New `kgs/lp_selection.py`; `lp_eligible_sfis.json`; `lp_eligibility_report.json`                                                                                        | Tests cover exact D1 closed-world participation, D10 exclude/include states, all six inclusion-with-warning profiles, no exception/sidecar path, D2 precedence, independent LC eligibility, multiple grains, exact counts, and warning propagation without using fallback root as evidence                                                                                                                                                                                                                     |
| 9    | Implement hard candidate filters and deterministic pair IDs                                      | New `kgs/lp_candidates.py`                                                                                                                                              | Tests prove no self-pairs, no D1-disallowed pairs, no D2-disallowed directions, one unordered D4 logical pair, and IDs stable under encounter/input ordering                                                                                                                                                                                                                                                                                                                                                   |
| 10   | Implement named evidence features within the built-in D3 candidate policy                        | `lp_candidates.py` or `lp_evidence.py`                                                                                                                                  | Each code-owned deterministic non-embedding signal is tested independently; tests cover hierarchy/DAG context, local rank, LC overlap, text, code/source/audit handling, named triggering values, and the rule that no feature publishes an edge. No runtime strategy registry, algorithm selector, or enabled-signal list is introduced                                                                                                                                                                       |
| 11   | Implement the built-in candidate policy's union, ranking, and budgets                            | `lp_candidates.py`; `lp_candidate_pairs.jsonl`; `lp_candidate_summary.json`                                                                                             | Deterministic tests verify bounded nomination, union, deduplication, configured per-SFI/total budget behavior before LLM work, code-owned stable ranking and total tie-breaking, pair-scale bounds, actual input/artifact content hashes, and stable candidate records without runtime ranking selectors or an internal policy version; tests acknowledge rather than claim to measure the D3/D12 recall LIMIT                                                                                                 |
| 12   | Implement bounded LP request construction and complete pre-call materialization                  | New `kgs/lp_generation.py`; `lp_generation_requests.jsonl`                                                                                                              | Tests verify complete validated candidate/request populations are written and reconcile before any external call, with exact coverage/order/IDs/counts/content hashes, bounded context, all parent paths, LC evidence limits, warnings, and stable request IDs                                                                                                                                                                                                                                                 |
| 13   | Implement the LP producer prompt and agent                                                       | `kgs/prompts.py`, `kgs/agents.py`, `kgs/llm.py`                                                                                                                         | Prompt fixtures demonstrate relation semantics, D6/D7 recurrence distinctions, curriculum-specific examples, D10 warnings, explicit `no_relation`/`needs_review`, D14 no overrides, and no out-of-request endpoints                                                                                                                                                                                                                                                                                            |
| 14   | Implement the independent LP checker and deterministic integrity validators                      | `kgs/prompts.py`, `kgs/agents.py`, `kgs/validators.py`, `kgs/llm.py`                                                                                                    | Tests cover one complete D4 pair judgment, accept/correct, missing/extra/duplicate pair, endpoint leakage, D1/D2/D5/D6 illegal outcomes, warning/evidence parity, and complete rather than patch correction                                                                                                                                                                                                                                                                                                    |
| 15   | Implement resumable generation orchestration and D13 failure accounting                          | `kgs/lp_generation.py`; draft/verdict/final/failure artifacts                                                                                                           | Interrupted-stage tests verify code-owned validated deterministic draft/verdict/final prefixes, separate failures, zero tolerance after configured retries, halt/no-success status, earliest-unfinished-stage resume without repeated valid calls, and fail-closed gaps/duplicates/order/truncation/alignment/material-input behavior without runtime checkpoint/resume/mismatch selectors                                                                                                                     |
| 16   | Reconcile final pair decisions under D4–D10 and D14                                              | New `kgs/lp_finalization.py`; `lp_final_claims.json`                                                                                                                    | Tests cover unified judgments, D6 precedence/exclusivity, D7 recurrence, D5 canonical `relatesTo`, D8 global DAG diagnostics, D9 direct-edge-only behavior, D10 warnings, D14 absence of overrides/hand edits, duplicates/conflicts, and visible nonpublishing `needs_review`                                                                                                                                                                                                                                  |
| 17   | Mint deterministic `Relationship` records and correct generic descriptions                       | `kgs/lp_finalization.py`, `kgs/schemas.py`                                                                                                                              | UUID snapshot tests cover both types; endpoint and metadata tests verify configured D11 author/provider/template/approver values plus code-owned source-license inheritance, template substitution, complete provenance, and Learning Commons-compatible semantics                                                                                                                                                                                                                                             |
| 18   | Implement standalone LP graph validation                                                         | New LP validator or `kgs/validators.py`                                                                                                                                 | Tests cover endpoints, self-loops, duplicates, D1/D2/D4–D10/D14 policy, complete D8 diagnostics, direct-edge provenance, exact D11 metadata/provenance, counts/collisions, unresolved-warning consistency, D13 zero-failure success condition, and rejection of structural-validity-as-semantic-proof claims                                                                                                                                                                                                   |
| 19   | Write standalone LP provenance, summary, unresolved, failure, and validation artifacts           | `lp_relationship_provenance.json`, `lp_generation_summary.json`, `lp_unresolved_items.json`, `lp_generation_failures.json`, `lp_validation_report.json`, relation files | Round-trip validation and independent count reconciliation cover every artifact; `needs_review`, policy exclusions, and D13 failures remain separate; warnings/provenance and accepted/nonpublished counts agree                                                                                                                                                                                                                                                                                               |
| 20   | Add the AS+LC+LP bundle schema and compiler                                                      | New `kgs/lp_export.py`, `kgs/schemas.py`                                                                                                                                | Bundle tests verify all upstream nodes, relationships, summaries, unresolved data, and the complete AS+LC `entity_provenance` mapping are preserved verbatim; LP fields/provenance are additive; required content hashes are complete; invalid LP blocks successful compilation                                                                                                                                                                                                                                |
| 21   | Write `as_lc_lp_nodes.jsonl` and `as_lc_lp_relationships.jsonl`                                  | `kgs/lp_export.py`                                                                                                                                                      | Projection tests prove node parity with AS+LC and exact relationship union/order across all four types                                                                                                                                                                                                                                                                                                                                                                                                         |
| 22   | Integrate LP into `create_kgs.build_kgs()` after `compile_as_lc_kg()`                            | `entries/create_kgs.py`                                                                                                                                                 | Orchestration test verifies phase order, returned bundle use, failure propagation, usage accounting, and `kg_run.json` success/error state                                                                                                                                                                                                                                                                                                                                                                     |
| 23   | Implement final-bundle reuse and stale-input detection                                           | `lp_export.py`, LP utilities                                                                                                                                            | Tests cover exact-match reuse using hashes/identifiers derived from actual material upstream/config/candidate/request/checkpoint/response/prompt/model/finalization inputs and artifacts, projection rewrite, invalid bundles, and D13 prefix alignment. Candidate-policy replacement requires deletion/regeneration rather than an internal version compatibility layer                                                                                                                                       |
| 24   | Add deterministic D12 release-policy conformance coverage                                        | Automated tests and deterministic synthetic fixtures/fakes                                                                                                              | Prove `needs_review` is visible, nonpublishing, and nonblocking; processing failure remains D13-governed; production success needs no evaluator artifact, semantic score, gold set, human sample/cadence/approval or threshold; structural/process validity cannot be labeled pedagogical truth; findings cannot edit graphs. Reassess the former global no-metrics prohibition to permit the separate Step 27 harness, without adding it to production config or success reports                                                                                                                                                                                                |
| 25   | Run the targeted six-curriculum structural/process matrix                                        | Deterministic test outputs and validation evidence                                                                                                                      | Madhi: scope-only coordinate; Nigeria: tree baseline; Pratham: DAG/multiple grains; Ghana math: unresolved ancestry/code anomalies; Rwanda: noisy LC evidence cannot auto-publish; Ghana English: recurrence mapping. Validate policy, provenance, counts, identities, checkpoints, and artifacts without a semantic-quality pass/fail claim                                                                                                                                                                   |
| 26 | Run all six complete pipelines from source PDFs (former Step 27) | Six complete result directories and immutable manifests | Testing-primary after revalidated Step 25 reviewer approval; explicit live authorization. All AS, AS+LC, AS+LC+LP bundles validate; every candidate/request has successful D13 coverage; no failed pair; exact source/config/code hashes, provenance, warnings, counts, collisions, projections, checkpoints and reuse reconcile. needs_review stays visible/nonpublishing/nonblocking. Reviewer approves exact candidate plus evidence before Step 27. |
| 27 | Implement, independently test, and execute LP evaluation harness | entries/evaluate_lps.py; evals/lp_eval/{schemas,sampling,prompts,judge,scoring}.py; separate lp_eval artifacts | Coding authors harness after Step 26 approval and settled D12 payloads; separate testing authors deterministic/fake-based tests and independently validates, then executes on six reviewed Step 26 snapshots only with explicit live authorization. All three D12 components, failure/ambiguity/concern distinctions, exact validation/cache/sampling/usage/reporting and D12-A dispositions required. No production tuning. User-created candidate commit and independent reviewer gate bind harness, tests, configs and immutable execution evidence. |
| 28 | Update user-facing pipeline, evaluation and artifact documentation | docs/pipeline/learning-progressions.md; architecture, index, artifacts, adding-curriculum, running/debugging docs | Coding then testing after Step 27 reviewer approval. Match actual Step 26 production and Step 27 evaluation artifacts; explain relatesTo symmetric lookup, direct edges/reachability, D10 warnings, D13 resume/failure, evaluation commands/config/conditions/denominators/cache/usage/failure/concern disposition, absent automatic semantic thresholds and human gold sets, and all D12/D14 LIMITs. |
| 29 | Final comprehensive release review | Entire brief, configs, code, tests, docs, six production snapshots and separate evaluation evidence | Read-only reviewer uses original pre-Step-1 baseline and exact Step 28 approved candidate. Verify every settled invariant, revalidated Step 24–25 chain, Step 26 structural/provenance/count/collision/D13 evidence, Step 27 independently tested executed/reported evaluator with no unresolved execution failures and complete concern dispositions, Step 28 documentation, LIMITs and zero unresolved implementation decisions. No semantic correctness claim or automatic score threshold. |

## 5.1 Suggested module boundary

To keep each phase reviewable and consistent with the existing `sfi_*` / `lc_*` organization:

```text
kgs/
  lp_index.py          # upstream graph/DAG/LC indexes and coordinate access
  lp_selection.py      # eligible SFI selection and unresolved gating
  lp_candidates.py     # pair filtering, evidence features, ranking, budgets
  lp_generation.py     # request construction, resume, producer/checker orchestration
  lp_finalization.py   # reconciliation, IDs, relationships, summary/provenance
  lp_export.py         # combined AS+LC+LP bundle and flat projections
```

Shared files should only receive shared responsibilities:

```text
schemas.py    # config and graph/LLM schemas
agents.py     # agent factories
prompts.py    # producer/checker prompt builders
llm.py        # calls, run dataclasses, usage buckets
validators.py # deterministic integrity checks
create_kgs.py # orchestration only
```

The separate Step 27 evaluator uses the parallel entry-point and five-module layout specified in D12.1; it is not another stage inside `create_kgs`. Step 27 also owns the minimal `config.py` setting/registry wiring for `LLM_LP_EVAL_JUDGE_MODEL` and the corresponding assignment in `.template.env` and the local `.env`, as specified in D12-J. The production model and shared registry behavior remain unchanged.

## 5.2 First full-run sequence (Step 26)

A sensible order for real pipeline validation is:

1. **Madhi math** — verifies a progression coordinate that exists in identity scope without final Class nodes.
2. **Nigeria math** — verifies the simplest explicit Grade tree.
3. **Pratham science** — verifies DAG ancestry and multiple Standard grains.
4. **Ghana math** — verifies inclusion-with-warning for unresolved-root fallback and code anomalies without treating fallback placement as evidence.
5. **Rwanda math** — verifies relation-specific signal policy where shared generic LCs can be noisy.
6. **Ghana English** — verifies recurrence versus developmental extension and substantive `relatesTo` behavior.

Each run should be reviewed at the earliest incorrect artifact rather than patching final JSONL by hand.

## 5.3 Definition of done for each curriculum

For each of the six runs:

```text
as_kg_bundle.validation_report.passed       == true
as_lc_kg_bundle.validation_report.passed    == true
as_lc_lp_kg_bundle.validation_report.passed == true
```

and:

```text
as_lc_lp node count
  = 1 + SFI count + LC count

as_lc_lp relationship count
  = hasChild + supports + buildsTowards + relatesTo
```

Every final LP relationship must:

- resolve both SFI endpoints;
- obey the approved statement-type and developmental policies;
- have deterministic identity;
- have complete relationship metadata and provenance;
- trace to one candidate pair and one accepted/checker-corrected judgment;
- pass the approved cycle, symmetry, exclusivity, transitivity, unresolved, and attribution policies;
- appear exactly once in the relevant standalone relationship file, combined bundle, and combined relationship JSONL.

Each successful run must also prove that the complete candidate/request population was materialized before external calls, every producer/checker/reconciled checkpoint is a valid aligned deterministic prefix, no D13 processing failure remains, and any included unresolved SFI/relationship carries the required warnings and provenance.

The checks above define Step 26 production completion. D12 additionally requires Step 27 evaluator execution and separate reports for each of these exact six snapshots, with all required scheduled judgments accounted for, no unresolved execution failures, retained ambiguity/disagreement/denominators, usage evidence, and concern dispositions under the approved D12-A policy. Required reports do not impose an automatic semantic score threshold or human gold-set prerequisite. Step 28 must describe actual findings and all LIMITs; Step 29 verifies both evidence sets without claiming pedagogical correctness. Confirmed false positives/negatives follow D14 upstream remediation and rerun, never generated-graph patching.
