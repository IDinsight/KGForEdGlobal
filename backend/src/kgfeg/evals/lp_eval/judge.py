"""Persist Learning Progressions evaluation schedules and validated attempt evidence."""

# Standard Library
import fcntl
import gzip
import hashlib
import json
import os
import sqlite3
import stat

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

# Third Party Library
from pydantic import TypeAdapter

# Package Library
from kgfeg.config import BackendSettings, Settings
from kgfeg.evals.lp_eval.prompts import _resolve_evidence_reference
from kgfeg.evals.lp_eval.sampling import (
    _decode_snapshot_json,
    _frozen_manifest,
    _frozen_output_boundary,
    _read_snapshot_bytes,
    _schedule_implementation,
    prepare_evaluation_schedule,
)
from kgfeg.evals.lp_eval.schemas import (
    AttemptEvent,
    ClassificationJudgment,
    CritiqueJudgment,
    DiscoveryInventory,
    EvaluationCache,
    EvaluationSchedule,
    EvaluationStore,
    EvaluationStoreManifest,
    JudgeUsage,
    ResolvedEvaluationSettings,
    ResolvedJudgeSettings,
    ScheduledRequest,
)
from kgfeg.kgs.lp_requests import canonical_lp_json, lp_material_content_hash

_CACHE_SCHEMA = {
    "events": "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, content_hash TEXT NOT NULL)",
    "metadata": "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
}
JUDGE_ATTEMPT_TIMEOUT_SECONDS = 180
JUDGE_CONCURRENCY = 4
JUDGE_MAX_RETRIES = 2
JUDGE_RETRY_WAITS_SECONDS = (5, 20)


