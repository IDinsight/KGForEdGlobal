"""Challenge full snapshot interpretation, immutable evidence and resume boundaries."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import hashlib
import json
import shutil
import subprocess

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

# Package Library
from kgfeg.entries import evaluate_lps as entry
from kgfeg.evals.lp_eval import judge, sampling, scoring
from kgfeg.evals.lp_eval.schemas import FrozenInputs, ReportProvenance
from tests.fixtures.lp_eval.snapshot_fixtures import build_snapshot
from tests.kgfeg.evals.lp_eval import test_current_input_contract as current
from tests.kgfeg.evals.lp_eval import test_independent_evaluator as evaluator
from tests.kgfeg.evals.lp_eval import test_projection_compatibility as projections

# Private fixtures are dependency-injected by pytest.
# pylint: disable=useless-param-doc

_offline = projections._offline
_sources = projections._sources
_CELLS = [("journal_bearing", "wire")]
_FORMATS = {"wire": "learning_commons_wire"}
_OVERRIDES = {
    "additional_diagnostic_replicates": 1,
    "diagnostic_pairs_per_cohort": 1,
    "independent_pairs_per_tag": 1,
    "independent_uniform_pairs": 1,
    "production_examples_per_tag": 1,
    "production_pairs_per_outcome": 1,
    "synthetic_cases_per_family": 1,
    "synthetic_control_replicates": 1,
}


def _copy(
    *, checkpoint: str, projection: str, root: Path, sources: dict[str, Path]
) -> Path:
    """Copy one independently selected format cell and validate its baseline.

    Parameters
    ----------
    checkpoint
        Original checkpoint family.
    projection
        Independently selected projection family.
    root
        Test-owned destination.
    sources
        Immutable reduced source fixtures.

    Returns
    -------
    Path
        Fully validated disposable source directory.
    """
    shutil.copytree(dst=root / "source", src=sources[checkpoint].parent)
    directory = root / "source/kgs"
    projections._format(directory=directory, projection=projection)
    snapshot = sampling.validate_lp_snapshot(projections._run(directory))
    assert snapshot.checkpoint_format == checkpoint
    assert snapshot.projection_format == _FORMATS[projection]
    return directory


def _read(path: Path) -> Any:
    """Decode synthetic JSON or JSONL without using a production reader.

    Parameters
    ----------
    path
        Test-owned artifact.

    Returns
    -------
    Any
        Decoded JSON material.
    """
    payload = path.read_bytes()
    return (
        [json.loads(line) for line in payload.splitlines()]
        if path.suffix == ".jsonl"
        else json.loads(payload)
    )


def _rewrite_manifest(*, frozen: FrozenInputs, mutation: str) -> FrozenInputs:
    """Create a separately authenticated adversarial manifest, preserving the original.

    Parameters
    ----------
    frozen
        Valid immutable input store.
    mutation
        Unsupported or unqualified interpretation.

    Returns
    -------
    FrozenInputs
        New test-only manifest reference.
    """
    material = _read(frozen.manifest_path)
    snapshot = material["snapshots"][0]
    if mutation.startswith("missing_"):
        del snapshot[mutation.removeprefix("missing_")]
    elif mutation == "version":
        snapshot["interpretation_version"] = "lp_snapshot_future"
    elif mutation == "projection":
        snapshot["projection_format"] = (
            "historical_flat_snake_case"
            if snapshot["projection_format"] == "learning_commons_wire"
            else "learning_commons_wire"
        )
    elif mutation == "checkpoint":
        snapshot["checkpoint_format"] = (
            "historical_prefix"
            if snapshot["checkpoint_format"] == "journal_bearing"
            else "journal_bearing"
        )
    else:
        snapshot["reader_fingerprints"] = [
            {
                "path": "/absent/historical/reader.py",
                "sha256": "0" * 64,
                "size_bytes": 123,
            }
        ]
    payload = current._dump(material)
    digest = hashlib.sha256(payload).hexdigest()
    destination = frozen.manifest_path.parent.parent / digest
    shutil.copytree(dst=destination, src=frozen.manifest_path.parent)
    destination.chmod(0o755)
    (destination / "manifest.json").chmod(0o600)
    (destination / "manifest.json").write_bytes(payload)
    return FrozenInputs(
        content_hash=digest, manifest_path=destination / "manifest.json"
    )


def _write(*, path: Path, value: Any) -> None:
    """Persist canonical synthetic material with independently computed byte hashes.

    Parameters
    ----------
    path
        Temporary fixture artifact.
    value
        JSON-compatible content.
    """
    path.write_bytes(
        b"".join(current._dump(row) for row in value)
        if path.suffix == ".jsonl"
        else current._dump(value)
    )


@pytest.mark.parametrize(argnames="checkpoint,projection", argvalues=_CELLS)
@pytest.mark.parametrize(
    argnames="name",
    argvalues=[
        "as_lc_kg_bundle.json",
        "as_lc_lp_kg_bundle.json",
        "lp_final_claims.json",
        "lp_relationship_provenance.json",
        "lp_relationships_builds_towards.jsonl",
        "lp_validation_report.json",
    ],
)
def test_all_cells_authoritative_corruption_is_not_hidden_by_projection(
    _sources: dict[str, Path],
    checkpoint: str,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    projection: str,
    tmp_path: Path,
) -> None:
    """A projection cannot legitimize changed upstream, claims, reports or provenance.

    Parameters
    ----------
    _sources
        Immutable complete source evidence.
    checkpoint
        Independent checkpoint contract.
    monkeypatch
        Restoring publication and dispatch guards.
    name
        Authoritative artifact changed while projections remain untouched.
    projection
        Independent projection family.
    tmp_path
        Temporary source and prior evaluation evidence.
    """
    directory = _copy(
        checkpoint=checkpoint, projection=projection, root=tmp_path, sources=_sources
    )
    path = directory / name
    value = _read(path)
    if name in {"as_lc_kg_bundle.json", "as_lc_lp_kg_bundle.json"}:
        value["framework"]["description"] = "Changed authoritative source"
    elif name == "lp_final_claims.json":
        value["claims"][0]["judgment"]["rationale"] = "Changed authoritative rationale"
    elif name == "lp_relationship_provenance.json":
        next(iter(value.values()))["source_framework"]["title"] = "Changed provenance"
    elif name.endswith(".jsonl"):
        value[0]["source_entity_value"] = "00000000-0000-0000-0000-000000000001"
        value[0]["target_entity_value"] = value[0]["source_entity_value"]
    else:
        value["object_counts"]["identifier_collisions"] = 1
    _write(path=path, value=value)
    current._reject(directory=directory, monkeypatch=monkeypatch, root=tmp_path)


@pytest.mark.parametrize(argnames="checkpoint,projection", argvalues=_CELLS)
def test_all_cells_complete_lifecycle_and_cached_resume(  # pylint: disable=too-many-statements
    _sources: dict[str, Path],
    checkpoint: str,
    monkeypatch: pytest.MonkeyPatch,
    projection: str,
    tmp_path: Path,
) -> None:
    """Freeze, execute offline, report and resume every supported interpretation.

    Parameters
    ----------
    _sources
        Independently constructed checkpoint evidence.
    checkpoint
        Selected checkpoint family.
    monkeypatch
        Restoring Git, discovery and provider guards.
    projection
        Selected projection family.
    tmp_path
        Isolated source and evaluator store.
    """
    directory = _copy(
        checkpoint=checkpoint, projection=projection, root=tmp_path, sources=_sources
    )
    source_before = projections._state(directory.parent)
    git_guard = Mock(side_effect=AssertionError("Evaluator queried Git"))
    for name in ("Popen", "check_output", "run"):
        monkeypatch.setattr(name=name, target=subprocess, value=git_guard)
    reference = entry.prepare_evaluation(
        overrides=_OVERRIDES,
        repository_root=tmp_path,
        results_root=directory.parent,
    )
    with judge.open_evaluation_store(reference) as session:
        schedule = session.schedule
        inputs = schedule.inputs
        assert len(schedule.curricula) == 1
        request_ids = {
            request.prompt.request_id for request in schedule.curricula[0].requests
        }
        assert len(request_ids) == schedule.total_requests
    snapshot = sampling.load_frozen_lp_inputs(inputs)[0]
    assert snapshot.projection_format == _FORMATS[projection]
    assert snapshot.interpretation_version == "lp_snapshot_v2"
    assert snapshot.reader_fingerprints == ()
    manifest = _read(inputs.manifest_path)
    bound = manifest["snapshots"][0]
    captured_lp = json.loads(bound["config_json"])["lp"]
    if checkpoint == "historical_prefix":
        assert "max_concurrent_requests" not in captured_lp
        assert set(bound["absent_artifacts"]) >= {
            "lp_generation_pending_completions.json",
            "lp_generation_usage.json",
            "lp_generation_checkpoint_transaction.json",
        }
    else:
        assert captured_lp["max_concurrent_requests"] == 4
    for artifact in bound["artifacts"]:
        digest = artifact["fingerprint"]["sha256"]
        assert (inputs.manifest_path.parent / digest).read_bytes() == Path(
            artifact["fingerprint"]["path"]
        ).read_bytes()
    calls: list[str] = []

    class _Transport:
        """Return deterministic synthetic ambiguity for the complete real schedule."""

        settings = schedule.judge

        async def judge(self, prompt: Any) -> Any:
            """Produce an offline response retaining the exact request identity.

            Parameters
            ----------
            prompt
                Authenticated scheduled prompt.

            Returns
            -------
            Any
                Valid synthetic response and known usage.
            """
            calls.append(prompt.request_id)
            return evaluator._reply(prompt)

        async def preflight(self) -> None:
            """Validate the offline transport without contacting a provider."""

    @asynccontextmanager
    async def _transport(settings: Any) -> Any:
        """Inject only the transport; preserve real material and cache checks.

        Parameters
        ----------
        settings
            Bound judge configuration.

        Yields
        ------
        Any
            Offline synthetic transport.
        """
        assert settings == schedule.judge
        yield _Transport()

    result = asyncio.run(
        entry.run_evaluation(
            provenance=ReportProvenance(evidence_kind="development"),
            reference=reference,
            transport_factory=_transport,
        )
    )
    assert result.execution_complete
    assert set(calls) == request_ids
    assert len(calls) == len(request_ids)
    report = _read(result.artifacts.directory / "lp_eval_report.json")
    interpretation = report["snapshot_interpretations"][0]
    assert interpretation["projection_format"] == _FORMATS[projection]
    assert interpretation["checkpoint_format"] == checkpoint
    assert interpretation["interpretation_version"] == bound["interpretation_version"]
    assert "reader_fingerprints" not in interpretation
    assert bound["reader_fingerprints"] == []
    assert interpretation["recorded_concurrency_capacity"] == (
        None if checkpoint == "historical_prefix" else 4
    )
    assert report["provenance"]["evidence_kind"] == "development"
    if checkpoint == "historical_prefix":
        assert (
            "not recorded"
            in (result.artifacts.directory / "lp_eval_report.md").read_text()
        )
    evidence_before = projections._state(tmp_path / "results")
    evaluator._run_metadata(directory=directory.parent.parent / "new/kgs", identity=987)
    guard = Mock(side_effect=AssertionError("Resume rediscovered or dispatched"))
    monkeypatch.setattr(name="discover_lp_runs", target=entry, value=guard)
    resumed = entry.prepare_evaluation(
        repository_root=tmp_path,
        results_root=directory.parent,
        resume_manifest=reference.manifest_path,
    )
    assert resumed == reference
    again = asyncio.run(
        entry.run_evaluation(
            provenance=ReportProvenance(evidence_kind="development"),
            reference=resumed,
            transport_factory=guard,
        )
    )
    assert again.execution_complete
    assert projections._state(tmp_path / "results") == evidence_before
    assert projections._state(directory.parent) == source_before
    guard.assert_not_called()
    git_guard.assert_not_called()
    original_read = scoring._store_read
    scorer_path = Path(scoring.__file__).resolve()

    def _scorer_change(path: Path) -> bytes:
        """Simulate a report-only implementation edit while preserving all inputs.

        Parameters
        ----------
        path
            Actual material requested by the report writer.

        Returns
        -------
        bytes
            Original material except the isolated scorer fingerprint.
        """
        payload = original_read(path)
        return (
            payload + b"\n# Synthetic scorer revision\n"
            if path == scorer_path
            else payload
        )

    monkeypatch.setattr(name="_store_read", target=scoring, value=_scorer_change)
    rescored = scoring.write_evaluation_reports(
        provenance=ReportProvenance(evidence_kind="development"),
        reference=reference,
    )
    assert rescored.directory == result.artifacts.directory
    rescored_report = _read(rescored.directory / "lp_eval_report.json")
    assert "scorer_sha256" not in rescored_report
    assert rescored_report == report
    assert (
        rescored_report["snapshot_interpretations"]
        == report["snapshot_interpretations"]
    )
    assert (rescored.directory / "lp_eval_judgments.jsonl").read_bytes() == (
        result.artifacts.directory / "lp_eval_judgments.jsonl"
    ).read_bytes()
    assert len(calls) == len(request_ids)
    assert projections._state(directory.parent) == source_before


@pytest.mark.parametrize(argnames="checkpoint,projection", argvalues=_CELLS)
@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "alias_collision",
        "array_order",
        "edge_order",
        "endpoint_key",
        "endpoint_value",
        "extra_row",
        "metadata_type",
        "missing_field",
        "missing_row",
        "nonfinite",
        "null_absence",
        "recursive_conversion",
        "stale_projection_hash",
    ],
)
def test_all_cells_projection_corruption_rejects_before_publication(  # pylint: disable=too-complex,too-many-branches,too-many-statements
    _sources: dict[str, Path],
    attack: str,
    checkpoint: str,
    monkeypatch: pytest.MonkeyPatch,
    projection: str,
    tmp_path: Path,
) -> None:
    """Reject complete-record corruption with source and prior evidence unchanged.

    Parameters
    ----------
    _sources
        Immutable synthetic evidence.
    attack
        Single projection or recorded-byte-binding defect.
    checkpoint
        Selected checkpoint family.
    monkeypatch
        Restoring publication and transport guards.
    projection
        Selected projection family.
    tmp_path
        Disposable evidence root.
    """
    directory = _copy(
        checkpoint=checkpoint, projection=projection, root=tmp_path, sources=_sources
    )
    path = directory / "as_lc_lp_nodes.jsonl"
    nodes = _read(path)
    owner = nodes[0]["properties"] if projection == "wire" else nodes[0]
    if attack == "alias_collision":
        original, alternate = (
            ("case_identifier_uuid", "caseIdentifierUUID")
            if projection == "internal"
            else ("caseIdentifierUUID", "case_identifier_uuid")
        )
        owner[alternate] = owner[original]
    elif attack == "array_order":
        if projection == "wire":
            nodes.reverse()
        else:
            nested = next(row["metadata"] for row in nodes if row.get("metadata"))
            nested["ordered_probe"] = [2, 1]
    elif attack == "edge_order":
        path = directory / "as_lc_lp_relationships.jsonl"
        nodes = _read(path)
        assert len(nodes) > 1
        nodes.reverse()
    elif attack in ("endpoint_key", "endpoint_value"):
        path = directory / "as_lc_lp_relationships.jsonl"
        nodes = _read(path)
        if projection == "wire":
            nodes[0]["source_identifier"] = "wrong"
        else:
            key = (
                "source_entity_key"
                if attack == "endpoint_key"
                else "source_entity_value"
            )
            if projection == "converted":
                key = projections._ALIASES[key]
            nodes[0][key] = (
                "case_identifier_uuid" if projection == "converted" else "wrong"
            )
    elif attack == "extra_row":
        nodes.append(nodes[0])
    elif attack == "metadata_type":
        if projection == "wire":
            owner["isCurrent"] = True
        else:
            owner["metadata"] = {"nested": [True, None, {"count": 1}]}
    elif attack == "missing_field":
        del nodes[0]["identifier"]
    elif attack == "missing_row":
        nodes.pop()
    elif attack == "nonfinite":
        owner["invalid"] = float("nan")
    elif attack == "null_absence":
        if projection == "wire":
            owner["description"] = None
        else:
            key = next(key for key, value in owner.items() if value is None)
            del owner[key]
    elif attack == "recursive_conversion":
        owner["metadata"] = {"caseIdentifierUUID": owner.get("caseIdentifierUUID")}
    else:
        # A producing report may bind projections; exact parity never excuses its hash.
        bundle_path = directory / "as_lc_lp_kg_bundle.json"
        bundle = _read(bundle_path)
        bundle["validation_report"]["artifact_byte_hashes"][path.name] = "0" * 64
        _write(path=bundle_path, value=bundle)
    _write(path=path, value=nodes)
    current._reject(directory=directory, monkeypatch=monkeypatch, root=tmp_path)


@pytest.mark.parametrize(argnames="checkpoint,projection", argvalues=_CELLS)
@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=[
        "frozen_bytes",
        "new_journal",
        "source_bytes",
        "source_format",
        "source_missing",
    ],
)
def test_all_cells_resume_rejects_material_changes_before_dispatch(
    _sources: dict[str, Path],
    checkpoint: str,
    mutation: str,
    projection: str,
    tmp_path: Path,
) -> None:
    """Source, absent-journal and frozen-copy changes cannot reuse a saved schedule.

    Parameters
    ----------
    _sources
        Immutable test evidence.
    checkpoint
        Selected checkpoint family.
    mutation
        Source or frozen-copy material change.
    projection
        Selected projection family.
    tmp_path
        Isolated source and saved invocation.
    """
    directory = _copy(
        checkpoint=checkpoint, projection=projection, root=tmp_path, sources=_sources
    )
    reference = entry.prepare_evaluation(
        overrides=_OVERRIDES, repository_root=tmp_path, results_root=directory
    )
    with judge.open_evaluation_store(reference) as session:
        frozen = session.schedule.inputs
    artifact = next(
        item
        for item in _read(frozen.manifest_path)["snapshots"][0]["artifacts"]
        if item["name"] == "as_lc_lp_nodes.jsonl"
    )
    if mutation == "frozen_bytes":
        path = frozen.manifest_path.parent / artifact["fingerprint"]["sha256"]
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b" ")
        path.chmod(0o444)
    elif mutation == "source_missing":
        (directory / "as_lc_lp_nodes.jsonl").unlink()
    elif mutation == "source_format":
        projections._format(
            directory=directory,
            projection="internal" if projection == "wire" else "wire",
        )
    else:
        path = directory / (
            "lp_generation_usage.json"
            if mutation == "new_journal"
            else "as_lc_lp_nodes.jsonl"
        )
        path.write_bytes(path.read_bytes() + b" " if path.exists() else b"{}\n")
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
                provenance=ReportProvenance(evidence_kind="development"),
                reference=reference,
                transport_factory=guard,
            )
        )
    with pytest.raises(ValueError):
        scoring.write_evaluation_reports(
            provenance=ReportProvenance(evidence_kind="development"),
            reference=reference,
        )
    guard.assert_not_called()
    assert projections._state(tmp_path) == before


@pytest.mark.parametrize(argnames="checkpoint,projection", argvalues=_CELLS)
@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=[
        "checkpoint",
        "missing_checkpoint_format",
        "missing_interpretation_version",
        "missing_projection_format",
        "missing_reader_fingerprints",
        "projection",
        "reader",
        "version",
    ],
)
def test_all_cells_validate_interpretation_and_ignore_reader_source_metadata(
    _sources: dict[str, Path],
    checkpoint: str,
    mutation: str,
    projection: str,
    tmp_path: Path,
) -> None:
    """Reject byte-authenticated manifests with missing or mismatched interpretation.

    Parameters
    ----------
    _sources
        Immutable test evidence.
    checkpoint
        Selected checkpoint family.
    mutation
        Deliberately changed interpretation descriptor.
    projection
        Selected projection family.
    tmp_path
        Isolated frozen and source evidence.
    """
    directory = _copy(
        checkpoint=checkpoint, projection=projection, root=tmp_path, sources=_sources
    )
    frozen = sampling.freeze_lp_inputs(
        inventory=sampling.discover_lp_runs(
            evaluation_root=tmp_path / "results/lp_evals", results_root=directory
        ),
        repository_root=tmp_path,
    )
    changed = _rewrite_manifest(frozen=frozen, mutation=mutation)
    before = projections._state(tmp_path)
    if mutation == "reader":
        loaded = sampling.load_frozen_lp_inputs(changed)
        assert loaded[0].reader_fingerprints[0].sha256 == "0" * 64
    else:
        with pytest.raises(sampling.LPSnapshotError):
            sampling.load_frozen_lp_inputs(changed)
    assert projections._state(tmp_path) == before
    assert sampling.load_frozen_lp_inputs(frozen)


@pytest.mark.parametrize(argnames="projection", argvalues=["wire"])
def test_empty_relationship_population_uses_nonempty_node_family(
    _sources: dict[str, Path],
    projection: str,
    tmp_path: Path,
) -> None:
    """Classify empty edge projections at the reader boundary using nonempty nodes.

    Parameters
    ----------
    _sources
        Independently produced node records.
    projection
        Independently constructed projection family.
    tmp_path
        Temporary projection-only fixture.
    """
    # Package Library
    from kgfeg.evals.lp_eval.compatibility import read_snapshot_projections
    from kgfeg.kgs.schemas import AcademicStandardsLCLPKGBundle
    from tests.fixtures.lp.delivery import projection_bytes

    directory = _copy(
        checkpoint="journal_bearing",
        projection=projection,
        root=tmp_path,
        sources=_sources,
    )
    path = directory / "as_lc_lp_kg_bundle.json"
    material = _read(path)
    for group in ("has_child", "supports", "builds_towards", "relates_to"):
        material["relationships_" + group] = []
    material["summary"]["total_relationship_count"] = 0
    _write(path=path, value=material)
    projections._format(directory=directory, projection=projection)
    assert (directory / "as_lc_lp_relationships.jsonl").read_bytes() == b""
    wire = projection_bytes(
        grade_mapping=_read(directory / "kg_run.json")["extra"]["as"][
            "grade_level_mapping"
        ],
        material=material,
    )
    nodes = _read(directory / "as_lc_lp_nodes.jsonl")
    assert nodes
    assert (
        read_snapshot_projections(
            bundle=AcademicStandardsLCLPKGBundle.model_validate(material),
            edges=[],
            nodes=nodes,
            wire_records=(
                [
                    json.loads(line)
                    for line in wire["as_lc_lp_nodes.jsonl"].splitlines()
                ],
                [],
            ),
        )
        == _FORMATS[projection]
    )


def test_more_than_six_current_snapshots_and_capacities_remain_framework_isolated(
    tmp_path: Path,
) -> None:
    """Discover seven current curricula with independent captured capacities.

    Parameters
    ----------
    tmp_path
        Isolated seven-curriculum invocation.
    """
    root = tmp_path / "inputs"
    expected: dict[str | None, tuple[str, str, int | None]] = {}

    for index in range(7):
        capacity = (1, 4)[index % 2]
        directory = build_snapshot(
            capacity=capacity,
            count=2,
            identity=88100 + index,
            root=root,
            title=f"Unfamiliar Ω curriculum {index}",
        )
        projections._format(directory=directory, projection="wire")
        expected[f"synthetic-evaluator-{88100 + index}"] = (
            "learning_commons_wire",
            "journal_bearing",
            capacity,
        )
    before = projections._state(root)
    reference = entry.prepare_evaluation(
        overrides=_OVERRIDES, repository_root=tmp_path, results_root=root
    )
    with judge.open_evaluation_store(reference) as session:
        schedule = session.schedule
        assert len(schedule.curricula) == 7
        snapshots = sampling.load_frozen_lp_inputs(schedule.inputs)
        assert len(snapshots) == 7
        assert {
            snapshot.run.doc_key: (
                snapshot.projection_format,
                snapshot.checkpoint_format,
                json.loads(snapshot.config_json)["lp"].get("max_concurrent_requests"),
            )
            for snapshot in snapshots
        } == expected
        for curriculum in schedule.curricula:
            snapshot = next(
                item for item in snapshots if item.run.doc_key == curriculum.doc_key
            )
            assert curriculum.framework_uuid == snapshot.run.framework_uuid
            bundle = json.loads(
                next(
                    item.payload
                    for item in snapshot.artifacts
                    if item.name == "as_lc_lp_kg_bundle.json"
                )
            )
            endpoints = {UUID(item["case_identifier_uuid"]) for item in bundle["items"]}
            assert all(
                set(request.canonical_endpoint_uuids) <= endpoints
                for request in curriculum.requests
                if request.component != "controls"
            )
        assert (
            sampling.prepare_evaluation_schedule(
                inputs=schedule.inputs, judge=schedule.judge, settings=schedule.settings
            )
            == schedule
        )
    assert projections._state(root) == before


@pytest.mark.parametrize(argnames="checkpoint,projection", argvalues=_CELLS)
@pytest.mark.parametrize(argnames="mutation", argvalues=["whitespace"])
def test_projection_bytes_qualify_snapshot_and_request_cache_identity(
    _sources: dict[str, Path],
    checkpoint: str,
    mutation: str,
    projection: str,
    tmp_path: Path,
) -> None:
    """Different raw inputs cannot reuse a cache qualified by another schedule.

    Parameters
    ----------
    _sources
        Immutable current evidence.
    checkpoint
        Independently selected original checkpoint format.
    mutation
        Valid whitespace change preserving the current projection format.
    projection
        Independently selected format.
    tmp_path
        Isolated invocation stores.
    """
    directory = _copy(
        checkpoint=checkpoint,
        projection=projection,
        root=tmp_path,
        sources=_sources,
    )
    first = entry.prepare_evaluation(
        overrides=_OVERRIDES, repository_root=tmp_path, results_root=directory
    )
    with judge.open_evaluation_store(first) as session:
        old = session.schedule
        request = next(r for r in old.curricula[0].requests if r.role == "base")
        attempt = session.start_attempt(request.prompt.request_id)
        reply = evaluator._reply(request.prompt)
        session.record_success(
            attempt=attempt, response_json=reply.response_json, usage=reply.usage
        )
        assert len(session.snapshot().judgments) == 1
    path = directory / "as_lc_lp_nodes.jsonl"
    assert mutation == "whitespace"
    path.write_bytes(path.read_bytes().replace(b"{", b"{ ", 1))
    second = entry.prepare_evaluation(
        new_invocation=True,
        overrides=_OVERRIDES,
        repository_root=tmp_path,
        results_root=directory,
    )
    with judge.open_evaluation_store(second) as session:
        new = session.schedule
        assert not session.snapshot().judgments
        assert not session.snapshot().events
    assert old.inputs.content_hash != new.inputs.content_hash
    assert old.material_content_hash != new.material_content_hash
    assert first.manifest_path.parent != second.manifest_path.parent
    # Request-local IDs may stay equal; the authenticated schedule qualifies them.
    shutil.copyfile(
        dst=second.manifest_path.parent / "attempts.sqlite3",
        src=first.manifest_path.parent / "attempts.sqlite3",
    )
    before = projections._state(tmp_path)
    with pytest.raises(
        ValueError, match="Invocation manifest differs from its ledger binding"
    ):
        with judge.open_evaluation_store(second):
            pytest.fail("A cache from different raw inputs became available")
    assert projections._state(tmp_path) == before


@pytest.mark.parametrize(argnames="checkpoint,projection", argvalues=_CELLS)
def test_source_change_during_freeze_cannot_publish_partial_inputs(
    _sources: dict[str, Path],
    checkpoint: str,
    monkeypatch: pytest.MonkeyPatch,
    projection: str,
    tmp_path: Path,
) -> None:
    """Mutation after validation is detected before frozen evidence publication.

    Parameters
    ----------
    _sources
        Complete reduced evidence.
    checkpoint
        Original checkpoint family.
    monkeypatch
        Inject source mutation after the real validation returns.
    projection
        Original projection family.
    tmp_path
        Test-owned evidence root.
    """
    directory = _copy(
        checkpoint=checkpoint, projection=projection, root=tmp_path, sources=_sources
    )
    inventory = sampling.discover_lp_runs(
        evaluation_root=tmp_path / "results/lp_evals", results_root=directory
    )
    original = sampling.validate_lp_snapshot
    changed = None

    def _changing(run: Any) -> Any:
        """Change test-owned source bytes only after real contract validation.

        Parameters
        ----------
        run
            Discovered input being validated.

        Returns
        -------
        Any
            Previously valid snapshot now invalidated by source mutation.
        """
        nonlocal changed
        snapshot = original(run)
        path = directory / "as_lc_lp_nodes.jsonl"
        path.write_bytes(path.read_bytes().replace(b"{", b"{ ", 1))
        changed = projections._state(directory.parent)
        return snapshot

    monkeypatch.setattr(name="validate_lp_snapshot", target=sampling, value=_changing)
    with pytest.raises(sampling.LPSnapshotError):
        sampling.freeze_lp_inputs(inventory=inventory, repository_root=tmp_path)
    assert projections._state(directory.parent) == changed
    assert not (tmp_path / "results/lp_evals").exists()
