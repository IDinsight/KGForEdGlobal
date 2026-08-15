"""This module contains the entry point for evaluating generated Learning Components
with an LLM judge.

Invoke from the backend directory via:

python src/skg/entries/evaluate_lcs.py rubric --replicates 3
python src/skg/entries/evaluate_lcs.py edges --concurrency 16
"""

# Future Library
from __future__ import annotations

# Standard Library
from pathlib import Path
from typing import Optional

# Third Party Library
import typer

# Package Library
from skg.evals.lc_eval.judge import (
    JUDGE_CONCURRENCY,
    judge_model_config,
    run_edge_judging,
    run_rubric_judging,
)
from skg.evals.lc_eval.sampling import (
    build_baseline_items,
    build_edge_items,
    discover_curricula,
    load_population,
    sample_rubric_items,
    write_edge_items,
    write_rubric_sample,
)
from skg.evals.lc_eval.scoring import (
    build_edge_report,
    build_rubric_report,
    write_edge_report,
    write_rubric_report,
)
from skg.utils.general import make_dir
from skg.utils.logging_ import logger

# Instantiate typer apps for the command line interface.
cli = typer.Typer(no_args_is_help=True)

DEFAULT_OUTPUT_SUBDIR = "lc_eval"
DEFAULT_SEED = 20260811

# Anchored to the repository root rather than the working directory, so the default
# resolves whether the command is invoked from the repository root or from `backend`.
DEFAULT_RESULTS_ROOT = Path(__file__).resolve().parents[4] / "results"


def _resolve_output_dir(*, output_dir: Optional[Path], results_root: Path) -> Path:
    """Resolve where evaluation artifacts are written.

    Parameters
    ----------
    output_dir
        Explicit output directory, or None to use `<results_root>/lc_eval`.
    results_root
        Root directory holding per-curriculum pipeline runs.

    Returns
    -------
    Path
        Directory to write artifacts into.
    """

    resolved = output_dir or (results_root / DEFAULT_OUTPUT_SUBDIR)
    make_dir(resolved)
    return resolved


@cli.command()
def edges(  # pylint: disable=R0917
    concurrency: int = typer.Option(
        JUDGE_CONCURRENCY, help="Maximum judge calls in flight at once."
    ),
    curricula: Optional[list[str]] = typer.Option(
        None,
        help=(
            "Curriculum directory names under the results root. Defaults to every "
            "curriculum with generated components."
        ),
    ),
    output_dir: Optional[Path] = typer.Option(
        None, help="Where to write artifacts. Defaults to <results-root>/lc_eval."
    ),
    replicates: int = typer.Option(
        1, help="Times each item is judged, for self-consistency."
    ),
    results_root: Path = typer.Option(
        DEFAULT_RESULTS_ROOT, help="Root directory of pipeline run results."
    ),
    seed: int = typer.Option(DEFAULT_SEED, help="Seed for distractors and ordering."),
) -> None:
    """Run the bidirectional discrimination evaluation over every asserted edge.

    Each edge is tested from both ends, and the cross-direction agreement counts
    reveal edges the graph does not confirm consistently.

    Deterministic lexical baselines are always scored and need no option, since they
    reuse the items already built and issue no model calls. This differs from the
    rubric evaluation, whose baseline creates extra items that must be judged and is
    therefore sized by `baselines_per_curriculum`.

    Parameters
    ----------
    concurrency
        Maximum judge calls in flight at once.
    curricula
        Curriculum directory names, or None to use every curriculum with generated
        components.
    output_dir
        Where to write artifacts, or None for the default.
    results_root
        Root directory of pipeline run results.
    replicates
        Times each item is judged.
    seed
        Seed for distractor selection and option ordering.
    """

    resolved = _resolve_output_dir(output_dir=output_dir, results_root=results_root)
    items = build_edge_items(
        curricula=curricula or discover_curricula(results_root=results_root),
        results_root=results_root,
        seed=seed,
    )
    write_edge_items(items=items, output_dir=resolved)
    judgements = run_edge_judging(
        concurrency=concurrency,
        items=items,
        model_config=judge_model_config(),
        output_dir=resolved,
        replicates=replicates,
    )
    report = build_edge_report(
        items=items,
        judge_model=judge_model_config().model,
        judgements=judgements,
    )
    write_edge_report(output_dir=resolved, report=report)
    for direction, scores in report.scores_by_direction.items():
        for score in scores:
            logger.success(
                f"{direction} [{score.slice_name}] n={score.n} "
                f"precision={score.precision:.3f} recall={score.recall:.3f} "
                f"f1={score.f1:.3f} exact={score.exact_match_rate:.3f}"
            )
    for tier, counts in report.distractor_acceptance.items():
        offered = counts["offered"] or 1
        logger.success(
            f"DISTRACTOR ACCEPTANCE [{tier}] {counts['accepted']}/{counts['offered']} "
            f"= {counts['accepted'] / offered:.3f}"
        )
    logger.success(f"Edge agreement across directions: {report.edge_agreement}")


