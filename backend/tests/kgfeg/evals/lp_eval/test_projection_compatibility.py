"""Validate historical and wire projections independently of checkpoint evidence."""

# Pytest fixtures intentionally use private names and retain parameter documentation.
# pylint: disable=useless-param-doc

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import shutil
import socket

from dataclasses import replace
from operator import itemgetter
from pathlib import Path
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

# Package Library
from kgfeg.config import BackendSettings
from kgfeg.evals.lp_eval import judge, sampling
from kgfeg.evals.lp_eval.schemas import resolve_evaluation_settings
from tests.fixtures.lp.delivery import projection_bytes
from tests.fixtures.lp_eval.snapshot_fixtures import (
    build_snapshot,
    load_historical_snapshot,
)

_NAMES = ("as_lc_lp_nodes.jsonl", "as_lc_lp_relationships.jsonl")


def _format(*, directory: Path, projection: str) -> None:
    """Construct a synthetic projection variant before freezing any evaluation evidence.

    Parameters
    ----------
    directory
        Test-owned copied source snapshot.
    projection
        Explicit historical internal or current wire representation.
    """
    material = json.loads((directory / "as_lc_lp_kg_bundle.json").read_bytes())
    if projection == "wire":
        config = json.loads((directory / "kg_run.json").read_bytes())["extra"]
        payloads = projection_bytes(
            grade_mapping=config["as"]["grade_level_mapping"], material=material
        )
    else:
        nodes = [{**material["framework"], "entity_type": "StandardsFramework"}]
        for group, identity, entity in (
            ("items", "case_identifier_uuid", "StandardsFrameworkItem"),
            ("learning_components", "identifier", "LearningComponent"),
        ):
            nodes.extend(
                {**row, "entity_type": entity}
                for row in sorted(material[group], key=itemgetter(identity))
            )
        edges = [
            row
            for group in (
                "relationships_has_child",
                "relationships_supports",
                "relationships_builds_towards",
                "relationships_relates_to",
            )
            for row in sorted(material[group], key=lambda row: row["identifier"])
        ]
        payloads = {
            name: b"".join(
                (
                    json.dumps(
                        ensure_ascii=False,
                        obj=row,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                ).encode()
                for row in rows
            )
            for name, rows in zip(_NAMES, (nodes, edges), strict=True)
        }
    for name, payload in payloads.items():
        (directory / name).write_bytes(payload)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid provider access in synthetic snapshot validation.

    Parameters
    ----------
    monkeypatch
        Restoring socket guards.
    """
    guard = Mock(side_effect=AssertionError("Snapshot tests must remain offline"))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _run(directory: Path) -> Any:
    """Discover a single completed synthetic snapshot.

    Parameters
    ----------
    directory
        Isolated kgs directory.

    Returns
    -------
    Any
        Discovered completed run identity.
    """
    inventory = sampling.discover_lp_runs(
        evaluation_root=directory.parent / "evaluations", results_root=directory
    )
    assert len(inventory.runs) == 1
    assert inventory.runs[0].status == "completed_candidate"
    return inventory.runs[0]


@pytest.fixture(scope="module")
def _sources(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Create immutable small source snapshots with each checkpoint contract.

    Parameters
    ----------
    tmp_path_factory
        Module-local temporary artifact factory.

    Returns
    -------
    dict[str, Path]
        Synthetic historical and journal-bearing source directories.
    """
    root = tmp_path_factory.mktemp("projection-sources")
    return {
        "historical_prefix": load_historical_snapshot(root),
        "journal_bearing": build_snapshot(count=4, identity=98765, root=root),
    }


def _state(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Capture bytes, timestamps and inodes to detect evidence rewrites.

    Parameters
    ----------
    root
        Synthetic snapshot subtree.

    Returns
    -------
    dict[str, tuple[bytes, int, int]]
        Complete stable file evidence excluding access times.
    """
    return {
        str(path.relative_to(root)): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
            path.stat().st_ino,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    argnames="checkpoint", argvalues=["historical_prefix", "journal_bearing"]
)
def test_corrupt_historical_boolean_cannot_be_frozen(
    _sources: dict[str, Path], checkpoint: str, tmp_path: Path
) -> None:
    """Reject numeric substitution for a boolean before publishing frozen evidence.

    Parameters
    ----------
    _sources
        Untouched source snapshots.
    checkpoint
        Checkpoint contract independent of the historical projection shape.
    tmp_path
        Temporary input and evaluator directory.
    """
    source = tmp_path / "source"
    shutil.copytree(dst=source, src=_sources[checkpoint].parent)
    directory = source / "kgs"
    _format(directory=directory, projection="internal")
    path = directory / _NAMES[0]
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert rows[0]["is_current"] is True
    rows[0]["is_current"] = 1
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "results/lp_evals", results_root=source
    )
    before = _state(source)
    with pytest.raises(sampling.LPSnapshotError):
        sampling.freeze_lp_inputs(inventory=inventory, repository_root=tmp_path)
    assert _state(source) == before
    assert not (tmp_path / "results/lp_evals").exists()


@pytest.mark.parametrize(
    argnames="checkpoint", argvalues=["historical_prefix", "journal_bearing"]
)
@pytest.mark.parametrize(argnames="projection", argvalues=["internal", "wire"])
def test_each_projection_checkpoint_combination_freezes_original_hashes(
    _sources: dict[str, Path], checkpoint: str, projection: str, tmp_path: Path
) -> None:
    """Validate all four combinations read-only and bind their exact original bytes.

    Parameters
    ----------
    _sources
        Immutable synthetic checkpoint sources.
    checkpoint
        Original checkpoint format, never migrated.
    projection
        Independently chosen projection format.
    tmp_path
        Isolated source and evaluator output root.
    """
    source = tmp_path / "source"
    shutil.copytree(dst=source, src=_sources[checkpoint].parent)
    directory = source / "kgs"
    _format(directory=directory, projection=projection)
    before = _state(source)
    validated = sampling.validate_lp_snapshot(_run(directory))
    assert validated.checkpoint_format == checkpoint
    artifacts = {artifact.name: artifact for artifact in validated.artifacts}
    for name in _NAMES:
        assert (
            artifacts[name].fingerprint.sha256
            == hashlib.sha256(before[f"kgs/{name}"][0]).hexdigest()
        )
    inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "results/lp_evals", results_root=source
    )
    frozen = sampling.freeze_lp_inputs(inventory=inventory, repository_root=tmp_path)
    restored = sampling.load_frozen_lp_inputs(frozen)
    assert len(restored) == 1
    assert restored[0].checkpoint_format == checkpoint
    assert _state(source) == before
    path = directory / _NAMES[0]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(sampling.LPSnapshotError):
        sampling.load_frozen_lp_inputs(frozen)


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "ambiguous",
        "duplicate_key",
        "empty",
        "extra",
        "mixed_files",
        "mixed_rows",
        "property_alias",
        "property_type",
        "wrong_endpoint",
        "wrong_order",
    ],
)
@pytest.mark.parametrize(
    argnames="checkpoint", argvalues=["historical_prefix", "journal_bearing"]
)
@pytest.mark.parametrize(argnames="projection", argvalues=["internal", "wire"])
def test_malformed_projection_never_falls_back_or_rewrites_evidence(  # pylint: disable=too-complex
    _sources: dict[str, Path],
    attack: str,
    checkpoint: str,
    projection: str,
    tmp_path: Path,
) -> None:
    """Reject mixed formats and valid-JSON corruption under either checkpoint contract.

    Parameters
    ----------
    _sources
        Original small synthetic sources.
    attack
        Independent raw or structural corruption.
    checkpoint
        Historical or current checkpoint evidence.
    projection
        Intended projection shape before corruption.
    tmp_path
        Isolated attack directory.
    """
    source = tmp_path / "source"
    shutil.copytree(dst=source, src=_sources[checkpoint].parent)
    directory = source / "kgs"
    _format(directory=directory, projection=projection)
    path = directory / _NAMES[0]
    payload = path.read_bytes()
    rows = [json.loads(line) for line in payload.splitlines()]
    if attack in ("mixed_files", "mixed_rows"):
        _format(
            directory=directory,
            projection="wire" if projection == "internal" else "internal",
        )
        if attack == "mixed_files":
            path.write_bytes(payload)
        else:
            rows[1] = json.loads(path.read_bytes().splitlines()[1])
            path.write_bytes(
                b"".join((json.dumps(row) + "\n").encode() for row in rows)
            )
    else:
        if attack == "ambiguous":
            rows[0]["entity_type" if projection == "wire" else "type"] = (
                "StandardsFramework" if projection == "wire" else "node"
            )
        elif attack == "extra":
            rows[0]["unexpected"] = "untrusted"
        elif attack == "property_alias":
            owner = rows[0]["properties"] if projection == "wire" else rows[0]
            old, new = (
                ("caseIdentifierUUID", "case_identifier_uuid")
                if projection == "wire"
                else ("case_identifier_uuid", "caseIdentifierUUID")
            )
            owner[new] = owner.pop(old)
        elif attack == "property_type":
            owner = rows[0]["properties"] if projection == "wire" else rows[0]
            owner["isCurrent" if projection == "wire" else "is_current"] = 1
        elif attack == "wrong_endpoint":
            edge_path = directory / _NAMES[1]
            edges = [json.loads(line) for line in edge_path.read_bytes().splitlines()]
            edges[0][
                "source_identifier" if projection == "wire" else "source_entity_value"
            ] = "wrong"
            edge_path.write_bytes(
                b"".join((json.dumps(row) + "\n").encode() for row in edges)
            )
        elif attack == "wrong_order":
            rows.reverse()
        payload = b"".join((json.dumps(row) + "\n").encode() for row in rows)
        if attack == "empty":
            payload = b""
        elif attack == "duplicate_key":
            payload = payload.replace(b"{", b'{"identifier":"duplicate",', 1)
        path.write_bytes(payload)
    before = _state(source)
    with pytest.raises(sampling.LPSnapshotError):
        sampling.validate_lp_snapshot(_run(directory))
    assert _state(source) == before


@pytest.mark.parametrize(argnames="filename", argvalues=["lp_export.py", "sampling.py"])
def test_projection_implementation_changes_invalidate_saved_schedule(
    _sources: dict[str, Path],
    filename: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject cached schedules when either projection validation dependency changes.

    Parameters
    ----------
    _sources
        Untouched synthetic run sources.
    filename
        Export or evaluator source dependency whose fingerprint is changed.
    monkeypatch
        Restoring fingerprint-reader seam; production source remains read-only.
    tmp_path
        Isolated input and evaluator store.
    """
    source = tmp_path / "source"
    shutil.copytree(dst=source, src=_sources["journal_bearing"].parent)
    inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "results/lp_evals", results_root=source
    )
    frozen = sampling.freeze_lp_inputs(inventory=inventory, repository_root=tmp_path)
    settings = BackendSettings(
        LEARNING_COMMONS_EXPORT_SCHEMA_VERSION="synthetic-test",
        PATHS_PROJECT_DIR=tmp_path,
        _env_file=None,
    )
    schedule = sampling.prepare_evaluation_schedule(
        inputs=frozen,
        judge=judge.resolve_judge_settings(settings),
        settings=resolve_evaluation_settings(),
    )
    reference = judge.persist_evaluation_schedule(
        repository_root=tmp_path, schedule=schedule
    )
    original = schedule.implementation_fingerprints
    assert sum(item.path.name == filename for item in original) >= 1
    changed = tuple(
        replace(item, sha256="0" * 64) if item.path.name == filename else item
        for item in original
    )
    before = _state(tmp_path)
    monkeypatch.setattr(
        name="_schedule_implementation", target=judge, value=lambda: changed
    )
    with pytest.raises(expected_exception=ValueError, match="implementation"):
        with judge.open_evaluation_store(reference):
            raise AssertionError("Incompatible schedule was opened")
    assert _state(tmp_path) == before
