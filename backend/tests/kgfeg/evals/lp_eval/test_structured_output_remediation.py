"""Challenge structured judgments through real SDKs and socket-free HTTP envelopes."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import copy
import hashlib
import json
import socket
import sqlite3

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

# Third Party Library
import httpx
import pytest

# Package Library
from kgfeg.config import BackendSettings
from kgfeg.evals.lp_eval import judge, prompts, sampling
from kgfeg.evals.lp_eval.schemas import EvaluationSettings, ScheduledRequest
from tests.kgfeg.evals.lp_eval import test_independent_evaluator as _existing


def _assert_http_failure(
    *,
    attempts: int,
    body: bytes,
    category: str,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    status: int,
    task: str,
    tmp_path: Path,
) -> None:
    """Challenge HTTP decoding, accounting and durable exhaustion through real SDKs.

    Parameters
    ----------
    attempts
        Required number of outer attempts and actual HTTP requests.
    body
        Malformed or non-object response bytes, possibly containing private canaries.
    category
        Expected failure category derived from response status and output validity.
    monkeypatch
        Restoring offline HTTP transport.
    provider
        Actual SDK adapter.
    status
        HTTP success or error code.
    task
        Scheduled classification or critique.
    tmp_path
        Isolated test ledger directory.
    """
    calls: list[bytes] = []
    waits: list[float] = []
    request = _request(task)
    settings = _settings(provider)
    schedule = SimpleNamespace(
        curricula=(SimpleNamespace(requests=(request,)),),
        judge=settings,
        material_content_hash="b" * 64,
        total_requests=1,
    )
    ledger = tmp_path / "attempts.sqlite3"
    session = _existing._session(path=str(ledger), schedule=schedule)

    async def _exercise(current: Any) -> None:
        """Require finite retries through the durable execution loop.

        Parameters
        ----------
        current
            Newly created or reopened isolated session.
        """
        async with judge.open_judge_transport(settings) as transport:
            with pytest.raises(judge.JudgeExecutionError):
                await judge._Execution(
                    check_material=lambda: None,
                    session=current,
                    sleep=_sleep,
                    transport=transport,
                ).run()

    async def _handler(wire: httpx.Request) -> httpx.Response:
        """Return invalid HTTP material without opening a network socket.

        Parameters
        ----------
        wire
            Actual SDK request.

        Returns
        -------
        httpx.Response
            Synthetic response with no usable accounting body.
        """
        assert wire.method == "POST"
        assert wire.extensions["timeout"]["read"] == 180
        calls.append(wire.content)
        return httpx.Response(
            content=body,
            headers={
                "content-type": "application/json",
                (
                    "request-id" if provider == "anthropic" else "x-request-id"
                ): f"offline-{len(calls)}",
            },
            status_code=status,
        )

    async def _sleep(delay: float) -> None:
        """Record permitted waits without wall-clock delay.

        Parameters
        ----------
        delay
            Prescribed retry delay.
        """
        waits.append(delay)

    _http(handler=_handler, monkeypatch=monkeypatch)
    try:
        asyncio.run(_exercise(session))
        saved = session.snapshot()
        failures = [event for event in saved.events if event.event == "failed"]
        assert not saved.judgments
        assert len(saved.events) == attempts * 2
        assert [event.attempt_number for event in failures] == list(
            range(1, attempts + 1)
        )
        assert [event.failure_category for event in failures] == [category] * attempts
        for number, event in enumerate(failures, start=1):
            assert event.usage is not None
            assert event.usage.provider_request_id == f"offline-{number}"
            assert all(
                value is None
                for key, value in event.usage.model_dump().items()
                if key != "provider_request_id"
            )
            assert event.raw_response is None
            assert event.judgment_json is None
            assert event.failure_message
            assert "PRIVATE_BODY" not in event.model_dump_json()
    finally:
        session._connection.close()

    # Reopen the physical ledger: terminal history must survive connection lifetime.
    before = ledger.read_bytes()
    connection = sqlite3.connect(ledger)
    try:
        events = judge._database_events(
            connection=connection, schedule_hash=schedule.material_content_hash
        )
        replay = judge.EvaluationSession(
            connection=connection, events=events, schedule=schedule
        )
        assert replay.snapshot() == saved
        asyncio.run(_exercise(replay))
        assert replay.snapshot() == saved
    finally:
        connection.close()
    assert ledger.read_bytes() == before
    assert len(calls) == attempts
    assert all(call == calls[0] for call in calls)
    assert waits == ([5, 20] if attempts == 3 else [])
    assert judge._ATTEMPT_OUTPUT.get() is None
    assert judge._ATTEMPT_USAGE.get() is None


def _envelope(*, provider: str, raw: str, task: str) -> dict[str, Any]:
    """Construct explicit provider wire output with known accounting.

    Parameters
    ----------
    provider
        Provider wire format.
    raw
        Exact judgment text or function arguments.
    task
        Classification or critique output tool.

    Returns
    -------
    dict
        Synthetic successful response, including private metadata canaries.
    """
    usage = {"input_tokens": 17, "output_tokens": 9}
    if provider == "anthropic":
        return {
            "id": "msg_offline",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "content": [
                {
                    "type": "thinking",
                    "thinking": "PRIVATE_THINKING",
                    "signature": "PRIVATE_SIGNATURE",
                },
                {"type": "text", "text": raw},
            ],
            "usage": {
                **usage,
                "cache_read_input_tokens": 4,
                "cache_creation_input_tokens": 2,
            },
            "metadata": {"secret": "PRIVATE_METADATA"},
        }
    return {
        "id": "resp_offline",
        "object": "response",
        "created_at": 1,
        "model": "gpt-5.2",
        "status": "completed",
        "incomplete_details": None,
        "output": [
            {
                "id": "reasoning_offline",
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "PRIVATE_THINKING"}],
            },
            {
                "id": "fc_offline",
                "type": "function_call",
                "call_id": "call_offline",
                "name": (
                    "ClassificationJudgment"
                    if task == "classification"
                    else "CritiqueJudgment"
                ),
                "arguments": raw,
                "status": "completed",
            },
        ],
        "usage": {
            **usage,
            "total_tokens": 26,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens_details": {"reasoning_tokens": 3},
        },
        "metadata": {"secret": "PRIVATE_METADATA"},
    }


def _http(*, handler: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a mock at the network transport, retaining SDK request construction.

    Parameters
    ----------
    handler
        Local HTTP responder.
    monkeypatch
        Restoring credentials and transport substitutions.
    """
    monkeypatch.setenv(name="ANTHROPIC_API_KEY", value="offline-synthetic-key")
    monkeypatch.setenv(name="OPENAI_API_KEY", value="offline-synthetic-key")

    def _transport(**kwargs: Any) -> httpx.MockTransport:
        """Assert no transport retries and return the local HTTP responder.

        Parameters
        ----------
        kwargs
            HTTP transport options.

        Returns
        -------
        httpx.MockTransport
            Socket-free transport.
        """
        assert kwargs["retries"] == 0
        return httpx.MockTransport(handler=handler)

    monkeypatch.setattr(name="AsyncHTTPTransport", target=httpx, value=_transport)


