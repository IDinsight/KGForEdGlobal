"""Typed discovery and validated input records for Learning Progressions evaluation."""

# Future Library
from __future__ import annotations

# Standard Library
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DiscoveredRun:
    """One run's recorded completion state, not a validated evaluation snapshot.

    Attributes
    ----------
    aliases
        Additional discovered paths resolving to this physical run directory.
    completed_at
        Recorded completion timestamp, when present.
    doc_key
        Claimed document identity for a completed candidate, pending validation.
    fingerprints
        Byte identities of the metadata read during discovery.
    framework_uuid
        Claimed framework identity, pending full artifact validation.
    kgs_directory
        Resolved physical run directory.
    reason
        Explanation of candidate inclusion or run exclusion.
    run_id
        Identity from the recorded KG execution metadata.
    started_at
        Recorded execution start.
    status
        Preliminary completion classification; never evaluation readiness.
    """

    aliases: tuple[Path, ...]
    completed_at: datetime | None
    doc_key: str | None
    fingerprints: tuple[FileFingerprint, ...]
    framework_uuid: UUID | None
    kgs_directory: Path
    reason: str
    run_id: UUID
    started_at: datetime
    status: Literal["completed_candidate", "failed", "unfinished"]


@dataclass(frozen=True, slots=True)
class DiscoveryAlias:
    """A directory alias observed without repeatedly traversing its target.

    Attributes
    ----------
    discovered_path
        Path encountered during traversal.
    resolved_path
        Physical path used for traversal and containment checks.
    """

    discovered_path: Path
    resolved_path: Path


@dataclass(frozen=True, slots=True)
class DiscoveryInventory:
    """Discovery evidence to pass to full snapshot validation.

    Attributes
    ----------
    aliases
        Observed directory aliases, including cycles that were not traversed again.
    evaluation_root
        Resolved evaluator output subtree excluded from traversal.
    results_root
        Resolved starting directory.
    runs
        One entry per physical run, sorted by resolved path.
    skipped_paths
        Output or external-symlink subtrees excluded from traversal.
    """

    aliases: tuple[DiscoveryAlias, ...]
    evaluation_root: Path
    results_root: Path
    runs: tuple[DiscoveredRun, ...]
    skipped_paths: tuple[DiscoverySkip, ...]


@dataclass(frozen=True, slots=True)
class DiscoverySkip:
    """A traversal exclusion, distinct from an incomplete curriculum run.

    Attributes
    ----------
    path
        Encountered path that was not traversed.
    reason
        Explicit output-subtree or external-symlink exclusion reason.
    """

    path: Path
    reason: Literal["evaluation_output", "outside_results_root"]


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """Actual input bytes observed at a resolved path.

    Attributes
    ----------
    path
        Resolved input file path.
    sha256
        SHA-256 digest of the exact bytes read.
    size_bytes
        Length of the bytes hashed.
    """

    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class FrozenArtifact:
    """One preserved artifact's original location and content-addressed copy identity.

    Attributes
    ----------
    fingerprint
        Original resolved path, SHA-256 and byte length.
    name
        Original run-relative artifact name; never used as an output filename.
    """

    fingerprint: FileFingerprint
    name: str


@dataclass(frozen=True, slots=True)
class FrozenInputManifest:
    """Fixed input selection, independent of subsequent discovery or evaluation calls.

    Attributes
    ----------
    inventory
        Original discovery inventory, including excluded runs and directory aliases.
    kind
        Identifies this input artifact without claiming evaluation completion.
    snapshots
        Validated curricula and their exact preserved material bindings.
    """

    inventory: DiscoveryInventory
    kind: Literal["lp_evaluation_inputs"]
    snapshots: tuple[FrozenSnapshot, ...]


@dataclass(frozen=True, slots=True)
class FrozenInputs:
    """Pinned reference used to reject edited or substituted input manifests.

    Attributes
    ----------
    content_hash
        SHA-256 of the complete canonical manifest bytes.
    manifest_path
        Exact absolute path of the published manifest.
    """

    content_hash: str
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class FrozenSnapshot:
    """A completed curriculum's exact material bindings without mutable payloads.

    Attributes
    ----------
    absent_artifacts
        Optional and transaction artifacts recorded absent during validation.
    artifacts
        Original names and byte identities, in deterministic name order.
    checkpoint_format
        Validated historical-prefix or journal-bearing interpretation.
    config_json
        Captured effective configuration in its original serialization shape.
    run
        Original completion and framework identity.
    source_artifact
        Original run-relative source DocumentIR path.
    """

    absent_artifacts: tuple[str, ...]
    artifacts: tuple[FrozenArtifact, ...]
    checkpoint_format: str
    config_json: str
    run: DiscoveredRun
    source_artifact: str


@dataclass(frozen=True, slots=True)
class SnapshotArtifact:
    """Exact input bytes retained separately from production and evaluator outputs.

    Attributes
    ----------
    fingerprint
        Actual resolved path, byte length and SHA-256.
    name
        Path relative to the run directory, including contained source evidence.
    payload
        Original bytes, without normalization or historical schema migration.
    """

    fingerprint: FileFingerprint
    name: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ValidatedSnapshot:
    """Validated material awaiting a separate immutable snapshot-freezing operation.

    Attributes
    ----------
    absent_artifacts
        Optional or transaction artifacts whose absence was verified.
    artifacts
        Exact captured material, sorted by run-relative name.
    checkpoint_format
        Explicit historical-prefix or journal-bearing interpretation.
    config_json
        Validated effective configuration in its original serialization shape.
    run
        Original discovery identity and completion evidence.
    source_artifact
        Run-relative source DocumentIR path, retaining relocated-path provenance.
    """

    absent_artifacts: tuple[str, ...]
    artifacts: tuple[SnapshotArtifact, ...]
    checkpoint_format: str
    config_json: str
    run: DiscoveredRun
    source_artifact: str
