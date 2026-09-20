# Engineering Brief: Learning Progressions KG Construction

**Status update — 2026-09-19:** Automatic recovery for new evaluator stores is approved under Section 3.3.11. Governance precedes a separate coding phase; old evaluator progress need not be preserved. Historical/current production input readers remain unchanged.

**Status update — 2026-09-18, historical/current compatibility extension approved for implementation:** The user reverses the current-only evaluator support policy and requests the governance-only proposal in Sections 1.6, D12.1.5 and 3.3.10. The packet supports three projection families independently crossed with historical prefixes or current journals, with strict raw/material/interpretation binding. The user explicitly approved this synchronized packet on 2026-09-18, stating: "ok i approve of these changes then." This governance-only approval record precedes a distinct coding phase in the same task. Earlier delivery/removal approvals do not authorize it; fixed review base remains `28d4f1218c71237bc7fda1027a8ac55866bd345f`. Historical records below retain their original approval scope. No code/tests/results/evaluation-evidence changes, live calls or Git mutations are authorized in this phase.

**Historical status — 2026-09-18, historical compatibility removal approved for implementation (reversal separately approved in Section 3.3.10):** The user explicitly approved Sections 1.6, D12.1.5 and 3.3.9, authorizing this governance-only approval record followed by a distinct coding phase in the same task. No further specification approval is pending. The current-only contract supersedes earlier historical-reader guarantees while preserving prior evidence. The user reports independent reapproval of the delivery-amendment K=21/F=25 chain and existing evaluator compatibility at `2ad4414260984b50ebe954a459e8cc2e75010d3a`; that verdict does not approve this removal or final Step 28 completion. Independent testing and reviewer reassessment of this change remain required at fixed review base `28d4f1218c71237bc7fda1027a8ac55866bd345f`. No results, prior evidence, active runs, live calls or Git mutations are authorized.

**Status update — 2026-09-17, export amendment approved for implementation:** The user selected Learning Commons-shaped delivery for the two AS+LC+LP JSONL files and requested this governance-only amendment before a separately approved coding phase in the same task. Section 1.6 records the complete selected contract; Section 3.3.8 records K=21/F=25 approval impact and the fixed review base `28d4f1218c71237bc7fda1027a8ac55866bd345f`. The prior internal format was explicitly required, so this is a contract change rather than an implementation defect. The user explicitly approved this amended contract on 2026-09-17, stating "i approve." A separate coding phase may now proceed, followed by independent testing and reviewer reapproval. Historical status and approval records below remain historical and do not override this gate.

**Status update — 2026-09-16:** The user approved restoring the original evaluator model default and removing the added `config.py` guard, with governance followed by a separate coding phase in this chat. D12-J and Section 3.3.6 govern. This changes only current Step 28 configuration policy; material hashes, independent testing/review and live-execution gates remain.

**Status update — 2026-09-15:** The user approved removal of Step 28's producing-commit prerequisite under D12.1.3 and Section 3.3.5. Actual artifact/configuration identities and the fixed evaluator review base remain required; D12.1.4 removes evaluator candidate-SHA recording and Git runtime gates. The user also approved Step 28 automatic recursive discovery and requested the governance update in this chat. D12.1.2 and Section 3.3.4 govern the single-command CLI, one required starting-directory argument, manifest-frozen resume and output beneath `results/lp_evals/`. All existing input-integrity, fixed-review-base, independent testing/reviewer and live-execution gates remain. The latest request authorizes sequential governance amendment and coding-only pylint remediation under Section 3.3.5.

**2026-09-13 status:** The user explicitly approved the synchronized 2026-09-13 prospective Step 26 checkpoint-format removal for implementation after clarification of continued historical-artifact use; approval is recorded in Section 3.3.3. D13-C3 and D13-C3-Z are settled: new production requires complete journal-bearing evidence and rejects prefix-only, previously upgraded legacy and compatibility-counter-bearing formats, including zero-valued counters, before calls or artifact changes. No implementation-governing decision or payload remains open for this amendment. A separate coding task may implement the removal, followed by independent testing, a user-created candidate and reviewer approval. The earlier concurrency approval remains recorded in Section 3.3.2. Steps 0–25 and Step 25 approval at `28d4f1218c71237bc7fda1027a8ac55866bd345f` retain their obligations; observed HEAD has no inferred reviewer approval. Step 27 remains gated by Step 26 reviewer approval. Independent early Step 28 coding/offline testing and its fixed base remain intact under D12.1.1. Completed historical graphs remain usable downstream and may be validated through compatible historical code/schema; the new production pipeline cannot resume/reuse their unsupported checkpoints, even for completed-run reuse. No regeneration follows merely from concurrency or this format change. Live evaluation/final completion still require Step 27 approval, all six reviewed snapshots, exact candidate/input binding, reports/dispositions and separate live authorization. This governance-only task grants no code/test/config/result changes, live calls, active-run intervention or Git mutations.

**Historical 2026-09-11 approval impact (original numbering):** The supplied former reviewer-approved frontier is Step 25 at `540ea950378ce54b54da1c7a93491b525609b574`; Steps 22–25 were revalidated there. On 2026-09-11 the observed root was `/Users/tzz/Projects/private/idi/KGForEdGlobal`, branch `tz6/lp-kg-build-step-25`, HEAD exactly that SHA, working tree clean, and no later commits. These observations are not a new reviewer approval. Adoption on 2026-09-11 reopens **K=24 through F=25**, retaining that SHA as the cross-step review base: Step 24's former repository-wide prohibition on required semantic samples/metrics in tests, scripts, and documentation is narrowed to the production pipeline, while required downstream evaluation is introduced. That is an approval-affecting change to the earlier release-policy conformance scope, not a purely prospective renumbering. Steps 24–25 need independent testing reassessment and reviewer reapproval before Step 26. Earlier production obligations, including Step 18's structural-only validation report and Steps 22–23 orchestration/reuse, remain unchanged; no evaluation field or prerequisite is added to those artifacts. If reassessment finds an earlier affected contract, stop and explicitly reopen that earliest owner rather than silently extending this range.

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

No additional model environment variable is required for production LP. The model registry already accepts the `"learning_progressions"` model-settings type. Production LP adds its own agents, prompts, retry behavior, and usage buckets while using the same configured KG model as AS and LC. The separate Step 28 evaluator uses `LLM_LP_EVAL_JUDGE_MODEL` under D12-J; that variable must not become a requirement for production startup or AS/LC/LP execution.

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

**SETTLED — AS+LC+LP delivery contract; implementation approval recorded under Section 3.3.8.**

The existing `as_nodes.jsonl` / `as_relationships.jsonl` and `as_lc_nodes.jsonl` / `as_lc_relationships.jsonl` pairs retain their Learning Commons-shaped delivery wire format. The AS+LC pair preserves AS wire records and appends LearningComponent nodes and `supports` relationships. The AS+LC+LP delivery pair extends that same contract:

1. `as_lc_lp_nodes.jsonl` preserves every validated `as_lc_nodes.jsonl` delivery record exactly, including its wire properties, identifiers and order. LP adds no nodes. Its node set must also reconcile exactly with the combined bundle.
2. `as_lc_lp_relationships.jsonl` preserves every validated `as_lc_relationships.jsonl` delivery record exactly and in order, then appends all `buildsTowards` records followed by all `relatesTo` records, each LP group sorted deterministically by relationship identifier. The four-type union is complete, with no duplicate or omitted relationships.
3. Use the existing Learning Commons wire models, serializers and explicit aliases. Node rows use the established `type`, `identifier`, `labels`, `properties` shape; relationship rows use the established relationship wrapper, including `source_identifier`, `source_labels`, `target_identifier` and `target_labels`. Properties retain established aliases such as `relationshipType` and established value encodings/optional-field handling. Do not apply blanket recursive camelization, add `entity_type` to delivery nodes, or copy arbitrary internal metadata into delivery properties.
4. `as_lc_lp_kg_bundle.json`, standalone LP relationship JSONL, provenance, requests, checkpoints, reports and all other internal artifacts keep their current formats and complete internal metadata. The wire projection exposes the existing serializer's supported fields; omitted internal metadata remains available in those authoritative internal artifacts. D11 attribution/ownership and provenance remain complete internally; projected fields must equal their authoritative values through the wire mapping.
5. Preserve relationship identifiers and node identifiers without reminting. Resolve internal SFI CASE UUID endpoint keys through the existing serializer to the corresponding wire node identifiers and labels. Preserve `buildsTowards` direction and D5's canonical `relatesTo` source/target choice; do not reorder endpoints according to a different wire identifier. Preserve uniqueness, collisions/count checks, strict schema and raw-wire validation, logical bundle parity, deterministic serialization, exclusive ownership, material hashes and read-back checks. Missing, stale or inconsistent upstream delivery records fail validation rather than being silently accepted or repaired.

**SETTLED policy direction — Historical and current evaluator inputs; implementation approval recorded under Section 3.3.10.**

The evaluator will accept validated snapshots through the explicit format-specific readers in D12.1.5. Projection format and checkpoint/captured-configuration format are independent:

| Projection family                                                                  | Complete historical prefixes; captured capacity absent   | Complete current journals; exact recorded capacity |
|------------------------------------------------------------------------------------|----------------------------------------------------------|----------------------------------------------------|
| Original historical flat snake_case                                                | Supported after strict validation                        | Supported after strict validation                  |
| Converted historical flat camelCase, preserving flat structure and nested metadata | Supported after strict validation                        | Supported after strict validation                  |
| Current Learning Commons wire                                                      | Supported after strict validation                        | Supported after strict validation                  |

This six-cell matrix is evaluator-only. It does not change the current production delivery contract above or grant production checkpoint resume/reuse compatibility. Do not classify an entire run from camelCase keys, journals, chronology or folder names. Malformed, ambiguous, partial, inconsistent and unlisted formats fail; current readers retain all current strictness. D12.1.5 defines the finite conversion map, exact bundle reconciliation, historical evidence requirements, capacity absence versus recorded value, raw/material hash preservation and interpretation binding across discovery, preparation, freezing, loading, source revalidation, resume, caches and reports. Both projection files must consistently implement one supported projection family; mixing rows or families within the pair is unsupported.

New runtime configuration still resolves omitted capacity to 4; evaluation never applies that default to captured historical evidence. No retrofit, migration, result rewrite, regeneration or live execution is authorized. Existing AS/AS+LC and internal bundle/audit contracts, optional producing SHA and Git-independent evaluation remain intact. Public consumer documentation will describe actual supported behavior after implementation, without agent-process notes. The 2026-09-18 removal remains a historical approval in Section 3.3.9; this reversal has its own explicit implementation approval recorded in Section 3.3.10.

Step 26's internal pending-completion and usage journals under D13-C3 accompany deterministic successful JSONL prefixes. New production continues to require complete authenticated journal-bearing checkpoints; absent journals are not empty journals. A committed store may lack the transient transaction file. Historical completed prefixes may be read by the evaluator under D12.1.5 after approval, but cannot be resumed, recovered, overwritten or reused by current production. Preserve original evidence and all separate testing/reviewer gates.

### Separate evaluation artifacts (Step 28)

The evaluator automatically discovers and validates completed curriculum snapshots beneath a supplied starting directory under D12.1.2; N >= 1 is derived from the frozen selection, not a required CLI number; the six named project curricula are the required completion population, not a hard-coded runtime roster. Early development may select only the validated, frozen Madhi mathematics snapshot under D12.1.1. Every manifest and report identifies its actual selected population and whether it is development evidence; absent future curricula cannot be represented as evaluated or as empty successful strata.

The evaluator reads fixed, validated production snapshots and writes only to invocation-specific directories beneath repository-root `results/lp_evals/`, disjoint from all production input/output directories. It does not add fields to `kgs.lp`, `kg_run.json`, LP validation reports, bundles, projections, or production provenance. Their structural-only meaning remains intact even when a separate evaluation exists.

Required evaluation artifacts are `lp_eval_manifest.json`, `lp_eval_population.json`, `lp_eval_sample.jsonl`, `lp_eval_requests.jsonl`, `lp_eval_judgments.jsonl`, `lp_eval_failures.jsonl`, `lp_eval_usage.json`, `lp_eval_report.json`, and `lp_eval_report.md`. They retain the discovery inventory and skip/conflict reasons, resolved starting root, frozen selected paths and byte/material hashes, optional recorded producing SHA, effective production and evaluator configs, population membership/counts, seeds/selection procedures, conditions/order/replicates, full request identities, prompt/schema/model settings hashes, validated outputs, attempt/failure disposition, denominators, and report-generation inputs. Independent-cohort artifacts bind the common upstream evidence-constructor settings and material payload hashes frozen before the production-metadata join. Reports retain that join and the common-condition numerator/denominator membership; overlapping production/independent selections retain distinct required judgment identities. Exact serialized field names are owned by Step 28 coding under the settled policies.

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
8. production release behavior remains structural/process-only under D12/D13: `needs_review` remains visible, never publishes, and does not block production success; required downstream Step 28 evaluation execution/reporting is a separate build-order completion obligation, with no automatic semantic score threshold or human gold-set prerequisite;
9. every candidate/request pair is processed successfully under D13's zero-tolerance failure policy, with validated deterministic-prefix checkpoint and resume behavior;
10. all six fixed Step 27 input snapshots receive the required Step 28 evaluation and reports, independently validated and reviewed; Step 29 documentation and Step 30 review disclose evaluator failures, ambiguity, quality concerns, sampling denominators, and accepted D12/D14 limitations; and
11. after approval of its complete policy packet, new Step 26 bounded production concurrency satisfies D13.1 and receives independent testing and reviewer approval before renumbered Step 27 completion.

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

Different requests may execute concurrently under approved Step 26/D13.1; each checker still depends on its own validated draft. Scheduling does not change these logical dependencies.

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

The placeholder entry above is explanatory, not a literal schema field. The compiler must copy the complete `as_lc_bundle.entity_provenance` mapping without deletion or reshaping, then add non-colliding LP relationship-provenance entries. It must likewise preserve the complete upstream framework, SFI, LC, `hasChild`, `supports`, summary, and unresolved content before adding LP fields. This bundle remains in its internal schema with complete metadata; only the two delivery JSONL projections use the Section 1.6 wire mapping.

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

Step 26, not historical Steps 2–3, owns optional `kgs.lp.max_concurrent_requests` with default 4 under the amendment explicitly approved for implementation in Section 3.3.2. Omission uses this documented default; an explicit value must be a positive integer, with booleans, null, zero, negatives and nonintegers rejected. This is a deliberate default exception for the new operational field, not permission to silently default other required policy. D13.1 governs recording, material identity and compatibility. Existing batching/evidence limits remain unchanged.

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

The Section 3.3.8 export policy is fully selected and the user explicitly approved it for implementation on 2026-09-17; independent testing and reviewer gates remain. The Section 3.3.9 removal policy was explicitly approved for implementation on 2026-09-18; independent testing and reviewer reassessment remain required. Section 3.3.10 now records the requested reversal and complete proposed compatibility packet; its separate explicit implementation approval is recorded on 2026-09-18; earlier approvals did not authorize it. The following markers are load-bearing. D1–D11, existing D13, D14 and D12-S/J/R/B/A retain their settled semantics. The 2026-09-11 D12 approvals and 2026-09-12 D13-C1/C2/C3 concurrency approval remain historical specification approvals. The 2026-09-13 D13-C3 format-removal amendment and D13-C3-Z complete removal boundary are settled and explicitly approved for implementation in Section 3.3.3. Policy selection, user implementation approval and independent reviewer approval remain distinct. Ordinary role, build-order, reviewer and live-execution gates still apply.

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

| ID     | Decision                                                            | Settled option                                                                                                                                                                                               | Status      |
|--------|---------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| D1     | How LP progression grain and statement-type pairings are configured | B — closed-world relation-specific pair matrices                                                                                                                                                             | **SETTLED** |
| D2     | How local developmental order is represented                        | A — one explicit primary ordered dimension for v1                                                                                                                                                            | **SETTLED** |
| D3     | Candidate nomination technology                                     | A — one built-in explainable deterministic non-embedding candidate policy                                                                                                                                    | **SETTLED** |
| D4     | Candidate-pair orientation and adjudication shape                   | A — one canonical unordered pair and one unified relation/direction judgment                                                                                                                                 | **SETTLED** |
| D5     | How conceptually symmetric `relatesTo` is serialized                | A — one UUID-canonicalized relationship per unordered pair                                                                                                                                                   | **SETTLED** |
| D6     | Whether one pair may publish both relation types                    | A — mutually exclusive, with `buildsTowards` precedence                                                                                                                                                      | **SETTLED** |
| D7     | How recurring practice is mapped                                    | C — extension → `buildsTowards`; meaningful recurrence → `relatesTo`; generic repetition → `no_relation`                                                                                                     | **SETTLED** |
| D8     | `buildsTowards` cycle policy                                        | A — the complete published graph must be acyclic with deterministic diagnostics                                                                                                                              | **SETTLED** |
| D9     | Transitive edge policy                                              | A — publish every directly accepted edge; perform neither transitive closure nor reduction                                                                                                                   | **SETTLED** |
| D10    | How unresolved AS ancestry affects LP                               | Required two-state profile policy; all six initial profiles include every otherwise-eligible unresolved SFI warned                                                                                           | **SETTLED** |
| D11    | Attribution/ownership metadata for inferred LP edges                | B — exact LP metadata, source-license inheritance, attribution template, and provenance                                                                                                                      | **SETTLED** |
| D12    | Semantic evaluation and release policy                              | Required downstream LLM-judge execution/reporting; configurable S1/R1 defaults; dedicated evaluator model; no budget ceilings or automatic semantic threshold; A1 concern disposition                        | **SETTLED** |
| D13    | Failed-request tolerance and release gate                           | A — any failed pair halts LP, with deterministic-prefix checkpoint and resume                                                                                                                                | **SETTLED** |
| D13-C1 | Production capacity setting, scope and default                      | Optional kgs.lp.max_concurrent_requests; default 4 admitted requests per run; positive integer; full effective-config identity                                                                               | **SETTLED** |
| D13-C2 | In-flight shutdown after exhausted retries                          | Finish active API calls only; no new producer/checker/retry calls after exhausted failure is observed                                                                                                        | **SETTLED** |
| D13-C3 | Durable completion and supported checkpoint evidence                | Sole-writer journal; contiguous prefixes; strict material identity; approved prospective prefix-only/upgrade removal and rejection of compatibility-counter-bearing formats, including zeros, under D13-C3-Z | **SETTLED** |
| D14    | Manual semantic edge overrides in v1                                | A — no forced semantic include/exclude/relation/direction overrides                                                                                                                                          | **SETTLED** |

Settled policy text is implementation-governing after its required explicit implementation approval. Section 3.3.2 records the 2026-09-12 concurrency approval; Section 3.3.3 records the later prospective checkpoint-format removal and its separate implementation approval. Rejected alternative analysis is historical only and cannot govern implementation unless a later user-approved governance change reopens that decision.

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

Deterministic finalization, not the producer or checker, attaches these values. Validation requires exact author/provider strings, exact source-license equality, exact template expansion and complete internal provenance. Standalone LP relationships and the combined bundle retain complete internal metadata; every supported delivery field must match its authoritative value through the Section 1.6 wire mapping. Projection does not remove or alter internal provenance or change D11 attribution policy.

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

Production completion remains governed by D1–D11 and D13: structural/process validation, producer/checker reconciliation, and zero unresolved processing failures. `needs_review` stays visible, nonpublishing, and nonblocking. The production pipeline does not read evaluation outputs or wait for a semantic score. Separately, the numbered project cannot complete Step 28 or advance through Steps 29–30 without the required evaluation execution, accountable reports, and independent review. This is an evaluation-process requirement, not a claim that scores prove pedagogical correctness.

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

