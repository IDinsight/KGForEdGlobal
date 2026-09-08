"""Independently verify bounded request material and complete offline persistence."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import socket

from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_generation
from kgfeg.kgs.lp_candidates import (
    LPCandidatePopulation,
    validate_lp_candidate_population,
)
from kgfeg.kgs.lp_generation import (
    LPGenerationRequest,
    build_lp_generation_requests,
    validate_lp_request_artifacts,
    write_lp_generation_request_artifacts,
)
from kgfeg.kgs.lp_selection import build_lp_selection
from kgfeg.kgs.schemas import AcademicStandardsLCKGBundle
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import CreateKGConfig
from tests.kgfeg.kgs import test_lp_candidates as _factories

_DOC_KEY = "synthetic-selection-document"
_FILES = (
    "lp_candidate_pairs.jsonl",
    "lp_candidate_summary.json",
    "lp_generation_requests.jsonl",
    "lp_generation_requests_manifest.json",
)
_PROFILES = (
    "ghana_english",
    "ghana_math",
    "madhi_math",
    "nigeria_math",
    "pratham_science",
    "rwanda_math",
)
_NAMESPACE = UUID("3f6b9f2a-7d8a-5d85-a9c3-9f3b8d3c3f4b")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid external execution in every request test.

    Parameters
    ----------
    monkeypatch
        Restoring network guard.
    """
    monkeypatch.setattr(name="connect", target=socket.socket, value=_reject_network)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=_reject_network)
    monkeypatch.setattr(name="create_connection", target=socket, value=_reject_network)


def _bundle(count: int = 4) -> AcademicStandardsLCKGBundle:
    """Create a small real nomination cohort with no hidden source copies.

    Parameters
    ----------
    count
        Number of eligible standards with shared text and coordinate.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Complete synthetic graph envelope.
    """
    bundle = _factories._bundle(
        items=tuple(_factories._item(number=n) for n in range(1, count + 1))
    )
    bundle.entity_provenance["items"] = {
        str(item.case_identifier_uuid): {} for item in bundle.items
    }
    return bundle


def _config(
    *,
    batch: int = 1,
    limits: dict[str, int] | None = None,
    profile: str = "nigeria_math",
) -> CreateKGConfig:
    """Use reviewed policy with explicit operational test bounds.

    Parameters
    ----------
    batch
        Maximum candidate pairs per request.
    limits
        Evidence limits to replace.
    profile
        Curriculum policy to load without running its pipeline.

    Returns
    -------
    CreateKGConfig
        Cross-validated runtime configuration.
    """
    payload = _factories._config(profile).model_dump(by_alias=True, mode="json")
    payload["lp"]["request_batch_size"] = batch
    payload["lp"]["candidate_policy"]["budgets"] = {
        "max_candidates_per_sfi": 20,
        "max_total_candidates": 100,
    }
    payload["lp"]["evidence_limits"].update(limits or {})
    return CreateKGConfig.model_validate(payload)


def _expanded_fixture(profile: str) -> AcademicStandardsLCKGBundle:
    """Add synthetic same-grain peers while preserving every reduced graph branch.

    Parameters
    ----------
    profile
        Approved reduced graph whose unchanged records remain in the test input.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Synthetic extension with real candidate coverage for each Standard grain.
    """
    original = _factories._fixture_bundle(profile)
    items = list(original.items)
    edges = [edge.model_dump(mode="json") for edge in original.relationships_has_child]
    for index, item in enumerate(original.items):
        if item.normalized_statement_type != "Standard":
            continue
        peer = item.model_copy(deep=True)
        peer.case_identifier_uuid = UUID(int=90000 + index)
        peer.identifier = UUID(int=190000 + index)
        peer.case_identifier_uri = f"urn:uuid:{peer.case_identifier_uuid}"
        items.append(peer)
        for edge in original.relationships_has_child:
            if UUID(edge.target_entity_value) == item.case_identifier_uuid:
                edges.append(
                    _factories._edge(
                        fallback=edge.metadata.get("unresolved_root_fallback", False),
                        source=UUID(edge.source_entity_value),
                        source_entity=edge.source_entity,
                        target=peer.case_identifier_uuid,
                    )
                )
    return _factories._bundle(
        components=tuple(
            component.model_dump(mode="json")
            for component in original.learning_components
        ),
        edges=tuple(edges),
        framework_uuid=original.framework.case_identifier_uuid,
        items=tuple(items),
        supports=tuple(
            edge.model_dump(mode="json") for edge in original.relationships_supports
        ),
    )


def _expected_paths(
    *,
    depth: int,
    parents: dict[UUID, set[UUID]],
    start: UUID,
) -> set[tuple[UUID, ...]]:
    """Enumerate every small-fixture ancestry branch independently of LP indexing.

    Parameters
    ----------
    depth
        Remaining retained depth.
    parents
        Adjacency derived directly from authoritative hierarchy rows.
    start
        Endpoint or ancestor whose complete branches are requested.

    Returns
    -------
    set[tuple[UUID, ...]]
        Every distinct bounded path, with no framework node inserted.
    """
    if depth == 0 or not parents[start]:
        return {()}
    return {
        (parent, *suffix)
        for parent in parents[start]
        for suffix in _expected_paths(depth=depth - 1, parents=parents, start=parent)
    }


def _hash(value: Any) -> str:
    """Hash canonical JSON independently of production helpers.

    Parameters
    ----------
    value
        Actual material under inspection.

    Returns
    -------
    str
        SHA-256 of strict sorted Unicode JSON.
    """
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    """Encode material independently using the artifact serialization contract.

    Parameters
    ----------
    value
        JSON-compatible material.

    Returns
    -------
    str
        Canonical JSON text.
    """
    return json.dumps(
        allow_nan=False,
        ensure_ascii=False,
        obj=value,
        separators=(",", ":"),
        sort_keys=True,
    )


def _reject_network(*args: Any, **kwargs: Any) -> None:
    """Reject socket calls rather than substituting model predictions.

    Parameters
    ----------
    args
        Positional socket arguments.
    kwargs
        Named socket arguments.

    Raises
    ------
    AssertionError
        Always, because these tests are entirely offline.
    """
    raise AssertionError("Request tests must not access the network.")


def _reseal(row: dict[str, Any]) -> dict[str, Any]:
    """Give adversarial request content a truthful digest and derived UUID.

    Parameters
    ----------
    row
        Mutated request payload.

    Returns
    -------
    dict[str, Any]
        Material whose integrity cannot be rejected only for an old digest.
    """
    material = {
        key: value
        for key, value in row.items()
        if key not in {"request_content_hash", "request_id"}
    }
    digest = _hash(material)
    return {
        **material,
        "request_content_hash": digest,
        "request_id": str(
            uuid5(name=f"lc:lp_generation_request:{digest}", namespace=_NAMESPACE)
        ),
    }


