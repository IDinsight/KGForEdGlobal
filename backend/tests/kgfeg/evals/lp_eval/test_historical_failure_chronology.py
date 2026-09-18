"""Challenge historical serial stage progress and request completion chronology."""

# Future Library
from __future__ import annotations

# Standard Library
from pathlib import Path
from typing import Literal

# Third Party Library
import pytest

# Package Library
from kgfeg.entries import evaluate_lps as entry
from kgfeg.evals.lp_eval import compatibility, judge, sampling
from kgfeg.kgs.lp_checkpoints import _LPFailure
from tests.kgfeg.evals.lp_eval import test_current_input_contract as current
from tests.kgfeg.evals.lp_eval import test_projection_compatibility as projections
from tests.kgfeg.evals.lp_eval import test_snapshot_reader_matrix as matrix

# Private fixtures are injected by pytest and retain their parameter documentation.
# pylint: disable=useless-param-doc

_offline = projections._offline
_sources = projections._sources
_Event = tuple[int, int, Literal["draft", "verdict"], int, int]
_PROGRESS = "request or stage progress regresses"
_RECOVERY = "later request precedes earlier request recovery"

# Each row independently specifies run, request, stage, attempt and final completion.
# Completion is not stage success: the serial writer resolves failures only when
# saving the final response, and resume skips all previously saved stages.
_HISTORIES: list[tuple[str, list[_Event], int, str | None]] = [
    (
        "saved-stage-regression",
        [(1, 0, "verdict", 1, 3), (2, 0, "draft", 1, 3)],
        0,
        _PROGRESS,
    ),
    (
        "interrupted-saved-stage-regression",
        [(1, 0, "verdict", 1, 3), (2, 0, "draft", 1, 3)],
        1,
        _PROGRESS,
    ),
    (
        "backward-draft-draft",
        [(1, 1, "draft", 1, 3), (2, 0, "draft", 1, 3)],
        0,
        _PROGRESS,
    ),
    (
        "backward-draft-checker",
        [(1, 1, "draft", 1, 3), (2, 0, "verdict", 1, 3)],
        0,
        _PROGRESS,
    ),
    (
        "backward-checker-draft",
        [(1, 1, "verdict", 1, 3), (2, 0, "draft", 1, 3)],
        0,
        _PROGRESS,
    ),
    (
        "backward-checker-checker",
        [(1, 1, "verdict", 1, 3), (2, 0, "verdict", 1, 3)],
        0,
        _PROGRESS,
    ),
    ("early-draft-draft", [(1, 0, "draft", 1, 3), (2, 1, "draft", 1, 3)], 0, _RECOVERY),
    (
        "early-draft-checker",
        [(1, 0, "draft", 1, 3), (2, 1, "verdict", 1, 3)],
        0,
        _RECOVERY,
    ),
    (
        "early-checker-draft",
        [(1, 0, "verdict", 1, 3), (2, 1, "draft", 1, 3)],
        0,
        _RECOVERY,
    ),
    (
        "early-checker-checker",
        [(1, 0, "verdict", 1, 3), (2, 1, "verdict", 1, 3)],
        0,
        _RECOVERY,
    ),
    (
        "early-nonexhausted-request",
        [(1, 0, "draft", 1, 2), (1, 1, "draft", 1, 2)],
        1,
        _RECOVERY,
    ),
    (
        "early-third-request",
        [(1, 0, "draft", 1, 2), (2, 1, "verdict", 1, 5), (4, 2, "draft", 1, 6)],
        0,
        _RECOVERY,
    ),
    (
        "early-across-unobserved-requests",
        [(1, 0, "draft", 1, 5), (4, 3, "verdict", 1, 6)],
        0,
        _RECOVERY,
    ),
    (
        "interrupted-unfinished-draft",
        [(1, 0, "draft", 1, 9), (7, 0, "draft", 1, 9)],
        1,
        None,
    ),
    (
        "interrupted-unfinished-checker",
        [(1, 0, "verdict", 1, 9), (7, 0, "verdict", 1, 9)],
        1,
        None,
    ),
    (
        "exhausted-draft-run-gaps",
        [(1, 0, "draft", 1, 11), (7, 0, "draft", 1, 11)],
        0,
        None,
    ),
    (
        "exhausted-checker-run-gaps",
        [(1, 0, "verdict", 1, 11), (7, 0, "verdict", 1, 11)],
        0,
        None,
    ),
    (
        "stage-progress-run-gaps",
        [(1, 0, "draft", 1, 11), (7, 0, "verdict", 1, 11)],
        0,
        None,
    ),
    (
        "next-request-in-completion-run",
        [(1, 0, "verdict", 1, 4), (4, 1, "draft", 1, 8)],
        0,
        None,
    ),
    (
        "next-request-after-delayed-completion",
        [(1, 0, "draft", 1, 4), (7, 1, "verdict", 1, 11)],
        0,
        None,
    ),
    (
        "different-completion-runs",
        [(1, 0, "draft", 1, 2), (2, 1, "verdict", 1, 3)],
        0,
        None,
    ),
    (
        "unobserved-requests-between-failures",
        [(1, 0, "draft", 1, 4), (4, 3, "verdict", 1, 8)],
        0,
        None,
    ),
    ("initial-failure-after-successful-requests", [(7, 2, "verdict", 1, 11)], 0, None),
    (
        "three-request-completions",
        [(1, 0, "draft", 1, 2), (2, 1, "verdict", 1, 5), (5, 2, "draft", 1, 6)],
        0,
        None,
    ),
    (
        "same-run-stage-and-request-progress",
        [(1, 0, "draft", 1, 1), (1, 0, "verdict", 1, 1), (1, 1, "draft", 1, 1)],
        1,
        None,
    ),
    ("same-run-draft-retries", [(1, 0, "draft", 1, 1), (1, 0, "draft", 2, 1)], 2, None),
    (
        "same-run-checker-retries",
        [(1, 0, "verdict", 1, 1), (1, 0, "verdict", 2, 1)],
        2,
        None,
    ),
    (
        "interrupted-attempt-counter-resets",
        [(1, 0, "draft", 1, 9), (1, 0, "draft", 2, 9), (7, 0, "draft", 1, 9)],
        2,
        None,
    ),
]


