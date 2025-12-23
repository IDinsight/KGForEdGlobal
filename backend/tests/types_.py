"""This module defines types used across the test suite."""

# Standard Library
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable, Optional, Protocol, TypedDict


class WriteCall(TypedDict):
    """TypedDict for a call to a write function."""

    fp: Path
    payload: dict[str, Any]
    text: str


class InstallWriteJsonMock(Protocol):
    """Protocol for the `mock_write_to_json` fixture installer function."""

    def __call__(
        self,
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


class ACompletionCall(TypedDict):
    """TypedDict for a call to an async completion function."""

    args: tuple
    kwargs: dict[str, Any]


class InstallGetACompletionMock(Protocol):
    """Protocol for the `mock_get_acompletion` fixture installer function."""

    def __call__(
        self,
        target_module: ModuleType,
        *,
        return_value: Optional[str] = None,
        return_values: Optional[list[str]] = None,
        side_effect: Optional[Callable[[dict[str, Any]], str | Awaitable[str]]] = None,
    ) -> list[ACompletionCall]:
        """Patch `target_module.get_acompletion` with an async fake.

        Parameters
        ----------
        target_module
            The module where to patch `get_acompletion`.
        return_value
            Constant string to return on every call.
        return_values
            Sequence of strings to return per call; if calls exceed length, the last
            value is reused.
        side_effect
            Function (sync or async) taking kwargs and returning a string. If provided,
            it overrides `return_value(s)`.

        Returns
        -------
        list[ACompletionCall]
            A live list of captured calls: [{"args": (), "kwargs": {...}}, ...].
        """


class LogCall(TypedDict, total=False):
    """TypedDict for a call to a logging function."""

    level: int | str
    message: str
    opt_kwargs: dict[str, Any]  # e.g., {"depth": 3, "exception": None}


class InstallLoguruMock(Protocol):
    """Protocol for the `mock_loguru` fixture installer function."""

    def __call__(
        self,
        target_module: ModuleType,
        *,
        fixed_level_name: str | None = None,
        raise_on_level: bool = False,
    ) -> list[LogCall]:
        """Patch `target_module.logger` with a stub that implements `.level()` and
        `.opt(...).log(...)`.

        Parameters
        ----------
        target_module
            The module where to patch `logger`.
        fixed_level_name
            If provided, `.level(...)` always returns this name.
        raise_on_level
            If True, `.level(...)` raises KeyError to test fallback paths.

        Returns
        -------
        list[LogCall]
            A live list of captured LogCall dicts (one per `.log(...)` invocation).
        """


class InstallValidateApiCall(Protocol):
    """Protocol for the `mock__validate_api_call` fixture installer function."""

    return_value: str
    calls: list[dict[str, Any]]

    def __call__(self, **kwargs: dict[str, Any]) -> Awaitable[str]:
        """Patch `validate_api_call` to record calls and return a fixed value.

        Parameters
        ----------
        **kwargs
            Arbitrary keyword arguments representing the parameters passed to
            `validate_api_call`.

        Returns
        -------
        Awaitable[str]
            The fixed return value specified when installing the mock.
        """

    @property
    def last(self) -> Optional[dict[str, Any]]:
        """Get the last call dict, or None if no calls have been made.

        Returns
        -------
        Optional[dict[str, Any]]
            The last call dictionary or None.
        """

    def reset(self) -> None:
        """Clear the call history."""