The single entry-point command orchestrates recursive discovery/preflight, preparation, judging/resume and reporting under D12.1.2, with only the starting results directory required from the CLI. The five modules own typed contracts; population/strata/sampling; blind/critique/control prompt rendering; validated bounded calls and caching; and deterministic scoring/reporting, respectively. Existing reusable utilities may be called without changing production behavior. Step 28 adds the evaluator-only `LLM_LP_EVAL_JUDGE_MODEL` setting and `lp_eval_judge` model binding; production continues to use `LLM_KG_MODEL`. No `kgs.lp` selector, candidate-policy modification, or production prompt/config tuning is part of the D12 evaluator amendment. The separate new Step 26 operational setting is governed only by D13.1.

A separate testing-role task independently derives the oracle, authors deterministic tests and synthetic fixtures, red-teams the harness, and executes the approved evaluation only after deterministic validation succeeds and the user separately authorizes live calls. For this step, execution evidence binds to actual input and evaluation-material content hashes under D12.1.4; no evaluator candidate-SHA recording is required. The final reviewer gate still follows complete execution/reporting. Coding cannot author those tests; testing cannot repair executable harness source, prompts, or evaluator configuration. The read-only reviewer evaluates both work products and execution evidence. Harness authors may run existing tests but may not supply the independent testing verdict.

### D12.1.1 SETTLED — Early development and N-curriculum operation

The user selected this extension after Step 24–25 reapproval and explicitly approved the synchronized updated brief for implementation on 2026-09-11, as recorded in Section 3.3.

- **Development start:** coding may implement the complete generalized evaluator using existing Madhi mathematics LP KG artifacts before new Step 26 approval or all Step 27 pipelines finish. A separate testing task may then author and run independent deterministic tests while Step 27 continues. These offline activities do not require new Step 26 implementation/testing/reviewer approval or Step 27 completion approval. They may continue while new Step 26 implementation/testing/review is pending if independent of unreviewed production implementation.
- **Development input integrity:** before consuming the Madhi artifacts, require the format-specific contract in D12.1.5 after Section 3.3.10 approval and identify and validate a complete frozen snapshot with source identity, effective configuration, AS+LC and LP bundles/projections, original bounded requests, producer/checker/final judgments, provenance, validation reports and material hashes. Preserve the original evidence required by D12.3. An existing final graph alone is insufficient. Record actual paths, hashes and validation status in a development manifest; this decision does not certify any directory as complete or reviewer-approved. Never consume a growing run or invent missing material.
- **Generalized runtime:** accept any positive number N of compatible curriculum snapshots discovered and frozen under D12.1.2, including one, several or more than six; no numeric N or manual snapshot enumeration is required. Derive identity, paths, local labels/order, policy permissions, evidence limits, populations and reporting groups from supplied snapshots/configuration. Generic evaluator code, prompts, schemas, sampling and scoring must not hard-code Madhi, another curriculum, an enumerated roster, a fixed count of six, curriculum-specific statement types, grade values, hierarchy shapes or evidence semantics. Keep populations framework-contained; no cross-framework pairs. Adding a compatible future snapshot requires input/configuration, not a code change.
- **Incremental availability:** discovery records unfinished/active runs as unselected under D12.1.2; their unavailable outputs do not block development or preparation for the selected set. A selected missing, incomplete, stale or invalid snapshot fails validation; do not silently drop it, fabricate examples or label it an exhausted stratum. Later curricula enter through new invocation manifests and schedules generated from discovery; resume never expands an existing frozen selection. Preserve earlier evidence and reuse only fully validated, materially identical requests under D12.4. Never relabel a one-curriculum report as complete six-curriculum evidence.
- **Independent offline validation:** retain all D12 components and both evidence-condition corrections from the start. Independently authored synthetic fixtures must exercise one, multiple and more-than-six selected curricula, unfamiliar labels, tree/DAG/unresolved context, and selection/identity/report isolation. Missing real curricula are development deferrals, not waived final coverage.
- **Execution and completion:** early development does not authorize live evaluation. Live evaluation retains Step 27 reviewer approval, independent deterministic harness validation and explicit fixed-input execution authorization as prerequisites. Step 28 remains incomplete until all six project snapshots are reviewer-approved under Step 27 and receive all required evaluation components/reports, with no unresolved execution failure, complete concern dispositions and independent final reviewer approval. Step 29 cannot begin from a Madhi-only development result.
- **Review base and concurrent work:** early Step 28 uses revalidated Step 25 SHA `28d4f1218c71237bc7fda1027a8ac55866bd345f` as its fixed review base through coding, testing, remediation and final review. Account for all later changes, including this approved governance extension, in the candidate range. New Step 26 and later Step 27 approvals and any recorded producer snapshot SHAs are additional input evidence, not an implicit replacement of the evaluator review base. Do not alter, stop or restart active production runs, rewrite their evidence or silently change the code/configuration they consume; any required checkout isolation remains subject to the user's Git authorization.

After Section 3.3.10 approval, D12.1.5 restores strict historical/current evaluation under its independent format dimensions. A complete historical Madhi snapshot may qualify only through actual validation of its historical contract; observations alone do not certify it. Early offline development retains the same review base and execution gates. Independent testing uses isolated format-specific synthetic fixtures; this grants no live regeneration or Step 27/28 completion. Producing SHA remains optional under D12.1.3 and historical records remain history.

### D12.1.2 SETTLED — Single-command recursive discovery and frozen resume

The user approved automatic discovery on 2026-09-15 and requested this governance update in the existing chat; Section 3.3.4 records the scope. This replaces the earlier requirement to manually enumerate snapshot paths. It does not weaken snapshot validation or authorize live execution.

- **CLI:** provide one command with one required argument: the starting results directory. Run discovery/preflight, preparation, judging/resume and reporting sequentially; preflight or preparation failure prevents judge dispatch. Do not require a numeric N, a curriculum roster, a user-authored input manifest, an output-directory argument, separate phase subcommands or an offline-mode flag. Preserve all optional S1/R1 CLI overrides and their defaults. Expose callable preparation, validation, reporting and injectable judge boundaries for offline coding checks and independent tests; a successful preflight is not live-call authorization.
- **Recursive discovery:** traverse the supplied root in deterministic path order to find directories named exactly `kgs`, including the root itself if it is a `kgs` directory. Parent depth, curriculum names and hash-directory names have no semantic significance: `<root>/<hash>/kgs`, `<root>/<curriculum>/<hash>/kgs` and deeper layouts use the same procedure. Treat each `kgs` directory as a run boundary rather than continuing to discover runs inside its artifacts. Resolve the starting root and candidate paths, prevent traversal cycles and duplicate aliases, and do not follow descendant directory symlinks outside that root. Exclude the evaluator output subtree from discovery. Never infer curriculum identity from a folder label.
- **Completion and selection:** inventory discovered runs and their completion evidence. Runs positively identified as unfinished, failed or active are unselected and recorded with reasons, without changing or reading their growing judgment populations as evaluation inputs. Do not treat discovery failure, unreadable/contradictory completion evidence, or a completed-looking run with missing/invalid material as an ordinary incomplete-run skip. Automatically select every completed candidate; validate its full D12.1.1/D12.4 source/config/code/artifact bindings before preparation. A success marker or final graph alone is insufficient. Invalid selected snapshots fail the invocation before judge calls; no silent dropping. Zero valid completed candidates is an explicit no-input failure, not completed evaluation.
- **Multiple snapshots and identities:** derive framework/doc_key identity from validated artifacts. Collapse only aliases of the same physical run directory and retain their discovery provenance. Distinct directories claiming the same framework/doc_key require explicit conflict reporting; do not silently choose a latest run, count copies as separate curricula or merge their evidence. N is the resulting number of distinct validated selected curriculum snapshots; support one, several and more than six without roster branches.
- **Freeze before calls:** write the discovery inventory and exact selected paths, source identities, captured effective configurations, optional recorded producing-code provenance, byte/material hashes and evaluation settings into the evaluation manifest. Validate stable material before and after freezing and before use; copying or content-addressed references must detect changes. The complete sample/evidence/request schedule is frozen before the first judge call. D12.1.3 removes the producing-SHA prerequisite; discovery still cannot upgrade historical receipts, fill missing concurrency values, regenerate results or consume active runs.
- **Output and resume:** write invocation-specific artifacts beneath repository-root `results/lp_evals/`, separate from production directories. Reject resolved-path aliases that could overwrite inputs. The command creates the manifest; users need not author it. Select the newest matching incomplete invocation by immutable recorded UTC creation time and then schedule identifier, under Section 3.3.11. Matching uses canonical root and effective evaluation/judge settings; validate candidates and fail specifically on unsafe state rather than silently starting fresh. If every match is complete, return the newest validated complete invocation without calls. Optional explicit controls remain available. Resume revalidates and uses the manifest's frozen selection and schedule, never adds newly completed discoveries, and fails if selected inputs are now missing, changed or invalid. New inputs belong to a distinct invocation; preserve earlier evidence and reuse only materially identical validated successes. Do not silently repeat completed live evaluation.
- **Unchanged gates:** both evidence-condition corrections, all D12-S/J/R/B/A settings, actual material-content binding, format-specific validation under D12.1.5 after Section 3.3.10 approval, independent testing/review, separate live authorization and the six reviewed project snapshots required for final completion remain mandatory. Discovery is operational selection, not reviewer approval or certification of pedagogical correctness.

### D12.1.3 SETTLED — Production commit provenance is optional for evaluation

On 2026-09-15 the user explicitly removed the requirement to identify the Git commit that produced a curriculum run, stating: "ok we can strike this requirement then. it doesn't make sense anymore to keep it around." Section 3.3.5 records this amendment.

Step 28 binds production inputs through actual source/document identity, captured effective configuration, original bounded requests and policy, judgments, provenance, validation reports, bundles/projections and their actual byte/material hashes. Identifying an exact producing Git SHA is not required for discovery, snapshot validation/freezing, offline development/testing, live evaluation or final Step 28 completion. Apply this uniformly to all curricula satisfying D12.1.5's supported format-specific contract; do not add a replacement mandatory producing-code fingerprint or a per-snapshot exception/approval gate.

Preserve a producing SHA when genuinely recorded; otherwise omit it or record it as unknown optional provenance. Its absence alone is not an input/execution failure, quality concern, required disposition or reason to reject a snapshot or regenerate results. Do not infer it from current HEAD, the review base, timestamps or compatible historical reconstruction. Missing or inconsistent required artifact/config/request evidence still fails validation. D12.1.5 governs format eligibility independently of producing provenance: unsupported or inconsistent evidence is rejected without receipt migration, default injection or production reuse. Supported historical evidence still requires full historical-contract validation after Section 3.3.10 approval.

The fixed Step 28 review-base SHA remains required for source review. D12.1.4 removes evaluator candidate-SHA/tree recording and runtime Git gates. Preserve actual evaluator prompt/schema/model/settings and material cache identities, independent deterministic testing, Step 27 reviewer approval, separate live authorization and all six reviewed project snapshots for final completion. This removes only the evaluator's producing-commit prerequisite; it does not alter production execution/reviewer evidence obligations or certify any pipeline run.

### D12.1.4 SETTLED — Git-independent evaluator and Typer entry point

On 2026-09-16 the user explicitly stated that Git state is not needed as an execution/resume gate and evaluator candidate-SHA recording is not needed in review/execution evidence, and requested the project's existing Typer CLI approach. This is approval to remove those requirements and implement the CLI conversion after recording this amendment, in sequential governance and coding phases in the same task.

Step 28 preparation, execution, resume, reporting and evaluation review must not require a Git checkout, HEAD, working-tree status, evaluator candidate SHA/tree or an execution-identity receipt containing them. Do not query Git in the evaluator or move candidate-SHA recording into an external mandatory evidence process. Unrelated commits or working-tree changes are not evaluation failures. This supersedes earlier exact-candidate-SHA/tree binding requirements for Step 28 throughout the brief and role instructions, including generic evidence rules and historical amendment wording. Prior approval records remain historical; production and other steps' evidence obligations are unchanged.

Keep the fixed source-review base `28d4f1218c71237bc7fda1027a8ac55866bd345f`, independent deterministic testing/review, Step 27 reviewer approval, separate live authorization, all required reports/dispositions and all six reviewed snapshots for final completion. Configuration, input, prompt, response-schema, model, request, schedule, cache and report hashes remain the material identity and resume checks. Python implementation hashes are neither recorded nor a resume gate; existing source-fingerprint fields are inert historical identity metadata under Section 3.3.11. Development evidence remains distinct from live evaluation; no prior evidence may be relabeled.

Implement the one-command CLI using the existing Typer dependency and entry-module conventions. The starting results directory remains the only required argument; all optional S1/R1 and explicit resume/new-invocation controls remain. Resolve the existing pydantic-settings configuration from the project environment managed by direnv; add no evaluator-specific dotenv loading. No live execution, production changes, test authorship or Git mutations are authorized by this amendment.

This affects the current unapproved Step 28 only; it does not reopen earlier approved steps or certify evaluator completion.

### D12.1.5 SETTLED policy direction — Historical and current snapshot readers; implementation approval recorded

The user explicitly reverses the 2026-09-18 current-only support policy and requests governance first. The user explicitly approved the following complete packet on 2026-09-18 under Section 3.3.10. Section 3.3.9 preserves the former approval as history. This extension belongs to evaluator input interpretation, not production regeneration or checkpoint reuse. No automatic certification of an observed directory follows.

**Independent format dimensions.** Support the six combinations in Section 1.6 through three projection readers and two checkpoint/configuration readers. Select a reader by its complete structural contract, permitted fields, authenticated receipt coverage and material consistency. Store both discriminators independently. Do not infer current format from camelCase, journal presence, a path, a date or a producing SHA. Each pair of node/relationship projection files must use one family throughout. Reject mixed-family rows/files, duplicate JSON object keys, alias collisions (including both spellings even with equal values), unknown fields, non-finite numbers, incomplete records and multiple plausible interpretations. An empty relationship population is classified using the nonempty node family and validated zero counts; it is not permission to guess a checkpoint format. A failed current-format check must never fall back to a weaker historical reader.

**Projection readers and authoritative reconciliation.**

- **Original flat snake_case:** validate the historical internal projection contract. Nodes are complete internal framework/SFI/LC records plus `entity_type`; relationships are complete internal relationship records with `relationship_type`, entity/key/value endpoints and nested metadata. The expected sequence is framework, SFIs sorted by CASE UUID, LCs sorted by identifier; relationships are identifier-sorted within `hasChild`, `supports`, `buildsTowards`, then `relatesTo`. Compare the entire records, including explicit nulls, arrays and nested metadata, against the validated authoritative combined bundle under that historical schema.
- **Converted flat camelCase:** the structure and data remain the historical flat projection. Derive the same expected historical records from the independently validated bundle, then apply only the closed mapping below to top-level field names. Additionally map the endpoint-key enum value `case_identifier_uuid` to `caseIdentifierUUID` in `sourceEntityKey` and `targetEntityKey`; `identifier` stays `identifier`. Endpoint UUID values, relationship identifiers and every other value remain unchanged. Do not recursively camelize metadata, change enum/string contents elsewhere, resolve endpoints into wire wrappers, omit internal fields, coerce types, inject defaults or remint identifiers. Compare the complete converted expected sequence against the parsed input using JSON-type-sensitive equality at every depth. This is an explicit interpretation of a user conversion, not a repair or proof of original production serialization.
- **Current wire:** require the existing strict Section 1.6 Learning Commons node/relationship wrapper, explicit serializers/aliases, value encodings, supported properties and optional-field semantics. Preserve exact validated AS+LC delivery records and order, followed by identifier-sorted LP wire groups. Resolve internal endpoint keys through the existing wire mapping and retain canonical internal direction. Require raw-wire schema checks and complete bundle parity; a flat record containing camelCase fields is not wire format. Preserve current raw-versus-typed checks so model parsing cannot discard malformed fields.

The converted reader's complete renamed-field map is:

| Internal field              | Converted flat field      |
|-----------------------------|---------------------------|
| `academic_subject`          | `academicSubject`         |
| `adoption_status`           | `adoptionStatus`          |
| `alternate_statement_code`  | `alternateStatementCode`  |
| `attribution_statement`     | `attributionStatement`    |
| `case_identifier_uri`       | `caseIdentifierURI`       |
| `case_identifier_uuid`      | `caseIdentifierUUID`      |
| `date_created`              | `dateCreated`             |
| `date_modified`             | `dateModified`            |
| `entity_type`               | `entityType`              |
| `grade_level`               | `gradeLevel`              |
| `in_language`               | `inLanguage`              |
| `is_current`                | `isCurrent`               |
| `normalized_statement_type` | `normalizedStatementType` |
| `relationship_type`         | `relationshipType`        |
| `source_entity`             | `sourceEntity`            |
| `source_entity_key`         | `sourceEntityKey`         |
| `source_entity_value`       | `sourceEntityValue`       |
| `statement_code`            | `statementCode`           |
| `statement_type`            | `statementType`           |
| `target_entity`             | `targetEntity`            |
| `target_entity_key`         | `targetEntityKey`         |
| `target_entity_value`       | `targetEntityValue`       |

Other fields permitted by the historical record schema (`author`, `description`, `identifier`, `jurisdiction`, `license`, `metadata`, `name`, `notes`, `provider`) retain their names and values where applicable to that entity. This list is not permission to insert fields absent from its schema. Unknown aliases, recursive metadata conversion, a snake_case endpoint-key enum in the converted family, or a second spelling fail. Adding another conversion scheme requires a separately reviewed contract.

For all projection readers, independently validate the bundle and its complete preserved AS+LC content, standalone relationships, final claims, provenance, reports, counts, deterministic IDs, endpoints, direction, policy, collision absence and request/judgment bindings before deriving expected rows. Compare complete records, not selected fields or counts alone. Preserve JSON boolean/number, null/absence, array order and nested-value distinctions. Parsing must reject duplicate keys before any dictionary can erase them.

**Checkpoint readers.**

- **Historical prefixes:** require the exact historical receipt schema and execution-material schema without `max_concurrent_requests`. Captured `kg_run.json` LP configuration must also lack that field. Receipt byte-hash coverage is exactly draft responses, validation verdicts, reconciled responses and failures. Pending-completion and usage journal files must be absent, with absence recorded and rechecked; unexpected files cannot be ignored as unauthenticated extras. The transient transaction must be absent. Require `completed`, positive integer run number, no unresolved failed pair IDs, complete deterministic contiguous draft/verdict/final coverage of every original request, matching receipt stage counts, exact request/stage/content hashes, correct draft/verdict dependencies, exact pair/outcome coverage, permitted corrections, and valid failure/recovery dispositions under the historical contract. Validate execution hash, request manifest, actual file hashes, captured producer/checker instructions, prompt/schema/model settings and retry limits. No journal/attempt evidence that the historical format never recorded is fabricated or demanded as proof of concurrent execution. Historical usage remains the actual recorded usage, with unavailable attempt detail or capacity explicitly unrecorded, never zero or an inferred value.
- **Current journals:** retain complete D13-C3 validation, including exact six-file receipt coverage, authenticated pending-completion and usage journals even when empty, full attempt/failure/accounting coverage, dependencies, retry limits and completed-state consistency. Require exact positive integer capacity in captured effective LP configuration, execution material and usage evidence; reject bool, null, strings and mismatches. Require no pending work or unfinished transaction for a completed snapshot. Reject compatibility-counter-bearing or previously upgraded schemas, including zero-valued `legacy_stage_counts`/`legacy_failure_count`, and any missing or partial journal; never downgrade these to historical prefixes. Production current-format transaction recovery remains separately governed and cannot be invoked by evaluator inspection.

