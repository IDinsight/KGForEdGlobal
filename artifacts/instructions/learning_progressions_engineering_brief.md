# Engineering Brief: Learning Progressions KG Construction

**Status:** Approved for implementation. D1–D14 remain fully recorded as **SETTLED**. The user approved the prior canonical brief—including its accepted LIMITs—for Step 1+ implementation on 2026-09-01, approved the first D3 amendment on 2026-09-02, and explicitly approved this governance simplification amendment on 2026-09-02. The approved amendment removes internal candidate-policy versioning and runtime candidate ranking/tie-breaking configuration, and moves universal single-valued coordinate, candidate-policy, provenance, checkpoint, resume, license-inheritance, and stale-input behavior into code-owned invariants. The reviewer-approved Step 1 state remains the predecessor because Step 1 does not implement or consume these LP configuration fields. Dependent Step 2 production remediation may now resume in a coding-primary task using `345bb517957d90e9bb68793947ec4428bd9f0997` as its review base.

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

No LP-specific model environment variable is required. The model registry already accepts the `"learning_progressions"` model-settings type. LP will add its own agents, prompts, retry behavior, and usage buckets while using the same configured KG model as AS and LC.

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

## 1.7 Success criteria

The implementation is complete when:

1. all six example configs validate with required `kgs.lp` sections;
2. all six full runs produce the LP audit artifacts and the three final AS+LC+LP outputs;
3. existing AS and AS+LC outputs continue to validate and retain their existing schemas;
4. every accepted progression relationship has valid SFI endpoints, deterministic identity, complete audit provenance, and an accounted-for producer/checker path;
5. the combined bundle's counts and projections reconcile exactly;
6. repeat/resume behavior follows existing KG conventions;
7. structural tests cover tree, DAG, unresolved hierarchy, scope-only grade, multiple Standard grains, sparse LC reuse, noisy LC reuse, and recurring-practice cases;
8. release behavior follows settled D12: there is no independent pre-release semantic or gold-set gate; `needs_review` remains visible, never publishes, and does not block release; and documentation does not present structural/process validity as pedagogical truth;
9. every candidate/request pair is processed successfully under D13's zero-tolerance failure policy, with validated deterministic-prefix checkpoint and resume behavior; and
10. release documentation discloses the accepted D12 and D14 limitations.

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

The adjudicator can only accept pairs that candidate generation nominates. Strong producer/checker agreement cannot recover a relationship that deterministic retrieval never surfaced. Under D12 Option D, v1 has no independent pre-release candidate-recall metric or gold-set requirement; missing plausible pairs may therefore remain undetected at release.

**LIMIT — Upstream AS and LC errors can influence LP.**

LP endpoints are stable SFIs, but hierarchy errors, missing scope values, faulty unresolved flags, or weak LC decomposition can change candidate evidence. LP provenance must retain the upstream bundle content hash so these dependencies are visible.

**LIMIT — The six reviewed examples are English-language artifacts.**

The normalization code is Unicode-aware, but candidate retrieval and progression prompts have not yet been validated on non-English or multilingual progression judgments. A lexical-only first implementation may require language-specific tuning later.

**LIMIT — V1 has no independent pre-release semantic validation.**

Pydantic and graph checks can prove endpoint integrity, deterministic identities, policy compliance, producer/checker process completion, and artifact reconciliation. They cannot prove that an inferred progression is instructionally sound. D12 permits release without an independent human or gold-set semantic gate; any later human audit is non-blocking and its corrections require earliest-stage remediation and rerun.

---

# 3. Settled decisions

## 3.1 Marker contract

The following markers are load-bearing. D1–D14 below are now **SETTLED**. **DECIDE** remains defined only for a future governance change that deliberately reopens or introduces an implementation choice.

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

An option letter alone does **not** settle a future decision when the selected option requires concrete policy payloads. D1–D14 satisfy this rule through the complete settled policies below.

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
| D12 | Semantic evaluation and release policy                               | D — no independent pre-release semantic/gold-set gate; non-blocking post-release human audit                       | **SETTLED** |
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

