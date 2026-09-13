"""Exercise bounded request scheduling and durable recovery with controlled outcomes."""

# Future Library
from __future__ import annotations

# Standard Library
import json
import socket

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, get_ident
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

from pydantic import ValidationError
from pydantic_ai.usage import RunUsage

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import lp_checkpoints, lp_generation
from kgfeg.kgs.lp_dispatch import LPDispatchClosed, LPDispatchGate
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.schemas import CreateKGConfig
from tests.kgfeg.kgs import test_lp_checker as _checker
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _old

_PENDING = "lp_generation_pending_completions.json"
_USAGE = "lp_generation_usage.json"


class _Scenario(_old._Harness):
    """Add independent call accounting and barriers to reduced upstream fixtures."""

    def __init__(
        self, *, batch: int = 1, capacity: int = 4, count: int = 4, root: Path
    ) -> None:
        """Prepare a capacity-controlled synthetic framework.

        Parameters
        ----------
        batch
            Pairs in each request.
        capacity
            Maximum admitted requests.
        count
            Eligible same-rank standards.
        root
            Temporary artifact directory.
        """
        super().__init__(batch=batch, count=count, root=root)
        self.config.learning_progressions.max_concurrent_requests = capacity
        self.callback: Any = None
        self.mutex = Lock()
        self.running: set[int] = set()
        self.peak = 0

    def _call(self, **kwargs: Any) -> Any:
        """Treat worker admission and controlled transport entry as separate events.

        Parameters
        ----------
        kwargs
            Actual immutable request, draft, gate and isolated usage tracker.

        Returns
        -------
        Any
            Complete synthetic proposal, or scripted failure.
        """
        kwargs["dispatch_guard"]()
        _old._assert_population(self.root)
        request = kwargs["request"]
        index = request.request_index
        stage = "draft" if kwargs["draft"] is None else "verdict"
        with self.mutex:
            assert index not in self.running, "Two stages overlap for one request."
            self.running.add(index)
            self.peak = max(self.peak, len(self.running))
            self.calls.append((stage, index))
        try:
            tracker = kwargs["usage_tracker"]
            assert tracker is not self.tracker
            bucket = (
                tracker.lp_generation
                if stage == "draft"
                else tracker.lp_generation_validation
            )
            bucket.add_run_usage(RunUsage(input_tokens=7, output_tokens=3, requests=1))
            if stage == "verdict":
                gate = kwargs["dispatch_guard"].__self__
                with gate.coordinate():
                    prefix = _old._rows(self.root / _old._DRAFT)
                    pending = _read(self.root / _PENDING)["draft"]
                    saved = {row["request_index"]: row for row in prefix + pending}
                    assert saved[index]["payload"] == kwargs["draft"].model_dump(
                        mode="json"
                    )
            if self.callback is not None:
                self.callback(index=index, stage=stage)
            return (
                _checker._draft(request)
                if stage == "draft"
                else _checker._verdict(request=request)
            )
        finally:
            with self.mutex:
                self.running.remove(index)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disallow live calls and use a synthetic fixed model binding.

    Parameters
    ----------
    monkeypatch
        Restoring environment and network patches.
    """
    guard = Mock(side_effect=AssertionError("Concurrency tests are offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _read(path: Path) -> Any:
    """Read independent JSON evidence.

    Parameters
    ----------
    path
        Test-owned artifact path.

    Returns
    -------
    Any
        Parsed artifact.
    """
    return json.loads(path.read_bytes())


# Default feedback keeps serial/default-capacity success and failure windows;
# the extra capacities and independent batch-size matrix remain opt-in.
@pytest.mark.parametrize(
    argnames="capacity",
    argvalues=[
        1,
        pytest.param(2, marks=pytest.mark.slow),
        4,
        pytest.param(7, marks=pytest.mark.slow),
    ],
)
@pytest.mark.parametrize(
    argnames="batch", argvalues=[1, pytest.param(3, marks=pytest.mark.slow)]
)
@pytest.mark.parametrize(argnames="fails", argvalues=[False, True])
# pylint: disable-next=too-complex,too-many-statements
def test_bounded_window_retains_completed_suffix_and_resumes_without_duplicate_calls(
    batch: int,
    capacity: int,
    fails: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Hold the first request until the rest of its window is durably complete.

    Parameters
    ----------
    batch
        Independent pair-batch size.
    capacity
        Admitted request capacity.
    fails
        Exhaust the first request after later work is safely retained.
    monkeypatch
        Observe coordinator persistence and replace only external calls.
    tmp_path
        Temporary generation evidence.
    """
    harness = _Scenario(batch=batch, capacity=capacity, count=8, root=tmp_path)
    # One full window plus one waiting request exercises backpressure and refill.
    harness.config.learning_progressions.candidate_policy.budgets.max_total_candidates = (
        capacity + 1
    ) * batch
    first, release, suffix = Event(), Event(), Event()
    progress = Event()
    admitted: list[int] = []
    writers: set[int] = set()
    original_dispatch = lp_checkpoints.LPGenerationCheckpoints.record_dispatch
    original_reconcile = lp_generation._reconcile_lp_request

    def _callback(*, index: int, stage: str) -> None:
        """Hold only the earliest producer; peers can complete their own checkers.

        Parameters
        ----------
        index
            Request position.
        stage
            Current producer or checker stage.
        """
        if index == 0 and stage == "draft":
            first.set()
            progress.set()
            # The controller's finally block always releases this fixture barrier.
            release.wait()
            if fails:
                raise TimeoutError("synthetic exhausted first request")

    def _dispatch(self: Any, *, request_index: int, stage: str) -> int:
        """Observe admission without changing its scheduler decisions.

        Parameters
        ----------
        self
            Sole checkpoint owner.
        request_index
            Request being admitted.
        stage
            Earliest unfinished stage.

        Returns
        -------
        int
            Real durable attempt index.
        """
        admitted.append(request_index)
        writers.add(get_ident())
        result = original_dispatch(self=self, request_index=request_index, stage=stage)
        progress.set()
        return result

    def _reconcile(**kwargs: Any) -> None:
        """Signal only after all later requests in the bounded window are durable.

        Parameters
        ----------
        kwargs
            Original coordinator reconciliation arguments.
        """
        original_reconcile(**kwargs)
        store = kwargs["store"]
        writers.add(get_ident())
        assert (
            len(set().union(*(set(rows) for rows in store.pending.values())))
            <= capacity
        )
        if all(
            store.get_row(request_index=i, stage="response") is not None
            for i in range(1, capacity)
        ):
            suffix.set()
        progress.set()

    def _wait_until(ready: Callable[[], bool]) -> None:
        """Bound inactivity without imposing a whole-workload speed requirement.

        Parameters
        ----------
        ready
            Barrier or future completion condition observed by the controller.
        """
        while not ready():
            progress.clear()
            if ready():
                break
            if future.done():
                future.result()
                raise AssertionError("Generation finished before the expected barrier.")
            assert progress.wait(timeout=30), "No coordinator progress for 30 seconds."

    harness.callback = _callback
    _old._install(harness=harness, monkeypatch=monkeypatch)
    monkeypatch.setattr(
        name="record_dispatch",
        target=lp_checkpoints.LPGenerationCheckpoints,
        value=_dispatch,
    )
    monkeypatch.setattr(
        name="_reconcile_lp_request", target=lp_generation, value=_reconcile
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(harness._run)
        future.add_done_callback(lambda _: progress.set())
        try:
            _wait_until(first.is_set)
            if capacity > 1:
                _wait_until(suffix.is_set)
            assert set(admitted) == set(range(capacity))
            assert _old._rows(tmp_path / _old._RESPONSE) == []
            pending = _read(tmp_path / _PENDING)
            assert [row["request_index"] for row in pending["response"]] == list(
                range(1, capacity)
            )
            assert harness.peak <= capacity
        finally:
            release.set()
        _wait_until(future.done)
        if fails:
            with pytest.raises(expected_exception=LPGenerationFailed):
                future.result()
        else:
            future.result()
    assert len(writers) == 1
    completed = set(harness.calls) - {("draft", 0)}
    harness.callback = None
    harness.calls.clear()
    results = harness._run()
    assert len(results) == capacity + 1
    assert not completed.intersection(harness.calls)
    usage = _read(tmp_path / _USAGE)
    succeeded = [
        (row["stage"], row["request_index"])
        for row in usage["attempts"]
        if row["status"] == "succeeded"
    ]
    assert len(succeeded) == len(set(succeeded)) == 2 * len(results)
    assert usage["unknown_usage_attempts"] == 0
    for name in (_old._DRAFT, _old._VERDICT, _old._RESPONSE):
        assert [row["request_index"] for row in _old._rows(tmp_path / name)] == list(
            range(len(results))
        )
    assert _read(tmp_path / _PENDING) == {"draft": [], "verdict": [], "response": []}
    assert _read(tmp_path / _old._RECEIPT)["status"] == "completed"


@pytest.mark.parametrize(
    argnames="value", argvalues=[True, False, None, 0, -1, 1.0, 1.5, "4", [], {}]
)
def test_capacity_rejects_every_nonpositive_or_noninteger_value(value: Any) -> None:
    """Operational capacity must be an explicit positive integer when supplied.

    Parameters
    ----------
    value
        Rejected raw configuration value.
    """
    payload = _fixtures._config().model_dump(by_alias=True, mode="json")
    payload["lp"]["max_concurrent_requests"] = value
    with pytest.raises(expected_exception=ValidationError):
        CreateKGConfig.model_validate(payload)


@pytest.mark.parametrize(argnames="count", argvalues=[0, 1, 2, 4])
@pytest.mark.parametrize(argnames="capacity", argvalues=[1, 4, 9])
def test_empty_singleton_and_small_populations_account_every_call(
    capacity: int, count: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty and smaller populations preserve exact attempts and configured capacity.

    Parameters
    ----------
    capacity
        Effective scheduler capacity.
    count
        Eligible standard count; zero or one produces no pairs.
    monkeypatch
        Offline stage seam.
    tmp_path
        Temporary artifacts.
    """
    harness = _Scenario(capacity=capacity, count=count, root=tmp_path)
    _old._install(harness=harness, monkeypatch=monkeypatch)
    results = harness._run()
    usage = _read(tmp_path / _USAGE)
    assert len(harness.calls) == 2 * len(results)
    assert len(usage["attempts"]) == len(harness.calls)
    assert usage["max_concurrent_requests"] == capacity
    assert harness.tracker.to_dict()["totals"]["requests"] == len(harness.calls)
    assert harness.tracker.to_dict()["totals"]["input_tokens"] == 7 * len(harness.calls)
    assert harness.tracker.to_dict()["totals"]["output_tokens"] == 3 * len(
        harness.calls
    )
    assert usage["unknown_usage_attempts"] == 0


@pytest.mark.parametrize(argnames="changed", argvalues=[False, True])
def test_legacy_prefix_only_reuse_requires_exact_actual_material(
    changed: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absent journals mean no pending work and grant no material identity exemption.

    Parameters
    ----------
    changed
        Remove the effective capacity from the captured legacy material.
    monkeypatch
        Offline stage seam.
    tmp_path
        Temporary legacy-shaped evidence.
    """
    harness = _Scenario(count=2, root=tmp_path)
    _old._install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    receipt = _read(tmp_path / _old._RECEIPT)
    for name in (_PENDING, _USAGE):
        receipt["artifact_byte_hashes"].pop(name)
        (tmp_path / name).unlink()
    if changed:
        receipt["material"].pop("max_concurrent_requests")
    (tmp_path / _old._RECEIPT).write_bytes(_old._bytes(receipt))
    before = _old._snapshot(tmp_path)
    harness.calls.clear()
    if changed:
        with pytest.raises(expected_exception=ValueError):
            harness._run()
        assert _old._snapshot(tmp_path) == before
    else:
        harness._run()
        assert _read(tmp_path / _USAGE)["legacy_stage_counts"] == {
            "draft": 1,
            "verdict": 1,
            "response": 1,
        }
    assert not harness.calls


@pytest.mark.parametrize(argnames="capacity", argvalues=[1, 2, 4, 8])
def test_omitted_default_matches_explicit_four_but_changed_capacity_rejects_reuse(
    capacity: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resolve omitted and explicit defaults to the same effective material identity.

    Parameters
    ----------
    capacity
        Explicit follow-up capacity.
    monkeypatch
        Offline stage seam.
    tmp_path
        Temporary generation evidence.
    """
    harness = _Scenario(count=2, root=tmp_path)
    raw = harness.config.model_dump(by_alias=True, mode="json")
    raw["lp"].pop("max_concurrent_requests")
    harness.config = CreateKGConfig.model_validate(raw)
    assert harness.config.learning_progressions.max_concurrent_requests == 4
    _old._install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    original = _old._snapshot(tmp_path)
    harness.calls.clear()
    raw["lp"]["max_concurrent_requests"] = capacity
    harness.config = CreateKGConfig.model_validate(raw)
    if capacity == 4:
        harness._run()
        receipt = _read(tmp_path / _old._RECEIPT)
        assert receipt["material"]["max_concurrent_requests"] == 4
    else:
        with pytest.raises(expected_exception=ValueError):
            harness._run()
        assert _old._snapshot(tmp_path) == original
    assert not harness.calls


@pytest.mark.parametrize(argnames="retries", argvalues=[0, 2])
@pytest.mark.parametrize(argnames="malformed", argvalues=[False, True])
def test_shutdown_finishes_active_calls_without_checker_or_retry_dispatch(
    malformed: bool, monkeypatch: pytest.MonkeyPatch, retries: int, tmp_path: Path
) -> None:
    """Exhaust one request while peers are active, then retain all returning evidence.

    Parameters
    ----------
    malformed
        One peer returns a failure during shutdown instead of valid output.
    monkeypatch
        Observe the coordinator's actual exhausted-failure boundary.
    retries
        Permitted retries for the exhausted producer.
    tmp_path
        Temporary artifact directory.
    """
    harness = _Scenario(root=tmp_path)
    harness.config.learning_progressions.retry.producer_max_retries = retries
    all_started, closed = Event(), Event()
    started: set[int] = set()
    original_observe = lp_generation._observe_lp_failures

    def _callback(*, index: int, stage: str) -> None:
        """Keep three peer calls active until failure has closed dispatch.

        Parameters
        ----------
        index
            Current request.
        stage
            Producer or checker.
        """
        assert stage == "draft", "Dependent checker started after shutdown."
        with harness.mutex:
            started.add(index)
            if len(started) == 4:
                all_started.set()
        assert all_started.wait(timeout=30)
        if index == 0:
            raise TimeoutError("synthetic exhausted producer")
        assert closed.wait(
            timeout=30
        ), "Coordinator waited for peers while holding its gate."
        if malformed and index == 2:
            raise ValueError("synthetic malformed shutdown return")

    def _observe(**kwargs: Any) -> Any:
        """Signal after the production coordinator has observed exhaustion.

        Parameters
        ----------
        kwargs
            Real coordinator outcome batch and gate.

        Returns
        -------
        Any
            Unmodified terminal failure classification.
        """
        result = original_observe(**kwargs)
        if result is not None:
            closed.set()
        return result

    harness.callback = _callback
    _old._install(harness=harness, monkeypatch=monkeypatch)
    monkeypatch.setattr(
        name="_observe_lp_failures", target=lp_generation, value=_observe
    )
    with pytest.raises(expected_exception=LPGenerationFailed):
        harness._run()
    assert harness.calls.count(("draft", 0)) == retries + 1
    assert all(harness.calls.count(("draft", i)) == 1 for i in range(1, 4))
    assert set(harness.calls) == {("draft", i) for i in range(4)}
    usage = _read(tmp_path / _USAGE)
    assert len(usage["attempts"]) == 4 + retries
    assert usage["unknown_usage_attempts"] == 0
    assert harness.tracker.to_dict()["totals"]["requests"] == 4 + retries
    assert len(_read(tmp_path / _old._FAILURE)) == retries + 1 + int(malformed)
    assert _read(tmp_path / _old._RECEIPT)["status"] == "failed"
    pending = _read(tmp_path / _PENDING)
    assert [r["request_index"] for r in pending["draft"]] == (
        [1, 3] if malformed else [1, 2, 3]
    )
    assert pending["verdict"] == pending["response"] == []
    assert [r["state"] for r in usage["request_states"]][4:] == [
        "not_started",
        "not_started",
    ]


def test_waiting_worker_cannot_pass_gate_during_coordinator_failure_publication() -> (
    None
):
    """A waiting worker must see closure after the coordinator exits its transaction."""
    entered, trying = Event(), Event()
    gate = LPDispatchGate()

    def _worker() -> None:
        """Attempt transport admission only after announcing a waiting worker."""
        trying.set()
        gate.check()
        entered.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with gate.coordinate():
            future = executor.submit(_worker)
            assert trying.wait(timeout=20)
            assert not entered.is_set()
            gate.close()
        with pytest.raises(expected_exception=LPDispatchClosed):
            future.result(timeout=20)
    assert not entered.is_set()
