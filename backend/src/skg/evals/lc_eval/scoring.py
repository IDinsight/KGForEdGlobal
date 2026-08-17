"""Aggregation of judge verdicts into the evaluation report."""

# Standard Library
import random
import re

from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional, Sequence

# Package Library
from skg.evals.lc_eval.schemas import (
    ControlDetection,
    CriterionScore,
    EdgeItem,
    EdgeJudgement,
    EdgeReport,
    EdgeScore,
    RubricItem,
    RubricJudgement,
    RubricReport,
    RubricVerdict,
)
from skg.utils.general import write_to_json

_BASELINE_STRATEGIES: tuple[str, ...] = (
    "above_mean_lexical",
    "select_all",
    "top_1_lexical",
)
_BOOTSTRAP_ITERATIONS = 2000
_COMPONENT_CRITERIA: tuple[str, ...] = ("atomicity", "faithfulness", "well_formedness")
_CONTROL_TARGETS: dict[str, str] = {
    "cross_strand_swap": "faithfulness",
    "forced_redundancy": "non_redundancy",
    "invented_specificity": "faithfulness",
    "truncation": "well_formedness",
    "under_decomposition": "coverage",
}
_PASSING_VALUE: dict[str, object] = {
    "atomicity": True,
    "coverage": "complete",
    "faithfulness": "grounded",
    "granularity": "appropriate",
    "non_redundancy": True,
    "well_formedness": True,
}
_SET_CRITERIA: tuple[str, ...] = ("coverage", "granularity", "non_redundancy")


def _baseline_selections(
    *, items: Sequence[EdgeItem], strategy: str
) -> dict[str, set[str]]:
    """Select candidates using a deterministic lexical strategy instead of a judge.

    Parameters
    ----------
    items
        Items to select for.
    strategy
        `select_all` accepts everything, establishing the degenerate floor.
        `top_1_lexical` accepts the single highest token-overlap candidate.
        `above_mean_lexical` accepts every candidate scoring above the item's mean.

    Returns
    -------
    dict[str, set[str]]
        Selected candidate ids keyed by item id.

    Raises
    ------
    ValueError
        If an unknown strategy is requested.
    """

    if strategy not in _BASELINE_STRATEGIES:
        raise ValueError(
            f"Unknown baseline strategy {strategy!r}; expected one of "
            f"{sorted(_BASELINE_STRATEGIES)}."
        )

    selections: dict[str, set[str]] = {}
    for item in items:
        overlaps = {
            candidate.candidate_id: _token_overlap(item.anchor_text, candidate.text)
            for candidate in item.candidates
        }
        if strategy == "select_all":
            selections[item.item_id] = set(overlaps)
        elif strategy == "top_1_lexical":
            selections[item.item_id] = {max(overlaps, key=overlaps.__getitem__)}
        else:
            mean = sum(overlaps.values()) / (len(overlaps) or 1)
            selections[item.item_id] = {k for k, v in overlaps.items() if v > mean}

    return selections


def _bootstrap_ci(*, seed: int, values: Sequence[bool]) -> tuple[float, float]:
    """Compute a bootstrap percentile interval for a proportion.

    Parameters
    ----------
    seed
        Seed controlling the resampling.
    values
        Per-item pass indicators.

    Returns
    -------
    tuple[float, float]
        Lower and upper bounds of the 95% interval, or (0.0, 0.0) when empty.
    """

    if not values:
        return (0.0, 0.0)

    rng = random.Random(seed)
    size = len(values)
    proportions = sorted(
        sum(values[rng.randrange(size)] for _ in range(size)) / size
        for _ in range(_BOOTSTRAP_ITERATIONS)
    )
    low = proportions[int(0.025 * _BOOTSTRAP_ITERATIONS)]
    high = proportions[int(0.975 * _BOOTSTRAP_ITERATIONS) - 1]
    return (round(low, 4), round(high, 4))


def _criterion_values(*, criterion: str, verdict: RubricVerdict) -> list[object]:
    """Extract every observed value of one criterion from a verdict.

    Component-level criteria yield one value per component; set-level criteria yield a
    single value.

    Parameters
    ----------
    criterion
        Criterion to extract.
    verdict
        Verdict to read.

    Returns
    -------
    list[object]
        Observed values.
    """

    if criterion in _SET_CRITERIA:
        return [getattr(verdict.set_verdict, criterion)]

    return [getattr(cv, criterion) for cv in verdict.component_verdicts]


