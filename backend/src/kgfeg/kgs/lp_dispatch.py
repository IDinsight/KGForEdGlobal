"""Guard each production LP transport dispatch and isolate its SDK retry state.

The shared KG model and its settings remain authoritative. Only LP attempts disable
hidden SDK retries: the coordinator owns their finite retry budget and shutdown gate.
"""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import copy
from threading import RLock
from typing import Any

# Third Party Library
import httpx

from httpx import AsyncBaseTransport
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.usage import RunUsage


class _LPTransport(AsyncBaseTransport):
    """Wrap an isolated HTTP transport without holding the gate across network waits."""

    def __init__(
        self, *, attempt: _LPTransportAttempt, transport: AsyncBaseTransport
    ) -> None:
        """Retain the shared attempt authority and the client's selected transport.

        Parameters
        ----------
        attempt
            One attempt shared by every default, proxy and mounted transport.
        transport
            Original isolated-client transport with unchanged connection settings.
        """

        self._attempt = attempt
        self._transport = transport

    async def aclose(self) -> None:
        """Close the owned transport through the ordinary HTTP client lifecycle."""

        await self._transport.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Start transport under the gate, then await its response outside the gate.

        Parameters
        ----------
        request
            Prepared provider request after HTTP hooks and routing.

        Returns
        -------
        httpx.Response
            Original transport response, including its unchanged body stream.
        """

        task = self._attempt.start(request=request, transport=self._transport)
        return await task


class _LPTransportAttempt:
    """Keep one transport start and any known local rejection for SDK restoration."""

    def __init__(self, gate: LPDispatchGate) -> None:
        """Capture the exact coordinator gate for this isolated provider attempt.

        Parameters
        ----------
        gate
            Shared shutdown, material-validation and checkpoint-publication lock.
        """

        self.dispatched = False
        self.error: LPDispatchClosed | LPDispatchIntegrityError | None = None
        self._gate = gate

    def start(
        self, *, request: httpx.Request, transport: AsyncBaseTransport
    ) -> asyncio.Task[httpx.Response]:
        """Enter the actual transport before releasing shutdown coordination.

        Parameters
        ----------
        request
            Fully prepared HTTP request selected for this transport.
        transport
            Original transport whose coroutine must enter while dispatch is open.

        Returns
        -------
        asyncio.Task[httpx.Response]
            Already-started transport call; its asynchronous waits hold no gate lock.

        Raises
        ------
        LPDispatchIntegrityError
            If the gate check fails.
        RuntimeError
            If the LP stage attempted a second transport dispatch.
        """

        with self._gate.coordinate():
            if self.dispatched:
                raise RuntimeError("LP stage attempted a second transport dispatch.")

            try:
                self._gate.check()
            except (LPDispatchClosed, LPDispatchIntegrityError) as error:
                self.error = error
                raise

            # Merely creating a coroutine or scheduling a normal task leaves a
            # preemption gap. Eager execution enters the transport synchronously,
            # through its first suspension, while closure/publication is excluded.
            task = asyncio.Task(
                coro=transport.handle_async_request(request),
                eager_start=True,
                loop=asyncio.get_running_loop(),
            )
            self.dispatched = True
            return task


class LPDispatchClosed(RuntimeError):
    """A call was cancelled before transport dispatch by the run's shutdown gate."""


class LPDispatchGate:
    """Serialize shutdown observation against admission and transport dispatch."""

    def __init__(self, *, check_material: Callable[[], None] | None = None) -> None:
        """Initialize an open gate with optional immutable-input and ownership checks.

        Parameters
        ----------
        check_material
            Verify frozen request inputs and directory ownership before transport use.
        """

        self._check_material = check_material
        self._closed = False
        self._lock = RLock()

    def check(self) -> None:
        """Reject new dispatch once the coordinator observes exhausted failure.

        Raises
        ------
        LPDispatchClosed
            If the LP stage attempted a second transport dispatch.
        LPDispatchIntegrityError
            If the LP stage attempted a second transport dispatch.
        """

        with self._lock:
            if self._closed:
                raise LPDispatchClosed("LP dispatch closed; this call was not started.")

            if self._check_material is not None:
                try:
                    self._check_material()
                except Exception as error:
                    self._closed = True
                    raise LPDispatchIntegrityError(
                        "LP dispatch material or directory ownership changed."
                    ) from error

    def close(self) -> None:
        """Close dispatch permanently for this invocation."""

        with self._lock:
            self._closed = True

    @contextmanager
    def coordinate(self) -> Iterator[None]:
        """Serialize failure observation and publication against transport startup.

        Yields
        ------
        None
            Exclusive coordinator access to a stable checkpoint state. Workers cannot
            start their transport while the coordinator observes outcomes or writes a
            transaction. A coordinator error closes dispatch before releasing access.
        """

        with self._lock:
            try:
                yield
            except BaseException:
                self._closed = True
                raise


