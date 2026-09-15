"""Discover and validate completed curriculum evidence for LP evaluation.

Discovery reads completion metadata. Snapshot validation reconstructs bounded requests
and reconciles persisted judgments, provenance and graph projections. Shared pure
artifact helpers and the read-only journal reader are used without invoking production
generation, recovery, writes or model calls. Validated bytes await separate freezing.
"""

# Standard Library
import fcntl
import hashlib
import json
import os
import stat

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from operator import itemgetter
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

# Third Party Library
from pydantic import SerializationInfo, SerializerFunctionWrapHandler, model_serializer

# Package Library
from kgfeg.evals.lp_eval.schemas import (
    DiscoveredRun,
    DiscoveryAlias,
    DiscoveryInventory,
    DiscoverySkip,
    FileFingerprint,
    SnapshotArtifact,
    ValidatedSnapshot,
)
from kgfeg.kgs.lc_export import _build_learning_component_nodes, _validate_merged_graph
from kgfeg.kgs.lp_artifacts import (
    LPGenerationSummary,
    LPStandaloneArtifacts,
    LPUnresolvedItems,
    _reconciled_counts,
)
from kgfeg.kgs.lp_checkpoints import (
    LPGenerationCheckpoints,
    _LPCheckpoint,
    _LPFailure,
    content_hash,
    reconciled_response,
)
from kgfeg.kgs.lp_export import (
    _artifact_byte_hashes,
    _combined_counts,
    _merge_material,
    _validate_combined_material,
)
from kgfeg.kgs.lp_finalization import LPFinalClaims, LPRelationships
from kgfeg.kgs.lp_requests import (
    LPGenerationRequest,
    LPRequestPopulation,
    _artifact_payloads,
    build_lp_generation_requests,
    build_lp_request_id,
    canonical_lp_json,
    lp_material_content_hash,
)
from kgfeg.kgs.lp_selection import build_lp_selection
from kgfeg.kgs.lp_validation import LPValidationReport
from kgfeg.kgs.lp_validation import _cycle_diagnostics as _lp_cycle_diagnostics
from kgfeg.kgs.lp_validation import _expected_relationship
from kgfeg.kgs.prompts import (
    build_lp_generation_prompt,
    validate_lp_generation_response,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsKGBundle,
    AcademicStandardsLCKGBundle,
    AcademicStandardsLCLPKGBundle,
    LCGenerationSummary,
    LPGenerationResponse,
    LPGenerationValidationVerdict,
)
from kgfeg.kgs.sfi_export import (
    _build_learning_commons_nodes,
    _build_learning_commons_relationships,
    _fingerprint_jsonable,
    _validate_graph_export,
)
from kgfeg.kgs.validators import verify_lp_generation_validation_integrity
from kgfeg.page_ir_extraction.validators import QualityError
from kgfeg.schemas import CreateKGConfig

_STAGE_FILES = {
    "draft": "lp_generation_draft_responses.jsonl",
    "verdict": "lp_generation_validation_verdicts.jsonl",
    "response": "lp_generation_responses.jsonl",
}