class EvaluationSession:
    """Sole-writer access to one validated invocation's transactional attempt ledger.

    Instances are yielded by open_evaluation_store and become unusable on context exit.
    The schedule and every cached success are validated before this object is exposed.
    No method dispatches a model call.
    """

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        events: tuple[AttemptEvent, ...],
        schedule: EvaluationSchedule,
    ) -> None:
        """Initialize an already locked session and replay its validated evidence.

        Parameters
        ----------
        connection
            Open transactional database held under the invocation writer lock.
        events
            Verified ordered ledger events.
        schedule
            Exact persisted and regenerated schedule.
        """

        self._connection = connection
        self._events: list[AttemptEvent] = []
        self._judgments: dict[str, ClassificationJudgment | CritiqueJudgment] = {}
        self._latest: dict[str, AttemptEvent] = {}
        self._requests = {
            request.prompt.request_id: request
            for curriculum in schedule.curricula
            for request in curriculum.requests
        }
        self.schedule = schedule
        self._head = schedule.material_content_hash

        for event in events:
            judgment = self._validate_event(event)
            self._remember(event=event, judgment=judgment)
            self._head = _event_hash(event=event, previous=self._head)

    def _append(self, event: AttemptEvent) -> None:
        """Commit one validated event and its chain head atomically.

        Parameters
        ----------
        event
            Proposed start or terminal record.

        Raises
        ------
        ValueError
            If the event is inconsistent with the schedule or prior evidence.
        sqlite3.Error
            If the transaction cannot commit; memory is then unchanged.
        """

        event = AttemptEvent.model_validate_json(_event_json(event))
        judgment = self._validate_event(event)
        digest = _event_hash(event=event, previous=self._head)

        with self._connection:
            self._connection.execute(
                "INSERT INTO events VALUES (?, ?, ?)",
                (len(self._events) + 1, _event_json(event), digest),
            )
            self._connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'head'",
                (canonical_lp_json({"count": len(self._events) + 1, "hash": digest}),),
            )

        self._remember(event=event, judgment=judgment)
        self._head = digest

    def _remember(
        self,
        *,
        event: AttemptEvent,
        judgment: ClassificationJudgment | CritiqueJudgment | None,
    ) -> None:
        """Apply an already committed event to the in-memory resume view.

        Parameters
        ----------
        event
            Committed event.
        judgment
            Validated semantic response, if this event is successful.
        """

        self._events.append(event)
        self._latest[event.request_id] = event

        if judgment is not None:
            self._judgments[event.request_id] = judgment

    def _terminal_identity(self, attempt: AttemptEvent) -> None:
        """Require the exact still-open start handle before recording its result.

        Parameters
        ----------
        attempt
            Handle returned by start_attempt, or an unfinished handle from snapshot.

        Raises
        ------
        ValueError
            If the handle is stale, fabricated, already terminal or not a start.
        """

        if (
            attempt.event != "started"
            or self._latest.get(attempt.request_id) != attempt
        ):
            raise ValueError("Attempt handle does not match the current durable start.")

    def _validate_event(
        self, event: AttemptEvent
    ) -> ClassificationJudgment | CritiqueJudgment | None:
        """Validate replay and writes with the same scheduled state machine.

        Parameters
        ----------
        event
            Next append-only record.

        Returns
        -------
        ClassificationJudgment | CritiqueJudgment | None
            Validated response for a successful terminal event.

        Raises
        ------
        ValueError
            If identity, dependencies, retry order, timing or output is invalid.
        """

        request = self._requests.get(event.request_id)

        if (
            request is None
            or request.material_content_hash != event.request_content_hash
        ):
            raise ValueError("Attempt is not bound to an exact scheduled request.")

        previous = self._latest.get(event.request_id)

        if event.event == "started":
            _validate_attempt_start(
                event=event,
                judgments=self._judgments,
                latest=self._latest,
                previous=previous,
                request=request,
            )
            return None

        if (
            previous is None
            or previous.event != "started"
            or previous.attempt_number != event.attempt_number
        ):
            raise ValueError(
                "Terminal event requires exactly one matching durable start."
            )

        if event.timestamp < previous.timestamp:
            raise ValueError("Terminal timestamp precedes its attempt start.")

        if event.event == "succeeded":
            judgment = validate_judge_response(
                request=request,
                response_json=event.judgment_json or "",
            )
            original = validate_judge_response(
                request=request,
                response_json=event.raw_response or "",
            )

            if judgment != original:
                raise ValueError("Cached judgment differs from its original response.")

            return judgment

        return None

    def record_failure(
        self,
        *,
        attempt: AttemptEvent,
        category: Literal[
            "timeout",
            "rate_limit",
            "server_error",
            "invalid_output",
            "interrupted",
            "authentication",
            "configuration",
            "invalid_input",
            "transport",
        ],
        message: str,
        raw_response: str | None = None,
        usage: JudgeUsage,
    ) -> None:
        """Append failed or interrupted attempt evidence without a semantic judgment.

        Parameters
        ----------
        attempt
            Exact current start handle.
        category
            Explicit execution failure class; ambiguity is never a failure.
        message
            Sanitized failure explanation, without credentials.
        raw_response
            Available model output, including malformed output.
        usage
            Observed accounting; JudgeUsage() explicitly records unknown values.

        Raises
        ------
        ValueError
            If the handle, failure record or event ordering is invalid.
        sqlite3.Error
            If durable recording fails.
        """

        self._terminal_identity(attempt)
        self._append(
            AttemptEvent(
                attempt_number=attempt.attempt_number,
                event="failed",
                failure_category=category,
                failure_message=message,
                raw_response=raw_response,
                request_content_hash=attempt.request_content_hash,
                request_id=attempt.request_id,
                timestamp=datetime.now(UTC),
                usage=usage,
            )
        )

    def record_success(
        self,
        *,
        attempt: AttemptEvent,
        response_json: str,
        usage: JudgeUsage,
    ) -> ClassificationJudgment | CritiqueJudgment:
        """Validate and durably record one complete scheduled response.

        Parameters
        ----------
        attempt
            Exact current start handle.
        response_json
            Original structured response; duplicate keys and unknown fields fail.
        usage
            Accounting for this attempt, including explicitly unknown values.

        Returns
        -------
        ClassificationJudgment | CritiqueJudgment
            Valid response in its scheduled display orientation.

        Raises
        ------
        ValueError
            If the handle or response is invalid. The start remains unfinished until
            its caller records failure; invalid output never enters success cache.
        sqlite3.Error
            If durable recording fails.
        """

        self._terminal_identity(attempt)
        judgment = validate_judge_response(
            request=self._requests[attempt.request_id],
            response_json=response_json,
        )
        self._append(
            AttemptEvent(
                attempt_number=attempt.attempt_number,
                event="succeeded",
                judgment_json=canonical_lp_json(judgment.model_dump(mode="json")),
                raw_response=response_json,
                request_content_hash=attempt.request_content_hash,
                request_id=attempt.request_id,
                timestamp=datetime.now(UTC),
                usage=usage,
            )
        )
        return judgment

    def snapshot(self) -> EvaluationCache:
        """Return immutable successful, failed and unfinished execution evidence.

        Returns
        -------
        EvaluationCache
            Every event and successful replicate, plus unresolved durable starts.
        """

        return EvaluationCache(
            events=tuple(self._events),
            judgments=tuple(self._judgments[key] for key in sorted(self._judgments)),
            unfinished=tuple(
                self._latest[key]
                for key in sorted(self._latest)
                if self._latest[key].event == "started"
            ),
        )

    def start_attempt(self, request_id: str) -> AttemptEvent:
        """Persist a start before dispatch; never repeat a successful request.

        Parameters
        ----------
        request_id
            Opaque identifier from this session's exact schedule.

        Returns
        -------
        AttemptEvent
            Durable handle required to complete this attempt.

        Raises
        ------
        ValueError
            If the request is absent, blocked by dependencies, already successful,
            unfinished, permanently failed, out of retries or above concurrency.
        """

        request = self._requests.get(request_id)

        if request is None:
            raise ValueError("Unknown scheduled request.")

        previous = self._latest.get(request_id)
        event = AttemptEvent(
            attempt_number=1 if previous is None else previous.attempt_number + 1,
            event="started",
            request_content_hash=request.material_content_hash,
            request_id=request_id,
            timestamp=datetime.now(UTC),
        )
        self._append(event)
        return event