class LPDispatchIntegrityError(ValueError):
    """Transport dispatch lost its validated input or ownership authority."""


def _wrap_lp_transports(
    *, attempt: _LPTransportAttempt, client: httpx.AsyncClient
) -> None:
    """Guard all routes on the privately owned client, including environment proxies.

    Parameters
    ----------
    attempt
        Single-dispatch authority shared across every route and redirect.
    client
        Isolated client before any request; shared model clients are never mutated.
    """

    # HTTPX exposes transport injection at construction but no public wrapping API for
    # its resolved proxy mounts. Retain those actual transports and their routing.
    client._transport = _LPTransport(attempt=attempt, transport=client._transport)
    client._mounts = {
        pattern: (
            None
            if transport is None
            else _LPTransport(attempt=attempt, transport=transport)
        )
        for pattern, transport in client._mounts.items()
    }


def run_lp_agent_attempt(
    *,
    agent: Agent,
    dispatch_guard: Callable[[], None] | None,
    usage: RunUsage,
    user_prompt: str | None,
) -> Any:
    """Run one tool-free attempt with no SDK or agent-level hidden retry allowance.

    Parameters
    ----------
    agent
        Existing LP producer or checker with its unchanged model and prompt.
    dispatch_guard
        Bound LPDispatchGate.check method for coordinated provider transport entry.
        Local test models may supply a plain guard callback; omission uses a local gate.
    usage
        Per-attempt observed usage accumulator, including invalid structured outputs.
    user_prompt
        Original bounded evidence message.

    Returns
    -------
    Any
        Untrusted agent result for deterministic validation by the caller.

    Raises
    ------
    Exception
        If the LP attempt failed.
    TypeError
        If the agent's model is not a supported LP provider or if the guard is unbound.
    """

    guard = dispatch_guard or LPDispatchGate().check
    model = agent.model

    if not isinstance(model, (AnthropicModel, OpenAIChatModel, OpenAIResponsesModel)):
        # Local FunctionModel/test transports still share the same dispatch boundary.
        guard()
        return agent.run_sync(usage=usage, user_prompt=user_prompt)

    gate = getattr(guard, "__self__", None)

    if not isinstance(gate, LPDispatchGate) or guard != gate.check:
        raise TypeError("LP provider dispatch requires a bound LPDispatchGate.check.")

    attempt = _LPTransportAttempt(gate)

    async def _check_dispatch(request: httpx.Request) -> None:
        """Reject repeated HTTP requests; transport entry owns the authoritative gate.

        Parameters
        ----------
        request
            Prepared provider request; no request content is logged or retained.

        Raises
        ------
        RuntimeError
            If the LP stage attempted a second transport dispatch.
        """

        del request

        if attempt.dispatched:
            raise RuntimeError("LP stage attempted a second transport dispatch.")

    # Preserve SDK timeout, credentials, endpoint, headers, model profile and settings.
    # Each thread owns its HTTP pool; shared AS/LC clients are never mutated or closed.
    client = httpx.AsyncClient(
        event_hooks={"request": [_check_dispatch]},
        headers=model.client._client.headers,
        timeout=model.client.timeout,
    )

    _wrap_lp_transports(attempt=attempt, client=client)
    isolated = copy(model)
    isolated.client = model.client.with_options(http_client=client, max_retries=0)

    try:
        with agent.override(model=isolated):
            return agent.run_sync(
                usage=usage,
                user_prompt=user_prompt,
            )
    except Exception as error:
        # Provider SDKs wrap transport-guard errors as connection failures. Preserve
        # the local cancellation/integrity outcome instead of inventing a failed API
        # call.
        if attempt.error is not None:
            raise attempt.error from error

        raise
    finally:
        asyncio.get_event_loop().run_until_complete(client.aclose())
