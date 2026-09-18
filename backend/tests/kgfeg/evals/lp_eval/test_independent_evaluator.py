"""Independently challenge evaluator sampling, evidence, execution and reporting."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import builtins
import hashlib
import io
import itertools
import json
import os
import shutil
import socket
import sqlite3
import subprocess

from collections import Counter
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

from pydantic import TypeAdapter, ValidationError
from typer.testing import CliRunner

# Package Library
from kgfeg.config import BackendSettings
from kgfeg.entries import evaluate_lps as entry
from kgfeg.evals.lp_eval import judge, prompts, sampling, scoring
from kgfeg.evals.lp_eval.schemas import (
    ConcernDisposition,
    EvaluationCache,
    EvaluationPair,
    EvaluationSchedule,
    EvaluationSettings,
    FileFingerprint,
    FrozenInputs,
    JudgeReply,
    JudgeUsage,
    ProductionPair,
    ProductionPopulation,
    ReportProvenance,
    SnapshotArtifact,
    UpstreamEvidenceSource,
    resolve_evaluation_settings,
)
from tests.fixtures.lp_eval.snapshot_fixtures import build_snapshot
from tests.kgfeg.kgs import test_lp_selection as selection_fixtures

# Private pytest fixtures are used parameters, not intentionally ignored arguments.
# pylint: disable=useless-param-doc


_ROOT = Path(__file__).resolve().parents[5]
# Two five-SFI and one six-SFI fixtures yield 10, 10 and 15 unordered pairs. Both default
# cohorts select all pairs. Per curriculum: 2P + I + 7(min(P,12)+min(I,12)) + 75.
_EXPECTED_REQUESTS = 245 + 288 + 245
_PROFILES = (
    "madhi_math",
    "nigeria_math",
    "pratham_science",
    "rwanda_math",
    "ghana_math",
    "ghana_english",
)


def _dump(value: Any) -> str:
    """Encode stable JSON without using implementation hash helpers.

    Parameters
    ----------
    value
        Synthetic material or setting value under examination.

    Returns
    -------
    str
        Constructed offline material for the stated test contract.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@pytest.fixture(scope="module")
def _frozen(tmp_path_factory: pytest.TempPathFactory) -> FrozenInputs:
    """Build and freeze synthetic runs without reading any repository results.

    Parameters
    ----------
    tmp_path_factory
        Factory for isolated test-owned source and evaluation artifacts.

    Returns
    -------
    FrozenInputs
        Three fully validated synthetic curricula in temporary storage.
    """
    repository = tmp_path_factory.mktemp("lp-evaluator-inputs").resolve()
    sources = repository / "synthetic-runs"
    for count, identity in ((5, 10000), (6, 20000), (5, 30000)):
        build_snapshot(count=count, identity=identity, root=sources)
    inventory = sampling.discover_lp_runs(
        evaluation_root=repository / "results/lp_evals", results_root=sources
    )
    assert len(inventory.runs) == 3
    assert all(run.status == "completed_candidate" for run in inventory.runs)
    reference = sampling.freeze_lp_inputs(
        inventory=inventory, repository_root=repository
    )
    assert reference.manifest_path.is_relative_to(repository)
    return reference


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject accidental provider dispatch at the socket boundary.

    Parameters
    ----------
    monkeypatch
        Restoring environment, model, or filesystem seam substitutions.
    """
    guard = Mock(side_effect=AssertionError("Offline evaluator tests cannot connect."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _production_population() -> ProductionPopulation:
    """Construct outcome and overlapping tag cells with independently known sizes.

    Returns
    -------
    ProductionPopulation
        Constructed offline material for the stated test contract.
    """
    outcomes = ("buildsTowards", "relatesTo", "no_relation", "needs_review")
    pairs = []
    for index in range(40):
        pair = EvaluationPair(
            endpoint_uuids=(UUID(int=2 * index + 1), UUID(int=2 * index + 2)),
            pair_id=str(index),
        )
        pairs.append(
            ProductionPair(
                checker_outcome="accepted",
                direction="first_to_second" if index < 10 else None,
                outcome=outcomes[index // 10],
                pair=pair,
                producer_direction=None,
                producer_outcome=outcomes[index // 10],
                producer_to_final_changes=(),
                published_relationship_uuid=(
                    UUID(int=1000 + index) if index < 20 else None
                ),
                request_content_hash="a" * 64,
                request_id=UUID(int=2000 + index),
                tags=(
                    ("same_rank", "equal_normalized_text")
                    if index % 2
                    else ("missing_coordinate",)
                ),
            )
        )
    return ProductionPopulation(
        artifact_fingerprints=(),
        doc_key="synthetic",
        framework_uuid=UUID(int=100),
        material_content_hash="a" * 64,
        outcome_counts=tuple((x, 10) for x in outcomes),
        pairs=tuple(pairs),
        tag_counts=(),
        upstream_input_content_hash="b" * 64,
    )


def _reply(prompt: Any) -> JudgeReply:
    """Return valid synthetic ambiguity, without treating model output as truth.

    Parameters
    ----------
    prompt
        Rendered request whose identity the fake must preserve.

    Returns
    -------
    JudgeReply
        Constructed offline material for the stated test contract.
    """
    shown = json.loads(prompt.user_message)
    response = {
        **shown["response_identity"],
        "confidence": 0.5,
        "explanation": "Synthetic uncertainty retained.",
    }
    if prompt.task == "classification":
        response.update(
            decision="ambiguous",
            direction=None,
            evidence_references=[prompt.references[0]],
        )
    else:
        response.update(
            grounding="ambiguous",
            claims=[
                {
                    "claim": "Synthetic unresolved material claim",
                    "evidence_references": [],
                    "explanation": "The fake intentionally does not infer support.",
                    "support": "unresolved",
                }
            ],
        )
    return JudgeReply(
        response_json=_dump(response), usage=JudgeUsage(input_tokens=7, output_tokens=3)
    )


@pytest.fixture(scope="module")
def _report_inputs(_schedule: Any) -> Any:
    """Prepare authenticated population and lexical inputs once for scoring tests.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.

    Returns
    -------
    Any
        Authenticated frozen test material for dependent checks.
    """
    return scoring.prepare_report_inputs(_schedule)


@pytest.fixture(autouse=True, scope="module")
def _results_are_not_test_inputs() -> Any:
    """Forbid repository-result reads, including during module fixture setup.

    Yields
    ------
    Any
        Restoring read guards; isolated pytest artifacts remain available.
    """
    results = (_ROOT / "results").resolve()

    def _guard(operation: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a file or directory reader without restricting temporary fixtures.

        Parameters
        ----------
        operation
            Original reader restored after the module finishes.

        Returns
        -------
        Callable[..., Any]
            Reader rejecting accidental dependencies on local generated results.
        """

        def _read(*args: Any, **kwargs: Any) -> Any:
            """Check the requested location before delegating to the real reader.

            Parameters
            ----------
            args
                Positional reader arguments.
            kwargs
                Named reader arguments.

            Returns
            -------
            Any
                Original reader result.
            """
            file = args[0] if args else kwargs.get("file", kwargs.get("path", "."))
            if isinstance(file, (str, bytes, os.PathLike)):
                assert (
                    not Path(os.fsdecode(file)).resolve().is_relative_to(results)
                ), "Evaluator pytest must not read repository results"
            return operation(*args, **kwargs)

        return _read

    with pytest.MonkeyPatch.context() as monkeypatch:
        for target, name in (
            (builtins, "open"),
            (io, "open"),
            (os, "listdir"),
            (os, "scandir"),
        ):
            monkeypatch.setattr(target, name, _guard(getattr(target, name)))
        yield


def _run_metadata(
    *, directory: Path, identity: int = 1, status: str | None = "success"
) -> None:
    """Write only synthetic discovery markers; these are not validated snapshots.

    Parameters
    ----------
    directory
        Isolated synthetic run boundary.
    identity
        Distinct synthetic framework and document identity.
    status
        Synthetic completion marker, or absence for an active run.
    """
    directory.mkdir(parents=True)
    (directory / "kg_run.json").write_text(
        _dump(
            {
                "run_id": str(UUID(int=identity)),
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T01:00:00Z" if status else None,
                "extra": {"status": status} if status else {},
            }
        )
    )
    if status == "success":
        (directory / "kg_run_manifest.json").write_text(
            _dump({"doc_key": str(identity)})
        )
        (directory / "lp_generation_requests_manifest.json").write_text(
            _dump(
                {
                    "doc_key": str(identity),
                    "framework_uuid": str(UUID(int=identity)),
                }
            )
        )


