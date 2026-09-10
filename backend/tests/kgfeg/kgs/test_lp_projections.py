"""Red-team complete internal graph projections through offline artifact boundaries."""

# Future Library
from __future__ import annotations

# Standard Library
import json
import socket

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import lp_export
from kgfeg.kgs.schemas import (
    AcademicStandardsLCLPKGBundle,
    LearningComponent,
    Relationship,
    StandardsFramework,
    StandardsFrameworkItem,
)
from tests.kgfeg.kgs import test_lp_export as _export
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_selection as _factories

_NODES = "as_lc_lp_nodes.jsonl"
_RELATIONSHIPS = "as_lc_lp_relationships.jsonl"
_GROUPS = (
    "relationships_has_child",
    "relationships_supports",
    "relationships_builds_towards",
    "relationships_relates_to",
)
_SENTINEL: dict[str, Any] = {
    "nested": [None, False, 0, "", [], {}, {"text": "é e\u0301 漢字 🧭\n\t"}],
    "explicit_null": None,
}


def _assert_projection(
    *, harness: _claims._Harness, result: AcademicStandardsLCLPKGBundle
) -> None:
    """Compare actual files to full upstream nodes and authenticated standalone edges.

    Parameters
    ----------
    harness
        Current upstream authority and persisted standalone files.
    result
        Combined graph returned by the real compiler.
    """
    upstream = harness.bundle.model_dump(mode="json")
    expected = deepcopy(upstream)
    for group, filename in (
        ("relationships_builds_towards", "lp_relationships_builds_towards.jsonl"),
        ("relationships_relates_to", "lp_relationships_relates_to.jsonl"),
    ):
        expected[group] = [
            json.loads(line)
            for line in (harness.root / filename).read_bytes().splitlines()
        ]
    for name, payload in _export._projection_bytes(expected).items():
        assert (harness.root / name).read_bytes() == payload
    nodes = [
        json.loads(line) for line in (harness.root / _NODES).read_bytes().splitlines()
    ]
    edges = [
        json.loads(line)
        for line in (harness.root / _RELATIONSHIPS).read_bytes().splitlines()
    ]
    assert len(nodes) == 1 + len(upstream["items"]) + len(
        upstream["learning_components"]
    )
    assert len(edges) == sum(len(expected[group]) for group in _GROUPS)
    assert result.summary.total_node_count == len(nodes)
    assert result.summary.total_relationship_count == len(edges)
    actual_counts = Counter(row["relationship_type"] for row in edges)
    assert actual_counts == Counter(
        {
            "hasChild": len(expected["relationships_has_child"]),
            "supports": len(expected["relationships_supports"]),
            "buildsTowards": len(expected["relationships_builds_towards"]),
            "relatesTo": len(expected["relationships_relates_to"]),
        }
    )
    identifiers = []
    schemas = {
        "LearningComponent": LearningComponent,
        "StandardsFramework": StandardsFramework,
        "StandardsFrameworkItem": StandardsFrameworkItem,
    }
    for node in nodes:
        entity = node.pop("entity_type")
        assert schemas[entity].model_validate(node).model_dump(mode="json") == node
        identifiers.append(
            node[
                (
                    "identifier"
                    if entity == "LearningComponent"
                    else "case_identifier_uuid"
                )
            ]
        )
    for edge in edges:
        assert Relationship.model_validate(edge).model_dump(mode="json") == edge
        identifiers.append(edge["identifier"])
    assert len(identifiers) == len(set(identifiers))
    persisted = json.loads((harness.root / _export._BUNDLE).read_bytes())
    assert persisted == result.model_dump(mode="json")
    assert _export._projection_bytes(persisted) == _export._projection_bytes(expected)
    for group in ("framework", "items", "learning_components", *_GROUPS[:2]):
        assert persisted[group] == upstream[group]


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid external transports and select a fixed synthetic model identity.

    Parameters
    ----------
    monkeypatch
        Restoring network guards and model setting.
    """
    guard = Mock(side_effect=AssertionError("Projection tests must remain offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _corrupt_payload(*, attack: str, payload: bytes) -> bytes:
    """Alter actual serialized rows while retaining valid JSON for population attacks.

    Parameters
    ----------
    attack
        Row omission, duplication, metadata corruption, reordering, or truncation.
    payload
        Correct projection bytes before the injected persistence fault.

    Returns
    -------
    bytes
        Altered bytes for the chosen attack, otherwise unchanged bytes.
    """
    rows = [json.loads(line) for line in payload.splitlines()]
    if attack == "drop":
        rows.pop()
    elif attack == "duplicate":
        rows.append(deepcopy(rows[0]))
    elif attack == "metadata":
        rows[0]["metadata"]["lost_content"] = True
    elif attack == "reorder":
        rows.reverse()
    encoded = b"".join(_export._bytes(row) for row in rows)
    return encoded[:-4] if attack == "truncate" else encoded


def _rich_harness(root: Path) -> _claims._Harness:
    """Build multiple rows per group with distinct metadata and scripted LP outcomes.

    Parameters
    ----------
    root
        Isolated artifact directory.

    Returns
    -------
    _claims._Harness
        Five standards, two components, four supports, and mixed pair judgments.
    """
    harness = _claims._Harness(count=5, root=root)
    components = []
    supports = []
    for number in (801, 800):
        component_uuid = UUID(int=number)
        components.append(
            {
                **_factories._COMMON,
                "description": f"Synthetic component é 漢字 {number}",
                "identifier": component_uuid,
                "metadata": {"source_sfi_uuids": [str(UUID(int=n)) for n in (1, 2)]},
            }
        )
        for target in (2, 1):
            edge = _factories._edge(source=component_uuid, target=UUID(int=target))
            edge.update(
                relationship_type="supports",
                source_entity="LearningComponent",
                source_entity_key="identifier",
            )
            supports.append(edge)
    harness.bundle = _factories._bundle(
        components=tuple(components),
        items=tuple(reversed(harness.bundle.items)),
        supports=tuple(supports),
    )
    for record in (
        harness.bundle.framework,
        *harness.bundle.items,
        *harness.bundle.learning_components,
        *harness.bundle.relationships_has_child,
        *harness.bundle.relationships_supports,
    ):
        record.metadata["projection_audit"] = deepcopy(_SENTINEL)
    harness.decisions = {
        (1, 2): ("buildsTowards", "first_to_second"),
        (1, 3): ("buildsTowards", "first_to_second"),
        (1, 4): ("relatesTo", None),
        (1, 5): ("relatesTo", None),
        (2, 3): ("buildsTowards", "first_to_second"),
        (3, 4): ("needs_review", None),
    }
    return harness


def test_all_four_groups_retain_exact_internal_fields_and_upstream_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep every internal field, direct edge, and prior consumer file unchanged.

    Parameters
    ----------
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _rich_harness(tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    for name in (
        "as_kg_bundle.json",
        "as_nodes.jsonl",
        "as_relationships.jsonl",
        "as_lc_kg_bundle.json",
        "as_lc_nodes.jsonl",
        "as_lc_relationships.jsonl",
    ):
        (tmp_path / name).write_bytes(b"opaque prior consumer bytes\r\n")
    before = _export._snapshot(tmp_path)
    upstream = harness.bundle.model_dump(mode="json")
    result = _export._compile(harness=harness)
    _assert_projection(harness=harness, result=result)
    assert harness.bundle.model_dump(mode="json") == upstream
    assert _export._snapshot(tmp_path) == {
        **before,
        _export._BUNDLE: _export._bytes(result.model_dump(mode="json")),
        **_export._projection_bytes(result.model_dump(mode="json")),
    }
    nodes = [json.loads(line) for line in (tmp_path / _NODES).read_bytes().splitlines()]
    edges = [
        json.loads(line)
        for line in (tmp_path / _RELATIONSHIPS).read_bytes().splitlines()
    ]
    assert Counter(row["entity_type"] for row in nodes) == {
        "StandardsFramework": 1,
        "StandardsFrameworkItem": 5,
        "LearningComponent": 2,
    }
    assert [row["relationship_type"] for row in edges] == (
        ["hasChild"] * 5 + ["supports"] * 4 + ["buildsTowards"] * 3 + ["relatesTo"] * 2
    )
    for row in (*nodes, *edges[:9]):
        assert row["metadata"]["projection_audit"] == _SENTINEL
        assert row["date_created"] is row["date_modified"] is None
    for row in edges[9:]:
        assert (
            row["metadata"]
            == result.entity_provenance[
                (
                    "relationships_builds_towards"
                    if row["relationship_type"] == "buildsTowards"
                    else "relationships_relates_to"
                )
            ][row["identifier"]]
        )
        assert (
            row["source_entity_key"]
            == row["target_entity_key"]
            == "case_identifier_uuid"
        )
        if row["relationship_type"] == "relatesTo":
            assert row["source_entity_value"] < row["target_entity_value"]
    assert "é e\u0301 漢字 🧭".encode("utf-8") in (tmp_path / _NODES).read_bytes()
    assert (
        "é e\u0301 漢字 🧭".encode("utf-8") in (tmp_path / _RELATIONSHIPS).read_bytes()
    )


@pytest.mark.parametrize(argnames="profile", argvalues=_export._PROFILES)
def test_curriculum_context_survives_complete_projections(
    monkeypatch: pytest.MonkeyPatch, profile: str, tmp_path: Path
) -> None:
    """Retain DAG parents, fallback edges, and unresolved relationship warning provenance.

    Parameters
    ----------
    monkeypatch
        Offline producer/checker seam.
    profile
        Reduced curriculum retaining its distinctive graph shape.
    tmp_path
        Isolated artifact directory.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(batch=20, profile=profile)
    harness.default_decision = "relatesTo"
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    _assert_projection(harness=harness, result=result)
    edges = [
        json.loads(line)
        for line in (tmp_path / _RELATIONSHIPS).read_bytes().splitlines()
    ]
    hierarchy = [edge for edge in edges if edge["relationship_type"] == "hasChild"]
    if profile == "pratham_science":
        parents: dict[str, set[str]] = {}
        for edge in hierarchy:
            parents.setdefault(edge["target_entity_value"], set()).add(
                edge["source_entity_value"]
            )
        assert any(len(values) > 1 for values in parents.values())
    if profile == "ghana_math":
        assert any(
            edge["metadata"].get("unresolved_root_fallback") for edge in hierarchy
        )
        assert result.validation_report.warnings
        assert any(
            edge["metadata"]["claim"]["judgment"]["warnings"]
            for edge in edges
            if edge["relationship_type"] == "relatesTo"
        )


