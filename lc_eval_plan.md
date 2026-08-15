# LC pipeline evaluation — rubric and sampling plan

Draft for review. Nothing implemented yet.

## Purpose

Measure how good the Learning Components pipeline's output is, using LLM-as-judge
with enough scaffolding that the resulting numbers are defensible.

**Measures:** decomposition quality (faithfulness, atomicity, coverage,
non-redundancy, granularity, well-formedness) and dedup quality (merge precision
and recall).

**Does not measure:** whether the Academic Standards layer (steps 1–10) correctly
extracted standards from source PDFs, or whether the LC selection policy picked the
right seed standards. Both are config/AS-pipeline concerns and are out of scope.

## Population

Computed from the four generated curricula on disk.

| Curriculum | LCs | Standards with LCs | 1 LC | 2 LCs | 3+ LCs | Merged dedup groups | Multi-parent LCs |
|---|---|---|---|---|---|---|---|
| Ghana | 245 | 159 | 72 | 54 | 33 | 12 | 36 |
| Nigeria | 297 | 267 | 176 | 67 | 24 | 79 | 80 |
| Rwanda | 796 | 469 | 207 | 167 | 95 | 56 | 65 |
| Tamil Nadu | 454 | 221 | 82 | 72 | 67 | 7 | 19 |
| **Total** | **1792** | **1116** | **537** | **360** | **219** | **154** | **200** |

## Two facts from the data that shape the design

**Self-reported confidence is not usable as a stratifier.** Across all 1792 LCs the
distribution is 0.6:1, 0.7:10, 0.8:94, 0.9:1398, 1.0:289 — 78% sit at exactly 0.9
and 94% at 0.9 or above. It carries almost no information. Stratify on
components-per-standard instead. Separately worth noting: any pipeline behaviour
keyed to a confidence threshold is effectively inert, and `lc_confidence_distribution`
in the summary is close to meaningless as a quality signal.

**The dedup gold set is small enough to evaluate exhaustively.** 154 merged groups
across all four curricula. Merge precision needs no sampling — judge all of them.

## Rubric

Two units of judgement: the individual component, and the component *set* for one
standard. Set-level criteria are where the real signal is; per-component criteria
are cheap and catch obvious breakage.

### Per component

**Faithfulness** — 3-point. Is every element of the component supported by the
standard's text (plus its ancestor path)?
- `grounded` — fully supported
- `extrapolated` — plausible but adds specificity not in the source (e.g. inventing
  a numeric bound, a method, or a manipulative)
- `unsupported` — contradicts the source or introduces unrelated content

**Atomicity** — binary. Does the component describe exactly one skill?
Fails when it contains a conjunction of distinct actions, or a compound of skill plus
context that a teacher would teach separately.

**Well-formedness** — binary. Is it a teachable skill rather than an activity,
example, or assessment task? The generation prompt explicitly forbids the latter,
so this is a direct test of prompt adherence.

### Per component set (one standard)

**Coverage** — 3-point. Do the components collectively capture the standard?
- `complete` — nothing meaningful in the standard is unrepresented
- `partial` — one or more distinct skills in the standard have no component
- `poor` — the set represents a small fraction of what the standard asks

This is the criterion the current artifacts cannot rule out and the main reason to
build this eval.

**Non-redundancy** — binary. Are the components mutually distinct, or do two
restate the same skill?

**Granularity** — 3-point (`too coarse` / `appropriate` / `too fine`), scored against
a single fixed written definition of the target grain, identical for all four
curricula:

> A learning component is one skill a teacher could teach and assess in a single
> task. If teaching it well would require two separate demonstrations, it is too
> coarse. If it cannot be assessed on its own without being combined with a
> neighbouring component, it is too fine.

The definition must not be relaxed per curriculum. Measured components per 10 words
of source text currently ranges from 1.10 (Ghana) to 2.46 (Tamil Nadu), and a judge
allowed to recalibrate its notion of "appropriate" per curriculum would score both as
appropriate and leave that 2× spread undetected.

### Dedup

**Merge precision** — binary per merged group. Are all members the same skill such
that collapsing them loses nothing? Run over all 154 groups.

Example from Rwanda that should pass:
```
canonical: add whole numbers not exceeding 50
members:   add whole numbers not exceeding 50
           add whole numbers where the sum and terms do not exceed 50
```

