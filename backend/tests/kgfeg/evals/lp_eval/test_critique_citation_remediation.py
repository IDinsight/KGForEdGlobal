"""Challenge historical-policy citations without live evidence or provider calls."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

# Package Library
from kgfeg.evals.lp_eval import judge, prompts, sampling
from kgfeg.evals.lp_eval.schemas import (
    EvaluationPair,
    EvaluationSettings,
    FrozenInputs,
    JudgeUsage,
    ScheduledRequest,
)
from kgfeg.kgs.lp_requests import LPGenerationRequest
from tests.kgfeg.evals.lp_eval import test_independent_evaluator as _existing

# Private fixtures are used parameters, not intentionally ignored arguments.
# pylint: disable=useless-param-doc

# Reuse only reduced synthetic input factories and the socket/result-read guards.
_frozen = _existing._frozen
_offline = _existing._offline
_results_are_not_test_inputs = _existing._results_are_not_test_inputs
_schedule = _existing._schedule
_POLICIES = ("original_checker_system_message", "original_producer_system_message")


def _freeze(*, base: Any, payload: dict[str, Any]) -> Any:
    """Exercise the direct production-view boundary with independently changed policy.

    Parameters
    ----------
    base
        Reduced original production critique.
    payload
        Synthetic historical material, including malformed policy edge cases.

    Returns
    -------
    Any
        Freshly constructed and material-bound direct critique view.
    """
    return sampling._production_freeze_view(
        audit=json.loads(base.audit_json),
        builder=SimpleNamespace(_input_content_hash=base.input_content_hash),
        pair=EvaluationPair(endpoint_uuids=base.endpoint_uuids, pair_id=base.pair_id),
        payload=payload,
        request=LPGenerationRequest.model_validate(payload["original_request"]),
        view="original_production_critique",
    )


def _request(*, scheduled: bool, view: Any) -> ScheduledRequest:
    """Render either public critique path and wrap its response-validation boundary.

    Parameters
    ----------
    scheduled
        Whether to regenerate references through schedule construction.
    view
        Direct evidence view under examination.

    Returns
    -------
    ScheduledRequest
        Isolated response-validation input, never an executable full invocation.
    """
    evidence = (
        sampling._schedule_evidence(base=view, condition="original_production_critique")
        if scheduled
        else view
    )
    prompt = prompts.render_critique_prompt(evidence=evidence, request_id="a" * 64)
    return ScheduledRequest(
        canonical_endpoint_uuids=view.endpoint_uuids,
        component="production",
        dependencies=(),
        evidence=evidence,
        material_content_hash=hashlib.sha256(prompt.user_message.encode()).hexdigest(),
        prompt=prompt,
        replicate=1,
        role="critique",
    )


def _response(*, reference: str, request: ScheduledRequest, support: str) -> str:
    """Supply an explicit policy claim, without deriving its truth from production.

    Parameters
    ----------
    reference
        Exact or intentionally malformed citation.
    request
        Opaque identity to preserve in the fake response.
    support
        Independently selected policy assessment.

    Returns
    -------
    str
        Complete critic response with a consistent aggregate category.
    """
    response = json.loads(_existing._reply(request.prompt).response_json)
    response.update(
        claims=[
            {
                "claim": "The historical policy permits same-rank development.",
                "evidence_references": [reference],
                "explanation": "Assess the shown policy text only.",
                "support": support,
            }
        ],
        grounding="grounded" if support == "supported" else "unsupported",
    )
    return _existing._dump(response)


@pytest.fixture(scope="module")
def _views(_frozen: FrozenInputs) -> Any:
    """Build original production views from a tiny offline generated snapshot.

    Parameters
    ----------
    _frozen
        Independently reduced synthetic curriculum material.

    Returns
    -------
    Any
        Paired blind and original-production critique views.
    """
    snapshot = sampling.load_frozen_lp_inputs(_frozen)[0]
    population = sampling.build_admissible_population(
        sampling.upstream_evidence_source(snapshot)
    )
    production = sampling.build_production_population(
        population=population, snapshot=snapshot
    )
    return sampling.build_production_evidence_builder(snapshot).build_pair(
        production.pairs[0].pair
    )


def test_classification_does_not_consult_critique_policy_references(
    _views: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep blind content, permissions and response validation independent of critique.

    Parameters
    ----------
    _views
        Reduced factual blind and original critique evidence.
    monkeypatch
        Restoring policy-reference sentinel.
    """
    evidence = sampling._schedule_evidence(
        base=_views.blind, condition="exact_production"
    )
    before = prompts.render_classification_prompt(
        evidence=evidence, request_id="a" * 64
    )
    guard = Mock(side_effect=AssertionError("Classification consulted critique policy"))
    monkeypatch.setattr(name="critique_policy_references", target=prompts, value=guard)
    monkeypatch.setattr(name="critique_policy_references", target=sampling, value=guard)
    after_evidence = sampling._schedule_evidence(
        base=_views.blind, condition="exact_production"
    )
    after = prompts.render_classification_prompt(
        evidence=after_evidence, request_id="a" * 64
    )
    assert after == before
    guard.assert_not_called()
    shown = json.loads(after.user_message)
    assert not any(
        field in shown["evidence"] for field in (*_POLICIES, "operative_judgment")
    )
    request = ScheduledRequest(
        canonical_endpoint_uuids=evidence.endpoint_uuids,
        component="production",
        dependencies=(),
        evidence=evidence,
        material_content_hash="b" * 64,
        prompt=after,
        replicate=1,
        role="base",
    )
    for permission in shown["assessment_permissions"]:
        response = json.loads(_existing._reply(after).response_json)
        response.update(permission)
        judgment = judge.validate_judge_response(
            request=request, response_json=_existing._dump(response)
        )
        assert judgment.decision == permission["decision"]
        assert judgment.direction == permission["direction"]
    response["evidence_references"] = ["/original_producer_system_message"]
    with pytest.raises(ValueError, match="permitted shown references"):
        judge.validate_judge_response(
            request=request, response_json=_existing._dump(response)
        )