def _database_events(
    *, connection: sqlite3.Connection, schedule_hash: str
) -> tuple[AttemptEvent, ...]:
    """Read the complete ledger, rejecting schema, chain and head inconsistencies.

    Parameters
    ----------
    connection
        Locked existing database.
    schedule_hash
        Expected exact invocation schedule identity.

    Returns
    -------
    tuple[AttemptEvent, ...]
        Canonical ordered events pending request-relative validation.

    Raises
    ------
    ValueError
        If storage is corrupt, altered, truncated or bound to another schedule.
    """

    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("Evaluation cache database integrity check failed.")

    objects = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    if objects != sorted(_CACHE_SCHEMA.items()):
        raise ValueError("Evaluation cache database schema differs.")

    metadata = dict(connection.execute("SELECT key, value FROM metadata").fetchall())

    if set(metadata) != {"head", "schedule"} or metadata["schedule"] != schedule_hash:
        raise ValueError("Evaluation cache schedule binding differs.")

    events = []
    digest = schedule_hash

    for expected, row in enumerate(
        connection.execute(
            "SELECT sequence, payload, content_hash FROM events ORDER BY sequence"
        ),
        start=1,
    ):
        sequence, payload, recorded_hash = row
        event = AttemptEvent.model_validate_json(payload)
        digest = _event_hash(event=event, previous=digest)

        if (
            sequence != expected
            or _event_json(event) != payload
            or digest != recorded_hash
        ):
            raise ValueError(
                "Evaluation cache event is truncated, altered or out of order."
            )

        events.append(event)

    if metadata["head"] != canonical_lp_json({"count": len(events), "hash": digest}):
        raise ValueError("Evaluation cache head does not cover its complete ledger.")

    return tuple(events)


def _event_hash(*, event: AttemptEvent, previous: str) -> str:
    """Hash one event together with its preceding committed chain identity.

    Parameters
    ----------
    event
        Valid structured event.
    previous
        Prior event hash, or the schedule hash for the first event.

    Returns
    -------
    str
        Material digest of the linked record.
    """

    return lp_material_content_hash(
        {
            "event": event.model_dump(mode="json"),
            "previous": previous,
        }
    )


def _event_json(event: AttemptEvent) -> str:
    """Serialize an event canonically for exact round-trip validation.

    Parameters
    ----------
    event
        Structured event to serialize.

    Returns
    -------
    str
        Canonical JSON, preserving unknown accounting values.
    """

    return canonical_lp_json(event.model_dump(mode="json"))