**Merge recall** — binary per pair, over a sample of pairs the blocking stage
nominated but the judge ruled DISTINCT. Should these have merged? This is where
false negatives hide and no one has looked at them.

### Anchoring examples

Correct decomposition looks like this (Rwanda):
```
STANDARD: Count, read, write, and order numbers less than or equal to 20.
   - Count numbers from 0 up to 20
   - Read numbers less than or equal to 20
   - Write numbers less than or equal to 20
   - Order numbers less than or equal to 20
```

The pattern under suspicion is the single-component restatement (Nigeria, 176 of 267
standards):
```
STANDARD: 4.  use the symbol <  or > to order fractions.
   - Order fractions using the symbols < or >
```
Whether that is correct behaviour on an already-atomic standard or a systematic
failure to decompose is precisely what Coverage is meant to settle.

## Negative controls

Injected into the judge stream indistinguishably from real items, 25 of each type.
With no human-labelled gold set these are the only ground truth available, so they
carry the validity argument for the whole eval rather than acting as a side check.

1. **Cross-strand swap** — replace a component with one from an unrelated strand.
   Faithfulness must fail.
2. **Truncation** — cut a component mid-clause. Well-formedness must fail.
3. **Invented specificity** — add a numeric bound or method absent from the standard.
   Faithfulness must return `extrapolated` or `unsupported`.
4. **Deliberate under-decomposition** — for a standard with 3+ real components, show
   only the first. Coverage must return `partial` or `poor`.
5. **Forced redundancy** — duplicate one component with reworded phrasing.
   Non-redundancy must fail.

Report detection rate per type. Anything below ~90% on types 1, 2 and 4 means the
rubric or the prompt needs work before the real run.

## Reliability protocol

No SME is available, so there is no human-labelled gold set. That is the binding
constraint on this design and it changes what the eval can claim (see *Framing* below).
Three compensating controls replace the human anchor.

- **Negative controls carry the validity argument.** With no human labels they are the
  only ground truth in the system, so they are scaled up from a sanity check to the
  primary evidence that the instrument works: 25 items per corruption type rather than
  12. If the judge cannot reliably detect known-bad items, no other number in the
  report is meaningful.
- **Self-consistency** — judge every item 3× with shuffled context ordering. Report
  per-criterion agreement. Items with unstable verdicts are flagged and excluded from
  headline figures rather than silently counted.
- **Cross-judge agreement** — re-judge a 150-item subset with a second, differently
  prompted judge. Agreement between independent judges is weaker evidence than
  human agreement, but it distinguishes "this criterion is stably measurable" from
  "this criterion is prompt-dependent noise."

**No prior human labelling exists.** Per-curriculum review sheets were generated
(`lc_*_review_sheet.md`, 2073 items) but none were filled in — 0 boxes ticked. No
manual audit of merges or decompositions has been done. There is therefore no
existing anchor to reuse, and negative controls are the sole ground truth in this
design.

The review sheets remain a ready-made instrument if human labelling ever becomes
possible. Labelling even 150 of those items would upgrade every claim in this eval
from model-assessed to validated, and is the single highest-value thing that could
be added later.

**Models.** Generator was `anthropic:claude-opus-4-8`. Judge is `claude-opus-5` —
a different model generation, so agreement is not self-consistency. Second judge for
the cross-judge subset uses the same model with an independently written prompt.

**Sampling parameters are unavailable on the Opus line.** Verified against the API on
2026-08-12: `temperature`, `top_p`, and `top_k` are all rejected with
`400 ... is deprecated for this model` on `claude-opus-5`, `claude-opus-4-8`, and
`claude-opus-4-7`. They are still accepted on `claude-sonnet-4-6` and
`claude-haiku-4-5`. This is a model-level deprecation and applies whether or not
extended thinking is enabled.

Consequence: a temperature or top_p sweep is not possible with this judge. Variance is
instead measured over two available axes, which separate two different questions a
temperature sweep would have conflated:

- **Within-condition variance** — repeated replicates at fixed settings. Answers
  whether the judge agrees with itself.
- **Between-condition variance** — `anthropic_effort` at `low`, `medium`, and `high`,
  the only sampling-adjacent knob left on the Opus line. Answers whether verdicts
  change when the judge reasons harder.

