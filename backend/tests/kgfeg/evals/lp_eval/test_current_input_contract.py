"""Reject unsupported evaluator inputs while retaining exact current material."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import hashlib
import json
import shutil
import subprocess

from pathlib import Path
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

from pydantic import TypeAdapter

# Package Library
from kgfeg.entries import evaluate_lps as entry
from kgfeg.evals.lp_eval import sampling
from kgfeg.evals.lp_eval.schemas import FrozenInputs, ReportProvenance
from tests.fixtures.lp_eval.snapshot_fixtures import build_snapshot
from tests.kgfeg.evals.lp_eval import test_projection_compatibility as projections
from tests.kgfeg.kgs import test_lp_checkpoint_formats as checkpoints

# Private pytest fixtures are used parameters.
# pylint: disable=useless-param-doc

_offline = projections._offline
_RECEIPT = "lp_generation_checkpoint_manifest.json"


def _copy(*, source: Path, target: Path) -> Path:
    """Copy test evidence and verify the valid starting contract.

    Parameters
    ----------
    source
        Module-local current-format source directory.
    target
        Isolated attack root.

    Returns
    -------
    Path
        Independently validated copy before mutation.
    """
    shutil.copytree(dst=target / "source", src=source.parent)
    directory = target / "source/kgs"
    sampling.validate_lp_snapshot(projections._run(directory))
    return directory


def _dump(value: Any) -> bytes:
    """Encode independently hashed synthetic JSON.

    Parameters
    ----------
    value
        Test-owned material.

    Returns
    -------
    bytes
        Canonical JSON with a terminating newline.
    """
    return (
        json.dumps(ensure_ascii=False, obj=value, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def _reject(*, directory: Path, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Require rejection before publication, preserving source and prior evidence.

    Parameters
    ----------
    directory
        Mutated synthetic completed candidate.
    monkeypatch
        Restoring schedule publication guards.
    root
        Test-only repository with preserved prior evidence.
    """
    prior = root / "results/lp_evals/prior-evidence.txt"
    prior.parent.mkdir(exist_ok=True, parents=True)
    prior.write_bytes(b"Synthetic immutable earlier evidence\n")
    before = projections._state(root)
    inventory = sampling.discover_lp_runs(
        evaluation_root=prior.parent, results_root=directory
    )
    assert inventory.runs[0].status == "completed_candidate"
    guard = Mock(side_effect=AssertionError("Rejected inputs reached publication"))
    for name in (
        "prepare_evaluation_schedule",
        "persist_evaluation_schedule",
        "run_evaluation",
    ):
        monkeypatch.setattr(name=name, target=entry, value=guard)
    with pytest.raises(sampling.LPSnapshotError):
        sampling.validate_lp_snapshot(inventory.runs[0])
    with pytest.raises(sampling.LPSnapshotError):
        sampling.freeze_lp_inputs(inventory=inventory, repository_root=root)
    with pytest.raises(sampling.LPSnapshotError):
        entry.prepare_evaluation(
            new_invocation=True, repository_root=root, results_root=directory
        )
    guard.assert_not_called()
    assert projections._state(root) == before


