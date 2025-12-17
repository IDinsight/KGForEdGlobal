"""This module defines constants used across the test suite."""

# Standard Library
from pathlib import Path

# Third Party Library
import pytest

PACKAGE_PATH = Path(__file__).resolve().parents[2]

# Assign default directories.
TESTS_DIR = PACKAGE_PATH / "backend" / "tests"
FIXTURES_DIR = TESTS_DIR / "fixtures"

# Define pytest marks.
ASYNC = pytest.mark.asyncio
PARAM = pytest.mark.parametrize
SKIP = pytest.mark.skip
SKIPIF = pytest.mark.skipif
XFAIL = pytest.mark.xfail

# Environment-based configuration with defaults.
POSTGRES_DB = "postgres-pg-vector-test"
POSTGRES_HOST = "localhost"
POSTGRES_PASSWORD = "pg-vector-test-pw"
POSTGRES_PORT = 5433
POSTGRES_USER = "pg-vector-test-user"
POSTGRES_ASYNC_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
REDIS_URL = "redis://localhost:6381"