def _invalid_envelope(
    *, mutation: str, provider: str, raw: str, task: str
) -> dict[str, Any]:
    """Inject one provider-envelope defect without normalizing away extra outputs.

    Parameters
    ----------
    mutation
        Output count, completion or SDK-decoding defect.
    provider
        Provider response shape.
    raw
        Exact structured judgment string.
    task
        Classification or critique.

    Returns
    -------
    dict
        Original synthetic provider envelope.
    """
    envelope = _envelope(provider=provider, raw=raw, task=task)
    parts = envelope["content" if provider == "anthropic" else "output"]
    if mutation.startswith("extra_"):
        parts.append(
            {
                "type": {
                    "extra_custom": "custom_tool_call",
                    "extra_computer": "computer_call",
                    "extra_shell": "local_shell_call",
                }[mutation],
                "id": "extra",
                "call_id": "extra",
                "name": "unexpected",
                "input": "UNEXPECTED_ARGUMENTS",
                "action": {"type": "click", "x": 1, "y": 2},
            }
        )
    elif mutation == "multiple":
        parts.append(copy.deepcopy(parts[-1]))
    elif mutation == "unexpected":
        parts.append({"type": "unrecognized_part", "text": "UNEXPECTED_ARGUMENTS"})
    elif mutation == "incomplete_item":
        parts[-1]["status"] = "incomplete"
    elif mutation == "truncated":
        envelope["stop_reason" if provider == "anthropic" else "status"] = (
            "max_tokens" if provider == "anthropic" else "incomplete"
        )
    elif mutation == "sdk_decode":
        if provider == "anthropic":
            envelope["container"] = 42
        else:
            envelope["created_at"] = "PRIVATE_INVALID_TIMESTAMP"
    return envelope


