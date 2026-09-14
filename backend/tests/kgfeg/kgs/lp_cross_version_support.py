"""Produce independent interrupted scheduler snapshots using the approved source."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import sys

from pathlib import Path
from threading import Event
from typing import Any, cast

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_checkpoints as cp
from kgfeg.kgs import lp_generation as gen
from kgfeg.kgs import lp_requests as req
from tests.kgfeg.kgs import test_lp_concurrency as concurrent
from tests.kgfeg.kgs import test_lp_orchestration as old


def _produce(  # pylint: disable=R0915, R1260
    *, capacity: int, mode: str, root: Path
) -> None:
    """Persist scheduler-produced history at a controlled transaction boundary.

    Parameters
    ----------
    capacity
        Effective number of admitted requests.
    mode
        Interrupted stage or failed-gap scenario.
    root
        New synthetic evidence directory.
    """
    root.mkdir(parents=True)
    harness = concurrent._Scenario(capacity=capacity, count=4, root=root / "snapshot")
    harness.config.learning_progressions.candidate_policy.budgets.max_total_candidates = (
        4
    )
    if capacity == 4:
        raw = harness.config.model_dump(mode="json", by_alias=True)
        raw["lp"].pop("max_concurrent_requests")
        harness.config = type(harness.config).model_validate(raw)
        assert harness.config.learning_progressions.max_concurrent_requests == 4
    original_write = cp._atomic_write
    original_reconcile = gen._reconcile_lp_request
    suffix = Event()
    fired = False
    transactions = []

    def _callback(*, index: int, stage: str) -> None:
        """Hold the first request until the durable suffix is complete.

        Parameters
        ----------
        index
            Request index.
        stage
            External stage.
        """
        if mode == "suffix" and index == 0 and stage == "draft":
            assert suffix.wait(timeout=45), "No durable suffix progress."
            raise TimeoutError("independent failed prefix hole")
        if mode == "failed_checker" and index == 0 and stage == "verdict":
            raise TimeoutError("independent saved-draft checker failure")

    def _reconcile(**kwargs: Any) -> None:
        """Observe complete pending responses after the real scheduler saves them.

        Parameters
        ----------
        kwargs
            Actual coordinator arguments.
        """
        original_reconcile(**kwargs)
        store = kwargs["store"]
        if all(
            store.get_row(request_index=i, stage="response") is not None
            for i in range(1, 4)
        ):
            suffix.set()

    def _write(*, path: Path, payload: bytes) -> None:
        """Interrupt durable intent without synthesizing checkpoint rows.

        Parameters
        ----------
        path
            Actual writer target.
        payload
            Complete original transaction bytes.
        """
        nonlocal fired
        hit = False
        if path.name == old._JOURNAL and not fired:
            snapshot = json.loads(payload)["next_payloads"]
            receipt = json.loads(snapshot[old._RECEIPT])
            pending = json.loads(snapshot[concurrent._PENDING])
            usage = json.loads(snapshot[concurrent._USAGE])
            has = {
                stage: bool(receipt["stage_counts"][stage] or pending[stage])
                for stage in ("draft", "verdict", "response")
            }
            hit = (
                mode == "initial"
                and receipt["run_number"] == 0
                or mode == "draft"
                and has["draft"]
                or mode == "verdict"
                and has["verdict"]
                or mode == "prefix"
                and receipt["stage_counts"]["response"] >= 1
                or mode == "unknown"
                and any(a["status"] == "succeeded" for a in usage["attempts"])
            )
        if hit and mode == "unknown":
            fired = True
            raise old._Interruption()
        if path.name == old._JOURNAL:
            transactions.append(hashlib.sha256(payload).hexdigest())
        original_write(path=path, payload=payload)
        if hit:
            fired = True
            raise old._Interruption()

    harness.callback = _callback
    with pytest.MonkeyPatch.context() as patch:
        cast(Any, concurrent._block_network).__wrapped__(patch)
        old._install(harness=harness, monkeypatch=patch)
        patch.setattr(name="_atomic_write", target=cp, value=_write)
        patch.setattr(name="_reconcile_lp_request", target=gen, value=_reconcile)
        try:
            harness._run()
        except (old._Interruption, gen.LPGenerationFailed):
            pass
    assert fired or mode in {"complete", "suffix", "failed_checker"}
    (root / "bundle.json").write_text(harness.bundle.model_dump_json())
    (root / "config.json").write_text(harness.config.model_dump_json(by_alias=True))
    txn = root / "snapshot" / old._JOURNAL
    payloads = json.loads(txn.read_bytes())["next_payloads"] if txn.exists() else None
    usage = (
        json.loads(payloads[concurrent._USAGE])
        if payloads
        else old._rows(root / "snapshot" / concurrent._USAGE)[0]
    )
    metadata = {
        "mode": mode,
        "capacity": capacity,
        "calls": harness.calls,
        "durable": [
            [a["stage"], a["request_index"]]
            for a in usage["attempts"]
            if a["status"] == "succeeded"
        ],
        "attempts": usage["attempts"],
        "sources": {
            str(Path(m.__file__)): hashlib.sha256(
                Path(m.__file__).read_bytes()
            ).hexdigest()
            for m in (cp, gen, req)
        },
        "transaction_sha256": transactions,
        "snapshot_hashes": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "snapshot").iterdir()
            if p.is_file()
        },
    }
    if mode == "suffix":
        assert {("draft", i) for i in range(1, 4)}.issubset(
            map(tuple, metadata["durable"])
        ) and not old._rows(root / "snapshot" / old._RESPONSE)
    (root / "producer.json").write_text(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    _produce(capacity=int(sys.argv[1]), mode=sys.argv[2], root=Path(sys.argv[3]))