@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=["references", "prompt", "prompts_source", "sampling_source"],
)
def test_incompatible_material_preserves_ledger_and_exhausted_attempts(  # pylint: disable=too-many-statements
    _schedule: Any,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    """Reject old schedules before cache access and retain all failed-attempt evidence.

    Parameters
    ----------
    _schedule
        Complete synthetic schedule with actual source and input identities.
    monkeypatch
        Restoring in-memory source or rendering substitutions.
    mutation
        Material change made without editing executable files.
    """
    repository = _schedule.inputs.manifest_path.parents[4]
    store = judge.persist_evaluation_schedule(
        repository_root=repository, schedule=_schedule
    )
    with judge.open_evaluation_store(store) as session:
        requests = [
            r for c in _schedule.curricula for r in c.requests if r.role == "critique"
        ]
        request = requests[
            ["references", "prompt", "prompts_source", "sampling_source"].index(
                mutation
            )
        ]
        by_id = {
            r.prompt.request_id: r for c in _schedule.curricula for r in c.requests
        }
        assert request.dependencies
        for identifier in request.dependencies:
            attempt = session.start_attempt(identifier)
            reply = _existing._reply(by_id[identifier].prompt)
            session.record_success(
                attempt=attempt, response_json=reply.response_json, usage=reply.usage
            )
        for _ in range(3):
            attempt = session.start_attempt(request.prompt.request_id)
            session.record_failure(
                attempt=attempt,
                category="invalid_output",
                message="Synthetic bad citation",
                raw_response="noncanonical policy citation",
                usage=JudgeUsage(input_tokens=7),
            )
        saved = session.snapshot()
    directory = store.manifest_path.parent
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with monkeypatch.context() as changed:
        if mutation == "references":
            changed.setattr(
                name="critique_policy_references",
                target=sampling,
                value=lambda payload: (),
            )
        elif mutation == "prompt":
            changed.setattr(
                name="_CRITIQUE_INSTRUCTIONS",
                target=prompts,
                value=prompts._CRITIQUE_INSTRUCTIONS
                + "\nChanged citation instruction.",
            )
        else:
            filename = "prompts.py" if mutation == "prompts_source" else "sampling.py"
            original_read = Path.read_bytes

            def _source_edit(path: Path) -> bytes:
                """Return changed source bytes while preserving frozen inputs.

                Parameters
                ----------
                path
                    Requested source or artifact.

                Returns
                -------
                bytes
                    Harmless source edit or original artifact bytes.
                """
                payload = original_read(path)
                return (
                    payload + b"\n# Source revision\n"
                    if path.name == filename
                    else payload
                )

            changed.setattr(name="read_bytes", target=Path, value=_source_edit)
        if mutation in {"prompts_source", "sampling_source"}:
            assert (
                sampling.prepare_evaluation_schedule(
                    inputs=_schedule.inputs,
                    judge=_schedule.judge,
                    settings=_schedule.settings,
                )
                == _schedule
            )
        else:
            fresh = sampling.prepare_evaluation_schedule(
                inputs=_schedule.inputs,
                judge=_schedule.judge,
                settings=_schedule.settings,
            )
            old_critic = next(
                r
                for c in _schedule.curricula
                for r in c.requests
                if r.role == "critique"
            )
            new_critic = next(
                r for c in fresh.curricula for r in c.requests if r.role == "critique"
            )
            assert fresh.material_content_hash != _schedule.material_content_hash
            assert new_critic.material_content_hash != old_critic.material_content_hash
            assert (
                new_critic.prompt.material_content_hash
                != old_critic.prompt.material_content_hash
            )
            if mutation != "prompt":
                assert new_critic.prompt.request_id != old_critic.prompt.request_id
        if mutation in {"prompts_source", "sampling_source"}:
            with judge.open_evaluation_store(store) as unchanged:
                assert unchanged.snapshot() == saved
        else:
            with pytest.raises(ValueError, match="reproduced"):
                with judge.open_evaluation_store(store):
                    pytest.fail("Incompatible schedule exposed the writable cache")
        assert {
            p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()
        } == before
    with judge.open_evaluation_store(store) as session:
        assert session.snapshot() == saved
        recovered = session.start_attempt(request.prompt.request_id)
        assert recovered.attempt_number == 4
        assert recovered.execution_number == len(saved.executions) + 1
        assert session.snapshot().events[: len(saved.events)] == saved.events


@pytest.mark.parametrize(
    argnames="reference",
    argvalues=[
        "original_producer_system_message",
        "producer policy permits this",
        "/original_producer_system_message (policy)",
        "/original_checker_system_message: same rank",
        "/original_checker_system_message/0",
        "/original_producer_system_message/",
        " /original_producer_system_message",
        "/original_checker_system_message ",
        "/original_producer_system_messag~2e",
        "/invented_source",
        "/operative_judgment",
        "/operative_judgment/rationale",
        "/target_pair_id",
        "/independent_classifier_answer",
        "/control_expectation",
        "",
    ],
)
@pytest.mark.parametrize(argnames="scheduled", argvalues=[False, True])
def test_invalid_citations_fail_response_and_prompt_guards(
    _views: Any,
    reference: str,
    scheduled: bool,
) -> None:
    """Reject descriptions, invented sources, hidden answers and rationale self-citation.

    Parameters
    ----------
    _views
        Reduced original production views.
    reference
        Noncanonical or prohibited citation, including existing non-evidence fields.
    scheduled
        Direct or fully scheduled rendering path.
    """
    request = _request(scheduled=scheduled, view=_views.critique)
    response = _response(reference=reference, request=request, support="supported")
    with pytest.raises(ValueError):
        judge.validate_judge_response(request=request, response_json=response)
    forged = replace(
        request.evidence, references=(*request.evidence.references, reference)
    )
    with pytest.raises(ValueError):
        prompts.render_critique_prompt(evidence=forged, request_id="a" * 64)


@pytest.mark.parametrize(argnames="field", argvalues=_POLICIES)
def test_policy_material_changes_bind_views_and_prompts(
    _views: Any, field: str
) -> None:
    """Bind changed historical text even when its exact citation pointer is unchanged.

    Parameters
    ----------
    _views
        Reduced original production evidence.
    field
        Historical policy field changed only in synthetic memory.
    """
    payload = json.loads(_views.critique.payload_json)
    payload[field] += "\nAdditional synthetic policy restriction."
    changed = _freeze(base=_views.critique, payload=payload)
    assert changed.references == _views.critique.references
    assert changed.payload_sha256 != _views.critique.payload_sha256
    assert changed.material_content_hash != _views.critique.material_content_hash
    for scheduled in (False, True):
        original = _request(scheduled=scheduled, view=_views.critique)
        updated = _request(scheduled=scheduled, view=changed)
        assert (
            updated.evidence.material_content_hash
            != original.evidence.material_content_hash
        )
        assert (
            updated.prompt.messages_content_hash
            != original.prompt.messages_content_hash
        )
        assert (
            updated.prompt.material_content_hash
            != original.prompt.material_content_hash
        )


@pytest.mark.parametrize(argnames="field", argvalues=_POLICIES)
@pytest.mark.parametrize(argnames="scheduled", argvalues=[False, True])
@pytest.mark.parametrize(
    argnames="value",
    argvalues=[
        None,
        "",
        " \n\t",
        0,
        True,
        [],
        {"text": "policy"},
        "missing",
        "  Policy π allows same-rank development.  ",
    ],
)
def test_policy_presence_is_identical_in_construction_schedule_and_guard(
    _views: Any,
    field: str,
    scheduled: bool,
    value: Any,
) -> None:
    """Permit exactly nonblank string policy fields and preserve historical bytes.

    Parameters
    ----------
    _views
        Reduced original production views.
    field
        Historical producer or checker policy location.
    scheduled
        Direct or scheduled critique path.
    value
        Present, absent, blank or incorrectly typed policy material.
    """
    payload = json.loads(_views.critique.payload_json)
    if value == "missing":
        del payload[field]
    else:
        payload[field] = value
    view = _freeze(base=_views.critique, payload=payload)
    request = _request(scheduled=scheduled, view=view)
    expected = {
        "/" + name
        for name in _POLICIES
        if isinstance(payload.get(name), str) and payload[name].strip()
    }
    actual = {
        ref
        for ref in request.prompt.references
        if not ref.startswith("/original_request/")
    }
    assert actual == expected
    assert json.loads(request.prompt.user_message)["evidence"] == payload
    reference = "/" + field
    if reference not in expected:
        with pytest.raises(ValueError):
            judge.validate_judge_response(
                request=request,
                response_json=_response(
                    reference=reference, request=request, support="supported"
                ),
            )
        with pytest.raises(ValueError):
            prompts.render_critique_prompt(
                evidence=replace(
                    request.evidence,
                    references=(*request.evidence.references, reference),
                ),
                request_id="a" * 64,
            )


@pytest.mark.parametrize(argnames="field", argvalues=_POLICIES)
@pytest.mark.parametrize(argnames="scheduled", argvalues=[False, True])
@pytest.mark.parametrize(argnames="support", argvalues=["supported", "contradicted"])
def test_policy_support_uses_exact_citations_and_separate_grounding(
    _views: Any,
    field: str,
    scheduled: bool,
    support: str,
) -> None:
    """Validate policy claims while retaining exact strings and the critic-only schema.

    Parameters
    ----------
    _views
        Reduced original production views.
    field
        Historical policy field whose exact pointer is valid.
    scheduled
        Direct or scheduled rendering path.
    support
        Explicit claim support or contradiction supplied by the synthetic critic.
    """
    request = _request(scheduled=scheduled, view=_views.critique)
    response = _response(reference="/" + field, request=request, support=support)
    judgment = judge.validate_judge_response(request=request, response_json=response)
    assert judgment.claims[0].evidence_references == ("/" + field,)
    assert judgment.grounding == (
        "grounded" if support == "supported" else "unsupported"
    )
    assert (
        "Copy citation strings exactly from permitted_evidence_references"
        in request.prompt.system_message
    )
    assert "Do not append" in request.prompt.system_message
    assert "cannot by itself establish factual" in request.prompt.system_message
    assert "relationship support between standards" in request.prompt.system_message
    changed = json.loads(response)
    changed["decision"] = "relatesTo"
    with pytest.raises(ValueError):
        judge.validate_judge_response(
            request=request, response_json=_existing._dump(changed)
        )


def test_prompt_hides_audit_answers_and_control_expectations(_views: Any) -> None:
    """Keep audit-only classifier answers and explicit synthetic oracles out of prompts.

    Parameters
    ----------
    _views
        Reduced original production views.
    """
    sentinel = "SECRET_INDEPENDENT_ANSWER_AND_CONTROL_EXPECTATION"
    view = replace(
        _views.critique, audit_json=_existing._dump({"classifier_answer": sentinel})
    )
    for scheduled in (False, True):
        request = _request(scheduled=scheduled, view=view)
        assert sentinel not in request.prompt.user_message
        assert sentinel not in request.prompt.system_message
        assert not any(
            ref.startswith("/operative_judgment") for ref in request.prompt.references
        )
    controls = prompts.build_synthetic_controls(
        settings=EvaluationSettings(),
        source=_existing._source(),
    )
    control = replace(
        next(c for c in controls if c.view == "synthetic_critique"),
        expectation_json=_existing._dump({"answer": sentinel}),
    )
    for scheduled in (False, True):
        request = _request(scheduled=scheduled, view=control)
        assert sentinel not in request.prompt.user_message
        assert all(
            ref.startswith("/original_request/") for ref in request.prompt.references
        )
