"""This module contains the main configurations for the backend.

Any configurations added to backend/.env.local should be added to `BackendSettings` as
well.
"""

# Standard Library
import uuid

from typing import Literal

# Third Party Library
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    """Pydantic settings for backend."""

    # Chat
    CHAT_ENV: Literal["dev", "prod", "local"] = "local"

    # Logging
    LOGGING_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Namespaces
    LC_CANONICAL_NAMESPACE_UUID: uuid.UUID = uuid.UUID(
        "3f6b9f2a-7d8a-5d85-a9c3-9f3b8d3c3f4b"
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="allow"
    )


Settings: BackendSettings = BackendSettings()