@pytest.fixture(scope="module")
def _schedule(_frozen: FrozenInputs) -> Any:
    """Prepare all required default requests from read-only development evidence.

    Parameters
    ----------
    _frozen
        Authenticated synthetic multi-curriculum manifest reference.

    Returns
    -------
    Any
        Authenticated frozen test material for dependent checks.
    """
    settings = BackendSettings(
        _env_file=None,
        PATHS_PROJECT_DIR=_ROOT,
        LLM_LP_EVAL_JUDGE_MODEL="anthropic:claude-opus-5",
    )
    return sampling.prepare_evaluation_schedule(
        inputs=_frozen,
        judge=judge.resolve_judge_settings(settings),
        settings=resolve_evaluation_settings(),
    )


def _session(*, path: str = ":memory:", schedule: Any) -> Any:
    """Create the real durable ledger schema in isolated test storage.

    Parameters
    ----------
    path
        Temporary SQLite ledger or source path.
    schedule
        Work plan bound to the isolated attempt ledger.

    Returns
    -------
    Any
        Constructed offline material for the stated test contract.
    """
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, content_hash TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO metadata VALUES (?, ?)",
        ("schedule", schedule.material_content_hash),
    )
    connection.execute(
        "INSERT INTO metadata VALUES (?, ?)",
        ("head", _dump({"count": 0, "hash": schedule.material_content_hash})),
    )
    connection.commit()
    return judge.EvaluationSession(connection=connection, events=(), schedule=schedule)


def _small_schedule(schedule: Any) -> Any:
    """Limit unit execution to eight independent scheduled classification tasks.

    Parameters
    ----------
    schedule
        Work plan bound to the isolated attempt ledger.

    Returns
    -------
    Any
        Constructed offline material for the stated test contract.
    """
    curriculum = schedule.curricula[0]
    requests = tuple(r for r in curriculum.requests if r.role == "base")[:8]
    return replace(
        schedule,
        curricula=(replace(curriculum, requests=requests),),
        total_requests=len(requests),
    )


def _source(
    *, count: int = 7, identity: int = 500, profile: str | None = None
) -> UpstreamEvidenceSource:
    """Build reduced upstream material without any nomination or judgment records.

    Parameters
    ----------
    count
        Synthetic endpoint or framework population size.
    identity
        Distinct synthetic framework and document identity.
    profile
        Reduced curriculum shape and its explicit policy.

    Returns
    -------
    UpstreamEvidenceSource
        Constructed offline material for the stated test contract.
    """
    config = selection_fixtures._config(profile or "nigeria_math")
    if profile:
        bundle = selection_fixtures._fixture_bundle(profile)
    else:
        bundle = selection_fixtures._bundle(
            framework_uuid=UUID(int=identity),
            items=tuple(
                selection_fixtures._item(number=n) for n in range(1, count + 1)
            ),
        )
    bundle.framework.metadata["doc_key"] = f"unfamiliar-{identity}"
    bundle.entity_provenance["framework"]["doc_key"] = f"unfamiliar-{identity}"
    payload = bundle.model_dump_json().encode()
    return UpstreamEvidenceSource(
        config_json=config.model_dump_json(by_alias=True),
        doc_key=f"unfamiliar-{identity}",
        framework_uuid=bundle.framework.case_identifier_uuid,
        upstream_artifact=SnapshotArtifact(
            fingerprint=FileFingerprint(
                path=Path("/synthetic/upstream.json"),
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            ),
            name="as_lc_kg_bundle.json",
            payload=payload,
        ),
    )


@pytest.mark.parametrize("profile", _PROFILES)
def test_admissible_pairs_independently_follow_type_rank_policy(profile: str) -> None:
    """Compare indexed pair membership with exhaustive reduced-policy enumeration.

    Parameters
    ----------
    profile
        Reduced curriculum shape and its explicit policy.
    """
    source = _source(profile=profile)
    population = sampling.build_admissible_population(source)
    bundle = json.loads(source.upstream_artifact.payload)
    config = json.loads(source.config_json)["lp"]
    assert population.total_pairs == len(tuple(population.iter_pairs()))
    expected = set()
    items = {UUID(row["case_identifier_uuid"]): row for row in bundle["items"]}
    # The approved fixture profiles permit same-type pairs; missing rank retains coherence.
    allowed = {
        tuple(sorted((p["first_statement_type"], p["second_statement_type"])))
        for p in config["relates_to"]["allowed_statement_type_pairs"]
    }
    for first, second in itertools.combinations(sorted(items), 2):
        if (
            tuple(
                sorted(
                    (items[first]["statement_type"], items[second]["statement_type"])
                )
            )
            in allowed
        ):
            expected.add((first, second))
    actual = {pair.endpoint_uuids for pair in population.iter_pairs()}
    assert actual == expected
    assert [population.pair_at(i) for i in range(len(actual))] == list(
        population.iter_pairs()
    )
    with pytest.raises(ValueError):
        population.pair_at(True)


def test_all_requests_offline_execution_reporting_and_denominators(
    _report_inputs: Any, _schedule: Any
) -> None:
    """Recompute all ambiguity metrics and usage from a complete independent fake run.

    Parameters
    ----------
    _report_inputs
        Frozen population and lexical scoring inputs.
    _schedule
        Complete default development request schedule.
    """
    session = _session(schedule=_schedule)

    class _Transport:
        """Offline transport returning controlled outcomes for scheduled requests."""

        settings = _schedule.judge

        async def judge(self, prompt: Any) -> JudgeReply:
            """Return a controlled response for the current scheduled request.

            Parameters
            ----------
            prompt
                Rendered request whose identity the fake must preserve.

            Returns
            -------
            JudgeReply
                Constructed offline material for the stated test contract.
            """
            return _reply(prompt)

    try:
        cache = asyncio.run(
            judge._Execution(
                check_material=lambda: None,
                session=session,
                sleep=asyncio.sleep,
                transport=_Transport(),
            ).run()
        )
        assert len(cache.judgments) == _EXPECTED_REQUESTS
        assert len(cache.events) == 2 * _EXPECTED_REQUESTS
        report = scoring.score_evaluation(
            cache=cache,
            inputs=_report_inputs,
            schedule=_schedule,
        )
        material = json.loads(report.report_json)
        assert material["execution_complete"]
        assert (
            material["valid_judgments"]
            == material["planned_judgments"]
            == _EXPECTED_REQUESTS
        )
        assert (
            material["usage_summary"]["tokens"]["input_tokens"]["total"]
            == _EXPECTED_REQUESTS * 7
        )
        assert material["usage_summary"]["cost"]["total_by_currency"] is None
        for metric in material["metrics"]:
            assert metric["valid"] == metric["ambiguous"] == metric["planned"]
            if "sample_conditioned_coverage" in metric:
                rate = metric["sample_conditioned_coverage"]["nominated"]
                assert rate["denominator"] == 0
                assert rate["value"] is None
        assert material["concerns"]
        assert "not" in scoring.render_evaluation_markdown(material).lower()
        invalid = replace(cache, judgments=cache.judgments[:-1])
        with pytest.raises(ValueError):
            scoring.score_evaluation(
                cache=invalid,
                inputs=_report_inputs,
                schedule=_schedule,
            )
    finally:
        session._connection.close()


def test_blind_view_retains_facts_and_original_critique_boundary(
    _frozen: FrozenInputs,
) -> None:
    """Preserve factual nomination support while withholding recommendations and answers.

    Parameters
    ----------
    _frozen
        Authenticated synthetic multi-curriculum manifest reference.
    """
    snapshot = sampling.load_frozen_lp_inputs(_frozen)[0]
    population = sampling.build_admissible_population(
        sampling.upstream_evidence_source(snapshot)
    )
    production = sampling.build_production_population(
        population=population, snapshot=snapshot
    )
    builder = sampling.build_production_evidence_builder(snapshot)
    views = builder.build_pair(production.pairs[0].pair)
    blind = json.loads(views.blind.payload_json)
    critique = json.loads(views.critique.payload_json)
    original = critique["original_request"]
    assert "nominated_relationships" not in views.blind.payload_json
    assert "operative_judgment" not in blind
    assert "nomination_facts" in blind["pairs"][0]
    facts = blind["pairs"][0]["nomination_facts"]["facts"]
    assert any("shared_count" in fact["field"] for fact in facts)
    assert any("aggregate_scope" in fact["field"] for fact in facts)
    assert "nominated_relationships" in _dump(original)
    assert "operative_judgment" in critique
    assert "original_request" not in blind
    assert views.blind.material_content_hash != views.critique.material_content_hash
    assert set(views.critique.references)
    policy_references = {
        "/original_checker_system_message",
        "/original_producer_system_message",
    }
    assert policy_references <= set(views.critique.references)
    assert all(critique[reference[1:]].strip() for reference in policy_references)
    assert all(
        ref.startswith("/original_request/") or ref in policy_references
        for ref in views.critique.references
    )
    assert not any(
        ref.startswith("/operative_judgment") for ref in views.critique.references
    )
    # The critic must see the actual original bounded request, not expanded content.
    request_rows = next(
        a for a in snapshot.artifacts if a.name == "lp_generation_requests.jsonl"
    )
    request = next(
        json.loads(line)
        for line in request_rows.payload.splitlines()
        if json.loads(line)["request_id"] == str(views.critique.request_id)
    )
    assert original == request


