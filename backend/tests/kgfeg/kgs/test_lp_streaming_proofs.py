"""Challenge complete streaming population proofs and their invocation boundaries."""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import os

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Third Party Library
import pytest

# Package Library
from kgfeg.kgs import lp_requests as req
from tests.kgfeg.kgs import test_lp_orchestration as old
from tests.kgfeg.kgs import test_lp_population_verifier as existing


class _ObservedStream:
    """Count every byte passed to the unmodified streaming digest implementation."""

    def __enter__(self) -> Any:
        """Enter a read-only stream context.

        Returns
        -------
        Any
            Counting proxy.
        """
        return self

    def __exit__(self, *args: Any) -> None:
        """Close the original file.

        Parameters
        ----------
        args
            Context exit information.
        """
        self.stream.close()

    def __init__(self, *, counts: dict[str, int], name: str, stream: Any) -> None:
        """Retain the real read-only stream.

        Parameters
        ----------
        counts
            Independent byte counters.
        name
            Observed artifact.
        stream
            Real binary file.
        """
        self.counts, self.name, self.stream = counts, name, stream

    def readable(self) -> bool:
        """Report readable stream capability.

        Returns
        -------
        bool
            Whether the original stream is readable.
        """
        return self.stream.readable()

    def readinto(self, buffer: Any) -> int:
        """Count bytes consumed by the actual SHA-256 loop.

        Parameters
        ----------
        buffer
            Digest scratch buffer.

        Returns
        -------
        int
            Actual bytes read.
        """
        size = self.stream.readinto(buffer)
        self.counts[self.name] = self.counts.get(self.name, 0) + size
        return size


def test_every_verification_streams_every_artifact_byte(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Repeated checks must read complete file contents, without timestamp exemptions.

    Parameters
    ----------
    monkeypatch
        Real-stream observer.
    tmp_path
        Synthetic population directory.
    """
    population = existing._population(tmp_path)
    verifier = req.LPRequestPopulationVerifier(population=population, root=tmp_path)
    counts: dict[str, int] = {}
    original = Path.open
    names = (*population.manifest.artifact_byte_hashes, req.MANIFEST_FILENAME)

    def _open(self: Path, *args: Any, **kwargs: Any) -> Any:
        """Wrap only the actual population binary streams.

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
            Real file or read-counting proxy.
        """
        stream = original(self, *args, **kwargs)  # pylint: disable=R1732
        return (
            _ObservedStream(counts=counts, name=self.name, stream=stream)
            if self.parent == tmp_path and self.name in names
            else stream
        )

    monkeypatch.setattr(name="open", target=Path, value=_open)
    for _ in range(3):
        verifier.verify(population=population, root=tmp_path)
    assert counts == {name: 3 * (tmp_path / name).stat().st_size for name in names}
    assert sum(len(v) for _, v in verifier._artifact_hashes) == 128
    assert not hasattr(verifier, "_payloads")


@pytest.mark.parametrize(
    argnames="kind",
    argvalues=["datetime", "tuple", "nan", "positive_inf", "negative_inf"],
)
def test_live_normalization_cannot_reuse_earlier_population(
    kind: str, tmp_path: Path
) -> None:
    """Invalid nested Python types and nonfinite values cannot inherit JSON authority.

    Parameters
    ----------
    kind
        JSON-normalizing mutation.
    tmp_path
        Synthetic population directory.
    """
    population = existing._population(tmp_path)
    nested = next(iter(population.requests[0].sfis[0].context.audit_context.values()))
    if kind == "datetime":
        nested["nullable"] = "2026-09-14T00:00:00Z"
    elif kind == "tuple":
        nested["nullable"] = ["a", 1]
    # Rebuild canonical identity for valid setup by using the existing null field for
    # nonfinite tests; datetime and tuple compare a private live hash directly first.
    if kind in {"datetime", "tuple"}:
        before = req._population_material_hash(population)
        nested["nullable"] = (
            datetime(2026, 9, 14, tzinfo=timezone.utc)
            if kind == "datetime"
            else ("a", 1)
        )
        try:
            after = req._population_material_hash(population)
        except (ValueError, TypeError):
            return
        assert (
            after != before
        ), "Invalid Python material normalized to the same live proof."
    else:
        verifier = req.LPRequestPopulationVerifier(population=population, root=tmp_path)
        nested["nullable"] = {
            "nan": float("nan"),
            "positive_inf": float("inf"),
            "negative_inf": -float("inf"),
        }[kind]
        with pytest.raises(expected_exception=(ValueError, TypeError)):
            verifier.verify(population=population, root=tmp_path)


@pytest.mark.parametrize(argnames="name", argvalues=old._INPUTS)
def test_same_size_restored_time_tail_edit_is_detected(
    name: str, tmp_path: Path
) -> None:
    """A final-byte edit cannot evade full streaming verification with restored metadata.

    Parameters
    ----------
    name
        One of all four population artifacts.
    tmp_path
        Synthetic population directory.
    """
    population = existing._population(tmp_path)
    verifier = req.LPRequestPopulationVerifier(population=population, root=tmp_path)
    path = tmp_path / name
    before = path.stat()
    payload = path.read_bytes()
    path.write_bytes(payload[:-1] + b" ")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    snapshot = old._snapshot(tmp_path)
    with pytest.raises(expected_exception=ValueError):
        verifier.verify(population=population, root=tmp_path)
    assert old._snapshot(tmp_path) == snapshot


def test_simultaneous_manifest_and_disk_mutation_during_setup_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A caller cannot reseal modified bytes after the full reader returned.

    Parameters
    ----------
    monkeypatch
        Mutation after real full validation.
    tmp_path
        Synthetic material directory.
    """
    population = existing._population(tmp_path)
    original = req.read_lp_request_population

    def _read(**kwargs: Any) -> Any:
        """Mutate disk and the caller's manifest after actual validation.

        Parameters
        ----------
        kwargs
            Complete real reader inputs.

        Returns
        -------
        Any
            Original validated reader result.
        """
        result = original(**kwargs)
        path = tmp_path / old._INPUTS[2]
        path.write_bytes(path.read_bytes() + b"\n")
        population.manifest.artifact_byte_hashes[path.name] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        (tmp_path / req.MANIFEST_FILENAME).write_bytes(
            old._bytes(population.manifest.model_dump(mode="json"))
        )
        return result

    monkeypatch.setattr(name="read_lp_request_population", target=req, value=_read)
    with pytest.raises(expected_exception=ValueError):
        req.LPRequestPopulationVerifier(population=population, root=tmp_path)
