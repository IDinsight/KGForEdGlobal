"""Challenge critique validation and retries with wholly synthetic offline requests."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import hashlib
import itertools
import json
import socket

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

# Third Party Library
import pytest

# Package Library
from kgfeg.config import BackendSettings
from kgfeg.evals.lp_eval import judge, prompts, sampling
from kgfeg.evals.lp_eval.schemas import (
    EvaluationSettings,
    JudgeReply,
    JudgeUsage,
    ScheduledRequest,
)
from tests.kgfeg.evals.lp_eval import test_independent_evaluator as _existing


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject sockets even if an injected provider transport is bypassed.

    Parameters
    ----------
    monkeypatch
        Restoring socket-boundary substitutions.
    """
    guard = Mock(side_effect=AssertionError("Offline tests cannot connect."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _request() -> ScheduledRequest:
    """Render a toy rationale control without loading production snapshots.

    Returns
    -------
    ScheduledRequest
        One synthetic critique used only for response and execution unit tests.
    """
    control = next(
        item
        for item in prompts.build_synthetic_controls(
            settings=EvaluationSettings(), source=_existing._source()
        )
        if item.view == "synthetic_critique"
    )
    evidence = sampling._schedule_evidence(base=control, condition="synthetic_control")
    prompt = prompts.render_critique_prompt(evidence=evidence, request_id="a" * 64)
    return ScheduledRequest(
        canonical_endpoint_uuids=control.endpoint_uuids,
        component="controls",
        dependencies=(),
        evidence=evidence,
        material_content_hash=hashlib.sha256(prompt.user_message.encode()).hexdigest(),
        prompt=prompt,
        replicate=1,
        role="control",
    )


def _response(
    *, grounding: str, request: ScheduledRequest, supports: tuple[str, ...]
) -> str:
    """Build explicit claim assessments whose aggregate is independently supplied.

    Parameters
    ----------
    grounding
        Aggregate category under examination.
    request
        Synthetic request containing the allowed identities and references.
    supports
        Ordered support states of the material claims.

    Returns
    -------
    str
        Complete structured critic response.
    """
    response = json.loads(_existing._reply(request.prompt).response_json)
    response["grounding"] = grounding
    response["claims"] = [
        {
            "claim": f"Material claim {index}",
            "evidence_references": [request.prompt.references[0]],
            "explanation": "Explicit synthetic assessment of shown evidence.",
            "support": support,
        }
        for index, support in enumerate(supports)
    ]
    return _existing._dump(response)


def _schedule(request: ScheduledRequest) -> Any:
    """Supply a single-request unit execution plan without asserting snapshot readiness.

    Parameters
    ----------
    request
        Exact synthetic request to execute.

    Returns
    -------
    Any
        Minimal session input for testing the real attempt state machine.
    """
    settings = BackendSettings(
        _env_file=None,
        LEARNING_COMMONS_EXPORT_SCHEMA_VERSION="1.0.0",
        LLM_LP_EVAL_JUDGE_MODEL="anthropic:claude-opus-5",
        PATHS_PROJECT_DIR=Path(__file__).resolve().parents[5],
    )
    return SimpleNamespace(
        curricula=(SimpleNamespace(requests=(request,)),),
        judge=judge.resolve_judge_settings(settings),
        material_content_hash="b" * 64,
        total_requests=1,
    )


@pytest.mark.parametrize(
    argnames="field", argvalues=["judgment_json", "raw_response", "both"]
)
def test_cache_revalidates_grounding_even_with_matching_event_identity(
    field: str,
) -> None:
    """Replaying a previously successful event must validate both response forms.

    Parameters
    ----------
    field
        Cached representation changed while request and attempt identities stay intact.
    """
    request = _request()
    schedule = _schedule(request)
    session = _existing._session(schedule=schedule)
    try:
        attempt = session.start_attempt(request.prompt.request_id)
        valid = _response(
            grounding="grounded", request=request, supports=("supported",)
        )
        session.record_success(
            attempt=attempt, response_json=valid, usage=JudgeUsage(input_tokens=7)
        )
        saved = session.snapshot()
        replay = judge.EvaluationSession(
            connection=session._connection,
            events=saved.events,
            executions=judge._database_executions(
                connection=session._connection,
                schedule_hash=schedule.material_content_hash,
            ),
            schedule=schedule,
        )
        assert replay.snapshot() == saved
        invalid = _response(
            grounding="partially_grounded", request=request, supports=("supported",)
        )
        fields = ("judgment_json", "raw_response") if field == "both" else (field,)
        changed = saved.events[-1].model_copy(update={name: invalid for name in fields})
        with pytest.raises(ValueError, match="partially grounded"):
            judge.EvaluationSession(
                connection=session._connection,
                events=(*saved.events[:-1], changed),
                executions=judge._database_executions(
                    connection=session._connection,
                    schedule_hash=schedule.material_content_hash,
                ),
                schedule=schedule,
            )
        assert session.snapshot() == saved
    finally:
        session._connection.close()


@pytest.mark.parametrize(
    argnames="grounding",
    argvalues=["grounded", "partially_grounded", "unsupported", "ambiguous"],
)
@pytest.mark.parametrize(
    argnames="supports",
    argvalues=[
        values
        for count in range(1, 5)
        for values in itertools.combinations(
            ("supported", "unsupported", "contradicted", "unresolved"), count
        )
    ]
    + [("supported", "supported"), ("unsupported", "unsupported")],
)
def test_grounding_claim_support_matrix(
    grounding: str, supports: tuple[str, ...]
) -> None:
    """Keep logical consistency separate from the critic's centrality and ambiguity judgment.

    Parameters
    ----------
    grounding
        Proposed aggregate grounding label.
    supports
        Complete synthetic material-claim support list.
    """
    request = _request()
    supported = supports.count("supported")
    permitted = {
        "grounded": supported == len(supports),
        "partially_grounded": 0 < supported < len(supports),
        "unsupported": supported == 0 or "contradicted" in supports,
        "ambiguous": True,
    }[grounding]
    response = _response(grounding=grounding, request=request, supports=supports)
    if permitted:
        result = judge.validate_judge_response(request=request, response_json=response)
        assert result.grounding == grounding
        assert tuple(claim.support for claim in result.claims) == supports
    else:
        with pytest.raises(ValueError):
            judge.validate_judge_response(request=request, response_json=response)


@pytest.mark.parametrize(argnames="invalid_attempts", argvalues=[1, 2, 3])
def test_grounding_validation_retries_share_total_allowance(
    invalid_attempts: int,
) -> None:
    """Count invalid aggregate responses as attempts; preserve valid ambiguity on repair.

    Parameters
    ----------
    invalid_attempts
        Invalid responses returned before a valid ambiguous response, or exhaustion.
    """
    request = _request()
    schedule = _schedule(request)
    session = _existing._session(schedule=schedule)
    calls: list[str] = []
    waits: list[float] = []

    class _Transport:
        """Return structured critic responses without a provider or network client."""

        settings = schedule.judge

        async def judge(self, prompt: Any) -> JudgeReply:
            """Return the next controlled response and known attempt usage.

            Parameters
            ----------
            prompt
                Rendered request whose identity remains unchanged across retries.

            Returns
            -------
            JudgeReply
                Deliberately inconsistent grounding or valid ambiguity.
            """
            calls.append(prompt.request_id)
            invalid = len(calls) <= invalid_attempts
            return JudgeReply(
                response_json=_response(
                    grounding="partially_grounded" if invalid else "ambiguous",
                    request=request,
                    supports=("supported",) if invalid else ("unresolved",),
                ),
                usage=JudgeUsage(input_tokens=11, output_tokens=5),
            )

    async def _sleep(delay: float) -> None:
        """Record retry waits without sleeping.

        Parameters
        ----------
        delay
            Requested wait in seconds.
        """
        waits.append(delay)

    try:
        execution = judge._Execution(
            check_material=lambda: None,
            session=session,
            sleep=_sleep,
            transport=_Transport(),
        )
        if invalid_attempts == 3:
            with pytest.raises(judge.JudgeExecutionError):
                asyncio.run(execution.run())
        else:
            asyncio.run(execution.run())
        assert len(calls) == min(invalid_attempts + 1, 3)
        assert waits == ([5] if invalid_attempts == 1 else [5, 20])
        cache = session.snapshot()
        failed = [event for event in cache.events if event.event == "failed"]
        terminal = [event for event in cache.events if event.event != "started"]
        assert [event.failure_category for event in failed] == [
            "invalid_output"
        ] * invalid_attempts
        assert sum(event.usage.input_tokens for event in terminal) == 11 * len(calls)
        assert sum(event.usage.output_tokens for event in terminal) == 5 * len(calls)
        assert not cache.unfinished
        if invalid_attempts == 3:
            assert not cache.judgments
            with pytest.raises(ValueError):
                session.start_attempt(request.prompt.request_id)
        else:
            assert len(cache.judgments) == 1
            assert cache.judgments[0].grounding == "ambiguous"
            replay = judge.EvaluationSession(
                connection=session._connection,
                events=cache.events,
                executions=judge._database_executions(
                    connection=session._connection,
                    schedule_hash=schedule.material_content_hash,
                ),
                schedule=schedule,
            )
            asyncio.run(
                judge._Execution(
                    check_material=lambda: None,
                    session=replay,
                    sleep=_sleep,
                    transport=_Transport(),
                ).run()
            )
            assert replay.snapshot() == cache
            assert len(calls) == invalid_attempts + 1
    finally:
        session._connection.close()


@pytest.mark.parametrize(
    argnames="model", argvalues=["openai:gpt-5.2", "anthropic:claude-sonnet-4-6"]
)
def test_judge_environment_override_preserves_production_binding(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve explicit environment overrides while production retains its own model.

    Parameters
    ----------
    model
        Dedicated evaluator environment override.
    monkeypatch
        Restoring synthetic environment changes.
    """
    monkeypatch.setenv(name="LLM_LP_EVAL_JUDGE_MODEL", value=model)
    settings = BackendSettings(
        _env_file=None,
        LEARNING_COMMONS_EXPORT_SCHEMA_VERSION="1.0.0",
        LLM_KG_MODEL="openai:gpt-5.2",
        PATHS_PROJECT_DIR=Path.cwd(),
    )
    resolved = judge.resolve_judge_settings(settings)
    assert resolved.model == model
    judge.validate_judge_settings(resolved)
    assert settings.llm_config("kgs").model == "openai:gpt-5.2"


@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
def test_unavailable_judge_preflight_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Reject unavailable configured models at the actual SDK metadata boundary.

    Parameters
    ----------
    monkeypatch
        Restoring synthetic credentials and the socket-free HTTP transport.
    provider
        Provider whose configured model is reported unavailable.
    """
    # Third Party Library
    import httpx

    model = "anthropic:claude-opus-5" if provider == "anthropic" else "openai:gpt-5.2"
    settings = BackendSettings(
        _env_file=None,
        LEARNING_COMMONS_EXPORT_SCHEMA_VERSION="1.0.0",
        LLM_LP_EVAL_JUDGE_MODEL=model,
        PATHS_PROJECT_DIR=Path.cwd(),
    )
    resolved = judge.resolve_judge_settings(settings)
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv(
        name="ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY",
        value="offline-synthetic-key",
    )

    async def _handler(request: Any) -> Any:
        """Return model unavailability without contacting a provider.

        Parameters
        ----------
        request
            Request constructed by the actual provider SDK.

        Returns
        -------
        Any
            Local metadata response with no available model.
        """
        calls.append((request.method, request.url.path))
        return httpx.Response(
            status_code=404,
            json={"error": {"message": "Model unavailable", "type": "not_found_error"}},
        )

    def _transport(**kwargs: Any) -> Any:
        """Require disabled transport retries and return a local handler.

        Parameters
        ----------
        kwargs
            Transport construction settings.

        Returns
        -------
        Any
            HTTP transport that never opens sockets.
        """
        assert kwargs["retries"] == 0
        return httpx.MockTransport(handler=_handler)

    async def _exercise() -> None:
        """Perform only the configured model's metadata preflight."""
        async with judge.open_judge_transport(resolved) as transport:
            with pytest.raises(judge.JudgeCallError) as failure:
                await transport.preflight()
            assert failure.value.category == "configuration"
            assert transport.settings.model == model

    monkeypatch.setattr(name="AsyncHTTPTransport", target=httpx, value=_transport)
    asyncio.run(_exercise())
    assert len(calls) == 1
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/models/" + model.partition(":")[2])
