"""Validated LP execution prefixes, material receipts, and separate failure evidence.

Each stage is durably journaled before its files are replaced. Interrupted commits
resume only from exact recorded old/new bytes and a fully validated next state. The
caller holds the exclusive generation lock for the lifetime of this store.
"""

# Future Library
from __future__ import annotations

# Standard Library
import copy
import hashlib
import json
import os
import re
import tempfile

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeVar, overload

# Third Party Library
from loguru import logger
from pydantic import Field, JsonValue, TypeAdapter
from pydantic_core import to_json

# Package Library
from kgfeg.kgs.lp_candidates import LPCandidatePopulation
from kgfeg.kgs.lp_requests import (
    MANIFEST_FILENAME,
    LPGenerationRequest,
    LPRequestManifest,
    LPRequestPopulation,
    file_stat_identity,
    read_lp_request_population,
)
from kgfeg.kgs.prompts import (
    build_lp_generation_prompt,
    validate_lp_generation_response,
)
from kgfeg.kgs.schemas import (
    LPCandidatePair,
    LPCandidateSummary,
    LPGenerationResponse,
    LPGenerationValidationVerdict,
)
from kgfeg.kgs.validators import (
    verify_lp_generation_response_integrity,
    verify_lp_generation_validation_integrity,
)
from kgfeg.schemas import BaseSchema

_JSON_RECORD = TypeAdapter(dict[str, JsonValue])
_FAILURES = "lp_generation_failures.json"
_FILENAMES = {
    "draft": "lp_generation_draft_responses.jsonl",
    "verdict": "lp_generation_validation_verdicts.jsonl",
    "response": "lp_generation_responses.jsonl",
}
_PENDING = "lp_generation_pending_completions.json"
_USAGE = "lp_generation_usage.json"
_RECEIPT = "lp_generation_checkpoint_manifest.json"
_TRANSACTION = "lp_generation_checkpoint_transaction.json"
_CHECKPOINT_NAMES = {*_FILENAMES.values(), _FAILURES, _PENDING, _USAGE, _RECEIPT}
_Record = TypeVar("_Record", bound=BaseSchema)


class _LPAttempt(BaseSchema):
    """One durably scheduled call, its observed outcome, and available usage."""

    attempt: int = Field(ge=1, strict=True)
    checkpoint_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_type: str | None = Field(default=None, min_length=1)
    request_id: str
    request_index: int = Field(ge=0, strict=True)
    run_number: int = Field(ge=1, strict=True)
    stage: Literal["draft", "verdict"]
    status: Literal["dispatched", "succeeded", "failed", "unknown", "cancelled"]
    usage: dict[str, int] | None = None


class _LPCheckpoint(BaseSchema):
    """A complete validated stage result and its exact material dependencies."""

    execution_content_hash: str
    payload: dict[str, Any]
    payload_content_hash: str
    prerequisite_content_hash: str
    prompt_content_hash: str
    request_index: int = Field(ge=0, strict=True)
    stage: Literal["draft", "verdict", "response"]


class _LPCheckpointTransaction(BaseSchema):
    """Complete intended checkpoint state and exact predecessor artifact hashes."""

    next_payloads: dict[str, str]
    previous_byte_hashes: dict[str, str | None]


class _LPFailure(BaseSchema):
    """One unsuccessful processing attempt retained after recovery or exhaustion."""

    attempt: int = Field(ge=1, strict=True)
    error_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    error_type: str = Field(min_length=1)
    exhausted: bool = Field(strict=True)
    pair_ids: list[str] = Field(min_length=1)
    request_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str
    request_index: int = Field(ge=0, strict=True)
    resolved_response_content_hash: str | None = None
    resolved_run_number: int | None = Field(default=None, ge=1, strict=True)
    run_number: int = Field(ge=1, strict=True)
    stage: Literal["draft", "verdict"]


class _PendingRecords(Mapping[int, _Record]):
    """Read-only live pending completions; every record read is a copy."""

    def __init__(self, records: dict[int, _Record]) -> None:
        """Wrap the store's private pending records without copying them.

        Parameters
        ----------
        records
            Pending records keyed by request index.
        """

        self._records = records

    def __getitem__(self, key: int) -> _Record:
        """Return a deep copy of one pending record.

        Parameters
        ----------
        key
            Request index.

        Returns
        -------
        _Record
            Independent copy; mutating it cannot reach the store.
        """

        return self._records[key].model_copy(deep=True)

    def __iter__(self) -> Iterator[int]:
        """Iterate over a snapshot of the pending request indices.

        Returns
        -------
        Iterator[int]
            Request indices in insertion order.
        """

        return iter(list(self._records))

    def __len__(self) -> int:
        """Return the number of pending records.

        Returns
        -------
        int
            Pending record count.
        """

        return len(self._records)


class _RecordSequence(Sequence[_Record]):
    """Read-only live record sequence; every record read is a copy."""

    def __init__(self, records: list[_Record]) -> None:
        """Wrap one of the store's private record lists without copying it.

        Parameters
        ----------
        records
            Stage prefix, attempt history or failure history.
        """

        self._records = records

    @overload
    def __getitem__(self, index: int) -> _Record:
        """Type one position read as a single record copy."""

    @overload
    def __getitem__(self, index: slice) -> tuple[_Record, ...]:
        """Type one slice read as a tuple of record copies."""

    def __getitem__(self, index: int | slice) -> _Record | tuple[_Record, ...]:
        """Return deep copies of the selected records.

        Parameters
        ----------
        index
            Position or slice.

        Returns
        -------
        _Record or tuple[_Record, ...]
            Independent copies; mutating them cannot reach the store.
        """

        if isinstance(index, slice):
            return tuple(
                record.model_copy(deep=True) for record in self._records[index]
            )

        return self._records[index].model_copy(deep=True)

    def __len__(self) -> int:
        """Return the number of records.

        Returns
        -------
        int
            Record count.
        """

        return len(self._records)


