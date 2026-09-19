# Evaluating Learning Progressions

The LP evaluator reads completed, validated production snapshots and writes separate
assessment artifacts. It does not change graphs, production configuration, or
production success reports. It supports any positive number of compatible curricula,
with framework-contained populations derived from each snapshot's configuration.

The reporting path also creates an evaluation workbook and interactive visual report.

## Command and discovery

Use the project's configured environment described in
[local setup](../development/local-setup.md). Settings come from the environment
managed by `direnv`; this entry point does not load an evaluator-specific dotenv file.
Run from `backend/`:

```bash
python src/kgfeg/entries/evaluate_lps.py --help

# Live-capable execution example, only after the required execution authorization:
python src/kgfeg/entries/evaluate_lps.py ../results/kg_for_ed
```

The Typer command has one required argument, `RESULTS_ROOT`, and no subcommands. By
default it performs preflight, frozen preparation, judging/resume, and reporting in
sequence. Calling it on real inputs can make external model calls. For presentation
only, `--render-report` treats `RESULTS_ROOT` as an existing saved report directory and
bypasses evaluation preparation and execution. Local preparation is available to
developers through `prepare_evaluation()` without constructing a provider client.

Discovery recursively finds directories named exactly `kgs`, including the supplied
root itself. Parent depth and folder names do not determine curriculum identity.
Discovery records unfinished/active-status and failed runs as excluded, avoids cycles
and duplicate physical aliases, excludes evaluator outputs, and does not follow
descendant directory symlinks outside the starting root. Distinct directories claiming
the same framework/document are conflicts; it does not choose the latest copy.

Every completed candidate must validate. A success marker or final graph alone is
insufficient: required source identity, captured configuration, original requests,
judgments, provenance, reports, bundles, projections, and actual hashes must reconcile.
Unreadable/contradictory completion evidence, invalid completed inputs, or no completed
inputs fails preparation before judge dispatch. A completed-looking snapshot whose
generation lock is actively held also fails snapshot validation; a retained unlocked
lock file is acceptable.

## Supported snapshot formats

Projection and checkpoint formats are independent. Every combination below is
supported only when all source, configuration, request, checkpoint, claim, provenance,
report and bundle evidence validates.

| Projection pair               | Historical complete prefix, capacity absent | Current complete journals, exact recorded capacity |
|-------------------------------|---------------------------------------------|----------------------------------------------------|
| Original flat snake_case      | Supported                                   | Supported                                          |
| Converted flat camelCase      | Supported                                   | Supported                                          |
| Current Learning Commons wire | Supported                                   | Supported                                          |

The original flat files contain full internal records and nested metadata. Converted
flat files retain that structure and use a finite set of top-level aliases, plus
`case_identifier_uuid` → `caseIdentifierUUID` for endpoint-key values. Nested metadata
is unchanged. These are distinct from wire records with `type`, `properties` and
delivery identifiers. The exact mapping is governed by engineering-brief D12.1.5.

The evaluator reconstructs each complete expected projection from the authenticated
combined bundle and requires exactly one format to match, including JSON types, nulls,
ordering, metadata and endpoints. It also verifies every recorded artifact hash and
captures the actual projection bytes. Conversion never excuses a mismatched recorded
hash. Unknown aliases, mixed formats or dropped metadata fail validation.

Historical checkpoints require all three complete ordered stage files and the failure
file under their original four-file receipt. Both journals and captured capacity must
be absent. Current checkpoints require the six-file receipt, complete pending/usage
journals and matching positive recorded capacity in configuration, execution material
and usage. Neither format permits an unfinished transaction. A missing current journal
cannot select the historical reader.

Historical configuration is validated against its exact schema without adding today's
default capacity. Original requests are reconstructed and byte-compared; their recorded
identities are never rewritten. No reader repairs, migrates or reseals inputs. This
read-only evaluator compatibility does not enable historical production checkpoint
resume or reuse.

## Frozen selection and resume

