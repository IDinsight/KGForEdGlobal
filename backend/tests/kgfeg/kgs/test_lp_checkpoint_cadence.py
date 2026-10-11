"""Challenge where LP checkpoint proofs run and what each incremental save writes."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import os
import statistics

from functools import partial
from pathlib import Path
from typing import Any, cast

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_checkpoints as cp
from kgfeg.kgs import lp_finalization, lp_generation
from kgfeg.kgs import lp_requests as req
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.utils import KGDirs
from tests.kgfeg.kgs import test_lp_checkpoint_formats as formats
from tests.kgfeg.kgs import test_lp_concurrency as concurrent
from tests.kgfeg.kgs import test_lp_generation as fixtures
from tests.kgfeg.kgs import test_lp_orchestration as old

_PENDING = "lp_generation_pending_completions.json"
_USAGE = "lp_generation_usage.json"
_STAGE_FILES = (old._DRAFT, old._VERDICT, old._RESPONSE)
_SMALL_FILES = (old._FAILURE, _PENDING, _USAGE)


class _CountedStream:
    """Count every byte read through a real binary stream."""

    def __init__(self, *, counts: dict[str, int], name: str, stream: Any) -> None:
        """Wrap one real stream.

        Parameters
        ----------
        counts
            Byte counters keyed by file name.
        name
            Observed file name.
        stream
            Real binary file object.
        """
        self.counts, self.name, self.stream = counts, name, stream

    def __enter__(self) -> _CountedStream:
        """Enter the stream context.

        Returns
        -------
        _CountedStream
            This counting proxy.
        """
        return self

    def __exit__(self, *args: Any) -> None:
        """Close the real stream.

        Parameters
        ----------
        args
            Context exit information.
        """
        self.stream.close()

    def __getattr__(self, attribute: str) -> Any:
        """Delegate everything else to the real stream.

        Parameters
        ----------
        attribute
            Attribute name.

        Returns
        -------
        Any
            The real stream's attribute.
        """
        return getattr(self.stream, attribute)

    def read(self, *args: Any) -> bytes:
        """Count bytes returned by a plain read.

        Parameters
        ----------
        args
            Optional size.

        Returns
        -------
        bytes
            Bytes actually read.
        """
        data = self.stream.read(*args)
        self.counts[self.name] = self.counts.get(self.name, 0) + len(data)
        return cast(bytes, data)

    def readinto(self, buffer: Any) -> int:
        """Count bytes consumed by a streaming digest.

        Parameters
        ----------
        buffer
            Scratch buffer.

        Returns
        -------
        int
            Bytes actually read.
        """
        size = self.stream.readinto(buffer) or 0
        self.counts[self.name] = self.counts.get(self.name, 0) + size
        return cast(int, size)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid external model transport with restored synthetic settings.

    Parameters
    ----------
    monkeypatch
        Restoring patches.
    """
    cast(Any, concurrent._block_network).__wrapped__(monkeypatch)


def _count_reads(
    *, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...], root: Path
) -> dict[str, int]:
    """Count bytes read from selected files in one directory.

    Parameters
    ----------
    monkeypatch
        Restoring patch of `Path.open`, which `Path.read_bytes` also uses.
    names
        Observed file names.
    root
        Observed directory.

    Returns
    -------
    dict[str, int]
        Live byte counters keyed by file name.
    """
    counts: dict[str, int] = {}
    original = Path.open

    def _open(self: Path, *args: Any, **kwargs: Any) -> Any:
        """Wrap only the observed files' real streams.

        Parameters
        ----------
        self
            File path.
        args
            Original positional arguments.
        kwargs
            Original keyword arguments.

        Returns
        -------
        Any
            Real stream or counting proxy.
        """
        stream = original(self, *args, **kwargs)  # pylint: disable=R1732
        if self.parent == root and self.name in names:
            return _CountedStream(counts=counts, name=self.name, stream=stream)
        return stream

    monkeypatch.setattr(name="open", target=Path, value=_open)
    return counts


def _full_rewrite(root: Path) -> dict[str, bytes]:
    """Independently re-encode every checkpoint file whole from its parsed content.

    This is what a writer that rewrites every file on every save produces for the
    same state: canonical JSON rows and objects, and a receipt whose byte hashes
    and stage counts describe those files.

    Parameters
    ----------
    root
        Checkpoint directory.

    Returns
    -------
    dict[str, bytes]
        Expected bytes of every checkpoint file, including the receipt.
    """
    files: dict[str, bytes] = {}
    counts: dict[str, int] = {}
    for stage, name in zip(("draft", "verdict", "response"), _STAGE_FILES):
        rows = old._rows(root / name)
        files[name] = b"".join(old._bytes(row) for row in rows)
        counts[stage] = len(rows)
    for name in _SMALL_FILES:
        files[name] = old._bytes(json.loads((root / name).read_bytes()))
    receipt = json.loads((root / old._RECEIPT).read_bytes())
    receipt["artifact_byte_hashes"] = {
        name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()
    }
    receipt["stage_counts"] = counts
    files[old._RECEIPT] = old._bytes(receipt)
    return files


