"""This is the main module for testing utils/logging_.py."""

# Standard Library
import asyncio
import logging
import re

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Optional

# Third Party Library
import pytest

# Package Library
from skg.utils import logging_
from tests.constants import ASYNC, PARAM
from tests.types_ import InstallLoguruMock

_UNESCAPED_ANGLE_BRACKET_RE = re.compile(pattern=r"(?<!\\)[<>]")


@PARAM(
    argnames=("expected", "x"),
    argvalues=[
        ("plain", "plain"),
        (r"\<tag\>", "<tag>"),
        (r"a\<b\>c", "a<b>c"),
        (r"\<\<\>\>", "<<>>"),
        ("None", None),
        ("123", 123),
    ],
)
def test__escape_angle_brackets_basic_cases(*, expected: str, x: Any) -> None:
    """Escape `<` and `>` in the string form of the input, with exact expected output.

    Parameters
    ----------
    expected
        The expected output string after escaping.
    x
        The input value to escape, which can be of any type. It will be converted to a
        string before escaping.
    """

    result = logging_._escape_angle_brackets(x=x)
    assert isinstance(result, str)
    assert result == expected


def test__escape_angle_brackets_does_not_mutate_containers() -> None:
    """Container inputs should not be mutated because the function operates on `str(x)`."""

    x: list[Any] = ["<a>", {"k": "<v>"}]
    x_before: list[Any] = deepcopy(x=x)
    _ = logging_._escape_angle_brackets(x=x)
    assert x == x_before


@PARAM(
    argnames=("x",),
    argvalues=[
        ("<>",),
        ("<<>>",),
        ("a<b>c",),
        ("prefix <tag> suffix",),
    ],
)
def test__escape_angle_brackets_escapes_every_angle_bracket(*, x: str) -> None:
    """All angle brackets in the output should be escaped (no raw `<` or `>`).

    Parameters
    ----------
    x
        The input string containing angle brackets to escape.
    """

    result = logging_._escape_angle_brackets(x=x)
    assert _UNESCAPED_ANGLE_BRACKET_RE.search(string=result) is None


def test__generate_entry_and_exit_log_str() -> None:
    """Test `generate_entry_log_str` and `generate_exit_log_str`."""

    class Obj:
        """A simple object with a `user_id` attribute."""

        def __init__(self) -> None:
            """Initialize the object with a `user_id` attribute."""

            self.user_id = 123

    args: tuple[Any, ...] = (Obj(), "a<b")  # args[0] has 'user_id'
    kwargs: dict[str, Any] = {"k": "v>z"}
    entry_str = logging_._generate_entry_log_str(
        args=args,
        extra_args=["user_id", "missing"],
        kwargs=kwargs,
        name="fn",
    )
    assert "ENTERING: 'fn'" in entry_str
    assert "args:\n" in entry_str and "kwargs:\n" in entry_str

    # `extra_args` section shows both present and missing attributes.
    assert "user_id: 123" in entry_str
    assert "missing: N/A" in entry_str

    exit_str = logging_._generate_exit_log_str(name="fn", result={"ok": "<yes>"})
    assert "EXITING: 'fn'" in exit_str

    # With stubbed escaper, the dict should be wrapped.
    assert "{'ok': '\\<yes\\>'}" in exit_str


def test_intercept_handler_forwards_stdlib_logs(
    fixture_loguru_capture: Iterable[str],
) -> None:
    """Test that `InterceptHandler` forwards stdlib logs into loguru.

    Parameters
    ----------
    fixture_loguru_capture
        The fixture that captures loguru messages.
    """

    std_logger = logging.getLogger("test_intercept")
    std_logger.handlers = [logging_.InterceptHandler()]
    std_logger.propagate = False
    std_logger.info("hello via stdlib")
    assert any("hello via stdlib" in line for line in fixture_loguru_capture)


