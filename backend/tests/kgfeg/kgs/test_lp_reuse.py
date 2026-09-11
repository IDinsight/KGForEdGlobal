"""Authenticate saved releases against actual offline evidence and current inputs."""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib
import json
import socket

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

# Third Party Library
import pytest

from pydantic import ValidationError

# Package Library
from kgfeg.config import Settings
from kgfeg.entries import create_kgs
from kgfeg.kgs import lp_checkpoints, lp_export, lp_finalization, prompts
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.schemas import AcademicStandardsLCLPKGBundle
from kgfeg.kgs.utils import KGDirs
from kgfeg.schemas import CreateKGConfig
from tests.kgfeg.kgs import test_create_kgs_lp as _entry
from tests.kgfeg.kgs import test_lp_artifacts as _artifacts
from tests.kgfeg.kgs import test_lp_export as _export
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_orchestration as _storage

_BUNDLE = "as_lc_lp_kg_bundle.json"
_PROJECTIONS = ("as_lc_lp_nodes.jsonl", "as_lc_lp_relationships.jsonl")
_RECEIPT = "lp_generation_checkpoint_manifest.json"
_STAGES = (
    "lp_generation_draft_responses.jsonl",
    "lp_generation_validation_verdicts.jsonl",
    "lp_generation_responses.jsonl",
)
_TRANSACTION = "lp_generation_checkpoint_transaction.json"


def _assert_rejected(*, harness: _claims._Harness, operation: str = "reuse") -> None:
    """Require rejection without changing any evidence or making another model call.

    Parameters
    ----------
    harness
        Current inputs and attacked saved release.
    operation
        Public reuse or compile boundary.
    """
    before = _state(harness.root)
    calls = list(harness.calls)
    with pytest.raises(_export._REJECTIONS):
        _invoke(harness=harness, operation=operation)
    assert _state(harness.root) == before
    assert harness.calls == calls


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject all external transport and use the existing shared KG model setting.

    Parameters
    ----------
    monkeypatch
        Restoring network and model substitutions.
    """
    guard = Mock(side_effect=AssertionError("Reuse tests must remain offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _invoke(
    *, harness: _claims._Harness, operation: str = "reuse", overwrite: bool = False
) -> AcademicStandardsLCLPKGBundle | None:
    """Call the public boundary with real upstream, configuration, and disk evidence.

    Parameters
    ----------
    harness
        Offline generation inputs and storage.
    operation
        Read-only bundle reuse or explicit compilation.
    overwrite
        Compilation replacement permission.

    Returns
    -------
    AcademicStandardsLCLPKGBundle | None
        Authenticated graph, or absence at the reuse boundary.
    """
    if operation == "compile":
        return lp_export.compile_as_lc_lp_kg(
            as_lc_bundle=harness.bundle,
            doc_key=_export._DOC_KEY,
            kg_config=harness.config,
            kg_dirs=KGDirs(root=harness.root),
            overwrite=overwrite,
        )
    return lp_export.reuse_as_lc_lp_kg(
        as_lc_bundle=harness.bundle,
        doc_key=_export._DOC_KEY,
        kg_config=harness.config,
        kg_dirs=KGDirs(root=harness.root),
    )


def _publish(
    *, harness: _claims._Harness, monkeypatch: pytest.MonkeyPatch
) -> AcademicStandardsLCLPKGBundle:
    """Create authentic release evidence through real processing and export stages.

    Parameters
    ----------
    harness
        Scripted untrusted proposals and authoritative input graph.
    monkeypatch
        Restoring external model-call seam.

    Returns
    -------
    AcademicStandardsLCLPKGBundle
        Initial saved release against which attacks are applied.
    """
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    return _export._compile(harness=harness)


def _reset_entry(harness: _entry._Harness) -> None:
    """Clear observations so persisted usage is checked against just the next run.

    Parameters
    ----------
    harness
        Entry-point observer with a completed prior run.
    """
    harness.calls.clear()
    harness.charges.clear()
    harness.trackers.clear()
    harness.proposals.calls.clear()


def _state(root: Path) -> dict[str, tuple[bytes, int, int]]:
    """Capture content, modification time, and inode to detect identical-byte rewrites.

    Parameters
    ----------
    root
        Temporary artifact directory.

    Returns
    -------
    dict[str, tuple[bytes, int, int]]
        File state excluding access time, which read-only validation may update.
    """
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
        for path in root.iterdir()
        if path.is_file()
    }


@pytest.mark.parametrize(argnames="operation", argvalues=["compile", "reuse"])
@pytest.mark.parametrize(
    argnames="change",
    argvalues=[
        "batch",
        "budget",
        "checker_instructions",
        "coordinate_order",
        "model",
        "model_settings",
        "producer_instructions",
        "retry",
        "unresolved_policy",
        "upstream_license",
        "upstream_provenance",
        "upstream_text",
    ],
)
def test_changed_current_material_rejects_before_any_reuse_write(  # pylint: disable=R1260
    change: str, monkeypatch: pytest.MonkeyPatch, operation: str, tmp_path: Path
) -> None:
    """Reject a complete saved release when a current material dependency changes.

    Parameters
    ----------
    change
        Curriculum, source, or execution input changed after publication.
    monkeypatch
        Offline calls and current model settings.
    operation
        Public export boundary under test.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    config = harness.config.learning_progressions
    if change == "batch":
        config.request_batch_size += 1
    elif change == "budget":
        config.candidate_policy.budgets.max_total_candidates += 1
    elif change in {"checker_instructions", "producer_instructions"}:
        setattr(config, change, getattr(config, change) + " Changed bounded guidance.")
    elif change == "coordinate_order":
        config.developmental_coordinate.ordered_values.reverse()
    elif change == "model":
        monkeypatch.setattr(
            name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.1"
        )
    elif change == "model_settings":
        monkeypatch.setattr(name="LLM_MAX_OUTPUT_TOKENS", target=Settings, value=2048)
    elif change == "retry":
        config.retry.checker_max_retries += 1
    elif change == "unresolved_policy":
        config.unresolved_participation = "exclude_unresolved"
    elif change == "upstream_license":
        harness.bundle.framework.license += " revised"
    elif change == "upstream_text":
        harness.bundle.items[0].description += " Changed source evidence."
    else:
        harness.bundle.entity_provenance["new_source_audit"] = {"changed": True}
    _assert_rejected(harness=harness, operation=operation)


