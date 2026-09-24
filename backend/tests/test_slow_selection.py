"""Exercise the real slow-test switch through isolated pytest subprocesses."""

# Standard Library
from pathlib import Path

# Third Party Library
import pytest

# Package Library
from tests import pytest_slow

pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    argnames="arguments,passed,deselected,exit_code",
    argvalues=[
        ([], 1, 4, 0),
        (["--run-slow"], 4, 0, 1),
        (["--run-slow", "-m", "slow"], 3, 1, 1),
        (["--run-slow", "-m", "not slow"], 1, 4, 0),
        (["test_example.py::test_slow_failure"], 0, 1, 5),
        (["--run-slow", "test_example.py::test_slow_failure"], 0, 0, 1),
    ],
)
def test_slow_selection_preserves_explicit_execution_and_failure_status(
    *,
    arguments: list[str],
    passed: int,
    deselected: int,
    exit_code: int,
    monkeypatch: pytest.MonkeyPatch,
    pytester: pytest.Pytester,
) -> None:
    """Check default exclusion, inherited/parameter marks, opt-in, and real failures."""
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    pytester.makeconftest(Path(pytest_slow.__file__).read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_example="""
        import pytest

        def test_fast():
            assert True

        @pytest.mark.slow
        def test_slow_failure():
            assert False, "opted-in failure must propagate"

        @pytest.mark.slow
        class TestSlow:
            def test_inherited_marker(self):
                assert True

        @pytest.mark.parametrize("value", [
            pytest.param(1, marks=pytest.mark.slow),
            pytest.param(2, marks=pytest.mark.slow),
        ])
        def test_slow_parameter(value):
            assert value > 0
        """
    )
    result = pytester.runpytest_subprocess("--strict-markers", "-q", *arguments)
    result.assert_outcomes(
        passed=passed, failed=int(exit_code == 1), deselected=deselected
    )
    assert result.ret == exit_code