def _invalid_judgment(*, mutation: str, request: ScheduledRequest) -> str:
    """Construct one explicit judgment defect while retaining all other fields.

    Parameters
    ----------
    mutation
        Judgment identity, syntax or semantic defect.
    request
        Synthetic scheduled task.

    Returns
    -------
    str
        Exact provider text or tool arguments.
    """
    task = request.prompt.task
    value = json.loads(_existing._reply(request.prompt).response_json)
    if mutation == "missing_identity":
        del value["request_id"]
    elif mutation == "wrong_identity":
        value["request_id"] = "wrong-secret-identity"
    elif mutation == "invalid_citation":
        if task == "classification":
            value["evidence_references"] = ["/PRIVATE_CITATION"]
        else:
            value["claims"][0]["evidence_references"] = ["/PRIVATE_CITATION"]
    elif mutation == "inconsistent_semantics":
        if task == "classification":
            value["direction"] = "first_to_second"
        else:
            value["grounding"] = "grounded"
    raw = _existing._dump(value)
    if mutation == "duplicate_key":
        raw = raw[:-1] + ',"request_id":"duplicate-secret"}'
    elif mutation == "malformed":
        raw = '{"private_invalid_text":'
    return raw


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent any accidental network connection.

    Parameters
    ----------
    monkeypatch
        Restoring socket substitutions.
    """
    guard = Mock(side_effect=AssertionError("Offline tests cannot connect."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _request(task: str) -> ScheduledRequest:
    """Render a synthetic control without reading any generated result directory.

    Parameters
    ----------
    task
        Classification or critique.

    Returns
    -------
    ScheduledRequest
        Toy control for transport and scheduled-validation tests.
    """
    view = "synthetic_critique" if task == "critique" else "synthetic_classification"
    control = next(
        item
        for item in prompts.build_synthetic_controls(
            settings=EvaluationSettings(), source=_existing._source()
        )
        if item.view == view
    )
    evidence = sampling._schedule_evidence(base=control, condition="synthetic_control")
    render = (
        prompts.render_critique_prompt
        if task == "critique"
        else prompts.render_classification_prompt
    )
    prompt = render(evidence=evidence, request_id="a" * 64)
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


def _settings(provider: str) -> Any:
    """Resolve explicit synthetic settings independently of local secrets.

    Parameters
    ----------
    provider
        Provider under test.

    Returns
    -------
    Any
        Frozen model and output configuration.
    """
    return judge.resolve_judge_settings(
        BackendSettings(
            _env_file=None,
            LEARNING_COMMONS_EXPORT_SCHEMA_VERSION="1.0.0",
            LLM_LP_EVAL_JUDGE_MODEL=(
                "anthropic:claude-opus-5"
                if provider == "anthropic"
                else "openai:gpt-5.2"
            ),
            PATHS_PROJECT_DIR=Path(__file__).resolve().parents[5],
        )
    )


@pytest.mark.parametrize(
    argnames="arrival_order",
    argvalues=[(0, 1, 2, 3), (3, 2, 1, 0), (1, 3, 0, 2)],
)
@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
def test_concurrent_http_attempts_isolate_raw_output_and_usage(
    arrival_order: tuple[int, ...], monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Keep overlapping SDK response evidence bound to its submitted request.

    Parameters
    ----------
    arrival_order
        Controlled HTTP arrival order, independent of task submission order.
    monkeypatch
        Restoring mocked network transport.
    provider
        Actual SDK adapter.
    """
    request = _request("classification")
    attempt_prompts = [
        prompts.render_classification_prompt(
            evidence=request.evidence,
            request_id=hashlib.sha256(
                f"concurrent-attempt-{index}".encode()
            ).hexdigest(),
        )
        for index in range(4)
    ]
    replies: list[Any] = []
    calls: list[int] = []

    async def _exercise() -> None:
        """Control request arrival while retaining four overlapping HTTP calls."""
        barrier = asyncio.Event()
        gates = [asyncio.Event() for _ in range(4)]
        gates[arrival_order[0]].set()

        async def _handler(wire: httpx.Request) -> httpx.Response:
            """Bind distinct counters and output to the identity in the HTTP body.

            Parameters
            ----------
            wire
                Actual SDK request.

            Returns
            -------
            httpx.Response
                Valid or extra-output envelope with unique raw evidence.
            """
            matches = [
                index
                for index, prompt in enumerate(attempt_prompts)
                if prompt.request_id.encode() in wire.content
            ]
            assert len(matches) == 1
            index = matches[0]
            assert index == arrival_order[len(calls)]
            calls.append(index)
            if len(calls) == 4:
                barrier.set()
            else:
                gates[arrival_order[len(calls)]].set()
            await asyncio.wait_for(barrier.wait(), timeout=10)
            raw = _existing._reply(attempt_prompts[index]).response_json.replace(
                "Synthetic uncertainty retained.", f"attempt-{index}"
            )
            material = _envelope(provider=provider, raw=raw, task="classification")
            material["usage"]["input_tokens"] = 100 + index
            if index % 2:
                material["content" if provider == "anthropic" else "output"].append(
                    {"type": "unexpected", "text": f"extra-{index}"}
                )
            return httpx.Response(status_code=200, json=material)

        _http(handler=_handler, monkeypatch=monkeypatch)
        async with judge.open_judge_transport(_settings(provider)) as transport:

            async def _invoke(index: int) -> Any:
                """Submit one identified attempt when its arrival gate opens.

                Parameters
                ----------
                index
                    Stable request identity in task submission order.

                Returns
                -------
                Any
                    Actual provider reply before scheduled validation.
                """
                await asyncio.wait_for(gates[index].wait(), timeout=10)
                return await transport.judge(attempt_prompts[index])

            replies.extend(
                await asyncio.gather(
                    *(_invoke(index) for index in range(4)),
                    return_exceptions=True,
                )
            )

    asyncio.run(_exercise())
    assert tuple(calls) == arrival_order
    assert len(replies) == 4
    for index, reply in enumerate(replies):
        if index % 2:
            assert isinstance(reply, judge.JudgeCallError)
            assert reply.category == "invalid_output"
            raw = reply.raw_response
            assert f"extra-{index}" in raw
        else:
            assert not isinstance(reply, BaseException)
            raw = reply.response_json
        assert reply.usage.input_tokens == 100 + index
        assert attempt_prompts[index].request_id in raw
        assert f"attempt-{index}" in raw
        assert all(
            f"attempt-{other}" not in raw for other in range(4) if other != index
        )
    assert judge._ATTEMPT_OUTPUT.get() is None
    assert judge._ATTEMPT_USAGE.get() is None


