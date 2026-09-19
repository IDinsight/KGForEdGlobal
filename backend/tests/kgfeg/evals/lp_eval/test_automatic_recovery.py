"""Challenge frozen selection and append-only recovery with isolated offline stores."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import hashlib
import json
import socket
import sqlite3

from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

# Package Library
from kgfeg.entries import evaluate_lps as entry
from kgfeg.evals.lp_eval import judge, sampling, scoring
from kgfeg.evals.lp_eval.schemas import JudgeUsage, ReportProvenance
from tests.fixtures.lp_eval.snapshot_fixtures import build_snapshot
from tests.kgfeg.evals.lp_eval import test_independent_evaluator as support

# Private pytest fixture parameters are intentionally documented.
# pylint: disable=useless-param-doc

_OVERRIDES = {
    "additional_diagnostic_replicates": 1,
    "diagnostic_pairs_per_cohort": 1,
    "independent_pairs_per_tag": 1,
    "independent_uniform_pairs": 1,
    "production_examples_per_tag": 1,
    "production_pairs_per_outcome": 1,
    "synthetic_cases_per_family": 1,
    "synthetic_control_replicates": 1,
}


class _Transport:
    """Return synthetic responses while exposing exact attempted request identities."""

    def __init__(self, *, failures: list[str], schedule: Any) -> None:
        """Capture an explicit error script and frozen settings.

        Parameters
        ----------
        failures
            Errors consumed for one selected request, then valid responses.
        schedule
            Frozen schedule supplying real prompt identities and settings.
        """
        self.calls: list[str] = []
        self.failures = list(failures)
        self.preflights = 0
        self.settings = schedule.judge
        self.target = schedule.curricula[0].requests[0].prompt.request_id

    async def judge(self, prompt: Any) -> Any:
        """Record dispatch and return one controlled result.

        Parameters
        ----------
        prompt
            Authenticated scheduled prompt.

        Returns
        -------
        Any
            Valid synthetic response with known token usage.
        """
        self.calls.append(prompt.request_id)
        if prompt.request_id == self.target and self.failures:
            category = self.failures.pop(0)
            if category == "citation":
                reply = support._reply(prompt)
                raw = json.loads(reply.response_json)
                raw["evidence_references"] = ["not/shown/in/evidence"]
                return replace(reply, response_json=json.dumps(raw))
            raise judge.JudgeCallError(
                category=category,
                message="Synthetic execution error",
                usage=JudgeUsage(input_tokens=11, output_tokens=2),
            )
        return support._reply(prompt)

    async def preflight(self) -> None:
        """Record local validation without contacting a provider."""
        self.preflights += 1


@pytest.fixture
def _invocation(tmp_path: Path) -> Any:
    """Prepare a small complete invocation in a disposable repository.

    Parameters
    ----------
    tmp_path
        Isolated synthetic repository root.

    Returns
    -------
    Any
        Persisted invocation reference with all material identities intact.
    """
    build_snapshot(count=3, identity=74100, root=tmp_path / "source")
    return _prepare(tmp_path)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make accidental network access fail immediately.

    Parameters
    ----------
    monkeypatch
        Restoring socket-boundary substitutions.
    """
    guard = Mock(side_effect=AssertionError("Recovery tests must remain offline"))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _prepare(root: Path) -> Any:
    """Repeat the same command's effective preparation settings.

    Parameters
    ----------
    root
        Test-owned repository.

    Returns
    -------
    Any
        Automatically selected or freshly frozen invocation.
    """
    return entry.prepare_evaluation(
        overrides=_OVERRIDES, repository_root=root, results_root=root / "source"
    )


def _read(reference: Any) -> tuple[Any, Any]:
    """Reopen the actual store and return fully validated immutable state.

    Parameters
    ----------
    reference
        Exact saved invocation.

    Returns
    -------
    tuple[Any, Any]
        Schedule and append-only cache.
    """
    with judge.open_evaluation_store(reference) as session:
        return session.schedule, session.snapshot()


def _run(*, reference: Any, transport: _Transport, waits: list[float]) -> Any:
    """Execute through the public recovery boundary with deterministic waits.

    Parameters
    ----------
    reference
        Exact persisted invocation.
    transport
        Single-attempt offline responder.
    waits
        Observed bounded retry delays, updated in place.

    Returns
    -------
    Any
        Validated completed cache.
    """

    async def _sleep(delay: float) -> None:
        """Record retry delay without waiting.

        Parameters
        ----------
        delay
            Requested retry delay in seconds.
        """
        waits.append(delay)

    return asyncio.run(
        judge.execute_evaluation(reference=reference, sleep=_sleep, transport=transport)
    )