Run as a 3x3 grid on a 150-item subset rather than the full sample; nine full passes
would cost roughly $470 against roughly $35 for the subset, and the subset is
sufficient for a quotable standard deviation. Using `claude-sonnet-4-6` purely to
regain temperature control is not recommended — variance from a weaker judge measures
that judge, not the pipeline.

## Framing — what this eval can and cannot claim

Without human labels, results must be reported as model-assessed, not validated.

- **Supportable:** "An independent frontier model, verified to detect N% of injected
  defects, assessed X% of decompositions as complete (95% CI ...)."
- **Not supportable:** "X% of decompositions are correct."

The distinction goes in the report itself, not just the methodology section. The
negative-control detection rate should be quoted alongside every headline figure,
because it is what licenses the reader to take the figure seriously at all.

## Sampling

**Primary sample: 400 standards**, 100 per curriculum. Within each curriculum,
allocation deliberately oversamples the informative strata:

| Stratum | Share of population | Sampled per curriculum |
|---|---|---|
| 1 component | 48% | 25 |
| 2 components | 32% | 35 |
| 3+ components | 20% | 40 |

Design weights applied when computing population-level estimates. Per-curriculum
figures carry roughly ±10pp at 95%; per-stratum-within-curriculum figures are
indicative only (±17pp) and should be pooled across curricula for anything
load-bearing.

**Dedup precision:** all 154 merged groups, no sampling.

**Dedup recall:** 150 nominated-but-DISTINCT pairs, stratified by curriculum.

**Negative controls:** 125 items (5 types × 25).

**Cross-judge subset:** 150 items drawn from the primary sample, re-judged with an
independently written prompt.

Sampling is seeded and the seed is recorded in the output, so runs are reproducible
and comparable across pipeline changes.

## Baselines

Every criterion needs a trivial comparator, otherwise a high score is
indistinguishable from an easy task.

- **Coverage baseline** — score the standard's own text as if it were the component
  set. Establishes what "no decomposition at all" scores.
- **Atomicity baseline** — score the standard's raw text as a component.
- **Discrimination baseline** — for the optional A/B probe, a lexical-overlap picker.
  Given the restatement rate, this is expected to exceed 90%; if the LLM judge scores
  similarly, that probe is measuring string similarity and must be reported as such.

## Optional: A/B discrimination probe

Kept as a secondary check, not a headline metric. Given a standard and N candidate
components, can the judge identify ours? Two changes from the original idea:
distractors drawn from siblings under the same parent only (random distractors make
it trivial), and single-component standards reported separately since they are
near-restatements and inflate the score. Scored as set-membership, not exact match,
because 200 LCs legitimately have multiple parents.

## Outputs

- `lc_eval_items.jsonl` — one record per judged item: inputs, all 3 verdicts,
  stability flag, and whether it was a negative control.
- `lc_eval_report.json` — per-criterion, per-curriculum, per-stratum scores with
  bootstrap confidence intervals; reliability figures; baseline comparisons; negative
  control detection rates.
- `lc_eval_failures.md` — failure taxonomy with real examples bucketed by type. This
  is the artifact that drives pipeline fixes; the scores are what go in the writeup.

No single "pipeline score" is produced. Pooling these criteria into one number would
hide the only thing worth knowing.

## Implementation notes

- Lives outside the pipeline modules — it consumes artifacts, it does not produce KG.
- Cache judgements by item hash in JSONL, mirroring the existing resumable pattern,
  so reruns cost nothing and a partial run can be resumed.
- Reads only `learning_components.jsonl`, `lc_supports_edges.json`,
  `lc_eligible_sfis.json`, `lc_dedup_groups.json`, and the dedup verdicts file. All
  four curricula already have every one of these on disk.

## Settled

1. No SME, so no human gold set. Negative controls scaled up to carry the validity
   argument; results framed as model-assessed rather than validated.
2. Judge is `claude-opus-5` via the existing Anthropic key; generator was
   `claude-opus-4-8`.
3. Granularity scored against the fixed written definition above.

## Cost

Roughly 1,100 judged items before the 3× self-consistency pass, so on the order of
3,500 judge calls, plus 150 for the cross-judge subset. Cached by item hash, so a
rerun after a prompt change only re-judges what actually changed.
