"""Evaluate completed LP runs.

Invoke from the backend directory via:

python src/kgfeg/entries/evaluate_lps.py ../results/kg_for_ed

The command performs local preflight, frozen preparation, judging/resume and reporting.
It can make live judge calls. Preparation remains separately callable without a client.
Use --render-report with a saved report directory to render only its Excel/HTML outputs.

To run a shorter version of this evaluation pipeline, use:

python src/kgfeg/entries/evaluate_lps.py \
  ../results/kg_for_ed \
  --new-invocation \
  --production-pairs-per-outcome 1 \
  --production-examples-per-tag 1 \
  --independent-uniform-pairs 1 \
  --independent-pairs-per-tag 1 \
  --diagnostic-pairs-per-cohort 1 \
  --base-blind-replicates 1 \
  --additional-diagnostic-replicates 1 \
  --critique-replicates 1 \
  --variant-replicates 1 \
  --synthetic-cases-per-family 1 \
  --synthetic-control-replicates 1 \
  --lexical-baseline-top-k 1
"""

# Standard Library
import asyncio
import hashlib
import json
import sqlite3
import sys

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

# Third Party Library
import httpx
import typer

from pydantic import ValidationError
from pydantic_ai.exceptions import AgentRunError, UserError

# Make the package importable when executing this file directly.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]

    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from kgfeg.evals.lp_eval.judge import (
    JudgeCallError,
    JudgeExecutionError,
    JudgeTransport,
    _store_directory_sync,
    _store_path,
    _store_read,
    _store_write,
    execute_evaluation,
    find_evaluation_store,
    open_evaluation_command,
    open_evaluation_store,
    open_judge_transport,
    persist_evaluation_schedule,
    resolve_judge_settings,
    validate_judge_settings,
)
from kgfeg.evals.lp_eval.presentation import render_evaluation_presentations
from kgfeg.evals.lp_eval.sampling import (
    _frozen_manifest,
    discover_lp_runs,
    freeze_lp_inputs,
    prepare_evaluation_schedule,
)
from kgfeg.evals.lp_eval.schemas import (
    EvaluationReportArtifacts,
    EvaluationSchedule,
    EvaluationSettings,
    EvaluationStore,
    ReportProvenance,
    ResolvedJudgeSettings,
    resolve_evaluation_settings,
)
from kgfeg.evals.lp_eval.scoring import write_evaluation_reports
from kgfeg.kgs.lp_requests import canonical_lp_json

# Instantiate the single-command CLI.
cli = typer.Typer(no_args_is_help=True)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """CLI execution outcome, distinct from semantic or project approval.

    Attributes
    ----------
    artifacts
        Exact immutable report generation.
    concern_count
        Number of reported groups requiring user disposition.
    execution_complete
        Whether every scheduled judgment and required report was produced.
    provenance
        Development/evaluation purpose retained through resume and report regeneration.
    """

    artifacts: EvaluationReportArtifacts
    concern_count: int
    execution_complete: bool
    provenance: ReportProvenance


