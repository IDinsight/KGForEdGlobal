# Generating LearningComponents from finalized SFIs

This plan covers **steps 11–19** of the KG creation pipeline: turning finalized
`StandardsFrameworkItem`s (SFIs) into `LearningComponent`s (LCs) and the
`supports` edges that connect them, then merging LCs + edges with the step-10
Academic Standards KG into a single combined AS+LC bundle (19).

It assumes steps 1–10 are done, i.e. we already have:

```text
sfi_final_records.json               # deterministic, deduplicated SFIs (list[SFIFinalRecord])
has_child_edges_final.json           # resolved SFI hierarchy
academic_standards_kg_bundle.json    # step-10 compiled AS KG (gates 11,
                                     #   framework context 13, merge 18)
```

The LC step is the same shape as the SFI extraction and hasChild steps: Python
does the deterministic work, the LLM does only the bounded curriculum
interpretation, and every artifact is resumable.

## Big picture

An academic standard is written at the grain of a unit or a week. Instruction
happens at a finer grain: a single lesson, activity, or question. A
`LearningComponent` is that finer grain — one atomic, teachable skill carved out
of a standard.

So the LC phase asks the LLM a single, bounded question per standard (step 14):

> “Break this standard into atomic skills.”

Each atomic skill becomes one `LearningComponent`, and each LC points back to the
SFI it came from via a `supports` relationship:

```text
LearningComponent supports StandardsFrameworkItem
```

LCs are **derived from** SFIs. That is the whole reason an LC always carries a
`supports` edge back to a standard.

## Design decisions (settled)

These are the load-bearing choices. Change them here and the rest of the plan
follows.

### D1. Eligibility is profile-driven, with a structural leaf-node default

There is no universal *semantic* rule for which SFIs should become LCs; it
depends on each country's curriculum structure. So the `DocumentProfile`
(`kgs.lc` config) may declare it explicitly, and when it does, the allowlist
wins:

```text
lc_source_statement_types: optional; when provided, min_length >= 1
```

With an allowlist, the step filters `final_sfi_records` to SFIs whose
source-facing `statement_type` is in it. Examples:

```text
Ghana        → ["Indicator"]                 # not Content Standards (groupings)
Zambia       → ["Specific Competence"]
Senegal Math → ["Objectif spécifique"]
Senegal Read → ["Objectif spécifique"]
```

We match on `statement_type` (source-facing), not `normalized_statement_type`,
because the source type is precise per-country and is exactly what the profile
already speaks in.