def test_cli_help_has_one_argument_all_controls_and_no_offline_mode() -> None:
    """Check the Typer contract without resolving credentials or dispatching calls."""
    result = CliRunner().invoke(
        app=entry.cli, args=["--help"], color=False, env={"COLUMNS": "240"}
    )
    assert result.exit_code == 0
    text = result.stdout.replace("\x1b", "")
    assert "RESULTS_ROOT" in text
    assert "--offline" not in text
    for name in dict(EvaluationSettings.model_fields.items()):
        assert "--" + name.replace("_", "-") in text


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--production-pairs-per-outcome", "0"),
        ("--variant-replicates", "-1"),
        ("--sampling-seed", "bad"),
    ],
)
def test_cli_invalid_settings_never_prepare(
    flag: str, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Reject invalid controls before preparation or provider resolution.

    Parameters
    ----------
    flag
        Optional CLI control being challenged.
    monkeypatch
        Restoring environment, model, or filesystem seam substitutions.
    value
        Synthetic material or setting value under examination.
    """
    prepare = Mock(side_effect=AssertionError("Invalid CLI must not prepare"))
    monkeypatch.setattr(name="prepare_evaluation", target=entry, value=prepare)
    result = CliRunner().invoke(app=entry.cli, args=["/not-needed", flag, value])
    assert result.exit_code == 2
    prepare.assert_not_called()


@pytest.mark.parametrize(
    "grounding,supports",
    [
        ("partially_grounded", ["supported"]),
        ("partially_grounded", ["unsupported"]),
        ("unsupported", ["supported"]),
    ],
)
def test_critique_grounding_must_reconcile_claim_support(
    grounding: str, supports: list[str]
) -> None:
    """Reject grounding categories that contradict the complete material claim list.

    Parameters
    ----------
    grounding
        Aggregate grounding category supplied by the fake critic.
    supports
        Explicit per-claim support assessments.
    """
    # Package Library
    from tests.kgfeg.evals.lp_eval.test_grounding_remediation import _request

    request = _request()
    response = json.loads(_reply(request.prompt).response_json)
    response["grounding"] = grounding
    response["claims"] = [
        {
            "claim": "The shown material claim",
            "evidence_references": [request.prompt.references[0]],
            "explanation": "Synthetic explicit support assessment",
            "support": support,
        }
        for support in supports
    ]
    with pytest.raises(ValueError):
        judge.validate_judge_response(request=request, response_json=_dump(response))


@pytest.mark.parametrize("count", [1, 3, 8])
def test_discovery_all_layouts_alias_cycles_and_active_exclusion(
    count: int, tmp_path: Path
) -> None:
    """Discover any positive curriculum count without interpreting folder labels.

    Parameters
    ----------
    count
        Synthetic endpoint or framework population size.
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    root = tmp_path / "inputs"
    runs = []
    for index in range(count):
        directory = (
            root
            / ("deeper/unfamiliar/label" if index % 2 else "flat")
            / str(index)
            / "kgs"
        )
        _run_metadata(directory=directory, identity=index + 1)
        runs.append(directory)
    _run_metadata(directory=root / "active/kgs", identity=101, status=None)
    (root / "active/kgs/lp_generation_responses.jsonl").write_bytes(
        b"growing invalid text"
    )
    _run_metadata(directory=root / "failed/kgs", identity=102, status="error")
    (root / "alias").symlink_to(runs[0], target_is_directory=True)
    (root / "cycle").symlink_to(root, target_is_directory=True)
    outside = tmp_path / "outside"
    _run_metadata(directory=outside / "kgs", identity=500)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    output = root / "lp_evals"
    _run_metadata(directory=output / "fake/kgs", identity=600)
    inventory = sampling.discover_lp_runs(evaluation_root=output, results_root=root)
    assert {
        r.kgs_directory for r in inventory.runs if r.status == "completed_candidate"
    } == set(runs)
    assert Counter(r.status for r in inventory.runs) == {
        "completed_candidate": count,
        "failed": 1,
        "unfinished": 1,
    }
    assert {s.reason for s in inventory.skipped_paths} == {
        "outside_results_root",
        "evaluation_output",
    }
    assert inventory.aliases
    one = sampling.discover_lp_runs(evaluation_root=output, results_root=runs[0])
    assert len(one.runs) == 1


def test_discovery_duplicate_framework_fails(tmp_path: Path) -> None:
    """Reject copied snapshots instead of arbitrarily selecting one.

    Parameters
    ----------
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    for name in ("old", "new"):
        _run_metadata(directory=tmp_path / name / "kgs")
    with pytest.raises(sampling.LPDiscoveryError, match="Conflicting"):
        sampling.discover_lp_runs(
            evaluation_root=tmp_path / "out", results_root=tmp_path
        )


@pytest.mark.parametrize(
    "mutation", ["missing", "contradictory", "invalid_json", "duplicate_json_keys"]
)
def test_discovery_invalid_completed_evidence_is_not_skipped(
    mutation: str, tmp_path: Path
) -> None:
    """Distinguish a broken completed candidate from an active run.

    Parameters
    ----------
    mutation
        Deliberate corruption applied only to isolated test material.
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    directory = tmp_path / "kgs"
    _run_metadata(directory=directory)
    if mutation == "missing":
        (directory / "kg_run_manifest.json").unlink()
    elif mutation == "contradictory":
        path = directory / "kg_run.json"
        value = json.loads(path.read_text())
        value["completed_at"] = None
        path.write_text(_dump(value))
    elif mutation == "invalid_json":
        (directory / "kg_run.json").write_text("{")
    else:
        (directory / "kg_run_manifest.json").write_text(
            '{"doc_key":"one","doc_key":"two"}'
        )
    with pytest.raises(sampling.LPDiscoveryError):
        sampling.discover_lp_runs(
            evaluation_root=tmp_path / "out", results_root=tmp_path
        )


def test_discovery_success_marker_alone_fails_snapshot_preparation(
    tmp_path: Path,
) -> None:
    """Require actual artifacts even when completion metadata claims success.

    Parameters
    ----------
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    _run_metadata(directory=tmp_path / "inputs/kgs")
    inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "results/lp_evals", results_root=tmp_path / "inputs"
    )
    with pytest.raises(sampling.LPSnapshotError):
        sampling.freeze_lp_inputs(inventory=inventory, repository_root=tmp_path)
    assert not (tmp_path / "results").exists()


def test_discovery_zero_completed_fails_with_inventory(tmp_path: Path) -> None:
    """Record excluded activity without pretending an empty evaluation completed.

    Parameters
    ----------
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    _run_metadata(directory=tmp_path / "kgs", status=None)
    with pytest.raises(sampling.LPDiscoveryError) as error:
        sampling.discover_lp_runs(
            evaluation_root=tmp_path / "out", results_root=tmp_path
        )
    assert error.value.inventory.runs[0].status == "unfinished"


def test_execution_concurrency_four_and_cached_resume(_schedule: Any) -> None:
    """Prove four overlapping calls and zero repeat dispatch after ledger replay.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    """
    reduced = _small_schedule(_schedule)
    session = _session(schedule=reduced)
    active = 0
    maximum = 0
    calls = []

    async def _exercise() -> None:
        """Record the injected timing or model callback without external effects."""
        barrier = asyncio.Event()

        class _Transport:
            """Offline transport returning controlled outcomes for scheduled requests."""

            settings = reduced.judge

            async def judge(self, prompt: Any) -> JudgeReply:
                """Return a controlled response for the current scheduled request.

                Parameters
                ----------
                prompt
                    Rendered request whose identity the fake must preserve.

                Returns
                -------
                JudgeReply
                    Constructed offline material for the stated test contract.
                """
                nonlocal active, maximum
                calls.append(prompt.request_id)
                active += 1
                maximum = max(maximum, active)
                if active == 4:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), timeout=2)
                active -= 1
                return _reply(prompt)

        transport = _Transport()
        execution = judge._Execution(
            check_material=lambda: None,
            session=session,
            sleep=asyncio.sleep,
            transport=transport,
        )
        cache = await execution.run()
        replay = judge.EvaluationSession(
            connection=session._connection, events=cache.events, schedule=reduced
        )
        resumed = judge._Execution(
            check_material=lambda: None,
            session=replay,
            sleep=asyncio.sleep,
            transport=transport,
        )
        assert await resumed.run() == cache

    try:
        asyncio.run(_exercise())
        assert maximum == 4
        assert len(calls) == len(set(calls)) == 8
    finally:
        session._connection.close()


