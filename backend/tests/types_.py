"""This module defines types used across the test suite."""

# Standard Library
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Protocol, TypedDict


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
