"""Challenge journal promotion, ownership and corrupted concurrent execution state."""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import json

from pathlib import Path
from typing import Any, cast

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_generation
from tests.kgfeg.kgs import test_lp_concurrency as _concurrent
from tests.kgfeg.kgs import test_lp_orchestration as _old

_NAMES = (
    _old._JOURNAL,
    _old._RECEIPT,
    _old._DRAFT,
    _old._VERDICT,
    _old._RESPONSE,
    _old._FAILURE,
    _concurrent._PENDING,
    _concurrent._USAGE,
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the same complete offline guard as the concurrent scenarios.

    Parameters
    ----------
    monkeypatch
        Restoring test patches.
    """
    cast(Any, _concurrent._block_network).__wrapped__(monkeypatch)


@pytest.mark.parametrize(argnames="after", argvalues=[False, True])
@pytest.mark.parametrize(
    argnames=("name", "phase"),
    argvalues=[
        *(
            pytest.param(
                name,
                phase,
                marks=(
                    ()
                    if name in {_old._JOURNAL, _concurrent._PENDING}
                    else pytest.mark.slow
                ),
            )
            for name in _NAMES
            if name not in _old._STAGE_WRITES
            for phase in ("draft", "draft_promotion", "verdict", "response")
        ),
        # Stage files are interrupted at the promotion that appends to them.
        *(
            pytest.param(name, phase, marks=pytest.mark.slow)
            for name, phases in _old._STAGE_WRITES.items()
            for phase in phases
            if phase != "initialize"
        ),
    ],
)
def test_interrupted_pending_and_promotion_transactions_reuse_every_durable_stage(
    after: bool, monkeypatch: pytest.MonkeyPatch, name: str, phase: str, tmp_path: Path
) -> None:
    """Interrupt exact transition contents across every shared-file publication.

    Parameters
    ----------
    after
        Interrupt after the write rather than before it.
    monkeypatch
        Restoring write fault injector.
    name
        Actual journal, pending, usage, prefix, failure or receipt boundary.
    phase
        Semantic transition, independent of preceding write counts.
    tmp_path
        Temporary checkpoint directory.
    """
    harness = _concurrent._Scenario(count=2, root=tmp_path)
    _old._install(harness=harness, monkeypatch=monkeypatch)
    current, fired = "", False

    def _interrupt(path: Path, payload: bytes, written: bool) -> None:
        """Interrupt one transaction without mocking persistence or recovery.

        Parameters
        ----------
        path
            Actual target path.
        payload
            Complete or appended bytes proposed by the sole writer.
        written
            Whether the real write has already happened.
        """
        nonlocal current, fired
        if path.name == _old._JOURNAL and not written:
            current = _old._transaction_phase(payload)
        if not fired and current == phase and path.name == name and written == after:
            fired = True
            raise _old._Interruption()

    with monkeypatch.context() as patch:
        _old._patch_writes(interrupt=_interrupt, patch=patch)
        with pytest.raises(expected_exception=_old._Interruption):
            harness._run()
    assert fired
    journal = tmp_path / _old._JOURNAL
    if journal.exists():
        material = json.loads(journal.read_bytes())["next_payloads"]
        usage = json.loads(material[_concurrent._USAGE])
    else:
        usage = _concurrent._read(tmp_path / _concurrent._USAGE)
    durable = {
        (row["stage"], row["request_index"])
        for row in usage["attempts"]
        if row["status"] == "succeeded"
    }
    harness.calls.clear()
    assert len(harness._run()) == 1
    assert not durable.intersection(harness.calls)
    saved_usage = _concurrent._read(tmp_path / _concurrent._USAGE)
    succeeded = [row for row in saved_usage["attempts"] if row["status"] == "succeeded"]
    assert len(succeeded) == 2
    assert sum(row["usage"]["requests"] for row in succeeded) == 2
    expected_unknown = int(
        phase == "draft" and name == _old._JOURNAL and not after
    ) + int(phase == "verdict" and name == _old._JOURNAL and not after)
    assert saved_usage["unknown_usage_attempts"] == expected_unknown
    harness.calls.clear()
    harness._run()
    assert not harness.calls
    assert _concurrent._read(tmp_path / _concurrent._USAGE) == saved_usage


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "duplicate_attempt",
        "wrong_index",
        "wrong_usage",
        "wrong_capacity",
        "truncated",
        "duplicate_key",
        "missing",
        "extra_pending",
        "pending_overlap",
    ],
)
def test_invalid_pending_or_usage_state_rejects_before_calls_and_preserves_bytes(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outer resealing cannot authorize malformed or contradictory execution state.

    Parameters
    ----------
    attack
        Adversarial pending or attempt artifact change.
    monkeypatch
        Offline call seam.
    tmp_path
        Temporary evidence subject to deliberate test corruption.
    """
    harness = _concurrent._Scenario(count=2, root=tmp_path)
    _old._install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    name = _concurrent._USAGE
    value = _concurrent._read(tmp_path / name)
    if attack == "duplicate_attempt":
        value["attempts"].append(value["attempts"][0])
    elif attack == "wrong_index":
        value["attempts"][0]["request_index"] = 9
    elif attack == "wrong_usage":
        value["attempts"][0]["usage"]["requests"] = -1
    elif attack == "wrong_capacity":
        value["max_concurrent_requests"] = 9
    elif attack in {"extra_pending", "pending_overlap"}:
        name = _concurrent._PENDING
        value = _concurrent._read(tmp_path / name)
        row = _old._rows(tmp_path / _old._DRAFT)[0]
        if attack == "extra_pending":
            row["request_index"] = 9
        value["draft"].append(row)
    payload = _old._bytes(value)
    if attack == "truncated":
        payload = payload[:-3]
    elif attack == "duplicate_key":
        payload = b'{"available_cost":null,' + payload[1:]
    (tmp_path / name).write_bytes(payload)
    _old._reseal(name=name, root=tmp_path)
    if attack == "missing":
        (tmp_path / name).unlink()
    before = _old._snapshot(tmp_path)
    harness.calls.clear()
    with pytest.raises(expected_exception=(ValueError, OSError)):
        harness._run()
    assert not harness.calls
    assert _old._snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="attack", argvalues=["replace_lock", "unlock"])