Before calls, the evaluator freezes discovery inventory, exact selected paths and
material hashes, input copies, effective settings, populations, samples, evidence
views, and the complete request/condition/replicate schedule. Resume revalidates both
frozen copies and selected source material. Missing, changed, or incompatible inputs
fail; newly completed curricula cannot enter an existing invocation. Frozen inputs
and resume must also satisfy the recorded projection, checkpoint and configuration
contracts. Frozen inputs containing unsupported formats and incompatible schedules are
rejected without upgrade, hash rewriting, or changes to source or prior evaluation
evidence. Each frozen snapshot requires its projection/checkpoint interpretation,
`lp_snapshot_v2` contract version and actual reader/dependency fingerprints. Together
with raw-byte hashes these bind the manifest, schedule and cache. Earlier manifests
lacking this descriptor are rejected without rewriting. Reader changes invalidate
frozen interpretation; report-only scoring changes retain the existing scoring
exception. JSON/Markdown reports identify each format and show historical concurrency
capacity as “not recorded.”

Repeating the same command automatically selects the newest matching incomplete
invocation by canonical starting directory and effective sampling, repetition and judge
settings. Ordering uses the immutable UTC creation time, then the schedule identifier
for ties. Matching stores are validated before selection; a locked, stale, corrupt or
unsupported store blocks recovery with a specific error, never silent fresh discovery.
If every match is complete, the newest validated complete invocation is returned
without repeating calls. The command prints the selected manifest, reused judgments,
remaining work and execution progress. A command lock covers selection through
reporting, and the invocation writer lock protects the attempt ledger.

New evaluations use the `lp_evaluation_store_v2` format with durable execution
boundaries. Earlier evaluator stores cannot be resumed or imported. Remove unwanted old
evaluator results separately before starting fresh; the command never deletes them.
Historical/current production snapshot support is unchanged. To retain overrides
without repeating them, use the exact invocation manifest printed by the command:

```bash
python src/kgfeg/entries/evaluate_lps.py ../results/kg_for_ed \
  --resume-manifest ../results/lp_evals/invocations/<schedule-hash>/manifest.json
```

`--resume-manifest` targets the invocation's `manifest.json`, not the report's
`lp_eval_manifest.json`. Omitted controls retain frozen values; explicit mismatches or
a changed model are rejected. Without explicit resume, supply the original overrides to
match the original settings rather than the defaults.

`--new-invocation` requests discovery again and preserves prior evidence. It is
mutually exclusive with `--resume-manifest`. Identical material can resolve to the
existing content-addressed invocation; this flag is not an exhausted-attempt reset or
permission to repeat completed calls. Fully cached execution avoids provider
construction.

## Sampling and repetition controls

All controls below are optional and apply per selected curriculum unless stated
otherwise. Effective values and explicit overrides are recorded with the frozen
schedule and reports. All counts are positive integers except the additional-replicate
count, which may be zero if base plus additional is at least two; the seed is an
integer.

| CLI option                           | Default  | Meaning                                                       |
|--------------------------------------|----------|---------------------------------------------------------------|
| `--production-pairs-per-outcome`     | 15       | Uniform sample within each of four production outcomes        |
| `--production-examples-per-tag`      | 2        | Minimum per diagnostic tag, crediting already selected pairs  |
| `--independent-uniform-pairs`        | 36       | Uniform sample from admissible upstream pairs                 |
| `--independent-pairs-per-tag`        | 3        | Independent sample per upstream tag                           |
| `--sampling-seed`                    | 20260911 | Deterministic selection and ordering seed                     |
| `--base-blind-replicates`            | 1        | Base judgments per real pair in each selected component       |
| `--critique-replicates`              | 1        | Separate operative-rationale critiques per production pair    |
| `--diagnostic-pairs-per-cohort`      | 12       | Pairs from each production/independent cohort for diagnostics |
| `--additional-diagnostic-replicates` | 2        | Extra identical-presentation base judgments                   |
| `--variant-replicates`               | 1        | Judgments per diagnostic pair for each of five variants       |
| `--synthetic-cases-per-family`       | 5        | Cases in each of five synthetic control families              |
| `--synthetic-control-replicates`     | 3        | Judgments per synthetic case                                  |
| `--lexical-baseline-top-k`           | 10       | Lexical ranking cutoff, capped by sampled availability        |

