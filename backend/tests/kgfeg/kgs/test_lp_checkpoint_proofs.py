"""Challenge invocation-local checkpoint proofs against mutable live dependencies."""

# Future Library
from __future__ import annotations

# Standard Library
import os

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_checkpoints as cp
from kgfeg.kgs import lp_generation as gen
from kgfeg.kgs import lp_requests as req
from kgfeg.page_ir_extraction.validators import QualityError
from tests.kgfeg.kgs import test_lp_concurrency as concurrent
from tests.kgfeg.kgs import test_lp_generation as fixtures
from tests.kgfeg.kgs import test_lp_orchestration as old


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid external model transport with restored synthetic settings.

    Parameters
    ----------
    monkeypatch
        Restoring patches.
    """
    if expected := os.environ.get("KGF_TEST_EXPECTED_SOURCE"):
        assert Path(cp.__file__).resolve().is_relative_to(Path(expected).resolve())
    cast(Any, concurrent._block_network).__wrapped__(monkeypatch)


def _store(
    *, monkeypatch: pytest.MonkeyPatch, root: Path
) -> cp.LPGenerationCheckpoints:
    """Run the production scheduler, then cold-load its complete synthetic history.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    root
        Temporary checkpoint directory.

    Returns
    -------
    LPGenerationCheckpoints
        Fully validated store with warm proofs.
    """
    harness = concurrent._Scenario(count=3, root=root)
    old._install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    population = req.build_lp_generation_requests(
        as_lc_bundle=harness.bundle, doc_key=fixtures._DOC_KEY, kg_config=harness.config
    )
    material = gen.lp_execution_material(
        kg_config=harness.config, population=population
    )
    return cp.LPGenerationCheckpoints(
        material=material, population=population, root=root
    )


def test_cache_history_is_bounded_by_current_positions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Repeated validation and equivalent encoding replacements do not retain versions.

    Parameters
    ----------
    monkeypatch
        Restoring offline call patch.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    before = store._payloads()
    sizes = (len(store._row_proofs), len(store._record_encodings))
    for _ in range(30):
        for row in store.rows["response"]:
            row.payload = dict(reversed(list(row.payload.items())))
        store._validate()
        assert store._payloads() == before
        assert (len(store._row_proofs), len(store._record_encodings)) == sizes
    store.rows["response"].pop()
    store._validate()
    store._payloads()
    assert len(store._row_proofs) == sizes[0] - 1
    assert len(store._record_encodings) == sizes[1] - 1


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict", "response"])
def test_changed_nested_record_revalidates(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """A nested payload edit cannot borrow an earlier record's semantic proof.

    Parameters
    ----------
    monkeypatch
        Offline seam and semantic observer.
    stage
        Altered completed stage.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    row = store.rows[stage][0]
    row.payload["unexpected"] = {"nested": [True]}
    row.payload_content_hash = cp.content_hash(row.payload)
    before = old._snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError):
        store._validate()
    assert old._snapshot(tmp_path) == before