@pytest.mark.parametrize(argnames="batch", argvalues=[1, 2, 3, 5, 6, 7])
def test_batches_cover_every_candidate_once_in_contiguous_rank_order(
    batch: int,
) -> None:
    """Exercise exact, remainder, singleton, and oversized batch boundaries.

    Parameters
    ----------
    batch
        Batch ceiling around a six-pair population.
    """
    bundle = _bundle()
    original = bundle.model_dump_json()
    config = _config(batch=batch)
    config_before = config.model_dump_json()
    population = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    candidates = population.candidates.candidates
    assert len(candidates) == 6
    expected_pairs = [candidate.pair_id for candidate in candidates]
    actual_pairs = [
        pair.pair_id for request in population.requests for pair in request.pairs
    ]
    assert actual_pairs == expected_pairs
    assert len(set(actual_pairs)) == 6
    assert [len(request.pairs) for request in population.requests] == [
        min(batch, 6 - start) for start in range(0, 6, batch)
    ]
    for index, request in enumerate(population.requests):
        assert request.request_index == index
        assert [pair.pair_id for pair in request.pairs] == expected_pairs[
            index * batch : (index + 1) * batch
        ]
        endpoints = {
            endpoint
            for pair in request.pairs
            for endpoint in (pair.first_sfi_uuid, pair.second_sfi_uuid)
        }
        assert [sfi.context.sfi_uuid for sfi in request.sfis] == sorted(
            endpoints, key=str
        )
        for pair in request.pairs:
            candidate = next(
                item for item in candidates if item.pair_id == pair.pair_id
            )
            assert pair.admissible_decisions == tuple(candidate.admissible_decisions)
            assert pair.candidate_content_hash == _hash(
                candidate.model_dump(mode="json")
            )
        row = request.model_dump(mode="json")
        assert row == _reseal(row)
        assert (
            LPGenerationRequest.model_validate_json(request.model_dump_json())
            == request
        )
    assert len({request.request_id for request in population.requests}) == len(
        population.requests
    )
    assert bundle.model_dump_json() == original
    assert config.model_dump_json() == config_before


@pytest.mark.parametrize(
    argnames="defect",
    argvalues=[
        "missing",
        "duplicate",
        "reordered",
        "id",
        "permissions",
        "warning",
        "count",
        "hash",
        "stale",
    ],
)
def test_candidate_validator_rejects_mutated_population(defect: str) -> None:
    """Exercise the public candidate validation seam without prefiltering corruption.

    Parameters
    ----------
    defect
        Invalid population property injected after real nomination.
    """
    bundle, config = _bundle(), _config()
    original = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    ).candidates
    rows = [row.model_copy(deep=True) for row in original.candidates]
    summary = original.summary.model_copy(deep=True)
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows[1] = rows[0]
    elif defect == "reordered":
        rows.reverse()
    elif defect == "id":
        rows[0].pair_id = str(UUID(int=999))
    elif defect == "permissions":
        rows[0].admissible_decisions = rows[0].admissible_decisions[1:]
    elif defect == "warning":
        rows[0].warnings = ["Invented warning"]
    elif defect == "count":
        summary.total_candidate_pairs += 1
    elif defect == "hash":
        summary.candidate_pairs_content_hash = "0" * 64
    else:
        bundle.items[0].description += " changed material"
    if defect != "hash":
        summary.candidate_pairs_content_hash = _hash(
            [row.model_dump(mode="json") for row in rows]
        )
    invalid = LPCandidatePopulation(candidates=tuple(rows), summary=summary)
    with pytest.raises(ValueError):
        validate_lp_candidate_population(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, population=invalid
        )


def test_candidate_validator_returns_independently_owned_records() -> None:
    """Validate nested copies so later callers cannot mutate the input population."""
    bundle, config = _bundle(), _config()
    original = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    ).candidates
    validated = validate_lp_candidate_population(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, population=original
    )
    assert validated == original
    validated.candidates[0].warnings.append("local mutation")
    assert "local mutation" not in original.candidates[0].warnings


@pytest.mark.parametrize(
    argnames="characters,count,items",
    argvalues=[
        (1, 1, 1),
        (1999, 1, 2),
        (2000, 1, 2),
        (2001, 1, 2),
        (2000, 0, 2),
        (2000, 1, 2),
        (2000, 2, 2),
        (2000, 3, 2),
        (64, 4, 8),
        (65, 4, 8),
        (66, 4, 8),
        (2000, 1000, 20),
    ],
)
def test_coordinate_origin_exact_aggregate_bounds_and_round_trip(
    characters: int, count: int, items: int, tmp_path: Path
) -> None:
    """Preserve exact coordinate semantics and truthful bounded origin records.

    Parameters
    ----------
    characters
        Aggregate origin character allowance.
    count
        Number of equivalent scope labels, including explicit absence.
    items
        Maximum retained origin records.
    tmp_path
        Isolated complete artifact destination.
    """
    bundle = _bundle(2)
    labels = ["Grade" + " " * n for n in range(count)]
    if count == 1:
        labels = ["Grade" + " " * (2000 - len("identity_scope_values['Grade']"))]
    origins = sorted(f"identity_scope_values[{label!r}]" for label in labels)
    bundle.items[0].metadata["identity_scope_values"] = {
        label: "primary one" for label in reversed(labels)
    }
    before = bundle.model_dump_json()
    config = _config(
        limits={
            "max_source_evidence_characters_per_sfi": characters,
            "max_source_evidence_items_per_sfi": items,
        }
    )
    dirs = KGDirs(root=tmp_path)
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    request = population.requests[0]
    endpoint = next(
        sfi
        for sfi in request.sfis
        if sfi.context.sfi_uuid == bundle.items[0].case_identifier_uuid
    )
    coordinate = endpoint.coordinate
    assert coordinate.canonical_value == ("PRIMARY ONE" if count else None)
    assert coordinate.rank == (0 if count else None)
    assert coordinate.status == ("resolved" if count else "missing")
    assert coordinate.sfi_uuid == bundle.items[0].case_identifier_uuid
    assert coordinate.statement_type == "Grade"
    expected_count = sum(
        sum(len(origin) for origin in origins[:index]) < characters
        for index in range(min(items, count))
    )
    assert len(coordinate.source_fields) == expected_count
    assert coordinate.omitted_source_field_count == count - expected_count
    assert coordinate.source_fields_content_hash == _hash(origins)
    assert sum(len(record.text) for record in coordinate.source_fields) == min(
        characters, sum(len(origin) for origin in origins[:items])
    )
    for index, record in enumerate(coordinate.source_fields):
        full = origins[index]
        allowance = characters - sum(len(origin) for origin in origins[:index])
        assert record.text == full[:allowance]
        assert record.original_characters == len(full)
        assert record.content_hash == hashlib.sha256(full.encode("utf-8")).hexdigest()
        assert record.truncated == (len(full) > allowance)
    warning = f"SFI {coordinate.sfi_uuid} coordinate source references are truncated."
    shortened = expected_count < count or sum(map(len, origins)) > characters
    assert (warning in endpoint.warnings) == shortened
    assert (warning in request.warnings) == shortened
    assert set(endpoint.warnings) <= set(request.warnings)
    if not count:
        assert all(
            decision.decision != "buildsTowards"
            for decision in request.pairs[0].admissible_decisions
        )
    assert LPGenerationRequest.model_validate_json(request.model_dump_json()) == request
    assert (
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )
        == population
    )
    assert bundle.model_dump_json() == before
    reordered = bundle.model_copy(deep=True)
    reordered.items.reverse()
    for item in reordered.items:
        item.metadata["identity_scope_values"] = dict(
            reversed(list(item.metadata["identity_scope_values"].items()))
        )
    assert (
        build_lp_generation_requests(
            as_lc_bundle=reordered, doc_key=_DOC_KEY, kg_config=config
        )
        == population
    )
    manifest = json.loads((tmp_path / _FILES[3]).read_text())
    rows = [
        json.loads(line) for line in (tmp_path / _FILES[2]).read_text().splitlines()
    ]
    assert manifest["requests_content_hash"] == _hash(rows)
    assert manifest["total_requests"] == len(rows) == 1
    assert manifest["total_candidate_pairs"] == len(request.pairs) == 1
    for filename, digest in manifest["artifact_byte_hashes"].items():
        assert digest == hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest()


