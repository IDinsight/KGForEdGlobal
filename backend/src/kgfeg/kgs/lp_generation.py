"""Resume bounded concurrent LP requests with sole-writer durable stage evidence.

A bounded window supplies backpressure even when the earliest request stalls. Workers
return untrusted outcomes and local usage; only the coordinator writes checkpoint,
pending completion, failure and usage state.
"""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import fcntl
import hashlib
import os

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Literal

# Third Party Library
from loguru import logger

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import prompts
from kgfeg.kgs.llm import KGUsageTracker, generate_learning_progressions_for_request
from kgfeg.kgs.lp_checkpoints import (
    LPGenerationCheckpoints,
    archive_lp_generation_artifacts,
    content_hash,
    reconciled_response,
    validate_lp_checkpoint_format,
)
from kgfeg.kgs.lp_dispatch import (
    LPDispatchClosed,
    LPDispatchGate,
    LPDispatchIntegrityError,
)
from kgfeg.kgs.lp_requests import (
    MANIFEST_FILENAME,
    LPRequestPopulation,
    build_lp_generation_requests,
    read_lp_request_population,
    write_lp_generation_request_artifacts,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPGenerationResponse,
    LPGenerationValidationVerdict,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.kgs.validators import (
    verify_lp_generation_response_integrity,
    verify_lp_generation_validation_integrity,
)
from kgfeg.model_registry import ModelConfig
from kgfeg.schemas import CreateKGConfig


class LPGenerationFailed(RuntimeError):
    """A stage exhausted its retries; LP processing cannot report success."""


@dataclass
class _LPAttemptOutcome:
    """A worker's observed result and isolated usage, without shared file writes."""

    error: BaseException | None
    payload: LPGenerationResponse | LPGenerationValidationVerdict | None
    usage_tracker: KGUsageTracker


class _LPDirectoryOwnership:
    """Retain the original lock descriptor, inode and directory identity."""

    def __init__(self, *, lock: BinaryIO, root: Path) -> None:
        """Capture the exclusively locked directory identity.

        Parameters
        ----------
        lock
            Already exclusively locked open file description.
        root
            Generation directory containing that lock.
        """

        self.lock = lock
        self.root = root
        self.directory_identity = self._identity(root.stat())
        self.lock_identity = self._identity(os.fstat(lock.fileno()))

    @staticmethod
    def _identity(stat: os.stat_result) -> tuple[int, int]:
        """Return a filesystem identity without content or timestamp heuristics.

        Parameters
        ----------
        stat
            Captured file or directory stat.

        Returns
        -------
        tuple[int, int]
            Device and inode identifiers.
        """

        return stat.st_dev, stat.st_ino

    def verify(self) -> None:
        """Fail closed on a replaced, closed, or no-longer-exclusive lock.

        Raises
        ------
        ValueError
            If the directory or lock identity has changed, or if the exclusive lock is
            no longer held.
        """

        path = self.root / ".lp_generation.lock"

        if (
            self._identity(self.root.stat()) != self.directory_identity
            or self._identity(os.fstat(self.lock.fileno())) != self.lock_identity
            or self._identity(path.stat()) != self.lock_identity
        ):
            raise ValueError("LP generation directory ownership was lost.")

        with path.open("rb") as probe:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return

            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)

        raise ValueError("LP generation exclusive lock is no longer held.")