def _distractor_acceptance(
    *, items: Sequence[EdgeItem], selections: dict[str, set[str]]
) -> dict[str, dict[str, int]]:
    """Count how often the judge accepts distractors at each distance tier.

    The `distant` tier acts as the negative control. A near-zero acceptance rate there
    means precision on the closer tiers reflects the pipeline; a material rate means
    the judge is over-accepting and every precision figure is inflated.

    Parameters
    ----------
    items
        Items that were judged.
    selections
        Judge selections keyed by item id.

    Returns
    -------
    dict[str, dict[str, int]]
        Accepted and offered counts per distance tier.
    """

    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"accepted": 0, "offered": 0}
    )
    for item in items:
        chosen = selections.get(item.item_id)
        if chosen is None:
            continue
        for candidate in item.candidates:
            if candidate.is_true:
                continue
            counts[candidate.distance]["offered"] += 1
            if candidate.candidate_id in chosen:
                counts[candidate.distance]["accepted"] += 1

    return dict(sorted(counts.items()))


def _edge_agreement(
    *, items: Sequence[EdgeItem], selections: dict[str, set[str]]
) -> dict[str, int]:
    """Classify each asserted edge by which directions confirmed it.

    Parameters
    ----------
    items
        Items that were judged.
    selections
        Judge selections keyed by item id.

    Returns
    -------
    dict[str, int]
        Counts of edges confirmed by both directions, one only, or neither.
    """

    confirmed: dict[str, set[tuple[str, str]]] = {
        "component_to_standards": set(),
        "standard_to_components": set(),
    }
    asserted: set[tuple[str, str]] = set()
    for item in items:
        chosen = selections.get(item.item_id)
        if chosen is None:
            continue
        for candidate in item.candidates:
            if not candidate.is_true:
                continue
            edge = (
                (item.anchor_text, candidate.text)
                if item.direction == "standard_to_components"
                else (candidate.text, item.anchor_text)
            )
            asserted.add(edge)
            if candidate.candidate_id in chosen:
                confirmed[item.direction].add(edge)

    agreement = {"both": 0, "component_only": 0, "neither": 0, "standard_only": 0}
    for edge in asserted:
        by_standard = edge in confirmed["standard_to_components"]
        by_component = edge in confirmed["component_to_standards"]
        if by_standard and by_component:
            agreement["both"] += 1
        elif by_standard:
            agreement["standard_only"] += 1
        elif by_component:
            agreement["component_only"] += 1
        else:
            agreement["neither"] += 1

    return agreement


def _item_passes(*, criterion: str, verdict: RubricVerdict) -> bool:
    """Return whether one item passes a criterion.

    An item passes a component-level criterion only when every component passes it.

    Parameters
    ----------
    criterion
        Criterion to test.
    verdict
        Verdict to test.

    Returns
    -------
    bool
        True when the item passes.
    """

    passing = _PASSING_VALUE[criterion]
    return all(
        value == passing
        for value in _criterion_values(criterion=criterion, verdict=verdict)
    )


def _majority_verdict(verdicts: Sequence[RubricVerdict]) -> tuple[RubricVerdict, bool]:
    """Pick the representative verdict across replicates and report stability.

    Parameters
    ----------
    verdicts
        Verdicts for one item, one per replicate.

    Returns
    -------
    tuple[RubricVerdict, bool]
        The replicate whose pass or fail outcome matches the per-criterion majority on
        every criterion, and whether every replicate agreed on every criterion.

    Raises
    ------
    ValueError
        If no verdicts are supplied.
    """

    if not verdicts:
        raise ValueError("Cannot pick a majority verdict from zero replicates.")

    criteria = _COMPONENT_CRITERIA + _SET_CRITERIA
    outcomes = {
        criterion: [_item_passes(criterion=criterion, verdict=v) for v in verdicts]
        for criterion in criteria
    }
    stable = all(len(set(values)) == 1 for values in outcomes.values())
    majority = {
        criterion: sum(values) * 2 >= len(values)
        for criterion, values in outcomes.items()
    }
    for verdict in verdicts:
        if all(
            _item_passes(criterion=criterion, verdict=verdict) == majority[criterion]
            for criterion in criteria
        ):
            return (verdict, stable)

    return (verdicts[0], stable)


