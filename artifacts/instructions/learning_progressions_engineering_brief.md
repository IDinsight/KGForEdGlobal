# Engineering Brief: Learning Progressions KG Construction

**Status:** Decision draft. No implementation should begin until every **DECIDE** item in Section 3 has an explicit, complete answer—including every concrete matrix, ordered value, threshold, attribution value, and policy parameter the chosen option requires—this file has been updated to record those answers as **SETTLED**, and the updated brief has received an implementation OK.

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

In the current code, `build_kgs()` calls `compile_as_lc_kg(...)` at its final step and discards the returned bundle. The insertion point is therefore explicit:

```python
as_lc_bundle = compile_as_lc_kg(...)

# New downstream phase.
learning_progressions = build_learning_progressions(
    as_lc_bundle=as_lc_bundle,
    ...,
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
- whether same-level `buildsTowards` is permitted;
- relation-specific inclusion and exclusion rules;
- which hierarchy, code, source-order, text, and LC signals are useful for that curriculum;
- curriculum-specific producer and checker instructions;
- unresolved-context policy and reviewed exceptions;
- candidate budgets and request batching.

Examples of code-owned behavior:

- strict Pydantic validation and cross-field checks;
- DAG-safe graph indexing;
- deterministic candidate and request identities;
- bounded candidate generation;
- producer/checker orchestration and resume behavior;
- endpoint, relation-shape, duplicate, cycle, provenance, and count validation;
- deterministic UUIDv5 relationship identities;
- artifact writing, fingerprints, and combined graph compilation.

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
8. semantic review meets the independently sampled metrics, explicit thresholds, denominator rules, minimum-support rules, and `needs_review` release policy chosen in **DECIDE D12**.

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

The storage representation of this conceptually symmetric relationship is still a **DECIDE** item; see **D5**.

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

The config shape—one axis or multiple—is a **DECIDE** item in **D2**.

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

The participating statement types and allowed relation-specific pairings must be explicit in `kgs.lp`; the exact policy shape is **DECIDE D1**.

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
- common action, object, representation, or concept language;
- curriculum-specific named signals.

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

The initial retrieval technology is **DECIDE D3**.

## 2.10 Producer/checker contract

**SETTLED — LP follows the repository's producer/checker architecture.**

For every bounded request:

1. the producer returns a complete structured judgment for every requested candidate pair;
2. an independent checker receives the same bounded evidence, the producer result, generic rules, and curriculum-specific validation instructions;
3. the checker either accepts the result or returns a complete corrected result;
4. Python verifies exact pair coverage, endpoint membership, allowed directions, schema integrity, and request/response identity;
5. only the accepted or checker-corrected result proceeds.

The model may not introduce an endpoint that was absent from the candidate request.

The request schema should support batching through a required positive `lp_request_batch_size`, with a correctness-first default of 1 unless **D4** results in another natural grouping. Batch size changes throughput, not semantics.

## 2.11 Proposed internal records

The final field names should be finalized after Section 3 decisions, but the implementation needs these concepts:

| Record                          | Purpose                                                                                                                      |
|---------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `LPEligibleSFI`                 | Final SFI plus statement type, coordinate, hierarchy status, and eligibility reason                                          |
| `LPCandidatePair`               | Deterministic pair identity, allowed decisions/directions, nomination signals, and bounded evidence references               |
| `LPGenerationRequest`           | One or more exact candidate pairs plus SFI/LC/hierarchy context and config fingerprint                                       |
| `LPPairJudgment`                | One structured accepted, negative, or unresolved judgment for one pair                                                       |
| `LPGenerationResponse`          | Exact complete judgment set for one request                                                                                  |
| `LPGenerationValidationVerdict` | Checker pass/fail, issues, and optional corrected complete response                                                          |
| `LPGenerationFailure`           | Request/pair failures after retries                                                                                          |
| `LPFinalClaim`                  | Reconciled semantic claim before conversion to `Relationship`                                                                |
| `LPGenerationSummary`           | Eligibility, candidate, decision, edge, failure, and distribution counts                                                     |
| `LPUnresolvedItems`             | `needs_review` judgments and exhausted request/pair failures; normal eligibility exclusions remain in the eligibility report |
| `LPValidationReport`            | Standalone LP graph checks and fingerprints                                                                                  |
| `AcademicStandardsLCLPKGBundle` | Complete triplet graph and merged validation state                                                                           |

## 2.12 Proposed combined bundle

**SETTLED — The combined bundle is additive and self-contained.**

Conceptually:

```json
{
  "entity_provenance": {
    "...all existing AS+LC provenance entries...": {},
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

A `no_relation` judgment is a normal adjudication outcome and is counted in the LP summary; it is not an unresolved item. Normal policy-based eligibility exclusions are accounted for in `lp_eligibility_report.json`, not mislabeled as unresolved judgments. `needs_review` judgments and exhausted request/pair failures are unresolved and appear in `lp_unresolved_items.json`.

## 2.13 Illustrative `kgs.lp` shape

The following is illustrative only. Field names and nesting must be updated after the **DECIDE** items are resolved.

```json
{
  "lp": {
    "generation_instructions": "Curriculum-specific progression policy...",
    "generation_validation_instructions": "Independent checker policy...",

    "developmental_dimension": {
      "statement_type": "Grade",
      "ordered_values": ["BASIC 1", "BASIC 2", "BASIC 3"]
    },

    "builds_towards": {
      "allowed_statement_type_pairs": [
        {
          "source_statement_type": "Indicator",
          "target_statement_type": "Indicator"
        }
      ],
      "allow_same_level": true,
      "max_forward_steps": null
    },

    "relates_to": {
      "allowed_statement_type_pairs": [
        {
          "left_statement_type": "Indicator",
          "right_statement_type": "Indicator"
        }
      ],
      "allow_cross_level": true
    },

    "candidate_generation": {
      "max_candidates_per_sfi": 24,
      "max_candidates_per_signal": 8,
      "use_hierarchy_context": true,
      "use_shared_learning_components": true,
      "use_text_similarity": true
    },

    "lp_request_batch_size": 1,
    "lp_max_failed_pair_rate": 0.0,
    "lp_max_failed_pair_count": 0
  }
}
```

The config must be cross-validated against `kgs.as.statement_type_policy`, controlled values, identity scope, and the final local coordinate vocabulary. Unknown or contradictory policy must fail at config load time.

## 2.14 Core limitations

**LIMIT — Candidate blocking creates a recall ceiling.**

The adjudicator can only accept pairs that candidate generation nominates. Strong producer/checker accuracy cannot recover a relationship that deterministic retrieval never surfaced. Candidate-recall evaluation is therefore as important as final-edge precision.

**LIMIT — Upstream AS and LC errors can influence LP.**

LP endpoints are stable SFIs, but hierarchy errors, missing scope values, faulty unresolved flags, or weak LC decomposition can change candidate evidence. LP provenance must retain the upstream bundle fingerprint so these dependencies are visible.

**LIMIT — The six reviewed examples are English-language artifacts.**

The normalization code is Unicode-aware, but candidate retrieval and progression prompts have not yet been validated on non-English or multilingual progression judgments. A lexical-only first implementation may require language-specific tuning later.

**LIMIT — Structural validation cannot prove pedagogical correctness.**

Pydantic and graph checks can prove endpoint integrity, deterministic identities, policy compliance, and artifact reconciliation. They cannot prove that an inferred progression is instructionally sound. That requires the evaluation decision in **D12** and human review.

---

# 3. Decisions to make before writing code

## 3.1 Marker contract

The following markers are load-bearing and must be preserved in this file.

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

An option letter alone does **not** settle a decision when the selected option requires curriculum matrices, local orders, failure limits, attribution values, evaluation thresholds, reviewer authority, or another concrete payload. Such a decision remains **DECIDE** until the payload is recorded.

### **LIMIT**

A known weakness or scope boundary we are accepting. It must be documented where a future reader will encounter it. It must not be silently hidden by code or prompts.

## 3.2 Decision log

| ID  | Decision                                                             | Recommended option                                                                                    | Status       |
|-----|----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|--------------|
| D1  | How LP progression grain and statement-type pairings are configured  | B — relation-specific pair matrices                                                                   | **DECIDE**   |
| D2  | How local developmental order is represented                         | A — one explicit primary ordered dimension for v1                                                     | **DECIDE**   |
| D3  | Candidate nomination technology                                      | A — explainable deterministic multi-signal retrieval, no embeddings in v1                             | **DECIDE**   |
| D4  | Candidate-pair orientation and adjudication shape                    | A — one canonical pair, one unified relation/direction judgment                                       | **DECIDE**   |
| D5  | How conceptually symmetric `relatesTo` is serialized                 | A — one canonical relationship per unordered pair                                                     | **DECIDE**   |
| D6  | Whether one pair may publish both relation types                     | A — mutually exclusive; `buildsTowards` takes precedence                                              | **DECIDE**   |
| D7  | How recurring practice is mapped                                     | C — extension → `buildsTowards`; substantive recurrence → `relatesTo`; generic repetition → none      | **DECIDE**   |
| D8  | `buildsTowards` cycle policy                                         | A — published graph must be acyclic                                                                   | **DECIDE**   |
| D9  | Transitive edge policy                                               | A — publish only directly adjudicated edges; no automatic closure or reduction                        | **DECIDE**   |
| D10 | How unresolved AS ancestry affects LP                                | C1 — exclude by default; permit inline, reviewed UUID-keyed eligibility exceptions                    | **DECIDE**   |
| D11 | Attribution/ownership metadata for inferred LP edges                 | B — dedicated LP relationship metadata                                                                | **DECIDE**   |
| D12 | Semantic evaluation and release thresholds                           | B — independently assembled gold set per curriculum, with explicit thresholds and `needs_review` gate | **DECIDE**   |
| D13 | Failed-request tolerance and release gate                            | B — configurable dual guard; zero failed pairs for the six release runs                               | **DECIDE**   |
| D14 | Manual semantic edge overrides in v1                                 | A — no forced semantic edges in v1; only reviewed eligibility exceptions                              | **DECIDE**   |

**Decision-completeness rule:** every row remains **DECIDE** until both the option and all option-required parameters are recorded. D1, D2, D11, D12, and D13 always require more than a letter; D10 also requires C1/C2 plus exception-state details when Option C is selected; D3 and D14 require additional concrete payloads when their non-default infrastructure/override options are selected. Alternatives explicitly labeled **Rejected alternative** below are retained for rationale only and are not selectable without first reopening the conflicting **SETTLED** rule.

---

## D1. Progression grain and statement-type pair policy

**DECIDE — How should each curriculum declare which SFI types can participate and which type combinations are valid?**

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

### Option A — One flat LP statement-type allowlist

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

### Option B — Relation-specific statement-type pair matrices

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

### Option C — Default all normalized Standards, with exclusions

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

### Recommendation

Choose **Option B**.

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

**Required D1 payload:** selecting the schema shape does not approve the six curriculum policies. Step 0 must also record the initial participation policy for Madhi mathematics, Nigeria mathematics, Pratham science, Rwanda mathematics, Ghana mathematics, and Ghana English in the form required by the selected option: relation-specific pair matrices for Option B, an allowlist for Option A, or exclusions for Option C. Directional source/target orientation must be explicit where applicable, and every omitted type/pair must have an unambiguous inclusion/exclusion meaning.

---

## D2. Local developmental-order model

**DECIDE — Should v1 support one progression coordinate or multiple coordinates?**

### Option A — One explicit primary ordered dimension

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

### Option B — Multiple ordered dimensions

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

### Recommendation

Choose **Option A** for v1.

Relation-specific config can still decide:

- whether same-level `buildsTowards` is allowed;
- maximum forward level gap;
- whether `relatesTo` may cross any number of levels;
- what to do with standards missing the coordinate.

**LIMIT if Option A is chosen:** Frameworks with genuine multi-axis progression will require a later schema extension rather than silent heuristic inference.

**Required D2 payload:** selecting one-axis or multi-axis support does not by itself define the six profiles. Step 0 must record, for each curriculum, the canonical coordinate source, the exact ordered local values, missing/unknown-coordinate behavior, whether same-level `buildsTowards` is allowed, any maximum forward gap, and the permitted cross-level behavior for `relatesTo`. If Option B is selected, it must also define how coordinate tuples are compared, when a direction is admissible across mixed dimensions, and how incomparability is represented.

---

## D3. Candidate nomination technology

**DECIDE — What should retrieve plausible pairs before LLM adjudication?**

### Option A — Explainable deterministic multi-signal retrieval, without embeddings

Candidate pairs are the union of bounded nominations from named rules such as:

```text
same/related local hierarchy context
adjacent or allowed progression ranks
shared exact LC
related LC tokens/tags
SFI token or character-ngram similarity
reviewed code-prefix signal
curriculum-specific rule
```

Each rule records why it nominated the pair. The union is deduplicated and bounded by per-rule and per-SFI budgets.

**ELI5:** Several transparent scouts each suggest a short list and say why. The judge reviews the combined list.

**Pros**

- Auditable.
- Reuses current repository patterns from LC dedup and hasChild candidate generation.
- No new model/service/dependency.
- Deterministic and easy to fixture-test.

**Cons**

- May miss conceptually related standards with little wording or hierarchy overlap.
- Multilingual recall may be weak.
- Threshold tuning is curriculum-sensitive.

### Option B — Add semantic embeddings/ANN as another nomination signal

**ELI5:** Add a scout that finds ideas that “mean similar things” even when they use different words.

**Pros**

- Better semantic recall for paraphrases and cross-domain links.
- Useful for `relatesTo` and progression pairs with low lexical overlap.

**Cons**

- Introduces an embedding model, dependency, cache, version, cost, and reproducibility contract.
- Requires multilingual evaluation.
- Similarity still does not prove progression direction.
- The user-set requirement currently names one shared KG LLM, not an embedding stack.

### Rejected alternative — Let an LLM nominate from large allowed cohorts

For example, show one source standard and all allowed standards in the next grade/domain, then ask for likely candidates. This is **not selectable** under the current brief because candidate generation is already **SETTLED** as deterministic, explainable, and bounded before any LLM call. Selecting this approach would first require reopening that architecture boundary.

Additional drawbacks are large cohorts, higher token cost, positional bias, weaker complete-consumption guarantees, and a less auditable candidate-discovery path.

### Recommendation

Choose **Option A** for v1, while implementing candidate rules behind a small strategy interface so embeddings can be added later without changing final schemas.

**LIMIT if Option A is chosen:** Candidate recall is expected to be the largest semantic weakness of v1. D12 must explicitly test candidate recall, not only edge precision.

**Required D3 payload if Option B is chosen:** record the exact embedding model/provider and version, multilingual scope, dependency/runtime boundary, vector/cache persistence and invalidation rules, batching/cost controls, deterministic fingerprint inputs, and behavior when the embedding capability is unavailable. Selecting “embeddings” without these values leaves D3 unresolved.

---

## D4. Candidate-pair orientation and adjudication shape

**DECIDE — Should one conceptual pair be judged once, or should each relation/direction be processed separately?**

### Option A — One canonical unordered pair, one unified judgment

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

### Option B — Directed candidate records

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

### Option C — Separate pipelines for `buildsTowards` and `relatesTo`

**Pros**

- Prompts can be narrowly specialized.
- Candidate retrieval can be relation-specific.

**Cons**

- Same pair may receive inconsistent outputs.
- More artifacts, agents, retries, and cost.
- Pair exclusivity becomes a late conflict-resolution problem.

### Recommendation

Choose **Option A**. Candidate nomination may still record relation-specific reasons, but the semantic decision is made once per pair.

---

## D5. `relatesTo` serialization

**DECIDE — `relatesTo` is conceptually symmetric, but every Learning Commons relationship row has a source and target. How should one relation be stored?**

### Option A — One canonical relationship per unordered pair

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

### Option B — Emit reciprocal relationships

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

### Recommendation

Choose **Option A**.

**LIMIT if Option A is chosen:** Combined-graph documentation and ingestion checks must explicitly state that `relatesTo` is stored once but semantically symmetric.

---

## D6. Can one pair publish both `buildsTowards` and `relatesTo`?

**DECIDE — May one logical SFI pair publish both relationship types?**

### Option A — Mutually exclusive, with `buildsTowards` precedence

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

### Option B — Allow both

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

### Recommendation

Choose **Option A**.

The unified pair response then has one published relationship at most.

---

## D7. Recurring practice and repeated skills

**DECIDE — What should happen when the same or nearly the same capability appears again at a later level?**

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

### Option A — Treat later recurrence as `buildsTowards`

**Pros**

- Produces rich vertical pathways.
- Learning Commons does not require strict prerequisites.

**Cons**

- Overstates simple repetition.
- Generic LCs can create false progressions.

### Option B — Treat recurrence as `relatesTo`

**Pros**

- Avoids claiming dependency.
- Captures curriculum continuity.

**Cons**

- A genuine developmental extension may be under-described.
- Could make `relatesTo` a dumping ground for repeated text.

### Option C — Classify by substantive change

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

### Recommendation

Choose **Option C**.

`recurring_practice` may be retained as an internal reason/category, but it will not become a third published relationship type.

---

## D8. `buildsTowards` cycle policy

**DECIDE — Must the published `buildsTowards` graph be acyclic?**

### Option A — Forbid every directed cycle

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

### Option B — Forbid cross-level cycles but allow same-level cycles

**Pros**

- Represents same-grade mutual development.
- Maintains forward grade progression.

**Cons**

- Same-level cycle semantics are difficult to distinguish from `relatesTo`.
- Topological use becomes conditional.

### Option C — Allow cycles and report them

**Pros**

- Maximum expressiveness.

**Cons**

- Downstream “prerequisite” traversal can loop.
- Harder to explain and validate.
- May hide direction errors.

### Recommendation

Choose **Option A**.

**LIMIT if Option A is chosen:** Spiral curricula may be represented less richly. Conceptual mutuality should be captured through `relatesTo` rather than cyclic `buildsTowards` edges.

---

## D9. Transitive edge policy

**DECIDE — Should finalization publish only directly adjudicated `buildsTowards` edges, add transitive closure, or reduce transitive edges?**

Suppose the graph contains:

```text
A --buildsTowards--> B --buildsTowards--> C
```

### Option A — Publish only directly adjudicated edges

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

### Option B — Materialize transitive closure

Automatically add every reachable `A -> C`.

**Pros**

- Fast ancestor/prerequisite queries.

**Cons**

- Dense graph.
- Generated edges have weaker provenance.
- “A helps B” and “B helps C” do not always prove that A directly helps C enough to publish.

### Option C — Apply transitive reduction

Remove `A -> C` whenever A can already reach C through other nodes.

**Pros**

- Sparse graph.
- Highlights a minimal path structure.

**Cons**

- Can delete a meaningful direct relationship.
- Reduction may not be unique when cycles are allowed.

### Recommendation

Choose **Option A**.

---

## D10. Unresolved Academic Standards ancestry

**DECIDE — May an SFI with unresolved self/ancestry participate in LP inference, and under what review policy?**

Ghana math contains 13 unresolved root-fallback hierarchy edges; Ghana English contains 2. A passed AS validation report therefore does not mean that every SFI has trustworthy curricular placement.

### Option A — Exclude every SFI with unresolved self or ancestry

**Pros**

- Conservative.
- No progression inference uses compromised hierarchy context.

**Cons**

- Can omit standards whose text, grade, code, and LCs are still sufficient.
- Ghana coverage decreases.

### Option B — Include unresolved SFIs automatically with an evidence warning

**Pros**

- Better coverage.
- Other signals may compensate.

**Cons**

- LLM may overtrust a misleading root placement.
- Harder to explain why some uncertain standards received edges.

### Option C — Exclude by default, permit explicit reviewed eligibility exceptions

A reviewed exception identifies the SFI, reviewer, review time, rationale, and which evidence may be used despite unresolved hierarchy. The exception changes only LP eligibility/evidence use; it never forces a semantic edge.

Because Step 2 must define a concrete schema, choosing Option C also requires choosing where reviewed exceptions live:

| Sub-option | Representation                                                                                                    | ELI5                                                              | Advantages                                                                             | Costs                                                                                                                |
|------------|-------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|----------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| **C1**     | Inline `kgs.lp` list keyed by final SFI `case_identifier_uuid`, with reviewer/time/rationale/evidence permissions | Keep each signed exception card inside the curriculum's LP policy | Smallest v1 surface; one config fingerprint; straightforward deployment and validation | Mixes general policy with a small number of instance decisions; UUIDs must be revisited if upstream identity changes |
| **C2**     | Separate reviewed sidecar referenced and fingerprinted by `kgs.lp`                                                | Keep exception cards in a separate audited binder                 | Cleaner policy/data separation; scales better if exceptions grow                       | Adds a file lifecycle, path/schema validation, deployment, and review surface                                        |

**Pros**

- Safe default with a controlled escape hatch.
- Mirrors the current LC unresolved-context philosophy.
- Audit-friendly.

**Cons**

- Requires manual review for exceptions.
- Exception selectors must be stable and fingerprinted.

### Recommendation

Choose **Option C1** for v1 unless expected exception volume justifies a sidecar.

The eligibility and candidate artifacts must always retain unresolved status, even for an approved exception. The exception record must be part of every material config/policy fingerprint.

**Required D10 payload if Option C is chosen:** choose C1 or C2 and record either an explicit initial value of “no exceptions” or the complete reviewed exception records, including stable selector, reviewer identity, review time, rationale, permitted evidence, and upstream/config fingerprint rules.

---

## D11. Attribution and ownership for inferred progression edges

**DECIDE — Whose authorship, provider identity, license, and attribution statement should the inferred LP relationships carry?**

Every `Relationship` requires `author`, `provider`, `license`, and `attribution_statement`. Current `hasChild` and `supports` edges inherit framework metadata. LP edges are different: they may be inferred by this pipeline rather than explicitly authored by the ministry or curriculum publisher.

### Option A — Inherit all framework metadata unchanged

**Pros**

- Simple.
- Consistent with current relationships.

**Cons**

- Can misleadingly imply that the source authority authored the inferred progression.
- Makes it difficult to distinguish source content from pipeline analysis.

### Option B — Add dedicated LP relationship metadata in `kgs.lp`

Conceptually:

```json
{
  "relationship_metadata": {
    "author": "<inference author>",
    "provider": "<graph provider>",
    "license": "<license for inferred relationships>",
    "attribution_statement": "Inferred from <source framework> by <pipeline/provider>."
  }
}
```

The final internal metadata also references the source framework author and license.

**Pros**

- Honest distinction between source and inference.
- Matches the Learning Commons pattern where the progression author and graph provider can differ.
- Clear release/legal boundary.

**Cons**

- Requires explicit organizational and licensing decisions.
- Adds config fields.

### Option C — Mixed metadata

For example, source authority as `author`, pipeline as `provider`, and inference disclosure only in attribution/metadata.

**Pros**

- Preserves source ownership prominence.

**Cons**

- `author` remains ambiguous for an inferred assertion.
- Different consumers may interpret it differently.

### Recommendation

Choose **Option B** and provide the exact values before coding the finalizer.

**Required D11 payload:** record the exact `author`, `provider`, `license`, and attribution-statement template, identify who approved those values, and define which source-framework authorship/license fields are retained in internal provenance. Placeholders or “inherit later” language leave D11 unresolved.

**LIMIT — This brief is not legal advice.** The relationship license and attribution wording require the project's legal/content-licensing owner to approve them.

---

## D12. Semantic evaluation and release gate

**DECIDE — What independently reviewed evidence, metric thresholds, and unresolved-judgment policy are required before accepting the generated graph?**

### Option A — Independently selected manual review protocol

Reviewers inspect a documented set of independently selected expected-positive and expected-negative pairs, plus stratified samples of accepted, `no_relation`, and `needs_review` outputs, and issue an explicit pass/fail decision.

**Pros**

- Fastest start.
- Lower annotation and harness effort.
- Can still expose obvious candidate-recall and edge-quality failures when pair selection is independent.

**Cons**

- No stable numeric regression metric.
- Reviewer judgment and sample choice have more influence.
- Harder to compare releases or detect gradual quality drift.

### Option B — Small, independently assembled reviewed gold set for every curriculum

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

### Option C — Gold sets for representative curricula only

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

### Recommendation

Choose **Option B**, record the complete threshold/denominator/`needs_review` policy during Step 0, and prioritize accepted-edge precision over global recall for the first release. A false progression edge is more damaging than an omitted edge, but candidate recall must still be measured on independently selected positive pairs so omission remains visible rather than silently accepted.

**Required D12 payload:** record the review/approval authority, minimum sample size and required case balance, sampling method, treatment of ambiguous/disputed cases, exact metrics and denominators, per-curriculum and aggregate numeric thresholds, `needs_review` release policy, and versioning/reapproval rule after semantic-policy changes. Option A instead requires a complete independent-selection/manual-review protocol and named pass/fail authority.

**LIMIT if Option B or C is chosen:** A small reviewed set estimates semantic quality; it does not prove correctness for every possible pair. Report sample sizes and uncertainty plainly, and do not treat a threshold pass as empirical prerequisite truth.

---

## D13. Failed-request tolerance and release policy

**DECIDE — What failed-pair count/rate may an exploratory run tolerate, and what stricter condition is required for release?**

A valid `no_relation` or `needs_review` response is not a request failure. Failures are timeouts, malformed outputs, exhausted retries, missing pair coverage, or integrity violations.

### Option A — Any failed pair fails the whole LP run

**Pros**

- Complete processing guarantee.
- Simple release meaning.

**Cons**

- Brittle for large runs.
- One transient model failure blocks every artifact.

### Option B — Configurable dual guard plus strict release policy

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

### Option C — Always continue and report failures

**Pros**

- Maximum robustness.

**Cons**

- Completeness can degrade silently.
- Weak release contract.

### Recommendation

Choose **Option B**.

Confidence should be stored for audit, but a numeric confidence threshold must not bypass the checker. Acceptance is based on a valid final judgment and policy, not confidence alone.

**Required D13 payload:** if Option B is selected, Step 0 must record the schema defaults or required per-profile values for maximum failed-pair rate and maximum failed-pair count, plus the release-time limits. The recommendation's intended release limit is zero failed pairs for each of the six example curriculum runs, but that value is not settled until recorded.

---

## D14. Manual semantic edge overrides

**DECIDE — May v1 force reviewed semantic include/exclude decisions, and if so where are they stored?**

This decision is separate from D10's reviewed eligibility exception.

### Option A — No forced semantic edges in v1

Operators can adjust config/prompts/candidate policy and rerun, but cannot force a particular `buildsTowards` or `relatesTo` edge into the production graph.

**Pros**

- Smaller scope.
- Avoids mixing hand-authored and generated semantics before the automated path is stable.
- Keeps one inference contract.

**Cons**

- Known false negatives cannot be patched directly.
- Repeated tuning may be expensive.

### Option B — Put forced include/exclude decisions directly in runtime config

**Pros**

- Easy deployment with the curriculum profile.

**Cons**

- Large configs become data patches.
- Policy and reviewed instance decisions are mixed.
- UUID-based overrides can be hard to maintain across identity-policy changes.

### Option C — Separate reviewed sidecar file referenced by config

Each row records pair identity, chosen decision, reviewer, timestamp, notes, and upstream bundle fingerprint.

**Pros**

- Strong auditability.
- Clean separation of policy and instance-level review.
- Can support include, exclude, or needs-review resolution.

**Cons**

- Additional artifact and lifecycle.
- More implementation/test work.

### Recommendation

Choose **Option A** for v1. Implement D10's narrow unresolved-eligibility exception, but defer forced semantic relationships until automated behavior and the gold-set workflow are stable.

**LIMIT if Option A is chosen:** Known semantic false negatives require policy/prompt changes and reruns rather than a release-side patch.

**Required D14 payload if Option B or C is chosen:** record the allowed override actions, stable pair/SFI selectors, reviewer identity and evidence fields, precedence relative to producer/checker output, stale-upstream-fingerprint behavior, conflict handling, and provenance/export treatment. Selecting an override location without these semantics leaves D14 unresolved.

---

## 3.3 Decision response template

A concise response can use this form, but the attached payloads are part of the decision—not optional follow-up:

```text
D1: B — attach the approved buildsTowards/relatesTo statement-type pair matrix for all six curricula
D2: A — attach each curriculum's coordinate source, ordered values, missing-value policy, same-level rule, and level-gap rules
D3: A
D4: A
D5: A
D6: A
D7: C
D8: A
D9: A
D10: C1 — initial reviewed exceptions: none | attach exception records
D11: B — author=<exact>; provider=<exact>; license=<exact>; attribution=<exact approved template>
D12: B — gold approver=<exact>; sampling=<exact>; minimum set=<exact>; thresholds/denominators=<exact>; needs_review policy=<exact>; ambiguity/reapproval policy=<exact>
D13: B — exploratory max rate=<exact>; exploratory max count=<exact>; release max rate/count=<exact>
D14: A
```

D2, D3, and D5 intentionally have only A/B selectable under the current **SETTLED** rules; the recorded rejected alternatives are not valid responses. D6 also has only A/B because separate requests are governed by D4 rather than being a publication-policy answer. Any hybrid choice must be written in full. This file must then be revised so the decision log, detailed decision section, invariants, illustrative config, and build-order/test language all agree before implementation begins.

---

# 4. Invariants

The invariants below are the code-level contracts. Items dependent on unresolved decisions remain explicitly marked and must be converted to **SETTLED** after Section 3 is answered.

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
11. **D1-dependent invariant:** only approved relation-specific statement-type pairs may become candidates or final edges.
12. **D2-dependent invariant:** every direction/rank check follows the approved local developmental-coordinate model.
13. **D10-dependent invariant:** unresolved SFI eligibility follows the approved exclusion/exception policy exactly and is visible in artifacts.

## 4.3 Candidate generation

14. **SETTLED — No self-pairs.**
15. **SETTLED — Deterministic pair identity:** the same doc_key, endpoints, and approved pair-orientation policy produce the same candidate ID.
16. **SETTLED — Exact uniqueness:** a logical pair is represented once under the approved D4 policy.
17. **SETTLED — Bounded retrieval:** candidate budgets are applied before any LLM call.
18. **SETTLED — Explainable nomination:** every pair records one or more named nomination reasons and the values that triggered them.
19. **SETTLED — No automatic semantic edge:** hierarchy, LC overlap, text similarity, code proximity, rank proximity, or source order can nominate but cannot publish a relationship.
20. **SETTLED — Audit flags propagate:** known AS code, merge, and unresolved anomalies remain available to producer/checker and final provenance.
21. **D3-dependent invariant:** candidate retrieval uses only the approved signal technologies and versions, all of which are fingerprinted.

## 4.4 LLM adjudication

22. **SETTLED — Exact request coverage:** every candidate assigned to a request appears exactly once in that request set.
23. **SETTLED — Exact response coverage:** every successful response contains exactly one judgment per requested pair; no missing or extra pair IDs.
24. **SETTLED — Endpoint containment:** the producer/checker cannot introduce SFIs outside the request.
25. **SETTLED — Allowed decisions only:** Python rejects a relationship type or direction not allowed for that pair.
26. **SETTLED — Same evidence for checker:** the checker receives the same bounded evidence plus the producer draft; it cannot rely on hidden global context.
27. **SETTLED — Complete correction:** a failing checker returns a complete corrected response, not an incremental patch.
28. **SETTLED — Negative vs ambiguity:** `no_relation`, `needs_review`, and processing failure remain distinct states.
29. **SETTLED — Confidence is audit data:** confidence does not by itself accept an edge or bypass the checker.
30. **D7-dependent invariant:** recurring-practice judgments map only according to the approved policy.
31. **D13-dependent invariant:** failed requests obey the approved rate/count guards and release policy.

## 4.5 Final relationships

32. **SETTLED — Ontology shape:** only SFI → SFI `buildsTowards` and SFI → SFI `relatesTo` are emitted.
33. **SETTLED — Endpoint keys:** both endpoints use `case_identifier_uuid`.
34. **SETTLED — Endpoint existence:** every source and target resolves to a final SFI in the upstream bundle.
35. **SETTLED — No self-loops.**
36. **SETTLED — Deterministic relationship UUID:** UUIDv5 identity is derived from doc_key, relationship type, and finalized endpoints; the LLM never chooses it.
37. **SETTLED — Correct description:** `buildsTowards` does not claim a strict prerequisite; `relatesTo` does not imply sequence/dependency.
38. **SETTLED — Complete provenance:** every edge resolves to candidate ID, request ID, producer/checker outcome, evidence summary, config/input fingerprints, and source framework.
39. **D4-dependent invariant:** pair orientation and conflict reconciliation follow the approved unified/directed model.
40. **D5-dependent invariant:** `relatesTo` storage and identity follow the approved canonical/reciprocal rule.
41. **D6-dependent invariant:** same-pair multi-relation behavior follows the approved exclusivity rule.
42. **D8-dependent invariant:** the final graph obeys the approved cycle policy.
43. **D9-dependent invariant:** finalization applies exactly the approved transitive policy and never silently materializes or deletes edges.
44. **D11-dependent invariant:** relationship author/provider/license/attribution values follow the approved inference-ownership policy.
45. **D14-dependent invariant:** manual semantic decisions, if supported, are explicit, reviewed, fingerprinted, and still pass every structural validator.

## 4.6 Artifacts, resume, and combined export

46. **SETTLED — Prefix-safe resume:** existing JSONL progress is reused only when it forms a valid deterministic prefix for current requests and fingerprints.
47. **SETTLED — Stale final bundle rejection:** `overwrite=false` reuses `as_lc_lp_kg_bundle.json` only when all material LP and upstream fingerprints match.
48. **SETTLED — Failed validation remains inspectable:** final artifacts may be written for diagnosis, but `kg_run.json` cannot report success when LP or combined validation fails.
49. **SETTLED — Count reconciliation:** summary counts equal actual list and JSONL counts for eligible SFIs, candidates, judgments, failures, final relationships, nodes, and all four relationship types.
50. **SETTLED — Identifier collision absence:** framework, SFI, LC, `hasChild`, `supports`, `buildsTowards`, and `relatesTo` identifiers are unique in the combined graph.
51. **SETTLED — Existing artifacts are not mutated:** standalone AS and AS+LC bundle/projection schemas remain unchanged.
52. **SETTLED — Upstream combined content is preserved:** the AS+LC framework, SFIs, LCs, `hasChild`, `supports`, summaries, unresolved data, and complete `entity_provenance` mapping are copied without deletion or reshaping before additive LP fields/provenance are introduced.
53. **SETTLED — Combined node parity:** `as_lc_lp_nodes.jsonl` contains exactly the framework, SFI, and LC nodes in the combined bundle.
54. **SETTLED — Combined relationship completeness:** `as_lc_lp_relationships.jsonl` contains exactly all `hasChild`, `supports`, `buildsTowards`, and `relatesTo` relationships in the combined bundle.
55. **SETTLED — Ordering is serialization detail:** deterministic ordering is used for stable files, but downstream semantics rely on IDs, endpoint keys, and relationship types rather than line order.
56. **D12-dependent invariant:** the six-curriculum release is not accepted until the approved independent-sampling method, semantic metrics, explicit thresholds/denominators, minimum-support rules, and `needs_review` policy all pass.

---

# 5. Build order

No implementation starts at Step 1 until Step 0 is complete.

| Step | One reviewable implementation aspect                                                             | Primary files/outputs                                                                                                                           | Review and test boundary                                                                                                                                                                                                                                                                           |
|------|--------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 0    | Resolve every **DECIDE** item and update this brief                                              | This Markdown file                                                                                                                              | All D1–D14 rows become **SETTLED** with chosen option, rationale, and every required concrete payload; no implementation-governing placeholder remains; implementation OK recorded                                                                                                                 |
| 1    | Establish six-curriculum LP regression fixtures                                                  | New reduced fixtures under the repository's test area                                                                                           | Fixture loader verifies current AS+LC bundle shapes, counts, DAG parents, unresolved flags, and LC alignments without changing production code                                                                                                                                                     |
| 2    | Define the intrinsic `kgs.lp` Pydantic models and standalone field validators                    | `backend/src/kgfeg/schemas.py`                                                                                                                  | Tests cover field types, aliases, forbidden extras, local model invariants, and representative valid/invalid LP policy objects; `CreateKGConfig` and checked-in configs remain valid until Step 3                                                                                                  |
| 3    | Wire `kgs.lp` as required, add AS-policy cross-validation, and update all six configs atomically | `backend/src/kgfeg/schemas.py`; `examples/**/config*.json`                                                                                      | Tests cover missing `lp`, unknown statement types/local values, bad or incomplete local order, invalid pair matrices, contradictory policies, cross-curriculum copy/paste errors, and successful validation/snapshots of all six explicit profiles                                                 |
| 4    | Replace dormant LP schemas with the approved intrinsic pair/evidence/judgment record schemas     | `backend/src/kgfeg/kgs/schemas.py`                                                                                                              | Schema tests cover valid accepted/negative/review records and reject intrinsic defects such as malformed UUIDs, self-pairs, illegal enum combinations, and invalid confidence/rationale fields; request-relative endpoint/coverage checks remain owned by Steps 12 and 14                          |
| 5    | Add LP usage buckets and model-settings plumbing using `LLM_KG_MODEL`                            | `kgs/llm.py`, existing model registry calls                                                                                                     | Usage serialization includes producer/checker buckets and `kgs_settings("learning_progressions")` is exercised; no prompt or agent factory is implemented before Steps 13–14, and no new model environment setting is introduced                                                                   |
| 6    | Build an AS+LC graph index that is safe for trees and DAGs                                       | New `kgs/lp_index.py` or equivalent                                                                                                             | Tests verify Pratham multi-parent ancestry, framework-root handling, LC-by-SFI indexes, and deterministic traversal order                                                                                                                                                                          |
| 7    | Implement local developmental-coordinate resolution                                              | New LP index/selection utility                                                                                                                  | Tests cover Madhi scope-only Class, explicit Grade/Class nodes, missing values, aliases/canonical values, and approved D2 behavior                                                                                                                                                                 |
| 8    | Implement LP SFI eligibility and unresolved-policy reporting                                     | New `kgs/lp_selection.py`; `lp_eligible_sfis.json`; `lp_eligibility_report.json`                                                                | Tests cover D1 pair participation, D10 unresolved handling, independent LC eligibility, multiple Standard grains, and exact exclusion counts                                                                                                                                                       |
| 9    | Implement hard candidate filters and deterministic pair IDs                                      | New `kgs/lp_candidates.py`                                                                                                                      | Tests prove no self-pairs, no disallowed type pairs/directions, no duplicate logical pairs, and stable IDs under input reordering                                                                                                                                                                  |
| 10   | Implement named candidate evidence features                                                      | `lp_candidates.py` or `lp_evidence.py`                                                                                                          | Each signal is unit-tested independently: hierarchy/DAG context, local rank, LC overlap, text similarity, code/audit handling, and curriculum-specific hooks                                                                                                                                       |
| 11   | Implement candidate union, ranking, and budgets                                                  | `lp_candidates.py`; `lp_candidate_pairs.jsonl`; `lp_candidate_summary.json`                                                                     | Deterministic snapshot tests verify per-rule nomination, top-k behavior, tie-breaking, pair-scale bounds, and D3 technology policy                                                                                                                                                                 |
| 12   | Implement bounded LP request construction                                                        | New `kgs/lp_generation.py`; `lp_generation_requests.jsonl`                                                                                      | Tests verify exact candidate coverage, bounded context, all parent paths, LC evidence limits, config/input fingerprints, and stable request IDs                                                                                                                                                    |
| 13   | Implement the LP producer prompt and agent                                                       | `kgs/prompts.py`, `kgs/agents.py`, `kgs/llm.py`                                                                                                 | Prompt fixtures demonstrate Learning Commons semantics, curriculum-specific instructions, explicit negative/review outputs, and no out-of-request endpoints                                                                                                                                        |
| 14   | Implement the independent LP checker and deterministic integrity validators                      | `kgs/prompts.py`, `kgs/agents.py`, `kgs/validators.py`, `kgs/llm.py`                                                                            | Tests cover accept, complete correction, missing pair, extra pair, illegal direction, unsupported rationale, and producer/checker evidence parity                                                                                                                                                  |
| 15   | Implement resumable generation orchestration and failure accounting                              | `kgs/lp_generation.py`; draft/verdict/final/failure artifacts                                                                                   | Interrupted-prefix tests verify safe resume, retry scope, exact file alignment, D13 guards, and that `no_relation` is not a failure                                                                                                                                                                |
| 16   | Reconcile final pair decisions under D4–D9                                                       | New `kgs/lp_finalization.py`; `lp_final_claims.json`                                                                                            | Tests cover relation precedence/exclusivity, recurring-practice mapping, canonical `relatesTo`, cycle policy, transitive policy, duplicate/conflict handling, and `needs_review` routing                                                                                                           |
| 17   | Mint deterministic `Relationship` records and correct generic descriptions                       | `kgs/lp_finalization.py`, `kgs/schemas.py`                                                                                                      | UUID snapshot tests cover both types; endpoint shape and metadata tests verify Learning Commons-compatible semantics and D11 attribution                                                                                                                                                           |
| 18   | Implement standalone LP graph validation                                                         | New LP validator or `kgs/validators.py`                                                                                                         | Tests cover endpoint existence, self-loops, duplicates, direction/rank policy, cycle/transitivity policy, provenance coverage, counts, collisions, unresolved consistency, and failure limits                                                                                                      |
| 19   | Write standalone LP provenance, summary, unresolved, and validation artifacts                    | `lp_relationship_provenance.json`, `lp_generation_summary.json`, `lp_unresolved_items.json`, `lp_validation_report.json`, relation files        | Round-trip Pydantic validation and count reconciliation tests cover every artifact                                                                                                                                                                                                                 |
| 20   | Add the AS+LC+LP bundle schema and compiler                                                      | New `kgs/lp_export.py`, `kgs/schemas.py`                                                                                                        | Bundle tests verify all upstream nodes, relationships, summaries, unresolved data, and the complete AS+LC `entity_provenance` mapping are preserved verbatim; LP fields/provenance are additive; fingerprints are complete; invalid LP blocks successful compilation                               |
| 21   | Write `as_lc_lp_nodes.jsonl` and `as_lc_lp_relationships.jsonl`                                  | `kgs/lp_export.py`                                                                                                                              | Projection tests prove node parity with AS+LC and exact relationship union/order across all four types                                                                                                                                                                                             |
| 22   | Integrate LP into `create_kgs.build_kgs()` after `compile_as_lc_kg()`                            | `entries/create_kgs.py`                                                                                                                         | Orchestration test verifies phase order, returned bundle use, failure propagation, usage accounting, and `kg_run.json` success/error state                                                                                                                                                         |
| 23   | Implement final-bundle reuse and stale-fingerprint detection                                     | `lp_export.py`, LP utilities                                                                                                                    | Tests cover `overwrite=false` reuse, changed config/candidate/response/upstream fingerprints, projection rewrite from reused bundle, and invalid existing bundle recovery                                                                                                                          |
| 24   | Add the semantic evaluation harness and reviewed-gold-set support approved in D12                | Test/evaluation fixtures and report script                                                                                                      | Records independent-versus-pipeline sampling provenance and metric denominators; reports candidate recall, accepted-edge precision, relation choice, direction, abstention/`needs_review`, unresolved handling, minimum support, and pass/fail against settled per-curriculum/aggregate thresholds |
| 25   | Run a targeted curriculum matrix before all six full reruns                                      | Generated test outputs only                                                                                                                     | Madhi: scope-only level; Nigeria: simple tree; Pratham: DAG/multi-grain; Ghana math: unresolved; Rwanda: noisy LC evidence; Ghana English: recurrence                                                                                                                                              |
| 26   | Tune only curriculum config/instructions and generic thresholds justified by the evaluation      | Six configs and, only when universally valid, generic code                                                                                      | Every change is traced to a failed fixture/evaluation case; no curriculum name appears in Python                                                                                                                                                                                                   |
| 27   | Run all six complete pipelines from source PDFs                                                  | Six complete result directories                                                                                                                 | All three bundles validate; zero release-blocking failures; outputs and counts reconcile; semantic gate passes                                                                                                                                                                                     |
| 28   | Update user-facing pipeline and artifact documentation                                           | New `docs/pipeline/learning-progressions.md`; update architecture, pipeline index, output artifacts, adding-curriculum, running/debugging docs  | Documentation examples match real generated artifacts and explicitly record every **LIMIT**                                                                                                                                                                                                        |
| 29   | Final release review                                                                             | Brief, configs, code, docs, six outputs                                                                                                         | Confirm all **SETTLED** decisions are implemented, every accepted **LIMIT** is visible, and no unresolved **DECIDE** marker remains                                                                                                                                                                |

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
4. **Ghana math** — verifies unresolved-root fallback policy and code anomalies.
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

The release review must also verify D12 sampling provenance, metric denominators, minimum support, per-curriculum/aggregate thresholds, and the `needs_review` release policy; graph validity alone is not release approval.