Prefix receipts with recorded capacity, journal receipts without exact capacity, partial journal coverage, unauthenticated journals and unsupported upgraded/counter-bearing schemas are unlisted combinations and fail. The six supported cells vary projection family independently of these two complete checkpoint/configuration contracts; they do not grant arbitrary checkpoint hybrids.

**Captured configuration and original request identity.** Use explicit strict captured-configuration schemas/serializers for the historical and current contracts. Preserve the exact recorded field set, values, JSON types and both existing configuration hash conventions. Historical capacity remains absent in the evaluation record and in all effective-configuration/request hash calculations; it is not defaulted to 1 or 4, null, or an invented execution setting. Current captured capacity remains mandatory. Do not hide other missing fields through today's defaults, broad `exclude_unset`, permissive extra-field stripping or trial-and-error schema fallback. Preserve the existing uniquely hash-proven recovery of the omitted overwrite flag: exactly one boolean candidate must match the recorded effective-config hash; neither or both is an error. This derives only that already-omitted flag and grants no general identity reconstruction exemption.

Recompute and verify the original deterministic candidate/request population using the selected captured contract, including its original configuration serialization, against original IDs, manifests, bounded payloads and bytes. An in-memory compatibility representation is permitted only when it independently reproduces and verifies the recorded identities; it must not copy a recorded digest/ID into a differently reconstructed request to force equality, alter stored requests, or replace original production evidence with newly reconstructed evidence. Shared current helpers must not silently inject capacity or current schema fields into historical hashes. Preserve exact-production versus common reconstructed-upstream evaluator evidence separation, including blind-before-critique and all grounding/citation repairs.

**Raw identity, conversion and interpretation binding.** Retain original file bytes and their actual SHA-256 identities, recorded content hashes and recorded absence of optional artifacts. Never replace a raw hash with the hash of normalized data. Verify each recorded byte hash against the named file bytes and each recorded material hash under its declared supported serialization contract. A converted projection cannot excuse a stale recorded byte hash: if a source receipt/report actually binds that projection's bytes, mismatch fails without resealing or exempting it. Where the producing contract did not record projection byte hashes, exact complete bundle-derived parity is the projection integrity check; freeze the present converted bytes and their hashes as converted inputs, without claiming they are the old snake_case byte identity. The inspected five bundles' artifact-byte-hash maps omit the two delivery projections; this observation never licenses ignoring a hash in another snapshot.

Keep any normalized semantic view separate from raw artifacts. New evaluator snapshot/manifests must record projection family, checkpoint family, captured-configuration contract and an explicit interpretation version, along with the validated captured-configuration contract. Bind that descriptor plus all raw and material input identities into snapshot, schedule/request/cache and report identities; equal normalized graphs from different bytes or interpretations cannot share an unqualified identity. The interpretation version describes evaluator reading only; it is not a D3 candidate-policy version, a replacement for material hashes, or a mandatory producing-code fingerprint.

**Uniform lifecycle and rejection boundary.** Apply the same reader contracts and cross-artifact checks to discovery validation, callable preparation, freeze, frozen-copy loading, source revalidation, schedule construction, cache lookup, resume and reporting. Preserve deterministic recursive discovery and all active/unfinished exclusions. A completed-looking invalid candidate fails the whole invocation; do not silently skip it or publish a partial success manifest. Validate every selected candidate and stable source state before publishing frozen inputs, manifests, schedules, cache changes or reports and before judge dispatch. Revalidate raw bytes, absence/presence, interpretation and source/lock identity around freezing and before subsequent use. Preserve existing nonblocking read-only production-lock observation and evaluator exclusive ownership; never create/repair/delete a production lock or invoke production resume for validation. Missing/stale source material or ownership loss fails closed.

Resume uses only the exact frozen selection, original byte copies/references and complete schedule. Revalidate all six supported combinations through frozen input and source paths with the same interpretation and material input identity; newly completed directories never enter an old invocation. Existing manifests lacking the required interpretation binding, containing unsupported/ambiguous descriptors, or bound to different source-artifact/config/request material fail without in-place migration, relabeling or reuse of unqualified cache entries. A new invocation after authorization may freeze newly validated inputs; this request does not modify existing evaluation evidence. A format change after freezing is a material input change even when semantic rows remain equal. Reports identify each snapshot's projection/checkpoint/configuration interpretation, actual input and interpretation hashes, and capacity as recorded or absent under the historical contract, without labeling historical evidence current or claiming concurrency. Preserve the existing scoring-only report regeneration exception solely for otherwise identical validated judgments; it cannot waive interpretation/input changes.

Every rejection preserves source and existing evaluation bytes and directory membership and makes zero external calls; return path-specific incompatibility externally. No automatic migration, journal synthesis, capacity retrofit, receipt/hash resealing, regeneration, graph patching or changes to protected directories are authorized.

**Independent validation and unchanged gates.** After coding, independent testing owns reduced synthetic fixtures for every supported matrix cell, strict negatives and lifecycle paths under Section 5.5. Reviewer reassessment covers the evaluator and affected K=21–F=25 export/reuse/orchestration contracts at fixed base `28d4f1218c71237bc7fda1027a8ac55866bd345f`. Preserve current production checkpoint gates and exports, optional producing-SHA provenance, Git-independent evaluator execution, all D12-S/J/R/B/A obligations, deterministic scheduling, locks and active runs. Neither this proposal nor its later specification approval grants live calls, Step 27 approval, final Step 28 completion or later-step advancement.

### D12.2 Three complementary components

1. **Production-pair assessment.** Sample published `buildsTowards`, published `relatesTo`, final `no_relation`, and final `needs_review` pairs, retaining checker accept/correct and producer-to-final changes as audit strata. First make a blind independent classification in a fresh context: permitted `buildsTowards` direction, `relatesTo`, `no_relation`, or evaluator `ambiguous`, with specific evidence references and explanation. Use the blind-classification evidence view defined in D12.3: hide production decision, rationale, confidence, correction, nomination recommendations/rankings, publication status, and sampling/control labels, while retaining permitted factual evidence and policy permissions. Freeze this response before a separate call critiques the operative production rationale using the original-production critique view, not the redacted blind view. The critique cannot revise the blind judgment or be fed back to it, and the blind judge's answer is not supplied to the critic. Assess relationship support separately from rationale grounding; a plausible relationship may have an unsupported explanation and a well-grounded explanation may remain semantically ambiguous. For a corrected pair, critique the operative corrected rationale and retain the original draft for correction diagnostics; any extra critique calls must be explicit in the materialized evaluation schedule.
2. **Independent upstream-pair assessment.** Construct a reproducible population of distinct, same-framework pairs from the fixed eligible AS+LC population using D1/D2/D10 admissibility. Do not restrict selection to candidates, published edges, production negatives, or judge-positive discoveries. Freeze selection and the common bounded-upstream evidence payloads before joining candidate/adjudication/publication metadata. Every independently sampled pair, nominated or not, receives the same `reconstructed_bounded_upstream` base condition under D12.3; production evidence availability must not select its condition or alter its payload. Include both a uniform probability sample and an upstream-feature-stratified diagnostic sample so low-signal pairs have a sampling route. Use bounded sampling/indexing rather than an all-pairs LLM pass. Afterwards identify judge-positive pairs never nominated, nominated but finalized as `no_relation` or `needs_review`, or published with differing relation/direction. Preserve overlapping cohort membership; do not exclude nominated pairs merely to inflate misses. Policy-excluded pairs are outside this estimand, not negatives.
3. **Evaluator checks.** Include clearly constructed synthetic controls, endpoint/evidence presentation-order changes, repeated fresh judgments, limited deterministic baselines, and paired evidence-sensitivity comparisons. Controls target an explicit synthetic truth or planted rationale defect; they never turn an unasserted real pair into a known negative. Include synthetic developmental extension, substantive nondirectional coherence, unrelated concepts, insufficient/contradictory evidence, and invented rationale evidence. Keep control expectations hidden from the judge and separate from real-population summaries. Baselines are evaluation-only comparisons (constant `no_relation` and a bounded lexical ordering diagnostic), never graph publishers or semantic truth. A weak control or unstable judge result is a reported quality concern, not an execution failure unless the request/output contract itself failed.

Every curriculum selected for an invocation must appear in each applicable component; Step 28 completion evidence must cover all six project curricula. Sampling must include same/cross/missing-rank cases; repeated or highly overlapping text; absent, shared, nonshared, and broadly reused LC evidence; DAG paths and unresolved ancestry; checker corrections; and truncated/bounded production evidence. Counts must expose absent strata, exhausted strata, and unsupported comparisons. Zero available cases is not evidence of good performance and must not be filled with invented real examples.

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

Before the first evaluator call for an invocation, freeze and validate every selected curriculum's input manifest, eligibility/pair populations, sample plan, conditions, prompt/schema/config/model settings, and the complete bounded request schedule for that invocation. The runtime accepts N selected curricula rather than requiring six inputs on every invocation. Live evaluation still follows the Step 27 reviewer gate and links selected snapshots to its reviewed source/config/artifact hashes and reviewer approval evidence; final Step 28 completion reconciles evidence across all six required project curricula. Offline development/preparation may use the validated frozen development snapshot under D12.1.1 without claiming Step 27 approval. Discover completed runs recursively under D12.1.2 on fresh invocation, then freeze exact paths and identities in the generated manifest. Never choose an arbitrary latest run or rediscover a different input selection during resume. Copying or content-addressed references must detect source changes before use; never read a live growing production run as a frozen snapshot. Reject aliased output paths that could overwrite inputs.

Cache identity must bind actual snapshot/evidence/request material, endpoint identity, component/condition, rendered prompt and response schema, effective judge provider/model/settings, replicate, presentation order, and the original production request/policy and operative production rationale supplied to its critique, plus the explicit classification-versus-critique view identity. Critique execution depends on completion of the associated blind classification, but its prompt does not contain that classifier's answer. Seed or model-name-only keys are insufficient. Validate cache records against the current exact schedule and content; reject stale, truncated, duplicated, extra, or mismatched records rather than silently accepting the last row. Reuse only fully validated successes. Changing scoring alone may regenerate reports from identical valid judgments, with actual report bytes identifying the new report generation.

Validate exact request IDs, one complete output per scheduled pair/task, no missing/extra/duplicate endpoints or fields, allowed relation/direction, bounded enum/range values, expected condition/replicate/order, and evidence-reference containment. No partial or malformed response enters successful judgments. Output-format retry/repair calls count as attempts and usage. Prior successes survive an interruption, and resume does not repeat them. Failures and their later dispositions remain separate append-only audit evidence.

**Execution failure** includes missing/stale inputs, invalid request/output, transport errors after the configured retry limit, unaccounted scheduled judgments, or unreproducible/missing reports. Required evaluation cannot be marked complete with an unresolved execution failure; partial diagnostic reports remain clearly incomplete. This status is separate from production D13 and never rewrites `kg_run.json` or production validation. Honest exhausted/empty-stratum accounting under the selected sampling plan is not a transport failure or a fabricated success.

**Semantic ambiguity** is a valid judge assessment of insufficient or contradictory evidence. Retain and report it without converting it to failure or negative.

**Reported quality concern** includes production/judge disagreement, unsupported rationale, missed sampled judge-positive pairs, poor synthetic-control behavior, presentation sensitivity, and replicate/evidence-condition instability. These do not automatically fail a semantic threshold. Record their disposition under D12-A; deterministic harness defects still fail independent engineering review.

Usage accounting includes every attempt, valid/failed response, retry, component, curriculum, condition and model; input/output and available reasoning/cache token counts; observed cost when available; and unknown usage explicitly. The user removed evaluation budget ceilings: do not implement dollar, total-token, or total-attempt caps, cost-reservation machinery, or a required cost-estimate approval gate. Missing pricing or usage is recorded as unknown rather than zero and does not itself block evaluation completion. Sampling, repetitions, evidence limits and finite retry behavior still bound the scheduled work. No automatic model fallback, sample shrinkage, quota substitution, or unrequested repeated full evaluation is allowed.

After Section 3.3.10 approval, snapshot validation enforces all Section 1.6/D12.1.5 format-specific requirements uniformly across discovery, preparation, freezing, source revalidation, resume, cache reuse and reporting. Preserve original raw bytes/material bindings plus explicit interpretation version and material input hashes. Unsupported, ambiguous or inconsistent inputs fail before evidence publication or calls, with unchanged source/prior evidence; cache reuse rejects interpretation or input changes.

### D12.5 SETTLED — Concrete policy packet

The user selected the following policies on 2026-09-11 and separately confirmed the operational settings. Earlier alternatives are superseded, not selectable presets. Sampling and repetition counts are operational evaluation controls, not semantic release thresholds. CLI overrides within the rules below are authorized configuration choices; changing a selection algorithm, label meaning, required component, or interpretation contract still requires governance.

**D12-S — S1 sampling defaults, configurable through the CLI.**

Per curriculum, the default samples up to 15 pairs uniformly without replacement within each of the four production outcomes (60 base). Supplement to at least 2 examples for each of the 12 tags below, adding at most 24 distinct pairs. Independently select 36 uniform admissible upstream pairs plus 3 per each of the 12 upstream tags (at most 72 draws before deduplication).

The CLI must expose the following settings; their final flag spellings are delegated to Step 28 and must be documented in CLI help and Step 29 documentation:

| CLI setting                            | Default  | Contract                                                                 |
|----------------------------------------|----------|--------------------------------------------------------------------------|
| Production pairs per outcome           | 15       | Positive integer target for each of the four outcomes in each curriculum |
| Production examples per diagnostic tag | 2        | Positive integer minimum, including already-selected examples            |
| Independent uniform pairs              | 36       | Positive integer target per curriculum                                   |
| Independent pairs per upstream tag     | 3        | Positive integer target per tag per curriculum                           |
| Sampling seed                          | 20260911 | Explicit integer used for all deterministic selection and ordering       |

These settings apply uniformly to the curricula selected for the invocation. Missing settings use the documented S1 defaults; CLI values override those defaults and appear in the effective evaluator configuration, schedule and reports. Required Step 28 evidence covers all six curricula, even if invocations are separated. Invalid values fail before calls. Empty/exhausted populations produce counted shortfalls, not fabricated samples. There is no CLI switch that marks a skipped component as completed.

Selection uses canonical UUID sorting before seeded selection, without-replacement sampling within each cell, stable listed tag order for supplements, no reallocation of exhausted quotas, and deduplication with all selection routes retained. A supplement adds only the number needed to reach its configured target. Its maximum additional population is the number of tags multiplied by that target.

The four base outcomes are published `buildsTowards`, published `relatesTo`, final `no_relation`, and final `needs_review`. The 12 production tags are: checker correction; same rank; different valid ranks; missing coordinate; equal normalized SFI text; unequal SFI text with token Jaccard at least 0.5; at least one endpoint without supporting LCs; shared exact LC; both with LCs but no shared LC; a supporting LC linked to at least 10 eligible SFIs; unresolved self/ancestry; and production evidence truncation. Upstream tags substitute multi-parent DAG context for checker correction and reconstructed-evidence truncation for production truncation.

Normalization is Unicode NFKC, casefold, whitespace collapse; token sets are Unicode alphanumeric runs with no stopword removal, and empty-set similarity is zero. These are sampling tags, not pedagogical labels or production policy changes. Independent tag selection reads only upstream material and fixed policy, not nomination/results. Exact probability calculations are required for the uniform cohort; diagnostic cohorts report selection routes and counts without population estimates.

**D12-J — Dedicated LP evaluation judge.**

Step 28 adds the exact evaluator-only assignment below to the repository-root `.template.env` and local `.env`, using standard environment assignment syntax:

```dotenv
LLM_LP_EVAL_JUDGE_MODEL=anthropic:claude-opus-5
```

This governance task records the requirement; it does not edit either environment file. The later coding task must preserve unrelated environment entries and secrets, never print the full `.env`, and never stage or commit secrets. The local `.env` assignment is local configuration, not a tracked review artifact.

Add the corresponding settings field and `lp_eval_judge` binding in `backend/src/kgfeg/config.py`, following the LC evaluator architecture. Resolve the judge through `Settings.llm_config("lp_eval_judge")` and existing `kgs_settings("learning_progressions")`. The user approved reusing the existing shared model-registry settings: inherit the configured shared output-token limit and provider-specific effort/thinking settings, rather than inventing a second set of LP evaluator knobs. Those are per-request model settings, not aggregate evaluation budget ceilings.

Do not make the evaluator variable required by production startup or by AS/LC/LP execution. Under the user-approved 2026-09-16 amendment in Section 3.3.6, `BackendSettings.LLM_LP_EVAL_JUDGE_MODEL` defaults to `"anthropic:claude-opus-5"`, following the existing environment-backed model settings. Retain the explicit `.env` and `.template.env` assignment. An omitted environment variable uses that default; do not add a separate presence/blank-value guard in `config.py`. Existing evaluator validation still rejects explicitly blank, malformed or unsupported model identifiers and unavailable configured models, without substituting another model. Production continues to resolve `LLM_KG_MODEL` independently; do not change the LC evaluator binding or production registry behavior. Independent tests must verify the approved default, explicit overrides and production-model independence rather than require failure solely because the environment variable is omitted.

Record the actual provider/model and complete effective non-secret model settings in the frozen run manifest and material cache identities. The requested model identifier is a user choice, not evidence of provider availability or SDK compatibility. Verify compatibility during Step 28 preflight; if unsupported or unavailable, report the configuration/execution failure without silently substituting another model. Any model change requires explicit user direction and a fresh material-bound schedule/cache identity.

Expanded evidence doubles each positive production count/text/depth limit, retains mandatory warnings and references, and uses canonical stable ordering for additional paths/LCs/snippets. Any unchanged or unavailable evidence is recorded. Fresh contexts and blind prompts remain required even when production and judge models differ.

**D12-R — R1 defaults, configurable through the CLI.**

The default makes one base blind judgment per selected real pair in each component's required base condition and one separate operative-rationale critique per selected production pair. A pair selected in both components receives both the production-evidence blind judgment and the common reconstructed-upstream judgment, with separate identities; pair deduplication does not collapse these distinct conditions. Per curriculum, select up to 12 production and 12 independent pairs by seeded uniform selection within each cohort, without using judge results, for diagnostic repetition and evidence comparisons.

Each diagnostic pair receives two additional identical-presentation base judgments (three total by default), one endpoint-swapped presentation, one reversed evidence-list presentation, one expanded-evidence judgment, one LC-removed judgment, and one trustworthy-hierarchy-removed judgment. All variants are separate fresh calls; remap displayed endpoints to canonical identities before comparison.

The CLI must expose these settings with the documented R1 defaults; final flag spellings are delegated to Step 28:

| CLI setting                                             | Default | Contract                                                     |
|---------------------------------------------------------|---------|--------------------------------------------------------------|
| Base blind replicates                                   | 1       | Positive integer per selected real pair                      |
| Operative-rationale critique replicates                 | 1       | Positive integer per selected production pair                |
| Diagnostic pairs per cohort                             | 12      | Positive integer target in each cohort/curriculum            |
| Additional identical-presentation diagnostic replicates | 2       | Nonnegative integer; base plus additional must be at least 2 |
| Replicates per presentation/evidence variant            | 1       | Positive integer for each of the five variants               |
| Synthetic cases per control family                      | 5       | Positive integer target per family/curriculum                |
| Replicates per synthetic control                        | 3       | Positive integer                                             |
| Lexical baseline top-k                                  | 10      | Positive integer, capped by the available sampled population |

