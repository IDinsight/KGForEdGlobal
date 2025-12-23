"""This module contains fixtures for backend tests.

NB: Fixtures provide a way to set up a consistent and isolated environment for tests.
They typically handle initialization, cleanup, etc. Mocks, on the other hand, are used
to simulate the behavior of real objects or dependencies that tests interact with,
especially when those dependencies are slow, complex, or external.
"""

# Standard Library
import importlib
import json
import sys

from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, AsyncGenerator, Generator, Optional

# Third Party Library
import logfire
import pytest

from redis import asyncio as aioredis

# Append the framework path.
PACKAGE_PATH = Path(__file__).resolve().parents[2]
if PACKAGE_PATH / "backend" / "src" not in sys.path:
    print(f"Appending '{PACKAGE_PATH / 'backend' / 'src'}' to system path...")
    sys.path.append(str(PACKAGE_PATH / "backend" / "src"))
if PACKAGE_PATH / "backend" / "tests" not in sys.path:
    print(f"Appending '{PACKAGE_PATH / 'backend' / 'tests'}' to system path...")
    sys.path.append(str(PACKAGE_PATH / "backend" / "tests"))

# Package Library
from skg.utils import logging_  # noqa: E402
from tests.constants import REDIS_URL  # noqa: E402
from tests.types_ import (  # noqa: E402
    InstallLoguruMock,
    InstallWriteJsonMock,
    LogCall,
    WriteCall,
)


# Fixtures.
@pytest.fixture(scope="function")
def fixture_loguru_capture() -> Generator[list[str], None, None]:
    """Attach a simple sink to a module's loguru logger and collect messages.

    Yields
    ------
    Generator[list[str], None, None]
        The list of captured log messages.
    """

    captured_msg: list[str] = []

    def sink(msg: str) -> None:
        """A simple sink that appends messages to a list.

        NB: `msg` is a loguru Message whereas str(msg) includes formatted line.

        Parameters
        ----------
        msg
            The log message.
        """

        captured_msg.append(str(msg))

    sink_id = logging_.logger.add(sink, format="{message}", level="DEBUG")

    try:
        yield captured_msg
    finally:
        logging_.logger.remove(sink_id)


@pytest.fixture(scope="function")
async def fixture_redis_client() -> AsyncGenerator[aioredis.Redis, None]:
    """Create a redis client for testing.

    Yields
    ------
    Generator[aioredis.Redis, None, None]
        Redis client for testing.
    """

    rclient = await aioredis.from_url(REDIS_URL, decode_responses=True)

    await rclient.flushdb()

    yield rclient

    await rclient.aclose()


@pytest.fixture(scope="function")
def mock_loguru_logger(monkeypatch: pytest.MonkeyPatch) -> InstallLoguruMock:
    """Mock `loguru` logger into a target module, capturing log calls.

    Usage:
        calls = mock_loguru_logger(target_module, raise_on_level=True)
        calls = mock_loguru_logger(target_module, fixed_level_name="INFO")

    Parameters
    ----------
    monkeypatch
        Pytest monkeypatch fixture.

    Returns
    -------
    InstallLoguruMock
        The installer function that patches `logger` in the target module.
    """

    def install(
        target_module: ModuleType,
        *,
        fixed_level_name: str | None = None,
        raise_on_level: bool = False,
    ) -> list[LogCall]:
        """Patch `target_module.logger` with a fake that captures log calls.

        Parameters
        ----------
        target_module
            The module where to patch `logger`.
        fixed_level_name
            If provided, all log calls will appear to use this level name.
        raise_on_level
            If True, calling `logger.level(name)` will raise a KeyError, simulating an
            unknown log level.

        Returns
        -------
        list[LogCall]
            The list that will capture the log calls.
        """

        calls: list[LogCall] = []

        class StubLogger:
            """A stub logger mimicking key loguru methods."""

            @staticmethod
            def exception(  # pylint: disable=unused-argument
                message: str,
                *args: Any,
                **kwargs: Any,
            ) -> None:
                """Capture the log call arguments for `logger.exception`.

                Parameters
                ----------
                message
                    The log message.
                args
                    Additional positional arguments (ignored).
                kwargs
                    Additional keyword arguments (ignored).
                """

                calls.append(
                    LogCall(
                        level="EXCEPTION",
                        message=message,
                        opt_kwargs={"exception": True},
                    )
                )

            @staticmethod
            def level(name: str) -> SimpleNamespace:
                """A stub for `logger.level(name)` that either raises KeyError or
                returns an object with a `.name` attribute.

                Parameters
                ----------
                name
                    The log level name.

                Returns
                -------
                Any
                    An object with a `.name` attribute.

                Raises
                ------
                KeyError
                    If `raise_on_level` is True, raises KeyError to simulate unknown
                    level.
                """

                if raise_on_level:
                    raise KeyError("unknown level")

                # Return an object with a `.name` attribute (loguru-like).
                lvl_name = fixed_level_name if fixed_level_name is not None else name

                return SimpleNamespace(name=lvl_name)

            @staticmethod
            def opt(**kwargs: dict[str, Any]) -> object:
                """A stub for `logger.opt(...)` that captures the kwargs and returns an
                object with a `.log(level, message)` method.

                Parameters
                ----------
                kwargs
                    Optional keyword arguments like `depth`, `exception`, etc.

                Returns
                -------
                object
                    An object with a `.log(level, message)` method.
                """

                opt_kwargs = dict(kwargs)

                class L:
                    """A stub logger with a `.log(level, message)` method."""

                    @staticmethod
                    def log(level: int | str, message: str) -> None:
                        """Capture the log call arguments.

                        Parameters
                        ----------
                        level
                            The log level (int or str).
                        message
                            The log message.
                        """

                        calls.append(
                            LogCall(level=level, message=message, opt_kwargs=opt_kwargs)
                        )

                return L()

        def _make_level(name: str) -> staticmethod:
            """Create a static method for a log level function.

            Parameters
            ----------
            name
                The log level name (e.g., 'info', 'error').

            Returns
            -------
            staticmethod
                A static method that captures log calls at this level.
            """

            def _fn(  # pylint: disable=unused-argument
                message: str,
                *args: Any,
                **kwargs: Any,
            ) -> None:
                """Capture the log call arguments.

                Parameters
                ----------
                message
                    The log message.
                args
                    Additional positional arguments (ignored).
                kwargs
                    Additional keyword arguments (ignored).
                """

                calls.append(
                    LogCall(level=name.upper(), message=message, opt_kwargs={})
                )

            return staticmethod(_fn)

        for _lvl in (
            "debug",
            "critical",
            "error",
            "info",
            "success",
            "trace",
            "warning",
        ):
            setattr(StubLogger, _lvl, _make_level(_lvl))

        monkeypatch.setattr(target_module, "logger", StubLogger())
        return calls

    return install


