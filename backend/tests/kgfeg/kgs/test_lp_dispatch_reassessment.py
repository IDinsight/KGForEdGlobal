"""Independently challenge transport-race and unknown-usage test assumptions."""

# Future Library
from __future__ import annotations

# Standard Library
import sys

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import FrameType, SimpleNamespace
from typing import Any, cast

# Third Party Library
import httpx
import pytest

from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import llm, lp_dispatch, lp_generation, lp_requests
from kgfeg.kgs.llm import KGUsageTracker
from kgfeg.kgs.lp_dispatch import (
    LPDispatchClosed,
    LPDispatchGate,
    LPDispatchIntegrityError,
)
from tests.kgfeg.kgs import test_lp_dispatch_concurrency as _sdk
from tests.kgfeg.kgs import test_lp_generation as _fixtures


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain the complete socket prohibition used by the original SDK tests.

    Parameters
    ----------
    monkeypatch
        Restoring socket patches.
    """
    cast(Any, _sdk._block_network).__wrapped__(monkeypatch)


@pytest.mark.parametrize(argnames="provider", argvalues=_sdk._PROVIDERS)
# pylint: disable-next=too-complex
def test_hook_return_preemption_cannot_launch_transport_after_coordinated_close(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Expose thread preemption without changing the HTTP client's send methods.

    Parameters
    ----------
    monkeypatch
        Replace only the network transport with a counted offline transport.
    provider
        Actual SDK route.
    """
    model = _sdk._model(provider)
    agent = Agent(model=model, retries=0)
    original_client = httpx.AsyncClient
    hook_finished, release, closed = Event(), Event(), Event()
    outbound: list[bool] = []
    gate = LPDispatchGate()

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Preserve the unmodified HTTP client and adapter-supplied hooks.

        Parameters
        ----------
        kwargs
            Actual adapter HTTP options.

        Returns
        -------
        httpx.AsyncClient
            Real HTTP client with its ordinary send methods.
        """
        return original_client(transport=httpx.MockTransport(_transport), **kwargs)

    def _transport(request: httpx.Request) -> httpx.Response:
        """Count network-boundary entries relative to coordinator closure.

        Parameters
        ----------
        request
            Prepared SDK request.

        Returns
        -------
        httpx.Response
            Known provider response with explicit usage.
        """
        outbound.append(closed.is_set())
        return _sdk._response(provider=provider, request=request)

    def _worker() -> Any:
        """Pause this thread exactly when the production HTTP hook returns.

        Returns
        -------
        Any
            The unmodified adapter outcome.
        """

        def _trace(frame: FrameType, event: str, arg: Any) -> Any:
            """Observe the standard trace callback protocol without altering values.

            Parameters
            ----------
            frame
                Current Python execution frame.
            event
                Trace event kind.
            arg
                Trace event payload; the original value remains unchanged.

            Returns
            -------
            Any
                Local trace callback only for the production request hook.
            """
            del arg
            if (
                frame.f_code.co_filename == lp_dispatch.__file__
                and frame.f_code.co_name == "_check_dispatch"
            ):
                if event == "return" and not hook_finished.is_set():
                    hook_finished.set()
                    assert release.wait(timeout=20), "Trace barrier was not released."
                return _trace
            return None

        previous = sys.gettrace()
        sys.settrace(_trace)
        try:
            return _sdk._run(agent=agent, gate=gate, usage=RunUsage())
        finally:
            sys.settrace(previous)

    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_worker)
        try:
            assert hook_finished.wait(timeout=20), "Production hook was not observed."
            assert not outbound, "The controlled request was already dispatched."
            with gate.coordinate():
                gate.close()
                closed.set()
        finally:
            release.set()
        try:
            assert future.result(timeout=20).output == "ok"
        except LPDispatchClosed:
            pass
    assert not outbound, "Unmodified HTTPX dispatched after the closed gate."


@pytest.mark.parametrize(argnames="provider", argvalues=_sdk._PROVIDERS)
@pytest.mark.parametrize(argnames="kind", argvalues=["closed", "integrity", "remote"])
def test_usage_classification_has_local_and_remote_failure_controls(
    kind: str, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Distinguish no-call local rejection from an actually dispatched unknown result.

    Parameters
    ----------
    kind
        Cancellation, local rejected material, or real mocked connection failure.
    monkeypatch
        Actual SDK factory and offline HTTP transport injection.
    provider
        Supported provider route.
    """
    config = _fixtures._config()
    population = lp_requests.build_lp_generation_requests(
        as_lc_bundle=_fixtures._bundle(2),
        doc_key="synthetic-selection-document",
        kg_config=config,
    )
    model = _sdk._model(provider)
    original_client = httpx.AsyncClient
    outbound: list[httpx.Request] = []
    checks = 0

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Close only after worker admission for the cancellation control.

        Parameters
        ----------
        kwargs
            Adapter HTTP options.

        Returns
        -------
        httpx.AsyncClient
            Unmodified client using the counted network seam.
        """
        if kind == "closed":
            gate.close()
        return original_client(transport=httpx.MockTransport(_transport), **kwargs)

    def _material() -> None:
        """Make the initial worker check succeed and optionally reject the hook."""
        nonlocal checks
        checks += 1
        if kind == "integrity" and checks == 2:
            raise ValueError("synthetic changed material")

    def _transport(request: httpx.Request) -> httpx.Response:
        """Record an actual network-boundary entry without inventing token usage.

        Parameters
        ----------
        request
            Prepared provider request.

        Returns
        -------
        httpx.Response
            No response; the mocked connection fails after dispatch.
        """
        outbound.append(request)
        raise httpx.ConnectError("synthetic remote outcome is unknown", request=request)

    gate = LPDispatchGate(check_material=_material)
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
        gate=gate,
        kg_config=config,
        model_config=Settings.llm_config("kgs"),
        population=population,
        request_index=0,
    )
    assert outcome.error is not None
    assert len(outbound) == int(kind == "remote")
    assert checks == (1 if kind == "closed" else 2)
    if kind == "closed":
        assert isinstance(outcome.error, LPDispatchClosed)
    elif kind == "integrity":
        assert isinstance(outcome.error, LPDispatchIntegrityError)
        assert isinstance(outcome.error, ValueError)
    else:
        assert not isinstance(
            outcome.error, (LPDispatchClosed, LPDispatchIntegrityError)
        )
    aggregate = KGUsageTracker()
    aggregate.lp_max_concurrent_requests = 4
    lp_generation._merge_lp_usage(outcome=outcome, target=aggregate)
    serialized = aggregate.to_dict()
    assert serialized["totals"]["requests"] == 0
    assert serialized["totals"]["total_tokens"] == 0
    assert serialized["lp_execution"]["available_cost"] is None
    assert serialized["lp_execution"]["unknown_usage_attempts"] == int(kind == "remote")