def _state(root: Path) -> dict[str, bytes]:
    """Capture every file byte and directory member without changing evidence.

    Parameters
    ----------
    root
        Test-owned evidence root.

    Returns
    -------
    dict[str, bytes]
        Relative paths and bytes, including empty directories.
    """
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else b""
        for p in root.rglob("*")
    }


def test_automatic_resume_reuses_successes_and_ignores_new_discovery(
    _invocation: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resume frozen work and return completed work without constructing a provider.

    Parameters
    ----------
    _invocation
        Complete isolated store.
    monkeypatch
        Restoring discovery guard.
    tmp_path
        Synthetic repository.
    """
    with judge.open_evaluation_store(_invocation) as session:
        entry._bind_provenance(
            has_attempts=False,
            provenance=ReportProvenance(evidence_kind="development"),
            reference=_invocation,
            schedule=session.schedule,
        )
        request = session.schedule.curricula[0].requests[0]
        attempt = session.start_attempt(request.prompt.request_id)
        reply = support._reply(request.prompt)
        session.record_success(
            attempt=attempt, response_json=reply.response_json, usage=reply.usage
        )
        saved = session.snapshot()
    build_snapshot(count=3, identity=75200, root=tmp_path / "source")
    guard = Mock(
        side_effect=AssertionError(
            "Frozen resume must not rediscover or call providers"
        )
    )
    monkeypatch.setattr(name="discover_lp_runs", target=entry, value=guard)
    alias = tmp_path / "source-alias"
    alias.symlink_to(target=tmp_path / "source", target_is_directory=True)
    resumed = entry.prepare_evaluation(
        overrides=_OVERRIDES, repository_root=tmp_path, results_root=alias
    )
    assert resumed == _invocation
    schedule, _ = _read(resumed)
    assert len(schedule.curricula) == 1
    transport = _Transport(failures=[], schedule=schedule)
    cache = _run(reference=resumed, transport=transport, waits=[])
    assert request.prompt.request_id not in transport.calls
    assert len(transport.calls) == schedule.total_requests - 1
    assert cache.events[: len(saved.events)] == saved.events
    assert all(j in cache.judgments for j in saved.judgments)
    assert _prepare(tmp_path) == _invocation
    result = asyncio.run(
        entry.run_evaluation(
            provenance=ReportProvenance(evidence_kind="development"),
            reference=resumed,
            transport_factory=guard,
        )
    )
    assert result.execution_complete
    before = _state(tmp_path)
    again = asyncio.run(
        entry.run_evaluation(
            provenance=ReportProvenance(evidence_kind="development"),
            reference=_prepare(tmp_path),
            transport_factory=guard,
        )
    )
    assert again.execution_complete
    assert _state(tmp_path) == before
    guard.assert_not_called()


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=["corrupt_schedule", "locked", "stale_source", "unsupported"],
)
def test_bad_matching_store_blocks_without_fresh_discovery(
    _invocation: Any, attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject unsafe matching state before writes, fallback discovery or dispatch.

    Parameters
    ----------
    _invocation
        Isolated store to challenge.
    attack
        Independent corruption or ownership conflict.
    monkeypatch
        Restoring discovery guard.
    tmp_path
        Synthetic repository.
    """
    # Standard Library
    import fcntl

    lock = None
    if attack == "locked":
        lock = (_invocation.manifest_path.parent / "writer.lock").open("rb")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif attack == "corrupt_schedule":
        (_invocation.manifest_path.parent / "schedule.json.gz").write_bytes(b"broken")
    elif attack == "unsupported":
        raw = json.loads(_invocation.manifest_path.read_bytes())
        raw["kind"] = "lp_evaluation_store_v1"
        _invocation.manifest_path.write_text(support._dump(raw))
    else:
        source = next((tmp_path / "source").rglob("as_lc_lp_nodes.jsonl"))
        source.write_bytes(source.read_bytes() + b"\n")
    before = _state(tmp_path)
    guard = Mock(side_effect=AssertionError("Unsafe state fell back to discovery"))
    monkeypatch.setattr(name="discover_lp_runs", target=entry, value=guard)
    try:
        with pytest.raises((ValueError, OSError)):
            _prepare(tmp_path)
        assert _state(tmp_path) == before
        guard.assert_not_called()
    finally:
        if lock is not None:
            lock.close()


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "delete",
        "duplicate",
        "event_count",
        "event_head",
        "execution_number",
        "history_hash",
    ],
)
def test_execution_boundary_tampering_fails_before_dispatch(
    _invocation: Any, attack: str
) -> None:
    """Reject corrupt execution history even when individual attempts are valid.

    Parameters
    ----------
    _invocation
        Isolated invocation with one committed request.
    attack
        Corruption of the independently stored execution chain.
    """
    with judge.open_evaluation_store(_invocation) as session:
        request = session.schedule.curricula[0].requests[0]
        attempt = session.start_attempt(request.prompt.request_id)
        reply = support._reply(request.prompt)
        session.record_success(
            attempt=attempt, response_json=reply.response_json, usage=reply.usage
        )
        schedule = session.schedule
    path = _invocation.manifest_path.parent / "attempts.sqlite3"
    with sqlite3.connect(path) as connection:
        raw, digest = connection.execute(
            "SELECT payload, content_hash FROM executions WHERE sequence = 1"
        ).fetchone()
        value = json.loads(raw)
        if attack == "delete":
            connection.execute("DELETE FROM executions")
        elif attack == "duplicate":
            connection.execute(
                "INSERT INTO executions VALUES (?, ?, ?)", (2, raw, digest)
            )
        elif attack == "history_hash":
            connection.execute("UPDATE executions SET content_hash = ?", ("0" * 64,))
        else:
            value[attack] = "0" * 64 if attack == "event_head" else 99
            connection.execute(
                "UPDATE executions SET payload = ?", (support._dump(value),)
            )
    before = _state(path.parent)
    transport = _Transport(failures=[], schedule=schedule)
    with pytest.raises(ValueError):
        _run(reference=_invocation, transport=transport, waits=[])
    assert transport.preflights == 0
    assert not transport.calls
    assert _state(path.parent) == before