def _store(root: Path, harness: old._Harness) -> cp.LPGenerationCheckpoints:
    """Load the harness's checkpoint directory with its own population and material.

    Parameters
    ----------
    root
        Checkpoint directory.
    harness
        Harness whose bundle and config produced the directory.

    Returns
    -------
    LPGenerationCheckpoints
        Fully verified writable store.
    """
    population = req.build_lp_generation_requests(
        as_lc_bundle=harness.bundle, doc_key=fixtures._DOC_KEY, kg_config=harness.config
    )
    material = lp_generation.lp_execution_material(
        kg_config=harness.config, population=population
    )
    return cp.LPGenerationCheckpoints(
        material=material, population=population, root=root
    )


def test_every_commit_leaves_the_bytes_a_full_rewrite_would_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Incremental commits leave every file and the receipt as a full rewrite would.

    Four invocations cover dispatch, completion, promotion, a retried call, an
    exhausted failure resolved in a later run, checker corrections, an interrupted
    call left unknown, and a commit interrupted after its stage append and then
    recovered.

    Parameters
    ----------
    monkeypatch
        Offline call seam, commit observer and append fault injector.
    tmp_path
        Synthetic checkpoint directory.
    """
    harness = old._Harness(count=3, root=tmp_path)
    harness.config.learning_progressions.retry.producer_max_retries = 1
    harness.config.learning_progressions.retry.checker_max_retries = 1
    harness.script[("draft", 0)] = [TimeoutError("retried")]
    harness.script[("verdict", 1)] = [TimeoutError("first"), TimeoutError("second")]
    old._install(harness=harness, monkeypatch=monkeypatch)
    original = cp.LPGenerationCheckpoints._commit_delta
    commits: list[str] = []

    def _commit_delta(self: cp.LPGenerationCheckpoints, **kwargs: Any) -> None:
        """Compare the directory with a full rewrite after each real commit.

        Parameters
        ----------
        self
            Writing store.
        kwargs
            Real commit arguments.
        """
        original(self, **kwargs)
        expected = _full_rewrite(tmp_path)
        assert {name: (tmp_path / name).read_bytes() for name in expected} == expected
        # The store's own whole-store encoding agrees with the incremental files.
        payloads = self._payloads()
        receipt = self._receipt_bytes(
            {
                name: hashlib.sha256(payload).hexdigest()
                for name, payload in payloads.items()
            }
        )
        assert {**payloads, old._RECEIPT: receipt} == expected
        commits.append(json.loads(expected[old._RECEIPT])["status"])

    monkeypatch.setattr(
        name="_commit_delta", target=cp.LPGenerationCheckpoints, value=_commit_delta
    )
    with pytest.raises(expected_exception=LPGenerationFailed):
        harness._run()
    harness.corrects = True
    harness.script[("draft", 2)] = [old._Interruption()]
    with pytest.raises(expected_exception=old._Interruption):
        harness._run()
    append = cp._append_bytes

    def _interrupted_append(*, offset: int, path: Path, payload: bytes) -> None:
        """Interrupt the first commit that appends a stage row, after the append.

        Parameters
        ----------
        offset
            Journaled previous length.
        path
            Stage file.
        payload
            Appended rows.
        """
        append(offset=offset, path=path, payload=payload)
        raise old._Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="_append_bytes", target=cp, value=_interrupted_append)
        with pytest.raises(expected_exception=old._Interruption):
            harness._run()
    assert (tmp_path / old._JOURNAL).exists()
    assert len(harness._run()) == 3
    assert not (tmp_path / old._JOURNAL).exists()
    assert commits[-1] == "completed" and "failed" in commits
    attempts = concurrent._read(tmp_path / _USAGE)["attempts"]
    assert {"failed", "succeeded", "unknown"} <= {row["status"] for row in attempts}
    failures = concurrent._read(tmp_path / old._FAILURE)
    assert any(row["exhausted"] and row["resolved_run_number"] for row in failures)
    assert any(
        row["payload"] != row_draft["payload"]
        for row, row_draft in zip(
            old._rows(tmp_path / old._RESPONSE), old._rows(tmp_path / old._DRAFT)
        )
    )


def test_checkpoint_edit_between_saves_stops_the_next_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A same-size checkpoint edit with its mtime restored is caught before dispatch.

    Parameters
    ----------
    monkeypatch
        Offline call seam.
    tmp_path
        Synthetic checkpoint directory with partial progress.
    """
    harness = old._Harness(count=3, root=tmp_path)
    harness.script[("draft", 1)] = [old._Interruption()]
    old._install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(expected_exception=old._Interruption):
        harness._run()
    store = _store(tmp_path, harness)
    store.begin_run()
    path = tmp_path / old._RESPONSE
    original = path.read_bytes()
    # A touch that keeps the bytes only refreshes the recorded identity.
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    store.verify_bytes()
    stat = path.stat()
    offset = original.index(b"Synthetic")
    path.write_bytes(original[:offset] + b"Z" + original[offset + 1 :])
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size
    assert path.stat().st_mtime_ns == stat.st_mtime_ns
    before = old._snapshot(tmp_path)
    attempts = len(store.attempts)
    with pytest.raises(expected_exception=ValueError, match=old._RESPONSE):
        store.record_dispatch(request_index=1, stage="draft")
    assert len(store.attempts) == attempts
    assert old._snapshot(tmp_path) == before