For example, an authorized invocation can override the two uniform sample sizes:

```bash
python src/kgfeg/entries/evaluate_lps.py ../results/kg_for_ed \
  --production-pairs-per-outcome 10 --independent-uniform-pairs 24
```

The defaults select up to 60 production base pairs, then at most 24 additional
diagnostic pairs, and 36 independent uniform plus 36 tagged draws before deduplication.
Selection is seeded and without replacement within each cell, with canonical UUID
ordering, retained selection routes, and no quota reallocation. Empty or exhausted
strata are reported as shortfalls, never filled with fabricated examples.

The 12 production tags cover checker correction; same/different/missing rank; equal
normalized text or unequal text with token Jaccard at least 0.5; missing, shared,
nonshared, or broadly reused LC evidence; unresolved ancestry; and evidence truncation.
Broad reuse means an LC supports at least 10 eligible SFIs. Upstream tags substitute
multi-parent DAG context for correction and reconstructed truncation for production
truncation. Text normalization uses Unicode NFKC, casefolding, and whitespace collapse;
tokens are Unicode alphanumeric runs without stopword removal. These are diagnostic
sampling features, not truth labels.

Identical-presentation repeats and five separate variants are also added: swapped
endpoints, reversed evidence lists, expanded evidence, LC removal, and
trustworthy-hierarchy removal. Expanded evidence doubles positive production
count/text/depth limits with stable ordering and explicit omissions. Removal also
removes derived facts and references, while preserving uncertainty warnings. Every
replicate is retained; a majority does not erase disagreement. Required components and
conditions cannot be disabled.

## Model and bounded execution

`LLM_LP_EVAL_JUDGE_MODEL` defaults to `anthropic:claude-opus-5` and can be overridden
through the normal environment. Omission uses the default; explicitly blank, malformed,
unsupported, or unavailable configuration fails without model fallback. This is a
configured identifier, not a claim that a live provider has been verified available.

There are no aggregate dollar, token, or attempt budgets and no required cost-estimate
approval gate. The explicit schedule, bounded evidence, and finite retries bound the
work. Usage reports include attempts, retries, valid/failed outcomes, curriculum,
component, condition, model, available input/output/reasoning/cache tokens, and
available cost. Unknown usage or pricing is not zero. Missing pricing alone does not
block completion.

Execution allows up to **4 concurrent requests**, with a **180-second timeout per
attempt** and **at most two retries after the initial attempt**, waiting **5 then 20
seconds**. Timeouts, HTTP 429/5xx responses, and invalid structured output are
retryable within that allowance. Input, authentication, configuration, and stale-cache
failures are not retryable. SDK and output-validation retries share the same
three-attempt maximum within each retry cycle.

After a terminal failure, already active calls finish without starting further calls.
Validated successes remain reusable without repeating their calls. Each deliberate
rerun may retry any previously failed request, including a transport or authentication
failure. Previously exhausted cycles receive one new bounded cycle. A partially
consumed cycle resumes with its remaining allowance; exhausting that cycle stops the
execution without opening another. The waits are 5 and 20 seconds within each cycle.
Lifetime attempt numbers never reset: after three failures, the next rerun starts at
lifetime attempt 4, cycle attempt 1. Execution boundaries, prior failures, later
dispositions and all usage remain in the append-only ledger and reports. Progress logs
identify lifetime and cycle attempts.

A historical failure never permanently blocks a deliberate rerun. Unfinished starts
receive an appended interrupted record with unknown remote outcome and usage before the
new execution begins. Rerunning authorizes a possible duplicate provider call/cost; it
does not erase the earlier attempt or assert that the provider cancelled it. Transport,
interruption and authentication/configuration failures still stop the current
execution; they do not cause automatic retry loops. Current configuration, inputs,
cache integrity and exclusive ownership must pass validation before new dispatch. The
CLI and Markdown report show the reason when execution is blocked.

## What the judge assesses