**LIMIT — Deterministic non-embedding retrieval creates an unmeasured v1 candidate-recall ceiling.** It may miss semantically related standards with weak lexical, hierarchy, code, or LC overlap. D12 Option D accepts that no independent pre-release candidate-recall metric is required; this limitation must be disclosed rather than presented as measured quality.

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

**LIMIT — The profile-level state cannot distinguish a strong individual unresolved case from a weak one.** Inclusion may admit unresolved SFIs with insufficient non-fallback evidence, while exclusion may omit usable SFIs. Producer/checker adjudication reduces but does not remove this all-or-nothing weakness, and D12 provides no independent pre-release semantic gate.

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

## D12. Semantic validation and release policy

**SETTLED — Option D: v1 has no independent pre-release semantic or gold-set gate; human audit is optional, post-release, and non-blocking.**

Independent human semantic review is not required before acceptance or release. Release depends on deterministic structural/process validation, successful producer/checker reconciliation, and D13's zero-tolerance processing-failure policy. `needs_review` remains visible in audit and summary artifacts, never publishes a relationship, and does not itself block release.

V1 defines no independently reviewed gold set, minimum human-review sample, audit cadence, semantic metric, numeric semantic threshold, or semantic pass/fail authority. Structural correctness, producer/checker agreement, and D13-compliant completion must never be described as proof of pedagogical correctness.

Human audit may occur after acceptance or release. When performed, it records the reviewed population or sampling method, reviewer identity, review time, findings, rationale, affected candidate/relationship IDs, and exact released artifact/effective-config content hashes. IDinsight is the organizational authority for audit findings and remediation/re-release decisions. Findings inform a later remediation or release; they do not retroactively become a prerequisite for the original release.

Confirmed defects are corrected at the earliest incorrect configuration, candidate, prompt, judgment, finalization, or validation stage and the affected pipeline is rerun. Generated graphs are never hand-edited.

The v1 build-order contract is exact:

- Step 24 adds deterministic D12 release-policy conformance coverage, not a semantic harness or gold set;
- Step 25 runs the targeted six-curriculum structural/process matrix without a semantic-quality pass/fail claim;
- Step 26 is explicitly deferred with no implementation or tuning work, and progression goes from reviewer-approved Step 25 directly to Step 27;
- Step 27 retains the six complete runs and every structural, provenance, count, collision, checkpoint/material-input-integrity, and D13 requirement without a D12 semantic-pass prerequisite; and
- Step 29 verifies accurate disclosure of this LIMIT and rejects pedagogical-correctness claims derived from structural/process evidence.

**LIMIT — V1 may release semantically incorrect or incomplete progression relationships because no independent human or gold-set semantic evaluation is required before release.** Producer/checker separation and deterministic validation reduce structural and process risk but do not establish pedagogical correctness. This limitation must be visible in release documentation and final review.

### Rejected historical alternative — Option A: pre-release manual review protocol

Reviewers inspect a documented set of independently selected expected-positive and expected-negative pairs, plus stratified samples of accepted, `no_relation`, and `needs_review` outputs, and issue an explicit pass/fail decision.

**Pros**

- Fastest start.
- Lower annotation and harness effort.
- Can still expose obvious candidate-recall and edge-quality failures when pair selection is independent.

**Cons**

- No stable numeric regression metric.
- Reviewer judgment and sample choice have more influence.
- Harder to compare releases or detect gradual quality drift.

### Rejected historical alternative — Option B: pre-release reviewed gold set for every curriculum

A suggested starting set per curriculum is approximately 40–60 pairs, deliberately balanced across:

```text
likely buildsTowards
likely relatesTo
hard no_relation negatives
same-level and cross-level cases
strong and weak LC overlap
same-branch and cross-branch cases
unresolved/audit-flag cases where applicable
```

The evaluation universe must not be selected only from `lp_candidate_pairs.jsonl`, published edges, or current model outputs. Otherwise candidate recall and accepted-edge precision become circular. At minimum, combine:

1. **independently nominated pairs** selected from the curriculum/AS+LC graph without consulting the current candidate list or model decision; and
2. **stratified pipeline samples** from nominated candidates, accepted edges, `no_relation`, and `needs_review` outcomes.

