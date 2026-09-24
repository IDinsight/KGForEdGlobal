"""Challenge delivery integrity independently from internal graph and checkpoint formats."""

# Future Library
from __future__ import annotations

# Standard Library
import json
import socket

from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import lp_export
from tests.fixtures.lp.delivery import delivery_records
from tests.kgfeg.kgs import test_lp_export as _export
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_projections as _projections
from tests.kgfeg.kgs import test_lp_reuse as _reuse


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject external network access during every synthetic boundary test.

    Parameters
    ----------
    monkeypatch
        Restoring socket guards.
    """
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")
    guard = Mock(side_effect=AssertionError("Delivery tests must remain offline"))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


@pytest.mark.parametrize(argnames="operation", argvalues=["compile", "reuse"])
@pytest.mark.parametrize(argnames="ending", argvalues=[b"\n", b"\r\n", b""])
def test_exact_upstream_bytes_survive_fresh_and_reused_delivery(
    ending: bytes, monkeypatch: pytest.MonkeyPatch, operation: str, tmp_path: Path
) -> None:
    """Preserve valid noncanonical JSON spacing and line endings with safe LP append.

    Parameters
    ----------
    ending
        Original final newline convention, including no final newline.
    monkeypatch
        Scripted model boundary.
    operation
        Fresh compiler or completed-bundle reuse.
    tmp_path
        Temporary evidence directory.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    harness.default_decision = "relatesTo"
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    if operation == "reuse":
        _export._compile(harness=harness)
    for name in ("as_lc_nodes.jsonl", "as_lc_relationships.jsonl"):
        rows = [
            json.loads(line) for line in (tmp_path / name).read_bytes().splitlines()
        ]
        (tmp_path / name).write_bytes(
            b"\r\n".join(
                json.dumps(ensure_ascii=False, obj=row).encode() for row in rows
            )
            + ending
        )
    before = _reuse._state(tmp_path)
    calls = list(harness.calls)
    result = _reuse._invoke(harness=harness, operation=operation)
    assert result is not None
    assert (tmp_path / "as_lc_lp_nodes.jsonl").read_bytes() == before[
        "as_lc_nodes.jsonl"
    ][0]
    edges = (tmp_path / "as_lc_lp_relationships.jsonl").read_bytes()
    assert edges.startswith(before["as_lc_relationships.jsonl"][0])
    expected_nodes, expected_edges = delivery_records(
        material=result.model_dump(mode="json")
    )
    assert [json.loads(line) for line in edges.splitlines()] == expected_edges
    assert [
        json.loads(line)
        for line in (tmp_path / "as_lc_lp_nodes.jsonl").read_bytes().splitlines()
    ] == expected_nodes
    after = _reuse._state(tmp_path)
    assert {
        name: after[name] for name in before if name not in _reuse._PROJECTIONS
    } == {
        name: value for name, value in before.items() if name not in _reuse._PROJECTIONS
    }
    assert harness.calls == calls


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "alias",
        "blank_line",
        "duplicate_key",
        "duplicate_row",
        "extra_metadata",
        "missing",
        "numeric_property",
        "omit",
        "reorder",
        "stale",
        "truncate",
    ],
)
@pytest.mark.parametrize(
    argnames="name", argvalues=["as_lc_nodes.jsonl", "as_lc_relationships.jsonl"]
)
@pytest.mark.parametrize(argnames="operation", argvalues=["compile", "reuse"])
def test_invalid_upstream_delivery_rejects_before_publication(  # pylint: disable=too-complex,too-many-branches
    attack: str,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    operation: str,
    tmp_path: Path,
) -> None:
    """Reject invalid upstream wire evidence without model calls or artifact changes.

    Parameters
    ----------
    attack
        Raw syntax, alias, value, population or material corruption.
    monkeypatch
        Offline producer/checker seam.
    name
        Upstream delivery file under attack.
    operation
        Fresh export or completed-bundle reuse.
    tmp_path
        Temporary synthetic artifact root.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    if operation == "reuse":
        _export._compile(harness=harness)
    path = tmp_path / name
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if attack == "missing":
        path.unlink()
    else:
        if attack == "alias":
            properties = rows[0]["properties"]
            properties["attribution_statement"] = properties.pop("attributionStatement")
        elif attack == "duplicate_row":
            rows.append(rows[0])
        elif attack == "extra_metadata":
            rows[0]["properties"]["metadata"] = {"unexpected": True}
        elif attack == "numeric_property":
            rows[0]["properties"]["identifier"] = 1
        elif attack == "omit":
            rows.pop()
        elif attack == "reorder":
            rows.reverse()
        elif attack == "stale":
            rows[0]["properties"]["attributionStatement"] += " changed"
        payload = b"".join(_export._bytes(row) for row in rows)
        if attack == "duplicate_key":
            payload = payload.replace(b"{", b'{"type":"wrong",', 1)
        elif attack == "truncate":
            payload = payload[:-8]
        elif attack == "blank_line":
            payload += b"\n"
        path.write_bytes(payload)
    _reuse._assert_rejected(harness=harness, operation=operation)


def test_wire_endpoint_order_uses_case_direction_not_delivery_identifier_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resolve divergent delivery identifiers without recanonicalizing symmetric endpoints.

    Parameters
    ----------
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Synthetic mixed-relation evidence directory.
    """
    harness = _projections._rich_harness(tmp_path)
    for item in harness.bundle.items:
        item.identifier = UUID(int=10000 - item.case_identifier_uuid.int)
    harness.bundle.learning_components[0].metadata["tags"] = ["mixed_case", "é 漢字"]
    harness.bundle.relationships_supports[0].metadata["support_confidence"] = 0.75
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    _projections._assert_projection(harness=harness, result=result)
    nodes, edges = delivery_records(material=result.model_dump(mode="json"))
    components = [node for node in nodes if node["labels"] == ["LearningComponent"]]
    assert components[0]["properties"]["tags"] == '["mixed_case","é 漢字"]'
    assert "caseIdentifierUUID" not in components[0]["properties"]
    assert "tags" not in components[1]["properties"]
    lp_edges = [
        edge for edge in edges if edge["label"] in ("buildsTowards", "relatesTo")
    ]
    assert {edge["label"] for edge in lp_edges} == {"buildsTowards", "relatesTo"}
    for edge in lp_edges:
        assert edge["source_identifier"] > edge["target_identifier"]
        assert (
            edge["properties"]["sourceEntityValue"]
            < edge["properties"]["targetEntityValue"]
        )
    assert (
        next(edge for edge in edges if edge["label"] == "supports")["properties"][
            "supportConfidence"
        ]
        == "0.75"
    )


