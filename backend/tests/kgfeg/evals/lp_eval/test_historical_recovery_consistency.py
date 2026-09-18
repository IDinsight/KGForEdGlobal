"""Validate request-wide recovery against historical serial completion semantics."""

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


@pytest.mark.parametrize(argnames="offset", argvalues=[0, 7])
@pytest.mark.parametrize(
    argnames="events,recoveries,retry_limit,valid",
    argvalues=[
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "draft", 2)],
            (1, 1),
            2,
            True,
            id="producer-retries-complete-same-run",
        ),
        pytest.param(
            [(1, 0, "verdict", 1), (1, 0, "verdict", 2)],
            (1, 1),
            2,
            True,
            id="checker-retries-complete-same-run",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "draft", 2)],
            (2, 2),
            2,
            True,
            id="producer-retries-complete-later-run",
        ),
        pytest.param(
            [(1, 0, "verdict", 1), (1, 0, "verdict", 2)],
            (2, 2),
            2,
            True,
            id="checker-retries-complete-later-run",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "draft", 2)],
            (1, 2),
            2,
            False,
            id="producer-attempts-cannot-resolve-separately",
        ),
        pytest.param(
            [(1, 0, "verdict", 1), (1, 0, "verdict", 2)],
            (1, 2),
            2,
            False,
            id="checker-attempts-cannot-resolve-separately",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "verdict", 1)],
            (1, 1),
            1,
            True,
            id="both-stages-retry-and-complete-same-run",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "verdict", 1)],
            (2, 2),
            1,
            True,
            id="both-stages-complete-later-run",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "verdict", 1)],
            (1, 2),
            1,
            False,
            id="stage-success-is-not-request-recovery",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (2, 0, "draft", 1)],
            (2, 3),
            0,
            False,
            id="exhausted-producer-runs-cannot-resolve-separately",
        ),
        pytest.param(
            [(1, 0, "verdict", 1), (2, 0, "verdict", 1)],
            (2, 3),
            0,
            False,
            id="exhausted-checker-runs-cannot-resolve-separately",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (2, 0, "verdict", 1)],
            (2, 3),
            0,
            False,
            id="exhausted-stages-cannot-resolve-separately",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (2, 0, "draft", 1)],
            (3, 3),
            0,
            True,
            id="producer-recovers-in-third-run",
        ),
        pytest.param(
            [(1, 0, "verdict", 1), (2, 0, "verdict", 1)],
            (3, 3),
            0,
            True,
            id="checker-recovers-in-third-run",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (2, 0, "verdict", 1)],
            (3, 3),
            0,
            True,
            id="producer-and-checker-recover-together",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (2, 0, "verdict", 1)],
            (5, 5),
            0,
            True,
            id="interrupted-runs-may-delay-completion",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (2, 1, "verdict", 1)],
            (2, 3),
            0,
            True,
            id="different-requests-have-different-completion-runs",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 1, "verdict", 1), (2, 1, "verdict", 1)],
            (1, 2, 2),
            1,
            True,
            id="earlier-request-completes-before-later-request-recovers",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 1, "verdict", 1), (2, 1, "verdict", 1)],
            (1, 2, 3),
            1,
            False,
            id="later-request-still-requires-one-recovery-run",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "draft", 2), (2, 0, "verdict", 1)],
            (3, 3, 3),
            1,
            True,
            id="all-attempts-and-stages-recover-together",
        ),
        pytest.param(
            [(1, 0, "draft", 1), (1, 0, "draft", 2), (2, 0, "verdict", 1)],
            (2, 2, 3),
            1,
            False,
            id="exhausted-producer-and-retried-checker-share-recovery",
        ),
    ],
)
def test_failure_histories_require_one_completion_run_per_request(
    *,
    events: list[tuple[int, int, Literal["draft", "verdict"], int]],
    offset: int,
    recoveries: tuple[int, ...],
    retry_limit: int,
    valid: bool,
) -> None:
    """Final response publication resolves every failure of that request together.

    Historical serial execution saves drafts and verdicts separately, but neither
    resolves a failure. Only appending the final response resolves all outstanding
    attempts across both stages. Resume skips that response, so no later execution
    can legitimately assign a second recovery run to the completed request.

    Parameters
    ----------
    events
        Run, request index, stage and attempt in historical execution order.
    offset
        Shift run numbers and request indices to avoid first-request assumptions.
    recoveries
        Independently specified completion runs for the failure rows.
    retry_limit
        Captured stage retries beyond the initial attempt.
    valid
        Whether the history can follow the serial completion and resume contract.
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
        for (run, index, stage, attempt), recovery in zip(
            events, recoveries, strict=True
        )
    ]
    before = [failure.model_dump_json() for failure in failures]
    retry_limits = {"draft": retry_limit, "verdict": retry_limit}
    if valid:
        compatibility.validate_historical_failures(
            failures=failures, retry_limits=retry_limits
        )
    else:
        with pytest.raises(ValueError, match="disagree on the recovery run"):
            compatibility.validate_historical_failures(
                failures=failures, retry_limits=retry_limits
            )
    assert [failure.model_dump_json() for failure in failures] == before
    assert retry_limits == {"draft": retry_limit, "verdict": retry_limit}


@pytest.mark.parametrize(
    argnames="case,recoveries,request_indices,valid",
    argvalues=[
        ("contradictory", (2, 3), (0, 0), False),
        ("recovered", (3, 3), (0, 0), True),
        ("delayed", (5, 5), (0, 0), True),
        ("separate_requests", (2, 3), (0, 1), True),
    ],
)
@pytest.mark.parametrize(
    argnames="projection", argvalues=["internal", "converted", "wire"]
)
@pytest.mark.parametrize(
    argnames="stages",
    argvalues=[("draft", "draft"), ("draft", "verdict"), ("verdict", "verdict")],
)
def test_preparation_reconciles_request_recovery_before_publication(
    *,
    _sources: dict[str, Path],
    case: str,
    monkeypatch: pytest.MonkeyPatch,
    projection: str,
    recoveries: tuple[int, int],
    request_indices: tuple[int, int],
    stages: tuple[str, str],
    tmp_path: Path,
    valid: bool,
) -> None:
    """Accept possible histories and reject contradictory ones before any publication.

    Every synthetic history has complete receipt and downstream hashes. Invalid
    cases therefore reach recovery validation instead of failing on stale evidence.
    All projection readers must enforce the same historical completion semantics.

    Parameters
    ----------
    _sources
        Immutable independently built historical and current source fixtures.
    case
        Named completion history for readable failure diagnostics.
    monkeypatch
        Restoring publication guards used after the real preparation boundary.
    projection
        Independent flat snake, converted flat camel or current wire projection.
    recoveries
        Claimed completion run for each failed attempt.
    request_indices
        Same-request contradiction or independent request completions.
    stages
        Failed producer/checker stages across two historical runs.
    tmp_path
        Fresh isolated repository and synthetic evidence directory.
    valid
        Independently determined historical completion verdict.
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
            "attempt": 1,
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
        for run, (index, recovery, stage) in enumerate(
            zip(request_indices, recoveries, stages, strict=True), start=1
        )
    ]
    receipt["run_number"] = max(recoveries)
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
    if not valid:
        with pytest.raises(
            sampling.LPSnapshotError, match="disagree on the recovery run"
        ):
            entry.prepare_evaluation(
                overrides=matrix._OVERRIDES,
                repository_root=tmp_path,
                results_root=directory,
            )
        assert projections._state(tmp_path) == before, case
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