@pytest.mark.parametrize(
    argnames="category",
    argvalues=[
        "timeout",
        "rate_limit",
        "server_error",
        "invalid_output",
        "citation",
        "transport",
        "authentication",
        "configuration",
        "invalid_input",
        "interrupted",
    ],
)
def test_explicit_reruns_preserve_partial_cycles_and_lifetime_usage(
    _invocation: Any, category: str
) -> None:
    """Renew exhausted cycles once and retain partial allowances after any error.

    Parameters
    ----------
    _invocation
        Complete isolated invocation.
    category
        Failure class from the first explicitly initiated execution.
    """
    schedule, _ = _read(_invocation)
    retryable = category in {
        "timeout",
        "rate_limit",
        "server_error",
        "invalid_output",
        "citation",
    }
    first_count = 3 if retryable else 1
    first = _Transport(failures=[category] * first_count, schedule=schedule)
    waits: list[float] = []
    with pytest.raises(judge.JudgeExecutionError):
        _run(reference=_invocation, transport=first, waits=waits)
    _, saved = _read(_invocation)
    assert first.calls.count(first.target) == first_count
    assert waits == ([5, 20] if retryable else [])
    second_count = 3 if retryable else 2
    second = _Transport(failures=["server_error"] * second_count, schedule=schedule)
    second_waits: list[float] = []
    with pytest.raises(judge.JudgeExecutionError):
        _run(reference=_invocation, transport=second, waits=second_waits)
    _, renewed = _read(_invocation)
    assert second.calls.count(second.target) == second_count
    assert second_waits == ([5, 20] if retryable else [20])
    assert renewed.events[: len(saved.events)] == saved.events
    assert renewed.executions[: len(saved.executions)] == saved.executions
    successes = {j.request_id for j in saved.judgments}
    assert not successes.intersection(second.calls)
    third = _Transport(failures=[], schedule=schedule)
    completed = _run(reference=_invocation, transport=third, waits=[])
    starts = [
        e
        for e in completed.events
        if e.request_id == third.target and e.event == "started"
    ]
    assert [e.attempt_number for e in starts] == list(
        range(1, first_count + second_count + 2)
    )
    assert [e.execution_number for e in starts] == [1] * first_count + [
        2
    ] * second_count + [3]
    assert len(completed.judgments) == schedule.total_requests
    assert len(completed.executions) == 3
    report = scoring.score_evaluation(
        cache=completed,
        inputs=scoring.prepare_report_inputs(schedule),
        schedule=schedule,
    )
    usage = json.loads(report.usage_json)
    target_rows = [r for r in usage["attempts"] if r["request_id"] == third.target]
    assert [r["cycle_attempt"] for r in target_rows] == [1, 2, 3] * (
        len(starts) // 3
    ) + [1]
    terminals = [e for e in completed.events if e.event != "started"]
    assert usage["totals"]["tokens"]["input_tokens"]["known_subtotal"] == sum(
        e.usage.input_tokens for e in terminals
    )
    assert json.loads(report.report_json)["execution_complete"]