def _bind_provenance(
    *,
    has_attempts: bool,
    provenance: ReportProvenance,
    reference: EvaluationStore,
    schedule: EvaluationSchedule,
) -> ReportProvenance:
    """Freeze development/evaluation purpose while the invocation lock is held.

    Parameters
    ----------
    has_attempts
        Whether any attempt already needs a previously recorded execution identity.
    provenance
        Explicit development/evaluation purpose, never an approval assertion.
    reference
        Locked invocation whose schedule and cache have been validated.
    schedule
        Exact complete schedule owning this execution.

    Returns
    -------
    ReportProvenance
        Bound development/evaluation purpose, including when reusing a completed cache.

    Raises
    ------
    ValueError
        If binding is missing for existing attempts, changed, duplicated or corrupt.
    """

    directory = reference.manifest_path.parent / "execution_identity"
    _store_path(directory)
    material = {
        "provenance": provenance.model_dump(mode="json"),
        "schedule_content_hash": schedule.material_content_hash,
    }
    payload = canonical_lp_json(material).encode("utf-8")

    if not directory.exists():
        if has_attempts:
            raise ValueError("Existing attempts have no frozen execution identity.")

        directory.mkdir()
        _store_directory_sync(directory.parent)
        path = directory / (hashlib.sha256(payload).hexdigest() + ".json")
        _store_write(path=path, payload=payload)
        _store_directory_sync(directory)
        return provenance

    paths = tuple(directory.iterdir())

    if len(paths) != 1:
        raise ValueError("Execution identity requires exactly one immutable record.")

    recorded = _store_read(paths[0])

    if paths[0].name != hashlib.sha256(recorded).hexdigest() + ".json":
        raise ValueError("Execution identity bytes differ from their hash.")

    identity = json.loads(recorded)

    if canonical_lp_json(identity).encode("utf-8") != recorded:
        raise ValueError("Execution identity is not canonical material.")

    if set(identity) != {"provenance", "schedule_content_hash"}:
        raise ValueError("Execution identity has missing or extra fields.")

    original = ReportProvenance.model_validate_json(
        canonical_lp_json(identity["provenance"])
    )

    if identity["schedule_content_hash"] != schedule.material_content_hash:
        raise ValueError("Execution identity belongs to another schedule.")

    if original != provenance:
        raise ValueError(
            "Invocation is bound to a different development/evaluation evidence kind."
        )

    return original


def _error_message(error: Exception) -> str:
    """Format local validation errors without serializing secret-bearing input values.

    Parameters
    ----------
    error
        Failure from local input validation or sanitized execution/reporting.

    Returns
    -------
    str
        Useful bounded diagnostic, excluding provider response bodies and tracebacks.
    """

    if isinstance(error, ValidationError):
        return "; ".join(
            (".".join(str(value) for value in item["loc"]) or "settings")
            + ": "
            + item["msg"]
            for item in error.errors(
                include_context=False, include_input=False, include_url=False
            )
        )

    if isinstance(error, (ValueError, OSError, JudgeExecutionError)):
        return str(error)[:2000]

    return f"Evaluation stopped ({type(error).__name__})."


def _render_report(
    *,
    evaluation_options_supplied: bool,
    output_directory: Path | None,
    report_directory: Path,
) -> None:
    """Render saved presentation derivatives without preparing or executing evaluation.

    Parameters
    ----------
    evaluation_options_supplied
        Whether any evaluation or resume controls were supplied.
    output_directory
        Optional isolated presentation destination.
    report_directory
        Exact immutable saved report, including a partial report.

    Raises
    ------
    typer.BadParameter
        If evaluation or resume options would otherwise be ignored.
    typer.Exit
        If saved material validation or presentation publication fails.
    """

    if evaluation_options_supplied:
        raise typer.BadParameter(
            "--render-report cannot be combined with evaluation controls, "
            "--new-invocation or --resume-manifest."
        )

    try:
        directory = render_evaluation_presentations(
            output_directory=output_directory, report_directory=report_directory
        )
    except (ValueError, OSError, RuntimeError) as error:
        typer.echo(f"Presentation rendering failed: {_error_message(error)}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Workbook: {directory / 'learning_progressions_evaluation.xlsx'}")
    typer.echo(f"Visualization: {directory / 'report.html'}")


def _report_result(
    *, artifacts: EvaluationReportArtifacts, provenance: ReportProvenance
) -> EvaluationResult:
    """Read the just-published report status without inventing a quality threshold.

    Parameters
    ----------
    artifacts
        Published immutable report generation.
    provenance
        Original bound execution identity.

    Returns
    -------
    EvaluationResult
        Execution status and pending concern count from the actual report.

    Raises
    ------
    ValueError
        If published manifest or report bytes fail their expected hash.
    """

    manifest_bytes = _store_read(artifacts.directory / "lp_eval_manifest.json")

    if hashlib.sha256(manifest_bytes).hexdigest() != artifacts.manifest_sha256:
        raise ValueError("Published report manifest changed.")

    manifest = json.loads(manifest_bytes)
    report_bytes = _store_read(artifacts.directory / "lp_eval_report.json")

    if (
        hashlib.sha256(report_bytes).hexdigest()
        != manifest["files"]["lp_eval_report.json"]["sha256"]
    ):
        raise ValueError("Published report changed.")

    report = json.loads(report_bytes)
    return EvaluationResult(
        artifacts=artifacts,
        concern_count=len(report["concerns"]),
        execution_complete=report["execution_complete"],
        provenance=provenance,
    )