class LPGenerationCheckpoints:
    """Persist separate success prefixes and auditable processing attempts.

    The store verifies exact artifact bytes, request alignment, stage prerequisites,
    complete corrections, and failure dispositions before allowing any reuse.
    Completion denotes processing coverage only, never graph or semantic validity.

    Records (stage rows, pending rows, attempts and failures) are private. Each is
    validated when the store creates or changes it, and its canonical bytes become
    the authority the store publishes. Callers read copies through read-only views,
    so no caller or worker can change persisted bytes or store decisions.
    """

    def __init__(
        self,
        *,
        execution_check: Callable[..., None] | None = None,
        material: dict[str, Any],
        ownership_check: Callable[[], None] | None = None,
        population: LPRequestPopulation,
        read_only: bool = False,
        root: Path,
    ) -> None:
        """Load current execution evidence or initialize an empty checkpoint set.

        Parameters
        ----------
        execution_check
            Optional immutable-input assertion around writer publication, called with
            `full` (complete proof) and `request_indices` (requests to re-hash when
            only the cheap guard runs).
        material
            Actual upstream, configuration, prompt-definition, and model identities.
            The store keeps its own copy.
        ownership_check
            Optional live directory-ownership assertion for the generation writer.
        population
            Fully reconciled on-disk candidate and request population.
        read_only
            Validate existing evidence without initializing or recovering files.
        root
            Locked generation directory.

        Raises
        ------
        ValueError
            If the LP checkpoint evidence is missing in read only mode.
        """

        # Proofs are private to this invocation and retain only immutable material.
        self._attempt_positions: dict[int, list[int]] = {}
        self._attempts: list[_LPAttempt] = []
        self._changes = 0
        self._committed_status: str | None = None
        self._failed_update = False
        self._failures: list[_LPFailure] = []
        self._file_identities: dict[str, tuple[int, int, int, int, int]] = {}
        self._material = copy.deepcopy(material)
        self._pending: dict[str, dict[int, _LPCheckpoint]] = {
            stage: {} for stage in _FILENAMES
        }
        self._record_encodings: dict[tuple[str, int], bytes] = {}
        self._request_state_encodings = [b""] * len(population.requests)
        self._row_proofs: dict[tuple[str, int], tuple[bytes, ...]] = {}
        self._rows: dict[str, list[_LPCheckpoint]] = {stage: [] for stage in _FILENAMES}
        self._validated_byte_hashes: tuple[tuple[str, bytes], ...] = ()
        self.execution_check = execution_check
        self.execution_hash = content_hash(self._material)
        self._material_digest = hashlib.sha256(
            _canonical_bytes(
                {"execution_hash": self.execution_hash, "material": self._material}
            )
        ).digest()
        self.ownership_check = ownership_check
        self.population = population
        self.root = root
        self.run_number = 0
        paths = [
            root / name
            for name in (
                *_FILENAMES.values(),
                _FAILURES,
                _PENDING,
                _USAGE,
                _RECEIPT,
                _TRANSACTION,
            )
        ]

        if any(path.exists() or path.is_symlink() for path in paths):
            self._load(read_only=read_only)
        elif read_only:
            raise ValueError("LP checkpoint evidence is missing.")
        else:
            self._refresh_request_states(range(len(population.requests)))
            self._save()

    def _check_checkpoint_files(self, *, full: bool) -> None:
        """Require every checkpoint file to hold the last validated bytes.

        A guard compares each file's stat identity with the one recorded after the
        store's own last write or full readback, and reads a file only when that
        identity differs. A full check reads every file. Either way, the bytes read
        must match the validated digest, and their identity becomes the new record.

        Parameters
        ----------
        full
            Read every file instead of trusting unchanged stat identities.

        Raises
        ------
        ValueError
            If a file's bytes differ from the validated state, or a file changes while
            it is being read.
        """

        refreshed = []

        for name, digest in self._validated_byte_hashes:
            path = self.root / name
            identity = file_stat_identity(path)

            if not full and identity == self._file_identities.get(name):
                continue

            payload = path.read_bytes()

            if (
                hashlib.sha256(payload).digest() != digest
                or file_stat_identity(path) != identity
            ):
                raise ValueError(f"LP checkpoint material changed: {name}.")

            if not full and name in self._file_identities:
                refreshed.append(name)

            self._file_identities[name] = identity

        if refreshed:
            logger.info(
                "LP checkpoint file identity changed with identical bytes; refreshed "
                "after readback: {}",
                ", ".join(refreshed),
            )

    def _commit(
        self, payloads: dict[str, bytes], *, request_indices: Iterable[int] = ()
    ) -> None:
        """Finish a journaled state replacement and retire its durable transaction.

        The applied files are read back against the journal's next state, and one
        authority check runs before the journal is retired.

        Parameters
        ----------
        payloads
            Complete validated artifact and receipt bytes already stored in the journal.
        request_indices
            Requests published by this commit, re-hashed by the authority guard.
        """

        for name in payloads:
            _atomic_write(path=self.root / name, payload=payloads[name])

        self._check_checkpoint_files(full=True)
        self.verify_execution_authority(full=False, request_indices=request_indices)
        (self.root / _TRANSACTION).unlink()
        _sync_directory(self.root)

    def _encode_records(self) -> None:
        """Validate every parsed record and make its canonical bytes its authority."""

        self._record_encodings = {}

        for stage in _FILENAMES:
            for index, row in enumerate(self._rows[stage]):
                self._record_bytes(key=(stage, index), record=row)

            for index, row in self._pending[stage].items():
                self._record_bytes(key=(stage, index), record=row)

        for position, attempt in enumerate(self._attempts):
            self._record_bytes(key=("attempt", position), record=attempt)

        for position, failure in enumerate(self._failures):
            self._record_bytes(key=("failure", position), record=failure)

    def _failed_pair_ids(self) -> list[str]:
        """Return the sorted pair identities of unresolved failures.

        Returns
        -------
        list[str]
            Pair identities whose failure has no completed response yet.
        """

        return sorted(
            {
                pair_id
                for failure in self._failures
                if failure.resolved_run_number is None
                for pair_id in failure.pair_ids
            }
        )

    def _load(self, *, read_only: bool) -> None:
        """Recover a proven interrupted commit, then validate every persisted byte.

        Parameters
        ----------
        read_only
            Validate a transaction without repairing or retiring it.

        Raises
        ------
        ValueError
            If the checkpoint receipt is invalid, the persisted artifacts are
            incomplete or noncanonical, or the stage prefixes, failures, and attempts
            are misaligned.
        """

        if (self.root / _TRANSACTION).exists() or (
            self.root / _TRANSACTION
        ).is_symlink():
            self._recover(read_only=read_only)
            return

        receipt_bytes = (self.root / _RECEIPT).read_bytes()
        receipt = _read_receipt(receipt_bytes)

        self._load_snapshot(
            {
                name: (self.root / name).read_bytes()
                for name in (*receipt["artifact_byte_hashes"], _RECEIPT)
            }
        )

        self.verify_bytes(full=True)

    def _load_snapshot(self, payloads: dict[str, bytes]) -> None:
        """Validate a complete checkpoint snapshot without changing persisted files.

        Parameters
        ----------
        payloads
            Complete artifact bytes from disk or a pending transaction journal.

        Raises
        ------
        ValueError
            If material, schemas, hashes, prefixes, or dispositions are invalid, or
            artifact bytes are not canonical and complete.
        """

        receipt = _read_receipt(payloads[_RECEIPT])

        if (
            receipt["material"] != self._material
            or receipt["execution_content_hash"] != self.execution_hash
            or self._material["request_manifest"]
            != self.population.manifest.model_dump(mode="json")
        ):
            raise ValueError("LP execution material changed; regenerate before reuse.")

        self.run_number = receipt["run_number"]
        self._committed_status = receipt["status"]

        if (
            isinstance(self.run_number, bool)
            or not isinstance(self.run_number, int)
            or self.run_number < 0
        ):
            raise ValueError("LP checkpoint run number is invalid.")

        if set(payloads) != _CHECKPOINT_NAMES:
            raise ValueError("LP checkpoint receipt has incomplete artifact coverage.")

        self._parse_artifacts(payloads=payloads, receipt=receipt)
        self._encode_records()
        self._validate()
        self._refresh_request_states(range(len(self.population.requests)))

        # Canonical byte equality also rejects duplicate JSON keys, non-finite values,
        # blank lines, missing terminal newlines, and schema-normalized alterations.
        expected_payloads = self._payloads()

        if payloads[_RECEIPT] != _canonical_bytes(self._receipt(expected_payloads)):
            raise ValueError("LP checkpoint receipt counts or disposition differ.")

        for name, expected in expected_payloads.items():
            if payloads[name] != expected:
                raise ValueError(f"LP checkpoint material is not canonical: {name}.")

        self._validated_byte_hashes = tuple(
            (name, hashlib.sha256(payloads[name]).digest()) for name in sorted(payloads)
        )

    def _parse_artifacts(
        self, *, payloads: dict[str, bytes], receipt: dict[str, Any]
    ) -> None:
        """Parse complete artifacts only after matching their receipt byte hashes.

        Parameters
        ----------
        payloads
            Complete artifact bytes to validate and parse.
        receipt
            Material-validated receipt with exact artifact filename coverage.

        Raises
        ------
        ValueError
            If artifact hashes differ, JSON is invalid, or records fail schema
            validation.
        """

        for name in receipt["artifact_byte_hashes"]:
            payload = payloads[name]

            if (
                hashlib.sha256(payload).hexdigest()
                != receipt["artifact_byte_hashes"][name]
            ):
                raise ValueError(f"LP checkpoint bytes differ from receipt: {name}.")

            if name == _FAILURES:
                raw = json.loads(payload)

                if not isinstance(raw, list):
                    raise ValueError("LP failure artifact must be an ordered list.")

                self._failures = [_LPFailure.model_validate(item) for item in raw]
            elif name == _PENDING:
                self._parse_pending(payload)
            elif name == _USAGE:
                raw = _read_usage(payload)
                if raw["max_concurrent_requests"] != self.capacity:
                    raise ValueError(
                        "LP usage capacity differs from execution material."
                    )
                self._attempts = [
                    _LPAttempt.model_validate(row) for row in raw["attempts"]
                ]
            else:
                stage = next(key for key, value in _FILENAMES.items() if value == name)
                self._rows[stage] = [
                    _LPCheckpoint.model_validate_json(line)
                    for line in payload.splitlines()
                ]

        self._attempt_positions = {}

        for position, attempt in enumerate(self._attempts):
            self._attempt_positions.setdefault(attempt.request_index, []).append(
                position
            )

    def _parse_pending(self, payload: bytes) -> None:
        """Parse unique ordered pending results for each successful stage.

        Parameters
        ----------
        payload
            Receipt-authenticated canonical pending-journal bytes.

        Raises
        ------
        ValueError
            If the pending journal is invalid, misaligned, or contains duplicate rows.
        """

        raw = json.loads(payload)

        if not isinstance(raw, dict) or set(raw) != set(_FILENAMES):
            raise ValueError("LP pending journal has invalid stage coverage.")

        self._pending = {}

        for stage, records in raw.items():
            parsed = [_LPCheckpoint.model_validate(row) for row in records]

            if [row.request_index for row in parsed] != sorted(
                {row.request_index for row in parsed}
            ):
                raise ValueError("LP pending journal has duplicate or unordered rows.")

            self._pending[stage] = {row.request_index: row for row in parsed}

    def _payloads(self) -> dict[str, bytes]:
        """Assemble the canonical artifacts from each record's authority bytes.

        No record is re-proved here: every record was validated and encoded when the
        store created or changed it.

        Returns
        -------
        dict[str, bytes]
            Exact artifact payloads covered by the commit receipt.
        """

        encodings = self._record_encodings
        pending = {
            stage: b"["
            + b",".join(encodings[(stage, index)] for index in sorted(rows))
            + b"]"
            for stage, rows in self._pending.items()
        }
        attempts = (
            b"["
            + b",".join(
                encodings[("attempt", position)]
                for position in range(len(self._attempts))
            )
            + b"]"
        )
        usage = {
            "attempts": attempts,
            "max_concurrent_requests": _canonical_bytes(self.capacity).rstrip(b"\n"),
            "available_cost": b"null",
            "request_states": b"[" + b",".join(self._request_state_encodings) + b"]",
            "unknown_usage_attempts": str(
                sum(attempt.usage is None for attempt in self._attempts)
            ).encode("ascii"),
        }

        return {
            _PENDING: _encoded_object(pending),
            _USAGE: _encoded_object(usage),
            **{
                filename: b"".join(
                    encodings[(stage, index)] + b"\n"
                    for index in range(len(self._rows[stage]))
                )
                for stage, filename in _FILENAMES.items()
            },
            _FAILURES: b"["
            + b",".join(
                encodings[("failure", position)]
                for position in range(len(self._failures))
            )
            + b"]\n",
        }

    def _previous_hashes(self) -> dict[str, str | None]:
        """Identify the committed predecessor before starting another transaction.

        Returns
        -------
        dict[str, str or None]
            Verified file hashes, or explicit absence for an entirely fresh store.

        Raises
        ------
        ValueError
            If the predecessor is incomplete, was never validated by this store, or
            its files have changed since the store's last write or full readback.
        """

        if not (self.root / _RECEIPT).exists():
            if any((self.root / name).exists() for name in _CHECKPOINT_NAMES):
                raise ValueError("LP checkpoint predecessor is incomplete.")

            return dict.fromkeys(_CHECKPOINT_NAMES)

        if {name for name, _ in self._validated_byte_hashes} != _CHECKPOINT_NAMES:
            raise ValueError(
                "LP checkpoint predecessor was not validated by this store."
            )

        # Unchanged stat identities vouch for the recorded digests; any difference
        # forces a readback against them before they become the journal's predecessor.
        self._check_checkpoint_files(full=False)

        return {name: digest.hex() for name, digest in self._validated_byte_hashes}

    def _put_attempt(self, *, attempt: _LPAttempt, position: int) -> None:
        """Validate a new or changed attempt and make its bytes its authority.

        Parameters
        ----------
        attempt
            Complete attempt record, never shared with any caller.
        position
            Existing position to replace, or the next position to append.

        Raises
        ------
        ValueError
            If the position is neither existing nor next, or the attempt moves to a
            different request.
        """

        attempt = _LPAttempt.model_validate(attempt.model_dump(mode="python"))

        if position == len(self._attempts):
            self._record_bytes(key=("attempt", position), record=attempt)
            self._attempts.append(attempt)
            self._attempt_positions.setdefault(attempt.request_index, []).append(
                position
            )
        elif (
            0 <= position < len(self._attempts)
            and self._attempts[position].request_index == attempt.request_index
        ):
            self._record_bytes(key=("attempt", position), record=attempt)
            self._attempts[position] = attempt
        else:
            raise ValueError("LP attempt position or request is invalid.")

    def _receipt(self, payloads: dict[str, bytes]) -> dict[str, Any]:
        """Compute the complete material receipt and truthful processing status.

        Parameters
        ----------
        payloads
            Actual canonical artifact bytes.

        Returns
        -------
        dict[str, Any]
            File hashes, stage counts, failure coverage, and execution material.
        """

        return {
            "artifact_byte_hashes": {
                name: hashlib.sha256(payload).hexdigest()
                for name, payload in payloads.items()
            },
            "execution_content_hash": self.execution_hash,
            "failed_pair_ids": self._failed_pair_ids(),
            "material": self._material,
            "run_number": self.run_number,
            "stage_counts": {stage: len(rows) for stage, rows in self._rows.items()},
            "status": self._status(),
        }

    def _recover(self, *, read_only: bool) -> None:
        """Roll forward only a validated transaction with exact old/new file states.

        Parameters
        ----------
        read_only
            Validate the complete recovery state without writing it.

        Raises
        ------
        ValueError
            If the journal or intended snapshot is invalid or stale, or an artifact
            matches neither its recorded previous nor next state.
        """

        transaction = _read_transaction(self.root)
        names = set(transaction.next_payloads)
        previous = transaction.previous_byte_hashes

        payloads = {
            name: payload.encode("utf-8")
            for name, payload in transaction.next_payloads.items()
        }

        # Validate requests, prompts, schemas, prefixes, and failure dispositions
        # before repairing any file. The journal never permits arbitrary corruption
        # repair.
        self._load_snapshot(payloads)

        for name in sorted(names):
            path = self.root / name
            actual = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.exists() or path.is_symlink()
                else None
            )
            following = hashlib.sha256(payloads[name]).hexdigest()

            if actual not in (previous[name], following):
                raise ValueError(
                    f"LP interrupted checkpoint has unexplained material: {name}."
                )

        if not read_only:
            # The one authority check before applying a journal written earlier.
            self.verify_execution_authority(full=False)
            self._commit(payloads)

    def _record_bytes(self, *, key: tuple[str, int], record: BaseSchema) -> bytes:
        """Validate one record and make its canonical bytes its publication authority.

        Called only when the store creates, changes or loads a record; publication
        reuses the stored bytes without re-proving them.

        Parameters
        ----------
        key
            Store-local collection and position, never an authority by itself.
        record
            Complete record, including every nested payload or usage value.

        Returns
        -------
        bytes
            Canonical JSON without its file-level terminal newline.
        """

        # Validate before JSON-mode coercion: an invalid datetime/tuple/object must not
        # become authority merely by encoding like a valid JSON value.
        material = _JSON_RECORD.validate_python(
            record.model_dump(mode="python", warnings="error"), strict=True
        )
        encoded = _canonical_bytes(material)[:-1]
        self._changes += 1
        self._record_encodings[key] = encoded
        return encoded

    def _refresh_request_states(self, indices: Iterable[int]) -> None:
        """Re-encode the processing state of the requests a change can affect.

        Parameters
        ----------
        indices
            Requests whose rows, attempts or failures changed.
        """

        for index in indices:
            self._request_state_encodings[index] = _canonical_bytes(
                self._request_state(index)
            )[:-1]

    def _request_state(self, index: int) -> dict[str, Any]:
        """Describe one request's unfinished, unknown, cancelled or unstarted work.

        Parameters
        ----------
        index
            Request position.

        Returns
        -------
        dict[str, Any]
            Population-aligned processing state, independent of semantic judgments.
        """

        finished = [
            stage
            for stage in _FILENAMES
            if self._row(request_index=index, stage=stage) is not None
        ]
        positions = self._attempt_positions.get(index)
        attempt = None if not positions else self._attempts[positions[-1]]

        if "response" in finished:
            state = "completed"
        elif any(
            failure.request_index == index and failure.resolved_run_number is None
            for failure in self._failures
        ):
            state = "failed"
        elif attempt is None:
            state = "unfinished" if finished else "not_started"
        else:
            state = {
                "dispatched": "dispatch_outcome_pending",
                "unknown": "unknown",
                "cancelled": "cancelled",
            }.get(attempt.status, "unfinished")

        return {
            "completed_stages": finished,
            "request_id": str(self.population.requests[index].request_id),
            "request_index": index,
            "state": state,
        }

    def _request_states(self) -> list[dict[str, Any]]:
        """Describe unfinished, unknown, cancelled and unstarted work explicitly.

        Returns
        -------
        list[dict[str, Any]]
            Population-aligned processing states, independent of semantic judgments.
        """

        return [
            self._request_state(index) for index in range(len(self.population.requests))
        ]

    def _row(self, *, request_index: int, stage: str) -> _LPCheckpoint | None:
        """Resolve one private prefix or pending row without copying it.

        Parameters
        ----------
        request_index
            Deterministic request position.
        stage
            Required draft, verdict, or response stage.

        Returns
        -------
        _LPCheckpoint or None
            The store's own record, which must never leave the store.
        """

        if request_index < len(self._rows[stage]):
            return self._rows[stage][request_index]

        return self._pending[stage].get(request_index)

    def _save(self, *, request_indices: Iterable[int] = ()) -> None:
        """Validate and durably journal the changed state before replacing any files.

        A save validates only the requests it changed (plus pending completions,
        which every save republishes) and re-proves only their records. One
        authority check runs before the journal is written and one before it is
        retired. The save whose receipt status first becomes `completed` runs the
        complete record validation and the full input and checkpoint proofs first.

        Parameters
        ----------
        request_indices
            Requests whose records this save adds or changes.

        Raises
        ------
        ValueError
            If state or predecessor evidence is invalid, or an unfinished
            transaction already exists.
        """

        self._changes += 1
        indices = sorted(
            {
                *request_indices,
                *(index for rows in self._pending.values() for index in rows),
            }
        )

        if self._status() == "completed" and self._committed_status != "completed":
            # Completion boundary: complete proofs before completion is first recorded.
            self._validate()
            self.verify_execution_authority(full=True)

            if self._validated_byte_hashes:
                self._check_checkpoint_files(full=True)
        else:
            self._validate(indices=indices)

        self._refresh_request_states(indices)
        payloads = self._payloads()
        receipt = self._receipt(payloads)
        payloads[_RECEIPT] = _canonical_bytes(receipt)

        if (self.root / _TRANSACTION).exists():
            raise ValueError("LP checkpoint has an unfinished transaction; reload it.")

        transaction = _LPCheckpointTransaction(
            next_payloads={
                name: payload.decode("utf-8") for name, payload in payloads.items()
            },
            previous_byte_hashes=self._previous_hashes(),
        )
        self.verify_execution_authority(full=False, request_indices=indices)
        self._validated_byte_hashes = tuple(
            (name, hashlib.sha256(payloads[name]).digest()) for name in sorted(payloads)
        )
        _atomic_write(
            path=self.root / _TRANSACTION,
            payload=_canonical_bytes(transaction.model_dump(mode="json")),
        )
        self._commit(payloads, request_indices=indices)
        self._committed_status = receipt["status"]

    def _set_failures(self, failures: list[_LPFailure]) -> None:
        """Order, validate and encode a changed failure history.

        Failures are rare, so the whole history is re-encoded whenever it changes.

        Parameters
        ----------
        failures
            Complete failure history, never shared with any caller.
        """

        ordered = sorted(
            (
                _LPFailure.model_validate(failure.model_dump(mode="python"))
                for failure in failures
            ),
            key=lambda failure: (
                failure.run_number,
                failure.request_index,
                0 if failure.stage == "draft" else 1,
                failure.attempt,
            ),
        )

        for position in range(len(self._failures)):
            self._record_encodings.pop(("failure", position), None)

        for position, failure in enumerate(ordered):
            self._record_bytes(key=("failure", position), record=failure)

        self._failures = ordered

    def _status(self) -> str:
        """Return the truthful processing status recorded in the receipt.

        Returns
        -------
        str
            `failed`, `completed` or `incomplete`.
        """

        if self._failed_pair_ids():
            return "failed"

        if len(self._rows["response"]) == len(self.population.requests):
            return "completed"

        return "incomplete"

    def _store_result(
        self, *, payload: BaseSchema, request_index: int, stage: str
    ) -> _LPCheckpoint:
        """Construct, validate and encode one pending result with saved dependencies.

        Parameters
        ----------
        payload
            Complete schema-validated stage payload.
        request_index
            Deterministic request position.
        stage
            Draft, verdict, or reconciled response.

        Returns
        -------
        _LPCheckpoint
            The store's own pending record, ready for atomic publication.

        Raises
        ------
        ValueError
            If the stage is already durable, lacks its prerequisite, or the new row
            fails validation.
        """

        if self._row(request_index=request_index, stage=stage) is not None:
            raise ValueError("LP stage completion is already durable.")

        request = self.population.requests[request_index]
        previous_stage = {"verdict": "draft", "response": "verdict"}.get(stage)
        previous = (
            None
            if previous_stage is None
            else self._row(request_index=request_index, stage=previous_stage)
        )

        if previous_stage is not None and previous is None:
            raise ValueError("LP stage lacks its durable dependency.")

        draft_row = self._row(request_index=request_index, stage="draft")
        row = _LPCheckpoint(
            execution_content_hash=self.execution_hash,
            payload=payload.model_dump(mode="json"),
            payload_content_hash=content_hash(payload.model_dump(mode="json")),
            prerequisite_content_hash=(
                request.request_content_hash
                if previous is None
                else content_hash(previous.model_dump(mode="json"))
            ),
            prompt_content_hash=_stage_prompt_hash(
                draft=(
                    None
                    if draft_row is None
                    else LPGenerationResponse.model_validate(draft_row.payload)
                ),
                material=self._material,
                request=request,
                stage=stage,
            ),
            request_index=request_index,
            stage=stage,
        )
        self._validate_row(index=request_index, row=row, stage=stage)
        self._record_bytes(key=(stage, request_index), record=row)
        self._pending[stage][request_index] = row
        return row

    @contextmanager
    def _updating(self) -> Iterator[None]:
        """Fail closed for the rest of the invocation if an update fails midway.

        An update that raises after changing any record, encoding or file may leave
        in-memory state ahead of the persisted state, so every later update or
        verification is refused. A rejected precondition changes nothing.

        Yields
        ------
        None
            Exclusive update scope.

        Raises
        ------
        ValueError
            If an earlier update of this store failed.
        """

        if self._failed_update:
            raise ValueError("LP checkpoint store failed an earlier update; reload it.")

        changes = self._changes

        try:
            yield
        except BaseException:
            if self._changes != changes:
                self._failed_update = True

            raise

    def _validate(self, *, indices: Iterable[int] | None = None) -> None:
        """Require stage-specific contiguous prefixes and honest failures.

        Without `indices`, every record is validated (a full verification point).
        With `indices`, only those requests' rows and attempts are validated, along
        with the cross-record invariants a change to them can affect: stage length
        order, pending capacity and boundaries, and failure and attempt dispositions.

        Parameters
        ----------
        indices
            Requests whose records changed, or None for complete validation.

        Raises
        ------
        ValueError
            If stage prefixes, row dependencies, or failure dispositions are invalid.
        """

        drafts = self._rows["draft"]
        verdicts = self._rows["verdict"]
        responses = self._rows["response"]

        if (
            not len(responses)
            <= len(verdicts)
            <= len(drafts)
            <= len(self.population.requests)
        ):
            raise ValueError("LP checkpoint stage lengths are misaligned.")

        pending_indices = {index for rows in self._pending.values() for index in rows}

        if len(pending_indices) > self.capacity:
            raise ValueError(
                "LP pending completions exceed the admitted request capacity."
            )

        targets = (
            set(range(len(drafts))) | pending_indices
            if indices is None
            else set(indices)
        )
        proofs: dict[tuple[str, int], tuple[bytes, ...]] = {}
        requests: dict[int, bytes] = {}

        for index in sorted(targets):
            self._validate_index_rows(index=index, proofs=proofs, requests=requests)

        self._validate_failures()
        self._validate_attempts(indices=None if indices is None else targets)

        if indices is None:
            self._row_proofs = proofs
        else:
            self._row_proofs.update(proofs)

    def _validate_attempt_completion(
        self, *, attempt: _LPAttempt, successful: set[tuple[int, str]]
    ) -> None:
        """Match one observed outcome to its durable success or failure evidence.

        Parameters
        ----------
        attempt
            Validated unique call identity and outcome.
        successful
            Stage identities already backed by a unique successful call.

        Raises
        ------
        ValueError
            If the attempt is misaligned with its durable evidence.
        """

        index = attempt.request_index
        key = (attempt.run_number, index, attempt.stage, attempt.attempt)

        if (index, attempt.stage) in successful:
            raise ValueError("LP attempt repeats an already completed stage.")

        if attempt.status == "succeeded":
            row = self._row(request_index=index, stage=attempt.stage)
            stage_key = (index, attempt.stage)

            if (
                row is None
                or stage_key in successful
                or attempt.checkpoint_content_hash
                != content_hash(row.model_dump(mode="json"))
            ):
                raise ValueError(
                    "LP successful attempt lacks its unique validated stage."
                )

            successful.add(stage_key)
        elif attempt.checkpoint_content_hash is not None:
            raise ValueError("LP unfinished or failed attempt claims a success.")

        matching = [
            failure
            for failure in self._failures
            if (
                failure.run_number,
                failure.request_index,
                failure.stage,
                failure.attempt,
            )
            == key
        ]

        if (attempt.status == "failed") != (len(matching) == 1):
            raise ValueError("LP attempt and failure evidence disagree.")

        if matching and (
            attempt.error_type != matching[0].error_type
            or attempt.error_content_hash != matching[0].error_content_hash
        ):
            raise ValueError("LP attempt error differs from failure evidence.")

    @staticmethod
    def _validate_attempt_usage(attempt: _LPAttempt) -> None:
        """Validate available counters without converting missing usage into zero.

        Parameters
        ----------
        attempt
            One observed or pending call record.

        Raises
        ------
        ValueError
            If the attempt usage is invalid, or the error identity is incomplete, or a
            successful or pending attempt claims contradictory error evidence, or a
            pending attempt claims observed usage.
        """

        if attempt.usage is not None and (
            set(attempt.usage)
            != {
                "cache_read_tokens",
                "cache_write_tokens",
                "input_tokens",
                "output_tokens",
                "requests",
                "runs",
            }
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in attempt.usage.values()
            )
        ):
            raise ValueError("LP attempt usage is invalid.")

        if (attempt.error_type is None) != (attempt.error_content_hash is None):
            raise ValueError("LP attempt error identity is incomplete.")

        if (
            attempt.status in {"dispatched", "succeeded"}
            and attempt.error_type is not None
        ):
            raise ValueError(
                "LP successful or pending attempt contains contradictory error evidence."
            )

        if attempt.status == "dispatched" and attempt.usage is not None:
            raise ValueError("LP pending outcome claims observed usage.")

    def _validate_attempts(self, *, indices: Iterable[int] | None = None) -> None:
        """Require unique bounded attempts, honest usage, and exact completion links.

        Parameters
        ----------
        indices
            Requests whose attempts to validate, or None for every request with any
            row, attempt or failure.

        Raises
        ------
        ValueError
            If an LP result is invalid.
        """

        if (
            sum(attempt.status == "dispatched" for attempt in self._attempts)
            > self.capacity
        ):
            raise ValueError("LP active dispatch records exceed capacity.")

        if indices is None:
            indices = (
                set(self._attempt_positions)
                | {failure.request_index for failure in self._failures}
                | set(range(len(self._rows["draft"])))
                | {index for rows in self._pending.values() for index in rows}
            )

        for index in sorted(set(indices)):
            self._validate_index_attempts(index)

    def _validate_cached_row(
        self,
        *,
        index: int,
        proofs: dict[tuple[str, int], tuple[bytes, ...]],
        requests: dict[int, bytes],
        row: _LPCheckpoint,
        stage: str,
    ) -> None:
        """Revalidate a row whenever it or a semantic dependency changed.

        Parameters
        ----------
        index
            Expected request position in the prefix or pending journal.
        proofs
            New proofs, installed only after the validation pass succeeds.
        requests
            Request digests computed from live records during this pass only.
        row
            Current stage payload and receipt identities.
        stage
            Expected stage; prefix membership and ordering remain separately checked.
        """

        if index not in requests:
            requests[index] = hashlib.sha256(
                to_json(
                    inf_nan_mode="constants",
                    value=self.population.requests[index].model_dump(
                        mode="json", warnings="error"
                    ),
                )
            ).digest()

        dependencies = [
            self._record_encodings.get((dependency, index), b"")
            for dependency in (("draft", "verdict") if stage != "draft" else ())
        ]
        key = (stage, index)
        proof = (
            self._material_digest,
            requests[index],
            self._record_encodings[key],
            *dependencies,
        )

        if self._row_proofs.get(key) != proof:
            self._validate_row(index=index, row=row, stage=stage)

        proofs[key] = proof

    def _validate_failures(self) -> None:
        """Require ordered bounded attempts and completion-backed dispositions.

        Raises
        ------
        ValueError
            If failure attempts violate request coverage, ordering, retry limits, or
            completion-backed resolution requirements.
        """

        seen: set[tuple[int, int, str, int]] = set()
        previous_order = (0, -1, -1, 0)

        for failure in self._failures:
            index = failure.request_index

            if index >= len(self.population.requests):
                raise ValueError("LP failure references an unrequested batch.")

            request = self.population.requests[index]
            key = (failure.run_number, index, failure.stage, failure.attempt)
            previous_key = (
                failure.run_number,
                index,
                failure.stage,
                failure.attempt - 1,
            )
            order = (
                failure.run_number,
                index,
                0 if failure.stage == "draft" else 1,
                failure.attempt,
            )
            maximum = self._material["retry_limits"][failure.stage] + 1

            if (
                order <= previous_order
                or failure.attempt > maximum
                or failure.exhausted != (failure.attempt == maximum)
                or (
                    failure.stage == "verdict"
                    and self._row(request_index=index, stage="draft") is None
                )
            ):
                raise ValueError("LP failure order, retry budget, or stage is invalid.")

            previous_order = order

            if key in seen or (failure.attempt > 1 and previous_key not in seen):
                raise ValueError("LP failure attempts contain duplicates or gaps.")

            seen.add(key)

            if (
                failure.request_id != str(request.request_id)
                or failure.request_content_hash != request.request_content_hash
                or failure.pair_ids != [pair.pair_id for pair in request.pairs]
                or failure.run_number > self.run_number
            ):
                raise ValueError("LP failure identity or coverage is misaligned.")

            resolved = failure.resolved_run_number
            response = self._row(request_index=index, stage="response")

            if resolved is None:
                if (
                    failure.resolved_response_content_hash is not None
                    or response is not None
                ):
                    raise ValueError(
                        "LP failure is unresolved despite a completed response."
                    )
            elif (
                not failure.run_number <= resolved <= self.run_number
                or response is None
                or failure.resolved_response_content_hash
                != response.payload_content_hash
            ):
                raise ValueError(
                    "LP resumed failure disposition lacks matching completion."
                )

    def _validate_index_attempts(self, index: int) -> None:
        """Validate one request's attempts, completion links and failure links.

        Parameters
        ----------
        index
            Request position.

        Raises
        ------
        ValueError
            If an attempt's identity, order, retry budget, usage or evidence link is
            invalid, or a completed stage lacks its successful attempt.
        """

        seen: dict[tuple[int, int, str, int], str] = {}
        successful: set[tuple[int, str]] = set()

        for position in self._attempt_positions.get(index, ()):
            attempt = self._attempts[position]
            key = (attempt.run_number, index, attempt.stage, attempt.attempt)

            if (
                key in seen
                or attempt.request_index != index
                or (
                    attempt.stage == "verdict"
                    and self._row(request_index=index, stage="draft") is None
                )
                or index >= len(self.population.requests)
                or attempt.run_number > self.run_number
                or attempt.attempt > self._material["retry_limits"][attempt.stage] + 1
                or (
                    attempt.attempt > 1
                    and seen.get((*key[:3], attempt.attempt - 1)) != "failed"
                )
                or attempt.request_id != str(self.population.requests[index].request_id)
            ):
                raise ValueError(
                    "LP attempt identity, order, or retry budget is invalid."
                )

            seen[key] = attempt.status
            self._validate_attempt_usage(attempt)
            self._validate_attempt_completion(attempt=attempt, successful=successful)

        for stage in ("draft", "verdict"):
            if (
                self._row(request_index=index, stage=stage) is not None
                and (index, stage) not in successful
            ):
                raise ValueError("LP completion lacks durable attempt accounting.")

        if any(
            seen.get(
                (
                    failure.run_number,
                    failure.request_index,
                    failure.stage,
                    failure.attempt,
                )
            )
            != "failed"
            for failure in self._failures
            if failure.request_index == index
        ):
            raise ValueError(
                "LP failure lacks its durable dispatch and outcome record."
            )

    def _validate_index_rows(
        self,
        *,
        index: int,
        proofs: dict[tuple[str, int], tuple[bytes, ...]],
        requests: dict[int, bytes],
    ) -> None:
        """Validate one request's prefix and pending rows in every stage.

        Parameters
        ----------
        index
            Request position.
        proofs
            New proofs collected during this validation pass.
        requests
            Request digests computed during this validation pass.

        Raises
        ------
        ValueError
            If a pending row overlaps a prefix or the population boundary, or a row
            fails validation.
        """

        for stage in _FILENAMES:
            in_prefix = index < len(self._rows[stage])
            pending_row = self._pending[stage].get(index)

            if pending_row is not None and (
                in_prefix or index >= len(self.population.requests)
            ):
                raise ValueError(
                    "LP pending completion overlaps a prefix or population boundary."
                )

            row = self._rows[stage][index] if in_prefix else pending_row

            if row is not None:
                self._validate_cached_row(
                    index=index, proofs=proofs, requests=requests, row=row, stage=stage
                )

    def _validate_row(self, *, index: int, row: _LPCheckpoint, stage: str) -> None:
        """Validate one complete stage row against its request and prerequisites.

        Parameters
        ----------
        index
            Contiguous position within the stage prefix.
        row
            Stored checkpoint with material identities and a complete payload.
        stage
            Expected code-owned stage for the containing file.

        Raises
        ------
        ValueError
            If row identity, schema, hashes, prerequisites, prompt material, or
            the selected complete response differs from the expected state.
        """

        draft_row = self._row(request_index=index, stage="draft")
        verdict_row = (
            row
            if stage == "verdict"
            else self._row(request_index=index, stage="verdict")
        )

        if (
            row.stage != stage
            or row.request_index != index
            or row.execution_content_hash != self.execution_hash
            or row.payload_content_hash != content_hash(row.payload)
        ):
            raise ValueError("LP checkpoint order, identity, or content differs.")

        request = self.population.requests[index]
        expected_prerequisite = request.request_content_hash
        draft: LPGenerationResponse | None = None

        if stage == "draft":
            response = LPGenerationResponse.model_validate(row.payload)
            verify_lp_generation_response_integrity(
                lp_generation_request=request, lp_generation_response=response
            )
        else:
            if draft_row is None:
                raise ValueError("LP checker lacks its validated producer dependency.")

            draft = LPGenerationResponse.model_validate(draft_row.payload)
            expected_prerequisite = content_hash(draft_row.model_dump(mode="json"))

            if verdict_row is None:
                raise ValueError("LP response lacks its validated checker dependency.")

            verdict = LPGenerationValidationVerdict.model_validate(verdict_row.payload)
            verify_lp_generation_validation_integrity(
                draft_response=draft,
                lp_generation_request=request,
                validation_verdict=verdict,
            )

            if stage == "response":
                final = reconciled_response(draft=draft, verdict=verdict)

                if row.payload != final.model_dump(mode="json"):
                    raise ValueError(
                        "LP response differs from the complete accepted/corrected judgment."
                    )

                verify_lp_generation_response_integrity(
                    lp_generation_request=request, lp_generation_response=final
                )
                expected_prerequisite = content_hash(
                    verdict_row.model_dump(mode="json")
                )

        if row.prerequisite_content_hash != expected_prerequisite:
            raise ValueError("LP checkpoint prerequisite changed.")

        expected_prompt = _stage_prompt_hash(
            draft=draft,
            material=self._material,
            request=request,
            stage=stage,
        )

        if row.prompt_content_hash != expected_prompt:
            raise ValueError("LP checkpoint prompt differs from current material.")

    def append(self, *, payload: BaseSchema, request_index: int, stage: str) -> None:
        """Persist local reconciliation and promote only contiguous stage prefixes.

        Parameters
        ----------
        payload
            Complete reconciled response.
        request_index
            Request with durably validated producer and checker stages.
        stage
            Local response stage; API outcomes use complete_attempt.

        Raises
        ------
        ValueError
            If request_index is greater than the total number of requests available.
        """

        with self._updating():
            self.verify_bytes()

            if stage != "response":
                raise ValueError("LP call results require atomic attempt accounting.")

            self._store_result(
                payload=payload, request_index=request_index, stage=stage
            )
            resolved = False
            failures = []

            for failure in self._failures:
                if (
                    failure.request_index == request_index
                    and failure.resolved_run_number is None
                ):
                    failure = failure.model_copy(
                        update={
                            "resolved_response_content_hash": content_hash(
                                payload.model_dump(mode="json")
                            ),
                            "resolved_run_number": self.run_number,
                        }
                    )
                    resolved = True

                failures.append(failure)

            if resolved:
                self._set_failures(failures)

            self.promote(request_indices=(request_index,))

    @property
    def attempts(self) -> Sequence[_LPAttempt]:
        """Return the attempt history as a read-only view of copies.

        Returns
        -------
        Sequence[_LPAttempt]
            Live view whose items are independent copies.
        """

        return _RecordSequence(self._attempts)

    def begin_run(self) -> None:
        """Retain interrupted call identities and assign a new persisted invocation."""

        with self._updating():
            self.verify_bytes()
            interrupted = []

            for position, attempt in enumerate(self._attempts):
                if attempt.status == "dispatched":
                    self._put_attempt(
                        attempt=attempt.model_copy(update={"status": "unknown"}),
                        position=position,
                    )
                    interrupted.append(attempt.request_index)

            self._changes += 1
            self.run_number += 1
            self._save(request_indices=interrupted)

    @property
    def capacity(self) -> int:
        """Return the immutable effective capacity captured for this execution."""

        capacity = self._material.get("max_concurrent_requests")

        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("LP execution material lacks a valid captured capacity.")

        return capacity

    def complete_attempt(
        self,
        *,
        attempt_index: int,
        error: BaseException | None,
        payload: BaseSchema | None,
        status: Literal["succeeded", "failed", "unknown", "cancelled"],
        usage: dict[str, int] | None,
    ) -> None:
        """Atomically retain a returned result or failure together with its usage.

        Parameters
        ----------
        attempt_index
            Index of the unique durable dispatch record.
        error
            Observed exception, retained by type and content hash only.
        payload
            Validated stage result, absent for any unsuccessful attempt.
        status
            Succeeded, failed, unknown, or cancelled before dispatch.
        usage
            Available counters; None explicitly denotes unavailable usage.

        Raises
        ------
        ValueError
            If the LP attempt contains errors.
        """

        with self._updating():
            self.verify_bytes()
            attempt = self._attempts[attempt_index]

            if attempt.status != "dispatched":
                raise ValueError("LP attempt already has an observed outcome.")

            changes: dict[str, Any] = {
                "status": status,
                "usage": None if usage is None else dict(usage),
            }

            if error is not None:
                changes["error_type"] = (
                    f"{type(error).__module__}.{type(error).__qualname__}"
                )
                changes["error_content_hash"] = content_hash(str(error))

            if status == "succeeded":
                if payload is None or error is not None:
                    raise ValueError(
                        "LP successful attempt requires its validated payload."
                    )

                row = self._store_result(
                    payload=payload,
                    request_index=attempt.request_index,
                    stage=attempt.stage,
                )
                changes["checkpoint_content_hash"] = content_hash(
                    row.model_dump(mode="json")
                )
            elif status == "failed":
                if error is None or payload is not None:
                    raise ValueError(
                        "LP failed attempt requires failure evidence only."
                    )

                request = self.population.requests[attempt.request_index]
                self._set_failures(
                    [
                        *self._failures,
                        _LPFailure(
                            attempt=attempt.attempt,
                            error_content_hash=content_hash(str(error)),
                            error_type=changes["error_type"],
                            exhausted=attempt.attempt
                            == self._material["retry_limits"][attempt.stage] + 1,
                            pair_ids=[pair.pair_id for pair in request.pairs],
                            request_content_hash=request.request_content_hash,
                            request_id=str(request.request_id),
                            request_index=attempt.request_index,
                            run_number=self.run_number,
                            stage=attempt.stage,
                        ),
                    ]
                )
            elif status not in {"unknown", "cancelled"} or payload is not None:
                raise ValueError("LP attempt outcome is invalid.")

            self._put_attempt(
                attempt=attempt.model_copy(update=changes), position=attempt_index
            )

            # Saving precedes promotion, so a crash never discards a valid suffix.
            self._save(request_indices=(attempt.request_index,))
            self.promote(request_indices=(attempt.request_index,))

    @property
    def failures(self) -> Sequence[_LPFailure]:
        """Return the failure history as a read-only view of copies.

        Returns
        -------
        Sequence[_LPFailure]
            Live view whose items are independent copies.
        """

        return _RecordSequence(self._failures)

    def get_row(self, *, request_index: int, stage: str) -> _LPCheckpoint | None:
        """Resolve one fully validated durable prefix or pending stage.

        Parameters
        ----------
        request_index
            Deterministic request position.
        stage
            Required draft, verdict, or response stage.

        Returns
        -------
        _LPCheckpoint or None
            Independent copy of the durable stage evidence, or absence without an
            invented success.
        """

        row = self._row(request_index=request_index, stage=stage)

        return None if row is None else row.model_copy(deep=True)

    @property
    def material(self) -> dict[str, Any]:
        """Return a copy of the captured execution material.

        Returns
        -------
        dict[str, Any]
            Independent copy; mutating it cannot change the store's receipts.
        """

        return copy.deepcopy(self._material)

    @property
    def pending(self) -> Mapping[str, Mapping[int, _LPCheckpoint]]:
        """Return pending completions as read-only views of copies.

        Returns
        -------
        Mapping[str, Mapping[int, _LPCheckpoint]]
            Live views keyed by stage and request index.
        """

        return MappingProxyType(
            {stage: _PendingRecords(rows) for stage, rows in self._pending.items()}
        )

    def promote(self, *, request_indices: Iterable[int] = ()) -> None:
        """Promote newly contiguous stages without losing saved suffix completions.

        Parameters
        ----------
        request_indices
            Requests the caller just changed, guarded together with promoted rows.
        """

        with self._updating():
            promoted = set(request_indices)

            for stage in _FILENAMES:
                pending = self._pending[stage]

                # The row keeps its key, so its authority bytes move with it.
                while len(self._rows[stage]) in pending:
                    self._changes += 1
                    promoted.add(len(self._rows[stage]))
                    self._rows[stage].append(pending.pop(len(self._rows[stage])))

            self._save(request_indices=promoted)

    def record_dispatch(self, *, request_index: int, stage: str) -> int:
        """Persist one bounded attempt identity before giving a worker its call.

        Parameters
        ----------
        request_index
            Admitted deterministic request position.
        stage
            Earliest unfinished producer or checker stage.

        Returns
        -------
        int
            Stable index used to record the one observed outcome.

        Raises
        ------
        ValueError
            If the dispatch would repeat a durably completed stage, duplicate an active
            or unknown attempt, or exceed the configured retry budget.
        """

        with self._updating():
            self.verify_bytes()

            if self._row(request_index=request_index, stage=stage) is not None:
                raise ValueError("LP dispatch would repeat a durably completed stage.")

            prior = [
                self._attempts[position]
                for position in self._attempt_positions.get(request_index, ())
                if self._attempts[position].run_number == self.run_number
                and self._attempts[position].stage == stage
            ]

            if any(item.status != "failed" for item in prior):
                raise ValueError("LP dispatch duplicates an active or unknown attempt.")

            attempt = len(prior) + 1

            if attempt > self._material["retry_limits"][stage] + 1:
                raise ValueError("LP dispatch exceeds the configured retry budget.")

            position = len(self._attempts)
            self._put_attempt(
                attempt=_LPAttempt(
                    attempt=attempt,
                    request_id=str(self.population.requests[request_index].request_id),
                    request_index=request_index,
                    run_number=self.run_number,
                    stage=stage,
                    status="dispatched",
                ),
                position=position,
            )
            self._save(request_indices=(request_index,))
            return position

    @property
    def retry_limits(self) -> dict[str, int]:
        """Return a copy of the captured retry limits per stage.

        Returns
        -------
        dict[str, int]
            Maximum retries for `draft` and `verdict`.
        """

        return dict(self._material["retry_limits"])

    @property
    def rows(self) -> Mapping[str, Sequence[_LPCheckpoint]]:
        """Return the stage prefixes as read-only views of copies.

        Returns
        -------
        Mapping[str, Sequence[_LPCheckpoint]]
            Live views keyed by stage.
        """

        return MappingProxyType(
            {stage: _RecordSequence(rows) for stage, rows in self._rows.items()}
        )

    def verify_bytes(self, *, full: bool = False) -> None:
        """Fail closed if persisted progress changed since this store validated it.

        Records cannot change outside the store's own validated updates, so only the
        files need checking.

        Parameters
        ----------
        full
            Read back every checkpoint file. Otherwise a file is read only when its
            stat identity differs from the store's last write or full readback.

        Raises
        ------
        ValueError
            If an earlier update failed, or persisted artifact bytes differ from the
            validated state.
        """

        if self._failed_update:
            raise ValueError("LP checkpoint store failed an earlier update; reload it.")

        self.verify_ownership()
        self._check_checkpoint_files(full=full)

    def verify_execution_authority(
        self, *, full: bool = True, request_indices: Iterable[int] = ()
    ) -> None:
        """Recheck immutable inputs and ownership before any durable publication.

        Parameters
        ----------
        full
            Run the complete input proof instead of the cheap guard.
        request_indices
            Requests the guard re-hashes; a full proof covers every request.
        """

        self.verify_ownership()

        if self.execution_check is not None:
            self.execution_check(full=full, request_indices=tuple(request_indices))

    def verify_ownership(self) -> None:
        """Reject publication or dispatch after losing generation-directory ownership."""

        if self.ownership_check is not None:
            self.ownership_check()


