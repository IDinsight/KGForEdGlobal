"""This module contains the main configurations for the backend.

Any configurations added to backend/.env.local should be added to `BackendSettings` as
well.
"""

# Standard Library
import uuid

from typing import Annotated, Any, Literal, Optional

# Third Party Library
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    """Pydantic settings for backend."""

    # Chat
    CHAT_ENV: Literal["dev", "prod", "testing"] = "testing"

    # FastAPI
    FASTAPI_API_KEY: SecretStr
    FASTAPI_DOCS_PASSWORD: SecretStr
    FASTAPI_DOCS_USER: SecretStr
    FASTAPI_ENV: Literal["dev", "local", "prod"] = "local"
    FASTAPI_HOST: str = "0.0.0.0"
    FASTAPI_PORT: int = 8000

    # LiteLLM
    LITELLM_MODEL_CHAT: str = "gpt-4o"
    LITELLM_MODEL_DEFAULT: str = "gpt-4o"

    # Logging
    LOGGING_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Namespaces
    LC_CANONICAL_NAMESPACE_UUID: uuid.UUID = uuid.UUID(
        "3f6b9f2a-7d8a-5d85-a9c3-9f3b8d3c3f4b"
    )
    PROJECT_NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "skg:canonical-ir:v1")

    # Postgres
    POSTGRES_ASYNC_API: str = Field("asyncpg", validation_alias="POSTGRES_ASYNC_API")
    POSTGRES_DB: str = Field("skg-local", validation_alias="POSTGRES_DB")
    POSTGRES_DB_POOL_SIZE: int = Field(10, validation_alias="POSTGRES_DB_POOL_SIZE")
    POSTGRES_HOST: str = Field("localhost", validation_alias="POSTGRES_HOST")
    POSTGRES_PASSWORD: str = Field("postgres", validation_alias="POSTGRES_PASSWORD")
    POSTGRES_PORT: str = Field("5432", validation_alias="POSTGRES_PORT")
    POSTGRES_SYNC_API: str = Field("psycopg2", validation_alias="POSTGRES_SYNC_API")
    POSTGRES_USER: str = Field("postgres", validation_alias="POSTGRES_USER")

    # Redis
    REDIS_CACHE_PREFIX_CHAT: str = "chat-sessions"
    REDIS_CACHE_PREFIX_DB_INITIALIZED: str = "DB_INITIALIZED"
    REDIS_URL: Annotated[
        str, Field(pattern=r"^rediss?://")  # allows redis:// or rediss://
    ] = "redis://localhost:6379"

    # Sentry
    SENTRY_DSN: Optional[SecretStr] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0

    # Text generation parameters.
    TEXT_GENERATION_DEFAULT: dict[str, Any] = Field(
        default_factory=lambda: {
            "frequency_penalty": 0.0,
            "n": 1,
            "presence_penalty": 0.0,
            "temperature": 0.0,
            "top_p": 0.9,
        }
    )
    TEXT_GENERATION_OPENAI: dict[str, Any] = Field(
        default_factory=lambda: {
            "frequency_penalty": 0.0,
            "n": 1,
            "presence_penalty": 0.0,
            "temperature": 0.0,
            "top_p": 0.9,
        }
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="allow"
    )

    @classmethod
    def create_sync_postgres_db_url(cls) -> str:
        """Create the synchronous PostgreSQL database URL.

        Returns
        -------
        str
            The PostgreSQL database URL.
        """

        return f"postgresql+{cls().POSTGRES_SYNC_API}://{cls().POSTGRES_USER}:{cls().POSTGRES_PASSWORD}@{cls().POSTGRES_HOST}:{cls().POSTGRES_PORT}/{cls().POSTGRES_DB}"


Settings: BackendSettings = BackendSettings()