@pytest.mark.parametrize(argnames="change", argvalues=["omitted", "suffix"])
def test_coordinate_origin_hidden_changes_invalidate_material(
    change: str, tmp_path: Path
) -> None:
    """Invalidate saved requests when only unshown origin material changes.

    Parameters
    ----------
    change
        Omitted record change or retained record suffix change.
    tmp_path
        Isolated saved population destination.
    """
    bundle = _bundle(2)
    labels = ["Grade ", "Grade!"] if change == "omitted" else ["Grade" + " " * 10000]
    bundle.items[0].metadata["identity_scope_values"] = {
        label: "PRIMARY ONE" for label in labels
    }
    config = _config(
        limits={
            "max_source_evidence_characters_per_sfi": 2000,
            "max_source_evidence_items_per_sfi": 1,
        }
    )
    dirs = KGDirs(root=tmp_path)
    original = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    saved = {name: (tmp_path / name).read_bytes() for name in _FILES}
    changed = bundle.model_copy(deep=True)
    scope = changed.items[0].metadata["identity_scope_values"]
    old = sorted(scope, key=lambda label: f"identity_scope_values[{label!r}]")[-1]
    scope[old[:-1] + "β"] = scope.pop(old)
    updated = build_lp_generation_requests(
        as_lc_bundle=changed, doc_key=_DOC_KEY, kg_config=config
    )
    first = original.requests[0].sfis[0].coordinate
    second = updated.requests[0].sfis[0].coordinate
    assert [record.text for record in first.source_fields] == [
        record.text for record in second.source_fields
    ]
    assert first.canonical_value == second.canonical_value == "PRIMARY ONE"
    assert first.rank == second.rank == 0
    assert first.source_fields_content_hash != second.source_fields_content_hash
    if change == "omitted":
        assert first.source_fields == second.source_fields
        assert (
            first.omitted_source_field_count == second.omitted_source_field_count == 1
        )
    else:
        assert (
            first.source_fields[0].original_characters
            == second.source_fields[0].original_characters
        )
        assert (
            first.source_fields[0].content_hash != second.source_fields[0].content_hash
        )
    assert original.requests[0].request_id != updated.requests[0].request_id
    assert (
        original.manifest.requests_content_hash
        != updated.manifest.requests_content_hash
    )
    with pytest.raises(expected_exception=ValueError, match="current material"):
        validate_lp_request_artifacts(
            as_lc_bundle=changed, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )
    assert saved == {name: (tmp_path / name).read_bytes() for name in _FILES}


@pytest.mark.parametrize(argnames="value", argvalues=["UNKNOWN", "PRIMARY TWO"])
def test_coordinate_origin_invalid_omitted_values_fail_before_writes(
    tmp_path: Path, value: str
) -> None:
    """Validate all coordinate values before bounding their origin evidence.

    Parameters
    ----------
    tmp_path
        Existing artifact destination that must remain intact.
    value
        Unrecognized or conflicting value beyond the retained origin population.
    """
    bundle = _bundle(2)
    retained_label = "Grade "
    omitted_label = "Grade" + "!" * 10000
    config = _config(limits={"max_source_evidence_items_per_sfi": 1})
    bundle.items[0].metadata["identity_scope_values"] = {
        retained_label: "PRIMARY ONE",
        omitted_label: "PRIMARY ONE",
    }
    control = (
        build_lp_generation_requests(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
        )
        .requests[0]
        .sfis[0]
        .coordinate
    )
    assert [record.text for record in control.source_fields] == [
        f"identity_scope_values[{retained_label!r}]"
    ]
    assert control.omitted_source_field_count == 1
    bundle.items[0].metadata["identity_scope_values"][omitted_label] = value
    for name in _FILES:
        (tmp_path / name).write_bytes(b"prior material")
    with pytest.raises(expected_exception=ValueError, match="coordinate"):
        write_lp_generation_request_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=config,
            kg_dirs=KGDirs(root=tmp_path),
        )
    assert all((tmp_path / name).read_bytes() == b"prior material" for name in _FILES)


@pytest.mark.parametrize(
    argnames="first,second", argvalues=[(None, None), (None, 2), (2, 0), (1, 1)]
)
def test_coordinate_origin_missing_and_resolved_permissions(
    first: int | None, second: int | None
) -> None:
    """Retain configured ranks and pair permissions independently of origin bounds.

    Parameters
    ----------
    first
        First endpoint local rank, or absent coordinate.
    second
        Second endpoint local rank, or absent coordinate.
    """
    bundle = _bundle(2)
    values = ["PRIMARY ONE", "PRIMARY TWO", "PRIMARY THREE"]
    for item, rank in zip(bundle.items, (first, second), strict=True):
        item.metadata["identity_scope_values"] = (
            {} if rank is None else {"grade ": values[rank].lower()}
        )
    request = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config(limits={"max_source_evidence_characters_per_sfi": 1}),
    ).requests[0]
    for endpoint, rank in zip(request.sfis, (first, second), strict=True):
        assert endpoint.coordinate.rank == rank
        assert endpoint.coordinate.canonical_value == (
            None if rank is None else values[rank]
        )
        assert endpoint.coordinate.status == ("missing" if rank is None else "resolved")
    allowed = {
        (decision.decision, decision.direction)
        for decision in request.pairs[0].admissible_decisions
    }
    assert ("relatesTo", None) in allowed
    directions = {
        direction for decision, direction in allowed if decision == "buildsTowards"
    }
    if first is None or second is None:
        assert not directions
    elif first == second:
        assert directions == {"first_to_second", "second_to_first"}
    else:
        assert directions == {"second_to_first"}


@pytest.mark.parametrize(
    argnames="defect",
    argvalues=[
        "canonical_value",
        "content_hash",
        "omitted_source_field_count",
        "order",
        "original_characters",
        "rank",
        "sfi_uuid",
        "source_fields_content_hash",
        "statement_type",
        "status",
        "text",
        "truncated",
        "warning",
    ],
)
def test_coordinate_origin_resealed_artifact_tampering_fails(
    defect: str, tmp_path: Path
) -> None:
    """Reject altered coordinate evidence even with truthful forged outer identities.

    Parameters
    ----------
    defect
        Coordinate semantics, provenance, or warning mutation.
    tmp_path
        Isolated adversarial artifacts.
    """
    bundle = _bundle(2)
    bundle.items[0].metadata["identity_scope_values"] = {
        "Grade": "PRIMARY ONE",
        "Grade ": "PRIMARY ONE",
        "Grade  ": "PRIMARY ONE",
    }
    config = _config(limits={"max_source_evidence_items_per_sfi": 2})
    dirs = KGDirs(root=tmp_path)
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    row = population.requests[0].model_dump(mode="json")
    coordinate = row["sfis"][0]["coordinate"]
    if defect in {"content_hash", "original_characters", "text", "truncated"}:
        coordinate["source_fields"][0][defect] = {
            "content_hash": "0" * 64,
            "original_characters": 100000,
            "text": "forged",
            "truncated": True,
        }[defect]
    elif defect == "order":
        coordinate["source_fields"].reverse()
    elif defect == "warning":
        row["sfis"][0]["warnings"] = []
        row["warnings"] = []
    else:
        coordinate[defect] = {
            "canonical_value": "PRIMARY TWO",
            "omitted_source_field_count": 0,
            "rank": 1,
            "sfi_uuid": str(UUID(int=999)),
            "source_fields_content_hash": "0" * 64,
            "statement_type": "Class",
            "status": "missing",
        }[defect]
    forged = _reseal(row)
    request_bytes = (_json(forged) + "\n").encode("utf-8")
    (tmp_path / _FILES[2]).write_bytes(request_bytes)
    manifest = population.manifest.model_dump(mode="json")
    manifest["artifact_byte_hashes"][_FILES[2]] = hashlib.sha256(
        request_bytes
    ).hexdigest()
    manifest["request_ids"] = [forged["request_id"]]
    manifest["requests_content_hash"] = _hash([forged])
    (tmp_path / _FILES[3]).write_text(_json(manifest) + "\n")
    with pytest.raises(expected_exception=ValueError, match="current material"):
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )


@pytest.mark.parametrize(argnames="shape", argvalues=["long_label", "many_aliases"])
def test_coordinate_source_references_cannot_bypass_request_bounds(
    shape: str, tmp_path: Path
) -> None:
    """Bound raw coordinate-origin labels or fail before writing insufficient context.

    Parameters
    ----------
    shape
        One oversized valid alias or many equivalent coordinate aliases.
    tmp_path
        Isolated destination for the actual complete materialization boundary.
    """
    bundle = _bundle(2)
    labels = (
        ["Grade" + " " * 100000]
        if shape == "long_label"
        else ["Grade" + " " * padding for padding in range(1, 1001)]
    )
    bundle.items[0].metadata["identity_scope_values"] = {
        label: "PRIMARY ONE" for label in labels
    }
    config = _config(limits={"max_source_evidence_characters_per_sfi": 2000})
    selection = build_lp_selection(as_lc_bundle=bundle, kg_config=config)
    coordinate = next(
        record.coordinate
        for record in selection.eligible_sfis
        if record.sfi.case_identifier_uuid == bundle.items[0].case_identifier_uuid
    )
    assert coordinate.canonical_value == "PRIMARY ONE"
    assert coordinate.rank == 0
    assert len(coordinate.source_fields) == len(labels)
    try:
        population = write_lp_generation_request_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=config,
            kg_dirs=KGDirs(root=tmp_path),
        )
    except ValueError as error:
        assert any(
            word in str(error).lower() for word in ("bound", "context", "exceed")
        )
        assert not list(tmp_path.iterdir())
        return
    assert len(population.requests) == 1
    request_size = len(population.requests[0].model_dump_json())
    assert request_size < 30000, (
        f"Unbounded coordinate source references escaped a 2000-character evidence "
        f"limit: {request_size} request characters for {len(labels)} valid aliases."
    )


def test_dag_path_overflow_fails_before_artifacts_change(tmp_path: Path) -> None:
    """Never silently choose one parent when the configured path bound is insufficient.

    Parameters
    ----------
    tmp_path
        Isolated materialization directory.
    """
    bundle = _expanded_fixture("pratham_science")
    config = _config(
        limits={"max_ancestor_paths_per_sfi": 1}, profile="pratham_science"
    )
    for name in _FILES:
        (tmp_path / name).write_bytes(b"prior material")
    with pytest.raises(expected_exception=ValueError, match="DAG paths"):
        write_lp_generation_request_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=config,
            kg_dirs=KGDirs(root=tmp_path),
        )
    assert all((tmp_path / name).read_bytes() == b"prior material" for name in _FILES)


@pytest.mark.parametrize(argnames="depth", argvalues=[1, 2, 8])
def test_dag_paths_preserve_each_branch_and_truthful_depth_bound(depth: int) -> None:
    """Compare bounded paths against the upstream graph rather than its traversal helper.

    Parameters
    ----------
    depth
        Maximum retained ancestor depth.
    """
    bundle = _expanded_fixture("pratham_science")
    config = _config(
        limits={"max_ancestor_path_depth": depth, "max_ancestor_paths_per_sfi": 100},
        profile="pratham_science",
    )
    population = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    parents: dict[UUID, set[UUID]] = {
        item.case_identifier_uuid: set() for item in bundle.items
    }
    for edge in bundle.relationships_has_child:
        if edge.source_entity == "StandardsFrameworkItem":
            parents[UUID(edge.target_entity_value)].add(UUID(edge.source_entity_value))
    seen_dag = False
    for request in population.requests:
        for sfi in request.sfis:
            direct = parents[sfi.context.sfi_uuid]
            assert set(sfi.parent_sfi_uuids) == direct
            seen_dag |= len(direct) > 1
            assert {
                path.ancestor_sfi_uuids for path in sfi.ancestor_paths
            } == _expected_paths(
                depth=depth,
                parents=parents,
                start=sfi.context.sfi_uuid,
            ) - {
                ()
            }
            assert {
                path.ancestor_sfi_uuids[0]
                for path in sfi.ancestor_paths
                if path.ancestor_sfi_uuids
            } == direct
            for path in sfi.ancestor_paths:
                chain = path.ancestor_sfi_uuids
                assert len(chain) <= depth
                previous = sfi.context.sfi_uuid
                for ancestor in chain:
                    assert ancestor in parents[previous]
                    previous = ancestor
                assert path.depth_truncated == bool(parents[previous])
            if any(path.depth_truncated for path in sfi.ancestor_paths):
                assert any("depth-truncated" in warning for warning in sfi.warnings)
                assert set(sfi.warnings) <= set(request.warnings)
    assert seen_dag


@pytest.mark.parametrize(argnames="filename", argvalues=_FILES)
def test_empty_population_records_zero_counts_and_truthful_empty_hashes(
    filename: str, tmp_path: Path
) -> None:
    """Materialize an empty candidate set explicitly and reject any missing receipt file.

    Parameters
    ----------
    filename
        Required artifact removed after successful persistence.
    tmp_path
        Isolated output directory.
    """
    bundle, config = _bundle(1), _config()
    dirs = KGDirs(root=tmp_path)
    population = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    assert population.requests == population.candidates.candidates == ()
    manifest = population.manifest
    assert manifest.total_requests == manifest.total_candidate_pairs == 0
    assert manifest.pair_ids == manifest.request_ids == ()
    assert (
        manifest.candidate_pairs_content_hash
        == manifest.requests_content_hash
        == _hash([])
    )
    assert (tmp_path / "lp_candidate_pairs.jsonl").read_bytes() == b""
    assert (tmp_path / "lp_generation_requests.jsonl").read_bytes() == b""
    assert (
        manifest.artifact_byte_hashes["lp_generation_requests.jsonl"]
        == hashlib.sha256(b"").hexdigest()
    )
    (tmp_path / filename).unlink()
    with pytest.raises(OSError):
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )


@pytest.mark.parametrize(argnames="filename", argvalues=_FILES)
def test_interrupted_write_never_returns_validated_population(
    filename: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inject partial writes at each boundary and reject the abandoned file population.

    Parameters
    ----------
    filename
        Artifact whose write is interrupted.
    monkeypatch
        Restoring filesystem fault injection.
    tmp_path
        Isolated output directory with a previous valid population.
    """
    bundle, config = _bundle(), _config()
    dirs = KGDirs(root=tmp_path)
    write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    write_bytes = Path.write_bytes

    def _interrupt(self: Path, data: bytes) -> int:
        """Leave a partial artifact and report the actual storage failure.

        Parameters
        ----------
        self
            Destination path.
        data
            Intended complete bytes.

        Returns
        -------
        int
            Bytes written for unaffected files.

        Raises
        ------
        OSError
            On the selected interrupted write.
        """
        if self.name == filename:
            write_bytes(data=data[:17], self=self)
            raise OSError("synthetic interrupted write")
        return write_bytes(data=data, self=self)

    monkeypatch.setattr(name="write_bytes", target=Path, value=_interrupt)
    with pytest.raises(expected_exception=OSError, match="synthetic interrupted"):
        write_lp_generation_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )
    with pytest.raises((OSError, ValueError)):
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )


@pytest.mark.parametrize(
    argnames="defect", argvalues=["missing", "duplicate", "reordered"]
)
def test_invalid_candidate_population_cannot_reach_writer(
    defect: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validate builder results before changing a previously complete artifact set.

    Parameters
    ----------
    defect
        Candidate population error returned by a faulty nomination stage.
    monkeypatch
        Restoring stage-boundary fault injection.
    tmp_path
        Directory whose sentinel bytes must survive validation failure.
    """
    bundle, config = _bundle(), _config()
    original = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    ).candidates
    rows = list(original.candidates)
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows[1] = rows[0]
    else:
        rows.reverse()
    invalid = LPCandidatePopulation(candidates=tuple(rows), summary=original.summary)

    def _invalid_population(**kwargs: Any) -> LPCandidatePopulation:
        """Return the corrupted population at the real downstream validation seam.

        Parameters
        ----------
        kwargs
            Authoritative candidate-construction inputs.

        Returns
        -------
        LPCandidatePopulation
            Invalid unfiltered candidate sequence.
        """
        assert set(kwargs) == {"as_lc_bundle", "doc_key", "kg_config"}
        return invalid

    monkeypatch.setattr(
        name="build_lp_candidates", target=lp_generation, value=_invalid_population
    )
    for filename in _FILES:
        (tmp_path / filename).write_bytes(b"prior complete population")
    with pytest.raises(ValueError):
        write_lp_generation_request_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=config,
            kg_dirs=KGDirs(root=tmp_path),
        )
    assert all(
        (tmp_path / filename).read_bytes() == b"prior complete population"
        for filename in _FILES
    )


def test_lc_descriptions_and_metadata_are_bounded_with_truthful_hashes() -> None:
    """Retain fixed-size LC excerpts while binding both to their full material."""
    bundle = _factories._signal_bundle(
        count=2, endpoints=(1, 2), signals=("shared_learning_components",)
    )
    component = bundle.learning_components[0]
    component.description = "λ" * 10000
    component.metadata["source_note"] = "é" * 10000
    population = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config(limits={"max_source_evidence_characters_per_sfi": 2000}),
    )
    request = population.requests[0]
    for sfi in request.sfis:
        retained = sfi.learning_components[0]
        for excerpt, full in (
            (retained.description, component.description),
            (retained.metadata, _json(component.model_dump(mode="json")["metadata"])),
        ):
            assert excerpt.text == full[:2000]
            assert (
                excerpt.content_hash == hashlib.sha256(full.encode("utf-8")).hexdigest()
            )
            assert excerpt.original_characters == len(full)
            assert excerpt.truncated
        assert retained.upstream_content_hash == _hash(
            component.model_dump(mode="json")
        )
        assert any(
            "LC evidence is truncated" in warning for warning in request.warnings
        )


@pytest.mark.parametrize(argnames="bound", argvalues=[1, 2, 3])
def test_lc_limits_filter_nomination_references_and_retain_complete_hashes(
    bound: int,
) -> None:
    """Keep omitted components out of request references and identify their full material.

    Parameters
    ----------
    bound
        Retained component count below, at, or above available evidence.
    """
    items = tuple(_factories._item(number=n) for n in (1, 2))
    components, supports = _factories._signal_components(
        endpoints=(1, 2), signals=("shared_learning_components",)
    )
    second = deepcopy(components[0])
    second["identifier"] = str(UUID(int=20001))
    second["description"] = "omitted component marker"
    for n in (1, 2):
        edge = _factories._edge(
            source=UUID(int=20001),
            source_entity="LearningComponent",
            target=UUID(int=n),
        )
        edge.update(relationship_type="supports", source_entity_key="identifier")
        supports.append(edge)
    components.append(second)
    bundle = _factories._bundle(
        components=tuple(components), items=items, supports=tuple(supports)
    )
    config = _config(
        limits={
            "max_learning_components_per_sfi": bound,
            "max_source_evidence_characters_per_sfi": 20000,
        }
    )
    request = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    ).requests[0]
    for sfi in request.sfis:
        assert len(sfi.learning_components) == min(bound, 2)
        assert sfi.omitted_learning_component_count == max(2 - bound, 0)
        assert sfi.learning_components_content_hash == _hash(
            [
                component.model_dump(mode="json")
                for component in bundle.learning_components
            ]
        )
        if bound == 1:
            assert any(
                "LC evidence is truncated" in warning for warning in request.warnings
            )
    pair = request.pairs[0]
    evidence = json.loads(pair.nomination.evidence.text)
    shared = next(
        item
        for item in evidence
        if item["evidence_type"] == "shared_learning_components"
    )
    assert len(shared["triggering_values"]["shared_values"]) == min(bound, 2)
    if bound == 1:
        assert str(UUID(int=20001)) not in pair.nomination.evidence.text
        assert pair.nomination.references_truncated
        assert shared["omitted_shared_value_count"] == 1
        assert any(
            "nomination evidence is truncated" in warning
            for warning in request.warnings
        )


@pytest.mark.parametrize(
    argnames="field",
    argvalues=[
        "audit_notes",
        "merge_notes",
        "normalized_statement_code",
        "statement_value_canonicalization",
    ],
)
def test_mandatory_audit_context_overflow_fails_closed(
    field: str, tmp_path: Path
) -> None:
    """Refuse to hide complete audit evidence behind an excerpt when it cannot fit.

    Parameters
    ----------
    field
        Known audit or code-normalization field.
    tmp_path
        Isolated destination that must remain untouched.
    """
    bundle = _bundle(2)
    bundle.items[0].metadata[field] = "x" * 2001
    config = _config(limits={"max_source_evidence_characters_per_sfi": 2000})
    with pytest.raises(expected_exception=ValueError, match="audit context exceeds"):
        write_lp_generation_request_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=config,
            kg_dirs=KGDirs(root=tmp_path),
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    argnames="field",
    argvalues=[
        "total_candidate_pairs",
        "total_requests",
        "pair_ids",
        "request_ids",
        "requests_content_hash",
        "artifact_byte_hashes",
        "config_content_hash",
        "upstream_content_hash",
    ],
)
def test_manifest_tampering_is_rejected(field: str, tmp_path: Path) -> None:
    """Reconcile manifest claims against actual rows and authoritative current inputs.

    Parameters
    ----------
    field
        Count, identity, or hash altered in the materialization receipt.
    tmp_path
        Isolated output directory.
    """
    bundle, config = _bundle(), _config()
    dirs = KGDirs(root=tmp_path)
    write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    path = tmp_path / _FILES[-1]
    manifest = json.loads(path.read_bytes())
    value = manifest[field]
    manifest[field] = (
        value + 1
        if isinstance(value, int)
        else (
            list(reversed(value))
            if isinstance(value, list)
            else ({} if isinstance(value, dict) else "0" * 64)
        )
    )
    path.write_text(data=_json(manifest) + "\n", encoding="utf-8")
    with pytest.raises(expected_exception=ValueError, match="manifest"):
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )


def test_nomination_ancestor_references_cannot_bypass_depth_limits() -> None:
    """Filter omitted hierarchy UUIDs from references and identity-bearing values."""
    bundle = _expanded_fixture("pratham_science")
    population = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config(
            limits={
                "max_ancestor_path_depth": 1,
                "max_source_evidence_characters_per_sfi": 20000,
            },
            profile="pratham_science",
        ),
    )
    omitted = 0
    for request in population.requests:
        retained = {
            str(node.sfi_uuid)
            for sfi in request.sfis
            for node in (sfi.context, *sfi.ancestors)
        }
        for pair in request.pairs:
            candidate = next(
                candidate
                for candidate in population.candidates.candidates
                if candidate.pair_id == pair.pair_id
            )
            assert pair.nomination.complete_evidence_content_hash == _hash(
                [evidence.model_dump(mode="json") for evidence in candidate.evidence]
            )
            assert not pair.nomination.evidence.truncated
            for evidence in json.loads(pair.nomination.evidence.text):
                for reference in evidence["references"]:
                    if reference.startswith("sfi:"):
                        assert reference.removeprefix("sfi:") in retained
                for ancestor in evidence["triggering_values"].get(
                    "shared_ancestors", []
                ):
                    assert ancestor["sfi_uuid"] in retained
                omitted += evidence.get("omitted_shared_ancestor_count", 0)
            if pair.nomination.references_truncated:
                assert any(
                    "nomination evidence is truncated" in warning
                    for warning in request.warnings
                )
    assert omitted > 0


@pytest.mark.parametrize(
    argnames="filename",
    argvalues=["lp_candidate_pairs.jsonl", "lp_generation_requests.jsonl"],
)
@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=[
        "missing",
        "duplicate",
        "reordered",
        "malformed",
        "trailing",
        "empty",
        "self_consistent_tamper",
    ],
)
def test_population_artifacts_reject_missing_duplicate_reordered_and_forged_rows(
    filename: str, mutation: str, tmp_path: Path
) -> None:
    """Reject changed populations even when an attacker updates the stored byte hash.

    Parameters
    ----------
    filename
        Candidate or request population under attack.
    mutation
        Missing, malformed, or self-consistently rehashed material.
    tmp_path
        Isolated output directory.
    """
    bundle, config = _bundle(), _config()
    dirs = KGDirs(root=tmp_path)
    write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    path = tmp_path / filename
    rows = path.read_bytes().splitlines(keepends=True)
    if mutation == "missing":
        rows.pop(1)
    elif mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "reordered":
        rows.reverse()
    elif mutation == "malformed":
        rows[-1] = b'{"unfinished":'
    elif mutation == "trailing":
        rows.append(b"garbage\n")
    elif mutation == "empty":
        rows = []
    else:
        row = json.loads(rows[0])
        row["warnings"] = ["forged context"]
        if filename == "lp_generation_requests.jsonl":
            row = _reseal(row)
        rows[0] = (_json(row) + "\n").encode("utf-8")
    payload = b"".join(rows)
    path.write_bytes(payload)
    manifest_path = tmp_path / _FILES[-1]
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifact_byte_hashes"][filename] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(data=_json(manifest) + "\n", encoding="utf-8")
    with pytest.raises(expected_exception=ValueError, match="current material"):
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )


@pytest.mark.parametrize(
    argnames="defect",
    argvalues=[
        "duplicate_pair",
        "missing_sfi",
        "extra_sfi",
        "reordered_sfis",
        "hash",
        "id",
        "reference_hash",
        "reference_length",
        "reference_flag",
    ],
)
def test_request_schema_rejects_identity_coverage_and_bounded_text_lies(
    defect: str,
) -> None:
    """Separate intrinsic coverage and excerpt checks from outer digest validation.

    Parameters
    ----------
    defect
        Invalid request property, resealed where needed to reach its own validator.
    """
    request = build_lp_generation_requests(
        as_lc_bundle=_bundle(), doc_key=_DOC_KEY, kg_config=_config()
    ).requests[0]
    row = request.model_dump(mode="json")
    if defect == "duplicate_pair":
        row["pairs"].append(deepcopy(row["pairs"][0]))
    elif defect == "missing_sfi":
        row["sfis"].pop()
    elif defect == "extra_sfi":
        row["sfis"].append(deepcopy(row["sfis"][0]))
    elif defect == "reordered_sfis":
        row["sfis"].reverse()
    elif defect == "hash":
        row["request_content_hash"] = "0" * 64
    elif defect == "id":
        row["request_id"] = str(UUID(int=999))
    else:
        reference = row["sfis"][0]["source_evidence"][0]["reference"]
        if defect == "reference_hash":
            reference["content_hash"] = "0" * 64
        elif defect == "reference_length":
            reference["original_characters"] = 0
        else:
            reference["truncated"] = True
    if defect not in {"hash", "id"}:
        row = _reseal(row)
    with pytest.raises(ValueError):
        LPGenerationRequest.model_validate(row)