**When the profile omits the allowlist, we do not fail — we default to leaf
SFIs.** A leaf is an SFI that never appears as a parent in
`has_child_edges_final.json`, further restricted to
`normalized_statement_type == "Standard"`. This follows the seed-selection
policy in `learning_components_generation.md` ("prefer teachable/leaf SFIs;
grouping nodes are context and roll-up anchors, not LC targets"), and it is
structural, not a semantic guess: in every curriculum we have curated so far
(Ghana Indicators, Zambia Specific Competences, Senegal objectifs spécifiques)
the explicit allowlist selects exactly the hierarchy's leaves.

The `normalized_statement_type == "Standard"` restriction is load-bearing: a
childless grouping node (an empty section, or one whose children failed to
extract) has no outgoing `hasChild` edge and would otherwise masquerade as a
leaf and become a semantically wrong LC source.

The default is visible, never silent:

- The eligibility report and summary record `lc_selection_mode`
  (`explicit_allowlist` | `leaf_default`), and defaulting adds a warning to the
  summary — the explicit allowlist remains the recommended per-country config.
- An *explicitly empty* allowlist still fails loudly (config validation):
  omitted means "use the leaf default"; empty means someone made a mistake.
- Caveat, accepted: leaf-ness inherits hierarchy quality. If hasChild
  resolution missed edges for an SFI, that SFI looks like a leaf. Systemic
  hierarchy problems already fail the run upstream; residual cases are exactly
  why the mode is recorded and warned.

Every SFI that is filtered out records a reason (`not_in_allowlist` in
allowlist mode; `not_a_leaf`, `grouping_node` in leaf-default mode;
`empty_text` in both) for the coverage report.

### D2. Decomposition is LLM-driven and structured — many LCs per SFI

One SFI may produce several LCs. We do not cap at one. The LLM is driven by a
structured prompt and validated structured output, exactly like SFI extraction.

We reuse the schemas that already exist for this:

```text
LCAtomicSkill        { description, rationale?, confidence, tags? }
                     # confidence: float 0..1 (NEW)
                     # tags: 2-5 short keywords in the source language (NEW) —
                     #   load-bearing from step 15 on: tag-overlap is the
                     #   semantic nomination signal in dedup blocking, and
                     #   the future pass-2 grouping reuses it. Emitted at 14
                     #   because adding them later would mean re-running
                     #   decomposition for every built curriculum.
LCResponseSFI        { sfi_uuid, skills: list[LCAtomicSkill] }
LCGenerationResponse { items: list[LCResponseSFI] }
```

`confidence` is the model's **decomposition** confidence for this skill — how
sure it is that the skill is directly implied by, and atomic to, the standard.
It is distinct from the parent SFI's *extraction* confidence (see D5).

Failure rule (loud, never fabricated): if the LLM returns empty/invalid for an
*eligible* SFI after retries, we emit **no** LC for it. We do **not** synthesize
a placeholder LC equal to the standard — that would inject a semantically-wrong
node (an LC must be finer-grained than its standard) and make `total_lcs` lie.
Instead the SFI is recorded in `lc_generation_failures.json` and surfaced in the
summary, mirroring how the hasChild step writes `has_child_unresolved_edges.json`
rather than inventing edges. The run continues; an optional `lc_max_failure_rate`
guard hard-fails only if too many eligible SFIs fail, so systemic problems still
stop the pipeline loudly.

Note: an LLM legitimately returning a *single* skill is fine and expected for a
standard that is already atomic — that is a real 1-skill decomposition, not a
failure. Only empty/invalid output is a failure.

### D3. LC identity is content-addressed within a configurable scope
### (revised 2026-08-02)

Each LC's deterministic ID derives from its normalized skill text plus a
scope segment controlled by the `lc_dedup_scope` config (default
`framework`):

```text
scope_segment (by lc_dedup_scope):
  framework    -> "framework"               # merge anywhere in the document
  top_ancestor -> {top_ancestor_case_uuid}  # only seeds sharing the first
                                            #   node on their hasChild path
  parent       -> {direct_parent_case_uuid} # only siblings
  none         -> {sfi_case_uuid}           # no cross-SFI merging
                                            #   (the original parent-owned IDs)

identity_key = lc:curriculum:{doc_key}:{scope_segment}:{normalized_text_hash}
identifier   = uuid5(LC_CANONICAL_NAMESPACE_UUID, identity_key)
```

Properties, all intended:

1. Reruns are idempotent and order-independent — keyed on
   `{normalized_text_hash}`, not output position; a skill repeated within one
   SFI collapses (identical key).
2. Exact-duplicate skills claimed by multiple SFIs inside the scope collapse
   into ONE LC by construction; each claiming SFI keeps its own `supports`
   edge (16). Dedup is identity, not a stage — deterministic, no LLM,
   nothing enters any model context.
3. Scope is expressed *structurally* (path-relative), never semantically
   ("grade"), because curricula disagree on what a grade is: Ghana/Nigeria/
   Rwanda have Grade top ancestors, Tamil Nadu's top ancestor is a Curricular
   Goal (grade lives in statement types), Senegal documents may be
   single-grade. Merged LC nodes carry NO grade — grade/scope is derivable
   from the standards they support, which is always consistent and
   multi-valued.

**Known effect of narrow scopes (documented 2026-08-04, from Tony
review):** any scope narrower than `framework` partitions the corpus into
independent buckets (e.g. `top_ancestor` on a grade-rooted hierarchy →
one bucket per grade), and dedup NEVER crosses a bucket boundary — by
design, not by accident. Consequence: a skill that recurs identically in
Grade 1, 2, and 3 mints as THREE separate LC nodes (their identity keys
differ in `{scope_segment}`, so they cannot collide even byte-identically),
and any graph traversal will encounter all three near-identical nodes side
by side. This is a deliberate trade, not a defect: each node's provenance,
supports edges, and section path are genuinely different, and a consumer
that wants them unified can still group them by `normalized_text_hash`
downstream. But it IS a visible multiplication of nodes — anyone switching
a curriculum off `framework` should expect it, and any future
graph-consumer design (search, tagging, prerequisite inference) should
decide explicitly whether cross-bucket same-text LCs are "one skill" for
its purposes. Under the default `framework` scope the effect does not
arise within a document; it DOES still arise across documents/curricula
(scope never crosses a doc_key), which is the intended jurisdiction
boundary.

History: this plan originally keyed identity on the seed SFI (parent-owned),
predicting cross-SFI byte-identical skill text "essentially never" occurs.
Measurement on the four decomposed fixtures (2026-08-02) refuted that: 4-10%
of skills are exact normalized duplicates — Ghana 24 groups (mostly
cross-grade spiral repeats, e.g. "identify the unknown in a problem" in
B4/B5/B6), Nigeria/Rwanda/Tamil Nadu mostly within-grade Content-vs-PO
complementary-view overlap. Every measured cross-grade group is safe to
merge: scope-bearing skills carry their scope in-text (different bounds →
different text → no merge), the rest are scope-free process/disposition
skills. `none` preserves the old behavior for any curriculum that
demonstrates a harmful merge.

### D4. Dedup happens at minting, by construction (revised 2026-08-02)

Cross-SFI exact duplicates merge because they mint the same `identifier`
(D3); there is no separate dedup stage and no LLM. On a multi-claim merge:
representative `description` = the most frequent surface form (tie-break:
lexicographically smallest); `tags` = sorted union; per-skill confidence
aggregates into `confidence_min`/`confidence_max` across claims; `metadata`
lists every contributing seed with per-claim confidence and request
provenance. Step 15's `lc_dedup_groups.json` records every multi-claim
group (exact + semantic, scope key, contributing SFIs, canonical surface
form) for audit. Normalization stays conservative (lowercase, whitespace-collapse,
trailing-period strip — no stemming, which would misfire on French/Wolof);
near-duplicates ("Compare fractions" vs "Comparing fractions") deliberately
do NOT merge here — that is pass-2 fuzzy grouping.

### D5. Each LC carries full provenance, inherited from its source SFI

An LC must be independently traceable to the PDF, exactly like an
`SFIFinalRecord` is — not require a join back through the SFI to find its page.
Because every LC is derived from one SFI (D3), it inherits that SFI's source
provenance verbatim, plus its own LC-specific fields. This mirrors the SFI
`metadata` convention (`sfi_finalization.py` writes `country`, `doc_key`,
`framework_title`, `pdf_name`, `primary_language`, `identity.namespace_uuid`) and
populates everything `EntityProvenance` needs for `role="learning_component"`
(segment ids, page indexes, section path, language).

Provenance is stored in `LearningComponent.metadata` (the schema explicitly
reserves `metadata` for "canonical node ids, doc_key references, provenance
pointers").

Why decompose from `sfi_final_records` rather than the step-10 bundle's
`items`? Not data availability — verified against Ghana run 2, the bundle
items carry the full provenance in their `metadata` dict, and items ↔ records
are 1:1 (same `case_identifier_uuid`s, counts validated by step 10). The
reason is type safety: `SFIFinalRecord` exposes provenance as typed, validated
fields, while the item buries it in `metadata: dict[str, Any]` — and 15
inherits ~10 of those fields verbatim, which should be checked attribute
access, not `.get()` chains. The bundle stays the source of truth where the
reference doc demands it: supports targets are final `case_identifier_uuid`s,
cross-validated against `bundle.items` in 18.

Inherited from the source SFI:

```text
source_sfi_uuids             # every claiming SFI's case_identifier_uuid
                             #   (one entry pre-merge; multi-entry when D4
                             #   merged claims — per-claim provenance beside it)
source_segment_ids           # DocumentIR segment ids
source_window_ids            # extraction window ids
source_page_indexes          # 0-based PDF page indexes
source_context_keys          # registry source-context keys
statement_type               # source-facing parent type (e.g. "Indicator")
normalized_statement_type     # "Standard" | "Standard Grouping" | "Other"
statement_code               # parent code, if any
source_sfi_confidence_min / source_sfi_confidence_max  # the parent SFI's *extraction* confidence
```

LC-specific fields:

```text
identity_key                 # the canonical string that minted this LC UUID
doc_key, country, framework_title, pdf_name, primary_language
identity.namespace_uuid      # LC_CANONICAL_NAMESPACE_UUID
confidence_min / confidence_max  # this LC's *decomposition* confidence (14);
                                 # min==max unless duplicate skill text collapsed (D4)
rationale                    # LCAtomicSkill.rationale, if provided
tags                         # LCAtomicSkill.tags — dormant until pass 2 (tag-overlap grouping)
llm_run_id, request_id       # which LLM call produced this skill
source_framework_uuid        # root StandardsFramework case UUID (from the step-10 bundle)
ancestor_path_uuids          # framework root → parent SFI, from hasChild (13 context)
generated_from_step10_bundle_fingerprint
                             # fingerprint of the AS bundle this LC was generated
                             # against — detects LCs stale vs a re-exported AS KG
```

Two confidences, kept separate on purpose: `source_sfi_confidence_*` is
inherited extraction confidence (was this a real standard), while
`confidence_*` is the model's decomposition confidence (is this a faithful
atomic skill of that standard). Neither substitutes for the other.

## Where it slots in

Four new modules, mirroring the sfi_* one-module-per-step layout (settled in
review: LC steps are first-class pipeline steps, so they get the same
granularity — and `build_kgs` wires them with one explicit call per module,
exactly like steps 3–10, rather than through a single mega-orchestrator):

- `skg/kgs/lc_selection.py` — steps 11–12: gates + LC-source SFI selection.
- `skg/kgs/lc_generation.py` — steps 13–14: request planning + LLM
  decomposition + resume (the plan-then-resolve shape of
  `sfi_relationships.py`; prompts/agents/validators additions go in the
  shared modules, like hasChild's).
- `skg/kgs/lc_dedup.py` — step 15: exact + semantic duplicate grouping,
  mirroring `sfi_dedup.py`.
- `skg/kgs/lc_finalization.py` — steps 16–18: mint LC nodes, emit supports
  edges, validate/summarize (identity minting mirrors `sfi_finalization.py`).
- `skg/kgs/lc_export.py` — step 19: AS+LC bundle merge, mirroring
  `sfi_export.py`.

```python
# 11-12.
lc_eligible_sfis, lc_eligibility_report = select_lc_source_sfis(
    academic_standards_bundle=final_bundle,   # 11 gates
    has_child_edges=has_child_edges,          # leaf computation (12)
    kg_dirs=kg_dirs,
    lc_config=kg_config.learning_components,
    sfi_final_records=sfi_final_records,
)

# 13-14.
lc_responses = decompose_lc_source_sfis(
    academic_standards_bundle=final_bundle,   # framework context (13)
    has_child_edges=has_child_edges,          # ancestor paths (13)
    kg_config=kg_run_inputs.kg_config,
    kg_dirs=kg_dirs,
    lc_eligible_sfis=lc_eligible_sfis,
    overwrite=config.overwrite,
    usage_tracker=usage_tracker,
)

# 15-17.
learning_components, supports_edges = mint_learning_components(
    academic_standards_bundle=final_bundle,   # framework uuid + fingerprint (D5)
    document_ir=kg_run_inputs.document_ir,
    kg_dirs=kg_dirs,
    lc_eligible_sfis=lc_eligible_sfis,
    lc_eligibility_report=lc_eligibility_report,
    lc_responses=lc_responses,
)

# 18.
as_lc_bundle = compile_as_lc_kg(
    academic_standards_bundle=final_bundle,   # step-10 output, read-only
    kg_dirs=kg_dirs,
    learning_components=learning_components,
    overwrite=config.overwrite,
    supports_edges=supports_edges,
)
```

Each call is independently resumable off its persisted artifacts.
`compile_as_lc_kg` mirrors `compile_academic_standards_kg` and returns the
merged bundle.

## Step overview

```text
step-10 AS KG bundle + final_sfi_records
  ↓  11  go/no-go gates                       Python; step-10 validation passed + unresolved-items policy
  ↓  12  select eligible LC-source SFIs       Python, deterministic; profile allowlist or leaf default
  ↓  13  plan + build LC generation requests  Python; one SFI per request (default) + context
  ↓  14  decompose SFIs into atomic skills    LLM, structured, resumable; failures recorded, not faked
  ↓  15  group duplicate skills               deterministic blocking + LLM pair judge; exact + semantic
  ↓  16  mint LearningComponent nodes         content-addressed UUIDv5 from canonical text within scope
  ↓  17  emit supports edges                  LC -> SFI, one edge per claiming SFI
  ↓  18  validate, persist, summarize         artifacts + PolicyCoverageReport LC stats
  ↓  19  merge into one AS+LC KG bundle       compose with step-10 AS bundle, cross-validate
```

## The pipeline in plain words (walkthrough for presenting)

A self-contained narrative of steps 11-19, follow-along-able without reading
code. One example threads through it: Ghana's cross-grade twins
*"Determine the HCF of any 2 or 3 numbers by prime factorisation"* (Basic 5)
and *"Determine the HCF of two or three numbers using prime factors"*
(Basic 6) — two indicators, in different grades, that should become **one**
LearningComponent supporting both.

**What comes in.** Steps 1-10 built the Academic Standards KG: SFI nodes
(strands, sub-strands, content standards, indicators…) connected by
hasChild edges. The LC phase adds a second layer: small, teachable,
atomic skills, each linked to every standard it serves.

**Step 11 — gates.** Pure checks, no artifacts changed: did step-10
validation pass, and does the run's unresolved-item count satisfy the
profile's policy? A run that is not LC-ready fails here, loudly, before
any tokens are spent.

**Step 12 — pick the seeds.** Deterministically select which SFIs deserve
decomposition. Default: structural leaves (no hasChild children). A
curriculum whose grain lives elsewhere overrides this with a
statement-type allowlist in its document profile (Rwanda lists its three
objective types) — config, not code. Both HCF indicators are leaves, so
both are seeds.

**Step 13 — build the requests.** For each seed, assemble one
deterministic request card: the seed's text and statement type, its full
ancestor path (framework → … → direct parent) for context, and a stable
request_id. No LLM. These cards are the join point every later step
resolves back to.

**Step 14 — decompose (LLM #1).** One request at a time, the model splits
each seed into 1-5 atomic skills — single action, single object — each
with 2-5 keyword tags and a confidence. The generic contract (what an
atomic skill is) is shared; everything curriculum-specific (split rules,
embedded-code handling, terminology) comes from `generation_instructions`
in the profile. Responses append to a jsonl keyed by request_id, so a
crashed run resumes where it stopped; individual seed failures are
recorded and the run continues unless the failure rate exceeds the
configured guard. Both HCF indicators are already atomic — each yields
one skill, nearly identically worded.

**Step 15 — find the duplicates.** The intricate one. Many seeds produce
the same skill (counting, measuring, and data-handling skills recur every
grade). Minting them all would fill the graph with near-copies. Step 15
decides, for every pair of generated skills, "same skill or not" —
without ever comparing all skills in one prompt. Internally:

1. *Collect units.* Join every response skill back to its step-13 request
   (for the ancestor path), normalize its text (lowercase, whitespace,
   trailing period — never stemming), and file it under
   (scope key, normalized text). Two claims with identical normalized text
   land in the same unit — **exact dedup falls out of the dict key**, free.
   Each unit accumulates: claim count, claiming SFIs, their direct
   parents, statement types, original spellings, tags.
2. *Build features.* Once per scope: token sets (Unicode, minus
   corpus-derived stopwords — words in >15% of texts), char-trigram sets,
   tag bags, and — only if the profile declares a language pack — a second
   token view with the pack's curated stopwords removed and affixes folded
   ("numbers"→"number"). Packs are pure config data; no language lives in
   code.
3. *Nominate pairs.* All-pairs sweep over the unique texts; a pair goes to
   the judge if ANY rule endorses it (rules are OR'd — high recall,
   because a false nomination costs one judge line, a missed one costs a
   redundant node). Three rings govern who meets whom:
   - **Neighborhood** (free hearing): both texts share a direct parent
     with <= 12 texts under it → judged automatically, zero resemblance
     needed. Catches same-topic duplicates with disjoint wording.
   - **Similarity reach** (earned hearing): anywhere inside scope, a pair
     is judged if it resembles — token Jaccard/containment/identity
     (core view, and pack view if declared), char-trigram Jaccard, or
     tag-bag Jaccard (the semantic channel: step-14's tags act as
     deterministic, language-faithful stand-ins for embeddings).
   - **Scope** (hard boundary, `lc_dedup_scope`): outside it — another
     framework — nothing is ever compared or merged.
   The HCF twins sit in different neighborhoods (different grades), but
   token + tag similarity nominate the pair.
4. *Judge (LLM #2).* Nominated pairs go to the model in batches of ~25,
   with a conservative rubric: same skill only if action, object,
   direction, and every qualifier match — "hours into minutes" vs
   "minutes into hours" is DISTINCT. A curriculum whose local conventions
   change what "same" means (does "whole numbers" include negatives?)
   declares them in `lc_dedup_instructions` in its profile, appended to
   the judge prompt like `generation_instructions` is at step 14.
   Verdicts append to a jsonl and replay on resume (a re-run with
   unchanged inputs costs zero tokens). The judge rules the HCF pair SAME.
5. *Cluster.* Union-find turns pairwise SAME verdicts into N-way groups
   (A=B, B=C → {A,B,C}), processing links in sorted order for
   determinism. A chaining guard refuses any merge that would put an
   explicitly-DISTINCT pair in one cluster; refusals are recorded as
   conflicts for review, resolving judge inconsistency toward
   *not merging* — a missed merge is a redundant node, a false merge is a
   wrong pedagogical claim.
6. *Elect the canonical.* Each cluster keeps one text: most claims, then
   shortest, then alphabetical. All variant spellings survive into
   metadata. The B5 HCF wording wins; the B6 wording becomes a variant.

**Step 16 — mint.** Deterministic. One LearningComponent per canonical
text, identity content-addressed
(`lc:curriculum:{doc_key}:{scope_segment}:{text_hash}`) — same skill,
same ID, on every re-run given the cached verdicts. Variants and
provenance ride along as metadata. The four fixtures' 2,073 claims
become 1,792 nodes.

**Step 17 — connect.** One `supports` edge per claiming SFI, carrying
that claim's own confidence. The HCF LearningComponent gets two edges —
one to the B5 indicator, one to the B6 indicator. This is where dedup
pays off: shared skills become visible bridges across grades.

**Step 18 — validate and summarize.** Coverage checks (every eligible
seed accounted for — decomposed, failed, or skipped, nothing silent),
dedup stats, low-confidence counts, LLM usage — persisted alongside the
artifacts.

**Step 19 — one bundle.** Merge the LC layer with the step-10 AS bundle
into a single cross-validated KG: standards, skills, and the edges
between them.

**The three design constants behind every step:** (1) anything
curriculum- or language-specific lives in the document profile, never in
code; (2) every step writes an inspectable, resumable artifact — LLM
outputs are cached and replayed, so re-runs are cheap and results
reproducible; (3) wherever the pipeline must choose under uncertainty,
it chooses the reversible error — an extra node over a wrong merge, a
recorded failure over a silent guess.

---

## Step 11 — Go/no-go gates

Deterministic, no LLM. Straight from `learning_components_generation.md`'s
pitfalls: **never generate LCs from an invalid or unresolved AS KG.** Both
gates read the step-10 bundle passed into `select_lc_source_sfis` and run before any
selection or LLM spend — 18's gate re-asserts them, but by then the tokens
are already spent, so the real gate is here.

**Gate 1 — validation.** Require, else fail loudly:

```text
bundle.validation_report.passed == true
bundle.validation_report.errors == []
```

This is what guarantees every future supports target is a real, reachable,
acyclic-hierarchy SFI (step-10 validation covers endpoint existence,
root-reachability, no cycles, no duplicate edges).

**Gate 2 — unresolved items: report-and-restrict, never fail.** Finalization
exclusions and unresolved root-fallback edges do not block the run: the
step-10 bundle already passed validation with them recorded (the project
accepted the export), and step-8-excluded SFIs never reach
`sfi_final_records` anyway. Default behavior:

- Generation proceeds over the **resolved subgraph**: 12 excludes any seed
  whose ancestor path passes through an unresolved root-fallback edge, with
  reason `unresolved_ancestor_path`. This mirrors the Ghana profile default
  ("keep generating from resolved seeds, never invent the missing organizer")
  and the reference doc's sanctioned alternative ("generate only for the
  resolved subgraph").
- The gap counts are logged as warnings and land in the eligibility report
  and summary — restricted loudly, never silently.

`lc_manual_review_overrides` therefore does not unlock the run — it **widens**
it: after a human actually reviews the unresolved items, setting
`allow_unresolved_ancestor_context: true` (with `reviewed_by`, `reviewed_at`,
`review_notes`) makes the gap subtrees eligible again, and the override block
is recorded verbatim in the summary. Rationale for this shape (settled in
review): a gate that must be overridden merely to run invites ceremonial fake
reviews; a gate that only widens scope keeps the audit trail honest.

Safety net (all modes): if selection yields zero eligible SFIs while any were
considered, 12 fails loudly after writing its artifacts — a fully-tainted or
misconfigured run must not continue silently.

## Step 12 — Select eligible LC-source SFIs

Deterministic, no LLM. Applies the profile allowlist, or the leaf-node default
when no allowlist is configured (D1).

**Input**

```text
final_sfi_records (list[SFIFinalRecord])
has_child_edges_final.json                              # for leaf computation
kg_config.learning_components.lc_source_statement_types  # optional
bundle.unresolved_items.relationship_unresolved_edges    # only under an
                                                         # 11 override
```

**Process**

```text
considered = all final_sfi_records

if lc_source_statement_types is provided:            # explicit_allowlist mode
    eligible = [s for s in considered
                if s.statement_type in lc_source_statement_types
                and s.description.strip()]
    exclusion reasons: "not_in_allowlist", "empty_text"
else:                                                 # leaf_default mode
    parents  = {source SFI uuid of every has_child edge}
    eligible = [s for s in considered
                if s.case_identifier_uuid not in parents      # leaf
                and s.normalized_statement_type == "Standard" # not a grouping
                and s.description.strip()]
    exclusion reasons: "not_a_leaf", "grouping_node", "empty_text"

excluded = considered - eligible, each tagged with its reason
```

Fail loudly if `lc_source_statement_types` is provided but empty (config
validation) — omitted means leaf default, empty means a config mistake.

In both modes, any seed whose hasChild ancestor path passes through an
unresolved root-fallback edge is excluded with reason
`unresolved_ancestor_path`, unless `lc_manual_review_overrides` sets
`allow_unresolved_ancestor_context: true` (11). Future profiles may also add
optional/review-gated seed tiers (e.g. Ghana's Content Standards decomposed
with child-Indicator context); v0 supports a single allowlist.

**Output**

```text
lc_eligible_sfis.json        # the eligible subset, with provenance carried through
lc_eligibility_report.json   # counts + per-reason exclusions, feeds 17:
                             #   lc_selection_mode  (explicit_allowlist | leaf_default)
                             #   total_lc_source_sfis_considered
                             #   total_lc_source_sfis_eligible
                             #   total_lc_source_sfis_excluded
                             #   total_lc_source_sfis_empty_text
                             #   lc_source_exclusion_reason_counts
```

## Step 13 — Plan and build LC generation requests

Deterministic, no LLM. Bounds the work into stable, resumable units, mirroring
the hasChild resolution-request pattern.

**Input**

```text
lc_eligible_sfis.json
has_child_edges_final.json                  # ancestor-path context
academic_standards_kg_bundle.json           # framework node → framework context
```

**Process**

- Chunk eligible SFIs into batches of `lc_request_batch_size`, **default 1** —
  one SFI per request, mirroring the hasChild step, whose code always sends a
  single child per request (`child_parent_sets=[parent_set]`) even though its
  schema allows a list. One-per-request scopes retries and resume to a single
  SFI and makes each request/response pair trivially debuggable; the cost
  (instructions repeated per call, more calls) is the same trade hasChild
  already makes, acceptable at per-curriculum scale.
- For each batch, build an `LCGenerationRequest` with a deterministic
  `request_id` (hash of the ordered SFI UUIDs; with batch size 1 this reduces
  to hasChild's hash-of-the-single-uuid pattern) and each SFI's source text +
  language tag.
- **Ancestor-path context** (recommended input object in
  `learning_components_generation.md`): for each eligible SFI, recover its
  full ancestor path (framework root → seed) from `has_child_edges_final.json`
  — per ancestor, `statement_type`, `description`, `case_identifier_uuid`. The
  path rides along in the request as disambiguation-only context (14 prompt
  contract) and is the authoritative source of grade/curriculum scope. Never
  derive scope from `grade_level` alone (lower-level SFIs in table-heavy
  curricula often carry none), and never from provenance labels or DocumentIR
  section paths — both are cumulative/noisy (reference-doc pitfalls).
- **Unresolved-path marker**: seeds admitted via the manual-review override
  keep whatever partial ancestor path truly exists (no invented organizers,
  no statement-code inference) and carry
  `ancestor_path_status = "unresolved_ancestor_path"`; the 14 prompt treats
  their SFI text as the sole scope authority (reference-doc rule). In default
  runs every path is complete — step 12 already excluded tainted seeds.
- **Framework context**: attach once per request from the step-10 bundle's
  framework node — `name`, `jurisdiction`, `academic_subject`, `in_language`.
- **Sibling context, optional and config-gated**
  (`lc_include_sibling_context`, default false): sibling SFIs under the same
  parent, included to help the model avoid near-duplicate skills across
  adjacent standards. Siblings are context only — the prompt forbids deriving
  skill content from them.
- Requests never carry `statement_code` as decomposition input: source PDFs
  can contain malformed or mismatched codes (Ghana caution in the reference
  doc). The SFI text plus ancestor context is the semantic source of truth;
  codes remain metadata.
- The request schema keeps its list shape regardless, so raising the batch
  size later is a config change, not a schema change.

**Output**

```text
lc_generation_requests.jsonl    # one LCGenerationRequest per line
```

## Step 14 — Decompose SFIs into atomic skills (LLM)

The only LLM step. Same agent/validator/resume stack as hasChild (D2).

**Input**

```text
lc_generation_requests.jsonl
kg_config.learning_components.generation_instructions
```

**Process**

- `prompts.py → build_lc_generation_prompt(...)`: combines
  `generation_instructions` with the batch. Each SFI shown with full source text
  and language. No translation — skills are produced in the SFI's source
  language (`fr`, `wo`, `mul`, `en`, …) per the v0 preserve-source rule.
- `agents.py → create_lc_generation_agent()`: structured output type
  `LCGenerationResponse`; validator raises `ModelRetry` on empty `items`, empty
  skill text, `sfi_uuid` not in the batch, `confidence` outside `[0, 1]`, skill
  text outside `[lc_min_skill_text_length, lc_max_skill_text_length]` when
  those are configured (both default off), or — when `lc_max_skills_per_sfi`
  is configured — an SFI whose skill count exceeds the cap (the retry message
  asks for a coarser-grain decomposition). With the cap unset, skill count is
  governed purely by the prompt contract below.
- `llm.py → generate_learning_components_for_request()`: one call per request;
  usage accumulates in a new `lc_generation` bucket on `KGUsageTracker`.
- Requests run **sequentially, in request order** — same loop shape as
  hasChild's `_run_resolution_requests`, one LLM call at a time, each response
  appended to `lc_generation_responses.jsonl` as it completes. Sequential is
  what makes prefix-resume valid (responses land in request order, so a stopped
  run leaves a clean validated prefix) and is easier to debug while the step is
  new. If throughput becomes a problem later, parallelize by keying resume on
  `request_id` instead of prefix — artifact formats don't change.
- Prompt contract:

```text
Each skill must be a single atomic, teachable skill — not an activity, resource,
assessment prompt, teacher guidance, a prerequisite skill the standard does not
state, or a restatement of the whole standard.
Each skill must be smaller and more teachable than the standard, directly
supported by the standard's text.
Ancestor and sibling context is for disambiguation only (e.g. whether
"capacité" means measurement or ability at this point in the curriculum); it
must never introduce skills, grades, or topics absent from the target standard.
Return skills in the same language as the source standard.
Produce exactly as many skills as the standard genuinely contains — no more, no
fewer. Do not pad a simple standard with extra skills, and do not compress a rich
standard to hit a smaller number. A standard that is already atomic yields one
skill.
Do not invent skills the standard does not imply.
For each skill, give a confidence in [0, 1]: how sure you are that the skill is
directly implied by, and a single atomic skill of, this standard. Lower it for
inferred, borderline, or possibly-redundant skills.
For each skill, also give 2-5 short keyword tags in the same language as the
standard, naming the concepts the skill involves.

(Only when lc_max_skills_per_sfi is configured:)
Return at most {N} skills per standard. If a standard genuinely contains more,
decompose at a coarser grain so the result fits within {N} — do not drop
content to meet the cap.
```

By default there is no hard cap on skills per standard — grain size and count
are governed by this prompt (and `generation_instructions`). An optional
runtime cap, `lc_max_skills_per_sfi` (the "maximum LearningComponents per SFI"
knob from `learning_components_generation.md`), adds a hard ceiling: it is
stated in the prompt and enforced by the validator via `ModelRetry`. An SFI
still over the cap after retries takes the normal D2 failure path
(`lc_generation_failures.json`, reason `over_max_skills`) — we never silently
truncate a skill list, because dropping skills would misrepresent the
decomposition while looking like a success.

There is deliberately **no minimum**: min = 1 is structural (an empty
decomposition is already a D2 failure), and a configured floor above 1 would
force the model to pad atomic standards with invented skills — exactly what the
prompt contract forbids. A rich standard can always be legitimately
re-decomposed coarser to fit a max; an atomic standard cannot be legitimately
split to reach a min.

- Failure handling: if a response is invalid/empty for an eligible SFI after
  retries, emit no LC and append the SFI to `lc_generation_failures.json`
  (`sfi_uuid`, `statement_type`, reason, last error). Never fabricate a
  placeholder LC. Track `lc_generation_failed_sfis_count`.
- Failure guard: after all requests, if
  `failed / eligible > lc_max_failure_rate`, raise loudly (systemic failure).
  Otherwise continue — one bad standard does not kill the run.
- Resume (`overwrite=False`): load the longest validated prefix of
  `lc_generation_responses.jsonl`; only call the LLM for remaining requests.

**Output**

```text
lc_generation_responses.jsonl   # one LCGenerationResponse per request (resume source)
lc_generation_failures.json     # eligible SFIs that produced no valid decomposition
```

## Step 15 — Group duplicate skills (exact + semantic)

New module `lc_dedup.py`, mirroring `sfi_dedup.py`: LLM-reviewed merge
groups feeding deterministic minting. Added 2026-08-02 (user decision):
dedup covers semantically identical skills, not just byte-identical ones —
"compare fractions" vs "comparing fractions" is one skill. Distinct from
pass-2 secondary supports, which asks whether an existing LC *also*
supports other standards; this step asks whether N minted-to-be nodes are
the same skill.

**Process**

1. **Exact grouping (deterministic):** normalize every skill text
   (lowercase, whitespace-collapse, trailing-period strip; no stemming —
   fr/wo later) and group within `lc_dedup_scope`.
2. **Candidate generation (deterministic blocking):** over the *unique*
   normalized texts in scope, nominate pairs via the UNION of two rule
   families (probed 2026-08-03: neither dominates — the language-agnostic
   family drops junk and needs no linguistic knowledge but loses real
   nominalization dups [6/175 judged sample]; the language-pack family
   recovers them; rules compose by OR at judge-call cost only):

   - **Language-independent core** (backbone; any curriculum, day one):
     Unicode tokenization, corpus-derived stopwords (document frequency
     > 15% within the run's own texts), token-set identity, token Jaccard
     >= 0.55, overlap coefficient >= 0.75 with >= 2 shared tokens,
     char-trigram Jaccard >= 0.6, tag-bag Jaccard >= 0.5.
   - **Per-language booster packs** (optional recall enhancers, never
     required): curated stopwords + affix folding declared ENTIRELY in the
     document profile (`lc_dedup_language_pack` — data, not code; the en
     pack is declared in each English curriculum's config; fr/wo packs get
     declared in Senegal's config only if its onboarding probe shows the
     core under-nominating). No language ever requires a code change.
   - **Neighborhood review sets** (similarity-blind completeness, added
     2026-08-03): ALL pairs of unique texts sharing a direct hasChild
     parent, when the neighborhood holds <= `neighborhood_all_pairs_max_size`
     texts (config in lc_dedup_blocking, default 12). Mirrors SFI dedup's
     bounded review sets (`max_dedup_review_set_candidates` precedent):
     within one Topic/Content Standard, duplication is likeliest and no
     wording similarity is required at all — this is the channel that
     catches same-meaning pairs with fully disjoint vocabulary AND
     divergent tags, which similarity gates cannot see. Measured at cap 12:
     +763/1082/45/592 pairs (~100 extra judge requests); covers 59/60
     Ghana and 42/48 Nigeria neighborhoods completely; oversized
     neighborhoods (Rwanda units up to 38, TN competencies up to 64) rely
     on the similarity gates.

   All folding/stopwording is for candidacy only, never for identity. The
   containment rule catches short subset variants ("compare fractions" ~
   "compare two fractions") that Jaccard punishes; **tag-bag overlap**
   (the skills' 2-5 keyword tags) is
   the semantic signal tokens cannot provide, catching synonym and
   nominalization variants ("appreciate managing time effectively" ~
   "develop a spirit of time management", token-J 0.14; "add 3-digit
   numbers" ~ "addition of 3-digit numbers"). Tags are compared as bags of
   folded tag-tokens (same folding as text tokens), never raw strings; the
   semantics live in the tags themselves — the model already abstracted
   each skill onto shared discrete labels at step 14 — so the comparison
   stays deterministic string overlap: a model-generated, inspectable,
   source-language substitute for embeddings (rejected for fr/wo
   reliability and determinism). Thresholds were set empirically by a
   margin probe (2026-08-02): the band just below an earlier, tighter gate
   set (J 0.75 / containment 0.9 / tag 0.7) still yielded ~3.5% genuine
   duplicates (7/200 judged sample of 3,752 band pairs ≈ ~130 missed) —
   so the gates were lowered to the probed band. At current thresholds:
   ~4,700 candidate pairs across the four fixtures (~190 judge requests) —
   the corpus never enters any context; only candidate pairs do. Blocking
   is recall-biased by design: a false nomination costs one judge line;
   precision is the judge's job (the judge rejected 96.5% of the probed
   band). Scaling note: the scoring stage is naive all-pairs O(n^2) over
   unique texts — correct and fast at the measured scale (<=1k texts/run,
   ~370k set comparisons in milliseconds), with a known escape hatch if a
   corpus ever reaches tens of thousands of skills: inverted-index
   prefiltering with prefix filtering (index each text's rarest tokens;
   provably exact for the Jaccard thresholds — no recall loss, unlike
   MinHash/LSH which would also break determinism), the standard
   entity-resolution similarity-join structure. LLM judging is never
   all-pairs (~1% of possible pairs nominated in practice). Lowering further requires a fresh margin probe; the yield
   gradient (16.4% in-gate -> 3.5% first margin) indicates fast-diminishing
   returns, with transitive capture and pass-2 grouping as residual nets.

   **Threshold portability across curricula**: the gates were calibrated on
   four English math corpora and are NOT assumed universal. Drift degrades
   gracefully (missed nominations -> redundant-but-valid nodes; never false
   merges — precision is threshold-independent) and is recoverable via
   re-run with overwrite. Guards: (a) the margin probe is part of each new
   curriculum's step-15 verification — sample the just-below band, judge
   it, recalibrate if yield is high; (b) stopwords/affix folding are
   language packs declared in the document profile (en declared in the
   English configs now; fr/wo at Senegal onboarding, which also brings the
   bilingual-text comparison question AND a tag-language rule for its
   generation_instructions —
   bilingual skills must tag in a consistent language or tag matching
   fails exactly where it is most needed; decided when its fixture
   exists); (c) thresholds live in
   `lc_dedup_blocking` config (probed defaults; repo numeric-knob
   precedent), so a curriculum whose probe demands different gates
   overrides them in its own config, no code change. Implementation requirement: tokenization must be
   Unicode-aware (the prototype's `[a-z0-9]+` is ASCII-only and would
   mangle fr/wo accented characters and drop non-Latin scripts entirely);
   tags remain source-language by design — the model that wrote the skill
   tags it in the same language, which is why tags outperform embeddings
   for fr/wo. **Validated live on Ghana (2026-08-02)**:
   110 pairs, 5 requests, 10/110 merged, all merges genuine (incl.
   cross-grade HCF twins and apply/use strategy variants), 4/4 sentinel
   pairs correct — notably merging a word-order conversion variant while
   rejecting its token-identical opposite-direction sibling in the same
   run; zero conflict-guard triggers. Ghana net: 285 skills -> 257 exact ->
   247 semantic-canonical LC nodes.
3. **LLM adjudication (bounded, cached, resumable):** batches of ~25 pairs
   per request, sequential with request_id-keyed resume + `overwrite`,
   usage bucket `lc_dedup` — the step-14 runner pattern. Conservative
   rubric: same skill ONLY if action, object, direction, and every
   scope/qualifier match. Direction flips ("hours into minutes" vs
   "minutes into hours"), bound changes, with/without qualifiers, and
   operation swaps are DISTINCT — measured candidates show token-identical
   opposites are common, which is exactly why no deterministic rule can
   adjudicate. A false merge is worse than a missed merge (missed = one
   extra node pass 2 can still group; false = wrong pedagogy claim).
   **Curriculum-local conventions** (Tony, 2026-08-04): what counts as
   "the same" can itself be curriculum-specific — one curriculum's "whole
   numbers" excludes negatives, another's includes them, so the identical
   pair of texts can be genuinely SAME in one framework and DISTINCT in
   another. `lc_dedup_instructions` in the document profile (the dedup
   analog of `generation_instructions`, default None) appends an
   authoritative curriculum-specific adjudication policy to the judge
   prompt; the generic conservative rubric alone runs when unset.
4. **Cluster + elect canonical (deterministic given verdicts):** union-find
   over merged verdicts; canonical text per cluster = most-claimed, then
   shortest, then lexicographically smallest. Shortest wins the length
   tie-break deliberately (confirmed with Tony 2026-08-04): the judge only
   ruled the pair SAME if every scope qualifier matches, so the shorter
   wording is the shared core every claimant supports, while a longer
   variant risks carrying phrasing bits not common to all members; the
   longer wordings are not lost — all variant surface forms are
   preserved in metadata. **LLM canonical naming: evaluated and rejected
   (user decision 2026-08-04).** A cached, resumable naming call
   (select/minimally-rephrase member wordings, token + collision guards)
   was built and run live on all four fixtures (~33k tokens): Nigeria
   gained verb-first phrasings for its nominal Content-row canonicals
   (38/79), but Rwanda (31/56) and TN (5/7) renames were mostly lateral
   verb swaps (tell/read, tiling/tessellation) — churn without
   significant improvement, at the cost of a second LLM stage and
   identity re-minting whenever the knob flips. Reverted; election is
   the design. Evidence preserved in lc_nigeria_naming_review.md; if
   canonical style ever matters downstream, restyle at export instead of
   changing minted identities. Pair verdicts are independent, so batching is pure
   packaging — cross-batch relatedness resolves here, transitively, in
   Python. Chaining guard: if a cluster would contain a pair explicitly
   judged DISTINCT, the contradicted merge link is dropped (links processed
   in sorted-pair order for determinism) and the conflict is recorded in
   `lc_dedup_groups.json` for review.

UUID stability: minting (16) keys identity on the canonical text, so node
UUIDs are deterministic *given the cached verdicts artifact* — the same
property SFI UUIDs already have with respect to step 7's LLM merge groups.

**Output**

```text
lc_dedup_candidate_pairs.jsonl  # deterministic blocking output
lc_dedup_verdicts.jsonl         # LLM pair adjudications (resume source)
lc_dedup_groups.json            # exact groups + semantic clusters + canonicals
```

## Step 16 — Mint LearningComponent nodes

Deterministic, no LLM. Content-addressed IDs within `lc_dedup_scope` (D3);
duplicates — exact and semantic (15) — collapse at minting (D4).

**Input**

```text
lc_generation_responses.jsonl
lc_eligible_sfis.json           # for attribution inheritance
lc_generation_requests.jsonl    # ancestor_path_uuids per SFI (D5)
academic_standards_kg_bundle.json  # source_framework_uuid + bundle fingerprint (D5)
document_ir.doc_key
```

**Process**

For each `(sfi, skill)` pair (failed SFIs contribute no pairs — see 14):

```text
text          = normalize_text(skill.description)
scope_segment = per lc_dedup_scope (D3): "framework" | top-ancestor UUID |
                direct-parent UUID | sfi.case_identifier_uuid
identity_key  = f"lc:curriculum:{doc_key}:{scope_segment}:{_hash_text(n_hex=32, value=text)}"
identifier    = uuid5(Settings.LC_CANONICAL_NAMESPACE_UUID, identity_key)
```

(Reuses `normalize_text`, `_hash_text`, `LC_CANONICAL_NAMESPACE_UUID` from SFI
finalization.) Assemble the `LearningComponent`, inheriting attribution from the
source SFI / KG metadata:

```text
identifier            = <minted UUID>
description           = skill.description
in_language           = sfi.in_language
academic_subject      = sfi.academic_subject
author                = sfi.author
attribution_statement = sfi.attribution_statement
license               = sfi.license
provider              = sfi.provider
metadata              = <full provenance per D5: inherited source provenance
                         + LC-specific fields>
```

Collapse nodes by `identifier`. Within one SFI this folds repeated skill
text; across SFIs (per `lc_dedup_scope`) it merges exact-duplicate claims
into one LC (D4 merge rules: representative surface form, union tags,
confidence_min/max, provenance listing every contributing seed). Attribution
fields are identical across seeds within one document by construction, so
inheritance is unambiguous. Every multi-claim group is recorded in step 15's
`lc_dedup_groups.json`.

**Output**

```text
learning_components.jsonl       # LC nodes
```

**Built + verified (2026-08-05)** in `lc_finalization.py`
(`mint_learning_components`, wired as `# 16.`): all four fixtures
reconcile exactly with step 15 — 245/297/796/454 nodes (= 1,792), claim
totals match (2,073), multi-claim node counts equal the dedup group
counts (36/85/72/22), reruns byte-identical. Display description = the
canonical text's most frequent original-casing surface form (settled in
review 2026-08-05); identity hashes the normalized canonical, so the two
always agree. Deviations from the D5 sketch, all because merged nodes
have per-claim context: `statement_type/code` became top-level
`statement_types` (sorted union) with per-claim values inside `claims`;
`ancestor_path_uuids` lives per-claim (claimants' paths differ);
`llm_run_id` and `rationale` dropped — neither exists on the step-14
response schema. Attribution inheritance is asserted, not assumed: a
divergence across claiming SFIs raises.

## Step 17 — Emit supports edges

Deterministic, no LLM. One edge per (LC, claiming SFI) pair — a merged LC
gets one `supports` edge to every SFI whose decomposition claimed it (D3/D4).

**Input**

```text
learning_components.jsonl       # incl. source_sfi_uuid in metadata
lc_eligible_sfis.json           # for target case_identifier_uuid
```

**Process**

The edge gets its own deterministic identity, mirroring how `SFIHasChildEdge`
carries a `relationship_identity_key` + minted UUID:

```text
rel_identity_key = f"lc:curriculum:{doc_key}:supports:{lc.identifier}:{sfi.case_identifier_uuid}"
rel_identifier   = uuid5(LC_CANONICAL_NAMESPACE_UUID, rel_identity_key)

Relationship(
    identifier        = rel_identifier,
    relationship_type = "supports",
    source_entity     = "LearningComponent",
    source_identifier = lc.identifier,
    target_entity     = "StandardsFrameworkItem",
    target_case_identifier_uuid = sfi.case_identifier_uuid,
    metadata = { relationship_identity_key: rel_identity_key,
                 support_role: "primary",
                 source_framework_uuid,           # root framework case UUID
                 target_sfi_statement_type,       # e.g. "Objectif spécifique"
                 support_confidence,              # = the LC's decomposition confidence
                 doc_key, llm_run_id, request_id, rationale },
)
```

(The reference doc also suggests denormalizing the target SFI description and
ancestor path descriptions onto the edge; we deliberately don't — both are one
join away via the target UUID in the merged bundle, and duplicating them
invites drift.)

Satisfies the existing `supports` validator (LearningComponent →
StandardsFrameworkItem, source identifier + target CASE UUID). Because both
endpoints are in `rel_identity_key`, multi-parent LCs mint one stable,
deterministic edge UUID per claiming SFI with no scheme change;
`support_confidence` on each edge is that claim's own confidence (per-claim,
not the node aggregate).

`support_role: "primary"` marks every edge from this step as a decomposition
edge — each claiming SFI's decomposition independently produced the skill,
so every claim edge is primary. Validation invariant: exactly one primary
edge per (LC, supported SFI) pair, and every LC has at least one primary
edge. The future pass 2 adds `support_role: "secondary"` edges to the same
nodes under the conservative evidence rules in
`learning_components_generation.md`; secondaries mint distinct deterministic
UUIDs with this exact scheme, no changes needed.

**Output**

```text
lc_supports_edges.json          # supports relationships
```

**Built + verified (2026-08-05)** in `lc_finalization.py`
(`build_lc_supports_edges`, wired as `# 17.`): 285/396/895/478 edges
(= 2,054) across the four fixtures — exactly claims (2,073) minus the 19
within-SFI merged claims; every LC has >= 1 edge, zero duplicate
(LC, SFI) pairs, reruns byte-identical. Two settled details: the edge
identity key follows the hasChild scheme
(`lc:curriculum:{doc_key}:relationship:supports:{lc}:{sfi}` — the plan's
sketch lacked the `relationship:` segment; repo consistency won), and
when ONE SFI claims an LC through several merged wordings the single
edge carries the MINIMUM of its claim confidences (user decision
2026-08-05: never overstate support; applied 7/9/3/0 times on
nigeria/rwanda/tamil_nadu/ghana). The Relationship schema's supports
validator runs on construction, and blank descriptions auto-fill with
the schema's canonical sentence.

## Step 18 — Validate, persist, summarize

Deterministic, no LLM.

**Input**

```text
learning_components.jsonl, lc_supports_edges.json, lc_eligibility_report.json,
lc_generation_failures.json
```

**Process — validation**

```text
Every LearningComponent has a deterministic UUIDv5 identifier.
Every LearningComponent has exactly one primary supports edge (this step emits
  only primaries; pass-2 secondaries are validated by pass 2, incl. no cycles).
Every supports edge targets a real, final SFI.
Every supports edge is LearningComponent -> StandardsFrameworkItem.
No duplicate supports relationship identifiers.
No duplicate (source LC, target SFI) supports pairs.
Coverage reconciles: every eligible SFI is accounted for exactly once as either
  produced-LCs, empty_text, or a decomposition failure — the three are disjoint
  and sum to total_lc_source_sfis_eligible. No eligible SFI silently vanishes,
  and none is covered by a fabricated LC.
```

Division of labor (settled in review): anything the LLM can fix is validated in
14, per response, via `ModelRetry` — empty items, empty skill text, unknown
`sfi_uuid`, out-of-range confidence, over-cap counts. Catching those here
instead would mean spending the whole run's LLM calls before discovering a bad
response, with no retry path left. So 17 does **not** re-check response-level
content; it asserts only run-level invariants over the deterministic 15/16
outputs (identity, edge shape, coverage reconciliation) that no single response
can establish. Persisted responses are re-validated by the resume loader when
read back from `lc_generation_responses.jsonl`, so on-disk artifacts cannot
bypass the 14 checks either.

**Process — summary.** Write a `LCGenerationSummary` (parallel to
`SFIFinalSummary` / `SFIHasChildResolutionSummary`). (Correction
2026-08-05: the "PolicyCoverageReport" this section originally referenced
does not exist in the codebase — the summary itself is the coverage
artifact.)

`LCGenerationSummary` fields:

```text
# eligibility (from 12)
lc_selection_mode                   # explicit_allowlist | leaf_default (D1)
total_lc_source_sfis_considered, total_lc_source_sfis_eligible,
total_lc_source_sfis_excluded, total_lc_source_sfis_empty_text,
lc_source_exclusion_reason_counts
# generation
total_lcs
lc_count_by_language                # parallels candidate_count_by_language
lc_count_by_source_statement_type   # parallels SFI count-by-type
lc_splits_distribution              # { "1": 380, "2": 95, ... }
lc_max_splits_observed
lc_confidence_distribution          # histogram of decomposition confidence
lc_generation_failed_sfis_count     # eligible SFIs with no valid decomposition
# LLM accounting (parallels has_child summary)
llm_request_count, llm_response_count
# gates (from 11)
manual_review_overrides             # verbatim override block, null unless configured
# non-fatal issues
warnings                            # e.g. ["sfi <uuid>: unusually high split count (18)",
                                    #       "no lc_source_statement_types configured; used leaf default", ...]
```

Most map directly onto `PolicyCoverageReport` LC fields already defined
(`total_lc_source_sfis_*`, `lc_source_exclusion_reason_counts`, `total_lcs`,
`lc_splits_distribution`, `lc_max_splits_observed`).

Deliberate divergence: the existing schema has `lc_fallback_sfis_count` and an
`lc_split_policy` field, which show the original author intended a 1-to-1
fallback and a policy switch. We rejected both — there is only one policy (LLM
decomposition) and no fabricated LCs — so `split_policy` never appears on any
record or in the summary. We leave `lc_fallback_sfis_count` unused (or repurpose
it to carry `lc_generation_failed_sfis_count`) and leave `lc_split_policy` unset.

**Process — entity provenance.** Emit one `EntityProvenance` record per LC,
consumed by the 18 merge (the SFI step carries the same fields on
`SFIFinalRecord` and the step-10 export builds `entity_provenance.json` from
them):

```text
EntityProvenance(
    entity_identifier = lc.identifier,
    entity_type       = "LearningComponent",
    role              = "learning_component",
    source_segment_ids, page_indices, section_path_text,  # inherited per D5
    text = TextUnit(skill.description, in_language),
)
```

**Output**

```text
lc_generation_summary.json
lc_entity_provenance.json        # EntityProvenance entries, consumed by 19
```

**Built + verified (2026-08-05)** in `lc_finalization.py`
(`summarize_learning_components`, wired as `# 18.`): all invariants pass
on the four fixtures (identifier recomputation from identity keys, edge
endpoints real, no duplicate identifiers or pairs, every LC >= 1 primary
edge, eligible = claimed ∪ failed disjointly — zero failures on all
four); `LCGenerationSummary` carries eligibility + generation + dedup +
edge counters, decile confidence histogram, splits distribution, and
step-12 warnings; both artifacts byte-identical across reruns. Coverage
note: the plan's "produced-LCs, empty_text, or failure" trichotomy
simplified to claimed/failed — empty_text is a step-12 EXCLUSION reason
(such SFIs are never eligible), so it partitions the excluded set, not
the eligible one; the summary reports it via
total_lc_source_sfis_empty_text.

## Step 19 — Merge into a single AS+LC KG bundle

Deterministic, no LLM. New module `skg/kgs/lc_export.py`, mirroring
`sfi_export.py` / `compile_academic_standards_kg` (step 10).

**Compose, never mutate.** The step-10 `academic_standards_kg_bundle.json`
stays untouched — it remains the standards-only KG for consumers that don't
want LCs, and step 10 stays idempotent. 18 reads both finished artifact sets
and writes one new, self-contained artifact.

**Input**

```text
academic_standards_kg_bundle.json   # step 10 — gate: validation_report.passed
                                    #   == true and errors == [], else fail loudly
                                    #   (re-asserts 11; kept because 18 can
                                    #   run standalone from persisted artifacts)
learning_components.jsonl           # 15
lc_supports_edges.json              # 17
lc_generation_summary.json          # 18
lc_generation_failures.json         # 14
lc_entity_provenance.json           # 18
```

**Process.** Assemble a new `AcademicStandardsLCKGBundle`:

```text
framework                  # from AS bundle, verbatim
items                      # from AS bundle, verbatim
relationships_has_child    # from AS bundle, verbatim
learning_components        # 16 nodes
relationships_supports     # 16 edges
entity_provenance          # AS provenance ∪ LC provenance; a key collision is an error
unresolved                 # {academic_standards, learning_components} — mirrored
                           #   shape (each side: an exclusion-reason dict + a list
                           #   of the actual unresolved/failed records), so the
                           #   merged artifact self-describes its gaps at the same
                           #   depth on both sides (revised 2026-08-07)
summary                    # AS export summary + LCGenerationSummary + combined counts
validation_report          # merged-graph validation below, + input_fingerprints
```

**Process — validation** (cross-artifact; the merged graph is checked as a
whole, which is the true home of checks 17 could only approximate against
`lc_eligible_sfis.json`):

```text
AS bundle validation passed and error-free (the gate above).
Every supports source identifier exists in learning_components.
Every supports target case_identifier_uuid exists in the AS bundle's items.
Every LC has >= 1 primary supports edge (exactly one per (LC, SFI) pair).
No identifier collisions across items, learning_components, and relationships.
Every LC has an entity_provenance entry; the provenance merge added no collisions.
Counts align: len(learning_components) == summary.total_lcs and
  len(relationships_supports) == sum of per-LC claiming SFIs
  (corrected 2026-08-06: the original "len(learning_components) ==
  len(relationships_supports)" was a relic of the pre-dedup 1-to-1
  design — multi-parent LCs have several edges, 1,792 nodes vs 2,054
  edges on the four fixtures).
```

Resume (`overwrite=False`): reuse an existing `as_lc_kg_bundle.json` only when
its `validation_report.input_fingerprints` match freshly computed fingerprints
of all six inputs — the same `_fingerprint_jsonable` staleness pattern
`sfi_export.py` uses for the AS bundle.

**Output**

```text
as_lc_kg_bundle.json       # the single AS+LC artifact; what downstream consumers
                           #   (and the future pass 2) load
as_lc_nodes.jsonl          # flat projection: every node (framework, SFIs, LCs),
                           #   one line each with an entity_type discriminator
as_lc_relationships.jsonl  # flat projection: every hasChild + supports edge
```

The flat projections were added 2026-08-06 (Tony): they are the shapes
CZI consumes — `as_lc_nodes.jsonl` for a curriculum (e.g. Ghana
mathematics) is the direct input to cross-evaluation against US-side
mathematics LCs. Pure projections of the bundle, written from the same
in-memory objects in the same pass, so they cannot drift from it.

**Built + verified (2026-08-06)** in `lc_export.py` (`compile_as_lc_kg`,
wired as `# 19.`): all four fixtures pass merged-graph validation —
Ghana 553 nodes / 592 relationships, Nigeria 652/750, Rwanda 1417/1515,
Tamil Nadu 711/734; flat projection line counts equal the bundle counts;
fingerprint-based reuse verified (unchanged inputs → existing bundle
returned as-is, projections rewritten); byte-identical reruns. The LC
pipeline (steps 11-19) is complete.

---

## Config additions (`kgs.lc`)

Extend `_CreateKGLearningComponentsConfig`:

```text
generation_instructions: str          # exists today
lc_source_statement_types: list[str] | None = None
                                      # NEW, optional                    (12, D1)
                                      #   provided → explicit allowlist (min_length >= 1;
                                      #   empty list fails validation)
                                      #   omitted  → leaf default: leaf SFIs with
                                      #   normalized_statement_type == "Standard"
lc_request_batch_size: int = 1        # NEW, optional batching knob       (13)
                                      #   default 1 = one SFI per request, hasChild
                                      #   parity; raise later for throughput without
                                      #   any schema change
lc_max_skills_per_sfi: int | None = None
                                      # NEW, optional hard ceiling        (14)
                                      #   unset → prompt alone governs count;
                                      #   set → cap stated in prompt, validator
                                      #   retries over-cap SFIs; still over after
                                      #   retries → D2 failure path, never
                                      #   truncation. No min: 1 is structural.
lc_max_failure_rate: float = 0.05     # NEW, hard-fail guard              (14, D2)
                                      #   max fraction of eligible SFIs allowed to
                                      #   fail decomposition before the run raises;
                                      #   isolated failures continue + report,
                                      #   set 1.0 to disable the guard
lc_manual_review_overrides: dict | None = None
                                      # NEW, review record                (11)
                                      #   unresolved items never block the run —
                                      #   resolved subgraph + loud report is the
                                      #   default; set allow_unresolved_ancestor_
                                      #   context=true (with reviewed_by/at/notes)
                                      #   to also include gap subtrees; recorded
                                      #   verbatim in the summary
lc_include_sibling_context: bool = False
                                      # NEW, prompt context knob          (13)
                                      #   sibling SFIs under the same parent as
                                      #   disambiguation-only context
lc_min_skill_text_length: int | None = None
lc_max_skill_text_length: int | None = None
                                      # NEW, validator knobs, default off (14)
lc_semantic_dedup: bool = true
                                      # NEW, gate for step 15's LLM pair
                                      #   adjudication; false = exact-only dedup
lc_dedup_batch_size: int = 25         # NEW, candidate pairs per judge request (15)
lc_dedup_blocking: nested sub-config  # NEW (15) — nomination thresholds, defaults =
                                      #   the probed values (repo precedent:
                                      #   max_has_child_parent_candidates etc.):
                                      #   token_jaccard_threshold      = 0.55
                                      #   containment_threshold        = 0.75
                                      #   containment_min_shared_tokens = 2
                                      #   trigram_jaccard_threshold    = 0.6
                                      #   tag_jaccard_threshold        = 0.5
                                      #   corpus_stopword_df           = 0.15
                                      #   Field docs state the rule: change these
                                      #   only on margin-probe evidence; each
                                      #   curriculum's onboarding probe validates
                                      #   or overrides them per curriculum.
lc_dedup_instructions: str | None = None
                                      # NEW (15, Tony 2026-08-04) — optional
                                      #   curriculum-specific adjudication policy
                                      #   appended to the judge prompt (local
                                      #   conventions, e.g. whether "whole
                                      #   numbers" includes negatives); the dedup
                                      #   analog of generation_instructions.
                                      #   None = generic conservative rubric only.
lc_dedup_language_pack: nested sub-config | None = None
                                      # NEW (15) — curated stopwords + affix
                                      #   folding (min_fold_length, stopwords,
                                      #   strip_prefixes, strip_suffixes)
                                      #   declared ENTIRELY in the document
                                      #   profile — data, not code; None =
                                      #   language-independent core rules only.
lc_dedup_scope: "framework" | "top_ancestor" | "parent" | "none" = "framework"
                                      # NEW, merge scope for exact-duplicate
                                      #   skills (15, D3): framework-wide by
                                      #   default; structural narrowing (shared
                                      #   top-of-path node / shared direct
                                      #   parent); "none" = parent-owned IDs,
                                      #   no cross-SFI merging. Lands in schema
                                      #   + all configs when 15 is built.
                                      #   KNOWN EFFECT of narrow scopes: see D3.
```

`generation_instructions` is the per-country pedagogical guidance for how to
decompose (grain size, what counts as a skill vs an activity, language policy).

## Schema work

Mostly reuse; little new.

```text
Extend: LCAtomicSkill  → add confidence: float (ge=0, le=1)   (LLM output, 14)
                     → add tags: list[str] = []              (LLM output, 14;
                       dormant until pass 2)
Reuse:  LCResponseSFI, LCGenerationResponse               (LLM output, 14)
Reuse:  LearningComponent, Relationship                      (KG nodes/edges, 16/17)
Reuse:  EntityProvenance                                     (provenance export, 18)
New:    LCGenerationRequest                                  (13, built)
        { request_id, framework_context: LCFrameworkContext,
          sfis: [LCRequestSFI] }
        LCRequestSFI = { final_sfi_uuid, description, language,
          statement_type, ancestor_path: [LCContextSFI],
          ancestor_path_status, siblings: [LCContextSFI] }
        # ancestor_path_status marks override-admitted seeds whose path
        #   crosses an unresolved fallback edge (reference-doc rule); path
        #   is then incomplete and must not be used to derive scope (14)
        # LCContextSFI = { case_identifier_uuid, description,
        #   statement_type } — shared ancestor/sibling context shape
New:    LCGenerationSummary  { ...stat fields per 18... }  (18)
New:    AcademicStandardsLCKGBundle                          (19)
        # mirrors AcademicStandardsKGBundle + learning_components,
        # relationships_supports, merged provenance/unresolved/summary
```

## Artifacts produced

```text
lc_eligible_sfis.json            # 12
lc_eligibility_report.json       # 12
lc_generation_requests.jsonl     # 13
lc_generation_responses.jsonl    # 14 (resume source)
lc_generation_failures.json      # 14  (eligible SFIs with no valid decomposition)
lc_dedup_candidate_pairs.jsonl   # 15  (deterministic blocking)
lc_dedup_verdicts.jsonl          # 15  (LLM pair adjudications, resume source)
lc_dedup_groups.json             # 15  (exact + semantic clusters, canonicals)
learning_components.jsonl        # 16  (LearningComponent + full provenance metadata, D5)
lc_supports_edges.json           # 17
lc_generation_summary.json       # 18
lc_entity_provenance.json        # 17  (EntityProvenance, consumed by 18)
as_lc_kg_bundle.json             # 19  (single merged AS+LC KG artifact)
```

LLM token usage accrues to a new `lc_generation` bucket on `KGUsageTracker`
(14), so it lands in `kg_run.json` under `extra.usage` alongside the SFI
buckets — no separate run-metadata wiring needed.

## One-line flow

```text
step-10 AS KG bundle + final SFIs
  ↓ 11  gates: step-10 validation passed; unresolved gaps → resolved subgraph + loud report
  ↓ 12  profile allowlist (or leaf-node default) picks LC-source SFIs
  ↓ 13  deterministic requests: SFI text + ancestor path + framework context
  ↓ 14  LLM decomposes each SFI into atomic skills (1..N); failures recorded, not faked
  ↓ 15  SFI-keyed UUIDv5 per (SFI, skill text), doc_key-scoped → mint LCs
  ↓ 16  emit supports edges LC -> SFI, 1-to-1
  ↓ 17  validate + persist + LC coverage stats
  ↓ 18  compose step-10 AS bundle + LC artifacts → as_lc_kg_bundle.json
```

---

## Future pass 2 — multi-parent supports and LC–LC edges (designed for, not built)

Settled in review (Jul 2): this step is **pass 1** of a two-pass model. Pass 1
creates each LC against its own decomposition parent (everything above). Pass 2
— a separate future step, not built now — revisits the finished LC set and may
add:

1. **Secondary `supports` edges**: an LC judged to also support another SFI.
   Multiple parents are allowed; cycles are not.
2. **LC–LC edges**: relationships between LCs, scoped to *local* context —
   neighboring leaves in the same hasChild neighborhood, not distant nodes.

Pass-2 shape (direction agreed, details open):

- **Candidate grouping by tag/word overlap, not embeddings.** Each LC already
  carries LLM-emitted `tags` (14). Overlap on tags groups similar LCs and
  their parents; each group goes to an LLM judge for multi-parent / relatedness
  decisions. Embeddings were rejected: not robust for fr/Wolof text, while tag
  overlap works in any language since all LCs in a curriculum share the source
  language.
- **Locality via the hierarchy.** Candidate neighborhoods come from traversing
  `has_child_edges_final.json` around each LC's `source_sfi_uuid` (sibling and
  nearby leaves) — cheap to compute, and keeps the judge from pairing distant,
  coincidentally-similar standards.
- **Conservative evidence rules** per `learning_components_generation.md`:
  always keep the primary edge; secondaries only when the LC text is explicitly
  supported by the other SFI; prefer nearby teachable SFIs; never grouping
  nodes (roll-up is inferable through `hasChild` ancestry); never from wording
  or code similarity alone.
- **Pass-2 validation**: exactly one primary per LC, secondaries pass the
  evidence rules, and the combined LC/SFI graph stays acyclic.

What pass 1 already guarantees so pass 2 needs no rework (the architecture
asks of this design):

```text
tags on every LC          # 14 — the only artifact expensive to add later
                          #   (would require re-running decomposition per curriculum)
stable node identity      # D3 — pass 2 adds edges to existing nodes, never re-mints
support_role on edges     # 16 — "primary" now; "secondary" distinguishable later
two-endpoint edge keys    # 16 — secondaries mint deterministic UUIDs, same scheme
locality is derivable     # D5 source_sfi_uuid + persisted hasChild edges
```

Nothing else about pass 2 is decided; it gets its own plan (and its own config,
e.g. `allow_secondary_supports`) when we build it.
