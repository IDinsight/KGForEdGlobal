"""This module contains utilities for logging."""

# pylint: disable=W0603
# Standard Library
import functools
import inspect
import logging
import os
import sys

from pathlib import Path
from typing import Any, Callable, Optional

# Third Party Library
import logfire
import loguru

from loguru import logger

# Package Library
from kgfeg.config import Settings
from kgfeg.utils.general import Valid, recurse_replace, redact_tokens

_LOGGER_INITIALIZED = False
LOGGING_LOG_LEVEL = Settings.LOGGING_LOG_LEVEL

# Register custom log levels immediately so that they can be intercepted appropriately.
logger.level("DEBUG", color="<white>", icon="🐞")
logger.level("INFO", color="<cyan>", icon="ℹ️")
logger.level("WARNING", color="<bold><magenta>", icon="⚠️")
logger.level("ERROR", color="<bold><red>", icon="❗")
logger.level("ATTN", no=35, color="<bold><yellow>", icon="🚨")
logger.level("CHAT", no=15, color="<bold><blue>", icon="💬")
logger.level("CELEBRATE", no=25, color="<bold><green>", icon="🎉")


class InterceptHandler(logging.Handler):
    """A logging handler that intercepts standard library `logging` records and
    forwards them to Loguru for consistent logging across the application.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a logging record by forwarding it to loguru.

        Parameters
        ----------
        record
            The log record emitted by the standard logging system.
        """

        try:
            # Try to map the stdlib log level name to loguru level.
            level: int | str = logger.level(record.levelname).name
        except KeyError:
            # Fall back to the numeric level if the name is unknown to loguru.
            level = record.levelno

        # Traverse the call stack to find the original log call.
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_back and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _escape_angle_brackets(x: Any) -> str:
    """Escape angle brackets for colorized logging. If this is not done, then
    `loguru` will throw a `ValueError` when attempting to log objects with angle
    brackets. See: https://github.com/Delgan/loguru/issues/140 for more details.

    Parameters
    ----------
    x
        Any object.

    Returns
    -------
    str
        The string version of `x` with escaped angle brackets.
    """

    return recurse_replace(r"\>", ">", recurse_replace(r"\<", "<", str(x)))


def _generate_entry_log_str(
    *, args: Any, extra_args: list[str], kwargs: Any, name: str
) -> str:
    """Generate a log string for function entry.

    Parameters
    ----------
    args
        Additional positional arguments.
    extra_args
        List of additional items to log when entering a function.
    kwargs
        Additional keyword arguments
    name
        The name of the function to log.

    Returns
    -------
    str
        The log string for function entry.
    """

    a_str = _escape_angle_brackets(args)
    k_str = _escape_angle_brackets(kwargs)
    extra_args_str = "\n".join(
        f"{_escape_angle_brackets(arg)}: {_escape_angle_brackets(getattr(args[0], arg, 'N/A'))}"
        for arg in extra_args
    )
    return f"ENTERING: '{name}'\n\nargs:\n{a_str}\n\nkwargs:\n{k_str}\n\nextra_args:\n{extra_args_str}"


def _generate_exit_log_str(*, name: str, result: Any) -> str:
    """Generate a log string for function entry.

    Parameters
    ----------
    name
        The name of the function to log.
    result
        The result from calling the function to log.

    Returns
    -------
    str
        The log string for function exit.
    """

    r_str = _escape_angle_brackets(result)
    return f"EXITING: '{name}'\nresult={r_str}"


