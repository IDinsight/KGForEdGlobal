"""This module contains fixtures for backend tests.

NB: Fixtures provide a way to set up a consistent and isolated environment for tests.
They typically handle initialization, cleanup, etc. Mocks, on the other hand, are used
to simulate the behavior of real objects or dependencies that tests interact with,
especially when those dependencies are slow, complex, or external.
"""

# Standard Library
import importlib
import sys

from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Generator
from unittest.mock import MagicMock

# Third Party Library
import logfire
import numpy as np
import pymupdf
import pytest

from PIL import Image, ImageDraw

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
from tests.constants import FIXTURES_DIR  # noqa: E402
from tests.types_ import InstallLoguruMock, LogCall  # noqa: E402


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


@pytest.fixture(scope="module")
def fixture_pdf_doc() -> Generator[pymupdf.Document, None, None]:
    """Open the PDF once for all tests to save time.

    Yields
    ------
    Generator[pymupdf.Document, None, None]
        The opened PDF document.
    """

    doc = pymupdf.open(FIXTURES_DIR / "utils" / "tanzania.pdf")

    yield doc

    doc.close()


@pytest.fixture
def synthetic_blank_page(tmp_path: Path) -> Path:
    """Create a perfectly white page.

    Parameters
    ----------
    tmp_path
        The temporary path fixture.

    Returns
    -------
    Path
        The path to the created blank image.
    """

    p = tmp_path / "blank.png"
    img = Image.new("RGB", (2480, 3508), "white")  # A4 at 300 DPI-ish
    img.save(p)

    return p


@pytest.fixture
def synthetic_dirty_blank_page(tmp_path: Path) -> Path:
    """Create a page that is blank to the human eye but has scanner noise/grain
    (off-white pixels).

    Parameters
    ----------
    tmp_path
        The temporary path fixture.

    Returns
    -------
    Path
        The path to the created dirty blank image.
    """

    p = tmp_path / "dirty_blank.png"
    w, h = 1000, 1400

    # Add random noise (240-255 range) - lighter than ink threshold.
    noise = np.random.randint(240, 256, (h, w), dtype=np.uint8)
    img = Image.fromarray(noise, mode="L")
    img.save(p)

    return p


@pytest.fixture
def synthetic_page_with_speck(tmp_path: Path) -> Path:
    """Create a blank page with a single small black dot (e.g. dust). Should still be
    considered blank.

    Parameters
    ----------
    tmp_path
        The temporary path fixture.

    Returns
    -------
    Path
        The path to the created image with a speck.
    """

    p = tmp_path / "speck.png"
    img = Image.new("L", (1000, 1400), "white")
    draw = ImageDraw.Draw(img)

    # Draw a small 5x5 black dot in the center.
    draw.rectangle([500, 700, 505, 705], fill="black")
    img.save(p)

    return p


# Mocks.
@pytest.fixture
def mock_empty_struct_page() -> MagicMock:
    """Return a Mock page that simulates:

    1. Normal dimensions (rect.width/height).
    2. get_text("dict") -> returns empty blocks (failure case).
    3. get_text("text") -> returns raw string (fallback case).

    Returns
    -------
    MagicMock
        The mocked page.
    """

    page = MagicMock()
    page.rect.width = 100
    page.rect.height = 100

    def side_effect(option: str) -> dict[str, list] | str:
        """Side effect for `get_text` method.

        Parameters
        ----------
        option
            The option passed to `get_text`.

        Returns
        -------
        dict[str, list] | str
            The simulated return value.
        """

        if option == "dict":
            return {"blocks": []}  # Simulate structured read failure

        return "Raw Fallback Text"  # Simulate raw read success

    page.get_text.side_effect = side_effect

    return page


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


# Conftest helpers.
