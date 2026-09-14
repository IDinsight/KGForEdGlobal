"""Resume approved scheduler checkpoints through current production scheduling."""

# Future Library
from __future__ import annotations

# Standard Library
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

from pathlib import Path
from typing import Any, cast

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_generation as gen
from kgfeg.kgs.schemas import AcademicStandardsLCKGBundle
from kgfeg.schemas import CreateKGConfig
from tests.kgfeg.kgs import test_lp_concurrency as concurrent
from tests.kgfeg.kgs import test_lp_orchestration as old

_APPROVED_SHA = "d53b76e985230bf9ed568b0dd18c3094a10b5c72"
_PRODUCER = Path(__file__).with_name("lp_cross_version_support.py")
_MODES = [
    "initial",
    "draft",
    "verdict",
    "prefix",
    "unknown",
    "failed_checker",
    "complete",
]


@pytest.fixture(scope="session")
def _approved_snapshots(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[tuple[int, str], Path]:
    """Create all inputs using a separate process importing the approved code.

    Parameters
    ----------
    tmp_path_factory
        Session-owned temporary evidence area.

    Returns
    -------
    dict
        Immutable independently produced snapshots.
    """
    base = tmp_path_factory.mktemp("approved-scheduler")
    approved = base / "approved"
    approved.mkdir()
    repo = Path(gen.__file__).resolve().parents[4]
    archive = subprocess.check_output(
        [
            "git",
            "archive",
            _APPROVED_SHA,
            "backend/src",
            "backend/tests",
            "backend/pyproject.toml",
            "examples",
        ],
        cwd=repo,
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as exported:
        exported.extractall(path=approved, filter="data")
    snapshots = {}
    for capacity in (1, 4):
        for mode in _MODES + (["suffix"] if capacity == 4 else []):
            path = base / "cross-version-inputs" / f"{capacity}-{mode}"
            if not path.exists():
                env = dict(
                    os.environ,
                    PYTHONPATH=f"{approved}/backend/src:{approved}/backend",
                    PATHS_PROJECT_DIR=str(approved),
                )
                argv = [sys.executable, str(_PRODUCER), str(capacity), mode, str(path)]
                with (base / f"producer-{capacity}-{mode}.log").open("w") as stream:
                    result = subprocess.run(  # pylint: disable=W1510
                        argv,
                        cwd="/tmp",
                        env=env,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        timeout=90,
                    )
                assert result.returncode == 0, path
            snapshots[capacity, mode] = path
    return snapshots


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid external transport.

    Parameters
    ----------
    monkeypatch
        Restoring synthetic settings.
    """
    cast(Any, concurrent._block_network).__wrapped__(monkeypatch)


def _resume(*, monkeypatch: pytest.MonkeyPatch, source: Path, target: Path) -> None:
    """Require exact durable-call reuse and independently reconciled usage.

    Parameters
    ----------
    monkeypatch
        Offline external-call seam.
    source
        Approved-source fixture and metadata.
    target
        Current-format checkpoint mixture to resume.
    """
    metadata = json.loads((source / "producer.json").read_bytes())
    harness = concurrent._Scenario(count=4, root=target)
    harness.bundle = AcademicStandardsLCKGBundle.model_validate_json(
        (source / "bundle.json").read_bytes()
    )
    harness.config = CreateKGConfig.model_validate_json(
        (source / "config.json").read_bytes()
    )
    old._install(harness=harness, monkeypatch=monkeypatch)
    responses = harness._run()
    assert len(responses) == 4
    durable = set(map(tuple, metadata["durable"]))
    assert not durable.intersection(harness.calls)
    expected = {
        (stage, index) for index in range(4) for stage in ("draft", "verdict")
    } - durable
    assert set(harness.calls) == expected and len(harness.calls) == len(expected)
    usage = json.loads((target / concurrent._USAGE).read_bytes())
    succeeded = [a for a in usage["attempts"] if a["status"] == "succeeded"]
    assert len(succeeded) == 8
    assert {(a["stage"], a["request_index"]) for a in succeeded} == {
        (stage, index) for stage in ("draft", "verdict") for index in range(4)
    }
    prior = metadata["attempts"]
    for before, after in zip(prior, usage["attempts"]):
        assert all(after[k] == v for k, v in before.items() if k != "status")
        assert after["status"] == (
            "unknown" if before["status"] == "dispatched" else before["status"]
        )
    assert usage["unknown_usage_attempts"] == sum(
        a["usage"] is None for a in usage["attempts"]
    )
    assert sum(a["usage"]["requests"] for a in succeeded) == 8
    for stage, name in [
        ("draft", old._DRAFT),
        ("verdict", old._VERDICT),
        ("response", old._RESPONSE),
    ]:
        assert [r["request_index"] for r in old._rows(target / name)] == list(range(4))
    failures = json.loads((target / old._FAILURE).read_bytes())
    assert all(f["resolved_run_number"] is not None for f in failures)
    assert all(
        not v for v in json.loads((target / concurrent._PENDING).read_bytes()).values()
    )
    harness.calls.clear()
    harness._run()
    assert not harness.calls
    assert json.loads((target / concurrent._USAGE).read_bytes()) == usage


@pytest.mark.parametrize(argnames="capacity", argvalues=[1, 4])
@pytest.mark.parametrize(argnames="mode", argvalues=_MODES)
def test_approved_checkpoints_resume_at_earliest_unfinished_stage(
    _approved_snapshots: Any,
    capacity: int,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resume saved prefixes, stage dependencies, failures and unknown outcomes.

    Parameters
    ----------
    capacity
        Serial or default-concurrent capacity.
    mode
        Interrupted or failed stage.
    monkeypatch
        Offline call seam.
    tmp_path
        Independent copy for optimized scheduler execution.
    """
    source = _approved_snapshots[capacity, mode]
    target = tmp_path / "snapshot"
    shutil.copytree(source / "snapshot", target)
    _resume(monkeypatch=monkeypatch, source=source, target=target)


@pytest.mark.parametrize(
    argnames="mode", argvalues=["initial", "draft", "verdict", "prefix"]
)
@pytest.mark.parametrize(
    argnames="mixture",
    argvalues=list(range(8)) + ["alternating", "reverse_alternating"],
)
def test_approved_current_format_transaction_mixtures_resume(
    _approved_snapshots: Any,
    mixture: Any,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Recover every file-prefix publication boundary and non-prefix old/new mixtures.

    Parameters
    ----------
    mixture
        Number of next files published, or a scattered mixture.
    mode
        Initial or update transaction stage.
    monkeypatch
        Offline call seam.
    tmp_path
        New independently mixed directory.
    """
    source = _approved_snapshots[1, mode]
    target = tmp_path / "snapshot"
    shutil.copytree(source / "snapshot", target)
    payloads = json.loads((target / old._JOURNAL).read_bytes())["next_payloads"]
    assert len(payloads) == 7
    for i, (name, payload) in enumerate(payloads.items()):
        selected = (
            (i < mixture)
            if isinstance(mixture, int)
            else (i % 2 == (0 if mixture == "alternating" else 1))
        )
        if selected:
            (target / name).write_text(payload)
    _resume(monkeypatch=monkeypatch, source=source, target=target)


def test_approved_pending_suffix_survives_failed_gap(
    _approved_snapshots: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Durable out-of-order producer and checker calls cannot repeat after a failed hole.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Independent resume directory.
    """
    source = _approved_snapshots[4, "suffix"]
    target = tmp_path / "snapshot"
    shutil.copytree(source / "snapshot", target)
    _resume(monkeypatch=monkeypatch, source=source, target=target)


def test_canonical_checkpoint_receipts_and_transactions_are_byte_identical(
    _approved_snapshots: Any, tmp_path: Path
) -> None:
    """Identical serial outcomes preserve every persisted byte and transaction encoding.

    Parameters
    ----------
    tmp_path
        Current-source comparison directory.
    """
    source = _approved_snapshots[1, "complete"]
    repo = Path(gen.__file__).resolve().parents[4]
    env = dict(
        os.environ,
        PYTHONPATH=f"{repo}/backend/src:{repo}/backend",
        PATHS_PROJECT_DIR=str(repo),
    )
    target = tmp_path / "optimized"
    argv = [sys.executable, str(_PRODUCER), "1", "complete", str(target)]
    result = subprocess.run(  # pylint: disable=W1510
        argv, cwd="/tmp", env=env, capture_output=True, timeout=90
    )
    assert result.returncode == 0, result.stderr.decode()
    approved = json.loads((source / "producer.json").read_bytes())
    optimized = json.loads((target / "producer.json").read_bytes())
    assert approved["snapshot_hashes"] == optimized["snapshot_hashes"]
    assert approved["transaction_sha256"] == optimized["transaction_sha256"]
    assert old._snapshot(source / "snapshot") == old._snapshot(target / "snapshot")
