"""This module contains top-level Pydantic models."""

# Standard Library
from typing import Any, Callable, Literal

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field


class Limits(BaseModel):
    """Pydantic model for global limits."""

    max_retry_attempts: int = Field(
        10, ge=0, description="Must be a non-negative integer"
    )

    model_config = ConfigDict(from_attributes=True)


class Valid(BaseModel):
    """Pydantic model for global valid values."""

    completion_finish_reasons: tuple[
        Literal[None, "function_call", "length", "stop"], ...
    ] = (None, "function_call", "length", "stop")
    json_file_exts: tuple[Literal[".json", ".jsonl"], ...] = (".json", ".jsonl")
    logging_levels: tuple[
        Literal["CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING"], ...
    ] = ("CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING")

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def is_valid_completion_finish_reason(
        cls, *, completion_finish_reason: str
    ) -> bool:
        """Check if a given completion finish reason is valid.

        Parameters
        ----------
        completion_finish_reason
            The completion finish reason to check.

        Returns
        -------
        bool
            True if the completion finish reason is valid, False otherwise.
        """

        return completion_finish_reason in cls().completion_finish_reasons

    @classmethod
    def is_valid_json_file_ext(cls, *, file_ext: str) -> bool:
        """Check if a given JSON file extension is valid.

        Parameters
        ----------
        file_ext
            The file extension to check.

        Returns
        -------
        bool
            True if the file extension is valid, False otherwise.
        """

        return file_ext in cls().json_file_exts

    @classmethod
    def is_valid_logging_level(cls, *, logging_level: str) -> bool:
        """Check if a given logging level is valid.

        Parameters
        ----------
        logging_level
            The logging level to check.

        Returns
        -------
        bool
            True if the logging level is valid, False otherwise.
        """

        return logging_level in cls().logging_levels


# Validator for API responses.
class ValidatorCall(BaseModel):
    """Pydantic model for API response validation."""

    num_retries: int = 3
    validator_module: Callable[..., Any]
    validator_kwargs: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)