Resolve overrides before schedule materialization; record every effective value in config, manifest and reports. Counts may change, but required conditions, control families, blind-before-critique separation and honest reporting cannot be disabled. Changed schedules cannot silently reuse records with mismatched material identities; genuinely unchanged requests may reuse fully validated successes. Repeated judgments need not be odd because no majority erases disagreement.

The five D12.2 control families produce 25 cases per curriculum at default settings. Rationale-defect controls use the critique schema and the other controls use classification. Coding specifies explicit synthetic expectations as harness control definitions; testing independently challenges those definitions and authors separate regression fixtures. Score constant `no_relation` on all real sampled pairs and lexical top-k pair rankings by SFI token Jaccard within each cohort, with canonical UUID tie-breaking, without additional LLM calls. Lexical ranking is a comparison with judge outcomes, never an asserted relationship/direction. No automatic control-detection threshold applies.

Rationale grounding is reported separately as grounded (all material claims supported by cited shown evidence), partially_grounded (some but not all material claims supported), unsupported (no material claim supported or the central justification contradicted), or ambiguous (shown evidence cannot resolve grounding). Relationship classification remains the independent blind assessment; these categories are not combined into a pass/fail score. Exact enum spellings are an implementation detail; meaning and per-category reporting are policy.

**D12-B — No aggregate evaluation budget ceilings; finite operational execution.**

The user explicitly removed dollar, total-token, and total-API-attempt budgets. Do not add mandatory budget configuration, budget-based termination, cost reservations, spending-ceiling approvals, or a required pricing/estimated-cost preflight gate. Usage and available cost reporting remain required; unavailable accounting must be explicit.

Current evaluator execution settings are governed by Section 3.3.12: concurrency 4, a
180-second timeout per attempt, and at most 10 retries after the initial attempt. Retry
waits are 5, 10, 20, 30, 60, 120, 180, 200, 300 and 300 seconds. Timeout,
HTTP 400/429/5xx and invalid structured output permit retries within the same
11-attempt cycle; hidden SDK/output-validation retries remain disabled. Other
configuration, authentication, transport and integrity failures retain their
immediate-stop behavior. Preserve Section 3.3.11 recovery, execution boundaries,
lifetime accounting and explicit-rerun authorization, as amended by Section 3.3.12.
There is no aggregate evaluation spending budget.

Live evaluation still needs explicit user authorization for the fixed-input execution and effective settings; recording policy or approving implementation is not itself authorization to call an external LLM. Do not reintroduce budget approval as a condition of that authorization.

**D12-A — A1: project-user disposition of quality concerns.**

IDinsight, acting through the project user, records `acknowledged`, `investigate`, or `remediation_requested` for reported concern groups before Step 28 completion. The reviewer checks completeness, integrity, and faithful disclosure; the user need not label pairs or endorse semantic correctness.

Group dispositions retain affected pair/condition IDs, authority, timestamp, rationale and report hashes. No score automatically mandates remediation or release rejection, no execution failure can be waived as a quality concern, and confirmed specification defects still trigger earliest-owner remediation/review. A future semantic threshold or gold-set gate requires another approved governance amendment.

### D12.6 Build order, remediation, and limits

Step 24 is production D12 release-policy conformance, reassessed under the 2026-09-11 amendment to distinguish the independent evaluator obligation (now Step 28). Step 25 remains the structural/process matrix; its revalidation with Step 24 is recorded in Section 3.3.1. New Step 26 adds bounded production request concurrency with coding, independent testing and review; Step 27 full-run execution/validation follows that reviewer gate without losing any source/config/provenance/count/collision/checkpoint/reuse/D13 checks. Step 28 coding and subsequent independent offline testing may start early under D12.1.1 using the validated frozen Madhi development snapshot, with generalized N-curriculum behavior. Step 27 reviewer approval remains required for live evaluation and final Step 28 completion; the complete required evaluation/reporting evidence still covers all six reviewed project snapshots. Step 29 documents both kinds of evidence; Step 30 reviews them separately.

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

Concurrent/out-of-order completions must be buffered or otherwise serialized without reusable prefix gaps. New Step 26 adds D13.1's bounded dispatch, durable pending-completion and shutdown behavior. Its later format-removal amendment has separate implementation approval recorded in Section 3.3.3. For new production, prefix-safe resume below is available only with D13-C3-supported checkpoint evidence; historical prefix-only artifacts remain preserved evidence and may be read only through D12.1.5's historical evaluator contract after Section 3.3.10 approval; production cannot reuse them.

With `overwrite=false`, the pipeline reloads and validates candidates, requests, every successful checkpoint prefix, the failure record, and hashes/identifiers derived from the actual material upstream bundle, effective config, prompts, model settings, requests, and stored artifacts. It reuses fully validated prefixes without repeating completed calls and resumes at the earliest unfinished producer/checker stage for the first incomplete request. A valid saved producer draft may therefore be reused when the checker failed. Gaps, duplicates, out-of-order rows, truncation/invalid JSONL, misalignment, or stale material inputs fail closed. Material changes fail closed; any regeneration of affected deterministic stages needs separate authorization before calls resume. Unsupported-format detection itself never regenerates, upgrades or rewrites evidence. Prior failure records remain audit evidence and the resumed run records later disposition. Candidate-policy replacement is not a resume case: affected artifacts are deleted and regenerated under D3.

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

The existing settled D13 payload is the zero-tolerance halt, pre-call materialization, checkpoint, failure-artifact and resume contract above. D13.1 adds prospective Step 26 requirements with settled payloads approved for implementation, not a retroactive concurrent-dispatch obligation.

---

### D13.1 Settled Step 26 concurrency and checkpoint-format contract — approved for implementation

Step 26 owns this capability; historical Steps 12–15 and 22–25 were not required to dispatch concurrently or produce the new journal format. Their existing materialization, integrity, checkpoint and failure obligations remain binding under their historical contracts. Section 3.3.2 records the 2026-09-12 policy selections and implementation approval. The 2026-09-13 removal of old-format compatibility is prospective to the Step 26 implementation still awaiting reviewer approval; its separate specification approval is recorded in Section 3.3.3. It is not a retroactive defect finding or approval reopening.

**Required execution and integrity properties**

1. Validate and materialize the complete deterministic candidate/request population, summaries, IDs, exact pair coverage, ordering and content hashes before any external call. Request batching and candidate budgets remain independent of concurrency.
2. Bound admitted in-flight requests with a positive finite capacity. Do not create a task/future for every request and merely put a semaphore around API calls. Use bounded dispatch and backpressure; queued work and in-memory completion buffers must not grow with the entire population. Admit eligible requests in deterministic order while permitting independent work to overlap.
3. Each request resumes at its earliest unfinished validated stage. Its checker starts only after its own producer draft passes schema, coverage, endpoint, allowed-outcome and material-input checks. A draft awaiting prefix publication may satisfy this dependency only through the fully validated durable state settled below. At most one producer/checker call is active per request; no speculative checker or duplicate stage.
4. Out-of-order completion cannot determine reusable file order, candidate/pair/final IDs, conflict resolution or graph semantics. Existing successful draft/verdict/final JSONL files remain deterministic contiguous stage-specific prefixes. Never skip a failed index or put a successful suffix directly in those files. Each checker/final record retains exact draft/verdict dependencies.
5. Preserve every durably validated completed stage across interruption, failure and resume, including successes beyond a prefix gap. Validate material identity/dependencies before reuse, promote only newly contiguous prefixes and do not repeat durably completed calls. Separately validated pending completions do not relax successful-JSONL prefix validation. An external call whose outcome was never durably recorded is not a reusable success; retain known attempt and unknown-outcome evidence honestly.
6. Stop new request admission on the first observed exhausted stage failure. Record request, stage, attempt history and all affected pair IDs; D13-C2 controls only the already-admitted bounded set. Handle simultaneous failures and shutdown races. Keep failed/cancelled/unfinished/not-started states distinct from no_relation and needs_review. Any unresolved failed pair prevents successful LP/combined status and successful downstream finalization.
7. Preserve existing producer/checker retry counts and eligibility. Workers, SDK behavior, recovery and shutdown must not multiply/reset permitted attempt budgets. Race-safe usage/failure aggregation includes every observed request/stage/attempt, retry and result, including calls finishing after the first failure, available token/cost data, and explicitly unknown usage rather than zero. Retain prior failure evidence and later dispositions.
8. One coordinated writer owns checkpoint, receipt, transaction, pending-completion, failure and usage state within the existing exclusive generation-directory ownership boundary. Workers return outcomes to that owner; they do not independently append/replace shared files. Updates have crash-consistent recovery. Reject a competing process, ambiguous/tampered state and ownership loss; stop dispatch/publication and fail closed when integrity cannot be established.
9. Preserve validation before/after calls and actual effective-config/upstream/candidate/request/prompt/model/artifact identities, stage-specific resume and valid existing work. Adding concurrency or changing capacity grants no material-hash exemption. Apply D13-C3's supported-format gate and strict effective-config identity rules; complete material identity alone cannot authorize an unsupported format.
10. Preserve graph semantics, candidate selection/ranking/budgets, request content/batching, evidence limits, prompts and model selection through `LLM_KG_MODEL`. No AS/LC parallelism, cross-curriculum scheduler, adaptive tuning, shared-registry refactor, embedding work or evaluator coupling. Evaluator concurrency/settings remain governed separately by D12-B.

**SETTLED D13-C1 — Optional production capacity setting, default 4**

Use the exact field `kgs.lp.max_concurrent_requests`. Omission means 4; an explicitly supplied value must be a positive integer. Reject booleans, null, zero, negative values, strings and other nonintegers. Value 1 selects serial scheduling. The user selected a default of 4, rather than the alternative requiring an explicit field in all six profiles; omission has the same documented behavior for every compatible profile.

Capacity counts admitted LP requests in one run, including producer/checker stages and retry waits, with at most one active API call per request. A request batch occupies one slot regardless of its pair count. Capacity is not a cross-run/provider-wide quota and is independent of evaluator D12-B concurrency. Freeze the resolved value before dispatch; record it in effective runtime config and execution/usage evidence, and include it in actual effective-config material identity. Omitted and explicitly supplied 4 resolve to the same effective setting; exact raw config byte hashes remain accurately recorded where required. No hot reload, adaptive capacity tuning or new model variable is introduced.

This policy default makes concurrent execution the default for a new invocation under the approved implementation. It does not authorize changing the code/configuration consumed by an active run. Required-all-profiles/default-1 alternatives are rejected for this amendment.

**SETTLED D13-C2 — Finish active calls only after exhausted retries**

At the first observed exhausted stage failure, the coordinator closes admission and API dispatch. Calls already dispatched may return under their existing configured timeouts; no subsequent producer, checker or retry API call may start in that invocation, including for already-admitted requests or workers waiting to retry. Coordinate failure observation and dispatch so a shutdown race cannot launch another call after that boundary. Queued or admitted-but-not-dispatched work remains explicitly unfinished.

Validate and durably persist valid returning results and retain every observed failure/usage or unknown outcome. Deterministic local reconciliation and contiguous checkpoint promotion may finish using available validated dependencies; they cannot start another API call or turn the failed run into success. A returned valid producer draft is saved for its checker on a later permitted resume. A malformed returning result is recorded as failure and receives no new retry during shutdown. Finishing all remaining stages of admitted requests is rejected.

Keep existing retry counts, eligibility and configured call timeouts; no new timeout/backoff policy or hidden SDK retry allowance is introduced. Retry dispatch at every layer must respect the shutdown boundary. Unknown remote outcomes are not silently labeled uncharged or successful. Any unresolved failed pair still prevents LP/combined success. If implementation discovers that the existing call boundary cannot enforce this settled behavior, surface the concrete integration/policy blocker rather than inventing a timeout or weakening the rule.

**SETTLED D13-C3 — Durable completion journal, required checkpoint evidence and strict material identity**

The journal/concurrency policy was approved on 2026-09-12. The user approved the prospective format-removal amendment on 2026-09-13 after clarification of historical-artifact use. Section 3.3.3 records that approval; D13-C3-Z below settles the complete removal boundary, including native zero-counter files. Separate coding, independent testing and reviewer gates remain.

Retain an internal pending-completion journal controlled by the sole writer, separate from successful JSONL and failures, integrated with exclusive directory ownership and crash-safe transactions/receipts. Retain validated stage payloads, request index/ID, exact producer/checker dependencies, material hashes and attempt/usage identities. Gaps may exist only in the pending journal, never in successful JSONL. Reject duplicates, corruption, stale identity and inconsistent dependencies. Recovery/prefix promotion is idempotent without duplicate calls or accounting; validate journal records and transaction/prefix state before reuse. Persist completed work before releasing its capacity slot, with bounded in-memory dispatch/buffering. No public graph schema change or runtime checkpoint-policy selector is authorized.

**Supported evidence for new production.** An existing checkpoint must have complete authenticated successful-prefix and failure artifacts, a pending-completion journal, an attempt/usage journal, and their exact receipt coverage. Empty journals must be explicitly present, valid and authenticated; missing journals do not mean empty pending work or zero usage. Every stored producer/checker completion and failure must have its required durable attempt/accounting evidence without a legacy-count exemption. Preserve exact request/stage prerequisites, retry limits, material identities, counts and honest unknown outcomes. Fresh execution with no existing checkpoint evidence may initialize a complete current-format store; it must never mistake partial or unsupported evidence for a fresh store.

**Remove old-format execution support.** Remove prefix-only loading, reuse, recovery and automatic upgrading, including the old-format branch, legacy completion/failure offsets, missing-capacity fallback for stored execution material and compatibility-only bookkeeping. The optional default 4 in D13-C1 still resolves new runtime configuration; capacity 1 remains supported. It is not a fallback that fills a missing captured capacity in stored evidence. Do not migrate receipts, retrofit defaults, normalize/rewrite hashes or automatically regenerate results. Matching config/material hashes, complete prefixes or a final success report cannot waive the format requirement.

**Previously upgraded legacy evidence is unsupported.** Adding journals to old prefixes does not establish complete journal-backed execution. Reject any checkpoint or transaction that relies on legacy stage/failure counts to excuse absent attempt evidence, including a completed upgraded run with empty pending journals, matching material identity or later successful native attempts. Do not grandfather such evidence, manufacture missing attempts, remove its compatibility fields in place, or relabel it as native journal execution. Retain it unchanged for compatible historical inspection. At the inspected HEAD, the concrete fields are `legacy_stage_counts` and `legacy_failure_count`; they are not permitted accounting exemptions in the new implementation.

**SETTLED D13-C3-Z — Reject compatibility-counter-bearing formats, including native zero-counter files.** The approved proposed removal boundary rejects every checkpoint or transaction whose usage schema contains `legacy_stage_counts` or `legacy_failure_count`, even if all values are zero and native attempt coverage is complete. The new current schema omits both fields; no transitional reader, bookkeeping exemption or receipt rewrite is retained. Existing native journal files in the older schema are therefore unsupported by new production and remain preserved for historical validation. The alternative retaining a zero-counter reader is not adopted. Complete journals produced under the approved current schema retain the full recovery/integrity contract, including capacity 1.

**Reject before effects, including recovery and overwrite.** Check supported format before any API dispatch or artifact mutation in generation, resume, reconciliation/finalization, standalone export, final-bundle reuse and the production pipeline entry path. The entry path must reject before AS/LC calls or artifact writes and before creating/replacing `kg_run.json`; unsupported-format failure does not overwrite a historical success/failure report. Report the affected path and incompatibility through the returned error/console, not by writing into the rejected evidence. Existing file bytes and directory membership remain unchanged: no archive, deletion, reset, receipt/transaction repair or retirement, projection rewrite, default injection, migration or regeneration. An overwrite flag is not permission to bypass this gate. This is a new Step 26 preflight obligation, not a retroactive defect in earlier pipeline entry behavior.

**Current-format transactions remain recoverable.** The transient transaction file may be absent in a committed store. A complete valid current-format transaction may recover an interrupted initial creation (all predecessor artifact hashes absent) or an ordinary update (complete predecessor hashes), including exact mixtures of its recorded old/new bytes. Validate its complete next snapshot, format, identities, dependencies and exact predecessor/next byte states before any repair. Reject old prefix-only transactions, compatibility upgrades with only newly added journals absent from the predecessor, unsupported next snapshots and unexplained partial evidence without changes. A missing on-disk journal justified by a valid interrupted current-format transaction is recoverable; unexplained absence is not. Sole-writer ownership, race-safe publication and active-call drain remain mandatory.

**Historical snapshot boundary.** Historical execution remains evidence of its producing contract; serial or prefix-only execution is not retrospectively a defect. After Section 3.3.10 approval, D12.1.5 permits strict read-only evaluator validation of complete historical prefixes or current journals independently of the three supported projection families. This does not change D13-C3/Z production rejection, authorize recovery/reuse/migration or relabel old evidence as concurrent execution. Preserve bytes, hashes, configuration/request identities, receipts, reports and optional producing provenance. No regeneration or live execution is authorized. Early development and its fixed review base remain; observed snapshots certify no Step 27/28 completion.

Separate testing independently challenges the approved boundary and all retained properties under Section 5.4. Reviewer approval follows coding, independent testing and the exact user-created candidate at the fixed Step 25 review base. The required amendment implementation approval is now recorded in Section 3.3.3; dependent work proceeds only in the appropriate separate role task, not this governance-only task.

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

Section 3.3.3 records approval of the prospective checkpoint-format removal. Section 3.3.2 records the earlier concurrency approval. Historical approvals and next-step handoffs below retain their original numbering and do not override the current order.

### 3.3.1 Historical decision and approval sequence — original numbering

D12-S/J/R/B/A are settled: S1 and R1 are documented CLI-configurable defaults; D12-J names the dedicated environment setting and shared model-settings binding; D12-B removes aggregate budgets and records the separately approved operational settings; A1 assigns concern disposition to the user for IDinsight. At that approval, no implementation-governing decision payload remained open.

**Implementation approval recorded — 2026-09-11.** After reviewing the final synchronized governance amendment, including the original-production rationale-critique correction and common independent-sample evidence correction, the user explicitly stated: "ok i approve." This approves the amendment for implementation, including the K=24/F=25 revalidation scope and unchanged former-frontier review base `540ea950378ce54b54da1c7a93491b525609b574`. Approval applies to the reviewed governance changes in the working tree; HEAD remains the former frontier and is not a commit containing this amendment. This is user specification approval, not independent reviewer reapproval of Steps 24–25 or approval of an implemented harness. It does not authorize live evaluation/full pipeline calls, Git staging/commits, or changes to active runs. No budget approval is required.

Original amendment handoff (subsequently completed at the exact SHA recorded below): testing-primary cross-step reassessment of Step 24 release-policy conformance and affected Step 25 matrix against `540ea950378ce54b54da1c7a93491b525609b574`, followed by a user-created candidate commit when repository content changes and independent reviewer reapproval. If only execution/oracle evidence changes against an already reviewed identical tree, reuse the exact candidate SHA with immutable evidence; do not invent an empty commit. Do not implement Step 27 while revalidating Steps 24–25.

After that gate, testing owns Step 26 full-run execution/validation with explicit live authorization. The early-start extension below permits Step 27 coding and subsequent independent offline testing to overlap Step 26; live evaluation and final Step 27 approval retain the Step 26 reviewer prerequisite. A separate testing task owns evaluator tests and authorized execution, and the reviewer gates the exact candidate plus immutable evaluation evidence. Step 28 follows Step 27 approval; Step 29 uses the original pre-Step-1 baseline through the exact Step 28 approved candidate and reviews all production and evaluation evidence. Each handoff must resolve its actual review-base/candidate SHA at that time; future SHAs must be observed rather than invented.