Each reviewed row records its sampling/inclusion method, reviewer, policy version, and whether the label is definitive, ambiguous, or excluded from a particular metric.

Evaluate separately:

- candidate recall: did deterministic retrieval surface the independently reviewed positive pair?
- accepted-edge precision: are sampled published edges correct?
- relation choice: `buildsTowards` vs `relatesTo`;
- `buildsTowards` direction accuracy;
- abstention/`needs_review` quality;
- unresolved-context handling;
- rationale grounding, reported separately from edge correctness.

Choosing Option B is incomplete unless Step 0 also records:

| Gate input                  | Required decision                                                                               |
|-----------------------------|-------------------------------------------------------------------------------------------------|
| Candidate recall            | Minimum per-curriculum threshold and whether an aggregate threshold also applies                |
| Accepted-edge precision     | Minimum per-curriculum threshold; aggregate success must not silently hide a failing curriculum |
| Relation-choice accuracy    | Minimum threshold and exact denominator                                                         |
| Direction accuracy          | Minimum threshold and denominator for reviewed `buildsTowards` positives                        |
| `needs_review` at release   | Maximum count/rate, zero-tolerance rule, or explicit reviewed-waiver policy                     |
| Ambiguous/unscorable labels | Exclude from denominators, adjudicate, or treat as release-blocking                             |
| Minimum sample support      | Minimum reviewed examples required before each metric can pass                                  |

**ELI5:** Do not grade the scout only on places the scout already chose to visit. Give it independently chosen destinations, define the passing score before seeing the results, and decide how many “I am not sure” answers a release may contain.

**Pros**

- Repeatable tuning and regression protection.
- Captures curriculum-specific semantics.
- Separates candidate-retrieval failures from LLM/finalization failures.
- Produces an actual release gate instead of a dashboard without pass/fail rules.

**Cons**

- Highest review effort.
- Gold labels can themselves be debatable.
- Small samples have uncertainty and require versioning when policy changes.
- Threshold revisions after a pilot require a brief update and reapproval rather than silent tuning.

### Rejected historical alternative — Option C: representative-curriculum gold sets

For example:

```text
Pratham       -> DAG + multiple grains
Ghana math    -> unresolved ancestry
Rwanda        -> noisy LC reuse
Ghana English -> recurrence
```

The same independent-sampling and explicit-threshold requirements apply to the selected curricula.

**Pros**

- Lower annotation effort than Option B.
- Covers major structural risks.

**Cons**

- May miss Madhi/Nigeria-specific semantics.
- Provides no per-curriculum release confidence for omitted profiles.

### Historical recommendation — withdrawn and superseded by settled Option D

The earlier Option B recommendation is rejected for v1. No pre-release semantic sample, metric, denominator, threshold, or `needs_review` tolerance is an implementation or release requirement.

There is no v1 D12 numeric or sampling payload. A future pre-release semantic gate requires a separately approved governance change.

The limitations of the rejected gold-set alternatives are retained as historical rationale only and do not create a v1 gate.

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

A human review or D12 post-release audit finding may lead to an approved change at the earliest incorrect curriculum configuration, candidate policy, producer/checker instruction, prompt, or universally valid generic-code stage, followed by a complete affected rerun. The producer and checker adjudicate the pair again under updated material inputs; a human finding never directly manufactures, changes, or deletes a published edge. Generated graphs are never hand-edited.

D10's profile-level unresolved-participation state cannot force a relationship, and a manual finding cannot conceal or replace a D13 processing failure. Every rerun remains subject to all settled pair, direction, recurrence, exclusivity, cycle, transitivity, attribution, provenance, structural-validation, checkpoint, and stale-reuse rules.

**LIMIT — Known semantic false positives or false negatives cannot be patched directly in v1.** They require earliest-stage remediation and a new run, may require additional LLM execution, and may still require further iteration.

This decision is separate from D10's reviewed eligibility exception.

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

## 3.3 Decision response template

A concise approval response for this governance simplification amendment can use this form:

```text
I approve the amended Learning Progressions engineering brief, including removal of runtime candidate ranking/tie-breaking and internal candidate-policy versioning, and movement of universal single-valued LP behavior from runtime configuration to code-owned invariants, for dependent Step 2+ implementation.
```

The decision ledger and this amendment are incorporated in full. A response that merely approves an option letter, only part of the brief, or an implementation with unstated exceptions does not approve the amendment. Approval does not authorize an agent to stage or commit. Dependent Step 2 production remediation may resume only after the user explicitly approves the amended brief.

---

# 4. Invariants

The invariants below are settled code-level contracts.

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
58. **SETTLED — D12 non-gate and disclosure:** release has no independent pre-release semantic/gold-set prerequisite; `needs_review` stays visible/nonpublishing/nonblocking, and no structural/process result is represented as pedagogical correctness.

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
| 24   | Add deterministic D12 release-policy conformance coverage                                        | Automated tests and deterministic synthetic fixtures/fakes                                                                                                              | Prove `needs_review` is visible, nonpublishing, and nonblocking; processing failure remains D13-governed; no semantic gold set, metric, threshold, sample, cadence, or human approval is required; structural/process validity cannot be labeled pedagogical truth; audit findings cannot edit generated graphs                                                                                                                                                                                                |
| 25   | Run the targeted six-curriculum structural/process matrix                                        | Deterministic test outputs and validation evidence                                                                                                                      | Madhi: scope-only coordinate; Nigeria: tree baseline; Pratham: DAG/multiple grains; Ghana math: unresolved ancestry/code anomalies; Rwanda: noisy LC evidence cannot auto-publish; Ghana English: recurrence mapping. Validate policy, provenance, counts, identities, checkpoints, and artifacts without a semantic-quality pass/fail claim                                                                                                                                                                   |
| 26   | Explicitly deferred from v1; no implementation or tuning work                                    | None                                                                                                                                                                    | No config, instruction, prompt, semantic-threshold, or generic-code tuning is authorized. Step 25 defects return to the earliest owning step through remediation/review; post-release findings follow D12/D14 remediation and rerun. Future semantic harness/tuning requires approved governance. After Step 25 approval, proceed directly to Step 27                                                                                                                                                          |
| 27   | Run all six complete pipelines from source PDFs                                                  | Six complete result directories                                                                                                                                         | All AS, AS+LC, and AS+LC+LP bundles validate; every candidate/request has successful D13 coverage; no failed pair; provenance, warnings, counts, collisions, projections, material content hashes, checkpoints, and reuse state reconcile. `needs_review` is visible/nonpublishing/nonblocking. No D12 semantic pass is required or claimed                                                                                                                                                                    |
| 28   | Update user-facing pipeline and artifact documentation                                           | New `docs/pipeline/learning-progressions.md`; update architecture, pipeline index, output artifacts, adding-curriculum, running/debugging docs                          | Examples match real artifacts; docs explain canonical undirected `relatesTo` lookup, direct-edge reachability, D10 warnings, D13 resume/failure behavior, optional post-release audit remediation, and every accepted LIMIT including D12/D14 without claiming pedagogical truth                                                                                                                                                                                                                               |
| 29   | Final release review                                                                             | Brief, configs, code, docs, six outputs                                                                                                                                 | Confirm every settled decision and invariant is implemented; Step 27 evidence satisfies structural/provenance/count/collision/D13 requirements; accepted LIMITs are disclosed; D12 absence of a pre-release semantic gate is explicit; no structural/process evidence is presented as pedagogical correctness; no implementation-governing placeholder or unresolved decision remains                                                                                                                          |

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

## 5.2 First full-run sequence

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

Under D12, release review does not require a semantic gold set, human-review sample, metric, threshold, or pass/fail authority. It must instead verify that `needs_review` is visible/nonpublishing/nonblocking, the absence of independent pre-release semantic validation is disclosed as a LIMIT, and no structural/process result or producer/checker agreement is described as pedagogical correctness. Under D14, any known false positive/negative is remediated upstream and rerun rather than patched in a generated graph.
