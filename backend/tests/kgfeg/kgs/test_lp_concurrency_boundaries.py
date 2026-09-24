"""Challenge concurrent transport boundaries and compare controlled graph semantics."""

# Future Library
from __future__ import annotations

# Standard Library
import subprocess
import sys

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import Any, cast

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_checkpoints, lp_generation, lp_requests
from kgfeg.kgs.lp_dispatch import LPDispatchGate
from kgfeg.kgs.lp_finalization import build_lp_relationships
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.utils import KGDirs
from tests.kgfeg.kgs import test_lp_concurrency as _concurrent
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _old
from tests.kgfeg.kgs import test_lp_relationships as _relationships


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep all boundaries offline with the fixed synthetic model binding.

    Parameters
    ----------
    monkeypatch
        Restoring test patches.
    """
    cast(Any, _concurrent._block_network).__wrapped__(monkeypatch)


def test_checkpoint_mutation_during_concurrent_calls_closes_and_preserves_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Changed persisted bytes halt all publication while active usage is retained.

    Parameters
    ----------
    monkeypatch
        Offline transport and closure observer.
    tmp_path
        Temporary checkpoint directory.
    """
    harness = _concurrent._Scenario(root=tmp_path)
    started, closed = Barrier(4), Event()
    original_close = LPDispatchGate.close
    saved: dict[str, bytes] = {}

    def _callback(*, index: int, stage: str) -> None:
        """Corrupt one checkpoint only after four producer calls are active.

        Parameters
        ----------
        index
            Current request index.
        stage
            Current stage.
        """
        assert stage == "draft"
        started.wait(timeout=30)
        if index == 0:
            (tmp_path / _old._DRAFT).write_bytes(b"invalid concurrent checkpoint\n")
            saved.update(_old._snapshot(tmp_path))
        else:
            assert closed.wait(timeout=30)

    def _close(self: LPDispatchGate) -> None:
        """Observe actual gate closure before releasing already-active peers.

        Parameters
        ----------
        self
            Real run gate.
        """
        original_close(self)
        closed.set()

    harness.callback = _callback
    _old._install(harness=harness, monkeypatch=monkeypatch)
    monkeypatch.setattr(name="close", target=LPDispatchGate, value=_close)
    with pytest.raises(expected_exception=ValueError, match="checkpoint"):
        harness._run()
    assert set(harness.calls) == {("draft", i) for i in range(4)}
    assert harness.tracker.to_dict()["totals"]["requests"] == 4
    assert _old._snapshot(tmp_path) == saved


def test_competing_process_cannot_take_generation_directory_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A separate process cannot acquire the active sole-writer ownership boundary.

    Parameters
    ----------
    monkeypatch
        Offline stage seam.
    tmp_path
        Temporary generation directory.
    """
    harness = _concurrent._Scenario(count=2, root=tmp_path)
    entered, release = Event(), Event()

    def _callback(*, index: int, stage: str) -> None:
        """Hold the first active producer while a separate process probes ownership.

        Parameters
        ----------
        index
            Current request index.
        stage
            Current stage.
        """
        if stage == "draft":
            assert index == 0
            entered.set()
            assert release.wait(timeout=30)

    harness.callback = _callback
    _old._install(harness=harness, monkeypatch=monkeypatch)
    program = """import fcntl,sys
with open(sys.argv[1], 'a+b') as lock:
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('competing-writer-rejected')
    else:
        raise SystemExit(3)
