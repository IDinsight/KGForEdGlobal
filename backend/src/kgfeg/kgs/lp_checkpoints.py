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
import tempfile

from pathlib import Path
from typing import Any, Literal

# Third Party Library
from pydantic import Field

# Package Library
from kgfeg.kgs.lp_requests import LPGenerationRequest, LPRequestPopulation
from kgfeg.kgs.prompts import (
    build_lp_generation_prompt,
    validate_lp_generation_response,
)
from kgfeg.kgs.schemas import LPGenerationResponse, LPGenerationValidationVerdict
from kgfeg.kgs.validators import (
    verify_lp_generation_response_integrity,
    verify_lp_generation_validation_integrity,
)
from kgfeg.schemas import BaseSchema

_FAILURES = "lp_generation_failures.json"
_FILENAMES = {
    "draft": "lp_generation_draft_responses.jsonl",
    "verdict": "lp_generation_validation_verdicts.jsonl",
    "response": "lp_generation_responses.jsonl",
}
_RECEIPT = "lp_generation_checkpoint_manifest.json"
_TRANSACTION = "lp_generation_checkpoint_transaction.json"


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
        self, *, material: dict[str, Any], population: LPRequestPopulation, root: Path
    ) -> None:
        """Load current execution evidence or initialize an empty checkpoint set.

        Parameters
        ----------
        material
            Actual upstream, configuration, prompt-definition, and model identities.
        population
            Fully reconciled on-disk candidate and request population.
        root
            Locked generation directory.
        """

        self.material = material
        self.population = population
        self.root = root
        self.execution_hash = content_hash(material)
        self.rows: dict[str, list[_LPCheckpoint]] = {stage: [] for stage in _FILENAMES}
        self.failures: list[_LPFailure] = []
        self.run_number = 0
        paths = [
            root / name
            for name in (*_FILENAMES.values(), _FAILURES, _RECEIPT, _TRANSACTION)
        ]

        if any(path.exists() for path in paths):
            self._load()
        else:
            self._save()

    def _commit(self, payloads: dict[str, bytes]) -> None:
        """Finish a journaled state replacement and retire its durable transaction.

        Parameters
        ----------
        payloads
            Complete validated artifact and receipt bytes already stored in the journal.
        """

        for name in (*_FILENAMES.values(), _FAILURES, _RECEIPT):
            _atomic_write(path=self.root / name, payload=payloads[name])

        self.verify_bytes()
        (self.root / _TRANSACTION).unlink()
        _sync_directory(self.root)

    def _load(self) -> None:
        """Recover a proven interrupted commit, then validate every persisted byte."""

        if (self.root / _TRANSACTION).exists():
            self._recover()

        self._load_snapshot(
            {
                name: (self.root / name).read_bytes()
                for name in (*_FILENAMES.values(), _FAILURES, _RECEIPT)
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
            If material, schemas, hashes, prefixes, or dispositions are invalid,
            or artifact bytes are not canonical and complete.
        QualityError
            If a stored draft, verdict, or correction fails request integrity.
        """

        receipt = json.loads(payloads[_RECEIPT])
        expected_keys = {
            "artifact_byte_hashes",
            "execution_content_hash",
            "material",
            "run_number",
            "status",
            "stage_counts",
            "failed_pair_ids",
        }

        if not isinstance(receipt, dict) or set(receipt) != expected_keys:
            raise ValueError("LP checkpoint receipt has invalid fields.")

        if (
            receipt["material"] != self.material
            or receipt["execution_content_hash"] != self.execution_hash
        ):
            raise ValueError("LP execution material changed; regenerate before reuse.")

        self.run_number = receipt["run_number"]

        if (
            isinstance(self.run_number, bool)
            or not isinstance(self.run_number, int)
            or self.run_number < 0
        ):
            raise ValueError("LP checkpoint run number is invalid.")

        names = {*_FILENAMES.values(), _FAILURES}

        if set(receipt["artifact_byte_hashes"]) != names:
            raise ValueError("LP checkpoint receipt has incomplete artifact coverage.")

        self._parse_artifacts(payloads=payloads, receipt=receipt)
        self._validate()

        # Canonical byte equality also rejects duplicate JSON keys, non-finite values,
        # blank lines, missing terminal newlines, and schema-normalized alterations.
        if self._receipt(self._payloads()) != receipt:
            raise ValueError("LP checkpoint receipt counts or disposition differ.")

        if payloads[_RECEIPT] != _canonical_bytes(receipt):
            raise ValueError("LP checkpoint receipt is not canonical complete JSON.")

        for name, expected in self._payloads().items():
            if payloads[name] != expected:
                raise ValueError(f"LP checkpoint material is not canonical: {name}.")

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
            else:
                stage = next(key for key, value in _FILENAMES.items() if value == name)
                self.rows[stage] = [
                    _LPCheckpoint.model_validate_json(line)
                    for line in payload.splitlines()
                ]

    def _payloads(self) -> dict[str, bytes]:
        """Serialize the separate canonical stage prefixes and failure history.

        Returns
        -------
        dict[str, bytes]
            Exact artifact payloads covered by the commit receipt.
        """

        return {
            **{
                filename: b"".join(
                    _canonical_bytes(row.model_dump(mode="json"))
                    for row in self.rows[stage]
                )
                for stage, filename in _FILENAMES.items()
            },
            _FAILURES: _canonical_bytes(
                [failure.model_dump(mode="json") for failure in self.failures]
            ),
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
            If the predecessor is incomplete, its receipt is invalid, or its
            artifact hashes have changed.
        """

        names = (*_FILENAMES.values(), _FAILURES, _RECEIPT)

        if not (self.root / _RECEIPT).exists():
            if any((self.root / name).exists() for name in names):
                raise ValueError("LP checkpoint predecessor is incomplete.")

            return dict.fromkeys(names)

        receipt_bytes = (self.root / _RECEIPT).read_bytes()
        receipt = json.loads(receipt_bytes)

        if receipt_bytes != _canonical_bytes(receipt) or set(
            receipt["artifact_byte_hashes"]
        ) != set(names) - {_RECEIPT}:
            raise ValueError("LP checkpoint predecessor receipt is invalid.")

        hashes: dict[str, str | None] = {
            _RECEIPT: hashlib.sha256(receipt_bytes).hexdigest()
        }

        for name, expected in receipt["artifact_byte_hashes"].items():
            actual = hashlib.sha256((self.root / name).read_bytes()).hexdigest()

            if actual != expected:
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
                if failure.exhausted and failure.resolved_run_number is None
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

    def _recover(self) -> None:
        """Roll forward only a validated transaction with exact old/new file states.

        Raises
        ------
        ValueError
            If the journal or intended snapshot is invalid or stale, or an artifact
            matches neither its recorded previous nor next state.
        """

        raw = (self.root / _TRANSACTION).read_bytes()
        transaction = _LPCheckpointTransaction.model_validate_json(raw)
        names = {*_FILENAMES.values(), _FAILURES, _RECEIPT}
        previous = transaction.previous_byte_hashes

        if (
            raw != _canonical_bytes(transaction.model_dump(mode="json"))
            or set(transaction.next_payloads) != names
            or set(previous) != names
            or (
                any(value is None for value in previous.values())
                and not all(value is None for value in previous.values())
            )
        ):
            raise ValueError("LP checkpoint transaction is incomplete or noncanonical.")

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
                hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            )
            following = hashlib.sha256(payloads[name]).hexdigest()

            if actual not in (previous[name], following):
                raise ValueError(
                    f"LP interrupted checkpoint has unexplained material: {name}."
                )

        self._commit(payloads)

    def _save(self) -> None:
        """Validate and durably journal complete state before replacing any files.

        Raises
        ------
        ValueError
            If state or predecessor evidence is invalid, or an unfinished
            transaction already exists.
        """

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
        _atomic_write(
            path=self.root / _TRANSACTION,
            payload=_canonical_bytes(transaction.model_dump(mode="json")),
        )
        self._commit(payloads)

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

        # Execution finishes the earliest request before starting another producer.
        if len(drafts) > len(responses) + 1:
            raise ValueError("LP checkpoints skip an unfinished request.")

        for stage, rows in self.rows.items():
            for index, row in enumerate(rows):
                self._validate_row(index=index, row=row, stage=stage)

        self._validate_failures()

    def _validate_failures(self) -> None:
        """Require ordered bounded attempts and completion-backed dispositions.

        Raises
        ------
        ValueError
            If failure attempts violate request coverage, ordering, retry limits, or
            completion-backed resolution requirements.
        """

        drafts = self.rows["draft"]
        responses = self.rows["response"]
        seen: set[tuple[int, int, str, int]] = set()
        previous_order = (0, -1, -1, 0)
        exhausted_runs: set[int] = set()

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
                or failure.run_number in exhausted_runs
                or failure.attempt > maximum
                or failure.exhausted != (failure.attempt == maximum)
                or index > len(responses)
                or (failure.stage == "verdict" and index >= len(drafts))
            ):
                raise ValueError("LP failure order, retry budget, or stage is invalid.")

            previous_order = order

            if failure.exhausted:
                exhausted_runs.add(failure.run_number)

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

            if resolved is None:
                if failure.resolved_response_content_hash is not None or index < len(
                    responses
                ):
                    raise ValueError(
                        "LP failure is unresolved despite a completed response."
                    )
            elif (
                not failure.run_number <= resolved <= self.run_number
                or index >= len(responses)
                or failure.resolved_response_content_hash
                != responses[index].payload_content_hash
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

        drafts = self.rows["draft"]
        verdicts = self.rows["verdict"]

        if (
            row.stage != stage
            or row.request_index != index
            or row.execution_content_hash != self.execution_hash
            or row.payload_content_hash != content_hash(row.payload)
        ):
            raise ValueError("LP checkpoint order, identity, or content differs.")

        request = self.population.requests[index]
        expected_prerequisite = request.request_content_hash

        if stage == "draft":
            response = LPGenerationResponse.model_validate(row.payload)
            verify_lp_generation_response_integrity(
                lp_generation_request=request, lp_generation_response=response
            )
        else:
            draft = LPGenerationResponse.model_validate(drafts[index].payload)
            expected_prerequisite = content_hash(drafts[index].model_dump(mode="json"))
            verdict = LPGenerationValidationVerdict.model_validate(
                row.payload if stage == "verdict" else verdicts[index].payload
            )
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
                    verdicts[index].model_dump(mode="json")
                )

        if row.prerequisite_content_hash != expected_prerequisite:
            raise ValueError("LP checkpoint prerequisite changed.")

        expected_prompt = _stage_prompt_hash(
            draft=(
                None
                if stage == "draft"
                else LPGenerationResponse.model_validate(drafts[index].payload)
            ),
            material=self.material,
            request=request,
            stage=stage,
        )

        if row.prompt_content_hash != expected_prompt:
            raise ValueError("LP checkpoint prompt differs from current material.")

    def append(self, *, payload: BaseSchema, request_index: int, stage: str) -> None:
        """Validate and commit the next complete result in one stage prefix.

        Parameters
        ----------
        payload
            Complete producer response, checker verdict, or reconciled response.
        request_index
            Exact next request position for this stage.
        stage
            Code-owned draft, verdict, or response stage.

        Raises
        ------
        ValueError
            If the append is noncontiguous, the payload or dependencies are invalid, or
            persisted evidence has changed.
        """

        self.verify_bytes()

        if request_index != len(self.rows[stage]):
            raise ValueError("LP checkpoint append is not the next contiguous row.")

        request = self.population.requests[request_index]
        previous_stage = {"verdict": "draft", "response": "verdict"}.get(stage)
        prerequisite = (
            request.request_content_hash
            if previous_stage is None
            else content_hash(
                self.rows[previous_stage][request_index].model_dump(mode="json")
            )
        )
        draft = (
            None
            if stage == "draft"
            else LPGenerationResponse.model_validate(
                self.rows["draft"][request_index].payload
            )
        )
        material = payload.model_dump(mode="json")
        self.rows[stage].append(
            _LPCheckpoint(
                execution_content_hash=self.execution_hash,
                payload=material,
                payload_content_hash=content_hash(material),
                prerequisite_content_hash=prerequisite,
                prompt_content_hash=_stage_prompt_hash(
                    draft=draft, material=self.material, request=request, stage=stage
                ),
                request_index=request_index,
                stage=stage,
            )
        )

        if stage == "response":
            for failure in self.failures:
                if (
                    failure.request_index == request_index
                    and failure.resolved_run_number is None
                ):
                    failure.resolved_run_number = self.run_number
                    failure.resolved_response_content_hash = content_hash(material)

        self._save()

    def begin_run(self) -> None:
        """Assign a persisted run ordinal so resumed dispositions remain explicit."""

        self.verify_bytes()
        self.run_number += 1
        self._save()

    def record_failure(
        self,
        *,
        attempt: int,
        error: Exception,
        exhausted: bool,
        request_index: int,
        stage: str,
    ) -> None:
        """Preserve processing evidence without placing it in successful prefixes.

        Parameters
        ----------
        attempt
            One-based call attempt within this stage and invocation.
        error
            Processing exception; raw messages are hashed to avoid exposing secrets.
        exhausted
            Whether this attempt exhausted the configured stage retry budget.
        request_index
            Batch whose complete pair population failed this attempt.
        stage
            Producer draft or checker verdict stage.
        """

        self.verify_bytes()
        request = self.population.requests[request_index]
        self.failures.append(
            _LPFailure(
                attempt=attempt,
                error_content_hash=content_hash(str(error)),
                error_type=f"{type(error).__module__}.{type(error).__qualname__}",
                exhausted=exhausted,
                pair_ids=[pair.pair_id for pair in request.pairs],
                request_content_hash=request.request_content_hash,
                request_id=str(request.request_id),
                request_index=request_index,
                run_number=self.run_number,
                stage=stage,
            )
        )
        self._save()

    def verify_bytes(self) -> None:
        """Fail closed if persisted progress changed since this store was validated.

        Raises
        ------
        ValueError
            If persisted artifact bytes differ from the validated in-memory state.
        """

        payloads = self._payloads()
        payloads[_RECEIPT] = _canonical_bytes(self._receipt(payloads))

        for name, payload in payloads.items():
            if (self.root / name).read_bytes() != payload:
                raise ValueError(f"LP checkpoint material changed: {name}.")


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

    names = (
        *_FILENAMES.values(),
        _FAILURES,
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