def _resume_selection(
    *,
    judge: ResolvedJudgeSettings,
    overrides: Mapping[str, int] | None,
    reference: EvaluationStore,
    repository_root: Path,
    results_root: Path,
) -> EvaluationStore:
    """Validate an explicit frozen selection against the supplied root and controls.

    Parameters
    ----------
    judge
        Currently resolved dedicated provider/model/settings.
    overrides
        Only explicitly supplied controls; omitted controls retain frozen values.
    reference
        Exact invocation selected automatically or explicitly.
    repository_root
        Checkout owning results/lp_evals.
    results_root
        User's resolved starting directory, never rediscovered.

    Returns
    -------
    EvaluationStore
        Same validated manifest reference.

    Raises
    ------
    ValueError
        If root, settings, model or selected input material differ.
    """

    with open_evaluation_store(reference) as session:
        schedule = session.schedule
        inventory = _frozen_manifest(schedule.inputs).inventory

    if (
        inventory.results_root != results_root
        or inventory.evaluation_root != repository_root / "results" / "lp_evals"
    ):
        raise ValueError(
            "Resume manifest belongs to a different results root or repository."
        )

    effective = schedule.settings.settings.model_dump()
    requested = resolve_evaluation_settings({**effective, **dict(overrides or {})})

    if requested.settings != schedule.settings.settings or judge != schedule.judge:
        raise ValueError("Resume controls/model differ from the frozen invocation.")

    return reference


def _setting_help(name: str) -> str:
    """Describe a CLI control using the settings schema as the default authority.

    Parameters
    ----------
    name
        Evaluation settings field exposed as a command-line option.

    Returns
    -------
    str
        Field description and effective default when no override is supplied.

    Raises
    ------
    KeyError
        If the option does not name an evaluation settings field.
    """

    field = dict(EvaluationSettings.model_fields.items())[name]
    return f"{field.description} Default: {field.default}."