def _admit_lp_requests(
    *,
    active: dict[Future[_LPAttemptOutcome], tuple[int, int]],
    executor: ThreadPoolExecutor,
    gate: LPDispatchGate,
    kg_config: CreateKGConfig,
    model_config: ModelConfig,
    population: LPRequestPopulation,
    store: LPGenerationCheckpoints,
) -> None:
    """Admit eligible stages in deterministic order within a bounded request window.

    Parameters
    ----------
    active
        Bounded calls awaiting coordinator observation.
    executor
        Fixed worker pool with at most one submitted call per admitted request.
    gate
        Per-run dispatch permission.
    kg_config
        Frozen curriculum configuration.
    model_config
        Frozen shared KG model.
    population
        Complete materialized request population.
    store
        Sole checkpoint writer and durable stage authority.
    """

    _verify_execution_material(kg_config=kg_config, population=population, store=store)
    start = len(store.rows["response"])
    active_indices = {index for index, _ in active.values()}

    # Completed suffixes keep their place in this bounded window until the earliest
    # hole closes. No population-sized future queue exists.
    for index in range(start, min(start + store.capacity, len(population.requests))):
        if index in active_indices:
            continue

        _reconcile_lp_request(request_index=index, store=store)

        if store.get_row(request_index=index, stage="response") is not None:
            continue

        # A worker may have returned while its peer was being persisted.
        if any(future.done() for future in active):
            break

        _verify_execution_material(
            kg_config=kg_config, population=population, store=store
        )
        draft_row = store.get_row(request_index=index, stage="draft")
        draft = (
            None
            if draft_row is None
            else LPGenerationResponse.model_validate(draft_row.payload)
        )
        stage = "draft" if draft is None else "verdict"
        attempt_index = store.record_dispatch(request_index=index, stage=stage)
        logger.info(
            f"LP attempt dispatched: request={index + 1}/{len(population.requests)}; "
            f"stage={stage}; attempt={store.attempts[attempt_index].attempt}; "
            f"capacity={store.capacity}"
        )
        future = executor.submit(
            _execute_lp_attempt,
            draft=draft,
            gate=gate,
            kg_config=kg_config,
            model_config=model_config,
            population=population,
            request_index=index,
        )
        active[future] = (index, attempt_index)


def _drain_lp_outcomes(
    *,
    active: dict[Future[_LPAttemptOutcome], tuple[int, int]],
    integrity_error: BaseException | None,
    kg_config: CreateKGConfig,
    population: LPRequestPopulation,
    store: LPGenerationCheckpoints,
    usage_tracker: KGUsageTracker,
) -> BaseException | None:
    """Drain active calls while forbidding writes after persistence or ownership loss.

    Parameters
    ----------
    active
        Bounded call set admitted before dispatch closed.
    integrity_error
        Prior persistence, input, or ownership failure, if any.
    kg_config
        Frozen effective policy.
    population
        Complete materialized request population.
    store
        Sole durable writer, usable only while its integrity remains established.
    usage_tracker
        Invocation-local counters retain observed usage even if persistence fails.

    Returns
    -------
    BaseException or None
        First integrity failure, without manufacturing reusable successful evidence.
    """

    for future, (_, attempt_index) in list(active.items()):
        outcome = future.result()

        if integrity_error is None:
            try:
                _retain_lp_outcome(
                    attempt_index=attempt_index,
                    kg_config=kg_config,
                    outcome=outcome,
                    population=population,
                    store=store,
                    usage_tracker=usage_tracker,
                )
                _reconcile_lp_request(
                    request_index=store.attempts[attempt_index].request_index,
                    store=store,
                )
            except BaseException as error:  # pylint: disable=broad-exception-caught
                integrity_error = error
        else:
            _merge_lp_usage(outcome=outcome, target=usage_tracker)

    return integrity_error