@contextmanager
def _exclusive_store_lock(path: Path) -> Iterator[None]:
    """Acquire a nonblocking exclusive writer lock on a safe evaluator-owned file.

    Parameters
    ----------
    path
        Existing or new lock path under an unaliased output directory.

    Yields
    ------
    None
        Exclusive access until context exit.

    Raises
    ------
    ValueError
        If another process owns the invocation or the lock path is unsafe.
    """

    _store_path(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)

    try:
        status = os.fstat(descriptor)

        if status.st_nlink != 1 or not stat.S_ISREG(status.st_mode):
            raise ValueError("Evaluation lock must be an unshared regular file.")

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another writer owns this evaluation store.") from exc

        yield
    finally:
        os.close(descriptor)


def _load_store_manifest(reference: EvaluationStore) -> EvaluationStoreManifest:
    """Read and validate an exact pinned invocation manifest and path.

    Parameters
    ----------
    reference
        Expected manifest location and byte hash.

    Returns
    -------
    EvaluationStoreManifest
        Canonical storage metadata with a validated input-output boundary.

    Raises
    ------
    ValueError
        If bytes, canonical structure or output location differ.
    """

    raw = _store_read(reference.manifest_path)

    if hashlib.sha256(raw).hexdigest() != reference.content_hash:
        raise ValueError("Evaluation store manifest content differs.")

    material = _decode_snapshot_json(raw)
    manifest = TypeAdapter(EvaluationStoreManifest).validate_json(raw)

    if (
        material
        != TypeAdapter(EvaluationStoreManifest).dump_python(manifest, mode="json")
        or raw != canonical_lp_json(material).encode()
    ):
        raise ValueError("Evaluation store manifest is not exact canonical material.")

    inventory = _frozen_manifest(manifest.inputs).inventory
    directory = (
        inventory.evaluation_root / "invocations" / manifest.schedule_content_hash
    )

    if reference.manifest_path != directory / "manifest.json":
        raise ValueError("Evaluation store is outside its frozen output directory.")

    _frozen_output_boundary(inventory=inventory, output=inventory.evaluation_root)
    return manifest


def _publish_store(
    *, directory: Path, inventory: DiscoveryInventory, schedule: EvaluationSchedule
) -> EvaluationStore:
    """Stage immutable schedule and empty transactional ledger, then publish once.

    Parameters
    ----------
    directory
        Absent final content-addressed directory under a held publish lock.
    inventory
        Original frozen discovery inventory.
    schedule
        Validated complete plan.

    Returns
    -------
    EvaluationStore
        Pinned published manifest reference.
    """

    staging = directory.parent / f".pending-{uuid4().hex}"
    staging.mkdir(mode=0o700)
    compressed = gzip.compress(_schedule_bytes(schedule), mtime=0)
    _store_write(path=staging / "schedule.json.gz", payload=compressed)
    manifest = EvaluationStoreManifest(
        inputs=schedule.inputs,
        kind="lp_evaluation_store_v1",
        schedule_content_hash=schedule.material_content_hash,
        schedule_sha256=hashlib.sha256(compressed).hexdigest(),
        selector_hash=_store_selector(
            judge=schedule.judge,
            results_root=inventory.results_root,
            settings=schedule.settings,
        ),
    )
    payload = canonical_lp_json(
        TypeAdapter(EvaluationStoreManifest).dump_python(manifest, mode="json")
    ).encode()
    connection = sqlite3.connect(staging / "attempts.sqlite3")

    try:
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = DELETE")

        with connection:
            for statement in _CACHE_SCHEMA.values():
                connection.execute(statement)

            connection.executemany(
                "INSERT INTO metadata VALUES (?, ?)",
                [
                    (
                        "head",
                        canonical_lp_json(
                            {"count": 0, "hash": schedule.material_content_hash}
                        ),
                    ),
                    ("schedule", schedule.material_content_hash),
                ],
            )
    finally:
        connection.close()

    _store_write(path=staging / "writer.lock", payload=b"")
    _store_write(path=staging / "manifest.json", payload=payload)
    _store_directory_sync(staging)
    os.rename(staging, directory)
    _store_directory_sync(directory.parent)
    return EvaluationStore(
        content_hash=hashlib.sha256(payload).hexdigest(),
        manifest_path=directory / "manifest.json",
    )


