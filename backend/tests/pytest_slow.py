"""Keep expensive offline integration tests available through explicit opt-in."""

# Third Party Library
import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Expose the full-suite switch in pytest's command-line help."""

    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Include slow offline integration/scale tests (required for full review).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker for strict-marker runs and pytest --markers."""

    config.addinivalue_line(
        "markers",
        "slow: expensive offline integration/scale test; requires --run-slow",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Report slow cases as deselected unless the full suite was requested."""

    if config.getoption("--run-slow"):
        return

    selected, deselected = [], []

    for item in items:
        if item.get_closest_marker("slow") is not None:
            deselected.append(item)
        else:
            selected.append(item)

    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
