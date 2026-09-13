"""Reject unsupported checkpoint evidence before effects and retain native recovery."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import socket

from copy import deepcopy
from itertools import product
from pathlib import Path
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

# Package Library
from kgfeg.config import Settings
from kgfeg.entries import create_kgs
from kgfeg.kgs import (
    lp_artifacts,
    lp_checkpoints,
    lp_export,
    lp_finalization,
    lp_generation,
)
from kgfeg.kgs.llm import KGUsageTracker
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.lp_requests import build_lp_generation_requests
from kgfeg.kgs.utils import KGDirs
from tests.kgfeg.kgs import test_create_kgs_lp as _entry
from tests.kgfeg.kgs import test_lp_concurrency as _concurrent
from tests.kgfeg.kgs import test_lp_orchestration as _storage
from tests.kgfeg.kgs import test_lp_reuse as _reuse

_PENDING = "lp_generation_pending_completions.json"
_RECEIPT = "lp_generation_checkpoint_manifest.json"
_TRANSACTION = "lp_generation_checkpoint_transaction.json"
_USAGE = "lp_generation_usage.json"
_STAGES = {
    "draft": "lp_generation_draft_responses.jsonl",
    "response": "lp_generation_responses.jsonl",
    "verdict": "lp_generation_validation_verdicts.jsonl",
}
_CHECKPOINTS = tuple(
    sorted(
        [*_STAGES.values(), "lp_generation_failures.json", _PENDING, _RECEIPT, _USAGE]
    )
)
_OPERATIONS = (
    "archive",
    "build",
    "build_overwrite",
    "compile",
    "compile_overwrite",
    "create",
    "create_overwrite",
    "finalize",
    "generate",
    "generate_overwrite",
    "preflight",
    "read_artifacts",
    "relationships",
    "reuse",
    "store",
    "store_read_only",
    "validate_claims",
    "write_artifacts",
)
_ATTACKS = (
    "compatibility_failure_zero",
    "compatibility_stage_zero",
    "compatibility_both_zero",
    "failure_missing_attempt",
    "material_capacity_missing",
    "material_capacity_bool",
    "material_capacity_null",
    "material_capacity_string",
    "material_capacity_zero",
    "missing_both_journals",
    "missing_pending",
    "missing_receipt",
    "missing_usage",
    "partial_pending",
    "prefix_complete",
    "prefix_empty",
    "prefix_partial",
    "prefix_stale_material",
    "receipt_extra_artifact",
    "receipt_unknown_field",
    "unauthenticated_pending",
    "unauthenticated_usage",
    "upgraded_complete",
    "upgraded_with_native_attempts",
    "usage_all_attempts_missing",
    "usage_attempts_missing",
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
    "transaction_partial_predecessor",
    "transaction_visible_legacy_usage",
    "transaction_visible_prefix_receipt",
    "transaction_next_failure_zero",
    "transaction_next_stage_zero",
    "transaction_next_missing_attempt",
    "transaction_next_missing_capacity",
    "transaction_next_missing_journal",
    "transaction_unexplained_missing_journal",
    "transaction_unknown_field",
)


def _attack(*, attack: str, root: Path) -> None:  # pylint: disable=R0912, R0915, R1260
    """Alter one independent format or accounting property in synthetic evidence.

    Parameters
    ----------
    attack
        Named incompatible format or deliberately missing execution evidence.
    root
        Test-owned complete native checkpoint and release directory.
    """
    receipt = _json(root / _RECEIPT)
    usage = _json(root / _USAGE)
    if attack.startswith("transaction_"):
        _attack_transaction(attack=attack, root=root)
        return
    if attack.startswith("compatibility_"):
        if attack in {"compatibility_stage_zero", "compatibility_both_zero"}:
            usage["legacy_stage_counts"] = dict.fromkeys(_STAGES, 0)
        if attack in {"compatibility_failure_zero", "compatibility_both_zero"}:
            usage["legacy_failure_count"] = 0
    elif attack.startswith("material_capacity_"):
        key = attack.removeprefix("material_capacity_")
        if key == "missing":
            del receipt["material"]["max_concurrent_requests"]
        else:
            receipt["material"]["max_concurrent_requests"] = {
                "bool": True,
                "null": None,
                "string": "1",
                "zero": 0,
            }[key]
        receipt["execution_content_hash"] = _hash(_bytes(receipt["material"]))
    elif attack.startswith("prefix_"):
        for name in (_PENDING, _USAGE):
            (root / name).unlink()
            del receipt["artifact_byte_hashes"][name]
        if attack in {"prefix_empty", "prefix_partial"}:
            for stage, name in _STAGES.items():
                payload = (
                    (root / name).read_bytes()
                    if attack == "prefix_partial" and stage == "draft"
                    else b""
                )
                (root / name).write_bytes(payload)
                receipt["artifact_byte_hashes"][name] = _hash(payload)
                receipt["stage_counts"][stage] = len(payload.splitlines())
            receipt["status"] = "incomplete"
        if attack == "prefix_stale_material":
            receipt["material"]["config_content_hash"] = "0" * 64
            receipt["execution_content_hash"] = _hash(_bytes(receipt["material"]))
        (root / _RECEIPT).write_bytes(_bytes(receipt))
        return
    elif attack in {
        "missing_both_journals",
        "missing_pending",
        "missing_usage",
        "missing_receipt",
    }:
        names = {
            "missing_both_journals": (_PENDING, _USAGE),
            "missing_pending": (_PENDING,),
            "missing_usage": (_USAGE,),
            "missing_receipt": (_RECEIPT,),
        }[attack]
        for name in names:
            (root / name).unlink()
        return
    elif attack == "partial_pending":
        (root / _PENDING).write_bytes(b'{"draft":[]}\n')
        receipt["artifact_byte_hashes"][_PENDING] = _hash(
            (root / _PENDING).read_bytes()
        )
    elif attack.startswith("unauthenticated_"):
        del receipt["artifact_byte_hashes"][
            _PENDING if attack.endswith("pending") else _USAGE
        ]
    elif attack == "receipt_extra_artifact":
        receipt["artifact_byte_hashes"]["unknown.json"] = "0" * 64
    elif attack == "receipt_unknown_field":
        receipt["format_version"] = "unknown"
    elif attack.startswith("upgraded_"):
        usage["legacy_stage_counts"] = {
            stage: (
                receipt["stage_counts"][stage] if attack == "upgraded_complete" else 1
            )
            for stage in _STAGES
        }
        usage["legacy_failure_count"] = len(_json(root / "lp_generation_failures.json"))
        usage["attempts"] = (
            []
            if attack == "upgraded_complete"
            else [row for row in usage["attempts"] if row["request_index"] > 0]
        )
        assert not any(_json(root / _PENDING).values())
        if attack == "upgraded_with_native_attempts":
            assert usage["attempts"]
    elif attack == "failure_missing_attempt":
        assert _json(root / "lp_generation_failures.json")
        usage["attempts"] = [
            row for row in usage["attempts"] if row["status"] != "failed"
        ]
    elif attack == "usage_all_attempts_missing":
        usage["attempts"] = []
    elif attack == "usage_attempts_missing":
        del usage["attempts"]
    elif attack in {"usage_draft_attempt_missing", "usage_verdict_attempt_missing"}:
        stage = "draft" if attack == "usage_draft_attempt_missing" else "verdict"
        usage["attempts"] = [row for row in usage["attempts"] if row["stage"] != stage]
    elif attack.startswith("usage_capacity_"):
        key = attack.removeprefix("usage_capacity_")
        if key == "missing":
            del usage["max_concurrent_requests"]
        else:
            usage["max_concurrent_requests"] = {
                "bool": True,
                "null": None,
                "string": "1",
                "zero": 0,
            }[key]
    elif attack == "usage_unknown_field":
        usage["format_version"] = "unknown"
    else:
        raise AssertionError(attack)
    (root / _USAGE).write_bytes(_bytes(usage))
    if _USAGE in receipt["artifact_byte_hashes"]:
        receipt["artifact_byte_hashes"][_USAGE] = _hash(_bytes(usage))
    (root / _RECEIPT).write_bytes(_bytes(receipt))


def _attack_transaction(  # pylint: disable=R0912, R1260
    *, attack: str, root: Path
) -> None:
    """Construct old, mixed, or malformed transactions with independently sealed bytes.

    Parameters
    ----------
    attack
        Invalid predecessor or next-snapshot property.
    root
        Test-owned native execution evidence.
    """
    payloads = {name: (root / name).read_text() for name in _CHECKPOINTS}
    previous: dict[str, str | None] = {
        name: _hash(payload.encode()) for name, payload in payloads.items()
    }
    receipt = json.loads(payloads[_RECEIPT])
    usage = json.loads(payloads[_USAGE])
    if attack == "transaction_old_prefix":
        for name in (_PENDING, _USAGE):
            del payloads[name], previous[name], receipt["artifact_byte_hashes"][name]
    elif attack == "transaction_mixed_upgrade":
        for name in (_PENDING, _USAGE):
            previous[name] = None
            (root / name).unlink()
    elif attack == "transaction_partial_predecessor":
        previous[_RECEIPT] = None
        (root / _RECEIPT).unlink()
    elif attack == "transaction_visible_legacy_usage":
        visible = deepcopy(usage)
        visible["legacy_stage_counts"] = dict.fromkeys(_STAGES, 0)
        (root / _USAGE).write_bytes(_bytes(visible))
        previous[_USAGE] = _hash(_bytes(visible))
    elif attack == "transaction_visible_prefix_receipt":
        visible = deepcopy(receipt)
        for name in (_PENDING, _USAGE):
            del visible["artifact_byte_hashes"][name]
        (root / _RECEIPT).write_bytes(_bytes(visible))
        previous[_RECEIPT] = _hash(_bytes(visible))
    elif attack == "transaction_next_failure_zero":
        usage["legacy_failure_count"] = 0
    elif attack == "transaction_next_stage_zero":
        usage["legacy_stage_counts"] = dict.fromkeys(_STAGES, 0)
    elif attack == "transaction_next_missing_attempt":
        usage["attempts"] = []
    elif attack == "transaction_next_missing_capacity":
        del usage["max_concurrent_requests"]
    elif attack == "transaction_next_missing_journal":
        del payloads[_PENDING]
    elif attack == "transaction_unexplained_missing_journal":
        (root / _PENDING).unlink()
    elif attack != "transaction_unknown_field":
        raise AssertionError(attack)
    if _USAGE in payloads:
        payloads[_USAGE] = _bytes(usage).decode()
        receipt["artifact_byte_hashes"][_USAGE] = _hash(_bytes(usage))
    payloads[_RECEIPT] = _bytes(receipt).decode()
    transaction = {"next_payloads": payloads, "previous_byte_hashes": previous}
    if attack == "transaction_unknown_field":
        transaction["format_version"] = "unknown"
    (root / _TRANSACTION).write_bytes(_bytes(transaction))


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Forbid real network transport and supply a fixed synthetic model binding.

    Parameters
    ----------
    monkeypatch
        Restoring network and model patches.

    Returns
    -------
    Mock
        Counter proving no socket dispatch occurred during setup or rejection.
    """
    guard = Mock(side_effect=AssertionError("Checkpoint format tests are offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")
    return guard


def _bytes(value: Any) -> bytes:
    """Serialize an independent canonical JSON payload.

    Parameters
    ----------
    value
        JSON-compatible synthetic material.

    Returns
    -------
    bytes
        Sorted compact UTF-8 JSON with one terminal newline.
    """
    return (
        json.dumps(ensure_ascii=False, obj=value, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def _hash(payload: bytes) -> str:
    """Hash actual evidence bytes independently of production helpers.

    Parameters
    ----------
    payload
        Exact stored bytes.

    Returns
    -------
    str
        SHA-256 byte digest.
    """
    return hashlib.sha256(payload).hexdigest()


def _invoke(
    *,
    harness: _entry._Harness,
    material: Any,
    operation: str,
    population: Any,
    relationships: Any,
) -> None:
    """Exercise a real effect-capable entry point against the same rejected evidence.

    Parameters
    ----------
    harness
        Upstream fixture and real pipeline observers.
    material
        Original validated execution identity.
    operation
        Public reader/writer, pipeline entry, or checkpoint recovery boundary.
    population
        Original validated complete request population.
    relationships
        Authentic standalone graph inputs captured before the attack.
    """
    kwargs = {
        "as_lc_bundle": harness.bundle,
        "doc_key": _entry._DOC_KEY,
        "kg_config": harness.config,
        "kg_dirs": KGDirs(root=harness.root),
    }
    overwrite = operation.endswith("_overwrite")
    if operation.startswith("create"):
        harness.config.overwrite = overwrite
        harness._save_config()
        create_kgs.create(harness.config_path)
    elif operation.startswith("build"):
        harness.config.overwrite = overwrite
        create_kgs.build_kgs(
            config=harness.config,
            document_ir_fp=harness.document,
            expected_doc_key=_entry._DOC_KEY,
            kg_dirs=kwargs["kg_dirs"],
            usage_tracker=KGUsageTracker(),
        )
    elif operation.startswith("generate"):
        lp_generation.generate_learning_progressions(
            **kwargs, overwrite=overwrite, usage_tracker=KGUsageTracker()
        )
    elif operation.startswith("compile"):
        lp_export.compile_as_lc_lp_kg(**kwargs, overwrite=overwrite)
    elif operation.startswith("store"):
        lp_checkpoints.LPGenerationCheckpoints(
            material=material,
            population=population,
            read_only=operation == "store_read_only",
            root=harness.root,
        )
    elif operation == "write_artifacts":
        lp_artifacts.write_lp_artifacts(**kwargs, relationships=relationships)
    elif operation == "archive":
        lp_checkpoints.archive_lp_generation_artifacts(harness.root)
    elif operation == "preflight":
        lp_checkpoints.validate_lp_checkpoint_format(harness.root)
    else:
        functions = {
            "finalize": lp_finalization.finalize_learning_progressions,
            "read_artifacts": lp_artifacts.read_lp_artifacts,
            "relationships": lp_finalization.build_lp_relationships,
            "reuse": lp_export.reuse_as_lc_lp_kg,
            "validate_claims": lp_finalization.validate_lp_final_claims_artifact,
        }
        functions[operation](**kwargs)


def _json(path: Path) -> Any:
    """Read synthetic JSON evidence without normalizing its source file.

    Parameters
    ----------
    path
        Test-owned artifact.

    Returns
    -------
    Any
        Decoded JSON value.
    """
    return json.loads(path.read_bytes())


def _state(root: Path) -> dict[str, Any]:
    """Capture every directory entry and detect even identical-byte file replacements.

    Parameters
    ----------
    root
        Synthetic evidence root.

    Returns
    -------
    dict[str, Any]
        Relative membership, file bytes, modification times and inode identities.
    """
    return {
        str(path.relative_to(root)): (
            ("directory", path.stat().st_ino)
            if path.is_dir()
            else (
                "file",
                path.read_bytes(),
                path.stat().st_mtime_ns,
                path.stat().st_ino,
            )
        )
        for path in root.rglob("*")
    }


@pytest.mark.parametrize(argnames="phase", argvalues=["initialize", "update"])
def test_current_transactions_recover_every_authenticated_old_new_file_mixture(
    _block_network: Mock, monkeypatch: pytest.MonkeyPatch, phase: str, tmp_path: Path
) -> None:
    """Validate before repair, then recover every binary mixture of seven native files.

    Parameters
    ----------
    monkeypatch
        Deterministic proposal and atomic-write fault seams.
    phase
        All-absent initialization predecessors or complete update predecessors.
    tmp_path
        Isolated preparation and 128 independent recovery directories.
    """
    source = tmp_path / "source"
    harness = _concurrent._Scenario(capacity=1, count=2, root=source)
    _storage._install(harness=harness, monkeypatch=monkeypatch)
    if phase == "update":
        harness._run()
    original = lp_checkpoints._atomic_write
    captured: dict[str, Any] = {}

    def _write(*, path: Path, payload: bytes) -> None:
        """Stop just after the intended next snapshot becomes durable.

        Parameters
        ----------
        path
            Real write destination.
        payload
            Real checkpoint transaction bytes.
        """
        original(path=path, payload=payload)
        if path.name == _TRANSACTION:
            captured.update(json.loads(payload))
            raise _storage._Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="_atomic_write", target=lp_checkpoints, value=_write)
        with pytest.raises(_storage._Interruption):
            harness._run()
    next_payloads = {
        name: payload.encode() for name, payload in captured["next_payloads"].items()
    }
    assert set(next_payloads) == set(_CHECKPOINTS)
    assert all(
        value is None for value in captured["previous_byte_hashes"].values()
    ) == (phase == "initialize")
    previous = {
        name: (source / name).read_bytes() if (source / name).exists() else None
        for name in _CHECKPOINTS
    }
    for name in _CHECKPOINTS:
        payload = previous[name]
        assert captured["previous_byte_hashes"][name] == (
            _hash(payload) if payload is not None else None
        )
    population = build_lp_generation_requests(
        as_lc_bundle=harness.bundle, doc_key=_entry._DOC_KEY, kg_config=harness.config
    )
    material = json.loads(next_payloads[_RECEIPT])["material"]
    expected_usage = json.loads(next_payloads[_USAGE])
    assert "legacy_stage_counts" not in expected_usage
    assert "legacy_failure_count" not in expected_usage
    before_calls = list(harness.calls)
    for number, choices in enumerate(product((False, True), repeat=len(_CHECKPOINTS))):
        root = tmp_path / f"mixture-{number}"
        root.mkdir()
        for name in _storage._INPUTS:
            (root / name).write_bytes((source / name).read_bytes())
        for name, use_next in zip(_CHECKPOINTS, choices, strict=True):
            payload = next_payloads[name] if use_next else previous[name]
            if payload is not None:
                (root / name).write_bytes(payload)
        (root / _TRANSACTION).write_bytes(_bytes(captured))
        before = _state(root)
        lp_checkpoints.validate_lp_checkpoint_format(root)
        assert _state(root) == before
        lp_checkpoints.LPGenerationCheckpoints(
            material=material, population=population, root=root
        )
        assert not (root / _TRANSACTION).exists()
        assert {
            name: (root / name).read_bytes() for name in _CHECKPOINTS
        } == next_payloads
        assert _json(root / _USAGE) == expected_usage
        recovered = _state(root)
        lp_checkpoints.LPGenerationCheckpoints(
            material=material, population=population, root=root
        )
        assert _state(root) == recovered
    assert harness.calls == before_calls
    _block_network.assert_not_called()


@pytest.mark.parametrize(
    argnames="name",
    argvalues=[
        "as_lc_lp_kg_bundle.json",
        "as_lc_lp_nodes.jsonl",
        "lp_final_claims.json",
        "lp_generation_failures.json",
        _PENDING,
        _USAGE,
    ],
)
def test_partial_stores_and_orphaned_outputs_cannot_initialize_new_production(
    _block_network: Mock, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Existing output fragments cannot be mistaken for a fresh production directory.

    Parameters
    ----------
    monkeypatch
        Offline phase observers.
    name
        Lone checkpoint or final output without authenticated companions.
    tmp_path
        Isolated incomplete production directory.
    """
    harness = _entry._Harness(root=tmp_path)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    (harness.root / name).write_bytes(b"{}\n")
    before = _state(harness.root)
    # The low-level checkpoint constructor initializes explicitly supplied populations;
    # production entry points own discovery of orphaned final outputs.
    for operation in (name for name in _OPERATIONS if not name.startswith("store")):
        with pytest.raises((ValueError, OSError)):
            _invoke(
                harness=harness,
                material=None,
                operation=operation,
                population=None,
                relationships=None,
            )
        assert _state(harness.root) == before, operation
        assert not harness.calls, operation
        assert not harness.proposals.calls, operation
        assert not harness.charges, operation
        _block_network.assert_not_called()


@pytest.mark.parametrize(argnames="attack", argvalues=_ATTACKS)
def test_unsupported_formats_reject_across_all_entry_and_export_paths_without_effects(
    _block_network: Mock, attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject unsupported evidence despite matching hashes, success reports or overwrite.

    Parameters
    ----------
    attack
        Independent missing, old, mixed, or malformed evidence attack.
    monkeypatch
        Offline upstream and model-call observers.
    tmp_path
        Synthetic complete release, never a historical production directory.
    """
    harness = _entry._Harness(root=tmp_path)
    harness.config.learning_progressions.max_concurrent_requests = 1
    harness._save_config()
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    if attack == "failure_missing_attempt":
        harness.proposals.failure = ("verdict", 1)
        with pytest.raises(LPGenerationFailed):
            create_kgs.create(harness.config_path)
        harness.proposals.failure = None
    create_kgs.create(harness.config_path)
    kwargs = {
        "as_lc_bundle": harness.bundle,
        "doc_key": _entry._DOC_KEY,
        "kg_config": harness.config,
    }
    population = build_lp_generation_requests(**kwargs)
    relationships = lp_finalization.build_lp_relationships(
        **kwargs, kg_dirs=KGDirs(root=harness.root)
    )
    material = _json(harness.root / _RECEIPT)["material"]
    lp_checkpoints.validate_lp_checkpoint_format(harness.root)
    assert _json(harness.root / "kg_run.json")["extra"]["status"] == "success"
    _attack(attack=attack, root=harness.root)
    # Absence of the lock also detects preflight paths that initialize ownership early.
    (harness.root / ".lp_generation.lock").unlink()
    _reuse._reset_entry(harness)
    before = _state(harness.root)
    for operation in _OPERATIONS:
        with pytest.raises((ValueError, OSError)) as caught:
            _invoke(
                harness=harness,
                material=material,
                operation=operation,
                population=population,
                relationships=relationships,
            )
        assert str(caught.value), operation
        if operation in {
            "build",
            "build_overwrite",
            "create",
            "create_overwrite",
            "generate",
            "generate_overwrite",
            "preflight",
        }:
            assert "incompatible" in str(caught.value), operation
            assert str(harness.root) in str(caught.value), operation
        assert _state(harness.root) == before, operation
        assert not harness.calls, operation
        assert not harness.proposals.calls, operation
        assert not harness.charges, operation
        _block_network.assert_not_called()
    # A rejected new invocation cannot create a report when none was present either.
    (harness.root / "kg_run.json").unlink()
    before = _state(harness.root)
    for operation in ("create", "create_overwrite"):
        with pytest.raises(ValueError, match="incompatible"):
            _invoke(
                harness=harness,
                material=material,
                operation=operation,
                population=population,
                relationships=relationships,
            )
        assert _state(harness.root) == before
        assert not harness.calls
        assert not harness.proposals.calls
        _block_network.assert_not_called()