def _schedule_bytes(schedule: EvaluationSchedule) -> bytes:
    """Serialize a complete plan without dropping any evidence or request fields.

    Parameters
    ----------
    schedule
        Materialized schedule.

    Returns
    -------
    bytes
        Canonical UTF-8 JSON.
    """

    return canonical_lp_json(
        TypeAdapter(EvaluationSchedule).dump_python(schedule, mode="json")
    ).encode()


def _store_directory_sync(directory: Path) -> None:
    """Flush directory entries after publishing immutable invocation material.

    Parameters
    ----------
    directory
        Evaluator-owned physical directory.
    """

    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _store_path(path: Path) -> None:
    """Reject path aliases and shared/nonregular files before evaluator I/O.

    Parameters
    ----------
    path
        Absolute file or directory path.

    Raises
    ------
    ValueError
        If a symlink, hard link, special file or relative path is encountered.
    """

    if not path.is_absolute() or path.resolve() != path:
        raise ValueError(
            f"Evaluation storage path must be absolute and unaliased: {path}"
        )

    if path.exists():
        status = path.stat()

        if not stat.S_ISDIR(status.st_mode) and (
            not stat.S_ISREG(status.st_mode) or status.st_nlink != 1
        ):
            raise ValueError(
                f"Evaluation storage file must be regular and unshared: {path}"
            )


def _store_read(path: Path) -> bytes:
    """Read stable bytes from one unaliased regular storage file.

    Parameters
    ----------
    path
        Existing evaluator artifact.

    Returns
    -------
    bytes
        Complete stable file contents.
    """

    _store_path(path)
    raw, _ = _read_snapshot_bytes(boundary=path.parent, path=path)
    _store_path(path)
    return raw


def _store_selector(
    *,
    judge: ResolvedJudgeSettings,
    results_root: Path,
    settings: ResolvedEvaluationSettings,
) -> str:
    """Identify the explicit starting directory and complete effective settings.

    Parameters
    ----------
    judge
        Effective non-secret model configuration.
    results_root
        Physical starting directory; its contents are not scanned here.
    settings
        Effective sampling and repetition controls.

    Returns
    -------
    str
        Lookup identity independent of later directory contents.
    """

    return lp_material_content_hash(
        {
            "judge": TypeAdapter(ResolvedJudgeSettings).dump_python(judge, mode="json"),
            "results_root": str(results_root.resolve(strict=True)),
            "settings": settings.settings.model_dump(mode="json"),
        }
    )


def _store_write(*, path: Path, payload: bytes) -> None:
    """Create and flush a new evaluator file without replacing existing evidence.

    Parameters
    ----------
    path
        New unaliased file path.
    payload
        Complete artifact bytes.
    """

    _store_path(path)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )

    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _validate_attempt_start(
    *,
    event: AttemptEvent,
    judgments: dict[str, ClassificationJudgment | CritiqueJudgment],
    latest: dict[str, AttemptEvent],
    previous: AttemptEvent | None,
    request: ScheduledRequest,
) -> None:
    """Enforce dependency completion and bounded per-request admission.

    Parameters
    ----------
    event
        Proposed durable start.
    judgments
        Previously validated successful responses.
    latest
        Last event for every attempted request.
    previous
        Prior event for this same request, if any.
    request
        Exact scheduled call.

    Raises
    ------
    ValueError
        If dependencies, concurrency, retry eligibility, sequence or time differ.
    """

    if not set(request.dependencies) <= judgments.keys():
        raise ValueError("Associated blind judgments must be frozen before critique.")

    if sum(item.event == "started" for item in latest.values()) >= JUDGE_CONCURRENCY:
        raise ValueError("Evaluation attempt concurrency is already occupied.")

    if previous is None:
        if event.attempt_number != 1:
            raise ValueError("First attempt number must be one.")

        return

    if previous.event != "failed" or previous.failure_category not in {
        "timeout",
        "rate_limit",
        "server_error",
        "invalid_output",
    }:
        raise ValueError(
            "A successful, unfinished or permanent failure cannot be retried."
        )

    if (
        event.attempt_number != previous.attempt_number + 1
        or event.timestamp < previous.timestamp
    ):
        raise ValueError("Retry sequence or timestamp differs from prior evidence.")