@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
@pytest.mark.parametrize(argnames="recovers", argvalues=[False, True])
def test_http_retries_are_durable_and_exhaustion_never_resets(
    monkeypatch: pytest.MonkeyPatch, provider: str, recovers: bool, tmp_path: Path
) -> None:
    """Count actual SDK requests through the durable attempt loop and offline replay.

    Parameters
    ----------
    monkeypatch
        Restoring mocked HTTP transport.
    provider
        Actual SDK provider.
    recovers
        Whether the third permitted attempt returns valid output.
    tmp_path
        Isolated durable ledger storage.
    """
    request = _request("classification")
    settings = _settings(provider)
    schedule = SimpleNamespace(
        curricula=(SimpleNamespace(requests=(request,)),),
        judge=settings,
        material_content_hash="b" * 64,
        total_requests=1,
    )
    session = _existing._session(
        path=str(tmp_path / "attempts.sqlite3"), schedule=schedule
    )
    calls: list[Any] = []
    waits = []

    async def _handler(wire: httpx.Request) -> httpx.Response:
        """Produce invalid output until the final permitted successful attempt.

        Parameters
        ----------
        wire
            Actual SDK request.

        Returns
        -------
        httpx.Response
            Invalid or valid output carrying known usage.
        """
        calls.append(json.loads(wire.content))
        raw = (
            _existing._reply(request.prompt).response_json
            if recovers and len(calls) == 3
            else "{}"
        )
        return httpx.Response(
            status_code=200,
            json=_envelope(provider=provider, raw=raw, task="classification"),
        )

    async def _sleep(delay: float) -> None:
        """Observe the prescribed retry delays without wall-clock waiting.

        Parameters
        ----------
        delay
            Requested retry delay.
        """
        waits.append(delay)

    async def _exercise() -> None:
        """Execute and replay the same completed or exhausted durable ledger."""
        async with judge.open_judge_transport(settings) as transport:
            execution = judge._Execution(
                check_material=lambda: None,
                session=session,
                sleep=_sleep,
                transport=transport,
            )
            if recovers:
                await execution.run()
            else:
                with pytest.raises(judge.JudgeExecutionError):
                    await execution.run()
            saved = session.snapshot()
            events = judge._database_events(
                connection=session._connection,
                schedule_hash=schedule.material_content_hash,
            )
            replay = judge.EvaluationSession(
                connection=session._connection, events=events, schedule=schedule
            )
            resumed = judge._Execution(
                check_material=lambda: None,
                session=replay,
                sleep=_sleep,
                transport=transport,
            )
            if recovers:
                assert await resumed.run() == saved
            else:
                with pytest.raises(judge.JudgeExecutionError):
                    await resumed.run()
            assert replay.snapshot() == saved

    _http(handler=_handler, monkeypatch=monkeypatch)
    try:
        asyncio.run(_exercise())
        assert len(calls) == 3
        assert waits == [5, 20]
        events = [
            event for event in session.snapshot().events if event.event != "started"
        ]
        assert len(events) == 3
        assert sum(event.usage.input_tokens for event in events) == 51
        assert sum(event.usage.output_tokens for event in events) == 27
        assert len(session.snapshot().judgments) == int(recovers)
        assert all(event.raw_response for event in events)
        assert calls[0] == calls[1] == calls[2]
    finally:
        session._connection.close()