def test_request_uuid_collisions_fail_before_materialization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise population collision detection under a faulty identity primitive.

    Parameters
    ----------
    monkeypatch
        Restoring UUID fault injection.
    tmp_path
        Output directory that must remain empty.
    """

    def _constant_uuid(*, name: str, namespace: UUID) -> UUID:
        """Return a colliding UUID for every distinct request payload.

        Parameters
        ----------
        name
            Material-derived identity name.
        namespace
            Canonical namespace.

        Returns
        -------
        UUID
            Deliberately duplicated identifier.
        """
        assert namespace == _NAMESPACE
        assert name.startswith("lc:lp_generation_request:")
        return UUID(int=999)

    monkeypatch.setattr(name="uuid5", target=lp_generation, value=_constant_uuid)
    with pytest.raises(expected_exception=ValueError, match="collision"):
        write_lp_generation_request_artifacts(
            as_lc_bundle=_bundle(),
            doc_key=_DOC_KEY,
            kg_config=_config(),
            kg_dirs=KGDirs(root=tmp_path),
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(argnames="expanded", argvalues=[False, True])
@pytest.mark.parametrize(argnames="profile", argvalues=_PROFILES)
def test_six_fixture_round_trips_reconcile_material_and_preserve_upstream(
    expanded: bool, profile: str, tmp_path: Path
) -> None:
    """Compare actual artifact rows, independent hashes, and untouched AS/AS+LC bytes.

    Parameters
    ----------
    expanded
        Whether synthetic same-grain peers make every Standard participate.
    profile
        One approved reduced curriculum projection.
    tmp_path
        Isolated artifact directory.
    """
    bundle = (
        _expanded_fixture(profile) if expanded else _factories._fixture_bundle(profile)
    )
    config = _config(batch=2, profile=profile)
    before = bundle.model_dump_json()
    config_before = config.model_dump_json()
    sentinels = {
        name: (name + " original bytes").encode()
        for name in (
            "as_kg_bundle.json",
            "as_nodes.jsonl",
            "as_relationships.jsonl",
            "as_lc_kg_bundle.json",
            "as_lc_nodes.jsonl",
            "as_lc_relationships.jsonl",
        )
    }
    for name, data in sentinels.items():
        (tmp_path / name).write_bytes(data)
    dirs = KGDirs(root=tmp_path)
    first = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    saved = {name: (tmp_path / name).read_bytes() for name in _FILES}
    assert (
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )
        == first
    )
    candidates = [json.loads(line) for line in saved[_FILES[0]].splitlines()]
    requests = [json.loads(line) for line in saved[_FILES[2]].splitlines()]
    manifest = json.loads(saved[_FILES[3]])
    if expanded:
        assert len(candidates) > 0
    elif profile in {"madhi_math", "nigeria_math", "pratham_science"}:
        assert candidates == []
    assert manifest["total_candidate_pairs"] == len(candidates)
    assert manifest["total_requests"] == len(requests)
    assert manifest["pair_ids"] == [row["pair_id"] for row in candidates]
    assert manifest["pair_ids"] == [
        pair["pair_id"] for row in requests for pair in row["pairs"]
    ]
    assert manifest["request_ids"] == [row["request_id"] for row in requests]
    assert manifest["candidate_pairs_content_hash"] == _hash(candidates)
    assert manifest["candidate_summary_content_hash"] == _hash(
        json.loads(saved[_FILES[1]])
    )
    assert manifest["requests_content_hash"] == _hash(requests)
    assert manifest["artifact_byte_hashes"] == {
        name: hashlib.sha256(saved[name]).hexdigest() for name in _FILES[:3]
    }
    assert bundle.model_dump_json() == before
    assert config.model_dump_json() == config_before
    assert all(
        (tmp_path / name).read_bytes() == data for name, data in sentinels.items()
    )
    bundle.items.reverse()
    bundle.learning_components.reverse()
    bundle.relationships_has_child.reverse()
    bundle.relationships_supports.reverse()
    second = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    assert second == first
    assert saved == {name: (tmp_path / name).read_bytes() for name in _FILES}


@pytest.mark.parametrize(argnames="size", argvalues=[1999, 2000, 2001])
def test_source_reference_exact_character_boundary(size: int) -> None:
    """Warn precisely when the complete origin label exceeds its character ceiling.

    Parameters
    ----------
    size
        Full reference length immediately below, at, and above the bound.
    """
    bundle = _bundle(2)
    key = "a" * (size - len("metadata.[0]"))
    bundle.items[0].metadata[key] = "value"
    request = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config(limits={"max_source_evidence_characters_per_sfi": 2000}),
    ).requests[0]
    sfi = next(
        sfi
        for sfi in request.sfis
        if sfi.context.sfi_uuid == bundle.items[0].case_identifier_uuid
    )
    reference = next(
        evidence.reference
        for evidence in sfi.source_evidence
        if evidence.excerpt.text == "value"
    )
    assert reference.original_characters == size
    assert len(reference.text) == min(size, 2000)
    assert reference.truncated == (size > 2000)
    assert any(
        "source evidence is truncated" in warning for warning in sfi.warnings
    ) == (size > 2000)


def test_source_reference_prefix_collisions_preserve_distinct_origins() -> None:
    """Keep distinct source records even when bounded labels have identical prefixes."""
    bundle = _bundle(2)
    item = bundle.items[0]
    item.metadata["a" * 3000 + "one"] = "first"
    item.metadata["a" * 3000 + "two"] = "second"
    request = build_lp_generation_requests(
        as_lc_bundle=bundle,
        doc_key=_DOC_KEY,
        kg_config=_config(limits={"max_source_evidence_characters_per_sfi": 2000}),
    ).requests[0]
    sfi = next(
        sfi for sfi in request.sfis if sfi.context.sfi_uuid == item.case_identifier_uuid
    )
    records = [
        record
        for record in sfi.source_evidence
        if record.excerpt.text in {"first", "second"}
    ]
    assert len(records) == 2
    assert records[0].reference.text == records[1].reference.text
    assert records[0].reference.content_hash != records[1].reference.content_hash
    assert {record.excerpt.text for record in records} == {"first", "second"}


@pytest.mark.parametrize(argnames="origin", argvalues=["metadata", "provenance"])
def test_source_reference_regression_bounds_growth_and_tracks_omitted_suffix(
    origin: str, tmp_path: Path
) -> None:
    """Bound adversarial metadata keys without losing their full identity or warnings.

    Parameters
    ----------
    origin
        Authoritative metadata or separately preserved item provenance.
    tmp_path
        Isolated round-trip artifact directory.
    """
    config = _config(
        limits={
            "max_source_evidence_characters_per_sfi": 2000,
            "max_source_evidence_items_per_sfi": 2,
        }
    )
    requests = []
    refs = []
    for index, key in enumerate(("a" * 10000, "a" * 100000, "a" * 99999 + "β")):
        bundle = _bundle(2)
        item = bundle.items[0]
        mapping = (
            item.metadata
            if origin == "metadata"
            else bundle.entity_provenance["items"][str(item.case_identifier_uuid)]
        )
        mapping[key] = "évidence"
        dirs = KGDirs(root=tmp_path / str(index))
        population = write_lp_generation_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )
        assert (
            validate_lp_request_artifacts(
                as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
            )
            == population
        )
        request = population.requests[0]
        sfi = next(
            sfi
            for sfi in request.sfis
            if sfi.context.sfi_uuid == item.case_identifier_uuid
        )
        reference = next(
            evidence.reference
            for evidence in sfi.source_evidence
            if evidence.excerpt.text == "évidence"
        )
        full = f"{origin}.{key}[0]"
        assert reference.text == full[:2000]
        assert reference.original_characters == len(full)
        assert (
            reference.content_hash == hashlib.sha256(full.encode("utf-8")).hexdigest()
        )
        assert reference.truncated is True
        assert any(
            "source evidence is truncated" in warning for warning in sfi.warnings
        )
        assert set(sfi.warnings) <= set(request.warnings)
        assert len(request.model_dump_json()) < 30000
        refs.append(reference)
        requests.append(request)
    assert len(requests[1].model_dump_json()) - len(requests[0].model_dump_json()) < 100
    assert refs[1].text == refs[2].text
    assert refs[1].original_characters == refs[2].original_characters
    assert refs[1].content_hash != refs[2].content_hash
    assert requests[1].request_id != requests[2].request_id
    assert requests[1].request_content_hash != requests[2].request_content_hash


@pytest.mark.parametrize(
    argnames="characters,items", argvalues=[(200, 1), (2000, 2), (2000, 20)]
)
def test_source_value_aggregate_limits_and_full_text_hashes(
    characters: int, items: int
) -> None:
    """Respect both source-item and aggregate character ceilings with truthful excerpts.

    Parameters
    ----------
    characters
        Aggregate per-SFI excerpt budget.
    items
        Maximum source evidence records per SFI.
    """
    bundle = _bundle(2)
    values = ["λ" * 1500, "é" * 1500, "remaining source"]
    bundle.items[0].metadata["candidate_source_texts"] = values
    config = _config(
        limits={
            "max_source_evidence_characters_per_sfi": characters,
            "max_source_evidence_items_per_sfi": items,
        }
    )
    request = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    ).requests[0]
    sfi = next(
        sfi
        for sfi in request.sfis
        if sfi.context.sfi_uuid == bundle.items[0].case_identifier_uuid
    )
    assert len(sfi.source_evidence) <= items
    assert (
        sum(len(evidence.excerpt.text) for evidence in sfi.source_evidence)
        <= characters
    )
    for evidence in sfi.source_evidence:
        source_index = int(evidence.reference.text.rsplit("[", 1)[1][:-1])
        full = values[source_index]
        assert evidence.excerpt.text == full[: len(evidence.excerpt.text)]
        assert evidence.excerpt.original_characters == len(full)
        assert (
            evidence.excerpt.content_hash
            == hashlib.sha256(full.encode("utf-8")).hexdigest()
        )
        assert evidence.excerpt.truncated == (len(full) > len(evidence.excerpt.text))
    assert sfi.omitted_source_evidence_count == 4 - len(sfi.source_evidence)
    assert any(
        "source evidence is truncated" in warning for warning in request.warnings
    )


@pytest.mark.parametrize(
    argnames="change",
    argvalues=[
        "batch",
        "bounds",
        "policy",
        "instructions",
        "description",
        "provenance",
        "framework",
        "lc",
    ],
)
def test_stale_material_is_rejected_and_changes_request_identity(
    change: str, tmp_path: Path
) -> None:
    """Detect altered material even when its prior artifacts are internally consistent.

    Parameters
    ----------
    change
        Material input changed after a complete valid write.
    tmp_path
        Isolated output directory.
    """
    bundle = _expanded_fixture("nigeria_math")
    config = _config()
    dirs = KGDirs(root=tmp_path)
    prior = write_lp_generation_request_artifacts(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
    )
    if change == "batch":
        config.learning_progressions.request_batch_size = 2
    elif change == "bounds":
        config.learning_progressions.evidence_limits.max_source_evidence_characters_per_sfi += (
            1
        )
    elif change == "policy":
        config.learning_progressions.unresolved_participation = "exclude_unresolved"
    elif change == "instructions":
        payload = config.model_dump(by_alias=True, mode="json")
        payload["lp"]["producer_instructions"] += " Additional synthetic instruction."
        config = CreateKGConfig.model_validate(payload)
    elif change == "description":
        bundle.items[0].description += " altered"
    elif change == "provenance":
        bundle.entity_provenance["items"][str(bundle.items[0].case_identifier_uuid)][
            "source_note"
        ] = "altered"
    elif change == "framework":
        bundle.framework.name += " altered"
    else:
        bundle.learning_components[0].description += " altered"
    with pytest.raises(ValueError):
        validate_lp_request_artifacts(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config, kg_dirs=dirs
        )
    current = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    assert (
        current.manifest.requests_content_hash != prior.manifest.requests_content_hash
    )
    assert set(current.manifest.request_ids).isdisjoint(prior.manifest.request_ids)


def test_unrelated_global_context_does_not_enter_bounded_requests() -> None:
    """Keep a large unrelated node out of endpoint context and source excerpts."""
    items = tuple(_factories._item(number=n) for n in (1, 2))
    sizes = []
    identities = []
    for length in (10000, 100000):
        unrelated = _factories._item(
            description="UNRELATED_GLOBAL_CONTEXT" * length,
            normalized_statement_type="Standard Grouping",
            number=3,
            statement_type="Topic",
        )
        bundle = _factories._bundle(items=(*items, unrelated))
        request = build_lp_generation_requests(
            as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=_config()
        ).requests[0]
        assert {sfi.context.sfi_uuid for sfi in request.sfis} == {
            UUID(int=1),
            UUID(int=2),
        }
        assert "UNRELATED_GLOBAL_CONTEXT" not in request.model_dump_json()
        sizes.append(len(request.model_dump_json()))
        identities.append(request.request_id)
    assert sizes[0] == sizes[1]
    assert identities[0] != identities[1]


def test_unresolved_and_audit_warnings_remain_available_without_fallback_evidence() -> (
    None
):
    """Carry inherited quality state into every affected bounded request."""
    bundle = _factories._fixture_bundle("ghana_math")
    config = _config(profile="ghana_math")
    population = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=_DOC_KEY, kg_config=config
    )
    seen_unresolved = False
    for request in population.requests:
        for pair in request.pairs:
            candidate = next(
                candidate
                for candidate in population.candidates.candidates
                if candidate.pair_id == pair.pair_id
            )
            assert pair.warnings == tuple(candidate.warnings)
            assert set(pair.warnings) <= set(request.warnings)
        for sfi in request.sfis:
            assert "provenance.audit" in sfi.context.audit_context
            assert (
                set(sfi.context.warnings) <= set(sfi.warnings) <= set(request.warnings)
            )
            if sfi.context.unresolved_ancestry:
                seen_unresolved = True
                assert sfi.context.root_fallback_relationship_uuids
                assert any(
                    "unresolved" in warning.lower() for warning in request.warnings
                )
                assert bundle.framework.case_identifier_uuid not in sfi.parent_sfi_uuids
                assert all(
                    bundle.framework.case_identifier_uuid not in path.ancestor_sfi_uuids
                    for path in sfi.ancestor_paths
                )
    assert seen_unresolved


@pytest.mark.parametrize(
    argnames="defect",
    argvalues=[
        "failed",
        "errors",
        "duplicate_sfi",
        "missing_endpoint",
        pytest.param(
            "collision",
            marks=pytest.mark.skip(
                reason="Cross-entity release collision checks belong to standalone/combined graph validation."
            ),
        ),
    ],
)
def test_upstream_integrity_failures_block_request_materialization(
    defect: str, tmp_path: Path
) -> None:
    """Consume the validated graph boundary without trusting its passed flag alone.

    Parameters
    ----------
    defect
        Invalid authoritative graph property.
    tmp_path
        Destination that must remain empty.
    """
    bundle = _bundle()
    if defect == "failed":
        bundle.validation_report.passed = False
    elif defect == "errors":
        bundle.validation_report.errors.append("synthetic upstream error")
    elif defect == "duplicate_sfi":
        bundle.items.append(bundle.items[0])
    elif defect == "missing_endpoint":
        bundle.relationships_has_child[0].target_entity_value = str(UUID(int=999))
    else:
        bundle.relationships_has_child[0].identifier = bundle.items[
            0
        ].case_identifier_uuid
    with pytest.raises(ValueError):
        write_lp_generation_request_artifacts(
            as_lc_bundle=bundle,
            doc_key=_DOC_KEY,
            kg_config=_config(),
            kg_dirs=KGDirs(root=tmp_path),
        )
    assert not list(tmp_path.iterdir())


def test_write_readback_detects_silent_short_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Require actual persisted bytes rather than a successful write return code.

    Parameters
    ----------
    monkeypatch
        Restoring storage fault injection.
    tmp_path
        Isolated output directory.
    """
    write_bytes = Path.write_bytes

    def _short_write(self: Path, data: bytes) -> int:
        """Pretend a truncated request write completed successfully.

        Parameters
        ----------
        self
            Destination file.
        data
            Intended complete bytes.

        Returns
        -------
        int
            Reported byte count, deliberately false for the request artifact.
        """
        actual = data[:11] if self.name == "lp_generation_requests.jsonl" else data
        write_bytes(data=actual, self=self)
        return len(data)

    monkeypatch.setattr(name="write_bytes", target=Path, value=_short_write)
    with pytest.raises(expected_exception=ValueError, match="write reconciliation"):
        write_lp_generation_request_artifacts(
            as_lc_bundle=_bundle(),
            doc_key=_DOC_KEY,
            kg_config=_config(),
            kg_dirs=KGDirs(root=tmp_path),
        )
    assert not (tmp_path / _FILES[-1]).exists()