def _validate_schedule(schedule: EvaluationSchedule) -> None:
    """Require full deterministic reproduction using identical frozen material.

    Parameters
    ----------
    schedule
        Persisted or newly materialized plan.

    Raises
    ------
    ValueError
        If implementation, inputs or any schedule field differ.
    """

    if schedule.implementation_fingerprints != _schedule_implementation():
        raise ValueError("Evaluation implementation differs from the frozen schedule.")

    material = TypeAdapter(EvaluationSchedule).dump_python(schedule, mode="json")
    del material["material_content_hash"]

    if lp_material_content_hash(material) != schedule.material_content_hash:
        raise ValueError("Evaluation schedule material hash differs.")

    expected = prepare_evaluation_schedule(
        inputs=schedule.inputs, judge=schedule.judge, settings=schedule.settings
    )

    if expected.material_content_hash != schedule.material_content_hash:
        raise ValueError("Evaluation schedule cannot be reproduced from frozen inputs.")


def canonicalize_classification(
    *, judgment: ClassificationJudgment, request: ScheduledRequest
) -> ClassificationJudgment:
    """Remap a displayed classification to its canonical endpoint orientation.

    This does not establish response validity or semantic correctness; the execution
    validator must also check scheduled permissions and evidence-reference containment.

    Parameters
    ----------
    judgment
        Schema-valid response in exactly the scheduled displayed orientation.
    request
        Corresponding scheduled classification request.

    Returns
    -------
    ClassificationJudgment
        Same decision, explanation and citations with canonical endpoints/direction.

    Raises
    ------
    ValueError
        If task, request, pair or displayed endpoint identity does not match.
    """

    displayed = request.evidence.endpoint_uuids
    canonical = request.canonical_endpoint_uuids

    if (
        request.prompt.task != "classification"
        or judgment.request_id != request.prompt.request_id
        or judgment.pair_id != request.evidence.pair_id
        or (judgment.first_sfi_uuid, judgment.second_sfi_uuid) != displayed
        or set(displayed) != set(canonical)
    ):
        raise ValueError("Classification does not match its scheduled presentation.")

    material = judgment.model_dump()
    material["first_sfi_uuid"], material["second_sfi_uuid"] = canonical

    if displayed != canonical and judgment.direction is not None:
        material["direction"] = {
            "first_to_second": "second_to_first",
            "second_to_first": "first_to_second",
        }[judgment.direction]

    return ClassificationJudgment.model_validate(material)


def find_evaluation_store(
    *,
    judge: ResolvedJudgeSettings,
    repository_root: Path,
    results_root: Path,
    settings: ResolvedEvaluationSettings,
) -> EvaluationStore | None:
    """Locate frozen invocation metadata without rediscovering curriculum runs.

    Parameters
    ----------
    judge
        Current effective dedicated judge settings.
    repository_root
        Repository root, used only to locate results/lp_evals.
    results_root
        Requested starting directory.
    settings
        Current effective sampling and repetition settings.

    Returns
    -------
    EvaluationStore | None
        Sole matching frozen invocation, or None when none exists.

    Raises
    ------
    ValueError
        If published metadata is corrupt or multiple exact selectors match. The caller
        must select an explicit manifest in the latter case.
    """

    root = repository_root.resolve(strict=True) / "results" / "lp_evals" / "invocations"
    _store_path(root)

    if not root.exists():
        return None

    selector = _store_selector(
        judge=judge, results_root=results_root, settings=settings
    )
    matches = []

    for directory in sorted(root.iterdir()):
        if directory.name.startswith("."):
            continue

        _store_path(directory)
        raw = _store_read(directory / "manifest.json")
        reference = EvaluationStore(
            content_hash=hashlib.sha256(raw).hexdigest(),
            manifest_path=directory / "manifest.json",
        )
        manifest = _load_store_manifest(reference)

        if manifest.selector_hash == selector:
            matches.append(reference)

    if len(matches) > 1:
        raise ValueError("Multiple frozen invocations match; select an exact manifest.")

    return matches[0] if matches else None