**Step 24–25 reapproval recorded.** The independent reviewer reapproved the complete K=24/F=25 chain at candidate `28d4f1218c71237bc7fda1027a8ac55866bd345f`, tree `45c2dd1c1248237cc87b9161535ff785099e1306`, using fixed review base `540ea950378ce54b54da1c7a93491b525609b574`. The exact-candidate matrix passed 2,571 cases with one existing collision-stage skip; all 93 release-policy cases passed. This approval is limited to that SHA/tree and establishes no pedagogical correctness.

**Early-start extension — implementation approval recorded, 2026-09-11.** The user requested starting Step 27 using existing Madhi mathematics LP KG artifacts while other curricula become available, and explicitly required a harness generalized for N curricula. D12.1.1 records the complete development/input/generalization/completion policy. At this governance edit, the observed repository is `/Users/tzz/Projects/private/idi/KGForEdGlobal`, branch `tz6/lp-kg-build-step-26`, HEAD `ce5b9da27a00f8162d8f087539b0657c0c5ee474`, initially clean. The single later commit formats Markdown tables; it is not represented as reviewer-approved.

After a second review of the synchronized six-file governance amendment for major issues, the user explicitly stated: "ok i approve". This approves the reviewed early-start/N-curriculum extension for implementation. Approval applies to the governance changes in the working tree; HEAD remains `ce5b9da27a00f8162d8f087539b0657c0c5ee474` and does not contain this extension. This is user specification approval, not independent approval of an implemented evaluator or certification of the Madhi input snapshot. Step 27 coding and subsequent independent offline testing may proceed under D12.1.1. Live evaluation, final Step 27 completion, exact candidate binding, Git authorization and active-run protections remain governed by the unchanged requirements.

This extension changes future Step 26–27 scheduling and evaluator generality only. It preserves Steps 1–25 production/release-policy contracts, both evidence-condition corrections, D12-S/J/R/B/A and all Step 26 completion obligations, so it does not reopen the reapproved Step 24–25 chain. Approval remains bound to the earlier exact SHA/tree; the new governance and intervening changes must be included in the later candidate review range. Earlier-owner defects discovered later still require explicit reopening.

With the user's approval of this synchronized extension recorded, the next coding task may implement Step 27 against review base `28d4f1218c71237bc7fda1027a8ac55866bd345f`, observe the actual current checkout/candidate state, and validate/freeze the selected Madhi development input before using it. A separate testing task follows coding and may validate offline before Step 26 finishes. Do not infer missing future SHAs, snapshot paths, approval status or artifacts. Step 26 continues under its own existing execution authorization; this extension grants no new live-call, active-run, Git or production-tuning authorization.


### 3.3.2 Current concurrency amendment — implementation approval recorded, 2026-09-12

The user requested this new bounded concurrent production execution step. Current references are renumbered; historical records retain original numbers and approval scopes. D13-C1/C2/C3 are now settled. Policy selection remains distinct from explicit approval of the completed synchronized amendment.

Observed root: `/Users/tzz/Projects/private/idi/KGForEdGlobal`; branch `tz6/lp-kg-build-step-26`; HEAD `dd92b9be8c70677457d080e19e0b1b694746cec1`. Initial `git status --short` was empty: no staged, modified or untracked user changes. Supplied governance review base `28d4f1218c71237bc7fda1027a8ac55866bd345f` is reachable and an ancestor. Intervening commits:

- `ce5b9da27a00f8162d8f087539b0657c0c5ee474`: Markdown formatting.
- `7a7394a367c97c3767350ed03a0c051f04a5240b`: approved early evaluator governance; its message uses historical Step 27 numbering.
- `dd92b9be8c70677457d080e19e0b1b694746cec1`: config paths and Pratham's LP source-evidence character bound changed from 2000 to 3000. The latter is material config, not just a path rename. Preserve these committed user changes and include them in candidate review and effective-config/request/snapshot identity checks. Earlier Step 25 approval does not approve this later tree or authorize stale reuse.

No `evaluate_lps.py` or `lp_eval` implementation files were found in this checkout's inspected entry/evaluation source directories. That does not prove no evaluator work exists elsewhere. Observe actual work at handoff and preserve its fixed base and evidence binding.

**Concurrency policy selections recorded — 2026-09-12.** After the ELI5 decision explanation, the user selected: "default 4 concurrent requests"; "finishing active calls only"; and "the journal". D13.1 records optional `kgs.lp.max_concurrent_requests` with default 4, the exact active-call shutdown boundary, and the durable journal with the explained strict material-identity compatibility policy. The required-field alternative was not selected. All three policy payloads were complete at that decision-resolution point; the subsequent explicit implementation approval is recorded below.

At this decision-resolution follow-up, repository root/branch/HEAD and the intervening commits remain as observed above. The six governance files already contain the uncommitted amendment from this task; preserve that draft and any user changes. Only those six files are updated to settle the policies. There are no production/config/test/environment/results changes or Git mutations from this task.

**Concurrency amendment implementation approval recorded — 2026-09-12.** After reviewing the completed synchronized amendment with all three decisions settled, the user explicitly stated: "yes i approve". This approves the six-file amendment for implementation, including optional `kgs.lp.max_concurrent_requests` default 4, active-calls-only failure shutdown, durable journal/strict material compatibility, the new Step 26 and renumbered Steps 27–30, preserved early Step 28 allowance and explicit review-base handling.

Approval applies to the reviewed governance changes in the working tree. Observed root remains `/Users/tzz/Projects/private/idi/KGForEdGlobal`, branch `tz6/lp-kg-build-step-26`, HEAD `dd92b9be8c70677457d080e19e0b1b694746cec1`, with the six governance files modified and no other reported working-tree changes. HEAD does not contain this amendment. Governance/new-Step-26 review base remains `28d4f1218c71237bc7fda1027a8ac55866bd345f`. This is user specification approval, not independent reviewer approval of new Step 26 or approval of a later candidate tree. The separate coding task may begin; independent testing, a user-created candidate and reviewer approval still follow. No live-call, active-run change, Git mutation or production implementation in this governance-only task is authorized by this record. Historical approvals and earlier exact SHA/tree bindings remain intact.

The user explicitly approved the completed synchronized concurrency amendment for implementation on 2026-09-12, stating: "yes i approve". The approval is recorded in engineering-brief Section 3.3.2; it is specification approval, not independent approval of implemented Step 26 code. The user settled D13-C1/C2/C3 on 2026-09-12: optional `kgs.lp.max_concurrent_requests` defaults to 4 admitted requests per run; after exhausted failure, finish active API calls only and start no further calls; retain validated out-of-order completions in a durable sole-writer journal with strict material-identity compatibility. These settled choices do not revoke already approved independent early Step 28 coding or subsequent independent offline testing under D12.1.1. Those activities may proceed before new Step 26 approval and while Step 27 inputs remain unavailable, using a complete validated frozen Madhi development snapshot; they must remain independent of unapproved production implementation and mutable inputs.

Current ownership: Steps 0–25 retain their obligations; new Step 26 bounded production LP request concurrency belongs to coding, followed by separate testing and reviewer approval; Step 27 six full pipelines belongs to testing, followed by reviewer approval; Step 28 generalized evaluator belongs to coding, followed by separate testing/execution and review; Step 29 documentation belongs to coding then testing/review; Step 30 is the terminal read-only comprehensive review. Historical records retain their original step numbers and approvals.

Every handoff must apply these review-base rules:

- New Step 26 uses revalidated Step 25 SHA `28d4f1218c71237bc7fda1027a8ac55866bd345f` throughout coding, testing, remediation and review. Include all intervening governance, source, config and test changes; observed HEAD is not automatically approved.
- Renumbered Step 27 uses the exact new Step 26 reviewer-approved SHA, observed from its verdict, not an invented future SHA. Previously started full runs retain their original code/config/source hashes, recorded review base and execution authorization. Their evidence cannot be relabeled as generated by the concurrency candidate. The Step 27 reviewer assesses any proposed retained snapshot against the new candidate and actual material identities, requiring fresh explicitly authorized evidence for affected paths. No automatic rerun, compatibility waiver or active-run change is granted.
- Step 28 work already started as historical Step 27 keeps fixed base `28d4f1218c71237bc7fda1027a8ac55866bd345f` through final evaluator review. New Step 26/27 approvals and producer snapshot SHAs are additional dependency/input evidence, not replacement review bases. Account for all intervening candidate changes and revalidate affected evidence without relabeling earlier results.
- Step 29 uses the exact Step 28 reviewer-approved SHA. Step 30 uses the original pre-Step-1 baseline through the exact Step 29 reviewer-approved candidate.

Live Step 28 evaluation and final evaluator completion retain Step 27 reviewer approval, independent deterministic harness testing, actual evaluator implementation/input content binding, all six reviewed project snapshots for completion, required reports and concern dispositions, and separate explicit live authorization. Preserve generalized N >= 1 operation, both evidence-condition corrections and D12-S/J/R/B/A. Offline subset readiness is not final approval.

Concurrency is a prospective enhancement, not evidence of an earlier defect. Serial execution alone establishes no violation. If evidence proves an earlier approved-state defect or retroactive contract change, identify earliest owner K, former frontier F and its fixed review base, then explicitly reopen and revalidate the affected chain. No earlier approval is automatically reopened by this amendment.

**Approval impact:** This governance inspection establishes no earlier production defect. Existing D13 anticipates serialized out-of-order completion without requiring concurrent dispatch. Inspected generation/checkpoint code uses per-request stages and an exclusive store/transaction boundary; the enhancement must preserve those guarantees. This is prospective Step 26 work, not automatic reopening of Step 15 or 22. Steps 0–25 obligations and exact approved SHA/tree remain intact. The later Pratham config edit requires material review and stale-input checks, not an unsupported defect verdict; a demonstrated earlier violation must reopen its earliest owner and affected chain.


### 3.3.3 Prospective Step 26 checkpoint-format removal — implementation approval recorded, 2026-09-13

The user requested governance-only removal of old checkpoint-format compatibility from the new production LP pipeline. The selected behavior requires complete journal-bearing evidence; removes prefix-only load/recovery/upgrade and compatibility-only bookkeeping; rejects unsupported formats before calls or artifact changes; and preserves evidence without receipt migration, concurrency-default retrofit, hash rewriting or automatic regeneration. D13-C3 rejects previously upgraded legacy evidence and D13-C3-Z adopts the proposed complete removal boundary: reject either compatibility counter field, including native zero-valued instances, with no transitional reader. The separate approval below applies to this later amendment; the earlier "yes i approve" remains the 2026-09-12 approval record.

**Rechecked repository binding:** root `/Users/tzz/Projects/private/idi/KGForEdGlobal`; branch `tz6/lp-kg-build-step-26`; HEAD `36058a5489a09c79717713fc33f8799a40de0179`; initial `git status --short` empty, with no pre-existing user changes. Fixed Step 26 review base `28d4f1218c71237bc7fda1027a8ac55866bd345f` is reachable and an ancestor. HEAD has no inferred reviewer approval. The review range includes the previously recorded formatting, early-evaluator governance and config edits, then `2bf4790324e5202d65c3fca27915f240b61b371c` (concurrency governance), and the subsequent concurrency/dispatch/test changes through observed HEAD, including removal of the unused checker wrapper and its tests. Preserve the committed Pratham LP source-evidence limit of 3000 characters and all unrelated work.

**Read-only implementation findings:** `lp_checkpoints.py` currently accepts prefix-only and journal-bearing receipt coverage, recovers both transaction shapes and upgrades prefix-only state in `begin_run()`. Legacy counters exempt old completions/failures from attempt validation and are serialized even for native runs. Generation can archive before checkpoint validation; artifact readers treat journals as optional; final reuse can rewrite projections; the CLI persists run status before reaching LP. The new pre-effect gate must cover these reachable paths without weakening current transaction recovery. Existing actual-transport eager dispatch coordination, private SDK client/routing/cleanup, finite retries, local-model support and known-zero-versus-unknown usage protections (R1/R2) remain required. Do not restore the unused checker wrapper or its removed tests.

**Preserved historical Madhi evidence:** `results/kg_for_ed/33c5da78839a7611308dd30f342b72f931a661235e094cc641832ec2a5548444/kgs`. The user supplied these earlier read-only inspection findings: 1,205 complete producer/checker/final request records; 472 `buildsTowards`, 435 `relatesTo` and 298 `no_relation`; no recorded generation failures, cycles or identifier collisions; original-schema reconstruction reproduced candidate/request bytes, final claims/provenance and graph projections; current raw Madhi configuration matches the recorded configuration; the snapshot is prefix-only; its exact producing Git SHA is absent from inspected records. These are supplied historical observations, not a new artifact inspection or reviewer verdict in this task. Preserve the directory unchanged and permit compatible historical validation/use subject to D12's existing integrity requirements. Do not invent the producing SHA, require regeneration just because concurrency was introduced, or certify Step 27/28 completion.

**Approval impact and ownership:** the change is owned by the current unapproved Step 26; no earlier approval or Steps 0–25 chain is automatically reopened. Sections 3.3.1–3.3.2 remain historical records. Supported-format requirements for new invocations are prospective, not claims that older prefix-only runs violated their producing contract. Preserve independent early Step 28 coding/offline testing under D12.1.1 and fixed base `28d4f1218c71237bc7fda1027a8ac55866bd345f`; production snapshot identities are additional input evidence, not replacement review bases. Step 27 still needs the exact Step 26 reviewer-approved SHA and a reviewer decision on retained snapshots and any affected fresh evidence. No rerun or compatibility waiver is granted.

**Implementation approval recorded — 2026-09-13.** The user asked to confirm continued use of old completed LP artifacts, specifically the Madhi results. The clarification distinguished downstream consumption and compatible historical validation from invoking the new production pipeline on an old directory: unsupported checkpoints cannot resume or participate in completed-run reuse/projection rewriting, including at concurrency 1; historical graph files remain usable and unchanged. After this clarification of the proposed removal, the user explicitly stated: "ok in that case, i approve of the edits". This records approval of the synchronized amendment and its proposed complete removal boundary in D13-C3-Z, including zero-valued compatibility fields. No schema exception is introduced.

Approval binds to the reviewed governance changes in the working tree and this faithful decision/approval record. Repository root and branch remain as recorded above; HEAD remains `36058a5489a09c79717713fc33f8799a40de0179`, which does not contain the amendment. At approval recording, only the six governance files are modified; no unrelated changes were observed. Fixed review base remains `28d4f1218c71237bc7fda1027a8ac55866bd345f`. No implementation-governing decision or payload remains open for this amendment. This is user specification approval for separate coding followed by independent testing and reviewer approval, not independent Step 26 approval, a producing-SHA certification, permission to start Step 27, live-call authorization, Git authorization or authority to modify historical results/active runs. Earlier approvals and independent early Step 28 work remain intact.

**Scope and next gates:** this task edits only the brief, root instructions and four role files. No production/tests/configs/results, live calls, data/graveyard inspection, active-run intervention, dependencies or Git mutations are authorized. With implementation approval recorded, separate coding implements the removal; independent testing removes obsolete acceptance expectations and validates rejection, preserved evidence, zero dispatch and all current-format/R1/R2 regressions, including full-suite `--run-slow`; the user creates the candidate and the reviewer gates Step 26. Copy-ready task handoffs belong in chat, not governance files.

---

### 3.3.4 Step 28 automatic discovery and CLI amendment — user approval recorded, 2026-09-15

During early Step 28 planning, the user requested one sequential evaluator command without an offline CLI mode, a starting results directory as the only required CLI input, automatic completed-run discovery, generated manifests for resume, and output under `results/lp_evals/`. The user then requested support for both `results/kg_for_ed/<hash>` and `results/kg_for_ed/<curriculum>/<hash>` parent layouts by locating each individual `kgs` directory, and stated: "i would like to explicitly approve the automatic discovery and update the governance brief as well in this chat session as well. no need to start a separate chat session just to update the governance brief."

This records user approval of the requested automatic-discovery behavior and direction to synchronize governance in this chat. D12.1.2 specifies the single-command/root-discovery contract and preserves the existing integrity, material-binding and execution gates. The user expressly selected this same-chat governance workflow; this turn is governance-only, with no production implementation or test authorship. Subsequent coding remains a distinct work phase under the coding role, and independent testing/review retain their separate ownership.

Observed repository: `/Users/tzz/Projects/private/idi/KGForEdGlobal`; branch `tz6/lp-kg-build-step-28`; HEAD `7cc69fc2616fd3fd8a140fbc617f010e7fdc42e3`; initially clean working tree. Fixed Step 28 review base `28d4f1218c71237bc7fda1027a8ac55866bd345f` is reachable and an ancestor, and remains unchanged through implementation, testing, remediation and final evaluator review. HEAD is observed state, not inferred reviewer approval.

**Approval impact:** this prospectively changes unimplemented Step 28 selection/CLI/output behavior. It does not reopen Steps 0–25, approve Step 26/27, certify any development snapshot, waive missing producing-code evidence, change S1/R1 or judge settings, or authorize live calls, Git mutations, production tuning or active-run changes. Historical explicit-selection language is superseded only by D12.1.2's validated recursive discovery and manifest-frozen selection. Final Step 28 completion still needs all six reviewed project snapshots, complete required evaluation/reporting/dispositions and independent reviewer approval. Handoffs remain in chat.

---

### 3.3.5 Step 28 producing-commit requirement removed — user approval recorded, 2026-09-15

After clarification that the producing SHA identifies the pipeline Git commit and is not technically necessary to assess fixed artifacts, the user stated: "ok we can strike this requirement then. it doesn't make sense anymore to keep it around." The user also requested correction of three pylint complexity findings in the existing Step 28 discovery code and asked that their added Raises documentation be preserved.

D12.1.3 records the complete removal of the evaluator's producing-commit prerequisite, including development and final evaluation; known provenance remains preserved and unknown provenance is not fabricated. Required source/config/artifact identities, compatible historical validation, frozen schedules, evaluator candidate/review-base bindings and all remaining execution/completion gates remain intact. Earlier statements in Sections 3.3.3–3.3.4 about not waiving the producing-SHA requirement are historical and superseded for Step 28 by this amendment.

The current request authorizes a governance amendment followed by coding-only discovery remediation in this same task as sequential work phases. It does not authorize test authorship, live calls, production-result changes or Git mutations. No other evaluator part is advanced by the pylint remediation.

Observed root: `/Users/tzz/Projects/private/idi/KGForEdGlobal`; branch `tz6/lp-kg-build-step-28`; HEAD `7cc69fc2616fd3fd8a140fbc617f010e7fdc42e3`; fixed review base `28d4f1218c71237bc7fda1027a8ac55866bd345f` remains an ancestor. Pre-existing changes comprise the six discovery-governance files and untracked evaluator discovery source, including user-added Raises documentation and formatting. Preserve them. This changes the current unapproved Step 28 contract and does not reopen earlier approved steps, replace any review base, supply a reviewer verdict or certify a snapshot.

---

### 3.3.6 Step 28 evaluator model default restored — user approval recorded, 2026-09-16

The user explicitly instructed: "let's change config.py back to how it was before. it will always be used in the `.env` file. no need for this guard. we can explicitly update the briefs/instructions as well in this chat." This approves the exact restoration of `LLM_LP_EVAL_JUDGE_MODEL: str = "anthropic:claude-opus-5"` and removal of the newly added missing/blank guard from the `lp_eval_judge` registry branch. Record this governance amendment first, then restore the configuration in a separate coding phase of the same task; no additional policy choice or approval is pending.