@pytest.mark.parametrize(
    argnames="mutation", argvalues=["mode", "schema", "sdk", "missing"]
)
def test_incompatible_output_binding_rejects_before_http(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    """Reject stale output modes, schemas and SDK identities before creating a client.

    Parameters
    ----------
    monkeypatch
        Restoring forbidden client construction.
    mutation
        Frozen material incompatibility.
    """
    settings = _settings("anthropic")
    contract = json.loads(settings.output_contract_json)
    if mutation == "mode":
        contract["mode"] = "tool"
    elif mutation == "schema":
        contract["schemas"]["classification"]["schema"]["properties"].pop("request_id")
    elif mutation == "sdk":
        contract["sdk_versions"]["anthropic"] = "old-sdk"
    else:
        contract = {}
    stale = replace(settings, output_contract_json=_existing._dump(contract))
    forbidden = Mock(side_effect=AssertionError("Stale material created HTTP client."))
    monkeypatch.setattr(name="AsyncClient", target=httpx, value=forbidden)

    async def _exercise() -> None:
        """Attempt to open incompatible transport without permitting external effects."""
        with pytest.raises(ValueError, match="structured output contract"):
            async with judge.open_judge_transport(stale):
                raise AssertionError("Incompatible transport was admitted.")

    asyncio.run(_exercise())
    forbidden.assert_not_called()


@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
@pytest.mark.parametrize(argnames="task", argvalues=["classification", "critique"])
def test_local_models_keep_typed_outputs_and_fresh_contexts(
    provider: str, task: str
) -> None:
    """Inject a local model without losing typed output or sharing prior responses.

    Parameters
    ----------
    provider
        Explicit native or tool contract.
    task
        Required judgment type.
    """
    # Third Party Library
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    request = _request(task)
    raw = _existing._reply(request.prompt).response_json
    calls: list[Any] = []
    name = "ClassificationJudgment" if task == "classification" else "CritiqueJudgment"

    def _model(messages: Any, info: Any) -> Any:
        """Return a typed output while inspecting fresh request history.

        Parameters
        ----------
        info
            SDK output schema contract.
        messages
            Agent history delivered to the local model.

        Returns
        -------
        Any
            Native text or intended tool call.
        """
        assert not any(isinstance(item, ModelResponse) for item in messages)
        calls.append(messages)
        if provider == "anthropic":
            assert info.model_request_parameters.output_mode == "native"
            part: TextPart | ToolCallPart = TextPart(content=raw)
        else:
            assert info.output_tools[0].name == name
            part = ToolCallPart(args=raw, tool_name=name)
        return ModelResponse(finish_reason="stop", parts=[part])

    async def _exercise() -> None:
        """Reuse a transport but require a fresh typed agent on each call."""
        transport = judge._ProviderJudge(
            model=FunctionModel(function=_model), settings=_settings(provider)
        )
        for _ in range(2):
            reply = await transport.judge(request.prompt)
            assert reply.response_json == raw
            judge.validate_judge_response(request=request, response_json=raw)

    asyncio.run(_exercise())
    assert len(calls) == 2
    assert calls[0] is not calls[1]


@pytest.mark.parametrize(
    argnames="body",
    argvalues=[
        pytest.param(b'{"output":', id="truncated"),
        pytest.param(b"[]", id="array"),
        pytest.param(b'{"PRIVATE_BODY":', id="private-truncated"),
        pytest.param(b'"PRIVATE_BODY"', id="string"),
        pytest.param(b"null", id="null"),
    ],
)
@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
@pytest.mark.parametrize(argnames="task", argvalues=["classification", "critique"])
def test_malformed_http_body_is_retryable_invalid_output(
    body: bytes,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    task: str,
    tmp_path: Path,
) -> None:
    """Reject undecodable HTTP successes with bounded retries and durable evidence.

    Parameters
    ----------
    body
        Invalid JSON or a non-object envelope, including unsafe body content.
    monkeypatch
        Restoring offline transport.
    provider
        Actual provider SDK.
    task
        Required output task.
    tmp_path
        Isolated durable test storage.
    """
    _assert_http_failure(
        attempts=3,
        body=body,
        category="invalid_output",
        monkeypatch=monkeypatch,
        provider=provider,
        status=200,
        task=task,
        tmp_path=tmp_path,
    )


@pytest.mark.parametrize(
    argnames="body", argvalues=[b'{"PRIVATE_BODY":', b'"PRIVATE_BODY"']
)
@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
@pytest.mark.parametrize(
    argnames=("status", "category", "attempts"),
    argvalues=[
        (400, "configuration", 1),
        (401, "authentication", 1),
        (403, "authentication", 1),
        (429, "rate_limit", 3),
        (500, "server_error", 3),
    ],
)
@pytest.mark.parametrize(argnames="task", argvalues=["classification", "critique"])
def test_non_success_http_body_retains_status_classification(
    *,
    attempts: int,
    body: bytes,
    category: str,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    status: int,
    task: str,
    tmp_path: Path,
) -> None:
    """Keep HTTP failures outside successful-body validation and hidden SDK retries.

    Parameters
    ----------
    attempts
        Retryable statuses permit three attempts; permanent failures permit one.
    body
        Invalid or non-object JSON with unsafe content.
    category
        Expected classification from HTTP status.
    monkeypatch
        Restoring offline transport.
    provider
        Actual provider SDK.
    status
        Unsuccessful HTTP status.
    task
        Required output task.
    tmp_path
        Isolated durable test storage.
    """
    _assert_http_failure(
        attempts=attempts,
        body=body,
        category=category,
        monkeypatch=monkeypatch,
        provider=provider,
        status=status,
        task=task,
        tmp_path=tmp_path,
    )


@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
@pytest.mark.parametrize(argnames="task", argvalues=["classification", "critique"])
def test_provider_payload_and_raw_success(
    monkeypatch: pytest.MonkeyPatch, provider: str, task: str
) -> None:
    """Inspect actual SDK payloads and retain exact native text or tool arguments.

    Parameters
    ----------
    monkeypatch
        Restoring socket-free transport.
    provider
        Actual SDK adapter under test.
    task
        Required judgment schema.
    """
    request = _request(task)
    raw = "  " + _existing._reply(request.prompt).response_json + "\n"
    settings = _settings(provider)
    calls: list[Any] = []

    async def _handler(wire: httpx.Request) -> httpx.Response:
        """Capture request body and return a valid structured judgment.

        Parameters
        ----------
        wire
            SDK-generated request.

        Returns
        -------
        httpx.Response
            Synthetic response with observed usage.
        """
        calls.append(json.loads(wire.content))
        assert wire.extensions["timeout"]["read"] == 180
        return httpx.Response(
            status_code=200,
            headers={"request-id": "offline-request"},
            json=_envelope(provider=provider, raw=raw, task=task),
        )

    async def _exercise() -> Any:
        """Execute a fresh agent through the actual provider client.

        Returns
        -------
        Any
            Captured raw reply.
        """
        async with judge.open_judge_transport(settings) as transport:
            assert transport._model.client.max_retries == 0
            return await transport.judge(request.prompt)

    _http(handler=_handler, monkeypatch=monkeypatch)
    reply = asyncio.run(_exercise())
    assert reply.response_json == raw
    judge.validate_judge_response(request=request, response_json=reply.response_json)
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (17, 9)
    assert reply.usage.cache_read_tokens == 4
    assert reply.usage.cost is None
    assert len(calls) == 1
    body = calls[0]
    frozen = json.loads(settings.output_contract_json)["schemas"][task]
    shared = json.loads(settings.model_settings_json)
    if provider == "anthropic":
        assert body["output_config"]["format"]["schema"] == frozen["schema"]
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["thinking"] == shared["anthropic_thinking"]
        assert body["max_tokens"] == shared["max_tokens"]
        assert not body.get("tools")
        assert not body.get("tool_choice")
    else:
        assert len(body["tools"]) == 1
        tool = body["tools"][0]
        assert tool["type"] == "function" and tool["strict"] is True
        assert tool["name"] == frozen["name"]
        assert tool["parameters"] == frozen["schema"]
        assert body["tool_choice"] == "required"
        assert body["reasoning"]["effort"] == shared["openai_reasoning_effort"]
        assert body["max_output_tokens"] == shared["max_tokens"]
    schema = frozen["schema"]
    assert schema["additionalProperties"] is False
    assert {"request_id", "pair_id", "first_sfi_uuid", "second_sfi_uuid"} <= set(
        schema["required"]
    )


@pytest.mark.parametrize(argnames="provider", argvalues=["anthropic", "openai"])
@pytest.mark.parametrize(argnames="task", argvalues=["classification", "critique"])
@pytest.mark.parametrize(
    argnames="mutation",
    argvalues=[
        "extra_custom",
        "extra_computer",
        "extra_shell",
        "multiple",
        "unexpected",
        "incomplete_item",
        "truncated",
        "malformed",
        "missing_identity",
        "wrong_identity",
        "duplicate_key",
        "invalid_citation",
        "inconsistent_semantics",
        "sdk_decode",
    ],
)
def test_provider_wire_rejections_preserve_accounting(
    monkeypatch: pytest.MonkeyPatch, mutation: str, provider: str, task: str
) -> None:
    """Reject malformed original envelopes and scheduled judgments without retry loss.

    Parameters
    ----------
    monkeypatch
        Restoring mocked HTTP responses.
    mutation
        Independently constructed invalid provider output.
    provider
        Provider envelope and actual SDK.
    task
        Classification or critique output.
    """
    request = _request(task)
    raw = _invalid_judgment(mutation=mutation, request=request)
    envelope = _invalid_envelope(
        mutation=mutation, provider=provider, raw=raw, task=task
    )
    calls: list[Any] = []

    async def _handler(wire: httpx.Request) -> httpx.Response:
        """Return the original adversarial response through the real decoder.

        Parameters
        ----------
        wire
            Actual SDK request.

        Returns
        -------
        httpx.Response
            Synthetic output without live calls.
        """
        calls.append(wire.method)
        return httpx.Response(status_code=200, json=envelope)

    async def _exercise() -> None:
        """Reject at adapter or exact schedule boundary, retaining raw evidence."""
        async with judge.open_judge_transport(_settings(provider)) as transport:
            try:
                reply = await transport.judge(request.prompt)
            except judge.JudgeCallError as error:
                assert error.category == "invalid_output"
                assert error.raw_response is not None
                assert (error.usage.input_tokens, error.usage.output_tokens) == (17, 9)
                assert all(
                    canary not in str(error)
                    for canary in (
                        "PRIVATE_",
                        "private_invalid_text",
                        "wrong-secret",
                        "duplicate-secret",
                    )
                )
                assert all(
                    canary not in error.raw_response
                    for canary in (
                        "PRIVATE_THINKING",
                        "PRIVATE_SIGNATURE",
                        "PRIVATE_METADATA",
                    )
                )
                if mutation.startswith("extra_"):
                    assert "UNEXPECTED_ARGUMENTS" in error.raw_response
                if mutation in ("malformed", "duplicate_key", "missing_identity"):
                    assert (
                        error.raw_response == raw
                        or json.loads(error.raw_response)[0]["parts"][0]["arguments"]
                        == raw
                    )
            else:
                assert mutation in {"wrong_identity", "invalid_citation"}
                assert reply.response_json == raw
                with pytest.raises(ValueError):
                    judge.validate_judge_response(
                        request=request, response_json=reply.response_json
                    )

    _http(handler=_handler, monkeypatch=monkeypatch)
    asyncio.run(_exercise())
    assert calls == ["POST"]