def _execute_lp_attempt(
    *,
    draft: LPGenerationResponse | None,
    gate: LPDispatchGate,
    kg_config: CreateKGConfig,
    model_config: ModelConfig,
    population: LPRequestPopulation,
    request_index: int,
) -> _LPAttemptOutcome:
    """Return one stage outcome and local accounting without mutating shared state.

    Parameters
    ----------
    draft
        This request's durably validated producer result, if checking.
    gate
        Run-wide dispatch gate shared with the sole writer.
    kg_config
        Frozen effective curriculum policy.
    model_config
        Frozen shared KG model configuration.
    population
        Complete validated pre-call request population.
    request_index
        Admitted request position within the bounded window.

    Returns
    -------
    _LPAttemptOutcome
        Validated result or observed exception, plus isolated available usage.
    """

    tracker = KGUsageTracker()
    request = population.requests[request_index]

    try:
        gate.check()

        # Each bounded worker owns an event loop and HTTP client. The public
        # synchronous call boundary remains injectable for independent
        # barrier-controlled tests.
        with asyncio.Runner() as runner:
            runner.get_loop()
            output = generate_learning_progressions_for_request(
                dispatch_guard=gate.check,
                draft=draft,
                kg_config=kg_config.model_copy(deep=True),
                model_config=model_config.model_copy(deep=True),
                request=request.model_copy(deep=True),
                usage_tracker=tracker,
            )

        if draft is None:
            payload = LPGenerationResponse.model_validate(
                output.model_dump(mode="python")
            )
            verify_lp_generation_response_integrity(
                lp_generation_request=request, lp_generation_response=payload
            )
        else:
            payload = LPGenerationValidationVerdict.model_validate(
                output.model_dump(mode="python")
            )
            verify_lp_generation_validation_integrity(
                draft_response=draft,
                lp_generation_request=request,
                validation_verdict=payload,
            )

        return _LPAttemptOutcome(error=None, payload=payload, usage_tracker=tracker)
    except BaseException as error:  # pylint: disable=broad-exception-caught
        return _LPAttemptOutcome(error=error, payload=None, usage_tracker=tracker)


def _finish_lp_requests(  # pylint: disable=too-complex
    *,
    kg_config: CreateKGConfig,
    model_config: ModelConfig,
    population: LPRequestPopulation,
    store: LPGenerationCheckpoints,
    usage_tracker: KGUsageTracker,
) -> None:
    """Coordinate bounded stage dispatch, durable outcomes, and active-call shutdown.

    Parameters
    ----------
    kg_config
        Frozen policy and retry limits.
    model_config
        Frozen shared production model.
    population
        Fully materialized request population.
    store
        Sole writer of all durable execution state.
    usage_tracker
        Run-local aggregate updated only by this coordinator.
    """

    gate = LPDispatchGate(
        check_material=partial(
            _verify_execution_material,
            kg_config=kg_config,
            population=population,
            store=store,
        )
    )
    active: dict[Future[_LPAttemptOutcome], tuple[int, int]] = {}
    stopped: BaseException | None = None
    integrity_error: BaseException | None = None
    capacity = kg_config.learning_progressions.max_concurrent_requests
    usage_tracker.lp_max_concurrent_requests = capacity

    with ThreadPoolExecutor(
        max_workers=capacity, thread_name_prefix="lp-request"
    ) as executor:
        try:
            while True:
                with gate.coordinate():
                    # Observe every already-returned result before launching a
                    # follow-up. One exhausted outcome closes transport dispatch before
                    # any slow disk I/O.
                    ready = [future for future in active if future.done()]
                    outcomes = [(future, future.result()) for future in ready]
                    stopped = _observe_lp_failures(
                        active=active,
                        gate=gate,
                        outcomes=outcomes,
                        population=population,
                        stopped=stopped,
                        store=store,
                    )

                    for future, outcome in outcomes:
                        index, attempt_index = active.pop(future)
                        _retain_lp_outcome(
                            attempt_index=attempt_index,
                            kg_config=kg_config,
                            outcome=outcome,
                            population=population,
                            store=store,
                            usage_tracker=usage_tracker,
                        )
                        _reconcile_lp_request(request_index=index, store=store)

                    if stopped is not None:
                        if not active:
                            break
                    else:
                        _admit_lp_requests(
                            active=active,
                            executor=executor,
                            gate=gate,
                            kg_config=kg_config,
                            model_config=model_config,
                            population=population,
                            store=store,
                        )
                        if not active and len(store.rows["response"]) == len(
                            population.requests
                        ):
                            break

                if active:
                    wait(fs=active, return_when=FIRST_COMPLETED)
        except BaseException as error:  # pylint: disable=broad-exception-caught
            # Integrity or persistence loss forbids any further publication. Durable
            # dispatch identities explicitly remain unknown on recovery.
            gate.close()
            integrity_error = error
        finally:
            gate.close()
            integrity_error = _drain_lp_outcomes(
                active=active,
                integrity_error=integrity_error,
                kg_config=kg_config,
                population=population,
                store=store,
                usage_tracker=usage_tracker,
            )

    if integrity_error is not None:
        raise integrity_error

    if stopped is not None:
        raise stopped