def load_evaluation_schedule(reference: EvaluationStore) -> EvaluationSchedule:
    """Validate a persisted schedule against current code and its frozen inputs.

    Reconstructing the schedule checks coverage, not selection authority: it uses only
    the original frozen input manifest and never scans for new curricula.

    Parameters
    ----------
    reference
        Exact invocation manifest reference.

    Returns
    -------
    EvaluationSchedule
        Persisted complete schedule, ready for cache validation.

    Raises
    ------
    ValueError
        If serialized data, implementation, inputs or complete schedule differ.
    """

    manifest = _load_store_manifest(reference)
    compressed = _store_read(reference.manifest_path.parent / "schedule.json.gz")

    if hashlib.sha256(compressed).hexdigest() != manifest.schedule_sha256:
        raise ValueError("Persisted compressed schedule bytes differ.")

    raw = gzip.decompress(compressed)
    schedule = TypeAdapter(EvaluationSchedule).validate_json(raw)

    if (
        _schedule_bytes(schedule) != raw
        or schedule.inputs != manifest.inputs
        or schedule.material_content_hash != manifest.schedule_content_hash
    ):
        raise ValueError(
            "Persisted schedule has extra, missing or noncanonical material."
        )

    inventory = _frozen_manifest(schedule.inputs).inventory
    selector = _store_selector(
        judge=schedule.judge,
        results_root=inventory.results_root,
        settings=schedule.settings,
    )

    if manifest.selector_hash != selector:
        raise ValueError("Invocation lookup identity differs from its frozen settings.")

    _validate_schedule(schedule)
    return schedule


@contextmanager
def open_evaluation_store(reference: EvaluationStore) -> Iterator[EvaluationSession]:
    """Lock and fully validate an invocation before exposing its cache.

    Parameters
    ----------
    reference
        Explicit frozen invocation to resume; never an arbitrary latest run.

    Yields
    ------
    EvaluationSession
        Sole writer with validated successful judgments and durable attempt history.

    Raises
    ------
    ValueError
        If the store is busy, unsafe, stale, corrupt or inconsistent.
    """

    _load_store_manifest(reference)
    directory = reference.manifest_path.parent

    with _exclusive_store_lock(directory / "writer.lock"):
        schedule = load_evaluation_schedule(reference)
        database = directory / "attempts.sqlite3"

        for path in (database, directory / "attempts.sqlite3-journal"):
            _store_path(path)

        if any(
            (directory / name).exists()
            for name in (
                "attempts.sqlite3-wal",
                "attempts.sqlite3-shm",
            )
        ):
            raise ValueError("Unexpected evaluation cache journal format.")

        connection = sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)

        try:
            connection.execute("PRAGMA synchronous = FULL")

            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise ValueError(
                    "Evaluation cache requires rollback-journal transactions."
                )

            events = _database_events(
                connection=connection,
                schedule_hash=schedule.material_content_hash,
            )
            yield EvaluationSession(
                connection=connection, events=events, schedule=schedule
            )
        finally:
            connection.close()


def persist_evaluation_schedule(
    *, repository_root: Path, schedule: EvaluationSchedule
) -> EvaluationStore:
    """Atomically publish complete invocation material before any judge attempt.

    Existing invocations are validated and returned unchanged. Interrupted staging
    directories remain hidden and are never interpreted as a published invocation.

    Parameters
    ----------
    repository_root
        Physical project root; outputs remain beneath results/lp_evals.
    schedule
        Complete materialized schedule from frozen inputs.

    Returns
    -------
    EvaluationStore
        Exact published manifest and its content hash.

    Raises
    ------
    ValueError
        If schedule/input validation, boundaries or an existing invocation differ.
    """

    _validate_schedule(schedule)
    inventory = _frozen_manifest(schedule.inputs).inventory
    output = repository_root.resolve(strict=True) / "results" / "lp_evals"

    if output != inventory.evaluation_root:
        raise ValueError(
            "Frozen selection belongs to a different evaluation output root."
        )

    _frozen_output_boundary(inventory=inventory, output=output)
    root = output / "invocations"
    _store_path(root)
    root.mkdir(parents=True, exist_ok=True)
    _store_directory_sync(output)
    directory = root / schedule.material_content_hash

    with _exclusive_store_lock(root / ".publish.lock"):
        _store_path(directory)

        if directory.exists():
            raw = _store_read(directory / "manifest.json")
            reference = EvaluationStore(
                content_hash=hashlib.sha256(raw).hexdigest(),
                manifest_path=directory / "manifest.json",
            )
            load_evaluation_schedule(reference)
            return reference

        return _publish_store(
            directory=directory, inventory=inventory, schedule=schedule
        )


