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
    """Repeated validation and edited record copies do not retain extra versions.

    Records only grow through validated store updates, so the caches hold exactly
    one proof per stage row and one encoding per row, attempt and failure.

    Parameters
    ----------
    monkeypatch
        Restoring offline call patch.
    tmp_path
        Synthetic checkpoint directory.
    """
    store = _store(monkeypatch=monkeypatch, root=tmp_path)
    before = store._payloads()
    rows = sum(len(store.rows[stage]) for stage in store.rows)
    sizes = (len(store._row_proofs), len(store._record_encodings))
    assert sizes == (rows, rows + len(store.attempts) + len(store.failures))
    for _ in range(30):
        # Reordered payload copies are equivalent edits that never reach the store.
        for row in store.rows["response"]:
            row.payload = dict(reversed(list(row.payload.items())))
        store._validate()
        assert store._payloads() == before
        assert (len(store._row_proofs), len(store._record_encodings)) == sizes


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict", "response"])
def test_changed_nested_record_revalidates(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """A nested payload edit cannot borrow an earlier record's semantic proof.

    A caller's edit of a returned copy never reaches the store's bytes or
    decisions; an edited record that does become authority is revalidated.

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
    before = old._snapshot(tmp_path)
    payloads = store._payloads()
    row = store.rows[stage][0]
    row.payload["unexpected"] = {"nested": [True]}
    row.payload_content_hash = cp.content_hash(row.payload)
    store._validate()
    store.verify_bytes(full=True)
    assert store._payloads() == payloads
    assert store.get_row(request_index=0, stage=stage) != row
    if stage != "response":
        with pytest.raises(expected_exception=ValueError, match="durably completed"):
            store.record_dispatch(request_index=0, stage=stage)
    # The same edit installed as the record's authority must fail validation.
    store._rows[stage][0] = row
    store._record_bytes(key=(stage, 0), record=row)
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

    The live request population is shared, so its edits force revalidation. The
    store keeps its own execution material: editing the returned copy changes
    nothing, and changed material is only ever presented by a new store, which
    rejects it before touching any file.

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
        try:
            store._validate()
        except (ValueError, QualityError):
            pass
        assert calls, "Changed material inherited semantic proof without revalidation."
        return
    captured = store.material
    material = store.material
    if change in {"producer", "checker"}:
        material[change + "_instructions"] += " changed"
    elif change == "model":
        material["model_settings"]["temperature"] = 0.125
    elif change == "retry":
        material["retry_limits"]["draft"] += 1
    else:
        material["request_manifest"]["total_requests"] += 1
    payloads = store._payloads()
    store._validate()
    assert store.material == captured != material
    assert store._payloads() == payloads
    before = old._snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError, match="material"):
        cp.LPGenerationCheckpoints(
            material=material, population=store.population, root=tmp_path
        )
    assert old._snapshot(tmp_path) == before


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
    # Drop one proof so the pass computes a fresh one before attempts fail.
    del store._row_proofs["response", 0]
    proofs = deepcopy(store._row_proofs)
    duplicate = store.attempts[0]
    position = len(store.attempts)
    store._attempts.append(duplicate)
    store._attempt_positions[duplicate.request_index].append(position)
    store._record_bytes(key=("attempt", position), record=duplicate)
    with pytest.raises(expected_exception=ValueError, match="attempt"):
        store._validate()
    assert store._row_proofs == proofs


def _seal_attack(*, attack: str, root: Path) -> str:
    """Write one prefix or dependency attack into the checkpoint files.

    Parameters
    ----------
    attack
        Altered position or prerequisite collection.
    root
        Completed synthetic checkpoint directory.

    Returns
    -------
    str
        Altered file, for the caller to reseal in the receipt.
    """
    if attack == "pending_overlap":
        value = concurrent._read(root / concurrent._PENDING)
        value["response"] = old._rows(root / old._RESPONSE)[:1]
        (root / concurrent._PENDING).write_bytes(old._bytes(value))
        return concurrent._PENDING
    name = old._VERDICT if attack == "missing_verdict" else old._DRAFT
    rows = old._rows(root / name)
    if attack == "duplicate":
        rows[1] = rows[0]
    elif attack == "gap":
        rows.pop(1)
    elif attack == "order":
        rows.reverse()
    elif attack in {"missing_draft", "missing_verdict"}:
        rows.clear()
    (root / name).write_bytes(b"".join(old._bytes(row) for row in rows))
    return name


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

    The read-only views refuse the edit, and the same edit sealed into the files
    fails closed at the next full verification (a new store) without changes.

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
    views: Any = store.rows
    with pytest.raises(expected_exception=(AttributeError, TypeError)):
        if attack == "duplicate":
            views["draft"][1] = store.rows["draft"][0]
        elif attack == "gap":
            views["draft"].pop(1)
        elif attack == "order":
            views["draft"].reverse()
        elif attack == "pending_overlap":
            pending: Any = store.pending
            pending["response"][0] = store.rows["response"][0]
        else:
            views["draft" if attack == "missing_draft" else "verdict"].clear()
    payloads = store._payloads()
    store._validate()
    assert store._payloads() == payloads
    name = _seal_attack(attack=attack, root=tmp_path)
    old._reseal(name=name, root=tmp_path)
    before = old._snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError):
        cp.LPGenerationCheckpoints(
            material=store.material, population=store.population, root=tmp_path
        )
    assert old._snapshot(tmp_path) == before


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
        # Live records cannot be replaced to match the altered disk.
        views: Any = store.rows
        with pytest.raises(expected_exception=TypeError):
            views["response"][0] = cp._LPCheckpoint.model_validate(rows[0])
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
    response = store._rows["response"][0]
    encoding = store._record_encodings["response", 0]
    saved = store.rows[dependency][0]
    saved.payload["unexpected_nested"] = {"wrong": [1]}
    saved.payload_content_hash = cp.content_hash(saved.payload)
    # The changed prerequisite becomes authority; the response keeps its bytes.
    store._rows[dependency][0] = saved
    store._record_bytes(key=(dependency, 0), record=saved)
    assert store._record_encodings["response", 0] == encoding
    with pytest.raises(expected_exception=(ValueError, QualityError)):
        store._validate_cached_row(
            index=0, proofs={}, requests={}, row=response, stage="response"
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
