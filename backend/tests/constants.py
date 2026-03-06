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