class _CapturedKGConfig(CreateKGConfig):
    """Interpret a captured configuration without serializing later operational defaults."""

    @model_serializer(mode="wrap")
    def _serialize_captured(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        """Preserve the original configuration's capacity-field presence.

        Parameters
        ----------
        handler
            Ordinary schema serialization.
        info
            Requested alias and exclusion settings.

        Returns
        -------
        dict[str, Any]
            Configuration in its captured historical shape.
        """

        material = handler(self)
        key = "lp" if info.by_alias is not False else "learning_progressions"

        if "max_concurrent_requests" not in self.learning_progressions.model_fields_set:
            material.get(key, {}).pop("max_concurrent_requests", None)

        return material


class _SnapshotReader:
    """Read stable input bytes once and retain exact path bindings for later freezing."""

    def __init__(self, root: Path) -> None:
        """Initialize a read-only run material collection.

        Parameters
        ----------
        root
            Resolved run directory.
        """

        self.root = root
        self.artifacts: dict[str, SnapshotArtifact] = {}
        self.absent: set[str] = set()

    def check_hashes(self, hashes: dict[str, str]) -> None:
        """Resolve recorded artifact digests against actual contained run files.

        Parameters
        ----------
        hashes
            Recorded basename-to-SHA-256 mapping.

        Raises
        ------
        ValueError
            A name escapes the run, a file is missing or its digest differs.
        """

        for name, expected in hashes.items():
            if Path(name).name != name or name in {"", ".", ".."}:
                raise ValueError(f"Invalid artifact binding name: {name!r}")

            self.read(name)

            if self.artifacts[name].fingerprint.sha256 != expected:
                raise ValueError(f"Artifact byte hash differs: {name}")

    def check_unchanged(self) -> None:
        """Recheck captured bytes and optional absences before returning evidence.

        Raises
        ------
        ValueError
            A material file changed or a previously absent artifact appeared.
        """

        for name, artifact in self.artifacts.items():
            raw, fingerprint = _read_snapshot_bytes(
                boundary=self.root.parent, path=self.root / name
            )

            if raw != artifact.payload or fingerprint != artifact.fingerprint:
                raise ValueError(f"Snapshot material changed: {name}")

        for name in self.absent:
            if os.path.lexists(self.root / name):
                raise ValueError(f"Snapshot absence changed: {name}")

    def optional(self, name: str) -> Any:
        """Read optional evidence while retaining absence as part of the input state.

        Parameters
        ----------
        name
            Optional artifact basename.

        Returns
        -------
        Any
            Decoded content, or None when absent.

        Raises
        ------
        ValueError
            Present evidence is invalid.
        """

        if not os.path.lexists(self.root / name):
            self.absent.add(name)
            return None

        return self.read(name)

    def read(self, name: str) -> Any:
        """Read strict JSON or JSONL without modifying the input directory.

        Parameters
        ----------
        name
            Run-relative artifact path, including a contained source-document path.

        Returns
        -------
        Any
            Strictly decoded material.

        Raises
        ------
        ValueError
            Evidence is unavailable, unstable or malformed.
        """

        if name not in self.artifacts:
            raw, fingerprint = _read_snapshot_bytes(
                boundary=self.root.parent, path=self.root / name
            )
            self.artifacts[name] = SnapshotArtifact(
                fingerprint=fingerprint, name=name, payload=raw
            )

        raw = self.artifacts[name].payload

        if name.endswith(".jsonl"):
            return [_decode_snapshot_json(line) for line in raw.splitlines()]

        return _decode_snapshot_json(raw)


class LPDiscoveryError(ValueError):
    """Invalid or unavailable discovery evidence.

    Attributes
    ----------
    inventory
        Completed discovery inventory for a no-candidate failure, when available.
    """

    def __init__(
        self, *, inventory: DiscoveryInventory | None = None, message: str
    ) -> None:
        """Retain diagnostic inventory without representing it as valid input.

        Parameters
        ----------
        inventory
            Discovery results available before failure.
        message
            Path-specific failure or no-completed-candidate explanation.
        """

        super().__init__(message)
        self.inventory = inventory


class LPSnapshotError(ValueError):
    """A selected completed run has unavailable, changed or inconsistent evidence."""


def _check_checkpoint_row(
    *,
    material: dict[str, Any],
    prerequisite: str,
    prompt_hash: str,
    request_index: int,
    row: _LPCheckpoint,
    stage: str,
) -> None:
    """Verify a stage's payload and exact request, prompt and prerequisite bindings.

    Parameters
    ----------
    material
        Captured execution material.
    prerequisite
        Expected prior-stage or bounded-request digest.
    prompt_hash
        Digest of the original stage messages.
    request_index
        Position in the original complete request schedule.
    row
        Parsed persisted checkpoint.
    stage
        Expected producer, checker or reconciled stage.

    Raises
    ------
    ValueError
        Any checkpoint binding differs.
    """

    if (
        row.execution_content_hash != content_hash(material)
        or row.payload_content_hash != content_hash(row.payload)
        or row.prerequisite_content_hash != prerequisite
        or row.prompt_content_hash != prompt_hash
        or row.request_index != request_index
        or row.stage != stage
    ):
        raise ValueError(f"Checkpoint material differs: {stage}[{request_index}]")


def _checkpoint_claims(
    *,
    claims: LPFinalClaims,
    population: LPRequestPopulation,
    reader: _SnapshotReader,
    rows: dict[str, list[_LPCheckpoint]],
) -> None:
    """Reconcile each final claim with its original request and both model stages.

    Parameters
    ----------
    claims
        Complete self-consistent claim artifact.
    population
        Upstream-reconstructed bounded requests and candidates.
    reader
        Captured run artifacts.
    rows
        Persisted stage rows with complete ordered coverage.

    Raises
    ------
    ValueError
        Selected responses, provenance or material dependencies differ.
    """

    material = reader.read("lp_generation_checkpoint_manifest.json")["material"]
    expected_claims = []
    candidates = {row.pair_id: row for row in population.candidates.candidates}

    for index, request in enumerate(population.requests):
        draft_row, verdict_row, response_row = (
            rows[stage][index] for stage in ("draft", "verdict", "response")
        )
        draft = LPGenerationResponse.model_validate(draft_row.payload)
        verdict = LPGenerationValidationVerdict.model_validate(verdict_row.payload)
        verify_lp_generation_validation_integrity(
            draft_response=draft,
            lp_generation_request=request,
            validation_verdict=verdict,
        )
        response = reconciled_response(draft=draft, verdict=verdict)
        _equal(
            actual=response_row.payload,
            expected=response.model_dump(mode="json"),
            label=f"Reconciled response {index}",
        )
        producer_prompt = build_lp_generation_prompt(
            lp_generation_request=request,
            producer_instructions=material["producer_instructions"],
        )
        checker_prompt = validate_lp_generation_response(
            checker_instructions=material["checker_instructions"],
            draft_response=draft,
            lp_generation_request=request,
            producer_instructions=material["producer_instructions"],
        )

        for stage, row, prerequisite, prompt in (
            ("draft", draft_row, request.request_content_hash, producer_prompt),
            (
                "verdict",
                verdict_row,
                content_hash(draft_row.model_dump(mode="json")),
                checker_prompt,
            ),
            (
                "response",
                response_row,
                content_hash(verdict_row.model_dump(mode="json")),
                checker_prompt,
            ),
        ):
            _check_checkpoint_row(
                material=material,
                prerequisite=prerequisite,
                prompt_hash=content_hash(
                    {
                        "system_message": prompt.system_message,
                        "user_message": prompt.user_message,
                    }
                ),
                request_index=index,
                row=row,
                stage=stage,
            )

        producers = {judgment.pair_id: judgment for judgment in draft.judgments}
        judgments = {judgment.pair_id: judgment for judgment in response.judgments}

        for pair in request.pairs:
            judgment = judgments[pair.pair_id]
            source, target = _judgment_endpoints(judgment.model_dump(mode="json"))
            expected_claims.append(
                {
                    "candidate": candidates[pair.pair_id].model_dump(mode="json"),
                    "judgment": judgment.model_dump(mode="json"),
                    "source_sfi_uuid": source,
                    "target_sfi_uuid": target,
                    "provenance": {
                        "checker_checkpoint_content_hash": content_hash(
                            verdict_row.model_dump(mode="json")
                        ),
                        "checker_outcome": (
                            "accepted" if verdict.passed else "corrected"
                        ),
                        "checker_prompt_content_hash": verdict_row.prompt_content_hash,
                        "checker_verdict_content_hash": verdict_row.payload_content_hash,
                        "execution_content_hash": content_hash(material),
                        "judgment_content_hash": content_hash(
                            judgment.model_dump(mode="json")
                        ),
                        "producer_checkpoint_content_hash": content_hash(
                            draft_row.model_dump(mode="json")
                        ),
                        "producer_judgment": producers[pair.pair_id].model_dump(
                            mode="json"
                        ),
                        "producer_prompt_content_hash": draft_row.prompt_content_hash,
                        "producer_response_content_hash": draft_row.payload_content_hash,
                        "request_content_hash": request.request_content_hash,
                        "request_id": str(request.request_id),
                        "response_checkpoint_content_hash": content_hash(
                            response_row.model_dump(mode="json")
                        ),
                        "response_content_hash": response_row.payload_content_hash,
                    },
                }
            )

    _equal(
        actual=[claim.model_dump(mode="json") for claim in claims.claims],
        expected=expected_claims,
        label="Final claim reconciliation",
    )


def _classify_run(*, kgs_directory: Path, results_root: Path) -> DiscoveredRun:
    """Read one run's status and completed-candidate identity claims.

    Parameters
    ----------
    kgs_directory
        Resolved directory containing the execution metadata.
    results_root
        Containment boundary for all metadata reads.

    Returns
    -------
    DiscoveredRun
        Preliminary status without graph, checkpoint or snapshot certification.

    Raises
    ------
    LPDiscoveryError
        Invalid or unavailable discovery evidence.
    ValueError
        Invalid metadata or contradictory status/timestamp fields.
    """

    metadata, fingerprint = _read_metadata(
        path=kgs_directory / "kg_run.json", results_root=results_root
    )

    try:
        run_id = UUID(_required_text(key="run_id", record=metadata))
        started_at = _timestamp(metadata.get("started_at"))
        completed_at = (
            None
            if metadata.get("completed_at") is None
            else _timestamp(metadata["completed_at"])
        )
        classification, reason = _completion_status(
            completed_at=completed_at,
            extra=metadata.get("extra"),
            started_at=started_at,
        )
    except (TypeError, ValueError) as exc:
        raise LPDiscoveryError(
            message=f"Invalid completion metadata in {fingerprint.path}: {exc}"
        ) from exc

    doc_key = None
    framework_uuid = None
    fingerprints = [fingerprint]

    if classification == "completed_candidate":
        manifest, manifest_fingerprint = _read_metadata(
            path=kgs_directory / "kg_run_manifest.json", results_root=results_root
        )
        requests, requests_fingerprint = _read_metadata(
            path=kgs_directory / "lp_generation_requests_manifest.json",
            results_root=results_root,
        )

        try:
            doc_key = _required_text(key="doc_key", record=manifest)

            if doc_key != _required_text(key="doc_key", record=requests):
                raise ValueError("KG and LP request manifests disagree on doc_key")

            framework_uuid = UUID(_required_text(key="framework_uuid", record=requests))
        except (TypeError, ValueError) as exc:
            raise LPDiscoveryError(
                message=f"Invalid completed-candidate identity in {kgs_directory}: {exc}"
            ) from exc

        fingerprints.extend([manifest_fingerprint, requests_fingerprint])

    return DiscoveredRun(
        aliases=(),
        completed_at=completed_at,
        doc_key=doc_key,
        fingerprints=tuple(fingerprints),
        framework_uuid=framework_uuid,
        kgs_directory=kgs_directory,
        reason=reason,
        run_id=run_id,
        started_at=started_at,
        status=classification,
    )


def _completion_status(
    *, completed_at: datetime | None, extra: Any, started_at: datetime
) -> tuple[Literal["completed_candidate", "failed", "unfinished"], str]:
    """Classify only internally consistent execution status and timestamps.

    Parameters
    ----------
    completed_at
        Optional recorded completion timestamp.
    extra
        Parsed execution metadata containing the terminal status.
    started_at
        Recorded execution start.

    Returns
    -------
    tuple[Literal["completed_candidate", "failed", "unfinished"], str]
        Preliminary state and its inclusion or exclusion explanation.

    Raises
    ------
    ValueError
        Status metadata is malformed or contradicts the recorded timestamps.
    """

    if not isinstance(extra, dict):
        raise ValueError("extra must be a JSON object")

    if completed_at is not None and completed_at < started_at:
        raise ValueError("completion precedes execution start")

    status = extra.get("status")

    if status is None and completed_at is None:
        return "unfinished", "Execution started without a recorded terminal status."

    if status == "error" and completed_at is not None:
        return "failed", "Execution recorded a terminal error."

    if status == "success" and completed_at is not None:
        return (
            "completed_candidate",
            "Recorded success; full snapshot validation is still required.",
        )

    raise ValueError("unrecognized or contradictory status/timestamp fields")


def _decode_snapshot_json(payload: bytes) -> Any:
    """Decode finite JSON with unique object keys.

    Parameters
    ----------
    payload
        Complete UTF-8 JSON bytes.

    Returns
    -------
    Any
        Decoded material.

    Raises
    ------
    ValueError
        JSON is malformed, contains duplicate keys or nonfinite values.
    """

    return json.loads(
        payload, object_pairs_hook=_metadata_object, parse_constant=_nonfinite_number
    )


def _equal(*, actual: Any, expected: Any, label: str) -> None:
    """Reject material differences without dumping potentially large source records.

    Parameters
    ----------
    actual
        Observed material.
    expected
        Independently derived required material.
    label
        Artifact or binding name for a concise failure.

    Raises
    ------
    ValueError
        Material differs.
    """

    if actual != expected:
        raise ValueError(f"{label} differs from its source material")


def _is_run_directory(*, evaluation_root: Path, path: Path, resolved: Path) -> bool:
    """Recognize run boundaries while preventing nested evaluator outputs.

    Parameters
    ----------
    evaluation_root
        Resolved evaluator output subtree.
    path
        Discovered directory path.
    resolved
        Physical directory path.

    Returns
    -------
    bool
        Whether the discovered or resolved directory is named kgs.

    Raises
    ------
    ValueError
        A recognized production run contains the evaluator output location.
    """

    if resolved.name != "kgs" and path.name != "kgs":
        return False

    if evaluation_root.is_relative_to(resolved):
        raise ValueError("evaluator output lies inside a production run")

    return True


def _judgment_endpoints(judgment: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve publishing endpoints from a structurally valid final judgment.

    Parameters
    ----------
    judgment
        Validated judgment with canonical pair order.

    Returns
    -------
    tuple[str | None, str | None]
        Directed or canonical symmetric endpoints; absent for nonpublishing outcomes.
    """

    if judgment["decision"] in {"no_relation", "needs_review"}:
        return None, None

    first, second = judgment["first_sfi_uuid"], judgment["second_sfi_uuid"]

    if judgment["direction"] == "second_to_first":
        return second, first

    return first, second


def _metadata_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate object keys instead of accepting a last-value overwrite.

    Parameters
    ----------
    pairs
        JSON object members in their encoded order.

    Returns
    -------
    dict[str, Any]
        Object retaining each key exactly once.

    Raises
    ------
    ValueError
        Duplicate key encountered in the JSON object.
    """

    result: dict[str, Any] = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")

        result[key] = value

    return result


def _nonfinite_number(value: str) -> None:
    """Reject non-JSON numeric constants in completion metadata.

    Parameters
    ----------
    value
        Nonfinite constant encountered by the JSON decoder.

    Raises
    ------
    ValueError
        Always, because these constants are not valid JSON.
    """

    raise ValueError(f"invalid JSON numeric constant: {value}")


def _path_exclusion(
    *, evaluation_root: Path, path: Path, resolved: Path, results_root: Path
) -> DiscoverySkip | None:
    """Identify subtrees outside the permitted discovery population.

    Parameters
    ----------
    evaluation_root
        Resolved output subtree excluded from discovery.
    path
        Encountered path retained in the exclusion record.
    resolved
        Physical path used for containment checks.
    results_root
        Resolved input discovery root.

    Returns
    -------
    DiscoverySkip | None
        An explicit exclusion, or None when traversal is permitted.
    """

    if not resolved.is_relative_to(results_root):
        return DiscoverySkip(path=path, reason="outside_results_root")

    if resolved.is_relative_to(evaluation_root):
        return DiscoverySkip(path=path, reason="evaluation_output")

    return None


def _read_metadata(
    *, path: Path, results_root: Path
) -> tuple[dict[str, Any], FileFingerprint]:
    """Read a stable regular JSON file contained inside the selected root.

    Parameters
    ----------
    path
        Required metadata file.
    results_root
        Resolved starting directory constraining file access.

    Returns
    -------
    tuple[dict[str, Any], FileFingerprint]
        Parsed object and identity of the actual bytes read.

    Raises
    ------
    LPDiscoveryError
        Invalid or unavailable discovery evidence.
    ValueError
        Metadata is not a regular file, resolves outside the root or changes while
        being read.
    """

    try:
        resolved = path.resolve(strict=True)

        if not resolved.is_relative_to(results_root):
            raise ValueError("metadata resolves outside the starting directory")

        before = resolved.stat()

        if not stat.S_ISREG(before.st_mode):
            raise ValueError("metadata is not a regular file")

        with resolved.open(mode="rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read()
            after = os.fstat(stream.fileno())

        if (
            not (
                _stat_identity(before)
                == _stat_identity(opened)
                == _stat_identity(after)
                == _stat_identity(resolved.stat())
            )
            or path.resolve(strict=True) != resolved
        ):
            raise ValueError("metadata changed while being read")

        decoded = json.loads(
            object_pairs_hook=_metadata_object, parse_constant=_nonfinite_number, s=raw
        )

        if not isinstance(decoded, dict):
            raise ValueError("metadata must be a JSON object")
    except (OSError, RuntimeError, ValueError) as exc:
        raise LPDiscoveryError(
            message=f"Cannot inspect required metadata {path}: {exc}"
        ) from exc

    return decoded, FileFingerprint(
        path=resolved, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
    )


def _read_snapshot_bytes(
    *, boundary: Path, path: Path
) -> tuple[bytes, FileFingerprint]:
    """Capture stable regular-file bytes inside the physical run/source boundary.

    Parameters
    ----------
    boundary
        Physical directory containing the run and its source-document artifacts.
    path
        Required input path.

    Returns
    -------
    tuple[bytes, FileFingerprint]
        Exact payload and resolved path/byte identity.

    Raises
    ------
    ValueError
        The file is missing, escapes containment or changes during reading.
    """

    resolved = path.resolve(strict=True)
    before = resolved.stat()

    if not resolved.is_relative_to(boundary) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Input must be a contained regular file: {path}")

    with resolved.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read()
        after = os.fstat(stream.fileno())

    if (
        len({_stat_identity(item) for item in (before, opened, after, resolved.stat())})
        != 1
        or path.resolve(strict=True) != resolved
    ):
        raise ValueError(f"Input changed while reading: {path}")

    return raw, FileFingerprint(
        path=resolved, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
    )


def _register_run_identity(
    *, identities: dict[tuple[str, str], Path], run: DiscoveredRun
) -> None:
    """Register completed identity claims without merging different snapshots.

    Parameters
    ----------
    identities
        Invocation-local index of previously observed identity claims.
    run
        Classified run whose completed identity claims should be registered.

    Raises
    ------
    LPDiscoveryError
        A completed candidate lacks identity or conflicts with a previous directory.
    """

    if run.status != "completed_candidate":
        return

    directory = run.kgs_directory

    if run.doc_key is None or run.framework_uuid is None:
        raise LPDiscoveryError(
            message=f"Completed candidate lacks identity: {directory}"
        )

    for identity in (
        ("doc_key", run.doc_key),
        ("framework_uuid", str(run.framework_uuid)),
    ):
        if identity in identities:
            raise LPDiscoveryError(
                message=(
                    f"Conflicting completed snapshots: {identities[identity]} and "
                    f"{directory} claim the same identity {identity}."
                )
            )

        identities[identity] = directory


def _required_text(*, key: str, record: dict[str, Any]) -> str:
    """Read an exact nonblank text field without normalizing its identity.

    Parameters
    ----------
    key
        Required object member.
    record
        Parsed metadata object.

    Returns
    -------
    str
        Exact recorded text.

    Raises
    ------
    ValueError
        Required key is missing, not a string or contains surrounding whitespace.
    """

    value = record.get(key)

    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{key} must be nonblank text without surrounding whitespace")

    return value


def _snapshot_config(reader: _SnapshotReader) -> _CapturedKGConfig:
    """Recover the captured effective configuration and verify both hash conventions.

    The execution metadata omits the overwrite flag. Its value is recovered only
    when exactly one boolean choice matches the recorded effective-config digest.
    A later concurrency default is never included in historical serialized material.

    Parameters
    ----------
    reader
        Captured execution metadata and original configuration hashes.

    Returns
    -------
    _CapturedKGConfig
        Validated policy whose serialized bytes retain their original shape.

    Raises
    ------
    ValueError
        Required namespaces are absent, defaults alter captured fields, or no unique
        effective configuration matches the recorded digest.
    """

    extra = reader.read("kg_run.json")["extra"]
    raw = {key: extra[key] for key in ("as", "lc", "lp", "metadata")}
    expected = reader.read("lp_generation_checkpoint_manifest.json")["material"][
        "config_content_hash"
    ]
    matches = []

    for overwrite in (False, True):
        config = _CapturedKGConfig.model_validate({**raw, "overwrite": overwrite})
        dumped = config.model_dump(mode="json")
        _equal(
            actual={key: dumped[key] for key in raw},
            expected=raw,
            label="Captured configuration namespaces",
        )

        if content_hash(dumped) == expected:
            matches.append(config)

    if len(matches) != 1:
        raise ValueError("Captured effective configuration has no unique hash binding")

    return matches[0]


def _snapshot_execution(
    *,
    config: _CapturedKGConfig,
    population: LPRequestPopulation,
    reader: _SnapshotReader,
) -> tuple[str, dict[str, list[_LPCheckpoint]]]:
    """Validate completed historical prefixes or complete journal-bearing execution.

    Parameters
    ----------
    config
        Captured effective configuration.
    population
        Fully reconstructed original requests.
    reader
        Stable input reader.

    Returns
    -------
    tuple[str, dict[str, list[_LPCheckpoint]]]
        Explicit format interpretation and ordered producer/checker/final rows.

    Raises
    ------
    ValueError
        Execution is incomplete, incompatible, stale or has unresolved failures.
    """

    receipt = reader.read("lp_generation_checkpoint_manifest.json")
    _snapshot_receipt_fields(config=config, receipt=receipt)
    material = receipt["material"]

    for name in ("producer_instructions", "checker_instructions"):
        _equal(
            actual=material[name],
            expected=config.model_dump(mode="json")["lp"][name],
            label=name,
        )

    _equal(
        actual=material["request_manifest"],
        expected=population.manifest.model_dump(mode="json"),
        label="Execution request manifest",
    )
    _equal(
        actual=receipt["execution_content_hash"],
        expected=content_hash(material),
        label="Execution material hash",
    )
    _equal(actual=receipt["status"], expected="completed", label="Execution status")
    _equal(actual=receipt["failed_pair_ids"], expected=[], label="Unresolved failures")
    _equal(
        actual=material["retry_limits"],
        expected={
            "draft": config.learning_progressions.retry.producer_max_retries,
            "verdict": config.learning_progressions.retry.checker_max_retries,
        },
        label="Captured retry limits",
    )

    if (
        isinstance(receipt["run_number"], bool)
        or not isinstance(receipt["run_number"], int)
    ) or receipt["run_number"] < 1:
        raise ValueError("Invalid completed execution run number")

    reader.optional("lp_generation_checkpoint_transaction.json")

    if "lp_generation_checkpoint_transaction.json" not in reader.absent:
        raise ValueError("Completed run has an unfinished checkpoint transaction")

    rows = {
        stage: [_LPCheckpoint.model_validate(row) for row in reader.read(name)]
        for stage, name in _STAGE_FILES.items()
    }
    expected_counts = {stage: len(population.requests) for stage in _STAGE_FILES}
    _equal(
        actual=receipt["stage_counts"],
        expected=expected_counts,
        label="Execution stage counts",
    )
    _equal(
        actual={stage: len(values) for stage, values in rows.items()},
        expected=expected_counts,
        label="Actual stage coverage",
    )
    reader.check_hashes(receipt["artifact_byte_hashes"])
    _snapshot_failures(population=population, reader=reader, rows=rows)

    if "max_concurrent_requests" in material:
        _equal(
            actual=material["max_concurrent_requests"],
            expected=config.model_dump(mode="json")["lp"]["max_concurrent_requests"],
            label="Captured execution capacity",
        )
        store = LPGenerationCheckpoints(
            material=material, population=population, read_only=True, root=reader.root
        )
        store.verify_bytes()
        return "journal_bearing", rows

    expected_files = {*_STAGE_FILES.values(), "lp_generation_failures.json"}
    _equal(
        actual=set(receipt["artifact_byte_hashes"]),
        expected=expected_files,
        label="Historical prefix receipt coverage",
    )

    for name in ("lp_generation_usage.json", "lp_generation_pending_completions.json"):
        reader.optional(name)

        if name not in reader.absent:
            raise ValueError(
                "Historical prefix receipt has unauthenticated journal material"
            )

    return "historical_prefix", rows


def _snapshot_failures(
    *,
    population: LPRequestPopulation,
    reader: _SnapshotReader,
    rows: dict[str, list[_LPCheckpoint]],
) -> None:
    """Validate preserved failed attempts and their successful recovery bindings.

    Parameters
    ----------
    population
        Original bounded requests.
    reader
        Captured failure artifact and receipt.
    rows
        Complete successful checkpoints.

    Raises
    ------
    ValueError
        A failure is unresolved or references inconsistent requests or recovery.
    """

    receipt = reader.read("lp_generation_checkpoint_manifest.json")

    for value in reader.read("lp_generation_failures.json"):
        failure = _LPFailure.model_validate(value)
        request = population.requests[failure.request_index]
        expected = {
            "pair_ids": [pair.pair_id for pair in request.pairs],
            "request_content_hash": request.request_content_hash,
            "request_id": str(request.request_id),
            "resolved_response_content_hash": rows["response"][
                failure.request_index
            ].payload_content_hash,
        }
        _equal(
            actual={key: value[key] for key in expected},
            expected=expected,
            label="Failure recovery binding",
        )

        if (
            failure.resolved_run_number is None
            or not failure.run_number
            <= failure.resolved_run_number
            <= receipt["run_number"]
        ):
            raise ValueError("Unresolved or invalid failure recovery")


def _snapshot_graphs(
    *,
    artifacts: LPStandaloneArtifacts,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> None:
    """Reconcile the additive combined graph, reports and exact internal projections.

    Parameters
    ----------
    artifacts
        Reconciled standalone LP artifacts.
    config
        Captured effective configuration.
    reader
        Stable input reader.
    upstream
        Validated upstream bundle.

    Raises
    ------
    ValueError
        Combined material, reports or projections disagree.
    """

    combined_raw = reader.read("as_lc_lp_kg_bundle.json")
    combined = AcademicStandardsLCLPKGBundle.model_validate(combined_raw)
    material = _merge_material(
        artifacts=artifacts, upstream=upstream.model_dump(mode="json")
    )
    _validate_combined_material(
        artifacts=artifacts,
        material=material,
        upstream=upstream.model_dump(mode="json"),
    )
    _equal(
        actual={
            key: value
            for key, value in combined_raw.items()
            if key != "validation_report"
        },
        expected=material,
        label="Additive combined graph",
    )
    report = combined_raw["validation_report"]
    _equal(
        actual=report["as_lc_validation_report"],
        expected=upstream.validation_report.model_dump(mode="json"),
        label="Combined upstream report",
    )
    _equal(
        actual=report["lp_validation_report"],
        expected=artifacts.validation_report.model_dump(mode="json"),
        label="Combined LP report",
    )
    hashes = {
        **artifacts.summary.input_content_hashes,
        "combined_graph": content_hash(material),
        "lp_generation_summary": content_hash(
            artifacts.summary.model_dump(mode="json")
        ),
        "lp_unresolved_items": content_hash(
            artifacts.unresolved_items.model_dump(mode="json")
        ),
        "lp_validation_report": content_hash(
            artifacts.validation_report.model_dump(mode="json")
        ),
    }
    _equal(
        actual=report["input_content_hashes"],
        expected=hashes,
        label="Combined material hashes",
    )
    _equal(
        actual=report["artifact_byte_hashes"],
        expected=_artifact_byte_hashes(artifacts),
        label="Combined artifact hashes",
    )
    _equal(
        actual=report["object_counts"],
        expected=_combined_counts(material),
        label="Combined object counts",
    )
    _equal(
        actual=hashes["effective_config"],
        expected=content_hash(config.model_dump(mode="json")),
        label="Combined configuration",
    )
    _equal(
        actual=combined.model_dump(mode="json"),
        expected=combined_raw,
        label="Combined schema round trip",
    )

    if not report["passed"] or report["errors"]:
        raise ValueError("Combined graph reports failed validation")

    nodes = [{**material["framework"], "entity_type": "StandardsFramework"}]

    for field, identity, entity in (
        ("items", "case_identifier_uuid", "StandardsFrameworkItem"),
        ("learning_components", "identifier", "LearningComponent"),
    ):
        nodes.extend(
            {**row, "entity_type": entity}
            for row in sorted(material[field], key=itemgetter(identity))
        )

    edges = [
        row
        for field in (
            "relationships_has_child",
            "relationships_supports",
            "relationships_builds_towards",
            "relationships_relates_to",
        )
        for row in sorted(material[field], key=lambda row: row["identifier"])
    ]
    _equal(
        actual=reader.read("as_lc_lp_nodes.jsonl"),
        expected=nodes,
        label="Combined node projection",
    )
    _equal(
        actual=reader.read("as_lc_lp_relationships.jsonl"),
        expected=edges,
        label="Combined edge projection",
    )


@contextmanager
def _snapshot_ownership(reader: _SnapshotReader) -> Iterator[None]:
    """Observe an existing writer lock without creating one for a historical copy.

    Parameters
    ----------
    reader
        Run whose material is being validated.

    Yields
    ------
    None
        Read-only validation scope.

    Raises
    ------
    ValueError
        The lock is replaced or lies outside the run.
    OSError
        A writer holds the lock or it cannot be read.
    """

    path = reader.root / ".lp_generation.lock"

    if not os.path.lexists(path):
        reader.absent.add(path.name)
        yield
        return

    if path.resolve(strict=True).parent != reader.root:
        raise ValueError("Generation lock resolves outside the run")

    with path.open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        before = _stat_identity(os.fstat(lock.fileno()))
        yield

        if before != _stat_identity(path.stat()):
            raise ValueError("Generation lock changed during validation")


def _snapshot_population(
    *,
    config: _CapturedKGConfig,
    doc_key: str,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> LPRequestPopulation:
    """Reconstruct requests with the captured configuration's historical hash shape.

    The shared pure builder copies configuration through the current schema. Its
    evidence construction is unchanged; only newly computed configuration-dependent
    identities are rebound here when that copy adds a later capacity default. Saved
    requests, receipts and their hashes are never changed.

    Parameters
    ----------
    config
        Captured policy with original field presence.
    doc_key
        Source document identity.
    reader
        Read-only original artifact collection.
    upstream
        Validated fixed upstream graph.

    Returns
    -------
    LPRequestPopulation
        Independently reconstructed and byte-matched original requests.

    Raises
    ------
    ValueError
        Reconstructed candidates, bounded evidence or material identities differ.
    """

    population = build_lp_generation_requests(
        as_lc_bundle=upstream, doc_key=doc_key, kg_config=config
    )
    config_hash = lp_material_content_hash(config.model_dump(mode="json"))
    summary = population.candidates.summary.model_copy(
        update={"config_content_hash": config_hash}
    )
    candidates = replace(population.candidates, summary=summary)
    summary_hash = lp_material_content_hash(summary.model_dump(mode="json"))
    requests = []

    for request in population.requests:
        material = request.model_dump(
            exclude={"request_content_hash", "request_id"}, mode="json"
        )
        material.update(
            config_content_hash=config_hash, candidate_summary_content_hash=summary_hash
        )
        digest = lp_material_content_hash(material)
        requests.append(
            LPGenerationRequest.model_validate(
                {
                    **material,
                    "request_content_hash": digest,
                    "request_id": build_lp_request_id(digest),
                }
            )
        )

    payloads = _artifact_payloads(candidates=candidates, requests=tuple(requests))
    manifest = population.manifest.model_copy(
        update={
            "artifact_byte_hashes": {
                name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()
            },
            "candidate_summary_content_hash": summary_hash,
            "config_content_hash": config_hash,
            "request_ids": tuple(request.request_id for request in requests),
            "requests_content_hash": lp_material_content_hash(
                [request.model_dump(mode="json") for request in requests]
            ),
        }
    )
    payloads["lp_generation_requests_manifest.json"] = (
        canonical_lp_json(manifest.model_dump(mode="json")) + "\n"
    ).encode()

    for name, expected in payloads.items():
        reader.read(name)
        _equal(actual=reader.artifacts[name].payload, expected=expected, label=name)

    return LPRequestPopulation(
        candidates=candidates, manifest=manifest, requests=tuple(requests)
    )


def _snapshot_receipt_fields(
    *, config: _CapturedKGConfig, receipt: dict[str, Any]
) -> None:
    """Require one recognized complete execution format without adding missing fields.

    Parameters
    ----------
    config
        Captured configuration and original capacity-field presence.
    receipt
        Recorded completed checkpoint receipt.

    Raises
    ------
    ValueError
        Receipt or execution fields are incomplete or unknown.
    """

    _equal(
        actual=set(receipt),
        expected={
            "artifact_byte_hashes",
            "execution_content_hash",
            "failed_pair_ids",
            "material",
            "run_number",
            "stage_counts",
            "status",
        },
        label="Checkpoint receipt fields",
    )
    expected = {
        "checker_instructions",
        "config_content_hash",
        "model_config",
        "model_settings",
        "producer_instructions",
        "prompt_definitions_content_hash",
        "request_manifest",
        "response_schema_content_hash",
        "retry_limits",
        "verdict_schema_content_hash",
    }

    if "max_concurrent_requests" in config.model_dump(mode="json")["lp"]:
        expected.add("max_concurrent_requests")

    _equal(
        actual=set(receipt["material"]),
        expected=expected,
        label="Captured execution fields",
    )


def _snapshot_relationships(
    *,
    claims: LPFinalClaims,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> LPRelationships:
    """Reconstruct direct published rows and provenance from reconciled final claims.

    Parameters
    ----------
    claims
        Complete reconciled final judgments.
    config
        Captured policy and attribution.
    reader
        Stable input reader.
    upstream
        Source framework and permitted endpoint authority.

    Returns
    -------
    LPRelationships
        Exact standalone relationship population.

    Raises
    ------
    ValueError
        Relationships, source attribution or provenance disagree with direct claims.
    """

    edges = []
    provenance = {}

    for claim in claims.claims:
        if claim.source_sfi_uuid is None:
            continue

        edge, proof = _expected_relationship(
            as_lc_bundle=upstream,
            claim=claim,
            doc_key=claims.request_manifest.doc_key,
            final_claims=claims,
            kg_config=config,
        )

        # Preserve the original LP configuration hash, without adding a later capacity.
        proof.effective_lp_config_content_hash = content_hash(
            config.model_dump(mode="json")["lp"]
        )
        edge.metadata = proof.model_dump(mode="json")
        edges.append(edge)
        provenance[str(edge.identifier)] = proof

    edges.sort(key=lambda edge: str(edge.identifier))
    relationships = LPRelationships(
        final_claims_content_hash=claims.content_hash,
        relationship_provenance=provenance,
        relationships_builds_towards=tuple(
            edge for edge in edges if edge.relationship_type == "buildsTowards"
        ),
        relationships_relates_to=tuple(
            edge for edge in edges if edge.relationship_type == "relatesTo"
        ),
    )

    for field, filename in (
        ("relationships_builds_towards", "lp_relationships_builds_towards.jsonl"),
        ("relationships_relates_to", "lp_relationships_relates_to.jsonl"),
    ):
        _equal(
            actual=reader.read(filename),
            expected=[
                edge.model_dump(mode="json") for edge in getattr(relationships, field)
            ],
            label=filename,
        )

    _equal(
        actual=reader.read("lp_relationship_provenance.json"),
        expected={
            key: proof.model_dump(mode="json") for key, proof in provenance.items()
        },
        label="Relationship provenance",
    )
    return relationships


def _snapshot_source(
    *, config: _CapturedKGConfig, reader: _SnapshotReader, run: DiscoveredRun
) -> str:
    """Bind original source identity while supporting relocated result directories.

    Parameters
    ----------
    config
        Captured framework metadata.
    reader
        Stable input reader.
    run
        Discovered completed candidate.

    Returns
    -------
    str
        Actual run-relative source DocumentIR path.

    Raises
    ------
    ValueError
        Source location escapes the run container or identities disagree.
    """

    manifest = reader.read("kg_run_manifest.json")
    captured_root = Path(manifest["kg_run_dir"])
    source = Path(manifest["document_ir_fp"])
    relative = source.relative_to(captured_root.parent)
    name = (Path("..") / relative).as_posix()
    document = reader.read(name)
    framework = reader.read("as_lc_kg_bundle.json")["framework"]
    identities = [
        manifest["doc_key"],
        document["doc_key"],
        framework["metadata"]["doc_key"],
    ]
    _equal(
        actual=identities,
        expected=[run.doc_key] * len(identities),
        label="Source document identity",
    )
    _equal(
        actual=framework["case_identifier_uuid"],
        expected=str(run.framework_uuid),
        label="Framework identity",
    )
    _equal(
        actual=document["pdf_name"],
        expected=manifest["pdf_name"],
        label="Source document name",
    )
    _equal(
        actual=document["page_count"],
        expected=manifest["page_count"],
        label="Source page count",
    )
    _equal(
        actual=manifest["framework_title"],
        expected=config.metadata.framework_title,
        label="Captured framework title",
    )
    return name


def _snapshot_standalone(
    *,
    claims: LPFinalClaims,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    relationships: LPRelationships,
    upstream: AcademicStandardsLCKGBundle,
) -> LPStandaloneArtifacts:
    """Reconcile recorded validation reports, counts, warnings and ambiguity artifacts.

    Parameters
    ----------
    claims
        Fully reconciled direct claims.
    config
        Captured configuration.
    reader
        Stable input reader.
    relationships
        Reconstructed direct relationships.
    upstream
        Validated upstream graph.

    Returns
    -------
    LPStandaloneArtifacts
        Standalone artifacts with actual counts and material links checked.

    Raises
    ------
    ValueError
        Summary, report, ambiguity or source material does not reconcile.
    """

    summary = LPGenerationSummary.model_validate(
        reader.read("lp_generation_summary.json")
    )
    report = LPValidationReport.model_validate(reader.read("lp_validation_report.json"))
    unresolved = LPUnresolvedItems.model_validate(
        reader.read("lp_unresolved_items.json")
    )
    failures = reader.read("lp_generation_failures.json")
    _equal(
        actual=report.cycle_diagnostics,
        expected=_lp_cycle_diagnostics(
            provenance=relationships.relationship_provenance,
            relationships=relationships.relationships_builds_towards,
        ),
        label="LP cycle diagnostics",
    )
    _equal(
        actual=report.object_counts["identifier_collisions"],
        expected=0,
        label="LP identifier collisions",
    )

    if not report.passed or report.errors or claims.graph_status != "acyclic":
        raise ValueError("LP graph lacks successful structural validation")

    _equal(
        actual=[claim.model_dump(mode="json") for claim in unresolved.claims],
        expected=[
            claim.model_dump(mode="json")
            for claim in claims.claims
            if claim.judgment.decision == "needs_review"
        ],
        label="Ambiguous claims",
    )
    _equal(
        actual=unresolved.final_claims_content_hash,
        expected=claims.content_hash,
        label="Ambiguity claim binding",
    )
    counts = _reconciled_counts(
        claims=claims, failures=failures, relationships=relationships, report=report
    )
    _equal(actual=summary.object_counts, expected=counts, label="Summary object counts")
    selection = build_lp_selection(as_lc_bundle=upstream, kg_config=config)

    for name, expected in (
        ("lp_eligibility_report.json", selection.model_dump(mode="json")),
        (
            "lp_eligible_sfis.json",
            [row.model_dump(mode="json") for row in selection.eligible_sfis],
        ),
    ):
        actual = reader.optional(name)

        if name not in reader.absent:
            _equal(actual=actual, expected=expected, label=name)

    hashes = {
        "as_lc_bundle": content_hash(upstream.model_dump(mode="json")),
        "checkpoint_receipt_bytes": reader.artifacts[
            "lp_generation_checkpoint_manifest.json"
        ].fingerprint.sha256,
        "effective_config": content_hash(config.model_dump(mode="json")),
        "final_claims": claims.content_hash,
        "relationships": content_hash(relationships.model_dump(mode="json")),
    }
    _equal(
        actual=report.input_content_hashes,
        expected=hashes,
        label="LP validation source hashes",
    )
    _equal(
        actual=summary.input_content_hashes,
        expected={
            **hashes,
            "eligibility_report": content_hash(selection.model_dump(mode="json")),
            "request_manifest": content_hash(
                claims.request_manifest.model_dump(mode="json")
            ),
        },
        label="Summary material hashes",
    )
    _equal(
        actual=summary.eligibility,
        expected=selection.model_dump(exclude={"sfis"}, mode="json"),
        label="Summary eligibility",
    )
    _equal(
        actual=summary.decision_counts,
        expected=claims.decision_counts,
        label="Summary decisions",
    )
    _equal(
        actual=summary.distributions,
        expected={
            "checker_outcome": dict(
                Counter(claim.provenance.checker_outcome for claim in claims.claims)
            ),
            "eligible_coordinate": dict(
                Counter(
                    row.coordinate.canonical_value
                    for row in selection.eligible_sfis
                    if row.coordinate.canonical_value is not None
                )
            ),
            "eligible_statement_type": dict(
                Counter(row.statement_type for row in selection.eligible_sfis)
            ),
        },
        label="Summary distributions",
    )
    _equal(
        actual=summary.warning_counts,
        expected=dict(
            Counter(
                warning
                for claim in claims.claims
                for warning in claim.judgment.warnings
            )
        ),
        label="Summary warning counts",
    )
    _equal(
        actual=summary.validation_report_content_hash,
        expected=report.content_hash,
        label="Summary validation report",
    )
    _equal(
        actual=summary.relationships_final_claims_content_hash,
        expected=claims.content_hash,
        label="Summary direct claims",
    )

    if not summary.validation_report_passed or counts["unresolved_failed_pairs"]:
        raise ValueError("Summary is not structurally complete")

    reader.check_hashes(summary.artifact_byte_hashes)
    reader.check_hashes(summary.input_artifact_byte_hashes)
    return LPStandaloneArtifacts(
        failures=tuple(failures),
        relationships=relationships,
        summary=summary,
        unresolved_items=unresolved,
        validation_report=report,
    )


def _snapshot_upstream(
    *, config: _CapturedKGConfig, reader: _SnapshotReader
) -> AcademicStandardsLCKGBundle:
    """Validate AS+LC graph structure, source preservation, provenance and wire
    projections.

    Parameters
    ----------
    config
        Captured curriculum policy and wire-grade mapping.
    reader
        Stable input reader.

    Returns
    -------
    AcademicStandardsLCKGBundle
        Reconciled upstream evidence, without a semantic truth interpretation.

    Raises
    ------
    ValueError
        Upstream bundles, provenance, reports or projections disagree.
    """

    academic = AcademicStandardsKGBundle.model_validate(
        reader.read("as_kg_bundle.json")
    )
    upstream = AcademicStandardsLCKGBundle.model_validate(
        reader.read("as_lc_kg_bundle.json")
    )

    for bundle, filename in (
        (academic, "as_validation_report.json"),
        (upstream, "as_lc_validation_report.json"),
    ):
        if not bundle.validation_report.passed or bundle.validation_report.errors:
            raise ValueError(f"Failed upstream validation: {filename}")

        _equal(
            actual=reader.read(filename),
            expected=(
                _upstream_report(academic=academic, upstream=upstream)
                if filename == "as_lc_validation_report.json"
                else bundle.validation_report.model_dump(mode="json")
            ),
            label=filename,
        )

    for field in ("framework", "items", "relationships_has_child"):
        _equal(
            actual=upstream.model_dump(mode="json")[field],
            expected=academic.model_dump(mode="json")[field],
            label=f"Upstream preservation: {field}",
        )

    errors = _validate_graph_export(
        relationships=academic.relationships_has_child,
        sf=academic.framework,
        sfis=academic.items,
    )
    errors.extend(
        _validate_merged_graph(
            academic_standards_bundle=academic,
            lc_generation_summary=LCGenerationSummary.model_validate(
                reader.read("lc_generation_summary.json")
            ),
            learning_components=upstream.learning_components,
            merged_entity_provenance=upstream.entity_provenance,
            supports_edges=upstream.relationships_supports,
        )
    )

    if errors:
        raise ValueError(f"Invalid upstream structure: {errors[0]}")

    _equal(
        actual=reader.read("as_entity_provenance.json"),
        expected=academic.entity_provenance,
        label="AS provenance",
    )
    _source_manifest_binding(
        actual=reader.read("kg_run_manifest.json"),
        original=academic.entity_provenance["kg_run_manifest"],
    )
    _equal(
        actual={
            key: value
            for key, value in upstream.entity_provenance.items()
            if key != "learning_components"
        },
        expected=academic.entity_provenance,
        label="Merged AS provenance",
    )
    _equal(
        actual=reader.read("lc_entity_provenance.json")["learning_components"],
        expected=upstream.entity_provenance["learning_components"],
        label="LC provenance",
    )
    nodes = _build_learning_commons_nodes(
        grade_level_mapping=config.academic_standards.grade_level_mapping,
        sf=academic.framework,
        sfis=academic.items,
    )
    edges = _build_learning_commons_relationships(
        nodes=nodes, relationships=academic.relationships_has_child
    )

    for prefix, node_rows, edge_rows in (
        ("as", nodes, edges),
        (
            "as_lc",
            [
                *nodes,
                *_build_learning_component_nodes(
                    learning_components=upstream.learning_components
                ),
            ],
            None,
        ),
    ):
        if edge_rows is None:
            edge_rows = [
                *edges,
                *_build_learning_commons_relationships(
                    nodes=node_rows, relationships=upstream.relationships_supports
                ),
            ]

        _equal(
            actual=reader.read(f"{prefix}_nodes.jsonl"),
            expected=[
                row.model_dump(by_alias=True, exclude_none=True, mode="json")
                for row in node_rows
            ],
            label=f"{prefix} wire nodes",
        )
        _equal(
            actual=reader.read(f"{prefix}_relationships.jsonl"),
            expected=[
                row.model_dump(by_alias=True, exclude_none=True, mode="json")
                for row in edge_rows
            ],
            label=f"{prefix} wire relationships",
        )

    _snapshot_upstream_bindings(
        academic=academic, config=config, reader=reader, upstream=upstream
    )
    return upstream


def _snapshot_upstream_bindings(
    *,
    academic: AcademicStandardsKGBundle,
    config: _CapturedKGConfig,
    reader: _SnapshotReader,
    upstream: AcademicStandardsLCKGBundle,
) -> None:
    """Check every upstream report fingerprint against preserved source artifacts.

    Parameters
    ----------
    academic
        Original Academic Standards bundle.
    config
        Captured configuration.
    reader
        Read-only source artifact collection.
    upstream
        Combined AS+LC bundle.

    Raises
    ------
    ValueError
        A recorded fingerprint or internal projection differs from its source.
    """

    as_material = {
        key: reader.read(key + ".json")
        for key in (
            "has_child_edges_final",
            "has_child_resolution_summary",
            "has_child_unresolved_edges",
            "sfi_final_records",
            "sfi_final_summary",
        )
    }
    as_material.update(
        {
            "grade_level_mapping": config.academic_standards.model_dump(mode="json")[
                "grade_level_mapping"
            ],
            "grade_level_statement_types": config.academic_standards.grade_level_statement_types,
            "is_current": config.metadata.is_current,
            "kg_run_manifest": academic.entity_provenance["kg_run_manifest"],
            "learning_commons_export_schema_version": academic.validation_report.learning_commons_export_schema_version,
            "learning_commons_nodes": reader.read("as_nodes.jsonl"),
            "learning_commons_relationships": reader.read("as_relationships.jsonl"),
        }
    )
    _equal(
        actual=academic.validation_report.input_fingerprints,
        expected={
            key: _fingerprint_jsonable(value) for key, value in as_material.items()
        },
        label="AS report source fingerprints",
    )
    lc_material = {
        key: reader.read(key + ".json")
        for key in (
            "lc_entity_provenance",
            "lc_generation_failures",
            "lc_generation_summary",
            "lc_supports_edges",
        )
    }
    lc_material.update(
        {
            "academic_standards_kg_bundle": academic.model_dump(mode="json"),
            "learning_components": reader.read("learning_components.jsonl"),
        }
    )
    _equal(
        actual=upstream.validation_report.input_fingerprints,
        expected={
            key: _fingerprint_jsonable(value) for key, value in lc_material.items()
        },
        label="AS+LC report source fingerprints",
    )
    _equal(
        actual=lc_material["lc_supports_edges"],
        expected=[
            edge.model_dump(mode="json") for edge in upstream.relationships_supports
        ],
        label="LC supports source",
    )
    _equal(
        actual=lc_material["learning_components"],
        expected=[
            node.model_dump(mode="json") for node in upstream.learning_components
        ],
        label="LC node source",
    )

    for name, expected in (
        ("as_standards_framework.json", academic.framework.model_dump(mode="json")),
        (
            "as_standards_framework_items.jsonl",
            [node.model_dump(mode="json") for node in academic.items],
        ),
        (
            "as_relationships_has_child.jsonl",
            [edge.model_dump(mode="json") for edge in academic.relationships_has_child],
        ),
        ("as_unresolved_items.json", academic.unresolved_items.model_dump(mode="json")),
    ):
        _equal(actual=reader.read(name), expected=expected, label=name)


def _source_manifest_binding(
    *, actual: dict[str, Any], original: dict[str, Any]
) -> None:
    """Permit relocation of absolute locations while requiring identical source material.

    Parameters
    ----------
    actual
        Current run manifest, potentially relocated with the result directory.
    original
        Manifest captured inside the original graph provenance.

    Raises
    ------
    ValueError
        Nonlocation material or the relative source layout changed.
    """

    locations = {"kg_run_dir", "document_ir_fp"}
    _equal(
        actual={key: value for key, value in actual.items() if key not in locations},
        expected={
            key: value for key, value in original.items() if key not in locations
        },
        label="Source manifest nonlocation material",
    )
    relative_paths = []

    for manifest in (actual, original):
        root = Path(manifest["kg_run_dir"])
        relative_paths.append(Path(manifest["document_ir_fp"]).relative_to(root.parent))

        if root.name != "kgs":
            raise ValueError("Captured run path does not identify a kgs directory")

    _equal(
        actual=relative_paths[0],
        expected=relative_paths[1],
        label="Relocated source layout",
    )


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return physical identity and mutation markers for an opened file.

    Parameters
    ----------
    value
        Filesystem stat result.

    Returns
    -------
    tuple[int, int, int, int, int]
        Device, inode, size and nanosecond modification/change timestamps.
    """

    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _timestamp(value: Any) -> datetime:
    """Require a timezone-aware execution timestamp.

    Parameters
    ----------
    value
        Recorded timestamp.

    Returns
    -------
    datetime
        Parsed timestamp preserving its supplied timezone.

    Raises
    ------
    ValueError
        Timestamp is not a string or lacks a timezone.
    """

    if not isinstance(value, str):
        raise ValueError("execution timestamps must be strings")

    result = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("execution timestamps must include a timezone")

    return result


def _upstream_report(
    *, academic: AcademicStandardsKGBundle, upstream: AcademicStandardsLCKGBundle
) -> dict[str, Any]:
    """Reconstruct the standalone merged report's additional delivery counts.

    Parameters
    ----------
    academic
        Source framework and fallback count.
    upstream
        Combined bundle and its internal report.

    Returns
    -------
    dict[str, Any]
        Original standalone report shape.
    """

    report = upstream.validation_report.model_dump(mode="json")
    counts = report["object_counts"]
    counts.update(
        {
            "learning_commons_framework_nodes": 1,
            "learning_commons_item_nodes": len(upstream.items),
            "learning_commons_nodes": 1
            + len(upstream.items)
            + len(upstream.learning_components),
            "learning_commons_relationships": len(upstream.relationships_has_child)
            + len(upstream.relationships_supports),
            "learning_commons_unresolved_fallback_relationships": academic.validation_report.object_counts[
                "learning_commons_unresolved_fallback_relationships"
            ],
        }
    )
    return report


def _walk_runs(
    *, evaluation_root: Path, results_root: Path
) -> tuple[list[Path], list[DiscoveryAlias], list[DiscoverySkip]]:
    """Traverse arbitrary parent layouts once per physical directory.

    Parameters
    ----------
    evaluation_root
        Resolved evaluator output subtree to exclude.
    results_root
        Resolved traversal root.

    Returns
    -------
    tuple[list[Path], list[DiscoveryAlias], list[DiscoverySkip]]
        Run boundaries, observed aliases and explicit traversal exclusions.

    Raises
    ------
    LPDiscoveryError
        Invalid or unavailable discovery evidence.
    ValueError
        Traversal root is not a directory or lies inside the evaluator output.
    """

    aliases: list[DiscoveryAlias] = []
    directories: list[Path] = []
    pending = [results_root]
    skipped: list[DiscoverySkip] = []
    visited: set[tuple[int, int]] = set()

    while pending:
        path = pending.pop()

        try:
            resolved = path.resolve(strict=True)

            exclusion = _path_exclusion(
                evaluation_root=evaluation_root,
                path=path,
                resolved=resolved,
                results_root=results_root,
            )

            if exclusion is not None:
                skipped.append(exclusion)
                continue

            info = resolved.stat()

            if not stat.S_ISDIR(info.st_mode):
                continue

            if path != resolved:
                aliases.append(
                    DiscoveryAlias(discovered_path=path, resolved_path=resolved)
                )

            identity = (info.st_dev, info.st_ino)

            if identity in visited:
                continue

            visited.add(identity)

            if _is_run_directory(
                evaluation_root=evaluation_root, path=path, resolved=resolved
            ):
                directories.append(resolved)
                continue

            with os.scandir(resolved) as entries:
                children = sorted(Path(entry.path) for entry in entries)

            pending.extend(reversed(children))
        except (OSError, RuntimeError, ValueError) as exc:
            raise LPDiscoveryError(
                message=f"Cannot safely discover runs at {path}: {exc}"
            ) from exc

    return sorted(directories), aliases, skipped


def discover_lp_runs(
    *, evaluation_root: Path, results_root: Path
) -> DiscoveryInventory:
    """Inventory recorded KG executions without consuming graph or judgment data.

    Completed candidates are not validated snapshots. The caller must validate their
    full material bindings, absence of active mutation and frozen evidence before
    preparing any evaluation request. Discovery never invokes a production pipeline,
    modifies a file, resolves a judge model or creates an evaluation directory.

    Parameters
    ----------
    evaluation_root
        Evaluator output subtree, supplied by the entry point from repository paths.
    results_root
        Starting directory; arbitrary parent layouts and a kgs root are accepted.

    Returns
    -------
    DiscoveryInventory
        Deterministically ordered preliminary candidates and excluded runs.

    Raises
    ------
    LPDiscoveryError
        Invalid root, ambiguous/unreadable metadata, conflicting candidate identities,
        changing metadata, aliased outputs or absence of completed candidates.
    ValueError
        Starting path is not a directory or lies inside the evaluator output.
    """

    try:
        resolved_root = results_root.resolve(strict=True)
        resolved_output = evaluation_root.resolve(strict=False)

        if not resolved_root.is_dir():
            raise ValueError("starting path is not a directory")

        if resolved_root.is_relative_to(resolved_output):
            raise ValueError("starting directory lies inside evaluator output")
    except (OSError, RuntimeError, ValueError) as exc:
        raise LPDiscoveryError(message=f"Invalid discovery root: {exc}") from exc

    directories, aliases, skipped = _walk_runs(
        evaluation_root=resolved_output, results_root=resolved_root
    )
    runs: list[DiscoveredRun] = []
    identities: dict[tuple[str, str], Path] = {}

    for directory in directories:
        run = _classify_run(kgs_directory=directory, results_root=resolved_root)
        run_aliases = {
            alias.discovered_path / directory.relative_to(alias.resolved_path)
            for alias in aliases
            if directory.is_relative_to(alias.resolved_path)
        }
        run = replace(run, aliases=tuple(sorted(run_aliases - {directory})))
        _register_run_identity(identities=identities, run=run)
        runs.append(run)

    # Recheck only previously read metadata; growing judgment files remain unopened.
    for run in runs:
        for fingerprint in run.fingerprints:
            _, observed = _read_metadata(
                path=fingerprint.path, results_root=resolved_root
            )

            if observed != fingerprint:
                raise LPDiscoveryError(
                    message=f"Run metadata changed during discovery: {fingerprint.path}"
                )

    inventory = DiscoveryInventory(
        aliases=tuple(aliases),
        evaluation_root=resolved_output,
        results_root=resolved_root,
        runs=tuple(runs),
        skipped_paths=tuple(skipped),
    )

    if not any(run.status == "completed_candidate" for run in runs):
        raise LPDiscoveryError(
            inventory=inventory,
            message="No completed curriculum candidates were discovered.",
        )

    return inventory


def validate_lp_snapshot(run: DiscoveredRun) -> ValidatedSnapshot:
    """Validate one completed run for evaluation without writing or invoking production.

    Original byte payloads, relative names, resolved paths and absences are returned
    for a separate freezing boundary. Passing this function establishes input
    consistency only; it does not constitute reviewer approval or a frozen snapshot.
    Producing Git provenance is retained if recorded and is never required.

    Parameters
    ----------
    run
        Completed candidate from recursive discovery.

    Returns
    -------
    ValidatedSnapshot
        Captured material and configuration suitable for subsequent snapshot freezing.

    Raises
    ------
    LPSnapshotError
        A selected run is active, incomplete, changed, unavailable or inconsistent.
    ValueError
        Run is not a completed candidate or lacks a document identity.
    """

    try:
        if run.status != "completed_candidate":
            raise ValueError("Only completed candidates may be validated")

        reader = _SnapshotReader(run.kgs_directory)

        for fingerprint in run.fingerprints:
            name = fingerprint.path.relative_to(run.kgs_directory).as_posix()
            reader.read(name)
            _equal(
                actual=reader.artifacts[name].fingerprint,
                expected=fingerprint,
                label=f"Discovery metadata {name}",
            )

        with _snapshot_ownership(reader):
            current = _classify_run(
                kgs_directory=run.kgs_directory, results_root=run.kgs_directory
            )
            _equal(
                actual=replace(current, aliases=run.aliases),
                expected=run,
                label="Selected completion state",
            )
            config = _snapshot_config(reader)
            source_name = _snapshot_source(config=config, reader=reader, run=run)
            upstream = _snapshot_upstream(config=config, reader=reader)

            if run.doc_key is None:
                raise ValueError("Missing document identity")

            population = _snapshot_population(
                config=config, doc_key=run.doc_key, reader=reader, upstream=upstream
            )

            for name in (
                *population.manifest.artifact_byte_hashes,
                "lp_generation_requests_manifest.json",
            ):
                reader.read(name)

            format_name, rows = _snapshot_execution(
                config=config, population=population, reader=reader
            )
            claims = LPFinalClaims.model_validate(reader.read("lp_final_claims.json"))
            _equal(
                actual=claims.request_manifest,
                expected=population.manifest,
                label="Final request manifest",
            )
            _equal(
                actual=claims.execution_material,
                expected=reader.read("lp_generation_checkpoint_manifest.json")[
                    "material"
                ],
                label="Final execution material",
            )
            _equal(
                actual=claims.checkpoint_receipt_byte_hash,
                expected=reader.artifacts[
                    "lp_generation_checkpoint_manifest.json"
                ].fingerprint.sha256,
                label="Final receipt binding",
            )
            _checkpoint_claims(
                claims=claims, population=population, reader=reader, rows=rows
            )
            relationships = _snapshot_relationships(
                claims=claims, config=config, reader=reader, upstream=upstream
            )
            artifacts = _snapshot_standalone(
                claims=claims,
                config=config,
                reader=reader,
                relationships=relationships,
                upstream=upstream,
            )
            _snapshot_graphs(
                artifacts=artifacts, config=config, reader=reader, upstream=upstream
            )
            reader.check_unchanged()

        return ValidatedSnapshot(
            absent_artifacts=tuple(sorted(reader.absent)),
            artifacts=tuple(
                reader.artifacts[name] for name in sorted(reader.artifacts)
            ),
            checkpoint_format=format_name,
            config_json=canonical_lp_json(config.model_dump(mode="json")),
            run=run,
            source_artifact=source_name,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        QualityError,
    ) as exc:
        raise LPSnapshotError(
            f"Invalid completed LP snapshot {run.kgs_directory}: {exc}"
        ) from exc


def validate_lp_snapshots(
    inventory: DiscoveryInventory,
) -> tuple[ValidatedSnapshot, ...]:
    """Validate every selected completed candidate, failing the invocation on any error.

    Parameters
    ----------
    inventory
        Recursive discovery inventory, including explicitly excluded unfinished runs.

    Returns
    -------
    tuple[ValidatedSnapshot, ...]
        Validated material in deterministic discovery order, pending freezing.

    Raises
    ------
    LPSnapshotError
        No completed candidate exists, identities conflict, or any selected input
        fails validation. Partial success is never returned.
    """

    selected = tuple(
        run for run in inventory.runs if run.status == "completed_candidate"
    )

    if not selected:
        raise LPSnapshotError("No completed LP snapshot was selected")

    identities: dict[tuple[str, str], Path] = {}

    try:
        for run in selected:
            _register_run_identity(identities=identities, run=run)
    except LPDiscoveryError as exc:
        raise LPSnapshotError(str(exc)) from exc

    return tuple(validate_lp_snapshot(run) for run in selected)