The former D12-J requirement to reject an omitted evaluator variable is superseded. An omitted value uses the declared default; explicit environment configuration continues to override it. Retain the `.template.env` and local `.env` assignment, shared registry settings, independent production/LC model bindings, and existing evaluator syntax/provider/availability validation. A default for an omitted setting is not permission to replace an explicitly invalid or unavailable configured model. Tests remain independently owned; their missing-setting oracle must be reassessed against this amendment, not preserved by retaining the rejected guard.

Observed root: `/Users/tzz/Projects/private/idi/KGForEdGlobal`; branch `tz6/lp-kg-build-step-28`; HEAD `e8412756b13a0ab6c3fa569f7c684aa9adfe4ffa`. Fixed review base remains `28d4f1218c71237bc7fda1027a8ac55866bd345f`. Pre-existing changes are the three source repairs in `config.py`, `entries/create_kgs.py` and `evals/lp_eval/schemas.py`, the Ghana/Pratham config-wiring test correction, and untracked evaluator tests. Preserve the production lock-retention and grounding-consistency repairs and all testing-owned work. The user's lock question requests clarification and does not authorize restoring production lock deletion.

This changes current unapproved Step 28 configuration behavior only; it does not reopen earlier approvals, certify Step 28 completion, alter Git-independent evaluation or optional producing-SHA provenance, or waive actual material hashes. Independent testing/review, Step 27 approval, separate live authorization, required reports/dispositions and all six reviewed snapshots for final completion remain. No automated-test authorship, live calls, active-run/result changes, staging or commits are authorized.

### 3.3.7 AS+LC projection description clarified — historical user decision, 2026-09-17

The user confirmed that the existing code is correct and requested a governance clarification. Section 1.6 incorrectly described the existing AS+LC JSONL delivery files as internal projections. AS-only and AS+LC JSONL use the Learning Commons-shaped delivery wire format; AS+LC+LP JSONL uses the separate internal projection format.

This corrects the specification's description of existing behavior. It requires no implementation, test, user-documentation, or generated-artifact changes and authorizes no regeneration. It does not itself reopen earlier approvals, replace any review base, or establish final Step 28 or formal Step 29 approval.

---

### 3.3.8 AS+LC+LP delivery export contract change — implementation approval recorded, 2026-09-17

Historical compatibility clauses in this approval record are superseded by the Section 3.3.9 removal approved on 2026-09-18. The original delivery amendment and K=21/F=25 approval impact remain recorded.

**Selected policy and rationale.** The user requested that the two AS+LC+LP delivery JSONL files extend the established AS/LC Learning Commons wire format. Section 1.6 records the complete five-part policy and historical compatibility boundary. The preceding Section 3.3.7 correctly described the former internal projection contract and remains historical; this new request changes that contract. Existing snake_case AS+LC+LP projections are not retrospectively labeled implementation defects. No policy payload or implementation-governing placeholder remains open. After reviewing the governance-only amendment, the user explicitly stated "i approve." on 2026-09-17. This approves the amended contract for the separate coding phase in this task; it does not certify the implementation or authorize live calls or Git mutations.

**Observed repository state.** Root `/Users/tzz/Projects/private/idi/KGForEdGlobal`; branch `tz6/lp-kg-build-step-29`; HEAD `1bef7ef8734cead185bb57e75519c42b03c03de8`; initial `git status --short` empty, with no pre-existing staged, modified or untracked changes. Fixed review base `28d4f1218c71237bc7fda1027a8ac55866bd345f` is reachable and an ancestor of HEAD. Intervening commits include governance, concurrency/checkpoint, evaluator, config, test and documentation work. Their presence and the branch name establish no later reviewer approval.

**Approval impact on adoption.** This is a retroactive change to the approved export contract owned by **K=21**, with recorded approved frontier **F=25** at `28d4f1218c71237bc7fda1027a8ac55866bd345f`. User approval of the amended contract reopens and invalidates progression approval for Steps **21, 22, 23, 24 and 25** pending affected-chain independent revalidation and reviewer reapproval. Retain that exact former-frontier SHA as the fixed cross-step review base throughout implementation, testing and review. Steps 0–20 retain their contracts, including unchanged internal finalization, provenance and bundle schemas. If evidence shows an earlier affected contract, stop and explicitly reopen its owner rather than silently extending the scope. Historical approvals remain records of the old contract, not approval of the amended implementation.

**Sequential roles and bounded implementation scope.** The user explicitly requested this governance-only phase, followed only after approval by a separate coding phase in this same task. Coding then changes Step 21 export and the minimum existing consumer surfaces required for compatibility: Steps 22–23 orchestration/reuse integration, current checkpoint/lock protections, evaluator `sampling.py` input validation and affected public documentation. Inspect `lp_export.py`, `lc_export.py`, shared wire serializers/schemas and all those integration points before implementation. This authorization is a compatibility exception for existing downstream consumers and documentation, not formal Step 26–29 approval, unrelated later functionality or production tuning. Preserve independent D12.1.1 work only where unaffected and independent of the reopened contract; affected evaluator compatibility work belongs to this amendment and requires independent validation. Other advancement beyond the former frontier waits for reapproval of the affected chain. The fixed Step 28 review base and all its live/final gates remain unchanged.

**Independent revalidation obligations.** Testing independently derives the wire-format oracle from Section 1.6 and existing AS/LC serializers, challenges node and upstream relationship preservation, all four relationship types, raw aliases/values/wrappers, endpoint resolution/direction, ordering, completeness/counts/collisions and internal metadata preservation. Revalidate Step 22 orchestration and failure/status propagation, Step 23 exact-match reuse/stale-input rejection/projection writing, Step 24 unchanged production release policy and the affected Step 25 six-curriculum matrix. Include unchanged internal artifact formats; missing/tampered upstream delivery rejection; locks, read-back and no-write unsupported-checkpoint gates; historical/current evaluator input validation independently of checkpoint format; and frozen-manifest/hash/cache integrity. Public documentation must match implemented consumer behavior and keep agent-process/development notes out. Coding authors no automated tests and provides the independent testing handoff in chat. A user-created candidate containing changed repository material and independent reviewer reapproval of the full affected chain follow testing; no agent stages or commits.

**Evidence protection and approval gate.** No existing results, evaluation evidence or active runs may be modified; no live calls, Git mutations, automatic migration, hash rewrite, historical relabeling or checkpoint-compatibility waiver is authorized. Historical snapshots remain usable through explicit compatible read-only validation without certification of Step 27/28 completion. Governance approval is not reviewer approval or live execution authorization. User implementation approval is recorded above; no further specification approval is pending for this scope. Independent testing and reviewer reapproval remain required.

### 3.3.9 Historical evaluator compatibility removal — implementation approval recorded, 2026-09-18

This is the historical removal approval. Section 3.3.10 records its separately approved reversal; the approval statements below do not authorize the extension.

**User decision and scope.** After identifying historical projection, checkpoint and configuration/request compatibility in `sampling.py`, the user stated: "ok let's remove the historical compatibility---it's not necessary". They intend to regenerate legacy artifacts themselves. Sections 1.6 and D12.1.5 record the complete selected current-format-only behavior; no policy choice or implementation-governing placeholder remains. This is a support-contract change, not a finding that the historical readers violated the former brief. On 2026-09-18 the user explicitly stated: "I approve implementation of the removal contract in engineering-brief Sections 1.6, D12.1.5 and 3.3.9." This authorizes the coding phase after a separate governance-only approval record in this task; no further approval of this same scope is pending.

**Repository and review base.** Observed root `/Users/tzz/Projects/private/idi/KGForEdGlobal`, branch `tz6/lp-kg-build-step-29`, HEAD `1bef7ef8734cead185bb57e75519c42b03c03de8`. Fixed base `28d4f1218c71237bc7fda1027a8ac55866bd345f` remains reachable and ancestral; HEAD is observation, not approval. Pre-existing changes comprise six governance files, evaluator/export implementation, tests/support and public documentation; preserve them. The existing reopened range K=21–F=25 and independent testing/reviewer reapproval remain. Removal is owned by the existing unapproved Step 28 input consumer already included in the delivery amendment; no additional earlier approved defect, later approval or review-base replacement is inferred.

**Implementation-start state and prior verdict.** At approval recording, root and branch are unchanged, HEAD is `2ad4414260984b50ebe954a459e8cc2e75010d3a`, the working tree is clean and the fixed base is an ancestor. The user reports that independent testing and review approved the delivery-amendment K=21–F=25 chain and its existing evaluator compatibility at that candidate. This does not approve historical-compatibility removal or final Step 28 completion. Preserve the earlier observations above as history and require independent testing and reviewer reassessment of this change and affected integration paths at the unchanged fixed base. No new earlier defect or later-step approval is implied.

**Sequential work and ownership.** Extend the existing same-chat delivery amendment workflow: record the explicit approval in a governance-only phase, then implement removal in a distinct coding phase in this same task. Coding owns evaluator source and minimum dependent schemas/documentation, preserving production `lp_export.py` behavior and current-format integrity. Testing owns fixture/expectation updates and independent red-team validation of D12.1.5 and affected-chain paths. Reviewer reapproval follows independent testing and the required user-created candidate. Do not mix production/test authorship into governance or embed task handoff messages in these files.

**Evidence and regeneration.** Do not modify, migrate, delete, regenerate or relabel historical source/results/evaluation evidence or active runs. Unsupported completed snapshots fail before evaluator freezing/publication/calls; prior frozen inputs and schedules fail current-format/material checks without repair. The user's intention to regenerate is not authorization for agent live execution, source-PDF runs, artifact rewriting or Git mutations. Preserve original historical approvals/evidence as history, optional producing SHA, fixed review bases, current runtime defaults, production checkpoint gates, D12 material hashes and all live/final-completion gates. Older guarantees of historical-reader availability in Sections 3.3.2–3.3.8 and dated root/role amendments cease to govern new evaluator acceptance under this approved amendment; they are not erased or relabeled.

**Validation and next gate.** Independently test current/current positive inputs and rejection for either old-format dimension, captured-configuration incompatibility, obsolete frozen inputs and stale material-bound schedules; retain byte/hash/lock/no-write evidence and current-format request reconstruction checks. Reassess affected evaluator, export/reuse/orchestration, release-policy and six-curriculum structural paths at the unchanged review base. The earlier F1 repair's JSON-type-sensitive comparison remains required. The approval-recording phase changes only six governance Markdown files; implementation is authorized as a distinct coding phase. Independent testing, a user-created candidate and reviewer reassessment follow coding.

### 3.3.10 Historical and current evaluator compatibility extension — implementation approval recorded, 2026-09-18

**User direction and approval gate.** The user requests both historical and current LP snapshot support, explicitly reversing the 2026-09-18 current-only policy. They require a governance-only phase to synchronize a concrete contract and obtain explicit implementation approval before code, followed by a distinct coding phase in this task. Sections 1.6 and D12.1.5 contain the complete proposed compatibility packet; no implementation-governing placeholder or unselected alternative remains in that packet. The user explicitly approved the synchronized packet on 2026-09-18, stating: "ok i approve of these changes then." This is specification approval for the separate coding phase in this task, not an independent implementation verdict or live authorization. Earlier delivery, removal, concurrency and evaluator approvals do not approve this extension or final Step 28 completion. Historical Sections 3.3.1–3.3.9 remain approval/execution history; adoption supersedes only their conflicting evaluator acceptance requirements.

**Observed repository and intervening changes.** Root `/Users/tzz/Projects/private/idi/KGForEdGlobal`; branch `tz6/lp-kg-build-step-29`; HEAD `e0400e598217cb06ea420415fe8816d741f58a75`; initial `git status --short` empty. Fixed review base `28d4f1218c71237bc7fda1027a8ac55866bd345f` is reachable and ancestral. Read-only inspection covered the intervening commit log and changed-file inventory: early-start/concurrency governance; production dispatch, checkpoint format removal and validated-proof caches; runtime profile/path/capacity changes including the preserved Pratham evidence limit; the evaluator from discovery/freezing through sampling, prompts, scheduling, execution, reporting and CLI; lock, grounding, structured-output, citation and JSON-comparison repairs; tests/fixtures, dependency/build and documentation changes; `2ad4414260984b50ebe954a459e8cc2e75010d3a` wire-delivery/strict-projection changes; and HEAD's current-only evaluator removal with its tests/docs. Preserve these changes and assess affected paths against the full fixed-base range, rather than reverting the removal commit or treating older compatibility code as a sufficient oracle. Observed history and branch naming establish no additional reviewer verdict.

**Read-only artifact observations, not validation verdicts.** The authorized inspection root is `results/kg_for_ed_orig`; each listed directory contains a `kgs` child. The following observations were rechecked from completion metadata, receipt shapes and captured configuration, raw receipt-covered artifact hashes, projection rows and combined-bundle counts. They do not certify source/config/request reconstruction, full historical/current checkpoint semantics, Step 27 review or evaluator readiness.

| Curriculum / directory prefix                   | Completion observation                        | Checkpoint/configuration observation                                        | Converted flat nodes / relationships  |
|-------------------------------------------------|-----------------------------------------------|-----------------------------------------------------------------------------|---------------------------------------|
| Nigeria mathematics / `09d6b52b54b2`            | Run success; checkpoint completed             | Four-file prefix receipt; capacity absent in captured config and material   | 429 / 932                             |
| Tamil Nadu (Madhi) mathematics / `33c5da78839a` | Run success; checkpoint completed             | Four-file prefix receipt; capacity absent in captured config and material   | 655 / 1,581                           |
| Ghana English / `e49a79263701`                  | Run success; checkpoint completed             | Four-file prefix receipt; capacity absent in captured config and material   | 703 / 1,788                           |
| Rwanda mathematics / `7b9629e6bd5a`             | Run success; checkpoint completed             | Six-file journal receipt; recorded config/material capacity 4               | 1,343 / 3,254                         |
| Ghana mathematics / `8d59d76cb439`              | Run success; checkpoint completed             | Six-file journal receipt; recorded config/material capacity 4               | 533 / 1,174                           |
| CBSE (Pratham) science / `3f8c25c19ed8`         | No completion timestamp or combined LP bundle | Incomplete; preserve exclusion, do not consume growing populations          | Unselected                            |

All five completed runs have flat camelCase projections, not current wire wrappers. A read-only comparison of every row reproduced their node and relationship projections from their combined bundles using exactly the top-level map and the `caseIdentifierUUID` endpoint-key enum mapping in D12.1.5. Nested metadata was unchanged. All receipt-covered raw file hashes matched in this inspection; these local checks are not independent full validation. Their combined validation reports bind internal/audit artifacts rather than the two delivery projection byte streams. The converted streams must gain their own actual byte identities in future evaluator manifests; no recorded historical hash is changed or bypassed. No artifacts or evaluation evidence were generated or edited by this inspection.

**Approval impact.** The current unapproved Step 28 owns this input-consumer extension. It changes support policy, not a finding that current-only rejection violated its approved contract. No new earlier production defect or additional K below 21 is inferred. The affected integration/reassessment range remains K=21–F=25: preserve and independently revalidate export parity, orchestration/status/locking, exact-material reuse and no-write unsupported-production-checkpoint rejection, release-policy separation and the six-curriculum structural matrix wherever touched. Historical reapproval reported at `2ad4414260984b50ebe954a459e8cc2e75010d3a` remains bound to that tree and contract; it cannot approve this extension. On adoption, affected compatibility assumptions/evidence require renewed testing and reviewer reassessment before progression relies on them. Steps 0–20 and production D13-C3/Z remain unchanged; discovering an earlier affected contract requires explicit reopening. Keep the fixed review base throughout; optional producing provenance is not a runtime Git gate.

**Approval-recording state.** Root, branch, HEAD and fixed review base remain as observed above. At approval recording, only the six governance Markdown files contain the synchronized proposal; no source, test, fixture, config, result or existing evaluation-evidence changes were present. Preserve this working-tree proposal. The approval-recording phase changes governance Markdown only and is completed before the separately authorized coding phase begins. Independent testing owns Section 5.5 fixtures and validation; reviewer reassessment and all live/final gates remain.

**Sequential ownership and protected evidence.** This phase changes only the brief, root instructions and four role instruction files. After the user explicitly approves the synchronized packet, record that approval in a governance-only phase and begin the distinct coding phase in this task. Coding owns evaluator readers, dependent schemas/material-binding/report support and minimum consumer documentation; any shared production-support change must preserve the existing production rejection/export contracts. Independent testing owns tests, fixtures and validation; reviewer reassessment follows the required reviewable candidate and independent evidence. Copy-ready handoffs stay in chat. No results, `data/`, `graveyard/`, active runs or existing evaluation evidence may be changed; no live calls, Git mutations or automatic regeneration are authorized. Final Step 28 still requires Step 27 reviewer approval, all six reviewed snapshots, complete authorized evaluation/reporting and concern dispositions. Read-only support for a snapshot cannot supply any of those approvals.

### 3.3.11 Step 28 automatic recovery — implementation authorized, 2026-09-19

The retry-count, wait-schedule and HTTP 400 automatic-retry clauses below are
superseded by Section 3.3.12; its other recovery requirements remain applicable.

The user authorized governance followed by a separate coding phase in this task for automatic recovery of newly created evaluator invocations, then explicitly accepted starting fresh without backward compatibility for saved evaluator state. Fixed source-review base remains `28d4f1218c71237bc7fda1027a8ac55866bd345f`. This amendment changes current unapproved Step 28 only; F1 remains withdrawn and F2–F4 resolved. No production contract or earlier approval is reopened.

Rerunning the same command selects the most recent matching incomplete invocation for the canonical starting directory and equal effective sampling, repetition and judge settings. Order by immutable UTC creation time, then schedule identifier for ties. Validate matching candidates before selecting; a corrupt, unsupported, materially stale or locked candidate is a specific blocker, never a reason to silently rediscover or fall back to fresh work. If all matches are complete, return the most recent validated complete invocation without repeating calls. Explicit new-discovery and manifest controls remain optional. Resume uses only the original frozen inputs and schedule and reuses every validated success.

Persist append-only execution-session boundaries and attempt history. Each user-initiated execution gives an already exhausted retryable request one new cycle of an initial attempt plus at most two retries. A partially consumed cycle continues with its remaining allowance; exhausting it during that execution cannot open another cycle. Lifetime attempt numbering never resets. Within an execution, timeout, HTTP 429/5xx and invalid structured output (including citations) permit bounded automatic retries. The user subsequently explicitly approved retrying after any prior evaluator execution error, including transport, authentication/configuration failures and uncertain remote outcomes. A deliberate rerun supplies authorization for possible duplicate remote execution/cost; no historical failure alone blocks it. Before the new execution boundary, append an interrupted/unknown-usage outcome for any durable unfinished start, without changing the original start. Failures that stopped an execution may be tried again only in a later user execution; partial three-attempt cycles keep their remaining allowance. Current invalid configuration, stale inputs, integrity failures and live ownership conflicts still fail validation before dispatch. Never reinterpret unknown usage as zero or a proven remote cancellation. Preserve concurrency 4, 180-second attempt timeout, 5/20-second within-cycle waits, SDK retry accounting, stop-and-drain, exclusive ownership and all original failures, usage and later dispositions. Logs and reports distinguish session, lifetime attempt and cycle progress, reused successes and remaining requests.

Use a new evaluator storage contract. Old saved evaluator invocations are unsupported: no reader, migration, success import or linked continuation is required for unsupported pre-v2 evaluator stores; current-format invocation progress remains resumable without implementation-hash gates. Preserve strict input and interpretation binding for new stores; implementation hashes are no longer collected or required. This does not remove D12.1.5 historical/current production snapshot readers or weaken production checkpoint restrictions. The user accepts losing old evaluator progress and starting fresh; this coding task does not delete real results or existing evidence or execute the fresh evaluation.

