"""Challenge guarded transport startup, real HTTP routing, and asynchronous cleanup."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import sys

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import FrameType, SimpleNamespace
from typing import Any, cast

# Third Party Library
import httpcore
import httpx
import pytest

from httpcore._backends.mock import AsyncMockBackend, AsyncMockStream
from pydantic_ai import Agent
from pydantic_ai.usage import RunUsage

# Package Library
from kgfeg.kgs import lp_dispatch, lp_generation
from kgfeg.kgs.llm import KGUsageTracker
from kgfeg.kgs.lp_dispatch import LPDispatchClosed, LPDispatchGate
from tests.kgfeg.kgs import test_lp_dispatch_concurrency as _sdk


class _Network(AsyncMockBackend):
    """Suspend the network boundary while retaining the real HTTP connection stack."""

    def __init__(self, *, body: bytes, closed: Event, kind: str) -> None:
        """Initialize synthetic wire data and observable asynchronous state.

        Parameters
        ----------
        body
            Complete provider response body.
        closed
            Observed coordinator closure.
        kind
            Successful, failed, or cancelled remote attempt.
        """
        super().__init__(buffer=[])
        self.body = body
        self.closed = closed
        self.kind = kind
        self.entered = Event()
        self.hosts: list[str] = []
        self.streams: list[_Stream] = []
        self.release: tuple[asyncio.AbstractEventLoop, asyncio.Future[None]] | None = (
            None
        )

    # Preserve HTTPcore's positional callback protocol.
    # pylint: disable-next=too-many-positional-arguments
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> _Stream:
        """Wait asynchronously before returning a socket-free HTTP byte stream.

        Parameters
        ----------
        host
            Host selected by real direct or proxy routing.
        port
            Selected port.
        timeout
            Unchanged connection timeout.
        local_address
            Unchanged source address selection.
        socket_options
            Unchanged socket options.

        Returns
        -------
        _Stream
            Controlled HTTP response bytes with tracked close behavior.
        """
        del local_address, port, socket_options, timeout
        assert not self.closed.is_set(), "A new connection began after closure."
        self.hosts.append(host)
        loop = asyncio.get_running_loop()
        release: asyncio.Future[None] = loop.create_future()
        self.release = (loop, release)
        self.entered.set()
        await asyncio.wait_for(fut=release, timeout=20)
        if self.kind == "connection_error":
            raise httpcore.ConnectError("synthetic unknown remote outcome")
        response = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(self.body)).encode()
            + b"\r\n\r\n"
            + self.body
        )
        buffers = [response]
        if host == "proxy.invalid":
            buffers.insert(0, b"HTTP/1.1 200 Connection established\r\n\r\n")
        stream = _Stream(buffer=buffers, kind=self.kind)
        self.streams.append(stream)
        return stream

    def finish(self) -> None:
        """Release or cancel the suspended connection from the coordinator thread."""
        if self.release is not None:
            loop, future = self.release

            def _finish() -> None:
                """Complete the fixture future on its owning worker loop."""
                if not future.done():
                    if self.kind == "cancelled":
                        future.cancel()
                    else:
                        future.set_result(None)

            loop.call_soon_threadsafe(_finish)


class _Stream(AsyncMockStream):
    """Retain outbound HTTP bytes and verify asynchronous stream cleanup."""

    def __init__(self, *, buffer: list[bytes], kind: str) -> None:
        """Initialize the reduced HTTP exchange.

        Parameters
        ----------
        buffer
            Ordered response frames, including a proxy handshake when selected.
        kind
            Successful response or a failure while reading the provider response.
        """
        super().__init__(buffer=buffer)
        self.closes = 0
        self.fail_on_read = len(buffer)
        self.kind = kind
        self.reads = 0
        self.writes: list[bytes] = []

    async def aclose(self) -> None:
        """Record close and force a cooperative asynchronous cleanup boundary."""
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        loop.call_soon(done.set_result, None)
        await done
        self.closes += 1
        await super().aclose()

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        """Inject active read failure after any successful proxy handshake.

        Parameters
        ----------
        max_bytes
            Requested read bound.
        timeout
            Existing configured read timeout.

        Returns
        -------
        bytes
            Synthetic response bytes unless the selected read fails.
        """
        self.reads += 1
        if self.reads == self.fail_on_read:
            if self.kind == "read_cancelled":
                raise asyncio.CancelledError("synthetic active read cancellation")
            if self.kind == "read_error":
                raise httpcore.ReadError("synthetic active response failure")
        return await super().read(max_bytes=max_bytes, timeout=timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        """Retain wire requests produced by HTTPcore.

        Parameters
        ----------
        buffer
            Outgoing HTTP request bytes.
        timeout
            Existing configured write timeout.
        """
        del timeout
        self.writes.append(buffer)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid sockets and remove ambient proxy inputs from deterministic fixtures.

    Parameters
    ----------
    monkeypatch
        Restoring socket and environment patches.
    """
    cast(Any, _sdk._block_network).__wrapped__(monkeypatch)
    for name in ("all_proxy", "http_proxy", "https_proxy", "no_proxy"):
        monkeypatch.delenv(name=name, raising=False)
        monkeypatch.delenv(name=name.upper(), raising=False)