"""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(harness._run)
        try:
            assert entered.wait(timeout=30)
            result = subprocess.run(
                args=[
                    sys.executable,
                    "-c",
                    program,
                    str(tmp_path / ".lp_generation.lock"),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=20,
            )
            assert result.returncode == 0
            assert result.stdout.strip() == "competing-writer-rejected"
        finally:
            release.set()
        future.result(timeout=30)


@pytest.mark.slow
@pytest.mark.parametrize(argnames="order", argvalues=[(3, 2, 1, 0), (2, 0, 3, 1)])
def test_reverse_and_permuted_stage_completion_preserves_contiguous_prefixes(
    monkeypatch: pytest.MonkeyPatch, order: tuple[int, ...], tmp_path: Path
) -> None:
    """Release producer and checker results in controlled non-prefix orders.

    Parameters
    ----------
    monkeypatch
        Offline barriers and sole-writer completion observer.
    order
        Explicit repeatable permutation, with no uncontrolled scheduling oracle.
    tmp_path
        Temporary generation evidence.
    """
    harness = _concurrent._Scenario(root=tmp_path)
    allowed = {(stage, i): Event() for stage in ("draft", "verdict") for i in range(4)}
    entered = {(stage, i): Event() for stage in ("draft", "verdict") for i in range(4)}
    saved = {(stage, i): Event() for stage in ("draft", "verdict") for i in range(4)}
    original = lp_generation._retain_lp_outcome

    def _callback(*, index: int, stage: str) -> None:
        """Return each active result only when selected by the external controller.

        Parameters
        ----------
        index
            Current request index.
        stage
            Producer or checker stage.
        """
        if index < 4:
            entered[stage, index].set()
            assert allowed[stage, index].wait(timeout=30)

    def _retain(**kwargs: Any) -> None:
        """Observe persistence after real validation and promotion complete.

        Parameters
        ----------
        kwargs
            Original writer outcome arguments.
        """
        original(**kwargs)
        attempt = kwargs["store"].attempts[kwargs["attempt_index"]]
        if attempt.request_index < 4:
            saved[attempt.stage, attempt.request_index].set()

    harness.callback = _callback
    _old._install(harness=harness, monkeypatch=monkeypatch)
    monkeypatch.setattr(name="_retain_lp_outcome", target=lp_generation, value=_retain)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(harness._run)
        try:
            for stage in ("draft", "verdict"):
                for index in order:
                    assert entered[stage, index].wait(timeout=30)
                    allowed[stage, index].set()
                    assert saved[stage, index].wait(timeout=30)
                    for name in (_old._DRAFT, _old._VERDICT, _old._RESPONSE):
                        rows = _old._rows(tmp_path / name)
                        assert [row["request_index"] for row in rows] == list(
                            range(len(rows))
                        )
        finally:
            for event in allowed.values():
                event.set()
        result = future.result(timeout=30)
    assert len(harness.calls) == len(set(harness.calls)) == 2 * len(result)


@pytest.mark.slow
@pytest.mark.parametrize(
    argnames="profile", argvalues=_fixtures._integration_profiles(_old._PROFILES)
)
def test_serial_and_concurrent_six_curriculum_decisions_edges_and_provenance_agree(
    monkeypatch: pytest.MonkeyPatch, profile: str, tmp_path: Path
) -> None:
    """Controlled outputs preserve graph semantics under different capacity settings.

    Parameters
    ----------
    monkeypatch
        Replace only semantic proposal calls.
    profile
        Reduced curriculum with its actual tree, DAG and unresolved context.
    tmp_path
        Separate serial and concurrent evidence directories.
    """
    observations = []
    for capacity in (1, 4):
        harness = _claims._Harness(root=tmp_path / str(capacity))
        harness.bundle = _fixtures._expanded_fixture(profile)
        # Keep every candidate/evidence record; amortize checkpoint work across pairs.
        harness.config = _fixtures._config(batch=4, profile=profile)
        harness.config.learning_progressions.max_concurrent_requests = capacity
        harness.config.learning_progressions.retry.producer_max_retries = 0
        harness.config.learning_progressions.retry.checker_max_retries = 0
        population = lp_requests.build_lp_generation_requests(
            as_lc_bundle=harness.bundle,
            doc_key="synthetic-selection-document",
            kg_config=harness.config,
        )
        # Small fixtures must still exercise independent requests at capacity four.
        if len(population.requests) <= 4:
            harness.config.learning_progressions.request_batch_size = 1
            population = lp_requests.build_lp_generation_requests(
                as_lc_bundle=harness.bundle,
                doc_key="synthetic-selection-document",
                kg_config=harness.config,
            )
        pair_count = sum(len(request.pairs) for request in population.requests)
        assert len(population.requests) >= min(4, pair_count)
        for index, pair in enumerate(
            pair for request in population.requests for pair in request.pairs
        ):
            decision = (
                "buildsTowards",
                "relatesTo",
                "no_relation",
                "needs_review",
            )[index % 4]
            possible = [
                choice
                for choice in pair.admissible_decisions
                if choice.decision == decision
            ]
            choice = (
                possible[0]
                if possible
                else next(
                    choice
                    for choice in pair.admissible_decisions
                    if choice.decision == "no_relation"
                )
            )
            harness.decisions[(pair.first_sfi_uuid.int, pair.second_sfi_uuid.int)] = (
                choice.decision,
                choice.direction,
            )
        _claims._install(harness=harness, monkeypatch=monkeypatch)
        outcomes = harness._run()
        final = harness._finalize()
        _claims._assert_provenance(artifact=final, root=harness.root)
        relationships = build_lp_relationships(
            as_lc_bundle=harness.bundle,
            doc_key="synthetic-selection-document",
            kg_config=harness.config,
            kg_dirs=KGDirs(root=harness.root),
        )
        _relationships._assert_metadata(harness=harness, rows=relationships)
        observations.append(
            {
                "judgments": [
                    judgment.model_dump(mode="json")
                    for response in outcomes
                    for judgment in response.judgments
                ],
                "requests": [
                    request.model_dump(
                        exclude={
                            "candidate_pairs_content_hash",
                            "candidate_summary_content_hash",
                            "config_content_hash",
                            "request_content_hash",
                            "request_id",
                        },
                        mode="json",
                    )
                    for request in population.requests
                ],
                "edges": [
                    edge.model_dump(exclude={"metadata"}, mode="json")
                    for edge in (
                        *relationships.relationships_builds_towards,
                        *relationships.relationships_relates_to,
                    )
                ],
            }
        )
    assert observations[0] == observations[1]


def test_simultaneous_exhausted_failures_all_remain_accounted_without_followup_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Multiple observed exhausted outcomes close dispatch and retain all failures.

    Parameters
    ----------
    monkeypatch
        Synchronize only completion observation outside the coordinator lock.
    tmp_path
        Temporary generation artifacts.
    """
    harness = _concurrent._Scenario(root=tmp_path)
    started = Barrier(4)
    original_wait = lp_generation.wait

    def _callback(*, index: int, stage: str) -> None:
        """Fail all first producer calls after every slot is active.

        Parameters
        ----------
        index
            Current request index.
        stage
            Current producer stage.
        """
        assert stage == "draft" and index < 4
        started.wait(timeout=30)
        raise TimeoutError("synthetic simultaneous exhaustion")

    def _wait(**kwargs: Any) -> Any:
        """Expose all peer outcomes together without changing worker execution.

        Parameters
        ----------
        kwargs
            Coordinator wait arguments outside the gate lock.

        Returns
        -------
        Any
            Original wait result after every initial peer has returned.
        """
        for future in kwargs["fs"]:
            future.result(timeout=30)
        return original_wait(**kwargs)

    harness.callback = _callback
    _old._install(harness=harness, monkeypatch=monkeypatch)
    monkeypatch.setattr(name="wait", target=lp_generation, value=_wait)
    with pytest.raises(expected_exception=LPGenerationFailed):
        harness._run()
    failures = _concurrent._read(tmp_path / _old._FAILURE)
    assert [row["request_index"] for row in failures] == [0, 1, 2, 3]
    assert len(harness.calls) == 4
    assert harness.tracker.to_dict()["totals"]["requests"] == 4
    assert _concurrent._read(tmp_path / _old._RECEIPT)["status"] == "failed"