def test_ownership_loss_stops_publication_after_active_call(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A returned proposal cannot publish after the original directory lock is lost.

    Parameters
    ----------
    attack
        Replace the lock inode or release its exclusive ownership.
    monkeypatch
        Observe the real lock descriptor without replacing verification.
    tmp_path
        Temporary generation directory.
    """
    harness = _concurrent._Scenario(count=2, root=tmp_path)
    owners: list[Any] = []
    before: dict[str, bytes] = {}
    original = lp_generation._LPDirectoryOwnership.__init__

    def _capture(self: Any, **kwargs: Any) -> None:
        """Retain the actual ownership object for deliberate lock loss.

        Parameters
        ----------
        self
            Original directory-ownership object.
        kwargs
            Actual lock and directory arguments.
        """
        original(self=self, **kwargs)
        owners.append(self)

    def _callback(*, index: int, stage: str) -> None:
        """Remove ownership only after one producer has entered transport.

        Parameters
        ----------
        index
            Current request position.
        stage
            Current stage.
        """
        assert index == 0 and stage == "draft"
        if attack == "replace_lock":
            (tmp_path / ".lp_generation.lock").unlink()
            (tmp_path / ".lp_generation.lock").touch()
        else:
            fcntl.flock(owners[0].lock.fileno(), fcntl.LOCK_UN)
        before.update(_old._snapshot(tmp_path))

    harness.callback = _callback
    _old._install(harness=harness, monkeypatch=monkeypatch)
    monkeypatch.setattr(
        name="__init__", target=lp_generation._LPDirectoryOwnership, value=_capture
    )
    with pytest.raises(expected_exception=ValueError, match="ownership|lock"):
        harness._run()
    assert harness.calls == [("draft", 0)]
    assert _old._snapshot(tmp_path) == before
    assert harness.tracker.to_dict()["totals"]["requests"] == 1