def _merge_lp_usage(*, outcome: _LPAttemptOutcome, target: KGUsageTracker) -> None:
    """Aggregate one isolated worker's observed usage exactly once on the writer.

    Parameters
    ----------
    outcome
        Per-attempt counters and outcome with no shared mutation.
    target
        Invocation-local KG usage buckets.
    """

    source = outcome.usage_tracker

    if (
        not source.lp_generation.requests
        and not source.lp_generation_validation.requests
        and not isinstance(outcome.error, (LPDispatchClosed, LPDispatchIntegrityError))
    ):
        target.lp_unknown_usage_attempts += 1

    for name in ("lp_generation", "lp_generation_validation"):
        incoming = getattr(source, name)
        aggregate = getattr(target, name)

        for field in (
            "cache_read_tokens",
            "cache_write_tokens",
            "input_tokens",
            "output_tokens",
            "requests",
            "runs",
        ):
            setattr(
                aggregate, field, getattr(aggregate, field) + getattr(incoming, field)
            )


def _observe_lp_failures(
    *,
    active: dict[Future[_LPAttemptOutcome], tuple[int, int]],
    gate: LPDispatchGate,
    outcomes: list[tuple[Future[_LPAttemptOutcome], _LPAttemptOutcome]],
    population: LPRequestPopulation,
    stopped: BaseException | None,
    store: LPGenerationCheckpoints,
) -> BaseException | None:
    """Close dispatch before saving any member of a returned result batch.

    Parameters
    ----------
    active
        Bounded outstanding call identities.
    gate
        Shutdown boundary shared with transport dispatch.
    outcomes
        All already-returned outcomes observed in this coordinator iteration.
    population
        Authoritative request identities.
    stopped
        First terminal failure already observed, if any.
    store
        Durable attempt identities and immutable retry budgets.

    Returns
    -------
    BaseException or None
        First terminal failure, preserving interruption separately from model failure.
    """

    for future, outcome in outcomes:
        index, attempt_index = active[future]
        attempt = store.attempts[attempt_index]

        if outcome.error is not None and (
            not isinstance(outcome.error, Exception)
            or isinstance(outcome.error, LPDispatchIntegrityError)
            or attempt.attempt == store.material["retry_limits"][attempt.stage] + 1
        ):
            gate.close()

            if stopped is None:
                stopped = (
                    outcome.error
                    if not isinstance(outcome.error, Exception)
                    or isinstance(outcome.error, LPDispatchIntegrityError)
                    else LPGenerationFailed(
                        f"LP {attempt.stage} failed for request "
                        f"{population.requests[index].request_id}; processing halted."
                    )
                )

    return stopped


def _reconcile_lp_request(
    *, request_index: int, store: LPGenerationCheckpoints
) -> None:
    """Finish a saved checker locally without requiring another external call.

    Parameters
    ----------
    request_index
        Request whose durable dependencies may now be complete.
    store
        Sole checkpoint writer.
    """

    draft = store.get_row(request_index=request_index, stage="draft")
    verdict = store.get_row(request_index=request_index, stage="verdict")

    if (
        draft is not None
        and verdict is not None
        and store.get_row(request_index=request_index, stage="response") is None
    ):
        store.append(
            payload=reconciled_response(
                draft=LPGenerationResponse.model_validate(draft.payload),
                verdict=LPGenerationValidationVerdict.model_validate(verdict.payload),
            ),
            request_index=request_index,
            stage="response",
        )