@cli.command(
    help=(
        "Preflight, freeze, judge/resume and report completed LP runs in sequence. "
        "Unfinished runs are excluded; invalid completed inputs fail before judge calls. "
        "With --render-report, RESULTS_ROOT is a saved report directory: render only, "
        "without judging, rescoring or resuming."
    ),
    epilog=(
        "Resumes the newest matching incomplete invocation using its frozen schedule. "
        "Use --new-invocation to discover again or --resume-manifest to select a run. "
        "Without --render-report this command can make live API calls. "
        "Offline callers use prepare_evaluation() "
        "and injected run_evaluation() transports. Reports are written to results/lp_evals/. "
        "Evaluation exit status: 0 for complete execution/reporting, 1 for execution/input/report "
        "failure, 2 for invalid CLI arguments, 130 for interruption. "
        "With --render-report, exit 0 means successful presentation generation only; "
        "the saved evaluation may still be incomplete. Quality concerns "
        "do not impose a passing score or certify project completion."
    ),
)
def evaluate(
    *,
    additional_diagnostic_replicates: int | None = typer.Option(
        None, help=_setting_help("additional_diagnostic_replicates"), show_default=False
    ),
    base_blind_replicates: int | None = typer.Option(
        None, help=_setting_help("base_blind_replicates"), show_default=False
    ),
    critique_replicates: int | None = typer.Option(
        None, help=_setting_help("critique_replicates"), show_default=False
    ),
    diagnostic_pairs_per_cohort: int | None = typer.Option(
        None, help=_setting_help("diagnostic_pairs_per_cohort"), show_default=False
    ),
    independent_pairs_per_tag: int | None = typer.Option(
        None, help=_setting_help("independent_pairs_per_tag"), show_default=False
    ),
    independent_uniform_pairs: int | None = typer.Option(
        None, help=_setting_help("independent_uniform_pairs"), show_default=False
    ),
    lexical_baseline_top_k: int | None = typer.Option(
        None, help=_setting_help("lexical_baseline_top_k"), show_default=False
    ),
    new_invocation: bool = typer.Option(
        False,
        "--new-invocation",
        help="Rediscover inputs while preserving earlier invocations.",
    ),
    output_directory: Path | None = typer.Option(
        None,
        help="Isolated presentation destination; requires --render-report.",
    ),
    production_examples_per_tag: int | None = typer.Option(
        None, help=_setting_help("production_examples_per_tag"), show_default=False
    ),
    production_pairs_per_outcome: int | None = typer.Option(
        None, help=_setting_help("production_pairs_per_outcome"), show_default=False
    ),
    render_report: bool = typer.Option(
        False,
        "--render-report",
        help="Render Excel/HTML from the saved report at RESULTS_ROOT, without evaluation.",
    ),
    results_root: Path = typer.Argument(
        ...,
        help="Starting results directory, or exact saved report directory with --render-report.",
    ),
    resume_manifest: Path | None = typer.Option(
        None,
        help="Exact invocation manifest.json; omitted controls retain frozen values.",
    ),
    sampling_seed: int | None = typer.Option(
        None, help=_setting_help("sampling_seed"), show_default=False
    ),
    synthetic_cases_per_family: int | None = typer.Option(
        None, help=_setting_help("synthetic_cases_per_family"), show_default=False
    ),
    synthetic_control_replicates: int | None = typer.Option(
        None, help=_setting_help("synthetic_control_replicates"), show_default=False
    ),
    variant_replicates: int | None = typer.Option(
        None, help=_setting_help("variant_replicates"), show_default=False
    ),
) -> None:
    """Preflight, freeze, judge/resume and report completed LP runs in sequence.

    Parameters
    ----------
    additional_diagnostic_replicates
        Optional override of the corresponding frozen sampling/repetition control.
    base_blind_replicates
        Optional override of the corresponding frozen sampling/repetition control.
    critique_replicates
        Optional override of the corresponding frozen sampling/repetition control.
    diagnostic_pairs_per_cohort
        Optional override of the corresponding frozen sampling/repetition control.
    independent_pairs_per_tag
        Optional override of the corresponding frozen sampling/repetition control.
    independent_uniform_pairs
        Optional override of the corresponding frozen sampling/repetition control.
    lexical_baseline_top_k
        Optional override of the corresponding frozen sampling/repetition control.
    new_invocation
        Discover a new selection instead of automatically resuming frozen work.
    output_directory
        Optional isolated presentation destination; requires render_report.
    production_examples_per_tag
        Optional override of the corresponding frozen sampling/repetition control.
    production_pairs_per_outcome
        Optional override of the corresponding frozen sampling/repetition control.
    render_report
        Render the supplied saved report without preparing or executing evaluation.
    results_root
        Starting curriculum results directory, or saved report when render_report is set.
    resume_manifest
        Optional exact frozen invocation to resume without rediscovery.
    sampling_seed
        Optional integer seed for deterministic selection and ordering.
    synthetic_cases_per_family
        Optional override of the corresponding frozen sampling/repetition control.
    synthetic_control_replicates
        Optional override of the corresponding frozen sampling/repetition control.
    variant_replicates
        Optional override of the corresponding frozen sampling/repetition control.

    Raises
    ------
    typer.BadParameter
        If controls are invalid or new discovery conflicts with explicit resume.
    typer.Exit
        Reports command status after completion, failure or interruption.
    """

    overrides = {
        name: value
        for name, value in {
            "additional_diagnostic_replicates": additional_diagnostic_replicates,
            "base_blind_replicates": base_blind_replicates,
            "critique_replicates": critique_replicates,
            "diagnostic_pairs_per_cohort": diagnostic_pairs_per_cohort,
            "independent_pairs_per_tag": independent_pairs_per_tag,
            "independent_uniform_pairs": independent_uniform_pairs,
            "lexical_baseline_top_k": lexical_baseline_top_k,
            "production_examples_per_tag": production_examples_per_tag,
            "production_pairs_per_outcome": production_pairs_per_outcome,
            "sampling_seed": sampling_seed,
            "synthetic_cases_per_family": synthetic_cases_per_family,
            "synthetic_control_replicates": synthetic_control_replicates,
            "variant_replicates": variant_replicates,
        }.items()
        if value is not None
    }

    if render_report:
        _render_report(
            evaluation_options_supplied=bool(
                overrides or new_invocation or resume_manifest is not None
            ),
            output_directory=output_directory,
            report_directory=results_root,
        )
        raise typer.Exit(code=0)

    if output_directory is not None:
        raise typer.BadParameter("--output-directory requires --render-report.")

    if new_invocation and resume_manifest is not None:
        raise typer.BadParameter(
            "--new-invocation and --resume-manifest are mutually exclusive."
        )

    try:
        if resume_manifest is None:
            resolve_evaluation_settings(overrides)
    except ValidationError as error:
        raise typer.BadParameter(_error_message(error)) from error

    try:
        repository_root = Path(__file__).resolve().parents[4]

        with open_evaluation_command(repository_root):
            typer.echo("Preflight and frozen preparation...")
            reference = prepare_evaluation(
                new_invocation=new_invocation,
                overrides=overrides,
                repository_root=repository_root,
                results_root=results_root,
                resume_manifest=resume_manifest,
            )
            typer.echo(f"Frozen invocation: {reference.manifest_path}")
            result = asyncio.run(
                run_evaluation(
                    progress=typer.echo,
                    provenance=ReportProvenance(evidence_kind="evaluation"),
                    reference=reference,
                )
            )
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        typer.echo(
            "Evaluation interrupted; durable attempts and completed judgments remain available.",
            err=True,
        )
        raise typer.Exit(code=130) from error
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        typer.echo(_error_message(error), err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Report: {result.artifacts.directory / 'lp_eval_report.md'}")
    typer.echo(
        f"Execution: {'complete' if result.execution_complete else 'incomplete'}; "
        f"concern groups requiring disposition: {result.concern_count}; "
        f"evidence: {result.provenance.evidence_kind}."
    )
    raise typer.Exit(code=0 if result.execution_complete else 1)


def prepare_evaluation(
    *,
    new_invocation: bool = False,
    overrides: Mapping[str, int] | None = None,
    repository_root: Path,
    results_root: Path,
    resume_manifest: Path | None = None,
) -> EvaluationStore:
    """Preflight local inputs and freeze or resume work without creating a provider
    client.

    Parameters
    ----------
    new_invocation
        Explicit rediscovery instead of automatic frozen-invocation lookup.
    overrides
        Supplied sampling/repetition controls, validated before fresh discovery.
    repository_root
        Checkout whose results/lp_evals owns all evaluation artifacts.
    results_root
        Arbitrarily nested curriculum results or an individual kgs directory.
    resume_manifest
        Optional exact invocation manifest; omitted settings then retain frozen values.

    Returns
    -------
    EvaluationStore
        Fully materialized, validated frozen invocation; no live calls occurred.

    Raises
    ------
    ValueError
        If arguments, inputs, settings, conflicts or frozen identity are invalid.
    """

    if new_invocation and resume_manifest is not None:
        raise ValueError("New discovery and explicit resume are mutually exclusive.")

    root = repository_root.resolve(strict=True)
    starting = results_root.resolve(strict=True)

    if not starting.is_dir():
        raise ValueError("Starting results path must be a directory.")

    judge = resolve_judge_settings()
    validate_judge_settings(judge)

    if resume_manifest is not None:
        path = resume_manifest.absolute()
        raw = _store_read(path)
        reference = EvaluationStore(
            content_hash=hashlib.sha256(raw).hexdigest(), manifest_path=path
        )
        return _resume_selection(
            judge=judge,
            overrides=overrides,
            reference=reference,
            repository_root=root,
            results_root=starting,
        )

    settings = resolve_evaluation_settings(overrides)
    reference = (
        None
        if new_invocation
        else find_evaluation_store(
            judge=judge,
            repository_root=root,
            results_root=starting,
            settings=settings,
        )
    )

    if reference is not None:
        return _resume_selection(
            judge=judge,
            overrides=overrides,
            reference=reference,
            repository_root=root,
            results_root=starting,
        )

    inventory = discover_lp_runs(
        evaluation_root=root / "results" / "lp_evals", results_root=starting
    )
    inputs = freeze_lp_inputs(inventory=inventory, repository_root=root)
    schedule = prepare_evaluation_schedule(
        inputs=inputs, judge=judge, settings=settings
    )
    return persist_evaluation_schedule(repository_root=root, schedule=schedule)


async def run_evaluation(
    *,
    progress: Callable[[str], None] | None = None,
    provenance: ReportProvenance,
    reference: EvaluationStore,
    transport_factory: Callable[
        [ResolvedJudgeSettings], AbstractAsyncContextManager[JudgeTransport]
    ] = open_judge_transport,
) -> EvaluationResult:
    """Judge a frozen invocation and report it through an injectable single-call
    transport.

    Fully cached runs avoid provider construction and remote availability checks. The
    caller owns live-call authorization; an injected local transport supports offline
    development without introducing another CLI mode.

    Parameters
    ----------
    progress
        Optional text-only progress sink.
    provenance
        Evidence purpose, explicitly development for local mock responders.
    reference
        Validated frozen invocation; no discovery occurs during dispatch.
    transport_factory
        Injectable provider context manager; defaults to the dedicated real model.

    Returns
    -------
    EvaluationResult
        Published complete or explicitly incomplete reporting outcome.

    Raises
    ------
    ValueError
        If inputs, cache or execution identity cannot be safely used or reported.
    asyncio.CancelledError
        If interrupted; validated partial reporting is attempted before propagation.
    """

    with open_evaluation_store(reference) as session:
        cache = session.snapshot()
        complete = len(cache.judgments) == session.schedule.total_requests
        bound = _bind_provenance(
            has_attempts=bool(cache.events),
            provenance=provenance,
            reference=reference,
            schedule=session.schedule,
        )
        judge = session.schedule.judge

        if progress is not None:
            progress(
                f"{'Resuming' if cache.executions else 'Starting'} evaluation: "
                f"{reference.manifest_path}; "
                f"selected curricula: {len(session.schedule.curricula)}; "
                f"reused judgments: {len(cache.judgments)}; "
                f"remaining: {session.schedule.total_requests - len(cache.judgments)}; "
                f"prior executions: {len(cache.executions)}."
            )

    errors: tuple[str, ...] = ()
    cancelled = False

    if not complete:
        try:
            async with transport_factory(judge) as transport:
                await execute_evaluation(reference=reference, transport=transport)
        except asyncio.CancelledError:
            errors = ("Evaluation interrupted; inspect durable attempt evidence.",)
            cancelled = True
        except (
            JudgeExecutionError,
            JudgeCallError,
            AgentRunError,
            UserError,
            httpx.HTTPError,
            OSError,
            ValueError,
            sqlite3.Error,
        ) as error:
            errors = (
                (
                    str(error)
                    if isinstance(error, JudgeExecutionError)
                    else f"Judge execution stopped ({type(error).__name__}); inspect attempt evidence."
                ),
            )

    if progress is not None:
        for message in errors:
            progress(f"Execution blocked: {message}")

        progress("Validating judgments and writing immutable reports...")

    artifacts = write_evaluation_reports(
        execution_errors=errors, provenance=bound, reference=reference
    )

    if cancelled:
        raise asyncio.CancelledError

    return _report_result(artifacts=artifacts, provenance=bound)


if __name__ == "__main__":
    cli()
