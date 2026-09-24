"""Challenge provider dispatch, cancellation, and checkpoint coordination offline."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import socket

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

# Third Party Library
import httpx
import pytest

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

# Package Library
from kgfeg.kgs import lp_dispatch
from kgfeg.kgs.lp_dispatch import (
    LPDispatchClosed,
    LPDispatchGate,
    LPDispatchIntegrityError,
    run_lp_agent_attempt,
)

_PROVIDERS = ("anthropic", "openai_chat", "openai_responses")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid socket connections even if an injected transport is bypassed.

    Parameters
    ----------
    monkeypatch
        Restoring socket patch.
    """
    guard = Mock(side_effect=AssertionError("Provider tests are offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)


def _model(provider: str) -> Any:
    """Construct an actual provider SDK using synthetic credentials and endpoint.

    Parameters
    ----------
    provider
        Supported adapter route.

    Returns
    -------
    Any
        Real model backed by a real SDK client.
    """
    if provider == "anthropic":
        anthropic_client = AsyncAnthropic(
            api_key="synthetic-key", base_url="https://offline.invalid"
        )
        return AnthropicModel(
            model_name="claude-sonnet-4-6",
            provider=AnthropicProvider(anthropic_client=anthropic_client),
        )
    openai_client = AsyncOpenAI(
        api_key="synthetic-key", base_url="https://offline.invalid"
    )
    factory = OpenAIChatModel if provider == "openai_chat" else OpenAIResponsesModel
    return factory(
        model_name="gpt-5.2", provider=OpenAIProvider(openai_client=openai_client)
    )


def _response(*, provider: str, request: httpx.Request) -> httpx.Response:
    """Return a minimal valid provider response with explicit observed usage.

    Parameters
    ----------
    provider
        Provider response wire shape.
    request
        Mocked outgoing request.

    Returns
    -------
    httpx.Response
        Synthetic text result compatible with the actual SDK parser.
    """
    payload: dict[str, Any]
    if provider == "anthropic":
        payload = {
            "id": "msg_offline",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }
    elif provider == "openai_chat":
        payload = {
            "id": "chatcmpl_offline",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-5.2",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }
    else:
        payload = {
            "id": "resp_offline",
            "object": "response",
            "created_at": 1,
            "model": "gpt-5.2",
            "status": "completed",
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {
                    "id": "msg_offline",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "ok", "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        }
    return httpx.Response(json=payload, request=request, status_code=200)


def _run(*, agent: Agent, gate: LPDispatchGate, usage: RunUsage) -> Any:
    """Own an event loop exactly as the production worker does.

    Parameters
    ----------
    agent
        Real provider-backed agent.
    gate
        Shared dispatch authority.
    usage
        Per-attempt accounting.

    Returns
    -------
    Any
        Unmodified adapter result.
    """
    with asyncio.Runner() as runner:
        runner.get_loop()
        return run_lp_agent_attempt(
            agent=agent,
            dispatch_guard=gate.check,
            usage=usage,
            user_prompt="synthetic input",
        )


@pytest.mark.parametrize(argnames="provider", argvalues=_PROVIDERS)
@pytest.mark.parametrize(argnames="failure", argvalues=["closed", "integrity"])
def test_actual_sdk_preserves_local_hook_exception_without_outbound_or_retry(
    failure: str, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Provider exception wrapping must retain a known local dispatch rejection.

    Parameters
    ----------
    failure
        Local cancellation or rejected material.
    monkeypatch
        Inject an offline transport into the isolated HTTP client.
    provider
        Actual SDK route under test.
    """
    model = _model(provider)
    agent = Agent(model=model, retries=0)
    original_client = httpx.AsyncClient
    outbound: list[httpx.Request] = []

    def _transport(request: httpx.Request) -> httpx.Response:
        """Record calls that reach the actual transport.

        Parameters
        ----------
        request
            Prepared request after all request hooks.

        Returns
        -------
        httpx.Response
            Controlled provider response.
        """
        outbound.append(request)
        return _response(provider=provider, request=request)

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Inject only the network boundary while preserving real event hooks.

        Parameters
        ----------
        kwargs
            Adapter-supplied HTTP options.

        Returns
        -------
        httpx.AsyncClient
            Real client with no sockets.
        """
        return original_client(transport=httpx.MockTransport(_transport), **kwargs)

    def _reject_material() -> None:
        """Represent detected invalid local checkpoint bytes."""
        raise ValueError("synthetic checkpoint mutation")

    gate = LPDispatchGate(
        check_material=_reject_material if failure == "integrity" else None
    )
    if failure == "closed":
        gate.close()
    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    usage = RunUsage()
    expected = LPDispatchClosed if failure == "closed" else LPDispatchIntegrityError
    with pytest.raises(expected_exception=expected) as caught:
        _run(agent=agent, gate=gate, usage=usage)
    assert caught.value.__cause__.__cause__.__class__.__name__ == "APIConnectionError"
    assert not outbound
    assert usage.requests == usage.input_tokens == usage.output_tokens == 0
    assert model.client.max_retries == 2


@pytest.mark.parametrize(argnames="provider", argvalues=_PROVIDERS)
def test_actual_transport_cannot_start_after_closed_gate_even_if_hook_already_passed(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Pause after request hooks but before transport and close dispatch atomically.

    Parameters
    ----------
    monkeypatch
        Inject only HTTP transport and its controllable pre-send boundary.
    provider
        Actual provider SDK route under test.
    """
    model = _model(provider)
    agent = Agent(model=model, retries=0)
    hook_passed, resume = Event(), Event()
    outbound: list[bool] = []
    closed = Event()
    gate = LPDispatchGate()

    def _transport(request: httpx.Request) -> httpx.Response:
        """Observe whether transport starts after coordinator closure.

        Parameters
        ----------
        request
            Provider request reaching the network boundary.

        Returns
        -------
        httpx.Response
            Controlled provider response.
        """
        outbound.append(closed.is_set())
        return _response(provider=provider, request=request)

    class _Client(httpx.AsyncClient):
        """Expose the real HTTP client's point after hooks and before transport."""

        async def _send_single_request(self, request: httpx.Request) -> httpx.Response:
            """Wait for coordinator closure before starting the mock transport.

            Parameters
            ----------
            request
                SDK-prepared request whose hooks already completed.

            Returns
            -------
            httpx.Response
                Result from the real client send path.
            """
            hook_passed.set()
            assert resume.wait(timeout=20), "Coordinator did not release transport."
            return await super()._send_single_request(request)

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Preserve adapter options and use the controlled transport.

        Parameters
        ----------
        kwargs
            Adapter HTTP options.

        Returns
        -------
        httpx.AsyncClient
            Instrumented real client.
        """
        return _Client(transport=httpx.MockTransport(_transport), **kwargs)

    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    usage = RunUsage()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run, agent=agent, gate=gate, usage=usage)
        try:
            assert hook_passed.wait(
                timeout=20
            ), "SDK did not reach the controlled boundary."
            with gate.coordinate():
                gate.close()
                closed.set()
        finally:
            resume.set()
        try:
            result = future.result(timeout=20)
            assert result.output == "ok"
        except LPDispatchClosed:
            pass
    assert not outbound, "An actual provider transport started after dispatch closure."


@pytest.mark.parametrize(argnames="provider", argvalues=_PROVIDERS)
@pytest.mark.parametrize(
    argnames="result_kind", argvalues=["success", "http_error", "connection_error"]
)
def test_sdk_attempt_uses_one_transport_and_preserves_settings_and_observed_usage(
    monkeypatch: pytest.MonkeyPatch, provider: str, result_kind: str
) -> None:
    """Real SDK failures cannot multiply the coordinator's permitted call budget.

    Parameters
    ----------
    monkeypatch
        Replace only the network transport.
    provider
        Actual supported provider route.
    result_kind
        Valid return, server failure or unavailable remote outcome.
    """
    model = _model(provider)
    original_client = httpx.AsyncClient
    outbound: list[httpx.Request] = []
    timeouts: list[Any] = []

    def _transport(request: httpx.Request) -> httpx.Response:
        """Observe the actual provider call and return controlled wire data.

        Parameters
        ----------
        request
            Request reaching the mock network transport.

        Returns
        -------
        httpx.Response
            Synthetic provider result.
        """
        outbound.append(request)
        if result_kind == "connection_error":
            raise httpx.ConnectError(
                message="synthetic remote failure", request=request
            )
        if result_kind == "http_error":
            return httpx.Response(
                json={
                    "error": {
                        "message": "synthetic server failure",
                        "type": "api_error",
                    }
                },
                request=request,
                status_code=500,
            )
        return _response(provider=provider, request=request)

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Retain actual SDK timeout while replacing only outbound transport.

        Parameters
        ----------
        kwargs
            HTTP options from production adapter.

        Returns
        -------
        httpx.AsyncClient
            Real client with the controlled transport.
        """
        timeouts.append(kwargs["timeout"])
        return original_client(transport=httpx.MockTransport(_transport), **kwargs)

    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    usage = RunUsage()
    agent = Agent(model=model, retries=0)
    if result_kind == "success":
        result = _run(agent=agent, gate=LPDispatchGate(), usage=usage)
        assert result.output == "ok"
        assert (usage.requests, usage.input_tokens, usage.output_tokens) == (1, 7, 3)
    else:
        with pytest.raises(expected_exception=Exception) as caught:
            _run(agent=agent, gate=LPDispatchGate(), usage=usage)
        assert caught.value.__class__.__name__ in {"ModelAPIError", "ModelHTTPError"}
        assert usage.requests == 0
    assert len(outbound) == 1
    assert timeouts == [model.client.timeout]
    assert model.client.max_retries == 2


@pytest.mark.parametrize(argnames="provider", argvalues=_PROVIDERS)
def test_transport_integrity_rejection_is_known_zero_usage_in_worker_aggregation(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """A restored local hook rejection has no unknown remote call to charge.

    Parameters
    ----------
    monkeypatch
        Real SDK model factory with synthetic network transport.
    provider
        Actual provider route.
    """
    # Package Library
    from kgfeg.config import Settings
    from kgfeg.kgs import llm, lp_generation, lp_requests
    from kgfeg.kgs.llm import KGUsageTracker
    from tests.kgfeg.kgs import test_lp_generation as fixtures

    config = fixtures._config()
    population = lp_requests.build_lp_generation_requests(
        as_lc_bundle=fixtures._bundle(2),
        doc_key="synthetic-selection-document",
        kg_config=config,
    )
    model = _model(provider)
    original_client = httpx.AsyncClient
    outbound: list[httpx.Request] = []
    checks = 0

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Inject a counted transport into the unmodified real SDK path.

        Parameters
        ----------
        kwargs
            Adapter-supplied HTTP options.

        Returns
        -------
        httpx.AsyncClient
            Controlled real client.
        """

        def _transport(request: httpx.Request) -> httpx.Response:
            """Record any attempted outbound transport.

            Parameters
            ----------
            request
                Synthetic SDK request.

            Returns
            -------
            httpx.Response
                Controlled response, if reached.
            """
            outbound.append(request)
            return _response(provider=provider, request=request)

        return original_client(transport=httpx.MockTransport(_transport), **kwargs)

    def _material() -> None:
        """Reject only at the HTTP hook after the worker admission check succeeds."""
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ValueError("synthetic rejected checkpoint bytes")

    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")
    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    monkeypatch.setattr(
        name="create_lp_generation_agent",
        target=llm,
        value=lambda **kwargs: Agent(model=model, retries=0),
    )
    outcome = lp_generation._execute_lp_attempt(
        draft=None,
        gate=LPDispatchGate(check_material=_material),
        kg_config=config,
        model_config=Settings.llm_config("kgs"),
        population=population,
        request_index=0,
    )
    assert isinstance(outcome.error, LPDispatchIntegrityError)
    assert checks == 2 and not outbound
    assert outcome.usage_tracker.lp_generation.requests == 0
    aggregate = KGUsageTracker()
    lp_generation._merge_lp_usage(outcome=outcome, target=aggregate)
    assert (
        aggregate.lp_unknown_usage_attempts == 0
    ), "Known local rejection was counted as unknown remote usage."