@pytest.mark.parametrize(argnames="count", argvalues=[0, 1])
def test_empty_populations_write_complete_files_without_model_calls(
    count: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Write one framework and an empty relationship file when all other groups are empty.

    Parameters
    ----------
    count
        Zero or one SFI, insufficient to form a pair.
    monkeypatch
        Offline model seam that must remain unused.
    tmp_path
        Isolated artifact directory.
    """
    harness = _claims._Harness(count=count, root=tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    _assert_projection(harness=harness, result=result)
    assert not harness.calls
    if count == 0:
        assert (tmp_path / _RELATIONSHIPS).read_bytes() == b""
        assert len((tmp_path / _NODES).read_bytes().splitlines()) == 1


@pytest.mark.parametrize(argnames="decision", argvalues=["needs_review", "no_relation"])
def test_nonpublishing_populations_keep_nodes_and_upstream_relationships(
    decision: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep complete graph projections even when no adjudication publishes an LP edge.

    Parameters
    ----------
    decision
        Uniform valid ambiguous or negative outcome.
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _rich_harness(tmp_path)
    harness.decisions = {}
    harness.default_decision = decision
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    _assert_projection(harness=harness, result=result)
    assert result.summary.total_node_count == 8
    assert result.summary.total_relationship_count == 9
    assert result.relationships_builds_towards == result.relationships_relates_to == []


@pytest.mark.parametrize(
    argnames="field", argvalues=["total_node_count", "total_relationship_count"]
)
def test_projection_count_mismatch_fails_before_output_write(
    field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject inconsistent total counts at the projection boundary without replacing files.

    Parameters
    ----------
    field
        Summary total corrupted after validated compilation.
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _rich_harness(tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    before = _export._snapshot(tmp_path)
    setattr(result.summary, field, getattr(result.summary, field) + 1)
    with pytest.raises(expected_exception=ValueError, match="projection counts"):
        lp_export._write_projections(bundle=result, root=tmp_path)
    assert _export._snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="name", argvalues=[_NODES, _RELATIONSHIPS])
@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "drop",
        "duplicate",
        "metadata",
        "missing",
        "read_error",
        "reorder",
        "truncate",
        "write_error",
    ],
)
def test_projection_persistence_failures_propagate_through_public_compiler(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Prevent a successful return after either projection fails writing or read-back.

    Parameters
    ----------
    attack
        Persistence error or byte/population corruption.
    monkeypatch
        Restoring filesystem seams around real serialization and validation.
    name
        Projection whose persistence fails.
    tmp_path
        Isolated artifact directory.
    """
    harness = _rich_harness(tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    before = _export._snapshot(tmp_path)
    reader = Path.read_bytes
    writer = lp_export._atomic_write
    writes = []

    def _read(self: Path) -> bytes:
        """Inject a projection-only read error while retaining real evidence reads.

        Parameters
        ----------
        self
            File being read.

        Returns
        -------
        bytes
            Unmodified bytes for every other read.

        Raises
        ------
        OSError
            When reading the targeted projection.
        """
        if attack == "read_error" and self.name == name:
            raise OSError("Synthetic projection read failure")
        return reader(self)

    def _write(*, path: Path, payload: bytes) -> None:
        """Inject only the selected output failure after real projection serialization.

        Parameters
        ----------
        path
            Compiler output path.
        payload
            Actual serialized bytes.

        Raises
        ------
        OSError
            When the selected projection write fails.
        """
        writes.append(path.name)
        if path.name == name:
            if attack == "write_error":
                raise OSError("Synthetic projection write failure")
            if attack == "missing":
                return
            payload = _corrupt_payload(attack=attack, payload=payload)
        writer(path=path, payload=payload)

    with monkeypatch.context() as scoped:
        scoped.setattr(name="read_bytes", target=Path, value=_read)
        scoped.setattr(name="_atomic_write", target=lp_export, value=_write)
        with pytest.raises(expected_exception=(OSError, ValueError)):
            _export._compile(harness=harness)
    assert name in writes
    after = _export._snapshot(tmp_path)
    assert {key: after[key] for key in before} == before
    assert set(after) <= set(before) | {_export._BUNDLE, _NODES, _RELATIONSHIPS}


@pytest.mark.parametrize(
    argnames="group", argvalues=["items", "learning_components", *_GROUPS]
)
def test_reordered_bundle_groups_have_identical_projection_bytes(
    group: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Make serialization independent of every populated group without rewriting authority.

    Parameters
    ----------
    group
        Bundle list whose encounter order is reversed.
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _rich_harness(tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    before = _export._snapshot(tmp_path)
    rows = getattr(result, group)
    assert len(rows) >= 2
    setattr(result, group, list(reversed(rows)))
    reordered = result.model_dump(mode="json")
    lp_export._write_projections(bundle=result, root=tmp_path)
    assert result.model_dump(mode="json") == reordered
    assert _export._snapshot(tmp_path) == before


def test_reordered_upstream_fresh_runs_preserve_identical_complete_projections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regenerate valid authority for reordered inputs and retain every projected byte.

    Parameters
    ----------
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Parent of independent run directories.
    """
    first = _rich_harness(tmp_path / "first")
    first.decisions = {}
    _export._persist(harness=first, monkeypatch=monkeypatch)
    first_result = _export._compile(harness=first)
    second = _rich_harness(tmp_path / "second")
    second.decisions = {}
    for group in ("items", "learning_components", *_GROUPS[:2]):
        setattr(second.bundle, group, list(reversed(getattr(second.bundle, group))))
    second.bundle.framework.metadata = dict(
        reversed(list(second.bundle.framework.metadata.items()))
    )
    _export._persist(harness=second, monkeypatch=monkeypatch)
    second_result = _export._compile(harness=second)
    _assert_projection(harness=first, result=first_result)
    _assert_projection(harness=second, result=second_result)
    for name in (_NODES, _RELATIONSHIPS):
        assert (first.root / name).read_bytes() == (second.root / name).read_bytes()
    assert (
        first_result.validation_report.input_content_hashes["as_lc_bundle"]
        != second_result.validation_report.input_content_hashes["as_lc_bundle"]
    )


@pytest.mark.parametrize(argnames="name", argvalues=[_NODES, _RELATIONSHIPS])
def test_write_time_evidence_mutation_during_projections_rejects_success(
    monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Recheck authenticated input bytes after the extended projection-writing interval.

    Parameters
    ----------
    monkeypatch
        Restoring real atomic writer wrapper.
    name
        Output write during which source evidence changes.
    tmp_path
        Isolated artifact directory.
    """
    harness = _rich_harness(tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    writer = lp_export._atomic_write

    def _write(*, path: Path, payload: bytes) -> None:
        """Write actual output before changing an authenticated input file.

        Parameters
        ----------
        path
            Compiler output path.
        payload
            Actual serialized output bytes.
        """
        writer(path=path, payload=payload)
        if path.name == name:
            source = tmp_path / "lp_generation_responses.jsonl"
            source.write_bytes(source.read_bytes() + b" ")

    monkeypatch.setattr(name="_atomic_write", target=lp_export, value=_write)
    with pytest.raises(
        expected_exception=ValueError, match="changed during bundle compilation"
    ):
        _export._compile(harness=harness)