def resolve_judge_settings(
    settings: BackendSettings | None = None,
) -> ResolvedJudgeSettings:
    """Capture the dedicated model and existing shared registry settings.

    This performs local configuration resolution only. It does not instantiate provider
    clients or verify remote model availability. Model availability is checked during
    execution preflight.

    Parameters
    ----------
    settings
        Injectable backend settings. Omission uses the environment-loaded Settings.
        Only the LP evaluator model is selected; production and LC bindings are
        independent.

    Returns
    -------
    ResolvedJudgeSettings
        Immutable canonical non-secret configuration and operational settings. The
        retry count covers all attempts, including output repair; SDK retry
        multiplication is forbidden.

    Raises
    ------
    ValueError
        If the evaluator model is unset, malformed, unsupported by the shared provider
        registry, or its shared settings cannot be serialized.
    """

    backend_settings = Settings if settings is None else settings
    model_config = backend_settings.llm_config("lp_eval_judge")
    provider, separator, model_name = model_config.model.partition(":")

    if (
        not separator
        or not provider
        or not model_name
        or ":" in model_name
        or any(character.isspace() for character in model_config.model)
    ):
        raise ValueError("LP evaluator model must use provider:model-name syntax.")

    try:
        model_settings = model_config.kgs_settings("learning_progressions")
    except KeyError as exc:
        raise ValueError(
            "LP evaluator provider is unsupported by the shared model registry."
        ) from exc

    execution = {
        "attempt_timeout_seconds": JUDGE_ATTEMPT_TIMEOUT_SECONDS,
        "concurrency": JUDGE_CONCURRENCY,
        "max_retries": JUDGE_MAX_RETRIES,
        "retry_waits_seconds": JUDGE_RETRY_WAITS_SECONDS,
        "sdk_max_retries": 0,
    }
    return ResolvedJudgeSettings(
        execution_json=json.dumps(
            execution, allow_nan=False, separators=(",", ":"), sort_keys=True
        ),
        model=model_config.model,
        model_config_json=json.dumps(
            model_config.model_dump(mode="json"),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        model_settings_json=json.dumps(
            dict(model_settings), allow_nan=False, separators=(",", ":"), sort_keys=True
        ),
        provider=provider,
    )


def validate_judge_response(
    *, request: ScheduledRequest, response_json: str
) -> ClassificationJudgment | CritiqueJudgment:
    """Validate exact pair/task identity, permission and shown-evidence citations.

    Parameters
    ----------
    request
        Exact scheduled call, including rendered prompt and response schema.
    response_json
        One complete structured response, without duplicate or nonfinite JSON values.

    Returns
    -------
    ClassificationJudgment | CritiqueJudgment
        Schema-valid scheduled response, with semantic ambiguity preserved.

    Raises
    ------
    ValueError
        If output syntax, fields, identity, permission or citations are invalid.
    """

    _decode_snapshot_json(response_json.encode())
    model = (
        ClassificationJudgment
        if request.prompt.task == "classification"
        else CritiqueJudgment
    )
    judgment = model.model_validate_json(response_json)

    if (
        judgment.request_id != request.prompt.request_id
        or judgment.pair_id != request.evidence.pair_id
        or (judgment.first_sfi_uuid, judgment.second_sfi_uuid)
        != request.evidence.endpoint_uuids
    ):
        raise ValueError(
            "Judge response identity differs from its scheduled presentation."
        )

    shown = json.loads(request.prompt.user_message)
    references: tuple[str, ...]

    if isinstance(judgment, ClassificationJudgment):
        if {
            "decision": judgment.decision,
            "direction": judgment.direction,
        } not in shown["assessment_permissions"]:
            raise ValueError(
                "Judge response violates the displayed assessment permissions."
            )

        references = judgment.evidence_references
    else:
        references = tuple(
            ref for claim in judgment.claims for ref in claim.evidence_references
        )

    if not set(references) <= set(request.prompt.references):
        raise ValueError("Judge cited evidence outside its permitted shown references.")

    for reference in references:
        _resolve_evidence_reference(payload=shown["evidence"], reference=reference)

    return judgment