def test_intercept_handler_falls_back_to_numeric_level(
    mock_loguru_logger: InstallLoguruMock,
) -> None:
    """Test that `InterceptHandler` falls back to numeric level if name unknown.

    Parameters
    ----------
    mock_loguru_logger
        The fixture that installs a mock loguru logger.
    """

    calls = mock_loguru_logger(logging_, raise_on_level=True)

    logging.addLevelName(51, "FOOBAR")
    rec = logging.LogRecord(
        args=(),
        exc_info=None,
        level=51,
        lineno=1,
        msg="fallback works",
        name="x",
        pathname=__file__,
    )

    logging_.InterceptHandler().emit(rec)

    assert calls and calls[0]["level"] == 51
    assert calls[0]["message"] == "fallback works"
    assert calls[0]["opt_kwargs"]["depth"] == 2
    assert calls[0]["opt_kwargs"]["exception"] is None


def test_intercept_handler_increments_depth_while_in_logging_frames(
    mock_loguru_logger: InstallLoguruMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that `InterceptHandler` increments depth while in logging frames by faking
    a chain of frames whose `co_filename` matches `logging.__file__` to force
    increments.

    Parameters
    ----------
    mock_loguru_logger
        The fixture that installs a mock loguru logger.
    monkeypatch
        The pytest monkeypatch fixture.
    """

    calls = mock_loguru_logger(logging_)  # No KeyError; level name passes through

    # Build a fake chain of logging frames of length n/
    n = 3
    logging_filename = logging.__file__

    class FakeCode:
        """A fake code object with a `co_filename` attribute."""

        def __init__(self, filename: str) -> None:
            """Initialize the fake code object with a filename.

            Parameters
            ----------
            filename
                The filename to set as `co_filename`.
            """

            self.co_filename = filename

    class FakeFrame:
        """A fake frame object with `f_code` and `f_back` attributes."""

        def __init__(self, depth_left: int, filename: str) -> None:
            """Initialize the fake frame object.

            Parameters
            ----------
            depth_left
                The number of frames left to create in the chain.
            filename
                The filename to set in the fake code object.
            """

            self.f_code = FakeCode(filename)
            self.f_back = (
                FakeFrame(depth_left - 1, filename) if depth_left > 0 else None
            )

    monkeypatch.setattr(
        logging,
        "currentframe",
        lambda: FakeFrame(depth_left=n, filename=logging_filename),
    )

    rec = logging.LogRecord(
        args=(),
        exc_info=None,
        level=logging.INFO,
        lineno=1,
        msg="depth test",
        name="x",
        pathname=__file__,
    )

    logging_.InterceptHandler().emit(rec)

    assert calls and calls[0]["message"] == "depth test"

    # Starts at 2, increments n times.
    assert calls[0]["opt_kwargs"]["depth"] == 2 + n

    assert calls[0]["level"] == "INFO"


def test_initialize_logger_builds_config_and_appends_file_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test `initialize_logger` builds config and appends file handler if given.

    Parameters
    ----------
    monkeypatch
        The pytest monkeypatch fixture.
    tmp_path
        The pytest temporary path fixture.
    """

    # No-op for external logfire configuration.
    monkeypatch.setattr(logging_.logfire, "configure", lambda **_: None)

    # Provide a fake logfire handler dict
    monkeypatch.setattr(
        logging_.logfire, "loguru_handler", lambda: {"sink": "logfire-sink"}
    )

    # Allow any level to be valid.
    monkeypatch.setattr(logging_.Valid, "is_valid_logging_level", lambda **_: True)

    # Keep Settings.CHAT_ENV predictable.
    monkeypatch.setattr(logging_.Settings, "CHAT_ENV", "local")

    # Avoid affecting the real logger configuration; just capture what would have been
    # configured.
    captured_cfgs: list[dict[str, Any]] = []

    def mock_configure(**cfg: Optional[dict[str, Any]]) -> None:
        """Capture the configuration dict.

        Parameters
        ----------
        **cfg
            The configuration dictionary.
        """

        captured_cfgs.append(cfg)

    # Mock out the real logger configure and remove methods.
    monkeypatch.setattr(logging_.logger, "configure", mock_configure)
    monkeypatch.setattr(logging_.logger, "remove", lambda: None)

    # Ensure function doesn't early-return.
    monkeypatch.setattr(logging_, "_LOGGER_INITIALIZED", False)

    # Call without file sink.
    lg = logging_.initialize_logger(logging_level="INFO")
    assert lg is logging_.logger
    assert captured_cfgs, "logger.configure should have been called"
    first_cfg = captured_cfgs[-1]
    assert "handlers" in first_cfg and isinstance(first_cfg["handlers"], list)

    # Expected: first handler (console-ish) + logfire handler.
    assert any(h.get("sink") == "logfire-sink" for h in first_cfg["handlers"])

    # Call with file sink to ensure an extra handler is appended.
    monkeypatch.setattr(logging_, "_LOGGER_INITIALIZED", False)
    captured_cfgs.clear()
    log_fp = tmp_path / "app.log"
    logging_.initialize_logger(logging_level="INFO", log_fp=log_fp)
    second_cfg = captured_cfgs[-1]
    sinks = [h.get("sink") for h in second_cfg["handlers"]]
    assert log_fp in sinks  # Path object allowed

    # Check idempotency: subsequent calls are no-ops.
    logging_.initialize_logger(logging_level="INFO")
    assert lg is logging_.logger


def test_initialize_logger_rejects_bad_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test `initialize_logger` raises ValueError if given invalid logging level.

    Parameters
    ----------
    monkeypatch
        The pytest monkeypatch fixture.
    """

    monkeypatch.setattr(logging_, "_LOGGER_INITIALIZED", False)
    monkeypatch.setattr(logging_.logger, "configure", lambda **_: None)  # Never called
    monkeypatch.setattr(logging_.logger, "remove", lambda: None)  # Never called
    monkeypatch.setattr(logging_.Valid, "is_valid_logging_level", lambda **_: False)

    with pytest.raises(ValueError) as e:
        logging_.initialize_logger(logging_level="NOPE")

    msg = str(e.value)
    allowed = logging_.Valid().logging_levels
    assert "Invalid logging level" in msg and "NOPE" in msg
    assert all(lvl in msg for lvl in allowed)


def test_log_func_call_sync_logs_entry_and_exit(
    fixture_loguru_capture: Iterable[str],
) -> None:
    """Test `log_func_call` decorator logs entry and exit for sync functions.

    Parameters
    ----------
    fixture_loguru_capture
        The fixture that captures loguru messages.
    """

    class C:
        """A simple class with a `user` attribute."""

        def __init__(self) -> None:
            """Initialize the class with a `user` attribute."""

            self.user = "fru"

    @logging_.log_func_call(
        entry=True, exit_=True, extra_args=["user", "missing"], level="INFO"
    )
    def add(self: C, x: int, y: int = 1) -> int:  # pylint: disable=unused-argument
        """Add two numbers.

        Parameters
        ----------
        self
            The instance of the class.
        x
            The first number.
        y
            The second number.

        Returns
        -------
        int
            The sum of the two numbers.
        """

        return x + y

    c = C()
    assert add(c, 2, y=3) == 5

    msg = "\n".join(fixture_loguru_capture)
    assert "ENTERING:" in msg and "EXITING:" in msg
    assert "user: fru" in msg and "missing: N/A" in msg
    assert "args:\n(" in msg and "kwargs:\n{" in msg


def test_log_func_call_sync_logs_entry_and_no_exit(
    fixture_loguru_capture: Iterable[str],
) -> None:
    """Test `log_func_call` decorator logs entry and not exit for sync functions.

    Parameters
    ----------
    fixture_loguru_capture
        The fixture that captures loguru messages.
    """

    class C:
        """A simple class with a `user` attribute."""

        def __init__(self) -> None:
            """Initialize the class with a `user` attribute."""

            self.user = "fru"

    @logging_.log_func_call(
        entry=True, exit_=False, extra_args=["user", "missing"], level="INFO"
    )
    def add(self: C, x: int, y: int = 1) -> int:  # pylint: disable=unused-argument
        """Add two numbers.

        Parameters
        ----------
        self
            The instance of the class.
        x
            The first number.
        y
            The second number.

        Returns
        -------
        int
            The sum of the two numbers.
        """

        return x + y

    c = C()
    assert add(c, 2, y=3) == 5

    msg = "\n".join(fixture_loguru_capture)
    assert "ENTERING:" in msg and "EXITING:" not in msg
    assert "user: fru" in msg and "missing: N/A" in msg
    assert "args:\n(" in msg and "kwargs:\n{" in msg


@ASYNC
async def test_log_func_call_async_logs_entry_and_exit(
    fixture_loguru_capture: Iterable[str],
) -> None:
    """Test `log_func_call` decorator logs entry and exit for async functions.

    Parameters
    ----------
    fixture_loguru_capture
        The fixture that captures loguru messages.
    """

    class C:
        """A simple class with a `req__id` attribute."""

        def __init__(self) -> None:
            """Initialize the class with a `req_id` attribute."""

            self.req_id = "r-123"

    @logging_.log_func_call(entry=True, exit_=True, extra_args=["req_id"], level="INFO")
    async def work(self: C, q: str) -> str:  # pylint: disable=unused-argument
        """A simple async function that uppercases a string.

        Parameters
        ----------
        self
            The instance of the class.
        q
            The string to uppercase.

        Returns
        -------
        str
            The uppercased string.
        """

        await asyncio.sleep(0)
        return q.upper()

    c = C()
    assert await work(c, "ok") == "OK"

    msg = "\n".join(fixture_loguru_capture)
    assert "ENTERING:" in msg and "EXITING:" in msg
    assert "req_id: r-123" in msg


@ASYNC
async def test_log_func_call_async_logs_entry_and_no_exit(
    fixture_loguru_capture: Iterable[str],
) -> None:
    """Test `log_func_call` decorator logs entry and not exit for async functions.

    Parameters
    ----------
    fixture_loguru_capture
        The fixture that captures loguru messages.
    """

    class C:
        """A simple class with a `req__id` attribute."""

        def __init__(self) -> None:
            """Initialize the class with a `req_id` attribute."""

            self.req_id = "r-123"

    @logging_.log_func_call(
        entry=True, exit_=False, extra_args=["req_id"], level="INFO"
    )
    async def work(self: C, q: str) -> str:  # pylint: disable=unused-argument
        """A simple async function that uppercases a string.

        Parameters
        ----------
        self
            The instance of the class.
        q
            The string to uppercase.

        Returns
        -------
        str
            The uppercased string.
        """

        await asyncio.sleep(0)
        return q.upper()

    c = C()
    assert await work(c, "ok") == "OK"

    msg = "\n".join(fixture_loguru_capture)
    assert "ENTERING:" in msg and "EXITING:" not in msg
    assert "req_id: r-123" in msg


def test_log_func_call_respects_entry_exit_flags(
    fixture_loguru_capture: Iterable[str],
) -> None:
    """Test `log_func_call` decorator respects entry and exit flags.

    Parameters
    ----------
    fixture_loguru_capture
        The fixture that captures loguru messages.
    """

    # Entry disabled, exit enabled.
    @logging_.log_func_call(entry=False, exit_=True, level="INFO")
    def ping() -> str:
        """A simple function that returns 'pong'.

        Returns
        -------
        str
            The string "pong".
        """

        return "pong"

    assert ping() == "pong"
    msg = "\n".join(fixture_loguru_capture)
    assert "EXITING:" in msg
    assert "ENTERING:" not in msg