def _per_save_work(
    *, count: int, monkeypatch: pytest.MonkeyPatch, root: Path
) -> tuple[list[int], dict[str, float], int]:
    """Run one invocation and measure the work done by each incremental save.

    Parameters
    ----------
    count
        Synthetic standards; requests grow quadratically with it.
    monkeypatch
        Offline call seam and observers.
    root
        Synthetic checkpoint directory.

    Returns
    -------
    tuple[list[int], dict[str, float], int]
        Rows whose proof each incremental save rechecks; population bytes read
        per population file, in multiples of that file's size; full population
        verifications.
    """
    harness = old._Harness(count=count, root=root)
    old._install(harness=harness, monkeypatch=monkeypatch)
    # Only production reads count: the harness's own call-time population audit
    # reads every file once per call.
    monkeypatch.setattr(name="_assert_population", target=old, value=lambda root: None)
    proofs: list[int] = []
    active: list[bool] = [False]
    verifications: list[int] = [0]
    save = cp.LPGenerationCheckpoints._save
    check_row = cp.LPGenerationCheckpoints._validate_cached_row
    verify = req.LPRequestPopulationVerifier.verify

    def _save(self: cp.LPGenerationCheckpoints, **kwargs: Any) -> None:
        """Attribute row proofs to incremental saves only.

        Parameters
        ----------
        self
            Writing store.
        kwargs
            Real save arguments.
        """
        incremental = not (
            self._status() == "completed" and self._committed_status != "completed"
        )
        if incremental:
            proofs.append(0)
        active[0] = incremental
        try:
            save(self, **kwargs)
        finally:
            active[0] = False

    def _check_row(self: cp.LPGenerationCheckpoints, **kwargs: Any) -> None:
        """Count one row whose proof a save rechecks.

        Parameters
        ----------
        self
            Validating store.
        kwargs
            Real row arguments.
        """
        if active[0]:
            proofs[-1] += 1
        check_row(self, **kwargs)

    def _verify(self: req.LPRequestPopulationVerifier, **kwargs: Any) -> None:
        """Count one full population verification.

        Parameters
        ----------
        self
            Retained proof.
        kwargs
            Real verification arguments.
        """
        verifications[0] += 1
        verify(self, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(name="_save", target=cp.LPGenerationCheckpoints, value=_save)
        patch.setattr(
            name="_validate_cached_row",
            target=cp.LPGenerationCheckpoints,
            value=_check_row,
        )
        patch.setattr(
            name="verify", target=req.LPRequestPopulationVerifier, value=_verify
        )
        counts = _count_reads(monkeypatch=patch, names=old._INPUTS, root=root)
        harness._run()
    reads = {
        name: counts.get(name, 0) / (root / name).stat().st_size for name in old._INPUTS
    }
    return proofs, reads, verifications[0]


def test_per_save_work_does_not_grow_with_completed_requests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Population reads and row proofs per save stay flat as completed work grows.

    Parameters
    ----------
    monkeypatch
        Offline call seam and observers.
    tmp_path
        Two synthetic checkpoint directories of different sizes.
    """
    small = _per_save_work(count=3, monkeypatch=monkeypatch, root=tmp_path / "small")
    large = _per_save_work(count=9, monkeypatch=monkeypatch, root=tmp_path / "large")
    assert len(large[0]) > 8 * len(small[0])
    # The population is read a fixed number of times per invocation, whatever its
    # size and however many saves run; every read is a whole-file read.
    assert large[1] == small[1]
    assert all(value == int(value) for value in small[1].values())
    assert large[2] == small[2] == 3
    # Serial execution: a save rechecks at most the rows of its own request plus
    # the one pending request, never the completed prefix.
    capacity = 1
    for proofs in (small[0], large[0]):
        assert max(proofs) <= 3 * (capacity + 1)
    late = large[0][len(large[0]) // 2 :]
    assert statistics.mean(late) <= statistics.mean(small[0]) + 0.5


def _label_full_proofs(
    *, monkeypatch: pytest.MonkeyPatch, point: list[str], root: Path
) -> list[tuple[str, dict[str, int]]]:
    """Record every full population proof with the point it runs at.

    Parameters
    ----------
    monkeypatch
        Restoring observers.
    point
        Current point label; the observers set `between`, `completion` and `end`.
    root
        Generation directory.

    Returns
    -------
    list[tuple[str, dict[str, int]]]
        Each full proof's point label and the population bytes it read.
    """
    full: list[tuple[str, dict[str, int]]] = []
    counts = _count_reads(monkeypatch=monkeypatch, names=old._INPUTS, root=root)
    reads = cast(Any, req.read_lp_request_population)
    verify = req.LPRequestPopulationVerifier.verify

    def _observe(label: str, action: Any) -> Any:
        """Record the bytes one full proof streams, under its point label.

        Parameters
        ----------
        label
            Full-verification point or `between`.
        action
            The real proof.

        Returns
        -------
        Any
            The proof's result.
        """
        counts.clear()
        result = action()
        full.append((label, dict(counts)))
        counts.clear()
        return result

    def _verify(self: req.LPRequestPopulationVerifier, **kwargs: Any) -> None:
        """Label one full population verification.

        Parameters
        ----------
        self
            Retained proof.
        kwargs
            Real verification arguments.
        """
        _observe(point[0], partial(verify, self, **kwargs))

    def _read(**kwargs: Any) -> Any:
        """Label one full population read.

        Parameters
        ----------
        kwargs
            Real reader arguments.

        Returns
        -------
        Any
            Validated population.
        """
        return _observe(point[0], partial(reads, **kwargs))

    monkeypatch.setattr(
        name="verify", target=req.LPRequestPopulationVerifier, value=_verify
    )
    for module in (req, cp, lp_generation, lp_finalization):
        monkeypatch.setattr(
            name="read_lp_request_population", target=module, value=_read
        )
    _track_points(monkeypatch=monkeypatch, point=point)
    return full


def _track_points(*, monkeypatch: pytest.MonkeyPatch, point: list[str]) -> None:
    """Move the point label through construction, completion and the end.

    Parameters
    ----------
    monkeypatch
        Restoring observers.
    point
        Current point label, updated in place.
    """
    save = cp.LPGenerationCheckpoints._save
    finish = lp_generation._finish_lp_requests
    store_init = cp.LPGenerationCheckpoints.__init__

    def _save(self: cp.LPGenerationCheckpoints, **kwargs: Any) -> None:
        """Label the save whose receipt first becomes `completed`.

        Parameters
        ----------
        self
            Writing store.
        kwargs
            Real save arguments.
        """
        label = point[0]
        if self._status() == "completed" and self._committed_status != "completed":
            point[0] = "completion"
        try:
            save(self, **kwargs)
        finally:
            point[0] = label

    def _init(self: cp.LPGenerationCheckpoints, **kwargs: Any) -> None:
        """Mark the span between a writable store's construction and the end.

        Parameters
        ----------
        self
            New store.
        kwargs
            Real constructor arguments.
        """
        store_init(self, **kwargs)
        if not kwargs.get("read_only"):
            point[0] = "between"

    def _finish(**kwargs: Any) -> None:
        """Mark the end-of-invocation proof.

        Parameters
        ----------
        kwargs
            Real scheduler arguments.
        """
        finish(**kwargs)
        point[0] = "end"

    monkeypatch.setattr(name="_save", target=cp.LPGenerationCheckpoints, value=_save)
    monkeypatch.setattr(name="__init__", target=cp.LPGenerationCheckpoints, value=_init)
    monkeypatch.setattr(name="_finish_lp_requests", target=lp_generation, value=_finish)


def test_full_verification_runs_at_construction_completion_and_read_only_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every population byte is streamed at each full point and nowhere between them.

    Parameters
    ----------
    monkeypatch
        Offline call seam and observers.
    tmp_path
        Synthetic checkpoint directory.
    """
    harness = old._Harness(count=3, root=tmp_path)
    old._install(harness=harness, monkeypatch=monkeypatch)
    point = ["setup"]
    full = _label_full_proofs(monkeypatch=monkeypatch, point=point, root=tmp_path)
    harness._run()
    point[0] = "read_only"
    cp.validate_lp_checkpoint_format(tmp_path)
    checkpoint_counts = _count_reads(
        monkeypatch=monkeypatch, names=tuple(cp._CHECKPOINT_NAMES), root=tmp_path
    )
    lp_finalization.finalize_learning_progressions(
        as_lc_bundle=harness.bundle,
        doc_key=fixtures._DOC_KEY,
        kg_config=harness.config,
        kg_dirs=KGDirs(root=tmp_path),
    )
    labels = [label for label, _ in full]
    assert "between" not in labels
    assert labels.count("completion") == 1 and labels.count("end") == 1
    assert {"setup", "read_only"} <= set(labels)
    assert labels.index("completion") < labels.index("end")
    artifacts = _population_artifacts(tmp_path)
    for label, streamed in full:
        # Each full proof reads or streams every population artifact byte.
        assert all(
            streamed.get(name, 0) >= (tmp_path / name).stat().st_size
            for name in artifacts
        ), label
    # The read-only finalization load reads every checkpoint file whole.
    assert all(
        checkpoint_counts.get(name, 0) >= (tmp_path / name).stat().st_size
        for name in cp._CHECKPOINT_NAMES
    )


def _population_artifacts(root: Path) -> list[str]:
    """Name the population artifacts the request manifest authenticates.

    Parameters
    ----------
    root
        Generation directory.

    Returns
    -------
    list[str]
        Candidate, summary and request files.
    """
    manifest = json.loads((root / req.MANIFEST_FILENAME).read_bytes())
    return sorted(manifest["artifact_byte_hashes"])


def test_directory_left_by_the_pre_change_writer_loads_and_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Files rewritten whole plus a full-payload journal recover and finish the run.

    The pre-change writer rewrote every checkpoint file on every save and
    journaled each file's complete next bytes. That state, stopped after its
    journal became durable, must load, recover and resume without repeating a
    completed call.

    Parameters
    ----------
    monkeypatch
        Offline call seam and journal interruption.
    tmp_path
        Synthetic checkpoint directory.
    """
    harness = old._Harness(count=3, root=tmp_path)
    harness.script[("draft", 1)] = [old._Interruption()]
    old._install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(expected_exception=old._Interruption):
        harness._run()
    previous: dict[str, bytes | None] = {
        name: (tmp_path / name).read_bytes() for name in cp._CHECKPOINT_NAMES
    }
    write = cp._atomic_write
    captured: dict[str, Any] = {}

    def _write(*, path: Path, payload: bytes) -> None:
        """Stop the next invocation once its first journal is durable.

        Parameters
        ----------
        path
            Real destination.
        payload
            Real bytes.
        """
        write(path=path, payload=payload)
        if path.name == old._JOURNAL and not captured:
            captured.update(json.loads(payload))
            raise old._Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="_atomic_write", target=cp, value=_write)
        with pytest.raises(expected_exception=old._Interruption):
            harness._run()
    next_payloads, previous_hashes = formats._journaled_states(
        captured=captured, phase="update", previous=previous
    )
    # Leave exactly what the pre-change writer leaves: every file written whole as
    # a new file at its previous state, and a journal of complete next bytes.
    for name, payload in previous.items():
        (tmp_path / name).unlink()
        (tmp_path / name).write_bytes(cast(bytes, payload))
    (tmp_path / old._JOURNAL).write_bytes(
        old._bytes(
            {
                "next_payloads": {
                    name: payload.decode() for name, payload in next_payloads.items()
                },
                "previous_byte_hashes": previous_hashes,
            }
        )
    )
    cp.validate_lp_checkpoint_format(tmp_path)
    harness.calls.clear()
    assert len(harness._run()) == 3
    assert not (tmp_path / old._JOURNAL).exists()
    # Request 0 was complete and is never called again; request 1's interrupted
    # draft is unknown, so it is dispatched once more.
    assert harness.calls == [("draft", 1), ("verdict", 1), ("draft", 2), ("verdict", 2)]
    expected = _full_rewrite(tmp_path)
    assert {name: (tmp_path / name).read_bytes() for name in expected} == expected
    receipt = json.loads(expected[old._RECEIPT])
    assert receipt["status"] == "completed" and receipt["run_number"] == 3