def initialize_logger(
    *,
    config: Optional[dict[str, Any]] = None,
    logging_level: str = LOGGING_LOG_LEVEL,
    log_fp: Optional[str | Path] = None,
) -> "loguru.Logger":
    """Initialize a `loguru` logger object.

    Parameters
    ----------
    config
        Dictionary used to initialize the logger object. If None, a default dictionary
        of parameters will be used.
    logging_level
        Specifies the logging level.
    log_fp
        If specified, then log will also be written to this filepath.

    Returns
    -------
    loguru.Logger
        `loguru` logger object.

    Raises
    ------
    ValueError
        If the logging level is an invalid valid.
    """

    global _LOGGER_INITIALIZED

    # Configure logfire.
    # logfire.configure(
    #     code_source=logfire.CodeSource(
    #         repository="https://github.com/IDinsight/KGForEdGlobal",
    #         revision="main",
    #     ),
    #     console=False,
    # )

    # Remove any default handlers attached to the root logger.
    logging.root.handlers = []

    # Install intercept handler at the root logger.
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(InterceptHandler())

    # Intercept all loggers.
    for existing_logger in logging.root.manager.loggerDict.values():
        if isinstance(existing_logger, logging.PlaceHolder):
            continue  # Skip incomplete logger definitions
        existing_logger.handlers = [InterceptHandler()]
        existing_logger.propagate = False

    # Prevent re-initialization and avoid running in Uvicorn's parent process during
    # --reload.
    if (
        _LOGGER_INITIALIZED
        and os.getenv("RUN_MAIN") != "true"
        and os.getenv("WERKZEUG_RUN_MAIN") != "true"
    ):
        return logger

    if not Valid.is_valid_logging_level(logging_level=logging_level):
        raise ValueError(
            f"Invalid logging level: {logging_level}. "
            f"Valid logging levels are: {Valid().logging_levels}"
        )

    logger.remove()

    config = config or {
        "handlers": [
            {
                "backtrace": True,
                "colorize": True,
                "diagnose": Settings.CHAT_ENV == "local",
                "enqueue": True,
                "filter": redact_tokens,
                "format": "<g>{time:YYYY-MM-DD HH:mm:ss}</g> | <level>{level.icon} {message}</level>",
                "level": logging_level,
                "serialize": False,
                "sink": sys.stderr,
            },
            logfire.loguru_handler(),
        ]
    }
    if log_fp:
        config["handlers"].append(
            {
                "backtrace": True,
                "delay": True,
                "diagnose": Settings.CHAT_ENV == "local",
                "encoding": "utf-8",
                "filter": redact_tokens,
                "level": logging_level,
                "rotation": "10 MB",
                "serialize": True,
                "sink": log_fp,
            },
        )

    logger.configure(**config)
    _LOGGER_INITIALIZED = True

    return logger


def log_func_call(
    *,
    entry: bool = True,
    exit_: bool = True,
    extra_args: Optional[list[str]] = None,
    level: str | int = LOGGING_LOG_LEVEL,
) -> Any:
    """Return a wrapper for logging entry and exit into functions.

    Parameters
    ----------
    entry
        Specifies whether the function is being entered.
    exit_
        Specifies whether the function is being exited.
    extra_args
        List of additional items to log when entering a function.
    level
        Specifies the logging level.

    Returns
    -------
    Any
        The return value from calling a function.
    """

    extra_args_ = extra_args or []
    logger_ = logger.opt(depth=1)

    def log_and_call(
        func: Callable, args: tuple, kwargs: dict, name: str, is_async: bool
    ) -> Any:
        """Log the entry and exit of a function call and executes the function,
        handling both synchronous and asynchronous functions.

        Parameters
        ----------
        func : Callable[..., Any]
            The function to be called (sync or async).
        args : tuple[Any, ...]
            Positional arguments to be passed to the function.
        kwargs : dict[str, Any]
            Keyword arguments to be passed to the function.
        name : str
            The fully-qualified name of the function, used in log messages.
        is_async : bool
            Whether the function is a coroutine (async) function.

        Returns
        -------
        Any
            The result of the function call, either from an awaited coroutine or a direct call.
        """

        if entry:
            logger_.log(
                level,
                _generate_entry_log_str(
                    args=args, extra_args=extra_args_, kwargs=kwargs, name=name
                ),
            )

        async def _async() -> Any:
            """Handle logging and execution of an asynchronous function.

            Returns
            -------
            Any
                The result of the awaited async function.
            """

            result = await func(*args, **kwargs)
            if exit_:
                logger_.log(level, _generate_exit_log_str(name=name, result=result))
            return result

        def _sync() -> Any:
            """Handle logging and execution of a synchronous function.

            Returns
            -------
            Any
                The result of the function call.
            """

            result = func(*args, **kwargs)
            if exit_:
                logger_.log(level, _generate_exit_log_str(name=name, result=result))
            return result

        return _async() if is_async else _sync()

    def wrapper(func: Callable) -> Callable:
        """Wrapper for logging entry and exit into functions.

        Parameters
        ----------
        func
            A function wrap for logging entry and exit.

        Returns
        -------
        Callable
            Function that logs entry and exit into functions.
        """

        name = func.__qualname__
        is_async = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            """Async wrapper for logging entry and exit into functions.

            Parameters
            ----------
            args
                Additional positional arguments.
            kwargs
                Additional keyword arguments

            Returns
            -------
            Any
                The return value from calling a function.
            """

            return await log_and_call(func, args, kwargs, name, is_async=True)

        @functools.wraps(func)
        def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
            """Sync wrapper for logging entry and exit into functions.

            Parameters
            ----------
            args
                Additional positional arguments.
            kwargs
                Additional keyword arguments

            Returns
            -------
            Any
                The return value from calling a function.
            """

            return log_and_call(func, args, kwargs, name, is_async=False)

        return async_wrapped if is_async else sync_wrapped

    return wrapper