def _retain_lp_outcome(
    *,
    attempt_index: int,
    kg_config: CreateKGConfig,
    outcome: _LPAttemptOutcome,
    population: LPRequestPopulation,
    store: LPGenerationCheckpoints,
    usage_tracker: KGUsageTracker,
) -> None:
    """Persist validated post-call material and all observed accounting atomically.

    Parameters
    ----------
    attempt_index
        Durable dispatch identity within the usage journal.
    kg_config
        Frozen effective policy.
    outcome
        Worker's isolated observed result.
    population
        Complete materialized request authority.
    store
        Sole durable writer.
    usage_tracker
        Invocation aggregate for existing KG reports.
    """

    _merge_lp_usage(outcome=outcome, target=usage_tracker)

    if isinstance(outcome.error, LPDispatchIntegrityError):
        raise outcome.error

    _verify_execution_material(kg_config=kg_config, population=population, store=store)
    attempt = store.attempts[attempt_index]
    bucket = (
        outcome.usage_tracker.lp_generation
        if attempt.stage == "draft"
        else outcome.usage_tracker.lp_generation_validation
    )
    usage = (
        {
            field: getattr(bucket, field)
            for field in (
                "cache_read_tokens",
                "cache_write_tokens",
                "input_tokens",
                "output_tokens",
                "requests",
                "runs",
            )
        }
        if bucket.requests or isinstance(outcome.error, LPDispatchClosed)
        else None
    )
    status: Literal["succeeded", "failed", "unknown", "cancelled"] = (
        "succeeded"
        if outcome.error is None
        else (
            "cancelled"
            if isinstance(outcome.error, LPDispatchClosed)
            else "failed" if isinstance(outcome.error, Exception) else "unknown"
        )
    )
    store.complete_attempt(
        attempt_index=attempt_index,
        error=outcome.error,
        payload=outcome.payload,
        status=status,
        usage=usage,
    )


def _verify_execution_material(
    *,
    kg_config: CreateKGConfig,
    population: LPRequestPopulation,
    store: LPGenerationCheckpoints,
    verify_checkpoints: bool = True,
) -> None:
    """Reconcile all population and checkpoint material around each dispatched call.

    Parameters
    ----------
    kg_config
        Frozen effective configuration.
    population
        Complete authoritative request population.
    store
        Current validated checkpoint state and directory owner.
    verify_checkpoints
        Include mutable checkpoint bytes. Transport checks share the coordinator gate,
        so they can validate a stable snapshot without racing publication.
    """

    store.verify_ownership()
    _verify_lp_inputs(
        kg_config=kg_config,
        material=store.material,
        population=population,
        root=store.root,
    )

    if verify_checkpoints:
        store.verify_bytes()


def _verify_lp_inputs(
    *,
    kg_config: CreateKGConfig,
    material: dict[str, Any],
    population: LPRequestPopulation,
    root: Path,
) -> None:
    """Reject changed immutable request or model inputs before calls and publication.

    Parameters
    ----------
    kg_config
        Frozen effective policy.
    material
        Captured execution identity.
    population
        Complete materialized candidate and request population.
    root
        Generation directory containing the immutable population artifacts.

    Raises
    ------
    ValueError
        If the LP prompt, model, or configuration has changed since the last dispatch.
    """

    read_lp_request_population(expected=population, root=root)

    if lp_execution_material(kg_config=kg_config, population=population) != material:
        raise ValueError("LP prompt or model material changed during execution.")


