"""This module contains the main configurations for the backend.

Any configurations added to backend/.env.local should be added to `BackendSettings` as
well.
"""

# Standard Library
import uuid

from pathlib import Path
from typing import Literal

# Third Party Library
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Package Library
from kgfeg.model_registry import ModelConfig


class BackendSettings(BaseSettings):
    """Pydantic settings for backend."""

    # Chat
    CHAT_ENV: Literal["dev", "prod", "local", "testing"] = "local"

    # Learning Commons
    LEARNING_COMMONS_EXPORT_SCHEMA_VERSION: str = ""

    # LLM
    LLM_ANTHROPIC_EFFORT: str = "high"
    LLM_ANTHROPIC_THINKING_BUDGET_TOKENS: int = 16384
    LLM_MAX_OUTPUT_TOKENS: int = 18432
    LLM_KG_MODEL: str = "anthropic:claude-opus-4-8"
    LLM_LC_EVAL_JUDGE_MODEL: str = "anthropic:claude-opus-5"
    LLM_OPENAI_REASONING_EFFORT: str = "high"
    LLM_OPENAI_TEMPERATURE: float = 0.0
    LLM_OPENAI_TOP_P: float = 0.95
    LLM_PAGE_IR_EXTRACTION_MODEL: str = "anthropic:claude-opus-4-8"
    LLM_PAGE_IR_VERIFICATION_MODEL: str = "anthropic:claude-opus-4-8"

    # Logging
    LOGGING_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Namespaces
    LC_CANONICAL_NAMESPACE_UUID: uuid.UUID = uuid.UUID(
        "3f6b9f2a-7d8a-5d85-a9c3-9f3b8d3c3f4b"
    )

    # Paths
    PATHS_PROJECT_DIR: Path

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="allow"
    )

    @field_validator("LEARNING_COMMONS_EXPORT_SCHEMA_VERSION", mode="before")
    @classmethod
    def validate_learning_commons_export_schema_version(cls, value: str) -> str:
        """Validate the Learning Commons export schema version.

        Parameters
        ----------
        value
            Raw environment value for the Learning Commons export schema version.

        Returns
        -------
        str
            The stripped non-empty schema version.

        Raises
        ------
        TypeError
            If the configured value is not a string.
        ValueError
            If the configured string is blank after stripping whitespace.
        """

        if not isinstance(value, str):
            raise TypeError("LEARNING_COMMONS_EXPORT_SCHEMA_VERSION must be a string.")

        value_clean = value.strip()

        if not value_clean:
            raise ValueError(
                "LEARNING_COMMONS_EXPORT_SCHEMA_VERSION must be a non-empty string."
            )

        return value_clean

    def _llm_type_registry(self, model_type: str) -> str:
        """Registry mapping LLM model types to their corresponding ModelConfig builders.

        Parameters
        ----------
        model_type
            The type of model configuration to retrieve. Expected values are:
                1. "page_ir_extraction" - for page IR extraction agents.
                2. "page_ir_verification" - for page IR verification agents.
                3. "kgs" - for knowledge graph construction agents.
                4. "lc_eval_judge" - for the Learning Components evaluation judge.

        Returns
        -------
        str
            The model string corresponding to the specified model type.

        Raises
        ------
        ValueError
            If an unsupported model type is provided.
        """

        match model_type:
            case "kgs":
                return self.LLM_KG_MODEL
            case "lc_eval_judge":
                return self.LLM_LC_EVAL_JUDGE_MODEL
            case "page_ir_extraction":
                return self.LLM_PAGE_IR_EXTRACTION_MODEL
            case "page_ir_verification":
                return self.LLM_PAGE_IR_VERIFICATION_MODEL
            case _:
                raise ValueError(f"Unsupported model type: {model_type}")

    def llm_config(self, model_type: str) -> ModelConfig:
        """Build a ModelConfig from env-driven fields.

        Parameters
        ----------
        model_type
            The type of model configuration to build. Expected values are:
                1. "page_ir_extraction" - for page IR extraction agents.
                2. "page_ir_verification" - for page IR verification agents.
                3. "kg" - for knowledge graph construction agents.
                4. "lc_eval_judge" - for the Learning Components evaluation judge.

        Returns
        -------
        ModelConfig
                The constructed ModelConfig.
        """

        return ModelConfig(
            anthropic_effort=self.LLM_ANTHROPIC_EFFORT,
            anthropic_thinking_budget_tokens=self.LLM_ANTHROPIC_THINKING_BUDGET_TOKENS,
            max_output_tokens=self.LLM_MAX_OUTPUT_TOKENS,
            model=self._llm_type_registry(model_type),
            openai_reasoning_effort=self.LLM_OPENAI_REASONING_EFFORT,
            openai_temperature=self.LLM_OPENAI_TEMPERATURE,
            openai_top_p=self.LLM_OPENAI_TOP_P,
        )


# Required fields are supplied by the environment, which the pydantic mypy plugin
# cannot see when it types the synthesized `__init__`.
Settings: BackendSettings = BackendSettings()  # type: ignore[call-arg]