@pytest.mark.parametrize(
    argnames="boundary", argvalues=["check_return", "transport_entry"]
)
@pytest.mark.parametrize(argnames="provider", argvalues=_sdk._PROVIDERS)
# Keep the two preemption boundaries and their cleanup in one coordinated scenario.
# pylint: disable-next=too-complex
def test_eager_start_excludes_shutdown_between_final_check_and_transport_entry(
    boundary: str, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Preempt at both sides of eager startup and inspect real lock exclusion.

    Parameters
    ----------
    boundary
        Last material check return or first actual transport instruction.
    monkeypatch
        Inject the network seam without replacing dispatch or task creation.
    provider
        Actual SDK route.
    """
    agent = Agent(model=_sdk._model(provider), retries=0)
    original_client = httpx.AsyncClient
    gate = LPDispatchGate()
    paused, resume, entered, closed, closing = (Event() for _ in range(5))
    release: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Keep real client, hooks and send methods with a controlled transport.

        Parameters
        ----------
        kwargs
            Adapter HTTP settings.

        Returns
        -------
        httpx.AsyncClient
            Isolated socket-free client.
        """
        return original_client(transport=httpx.MockTransport(_transport), **kwargs)

    def _close() -> None:
        """Attempt the same coordinated shutdown used by the generation owner."""
        closing.set()
        with gate.coordinate():
            gate.close()
            closed.set()

    async def _transport(request: httpx.Request) -> httpx.Response:
        """Reach real transport entry before asynchronously waiting for a response.

        Parameters
        ----------
        request
            Prepared request after routing.

        Returns
        -------
        httpx.Response
            Valid synthetic provider response.
        """
        if boundary == "transport_entry":
            paused.set()
            assert resume.wait(timeout=20)
        assert not closed.is_set()
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        release.append((loop, done))
        entered.set()
        await asyncio.wait_for(fut=done, timeout=20)
        return _sdk._response(provider=provider, request=request)

    def _worker() -> Any:
        """Trace the final check without replacing it or its outer lock.

        Returns
        -------
        Any
            Actual SDK result.
        """

        def _trace(frame: FrameType, event: str, arg: Any) -> Any:
            """Pause only at the final production gate-check return.

            Parameters
            ----------
            frame
                Current Python frame.
            event
                Trace event.
            arg
                Unmodified event value.

            Returns
            -------
            Any
                Trace callback for the relevant frame only.
            """
            del arg
            if (
                frame.f_code.co_filename == lp_dispatch.__file__
                and frame.f_code.co_name == "check"
            ):
                if event == "return" and not paused.is_set():
                    paused.set()
                    assert resume.wait(timeout=20)
                return _trace
            return None

        previous = sys.gettrace()
        if boundary == "check_return":
            sys.settrace(_trace)
        try:
            return _sdk._run(agent=agent, gate=gate, usage=RunUsage())
        finally:
            sys.settrace(previous)

    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(_worker)
        try:
            assert paused.wait(timeout=20)
            # This probe runs on another thread; RLock reentrancy cannot fake exclusion.
            # A context manager would block instead of observing lock ownership.
            # pylint: disable-next=consider-using-with
            acquired = gate._lock.acquire(blocking=False)
            if acquired:
                gate._lock.release()
            assert not acquired, "Transport startup is outside coordinator exclusion."
            closer = executor.submit(_close)
            assert closing.wait(timeout=20) and not closed.is_set()
            resume.set()
            assert entered.wait(timeout=20)
            closer.result(timeout=20)
            assert closed.is_set() and not worker.done()
        finally:
            resume.set()
            if entered.wait(timeout=20):
                loop, done = release[0]
                loop.call_soon_threadsafe(done.set_result, None)
        assert worker.result(timeout=20).output == "ok"


@pytest.mark.parametrize(
    argnames="kind",
    argvalues=[
        "success",
        "connection_error",
        "cancelled",
        "read_error",
        "read_cancelled",
    ],
)
@pytest.mark.parametrize(argnames="provider", argvalues=_sdk._PROVIDERS)
@pytest.mark.parametrize(
    argnames="route", argvalues=["default", "mounted", "proxy", "no_proxy"]
)
# Retain route, drain and accounting assertions around the same observed calls.
# pylint: disable-next=too-complex,too-many-statements
def test_native_http_routes_overlap_drain_and_close_without_hidden_retry(
    kind: str, monkeypatch: pytest.MonkeyPatch, provider: str, route: str
) -> None:
    """Use real HTTPcore pools and proxy handshakes over synthetic network streams.

    Parameters
    ----------
    kind
        Successful return, unknown connection error, or active cancellation.
    monkeypatch
        Replace only network backends and the isolated-client factory.
    provider
        Actual provider SDK and parser.
    route
        Default, explicit mount, environment proxy, or environment bypass.
    """
    models = [_sdk._model(provider) for _ in range(3)]
    agents = [Agent(model=model, retries=0) for model in models]
    original_client = httpx.AsyncClient
    gate, closed = LPDispatchGate(), Event()
    factory_ready = Event()
    clients: list[httpx.AsyncClient] = []
    networks: list[_Network] = []
    selected_routes: list[bool] = []
    if route in {"proxy", "no_proxy"}:
        monkeypatch.setenv(name="HTTPS_PROXY", value="http://proxy.invalid:8080")
    if route == "no_proxy":
        monkeypatch.setenv(name="NO_PROXY", value="offline.invalid")

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        """Retain native transports and install a fake below connection pooling.

        Parameters
        ----------
        kwargs
            Adapter-supplied timeout, headers and hooks.

        Returns
        -------
        httpx.AsyncClient
            Real routing and HTTP processing with socket-free network backends.
        """
        options = dict(kwargs)
        mounted = httpx.AsyncHTTPTransport() if route == "mounted" else None
        if mounted is not None:
            options["mounts"] = {"https://offline.invalid": mounted}
        client = original_client(**options)
        request = httpx.Request(method="POST", url="https://offline.invalid")
        body = _sdk._response(provider=provider, request=request).content
        network = _Network(body=body, closed=closed, kind=kind)
        # Replace sockets below the real default/mounted/proxy transports.
        transports = [client._transport, *client._mounts.values()]
        for transport in transports:
            if transport is not None:
                assert isinstance(transport, httpx.AsyncHTTPTransport)
                transport._pool._network_backend = network
        selected = client._transport_for_url(request.url)
        selected_routes.append(
            selected is mounted if mounted else selected is client._transport
        )
        clients.append(client)
        networks.append(network)
        if len(networks) == 2:
            factory_ready.set()
        return client

    monkeypatch.setattr(
        name="httpx", target=lp_dispatch, value=SimpleNamespace(AsyncClient=_client)
    )
    usages = [RunUsage(), RunUsage(), RunUsage()]
    errors: list[BaseException | None] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _sdk._run, agent=agents[index], gate=gate, usage=usages[index]
            )
            for index in range(2)
        ]
        try:
            # Each factory completes before its connection barrier is published.
            for future in futures:
                assert not future.done()
            # Factory and connection barriers prove overlap without timing sleeps.
            assert factory_ready.wait(timeout=20)
            assert all(network.entered.wait(timeout=20) for network in networks)
            with gate.coordinate():
                gate.close()
                closed.set()
            assert not any(future.done() for future in futures)
        finally:
            for network in networks:
                network.finish()
        for future in futures:
            try:
                result = future.result(timeout=20)
                assert kind == "success" and result.output == "ok"
                errors.append(None)
            except BaseException as error:  # pylint: disable=broad-exception-caught
                assert kind != "success"
                if kind.endswith("cancelled"):
                    assert isinstance(error, asyncio.CancelledError)
                else:
                    assert error.__class__.__name__ == "ModelAPIError"
                errors.append(error)
    with pytest.raises(expected_exception=LPDispatchClosed):
        _sdk._run(agent=agents[2], gate=gate, usage=usages[2])
    assert all(client.is_closed for client in clients)
    assert all(
        not model.client.is_closed() and model.client.max_retries == 2
        for model in models
    )
    assert len(networks) == 3 and not networks[2].hosts
    expected_host = "proxy.invalid" if route == "proxy" else "offline.invalid"
    assert [network.hosts for network in networks[:2]] == [
        [expected_host],
        [expected_host],
    ]
    assert selected_routes == [route != "proxy"] * 3
    aggregate = KGUsageTracker()
    for observed_error, usage in zip(errors, usages[:2], strict=True):
        tracker = KGUsageTracker()
        tracker.lp_generation.add_run_usage(usage)
        outcome = lp_generation._LPAttemptOutcome(
            error=observed_error, payload=None, usage_tracker=tracker
        )
        lp_generation._merge_lp_usage(outcome=outcome, target=aggregate)
    assert aggregate.lp_generation.requests == (2 if kind == "success" else 0)
    assert aggregate.lp_unknown_usage_attempts == (0 if kind == "success" else 2)
    assert usages[2].requests == 0
    streams = [stream for network in networks for stream in network.streams]
    if kind in {"success", "read_error", "read_cancelled"}:
        assert len(streams) == 2 and all(stream.closes == 1 for stream in streams)
        assert all(b"POST " in b"".join(stream.writes) for stream in streams)
        assert all(
            (b"CONNECT " in b"".join(stream.writes)) == (route == "proxy")
            for stream in streams
        )
    else:
        assert not streams
    assert (
        aggregate.lp_generation.input_tokens,
        aggregate.lp_generation.output_tokens,
    ) == ((14, 6) if kind == "success" else (0, 0))