def generate_learning_progressions(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool,
    usage_tracker: KGUsageTracker,
) -> tuple[LPGenerationResponse, ...]:
    """Materialize, execute, and resume bounded independent production LP requests.

    Parameters
    ----------
    as_lc_bundle
        Final validated upstream AS+LC bundle for this document.
    doc_key
        Authoritative document identity.
    kg_config
        Effective curriculum policy, evidence bounds, retries, and capacity.
    kg_dirs
        Directory receiving the complete request and checkpoint artifacts.
    overwrite
        Explicit regeneration archives prior evidence before replacing affected files.
    usage_tracker
        Existing run-local LP usage buckets, aggregated by the sole writer.

    Returns
    -------
    tuple[LPGenerationResponse, ...]
        Complete ordered accepted/corrected responses, including negative and ambiguous
        judgments. Processing completion does not establish pedagogical truth.

    Raises
    ------
    LPGenerationFailed
        If any exhausted stage failure remains; no partial success returns.
    ValueError
        If material, checkpoints, pending completions, or ownership are invalid.
    """

    if not isinstance(overwrite, bool):
        raise ValueError("LP overwrite must be an explicit boolean.")

    validate_lp_checkpoint_format(kg_dirs.root)
    bundle = AcademicStandardsLCKGBundle.model_validate_json(
        as_lc_bundle.model_dump_json()
    )
    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )
    expected = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=doc_key, kg_config=config
    )
    kg_dirs.root.mkdir(exist_ok=True, parents=True)

    with (kg_dirs.root / ".lp_generation.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ownership = _LPDirectoryOwnership(lock=lock, root=kg_dirs.root)
        ownership.verify()
        validate_lp_checkpoint_format(kg_dirs.root)

        if overwrite:
            archive_lp_generation_artifacts(kg_dirs.root)

        input_names = (*expected.manifest.artifact_byte_hashes, MANIFEST_FILENAME)
        execution_names = (
            "lp_generation_checkpoint_manifest.json",
            "lp_generation_checkpoint_transaction.json",
            "lp_generation_failures.json",
            "lp_generation_draft_responses.jsonl",
            "lp_generation_validation_verdicts.jsonl",
            "lp_generation_responses.jsonl",
            "lp_generation_pending_completions.json",
            "lp_generation_usage.json",
        )

        if any(
            (kg_dirs.root / name).exists() for name in (*input_names, *execution_names)
        ):
            population = read_lp_request_population(
                expected=expected, root=kg_dirs.root
            )
        else:
            population = write_lp_generation_request_artifacts(
                as_lc_bundle=bundle, doc_key=doc_key, kg_config=config, kg_dirs=kg_dirs
            )

        material = lp_execution_material(kg_config=config, population=population)
        store = LPGenerationCheckpoints(
            execution_check=partial(
                _verify_lp_inputs,
                kg_config=config,
                material=material,
                population=population,
                root=kg_dirs.root,
            ),
            material=material,
            ownership_check=ownership.verify,
            population=population,
            root=kg_dirs.root,
        )
        store.begin_run()
        _finish_lp_requests(
            kg_config=config,
            model_config=ModelConfig.model_validate(material["model_config"]),
            population=population,
            store=store,
            usage_tracker=usage_tracker,
        )
        _verify_execution_material(kg_config=config, population=population, store=store)

        if any(
            failure.resolved_run_number is None for failure in store.failures
        ) or len(store.rows["response"]) != len(population.requests):
            raise LPGenerationFailed(
                "LP processing failures or unfinished requests remain."
            )

        return tuple(
            LPGenerationResponse.model_validate(row.payload)
            for row in store.rows["response"]
        )


def lp_execution_material(
    *, kg_config: CreateKGConfig, population: LPRequestPopulation
) -> dict[str, Any]:
    """Capture actual configuration, model, schemas, and prompt definition material.

    Parameters
    ----------
    kg_config
        Complete effective KG configuration, including LP retry budgets and capacity.
    population
        Reconciled upstream, candidate, and request content identities.

    Returns
    -------
    dict[str, Any]
        Actual material identities, with no manual compatibility-policy selectors.
    """

    model = Settings.llm_config("kgs")
    return {
        "checker_instructions": kg_config.learning_progressions.checker_instructions,
        "config_content_hash": content_hash(
            kg_config.model_dump(by_alias=True, mode="json")
        ),
        "max_concurrent_requests": kg_config.learning_progressions.max_concurrent_requests,
        "model_config": model.model_dump(mode="json"),
        "model_settings": dict(model.kgs_settings("learning_progressions")),
        "producer_instructions": kg_config.learning_progressions.producer_instructions,
        "prompt_definitions_content_hash": hashlib.sha256(
            Path(prompts.__file__).read_bytes()
        ).hexdigest(),
        "request_manifest": population.manifest.model_dump(mode="json"),
        "response_schema_content_hash": content_hash(
            LPGenerationResponse.model_json_schema()
        ),
        "retry_limits": {
            "draft": kg_config.learning_progressions.retry.producer_max_retries,
            "verdict": kg_config.learning_progressions.retry.checker_max_retries,
        },
        "verdict_schema_content_hash": content_hash(
            LPGenerationValidationVerdict.model_json_schema()
        ),
    }
