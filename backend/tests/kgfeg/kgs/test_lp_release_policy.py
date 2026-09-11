"""Exercise release policy with synthetic proposals, never pedagogical truth labels.

These production checks do not measure candidate recall or semantic quality.
Separate evaluation artifacts and reports may exist without becoming production
prerequisites. Findings require upstream remediation and rerun, never edge overrides.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import socket

from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

from pydantic import ValidationError

# Package Library
from kgfeg.config import Settings
from kgfeg.entries import create_kgs
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.lp_validation import LPValidationReport
from kgfeg.schemas import CreateKGConfig
from tests.kgfeg.kgs import test_create_kgs_lp as _entry
from tests.kgfeg.kgs import test_lp_artifacts as _artifacts
from tests.kgfeg.kgs import test_lp_candidates as _candidates
from tests.kgfeg.kgs import test_lp_finalization as _claims
from tests.kgfeg.kgs import test_lp_generation as _fixtures
from tests.kgfeg.kgs import test_lp_reuse as _reuse

_BUNDLE = "as_lc_lp_kg_bundle.json"
_SCOPE = (
    "Validation covers structural and process integrity only; it does not establish "
    "pedagogical correctness."
)
_UNSUPPORTED = (
    ("evaluation_artifacts", "separate-evaluation/"),
    ("evaluation_required", True),
    ("evaluation_score", 1.0),
    ("semantic_gold_set", "synthetic-gold.json"),
    ("semantic_metric", "precision"),
    ("semantic_threshold", 0.99),
    ("minimum_human_review_sample", 100),
    ("audit_cadence", "before_every_release"),
    ("human_semantic_approval", True),
    ("semantic_pass_fail_authority", "Synthetic reviewer"),
    ("semantic_overrides", [{"decision": "relatesTo"}]),
    ("maximum_failed_pairs", 1),
    ("maximum_failed_pair_rate", 0.01),
)


def _audit(*, action: str, harness: _claims._Harness) -> Path:
    """Record a complete synthetic post-release finding outside graph authority.

    This is a test-owned adversarial sidecar, not a new production audit schema or
    release prerequisite. Even a fully attributed finding cannot force an edge.

    Parameters
    ----------
    action
        Requested semantic edit used to challenge artifact integrity.
    harness
        Already released synthetic graph and exact effective configuration.

    Returns
    -------
    Path
        Test-owned finding, bound to the actual released bytes and config material.
    """
    bundle = _json(harness.root / _BUNDLE)
    rows = bundle["relationships_builds_towards"] + bundle["relationships_relates_to"]
    claims = _json(harness.root / "lp_final_claims.json")["claims"]
    path = harness.root / "lp_semantic_overrides.json"
    path.write_bytes(
        _artifacts._bytes(
            {
                "affected_candidate_ids": [
                    row["judgment"]["pair_id"] for row in claims
                ],
                "affected_relationship_ids": [row["identifier"] for row in rows],
                "authority": "IDinsight",
                "effective_config_content_hash": _artifacts._hash(
                    harness.config.model_dump(by_alias=True, mode="json")
                ),
                "findings": [{"requested_action": action, "semantic_approved": False}],
                "population_or_sampling_method": "All synthetic candidate pairs.",
                "rationale": "Synthetic audit allegation; no pedagogical truth oracle.",
                "released_artifact_byte_hashes": {
                    name: hashlib.sha256((harness.root / name).read_bytes()).hexdigest()
                    for name in (
                        _BUNDLE,
                        "as_lc_lp_relationships.jsonl",
                        "lp_relationships_builds_towards.jsonl",
                        "lp_relationships_relates_to.jsonl",
                    )
                },
                "review_time": "2026-09-10T12:00:00Z",
                "reviewer_identity": "Synthetic reviewer",
            }
        )
    )
    return path


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid network access and pin the shared KG setting for offline fakes.

    Parameters
    ----------
    monkeypatch
        Restoring transport and non-secret model-identity substitutions.
    """
    guard = Mock(side_effect=AssertionError("Release-policy tests must stay offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _json(path: Path) -> Any:
    """Read actual JSON material without using reported counts as an oracle.

    Parameters
    ----------
    path
        Temporary artifact.

    Returns
    -------
    Any
        Decoded JSON payload.
    """
    return json.loads(path.read_bytes())


@pytest.mark.parametrize(
    argnames="action", argvalues=["delete", "direction", "insert", "relation"]
)
@pytest.mark.parametrize(argnames="operation", argvalues=["compile", "reuse"])
@pytest.mark.parametrize(argnames="surface", argvalues=["combined", "standalone"])
def test_audit_cannot_authorize_generated_bundle_edits(
    action: str,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    surface: str,
    tmp_path: Path,
) -> None:
    """Reject every forced semantic edit without silently repairing the saved graph.

    Parameters
    ----------
    action
        Requested forced exclusion, reversal, inclusion, or relation change.
    monkeypatch
        Offline producer/checker seam.
    operation
        Public compilation or saved-release reuse boundary.
    surface
        Combined bundle or standalone relationship rows under attack.
    tmp_path
        Isolated release whose bytes are deliberately corrupted only in this test.
    """
    harness = _artifacts._mixed(tmp_path)
    _reuse._publish(harness=harness, monkeypatch=monkeypatch)
    _audit(action=action, harness=harness)
    path = tmp_path / _BUNDLE
    payload = _json(path)
    builds = payload["relationships_builds_towards"]
    if action == "delete":
        builds.pop()
    elif action == "direction":
        row = builds[0]
        row["source_entity_value"], row["target_entity_value"] = (
            row["target_entity_value"],
            row["source_entity_value"],
        )
    elif action == "insert":
        row = deepcopy(payload["relationships_relates_to"][0])
        row.update(
            identifier=str(UUID(int=999999)),
            source_entity_value=str(UUID(int=3)),
            target_entity_value=str(UUID(int=4)),
        )
        payload["relationships_relates_to"].append(row)
    else:
        row = builds.pop()
        row["relationship_type"] = "relatesTo"
        payload["relationships_relates_to"].append(row)
    if surface == "combined":
        path.write_bytes(_artifacts._bytes(payload))
    else:
        for key, name in (
            ("relationships_builds_towards", "lp_relationships_builds_towards.jsonl"),
            ("relationships_relates_to", "lp_relationships_relates_to.jsonl"),
        ):
            (tmp_path / name).write_bytes(
                b"".join(_artifacts._bytes(row) for row in payload[key])
            )
        _artifacts._reseal(tmp_path)
    _reuse._assert_rejected(harness=harness, operation=operation)


@pytest.mark.parametrize(
    argnames="surface", argvalues=["lp", "relationship_metadata", "retry"]
)
@pytest.mark.parametrize(argnames="field,value", argvalues=_UNSUPPORTED)
def test_config_rejects_semantic_prerequisites_overrides_and_failure_tolerances(
    field: str, surface: str, value: Any
) -> None:
    """Keep optional semantic review distinct from required inference ownership.

    Parameters
    ----------
    field
        Unsupported semantic gate, override, or failure-tolerance selector.
    surface
        Runtime namespace challenged with an otherwise valid configuration.
    value
        Concrete attempted policy payload.
    """
    harness = _claims._Harness(root=Path("unused-test-path"))
    payload = harness.config.model_dump(by_alias=True, mode="json")
    assert CreateKGConfig.model_validate(payload) == harness.config
    assert payload["lp"]["relationship_metadata"]["approved_by"] == "IDinsight"
    target = payload["lp"] if surface == "lp" else payload["lp"][surface]
    target[field] = value
    with pytest.raises(ValidationError) as caught:
        CreateKGConfig.model_validate(payload)
    expected = ("lp", field) if surface == "lp" else ("lp", surface, field)
    assert any(
        error["loc"] == expected and error["type"] == "extra_forbidden"
        for error in caught.value.errors()
    )


@pytest.mark.parametrize(argnames="initial_report", argvalues=[False, True])
@pytest.mark.parametrize(argnames="profile", argvalues=_claims._PROFILES)
def test_evaluation_artifacts_cannot_gate_production_or_invalidate_reuse(
    initial_report: bool,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    tmp_path: Path,
) -> None:
    """Keep separate evaluation state inert across production success and reuse.

    Parameters
    ----------
    initial_report
        Whether unfavorable diagnostic evidence exists before the first release.
    monkeypatch
        Offline producer/checker and upstream phase substitutions.
    profile
        Reduced curriculum retaining its distinctive structural evidence.
    tmp_path
        Isolated production inputs, outputs, and disjoint diagnostic directory.
    """
    harness = _entry._Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(batch=20, profile=profile)
    harness.config.overwrite = False
    harness.proposals.bundle = harness.bundle
    harness.proposals.config = harness.config
    harness.proposals.default_decision = "needs_review"
    harness.results["compile_as_lc_kg"] = harness.bundle
    harness._save_config()
    evaluation = tmp_path / "separate-evaluation"
    evaluation.mkdir()
    report = evaluation / "lp_eval_report.json"
    # Adversarial opaque sidecars, not an evaluator schema or semantic truth labels.
    unfavorable = b'{"execution_complete": false, "score": 0, "approved": false}'
    if initial_report:
        report.write_bytes(unfavorable)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    assert harness.proposals.calls
    bundle = _json(harness.root / _BUNDLE)
    counts = bundle["summary"]["learning_progressions"]["object_counts"]
    assert counts["candidate_pairs"] > 0
    assert counts["needs_review_claims"] == counts["candidate_pairs"]
    assert counts["unresolved_failed_pairs"] == 0
    assert not bundle["relationships_builds_towards"]
    assert not bundle["relationships_relates_to"]
    assert (
        bundle["unresolved_items"]["learning_progressions"]["total_needs_review"]
        == counts["candidate_pairs"]
    )
    saved = {
        name: value[0]
        for name, value in _reuse._state(harness.root).items()
        if name != "kg_run.json"
    }
    for payload in (
        unfavorable,
        b'{"execution_complete": true, "score": 1, "approved": true}',
        b'{"incomplete":',
        None,
    ):
        if payload is None:
            report.unlink()
        else:
            report.write_bytes(payload)
        _reuse._reset_entry(harness)
        create_kgs.create(harness.config_path)
        _entry._assert_run(error=None, harness=harness)
        assert not harness.proposals.calls
        assert saved == {
            name: value[0]
            for name, value in _reuse._state(harness.root).items()
            if name != "kg_run.json"
        }
        assert (
            report.read_bytes() == payload
            if payload is not None
            else not report.exists()
        )
    for validation in (
        _json(harness.root / "lp_validation_report.json"),
        bundle["validation_report"],
    ):
        assert validation["passed"] is True
        assert validation["semantic_validation_performed"] is False
        assert validation["pedagogical_correctness_established"] is False
        assert validation["semantic_scope_notice"] == _SCOPE


def test_exclusion_negative_ambiguity_and_failure_counts_are_distinct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exclude fallback ancestry before adjudicating accepted, negative, and review pairs.

    Parameters
    ----------
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Isolated synthetic release.
    """
    harness = _claims._Harness(count=4, root=tmp_path)
    harness.bundle = _candidates._bundle(
        edges=(
            _candidates._edge(
                fallback=True,
                source=harness.bundle.framework.case_identifier_uuid,
                source_entity="StandardsFramework",
                target=UUID(int=4),
            ),
        ),
        items=tuple(harness.bundle.items),
    )
    harness.config.learning_progressions.unresolved_participation = "exclude_unresolved"
    harness.decisions = {
        (1, 2): ("relatesTo", None),
        (1, 3): ("no_relation", None),
        (2, 3): ("needs_review", None),
    }
    bundle = _reuse._publish(harness=harness, monkeypatch=monkeypatch)
    summary = _json(tmp_path / "lp_generation_summary.json")
    unresolved = _json(tmp_path / "lp_unresolved_items.json")
    candidates = _artifacts._rows(tmp_path / "lp_candidate_pairs.jsonl")
    assert len(candidates) == 3
    assert str(UUID(int=4)) not in {
        row[key] for row in candidates for key in ("first_sfi_uuid", "second_sfi_uuid")
    }
    assert summary["eligibility"]["unresolved_sfis_policy_excluded"] == 1
    assert summary["object_counts"]["accepted_claims"] == 1
    assert summary["object_counts"]["no_relation_claims"] == 1
    assert summary["object_counts"]["needs_review_claims"] == 1
    assert summary["object_counts"]["unresolved_failed_pairs"] == 0
    assert unresolved["total_needs_review"] == len(unresolved["claims"]) == 1
    assert unresolved["claims"][0]["judgment"]["first_sfi_uuid"] == str(UUID(int=2))
    assert unresolved["claims"][0]["judgment"]["second_sfi_uuid"] == str(UUID(int=3))
    assert _json(tmp_path / "lp_generation_failures.json") == []
    assert bundle.validation_report.passed
    assert len(bundle.relationships_relates_to) == 1
    assert not bundle.relationships_builds_towards


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_failure_after_ambiguity_blocks_until_real_processing_recovers(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Preserve completed ambiguous judgments while one failed pair halts release.

    Parameters
    ----------
    monkeypatch
        Upstream stand-ins and offline model seam; LP stages remain real.
    stage
        Producer or checker that fails after a completed ambiguous pair.
    tmp_path
        Isolated status, checkpoints, and eventual graph.
    """
    harness = _entry._Harness(root=tmp_path)
    harness.proposals.default_decision = "needs_review"
    harness.proposals.failure = (stage, 1)
    evaluation = tmp_path / "separate-evaluation"
    evaluation.mkdir()
    report = evaluation / "lp_eval_report.json"
    favorable = b'{"execution_complete": true, "score": 1, "approved": true}'
    report.write_bytes(favorable)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    with pytest.raises(LPGenerationFailed):
        create_kgs.create(harness.config_path)
    _entry._assert_run(error=LPGenerationFailed, harness=harness)
    responses = _artifacts._rows(harness.root / "lp_generation_responses.jsonl")
    assert len(responses) == 1
    assert responses[0]["payload"]["judgments"][0]["decision"] == "needs_review"
    failures = _json(harness.root / "lp_generation_failures.json")
    assert len(failures) == 1
    assert not (harness.root / _BUNDLE).exists()
    assert "compile_as_lc_lp_kg" not in harness.calls
    draft_prefix = (harness.root / "lp_generation_draft_responses.jsonl").read_bytes()
    harness.proposals.failure = None
    _reuse._reset_entry(harness)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    assert harness.proposals.calls == (
        ([("draft", 1)] if stage == "draft" else [])
        + [("verdict", 1), ("draft", 2), ("verdict", 2)]
    )
    assert (
        (harness.root / "lp_generation_draft_responses.jsonl")
        .read_bytes()
        .startswith(draft_prefix)
    )
    bundle = _json(harness.root / _BUNDLE)
    counts = bundle["summary"]["learning_progressions"]["object_counts"]
    assert counts["needs_review_claims"] == 3
    assert counts["unresolved_failed_pairs"] == 0
    assert counts["generation_failure_attempts"] == 1
    assert (
        bundle["unresolved_items"]["learning_progressions"]["total_needs_review"] == 3
    )
    assert not bundle["relationships_builds_towards"]
    assert not bundle["relationships_relates_to"]
    recovered = _json(harness.root / "lp_generation_failures.json")
    assert len(recovered) == 1
    assert recovered[0]["resolved_run_number"] == 2
    completed = _artifacts._rows(harness.root / "lp_generation_responses.jsonl")
    assert recovered[0]["resolved_response_content_hash"] == _artifacts._hash(
        completed[1]["payload"]
    )
    assert failures[0]["resolved_run_number"] is None
    assert failures[0]["exhausted"] is True
    assert report.read_bytes() == favorable


@pytest.mark.parametrize(argnames="count", argvalues=[1, 12])
def test_needs_review_population_size_does_not_create_a_release_threshold(
    count: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Release empty or entirely ambiguous populations without sample-size gates.

    Parameters
    ----------
    count
        Synthetic cohort size; nomination recall is not measured by this test.
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Isolated release with no independent semantic review inputs.
    """
    harness = _claims._Harness(count=count, root=tmp_path)
    harness.default_decision = "needs_review"
    result = _reuse._publish(harness=harness, monkeypatch=monkeypatch)
    candidates = _artifacts._rows(tmp_path / "lp_candidate_pairs.jsonl")
    pair_ids = {row["pair_id"] for row in candidates}
    assert len(pair_ids) == len(candidates)
    if count == 1:
        assert not pair_ids
        assert not harness.calls
    else:
        assert len(pair_ids) > 3
        assert {stage for stage, _ in harness.calls} == {"draft", "verdict"}
    unresolved = _json(tmp_path / "lp_unresolved_items.json")
    assert {row["judgment"]["pair_id"] for row in unresolved["claims"]} == pair_ids
    assert unresolved["total_needs_review"] == len(pair_ids)
    counts = result.summary.learning_progressions["object_counts"]
    assert counts["candidate_pairs"] == counts["needs_review_claims"] == len(pair_ids)
    assert counts["accepted_claims"] == counts["no_relation_claims"] == 0
    assert counts["unresolved_failed_pairs"] == 0
    assert not result.relationships_builds_towards
    assert not result.relationships_relates_to
    assert result.validation_report.passed
    assert result.validation_report.semantic_validation_performed is False
    assert result.validation_report.pedagogical_correctness_established is False
    assert result.unresolved_items.learning_progressions == unresolved
    calls = list(harness.calls)
    assert _reuse._invoke(harness=harness) == result
    assert harness.calls == calls


@pytest.mark.parametrize(argnames="corrected", argvalues=[False, True])
@pytest.mark.parametrize(argnames="mixed", argvalues=[False, True])
def test_needs_review_releases_without_semantic_inputs_and_survives_reuse(
    corrected: bool, mixed: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Release complete ambiguous work, including corrected high-confidence proposals.

    Parameters
    ----------
    corrected
        Have the checker replace producer positives with the prescribed outcomes.
    mixed
        Include one accepted low-confidence edge and one confident negative.
    monkeypatch
        Upstream stand-ins and offline model seam; LP stages remain real.
    tmp_path
        Empty temporary workspace with no semantic review prerequisites.
    """
    harness = _entry._Harness(root=tmp_path)
    outcomes: dict[tuple[int, int], tuple[str, str | None]] = {
        (1, 2): (
            ("buildsTowards", "first_to_second") if mixed else ("needs_review", None)
        ),
        (1, 3): ("no_relation", None) if mixed else ("needs_review", None),
        (2, 3): ("needs_review", None),
    }
    harness.proposals.decisions = outcomes
    if corrected:
        harness.proposals.decisions = {pair: ("relatesTo", None) for pair in outcomes}
        harness.proposals.corrections = outcomes
    # Validate config independently because upstream input loading is stubbed here.
    assert (
        CreateKGConfig.model_validate(_json(harness.config_path)["kgs"])
        == harness.config
    )
    assert (
        harness.config.learning_progressions.relationship_metadata.approved_by
        == "IDinsight"
    )
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    unresolved = _json(harness.root / "lp_unresolved_items.json")
    expected_review = {
        pair for pair, outcome in outcomes.items() if outcome[0] == "needs_review"
    }
    assert {
        (
            UUID(row["judgment"]["first_sfi_uuid"]).int,
            UUID(row["judgment"]["second_sfi_uuid"]).int,
        )
        for row in unresolved["claims"]
    } == expected_review
    assert unresolved["total_needs_review"] == len(expected_review)
    assert {row["provenance"]["checker_outcome"] for row in unresolved["claims"]} == {
        "corrected" if corrected else "accepted"
    }
    summary = _json(harness.root / "lp_generation_summary.json")
    assert summary["object_counts"]["needs_review_claims"] == len(expected_review)
    assert summary["object_counts"]["accepted_claims"] == int(mixed)
    assert summary["object_counts"]["no_relation_claims"] == int(mixed)
    assert summary["object_counts"]["unresolved_failed_pairs"] == 0
    assert _json(harness.root / "lp_generation_failures.json") == []
    bundle = _json(harness.root / _BUNDLE)
    assert bundle["unresolved_items"]["learning_progressions"] == unresolved
    assert bundle["summary"]["learning_progressions"] == summary
    for name in (
        "lp_relationships_builds_towards.jsonl",
        "lp_relationships_relates_to.jsonl",
        "as_lc_lp_relationships.jsonl",
    ):
        rows = [
            row
            for row in _artifacts._rows(harness.root / name)
            if row["relationship_type"] in {"buildsTowards", "relatesTo"}
        ]
        assert len(rows) == (int(mixed) if "relates_to" not in name else 0)
        assert {
            (UUID(row["source_entity_value"]).int, UUID(row["target_entity_value"]).int)
            for row in rows
        } == ({(1, 2)} if rows else set())
    for report in (
        _json(harness.root / "lp_validation_report.json"),
        bundle["validation_report"],
    ):
        assert report["passed"] is True
        assert report["semantic_validation_performed"] is False
        assert report["pedagogical_correctness_established"] is False
        assert report["semantic_scope_notice"] == _SCOPE
    limitations = _json(harness.root / "lp_candidate_summary.json")["limitations"]
    assert (
        "Candidate recall is unmeasured; deterministic non-embedding nomination may miss plausible pairs."
        in limitations
    )
    assert (
        "Candidate structural and process counts do not establish pedagogical correctness."
        in limitations
    )
    saved = {
        name: (harness.root / name).read_bytes()
        for name in (_BUNDLE, "lp_unresolved_items.json", "lp_generation_summary.json")
    }
    _reuse._reset_entry(harness)
    create_kgs.create(harness.config_path)
    _entry._assert_run(error=None, harness=harness)
    assert not harness.proposals.calls
    assert saved == {name: (harness.root / name).read_bytes() for name in saved}


def test_optional_audit_is_nonblocking_and_cannot_resolve_ambiguity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep attributed post-release findings inert until upstream inputs are rerun.

    Parameters
    ----------
    monkeypatch
        Offline producer/checker seam.
    tmp_path
        Isolated release and synthetic post-release record.
    """
    harness = _claims._Harness(root=tmp_path)
    harness.default_decision = "needs_review"
    original = _reuse._publish(harness=harness, monkeypatch=monkeypatch)
    path = _audit(action="include_relatesTo", harness=harness)
    before = {name: value[0] for name, value in _reuse._state(tmp_path).items()}
    calls = list(harness.calls)
    assert _reuse._invoke(harness=harness) == original
    assert before == {name: value[0] for name, value in _reuse._state(tmp_path).items()}
    assert harness.calls == calls
    assert path.exists()
    # A material prompt correction cannot reuse old judgments, even with an audit.
    harness.config.learning_progressions.producer_instructions += (
        " Re-examine the bounded evidence."
    )
    _reuse._assert_rejected(harness=harness)
    rerun_root = tmp_path / "affected-rerun"
    rerun_root.mkdir()
    rerun = _claims._Harness(root=rerun_root)
    rerun.config = harness.config.model_copy(deep=True)
    rerun.default_decision = "needs_review"
    released = _reuse._publish(harness=rerun, monkeypatch=monkeypatch)
    assert rerun.calls
    assert released.validation_report.passed
    assert not released.relationships_builds_towards
    assert not released.relationships_relates_to
    assert (
        released.validation_report.input_content_hashes["effective_config"]
        != original.validation_report.input_content_hashes["effective_config"]
    )
    assert (tmp_path / _BUNDLE).read_bytes() == before[_BUNDLE]


@pytest.mark.parametrize(argnames="attack", argvalues=["endpoint", "missing_pair"])
@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_processing_integrity_failures_cannot_become_needs_review(
    attack: str, monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Halt on invalid ambiguous proposals instead of laundering them into abstention.

    Parameters
    ----------
    attack
        Out-of-request endpoint or incomplete response coverage.
    monkeypatch
        Offline model seam; real generation and run-status checks remain active.
    stage
        Producer response or complete checker correction to corrupt.
    tmp_path
        Isolated processing and failure evidence.
    """
    harness = _entry._Harness(root=tmp_path)
    harness.proposals.default_decision = "needs_review"
    harness.proposals.corrections = {}
    original = harness.proposals._call

    def _invalid_call(**kwargs: Any) -> Any:
        """Corrupt one synthetic proposal after an earlier valid ambiguous request.

        Parameters
        ----------
        kwargs
            Actual bounded request and optional producer draft.

        Returns
        -------
        Any
            Untrusted proposal requiring deterministic coverage and endpoint checks.
        """
        output = original(**kwargs)
        current_stage = "draft" if kwargs["draft"] is None else "verdict"
        if kwargs["request"].request_index == 1 and current_stage == stage:
            response = output if stage == "draft" else output.corrected_response
            assert response is not None
            if attack == "missing_pair":
                response.judgments.pop()
            else:
                response.judgments[0].second_sfi_uuid = UUID(int=999999)
        return output

    monkeypatch.setattr(name="_call", target=harness.proposals, value=_invalid_call)
    harness._install(monkeypatch=monkeypatch, real_lp=True)
    with pytest.raises(LPGenerationFailed):
        create_kgs.create(harness.config_path)
    _entry._assert_run(error=LPGenerationFailed, harness=harness)
    requests = _artifacts._rows(harness.root / "lp_generation_requests.jsonl")
    responses = _artifacts._rows(harness.root / "lp_generation_responses.jsonl")
    failures = _json(harness.root / "lp_generation_failures.json")
    assert len(responses) == 1
    assert responses[0]["payload"]["judgments"][0]["decision"] == "needs_review"
    assert len(failures) == 1
    assert failures[0]["stage"] == stage
    assert failures[0]["pair_ids"] == [row["pair_id"] for row in requests[1]["pairs"]]
    assert failures[0]["exhausted"] is True
    assert failures[0]["resolved_run_number"] is None
    assert harness.proposals.calls == (
        [("draft", 0), ("verdict", 0), ("draft", 1)]
        + ([("verdict", 1)] if stage == "verdict" else [])
    )
    assert "compile_as_lc_lp_kg" not in harness.calls
    assert not (harness.root / _BUNDLE).exists()
    assert not (harness.root / "lp_unresolved_items.json").exists()


@pytest.mark.parametrize(
    argnames="claim", argvalues=["pedagogical", "scope", "semantic", "semantic_gate"]
)
@pytest.mark.parametrize(
    argnames="surface", argvalues=["combined", "nested", "standalone"]
)
def test_report_overclaims_are_rejected_even_with_recomputed_content_hashes(
    claim: str, monkeypatch: pytest.MonkeyPatch, surface: str, tmp_path: Path
) -> None:
    """A passing process report cannot be relabeled as an independent semantic pass.

    Parameters
    ----------
    claim
        False pedagogical assertion, scope description, or semantic-gate assertion.
    monkeypatch
        Offline producer/checker seam.
    surface
        Standalone, combined, or nested LP report under attack.
    tmp_path
        Isolated release with test-only tampered evidence.
    """
    harness = _artifacts._mixed(tmp_path)
    _reuse._publish(harness=harness, monkeypatch=monkeypatch)
    path = tmp_path / (
        "lp_validation_report.json" if surface == "standalone" else _BUNDLE
    )
    payload = _json(path)
    report = payload if surface == "standalone" else payload["validation_report"]
    if surface == "nested":
        report = report["lp_validation_report"]
    if claim == "pedagogical":
        report["pedagogical_correctness_established"] = True
    elif claim == "scope":
        report["semantic_scope_notice"] = (
            "Producer/checker agreement proves pedagogical correctness."
        )
    elif claim == "semantic":
        report["semantic_validation_performed"] = True
    else:
        report["validation_checks"].append("independent_semantic_quality_gate")
    if "content_hash" in report:
        report["content_hash"] = _artifacts._hash(
            {key: value for key, value in report.items() if key != "content_hash"}
        )
        with pytest.raises(ValidationError):
            LPValidationReport.model_validate(report)
    path.write_bytes(_artifacts._bytes(payload))
    if surface == "standalone":
        _artifacts._reseal(tmp_path)
    _reuse._assert_rejected(harness=harness)