@cli.command()
def rubric(  # pylint: disable=R0917
    baselines_per_curriculum: int = typer.Option(
        25, help="Baseline items per curriculum, or 0 to skip baselines."
    ),
    concurrency: int = typer.Option(
        JUDGE_CONCURRENCY, help="Maximum judge calls in flight at once."
    ),
    controls_per_type: int = typer.Option(
        25, help="Negative controls to plant per corruption type."
    ),
    curricula: Optional[list[str]] = typer.Option(
        None,
        help=(
            "Curriculum directory names under the results root. Defaults to every "
            "curriculum with generated components."
        ),
    ),
    output_dir: Optional[Path] = typer.Option(
        None, help="Where to write artifacts. Defaults to <results-root>/lc_eval."
    ),
    replicates: int = typer.Option(
        1, help="Times each item is judged, for self-consistency. Must be odd."
    ),
    results_root: Path = typer.Option(
        DEFAULT_RESULTS_ROOT, help="Root directory of pipeline run results."
    ),
    seed: int = typer.Option(DEFAULT_SEED, help="Seed for sampling and corruptions."),
) -> None:
    """Run the rubric evaluation over a stratified sample of decompositions.

    The process is as follows:

    1. Load every standard with components across the requested curricula.
    2. Draw a stratified sample and plant negative controls.
    3. Build baseline items whose only component is the standard text verbatim.
    4. Judge every item the requested number of times, resuming from cache.
    5. Aggregate the verdicts into a report.

    Parameters
    ----------
    baselines_per_curriculum
        Baseline items per curriculum, or 0 to skip baselines.
    concurrency
        Maximum judge calls in flight at once.
    controls_per_type
        Negative controls to plant per corruption type.
    curricula
        Curriculum directory names, or None to use every curriculum with generated
        components.
    output_dir
        Where to write artifacts, or None for the default.
    replicates
        Times each item is judged. Must be odd, so that every criterion has a majority
        across replicates and no item has to be dropped or tie-broken arbitrarily.
    results_root
        Root directory of pipeline run results.
    seed
        Seed for sampling and corruption selection.

    Raises
    ------
    typer.BadParameter
        If `replicates` is even.
    """

    if replicates % 2 == 0:
        raise typer.BadParameter(
            f"replicates must be odd so every criterion has a majority across "
            f"replicates; got {replicates}."
        )

    resolved = _resolve_output_dir(output_dir=output_dir, results_root=results_root)
    population = load_population(
        curricula=curricula or discover_curricula(results_root=results_root),
        results_root=results_root,
    )
    sample = sample_rubric_items(
        control_per_type=controls_per_type, population=population, seed=seed
    )
    baselines = (
        build_baseline_items(
            per_curriculum=baselines_per_curriculum, population=population, seed=seed
        )
        if baselines_per_curriculum
        else []
    )
    write_rubric_sample(output_dir=resolved, sample=sample)

    items = list(sample.items) + baselines
    judgements = run_rubric_judging(
        concurrency=concurrency,
        items=items,
        model_config=judge_model_config(),
        output_dir=resolved,
        replicates=replicates,
    )
    report = build_rubric_report(
        baseline_items=baselines,
        items=items,
        judge_model=judge_model_config().model,
        judgements=judgements,
        replicates=replicates,
        seed=seed,
    )
    write_rubric_report(output_dir=resolved, report=report)
    for score in report.scores_overall:
        logger.success(
            f"{score.criterion}: n={score.n} pass={score.pass_rate:.3f} "
            f"[{score.ci_low:.3f}, {score.ci_high:.3f}]"
        )


if __name__ == "__main__":
    cli()