def test_transport_waits_for_stable_checkpoint_publication_without_lock_deadlock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Block a worker's transport check during a real partially published transaction.

    Parameters
    ----------
    monkeypatch
        Observe worker entry and pause one real atomic publication.
    tmp_path
        Temporary generation evidence.
    """
    harness = _concurrent._Scenario(capacity=2, count=3, root=tmp_path)
    publishing, trying, release = Event(), Event(), Event()
    original_write = lp_checkpoints._atomic_write
    original_call = harness._call
    phase, fired = "", False

    def _call(**kwargs: Any) -> Any:
        """Delay the second worker before its actual transport gate check.

        Parameters
        ----------
        kwargs
            Original isolated worker call arguments.

        Returns
        -------
        Any
            Unchanged controlled result after real gate validation.
        """
        if kwargs["request"].request_index == 1 and kwargs["draft"] is None:
            assert publishing.wait(timeout=30)
            trying.set()
        return original_call(**kwargs)

    def _write(*, path: Path, payload: bytes) -> None:
        """Pause after one journal member is replaced but before receipt completion.

        Parameters
        ----------
        path
            Real publication destination.
        payload
            Actual complete atomic bytes.
        """
        nonlocal phase, fired
        if path.name == _old._JOURNAL:
            phase = _old._transaction_phase(payload)
        original_write(path=path, payload=payload)
        if not fired and phase == "draft" and path.name == _concurrent._PENDING:
            fired = True
            publishing.set()
            assert release.wait(timeout=30)

    monkeypatch.setattr(
        name="generate_learning_progressions_for_request",
        target=lp_generation,
        value=_call,
    )
    monkeypatch.setattr(name="_atomic_write", target=lp_checkpoints, value=_write)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(harness._run)
        try:
            assert publishing.wait(timeout=30) and trying.wait(timeout=30)
            assert ("draft", 1) not in harness.calls
        finally:
            release.set()
        assert len(future.result(timeout=30)) == 3
    assert len(harness.calls) == len(set(harness.calls)) == 6