@pytest.mark.parametrize(argnames="offset", argvalues=[0, 7])
@pytest.mark.parametrize(
    argnames="events,retry_limit,error",
    argvalues=[
        pytest.param(events, retries, error, id=name)
        for name, events, retries, error in _HISTORIES
    ],
)
def test_failures_preserve_serial_stage_and_request_progress(
    *,
    error: str | None,
    events: list[_Event],
    offset: int,
    retry_limit: int,
) -> None:
    """Run changes reset attempts without undoing saved stages or completed requests.

    Parameters
    ----------
    error
        Expected chronology violation, or acceptance for a possible serial history.
    events
        Independent run, request, stage, attempt and completion observations.
    offset
        Shift both run and request ordinals without changing the oracle.
    retry_limit
        Captured retries beyond the first attempt for each stage.
    """
    failures = [
        _LPFailure(
            attempt=attempt,
            error_content_hash="a" * 64,
            error_type="TimeoutError",
            exhausted=attempt == retry_limit + 1,
            pair_ids=[f"pair-{index + offset}"],
            request_content_hash="b" * 64,
            request_id=f"request-{index + offset}",
            request_index=index + offset,
            resolved_response_content_hash="c" * 64,
            resolved_run_number=recovery + offset,
            run_number=run + offset,
            stage=stage,
        )
        for run, index, stage, attempt, recovery in events
    ]
    before = [failure.model_dump_json() for failure in failures]
    retry_limits = {"draft": retry_limit, "verdict": retry_limit}
    if error is None:
        compatibility.validate_historical_failures(
            failures=failures, retry_limits=retry_limits
        )
    else:
        with pytest.raises(ValueError, match=error):
            compatibility.validate_historical_failures(
                failures=failures, retry_limits=retry_limits
            )
    assert [failure.model_dump_json() for failure in failures] == before
    assert retry_limits == {"draft": retry_limit, "verdict": retry_limit}