@pytest.mark.parametrize(
    argnames="failure",
    argvalues=["authentication", "configuration", "changed_settings"],
)
def test_failed_current_preflight_cannot_close_unknown_history(
    _invocation: Any, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep starts, boundaries and bytes intact until current preflight succeeds.

    Parameters
    ----------
    _invocation
        Isolated invocation.
    failure
        Invalid current provider configuration or mismatched settings.
    monkeypatch
        Restoring transport preflight substitution.
    """
    with judge.open_evaluation_store(_invocation) as session:
        session.start_attempt(
            session.schedule.curricula[0].requests[0].prompt.request_id
        )
        schedule = session.schedule
    before = _state(_invocation.manifest_path.parent)
    transport = _Transport(failures=[], schedule=schedule)
    if failure == "changed_settings":
        transport.settings = replace(schedule.judge, model="anthropic:other-model")
    else:

        async def _preflight() -> None:
            """Reject current configuration before any execution mutation."""
            raise judge.JudgeCallError(
                category=failure, message="Synthetic invalid current configuration"
            )

        monkeypatch.setattr(name="preflight", target=transport, value=_preflight)
    with pytest.raises((ValueError, judge.JudgeExecutionError)):
        _run(reference=_invocation, transport=transport, waits=[])
    assert not transport.calls
    assert _state(_invocation.manifest_path.parent) == before


def test_fresh_material_collects_no_source_digests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prepare, freeze and resume while all Python source-byte reads are forbidden.

    Parameters
    ----------
    monkeypatch
        Restoring source-byte guard.
    tmp_path
        Isolated synthetic repository.
    """
    # Standard Library
    import io

    build_snapshot(count=3, identity=74100, root=tmp_path / "source")
    original = io.open

    def _open(*args: Any, **kwargs: Any) -> Any:
        """Allow artifacts while rejecting source reads used for implementation hashes.

        Parameters
        ----------
        args
            Reader positional arguments.
        kwargs
            Reader keyword arguments.

        Returns
        -------
        Any
            Artifact stream.
        """
        path = args[0] if args else kwargs.get("file")
        assert not isinstance(path, (str, Path)) or Path(path).suffix != ".py"
        return original(*args, **kwargs)

    monkeypatch.setattr(name="open", target=io, value=_open)
    reference = _prepare(tmp_path)
    schedule, cache = _read(reference)
    assert not cache.events
    assert schedule.implementation_fingerprints == ()
    assert all(
        not s.reader_fingerprints
        for s in sampling.load_frozen_lp_inputs(schedule.inputs)
    )
    assert _prepare(tmp_path) == reference
    transport = _Transport(failures=[], schedule=schedule)
    cache = _run(reference=reference, transport=transport, waits=[])
    assert all(
        record.implementation_content_hash is None for record in cache.executions
    )
    reports = scoring.write_evaluation_reports(
        provenance=ReportProvenance(evidence_kind="development"), reference=reference
    )
    report = json.loads((reports.directory / "lp_eval_report.json").read_bytes())
    assert "scorer_sha256" not in report
    assert not list((tmp_path / "results").rglob("recovery_transition.json"))


def test_legacy_source_metadata_preserves_request_ids_without_source_access(
    _invocation: Any, tmp_path: Path
) -> None:
    """Reuse saved request namespaces even when historical source files are absent.

    Parameters
    ----------
    _invocation
        Fresh invocation used to derive test-only historical identity metadata.
    tmp_path
        Synthetic repository.
    """
    # Package Library
    from kgfeg.evals.lp_eval.schemas import FileFingerprint

    fresh, _ = _read(_invocation)
    historical = replace(
        fresh,
        implementation_fingerprints=(
            FileFingerprint(
                path=Path("/absent/old/evaluator.py"), sha256="a" * 64, size_bytes=123
            ),
        ),
    )
    historical = sampling.prepare_evaluation_schedule(
        identity=historical,
        inputs=fresh.inputs,
        judge=fresh.judge,
        settings=fresh.settings,
    )
    # Derive the saved namespace from test-owned historical metadata, never from
    # the regenerated schedule: a scheduler dropping it must not redefine the oracle.
    legacy_namespace = hashlib.sha256(
        support._dump(
            [
                {
                    "path": "/absent/old/evaluator.py",
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                }
            ]
        ).encode()
    ).hexdigest()
    expected_ids = set()
    for curriculum in historical.curricula:
        for item in curriculum.requests:
            identity = {
                "canonical_endpoint_uuids": [
                    str(endpoint) for endpoint in item.canonical_endpoint_uuids
                ],
                "component": item.component,
                "evidence_content_hash": item.evidence.material_content_hash,
                "implementation_hash": legacy_namespace,
                "judge": asdict(fresh.judge),
                "replicate": item.replicate,
                "role": item.role,
                "settings": fresh.settings.settings.model_dump(mode="json"),
            }
            expected_id = hashlib.sha256(support._dump(identity).encode()).hexdigest()
            assert (
                item.prompt.request_id == expected_id
            ), "Saved legacy request ID changed"
            expected_ids.add(expected_id)
    reference = judge.persist_evaluation_schedule(
        repository_root=tmp_path, schedule=historical
    )
    with judge.open_evaluation_store(reference) as session:
        request = session.schedule.curricula[0].requests[0]
        attempt = session.start_attempt(request.prompt.request_id)
        reply = support._reply(request.prompt)
        session.record_success(
            attempt=attempt, response_json=reply.response_json, usage=reply.usage
        )
        saved = session.snapshot()
    database = reference.manifest_path.parent / "attempts.sqlite3"
    with sqlite3.connect(database) as connection:
        record = json.loads(
            connection.execute(
                "SELECT payload FROM executions WHERE sequence=1"
            ).fetchone()[0]
        )
        record["implementation_content_hash"] = "f" * 64
        digest = hashlib.sha256(
            support._dump(
                {"previous": historical.material_content_hash, "record": record}
            ).encode()
        ).hexdigest()
        connection.execute(
            "UPDATE executions SET payload=?, content_hash=? WHERE sequence=1",
            (support._dump(record), digest),
        )
        connection.execute(
            "UPDATE metadata SET value=? WHERE key='execution_head'",
            (support._dump({"count": 1, "hash": digest}),),
        )
    schedule, old_cache = _read(reference)
    immutable = {
        name: (reference.manifest_path.parent / name).read_bytes()
        for name in ("manifest.json", "schedule.json.gz")
    }
    assert schedule == historical
    assert _prepare(tmp_path) == reference
    transport = _Transport(failures=[], schedule=schedule)
    completed = _run(reference=reference, transport=transport, waits=[])
    assert {j.request_id for j in completed.judgments} == expected_ids
    assert request.prompt.request_id not in transport.calls
    assert completed.events[: len(saved.events)] == saved.events
    assert completed.executions[0] == old_cache.executions[0]
    assert completed.executions[1].implementation_content_hash is None
    assert all(
        (reference.manifest_path.parent / name).read_bytes() == payload
        for name, payload in immutable.items()
    )


def test_report_generation_identity_follows_actual_output_bytes(
    _invocation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish changed report bytes separately while preserving identical judgments.

    Parameters
    ----------
    _invocation
        Isolated frozen store.
    monkeypatch
        Restoring report-rendering seam.
    """
    provenance = ReportProvenance(evidence_kind="development")
    first = scoring.write_evaluation_reports(
        provenance=provenance, reference=_invocation
    )
    before = _state(first.directory)
    original = scoring.render_evaluation_markdown

    def _render(report: dict[str, Any]) -> str:
        """Produce a deterministic presentation-only change.

        Parameters
        ----------
        report
            Unchanged validated report data.

        Returns
        -------
        str
            Readable report with an additional rendering note.
        """
        return original(report) + "\nOffline rendering revision.\n"

    monkeypatch.setattr(
        name="render_evaluation_markdown", target=scoring, value=_render
    )
    second = scoring.write_evaluation_reports(
        provenance=provenance, reference=_invocation
    )
    assert first.directory != second.directory
    assert _state(first.directory) == before
    assert (first.directory / "lp_eval_judgments.jsonl").read_bytes() == (
        second.directory / "lp_eval_judgments.jsonl"
    ).read_bytes()
    assert (first.directory / "lp_eval_report.json").read_bytes() == (
        second.directory / "lp_eval_report.json"
    ).read_bytes()
    manifest = json.loads((second.directory / "lp_eval_manifest.json").read_bytes())
    for name, fingerprint in manifest["files"].items():
        payload = (second.directory / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == fingerprint["sha256"]
        assert len(payload) == fingerprint["size_bytes"]
    assert (
        scoring.write_evaluation_reports(provenance=provenance, reference=_invocation)
        == second
    )


@pytest.mark.parametrize(argnames="tied", argvalues=[False, True])
def test_selection_orders_incomplete_then_creation_time_then_schedule(
    _invocation: Any, tied: bool, tmp_path: Path
) -> None:
    """Validate every match and choose deterministically without using file mtime.

    Parameters
    ----------
    _invocation
        First valid incomplete invocation.
    tied
        Whether creation timestamps tie and require schedule-identifier ordering.
    tmp_path
        Isolated source and evaluator stores.
    """
    references = [_invocation]
    for identity in (75200, 76300):
        build_snapshot(count=3, identity=identity, root=tmp_path / "source")
        references.append(
            entry.prepare_evaluation(
                new_invocation=True,
                overrides=_OVERRIDES,
                repository_root=tmp_path,
                results_root=tmp_path / "source",
            )
        )
    for index, reference in enumerate(references):
        material = json.loads(reference.manifest_path.read_bytes())
        material["created_at"] = f"2026-09-{10 if tied else 12-index:02d}T00:00:00Z"
        payload = support._dump(material).encode()
        reference.manifest_path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        with sqlite3.connect(
            reference.manifest_path.parent / "attempts.sqlite3"
        ) as connection:
            connection.execute(
                "UPDATE metadata SET value=? WHERE key='manifest'", (digest,)
            )
        references[index] = replace(reference, content_hash=digest)
    ordered = sorted(
        references,
        key=lambda r: (
            json.loads(r.manifest_path.read_bytes())["created_at"],
            r.manifest_path.parent.name,
        ),
        reverse=True,
    )
    assert _prepare(tmp_path) == ordered[0]
    schedule, _ = _read(ordered[0])
    _run(
        reference=ordered[0],
        transport=_Transport(failures=[], schedule=schedule),
        waits=[],
    )
    assert _prepare(tmp_path) == ordered[1]
    for reference in ordered[1:]:
        schedule, _ = _read(reference)
        _run(
            reference=reference,
            transport=_Transport(failures=[], schedule=schedule),
            waits=[],
        )
    assert _prepare(tmp_path) == ordered[0]
    (ordered[-1].manifest_path.parent / "schedule.json.gz").write_bytes(
        b"broken older match"
    )
    before = _state(tmp_path)
    with pytest.raises(ValueError):
        _prepare(tmp_path)
    assert _state(tmp_path) == before


def test_stop_and_drain_preserves_active_successes_and_blocks_waiting_retries(
    _invocation: Any,
) -> None:
    """Stop new calls at terminal failure and retain the other admitted outcomes.

    Parameters
    ----------
    _invocation
        Frozen schedule larger than the concurrency limit.
    """
    schedule, _ = _read(_invocation)
    active = 0
    maximum = 0
    calls: list[str] = []
    waits: list[float] = []

    async def _exercise() -> None:
        """Coordinate all four active requests and one retry waiter deterministically."""
        admitted = asyncio.Event()
        retry_waiting = asyncio.Event()
        stopped = asyncio.Event()

        class _DrainTransport(_Transport):
            """Expose terminal failure only after four calls and a waiting retry."""

            async def judge(self, prompt: Any) -> Any:
                """Return controlled outcomes in a deterministic concurrent order.

                Parameters
                ----------
                prompt
                    Frozen prompt dispatched under the real scheduler.

                Returns
                -------
                Any
                    Valid response for calls that were active when admission stopped.
                """
                nonlocal active, maximum
                number = len(calls)
                calls.append(prompt.request_id)
                active += 1
                maximum = max(maximum, active)
                if active == 4:
                    admitted.set()
                await admitted.wait()
                try:
                    if number == 0:
                        raise judge.JudgeCallError(
                            category="rate_limit", message="Synthetic retry waiter"
                        )
                    if number == 1:
                        await retry_waiting.wait()
                        stopped.set()
                        raise judge.JudgeCallError(
                            category="authentication",
                            message="Synthetic terminal failure",
                        )
                    await stopped.wait()
                    return support._reply(prompt)
                finally:
                    active -= 1

        async def _sleep(delay: float) -> None:
            """Expose the waiting retry until terminal failure closes admission.

            Parameters
            ----------
            delay
                Requested bounded retry delay.
            """
            waits.append(delay)
            retry_waiting.set()
            await stopped.wait()

        with pytest.raises(judge.JudgeExecutionError):
            await judge.execute_evaluation(
                reference=_invocation,
                sleep=_sleep,
                transport=_DrainTransport(failures=[], schedule=schedule),
            )

    asyncio.run(asyncio.wait_for(_exercise(), timeout=20))
    _, cache = _read(_invocation)
    assert maximum == 4
    assert active == 0
    assert len(calls) == len(set(calls)) == 4
    assert waits == [5]
    assert len(cache.judgments) == 2
    assert not cache.unfinished
    assert Counter(e.failure_category for e in cache.events if e.event == "failed") == {
        "rate_limit": 1,
        "authentication": 1,
    }
    replay = _Transport(failures=[], schedule=schedule)
    completed = _run(reference=_invocation, transport=replay, waits=[])
    assert not {j.request_id for j in cache.judgments}.intersection(replay.calls)
    assert completed.events[: len(cache.events)] == cache.events
    assert len(completed.judgments) == schedule.total_requests


@pytest.mark.parametrize(argnames="prior_attempts", argvalues=[1, 2, 3])
def test_unfinished_start_is_closed_before_new_execution(
    _invocation: Any, prior_attempts: int
) -> None:
    """Recover crashes at every cycle position without rewriting starts or usage.

    Parameters
    ----------
    _invocation
        Test-owned invocation.
    prior_attempts
        Lifetime attempt left unfinished at crash.
    """
    with judge.open_evaluation_store(_invocation) as session:
        request = session.schedule.curricula[0].requests[0]
        for number in range(1, prior_attempts + 1):
            attempt = session.start_attempt(request.prompt.request_id)
            if number < prior_attempts:
                session.record_failure(
                    attempt=attempt,
                    category="timeout",
                    message="Known prior failure",
                    usage=JudgeUsage(input_tokens=13, output_tokens=2),
                )
        saved = session.snapshot()
        schedule = session.schedule
    transport = _Transport(failures=[], schedule=schedule)
    completed = _run(reference=_invocation, transport=transport, waits=[])
    assert completed.events[: len(saved.events)] == saved.events
    interrupted = completed.events[len(saved.events)]
    assert interrupted.event == "failed"
    assert interrupted.failure_category == "interrupted"
    assert interrupted.attempt_number == prior_attempts
    assert interrupted.execution_number == 1
    assert interrupted.usage == JudgeUsage()
    assert completed.executions[1].event_count == len(saved.events) + 1
    assert completed.executions[1].timestamp >= interrupted.timestamp
    retry = next(
        e
        for e in completed.events[len(saved.events) + 1 :]
        if e.request_id == request.prompt.request_id
    )
    assert retry.attempt_number == prior_attempts + 1
    assert retry.execution_number == 2
    assert transport.calls.count(request.prompt.request_id) == 1
    report = scoring.score_evaluation(
        cache=completed,
        inputs=scoring.prepare_report_inputs(schedule),
        schedule=schedule,
    )
    usage = json.loads(report.usage_json)
    assert usage["totals"]["tokens"]["input_tokens"]["unknown_attempts"] == 1
    assert usage["totals"]["tokens"]["input_tokens"]["total"] is None
    assert (
        usage["totals"]["tokens"]["input_tokens"]["known_subtotal"]
        == (prior_attempts - 1) * 13 + schedule.total_requests * 7
    )