@pytest.mark.parametrize(argnames="module", argvalues=["finalization", "prompts"])
def test_changed_definition_material_rejects_without_touching_production(
    module: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bind reuse to real definition bytes without a manually maintained policy version.

    Parameters
    ----------
    module
        Prompt or deterministic finalization source material.
    monkeypatch
        Restoring module-location substitution.
    tmp_path
        Isolated release and synthetic changed definition.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    target = lp_finalization if module == "finalization" else prompts
    changed = tmp_path / "changed_definition.py"
    definition_path = target.__file__
    assert definition_path is not None
    definition = Path(definition_path).read_bytes()
    assert b"buildsTowards" in definition
    changed.write_bytes(definition.replace(b"buildsTowards", b"relatesTo"))
    monkeypatch.setattr(name="__file__", target=target, value=str(changed))
    _assert_rejected(harness=harness)


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "candidate_order",
        "candidate_warning",
        "request_order",
        "response_rationale",
        "checker_correction",
        "final_claim",
        "failure_disposition",
        "summary_counts",
        "provenance",
    ],
)
def test_changed_stored_material_rejects_well_formed_edits_and_recomputed_hashes(  # pylint: disable=R0912, R1260
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject changed actual populations and judgments despite repaired local digests.

    Parameters
    ----------
    attack
        Semantically readable material edited after a successful final export.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    harness.config.learning_progressions.request_batch_size = 1
    _publish(harness=harness, monkeypatch=monkeypatch)
    name = {
        "candidate_order": "lp_candidate_pairs.jsonl",
        "candidate_warning": "lp_candidate_pairs.jsonl",
        "request_order": "lp_generation_requests.jsonl",
        "response_rationale": _STAGES[2],
        "checker_correction": _STAGES[1],
        "final_claim": "lp_final_claims.json",
        "failure_disposition": _RECEIPT,
        "summary_counts": "lp_generation_summary.json",
        "provenance": "lp_relationship_provenance.json",
    }[attack]
    path = tmp_path / name
    if name.endswith(".jsonl"):
        rows = _storage._rows(path)
        if attack.endswith("order"):
            assert len(rows) > 1
            rows.reverse()
        elif attack == "candidate_warning":
            rows[0]["warnings"].append("Synthetic changed evidence warning")
        elif attack == "response_rationale":
            rows[0]["payload"]["judgments"][0]["rationale"] += " Revised rationale."
            rows[0]["payload_content_hash"] = _export._hash(rows[0]["payload"])
        else:
            rows[0]["payload"]["issues"].append("Changed checker evidence")
            rows[0]["payload_content_hash"] = _export._hash(rows[0]["payload"])
        path.write_bytes(b"".join(_export._bytes(row) for row in rows))
        if name in _STAGES:
            _storage._reseal(name=name, root=tmp_path)
    else:
        material = json.loads(path.read_bytes())
        if attack == "final_claim":
            material["claims"][0]["judgment"]["rationale"] += " Revised final claim."
        elif attack == "failure_disposition":
            material["status"] = "failed"
        elif attack == "summary_counts":
            material["object_counts"]["candidate_pairs"] += 1
        else:
            next(iter(material.values()))["claim"]["judgment"][
                "rationale"
            ] += " Revised provenance."
        if "content_hash" in material:
            material["content_hash"] = _export._hash(
                {key: value for key, value in material.items() if key != "content_hash"}
            )
        path.write_bytes(_export._bytes(material))
        if attack in {"provenance", "summary_counts"}:
            _artifacts._reseal(tmp_path)
    _assert_rejected(harness=harness)


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=["duplicate", "empty", "gap", "reorder", "stale_id", "truncate"],
)
@pytest.mark.parametrize(argnames="name", argvalues=_STAGES)
def test_checkpoint_prefix_corruption_rejects_even_with_resealed_receipts(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Validate all stage prefixes rather than accepting their outer count/hash claims.

    Parameters
    ----------
    attack
        Missing, duplicate, misordered, misidentified, or truncated successful work.
    monkeypatch
        Offline model seam.
    name
        Producer, checker, or reconciled checkpoint.
    tmp_path
        Isolated release directory.
    """
    harness = _claims._Harness(batch=1, count=3, root=tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    path = tmp_path / name
    rows = _storage._rows(path)
    assert len(rows) == 3
    if attack == "duplicate":
        rows.insert(1, deepcopy(rows[0]))
    elif attack == "empty":
        rows.clear()
    elif attack == "gap":
        rows.pop(1)
    elif attack == "reorder":
        rows.reverse()
    elif attack == "stale_id":
        rows[0]["payload"]["request_id"] = "00000000-0000-0000-0000-000000000999"
        rows[0]["payload_content_hash"] = _export._hash(rows[0]["payload"])
    path.write_bytes(b"".join(_export._bytes(row) for row in rows))
    _storage._reseal(name=name, root=tmp_path)
    if attack == "truncate":
        path.write_bytes(path.read_bytes()[:-9])
    _assert_rejected(harness=harness)


@pytest.mark.parametrize(argnames="overwrite", argvalues=[False, True])
def test_competing_generation_lock_blocks_reuse_and_overwrite(
    monkeypatch: pytest.MonkeyPatch, overwrite: bool, tmp_path: Path
) -> None:
    """Prevent publication while another writer owns checkpoint authority.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    overwrite
        Explicit replacement versus ordinary reuse.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    before = _state(tmp_path)
    with (tmp_path / ".lp_generation.lock").open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            _invoke(
                harness=harness,
                operation="compile" if overwrite else "reuse",
                overwrite=overwrite,
            )
    assert _state(tmp_path) == before


@pytest.mark.parametrize(
    argnames="outcome", argvalues=["empty", "mixed", "needs_review", "no_relation"]
)
def test_entry_exact_reuse_skips_lp_stages_preserves_checkpoints_and_records_zero_lp_usage(
    monkeypatch: pytest.MonkeyPatch, outcome: str, tmp_path: Path
) -> None:
    """Reuse authentic final graphs before generation can advance its run ordinal.

    Parameters
    ----------
    monkeypatch
        Replaced upstream work and external calls, retaining every real LP stage.
    outcome
        Empty, accepted, negative, or ambiguous population.
    tmp_path
        Isolated pipeline output directory.
    """
    harness = _entry._Harness(root=tmp_path)
    if outcome == "empty":
        harness.bundle = _fixtures._bundle(1)
        harness.proposals.bundle = harness.bundle
        harness.results["compile_as_lc_kg"] = harness.bundle
    elif outcome == "mixed":
        harness.proposals.decisions = {
            (1, 2): ("buildsTowards", "first_to_second"),
            (1, 3): ("relatesTo", None),
        }
        harness.proposals.default_decision = "needs_review"
    else:
        harness.proposals.default_decision = outcome
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    before = _state(harness.root)
    expected = json.loads(before[_BUNDLE][0])
    for name in _PROJECTIONS:
        (harness.root / name).unlink()
    _reset_entry(harness)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    assert harness.calls == list(_entry._PHASES[:-5])
    assert harness.proposals.calls == []
    after = _state(harness.root)
    stable = set(before) - {*_PROJECTIONS, "kg_run.json"}
    assert {name: after[name] for name in stable} == {
        name: before[name] for name in stable
    }
    for name, payload in _export._projection_bytes(expected).items():
        assert after[name][0] == payload
    assert expected["validation_report"]["pedagogical_correctness_established"] is False


@pytest.mark.parametrize(
    argnames="attack", argvalues=["config", "invalid_bundle", "prefix", "transaction"]
)
def test_entry_invalid_saved_release_records_error_without_advancing_lp(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Propagate reuse integrity failures to durable run status without LP calls.

    Parameters
    ----------
    attack
        Stale input or corrupt release evidence.
    monkeypatch
        Offline entry-point seams.
    tmp_path
        Isolated pipeline directory.
    """
    harness = _entry._Harness(root=tmp_path)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    if attack == "config":
        harness.config.learning_progressions.request_batch_size += 1
        harness._save_config()
    elif attack == "invalid_bundle":
        (harness.root / _BUNDLE).write_bytes(b"{}\n")
    elif attack == "prefix":
        path = harness.root / _STAGES[1]
        path.write_bytes(path.read_bytes()[:-10])
    else:
        (harness.root / _TRANSACTION).write_bytes(b"{}\n")
    before = _state(harness.root)
    _reset_entry(harness)
    with pytest.raises(_export._REJECTIONS) as error:
        create_kgs.create(harness.config_path)
    _entry._assert_run(error=type(error.value), harness=harness)
    assert harness.calls == list(_entry._PHASES[:-5])
    assert harness.proposals.calls == []
    after = _state(harness.root)
    stable = set(before) - {"kg_run.json"}
    assert {name: after[name] for name in stable} == {
        name: before[name] for name in stable
    }


def test_entry_overwrite_regenerates_changed_material_and_then_reuses_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regenerate stale configuration explicitly and reuse only the new evidence chain.

    Parameters
    ----------
    monkeypatch
        Offline entry-point and model seams.
    tmp_path
        Isolated pipeline directory.
    """
    harness = _entry._Harness(root=tmp_path)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    original = (harness.root / _BUNDLE).read_bytes()
    harness.config.overwrite = True
    harness.config.learning_progressions.request_batch_size = 2
    harness._save_config()
    _reset_entry(harness)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    assert harness.calls == list(_entry._PHASES)
    assert harness.proposals.calls == [
        ("draft", 0),
        ("verdict", 0),
        ("draft", 1),
        ("verdict", 1),
    ]
    assert harness.mocks["compile_as_lc_lp_kg"].call_args.kwargs["overwrite"] is True
    assert (harness.root / _BUNDLE).read_bytes() != original
    before = _state(harness.root)
    calls = list(harness.proposals.calls)
    result = _invoke(harness=harness.proposals)
    assert result is not None
    assert result.model_dump(mode="json") == json.loads(before[_BUNDLE][0])
    assert harness.proposals.calls == calls
    assert _state(harness.root)[_RECEIPT] == before[_RECEIPT]


@pytest.mark.parametrize(argnames="name", argvalues=_PROJECTIONS)
def test_entry_projection_failure_on_reuse_records_error_and_can_retry_without_calls(
    monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Persist failure status and recover derived files without repeating adjudication.

    Parameters
    ----------
    monkeypatch
        Offline seams and scoped projection persistence failure.
    name
        Projection whose atomic write fails.
    tmp_path
        Isolated pipeline directory.
    """
    harness = _entry._Harness(root=tmp_path)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    before = _state(harness.root)
    writer = lp_export._atomic_write

    def _write(*, path: Path, payload: bytes) -> None:
        """Fail the selected derived artifact after authenticating real evidence.

        Parameters
        ----------
        path
            Output destination.
        payload
            Validated serialized bytes.
        """
        if path.name == name:
            raise OSError("Synthetic reuse projection persistence failure")
        writer(path=path, payload=payload)

    _reset_entry(harness)
    with monkeypatch.context() as scoped:
        scoped.setattr(name="_atomic_write", target=lp_export, value=_write)
        with pytest.raises(expected_exception=OSError, match="Synthetic reuse"):
            create_kgs.create(harness.config_path)
    _entry._assert_run(error=OSError, harness=harness)
    assert not harness.proposals.calls
    _reset_entry(harness)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    assert not harness.proposals.calls
    after = _state(harness.root)
    for key in (_BUNDLE, _RECEIPT, *_STAGES, "lp_generation_failures.json"):
        assert after[key] == before[key]


@pytest.mark.parametrize(argnames="operation", argvalues=["compile", "reuse"])
@pytest.mark.parametrize(argnames="profile", argvalues=_export._PROFILES)
def test_exact_reuse_preserves_full_curriculum_content_and_only_rewrites_projections(
    monkeypatch: pytest.MonkeyPatch, operation: str, profile: str, tmp_path: Path
) -> None:
    """Preserve graph, warning, provenance, and checkpoint authority across curriculum shapes.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    operation
        Public compilation or reuse entry point.
    profile
        Reduced curriculum retaining its tree, DAG, or unresolved-context shape.
    tmp_path
        Isolated release directory.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(batch=20, profile=profile)
    harness.default_decision = "relatesTo"
    upstream = harness.bundle.model_dump(mode="json")
    expected = _publish(harness=harness, monkeypatch=monkeypatch)
    for name in (
        "as_kg_bundle.json",
        "as_nodes.jsonl",
        "as_relationships.jsonl",
        "as_lc_kg_bundle.json",
        "as_lc_nodes.jsonl",
        "as_lc_relationships.jsonl",
    ):
        (tmp_path / name).write_bytes(b"opaque upstream consumer artifact\n")
    before = _state(tmp_path)
    calls = list(harness.calls)
    result = _invoke(harness=harness, operation=operation)
    assert result == expected
    assert result is not None
    assert harness.calls == calls
    assert harness.bundle.model_dump(mode="json") == upstream
    after = _state(tmp_path)
    assert set(before) == set(after)
    assert {key: value for key, value in after.items() if key not in _PROJECTIONS} == {
        key: value for key, value in before.items() if key not in _PROJECTIONS
    }
    material = result.model_dump(mode="json")
    for name, payload in _export._projection_bytes(material).items():
        assert after[name][0] == payload
    assert material["summary"]["total_node_count"] == 1 + len(upstream["items"]) + len(
        upstream["learning_components"]
    )
    assert material["summary"]["total_relationship_count"] == sum(
        len(material[key])
        for key in (
            "relationships_has_child",
            "relationships_supports",
            "relationships_builds_towards",
            "relationships_relates_to",
        )
    )
    for key in (
        "framework",
        "items",
        "learning_components",
        "relationships_has_child",
        "relationships_supports",
    ):
        assert material[key] == upstream[key]
    for key, value in upstream["entity_provenance"].items():
        assert material["entity_provenance"][key] == value
    assert material["summary"]["as_lc_summary"] == upstream["summary"]
    for key, value in upstream["unresolved_items"].items():
        assert material["unresolved_items"][key] == value
    assert result.validation_report.semantic_validation_performed is False
    assert result.validation_report.pedagogical_correctness_established is False
    # Every bound digest is checked against actual disk bytes independently.
    for name, digest in result.validation_report.artifact_byte_hashes.items():
        assert hashlib.sha256(after[name][0]).hexdigest() == digest


@pytest.mark.parametrize(
    argnames="attack", argvalues=["corrupt_bundle", "missing_projection"]
)
def test_explicit_overwrite_replaces_only_validated_export_and_preserves_checkpoints(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Allow explicit replacement of derived output without relaxing evidence validation.

    Parameters
    ----------
    attack
        Broken final output to replace.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    expected = _publish(harness=harness, monkeypatch=monkeypatch)
    if attack == "corrupt_bundle":
        (tmp_path / _BUNDLE).write_bytes(b"corrupt\n")
        _assert_rejected(harness=harness)
    else:
        (tmp_path / _PROJECTIONS[0]).unlink()
    before = _state(tmp_path)
    calls = list(harness.calls)
    result = _invoke(harness=harness, operation="compile", overwrite=True)
    assert result == expected
    assert harness.calls == calls
    after = _state(tmp_path)
    for name, state in before.items():
        if name not in (_BUNDLE, *_PROJECTIONS):
            assert after[name] == state
    assert after[_BUNDLE][0] == _export._bytes(expected.model_dump(mode="json"))


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=["config", "failed_upstream", "missing_checkpoint", "transaction"],
)
def test_explicit_overwrite_still_rejects_invalid_or_stale_evidence(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Require valid current standalone material even when output replacement is authorized.

    Parameters
    ----------
    attack
        Upstream or checkpoint defect that overwrite cannot waive.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    if attack == "config":
        harness.config.learning_progressions.request_batch_size += 1
    elif attack == "failed_upstream":
        harness.bundle.validation_report.passed = False
    elif attack == "missing_checkpoint":
        (tmp_path / _STAGES[0]).unlink()
    else:
        (tmp_path / _TRANSACTION).write_bytes(b"{}\n")
    before = _state(tmp_path)
    with pytest.raises(_export._REJECTIONS):
        _invoke(harness=harness, operation="compile", overwrite=True)
    assert _state(tmp_path) == before


@pytest.mark.parametrize(
    argnames="name",
    argvalues=["algorithm_version", "fingerprint_inputs", "ranking", "strategies"],
)
def test_forbidden_candidate_compatibility_selectors_cannot_authorize_reuse(
    monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Reject candidate-policy compatibility switches at current configuration validation.

    Parameters
    ----------
    monkeypatch
        Offline model seam.
    name
        Forbidden runtime algorithm or compatibility selector.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    payload = harness.config.model_dump(by_alias=True, mode="json")
    payload["lp"]["candidate_policy"][name] = "synthetic replacement"
    before = _state(tmp_path)
    with pytest.raises(ValidationError):
        CreateKGConfig.model_validate(payload)
    assert _state(tmp_path) == before


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "counts",
        "collision",
        "edge",
        "hashes",
        "invalid_json",
        "passed_flag",
        "provenance",
        "upstream",
        "warnings",
    ],
)
def test_invalid_saved_bundle_never_repairs_itself_under_reuse(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject saved graph forgeries despite a previously valid standalone evidence chain.

    Parameters
    ----------
    attack
        Structural, provenance, material, or syntax corruption in the final graph.
    monkeypatch
        Offline model seam.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    original = _publish(harness=harness, monkeypatch=monkeypatch)
    material = original.model_dump(mode="json")
    if attack == "counts":
        material["summary"]["total_relationship_count"] += 1
    elif attack == "collision":
        material["items"][0]["case_identifier_uuid"] = material["framework"][
            "case_identifier_uuid"
        ]
    elif attack == "edge":
        material["relationships_builds_towards"].pop()
    elif attack == "hashes":
        material["validation_report"]["input_content_hashes"]["effective_config"] = (
            "0" * 64
        )
    elif attack == "passed_flag":
        material["validation_report"]["passed"] = False
    elif attack == "provenance":
        material["entity_provenance"]["relationships_relates_to"].clear()
    elif attack == "upstream":
        material["framework"]["name"] += " Forged source title"
    elif attack == "warnings":
        material["validation_report"]["warnings"].append("Unaccounted warning")
    payload = b'{"truncated":' if attack == "invalid_json" else _export._bytes(material)
    (tmp_path / _BUNDLE).write_bytes(payload)
    _assert_rejected(harness=harness)
    _assert_rejected(harness=harness, operation="compile")


@pytest.mark.parametrize(argnames="attack", argvalues=["corrupt", "missing"])
@pytest.mark.parametrize(
    argnames="name",
    argvalues=[
        "lp_candidate_pairs.jsonl",
        "lp_candidate_summary.json",
        "lp_generation_requests.jsonl",
        "lp_generation_requests_manifest.json",
        _RECEIPT,
        *_STAGES,
        "lp_generation_failures.json",
        "lp_final_claims.json",
        "lp_relationship_provenance.json",
        "lp_generation_summary.json",
        "lp_unresolved_items.json",
        "lp_validation_report.json",
        "lp_relationships_builds_towards.jsonl",
        "lp_relationships_relates_to.jsonl",
    ],
)
def test_missing_or_corrupt_bound_artifact_blocks_existing_release(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Require every bound population, checkpoint, claim, and release artifact.

    Parameters
    ----------
    attack
        Missing input or malformed actual bytes.
    monkeypatch
        Offline model seam.
    name
        Required material artifact.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    path = tmp_path / name
    if attack == "missing":
        path.unlink()
    else:
        path.write_bytes(b"{invalid\n")
    _assert_rejected(harness=harness)


def test_missing_release_does_not_initialize_any_generation_artifacts(
    tmp_path: Path,
) -> None:
    """Treat absent final output as a resume opportunity without touching checkpoints.

    Parameters
    ----------
    tmp_path
        Empty temporary output directory.
    """
    harness = _claims._Harness(root=tmp_path)
    assert _invoke(harness=harness) is None
    assert _state(tmp_path) == {}


@pytest.mark.parametrize(argnames="attack", argvalues=["corrupt", "missing", "stale"])
@pytest.mark.parametrize(argnames="name", argvalues=_PROJECTIONS)
def test_projection_repair_uses_saved_authority_and_keeps_all_evidence_untouched(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Rebuild missing or stale derived rows without re-adjudication or graph rewriting.

    Parameters
    ----------
    attack
        Derived-file corruption to repair.
    monkeypatch
        Offline model seam.
    name
        Node or relationship projection.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    original = _publish(harness=harness, monkeypatch=monkeypatch)
    path = tmp_path / name
    if attack == "missing":
        path.unlink()
    elif attack == "corrupt":
        path.write_bytes(b"{invalid\n")
    else:
        path.write_bytes(b"{}\n")
    before = _state(tmp_path)
    calls = list(harness.calls)
    assert _invoke(harness=harness) == original
    assert harness.calls == calls
    after = _state(tmp_path)
    for key, value in before.items():
        if key not in _PROJECTIONS:
            assert after[key] == value
    for key, payload in _export._projection_bytes(
        original.model_dump(mode="json")
    ).items():
        assert after[key][0] == payload


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_recovered_failures_remain_exact_audit_history_during_final_reuse(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Recover the earliest unfinished call and retain later dispositions in reused output.

    Parameters
    ----------
    monkeypatch
        Offline model seam with one exhausted request.
    stage
        Producer or checker stage whose transient failure is recorded.
    tmp_path
        Isolated release directory.
    """
    harness = _claims._Harness(count=2, root=tmp_path)
    harness.default_decision = "needs_review"
    harness.failure = (stage, 0)
    _claims._install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(LPGenerationFailed):
        harness._run()
    assert not (tmp_path / _BUNDLE).exists()
    failure = json.loads((tmp_path / "lp_generation_failures.json").read_bytes())[0]
    assert failure["resolved_run_number"] is None
    harness.failure = None
    harness.calls.clear()
    expected = _publish(harness=harness, monkeypatch=monkeypatch)
    assert harness.calls == (
        [("verdict", 0)] if stage == "verdict" else [("draft", 0), ("verdict", 0)]
    )
    before = _state(tmp_path)
    calls = list(harness.calls)
    assert _invoke(harness=harness) == expected
    assert harness.calls == calls
    assert (
        _state(tmp_path)["lp_generation_failures.json"]
        == before["lp_generation_failures.json"]
    )
    assert _state(tmp_path)[_RECEIPT] == before[_RECEIPT]
    counts = expected.summary.learning_progressions["object_counts"]
    assert (
        counts["generation_failure_attempts"]
        == counts["resolved_failure_attempts"]
        == 1
    )
    assert counts["unresolved_failed_pairs"] == 0
    assert expected.unresolved_items.learning_progressions["total_needs_review"] == 1


@pytest.mark.parametrize(
    argnames="name",
    argvalues=["semantic_overrides", "forced_edges", "unresolved_exceptions"],
)
def test_semantic_and_per_sfi_overrides_remain_forbidden_config_inputs(
    name: str, tmp_path: Path
) -> None:
    """Reject manual relationship edits or selective unresolved exceptions as runtime policy.

    Parameters
    ----------
    name
        Unsupported override field.
    tmp_path
        Isolated harness directory.
    """
    harness = _claims._Harness(root=tmp_path)
    payload = harness.config.model_dump(by_alias=True, mode="json")
    payload["lp"][name] = [{"pair_id": "synthetic", "decision": "relatesTo"}]
    with pytest.raises(ValidationError):
        CreateKGConfig.model_validate(payload)


@pytest.mark.parametrize(argnames="after", argvalues=[False, True])
def test_transaction_recovery_requires_generation_and_revalidation_before_reuse(
    after: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reject unfinished durable transactions and stale exports after real recovery.

    Parameters
    ----------
    after
        Interrupt after replacing the manifest instead of before replacement.
    monkeypatch
        Scoped actual checkpoint writer interruption.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    original = (tmp_path / _BUNDLE).read_bytes()
    writer = lp_checkpoints._atomic_write
    fired = False

    def _write(*, path: Path, payload: bytes) -> None:
        """Interrupt a real transaction at the manifest publication boundary.

        Parameters
        ----------
        path
            Checkpoint output path.
        payload
            Real next-state serialized material.
        """
        nonlocal fired
        hit = path.name == _RECEIPT and not fired
        if hit and not after:
            fired = True
            raise _storage._Interruption()
        writer(path=path, payload=payload)
        if hit and after:
            fired = True
            raise _storage._Interruption()

    calls = list(harness.calls)
    with monkeypatch.context() as scoped:
        scoped.setattr(name="_atomic_write", target=lp_checkpoints, value=_write)
        with pytest.raises(_storage._Interruption):
            harness._run()
    assert fired
    assert (tmp_path / _TRANSACTION).exists()
    _assert_rejected(harness=harness)
    harness._run()
    assert not (tmp_path / _TRANSACTION).exists()
    assert harness.calls == calls
    # Recovery changes receipt material; old standalone claims and export are stale.
    _assert_rejected(harness=harness)
    _export._persist(harness=harness, monkeypatch=monkeypatch)
    _assert_rejected(harness=harness)
    result = _invoke(harness=harness, operation="compile", overwrite=True)
    assert result is not None
    assert (tmp_path / _BUNDLE).read_bytes() != original
    assert _invoke(harness=harness) == result
    assert harness.calls == calls


@pytest.mark.parametrize(argnames="name", argvalues=[_BUNDLE, _RECEIPT, _TRANSACTION])
def test_write_time_changes_during_projection_repair_cannot_return_success(
    monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Recheck bundle and checkpoint authority after writing derived projections.

    Parameters
    ----------
    monkeypatch
        Restoring actual projection writer wrapper.
    name
        Release or execution evidence changed during a projection write.
    tmp_path
        Isolated release directory.
    """
    harness = _artifacts._mixed(tmp_path)
    _publish(harness=harness, monkeypatch=monkeypatch)
    writer = lp_export._atomic_write
    calls = list(harness.calls)

    def _write(*, path: Path, payload: bytes) -> None:
        """Write the actual projection before injecting a concurrent authority change.

        Parameters
        ----------
        path
            Output path.
        payload
            Validated projection bytes.
        """
        writer(path=path, payload=payload)
        if path.name == _PROJECTIONS[-1]:
            target = tmp_path / name
            target.write_bytes(
                target.read_bytes() + b" " if target.exists() else b"{}\n"
            )

    monkeypatch.setattr(name="_atomic_write", target=lp_export, value=_write)
    with pytest.raises(_export._REJECTIONS):
        _invoke(harness=harness)
    assert harness.calls == calls