def _sd(values: Sequence[float]) -> float:
    """Return the population standard deviation of a small sample.

    Parameters
    ----------
    values
        Per-replicate measurements of one metric.

    Returns
    -------
    float
        Standard deviation, or zero when fewer than two replicates were run.
    """

    if len(values) < 2:
        return 0.0

    mean = sum(values) / len(values)
    return round((sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5, 5)


def _score_criterion(
    *,
    criterion: str,
    replicate_verdicts: Optional[Sequence[Sequence[RubricVerdict]]] = None,
    seed: int,
    verdicts: Sequence[RubricVerdict],
) -> CriterionScore:
    """Score one criterion over a slice of items.

    Parameters
    ----------
    criterion
        Criterion to score.
    seed
        Seed for the bootstrap interval.
    replicate_verdicts
        Verdicts grouped by replicate, used to measure how much the pass rate moves
        when the judge runs again. None when replication data is unavailable.
    verdicts
        One representative verdict per item.

    Returns
    -------
    CriterionScore
        Pass rate, bootstrap interval, value distribution, and replicate spread.
    """

    passes = [_item_passes(criterion=criterion, verdict=v) for v in verdicts]
    counts: Counter[str] = Counter()
    for verdict in verdicts:
        for value in _criterion_values(criterion=criterion, verdict=verdict):
            counts[str(value)] += 1

    low, high = _bootstrap_ci(seed=seed, values=passes)
    per_replicate = [
        sum(_item_passes(criterion=criterion, verdict=v) for v in group) / len(group)
        for group in (replicate_verdicts or [])
        if group
    ]
    return CriterionScore(
        ci_high=high,
        ci_low=low,
        criterion=criterion,
        n=len(verdicts),
        pass_rate=round(sum(passes) / len(passes), 4) if passes else 0.0,
        sd=_sd(per_replicate),
        value_counts=dict(sorted(counts.items())),
    )


def _attach_edge_sd(
    *,
    items: Sequence[EdgeItem],
    score: EdgeScore,
    selections_by_replicate: Sequence[dict[str, set[str]]],
) -> EdgeScore:
    """Attach per-metric standard deviations measured across replicates.

    Parameters
    ----------
    items
        Items in the slice.
    score
        Score computed from the representative replicate.
    selections_by_replicate
        Judge selections for each replicate.

    Returns
    -------
    EdgeScore
        The score with replicate spread attached.
    """

    if len(selections_by_replicate) < 2:
        return score

    per_metric: dict[str, list[float]] = defaultdict(list)
    for selections in selections_by_replicate:
        replicate = _score_edge_slice(
            items=items, selections=selections, slice_name=score.slice_name
        )
        per_metric["exact_match_rate"].append(replicate.exact_match_rate)
        per_metric["f1"].append(replicate.f1)
        per_metric["precision"].append(replicate.precision)
        per_metric["recall"].append(replicate.recall)

    return score.model_copy(
        update={
            "standard_deviations": {k: _sd(v) for k, v in sorted(per_metric.items())}
        }
    )


def _score_edge_slice(
    *,
    items: Sequence[EdgeItem],
    selections: dict[str, set[str]],
    slice_name: str,
) -> EdgeScore:
    """Score precision, recall, and exact match over one slice of items.

    Parameters
    ----------
    items
        Items in the slice.
    selections
        Judge selections keyed by item id.
    slice_name
        Name recorded on the score.

    Returns
    -------
    EdgeScore
        Aggregate score for the slice.
    """

    true_positive = false_positive = false_negative = exact = 0
    scored = 0
    for item in items:
        if item.item_id not in selections:
            continue
        scored += 1
        chosen = selections[item.item_id]
        truth = {c.candidate_id for c in item.candidates if c.is_true}
        true_positive += len(chosen & truth)
        false_positive += len(chosen - truth)
        false_negative += len(truth - chosen)
        exact += int(chosen == truth)

    precision = true_positive / (true_positive + false_positive or 1)
    recall = true_positive / (true_positive + false_negative or 1)
    return EdgeScore(
        exact_match_rate=round(exact / scored, 4) if scored else 0.0,
        f1=round(2 * precision * recall / (precision + recall or 1), 4),
        n=scored,
        precision=round(precision, 4),
        recall=round(recall, 4),
        slice_name=slice_name,
    )


def _score_slice(
    *,
    replicate_verdicts: Optional[Sequence[Sequence[RubricVerdict]]] = None,
    seed: int,
    verdicts: Sequence[RubricVerdict],
) -> list[CriterionScore]:
    """Score every criterion over one slice of items.

    Parameters
    ----------
    seed
        Seed for bootstrap intervals.
    replicate_verdicts
        Verdicts grouped by replicate, or None when unavailable.
    verdicts
        One representative verdict per item.

    Returns
    -------
    list[CriterionScore]
        Scores in criterion-name order.
    """

    return [
        _score_criterion(
            criterion=criterion,
            replicate_verdicts=replicate_verdicts,
            seed=seed,
            verdicts=verdicts,
        )
        for criterion in sorted(_COMPONENT_CRITERIA + _SET_CRITERIA)
    ]


def _token_overlap(anchor: str, candidate: str) -> float:
    """Return the Jaccard overlap of word tokens between two texts.

    Tokens are matched with a Unicode-aware pattern so curricula written in
    non-Latin scripts are compared on their own words rather than collapsing to
    whatever Latin digits they happen to contain.

    Parameters
    ----------
    anchor
        Anchor text of the item.
    candidate
        Candidate text being compared.

    Returns
    -------
    float
        Overlap between zero and one, zero when both texts have no tokens.
    """

    left = set(re.findall(r"\w+", anchor.lower()))
    right = set(re.findall(r"\w+", candidate.lower()))
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def build_edge_report(
    *,
    items: Sequence[EdgeItem],
    judge_model: str,
    judgements: Sequence[EdgeJudgement],
) -> EdgeReport:
    """Aggregate discrimination verdicts, sliced by direction and population.

    Parameters
    ----------
    items
        Items that were judged.
    judge_model
        Model that produced the verdicts.
    judgements
        Judge selections.

    Returns
    -------
    EdgeReport
        Scores per direction and cross-direction edge agreement.
    """

    by_replicate: dict[int, dict[str, set[str]]] = defaultdict(dict)
    for judgement in judgements:
        by_replicate[judgement.replicate][judgement.item_id] = set(
            judgement.selected_candidate_ids
        )
    selections = by_replicate.get(0, {})
    by_direction: dict[str, list[EdgeItem]] = defaultdict(list)
    for item in items:
        by_direction[item.direction].append(item)

    scores: dict[str, list[EdgeScore]] = {}
    for direction, direction_items in sorted(by_direction.items()):
        replicate_selections = [by_replicate[r] for r in sorted(by_replicate)]
        slices = [
            _attach_edge_sd(
                items=direction_items,
                score=_score_edge_slice(
                    items=direction_items, selections=selections, slice_name="all"
                ),
                selections_by_replicate=replicate_selections,
            )
        ]
        by_curriculum: dict[str, list[EdgeItem]] = defaultdict(list)
        for item in direction_items:
            by_curriculum[item.curriculum].append(item)
        slices += [
            _score_edge_slice(
                items=v, selections=selections, slice_name=f"curriculum: {k}"
            )
            for k, v in sorted(by_curriculum.items())
        ]
        if direction == "component_to_standards":
            for label, subset in (
                ("multi-parent", [i for i in direction_items if i.is_multi_parent]),
                (
                    "single-parent",
                    [i for i in direction_items if not i.is_multi_parent],
                ),
            ):
                if subset:
                    slices.append(
                        _score_edge_slice(
                            items=subset, selections=selections, slice_name=label
                        )
                    )
        scores[direction] = slices

    baseline_scores: dict[str, list[EdgeScore]] = {}
    for direction, direction_items in sorted(by_direction.items()):
        baseline_scores[direction] = [
            _score_edge_slice(
                items=direction_items,
                selections=_baseline_selections(
                    items=direction_items, strategy=strategy
                ),
                slice_name=strategy,
            )
            for strategy in _BASELINE_STRATEGIES
        ]

    return EdgeReport(
        baseline_scores=baseline_scores,
        distractor_acceptance=_distractor_acceptance(
            items=items, selections=selections
        ),
        edge_agreement=_edge_agreement(items=items, selections=selections),
        judge_model=judge_model,
        scores_by_direction=scores,
    )


def build_rubric_report(
    *,
    baseline_items: Optional[Sequence[RubricItem]] = None,
    items: Sequence[RubricItem],
    judge_model: str,
    judgements: Sequence[RubricJudgement],
    replicates: int,
    seed: int,
) -> RubricReport:
    """Aggregate judge verdicts into the evaluation report.

    Real items whose replicates disagree on any criterion are excluded from headline
    scores and counted separately, so instability is visible rather than averaged away.

    Parameters
    ----------
    baseline_items
        Items whose single component is the standard text verbatim, if judged.
    items
        Every item that was judged.
    judge_model
        Model that produced the verdicts.
    judgements
        All verdicts, across items and replicates.
    replicates
        Replicates judged per item.
    seed
        Sampling seed, recorded in the report and used for bootstrap intervals.

    Returns
    -------
    RubricReport
        The aggregated report.
    """

    verdicts_by_item: dict[str, dict[int, RubricVerdict]] = defaultdict(dict)
    for judgement in judgements:
        verdicts_by_item[judgement.item_id][judgement.replicate] = judgement.verdict

    baseline_ids = {item.item_id for item in baseline_items or []}
    item_by_id = {item.item_id: item for item in items}

    baseline_verdicts: list[RubricVerdict] = []
    by_curriculum: dict[str, list[RubricVerdict]] = defaultdict(list)
    by_stratum: dict[str, list[RubricVerdict]] = defaultdict(list)
    control_hits: dict[str, list[bool]] = defaultdict(list)
    real_verdicts: list[RubricVerdict] = []
    replicate_groups: dict[int, list[RubricVerdict]] = defaultdict(list)
    unstable = 0

    for item_id, verdicts_by_replicate in sorted(verdicts_by_item.items()):
        item_verdicts = [
            verdicts_by_replicate[r] for r in sorted(verdicts_by_replicate)
        ]
        representative, stable = _majority_verdict(item_verdicts)
        item = item_by_id.get(item_id)
        if item is None:
            continue

        if item_id in baseline_ids:
            baseline_verdicts.append(representative)
            continue

        if item.corruption is not None:
            criterion = _CONTROL_TARGETS[item.corruption]
            control_hits[item.corruption].append(
                not _item_passes(criterion=criterion, verdict=representative)
            )
            continue

        for replicate, verdict in verdicts_by_replicate.items():
            replicate_groups[replicate].append(verdict)

        if not stable:
            unstable += 1

        by_curriculum[item.curriculum].append(representative)
        by_stratum[item.stratum].append(representative)
        real_verdicts.append(representative)

    return RubricReport(
        baseline_scores=(
            _score_slice(seed=seed, verdicts=baseline_verdicts)
            if baseline_verdicts
            else []
        ),
        control_detections=[
            ControlDetection(
                corruption=corruption,
                detected=sum(hits),
                detection_rate=round(sum(hits) / len(hits), 4),
                targeted_criterion=_CONTROL_TARGETS[corruption],
                total=len(hits),
            )
            for corruption, hits in sorted(control_hits.items())
        ],
        judge_model=judge_model,
        replicates=replicates,
        scores_by_curriculum={
            curriculum: _score_slice(seed=seed, verdicts=slice_verdicts)
            for curriculum, slice_verdicts in sorted(by_curriculum.items())
        },
        scores_by_stratum={
            stratum: _score_slice(seed=seed, verdicts=slice_verdicts)
            for stratum, slice_verdicts in sorted(by_stratum.items())
        },
        scores_overall=_score_slice(
            replicate_verdicts=[replicate_groups[i] for i in sorted(replicate_groups)],
            seed=seed,
            verdicts=real_verdicts,
        ),
        seed=seed,
        unstable_item_count=unstable,
    )


def write_edge_report(*, output_dir: Path, report: EdgeReport) -> None:
    """Write the aggregated discrimination report.

    Parameters
    ----------
    output_dir
        Directory to write into.
    report
        Report to persist.
    """

    write_to_json(
        fp=output_dir / "lc_eval_edge_report.json",
        json_info=report.model_dump(mode="json"),
    )


def write_rubric_report(*, output_dir: Path, report: RubricReport) -> None:
    """Write the aggregated evaluation report.

    Parameters
    ----------
    output_dir
        Directory to write `lc_eval_report.json` into.
    report
        Report to persist.
    """

    write_to_json(
        fp=output_dir / "lc_eval_report.json",
        json_info=report.model_dump(mode="json"),
    )
