# Next steps

Open items from the LC evaluation run (2026-08-12, judge `anthropic:claude-opus-5`,
artifacts under `results/lc_eval/`).

## 1. LC generation prompt: unsatisfiable contract for atomic SFIs

`backend/src/skg/kgs/prompts.py`, `build_lc_generation_prompt()`, Task boundary block.

Three rules cannot all hold when an SFI is already a single skill:

| Line | Rule | Conflict |
| --- | --- | --- |
| 960 | skill must not be "a restatement of the whole standard" | a one-to-one decomposition *is* one |
| 961 | skill must be "smaller and more teachable than the standard" | an atomic standard has no smaller form |
| 963 | when the SFI is atomic, "return exactly one cleanly restated skill" | mandates what 960 and 961 forbid |

`"cleanly restated"` is also undefined, while every surrounding constraint penalises
changing the text (961 "directly supported by", 966 "never add materials, tools, or
methods", 981 "invent none", 983 confidence keyed to direct support). Copying verbatim
is the risk-free way to satisfy 963, so the prompt makes copy-through the rational
strategy.

### Observed effect

Across all 537 single-component standards:

| Outcome | Count | Share |
| --- | --- | --- |
| Verbatim copy of the standard | 93 | 17.3% |
| Substring / superstring of it | 121 | 22.5% |
| Genuinely reworded | 323 | 60.1% |

By curriculum, verbatim copies as a share of that curriculum's single-component
standards: Rwanda 66/207 (32%), Tamil Nadu 17/82 (21%), Ghana 8/72 (11%),
Nigeria 2/176 (1%). Nigeria's `generation_instructions` are the most prescriptive
about splitting and it has almost no copy-through, so curriculum config already
modulates this.

### Proposed edit

```diff
-- Each skill must be a single atomic, teachable skill — not an activity, resource, assessment prompt, teacher guidance, a prerequisite skill the standard does not state, or a restatement of the whole standard.
-- Each skill must be smaller and more teachable than the standard and directly supported by the standard's own text.
+- Each skill must be a single atomic, teachable skill — not an activity, resource, assessment prompt, teacher guidance, or a prerequisite skill the standard does not state.
+- Each skill must be directly supported by the standard's own text. When a standard contains several skills, each must be smaller and more teachable than the standard as a whole.
 - Prefer concise teachable components that could support lesson planning or learning resource tagging.
-- A single skill is a valid decomposition: when an SFI is already atomic, return exactly one cleanly restated skill. Never force a split and never pad the skill count.
+- A single skill is a valid decomposition: when an SFI is already atomic, return exactly one skill. Never force a split and never pad the skill count.
+- Restate every skill as a teachable skill statement: lead with the skill's verb and drop stems ("Pupils should be able to:"), codes, list markers, assessment framing, and source formatting. Restating is required even for a one-to-one decomposition — never return the standard's text unchanged. Restating changes framing only; it must not add, drop, or narrow content.
```

Dropping the 960 clause loses nothing: its intent is already covered by the rewritten
961 and by 964 ("never additionally emit a summary skill that restates the entire SFI").
The final sentence of the new rule is the guardrail — without it the edit reopens the
invention problem the rest of the prompt exists to prevent.

Left alone for now: line 983 defines confidence as confidence that the skill is
"directly supported by the SFI text", which quietly rewards copying. Revisit only if
copy-through persists after the above.

### Priority

Low urgency. Copy-through produces low-value output, not wrong output — those LCs are
faithful (0.994), attach correctly (edge eval confirmed 99.8% / 97.6%), and mostly pass
atomicity (0.966). 93 verbatim LCs is 5.2% of all 1,792. **Do not re-run generation for
this alone.** Batch it into the next full re-run, which is needed anyway (see §3).

## 2. Eval granularity criterion is mis-specified

`backend/src/skg/evals/lc_eval/prompts.py`, `_GRANULARITY_DEFINITION`.

Two defects, both eval-side:

- It instructs the judge to "apply this definition identically regardless of which
  curriculum the standard comes from", but generation prompt line 970 tells the model
  the per-curriculum `generation_instructions` are authoritative and override the
  generic rules. Those instructions carry explicit granularity policy (Ghana: paired
  verbs are usually two skills; Nigeria: each numbered item is at least one skill;
  Rwanda: split comma enumerations). The judge grades against a spec the pipeline was
  told to override.
- It has no counterpart to "never force a split and never pad the skill count". The
  equivalent guard was written for coverage — "A standard that is already a single
  atomic skill is correctly covered by one component; do not mark it partial merely for
  having few components" — and omitted from granularity.

Consequence: part of the granularity shortfall is the two documents disagreeing, not
the pipeline misbehaving. Of 20 single-component standards judged `too_coarse`,
9 also have `atomicity = False` (real defect, both specs agree) and 11 have
`atomicity = True` (likely spec mismatch).

### Resolved 2026-08-14

`_GRANULARITY_DEFINITION` was rewritten clause-by-clause from the generation contract:
`too_coarse` now means the standard states skills separately that were left bundled,
`too_fine` means the split goes below what the standard states, and the definition says
explicitly that a standard which is itself one skill is correctly rendered as one
component. The curriculum-independence clause was removed.

Re-run into `results/lc_eval_aligned/` — same seed, same 502 items, 3 replicates,
1,806 judgements.

| Criterion | Before | After |
| --- | --- | --- |
| granularity | 0.919 | **0.931** |
| coverage | 0.975 | 0.992 |
| atomicity | 0.966 | 0.966 |
| non_redundancy | 0.988 | 0.987 |
| well_formedness | 0.991 | 0.989 |
| faithfulness | 0.994 | 0.995 |

The "after" column is scored over all 377 real items. An earlier version of these
figures (granularity 0.957) was inflated by the aggregation bug in §2a below and should
not be quoted.

Single-component standards judged `too_coarse` in at least 2 of 3 runs: **20/100 → 2/100**.

Granularity by stratum: one_component 0.887 → 0.979, two_components 0.889 → 0.914,
three_plus 0.974 → 0.984. The gain concentrates where the mismatch was predicted to
bite, which is what distinguishes a spec fix from a blanket relaxation.

Validity checks that the change did not simply make the judge lenient:

- Negative-control detection is unchanged on every corruption type: cross_strand_swap
  1.00, forced_redundancy 1.00, invented_specificity 1.00, truncation 0.96,
  under_decomposition 1.00. A judge that had merely been loosened would start passing
  planted defects.
- Verdict changes run both ways on the identical 377 real items: 22 too_coarse →
  appropriate and 12 too_fine → appropriate, but also 3 appropriate → too_coarse,
  6 appropriate → too_fine, 2 too_coarse → too_fine.
- Judge self-consistency improved: items whose replicates disagreed fell from 55 to 25
  of 377. The old wording was ambiguous enough to flip the judge between runs.

Stratification distortion has also largely vanished now that strata score alike —
population-weighted granularity is 0.959 vs 0.957 reported (was 0.905 vs 0.919).

## 2a. Replicate aggregation dropped unstable items (fixed 2026-08-15)

`_majority_verdict` never took a majority. It returned `verdicts[0]` plus a unanimity
flag, and `build_rubric_report` then did `if not stable: continue` — so any item where
the three replicates disagreed on any one of the six criteria was excluded from every
score.

The excluded items are not a random subsample: an item the judge flip-flops on sits
near a decision boundary, and boundary items fail more often than average. The headline
therefore described only items the judge was certain about.

Fixed by selecting the replicate whose pass or fail outcome matches the per-criterion
majority, and by keeping unstable items in the scores. `unstable_item_count` (25 of 377)
stays as a reported diagnostic rather than an exclusion filter. A replicate matching the
majority exactly exists for all 377 items, so no synthetic verdict is needed.

| Criterion | Unanimous-only (was) | Majority, all items (now) |
| --- | --- | --- |
| granularity | 0.957 | **0.931** |
| well_formedness | 0.997 | 0.989 |
| atomicity | 0.974 | 0.966 |
| non_redundancy | 0.994 | 0.987 |
| coverage | 0.997 | 0.992 |
| faithfulness | 0.997 | 0.995 |

This also removes an inconsistency where `pass_rate` was computed over 352 items while
its `sd` was computed over 377, because `replicate_groups` was populated before the
`continue`.

## 2b. Prompt aligned further; cached verdicts are now stale

A clause-by-clause audit of `RUBRIC_SYSTEM_MESSAGE` against the generation contract
found three more mismatches, all applied 2026-08-15 but **not yet re-run**:

- `FAITHFULNESS` accepted anything supported by "the standard text or its ancestor
  path". Generation rule 974 permits ancestor-derived content only when the curriculum
  instructions authorize it, which Rwanda and Tamil Nadu do and Ghana and Nigeria do
  not. Now worded so ancestors may only supply what the standard leaves elliptical.
  Components drawing content words from ancestors: tamil_nadu 41.0%, rwanda 23.6%,
  nigeria 19.7%, ghana 4.6%. Nigeria is the outlier worth checking, though the measure
  is crude — its ancestors are Topic/Sub-Theme/Theme, so topical overlap is expected
  without any borrowing.
- `WELL_FORMEDNESS` omitted "resource" and "teacher guidance", both named in rule 960.
- `NON_REDUNDANCY` missed rule 964, the whole-plus-parts case where a component restates
  the entire standard alongside components covering its parts. Upper bound of 72 such
  sets across the four curricula.

Checked and deliberately not added: rule 985 (no leaked statement codes or list markers)
has zero violations across all 1,792 components, and rule 967 (source language) carries
no signal since every config mandates English.

`_GRANULARITY_DEFINITION` was also inlined into `RUBRIC_SYSTEM_MESSAGE`, verified
byte-identical in the rendered prompt.

**Warning:** the verdicts in `results/lc_eval_aligned/` were produced by the previous
prompt. The judge cache is keyed by `(item_id, replicate)` and does not hash the prompt,
so a same-directory re-run could report stale verdicts as current. In practice the
§2c change below altered every `item_id`, so the next run will miss the cache entirely
and re-judge all 1,806 — correct, but at full cost. Folding a prompt fingerprint into
the cache key is still the real fix and is not yet done.

## 2c. Rubric cache key ignored component text (fixed 2026-08-15)

`_build_item_id` hashed only `curriculum|sfi_uuid|corruption`. Component text was not
in the basis, so regenerating a standard's components left the identifier unchanged and
a re-run would serve verdicts judged against the previous components. The edge builder
already folded candidate text into its basis; the rubric builder did not.

This was about to bite: §3 plans a generation re-run, after which the rubric eval would
have scored new components using old verdicts.

Fixed by adding component text to the basis, mirroring `_build_edge_item_id`. Verified
that two component sets under the same standard now produce different identifiers, and
that sampling still yields the same 1,116-item population and 377-item sample.

## 2d. Cross-strand-swap control produced duplicate component ids (fixed 2026-08-15)

`_corrupt_cross_strand_swap` wrote the replacement component at a random index but
always assigned `components[0].component_id`, so whenever that index was not zero two
components shared an id. 11 of 25 control items were affected.

`_judge_one` compares judged against shown ids as sets, so a duplicate passed validation
while one component went unjudged — potentially the deliberately unfaithful one, which
is the whole point of the control. Detection still measured 1.00, so the control worked
in spite of the defect rather than because it was sound.

Fixed by preserving the id at the replaced index. Verified 0 duplicates across all
control types.

The `judged != shown` check in `_judge_one` compared sets, so it could not detect
duplicate ids being shown at all. Now it rejects duplicate shown ids outright and
compares sorted lists rather than sets, which also catches a judge returning two
verdicts for one component. Confirmed to fire on the exact input that slipped through.
The edge path was checked and is unaffected: 0 of 2,908 items have duplicate
candidate ids.

## 2e. No negative control targets granularity or atomicity

`_CONTROL_TARGETS` maps the five controls to faithfulness (x2), non_redundancy,
well_formedness, and coverage. Granularity and atomicity have none, so neither has
demonstrated sensitivity — and granularity carries the headline finding.

`under_decomposition` looks like it should serve as the granularity control, but under
the pre-2026-08-15 prompt it tripped coverage 25/25 and granularity only 1/25. The
rewritten definition ("too_coarse: a component leaves bundled what the standard states
separately") describes exactly what that corruption builds, so it may trip granularity
on the next run. Check this before quoting the granularity number as validated, and
consider retargeting `under_decomposition` to granularity if it does.

## 3. Re-run generation and diff against the merge

The evaluated artifacts were generated 2026-08-07 to 08-10; the `tz6/kg-rework` merge
(`619bd5f`) landed 2026-08-11 01:51. So the scored output predates the merge.

The LC step itself is unaffected: `build_lc_generation_prompt` is byte-identical either
side of the merge, and no LC-specific symbol changed. Conclusions about decomposition
quality — granularity, atomicity, faithfulness, copy-through — still describe current
code.

What the merge rewrote is everything upstream: `sfi_relationships.py` (+4107),
`sfi_dedup.py` (+2275), `sfi_finalization.py` (+2197), `sfi_extraction.py` (+293), and
a new `sfi_source_anchors.py` (+1082). A re-run would likely feed a different SFI set
into an unchanged LC step, so any LC delta is attributable to the SFI rework.

Not a validity blocker for the eval. Worth doing to see what the SFI rework changed.

Sequence matters — run these as separate steps or the deltas are unattributable:

1. Re-run generation on the merged pipeline, unchanged. Diff vs. current artifacts to
   isolate what the merge changed.
2. Apply §2 (eval-side), re-run the rubric eval. Isolates measurement change.
3. Apply §1 (pipeline-side), re-run both. Isolates generation change.

## 4. Report caveats to carry forward

- Three of the six rubric criteria carry no evidence against the null baseline. A
  verbatim restatement of the standard scores 1.000 on coverage, faithfulness and
  non-redundancy, because one component copied from the source is complete, grounded
  and non-redundant by construction. Only granularity (0.957 vs 0.130), atomicity
  (0.974 vs 0.130) and well_formedness (0.997 vs 0.860) separate the pipeline from
  doing nothing. Report those as the result; the other three are hygiene checks.
- Headline rubric numbers are unweighted averages over a stratified sample that
  over-represents three-plus-component standards (36% of sample vs 20% of population).
  After the granularity fix the distortion is negligible — population-weighted
  granularity 0.959 vs 0.957 reported. Within-stratum and subgroup claims are unaffected.
- `_sheet_granularity` in the workbook counted baseline items alongside real ones and
  used whichever replicate was read first rather than the majority verdict, which
  inflated its too_coarse column from 13 to 100. Fixed 2026-08-14; the other four sheets
  read the aggregated report, which has always excluded baselines
  (`scoring.py:681-683`).
- 15 standard texts are duplicated across the corpus, which is why 2,054 edges collapse
  to 2,031 distinct standard–component pairs in the bidirectional agreement table.
  Unclear whether these are legitimate recurrences across grades or a dedup miss
  upstream — not yet checked.
- No human-labelled data exists. Internal consistency is established (distant distractor
  acceptance 0.5%, replicate SD under 0.6%) but agreement with a subject expert is
  untested. Highest-value addition if an SME becomes available: dual-label ~100 items
  and compute Cohen's kappa. That converts every number from "the judge says X" to
  "the judge, which agrees with experts at κ, says X".

## 5. Carried over

- Coverage-vs-source reconciliation with Tony — Ghana ~17 standards missing; Rwanda
  PDF diff.
- Investigate `#106` hasChild misattachment (buying/selling under Time unit).
- Dedup evaluation, specified in `lc_eval_plan.md`, never built. Free baseline is
  exact-match-only vs. semantic dedup: Ghana 28/12, Nigeria 17/104, Rwanda 41/84,
  Tamil Nadu 20/7.