**Production-pair assessment** samples published `buildsTowards`, published
`relatesTo`, final `no_relation`, and final `needs_review` pairs. Blind classification
hides production conclusions, rationale, confidence, publication status, and nomination
recommendations. It retains permitted factual nomination evidence, references, limits,
and warnings. Its outcomes are a permitted relation/direction, `no_relation`, or
evaluator `ambiguous`.

Only after the blind response is validated and frozen does a fresh call critique the
operative production rationale. That call receives the original bounded production
request/policy and rationale, including the corrected rationale when applicable. It
does not receive the classifier's answer and cannot revise it. Grounding is reported
separately as `grounded`, `partially_grounded`, `unsupported`, or `ambiguous`.

Critiques may cite the original bounded request and the historical producer/checker
policy text actually shown to the critic. Policy text can support a claim about that
policy; it cannot by itself establish factual relationship support between standards.
The rationale being assessed cannot serve as evidence for itself. Citations must
exactly match the supplied permitted references, without appended descriptions or
quotations. The policy references `/original_producer_system_message` and
`/original_checker_system_message` are permitted only when their corresponding text is
present and nonblank. This does not expand the blind classifier's evidence view.

**Independent upstream-pair assessment** selects from admissible eligible AS+LC pairs
without reading nomination or production outcomes. Every selected pair uses the same
`reconstructed_bounded_upstream` construction rules, whether nominated or not. Evidence
is frozen before joining production outcomes for reporting. A pair selected in both
components retains both judgments and identities.

**Evaluator checks** use constructed developmental extension, nondirectional coherence,
unrelated concepts, insufficient/contradictory evidence, and invented-rationale
controls. Control expectations are hidden and kept separate from real-pair results.
Constant `no_relation` and lexical top-k baselines add no model calls. An unasserted
real pair is never assumed to be a known negative.

Original production evidence, the blind view, reconstructed upstream evidence, expanded
evidence, and evidence-removal conditions are distinct. Additional upstream evidence
cannot rescue an unsupported original-production rationale during grounding assessment.

## Cache, artifacts, and interpretation

Cache identity includes actual snapshot/evidence/request material, endpoints,
component, condition/view, rendered prompt, response schema, judge settings, replicate
and presentation order, and the original request/policy/rationale for critiques. Reuse
accepts only fully validated successes; stale, truncated, duplicate, extra, or
mismatched records fail closed.

Outputs are rooted at repository-root `results/lp_evals/`, independently of the
supplied production results root:

```text
results/lp_evals/
  inputs/<input-hash>/manifest.json     # frozen selection and input copies
  invocations/<schedule-hash>/
    manifest.json                       # --resume-manifest target
    schedule.json.gz
    attempts.sqlite3                    # durable attempt/judgment ledger
    writer.lock
    execution_identity/                 # evidence kind + schedule hash, no Git identity
    reports/<report-manifest-hash>/
      lp_eval_manifest.json
      lp_eval_inputs.json
      lp_eval_population.json
      lp_eval_sample.jsonl
      lp_eval_requests.jsonl
      lp_eval_judgments.jsonl
      lp_eval_failures.jsonl
      lp_eval_usage.json
      lp_eval_report.json
      lp_eval_report.md
    dispositions/<disposition-hash>/lp_eval_dispositions.json
```

Reports and presentations are immutable generations identified by their output bytes.
Scoring changes may generate new reports from compatible judgments while preserving old
evidence. Production graphs and `kg_run.json` remain untouched.

Read raw numerators/denominators, sample membership, shortfalls, all replicates,
disagreement, ambiguity, and omitted evidence alongside rates. Nomination coverage uses
valid judge-positive independently sampled pairs under the common reconstructed
condition: nominated positives divided by all positives in that
condition/cohort/replicate. Matching publication and nominated-but-rejected/unresolved
categories use the same denominator. Diagnostic oversamples are not population
estimates, and zero denominators are explicitly unavailable, not 0% or 100%. Agreement
and judge support are not semantic precision/recall or curriculum-wide recall.

Execution failures, valid semantic ambiguity, and quality concerns are separate.
Missing judgments, exhausted failures, or invalid reports prevent execution completion.
Disagreement, unsupported rationales, missed sampled positives, control performance,
and presentation/evidence instability become concern groups without an automatic score
gate.
