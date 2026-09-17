"""Shared report artifact validation and publication helpers."""

# Standard Library
import hashlib
import json
import os
import tempfile

from pathlib import Path
from typing import Any

# Third Party Library
from pydantic import TypeAdapter

# Package Library
from kgfeg.evals.lp_eval.judge import (
    _load_store_manifest,
    _store_directory_sync,
    _store_path,
    _store_read,
    _store_write,
)
from kgfeg.evals.lp_eval.schemas import EvaluationReportArtifacts, EvaluationStore
from kgfeg.kgs.lp_requests import canonical_lp_json


def _publish_files(*, directory: Path, files: dict[str, bytes]) -> None:
    """Publish immutable files together and verify exact reuse.

    Parameters
    ----------
    directory
        Content-addressed output directory with unaliased parents.
    files
        Fixed artifact names and their complete bytes.

    Raises
    ------
    ValueError
        If an existing generation differs or an output path is unsafe.
    """

    _store_path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)

    if directory.exists():
        if {path.name for path in directory.iterdir()} != set(files):
            raise ValueError(
                "Existing report generation has missing or extra artifacts."
            )

        for name, payload in files.items():
            if _store_read(directory / name) != payload:
                raise ValueError("Existing immutable report bytes differ.")

        return

    with tempfile.TemporaryDirectory(
        prefix=".report-", dir=directory.parent
    ) as temporary:
        staging = Path(temporary)

        for name, payload in files.items():
            _store_write(path=staging / name, payload=payload)

        _store_directory_sync(staging)
        os.rename(staging, directory)
        _store_directory_sync(directory.parent)


def _validated_report_manifest(reference: EvaluationReportArtifacts) -> dict[str, Any]:
    """Read and hash-check every immutable report artifact before disposition.

    Parameters
    ----------
    reference
        Pinned report generation.

    Returns
    -------
    dict[str, Any]
        Verified manifest.

    Raises
    ------
    ValueError
        If a report file or manifest is missing, altered, extra or aliased.
    """

    payload = _store_read(reference.directory / "lp_eval_manifest.json")

    if hashlib.sha256(payload).hexdigest() != reference.manifest_sha256:
        raise ValueError("Report manifest hash differs.")

    manifest = json.loads(payload)
    invocation = TypeAdapter(EvaluationStore).validate_json(
        canonical_lp_json(manifest["invocation"])
    )
    _load_store_manifest(invocation)
    expected = invocation.manifest_path.parent / "reports" / reference.manifest_sha256

    if reference.directory != expected:
        raise ValueError("Report reference is outside its bound invocation.")

    if {path.name for path in reference.directory.iterdir()} != {
        *manifest["files"],
        "lp_eval_manifest.json",
    }:
        raise ValueError("Report artifact membership differs.")

    for name, fingerprint in manifest["files"].items():
        if Path(name).name != name:
            raise ValueError("Report artifact name is not a local filename.")

        material = _store_read(reference.directory / name)

        if (
            hashlib.sha256(material).hexdigest() != fingerprint["sha256"]
            or len(material) != fingerprint["size_bytes"]
        ):
            raise ValueError("Report artifact bytes differ.")

    return manifest