The user subsequently approved removing evaluator implementation hashes entirely. Do not collect, compare or require Python source hashes, reader implementation hashes, execution implementation hashes, scorer hashes, renderer hashes or a source-transition allowlist for evaluator preparation, execution, resume or reporting. Delete recovery_transition.json and its transition machinery. Source edits alone never block evaluation. Preserve frozen raw input/configuration/prompt/response-schema/model/request/schedule/ledger/output identities, interpretation format/version, strict response validation and execution locks. Existing saved implementation fields remain inert historical metadata only where needed to read unchanged evidence and reproduce existing request IDs; never compare them with current source or assign them to new attempts. Fresh evaluation records contain no implementation digests. The existing invocation must continue automatically with validated successes and original request IDs intact, without rewriting prior evidence. Regenerated reports and presentations are identified by their actual output bytes. This supersedes conflicting evaluator implementation-hash clauses in earlier approval records and generic role instructions; production requirements and the fixed source-review base are unchanged. This approval authorizes governance followed by coding in the same task; independent testing/review and separate live execution gates remain.

Coding owns source/support/consumer documentation and existing offline checks; independent testing owns new regression coverage and validation, followed by reviewer reassessment. No live calls, test authorship, Git mutations, final Step 28 completion or later-step advancement are authorized. The user's explicit approval of this plan supplies implementation authorization; no further specification approval is pending.

### 3.3.12 Step 28 retry expansion and provider-error diagnostics — 2026-09-20

The user selected and manually implemented the following evaluator-only change: 10
retries after the initial attempt, giving 11 attempts per cycle, with retry waits of 5,
10, 20, 30, 60, 120, 180, 200, 300 and 300 seconds. Concurrency remains 4, each attempt
retains its 180-second timeout, and hidden SDK/output-validation retries remain
disabled. This supersedes the two-retry, three-attempt and 5/20-second clauses in D12-B
and Section 3.3.11.

Timeouts, HTTP 400/429/5xx and invalid structured output, including citations, permit
bounded automatic retries. HTTP 400 remains recorded as configuration failure but is
retryable. The current implementation recognizes it through the configuration category
and the exact failure-message prefix "Judge provider returned HTTP 400.". This applies
to all HTTP 400 responses, not only the provider message "Invalid request data". Other
configuration, authentication, transport and integrity failures retain their existing
automatic-stop behavior.

Preserve frozen requests, validated-success reuse, lifetime attempt numbering,
append-only execution boundaries, unknown-usage accounting, exclusive ownership and
stop-and-drain. Explicit reruns renew exhausted cycles once; partially consumed cycles
retain their remaining allowance. No execution automatically renews an exhausted cycle.
Reports calculate cycle progress from the saved execution settings.

Capture only string-valued provider error type/message fields when present, redact the
active API key, bound them to 200/4,000 characters respectively before adding
truncation markers, and JSON-escape the saved diagnostics. Retain the provider request
ID in usage evidence. Do not claim complete sensitive-data redaction or save full error
bodies/request headers through this path.

The changed retry count and waits are material execution settings. Do not rewrite old
schedules or attempt history to make them compatible. The user selected fresh
evaluation for the changed settings; matching ten-retry invocations remain resumable.
Historical evidence remains unchanged.

# 4. Invariants

Invariants 1–57 remain production code-level contracts. Invariant 58 preserves structural-only production success while acknowledging the separate amended completion obligation. Invariants 59–66 govern only the Step 28 evaluator. D12.1.1 separates early offline development from live evaluation and final completion, which retain Step 27 reviewer approval. The user's implementation approval of the early-start extension is recorded in Section 3.3.

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
47. **SETTLED — Stage-specific prefix-safe checkpoints:** validated producer drafts, checker verdicts, and reconciled responses are written in deterministic contiguous prefixes; failures are separate, and `overwrite=false` resumes at the earliest unfinished stage without repeating valid completed calls. This behavior is code-owned and has no runtime checkpoint/resume policy fields. New Step 26 production additionally requires D13-C3-supported journal-bearing evidence; prefix validity alone is insufficient. Historical prefix-only evidence remains governed by its producing contract and may be validated read-only under D12.1.5 after Section 3.3.10 approval, without production resume/reuse compatibility.
48. **SETTLED — Prefix validation fails closed:** gaps, duplicates, out-of-order records, truncation, invalid JSONL, request misalignment, and hashes or identifiers that show stale material inputs are rejected. Runtime configuration cannot select fingerprint inputs or mismatch behavior. After approval of the prospective Step 26 amendment, unsupported checkpoint/transaction formats fail before calls, recovery or any artifact changes under D13-C3; identical material does not waive this gate.
49. **SETTLED — Stale final bundle rejection:** `overwrite=false` reuses `as_lc_lp_kg_bundle.json` only when hashes/identifiers derived from the actual material LP and upstream inputs and artifacts match. New Step 26 production also requires supported checkpoint evidence before reuse or projection writes under D13-C3. Section 1.6/D12.1.5 govern independent evaluator format-specific readers after Section 3.3.10 approval, preserving original raw/material hashes and interpretation binding without waiving production reuse gates. Candidate-policy replacements do not use a version compatibility check; affected artifacts are deleted and regenerated under separate authorization.
50. **SETTLED — Failed validation remains inspectable:** final artifacts may ordinarily be written for diagnosis, but `kg_run.json` cannot report success when LP or combined validation fails. The prospective Step 26 unsupported-format preflight is an explicit no-write case: preserve the existing run manifest and all other evidence; report rejection externally without asserting success for the new invocation. This does not relabel a historical run's recorded status.
51. **SETTLED — Count reconciliation:** summary counts equal actual list and JSONL counts for eligible SFIs, candidates, requests, judgments, failures, final relationships, nodes, and all four relationship types.
52. **SETTLED — Identifier collision absence:** framework, SFI, LC, `hasChild`, `supports`, `buildsTowards`, and `relatesTo` identifiers are unique in the combined graph.
53. **SETTLED — Existing artifacts are not mutated:** standalone AS and AS+LC bundle/projection schemas remain unchanged.
54. **SETTLED — Upstream combined content is preserved:** the AS+LC framework, SFIs, LCs, `hasChild`, `supports`, summaries, unresolved data, and complete `entity_provenance` mapping are copied without deletion or reshaping before additive LP fields/provenance are introduced.
55. **SETTLED — Combined node parity:** `as_lc_lp_nodes.jsonl` preserves validated AS+LC delivery node records exactly under Section 1.6, with exactly the framework, SFI and LC node set in the internal combined bundle.
56. **SETTLED — Combined relationship completeness:** `as_lc_lp_relationships.jsonl` preserves validated AS+LC delivery relationships and appends all `buildsTowards`/`relatesTo` records using the same wire serializers/aliases under Section 1.6. The complete four-type union, identities and direction match the internal bundle; complete internal metadata remains in unchanged internal artifacts.
57. **SETTLED — Ordering is serialization detail:** preserve upstream delivery record order and deterministic identifier order within appended `buildsTowards`, then `relatesTo`, groups under Section 1.6. Downstream semantics rely on IDs, resolved endpoint keys and relationship types rather than line order.
58. **SETTLED — D12 production non-gate and disclosure:** production LP/combined success does not consume an evaluation score, human gold set, or semantic approval; `needs_review` stays visible/nonpublishing/nonblocking. Separate required Step 28 execution/reporting gates numbered project completion; neither process results nor judge assessments prove pedagogical correctness.

For evaluator inspection only, historical flat projections satisfy the historical projection contracts in D12.1.5 after Section 3.3.10 approval; invariants 55–57 continue to govern current production wire exports unchanged. Historical acceptance requires complete parity and historical ordering, never a waiver of identities, counts, metadata or material integrity.

## 4.7 Independent evaluation (Step 28 only)

59. **SETTLED — Read-only production boundary:** evaluation uses fixed validated inputs, never mutates production graphs/config/prompts/candidate policy or their success reports, and writes distinct evaluation artifacts.
60. **SETTLED — Blindness and evidence separation:** independent classification uses a redacted view retaining permitted facts; production conclusions/rationale and control labels cannot leak into it. After classification is frozen, a separate critic receives original bounded production evidence and the operative rationale, without the classifier's answer. Classification and critique have distinct request/cache identities. Exact production, reconstructed bounded upstream, expanded upstream, and evidence-removal conditions remain separate; removed evidence cannot survive in derived fields or be used to rescore original-production grounding. Every independently sampled pair receives the same bounded-upstream construction rules without consulting nomination status; freeze those payloads before joining production outcomes. Nomination coverage uses their common-condition judge-positive denominator, never nomination-dependent evidence conditions.
61. **SETTLED — Reproducible independent sampling:** both production outcome strata and independently selected admissible upstream populations are required; seeds, memberships, shortfalls, selection routes and denominators are retained. Unasserted real pairs are never assumed negative.
62. **SETTLED — Evaluator integrity:** exact schedule/output/endpoint/evidence validation, material-content-bound cache reuse, distinct replicate/order identities, separate failure records, bounded calls and usage accounting are mandatory. After Section 3.3.10 approval, raw byte identities, independent projection/checkpoint/configuration descriptors and interpretation version bind every lifecycle boundary under D12.1.5; interpretation changes cannot silently reuse old frozen evidence or caches.
63. **SETTLED — Honest interpretation:** relationship support, rationale grounding, production agreement, sample-conditioned nomination coverage, controls and judge stability are separate reported assessments. They are not semantic precision/recall or proof of pedagogical correctness.
64. **SETTLED — Completion versus quality:** unresolved execution failures prevent evaluation completion; ambiguity is a valid response; quality concerns and all disagreements retain accountable disposition without an automatic semantic score threshold. D12-A assigns concern disposition to IDinsight through the project user.
65. **SETTLED — Independent validation and remediation:** coding authors executable harness support; separate testing authors tests and validates/executes; reviewer gates completion. Confirmed production defects follow D14 earliest-stage remediation and rerun, not evaluator graph edits.
66. **SETTLED — N-curriculum evaluator and staged readiness:** generic evaluation supports any N >= 1 compatible curriculum snapshots selected by validated recursive discovery and frozen manifests under D12.1.2 without curriculum-name, roster or fixed-count branches. Every selected snapshot must satisfy D12.1.5's supported projection/checkpoint/captured-configuration contract after Section 3.3.10 approval and validate its required source/config/artifact bindings; a producing Git SHA is optional under D12.1.3. Unavailable unselected curricula do not block offline development. A Madhi development snapshot cannot substitute for the six reviewed Step 27 snapshots, authorized evaluation and complete reporting required at the final Step 28 gate.

## 4.8 Prospective concurrency and checkpoint-format invariants (Step 26 only)

The 2026-09-12 approval requires D13.1's optional positive-integer capacity defaulting to 4, bounded admission/buffers, per-request validated producer-before-checker order, sole-writer crash-safe journals and contiguous prefix promotion, strict material-bound stage reuse, active-calls-only shutdown and complete race-safe accounting. The 2026-09-13 amendment adds required supported-format journal-bearing evidence, rejection of prefix-only and previously upgraded legacy evidence, and pre-effect rejection across new production entry/resume/recovery/overwrite/final-reuse paths. D13-C3 defines complete current-format interrupted-transaction recovery and settled D13-C3-Z rejects compatibility fields even when zero; removal implementation approval is recorded in Section 3.3.3. Preserve capacity 1, local-model test support and all actual-transport/R1/R2 protections. These are prospective Step 26 obligations, not retroactive invalidation of historical artifacts or earlier approvals. D12 development/input/completion requirements remain intact.

---

# 5. Build order

Section 3.3.10's historical/current evaluator extension is a complete packet explicitly approved by the user on 2026-09-18. Historical approvals below do not authorize this reversal. After approval, coding remains a distinct phase, followed by independent Section 5.5 testing and affected-chain reviewer reassessment; no production checkpoint compatibility or later-step advancement is granted.

The Section 3.3.9 removal has explicit implementation approval recorded on 2026-09-18 for a distinct coding phase, followed by independent testing and reviewer reassessment. The Section 3.3.8 export amendment has explicit user approval for its separate coding phase, followed by revalidation of K=21 through F=25 at the fixed former-frontier review base. Minimum existing downstream compatibility/documentation changes are scoped there and do not approve later steps. No implementation starts at Step 1 until Step 0 is complete. The sole early-start exception is approved D12.1.1: Step 28 coding and subsequent independent offline testing may proceed while new Step 26 is pending and overlap Step 27. Live evaluation/final Step 28 completion retain Step 27 reviewer approval. The earlier Step 26 concurrency amendment was approved in Section 3.3.2; the later format-removal amendment has its own complete settled packet and recorded implementation approval under Section 3.3.3. Step 27 requires Step 26 reviewer approval. Existing active runs remain protected. Earlier step rows retain historical contracts; the new supported-format gate applies prospectively through Step 26's integration and regression obligations.