def test_wire_explicit_optional_fields_and_value_encodings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep explicit aliases, string booleans, encoded grade lists and absent nulls.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated graph fixture directory.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    result.items[0].is_current = False
    result.items[0].notes = "é e\u0301 漢字 🧭"
    result.items[0].date_created = "2026-01-01"
    mapping = {"12": ["2", "1", "2"]}
    nodes, edges = lp_export.build_lp_delivery_records(
        bundle=result, grade_level_mapping=mapping
    )
    expected = delivery_records(
        grade_mapping=mapping, material=result.model_dump(mode="json")
    )
    assert (nodes, edges) == expected
    properties = nodes[1]["properties"]
    assert properties["isCurrent"] == "false"
    assert properties["gradeLevel"] == '["2","1"]'
    assert properties["dateCreated"] == "2026-01-01"
    assert "dateModified" not in properties
    assert properties["notes"] == "é e\u0301 漢字 🧭"


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "node_node",
        "node_relationship",
        "unresolved_source",
        "unresolved_target",
    ],
)
def test_wire_identity_collisions_and_unresolved_endpoints_reject(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject collisions in delivery identifiers even when internal CASE identities differ.

    Parameters
    ----------
    attack
        Delivery identifier collision or unknown relationship endpoint.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated graph directory.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    result = _export._compile(harness=harness)
    if attack == "node_node":
        result.items[0].identifier = result.framework.identifier
    elif attack == "node_relationship":
        result.items[0].identifier = result.relationships_has_child[0].identifier
    else:
        setattr(
            result.relationships_has_child[0],
            (
                "source_entity_value"
                if attack == "unresolved_source"
                else "target_entity_value"
            ),
            str(UUID(int=999)),
        )
    before = _reuse._state(tmp_path)
    with pytest.raises(expected_exception=ValueError, match="collide|resolve"):
        lp_export.build_lp_delivery_records(bundle=result, grade_level_mapping={})
    assert _reuse._state(tmp_path) == before


@pytest.mark.parametrize(
    argnames="name", argvalues=["as_lc_nodes.jsonl", "as_lc_relationships.jsonl"]
)
@pytest.mark.parametrize(argnames="operation", argvalues=["compile", "reuse"])
def test_write_time_upstream_delivery_changes_cannot_return_success(
    monkeypatch: pytest.MonkeyPatch, name: str, operation: str, tmp_path: Path
) -> None:
    """Detect upstream byte changes across the locked projection publication interval.

    Parameters
    ----------
    monkeypatch
        Restoring real writer wrapper and offline model seam.
    name
        Upstream delivery artifact changed during publication.
    operation
        Fresh compilation or completed-bundle reuse.
    tmp_path
        Temporary evidence directory.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    if operation == "reuse":
        _export._compile(harness=harness)
    before = _reuse._state(tmp_path)
    writer = lp_export._atomic_write
    touched = []

    def _write(*, path: Path, payload: bytes) -> None:
        """Write actual delivery before injecting an upstream byte-only change.

        Parameters
        ----------
        path
            Actual compiler output path.
        payload
            Real serialized output bytes.
        """
        writer(path=path, payload=payload)
        if path.name == "as_lc_lp_nodes.jsonl":
            source = tmp_path / name
            source.write_bytes(source.read_bytes() + b" ")
            touched.append(name)

    monkeypatch.setattr(name="_atomic_write", target=lp_export, value=_write)
    with pytest.raises(
        expected_exception=ValueError, match="delivery round-trip changed"
    ):
        _reuse._invoke(harness=harness, operation=operation)
    assert touched == [name]
    after = _reuse._state(tmp_path)
    assert {key: after[key] for key in before if key.startswith("lp_")} == {
        key: value for key, value in before.items() if key.startswith("lp_")
    }