@pytest.mark.parametrize(
    argnames="change",
    argvalues=["request", "producer", "checker", "model", "retry", "manifest"],
)
def test_changed_request_and_execution_material_revalidate(
    change: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Recompute semantic validation when any consumed request or execution value changes.

    Parameters
    ----------
    change
        Nested dependency edited in place.
    monkeypatch
        Restoring call seam and observer.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    calls = []
    original = store._validate_row

    def _observe(**kwargs: Any) -> None:
        """Observe real semantic validation without replacing its result.

        Parameters
        ----------
        kwargs
            Actual semantic-validation arguments.
        """
        calls.append((kwargs["stage"], kwargs["index"]))
        original(**kwargs)

    monkeypatch.setattr(name="_validate_row", target=store, value=_observe)
    if change == "request":
        store.population.requests[0].sfis[0].context.description.text += " changed"
    elif change in {"producer", "checker"}:
        store.material[change + "_instructions"] += " changed"
    elif change == "model":
        store.material["model_settings"]["temperature"] = 0.125
    elif change == "retry":
        store.material["retry_limits"]["draft"] += 1
    else:
        store.material["request_manifest"]["total_requests"] += 1
    try:
        store._validate()
    except (ValueError, QualityError):
        pass
    assert calls, "Changed material inherited semantic proof without revalidation."


def test_cold_load_inherits_no_prior_proofs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each invocation validates every saved stage even with matching file metadata.

    Parameters
    ----------
    monkeypatch
        Restoring call seam and semantic observer.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    calls = []
    original = cp.LPGenerationCheckpoints._validate_row

    def _observe(self: Any, **kwargs: Any) -> None:
        """Count original semantic checks.

        Parameters
        ----------
        self
            New invocation.
        kwargs
            Actual row arguments.
        """
        calls.append((kwargs["stage"], kwargs["index"]))
        original(self=self, **kwargs)

    monkeypatch.setattr(
        name="_validate_row", target=cp.LPGenerationCheckpoints, value=_observe
    )
    cp.LPGenerationCheckpoints(
        material=store.material,
        population=store.population,
        read_only=True,
        root=tmp_path,
    )
    assert len(calls) == 3 * len(store.population.requests)


def test_failed_validation_cannot_install_partial_proofs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Later validation failure leaves the previously accepted proof set unchanged.

    Parameters
    ----------
    monkeypatch
        Restoring call seam.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    proofs = deepcopy(store._row_proofs)
    store.material["retry_limits"]["draft"] += 1
    store.attempts.append(store.attempts[0].model_copy(deep=True))
    with pytest.raises(expected_exception=ValueError):
        store._validate()
    assert store._row_proofs == proofs


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "duplicate",
        "gap",
        "order",
        "pending_overlap",
        "missing_draft",
        "missing_verdict",
    ],
)
def test_order_and_prerequisite_edits_fail_closed(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prefix and dependency checks remain mandatory when row bytes are cached.

    Parameters
    ----------
    attack
        Altered position or prerequisite collection.
    monkeypatch
        Offline call seam.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    if attack == "duplicate":
        store.rows["draft"][1] = store.rows["draft"][0]
    elif attack == "gap":
        store.rows["draft"].pop(1)
    elif attack == "order":
        store.rows["draft"].reverse()
    elif attack == "pending_overlap":
        store.pending["response"][0] = store.rows["response"][0]
    elif attack == "missing_draft":
        store.rows["draft"].clear()
    else:
        store.rows["verdict"].clear()
    with pytest.raises(expected_exception=ValueError):
        store._validate()


@pytest.mark.parametrize(
    argnames="value",
    argvalues=[
        datetime(2026, 9, 14, tzinfo=timezone.utc),
        ("a",),
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_record_normalization_cannot_alias_cached_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: Any
) -> None:
    """Invalid Python values cannot become canonical cached JSON through coercion.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Synthetic checkpoint directory.
    value
        Unsupported or nonfinite nested live value.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    row = store.rows["response"][0]
    row.payload["probe"] = [value]
    with pytest.raises(expected_exception=(ValueError, TypeError)):
        store._record_bytes(key=("response", 0), record=row)


@pytest.mark.parametrize(argnames="simultaneous", argvalues=[False, True])
def test_resealed_disk_cannot_replace_last_validated_snapshot(
    monkeypatch: pytest.MonkeyPatch, simultaneous: bool, tmp_path: Path
) -> None:
    """Even identical live and resealed disk edits cannot replace validated byte authority.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    simultaneous
        Also replace live records to match the altered disk.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    rows = old._rows(tmp_path / old._RESPONSE)
    rows[0]["payload"]["judgments"][0]["rationale"] += " altered"
    rows[0]["payload_content_hash"] = cp.content_hash(rows[0]["payload"])
    (tmp_path / old._RESPONSE).write_bytes(b"".join(old._bytes(row) for row in rows))
    old._reseal(name=old._RESPONSE, root=tmp_path)
    if simultaneous:
        store.rows["response"][0] = cp._LPCheckpoint.model_validate(rows[0])
    before = old._snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError):
        store.verify_bytes()
    assert old._snapshot(tmp_path) == before


def test_scheduler_rejects_resealed_checkpoint_edit_during_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """External checkpoint edits cannot inherit warm proofs at outcome publication.

    Parameters
    ----------
    monkeypatch
        Offline call seam; checkpoint validators remain unmodified.
    tmp_path
        Synthetic scheduler output directory.
    """
    harness = concurrent._Scenario(capacity=1, count=3, root=tmp_path)
    before: dict[str, bytes] = {}

    def _tamper(*, index: int, stage: str) -> None:
        """Reseal an earlier response while the next real worker call is active.

        Parameters
        ----------
        index
            Active request position.
        stage
            Active producer or checker stage.
        """
        if index != 1 or stage != "draft":
            return
        rows = old._rows(tmp_path / old._RESPONSE)
        assert len(rows) == 1
        rows[0]["payload"]["judgments"][0]["rationale"] += " altered during call"
        rows[0]["payload_content_hash"] = cp.content_hash(rows[0]["payload"])
        (tmp_path / old._RESPONSE).write_bytes(
            b"".join(old._bytes(row) for row in rows)
        )
        old._reseal(name=old._RESPONSE, root=tmp_path)
        before.update(old._snapshot(tmp_path))

    harness.callback = _tamper
    old._install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(expected_exception=ValueError):
        harness._run()
    assert before and old._snapshot(tmp_path) == before
    assert harness.calls == [("draft", 0), ("verdict", 0), ("draft", 1)]
    with pytest.raises(expected_exception=ValueError):
        cp.validate_lp_checkpoint_format(tmp_path)


@pytest.mark.parametrize(argnames="dependency", argvalues=["draft", "verdict"])
def test_unchanged_response_revalidates_changed_prerequisite(
    dependency: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A response's unchanged bytes cannot exempt changed draft or verdict material.

    Parameters
    ----------
    dependency
        Stage dependency changed underneath a cached final response.
    monkeypatch
        Offline call seam.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    proof = store._row_proofs["response", 0]
    saved = store.rows[dependency][0]
    saved.payload["unexpected_nested"] = {"wrong": [1]}
    saved.payload_content_hash = cp.content_hash(saved.payload)
    with pytest.raises(expected_exception=(ValueError, QualityError)):
        store._validate_cached_row(
            index=0,
            material=proof[0],
            proofs={},
            requests={},
            row=store.rows["response"][0],
            stage="response",
        )


@pytest.mark.parametrize(argnames="capacity", argvalues=[1, 4])
def test_worker_owned_objects_cannot_mutate_durable_checkpoint_proofs(
    capacity: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retained call inputs and outputs cannot rewrite coordinator-owned history.

    Parameters
    ----------
    capacity
        Serial or default bounded scheduler capacity.
    monkeypatch
        Only the public external-call boundary is replaced.
    tmp_path
        Synthetic scheduler output directory.
    """
    harness = concurrent._Scenario(capacity=capacity, count=3, root=tmp_path)
    retained: list[tuple[dict[str, Any], Any]] = []
    mutations: list[int] = []

    def _call(**kwargs: Any) -> Any:
        """Mutate only prior worker objects whose results are already durable.

        Parameters
        ----------
        kwargs
            Actual copied worker inputs and dispatch guard.

        Returns
        -------
        Any
            Unmodified synthetic result for this call.
        """
        gate = kwargs["dispatch_guard"].__self__
        with gate.coordinate():
            saved = old._rows(tmp_path / old._DRAFT)
            saved += concurrent._read(tmp_path / concurrent._PENDING)["draft"]
            durable = {row["request_index"] for row in saved}
            for previous, output in retained:
                index = previous["request"].request_index
                if previous["draft"] is not None or index not in durable:
                    continue
                output.judgments[0].rationale = "mutated retained worker output"
                previous["request"].framework_title.text = (
                    "mutated retained worker input"
                )
                previous[
                    "kg_config"
                ].learning_progressions.retry.producer_max_retries = 99
                previous["model_config"].model = "openai:synthetic-mutated"
                mutations.append(index)
        output = harness._call(**kwargs)
        with gate.coordinate():
            retained.append((kwargs, output))
        return output

    monkeypatch.setattr(
        name="generate_learning_progressions_for_request", target=gen, value=_call
    )
    responses = harness._run()
    assert mutations and len(responses) == 3
    assert all(
        judgment.rationale == "Synthetic negative judgment for process testing."
        for response in responses
        for judgment in response.judgments
    )
    before = old._snapshot(tmp_path)
    cp.validate_lp_checkpoint_format(tmp_path)
    assert old._snapshot(tmp_path) == before
    receipt = concurrent._read(tmp_path / old._RECEIPT)
    assert harness._run() == responses
    assert len(harness.calls) == 6
    receipt["run_number"] += 1
    assert concurrent._read(tmp_path / old._RECEIPT) == receipt
    assert {
        name: payload
        for name, payload in old._snapshot(tmp_path).items()
        if name != old._RECEIPT
    } == {name: payload for name, payload in before.items() if name != old._RECEIPT}