| Step  | One reviewable implementation aspect                                                                                 | Primary files/outputs                                                                                                                                                    | Review and test boundary                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
|-------|----------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 0     | Record D1–D14 and later governance amendments consistently, then obtain implementation approval                      | This brief plus synchronized repository/role governance instructions                                                                                                     | Every concrete payload is present; dependent model/config/invariant/build/test language agrees; no implementation-governing placeholder or unresolved decision remains; user implementation OK recorded                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 1     | Establish six-curriculum LP regression fixtures                                                                      | New reduced fixtures under the repository's test area                                                                                                                    | Fixture loader verifies current AS+LC bundle shapes, counts, DAG parents, unresolved flags, and LC alignments without changing production code                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 2     | Define the intrinsic `kgs.lp` Pydantic models and standalone field validators                                        | `backend/src/kgfeg/schemas.py`                                                                                                                                           | Tests cover relation-specific pair policies; D2 coordinate type/order; D3 per-SFI/total budgets; D10's required two-state policy; D11 author/provider/template/approver; positive batch/evidence bounds; and D13 retry counts. Reject runtime candidate algorithm/version/strategy/technology/signal/ranking/tie-breaking/fingerprint selectors, D2 invariant selectors, license-source/provenance switches, checkpoint/resume policies, and failure-tolerance thresholds; cross-profile wiring remains Step 3                                                                                                                                                                                                                                                                                            |
| 3     | Wire `kgs.lp` as required, add AS-policy cross-validation, and update all six configs atomically                     | `backend/src/kgfeg/schemas.py`; `examples/**/config*.json`                                                                                                               | Tests prove all six profiles reproduce the exact D1 matrices, D2 coordinate types/orders, D3 per-SFI/total budgets, D10 inclusion-with-warning state, D11 configured values/template, retry counts, and curriculum instructions, with no repeated D2/D3/D11/D13 invariant fields. Cross-validation rejects unknown values, omitted variable policy, invariant-override fields, and copy/paste errors; later owning steps test the code-owned runtime behavior                                                                                                                                                                                                                                                                                                                                             |
| 4     | Replace dormant LP schemas with the approved intrinsic pair/evidence/judgment record schemas                         | `backend/src/kgfeg/kgs/schemas.py`                                                                                                                                       | Schema tests cover valid accepted/negative/review records and reject intrinsic defects such as malformed UUIDs, self-pairs, illegal enum combinations, and invalid confidence/rationale fields; request-relative endpoint/coverage checks remain owned by Steps 12 and 14                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 5     | Add LP usage buckets and model-settings plumbing using `LLM_KG_MODEL`                                                | `kgs/llm.py`, existing model registry calls                                                                                                                              | Usage serialization includes producer/checker buckets and `kgs_settings("learning_progressions")` is exercised; no prompt or agent factory is implemented before Steps 13–14, and no new model environment setting is introduced                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 6     | Build an AS+LC graph index that is safe for trees and DAGs                                                           | New `kgs/lp_index.py` or equivalent                                                                                                                                      | Tests verify Pratham multi-parent ancestry, framework-root handling, LC-by-SFI indexes, and deterministic traversal order                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 7     | Implement local developmental-coordinate resolution                                                                  | New LP index/selection utility                                                                                                                                           | Tests cover every exact D2 order, Madhi scope-only Class, explicit Grade/Class nodes, alias canonicalization, missing-coordinate `buildsTowards` exclusion and `relatesTo` retention, invalid/ambiguous/conflicting hard failure, same-rank permission, forward-only direction, and unlimited gaps                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 8     | Implement LP SFI eligibility and unresolved-policy reporting                                                         | New `kgs/lp_selection.py`; `lp_eligible_sfis.json`; `lp_eligibility_report.json`                                                                                         | Tests cover exact D1 closed-world participation, D10 exclude/include states, all six inclusion-with-warning profiles, no exception/sidecar path, D2 precedence, independent LC eligibility, multiple grains, exact counts, and warning propagation without using fallback root as evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 9     | Implement hard candidate filters and deterministic pair IDs                                                          | New `kgs/lp_candidates.py`                                                                                                                                               | Tests prove no self-pairs, no D1-disallowed pairs, no D2-disallowed directions, one unordered D4 logical pair, and IDs stable under encounter/input ordering                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 10    | Implement named evidence features within the built-in D3 candidate policy                                            | `lp_candidates.py` or `lp_evidence.py`                                                                                                                                   | Each code-owned deterministic non-embedding signal is tested independently; tests cover hierarchy/DAG context, local rank, LC overlap, text, code/source/audit handling, named triggering values, and the rule that no feature publishes an edge. No runtime strategy registry, algorithm selector, or enabled-signal list is introduced                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 11    | Implement the built-in candidate policy's union, ranking, and budgets                                                | `lp_candidates.py`; `lp_candidate_pairs.jsonl`; `lp_candidate_summary.json`                                                                                              | Deterministic tests verify bounded nomination, union, deduplication, configured per-SFI/total budget behavior before LLM work, code-owned stable ranking and total tie-breaking, pair-scale bounds, actual input/artifact content hashes, and stable candidate records without runtime ranking selectors or an internal policy version; tests acknowledge rather than claim to measure the D3/D12 recall LIMIT                                                                                                                                                                                                                                                                                                                                                                                            |
| 12    | Implement bounded LP request construction and complete pre-call materialization                                      | New `kgs/lp_generation.py`; `lp_generation_requests.jsonl`                                                                                                               | Tests verify complete validated candidate/request populations are written and reconcile before any external call, with exact coverage/order/IDs/counts/content hashes, bounded context, all parent paths, LC evidence limits, warnings, and stable request IDs                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 13    | Implement the LP producer prompt and agent                                                                           | `kgs/prompts.py`, `kgs/agents.py`, `kgs/llm.py`                                                                                                                          | Prompt fixtures demonstrate relation semantics, D6/D7 recurrence distinctions, curriculum-specific examples, D10 warnings, explicit `no_relation`/`needs_review`, D14 no overrides, and no out-of-request endpoints                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 14    | Implement the independent LP checker and deterministic integrity validators                                          | `kgs/prompts.py`, `kgs/agents.py`, `kgs/validators.py`, `kgs/llm.py`                                                                                                     | Tests cover one complete D4 pair judgment, accept/correct, missing/extra/duplicate pair, endpoint leakage, D1/D2/D5/D6 illegal outcomes, warning/evidence parity, and complete rather than patch correction                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 15    | Implement resumable generation orchestration and D13 failure accounting                                              | `kgs/lp_generation.py`; draft/verdict/final/failure artifacts                                                                                                            | Interrupted-stage tests verify code-owned validated deterministic draft/verdict/final prefixes, separate failures, zero tolerance after configured retries, halt/no-success status, earliest-unfinished-stage resume without repeated valid calls, and fail-closed gaps/duplicates/order/truncation/alignment/material-input behavior without runtime checkpoint/resume/mismatch selectors                                                                                                                                                                                                                                                                                                                                                                                                                |
| 16    | Reconcile final pair decisions under D4–D10 and D14                                                                  | New `kgs/lp_finalization.py`; `lp_final_claims.json`                                                                                                                     | Tests cover unified judgments, D6 precedence/exclusivity, D7 recurrence, D5 canonical `relatesTo`, D8 global DAG diagnostics, D9 direct-edge-only behavior, D10 warnings, D14 absence of overrides/hand edits, duplicates/conflicts, and visible nonpublishing `needs_review`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 17    | Mint deterministic `Relationship` records and correct generic descriptions                                           | `kgs/lp_finalization.py`, `kgs/schemas.py`                                                                                                                               | UUID snapshot tests cover both types; endpoint and metadata tests verify configured D11 author/provider/template/approver values plus code-owned source-license inheritance, template substitution, complete provenance, and Learning Commons-compatible semantics                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 18    | Implement standalone LP graph validation                                                                             | New LP validator or `kgs/validators.py`                                                                                                                                  | Tests cover endpoints, self-loops, duplicates, D1/D2/D4–D10/D14 policy, complete D8 diagnostics, direct-edge provenance, exact D11 metadata/provenance, counts/collisions, unresolved-warning consistency, D13 zero-failure success condition, and rejection of structural-validity-as-semantic-proof claims                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 19    | Write standalone LP provenance, summary, unresolved, failure, and validation artifacts                               | `lp_relationship_provenance.json`, `lp_generation_summary.json`, `lp_unresolved_items.json`, `lp_generation_failures.json`, `lp_validation_report.json`, relation files  | Round-trip validation and independent count reconciliation cover every artifact; `needs_review`, policy exclusions, and D13 failures remain separate; warnings/provenance and accepted/nonpublished counts agree                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 20    | Add the AS+LC+LP bundle schema and compiler                                                                          | New `kgs/lp_export.py`, `kgs/schemas.py`                                                                                                                                 | Bundle tests verify all upstream nodes, relationships, summaries, unresolved data, and the complete AS+LC `entity_provenance` mapping are preserved verbatim; LP fields/provenance are additive; required content hashes are complete; invalid LP blocks successful compilation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 21    | Write `as_lc_lp_nodes.jsonl` and `as_lc_lp_relationships.jsonl`                                                      | `kgs/lp_export.py`                                                                                                                                                       | Section 1.6 wire-delivery tests prove exact AS+LC record preservation, aliases/wrappers, endpoint resolution, node parity, four-type union/order and unchanged internal formats; Section 3.3.8 governs K=21/F=25 revalidation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 22    | Integrate LP into `create_kgs.build_kgs()` after `compile_as_lc_kg()`                                                | `entries/create_kgs.py`                                                                                                                                                  | Orchestration test verifies phase order, returned bundle use, failure propagation, usage accounting, and `kg_run.json` success/error state                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 23    | Implement final-bundle reuse and stale-input detection                                                               | `lp_export.py`, LP utilities                                                                                                                                             | Tests cover exact-match reuse using hashes/identifiers derived from actual material upstream/config/candidate/request/checkpoint/response/prompt/model/finalization inputs and artifacts, Section 1.6 wire projection writing and upstream integrity, invalid bundles, and D13 prefix alignment; preserve production reuse gates while evaluator compatibility follows Sections 3.3.8–3.3.10 and D12.1.5 after its separate approval. Candidate-policy replacement requires deletion/regeneration rather than an internal version compatibility layer                                                                                                                                                                                                                                                     |
| 24    | Add deterministic D12 release-policy conformance coverage                                                            | Automated tests and deterministic synthetic fixtures/fakes                                                                                                               | Prove `needs_review` is visible, nonpublishing, and nonblocking; processing failure remains D13-governed; production success needs no evaluator artifact, semantic score, gold set, human sample/cadence/approval or threshold; structural/process validity cannot be labeled pedagogical truth; findings cannot edit graphs. Reassess the former global no-metrics prohibition to permit the separate Step 28 harness, without adding it to production config or success reports                                                                                                                                                                                                                                                                                                                         |
| 25    | Run the targeted six-curriculum structural/process matrix                                                            | Deterministic test outputs and validation evidence                                                                                                                       | Madhi: scope-only coordinate; Nigeria: tree baseline; Pratham: DAG/multiple grains; Ghana math: unresolved ancestry/code anomalies; Rwanda: noisy LC evidence cannot auto-publish; Ghana English: recurrence mapping. Validate policy, provenance, counts, identities, checkpoints, and artifacts without a semantic-quality pass/fail claim                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 26    | Implement bounded production LP concurrency and the approved supported-checkpoint-format boundary                    | kgs/lp_generation.py; kgs/lp_checkpoints.py; minimum schema/usage/receipt, artifact/reuse and pipeline-entry integration                                                 | Separate coding after approval of the applicable complete D13-C1/C2/C3 packet, including Section 3.3.3 format removal; independent offline testing and reviewer approval at an exact user-created candidate; fixed base is revalidated Step 25. Require complete journal-bearing evidence and pre-effect rejection of unsupported/previously upgraded legacy formats. Preserve current-format recovery, pending work, strict identity, sole writer, capacity 1/default 4, active-call drain, actual-transport/R1/R2 protections and accounting. Section 5.4 includes full-suite --run-slow. No prompt/model/candidate/semantic tuning.                                                                                                                                                                    |
| 27    | Run all six complete pipelines from source PDFs (historically Step 26 in the 2026-09-11 order; Step 27 before that)  | Six complete result directories and immutable manifests                                                                                                                  | Testing-primary after new Step 26 reviewer approval, using its exact SHA as review base; explicit live authorization. Preserve all AS/AS+LC/AS+LC+LP validation, D13 coverage, source/config/code hashes, provenance, warnings, counts, collisions, projections, checkpoints and reuse obligations. needs_review stays visible/nonpublishing/nonblocking. Step 28 offline development may overlap under D12.1.1; Step 27 reviewer approval remains required before live evaluation and final Step 28 completion.                                                                                                                                                                                                                                                                                          |
| 28    | Implement, independently test, and execute the generalized LP evaluation harness                                     | entries/evaluate_lps.py; evals/lp_eval/{schemas,sampling,prompts,judge,scoring}.py; separate lp_eval artifacts                                                           | After approval of D12.1.1, coding may start from the revalidated Step 25 base using a validated frozen Madhi snapshot before new Step 26 approval and while Step 27 continues. Support any N >= 1 curricula via D12.1.2 discovery and D12.1.5 format-specific input validation after Section 3.3.10 approval, one command, a frozen resume manifest, automatic bounded recovery under Section 3.3.11 and results/lp_evals/ output, without a hard-coded roster or count. Separate testing may validate offline after coding. Live evaluation requires Step 27 reviewer approval and explicit authorization; final completion still requires all six reviewed snapshots, all D12 components/reports/dispositions, actual material-content binding and independent reviewer approval. No production tuning. |
| 29    | Update user-facing pipeline, evaluation and artifact documentation                                                   | docs/pipeline/learning-progressions.md; architecture, index, artifacts, adding-curriculum, running/debugging docs                                                        | Coding then testing after Step 28 reviewer approval. Match actual Step 27 production and Step 28 evaluation artifacts; explain relatesTo symmetric lookup, direct edges/reachability, D10 warnings, D13 resume/failure, evaluation commands/config/conditions/denominators/cache/usage/failure/concern disposition, absent automatic semantic thresholds and human gold sets, and all D12/D14 LIMITs.                                                                                                                                                                                                                                                                                                                                                                                                     |
| 30    | Final comprehensive release review                                                                                   | Entire brief, configs, code, tests, docs, six production snapshots and separate evaluation evidence                                                                      | Read-only reviewer uses original pre-Step-1 baseline and exact Step 29 approved candidate. Verify every settled invariant, revalidated Step 24–25 chain, Step 26 concurrency testing/reviewer approval, Step 27 structural/provenance/count/collision/D13 evidence, Step 28 independently tested executed/reported evaluator with no unresolved execution failures and complete concern dispositions, Step 29 documentation, LIMITs and zero unresolved implementation decisions. No semantic correctness claim or automatic score threshold.                                                                                                                                                                                                                                                             |

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

The separate Step 28 evaluator uses the parallel entry-point and five-module layout specified in D12.1; it is not another stage inside `create_kgs`. Step 28 also owns the minimal `config.py` setting/registry wiring for `LLM_LP_EVAL_JUDGE_MODEL` and the corresponding assignment in `.template.env` and the local `.env`, as specified in D12-J. The production model and shared registry behavior remain unchanged.

## 5.2 First full-run sequence (Step 27)

After new Step 26 reviewer approval, record the effective production concurrency value, exact reviewed candidate, supported checkpoint format and actual material inputs for newly authorized Step 27 execution. Check the selected shutdown/reuse/accounting contract wherever exercised. Previously started runs retain their original bindings under Sections 3.3.2–3.3.3 and are not silently converted to new-candidate evidence. The reviewer preserves historical snapshots as prior evidence under their original bindings. After Section 3.3.10 approval, evaluator inputs must satisfy the actual supported historical/current contracts in D12.1.5. Reader support is not Step 27 snapshot approval, production checkpoint compatibility or live-run authorization.

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

Each successful run must also prove that the complete candidate/request population was materialized before external calls, every producer/checker/reconciled checkpoint is a valid aligned deterministic prefix, no D13 processing failure remains, and any included unresolved SFI/relationship carries the required warnings and provenance. New Step 26 production additionally proves complete supported journal-bearing checkpoint/attempt evidence under D13-C3. Retained historical snapshots remain prior evidence under their producing contracts and identities, without retrofitting journals or relabeling execution. Current evaluator inputs must independently satisfy D12.1.5; missing producing-code provenance remains optional under D12.1.3, without waiving required material identities.

The checks above define Step 27 production completion. D12 additionally requires Step 28 evaluator execution and separate reports for each of these exact six snapshots, with all required scheduled judgments accounted for, no unresolved execution failures, retained ambiguity/disagreement/denominators, usage evidence, and concern dispositions under the approved D12-A policy. Required reports do not impose an automatic semantic score threshold or human gold-set prerequisite. Step 29 must describe actual findings and all LIMITs; Step 30 verifies both evidence sets without claiming pedagogical correctness. Confirmed false positives/negatives follow D14 upstream remediation and rerun, never generated-graph patching.

## 5.4 New Step 26 independent testing and reviewer boundary

Coding owns only the approved bounded execution/format-removal surface and minimum schema/checkpoint/usage, artifact/reuse and pipeline-entry integration. The later format-removal scope has its required implementation approval recorded in Section 3.3.3. Separate testing independently derives an offline matrix with controllable barriers/fakes, not live APIs or timing-dependent sleeps. Current-format positive fixtures must carry complete journals and attempt evidence; remove obsolete old-format acceptance/upgrade expectations rather than preserving them as an oracle:

- Capacity 1, omitted/explicit default 4 and other positive capacities; reject booleans/null/zero/negatives/nonintegers. Cover empty/singleton/larger-than-capacity populations, retry-wait slots and independence from pair batch size. Prove bounded tasks/backlog/buffers even when an early request stalls.
- Complete materialized/reconciled population before the first call; invalid/stale input fails before dispatch. Scheduling changes no candidates, request/batch content, prompts or model selection.
- Concurrent independent requests; each checker waits for its validated draft. A failed producer cannot dispatch its dependent checker or bypass run-level failure.
- Reverse/random completion, a slow first request, different draft/verdict/final prefix lengths, and completed later requests beyond a failed gap. Successful JSONL stays contiguous; valid pending work survives.
- Interrupt current-format initial creation, journal writes, prefix promotion and receipt/transaction updates at every covered write/removal boundary. Recover complete valid transactions idempotently without duplicate calls/usage; reuse a saved producer with unfinished checker and completed suffix stages after filling the hole. Distinguish explained old/new byte mixtures from unexplained missing journals. Preserve the complete current-format recovery/integrity matrix.
- First/simultaneous exhausted failures and admission/dispatch races: finish only calls active at failure observation, with no subsequent producer/checker/retry API calls, including waiting workers/SDK retries. Save returned valid drafts, record malformed results without shutdown retries, and permit only local reconciliation/promotion. Preserve retry budgets, cancellation/unknown outcomes and zero-success while failures remain unresolved.
- Sole writer, competing processes, lost ownership, concurrent usage/failure aggregation and material changes during active calls. Reject corrupted/truncated/duplicate/misaligned/stale state.
- Unsupported-format rejection: replace the acceptance branch of `test_legacy_prefix_only_reuse_requires_exact_actual_material` and any other obsolete load/recovery/upgrade expectations with rejection before effects. Cover empty/partial/complete prefix-only stores, matching and mismatched material, missing/partial/unauthenticated journals, old transactions, mixed-format upgrade transactions, completed previously upgraded evidence with legacy coverage exemptions, and D13-C3-Z rejection of either compatibility field even when all values are zero. Reject malformed/unknown formats and non-native missing attempt coverage even with resealed hashes, final success reports or empty pending work.
- For every unsupported case, prove zero actual transport dispatch and unchanged artifact bytes/directory membership through generation/resume, recovery, overwrite/archive, finalization, standalone export, final reuse/projection and pipeline entry. Assert no AS/LC calls, no creation/replacement of `kg_run.json`, no receipt migration/default retrofit/hash rewrite, and no automatic regeneration. Use synthetic temporary evidence, not historical result edits.
- Effective-capacity identity: omitted/explicit 4 resolve identically for new config while raw config evidence remains accurate; capacity 1 remains supported. Missing captured capacity, changed effective capacity and any stale identity fail closed. The new-config default must not populate old receipts.
- Preserve and rerun R1/R2 and affected real-SDK offline transport tests: coordinated final-check/actual-transport eager entry, hook-return preemption, default/mounted/proxy routing, genuine asynchronous overlap/drain, private-client cleanup, no hidden SDK retry, unchanged model/settings and local-model branch. Distinguish known zero-dispatch cancellation/integrity rejection from dispatched unknown outcomes and retain all observed usage.
- With identical controlled producer/checker outputs, serial/concurrent schedules yield identical decisions, relationships, provenance linkage and stable ordering. Reconcile every observed attempt and count; do not claim live model determinism.
- Affected generation/reuse/orchestration regressions and six-curriculum structural/process paths through Step 25, AS/AS+LC compatibility, D12 release-policy separation and LLM_KG_MODEL. New coverage belongs to Step 26, not automatic reopening.

Full-suite validation must include `--run-slow` (for example, `make test TEST_ARGS=--run-slow` from `backend/` using the existing offline test environment). A default run that deselects slow integration/scale matrices is not full-suite evidence. Record commands, counts, skips/deselections and failures; preserve legitimate current-format tests and do not restore removed unused-wrapper tests. Rerun affected SDK, shutdown, accounting, generation/reuse/orchestration, six-curriculum and D12 regressions.

The reviewer requires the approved complete policy packet, including the resolved format boundary and Section 3.3.3 approval, coding handoff, separate deterministic test evidence including the full slow suite, exact user-created candidate SHA/tree, fixed Step 25 base and all intervening changes. No live throughput benchmark is a Step 26 prerequisite or authorized by specification approval. Confirmed earlier-owner defects require explicit reopening. Only Step 26 reviewer approval permits renumbered Step 27 progression; independent early Step 28 permission remains intact.

## 5.5 Step 28 compatibility extension — independent validation after approval and coding

D12.1.5 and Section 3.3.10 govern this evaluator-only extension; implementation approval is recorded on 2026-09-18. Independent testing owns reduced synthetic fixtures and derives the oracle from the supported historical/current schemas and authoritative bundle contracts, not the implementation's output. Do not edit real result directories to construct cases.

- Exercise all six Section 1.6 cells: original flat snake_case, converted flat camelCase and current wire, each with historical completed prefixes/capacity absence and current complete journals/exact recorded capacity. Cover one, multiple and more-than-six curricula, unfamiliar labels, empty relationship populations and valid recorded current capacities including 1 and 4. Do not infer the checkpoint family from projections or from presence of one journal.
- For converted projections, independently construct every explicit alias and the endpoint-key enum mapping; verify complete bundle-derived parity, unchanged nested metadata, null/absence, arrays, IDs, direction, counts and ordering. Reject recursive conversions, mixed node/edge families or rows, double spellings, alias collisions, unknown keys, wrong endpoint keys/values, omitted/extra records and nested boolean/number substitutions. Preserve strict raw wire checks.
- Historical evidence must prove the original full config, request/candidate identities/bytes, exact four-file receipt, all complete aligned stage prefixes/dependencies, outcomes, final artifacts and failure dispositions. Reject gaps, duplicates, reordering, truncation, missing/extra requests, stale hashes, invalid corrections, unresolved failures, stray journals, recorded capacity in the prefix contract and unfinished transactions. Missing historical attempt-level accounting stays unrecorded, never fabricated.
- Current journal evidence must retain exact six-file coverage, full attempt/accounting/dependency/retry validation and agreement of captured config/material/usage capacity. Reject missing/partial/unauthenticated journals, bool/null/string/invalid or mismatched capacity, missing capacity, legacy counters even zero, upgraded/unknown shapes, pending work and unfinished transactions. A failed current reader cannot downgrade to historical acceptance.
- Test every matrix cell through discovery, preparation, freezing, frozen loading, source revalidation, schedule/cache identity, resume and reports. Preserve source bytes and absence markers; mutate isolated source/frozen copies, interpretation versions, descriptors, captured config, request IDs, nested metadata and receipts separately. Material changes fail even if semantic normalization yields equal rows. Current reader/Python source edits alone do not block resume, and no new implementation digests are collected. Verify manifests/reports identify both formats and capacity absence accurately, distinct raw identities remain distinct, stale caches and unqualified old manifests fail without migration, and valid unchanged frozen schedules retain successes without new discovery or repeated calls.
- Verify recorded projection byte-hash mismatches fail even if bundle parity holds; absence of a historical projection-byte receipt is not a missing required artifact when that format never recorded it. Freeze actual converted bytes without claiming original snake_case raw hashes. Corrupt authoritative bundles, provenance, reports, AS+LC preservation and standalone relationships to prove converted projections cannot conceal inconsistent evidence.
- Reject invalid completed candidates before any evaluator evidence publication or transport dispatch. Assert unchanged bytes and directory membership for source and existing evaluation evidence, including no `kg_run.json`, receipt, config, journal, lock or projection repair. Preserve incomplete/active-run exclusion, zero valid-input failure, duplicate-framework conflicts, locks, path/symlink protections and detection of source changes during validation/freezing/use.
- Reassess affected K=21–F=25 export/reuse/orchestration, release-policy and structural regressions plus all retained evaluator grounding/citation/structured-output, deterministic scheduling and material binding. Prove production still rejects historical checkpoints before effects, including overwrite and completed-bundle reuse, and still exports current wire records. Preserve D12 evidence-condition separation, Git-independent operation, optional producing SHA and live/final gates. Include applicable existing slow integration checks; report exact commands, counts, skips/deselections and limitations.
- For Section 3.3.12, independently verify all ten retry waits, success on late
  attempts, exhaustion at attempt 11, explicit-rerun continuation at attempt 12,
  partial-cycle recovery, HTTP 400 retry eligibility and unchanged stop behavior
  for other permanent failures. Verify provider-error capture, active-key
  redaction, truncation, malformed/missing error bodies, provider request IDs,
  saved-setting-based usage reporting, validated-success reuse and stop-and-drain.
  Earlier three-attempt test results do not validate this amendment.

Coding supplies the independent-testing handoff in chat after implementation; this governance proposal is not a testing verdict or execution authorization.