def test_execution_failure_drains_active_calls_without_new_admission(
    _schedule: Any,
) -> None:
    """Retain in-flight successes and stop admission when one call fails permanently.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    """
    reduced = _small_schedule(_schedule)
    session = _session(schedule=reduced)
    calls = []

    async def _exercise() -> None:
        """Record the injected timing or model callback without external effects."""
        all_started = asyncio.Event()
        failed = asyncio.Event()

        class _Transport:
            """Offline transport returning controlled outcomes for scheduled requests."""

            settings = reduced.judge

            async def judge(self, prompt: Any) -> JudgeReply:
                """Return a controlled response for the current scheduled request.

                Parameters
                ----------
                prompt
                    Rendered request whose identity the fake must preserve.

                Returns
                -------
                JudgeReply
                    Constructed offline material for the stated test contract.
                """
                calls.append(prompt.request_id)
                position = len(calls)
                if position == 4:
                    all_started.set()
                await asyncio.wait_for(all_started.wait(), timeout=2)
                if position == 1:
                    failed.set()
                    raise judge.JudgeCallError(
                        category="authentication", message="Synthetic permanent failure"
                    )
                await asyncio.wait_for(failed.wait(), timeout=2)
                return _reply(prompt)

        execution = judge._Execution(
            check_material=lambda: None,
            session=session,
            sleep=asyncio.sleep,
            transport=_Transport(),
        )
        with pytest.raises(judge.JudgeExecutionError):
            await execution.run()

    try:
        asyncio.run(_exercise())
        assert len(calls) == 4
        assert len(session.snapshot().judgments) == 3
        assert not session.snapshot().unfinished
    finally:
        session._connection.close()


@pytest.mark.parametrize(
    "category,retryable",
    [
        ("rate_limit", True),
        ("server_error", True),
        ("timeout", True),
        ("invalid_output", True),
        ("authentication", False),
        ("configuration", False),
        ("invalid_input", False),
    ],
)
def test_execution_finite_attempts_waits_and_usage(
    _schedule: Any, category: str, retryable: bool
) -> None:
    """Count retryable failures inside one three-attempt allowance with known usage.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    category
        Injected transport or output failure category.
    retryable
        Whether the approved finite retry contract permits another attempt.
    """
    reduced = _small_schedule(_schedule)
    reduced = replace(
        reduced,
        curricula=(
            replace(reduced.curricula[0], requests=reduced.curricula[0].requests[:1]),
        ),
        total_requests=1,
    )
    session = _session(schedule=reduced)
    calls = []
    waits = []

    class _Transport:
        """Offline transport returning controlled outcomes for scheduled requests."""

        settings = reduced.judge

        async def judge(self, prompt: Any) -> JudgeReply:
            """Return a controlled response for the current scheduled request.

            Parameters
            ----------
            prompt
                Rendered request whose identity the fake must preserve.

            Returns
            -------
            JudgeReply
                Constructed offline material for the stated test contract.
            """
            calls.append(prompt.request_id)
            if category == "invalid_output":
                return JudgeReply(response_json="{}", usage=JudgeUsage(input_tokens=11))
            raise judge.JudgeCallError(
                category=category,
                message="Injected failure",
                usage=JudgeUsage(input_tokens=11),
            )

    async def _sleep(delay: float) -> None:
        """Record the injected timing or model callback without external effects.

        Parameters
        ----------
        delay
            Requested retry delay or attempt timeout in seconds.
        """
        waits.append(delay)

    try:
        execution = judge._Execution(
            check_material=lambda: None,
            session=session,
            sleep=_sleep,
            transport=_Transport(),
        )
        with pytest.raises(judge.JudgeExecutionError):
            asyncio.run(execution.run())
        assert len(calls) == (3 if retryable else 1)
        assert waits == ([5, 20] if retryable else [])
        failures = [e for e in session.snapshot().events if e.event == "failed"]
        assert len(failures) == len(calls)
        assert sum(e.usage.input_tokens for e in failures) == len(calls) * 11
        assert all(e.usage.output_tokens is None for e in failures)
        assert not session.snapshot().judgments
    finally:
        session._connection.close()


def test_execution_timeout_is_applied_per_attempt(
    _schedule: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observe the real asynchronous timeout boundary without waiting three minutes.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    monkeypatch
        Restoring environment, model, or filesystem seam substitutions.
    """
    reduced = _small_schedule(_schedule)
    reduced = replace(
        reduced,
        curricula=(
            replace(reduced.curricula[0], requests=reduced.curricula[0].requests[:1]),
        ),
        total_requests=1,
    )
    session = _session(schedule=reduced)
    observed = []
    timeout = asyncio.timeout

    def _timeout(delay: float) -> Any:
        """Record the injected timing or model callback without external effects.

        Parameters
        ----------
        delay
            Requested retry delay or attempt timeout in seconds.

        Returns
        -------
        Any
            Constructed offline material for the stated test contract.
        """
        observed.append(delay)
        return timeout(0)

    class _Transport:
        """Offline transport returning controlled outcomes for scheduled requests."""

        settings = reduced.judge

        async def judge(self, prompt: Any) -> JudgeReply:
            """Return a controlled response for the current scheduled request.

            Parameters
            ----------
            prompt
                Rendered request whose identity the fake must preserve.

            """
            await asyncio.Future()
            raise AssertionError(prompt.request_id)

    async def _sleep(delay: float) -> None:
        """Record the injected timing or model callback without external effects.

        Parameters
        ----------
        delay
            Requested retry delay or attempt timeout in seconds.
        """
        assert delay in (5, 20)

    monkeypatch.setattr(name="timeout", target=judge.asyncio, value=_timeout)
    try:
        with pytest.raises(judge.JudgeExecutionError):
            asyncio.run(
                judge._Execution(
                    check_material=lambda: None,
                    session=session,
                    sleep=_sleep,
                    transport=_Transport(),
                ).run()
            )
        assert observed == [180, 180, 180]
        assert [
            e.failure_category for e in session.snapshot().events if e.event == "failed"
        ] == ["timeout"] * 3
    finally:
        session._connection.close()


def test_frozen_input_full_validation_and_byte_preservation(
    _frozen: FrozenInputs,
) -> None:
    """Revalidate current synthetic snapshots without production resume.

    Parameters
    ----------
    _frozen
        Authenticated synthetic multi-curriculum manifest reference.
    """
    snapshots = sampling.load_frozen_lp_inputs(_frozen)
    before = {
        a.fingerprint.path: hashlib.sha256(a.fingerprint.path.read_bytes()).hexdigest()
        for s in snapshots
        for a in s.artifacts
    }
    for snapshot in snapshots:
        validated = sampling.validate_lp_snapshot(snapshot.run)
        assert validated.config_json == snapshot.config_json
        assert validated.checkpoint_format == snapshot.checkpoint_format
        assert {a.name: a.fingerprint.sha256 for a in validated.artifacts} == {
            a.name: a.fingerprint.sha256 for a in snapshot.artifacts
        }
    assert len(snapshots) == 3
    assert Counter(s.checkpoint_format for s in snapshots) == {
        "journal_bearing": 3,
    }
    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before
    }


@pytest.mark.parametrize(
    "mutation", ["changed", "missing", "appeared", "lock_appeared"]
)
def test_frozen_source_detects_changed_missing_and_new_material(
    mutation: str, tmp_path: Path
) -> None:
    """Check selected-source byte and absence bindings on isolated synthetic evidence.

    Parameters
    ----------
    mutation
        Deliberate corruption applied only to isolated test material.
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    # Package Library
    from kgfeg.evals.lp_eval.schemas import FrozenArtifact, FrozenSnapshot

    directory = tmp_path / "kgs"
    _run_metadata(directory=directory)
    path = directory / "material.json"
    path.write_bytes(b"original")
    inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "out", results_root=tmp_path
    )
    snapshot = FrozenSnapshot(
        absent_artifacts=(".lp_generation.lock", "later.json"),
        artifacts=(
            FrozenArtifact(
                fingerprint=FileFingerprint(
                    path=path,
                    sha256=hashlib.sha256(b"original").hexdigest(),
                    size_bytes=8,
                ),
                name="material.json",
            ),
        ),
        checkpoint_format="journal_bearing",
        config_json="{}",
        run=inventory.runs[0],
        source_artifact="material.json",
    )
    sampling._frozen_source_check(snapshot)
    if mutation == "changed":
        path.write_bytes(b"tampered")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "lock_appeared":
        (directory / ".lp_generation.lock").touch()
    else:
        (directory / "later.json").write_text("new")
    with pytest.raises((OSError, ValueError)):
        sampling._frozen_source_check(snapshot)


