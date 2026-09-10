"""Red-team standalone persistence using real offline checkpoint authority."""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib
import json
import socket

from collections import Counter
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID, uuid5

# Third Party Library
import pytest

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import lp_artifacts
from kgfeg.kgs.lp_artifacts import (
    LPGenerationSummary,
    LPStandaloneArtifacts,
    LPUnresolvedItems,
    read_lp_artifacts,
    write_lp_artifacts,
)
from kgfeg.kgs.lp_finalization import LPFinalizationCycleError, LPRelationships
from kgfeg.kgs.lp_selection import select_lp_sfis
from kgfeg.kgs.utils import KGDirs
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_validation as _validation

_BUILDS = "lp_relationships_builds_towards.jsonl"
_FAILURES = "lp_generation_failures.json"
_PROVENANCE = "lp_relationship_provenance.json"
_RELATES = "lp_relationships_relates_to.jsonl"
_REPORT = "lp_validation_report.json"
_SUMMARY = "lp_generation_summary.json"
_UNRESOLVED = "lp_unresolved_items.json"
_FILES = (_BUILDS, _FAILURES, _PROVENANCE, _RELATES, _REPORT, _SUMMARY, _UNRESOLVED)
_INPUTS = (
    "lp_candidate_pairs.jsonl",
    "lp_candidate_summary.json",
    "lp_eligible_sfis.json",
    "lp_eligibility_report.json",
    "lp_final_claims.json",
    "lp_generation_checkpoint_manifest.json",
    "lp_generation_draft_responses.jsonl",
    _FAILURES,
    "lp_generation_requests.jsonl",
    "lp_generation_requests_manifest.json",
    "lp_generation_responses.jsonl",
    "lp_generation_validation_verdicts.jsonl",
)
_PROFILES = (
    "ghana_english",
    "ghana_math",
    "madhi_math",
    "nigeria_math",
    "pratham_science",
    "rwanda_math",
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid network transport and pin a synthetic execution identity.

    Parameters
    ----------
    monkeypatch
        Restoring socket guards and model identity.
    """
    guard = Mock(side_effect=AssertionError("Artifact tests must remain offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _bytes(value: Any) -> bytes:
    """Encode the independent canonical serialization oracle.

    Parameters
    ----------
    value
        JSON-compatible material.

    Returns
    -------
    bytes
        Sorted compact UTF-8 JSON followed by one newline.
    """
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _complete(
    *, harness: _claims._Harness, monkeypatch: pytest.MonkeyPatch
) -> LPRelationships:
    """Persist eligibility and complete real checkpoints with scripted model calls.

    Parameters
    ----------
    harness
        Isolated current material and endpoint-keyed proposal script.
    monkeypatch
        Offline model-call seam.

    Returns
    -------
    LPRelationships
        Complete directly adjudicated relationship rows.
    """
    select_lp_sfis(
        as_lc_bundle=harness.bundle,
        kg_config=harness.config,
        kg_dirs=KGDirs(root=harness.root),
    )
    _, relationships = _validation._complete(harness=harness, monkeypatch=monkeypatch)
    return relationships


def _hash(value: Any) -> str:
    """Hash actual canonical content without using production hash helpers.

    Parameters
    ----------
    value
        Material to bind.

    Returns
    -------
    str
        Independent SHA-256 digest.
    """
    return hashlib.sha256(_bytes(value)).hexdigest()


def _mixed(root: Path) -> _claims._Harness:
    """Script direct, transitive-path, negative, and ambiguous outcomes.

    Parameters
    ----------
    root
        Isolated artifact directory.

    Returns
    -------
    _claims._Harness
        Six logical pairs with four directly accepted edges.
    """
    harness = _claims._Harness(count=4, root=root)
    harness.decisions = {
        (1, 2): ("buildsTowards", "first_to_second"),
        (1, 3): ("buildsTowards", "first_to_second"),
        (1, 4): ("relatesTo", None),
        (2, 3): ("buildsTowards", "first_to_second"),
        (2, 4): ("no_relation", None),
        (3, 4): ("needs_review", None),
    }
    return harness


def _read(harness: _claims._Harness) -> LPStandaloneArtifacts:
    """Read against current upstream material through the public boundary.

    Parameters
    ----------
    harness
        Current material and isolated directory.

    Returns
    -------
    LPStandaloneArtifacts
        Authenticated persisted objects.
    """
    return read_lp_artifacts(
        as_lc_bundle=harness.bundle,
        doc_key=_validation._DOC_KEY,
        kg_config=harness.config,
        kg_dirs=KGDirs(root=harness.root),
    )


def _reseal(root: Path) -> None:
    """Recompute attacker-controlled companion hashes and the summary digest.

    Parameters
    ----------
    root
        Already-written standalone files under attack.
    """
    summary = json.loads((root / _SUMMARY).read_bytes())
    report = json.loads((root / _REPORT).read_bytes())
    summary["validation_report_content_hash"] = report["content_hash"]
    summary["validation_report_passed"] = report["passed"]
    for name in _FILES:
        if name != _SUMMARY:
            summary["artifact_byte_hashes"][name] = hashlib.sha256(
                (root / name).read_bytes()
            ).hexdigest()
    summary["content_hash"] = _hash(
        {key: value for key, value in summary.items() if key != "content_hash"}
    )
    (root / _SUMMARY).write_bytes(_bytes(summary))
    LPGenerationSummary.model_validate(summary)


def _rows(path: Path) -> list[dict[str, Any]]:
    """Read actual rows without relying on production population summaries.

    Parameters
    ----------
    path
        JSONL artifact.

    Returns
    -------
    list[dict[str, Any]]
        Actual row population.
    """
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _write(
    *, harness: _claims._Harness, relationships: LPRelationships
) -> LPStandaloneArtifacts:
    """Write through the public standalone boundary.

    Parameters
    ----------
    harness
        Current upstream authority.
    relationships
        Complete proposed graph and provenance.

    Returns
    -------
    LPStandaloneArtifacts
        Read-back checked standalone artifacts.
    """
    return write_lp_artifacts(
        as_lc_bundle=harness.bundle,
        doc_key=_validation._DOC_KEY,
        kg_config=harness.config,
        kg_dirs=KGDirs(root=harness.root),
        relationships=relationships,
    )


def test_canonical_round_trip_retains_exact_claims_counts_hashes_and_provenance(  # pylint: disable=R0915
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retain all direct evidence, independently counted populations, and exact bytes.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    harness.corrections = dict(harness.decisions)
    harness.bundle.framework.attribution_statement = "Synthetic source: café, 数学."
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    before = _validation._snapshot(tmp_path)
    original = relationships.model_dump_json()
    result = _write(harness=harness, relationships=relationships)
    after = _validation._snapshot(tmp_path)
    assert set(after) - set(before) == set(_FILES) - {_FAILURES}
    assert all(after[name] == value for name, value in before.items())
    assert relationships.model_dump_json() == original
    assert _read(harness) == result
    assert _validation._snapshot(tmp_path) == after
    assert LPStandaloneArtifacts.model_validate_json(result.model_dump_json()) == result
    claims = json.loads(after["lp_final_claims.json"])["claims"]
    judgments = [
        judgment
        for row in _rows(tmp_path / "lp_generation_responses.jsonl")
        for judgment in row["payload"]["judgments"]
    ]
    decisions = Counter(judgment["decision"] for judgment in judgments)
    assert decisions == {
        "buildsTowards": 3,
        "relatesTo": 1,
        "no_relation": 1,
        "needs_review": 1,
    }
    assert result.summary.decision_counts == decisions
    counts = result.summary.object_counts
    assert counts == {
        "accepted_claims": 4,
        "builds_towards_claims": 3,
        "builds_towards_relationships": 3,
        "candidate_pairs": 6,
        "final_claims": 6,
        "generation_failure_attempts": 0,
        "identifier_collisions": 0,
        "needs_review_claims": 1,
        "no_relation_claims": 1,
        "nonpublishing_claims": 2,
        "relationship_provenance": 4,
        "relates_to_claims": 1,
        "relates_to_relationships": 1,
        "relationships": 4,
        "requests": 2,
        "resolved_failure_attempts": 0,
        "unresolved_failed_pairs": 0,
        "unresolved_warning_pairs": 0,
    }
    assert counts["candidate_pairs"] == len(
        _rows(tmp_path / "lp_candidate_pairs.jsonl")
    )
    assert counts["requests"] == len(_rows(tmp_path / "lp_generation_requests.jsonl"))
    assert counts["final_claims"] == len(judgments) == len(claims)
    assert result.summary.distributions["checker_outcome"] == {"corrected": 6}
    assert [
        claim.model_dump(mode="json") for claim in result.unresolved_items.claims
    ] == [claim for claim in claims if claim["judgment"]["decision"] == "needs_review"]
    assert result.unresolved_items.total_needs_review == 1
    edges = _rows(tmp_path / _BUILDS) + _rows(tmp_path / _RELATES)
    assert {
        (UUID(edge["source_entity_value"]).int, UUID(edge["target_entity_value"]).int)
        for edge in edges
    } == {(1, 2), (1, 3), (1, 4), (2, 3)}
    provenance = json.loads(after[_PROVENANCE])
    for edge in edges:
        expected_id = uuid5(
            name=f"lc:curriculum:synthetic-selection-document:relationship:{edge['relationship_type']}:{edge['source_entity_value']}:{edge['target_entity_value']}",
            namespace=UUID("3f6b9f2a-7d8a-5d85-a9c3-9f3b8d3c3f4b"),
        )
        assert edge["identifier"] == str(expected_id)
        assert edge["metadata"] == provenance[edge["identifier"]]
        record = provenance[edge["identifier"]]
        assert record["claim"] in claims
        assert record["claim"]["judgment"] in judgments
        assert (
            record["source_framework"]["attribution_statement"]
            == harness.bundle.framework.attribution_statement
        )
        assert (
            record["source_framework"]["license"]
            == edge["license"]
            == harness.bundle.framework.license
        )
        assert edge["author"] == "LLM generated"
        assert edge["provider"] == "IDinsight"
        assert "not stated or endorsed" in edge["attribution_statement"]
    _claims._assert_provenance(artifact=harness._finalize(validate=True), root=tmp_path)
    for name in _FILES:
        payload = after[name]
        if name.endswith("jsonl"):
            assert payload == b"".join(_bytes(row) for row in _rows(tmp_path / name))
            identifiers = [row["identifier"] for row in _rows(tmp_path / name)]
            assert identifiers == sorted(identifiers)
        else:
            assert payload == _bytes(json.loads(payload))
        if name != _SUMMARY:
            assert (
                result.summary.artifact_byte_hashes[name]
                == hashlib.sha256(payload).hexdigest()
            )
    for name, digest in result.summary.input_artifact_byte_hashes.items():
        assert digest == hashlib.sha256(before[name]).hexdigest()
    for name in (_REPORT, _SUMMARY, _UNRESOLVED):
        material = json.loads(after[name])
        assert material.pop("content_hash") == _hash(material)
    assert result.validation_report.passed
    assert result.validation_report.semantic_validation_performed is False
    assert result.validation_report.pedagogical_correctness_established is False
    reversed_rows = relationships.model_copy(
        deep=True,
        update={
            "relationships_builds_towards": tuple(
                reversed(relationships.relationships_builds_towards)
            )
        },
    )
    assert _write(harness=harness, relationships=reversed_rows) == result
    assert _validation._snapshot(tmp_path) == after


@pytest.mark.parametrize(
    argnames="category",
    argvalues=["framework", "has_child", "learning_component", "sfi", "supports"],
)
def test_collisions_remain_failed_inspectable_diagnostics(
    category: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retain invalid rows and collision counts without turning diagnostics into success.

    Parameters
    ----------
    category
        Upstream identifier category reused by an LP edge.
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture("ghana_math")
    harness.config = _fixtures._config(batch=20, profile="ghana_math")
    harness.default_decision = "relatesTo"
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    collision = {
        "framework": harness.bundle.framework.case_identifier_uuid,
        "has_child": harness.bundle.relationships_has_child[0].identifier,
        "learning_component": harness.bundle.learning_components[0].identifier,
        "sfi": harness.bundle.items[0].case_identifier_uuid,
        "supports": harness.bundle.relationships_supports[0].identifier,
    }[category]
    rows = list(relationships.relationships_relates_to)
    rows[0] = rows[0].model_copy(deep=True, update={"identifier": collision})
    attacked = relationships.model_copy(
        deep=True, update={"relationships_relates_to": tuple(rows)}
    )
    result = _write(harness=harness, relationships=attacked)
    assert not result.validation_report.passed
    assert result.summary.validation_report_passed is False
    assert result.summary.object_counts["identifier_collisions"] == 1
    assert str(collision) in {row["identifier"] for row in _rows(tmp_path / _RELATES)}
    assert _read(harness) == result


def test_cycle_diagnostics_round_trip_with_direct_claim_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Persist whole-graph cycle evidence without silently repairing accepted rows.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(batch=1, count=3, root=tmp_path)
    harness.decisions = {
        (1, 2): ("buildsTowards", "first_to_second"),
        (1, 3): ("buildsTowards", "second_to_first"),
        (2, 3): ("buildsTowards", "first_to_second"),
    }
    _claims._install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    with pytest.raises(LPFinalizationCycleError) as caught:
        harness._finalize()
    relationships = _validation._cycle_relationships(
        artifact=caught.value.artifact, harness=harness
    )
    result = _write(harness=harness, relationships=relationships)
    assert not result.validation_report.passed
    assert result.validation_report.cycle_diagnostics.cyclic_edge_count == 3
    assert result.summary.object_counts["relationships"] == 3
    assert _read(harness) == result


@pytest.mark.parametrize(argnames="count", argvalues=[0, 1])
def test_empty_populations_have_zero_byte_relations_and_no_calls(
    count: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Distinguish no SFIs from eligible SFIs with no candidate pair.

    Parameters
    ----------
    count
        Zero or one eligible SFI.
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(count=count, root=tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    result = _write(harness=harness, relationships=relationships)
    assert result.validation_report.passed
    assert harness.calls == []
    assert (
        (tmp_path / _BUILDS).read_bytes() == (tmp_path / _RELATES).read_bytes() == b""
    )
    assert all(value == 0 for value in result.summary.object_counts.values())
    assert result.summary.eligibility["total_sfis_eligible"] == count
    assert result.summary.eligibility["total_sfis_considered"] == count
    assert result.failures == result.unresolved_items.claims == ()
    assert _read(harness) == result


@pytest.mark.parametrize(
    argnames="attack", argvalues=["active_failure", "missing_response", "transaction"]
)
def test_incomplete_processing_cannot_write_standalone_success(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep unfinished checkpoints and active failure evidence intact for recovery.

    Parameters
    ----------
    attack
        Actual failed execution, missing prefix, or unfinished transaction.
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    if attack == "active_failure":
        harness.failure = ("verdict", 0)
        _claims._install(harness=harness, monkeypatch=monkeypatch)
        with pytest.raises(_claims.LPGenerationFailed):
            harness._run()
        relationships = LPRelationships(
            final_claims_content_hash="unavailable",
            relationship_provenance={},
            relationships_builds_towards=(),
            relationships_relates_to=(),
        )
    else:
        relationships = _complete(harness=harness, monkeypatch=monkeypatch)
        if attack == "missing_response":
            (tmp_path / "lp_generation_responses.jsonl").write_bytes(b"")
        else:
            (tmp_path / "lp_generation_checkpoint_transaction.json").write_bytes(
                _bytes({"status": "unfinished"})
            )
    before = _validation._snapshot(tmp_path)
    with pytest.raises(_claims._REJECTIONS):
        _write(harness=harness, relationships=relationships)
    assert _validation._snapshot(tmp_path) == before
    assert not (tmp_path / _SUMMARY).exists()
    if attack == "active_failure":
        failures = json.loads(before[_FAILURES])
        assert len(failures) == 1
        assert failures[0]["resolved_run_number"] is None


@pytest.mark.parametrize(argnames="operation", argvalues=["read", "write"])
@pytest.mark.parametrize(
    argnames="race", argvalues=["eligibility", "failure", "transaction"]
)
def test_input_changes_after_validation_fail_before_artifact_access(
    monkeypatch: pytest.MonkeyPatch, operation: str, race: str, tmp_path: Path
) -> None:
    """Reject a changed input snapshot between real validation and locked access.

    Parameters
    ----------
    monkeypatch
        Restoring orchestration seam after full ordinary validation.
    operation
        Standalone reader or writer.
    race
        Material changed immediately before the final lock boundary.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    _write(harness=harness, relationships=relationships)
    original = lp_artifacts._prepare_artifacts
    filename = {
        "eligibility": "lp_eligibility_report.json",
        "failure": _FAILURES,
        "transaction": "lp_generation_checkpoint_transaction.json",
    }[race]
    before = _validation._snapshot(tmp_path)

    def _prepare_then_change(**kwargs: Any) -> Any:
        """Change one file only after the normal validator has authenticated it.

        Parameters
        ----------
        kwargs
            Complete public artifact-processing inputs.

        Returns
        -------
        Any
            Real prepared objects and their now-outdated input snapshot.
        """
        prepared = original(**kwargs)
        path = tmp_path / filename
        path.write_bytes(before.get(filename, b"{}") + b" ")
        return prepared

    monkeypatch.setattr(
        name="_prepare_artifacts", target=lp_artifacts, value=_prepare_then_change
    )
    with pytest.raises(expected_exception=ValueError, match="changed|unfinished"):
        if operation == "read":
            _read(harness)
        else:
            _write(harness=harness, relationships=relationships)
    after = _validation._snapshot(tmp_path)
    assert after[filename] == before.get(filename, b"{}") + b" "
    assert {key: value for key, value in after.items() if key != filename} == {
        key: value for key, value in before.items() if key != filename
    }


@pytest.mark.parametrize(argnames="existing", argvalues=[False, True])
@pytest.mark.parametrize(argnames="filename", argvalues=list(_FILES))
def test_interrupted_replacements_never_publish_a_mixed_success(
    existing: bool, filename: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inject replacement failures and reject incomplete sets while allowing old bytes.

    Parameters
    ----------
    existing
        Whether a prior valid standalone set already exists.
    filename
        Exact file replacement to interrupt.
    monkeypatch
        Offline and filesystem failure seams.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    if existing:
        _write(harness=harness, relationships=relationships)
    before = _validation._snapshot(tmp_path)
    changed = relationships.model_copy(
        deep=True,
        update={
            "relationships_builds_towards": relationships.relationships_builds_towards[
                :-1
            ]
        },
    )
    original = lp_artifacts.os.replace

    def _interrupt(*, dst: Any, src: Any) -> None:
        """Fail before replacing the selected complete file.

        Parameters
        ----------
        dst
            Destination path.
        src
            Temporary file path.
        """
        if Path(dst).name == filename:
            raise OSError("Synthetic interrupted replacement.")
        original(dst=dst, src=src)

    with monkeypatch.context() as patch:
        patch.setattr(name="replace", target=lp_artifacts.os, value=_interrupt)
        with pytest.raises(expected_exception=OSError, match="Synthetic interrupted"):
            _write(harness=harness, relationships=changed)
    after = _validation._snapshot(tmp_path)
    assert not list(tmp_path.glob(".lp_artifacts-*"))
    assert after[_FAILURES] == before[_FAILURES]
    if existing and all(after[name] == before[name] for name in _FILES):
        assert _read(harness).validation_report.passed
    else:
        with pytest.raises(_claims._REJECTIONS):
            _read(harness)
    restored = _write(harness=harness, relationships=relationships)
    assert _read(harness) == restored
    assert restored.validation_report.passed


@pytest.mark.parametrize(argnames="operation", argvalues=["read", "write"])
def test_lock_contention_fails_without_mutating_any_artifact(
    monkeypatch: pytest.MonkeyPatch, operation: str, tmp_path: Path
) -> None:
    """Use separate file descriptions to exercise real nonblocking lock contention.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    operation
        Reader or writer attempted under an independently held lock.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    _write(harness=harness, relationships=relationships)
    before = _validation._snapshot(tmp_path)
    with (tmp_path / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            if operation == "read":
                _read(harness)
            else:
                _write(harness=harness, relationships=relationships)
    assert _validation._snapshot(tmp_path) == before
    assert _read(harness).validation_report.passed


@pytest.mark.parametrize(argnames="filename", argvalues=list(_FILES))
@pytest.mark.parametrize(
    argnames="mutation", argvalues=["missing", "truncated", "whitespace"]
)
def test_missing_malformed_or_noncanonical_files_are_rejected(
    filename: str, monkeypatch: pytest.MonkeyPatch, mutation: str, tmp_path: Path
) -> None:
    """Reject missing, malformed, and byte-edited artifacts even after rehashing.

    Parameters
    ----------
    filename
        Standalone artifact under attack.
    monkeypatch
        Offline call seam.
    mutation
        File removal, truncation, or noncanonical whitespace.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    _write(harness=harness, relationships=relationships)
    path = tmp_path / filename
    if mutation == "missing":
        path.unlink()
    elif mutation == "truncated":
        path.write_bytes(path.read_bytes()[:-3])
    else:
        path.write_bytes(path.read_bytes() + b" ")
        if filename != _SUMMARY:
            _reseal(tmp_path)
    before = _validation._snapshot(tmp_path)
    with pytest.raises(_claims._REJECTIONS):
        _read(harness)
    assert _validation._snapshot(tmp_path) == before


def test_policy_exclusions_are_not_unresolved_judgments_or_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep excluded unresolved ancestry in eligibility only, with exact counts.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture("ghana_math")
    harness.config = _fixtures._config(batch=4, profile="ghana_math")
    harness.config.learning_progressions.unresolved_participation = "exclude_unresolved"
    harness.default_decision = "needs_review"
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    result = _write(harness=harness, relationships=relationships)
    excluded: set[str] = set()
    for edge in harness.bundle.relationships_has_child:
        if edge.metadata.get("unresolved_root_fallback"):
            excluded.add(edge.target_entity_value)
    while True:
        descendants = {
            edge.target_entity_value
            for edge in harness.bundle.relationships_has_child
            if edge.source_entity_value in excluded
        }
        if descendants <= excluded:
            break
        excluded |= descendants
    assert excluded
    assert result.summary.eligibility["unresolved_sfis_policy_excluded"] == len(
        excluded
    )
    assert result.summary.eligibility["unresolved_sfis_eligible"] == 0
    assert result.summary.object_counts["unresolved_warning_pairs"] == 0
    candidates = _rows(tmp_path / "lp_candidate_pairs.jsonl")
    assert candidates
    assert not excluded & {
        candidate[key]
        for candidate in candidates
        for key in ("first_sfi_uuid", "second_sfi_uuid")
    }
    assert result.unresolved_items.total_needs_review == len(candidates)
    assert result.summary.object_counts["relationships"] == 0
    assert result.summary.object_counts["generation_failure_attempts"] == 0
    assert result.failures == ()
    assert result.validation_report.passed
    assert _read(harness) == result


@pytest.mark.parametrize(
    argnames="decision", argvalues=["needs_review", "no_relation", "relatesTo"]
)
@pytest.mark.parametrize(argnames="profile", argvalues=list(_PROFILES))
def test_profiles_preserve_dag_warnings_exclusions_and_non_gating_outcomes(
    decision: str, monkeypatch: pytest.MonkeyPatch, profile: str, tmp_path: Path
) -> None:
    """Carry every reduced curriculum's real evidence through all standalone files.

    Parameters
    ----------
    decision
        Uniform independently scripted outcome.
    monkeypatch
        Offline call seam.
    profile
        Reduced curriculum with synthetic same-grain peers.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(batch=4, profile=profile)
    harness.default_decision = decision
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    result = _write(harness=harness, relationships=relationships)
    assert result.validation_report.passed
    assert _read(harness) == result
    candidates = _rows(tmp_path / "lp_candidate_pairs.jsonl")
    assert candidates
    candidate_summary = json.loads(
        (tmp_path / "lp_candidate_summary.json").read_bytes()
    )
    assert candidate_summary["evidence_type_counts"] == Counter(
        evidence["evidence_type"]
        for candidate in candidates
        for evidence in candidate["evidence"]
    )
    assert candidate_summary["candidate_warning_counts"] == Counter(
        warning for candidate in candidates for warning in candidate["warnings"]
    )
    eligible = json.loads((tmp_path / "lp_eligible_sfis.json").read_bytes())
    selection = json.loads((tmp_path / "lp_eligibility_report.json").read_bytes())
    allowed = {
        pair.source_statement_type
        for pair in harness.config.learning_progressions.builds_towards.allowed_statement_type_pairs
    }
    eligible_ids = {
        str(item.case_identifier_uuid)
        for item in harness.bundle.items
        if item.statement_type in allowed
    }
    assert {
        record["sfi"]["case_identifier_uuid"] for record in eligible
    } == eligible_ids
    assert result.summary.eligibility["total_sfis_eligible"] == len(eligible_ids)
    assert result.summary.eligibility["total_sfis_excluded"] == len(
        harness.bundle.items
    ) - len(eligible_ids)
    assert result.summary.eligibility == {
        key: value for key, value in selection.items() if key != "sfis"
    }
    assert result.summary.object_counts["candidate_pairs"] == len(candidates)
    assert result.summary.object_counts["nonpublishing_claims"] == (
        0 if decision == "relatesTo" else len(candidates)
    )
    assert result.unresolved_items.total_needs_review == (
        len(candidates) if decision == "needs_review" else 0
    )
    assert result.summary.object_counts["relationships"] == (
        len(candidates) if decision == "relatesTo" else 0
    )
    assert result.summary.object_counts["unresolved_failed_pairs"] == 0
    assert result.failures == ()
    claims = json.loads((tmp_path / "lp_final_claims.json").read_bytes())["claims"]
    warned = [claim for claim in claims if claim["candidate"]["warnings"]]
    assert result.summary.warning_counts == Counter(
        warning for claim in claims for warning in claim["judgment"]["warnings"]
    )
    assert result.summary.object_counts["unresolved_warning_pairs"] == len(warned)
    for claim in warned:
        assert set(claim["candidate"]["warnings"]) <= set(claim["judgment"]["warnings"])
        assert any(
            claim["judgment"]["pair_id"] in warning
            for warning in result.validation_report.warnings
        )
    if profile == "ghana_math":
        assert warned
    if profile == "pratham_science":
        assert any(len(record["parent_sfi_uuids"]) >= 2 for record in eligible)
        for claim in claims:
            assert claim["candidate"] in candidates
    if profile == "madhi_math":
        assert not any(item.statement_type == "Class" for item in harness.bundle.items)
        assert all(record["coordinate"]["canonical_value"] for record in eligible)
    if decision == "relatesTo":
        for edge in _rows(tmp_path / _RELATES):
            assert edge["source_entity_value"] < edge["target_entity_value"]
            assert edge["metadata"]["claim"] in claims
    assert result.validation_report.semantic_validation_performed is False
    assert result.validation_report.pedagogical_correctness_established is False


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "count",
        "decision_count",
        "drop_direct_edge",
        "false_pass",
        "provenance",
        "semantic_gate",
        "unresolved",
        "warning",
    ],
)
def test_recomputed_hashes_do_not_authorize_edited_content(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Authenticate actual authority instead of trusting a self-consistent report.

    Parameters
    ----------
    attack
        Count, row, provenance, ambiguity, warning, or release-claim forgery.
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    if attack == "false_pass":
        relationships = relationships.model_copy(
            deep=True, update={"relationships_builds_towards": ()}
        )
    result = _write(harness=harness, relationships=relationships)
    name = _SUMMARY
    payload = json.loads((tmp_path / name).read_bytes())
    if attack == "count":
        payload["object_counts"]["candidate_pairs"] += 1
    elif attack == "decision_count":
        payload["decision_counts"]["no_relation"] += 1
    elif attack == "drop_direct_edge":
        (tmp_path / _BUILDS).write_bytes(
            b"".join(_bytes(row) for row in _rows(tmp_path / _BUILDS)[1:])
        )
    elif attack in {"false_pass", "semantic_gate", "warning"}:
        name = _REPORT
        payload = result.validation_report.model_dump(mode="json")
        if attack == "false_pass":
            assert payload["passed"] is False
            payload.update(errors=[], passed=True)
        elif attack == "semantic_gate":
            payload["semantic_validation_performed"] = True
        else:
            payload["warnings"].append("Invented warning.")
    elif attack == "provenance":
        name = _PROVENANCE
        payload = json.loads((tmp_path / name).read_bytes())
        next(iter(payload.values()))["claim"]["judgment"][
            "rationale"
        ] = "Forged direct claim evidence."
    else:
        name = _UNRESOLVED
        payload = result.unresolved_items.model_dump(mode="json")
        payload.update(claims=[], total_needs_review=0)
    if "content_hash" in payload:
        payload["content_hash"] = _hash(
            {key: value for key, value in payload.items() if key != "content_hash"}
        )
    (tmp_path / name).write_bytes(_bytes(payload))
    _reseal(tmp_path)
    before = _validation._snapshot(tmp_path)
    with pytest.raises(_claims._REJECTIONS):
        _read(harness)
    assert _validation._snapshot(tmp_path) == before


def test_recovered_failure_history_is_exact_and_does_not_become_ambiguity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preserve prior producer/checker failures and their later disposition verbatim.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    harness.default_decision = "needs_review"
    _claims._install(harness=harness, monkeypatch=monkeypatch)
    for stage in ("draft", "verdict"):
        harness.failure = (stage, 0)
        with pytest.raises(_claims.LPGenerationFailed):
            harness._run()
    harness.failure = None
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    failure_bytes = (tmp_path / _FAILURES).read_bytes()
    result = _write(harness=harness, relationships=relationships)
    assert (tmp_path / _FAILURES).read_bytes() == failure_bytes
    assert len(result.failures) == 2
    assert all(
        failure["resolved_run_number"] is not None for failure in result.failures
    )
    assert result.summary.object_counts["generation_failure_attempts"] == 2
    assert result.summary.object_counts["resolved_failure_attempts"] == 2
    assert result.summary.object_counts["unresolved_failed_pairs"] == 0
    assert result.unresolved_items.total_needs_review == 1
    assert result.validation_report.passed
    assert _read(harness) == result


@pytest.mark.parametrize(argnames="filename", argvalues=list(_INPUTS))
def test_stale_material_inputs_are_rejected_without_rewriting_outputs(
    filename: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject actual content changes throughout the authoritative artifact chain.

    Parameters
    ----------
    filename
        Persisted upstream, eligibility, or checkpoint input to alter.
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    _write(harness=harness, relationships=relationships)
    path = tmp_path / filename
    if filename == "lp_eligible_sfis.json":
        payload = json.loads(path.read_bytes())
        payload.pop()
        path.write_bytes(_bytes(payload))
    elif filename == "lp_eligibility_report.json":
        payload = json.loads(path.read_bytes())
        payload["total_sfis_eligible"] += 1
        path.write_bytes(_bytes(payload))
    else:
        path.write_bytes(path.read_bytes() + b" ")
    before = _validation._snapshot(tmp_path)
    for operation in ("read", "write"):
        with pytest.raises(_claims._REJECTIONS):
            if operation == "read":
                _read(harness)
            else:
                _write(harness=harness, relationships=relationships)
        assert _validation._snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="attack", argvalues=["config", "model", "upstream"])
def test_stale_runtime_inputs_cannot_reuse_a_passed_report(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject material runtime changes despite complete self-consistent saved files.

    Parameters
    ----------
    attack
        Effective policy, model identity, or source authority change.
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    _write(harness=harness, relationships=relationships)
    if attack == "config":
        harness.config.learning_progressions.request_batch_size += 1
    elif attack == "model":
        monkeypatch.setattr(
            name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5-mini"
        )
    else:
        harness.bundle.framework.attribution_statement += " Changed authority."
    before = _validation._snapshot(tmp_path)
    with pytest.raises(_claims._REJECTIONS):
        _read(harness)
    with pytest.raises(_claims._REJECTIONS):
        _write(harness=harness, relationships=relationships)
    assert _validation._snapshot(tmp_path) == before


def test_unresolved_schema_rejects_negative_duplicate_and_miscounted_claims(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Require exact ambiguous claims even when their content digest is recomputed.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    result = _write(harness=harness, relationships=relationships)
    final_claims = json.loads((tmp_path / "lp_final_claims.json").read_bytes())[
        "claims"
    ]
    for attack in ("duplicate", "negative", "wrong_count"):
        payload = result.unresolved_items.model_dump(mode="json")
        if attack == "duplicate":
            payload["claims"] *= 2
            payload["total_needs_review"] = 2
        elif attack == "negative":
            payload["claims"] = [
                next(
                    claim
                    for claim in final_claims
                    if claim["judgment"]["decision"] == "no_relation"
                )
            ]
        else:
            payload["total_needs_review"] += 1
        payload["content_hash"] = _hash(
            {key: value for key, value in payload.items() if key != "content_hash"}
        )
        with pytest.raises(ValueError):
            LPUnresolvedItems.model_validate(payload)


@pytest.mark.parametrize(
    argnames="counter",
    argvalues=["candidate_pairs", "generation_failure_attempts", "relationships"],
)
def test_writer_reconciles_counts_instead_of_copying_validator_claims(
    counter: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject a loadable validator result that disagrees with actual populations.

    Parameters
    ----------
    counter
        Validator count independently recomputed by artifact persistence.
    monkeypatch
        Validator return seam after ordinary real validation.
    tmp_path
        Isolated evidence directory.
    """
    harness = _mixed(tmp_path)
    relationships = _complete(harness=harness, monkeypatch=monkeypatch)
    original = lp_artifacts.validate_lp_graph

    def _miscount(**kwargs: Any) -> Any:
        """Return a self-consistent hash around one incorrect population claim.

        Parameters
        ----------
        kwargs
            Real standalone validation inputs.

        Returns
        -------
        Any
            Schema-valid report with a falsified actual object count.
        """
        report = original(**kwargs)
        payload = report.model_dump(mode="json")
        payload["object_counts"][counter] += 1
        payload["content_hash"] = _hash(
            {key: value for key, value in payload.items() if key != "content_hash"}
        )
        return type(report).model_validate(payload)

    monkeypatch.setattr(name="validate_lp_graph", target=lp_artifacts, value=_miscount)
    before = _validation._snapshot(tmp_path)
    with pytest.raises(
        expected_exception=ValueError, match="counts or material disagree"
    ):
        _write(harness=harness, relationships=relationships)
    assert _validation._snapshot(tmp_path) == before
