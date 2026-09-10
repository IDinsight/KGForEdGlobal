"""Verify additive graph compilation against independently inspected offline evidence."""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib
import json
import socket

from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

from pydantic import ValidationError

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import lp_export
from kgfeg.kgs.lp_artifacts import LPStandaloneArtifacts
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.schemas import AcademicStandardsLCLPKGBundle
from kgfeg.kgs.utils import KGDirs
from kgfeg.page_ir_extraction.validators import QualityError
from tests.kgfeg.kgs import test_lp_artifacts as _artifacts
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _storage

_BUNDLE = "as_lc_lp_kg_bundle.json"
_DOC_KEY = "synthetic-selection-document"
_LP_KEYS = ("relationships_builds_towards", "relationships_relates_to")
_PROFILES = (
    "ghana_english",
    "ghana_math",
    "madhi_math",
    "nigeria_math",
    "pratham_science",
    "rwanda_math",
)
_REJECTIONS = (ValueError, QualityError, LPGenerationFailed, OSError)
_STAGES = (
    "lp_generation_draft_responses.jsonl",
    "lp_generation_validation_verdicts.jsonl",
    "lp_generation_responses.jsonl",
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject external transport and use a synthetic model execution identity.

    Parameters
    ----------
    monkeypatch
        Restoring socket and settings substitutions.
    """
    guard = Mock(side_effect=AssertionError("Combined bundle tests must stay offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _bytes(value: Any) -> bytes:
    """Encode the independent canonical JSON oracle.

    Parameters
    ----------
    value
        JSON-compatible payload.

    Returns
    -------
    bytes
        Compact sorted Unicode JSON terminated by a newline.
    """
    return (
        json.dumps(ensure_ascii=False, obj=value, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _compile(
    *, harness: _claims._Harness, overwrite: bool = False
) -> AcademicStandardsLCLPKGBundle:
    """Exercise the public compiler with current upstream and persisted evidence.

    Parameters
    ----------
    harness
        Current bundle, configuration, and temporary evidence root.
    overwrite
        Explicitly replace a previously saved bundle after validating its inputs.

    Returns
    -------
    AcademicStandardsLCLPKGBundle
        Read-back verified combined graph.
    """
    return lp_export.compile_as_lc_lp_kg(
        as_lc_bundle=harness.bundle,
        doc_key=_DOC_KEY,
        kg_config=harness.config,
        kg_dirs=KGDirs(root=harness.root),
        overwrite=overwrite,
    )


def _hash(value: Any) -> str:
    """Hash actual content independently of production helpers.

    Parameters
    ----------
    value
        JSON-compatible payload.

    Returns
    -------
    str
        Canonical material SHA-256 digest.
    """
    return hashlib.sha256(_bytes(value)).hexdigest()


def _persist(
    *, harness: _claims._Harness, monkeypatch: pytest.MonkeyPatch
) -> LPStandaloneArtifacts:
    """Complete real checkpoints and artifacts using scripted model proposals.

    Parameters
    ----------
    harness
        Independent endpoint-keyed proposal script and current upstream authority.
    monkeypatch
        Restoring external model-call substitution.

    Returns
    -------
    LPStandaloneArtifacts
        Authenticated standalone records from their public writer.
    """
    relationships = _artifacts._complete(harness=harness, monkeypatch=monkeypatch)
    return _artifacts._write(harness=harness, relationships=relationships)


def _projection_bytes(material: dict[str, Any]) -> dict[str, bytes]:
    """Derive complete ordered internal rows independently from authoritative material.

    Parameters
    ----------
    material
        Upstream node and complete combined relationship payloads.

    Returns
    -------
    dict[str, bytes]
        Exact two-file projection oracle, including nulls and nested metadata.
    """
    nodes = [{**material["framework"], "entity_type": "StandardsFramework"}]
    for group, entity, identifier in (
        ("items", "StandardsFrameworkItem", "case_identifier_uuid"),
        ("learning_components", "LearningComponent", "identifier"),
    ):
        indexed = {row[identifier]: row for row in material[group]}
        assert len(indexed) == len(material[group])
        nodes.extend({**indexed[key], "entity_type": entity} for key in sorted(indexed))
    relationships: list[dict[str, Any]] = []
    for group in (
        "relationships_has_child",
        "relationships_supports",
        "relationships_builds_towards",
        "relationships_relates_to",
    ):
        indexed = {row["identifier"]: row for row in material[group]}
        assert len(indexed) == len(material[group])
        relationships.extend(indexed[key] for key in sorted(indexed))
    return {
        "as_lc_lp_nodes.jsonl": b"".join(_bytes(row) for row in nodes),
        "as_lc_lp_relationships.jsonl": b"".join(_bytes(row) for row in relationships),
    }


def _snapshot(root: Path) -> dict[str, bytes]:
    """Capture actual immediate files without using stored hash claims.

    Parameters
    ----------
    root
        Isolated evidence directory.

    Returns
    -------
    dict[str, bytes]
        Filename-to-bytes mapping.
    """
    return {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}


@pytest.mark.parametrize(argnames="decision", argvalues=["needs_review", "no_relation"])
def test_all_nonpublishing_judgments_remain_successful_without_edges(
    decision: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep complete negative or ambiguous populations distinct from processing failure.

    Parameters
    ----------
    decision
        Uniform valid nonpublishing judgment for every candidate.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.default_decision = decision
    _persist(harness=harness, monkeypatch=monkeypatch)
    result = _compile(harness=harness)
    counts = result.summary.learning_progressions["object_counts"]
    assert counts["candidate_pairs"] == counts["final_claims"] == 6
    assert counts[f"{decision}_claims"] == 6
    assert counts["accepted_claims"] == counts["unresolved_failed_pairs"] == 0
    assert result.validation_report.passed
    assert result.relationships_builds_towards == result.relationships_relates_to == []
    assert result.unresolved_items.learning_progressions["total_needs_review"] == (
        6 if decision == "needs_review" else 0
    )


@pytest.mark.parametrize(argnames="profile", argvalues=_PROFILES)
def test_complete_upstream_content_and_standalone_audit_shapes_survive(  # pylint: disable=R0915
    monkeypatch: pytest.MonkeyPatch, profile: str, tmp_path: Path
) -> None:
    """Preserve every upstream field and add exact authenticated LP audit payloads.

    Parameters
    ----------
    monkeypatch
        Offline producer/checker seam.
    profile
        Reduced curriculum with its distinctive hierarchy and evidence shapes.
    tmp_path
        Isolated artifact directory.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(batch=20, profile=profile)
    harness.default_decision = "relatesTo"
    sentinel: dict[str, Any] = {
        "nested": [None, False, 0, "é 漢字", {"empty": [], "map": {}}]
    }
    harness.bundle.entity_provenance.update(
        custom_audit=deepcopy(sentinel),
        custom_list=[deepcopy(sentinel), "retained"],
        custom_null=None,
    )
    harness.bundle.summary.academic_standards.finalization_exclusion_summary = {
        "synthetic historical exclusion": 7
    }
    harness.bundle.summary.learning_components.manual_review_overrides = deepcopy(
        sentinel
    )
    harness.bundle.summary.learning_components.warnings = ["Historical LC warning"]
    harness.bundle.unresolved_items.academic_standards.relationship_unresolved_edges.append(
        {"historical_audit": deepcopy(sentinel)}
    )
    harness.bundle.unresolved_items.learning_components.lc_source_exclusion_reason_counts = {
        "synthetic historical exclusion": 3
    }
    harness.bundle.validation_report.input_fingerprints["custom_audit"] = "preserve me"
    upstream = harness.bundle.model_dump(mode="json")
    config = harness.config.model_dump(by_alias=True, mode="json")
    standalone = _persist(harness=harness, monkeypatch=monkeypatch)
    # These existing consumer files are opaque compiler inputs and must remain intact.
    for name in (
        "as_kg_bundle.json",
        "as_nodes.jsonl",
        "as_relationships.jsonl",
        "as_lc_kg_bundle.json",
        "as_lc_nodes.jsonl",
        "as_lc_relationships.jsonl",
    ):
        (tmp_path / name).write_bytes(b"upstream consumer artifact\n")
    before = _snapshot(tmp_path)
    calls = list(harness.calls)
    result = _compile(harness=harness)
    material = result.model_dump(mode="json")
    assert harness.calls == calls
    assert harness.bundle.model_dump(mode="json") == upstream
    assert harness.config.model_dump(by_alias=True, mode="json") == config
    for name in (
        "framework",
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
    ):
        assert material[name] == upstream[name]
    assert material["summary"]["as_lc_summary"] == upstream["summary"]
    for name in ("academic_standards", "learning_components"):
        assert material["summary"][name] == upstream["summary"][name]
        assert material["unresolved_items"][name] == upstream["unresolved_items"][name]
    for name, value in upstream["entity_provenance"].items():
        assert material["entity_provenance"][name] == value
    assert set(material["entity_provenance"]) == set(
        upstream["entity_provenance"]
    ) | set(_LP_KEYS)
    assert (
        material["validation_report"]["as_lc_validation_report"]
        == upstream["validation_report"]
    )
    assert material["summary"]["learning_progressions"] == json.loads(
        before["lp_generation_summary.json"]
    )
    assert material["unresolved_items"]["learning_progressions"] == json.loads(
        before["lp_unresolved_items.json"]
    )
    assert material["validation_report"]["lp_validation_report"] == json.loads(
        before["lp_validation_report.json"]
    )
    provenance = json.loads(before["lp_relationship_provenance.json"])
    for name, filename in zip(
        _LP_KEYS, (_artifacts._BUILDS, _artifacts._RELATES), strict=True
    ):
        rows = [json.loads(line) for line in before[filename].splitlines()]
        assert material[name] == rows
        assert material["entity_provenance"][name] == {
            row["identifier"]: provenance[row["identifier"]] for row in rows
        }
        for row in rows:
            assert row["metadata"] == provenance[row["identifier"]]
    counts = material["validation_report"]["object_counts"]
    assert counts == {
        "frameworks": 1,
        "standards_framework_items": len(upstream["items"]),
        "learning_components": len(upstream["learning_components"]),
        **{
            name: len(material[name])
            for name in ("relationships_has_child", "relationships_supports", *_LP_KEYS)
        },
        "total_node_count": 1
        + len(upstream["items"])
        + len(upstream["learning_components"]),
        "total_relationship_count": sum(
            len(material[name])
            for name in ("relationships_has_child", "relationships_supports", *_LP_KEYS)
        ),
    }
    assert material["summary"]["total_node_count"] == counts["total_node_count"]
    assert (
        material["summary"]["total_relationship_count"]
        == counts["total_relationship_count"]
    )
    if profile == "pratham_science":
        parents = Counter(
            row["target_entity_value"] for row in material["relationships_has_child"]
        )
        assert max(parents.values()) >= 2
    assert material["learning_components"] and material["relationships_supports"]
    assert standalone.validation_report.passed
    assert _snapshot(tmp_path) == {
        **before,
        _BUNDLE: _bytes(material),
        **_projection_bytes(material),
    }
    assert AcademicStandardsLCLPKGBundle.model_validate_json(_bytes(material)) == result
    result.entity_provenance["custom_audit"]["nested"].append("caller mutation")
    assert harness.bundle.model_dump(mode="json") == upstream
    assert (tmp_path / _BUNDLE).read_bytes() == _bytes(material)


@pytest.mark.parametrize(
    argnames="attack", argvalues=["duplicate", "gap", "reorder", "stale_id", "truncate"]
)
@pytest.mark.parametrize(argnames="name", argvalues=_STAGES)
def test_corrupt_checkpoint_authority_blocks_compilation(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Reject invalid checkpoint populations despite rehashed local receipts.

    Parameters
    ----------
    attack
        Prefix or request-identity corruption.
    monkeypatch
        Offline model-call seam.
    name
        Producer, checker, or reconciled checkpoint artifact.
    tmp_path
        Isolated evidence directory.
    """
    harness = _artifacts._mixed(tmp_path)
    harness.config.learning_progressions.request_batch_size = 1
    _persist(harness=harness, monkeypatch=monkeypatch)
    path = tmp_path / name
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    assert len(rows) == 6
    if attack == "duplicate":
        rows.insert(1, deepcopy(rows[0]))
    elif attack == "gap":
        rows.pop(1)
    elif attack == "reorder":
        rows[0], rows[1] = rows[1], rows[0]
    elif attack == "stale_id":
        rows[0]["payload"]["request_id"] = "stale-request"
    path.write_bytes(b"".join(_bytes(row) for row in rows))
    _storage._reseal(name=name, root=tmp_path)
    if attack == "truncate":
        path.write_bytes(path.read_bytes()[:-9])
    before = _snapshot(tmp_path)
    with pytest.raises(_REJECTIONS):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before
    assert _BUNDLE not in before


@pytest.mark.parametrize(argnames="count", argvalues=[0, 1])
def test_empty_candidate_and_relationship_populations_compile_without_calls(
    count: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep zero populations explicit and require completed empty audit authority.

    Parameters
    ----------
    count
        Zero or one eligible SFI, insufficient to form a candidate pair.
    monkeypatch
        Offline model seam which must remain unused.
    tmp_path
        Isolated evidence root.
    """
    harness = _claims._Harness(count=count, root=tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    result = _compile(harness=harness)
    assert harness.calls == []
    assert result.validation_report.passed
    assert result.summary.total_node_count == 1 + count
    assert result.summary.total_relationship_count == count
    assert result.relationships_builds_towards == result.relationships_relates_to == []
    assert result.summary.learning_progressions["object_counts"]["candidate_pairs"] == 0
    assert result.unresolved_items.learning_progressions["claims"] == []
    assert result.entity_provenance["relationships_builds_towards"] == {}
    assert result.entity_provenance["relationships_relates_to"] == {}


@pytest.mark.parametrize(
    argnames="category",
    argvalues=["framework", "has_child", "learning_component", "sfi", "supports"],
)
def test_failed_lp_collision_diagnostics_cannot_compile_success(
    category: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep invalid LP diagnostics inspectable while rejecting combined release.

    Parameters
    ----------
    category
        Upstream identifier category collided with by an LP row.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence root.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture("ghana_math")
    harness.config = _fixtures._config(batch=20, profile="ghana_math")
    harness.default_decision = "relatesTo"
    relationships = _artifacts._complete(harness=harness, monkeypatch=monkeypatch)
    identifier = {
        "framework": harness.bundle.framework.case_identifier_uuid,
        "has_child": harness.bundle.relationships_has_child[0].identifier,
        "learning_component": harness.bundle.learning_components[0].identifier,
        "sfi": harness.bundle.items[0].case_identifier_uuid,
        "supports": harness.bundle.relationships_supports[0].identifier,
    }[category]
    rows = list(relationships.relationships_relates_to)
    rows[0] = rows[0].model_copy(deep=True, update={"identifier": identifier})
    relationships = relationships.model_copy(
        deep=True, update={"relationships_relates_to": tuple(rows)}
    )
    artifacts = _artifacts._write(harness=harness, relationships=relationships)
    assert artifacts.validation_report.passed is False
    before = _snapshot(tmp_path)
    with pytest.raises(
        expected_exception=ValueError, match="standalone LP validation failed"
    ):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before


def test_hashes_bind_actual_graph_inputs_and_every_evidence_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reconcile all graph and evidence hashes from bytes and decoded material.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    before = _snapshot(tmp_path)
    result = _compile(harness=harness)
    material = result.model_dump(mode="json")
    report = material.pop("validation_report")
    summary = json.loads(before["lp_generation_summary.json"])
    assert report["input_content_hashes"] == {
        **summary["input_content_hashes"],
        "combined_graph": _hash(material),
        "lp_generation_summary": _hash(summary),
        "lp_unresolved_items": _hash(json.loads(before["lp_unresolved_items.json"])),
        "lp_validation_report": _hash(json.loads(before["lp_validation_report.json"])),
    }
    expected_names = {name for name in before if name.endswith((".json", ".jsonl"))}
    assert set(report["artifact_byte_hashes"]) == expected_names
    for name in expected_names:
        assert (
            report["artifact_byte_hashes"][name]
            == hashlib.sha256(before[name]).hexdigest()
        )
    assert report["input_content_hashes"]["as_lc_bundle"] == _hash(
        harness.bundle.model_dump(mode="json")
    )
    assert report["input_content_hashes"]["effective_config"] == _hash(
        harness.config.model_dump(by_alias=True, mode="json")
    )
    receipt = json.loads(before["lp_generation_checkpoint_manifest.json"])
    assert receipt["material"]["model_config"]
    claims = json.loads(before["lp_final_claims.json"])["claims"]
    for claim in claims:
        assert claim["provenance"]["producer_prompt_content_hash"]
        assert claim["provenance"]["checker_prompt_content_hash"]
    assert (tmp_path / _BUNDLE).read_bytes() == _bytes(result.model_dump(mode="json"))


def test_lock_contention_cannot_write_combined_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Respect the generation lock without damaging evidence or existing outputs.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    before = _snapshot(tmp_path)
    with (tmp_path / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            _compile(harness=harness)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="name", argvalues=_artifacts._INPUTS)
def test_missing_generation_evidence_cannot_be_reinitialized_by_compilation(
    monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Require existing mandatory evidence while retaining optional eligibility behavior.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    name
        Generation or eligibility artifact removed after standalone writing.
    tmp_path
        Isolated evidence directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    (tmp_path / name).unlink()
    before = _snapshot(tmp_path)
    with pytest.raises(_REJECTIONS):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    argnames="attack", argvalues=["malformed", "missing", "noncanonical"]
)
@pytest.mark.parametrize(argnames="name", argvalues=_artifacts._FILES)
def test_missing_malformed_and_noncanonical_standalone_artifacts_fail_closed(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Authenticate every standalone JSON shape and its exact serialized population.

    Parameters
    ----------
    attack
        File absence, invalid syntax, or byte-level serialization change.
    monkeypatch
        Offline model seam.
    name
        Required standalone artifact under attack.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    path = tmp_path / name
    if attack == "missing":
        path.unlink()
    elif attack == "malformed":
        path.write_bytes(b'{"invalid":')
    else:
        path.write_bytes(b" " + path.read_bytes())
    before = _snapshot(tmp_path)
    with pytest.raises(_REJECTIONS):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before


def test_mixed_outcomes_remain_direct_visible_nonblocking_and_nonsemantic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Publish only direct accepted edges and preserve ambiguity without a semantic gate.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    result = _compile(harness=harness)
    counts = result.summary.learning_progressions["object_counts"]
    assert counts["accepted_claims"] == 4
    assert counts["needs_review_claims"] == counts["no_relation_claims"] == 1
    assert counts["unresolved_failed_pairs"] == 0
    assert len(result.relationships_builds_towards) == 3
    assert len(result.relationships_relates_to) == 1
    assert result.summary.total_relationship_count == 8
    persisted = json.loads((tmp_path / _BUNDLE).read_bytes())
    standalone_provenance = json.loads(
        (tmp_path / "lp_relationship_provenance.json").read_bytes()
    )
    for name, filename in zip(
        _LP_KEYS, (_artifacts._BUILDS, _artifacts._RELATES), strict=True
    ):
        expected_rows = [
            json.loads(line) for line in (tmp_path / filename).read_bytes().splitlines()
        ]
        assert expected_rows
        expected_provenance = {
            row["identifier"]: standalone_provenance[row["identifier"]]
            for row in expected_rows
        }
        assert [row.model_dump(mode="json") for row in getattr(result, name)] == (
            expected_rows
        )
        assert result.entity_provenance[name] == expected_provenance
        assert persisted[name] == expected_rows
        assert persisted["entity_provenance"][name] == expected_provenance
        for row in expected_rows:
            assert row["metadata"] == expected_provenance[row["identifier"]]
    unresolved = result.unresolved_items.learning_progressions
    assert unresolved["total_needs_review"] == len(unresolved["claims"]) == 1
    assert unresolved["claims"][0]["judgment"]["decision"] == "needs_review"
    assert result.validation_report.passed
    assert result.validation_report.semantic_validation_performed is False
    assert result.validation_report.pedagogical_correctness_established is False
    assert result.validation_report.semantic_scope_notice == (
        "Validation covers structural and process integrity only; it does not establish pedagogical correctness."
    )
    for field in (
        "pedagogical_correctness_established",
        "semantic_validation_performed",
    ):
        material = result.model_dump(mode="json")
        material["validation_report"][field] = True
        with pytest.raises(ValidationError):
            AcademicStandardsLCLPKGBundle.model_validate(material)
    material = result.model_dump(mode="json")
    material["validation_report"]["errors"] = ["Combined integrity failure"]
    with pytest.raises(expected_exception=ValidationError, match="contradicts"):
        AcademicStandardsLCLPKGBundle.model_validate(material)


@pytest.mark.parametrize(
    argnames="name", argvalues=["lp_eligible_sfis.json", "lp_eligibility_report.json"]
)
def test_optional_eligibility_appearance_after_authentication_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Bind absent optional inputs as absent rather than accepting late evidence.

    Parameters
    ----------
    monkeypatch
        Restoring wrapper around the real authenticated reader.
    name
        Initially absent eligibility artifact introduced before writing.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    relationships = _artifacts._complete(harness=harness, monkeypatch=monkeypatch)
    (tmp_path / name).unlink()
    _artifacts._write(harness=harness, relationships=relationships)
    _compile(harness=harness)
    before = (tmp_path / _BUNDLE).read_bytes()
    reader = lp_export.read_lp_artifacts

    def _read(**kwargs: Any) -> LPStandaloneArtifacts:
        """Authenticate absence before introducing an optional input.

        Parameters
        ----------
        kwargs
            Current-material reader arguments.

        Returns
        -------
        LPStandaloneArtifacts
            Original authenticated snapshot.
        """
        result = reader(**kwargs)
        (tmp_path / name).write_bytes(b"{}\n")
        return result

    monkeypatch.setattr(name="read_lp_artifacts", target=lp_export, value=_read)
    with pytest.raises(
        expected_exception=ValueError, match="input appeared during bundle compilation"
    ):
        _compile(harness=harness)
    assert (tmp_path / _BUNDLE).read_bytes() == before


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_processing_failure_blocks_until_real_recovery_and_retains_history(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Keep failed execution distinct from later successful ambiguous adjudication.

    Parameters
    ----------
    monkeypatch
        Offline model seam with an injected transport timeout.
    stage
        Producer or checker stage whose request fails.
    tmp_path
        Isolated evidence root.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    harness.default_decision = "needs_review"
    harness.failure = (stage, 0)
    _claims._install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(LPGenerationFailed):
        harness._run()
    before = _snapshot(tmp_path)
    with pytest.raises(_REJECTIONS):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before
    harness.failure = None
    _persist(harness=harness, monkeypatch=monkeypatch)
    failure_bytes = (tmp_path / "lp_generation_failures.json").read_bytes()
    result = _compile(harness=harness)
    counts = result.summary.learning_progressions["object_counts"]
    assert (
        counts["generation_failure_attempts"]
        == counts["resolved_failure_attempts"]
        == 1
    )
    assert counts["unresolved_failed_pairs"] == 0
    assert result.unresolved_items.learning_progressions["total_needs_review"] == 1
    assert result.validation_report.passed
    assert (tmp_path / "lp_generation_failures.json").read_bytes() == failure_bytes
    failure = json.loads(failure_bytes)[0]
    assert failure["resolved_run_number"] is not None
    assert failure["resolved_response_content_hash"]


@pytest.mark.parametrize(argnames="name", argvalues=_LP_KEYS)
@pytest.mark.parametrize(
    argnames="value",
    argvalues=[None, {}, {"retained": ["arbitrary upstream provenance"]}],
)
def test_provenance_namespace_collisions_reject_even_empty_or_null_values(
    monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path, value: Any
) -> None:
    """Reject overwriting any existing upstream LP provenance namespace.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    name
        Reserved additive relationship-provenance key.
    tmp_path
        Isolated evidence directory.
    value
        Existing upstream JSON payload, including otherwise easy-to-ignore values.
    """
    harness = _artifacts._mixed(tmp_path)
    harness.bundle.entity_provenance[name] = deepcopy(value)
    _persist(harness=harness, monkeypatch=monkeypatch)
    before = _snapshot(tmp_path)
    with pytest.raises(
        expected_exception=ValueError, match="upstream provenance already contains"
    ):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before
    assert harness.bundle.entity_provenance[name] == value


@pytest.mark.parametrize(
    argnames="attack", argvalues=["counts", "provenance", "unresolved", "validation"]
)
def test_rehashed_standalone_audit_edits_are_not_authenticated(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject self-consistent audit forgeries using actual checkpoint authority.

    Parameters
    ----------
    attack
        Audit payload edited along with its local digest and companion hashes.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    name = {
        "counts": "lp_generation_summary.json",
        "provenance": "lp_relationship_provenance.json",
        "unresolved": "lp_unresolved_items.json",
        "validation": "lp_validation_report.json",
    }[attack]
    path = tmp_path / name
    material = json.loads(path.read_bytes())
    if attack == "counts":
        material["object_counts"]["candidate_pairs"] += 1
    elif attack == "provenance":
        material[next(iter(material))]["claim"]["judgment"][
            "rationale"
        ] = "Forged but locally well-formed rationale."
    elif attack == "unresolved":
        material["claims"] = []
        material["total_needs_review"] = 0
    else:
        material["warnings"].append("Forged structural validation warning")
    if "content_hash" in material:
        material["content_hash"] = _hash(
            {key: value for key, value in material.items() if key != "content_hash"}
        )
    path.write_bytes(_bytes(material))
    _artifacts._reseal(tmp_path)
    before = _snapshot(tmp_path)
    with pytest.raises(_REJECTIONS):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before


def test_repeated_compilation_has_identical_bytes_without_rewriting_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Make deterministic fresh compilation independent of dictionary insertion order.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    first = _compile(harness=harness)
    before = _snapshot(tmp_path)
    harness.bundle.entity_provenance = dict(
        reversed(list(harness.bundle.entity_provenance.items()))
    )
    second = _compile(harness=harness)
    assert first == second
    assert _snapshot(tmp_path) == before
    for name, expected in _projection_bytes(first.model_dump(mode="json")).items():
        assert before[name] == expected


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=["config", "doc_key", "model", "upstream", "unfinished_transaction"],
)
def test_stale_material_and_unfinished_generation_cannot_compile(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Authenticate current material instead of trusting a previously passed LP report.

    Parameters
    ----------
    attack
        Current material or generation state changed after standalone validation.
    monkeypatch
        Offline model seam and current model setting.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    if attack == "config":
        harness.config.learning_progressions.request_batch_size += 1
    elif attack == "doc_key":
        harness.bundle.entity_provenance["framework"]["doc_key"] = "changed-document"
    elif attack == "model":
        monkeypatch.setattr(
            name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.1"
        )
    elif attack == "upstream":
        harness.bundle.entity_provenance["custom_stale_material"] = {"new": True}
    else:
        (tmp_path / "lp_generation_checkpoint_transaction.json").write_bytes(b"{}\n")
    before = _snapshot(tmp_path)
    with pytest.raises(_REJECTIONS):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    argnames="location",
    argvalues=[
        "as_frameworks",
        "as_has_child",
        "as_sfis",
        "lc_nodes",
        "lc_supports",
        "total_nodes",
        "total_relationships",
    ],
)
def test_upstream_counts_are_checked_against_actual_populations(
    location: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject schema-valid upstream summaries that overstate actual graph populations.

    Parameters
    ----------
    location
        Upstream count to corrupt before any downstream artifacts are generated.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    summary = harness.bundle.summary
    owner, field = {
        "as_frameworks": (summary.academic_standards, "framework_count"),
        "as_has_child": (summary.academic_standards, "has_child_relationship_count"),
        "as_sfis": (summary.academic_standards, "final_sfi_count"),
        "lc_nodes": (summary.learning_components, "total_lcs"),
        "lc_supports": (summary.learning_components, "total_supports_edges"),
        "total_nodes": (summary, "total_node_count"),
        "total_relationships": (summary, "total_relationship_count"),
    }[location]
    setattr(owner, field, getattr(owner, field) + 1)
    _persist(harness=harness, monkeypatch=monkeypatch)
    before = _snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError, match="summary counts differ"):
        _compile(harness=harness)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="attack", argvalues=["errors_with_passed", "failed"])
def test_upstream_failed_validation_blocks_before_artifact_reader(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject upstream validation failure even when its passed flag claims success.

    Parameters
    ----------
    attack
        False success flag or explicit failed report.
    monkeypatch
        Restoring reader guard.
    tmp_path
        Directory intentionally lacking standalone evidence.
    """
    harness = _claims._Harness(root=tmp_path)
    if attack == "failed":
        harness.bundle.validation_report.passed = False
    else:
        harness.bundle.validation_report.errors.append("Upstream error")
    guard = Mock(side_effect=AssertionError("Failed upstream must gate artifact reads"))
    monkeypatch.setattr(name="read_lp_artifacts", target=lp_export, value=guard)
    with pytest.raises(
        expected_exception=ValueError, match="passed, error-free AS\\+LC"
    ):
        _compile(harness=harness)
    guard.assert_not_called()
    assert _snapshot(tmp_path) == {}


@pytest.mark.parametrize(
    argnames="attack", argvalues=["corrupt_readback", "write_error"]
)
def test_write_errors_and_corrupt_readback_cannot_return_success(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Propagate persistence failure and reject bytes differing from compiled material.

    Parameters
    ----------
    attack
        Failed atomic write or altered schema-valid readback payload.
    monkeypatch
        Restoring wrapper at the actual persistence boundary.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    _compile(harness=harness, overwrite=True)
    before = _snapshot(tmp_path)
    writer = lp_export._atomic_write
    writes: list[str] = []

    def _write(*, path: Path, payload: bytes) -> None:
        """Inject a write error or an altered, schema-valid on-disk bundle.

        Parameters
        ----------
        path
            Bundle output path.
        payload
            Correct compiler output before failure injection.

        Raises
        ------
        OSError
            When the scripted write failure is requested.
        """
        writes.append(path.name)
        assert path.name == _BUNDLE
        if attack == "write_error":
            raise OSError("Synthetic persistence failure")
        material = json.loads(payload)
        material["entity_provenance"]["readback_corruption"] = True
        writer(path=path, payload=_bytes(material))

    monkeypatch.setattr(name="_atomic_write", target=lp_export, value=_write)
    with pytest.raises(_REJECTIONS):
        _compile(harness=harness, overwrite=True)
    assert writes == [_BUNDLE]
    after = _snapshot(tmp_path)
    assert {name: value for name, value in after.items() if name != _BUNDLE} == {
        name: value for name, value in before.items() if name != _BUNDLE
    }
    if attack == "write_error":
        assert after == before


@pytest.mark.parametrize(
    argnames="name",
    argvalues=[
        "lp_generation_summary.json",
        "lp_generation_responses.jsonl",
        "lp_generation_checkpoint_manifest.json",
        "lp_relationship_provenance.json",
        "lp_validation_report.json",
        "lp_eligible_sfis.json",
        "lp_generation_checkpoint_transaction.json",
    ],
)
@pytest.mark.parametrize(
    argnames="timing", argvalues=["after_authentication", "after_write"]
)
def test_write_time_evidence_changes_never_return_success(
    monkeypatch: pytest.MonkeyPatch, name: str, timing: str, tmp_path: Path
) -> None:
    """Detect actual evidence edits across the authentication and atomic-write boundary.

    Parameters
    ----------
    monkeypatch
        Restoring wrappers which preserve the real reader and writer.
    name
        Bound artifact to change, or unfinished generation marker to introduce.
    timing
        Mutation immediately after authentication or after atomic bundle replacement.
    tmp_path
        Isolated evidence root.
    """
    harness = _artifacts._mixed(tmp_path)
    _persist(harness=harness, monkeypatch=monkeypatch)
    reader = lp_export.read_lp_artifacts
    writer = lp_export._atomic_write

    def _change() -> None:
        """Change actual persisted evidence while retaining its visible bytes."""
        path = tmp_path / name
        path.write_bytes(path.read_bytes() + b" " if path.exists() else b"{}\n")

    def _read(**kwargs: Any) -> LPStandaloneArtifacts:
        """Authenticate normally before injecting an inter-stage change.

        Parameters
        ----------
        kwargs
            Current-material reader inputs.

        Returns
        -------
        LPStandaloneArtifacts
            Authenticated snapshot captured before the injected edit.
        """
        result = reader(**kwargs)
        _change()
        return result

    def _write(*, path: Path, payload: bytes) -> None:
        """Perform the actual atomic write before changing source evidence.

        Parameters
        ----------
        path
            Combined bundle output path.
        payload
            Canonical compiled bytes.
        """
        writer(path=path, payload=payload)
        _change()

    if timing == "after_authentication":
        monkeypatch.setattr(name="read_lp_artifacts", target=lp_export, value=_read)
    else:
        monkeypatch.setattr(name="_atomic_write", target=lp_export, value=_write)
    with pytest.raises(
        expected_exception=ValueError,
        match="changed during bundle compilation|transaction is unfinished",
    ):
        _compile(harness=harness)
    assert (tmp_path / _BUNDLE).exists() is (timing == "after_write")