@pytest.mark.parametrize("mutation", ["mode", "schema", "sdk", "source"])
def test_incompatible_store_is_rejected_without_evidence_writes(
    _schedule: Any, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """Preserve a frozen invocation when runtime output or source material changes.

    Parameters
    ----------
    _schedule
        Complete synthetic schedule with authenticated input material.
    monkeypatch
        Restoring simulated runtime material changes.
    mutation
        Output or implementation identity changed after freezing.
    """
    frozen_manifest = json.loads(_schedule.inputs.manifest_path.read_text())
    repository = Path(frozen_manifest["inventory"]["evaluation_root"]).parents[1]
    reference = judge.persist_evaluation_schedule(
        repository_root=repository, schedule=_schedule
    )
    directory = reference.manifest_path.parent
    before = {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }
    assert any(
        item.path.name == "output.py" for item in _schedule.implementation_fingerprints
    )
    if mutation == "source":
        monkeypatch.setattr(
            name="_schedule_implementation", target=judge, value=lambda: ()
        )
        expected = "implementation"
    else:
        contract = json.loads(_schedule.judge.output_contract_json)
        if mutation == "mode":
            contract["mode"] = "tool"
        elif mutation == "schema":
            contract["schemas"]["classification"]["schema"]["required"].append(
                "new_field"
            )
        else:
            contract["sdk_versions"]["anthropic"] = "new-sdk-version"
        monkeypatch.setattr(
            name="judge_output_contract",
            target=sampling,
            value=lambda config: _dump(contract),
        )
        expected = "structured output contract"
    with pytest.raises(ValueError, match=expected):
        with judge.open_evaluation_store(reference):
            raise AssertionError("Incompatible frozen invocation was opened.")
    after = {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_incomplete_cache_cannot_report_completion(
    _report_inputs: Any, _schedule: Any
) -> None:
    """Expose every unsatisfied scheduled judgment without turning it into ambiguity.

    Parameters
    ----------
    _report_inputs
        Frozen population and lexical scoring inputs.
    _schedule
        Complete default development request schedule.
    """
    report = scoring.score_evaluation(
        cache=EvaluationCache(events=(), judgments=(), unfinished=()),
        inputs=_report_inputs,
        schedule=_schedule,
    )
    material = json.loads(report.report_json)
    assert not material["execution_complete"]
    assert material["missing_judgments"] == _EXPECTED_REQUESTS
    assert material["valid_judgments"] == 0
    assert all(metric["ambiguous"] == 0 for metric in material["metrics"])
    assert len(report.failures_jsonl.splitlines()) == _EXPECTED_REQUESTS


def test_independent_selection_is_unchanged_by_production_join(_schedule: Any) -> None:
    """Change only reporting metadata and preserve every independent request and answer.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    """
    curriculum = _schedule.curricula[0]
    requests = tuple(r for r in curriculum.requests if r.component == "independent")
    altered = replace(
        curriculum,
        production_population=replace(curriculum.production_population, pairs=()),
    )
    changed_schedule = replace(
        _schedule, curricula=(altered,) + _schedule.curricula[1:]
    )
    assert (
        tuple(r for r in altered.requests if r.component == "independent") == requests
    )
    session = _session(schedule=_schedule)
    try:
        request = next(
            r
            for r in requests
            if r.role == "base"
            and any(
                p.pair.pair_id == r.evidence.pair_id
                for p in curriculum.production_population.pairs
            )
        )
        attempt = session.start_attempt(request.prompt.request_id)
        reply = _reply(request.prompt)
        session.record_success(
            attempt=attempt, response_json=reply.response_json, usage=reply.usage
        )
        before = scoring._rows(cache=session.snapshot(), schedule=_schedule)
        after = scoring._rows(cache=session.snapshot(), schedule=changed_schedule)
        left = next(r for r in before if r["request_id"] == request.prompt.request_id)
        right = next(r for r in after if r["request_id"] == request.prompt.request_id)
        assert left["production"] is not None and right["production"] is None
        assert {key: value for key, value in left.items() if key != "production"} == {
            key: value for key, value in right.items() if key != "production"
        }
    finally:
        session._connection.close()


def test_judge_missing_setting_uses_default_without_changing_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted evaluator setting uses its default independently of production.

    Parameters
    ----------
    monkeypatch
        Restoring environment, model, or filesystem seam substitutions.
    """
    monkeypatch.delenv(name="LLM_LP_EVAL_JUDGE_MODEL", raising=False)
    settings = BackendSettings(
        _env_file=None, PATHS_PROJECT_DIR=_ROOT, LLM_KG_MODEL="openai:gpt-5.2"
    )
    assert settings.llm_config("kgs").model == "openai:gpt-5.2"
    resolved = judge.resolve_judge_settings(settings)
    assert resolved.model == "anthropic:claude-opus-5"
    judge.validate_judge_settings(resolved)
    assert settings.llm_config("kgs").model == "openai:gpt-5.2"


@pytest.mark.parametrize("model", ["", " ", "unknown:made-up", "not-a-provider-model"])
def test_judge_unavailable_configuration_has_no_fallback(model: str) -> None:
    """Unsupported or blank judge settings cannot resolve another model.

    Parameters
    ----------
    model
        Explicit synthetic judge model setting.
    """
    settings = BackendSettings(
        _env_file=None, PATHS_PROJECT_DIR=_ROOT, LLM_LP_EVAL_JUDGE_MODEL=model
    )
    with pytest.raises((ValueError, KeyError)):
        judge.validate_judge_settings(judge.resolve_judge_settings(settings))


def test_judge_uses_independent_binding_and_shared_settings() -> None:
    """Keep production and evaluator models separate while inheriting registry knobs."""
    settings = BackendSettings(
        _env_file=None,
        PATHS_PROJECT_DIR=_ROOT,
        LLM_KG_MODEL="openai:gpt-5.2",
        LLM_LC_EVAL_JUDGE_MODEL="openai:gpt-5.2",
        LLM_LP_EVAL_JUDGE_MODEL="anthropic:claude-opus-5",
        LLM_MAX_OUTPUT_TOKENS=9000,
    )
    resolved = judge.resolve_judge_settings(settings)
    assert resolved.model == "anthropic:claude-opus-5"
    assert json.loads(resolved.model_settings_json) == dict(
        settings.llm_config("lp_eval_judge").kgs_settings("learning_progressions")
    )
    assert json.loads(resolved.execution_json) == {
        "attempt_timeout_seconds": 180,
        "concurrency": 4,
        "max_retries": 2,
        "retry_waits_seconds": [5, 20],
        "sdk_max_retries": 0,
    }
    assert settings.llm_config("kgs").model == "openai:gpt-5.2"


def test_ledger_crash_keeps_success_and_blocks_uncertain_attempt(
    _schedule: Any, tmp_path: Path
) -> None:
    """Reopen committed success plus a start-only crash without duplicating dispatch.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    path = tmp_path / "attempts.sqlite3"
    session = _session(path=str(path), schedule=_schedule)
    requests = [r for r in _schedule.curricula[0].requests if r.role == "base"][:2]
    attempt = session.start_attempt(requests[0].prompt.request_id)
    reply = _reply(requests[0].prompt)
    session.record_success(
        attempt=attempt, response_json=reply.response_json, usage=reply.usage
    )
    session.start_attempt(requests[1].prompt.request_id)
    saved = session.snapshot()
    session._connection.close()
    with sqlite3.connect(path) as connection:
        events = judge._database_events(
            connection=connection, schedule_hash=_schedule.material_content_hash
        )
        replay = judge.EvaluationSession(
            connection=connection, events=events, schedule=_schedule
        )
        assert replay.snapshot() == saved
        assert len(saved.judgments) == len(saved.unfinished) == 1
        with pytest.raises(ValueError):
            replay.start_attempt(requests[0].prompt.request_id)
        with pytest.raises(ValueError):
            replay.start_attempt(requests[1].prompt.request_id)


def test_ledger_dependency_duplicate_and_durable_replay(
    _schedule: Any, tmp_path: Path
) -> None:
    """Require blind completion before critique and preserve validated successes on reopen.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    session = _session(path=str(tmp_path / "ledger.sqlite3"), schedule=_schedule)
    try:
        critique = next(
            r for r in _schedule.curricula[0].requests if r.role == "critique"
        )
        with pytest.raises(ValueError):
            session.start_attempt(critique.prompt.request_id)
        requests = {
            r.prompt.request_id: r for c in _schedule.curricula for r in c.requests
        }
        for identifier in critique.dependencies:
            attempt = session.start_attempt(identifier)
            reply = _reply(requests[identifier].prompt)
            session.record_success(
                attempt=attempt, response_json=reply.response_json, usage=reply.usage
            )
        attempt = session.start_attempt(critique.prompt.request_id)
        reply = _reply(critique.prompt)
        session.record_success(
            attempt=attempt, response_json=reply.response_json, usage=reply.usage
        )
        with pytest.raises(ValueError):
            session.start_attempt(critique.prompt.request_id)
        saved = session.snapshot()
        events = judge._database_events(
            connection=session._connection,
            schedule_hash=_schedule.material_content_hash,
        )
        replay = judge.EvaluationSession(
            connection=session._connection, events=events, schedule=_schedule
        )
        assert replay.snapshot() == saved
    finally:
        session._connection.close()


@pytest.mark.parametrize("mutation", ["delete", "alter", "extra", "head"])
def test_ledger_tamper_fails_closed(_schedule: Any, mutation: str) -> None:
    """Reject truncated, changed, extra or unsealed durable records.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    mutation
        Deliberate corruption applied only to isolated test material.
    """
    session = _session(schedule=_schedule)
    try:
        request = _schedule.curricula[0].requests[0]
        attempt = session.start_attempt(request.prompt.request_id)
        reply = _reply(request.prompt)
        session.record_success(
            attempt=attempt, response_json=reply.response_json, usage=reply.usage
        )
        if mutation == "delete":
            session._connection.execute("DELETE FROM events WHERE sequence = 2")
        elif mutation == "alter":
            session._connection.execute(
                "UPDATE events SET payload = '{}' WHERE sequence = 2"
            )
        elif mutation == "extra":
            session._connection.execute(
                "INSERT INTO events SELECT 3, payload, content_hash FROM events WHERE sequence = 2"
            )
        else:
            session._connection.execute(
                "UPDATE metadata SET value = '{}' WHERE key = 'head'"
            )
        with pytest.raises(ValueError):
            judge._database_events(
                connection=session._connection,
                schedule_hash=_schedule.material_content_hash,
            )
    finally:
        session._connection.close()


def test_local_provider_uses_one_function_model_call(_schedule: Any) -> None:
    """Exercise the actual Pydantic AI adapter offline, including malformed output.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    """
    # Third Party Library
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    prompt = _schedule.curricula[0].requests[0].prompt
    calls = []

    def _model(messages: Any, info: Any) -> Any:
        """Record the injected timing or model callback without external effects.

        Parameters
        ----------
        info
            Pydantic AI callback metadata supplied to the local model.
        messages
            Fresh messages delivered to the local model callback.

        Returns
        -------
        Any
            Constructed offline material for the stated test contract.
        """
        calls.append((messages, info))
        return ModelResponse(parts=[TextPart(content="{}")], finish_reason="stop")

    transport = judge._ProviderJudge(
        model=FunctionModel(function=_model), settings=_schedule.judge
    )
    with pytest.raises(judge.JudgeCallError) as failure:
        asyncio.run(transport.judge(prompt))
    assert failure.value.category == "invalid_output"
    assert failure.value.raw_response == "{}"
    assert len(calls) == 1
    assert failure.value.usage.input_tokens is not None
    assert "request_id: missing" in str(failure.value)


def test_nomination_coverage_positive_denominator_is_condition_specific(
    _report_inputs: Any, _schedule: Any
) -> None:
    """Derive coverage from sampled positives, excluding ambiguity and real negatives.

    Parameters
    ----------
    _report_inputs
        Frozen population and lexical scoring inputs.
    _schedule
        Complete default development request schedule.
    """
    session = _session(schedule=_schedule)
    selected = [
        r
        for r in _schedule.curricula[0].requests
        if r.component == "independent" and r.role == "base"
    ][:4]
    try:
        for index, request in enumerate(selected):
            response = json.loads(_reply(request.prompt).response_json)
            response["decision"] = [
                "relatesTo",
                "relatesTo",
                "no_relation",
                "ambiguous",
            ][index]
            attempt = session.start_attempt(request.prompt.request_id)
            session.record_success(
                attempt=attempt, response_json=_dump(response), usage=JudgeUsage()
            )
        report = json.loads(
            scoring.score_evaluation(
                cache=session.snapshot(),
                inputs=_report_inputs,
                schedule=_schedule,
            ).report_json
        )
        metric = next(
            m
            for m in report["metrics"]
            if m["dimensions"]["doc_key"] == _schedule.curricula[0].doc_key
            and m["dimensions"]["component"] == "independent"
            and m["dimensions"]["condition"] == "reconstructed_bounded_upstream"
            and m["dimensions"]["replicate"] == 1
            and m["dimensions"]["stratum_kind"] == "selected_union"
        )
        nominated = {
            p.pair.pair_id for p in _schedule.curricula[0].production_population.pairs
        }
        rate = metric["sample_conditioned_coverage"]["nominated"]
        assert rate["denominator"] == 2
        assert rate["numerator"] == sum(
            r.evidence.pair_id in nominated for r in selected[:2]
        )
        assert metric["ambiguous"] == 1
    finally:
        session._connection.close()


def test_prepare_resume_does_not_rediscover_new_inputs(
    _schedule: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise orchestration with a frozen selection and newly completed discovery candidate.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    monkeypatch
        Restoring environment, model, or filesystem seam substitutions.
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    # Package Library
    from kgfeg.evals.lp_eval.schemas import EvaluationStore

    reference = EvaluationStore(
        content_hash="a" * 64, manifest_path=tmp_path / "manifest.json"
    )
    _run_metadata(directory=tmp_path / "new/kgs", identity=99)
    monkeypatch.setattr(
        name="resolve_judge_settings", target=entry, value=lambda: _schedule.judge
    )
    monkeypatch.setattr(
        name="find_evaluation_store", target=entry, value=lambda **kwargs: reference
    )
    resume = Mock(return_value=reference)
    monkeypatch.setattr(name="_resume_selection", target=entry, value=resume)
    discover = Mock(side_effect=AssertionError("Frozen resume must not rediscover"))
    monkeypatch.setattr(name="discover_lp_runs", target=entry, value=discover)
    assert (
        entry.prepare_evaluation(repository_root=tmp_path, results_root=tmp_path)
        == reference
    )
    resume.assert_called_once()
    discover.assert_not_called()


def test_production_sampling_counts_probabilities_supplement_credit_and_order() -> None:
    """Reconcile each cell independently and ensure supplements use prior selections."""
    population = _production_population()
    settings = EvaluationSettings(
        production_pairs_per_outcome=2, production_examples_per_tag=3
    )
    plan = sampling.sample_production_pairs(population=population, settings=settings)
    again = sampling.sample_production_pairs(
        population=replace(population, pairs=tuple(reversed(population.pairs))),
        settings=settings,
    )
    assert plan == again
    assert len(plan.cells) == 16
    selected: set[str] = set()
    for cell in plan.cells:
        assert len(set(cell.drawn_pair_ids)) == len(cell.drawn_pair_ids)
        if "/outcome/" in cell.route:
            assert len(cell.drawn_pair_ids) == 2
            assert cell.population_count == 10
            assert cell.inclusion_probability == (1, 5)
        else:
            candidates = {
                p.pair.pair_id
                for p in population.pairs
                if cell.route.split("/")[-1] in p.tags
            }
            prior = candidates & selected
            assert len(cell.drawn_pair_ids) == min(
                len(candidates - selected), max(0, 3 - len(prior))
            )
            assert cell.inclusion_probability is None
            assert set(cell.selected_pair_ids) == prior | set(cell.drawn_pair_ids)
        assert cell.shortfall == max(0, cell.target - len(cell.selected_pair_ids))
        selected.update(cell.drawn_pair_ids)
    assert selected == {p.pair.pair_id for p in plan.pairs}
    for pair in plan.pairs:
        assert set(pair.routes) == {
            c.route for c in plan.cells if pair.pair.pair_id in c.selected_pair_ids
        }


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("status", [429, 500])
def test_provider_sdk_does_not_multiply_attempts(
    _schedule: Any, monkeypatch: pytest.MonkeyPatch, provider: str, status: int
) -> None:
    """Count actual SDK HTTP attempts under a local response transport.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    monkeypatch
        Restoring fake credential and HTTP transport substitutions.
    provider
        SDK provider whose retry policy is exercised.
    status
        Retryable local HTTP response code.
    """
    # Third Party Library
    import httpx

    calls: list[str] = []
    model = "anthropic:claude-opus-5" if provider == "anthropic" else "openai:gpt-5.2"
    resolved = judge.resolve_judge_settings(
        BackendSettings(
            _env_file=None, PATHS_PROJECT_DIR=_ROOT, LLM_LP_EVAL_JUDGE_MODEL=model
        )
    )
    monkeypatch.setenv(
        name="ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY",
        value="offline-synthetic-key",
    )

    async def _handler(request: Any) -> Any:
        """Return a local failure without opening any socket.

        Parameters
        ----------
        request
            HTTP request built by the actual SDK.

        Returns
        -------
        Any
            Synthetic retryable provider response.
        """
        calls.append(str(request.url))
        return httpx.Response(
            status_code=status,
            json={
                "type": "error",
                "error": {"type": "rate_limit_error", "message": "offline failure"},
            },
        )

    def _transport(**kwargs: Any) -> Any:
        """Verify HTTP retry configuration and install the local response handler.

        Parameters
        ----------
        kwargs
            Transport construction options.

        Returns
        -------
        Any
            Socket-free HTTP transport.
        """
        assert kwargs["retries"] == 0
        return httpx.MockTransport(handler=_handler)

    async def _exercise() -> None:
        """Exercise one actual SDK attempt through the provider adapter."""
        async with judge.open_judge_transport(resolved) as transport:
            with pytest.raises(judge.JudgeCallError):
                await transport.judge(_schedule.curricula[0].requests[0].prompt)

    monkeypatch.setattr(name="AsyncHTTPTransport", target=httpx, value=_transport)
    asyncio.run(_exercise())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "request",
        "pair",
        "endpoint",
        "evidence",
        "direction",
        "confidence",
        "duplicate_key",
    ],
)
def test_response_identity_schema_and_reference_rejections(
    _schedule: Any, mutation: str
) -> None:
    """Reject malformed and stale responses before they become successful judgments.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    mutation
        Deliberate corruption applied only to isolated test material.
    """
    request = _schedule.curricula[0].requests[0]
    material = json.loads(_reply(request.prompt).response_json)
    if mutation == "extra":
        material["extra"] = True
    elif mutation == "missing":
        del material["decision"]
    elif mutation == "request":
        material["request_id"] = "wrong"
    elif mutation == "pair":
        material["pair_id"] = "wrong"
    elif mutation == "endpoint":
        material["first_sfi_uuid"] = str(UUID(int=2**128 - 1))
        assert material["first_sfi_uuid"] not in {
            str(uid) for uid in request.canonical_endpoint_uuids
        }
    elif mutation == "evidence":
        material["evidence_references"] = ["/hidden"]
    elif mutation == "direction":
        material["direction"] = "first_to_second"
    elif mutation == "confidence":
        material["confidence"] = 1.01
    response = _dump(material)
    if mutation == "duplicate_key":
        response = response[:-1] + ',"decision":"no_relation"}'
    with pytest.raises(ValueError):
        judge.validate_judge_response(request=request, response_json=response)


def test_schedule_counts_overlap_repetitions_and_identities(_schedule: Any) -> None:
    """Independently derive the complete schedule from selected cohorts and defaults.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    """
    requests = [r for c in _schedule.curricula for r in c.requests]
    assert len(requests) == _schedule.total_requests == _EXPECTED_REQUESTS
    assert sorted(len(c.requests) for c in _schedule.curricula) == [245, 245, 288]
    material = TypeAdapter(EvaluationSchedule).dump_python(_schedule, mode="json")
    recorded_hash = material.pop("material_content_hash")
    assert hashlib.sha256(_dump(material).encode()).hexdigest() == recorded_hash
    implementation = material["implementation_fingerprints"]
    implementation_hash = hashlib.sha256(_dump(implementation).encode()).hexdigest()
    expected_paths = set((_ROOT / "backend/src/kgfeg").rglob("*.py")) - {
        _ROOT / "backend/src/kgfeg/evals/lp_eval/scoring.py"
    }
    assert {Path(item["path"]) for item in implementation} == expected_paths
    for item in implementation:
        payload = Path(item["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        assert len(payload) == item["size_bytes"]
    for request in requests:
        identity = {
            "canonical_endpoint_uuids": [
                str(x) for x in request.canonical_endpoint_uuids
            ],
            "component": request.component,
            "evidence_content_hash": request.evidence.material_content_hash,
            "implementation_hash": implementation_hash,
            "judge": material["judge"],
            "replicate": request.replicate,
            "role": request.role,
            "settings": material["settings"]["settings"],
        }
        assert (
            hashlib.sha256(_dump(identity).encode()).hexdigest()
            == request.prompt.request_id
        )
    assert len({r.prompt.request_id for r in requests}) == len(requests)
    for curriculum in _schedule.curricula:
        p = len(curriculum.production_sample.pairs)
        i = len(curriculum.independent_sample.pairs)
        assert (
            len(curriculum.requests) == 2 * p + i + 7 * (min(p, 12) + min(i, 12)) + 75
        )
        overlap = {x.pair.pair_id for x in curriculum.production_sample.pairs} & {
            x.pair.pair_id for x in curriculum.independent_sample.pairs
        }
        for pair in overlap:
            base = [
                r
                for r in curriculum.requests
                if r.evidence.pair_id == pair and r.role == "base"
            ]
            assert {r.component for r in base} == {"production", "independent"}
            assert len(base) == 2
            assert len({r.evidence.condition for r in base}) == 2
        seen: set[str] = set()
        for request in curriculum.requests:
            assert set(request.dependencies) <= seen
            seen.add(request.prompt.request_id)
            if request.component == "independent" and request.role == "base":
                assert request.evidence.condition == "reconstructed_bounded_upstream"
            if request.role == "critique":
                assert request.dependencies
                assert request.evidence.condition == "original_production_critique"
                assert (
                    "Synthetic uncertainty retained" not in request.prompt.user_message
                )


def test_schedule_runs_without_git_and_binds_source_changes(
    _schedule: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validate material identities while Git queries are unavailable.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    monkeypatch
        Restoring environment, model, or filesystem seam substitutions.
    """
    guard = Mock(side_effect=AssertionError("Evaluator must not query Git"))
    monkeypatch.setattr(name="run", target=subprocess, value=guard)
    monkeypatch.setattr(name="check_output", target=subprocess, value=guard)
    monkeypatch.setattr(name="Popen", target=subprocess, value=guard)
    # The full schedule was prepared independently; a changed source binding fails first.
    changed = replace(_schedule, implementation_fingerprints=())
    with pytest.raises(ValueError, match="implementation"):
        judge._validate_schedule(changed)
    source = _source()
    sampling.sample_independent_pairs(
        population=sampling.build_admissible_population(source),
        settings=EvaluationSettings(),
    )
    guard.assert_not_called()


def test_settings_defaults_overrides_and_repetition_boundary() -> None:
    """Pin all approved defaults and record explicit overrides, including equal values."""
    assert EvaluationSettings().model_dump() == {
        "production_pairs_per_outcome": 15,
        "production_examples_per_tag": 2,
        "independent_uniform_pairs": 36,
        "independent_pairs_per_tag": 3,
        "sampling_seed": 20260911,
        "base_blind_replicates": 1,
        "critique_replicates": 1,
        "diagnostic_pairs_per_cohort": 12,
        "additional_diagnostic_replicates": 2,
        "variant_replicates": 1,
        "synthetic_cases_per_family": 5,
        "synthetic_control_replicates": 3,
        "lexical_baseline_top_k": 10,
    }
    assert resolve_evaluation_settings({"sampling_seed": -7}).overrides == (
        "sampling_seed",
    )
    assert (
        resolve_evaluation_settings(
            {"base_blind_replicates": 2, "additional_diagnostic_replicates": 0}
        ).settings.base_blind_replicates
        == 2
    )
    with pytest.raises(ValidationError):
        resolve_evaluation_settings({"additional_diagnostic_replicates": 0})
    for forbidden in ("budget", "max_total_tokens", "max_api_attempts", "offline"):
        with pytest.raises(ValidationError):
            resolve_evaluation_settings({forbidden: 1})


@pytest.mark.parametrize(
    "field",
    [
        name
        for name in dict(EvaluationSettings.model_fields.items())
        if name not in {"sampling_seed", "additional_diagnostic_replicates"}
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1", None])
def test_settings_reject_invalid_counts(field: str, value: Any) -> None:
    """Disallow silently disabled components and coerced operational counts.

    Parameters
    ----------
    field
        Operational setting under validation.
    value
        Synthetic material or setting value under examination.
    """
    with pytest.raises(ValidationError):
        resolve_evaluation_settings({field: value})


@pytest.mark.parametrize("checkpoint_format", ["journal_bearing"])
@pytest.mark.parametrize(
    "mutation",
    ["source", "configuration", "response", "projection", "journal", "missing"],
)
def test_snapshot_integrity_rejects_tampered_test_artifacts(
    _frozen: FrozenInputs, checkpoint_format: str, mutation: str, tmp_path: Path
) -> None:
    """Require real validation to reject corrupt current-format copies.

    Parameters
    ----------
    _frozen
        Authenticated current synthetic snapshots.
    checkpoint_format
        Format whose full source/config/checkpoint bindings are challenged.
    mutation
        Independently selected corruption, never applied to production evidence.
    tmp_path
        Isolated attack copy preserving the untouched module fixtures.
    """
    snapshot = next(
        item
        for item in sampling.load_frozen_lp_inputs(_frozen)
        if item.checkpoint_format == checkpoint_format
    )
    copied = tmp_path / "source"
    shutil.copytree(snapshot.run.kgs_directory.parent, copied)
    directory = copied / "kgs"
    if mutation == "source":
        path = copied / "document_ir.json"
        material = json.loads(path.read_bytes())
        material["doc_key"] = "different-synthetic-source"
        path.write_text(_dump(material))
    elif mutation == "configuration":
        path = directory / "kg_run.json"
        material = json.loads(path.read_bytes())
        material["extra"]["lp"]["producer_instructions"] += " Changed."
        path.write_text(_dump(material))
    elif mutation == "response":
        path = directory / "lp_generation_responses.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["payload"]["judgments"][0]["rationale"] = "Changed synthetic rationale."
        path.write_text("".join(_dump(row) + "\n" for row in rows))
    elif mutation == "projection":
        (directory / "as_lc_lp_relationships.jsonl").write_text("")
    elif mutation == "journal":
        (directory / "lp_generation_usage.json").write_text("{}\n")
    else:
        (directory / "lp_generation_draft_responses.jsonl").unlink()
    before = {
        path.relative_to(copied): path.read_bytes()
        for path in copied.rglob("*")
        if path.is_file()
    }
    run = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "eval", results_root=directory
    ).runs[0]
    assert run.status == "completed_candidate"
    with pytest.raises(sampling.LPSnapshotError):
        sampling.validate_lp_snapshot(run)
    assert before == {
        path.relative_to(copied): path.read_bytes()
        for path in copied.rglob("*")
        if path.is_file()
    }


def test_store_excludes_concurrent_writers(tmp_path: Path) -> None:
    """Reject a second writer while the first holds the actual filesystem lock.

    Parameters
    ----------
    tmp_path
        Isolated pytest-owned directory for disposable evidence.
    """
    path = tmp_path / "writer.lock"
    with judge._exclusive_store_lock(path):
        with pytest.raises((ValueError, OSError)):
            with judge._exclusive_store_lock(path):
                pytest.fail("Second writer acquired the same store")


def test_swapped_endpoint_assessment_is_remapped(_schedule: Any) -> None:
    """Remap semantic direction without conflating displayed and canonical UUID order.

    Parameters
    ----------
    _schedule
        Complete default development request schedule.
    """
    request = next(
        r
        for r in _schedule.curricula[0].requests
        if r.evidence.presentation == "endpoint_swapped"
        and any(
            x["decision"] == "buildsTowards"
            for x in json.loads(r.prompt.user_message)["assessment_permissions"]
        )
    )
    material = json.loads(_reply(request.prompt).response_json)
    permission = next(
        x
        for x in json.loads(request.prompt.user_message)["assessment_permissions"]
        if x["decision"] == "buildsTowards"
    )
    material.update(permission)
    assessment = judge.validate_judge_response(
        request=request, response_json=_dump(material)
    )
    canonical = judge.canonicalize_classification(judgment=assessment, request=request)
    assert (
        canonical.first_sfi_uuid,
        canonical.second_sfi_uuid,
    ) == request.canonical_endpoint_uuids
    assert canonical.direction != assessment.direction


@pytest.mark.parametrize("count", [1, 3, 8])
def test_synthetic_controls_generalize_and_hide_expectations(count: int) -> None:
    """Require all five toy control families for each unfamiliar framework.

    Parameters
    ----------
    count
        Synthetic endpoint or framework population size.
    """
    identities = set()
    for index in range(count):
        source = _source(identity=500 + index)
        controls = prompts.build_synthetic_controls(
            settings=EvaluationSettings(), source=source
        )
        assert Counter(c.family for c in controls) == {
            name: 5
            for name in (
                "developmental_extension",
                "nondirectional_coherence",
                "unrelated_concepts",
                "insufficient_or_contradictory_evidence",
                "invented_rationale_evidence",
            )
        }
        assert len(controls) == 25
        for control in controls:
            assert control.pair_id not in identities
            identities.add(control.pair_id)
            render = (
                prompts.render_critique_prompt
                if control.view == "synthetic_critique"
                else prompts.render_classification_prompt
            )
            prompt = render(evidence=control, request_id="a" * 64)
            shown = json.loads(prompt.user_message)
            assert (
                "expectation" not in shown
                and "family" not in shown
                and "construction" not in shown
            )
            expectation = json.loads(control.expectation_json)
            if control.family == "invented_rationale_evidence":
                assert expectation == {"grounding": "unsupported"}
                assert (
                    "40 percentage points"
                    in shown["evidence"]["operative_judgment"]["rationale"]
                )
                assert "no studies" in shown["evidence"]["original_request"]["context"]
            elif control.family == "developmental_extension":
                texts = {
                    row["sfi_uuid"]: row["description"]
                    for row in shown["evidence"]["sfis"]
                }
                target = control.endpoint_uuids[
                    1 if expectation["direction"] == "first_to_second" else 0
                ]
                assert "compose" in texts[str(target)]


@pytest.mark.parametrize(
    "first,second,expected",
    [
        ("Ａ  B", "a b", Fraction(1)),
        ("é e\u0301", "É", Fraction(1)),
        ("a a b", "b c", Fraction(1, 3)),
        ("!", "?", Fraction(0)),
        ("the cat", "the dog", Fraction(1, 3)),
    ],
)
def test_unicode_normalization_and_jaccard(
    expected: Fraction, first: str, second: str
) -> None:
    """Require Unicode set tokens, no stopword filtering, and zero empty similarity.

    Parameters
    ----------
    expected
        Independently calculated token-set similarity.
    first
        First input string for Unicode normalization.
    second
        Second input string for Unicode normalization.
    """
    assert sampling.text_token_jaccard(first=first, second=second) == expected


@pytest.mark.parametrize("count,target", [(2, 36), (7, 5), (15, 36)])
def test_uniform_sampling_exact_probability_shortfalls_and_frozen_evidence(
    count: int, target: int
) -> None:
    """Count the complete independent population and verify exact uniform probabilities.

    Parameters
    ----------
    count
        Synthetic endpoint or framework population size.
    target
        Requested uniform-cohort sample count.
    """
    population = sampling.build_admissible_population(_source(count=count))
    settings = EvaluationSettings(independent_uniform_pairs=target)
    plan = sampling.sample_independent_pairs(population=population, settings=settings)
    size = count * (count - 1) // 2
    cell = plan.cells[0]
    expected = Fraction(min(target, size), size)
    assert population.total_pairs == cell.population_count == size
    assert cell.inclusion_probability == (expected.numerator, expected.denominator)
    assert cell.shortfall == max(0, target - size)
    assert len(cell.drawn_pair_ids) == min(target, size)
    assert len(plan.pairs) <= min(target, size) + 36
    assert all(
        view.condition == "reconstructed_bounded_upstream"
        for view in plan.base_evidence
    )
    assert len(plan.base_evidence) == len(plan.pairs)
    assert plan == sampling.sample_independent_pairs(
        population=population, settings=settings
    )


@pytest.mark.parametrize("profile", _PROFILES)
def test_upstream_evidence_removal_expansion_and_snapshot_immutability(
    profile: str,
) -> None:
    """Remove whole evidence families and double limits without editing inputs.

    Parameters
    ----------
    profile
        Reduced curriculum shape and its explicit policy.
    """
    source = _source(profile=profile)
    population = sampling.build_admissible_population(source)
    builder = sampling.build_upstream_evidence_builder(source)
    for pair in list(population.iter_pairs())[:3]:
        views = {
            condition: builder.build_pair(
                condition=condition,
                first_sfi_uuid=pair.endpoint_uuids[0],
                second_sfi_uuid=pair.endpoint_uuids[1],
            )
            for condition in (
                "reconstructed_bounded_upstream",
                "expanded_upstream",
                "lc_removed",
                "hierarchy_removed",
            )
        }
        audit = json.loads(views["expanded_upstream"].audit_json)
        assert audit["effective_limits"] == {
            key: 2 * value for key, value in audit["production_limits"].items()
        }
        assert len({v.material_content_hash for v in views.values()}) == 4
        for condition in ("lc_removed", "hierarchy_removed"):
            audit = json.loads(views[condition].audit_json)
            assert "removals" in audit
            payload = json.loads(views[condition].payload_json)
            assert payload != json.loads(
                views["reconstructed_bounded_upstream"].payload_json
            )
            forbidden = (
                {
                    "learning_components",
                    "learning_components_content_hash",
                    "omitted_learning_component_count",
                }
                if condition == "lc_removed"
                else {"ancestor_paths", "ancestors", "parent_sfi_uuids"}
            )
            for endpoint in payload["sfis"]:
                assert not forbidden.intersection(endpoint)
                assert "source_evidence" not in endpoint
                assert endpoint["warnings"]
            if condition == "lc_removed":
                for component in json.loads(source.upstream_artifact.payload)[
                    "learning_components"
                ]:
                    assert component["identifier"] not in views[condition].payload_json
        assert (
            hashlib.sha256(source.upstream_artifact.payload).hexdigest()
            == source.upstream_artifact.fingerprint.sha256
        )


def test_user_disposition_authority_cannot_waive_execution() -> None:
    """Reject agent authority and unbound concern records without writing dispositions."""
    # Standard Library
    from datetime import UTC, datetime

    with pytest.raises(ValidationError):
        ConcernDisposition(
            authority="testing agent",
            concern_id="a" * 64,
            disposition="acknowledged",
            rationale="Synthetic",
            report_json_sha256="b" * 64,
            report_markdown_sha256="c" * 64,
            timestamp=datetime.now(UTC),
        )
    assert ReportProvenance(evidence_kind="development") != ReportProvenance(
        evidence_kind="evaluation"
    )
