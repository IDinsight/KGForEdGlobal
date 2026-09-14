"""Validated LP execution prefixes, material receipts, and separate failure evidence.

Each stage is durably journaled before its files are replaced. Interrupted commits
resume only from exact recorded old/new bytes and a fully validated next state. The
caller holds the exclusive generation lock for the lifetime of this store.
"""

# Future Library
from __future__ import annotations

# Standard Library
import hashlib
import json
import os
import re
import tempfile

from pathlib import Path
from typing import Any, Callable, Literal

# Third Party Library
from pydantic import Field, JsonValue, TypeAdapter
from pydantic_core import to_json

# Package Library
from kgfeg.kgs.lp_candidates import LPCandidatePopulation
from kgfeg.kgs.lp_requests import (
    MANIFEST_FILENAME,
    LPGenerationRequest,
    LPRequestManifest,
    LPRequestPopulation,
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


class LPGenerationCheckpoints:
    """Persist separate success prefixes and auditable processing attempts.

    The store verifies exact artifact bytes, request alignment, stage prerequisites,
    complete corrections, and failure dispositions before allowing any reuse.
    Completion denotes processing coverage only, never graph or semantic validity.
    """

    def __init__(
        self,
        *,
        execution_check: Callable[[], None] | None = None,
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
            Optional immutable-input assertion immediately before writer publication.
        material
            Actual upstream, configuration, prompt-definition, and model identities.
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
        self._record_encodings: dict[tuple[str, int], tuple[bytes, bytes]] = {}
        self._row_proofs: dict[tuple[str, int], tuple[bytes, ...]] = {}
        self._validated_byte_hashes: tuple[tuple[str, bytes], ...] = ()
        self.execution_check = execution_check
        self.ownership_check = ownership_check
        self.pending: dict[str, dict[int, _LPCheckpoint]] = {
            stage: {} for stage in _FILENAMES
        }
        self.attempts: list[_LPAttempt] = []
        self.material = material
        self.population = population
        self.root = root
        self.execution_hash = content_hash(material)
        self.rows: dict[str, list[_LPCheckpoint]] = {stage: [] for stage in _FILENAMES}
        self.failures: list[_LPFailure] = []
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
            self._save()

    def _commit(self, payloads: dict[str, bytes]) -> None:
        """Finish a journaled state replacement and retire its durable transaction.

        Parameters
        ----------
        payloads
            Complete validated artifact and receipt bytes already stored in the journal.
        """

        for name in payloads:
            self.verify_execution_authority()
            _atomic_write(path=self.root / name, payload=payloads[name])

        self.verify_bytes()
        self.verify_execution_authority()
        (self.root / _TRANSACTION).unlink()
        _sync_directory(self.root)

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

        self.verify_bytes()

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
            receipt["material"] != self.material
            or receipt["execution_content_hash"] != self.execution_hash
            or self.material["request_manifest"]
            != self.population.manifest.model_dump(mode="json")
        ):
            raise ValueError("LP execution material changed; regenerate before reuse.")

        self.run_number = receipt["run_number"]

        if (
            isinstance(self.run_number, bool)
            or not isinstance(self.run_number, int)
            or self.run_number < 0
        ):
            raise ValueError("LP checkpoint run number is invalid.")

        if set(payloads) != _CHECKPOINT_NAMES:
            raise ValueError("LP checkpoint receipt has incomplete artifact coverage.")

        self._parse_artifacts(payloads=payloads, receipt=receipt)
        self._validate()

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

                self.failures = [_LPFailure.model_validate(item) for item in raw]
            elif name == _PENDING:
                self._parse_pending(payload)
            elif name == _USAGE:
                raw = _read_usage(payload)
                if raw["max_concurrent_requests"] != self.capacity:
                    raise ValueError(
                        "LP usage capacity differs from execution material."
                    )
                self.attempts = [
                    _LPAttempt.model_validate(row) for row in raw["attempts"]
                ]
            else:
                stage = next(key for key, value in _FILENAMES.items() if value == name)
                self.rows[stage] = [
                    _LPCheckpoint.model_validate_json(line)
                    for line in payload.splitlines()
                ]

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

        self.pending = {}

        for stage, records in raw.items():
            parsed = [_LPCheckpoint.model_validate(row) for row in records]

            if [row.request_index for row in parsed] != sorted(
                {row.request_index for row in parsed}
            ):
                raise ValueError("LP pending journal has duplicate or unordered rows.")

            self.pending[stage] = {row.request_index: row for row in parsed}

    def _payloads(self) -> dict[str, bytes]:
        """Serialize the separate canonical stage prefixes and failure history.

        Returns
        -------
        dict[str, bytes]
            Exact artifact payloads covered by the commit receipt.
        """

        pending = {
            stage: b"["
            + b",".join(
                self._record_bytes(key=(stage, index), record=rows[index])
                for index in sorted(rows)
            )
            + b"]"
            for stage, rows in self.pending.items()
        }
        attempts = (
            b"["
            + b",".join(
                self._record_bytes(key=("attempt", index), record=attempt)
                for index, attempt in enumerate(self.attempts)
            )
            + b"]"
        )
        usage = {
            "attempts": attempts,
            "max_concurrent_requests": _canonical_bytes(self.capacity).rstrip(b"\n"),
            "available_cost": b"null",
            "request_states": _canonical_bytes(self._request_states()).rstrip(b"\n"),
            "unknown_usage_attempts": str(
                sum(attempt.usage is None for attempt in self.attempts)
            ).encode("ascii"),
        }
        payloads = {
            _PENDING: _encoded_object(pending),
            _USAGE: _encoded_object(usage),
            **{
                filename: b"".join(
                    self._record_bytes(key=(stage, index), record=row) + b"\n"
                    for index, row in enumerate(self.rows[stage])
                )
                for stage, filename in _FILENAMES.items()
            },
            _FAILURES: b"["
            + b",".join(
                self._record_bytes(key=("failure", index), record=failure)
                for index, failure in enumerate(self.failures)
            )
            + b"]\n",
        }

        # Drop retired/replaced positions; history size, not save count, bounds memory.
        keys = {
            (stage, index)
            for stage in _FILENAMES
            for index in (*range(len(self.rows[stage])), *self.pending[stage])
        }
        keys.update(("attempt", index) for index in range(len(self.attempts)))
        keys.update(("failure", index) for index in range(len(self.failures)))
        self._record_encodings = {
            key: value for key, value in self._record_encodings.items() if key in keys
        }
        return payloads

    def _previous_hashes(self) -> dict[str, str | None]:
        """Identify the committed predecessor before starting another transaction.

        Returns
        -------
        dict[str, str or None]
            Verified file hashes, or explicit absence for an entirely fresh store.

        Raises
        ------
        ValueError
            If the predecessor is incomplete, its receipt is invalid, or its artifact
            hashes have changed.
        """

        names = (*self._payloads(), _RECEIPT)

        if not (self.root / _RECEIPT).exists():
            if any((self.root / name).exists() for name in names):
                raise ValueError("LP checkpoint predecessor is incomplete.")

            return dict.fromkeys(names)

        receipt_bytes = (self.root / _RECEIPT).read_bytes()
        receipt = _read_receipt(receipt_bytes)
        _read_usage((self.root / _USAGE).read_bytes())

        hashes: dict[str, str | None] = {
            _RECEIPT: hashlib.sha256(receipt_bytes).hexdigest()
        }

        for name, expected_digest in receipt["artifact_byte_hashes"].items():
            actual = hashlib.sha256((self.root / name).read_bytes()).hexdigest()

            if actual != expected_digest:
                raise ValueError(f"LP checkpoint predecessor changed: {name}.")

            hashes[name] = actual

        return hashes

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

        failed = sorted(
            {
                pair_id
                for failure in self.failures
                if failure.resolved_run_number is None
                for pair_id in failure.pair_ids
            }
        )
        return {
            "artifact_byte_hashes": {
                name: hashlib.sha256(payload).hexdigest()
                for name, payload in payloads.items()
            },
            "execution_content_hash": self.execution_hash,
            "failed_pair_ids": failed,
            "material": self.material,
            "run_number": self.run_number,
            "stage_counts": {stage: len(rows) for stage, rows in self.rows.items()},
            "status": (
                "failed"
                if failed
                else (
                    "completed"
                    if len(self.rows["response"]) == len(self.population.requests)
                    else "incomplete"
                )
            ),
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
            self._commit(payloads)

    def _record_bytes(self, *, key: tuple[str, int], record: BaseSchema) -> bytes:
        """Reuse canonical encoding only after checking the complete live record.

        Parameters
        ----------
        key
            Store-local collection and position, never an authority by itself.
        record
            Current record, including every nested payload or usage value.

        Returns
        -------
        bytes
            Canonical JSON without its file-level terminal newline.
        """

        # Validate before JSON-mode coercion: an invalid datetime/tuple/object must not
        # inherit a prior proof merely by encoding like a valid JSON value.
        material = _JSON_RECORD.validate_python(
            record.model_dump(mode="python", warnings="error"), strict=True
        )
        live = hashlib.sha256(
            to_json(inf_nan_mode="constants", value=material)
        ).digest()
        previous = self._record_encodings.get(key)

        if previous is not None and previous[0] == live:
            return previous[1]

        encoded = _canonical_bytes(material)[:-1]
        self._record_encodings[key] = (live, encoded)
        return encoded

    def _request_states(self) -> list[dict[str, Any]]:
        """Describe unfinished, unknown, cancelled and unstarted work explicitly.

        Returns
        -------
        list[dict[str, Any]]
            Population-aligned processing states, independent of semantic judgments.
        """

        last_attempt = {attempt.request_index: attempt for attempt in self.attempts}
        failed = {
            failure.request_index
            for failure in self.failures
            if failure.resolved_run_number is None
        }
        states = []

        for index, request in enumerate(self.population.requests):
            finished = [
                stage
                for stage in _FILENAMES
                if self.get_row(request_index=index, stage=stage) is not None
            ]
            attempt = last_attempt.get(index)

            if "response" in finished:
                state = "completed"
            elif index in failed:
                state = "failed"
            elif attempt is None:
                state = "unfinished" if finished else "not_started"
            else:
                state = {
                    "dispatched": "dispatch_outcome_pending",
                    "unknown": "unknown",
                    "cancelled": "cancelled",
                }.get(attempt.status, "unfinished")
            states.append(
                {
                    "completed_stages": finished,
                    "request_id": str(request.request_id),
                    "request_index": index,
                    "state": state,
                }
            )

        return states

    def _save(self) -> None:
        """Validate and durably journal complete state before replacing any files.

        Raises
        ------
        ValueError
            If state or predecessor evidence is invalid, or an unfinished
            transaction already exists.
        """

        self.verify_execution_authority()
        self._validate()
        payloads = self._payloads()
        payloads[_RECEIPT] = _canonical_bytes(self._receipt(payloads))

        if (self.root / _TRANSACTION).exists():
            raise ValueError("LP checkpoint has an unfinished transaction; reload it.")

        transaction = _LPCheckpointTransaction(
            next_payloads={
                name: payload.decode("utf-8") for name, payload in payloads.items()
            },
            previous_byte_hashes=self._previous_hashes(),
        )
        self.verify_execution_authority()
        self._validated_byte_hashes = tuple(
            (name, hashlib.sha256(payloads[name]).digest()) for name in sorted(payloads)
        )
        _atomic_write(
            path=self.root / _TRANSACTION,
            payload=_canonical_bytes(transaction.model_dump(mode="json")),
        )
        self._commit(payloads)

    def _store_result(
        self, *, payload: BaseSchema, request_index: int, stage: str
    ) -> _LPCheckpoint:
        """Construct and validate one pending result with exact saved dependencies.

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
            Pending result ready for atomic publication with attempt evidence.

        Raises
        ------
        ValueError
            If the stage is already durable, lacks its prerequisite, or the new row
            fails validation.
        """

        if self.get_row(request_index=request_index, stage=stage) is not None:
            raise ValueError("LP stage completion is already durable.")

        request = self.population.requests[request_index]
        previous_stage = {"verdict": "draft", "response": "verdict"}.get(stage)
        previous = (
            None
            if previous_stage is None
            else self.get_row(request_index=request_index, stage=previous_stage)
        )

        if previous_stage is not None and previous is None:
            raise ValueError("LP stage lacks its durable dependency.")

        draft_row = self.get_row(request_index=request_index, stage="draft")
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
                material=self.material,
                request=request,
                stage=stage,
            ),
            request_index=request_index,
            stage=stage,
        )
        self._validate_row(index=request_index, row=row, stage=stage)
        self.pending[stage][request_index] = row
        return row

    def _validate(self) -> None:
        """Require complete stage-specific contiguous prefixes and honest failures.

        Raises
        ------
        ValueError
            If stage prefixes, row dependencies, or failure dispositions are invalid.
        """

        drafts = self.rows["draft"]
        verdicts = self.rows["verdict"]
        responses = self.rows["response"]

        if (
            not len(responses)
            <= len(verdicts)
            <= len(drafts)
            <= len(self.population.requests)
        ):
            raise ValueError("LP checkpoint stage lengths are misaligned.")

        material = hashlib.sha256(
            _canonical_bytes(
                {
                    "execution_hash": self.execution_hash,
                    "material": self.material,
                }
            )
        ).digest()
        requests: dict[int, bytes] = {}
        proofs: dict[tuple[str, int], tuple[bytes, ...]] = {}

        for stage, rows in self.rows.items():
            for index, row in enumerate(rows):
                self._validate_cached_row(
                    index=index,
                    material=material,
                    proofs=proofs,
                    requests=requests,
                    row=row,
                    stage=stage,
                )

        pending_indices = set()

        for stage, pending_rows in self.pending.items():
            for index, row in pending_rows.items():
                if not len(self.rows[stage]) <= index < len(self.population.requests):
                    raise ValueError(
                        "LP pending completion overlaps a prefix or population boundary."
                    )

                self._validate_cached_row(
                    index=index,
                    material=material,
                    proofs=proofs,
                    requests=requests,
                    row=row,
                    stage=stage,
                )
                pending_indices.add(index)

        if len(pending_indices) > self.capacity:
            raise ValueError(
                "LP pending completions exceed the admitted request capacity."
            )

        self._validate_failures()
        self._validate_attempts()
        self._row_proofs = proofs

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
            row = self.get_row(request_index=index, stage=attempt.stage)
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
            for failure in self.failures
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

    def _validate_attempts(self) -> None:
        """Require unique bounded attempts, honest usage, and exact completion links.

        Raises
        ------
        ValueError
            If an LP result is invalid.
        """

        if (
            sum(attempt.status == "dispatched" for attempt in self.attempts)
            > self.capacity
        ):
            raise ValueError("LP active dispatch records exceed capacity.")

        seen: dict[tuple[int, int, str, int], str] = {}
        successful: set[tuple[int, str]] = set()

        for attempt in self.attempts:
            index = attempt.request_index
            key = (attempt.run_number, index, attempt.stage, attempt.attempt)

            if (
                key in seen
                or (
                    attempt.stage == "verdict"
                    and self.get_row(request_index=index, stage="draft") is None
                )
                or index >= len(self.population.requests)
                or attempt.run_number > self.run_number
                or attempt.attempt > self.material["retry_limits"][attempt.stage] + 1
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
            indices = set(range(len(self.rows[stage]))) | set(self.pending[stage])

            if any((index, stage) not in successful for index in indices):
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
            for failure in self.failures
        ):
            raise ValueError(
                "LP failure lacks its durable dispatch and outcome record."
            )

    def _validate_cached_row(
        self,
        *,
        index: int,
        material: bytes,
        proofs: dict[tuple[str, int], tuple[bytes, ...]],
        requests: dict[int, bytes],
        row: _LPCheckpoint,
        stage: str,
    ) -> None:
        """Revalidate changed rows and every changed semantic dependency.

        Parameters
        ----------
        index
            Expected request position in the prefix or pending journal.
        material
            Digest of actual execution material computed for this validation pass.
        proofs
            New proof set, installed only after the complete state validates.
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

        dependencies = []

        for dependency in ("draft", "verdict") if stage != "draft" else ():
            saved = (
                row
                if dependency == stage
                else self.get_row(request_index=index, stage=dependency)
            )
            dependencies.append(
                b""
                if saved is None
                else self._record_bytes(key=(dependency, index), record=saved)
            )
        key = (stage, index)
        proof = (
            material,
            requests[index],
            self._record_bytes(key=key, record=row),
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

        for failure in self.failures:
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
            maximum = self.material["retry_limits"][failure.stage] + 1

            if (
                order <= previous_order
                or failure.attempt > maximum
                or failure.exhausted != (failure.attempt == maximum)
                or (
                    failure.stage == "verdict"
                    and self.get_row(request_index=index, stage="draft") is None
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
            response = self.get_row(request_index=index, stage="response")

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

        draft_row = self.get_row(request_index=index, stage="draft")
        verdict_row = (
            row
            if stage == "verdict"
            else self.get_row(request_index=index, stage="verdict")
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
            material=self.material,
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

        self.verify_bytes()

        if stage != "response":
            raise ValueError("LP call results require atomic attempt accounting.")

        self._store_result(payload=payload, request_index=request_index, stage=stage)

        for failure in self.failures:
            if (
                failure.request_index == request_index
                and failure.resolved_run_number is None
            ):
                failure.resolved_run_number = self.run_number
                failure.resolved_response_content_hash = content_hash(
                    payload.model_dump(mode="json")
                )

        self.promote()

    def begin_run(self) -> None:
        """Retain interrupted call identities and assign a new persisted invocation."""

        self.verify_bytes()

        for attempt in self.attempts:
            if attempt.status == "dispatched":
                attempt.status = "unknown"

        self.run_number += 1
        self._save()

    @property
    def capacity(self) -> int:
        """Return the immutable effective capacity captured for this execution."""

        capacity = self.material.get("max_concurrent_requests")

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

        self.verify_bytes()
        attempt = self.attempts[attempt_index]

        if attempt.status != "dispatched":
            raise ValueError("LP attempt already has an observed outcome.")

        attempt.status = status
        attempt.usage = usage

        if error is not None:
            attempt.error_type = f"{type(error).__module__}.{type(error).__qualname__}"
            attempt.error_content_hash = content_hash(str(error))

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
            attempt.checkpoint_content_hash = content_hash(row.model_dump(mode="json"))
        elif status == "failed":
            if error is None or payload is not None:
                raise ValueError("LP failed attempt requires failure evidence only.")

            request = self.population.requests[attempt.request_index]
            self.failures.append(
                _LPFailure(
                    attempt=attempt.attempt,
                    error_content_hash=content_hash(str(error)),
                    error_type=f"{type(error).__module__}.{type(error).__qualname__}",
                    exhausted=attempt.attempt
                    == self.material["retry_limits"][attempt.stage] + 1,
                    pair_ids=[pair.pair_id for pair in request.pairs],
                    request_content_hash=request.request_content_hash,
                    request_id=str(request.request_id),
                    request_index=attempt.request_index,
                    run_number=self.run_number,
                    stage=attempt.stage,
                )
            )
            self.failures.sort(
                key=lambda failure: (
                    failure.run_number,
                    failure.request_index,
                    0 if failure.stage == "draft" else 1,
                    failure.attempt,
                )
            )
        elif status not in {"unknown", "cancelled"} or payload is not None:
            raise ValueError("LP attempt outcome is invalid.")

        # Saving precedes promotion, so a crash never discards a valid suffix.
        self._save()
        self.promote()

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
            Durable stage evidence, or absence without an invented success.
        """

        if request_index < len(self.rows[stage]):
            return self.rows[stage][request_index]

        return self.pending[stage].get(request_index)

    def promote(self) -> None:
        """Promote newly contiguous stages without losing saved suffix completions."""

        for stage in _FILENAMES:
            pending = self.pending[stage]

            while len(self.rows[stage]) in pending:
                self.rows[stage].append(pending.pop(len(self.rows[stage])))

        self._save()

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

        self.verify_bytes()

        if self.get_row(request_index=request_index, stage=stage) is not None:
            raise ValueError("LP dispatch would repeat a durably completed stage.")

        prior = [
            item
            for item in self.attempts
            if item.run_number == self.run_number
            and item.request_index == request_index
            and item.stage == stage
        ]

        if any(item.status != "failed" for item in prior):
            raise ValueError("LP dispatch duplicates an active or unknown attempt.")

        attempt = len(prior) + 1

        if attempt > self.material["retry_limits"][stage] + 1:
            raise ValueError("LP dispatch exceeds the configured retry budget.")

        self.attempts.append(
            _LPAttempt(
                attempt=attempt,
                request_id=str(self.population.requests[request_index].request_id),
                request_index=request_index,
                run_number=self.run_number,
                stage=stage,
                status="dispatched",
            )
        )
        self._save()
        return len(self.attempts) - 1

    def verify_bytes(self) -> None:
        """Fail closed if persisted progress changed since this store was validated.

        Raises
        ------
        ValueError
            If persisted artifact bytes differ from the validated in-memory state.
        """

        self.verify_ownership()
        payloads = self._payloads()
        payloads[_RECEIPT] = _canonical_bytes(self._receipt(payloads))

        # Disk and live records cannot jointly replace the last validated snapshot.
        # Only a fully validated load/save can establish a new byte authority.
        current = tuple(
            (name, hashlib.sha256(payloads[name]).digest()) for name in sorted(payloads)
        )

        if current != self._validated_byte_hashes:
            raise ValueError(
                "LP in-memory checkpoint material changed after validation."
            )

        for name, payload in payloads.items():
            if (self.root / name).read_bytes() != payload:
                raise ValueError(f"LP checkpoint material changed: {name}.")

    def verify_execution_authority(self) -> None:
        """Recheck immutable inputs and ownership before any durable publication."""

        self.verify_ownership()

        if self.execution_check is not None:
            self.execution_check()

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