@pytest.mark.parametrize(
    argnames="projection", argvalues=["internal", "converted", "wire"]
)
@pytest.mark.parametrize(
    argnames="events,error",
    argvalues=[
        pytest.param(events, error, id=name)
        for name, events, retries, error in _HISTORIES
        if retries == 0
    ],
)
def test_preparation_enforces_chronology_before_publication(
    *,
    _sources: dict[str, Path],
    error: str | None,
    events: list[_Event],
    monkeypatch: pytest.MonkeyPatch,
    projection: str,
    tmp_path: Path,
) -> None:
    """Validate fully bound histories at real preparation, frozen loading and resume.

    All receipt and downstream hashes are reconciled in synthetic copies, so an
    invalid history must fail on chronology rather than unrelated stale material.
    Rejection preserves the entire isolated tree, including earlier evidence.

    Parameters
    ----------
    _sources
        Independently built historical and current source fixtures.
    error
        Specific chronology rejection, or acceptance for a possible history.
    events
        Historical failed attempts with independently specified completion runs.
    monkeypatch
        Restoring effect guards after exercising unmocked preparation.
    projection
        Original flat, converted flat or current wire projection.
    tmp_path
        Fresh isolated repository and synthetic evidence directory.
    """
    directory = matrix._copy(
        checkpoint="historical_prefix",
        projection=projection,
        root=tmp_path,
        sources=_sources,
    )
    receipt = matrix._read(directory / matrix._RECEIPT)
    requests = matrix._read(directory / "lp_generation_requests.jsonl")
    responses = matrix._read(directory / "lp_generation_responses.jsonl")
    assert receipt["material"]["retry_limits"] == {"draft": 0, "verdict": 0}
    failures = [
        {
            "attempt": attempt,
            "error_content_hash": "a" * 64,
            "error_type": "TimeoutError",
            "exhausted": True,
            "pair_ids": [pair["pair_id"] for pair in requests[index]["pairs"]],
            "request_content_hash": requests[index]["request_content_hash"],
            "request_id": requests[index]["request_id"],
            "request_index": index,
            "resolved_response_content_hash": responses[index]["payload_content_hash"],
            "resolved_run_number": recovery,
            "run_number": run,
            "stage": stage,
        }
        for run, index, stage, attempt, recovery in events
    ]
    receipt["run_number"] = max(event[4] for event in events)
    matrix._write(path=directory / matrix._RECEIPT, value=receipt)
    matrix._write(path=directory / "lp_generation_failures.json", value=failures)
    matrix._receipt_hashes(directory)
    matrix._recovery_outputs(directory)
    projections._format(directory=directory, projection=projection)
    prior = tmp_path / "results/lp_evals/prior-evidence.txt"
    prior.parent.mkdir(parents=True)
    prior.write_bytes(b"Synthetic immutable earlier evidence\n")
    before = projections._state(tmp_path)
    source_before = projections._state(directory.parent)
    if error is not None:
        with pytest.raises(sampling.LPSnapshotError, match=error):
            entry.prepare_evaluation(
                overrides=matrix._OVERRIDES,
                repository_root=tmp_path,
                results_root=directory,
            )
        assert projections._state(tmp_path) == before
        current._reject(directory=directory, monkeypatch=monkeypatch, root=tmp_path)
    else:
        reference = entry.prepare_evaluation(
            overrides=matrix._OVERRIDES,
            repository_root=tmp_path,
            results_root=directory,
        )
        with judge.open_evaluation_store(reference) as session:
            assert session.schedule.total_requests > 0
            snapshots = sampling.load_frozen_lp_inputs(session.schedule.inputs)
            assert len(snapshots) == 1
            assert snapshots[0].checkpoint_format == "historical_prefix"
            assert snapshots[0].projection_format == matrix._FORMATS[projection]
        evaluation_before = projections._state(tmp_path / "results")
        assert (
            entry.prepare_evaluation(
                repository_root=tmp_path,
                results_root=directory,
                resume_manifest=reference.manifest_path,
            )
            == reference
        )
        assert projections._state(tmp_path / "results") == evaluation_before
    assert projections._state(directory.parent) == source_before
    assert prior.read_bytes() == b"Synthetic immutable earlier evidence\n"