def _atomic_write(*, path: Path, payload: bytes) -> None:
    """Replace one artifact atomically with flushed complete bytes.

    Parameters
    ----------
    path
        Artifact path within the locked generation directory.
    payload
        Complete validated UTF-8 serialization.
    """

    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")

    try:
        with os.fdopen(fd=descriptor, mode="wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(dst=path, src=temporary)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _canonical_bytes(value: Any) -> bytes:
    """Serialize canonical finite JSON with a mandatory terminal newline.

    Parameters
    ----------
    value
        JSON-compatible artifact material.

    Returns
    -------
    bytes
        Canonical UTF-8 JSON payload.
    """

    return (
        json.dumps(
            allow_nan=False,
            ensure_ascii=False,
            obj=value,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _encoded_object(values: dict[str, bytes]) -> bytes:
    """Join already canonical JSON values using the existing object byte format.

    Parameters
    ----------
    values
        Field names and canonical JSON values without terminal newlines.

    Returns
    -------
    bytes
        Sorted compact JSON object with its mandatory terminal newline.
    """

    return (
        b"{"
        + b",".join(
            _canonical_bytes(key)[:-1] + b":" + values[key] for key in sorted(values)
        )
        + b"}\n"
    )


def _read_receipt(payload: bytes) -> dict[str, Any]:
    """Require a canonical receipt authenticating the complete journal-bearing store.

    Parameters
    ----------
    payload
        Captured receipt bytes from disk or a transaction.

    Returns
    -------
    dict[str, Any]
        Receipt with exact artifact coverage and captured execution capacity.

    Raises
    ------
    ValueError
        If the receipt format or recorded material is unsupported.
    """

    receipt = json.loads(payload)

    if not isinstance(receipt, dict) or set(receipt) != {
        "artifact_byte_hashes",
        "execution_content_hash",
        "failed_pair_ids",
        "material",
        "run_number",
        "stage_counts",
        "status",
    }:
        raise ValueError("LP checkpoint receipt has invalid fields.")

    hashes = receipt["artifact_byte_hashes"]

    if (
        not isinstance(hashes, dict)
        or set(hashes) != _CHECKPOINT_NAMES - {_RECEIPT}
        or payload != _canonical_bytes(receipt)
        or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in hashes.values()
        )
    ):
        raise ValueError(
            "LP checkpoint receipt requires complete authenticated journals."
        )

    material = receipt["material"]

    if not isinstance(material, dict) or set(material) != {
        "checker_instructions",
        "config_content_hash",
        "max_concurrent_requests",
        "model_config",
        "model_settings",
        "producer_instructions",
        "prompt_definitions_content_hash",
        "request_manifest",
        "response_schema_content_hash",
        "retry_limits",
        "verdict_schema_content_hash",
    }:
        raise ValueError(
            "LP checkpoint execution material has incomplete or unknown fields."
        )

    capacity = material["max_concurrent_requests"]

    if (
        isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or capacity < 1
        or receipt["execution_content_hash"] != content_hash(material)
    ):
        raise ValueError(
            "LP checkpoint material lacks a valid captured capacity or identity."
        )

    return receipt


def _read_transaction(root: Path) -> _LPCheckpointTransaction:
    """Read only complete native initialization or update transactions.

    Parameters
    ----------
    root
        Existing execution directory, which remains unchanged.

    Returns
    -------
    _LPCheckpointTransaction
        Canonical complete transaction pending semantic and old/new byte validation.

    Raises
    ------
    ValueError
        If coverage, predecessor hashes, or visible receipt/usage formats are
        unsupported.
    """

    raw = (root / _TRANSACTION).read_bytes()
    transaction = _LPCheckpointTransaction.model_validate_json(raw)
    previous = transaction.previous_byte_hashes

    if (
        raw != _canonical_bytes(transaction.model_dump(mode="json"))
        or set(transaction.next_payloads) != _CHECKPOINT_NAMES
        or set(previous) != _CHECKPOINT_NAMES
        or (
            any(value is None for value in previous.values())
            and not all(value is None for value in previous.values())
        )
        or any(
            value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in previous.values()
        )
    ):
        raise ValueError("LP checkpoint transaction is incomplete or noncanonical.")

    # Visible predecessor evidence cannot be upgraded by a transaction's next state.
    for name, reader in ((_RECEIPT, _read_receipt), (_USAGE, _read_usage)):
        path = root / name

        if path.exists() or path.is_symlink():
            reader(path.read_bytes())

    return transaction


def _read_usage(payload: bytes) -> dict[str, Any]:
    """Require explicit canonical attempt evidence without compatibility counters.

    Parameters
    ----------
    payload
        Usage journal bytes from a committed or interrupted checkpoint.

    Returns
    -------
    dict[str, Any]
        Current usage record, pending receipt and attempt validation.

    Raises
    ------
    ValueError
        If fields, capacity, or canonical encoding are unsupported.
    """

    raw = json.loads(payload)

    if not isinstance(raw, dict) or set(raw) != {
        "attempts",
        "available_cost",
        "max_concurrent_requests",
        "request_states",
        "unknown_usage_attempts",
    }:
        raise ValueError(
            "LP usage journal has invalid fields; compatibility counters are unsupported."
        )

    capacity = raw["max_concurrent_requests"]

    if (
        isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or capacity < 1
        or not isinstance(raw["attempts"], list)
        or payload != _canonical_bytes(raw)
    ):
        raise ValueError("LP usage journal is invalid or noncanonical.")

    return raw


def _stage_prompt_hash(
    *,
    draft: LPGenerationResponse | None,
    material: dict[str, Any],
    request: LPGenerationRequest,
    stage: str,
) -> str:
    """Bind each call checkpoint to the exact messages used for that stage.

    Parameters
    ----------
    draft
        Complete producer draft for checker input, absent for producer calls.
    material
        Current execution material containing effective curriculum instructions.
    request
        Authoritative bounded request.
    stage
        Code-owned execution stage; reconciliation binds to the checker messages.

    Returns
    -------
    str
        Actual system and user message content hash.

    Raises
    ------
    ValueError
        If a checker or response stage lacks its producer draft.
    """

    if stage == "draft":
        prompt = build_lp_generation_prompt(
            lp_generation_request=request,
            producer_instructions=material["producer_instructions"],
        )
    else:
        if draft is None:
            raise ValueError("LP checker prompt requires a validated producer draft.")

        prompt = validate_lp_generation_response(
            checker_instructions=material["checker_instructions"],
            draft_response=draft,
            lp_generation_request=request,
            producer_instructions=material["producer_instructions"],
        )

    return content_hash(
        {"system_message": prompt.system_message, "user_message": prompt.user_message}
    )


def _sync_directory(path: Path) -> None:
    """Flush directory entries so journal publication precedes artifact replacement.

    Parameters
    ----------
    path
        Directory whose renames or transaction removal must be durable.
    """

    descriptor = os.open(flags=os.O_RDONLY, path=path)

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def archive_lp_generation_artifacts(root: Path) -> None:
    """Archive existing generation evidence before explicit deterministic regeneration.

    Parameters
    ----------
    root
        Locked directory containing any prior candidate, request, and stage files.

    Raises
    ------
    ValueError
        If existing archived evidence conflicts with its content identity, or copied
        evidence fails byte reconciliation.
    """

    validate_lp_checkpoint_format(root)
    names = (
        *_FILENAMES.values(),
        _FAILURES,
        _PENDING,
        _USAGE,
        _RECEIPT,
        _TRANSACTION,
        "lp_candidate_pairs.jsonl",
        "lp_candidate_summary.json",
        "lp_generation_requests.jsonl",
        "lp_generation_requests_manifest.json",
    )
    payloads = {
        name: (root / name).read_bytes() for name in names if (root / name).exists()
    }

    if not payloads:
        return

    hashes = {
        name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()
    }
    archive = root / "lp_generation_history" / content_hash(hashes)
    archive.mkdir(exist_ok=True, parents=True)

    for name, payload in payloads.items():
        path = archive / name

        if path.exists() and path.read_bytes() != payload:
            raise ValueError("LP archived evidence differs from its content identity.")

        _atomic_write(path=path, payload=payload)

    _atomic_write(
        path=archive / "disposition.json",
        payload=_canonical_bytes(
            {
                "artifact_byte_hashes": hashes,
                "disposition": "superseded_by_explicit_regeneration",
            }
        ),
    )

    for name, payload in payloads.items():
        if (archive / name).read_bytes() != payload:
            raise ValueError("LP prior evidence archive failed reconciliation.")

    for name in payloads:
        (root / name).unlink()


def content_hash(value: Any) -> str:
    """Identify actual canonical material without a configurable version marker.

    Parameters
    ----------
    value
        Complete JSON-compatible material.

    Returns
    -------
    str
        SHA-256 digest of canonical JSON bytes.

    Raises
    ------
    TypeError
        If material cannot be serialized as JSON.
    ValueError
        If material contains non-finite numbers or circular references.
    """

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def reconciled_response(
    *, draft: LPGenerationResponse, verdict: LPGenerationValidationVerdict
) -> LPGenerationResponse:
    """Select a whole accepted draft or complete checker replacement.

    Parameters
    ----------
    draft
        Integrity-validated producer response.
    verdict
        Integrity-validated checker verdict.

    Returns
    -------
    LPGenerationResponse
        Independent copy of the complete selected judgment set.

    Raises
    ------
    ValueError
        If a failed verdict lacks a complete correction or the selected response fails
        schema validation.
    """

    response = draft if verdict.passed else verdict.corrected_response

    if response is None:
        raise ValueError("LP correction must replace the complete response.")

    return LPGenerationResponse.model_validate(response.model_dump(mode="python"))


def validate_lp_checkpoint_format(root: Path) -> None:
    """Reject unsupported execution evidence without calls, locks, or file changes.

    This preflight authenticates stored execution against its recorded population.
    Generation and finalization additionally compare it with current runtime inputs. A
    valid interrupted native transaction is inspected without repairing its files.

    Parameters
    ----------
    root
        Prospective generation directory; absence permits fresh initialization.

    Raises
    ------
    ValueError
        If existing evidence is unsupported, incomplete, unauthenticated, or invalid.
    """

    evidence_names = _CHECKPOINT_NAMES | {
        _TRANSACTION,
        "as_lc_lp_kg_bundle.json",
        "as_lc_lp_nodes.jsonl",
        "as_lc_lp_relationships.jsonl",
        "lp_final_claims.json",
        "lp_generation_summary.json",
        "lp_relationship_provenance.json",
        "lp_relationships_builds_towards.jsonl",
        "lp_relationships_relates_to.jsonl",
        "lp_unresolved_items.json",
        "lp_validation_report.json",
    }

    if not any(
        (root / name).exists() or (root / name).is_symlink() for name in evidence_names
    ):
        return

    try:
        if (root / _TRANSACTION).exists() or (root / _TRANSACTION).is_symlink():
            transaction = _read_transaction(root)
            receipt = _read_receipt(transaction.next_payloads[_RECEIPT].encode("utf-8"))
            _read_usage(transaction.next_payloads[_USAGE].encode("utf-8"))
        else:
            receipt = _read_receipt((root / _RECEIPT).read_bytes())
            _read_usage((root / _USAGE).read_bytes())

        population = LPRequestPopulation(
            candidates=LPCandidatePopulation(
                candidates=tuple(
                    LPCandidatePair.model_validate_json(line)
                    for line in (root / "lp_candidate_pairs.jsonl")
                    .read_bytes()
                    .splitlines()
                ),
                summary=LPCandidateSummary.model_validate_json(
                    (root / "lp_candidate_summary.json").read_bytes()
                ),
            ),
            manifest=LPRequestManifest.model_validate_json(
                (root / MANIFEST_FILENAME).read_bytes()
            ),
            requests=tuple(
                LPGenerationRequest.model_validate_json(line)
                for line in (root / "lp_generation_requests.jsonl")
                .read_bytes()
                .splitlines()
            ),
        )
        population = read_lp_request_population(expected=population, root=root)
        LPGenerationCheckpoints(
            material=receipt["material"],
            population=population,
            read_only=True,
            root=root,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise ValueError(
            f"LP checkpoint evidence is incompatible at {root}: {error}"
        ) from error