@pytest.fixture(scope="module")
def _source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Prepare a current snapshot independent of production results.

    Parameters
    ----------
    tmp_path_factory
        Temporary test storage factory.

    Returns
    -------
    Path
        Complete native journal and current wire fixture.
    """
    return build_snapshot(
        count=5, identity=91001, root=tmp_path_factory.mktemp("current-contract")
    )


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "compatibility_both_zero",
        "compatibility_failure_zero",
        "compatibility_stage_zero",
        "material_capacity_missing",
        "material_capacity_bool",
        "material_capacity_null",
        "material_capacity_string",
        "material_capacity_zero",
        "missing_both_journals",
        "missing_pending",
        "missing_usage",
        "partial_pending",
        "prefix_complete",
        "receipt_unknown_field",
        "unauthenticated_pending",
        "unauthenticated_usage",
        "upgraded_complete",
        "upgraded_with_native_attempts",
        "usage_all_attempts_missing",
        "usage_draft_attempt_missing",
        "usage_verdict_attempt_missing",
        "usage_capacity_missing",
        "usage_capacity_bool",
        "usage_capacity_null",
        "usage_capacity_string",
        "usage_capacity_zero",
        "usage_unknown_field",
        "transaction_old_prefix",
        "transaction_mixed_upgrade",
        "transaction_next_failure_zero",
    ],
)
def test_checkpoint_contract_rejects_before_effects(
    _source: Path, attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject corrupted journals even when their receipt hashes are resealed.

    Parameters
    ----------
    _source
        Valid native execution evidence.
    attack
        One absent, partial, legacy or unauthenticated checkpoint property.
    monkeypatch
        Restoring publication and call guards.
    tmp_path
        Isolated evidence copy.
    """
    directory = _copy(source=_source, target=tmp_path)
    checkpoints._attack(attack=attack, root=directory)
    _reject(directory=directory, monkeypatch=monkeypatch, root=tmp_path)


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "capacity_bool",
        "capacity_float",
        "capacity_missing",
        "capacity_negative",
        "capacity_null",
        "capacity_string",
        "capacity_zero",
        "capacity_mismatch",
        "defaulted_field",
        "unknown_field",
        "wrong_overwrite_hash",
    ],
)
def test_configuration_requires_exact_captured_effective_material(
    _source: Path, attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Do not default, coerce, normalize or repair captured effective configuration.

    Parameters
    ----------
    _source
        Complete valid baseline.
    attack
        Single unsupported configuration change.
    monkeypatch
        Restoring publication and call guards.
    tmp_path
        Isolated evidence root.
    """
    directory = _copy(source=_source, target=tmp_path)
    path = directory / "kg_run.json"
    run = json.loads(path.read_bytes())
    lp = run["extra"]["lp"]
    if attack == "capacity_missing":
        del lp["max_concurrent_requests"]
    elif attack.startswith("capacity_"):
        lp["max_concurrent_requests"] = {
            "capacity_bool": True,
            "capacity_float": 4.0,
            "capacity_negative": -1,
            "capacity_null": None,
            "capacity_string": "4",
            "capacity_zero": 0,
            "capacity_mismatch": 1,
        }[attack]
    elif attack == "defaulted_field":
        del run["extra"]["as"]["grade_level_mapping"]
    elif attack == "unknown_field":
        lp["checkpoint_policy"] = "compatible"
    else:
        receipt = json.loads((directory / _RECEIPT).read_bytes())
        receipt["material"]["config_content_hash"] = "0" * 64
        (directory / _RECEIPT).write_bytes(_dump(receipt))
    path.write_bytes(_dump(run))
    _reject(directory=directory, monkeypatch=monkeypatch, root=tmp_path)


@pytest.mark.parametrize(
    argnames="original,replacement",
    argvalues=[
        (True, 1),
        (False, 0),
        ({"nested": [True]}, {"nested": [1]}),
        ([{"nested": False}], [{"nested": 0}]),
    ],
)
def test_current_projection_comparison_preserves_nested_json_types(
    _source: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: Any,
    replacement: Any,
    tmp_path: Path,
) -> None:
    """Exercise the projection comparator with equal-Python, different-JSON values.

    Parameters
    ----------
    _source
        Fully valid current snapshot.
    monkeypatch
        Isolated serializer-result seam for nested JSON comparator coverage.
    original
        Trusted synthetic serializer value.
    replacement
        Numerically equal attack with a different JSON type.
    tmp_path
        Disposable source directory.
    """
    directory = _copy(source=_source, target=tmp_path)
    serialize = sampling.build_lp_delivery_records

    def _records(**kwargs: Any) -> Any:
        """Add one nested test value at the serializer-to-comparator boundary.

        Parameters
        ----------
        kwargs
            Unchanged real serializer inputs.

        Returns
        -------
        Any
            Real wire rows plus the isolated comparator probe.
        """
        nodes, edges = serialize(**kwargs)
        nodes[0]["properties"]["comparatorProbe"] = original
        return nodes, edges

    monkeypatch.setattr(
        name="build_lp_delivery_records", target=sampling, value=_records
    )
    path = directory / "as_lc_lp_nodes.jsonl"
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    rows[0]["properties"]["comparatorProbe"] = original
    path.write_bytes(b"".join(_dump(row) for row in rows))
    sampling.validate_lp_snapshot(projections._run(directory))
    assert original == replacement
    assert _dump(original) != _dump(replacement)
    rows[0]["properties"]["comparatorProbe"] = replacement
    path.write_bytes(b"".join(_dump(row) for row in rows))
    before = projections._state(directory.parent)
    with pytest.raises(
        expected_exception=sampling.LPSnapshotError, match="Combined node projection"
    ):
        sampling.validate_lp_snapshot(projections._run(directory))
    assert projections._state(directory.parent) == before


@pytest.mark.parametrize(argnames="capacity", argvalues=[None, 1, 4])
@pytest.mark.parametrize(argnames="overwrite", argvalues=[False, True])
def test_current_runtime_defaults_overwrite_recovery_and_optional_provenance(
    capacity: int | None,
    monkeypatch: pytest.MonkeyPatch,
    overwrite: bool,
    tmp_path: Path,
) -> None:
    """Recover either overwrite hash without Git or mandatory producing provenance.

    Parameters
    ----------
    capacity
        Omitted or explicit supported runtime capacity.
    monkeypatch
        Restoring Git-query guard.
    overwrite
        Omitted metadata flag recovered by exact configuration digest.
    tmp_path
        Isolated execution and freeze root.
    """
    directory = build_snapshot(
        capacity=capacity,
        count=4,
        identity=92001,
        overwrite=overwrite,
        root=tmp_path / "inputs",
    )
    guard = Mock(side_effect=AssertionError("Evaluator queried Git"))
    for name in ("Popen", "check_output", "run"):
        monkeypatch.setattr(name=name, target=subprocess, value=guard)
    for known in (False, True):
        path = directory / "kg_run.json"
        run = json.loads(path.read_bytes())
        if known:
            run["extra"]["producing_git_sha"] = "a" * 40
            path.write_bytes(_dump(run))
        before = projections._state(directory.parent)
        inventory = sampling.discover_lp_runs(
            evaluation_root=tmp_path / "results/lp_evals", results_root=directory
        )
        frozen = sampling.freeze_lp_inputs(
            inventory=inventory, repository_root=tmp_path
        )
        snapshot = sampling.load_frozen_lp_inputs(frozen)[0]
        config = json.loads(snapshot.config_json)
        assert config["overwrite"] is overwrite
        assert config["lp"]["max_concurrent_requests"] == (
            4 if capacity is None else capacity
        )
        captured = next(
            item for item in snapshot.artifacts if item.name == "kg_run.json"
        )
        assert captured.payload == path.read_bytes()
        assert ("producing_git_sha" in json.loads(captured.payload)["extra"]) is known
        assert (
            captured.fingerprint.sha256 == hashlib.sha256(captured.payload).hexdigest()
        )
        assert projections._state(directory.parent) == before
    guard.assert_not_called()


@pytest.mark.parametrize(
    argnames="label", argvalues=["historical_prefix", "journal_bearing"]
)
@pytest.mark.parametrize(
    argnames="mutation", argvalues=["capacity", "prefix", "projection"]
)
def test_old_frozen_inputs_cannot_be_relabelled_current(
    _source: Path, label: str, mutation: str, tmp_path: Path
) -> None:
    """Reject byte-authenticated obsolete synthetic manifests regardless of label.

    Parameters
    ----------
    _source
        Original current snapshot used only for disposable evidence.
    label
        Historical or misleading current format label.
    mutation
        Unsupported dimension in the authenticated test manifest.
    tmp_path
        Synthetic repository with untouched prior frozen evidence.
    """
    directory = _copy(source=_source, target=tmp_path)
    inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "results/lp_evals", results_root=directory
    )
    frozen = sampling.freeze_lp_inputs(inventory=inventory, repository_root=tmp_path)
    prior = projections._state(frozen.manifest_path.parent)
    manifest = json.loads(frozen.manifest_path.read_bytes())
    snapshot = manifest["snapshots"][0]
    if mutation == "projection":
        projections._format(directory=directory, projection="internal")
    elif mutation == "prefix":
        checkpoints._attack(attack="prefix_complete", root=directory)
    else:
        path = directory / "kg_run.json"
        run = json.loads(path.read_bytes())
        del run["extra"]["lp"]["max_concurrent_requests"]
        path.write_bytes(_dump(run))
        config = json.loads(snapshot["config_json"])
        del config["lp"]["max_concurrent_requests"]
        snapshot["config_json"] = _dump(config).decode().rstrip("\n")
    current_inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "results/lp_evals", results_root=directory
    )
    manifest["inventory"] = TypeAdapter(type(current_inventory)).dump_python(
        current_inventory, mode="json"
    )
    snapshot["run"] = manifest["inventory"]["runs"][0]
    snapshot["checkpoint_format"] = label
    copies = {}
    artifacts = []
    for artifact in snapshot["artifacts"]:
        path = Path(artifact["fingerprint"]["path"])
        if not path.exists():
            snapshot["absent_artifacts"].append(artifact["name"])
            continue
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        artifact["fingerprint"].update(sha256=digest, size_bytes=len(payload))
        copies[digest] = payload
        artifacts.append(artifact)
    snapshot["artifacts"] = artifacts
    snapshot["absent_artifacts"].sort()
    payload = _dump(manifest)
    digest = hashlib.sha256(payload).hexdigest()
    destination = frozen.manifest_path.parent.parent / digest
    destination.mkdir()
    for name, content in copies.items():
        (destination / name).write_bytes(content)
    (destination / "manifest.json").write_bytes(payload)
    obsolete = FrozenInputs(
        content_hash=digest, manifest_path=destination / "manifest.json"
    )
    before = projections._state(tmp_path)
    expected = (
        "journal_bearing"
        if label == "historical_prefix"
        else "Invalid completed LP snapshot"
    )
    with pytest.raises(expected_exception=sampling.LPSnapshotError, match=expected):
        sampling.load_frozen_lp_inputs(obsolete)
    assert projections._state(tmp_path) == before
    assert projections._state(frozen.manifest_path.parent) == prior


@pytest.mark.parametrize(
    argnames="name",
    argvalues=[
        "lp_generation_requests.jsonl",
        "lp_generation_requests_manifest.json",
        "lp_candidate_pairs.jsonl",
        "lp_relationship_provenance.json",
    ],
)
def test_reconstruction_rejects_changed_request_and_provenance_material(
    _source: Path, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Retain current request byte matching and authoritative provenance reconstruction.

    Parameters
    ----------
    _source
        Valid current fixture.
    monkeypatch
        Restoring publication and call guards.
    name
        Current artifact whose bytes or provenance are independently changed.
    tmp_path
        Isolated evidence root.
    """
    directory = _copy(source=_source, target=tmp_path)
    path = directory / name
    if name == "lp_relationship_provenance.json":
        value = json.loads(path.read_bytes())

        first = next(iter(value.values()))
        assert first["source_framework"]["title"]
        first["source_framework"]["title"] = "Changed source title"
        path.write_bytes(_dump(value))
    else:
        # Semantically identical whitespace still violates exact captured requests.
        path.write_bytes(path.read_bytes() + b" ")
    _reject(directory=directory, monkeypatch=monkeypatch, root=tmp_path)


def test_resume_rejects_changed_captured_capacity_before_transport(
    _source: Path, tmp_path: Path
) -> None:
    """Retain schedules and caches unchanged when selected material becomes invalid.

    Parameters
    ----------
    _source
        Current fixture with real frozen selection and schedule.
    tmp_path
        Isolated invocation directory.
    """
    directory = _copy(source=_source, target=tmp_path)
    reference = entry.prepare_evaluation(
        repository_root=tmp_path, results_root=directory
    )
    path = directory / "kg_run.json"
    run = json.loads(path.read_bytes())
    del run["extra"]["lp"]["max_concurrent_requests"]
    path.write_bytes(_dump(run))
    before = projections._state(tmp_path)
    guard = Mock(
        side_effect=AssertionError("Invalid resume reached provider construction")
    )
    with pytest.raises(ValueError):
        entry.prepare_evaluation(
            repository_root=tmp_path,
            results_root=directory,
            resume_manifest=reference.manifest_path,
        )
    with pytest.raises(ValueError):
        asyncio.run(
            entry.run_evaluation(
                provenance=ReportProvenance(evidence_mode="development"),
                reference=reference,
                transport_factory=guard,
            )
        )
    guard.assert_not_called()
    assert projections._state(tmp_path) == before