@pytest.fixture(autouse=True)
def mock_silence_logfire_and_otel_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable Logfire and OpenTelemetry instrumentation for tests by uninstrumenting
    anything that might have been instrumented already.

    Parameters
    ----------
    monkeypatch
        pytest's monkeypatch fixture.
    """

    # 1. Disable the OpenTelemetry SDK entirely for tests.
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    # 2. Disable Logfire’s Pydantic plugin (if present).
    monkeypatch.setenv("LOGFIRE_PYDANTIC_RECORD", "off")
    monkeypatch.setenv("PYDANTIC_DISABLE_PLUGINS", "true")

    # 3. Proactively uninstrument common OTEL instrumentations that Logfire may have
    # enabled (`logfire` delegates to OTEL instrumentors (e.g.,
    # HTTPXClientInstrumentor().instrument()).
    def _try_uninstrument(mod_name: str, cls_name: str) -> None:
        """Try to uninstrument a given instrumentor class from a module.

        Parameters
        ----------
        mod_name
            The module name where the instrumentor class is located.
        cls_name
            The class name of the instrumentor.
        """

        try:
            mod = importlib.import_module(mod_name)
            inst_cls = getattr(mod, cls_name, None)
            if inst_cls is not None:
                inst = inst_cls()

                # Some instrumentors raise if not instrumented; swallow any errors.
                try:
                    inst.uninstrument()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # Add/remove as needed for tests.
    for name in (
        "instrument_httpx",
        "instrument_requests",
        "instrument_fastapi",
        "instrument_sqlalchemy",
        "instrument_asyncpg",
        "instrument_aiohttp_client",
        "instrument_aiohttp_server",
        "instrument_redis",
        "instrument_pydantic",
        "instrument_openai",
        "instrument_google_genai",
        "instrument_anthropic",
    ):
        if hasattr(logfire, name):
            monkeypatch.setattr(
                logfire, name, (lambda *args, **kwargs: None), raising=False
            )


@pytest.fixture(scope="function")
def mock_write_to_json(monkeypatch: pytest.MonkeyPatch) -> InstallWriteJsonMock:
    """Mock `write_to_json` into a target module, writing JSON to disk and capturing
    calls.

    Usage:
        calls = mock_write_to_json(target_module, dump_kwargs={"indent": 2})

        # Run code that calls target_module.write_to_json(fp, payload)
        assert calls[0]["fp"].exists()
        assert json.loads(calls[0]["text"]) == calls[0]["payload"]

    Parameters
    ----------
    monkeypatch
        Pytest monkeypatch fixture.

    Returns
    -------
    InstallWriteJsonMock
        The installer function that patches `write_to_json` in the target module.
    """

    calls: list[WriteCall] = []

    def install(
        target_module: ModuleType,
        *,
        dump_kwargs: Optional[dict[str, Any]] = None,
        ensure_parent: bool = True,
    ) -> list[WriteCall]:
        """Patch `target_module.write_to_json` with a fake that writes JSON to disk and
        records each call.

        Parameters
        ----------
        target_module
            The module where to patch `write_to_json`.
        dump_kwargs
            Optional keyword arguments to pass to `json.dump` when writing the file.
        ensure_parent
            Whether to ensure the parent directory exists before writing the file.

        Returns
        -------
        list[WriteCall]
            The list that will capture the call arguments.
        """

        dump_kwargs = dump_kwargs or {}

        def fake_write_to_json(fp: Path, payload: dict[str, Any]) -> None:
            """A fake `write_to_json` that writes the payload to disk and captures the
            call arguments.

            Parameters
            ----------
            fp
                File path to write the JSON payload.
            payload
                The JSON-serializable payload to write.
            """

            if ensure_parent:
                fp.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(payload, **dump_kwargs)
            fp.write_text(text, encoding="utf-8")
            calls.append(WriteCall(fp=fp, payload=payload, text=text))

        # NB: patch where it's imported/used (the module under test), not the original
        # definition.
        monkeypatch.setattr(target_module, "write_to_json", fake_write_to_json)

        return calls

    return install


# Conftest helpers.
