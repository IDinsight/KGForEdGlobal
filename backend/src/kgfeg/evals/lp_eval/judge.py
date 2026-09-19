"""Persist Learning Progressions evaluation schedules and validated attempt evidence."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import fcntl
import gzip
import hashlib
import json
import os
import sqlite3
import stat

from abc import abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

# Third Party Library
import httpx

from loguru import logger
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.exceptions import AgentRunError, UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage, UsageLimits

# Package Library
from kgfeg.config import BackendSettings, Settings
from kgfeg.evals.lp_eval.output import (
    judge_output_contract,
    judge_output_profile,
    judge_output_type,
)
from kgfeg.evals.lp_eval.prompts import _resolve_evidence_reference
from kgfeg.evals.lp_eval.sampling import (
    _decode_snapshot_json,
    _frozen_bytes,
    _frozen_manifest,
    _frozen_output_boundary,
    _read_snapshot_bytes,
    _stat_identity,
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
    ExecutionRecord,
    FrozenInputManifest,
    FrozenInputs,
    JudgePrompt,
    JudgeReply,
    JudgeUsage,
    RationaleClaimAssessment,
    ResolvedEvaluationSettings,
    ResolvedJudgeSettings,
    ScheduledRequest,
)
from kgfeg.kgs.lp_requests import canonical_lp_json, lp_material_content_hash
from kgfeg.model_registry import ModelConfig

_ATTEMPT_OUTPUT: ContextVar[list[_ProviderOutput | None] | None] = ContextVar(
    "lp_eval_attempt_output", default=None
)
_ATTEMPT_USAGE: ContextVar[list[JudgeUsage | None] | None] = ContextVar(
    "lp_eval_attempt_usage", default=None
)
_CACHE_SCHEMA = {
    "events": "CREATE TABLE events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, content_hash TEXT NOT NULL)",
    "executions": "CREATE TABLE executions (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, content_hash TEXT NOT NULL)",
    "metadata": "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
}
_OUTPUT_VALIDATION_REASONS = frozenset(
    {
        "A grounded rationale must support every material claim.",
        "A partially grounded rationale must support some but not all claims.",
        "An unsupported rationale with supported claims needs a contradiction.",
        "Critique endpoints must be distinct.",
        "Evidence references must not repeat.",
        "Judgment endpoints must be distinct.",
        "Judgment text and evidence references cannot be blank.",
        "Only a developmental assessment has a direction.",
        "Supported or contradicted claims need shown evidence.",
    }
)
JUDGE_ATTEMPT_TIMEOUT_SECONDS = 180
JUDGE_CONCURRENCY = 4
JUDGE_MAX_RETRIES = 2
JUDGE_RETRY_WAITS_SECONDS = (5, 20)


class _Execution:
    """Coordinate a bounded set of requests through one durable session writer."""

    def __init__(
        self,
        *,
        check_material: Callable[[], None],
        session: EvaluationSession,
        sleep: Callable[[float], Awaitable[None]],
        transport: JudgeTransport,
    ) -> None:
        """Capture validated state and injectable transport/retry boundaries.

        Parameters
        ----------
        check_material
            Check immutable inputs immediately before each attempt.
        session
            Locked, revalidated invocation.
        sleep
            Awaitable retry wait; independently replaceable for offline checks.
        transport
            Single-attempt transport bound to the exact frozen model settings.
        """

        self._check_material = check_material
        self._session = session
        self._sleep = sleep
        self._transport = transport
        self._stop = asyncio.Event()
        self._fatal_message: str | None = None
        cache = session.snapshot()
        self._latest = {event.request_id: event for event in cache.events}
        self._completed = {judgment.request_id for judgment in cache.judgments}
        self._total_requests = session.schedule.total_requests
        self._attempts_started = 0
        self._request_numbers = {
            request.prompt.request_id: index
            for index, request in enumerate(
                (
                    request
                    for curriculum in session.schedule.curricula
                    for request in curriculum.requests
                ),
                start=1,
            )
        }

        logger.info(
            "LP evaluation: scheduled={}; cached={}; remaining={}; concurrency={}",
            self._total_requests,
            len(self._completed),
            self._total_requests - len(self._completed),
            JUDGE_CONCURRENCY,
        )

    def _admit(
        self, *, active: set[asyncio.Task[None]], pending: list[ScheduledRequest]
    ) -> None:
        """Fill available slots with dependency-ready requests in schedule order.

        Parameters
        ----------
        active
            Existing bounded worker tasks, updated in place.
        pending
            Unadmitted frozen requests, updated in place.
        """

        ready = [
            request
            for request in pending
            if set(request.dependencies) <= self._completed
        ][: JUDGE_CONCURRENCY - len(active)]

        for request in ready:
            pending.remove(request)
            active.add(asyncio.create_task(self._request(request)))

    async def _attempt(self, request: ScheduledRequest) -> bool:
        """Commit one attempt and validate its raw response against the schedule.

        Parameters
        ----------
        request
            Exact dependency-ready request.

        Returns
        -------
        bool
            True for a committed success, false for stopped or retryable work.

        Raises
        ------
        JudgeExecutionError
            If frozen material or a terminal attempt prevents execution.
        asyncio.CancelledError
            If the caller interrupts the attempt.
        sqlite3.Error
            If durable recording fails.
        """

        if self._stop.is_set():
            return False

        try:
            self._check_material()

            if self._transport.settings != self._session.schedule.judge:
                raise ValueError("Transport settings changed during execution.")
        except (OSError, ValueError):
            raise JudgeExecutionError(
                cache=self._session.snapshot(),
                message="Frozen inputs or transport settings changed during execution.",
            ) from None

        attempt = self._session.start_attempt(request.prompt.request_id)
        self._attempts_started += 1

        logger.info(
            "LP evaluation attempt started: request={}/{}; lifetime_attempt={}; cycle_attempt={}/{}; "
            "execution={}; "
            "attempts_this_invocation={}; completed={}/{}; "
            "component={}; task={}; request_id={}",
            self._request_numbers[request.prompt.request_id],
            self._total_requests,
            attempt.attempt_number,
            (attempt.attempt_number - 1) % 3 + 1,
            JUDGE_MAX_RETRIES + 1,
            attempt.execution_number,
            self._attempts_started,
            len(self._completed),
            self._total_requests,
            request.component,
            request.prompt.task,
            request.prompt.request_id,
        )

        reply = await self._dispatch(attempt=attempt, prompt=request.prompt)

        if reply is None:
            return False

        try:
            self._session.record_success(
                attempt=attempt,
                response_json=reply.response_json,
                usage=reply.usage,
            )
        except ValueError as error:
            self._failure(
                attempt=attempt,
                error=JudgeCallError(
                    category="invalid_output",
                    message=_output_validation_message(error),
                    raw_response=reply.response_json,
                    usage=reply.usage,
                ),
            )
            return False

        self._completed.add(request.prompt.request_id)

        logger.info(
            "LP evaluation request completed: request={}/{}; "
            "completed={}/{}; remaining={}; request_id={}",
            self._request_numbers[request.prompt.request_id],
            self._total_requests,
            len(self._completed),
            self._total_requests,
            self._total_requests - len(self._completed),
            request.prompt.request_id,
        )

        return True

    async def _dispatch(
        self, *, attempt: AttemptEvent, prompt: JudgePrompt
    ) -> JudgeReply | None:
        """Apply timeout and preserve observed usage even when a call is interrupted.

        Parameters
        ----------
        attempt
            Already committed start.
        prompt
            Frozen judge-visible messages.

        Returns
        -------
        JudgeReply | None
            Unvalidated response, or None after a retryable recorded failure.

        Raises
        ------
        JudgeExecutionError
            If a terminal attempt fails.
        asyncio.CancelledError
            If execution is interrupted after retaining available accounting.
        sqlite3.Error
            If recording the attempt outcome fails.
        """

        observed: list[JudgeUsage | None] = [None]
        token = _ATTEMPT_USAGE.set(observed)

        try:
            async with asyncio.timeout(JUDGE_ATTEMPT_TIMEOUT_SECONDS):
                return await self._transport.judge(prompt)
        except asyncio.CancelledError:
            self._stop.set()
            self._failure(
                attempt=attempt,
                error=JudgeCallError(
                    category="interrupted",
                    message="Execution was cancelled; remote completion and usage may be unknown.",
                    usage=observed[0],
                ),
            )
            raise
        except (
            JudgeCallError,
            AgentRunError,
            UserError,
            TimeoutError,
            httpx.HTTPError,
            ValueError,
            TypeError,
        ) as error:
            failure = _classify_judge_error(error)

            if observed[0] is not None:
                failure.usage = observed[0]

            self._failure(attempt=attempt, error=failure)

            if self._stop.is_set():
                raise JudgeExecutionError(
                    cache=self._session.snapshot(),
                    message="A terminal judge attempt failed.",
                ) from None

            return None
        finally:
            _ATTEMPT_USAGE.reset(token)

    def _failure(self, *, attempt: AttemptEvent, error: JudgeCallError) -> None:
        """Append one failure and close admission when retry is not permitted.

        Parameters
        ----------
        attempt
            Durable start handle.
        error
            Sanitized error with available usage.
        """

        self._session.record_failure(
            attempt=attempt,
            category=error.category,
            message=str(error),
            raw_response=error.raw_response,
            usage=error.usage,
        )
        event = self._session.snapshot().events[-1]
        self._latest[event.request_id] = event

        if not _retryable_event(event):
            self._stop.set()

        logger.warning(
            "LP evaluation attempt failed: request={}/{}; lifetime_attempt={}; cycle_attempt={}/{}; "
            "execution={}; "
            "completed={}/{}; retryable={}; category={}; reason={}; request_id={}",
            self._request_numbers[event.request_id],
            self._total_requests,
            event.attempt_number,
            (event.attempt_number - 1) % 3 + 1,
            JUDGE_MAX_RETRIES + 1,
            event.execution_number,
            len(self._completed),
            self._total_requests,
            _retryable_event(event),
            error.category,
            str(error),
            event.request_id,
        )

    async def _request(self, request: ScheduledRequest) -> None:
        """Run remaining attempts using durable history and fixed retry waits.

        Parameters
        ----------
        request
            Frozen call whose earlier attempts are already recorded.

        Raises
        ------
        BaseException
            If execution or storage fails; new admission closes immediately.
        """

        try:
            while not self._stop.is_set():
                previous = self._latest.get(request.prompt.request_id)

                if previous is not None and _retryable_event(previous):
                    await _wait_for_retry(
                        delay=JUDGE_RETRY_WAITS_SECONDS[
                            (previous.attempt_number - 1) % 3
                        ],
                        sleep=self._sleep,
                        stop=self._stop,
                    )

                if self._stop.is_set():
                    return

                if await self._attempt(request):
                    return
        except BaseException:
            self._stop.set()
            raise

    def _resume_ready(self) -> None:
        """Report prior failures without treating history as a dispatch prohibition."""

        failed = [
            event for event in self._latest.values() if event.event != "succeeded"
        ]
        if failed:
            logger.info(
                "LP evaluation recovery: {} prior failed/interrupted requests may be retried; "
                "unknown remote outcomes and usage remain recorded.",
                len(failed),
            )

    async def run(self) -> EvaluationCache:
        """Execute ready requests with bounded tasks and drain active work on failure.

        Returns
        -------
        EvaluationCache
            Complete validated judgments and append-only attempt evidence.

        Raises
        ------
        JudgeExecutionError
            If failures, unfinished prior attempts or dependencies block completion.
        BaseException
            If execution is interrupted; outstanding worker tasks are cleaned up.
        """

        self._resume_ready()
        pending = [
            request
            for curriculum in self._session.schedule.curricula
            for request in curriculum.requests
            if request.prompt.request_id not in self._completed
        ]
        active: set[asyncio.Task[None]] = set()

        try:
            while pending or active:
                if not self._stop.is_set():
                    self._admit(active=active, pending=pending)

                if not active:
                    break

                done, active = await asyncio.wait(
                    active, return_when=asyncio.FIRST_COMPLETED
                )

                for task in done:
                    error = task.exception()

                    if error is not None:
                        self._stop.set()
                        self._fatal_message = (
                            str(error)
                            if isinstance(error, JudgeExecutionError)
                            else f"Execution stopped ({type(error).__name__}); inspect durable attempts."
                        )
        except BaseException:
            self._stop.set()

            for task in active:
                task.cancel()

            await asyncio.gather(*active, return_exceptions=True)
            raise

        cache = self._session.snapshot()

        if len(cache.judgments) != self._session.schedule.total_requests:
            raise JudgeExecutionError(
                cache=cache,
                message=self._fatal_message
                or "Evaluation is incomplete; see failed, unfinished and unattempted requests.",
            )

        return cache


class _MaterialWatch:
    """Check selected files and required absences between full hash validations."""

    def __init__(
        self, *, reference: EvaluationStore, schedule: EvaluationSchedule
    ) -> None:
        """Capture filesystem identities before the next full validation.

        Parameters
        ----------
        reference
            Exact invocation whose schedule is being executed.
        schedule
            Loaded schedule and frozen input paths.
        """

        manifest = _frozen_manifest(schedule.inputs)
        paths = {
            reference.manifest_path,
            reference.manifest_path.parent / "schedule.json.gz",
            schedule.inputs.manifest_path,
        }
        self._absent: list[Path] = []

        for snapshot in manifest.snapshots:
            for artifact in snapshot.artifacts:
                paths.add(artifact.fingerprint.path)
                paths.add(
                    schedule.inputs.manifest_path.parent / artifact.fingerprint.sha256
                )

            self._absent.extend(
                snapshot.run.kgs_directory / name for name in snapshot.absent_artifacts
            )

        self._identities = {path: _stat_identity(path.stat()) for path in paths}

    def check(self) -> None:
        """Reject changed, missing or aliased material before admitting an attempt.

        Raises
        ------
        ValueError
            If a watched file or required absence differs.
        """

        for path, expected in self._identities.items():
            if (
                path.resolve(strict=True) != path
                or _stat_identity(path.stat()) != expected
            ):
                raise ValueError("Frozen evaluation material changed during execution.")

        if any(os.path.lexists(path) for path in self._absent):
            raise ValueError(
                "A required frozen input absence changed during execution."
            )


class _ProviderJudge:
    """Fresh Pydantic AI agents using the shared model configuration."""

    def __init__(self, *, model: Model, settings: ResolvedJudgeSettings) -> None:
        """Bind the model without sharing agent history across judgments.

        Parameters
        ----------
        model
            Isolated Pydantic AI model; local models are also injectable.
        settings
            Exact frozen evaluator configuration.
        """

        self._model = model
        self._settings = settings

    async def judge(self, prompt: JudgePrompt) -> JudgeReply:
        """Run one typed agent attempt and retain raw output and usage on failure.

        Parameters
        ----------
        prompt
            Frozen schema-bearing instructions and bounded evidence.

        Returns
        -------
        JudgeReply
            Raw native JSON or output-tool arguments and available usage.

        Raises
        ------
        JudgeCallError
            If the model or complete-response contract fails.
        """

        config = ModelConfig.model_validate_json(self.settings.model_config_json)
        validate_judge_settings(self.settings)
        schema = (
            ClassificationJudgment
            if prompt.task == "classification"
            else CritiqueJudgment
        )
        schema_json = canonical_lp_json(schema.model_json_schema())

        if (
            prompt.response_schema_json != schema_json
            or prompt.response_schema_sha256
            != hashlib.sha256(schema_json.encode()).hexdigest()
        ):
            raise JudgeCallError(
                category="invalid_input",
                message="Frozen response schema differs from the typed output schema.",
            )

        mode = json.loads(self.settings.output_contract_json)["mode"]
        agent = Agent(
            instructions=prompt.system_message,
            model=self._model,
            model_settings=config.kgs_settings("learning_progressions"),
            output_retries=0,
            output_type=judge_output_type(config=config, task=prompt.task),
            retries=0,
        )
        usage = RunUsage()
        observed: list[JudgeUsage | None] = _ATTEMPT_USAGE.get() or [None]
        output: list[_ProviderOutput | None] = [None]
        output_token = _ATTEMPT_OUTPUT.set(output)
        token = _ATTEMPT_USAGE.set(observed)

        try:
            with capture_run_messages() as messages:
                try:
                    result = await agent.run(
                        prompt.user_message,
                        usage=usage,
                        usage_limits=UsageLimits(request_limit=1),
                    )
                except Exception as error:
                    accounting = observed[0] or _agent_usage(
                        messages=messages, usage=usage
                    )

                    if output[0] is not None and output[0].failure is not None:
                        output[0].failure.usage = accounting
                        raise output[0].failure from None

                    # Inspect the unmodified response even when SDK validation failed;
                    # wrapped SDK errors otherwise hide missing-field diagnostics.
                    has_response = any(
                        isinstance(item, ModelResponse) for item in messages
                    )

                    if has_response:
                        _agent_reply(
                            messages=messages,
                            mode=mode,
                            prompt=prompt,
                            usage=accounting,
                        )

                    failure = (
                        JudgeCallError(
                            category="invalid_output",
                            message="Provider output could not be decoded by the SDK.",
                        )
                        if output[0] is not None and not has_response
                        else _classify_judge_error(error)
                    )
                    raise JudgeCallError(
                        category=failure.category,
                        message=str(failure),
                        raw_response=(
                            output[0].raw_response
                            if output[0] is not None
                            else _agent_raw_output(messages)
                        ),
                        usage=accounting,
                    ) from None

            accounting = observed[0] or _agent_usage(messages=messages, usage=usage)

            if output[0] is not None and output[0].failure is not None:
                output[0].failure.usage = accounting
                raise output[0].failure

            if not isinstance(result.output, schema):
                raise JudgeCallError(
                    category="invalid_output",
                    message="Agent returned an unexpected typed output.",
                    raw_response=_agent_raw_output(messages),
                    usage=accounting,
                )

            return _agent_reply(
                messages=messages,
                mode=mode,
                prompt=prompt,
                usage=accounting,
            )
        finally:
            _ATTEMPT_USAGE.reset(token)
            _ATTEMPT_OUTPUT.reset(output_token)

    async def preflight(self) -> None:
        """Verify model access without making a generation request.

        Local models need no remote lookup. Generation itself always uses Agent.run;
        the provider metadata API is used only for availability checking.

        Raises
        ------
        JudgeCallError
            If the configured remote model cannot be accessed.
        """

        if isinstance(self._model, (AnthropicModel, OpenAIResponsesModel)):
            try:
                async with asyncio.timeout(JUDGE_ATTEMPT_TIMEOUT_SECONDS):
                    models = getattr(self._model.client, "models", None)

                    if models is None:
                        raise JudgeCallError(
                            category="configuration",
                            message="The configured provider has no model availability lookup.",
                        )

                    await models.retrieve(self._model.model_name)
            except Exception as error:
                raise _classify_judge_error(error) from None

    @property
    def settings(self) -> ResolvedJudgeSettings:
        """Return the exact frozen configuration used for every agent.

        Returns
        -------
        ResolvedJudgeSettings
            Provider, model and effective settings.
        """
        return self._settings


@dataclass(frozen=True, slots=True)
class _ProviderOutput:
    """Original provider output evidence before lossy SDK normalization.

    Attributes
    ----------
    failure
        Output-envelope violation, with sanitized raw evidence attached.
    raw_response
        Only returned output fields; no request, headers or provider metadata.
    """

    failure: JudgeCallError | None
    raw_response: str | None


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
        executions: tuple[ExecutionRecord, ...],
        schedule: EvaluationSchedule,
    ) -> None:
        """Initialize an already locked session and replay its validated evidence.

        Parameters
        ----------
        connection
            Open transactional database held under the invocation writer lock.
        events
            Verified ordered ledger events.
        executions
            Durable execution boundaries bound to the event chain.
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

        self._execution_started = False
        self._executions: list[ExecutionRecord] = []
        self._execution_head = schedule.material_content_hash
        pending = iter(executions)
        boundary = next(pending, None)

        for index in range(len(events) + 1):
            while boundary is not None and boundary.event_count == index:
                self._validate_execution(boundary)
                self._executions.append(boundary)
                self._execution_head = _execution_hash(
                    previous=self._execution_head, record=boundary
                )
                boundary = next(pending, None)

            if index < len(events):
                event = events[index]
                judgment = self._validate_event(event)
                self._remember(event=event, judgment=judgment)
                self._head = _event_hash(event=event, previous=self._head)

        if boundary is not None:
            raise ValueError("Execution boundary lies outside the attempt history.")

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

        if not self._executions or (
            event.execution_number != self._executions[-1].execution_number
            or event.timestamp < self._executions[-1].timestamp
        ):
            raise ValueError(
                "Attempt does not belong to the active recorded execution."
            )

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
            or previous.execution_number != event.execution_number
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

    def _validate_execution(self, record: ExecutionRecord) -> None:
        """Require an ordered boundary over complete, safely retryable prior work.

        Parameters
        ----------
        record
            Proposed execution boundary.

        Raises
        ------
        ValueError
            If history, chronology or prior outcomes cannot support safe execution.
        """

        if (
            record.execution_number != len(self._executions) + 1
            or record.event_count != len(self._events)
            or record.event_head != self._head
            or record.timestamp.utcoffset() is None
            or (self._executions and record.timestamp < self._executions[-1].timestamp)
            or any(
                record.timestamp < event.timestamp for event in self._latest.values()
            )
        ):
            raise ValueError(
                "Execution boundary differs from recorded attempt history."
            )

    def begin_execution(self) -> None:
        """Record at most one new execution opportunity during this locked session.

        Raises
        ------
        ValueError
            If current material or execution history cannot be validated.
        """

        if self._execution_started:
            return

        for attempt in self.snapshot().unfinished:
            self.record_failure(
                attempt=attempt,
                category="interrupted",
                message="A user rerun superseded this unfinished attempt; remote outcome and usage remain unknown.",
                usage=JudgeUsage(),
            )

        record = ExecutionRecord(
            event_count=len(self._events),
            event_head=self._head,
            execution_number=len(self._executions) + 1,
            timestamp=datetime.now(UTC),
        )
        self._validate_execution(record)
        payload = canonical_lp_json(record.model_dump(exclude_none=True, mode="json"))
        digest = _execution_hash(previous=self._execution_head, record=record)

        with self._connection:
            self._connection.execute(
                "INSERT INTO executions VALUES (?, ?, ?)",
                (record.execution_number, payload, digest),
            )
            self._connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'execution_head'",
                (
                    canonical_lp_json(
                        {"count": record.execution_number, "hash": digest}
                    ),
                ),
            )

        self._executions.append(record)
        self._execution_head = digest
        self._execution_started = True

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
                execution_number=attempt.execution_number,
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
                execution_number=attempt.execution_number,
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
            executions=tuple(self._executions),
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

        self.begin_execution()
        previous = self._latest.get(request_id)
        event = AttemptEvent(
            attempt_number=1 if previous is None else previous.attempt_number + 1,
            event="started",
            execution_number=len(self._executions),
            request_content_hash=request.material_content_hash,
            request_id=request_id,
            timestamp=datetime.now(UTC),
        )
        self._append(event)
        return event


class JudgeCallError(RuntimeError):
    """A classified single-attempt failure with available response and accounting."""

    def __init__(
        self,
        *,
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
        usage: JudgeUsage | None = None,
    ) -> None:
        """Keep failure details without treating them as a semantic response.

        Parameters
        ----------
        category
            Explicit execution failure category.
        message
            Sanitized explanation without credentials or request headers.
        raw_response
            Available response text, including malformed output.
        usage
            Observed accounting, or unknown when no accounting was received.
        """

        super().__init__(message)
        self.category = category
        self.raw_response = raw_response
        self.usage = JudgeUsage() if usage is None else usage


class JudgeExecutionError(RuntimeError):
    """Incomplete execution with immutable evidence for subsequent partial reporting."""

    def __init__(self, *, cache: EvaluationCache, message: str) -> None:
        """Retain validated progress independently of the exception message.

        Parameters
        ----------
        cache
            Successful responses and all recorded attempt evidence.
        message
            Reason execution cannot continue.
        """

        super().__init__(message)
        self.cache = cache


class JudgeTransport(Protocol):
    """Injectable, fresh-context single-attempt transport with no hidden retries."""

    @abstractmethod
    async def judge(self, prompt: JudgePrompt) -> JudgeReply:
        """Dispatch exactly one attempt using only the frozen judge-visible prompt.

        Parameters
        ----------
        prompt
            Exact messages and schema; no sampling labels or prior judge answers.

        Returns
        -------
        JudgeReply
            Unvalidated output plus available accounting.

        Raises
        ------
        JudgeCallError
            If this one attempt fails.
        """

        raise NotImplementedError

    @abstractmethod
    async def preflight(self) -> None:
        """Check configured model access without generating a judgment.

        Raises
        ------
        JudgeCallError
            If provider authentication, model availability or compatibility fails.
        """

        raise NotImplementedError

    @property
    @abstractmethod
    def settings(self) -> ResolvedJudgeSettings:
        """Return the exact non-secret settings used by this transport.

        Returns
        -------
        ResolvedJudgeSettings
            Immutable provider, model and settings binding.
        """

        raise NotImplementedError


def _agent_output_json(*, mode: str, response: ModelResponse, schema_name: str) -> str:
    """Extract the sole intended output without accepting incomplete responses.

    Parameters
    ----------
    mode
        Frozen native or tool mode.
    response
        The only response from this attempt.
    schema_name
        Intended output tool name when tool mode is used.

    Returns
    -------
    str
        Native text or tool arguments, pending strict JSON validation.

    Raises
    ------
    JudgeCallError
        If completion, output count or part types are invalid.
    """

    parts = [part for part in response.parts if not isinstance(part, ThinkingPart)]

    if response.finish_reason not in (
        {"stop"} if mode == "native" else {"stop", "tool_call"}
    ):
        problem = "Judge output is incomplete, truncated, filtered or missing a completion reason."
    elif len(parts) != 1:
        problem = f"Expected exactly one output part; received {len(parts)}."
    elif mode == "native" and isinstance(parts[0], TextPart):
        return parts[0].content
    elif mode == "tool" and isinstance(parts[0], ToolCallPart):
        if parts[0].tool_name != schema_name:
            problem = "Judge called an unexpected output tool."
        else:
            arguments = parts[0].args
            return arguments if isinstance(arguments, str) else json.dumps(arguments)
    else:
        problem = "Judge returned an unexpected output part for the frozen mode."

    raise JudgeCallError(category="invalid_output", message=problem)


def _agent_raw_output(messages: list[ModelMessage]) -> str | None:
    """Capture output text and tool arguments without input messages or metadata.

    Parameters
    ----------
    messages
        Captured single-attempt messages.

    Returns
    -------
    str | None
        Raw text for a single text output, otherwise an ordered response envelope.
        Thinking, signatures, headers and provider metadata are deliberately omitted.
    """

    responses = [message for message in messages if isinstance(message, ModelResponse)]

    if not responses:
        return None

    if len(responses) == 1 and all(
        isinstance(part, (TextPart, ThinkingPart)) for part in responses[0].parts
    ):
        texts = [
            part.content for part in responses[0].parts if isinstance(part, TextPart)
        ]

        if len(texts) == 1:
            return texts[0]

    material = []

    for response in responses:
        parts: list[dict[str, Any]] = []

        for part in response.parts:
            if isinstance(part, TextPart):
                parts.append({"kind": "text", "text": part.content})
            elif isinstance(part, ToolCallPart):
                parts.append(
                    {"arguments": part.args, "kind": "tool", "name": part.tool_name}
                )
            elif not isinstance(part, ThinkingPart):
                parts.append({"kind": part.part_kind})

        material.append({"finish_reason": response.finish_reason, "parts": parts})

    return json.dumps(material, ensure_ascii=False, separators=(",", ":"))


def _agent_reply(
    *, messages: list[ModelMessage], mode: str, prompt: JudgePrompt, usage: JudgeUsage
) -> JudgeReply:
    """Require exactly one complete native object or intended output-tool call.

    Parameters
    ----------
    messages
        Captured messages from a single fresh agent attempt.
    mode
        Frozen explicit native or tool output mode.
    prompt
        Intended schema and task, already verified against the frozen material.
    usage
        Available accounting, retained when completion validation fails.

    Returns
    -------
    JudgeReply
        Original JSON or tool arguments, pending strict scheduled identity checks.

    Raises
    ------
    JudgeCallError
        If output is missing, incomplete, duplicated, malformed or unexpected.
    """

    responses = [message for message in messages if isinstance(message, ModelResponse)]
    schema = (
        ClassificationJudgment if prompt.task == "classification" else CritiqueJudgment
    )
    raw = _agent_raw_output(messages)

    if len(responses) != 1:
        raise JudgeCallError(
            category="invalid_output",
            message=f"Expected one model response; received {len(responses)}.",
            raw_response=raw,
            usage=usage,
        )

    try:
        response_json = _agent_output_json(
            mode=mode, response=responses[0], schema_name=schema.__name__
        )
        _decode_snapshot_json(response_json.encode())
        schema.model_validate_json(response_json)
    except JudgeCallError as error:
        error.raw_response = raw
        error.usage = usage
        raise
    except ValueError as error:
        raise JudgeCallError(
            category="invalid_output",
            message=_output_validation_message(error),
            raw_response=raw,
            usage=usage,
        ) from None

    return JudgeReply(response_json=response_json, usage=usage)


def _agent_usage(*, messages: list[ModelMessage], usage: RunUsage) -> JudgeUsage:
    """Adapt Pydantic AI accounting when no raw HTTP accounting is available.

    Provider HTTP accounting takes precedence because normalized usage objects have
    default zero fields that cannot establish whether a provider reported a value.

    Parameters
    ----------
    messages
        Captured model responses, also available for invalid output.
    usage
        Per-attempt accumulator used by the rest of the KG code.

    Returns
    -------
    JudgeUsage
        Positive observed counters; indistinguishable default zeros remain unknown.
    """

    responses = [message for message in messages if isinstance(message, ModelResponse)]
    response = responses[-1] if responses else None
    return JudgeUsage(
        cache_read_tokens=usage.cache_read_tokens or None,
        cache_write_tokens=usage.cache_write_tokens or None,
        input_tokens=usage.input_tokens or None,
        output_tokens=usage.output_tokens or None,
        provider_model=response.model_name if response else None,
        provider_request_id=response.provider_response_id if response else None,
        reasoning_tokens=usage.details.get("reasoning_tokens") or None,
    )


def _classify_judge_error(error: Exception) -> JudgeCallError:
    """Classify Pydantic AI errors and metadata failures without leaking credentials.

    Parameters
    ----------
    error
        Failure from the agent, provider metadata lookup or timeout boundary.

    Returns
    -------
    JudgeCallError
        Safe failure category; only the configured transient classes permit retry.
    """

    if isinstance(error, JudgeCallError):
        return error

    if _is_timeout(error):
        return JudgeCallError(
            category="timeout", message="Judge attempt exceeded its timeout."
        )

    code = getattr(error, "status_code", None)

    if isinstance(code, int):
        return _status_failure(code)

    if isinstance(error, UnexpectedModelBehavior):
        return JudgeCallError(
            category="invalid_output",
            message="Judge returned invalid or incomplete output.",
        )

    category: Literal["configuration", "transport"] = (
        "configuration"
        if isinstance(error, (UserError, ValueError, TypeError))
        else "transport"
    )
    return JudgeCallError(
        category=category, message=f"Judge attempt failed ({type(error).__name__})."
    )


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

    if (
        set(metadata) != {"execution_head", "head", "manifest", "schedule"}
        or metadata["schedule"] != schedule_hash
    ):
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


def _database_executions(
    *, connection: sqlite3.Connection, schedule_hash: str
) -> tuple[ExecutionRecord, ...]:
    """Validate the complete append-only execution chain and its committed head.

    Parameters
    ----------
    connection
        Existing database under exclusive ownership.
    schedule_hash
        Original frozen schedule identity.

    Returns
    -------
    tuple[ExecutionRecord, ...]
        Ordered boundaries for state-machine replay.

    Raises
    ------
    ValueError
        If sequence, canonical bytes or committed chain head differ.
    """

    records = []
    digest = schedule_hash

    for expected, (sequence, payload, recorded_hash) in enumerate(
        connection.execute(
            "SELECT sequence, payload, content_hash FROM executions ORDER BY sequence"
        ),
        start=1,
    ):
        record = ExecutionRecord.model_validate_json(payload)
        digest = _execution_hash(previous=digest, record=record)

        if (
            sequence != expected
            or record.execution_number != expected
            or payload
            != canonical_lp_json(record.model_dump(exclude_none=True, mode="json"))
            or digest != recorded_hash
        ):
            raise ValueError("Evaluation execution history is altered or out of order.")

        records.append(record)

    head = connection.execute(
        "SELECT value FROM metadata WHERE key = 'execution_head'"
    ).fetchone()

    if head != (canonical_lp_json({"count": len(records), "hash": digest}),):
        raise ValueError("Execution head does not cover the complete history.")

    return tuple(records)


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


def _execution_hash(*, previous: str, record: ExecutionRecord) -> str:
    """Bind an execution record to its preceding committed boundary.

    Parameters
    ----------
    previous
        Prior chain hash, initially the schedule hash.
    record
        Boundary containing the attempt-chain count and head.

    Returns
    -------
    str
        Deterministic content identity.
    """

    return lp_material_content_hash(
        {
            "previous": previous,
            "record": record.model_dump(exclude_none=True, mode="json"),
        }
    )


def _is_timeout(error: BaseException) -> bool:
    """Recognize timeouts wrapped by Pydantic AI and its provider clients.

    Parameters
    ----------
    error
        Outermost exception.

    Returns
    -------
    bool
        Whether its finite cause chain contains a transport or asyncio timeout.
    """

    seen: set[int] = set()
    current: BaseException | None = error

    while current is not None and id(current) not in seen:
        seen.add(id(current))

        if isinstance(current, (TimeoutError, httpx.TimeoutException)):
            return True

        current = current.__cause__ or current.__context__

    return False


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

    manifest = _store_manifest(reference)

    inventory = _frozen_manifest(manifest.inputs).inventory
    directory = (
        inventory.evaluation_root / "invocations" / manifest.schedule_content_hash
    )

    if reference.manifest_path != directory / "manifest.json":
        raise ValueError("Evaluation store is outside its frozen output directory.")

    _frozen_output_boundary(inventory=inventory, output=inventory.evaluation_root)
    return manifest


def _output_validation_message(error: ValueError) -> str:
    """Describe validation failures without copying values or provider exceptions.

    Parameters
    ----------
    error
        Strict JSON, schema or request-relative validation failure.

    Returns
    -------
    str
        Known field locations and error codes, or a fixed domain diagnostic.
    """

    if isinstance(error, ValidationError):
        fields = (
            set(ClassificationJudgment.model_fields)
            | set(CritiqueJudgment.model_fields)
            | set(RationaleClaimAssessment.model_fields)
        )
        details = []

        for item in error.errors(
            include_context=False, include_input=False, include_url=False
        ):
            location = (
                ".".join(
                    str(part) if isinstance(part, int) or part in fields else "<extra>"
                    for part in item["loc"]
                )
                or "judgment"
            )
            reason = item["type"]
            domain_message = item["msg"].removeprefix("Value error, ")

            if domain_message in _OUTPUT_VALIDATION_REASONS:
                reason += f" ({domain_message})"

            details.append(f"{location}: {reason}")

        return "Structured output validation failed: " + "; ".join(details)

    if isinstance(error, json.JSONDecodeError):
        return f"Malformed output JSON at line {error.lineno}, column {error.colno}."

    if str(error).startswith("Duplicate JSON key:"):
        return "Output JSON contains a duplicate object key."

    if str(error).startswith("invalid JSON numeric constant:"):
        return "Output JSON contains a nonfinite numeric constant."

    safe_messages = {
        "Judge response identity differs from its scheduled presentation.",
        "Judge response violates the displayed assessment permissions.",
        "Judge cited evidence outside its permitted shown references.",
        "Evidence reference must be a JSON pointer.",
        "Evidence reference is outside the shown payload.",
        "Noncanonical evidence array index.",
    }
    return (
        str(error)
        if str(error) in safe_messages
        else (
            "Output failed strict JSON or scheduled validation (including duplicate keys "
            "and nonfinite values)."
        )
    )


def _provider_output(*, material: dict[str, Any], provider: str) -> _ProviderOutput:
    """Validate original output count and completion before SDK normalization.

    Parameters
    ----------
    material
        Decoded successful HTTP response, never a request or error-body dump.
    provider
        Configured provider defining the response envelope.

    Returns
    -------
    _ProviderOutput
        Sanitized output evidence and any envelope failure for durable accounting.
    """

    native = provider == "anthropic"
    parts = material.get("content" if native else "output")
    raw = json.dumps(
        (
            [_provider_output_part(part) for part in parts]
            if isinstance(parts, list)
            else None
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    problem = None
    complete = (
        material.get("stop_reason") == "end_turn"
        if native
        else material.get("status") == "completed"
        and material.get("incomplete_details") is None
    )

    if not complete:
        problem = "Provider output is incomplete, truncated, filtered or missing completion status."
    elif not isinstance(parts, list) or any(
        not isinstance(part, dict) for part in parts
    ):
        problem = "Provider output parts are missing or malformed."
    else:
        thinking = ("thinking", "redacted_thinking") if native else ("reasoning",)
        outputs = [part for part in parts if part.get("type") not in thinking]
        expected = "text" if native else "function_call"

        if len(outputs) != 1 or outputs[0].get("type") != expected:
            problem = "Provider must return exactly one intended output and no unexpected tools or parts."
        elif outputs[0].get("status") not in (None, "completed"):
            problem = "Provider returned an incomplete output item."
        elif not isinstance(outputs[0].get("text" if native else "arguments"), str):
            problem = "Provider output text or tool arguments are missing or malformed."

    failure = (
        JudgeCallError(category="invalid_output", message=problem, raw_response=raw)
        if problem is not None
        else None
    )
    return _ProviderOutput(failure=failure, raw_response=raw)


def _provider_output_part(part: Any) -> dict[str, Any]:
    """Keep output text and tool arguments without reasoning or metadata fields.

    Parameters
    ----------
    part
        One provider output item or nested message content item.

    Returns
    -------
    dict[str, Any]
        Allowlisted output evidence, including unexpected tool arguments.
    """

    if not isinstance(part, dict):
        return {"type": "malformed_part"}

    if part.get("type") in ("thinking", "redacted_thinking", "reasoning"):
        return {"type": part["type"]}

    result = {
        key: part[key]
        for key in (
            "action",
            "arguments",
            "input",
            "name",
            "refusal",
            "status",
            "text",
            "type",
        )
        if key in part
    }

    if isinstance(part.get("content"), list):
        result["content"] = [_provider_output_part(item) for item in part["content"]]

    return result


def _provider_usage(
    *, material: dict[str, Any], provider: str, request_id: str | None
) -> JudgeUsage:
    """Preserve observed provider counters without inventing zeros or prices.

    Parameters
    ----------
    material
        Provider response or error body; absent accounting is allowed.
    provider
        Provider determining token detail field names.
    request_id
        Available provider HTTP request identifier.

    Returns
    -------
    JudgeUsage
        Nonnegative observed counters and explicit unknown values.
    """

    observed = material.get("usage")
    observed = observed if isinstance(observed, dict) else {}
    input_details = observed.get("input_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = observed.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    return JudgeUsage(
        cache_read_tokens=_usage_count(
            observed.get("cache_read_input_tokens")
            if provider == "anthropic"
            else input_details.get("cached_tokens")
        ),
        cache_write_tokens=_usage_count(observed.get("cache_creation_input_tokens")),
        input_tokens=_usage_count(observed.get("input_tokens")),
        output_tokens=_usage_count(observed.get("output_tokens")),
        provider_model=(
            material.get("model")
            if isinstance(material.get("model"), str) and material["model"].strip()
            else None
        ),
        provider_request_id=request_id,
        reasoning_tokens=_usage_count(output_details.get("reasoning_tokens")),
    )


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
        created_at=datetime.now(UTC),
        inputs=schedule.inputs,
        kind="lp_evaluation_store_v2",
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
                    (
                        "execution_head",
                        canonical_lp_json(
                            {"count": 0, "hash": schedule.material_content_hash}
                        ),
                    ),
                    ("manifest", hashlib.sha256(payload).hexdigest()),
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


def _retryable_event(event: AttemptEvent) -> bool:
    """Determine whether a recorded failure still has a permitted retry remaining.

    Parameters
    ----------
    event
        Last durable event for one scheduled request.

    Returns
    -------
    bool
        True for retryable failures with allowance remaining in their current cycle.
    """

    return (
        event.event == "failed"
        and (event.attempt_number - 1) % (JUDGE_MAX_RETRIES + 1) < JUDGE_MAX_RETRIES
        and event.failure_category
        in {"timeout", "rate_limit", "server_error", "invalid_output"}
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


def _status_failure(code: int) -> JudgeCallError:
    """Map provider HTTP status to retryable or immediate-failure categories.

    Parameters
    ----------
    code
        Status exposed by Pydantic AI or the provider metadata API.

    Returns
    -------
    JudgeCallError
        Sanitized error without provider headers, URLs or credentials.
    """

    category: Literal["rate_limit", "server_error", "authentication", "configuration"]

    if code == 429:
        category = "rate_limit"
    elif 500 <= code <= 599:
        category = "server_error"
    elif code in {401, 403}:
        category = "authentication"
    else:
        category = "configuration"

    return JudgeCallError(
        category=category, message=f"Judge provider returned HTTP {code}."
    )


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


def _store_inventory(
    *, inputs: FrozenInputs, reference: EvaluationStore
) -> DiscoveryInventory:
    """Read authenticated lookup metadata without validating unrelated live inputs.

    Parameters
    ----------
    inputs
        Exact input-manifest identity recorded by the saved schedule.
    reference
        Invocation whose output root contains the frozen manifest.

    Returns
    -------
    DiscoveryInventory
        Original root and selection metadata for lookup only.

    Raises
    ------
    ValueError
        If path, bytes, schema or canonical identity differs.
    """

    evaluation_root = reference.manifest_path.parents[2]
    expected = evaluation_root / "inputs" / inputs.content_hash / "manifest.json"

    if inputs.manifest_path != expected:
        raise ValueError("Lookup input manifest is outside its evaluation store.")

    raw = _store_read(expected)

    if hashlib.sha256(raw).hexdigest() != inputs.content_hash:
        raise ValueError("Lookup input manifest hash differs.")

    manifest = TypeAdapter(FrozenInputManifest).validate_json(raw)

    if (
        _frozen_bytes(manifest) != raw
        or manifest.inventory.evaluation_root != evaluation_root
    ):
        raise ValueError("Lookup input manifest has inconsistent canonical metadata.")

    return manifest.inventory


def _store_manifest(reference: EvaluationStore) -> EvaluationStoreManifest:
    """Validate stored manifest bytes independently of current snapshot readers.

    Parameters
    ----------
    reference
        Exact manifest path and byte identity.

    Returns
    -------
    EvaluationStoreManifest
        Canonical lookup record, requiring full validation before reuse.

    Raises
    ------
    ValueError
        If metadata is unsupported, corrupt or bound to another directory.
    """

    raw = _store_read(reference.manifest_path)

    if hashlib.sha256(raw).hexdigest() != reference.content_hash:
        raise ValueError("Evaluation store manifest content differs.")

    material = _decode_snapshot_json(raw)

    if (
        not isinstance(material, dict)
        or material.get("kind") != "lp_evaluation_store_v2"
    ):
        raise ValueError(
            f"Unsupported evaluator store: {reference.manifest_path}. "
            f"This version requires a fresh evaluation; old progress cannot be imported. "
            f"Remove the old evaluator results separately before rerunning."
        )

    manifest = TypeAdapter(EvaluationStoreManifest).validate_json(raw)

    if manifest.created_at.utcoffset() is None:
        raise ValueError("Invocation creation time must include a timezone.")

    if (
        material
        != TypeAdapter(EvaluationStoreManifest).dump_python(manifest, mode="json")
        or raw != canonical_lp_json(material).encode()
    ):
        raise ValueError("Evaluation store manifest is not exact canonical material.")

    if reference.manifest_path.parent.name != manifest.schedule_content_hash:
        raise ValueError("Invocation directory differs from its schedule identity.")

    return manifest


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


def _store_schedule(reference: EvaluationStore) -> EvaluationSchedule:
    """Validate stored lookup identity before classifying an invocation as a match.

    Parameters
    ----------
    reference
        Pinned manifest whose schedule and settings must agree.

    Returns
    -------
    EvaluationSchedule
        Canonical recorded schedule, pending full input and material revalidation.

    Raises
    ------
    ValueError
        If recorded bytes, hashes, selection or lookup identity differ.
    """

    manifest = _store_manifest(reference)
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

    material = TypeAdapter(EvaluationSchedule).dump_python(schedule, mode="json")
    del material["material_content_hash"]

    if lp_material_content_hash(material) != schedule.material_content_hash:
        raise ValueError("Lookup schedule material hash differs.")

    inventory = _store_inventory(inputs=schedule.inputs, reference=reference)
    selector = _store_selector(
        judge=schedule.judge,
        results_root=inventory.results_root,
        settings=schedule.settings,
    )

    if manifest.selector_hash != selector:
        raise ValueError("Invocation lookup identity differs from its frozen settings.")

    return schedule


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
        Canonical starting directory recorded at preparation; no filesystem lookup.
    settings
        Effective sampling and repetition controls.

    Returns
    -------
    str
        Lookup identity independent of later directory contents.
    """

    if not results_root.is_absolute() or ".." in results_root.parts:
        raise ValueError("Lookup root must be an absolute canonical recorded path.")

    return lp_material_content_hash(
        {
            "judge": TypeAdapter(ResolvedJudgeSettings).dump_python(judge, mode="json"),
            "results_root": str(results_root),
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


def _usage_count(value: Any) -> int | None:
    """Retain a valid observed token count and mark unavailable counters unknown.

    Parameters
    ----------
    value
        Provider counter, possibly absent.

    Returns
    -------
    int | None
        Exact nonnegative integer, or unknown for absent/invalid accounting.
    """

    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


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

    if any(
        item.event == "failed"
        and not _retryable_event(item)
        and item.execution_number == event.execution_number
        for item in latest.values()
    ):
        raise ValueError("Execution has stopped; only active calls may finish.")

    if not set(request.dependencies) <= judgments.keys():
        raise ValueError("Associated blind judgments must be frozen before critique.")

    if sum(item.event == "started" for item in latest.values()) >= JUDGE_CONCURRENCY:
        raise ValueError("Evaluation attempt concurrency is already occupied.")

    if previous is None:
        if event.attempt_number != 1:
            raise ValueError("First attempt number must be one.")

        return

    if previous.event != "failed":
        raise ValueError("A successful or unfinished request cannot be retried.")

    if (
        event.attempt_number != previous.attempt_number + 1
        or event.timestamp < previous.timestamp
    ):
        raise ValueError("Retry sequence or timestamp differs from prior evidence.")

    if (
        not _retryable_event(previous)
        and event.execution_number <= previous.execution_number
    ):
        raise ValueError("Exhausted retry cycle requires a new user execution.")


def _validate_schedule(schedule: EvaluationSchedule) -> None:
    """Require full deterministic reproduction using identical frozen material.

    Parameters
    ----------
    schedule
        Persisted or newly materialized plan.

    Raises
    ------
    ValueError
        If inputs or any material schedule field differ.
    """

    material = TypeAdapter(EvaluationSchedule).dump_python(schedule, mode="json")
    del material["material_content_hash"]

    if lp_material_content_hash(material) != schedule.material_content_hash:
        raise ValueError("Evaluation schedule material hash differs.")

    expected = prepare_evaluation_schedule(
        identity=schedule,
        inputs=schedule.inputs,
        judge=schedule.judge,
        settings=schedule.settings,
    )

    if expected.material_content_hash != schedule.material_content_hash:
        raise ValueError("Evaluation schedule cannot be reproduced from frozen inputs.")


@contextmanager
def _validated_execution_session(
    *, reference: EvaluationStore, transport: JudgeTransport
) -> Iterator[EvaluationSession]:
    """Open a revalidated invocation and check its exact transport settings.

    Parameters
    ----------
    reference
        Frozen invocation reference.
    transport
        Caller-supplied single-attempt boundary.

    Yields
    ------
    EvaluationSession
        Sole writer with checked model configuration.

    Raises
    ------
    ValueError
        If the transport changes the frozen provider, model or settings.
    """

    with open_evaluation_store(reference) as session:
        validate_judge_settings(session.schedule.judge)

        if transport.settings != session.schedule.judge:
            raise ValueError("Transport settings differ from the frozen judge.")

        yield session


async def _wait_for_retry(
    *, delay: float, sleep: Callable[[float], Awaitable[None]], stop: asyncio.Event
) -> None:
    """Wait the configured delay unless terminal failure closes admission first.

    Parameters
    ----------
    delay
        Required retry delay in seconds.
    sleep
        Injectable awaitable sleep.
    stop
        Shared terminal-failure signal.

    Raises
    ------
    asyncio.CancelledError
        If execution is interrupted; both wait tasks are cleaned up.
    """

    sleeper = asyncio.ensure_future(sleep(delay))
    stopped = asyncio.create_task(stop.wait())

    try:
        done, _ = await asyncio.wait(
            {sleeper, stopped}, return_when=asyncio.FIRST_COMPLETED
        )

        if sleeper in done:
            sleeper.result()
    finally:
        for task in (sleeper, stopped):
            if not task.done():
                task.cancel()

        await asyncio.gather(sleeper, stopped, return_exceptions=True)


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


async def execute_evaluation(
    *,
    reference: EvaluationStore,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    transport: JudgeTransport,
) -> EvaluationCache:
    """Execute or resume the complete frozen schedule through an injectable transport.

    This callable can make external requests. Its caller owns execution authorization.
    Fully cached schedules return without model preflight or repeated generation.

    Parameters
    ----------
    reference
        Exact persisted invocation; no discovery occurs during execution.
    sleep
        Retry wait seam, defaulting to actual asynchronous 5/20-second waits.
    transport
        Fresh-context, single-attempt provider boundary with frozen settings.

    Returns
    -------
    EvaluationCache
        Complete validated responses and all attempt evidence, ready for reporting.

    Raises
    ------
    JudgeExecutionError
        If preflight, prior unresolved attempts or execution prevents completion.
    ValueError
        If frozen inputs, cache, schedule or transport settings fail validation.
    asyncio.CancelledError
        If the caller interrupts execution; committed evidence remains intact.
    """

    with _validated_execution_session(
        reference=reference, transport=transport
    ) as session:
        cache = session.snapshot()

        if len(cache.judgments) == session.schedule.total_requests:
            return cache

        watch = _MaterialWatch(reference=reference, schedule=session.schedule)
        execution = _Execution(
            check_material=watch.check,
            session=session,
            sleep=sleep,
            transport=transport,
        )
        execution._resume_ready()

        try:
            async with asyncio.timeout(JUDGE_ATTEMPT_TIMEOUT_SECONDS):
                await transport.preflight()
        except Exception as error:
            failure = _classify_judge_error(error)
            raise JudgeExecutionError(
                cache=cache, message=f"Judge preflight failed: {failure}"
            ) from None

        # Preflight may wait on the network. Recheck material before any generation.
        _validate_schedule(session.schedule)
        session.begin_execution()
        cache = await execution.run()
        _validate_schedule(session.schedule)
        watch.check()
        return cache


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
        Newest validated incomplete match, then newest complete match, or None.

    Raises
    ------
    ValueError
        If a matching invocation is unsupported, locked, corrupt or stale. Such a
        blocker never permits fallback to fresh discovery.
    """

    root = repository_root.resolve(strict=True) / "results" / "lp_evals" / "invocations"
    _store_path(root)

    if not root.exists():
        return None

    selector = _store_selector(
        judge=judge, results_root=results_root.resolve(strict=True), settings=settings
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
        manifest = _store_manifest(reference)
        _store_schedule(reference)

        if manifest.selector_hash != selector:
            continue

        try:
            with open_evaluation_store(reference) as session:
                incomplete = (
                    len(session.snapshot().judgments) < session.schedule.total_requests
                )
        except (ValueError, OSError, sqlite3.Error) as error:
            raise ValueError(
                f"Cannot resume matching invocation {reference.manifest_path}: {error}"
            ) from error

        matches.append(
            (incomplete, manifest.created_at, manifest.schedule_content_hash, reference)
        )

    return max(matches, key=lambda item: item[:3])[3] if matches else None


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
        If serialized data, inputs or complete schedule differ.
    """

    schedule = _store_schedule(reference)
    _validate_schedule(schedule)
    return schedule


@contextmanager
def open_evaluation_command(repository_root: Path) -> Iterator[None]:
    """Keep selection, preparation, execution and reporting under one command lock.

    Parameters
    ----------
    repository_root
        Repository owning the separate evaluator output directory.

    Yields
    ------
    None
        Exclusive command ownership; another command fails instead of dispatching.

    Raises
    ------
    ValueError
        If output paths are unsafe or another command already owns the lock.
    """

    output = repository_root.resolve(strict=True) / "results" / "lp_evals"
    _store_path(output)
    output.mkdir(parents=True, exist_ok=True)

    with _exclusive_store_lock(output / ".command.lock"):
        yield


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

            manifest_hash = connection.execute(
                "SELECT value FROM metadata WHERE key = 'manifest'"
            ).fetchone()

            if manifest_hash != (reference.content_hash,):
                raise ValueError("Invocation manifest differs from its ledger binding.")

            events = _database_events(
                connection=connection,
                schedule_hash=schedule.material_content_hash,
            )
            executions = _database_executions(
                connection=connection, schedule_hash=schedule.material_content_hash
            )
            yield EvaluationSession(
                connection=connection,
                events=events,
                executions=executions,
                schedule=schedule,
            )
        finally:
            connection.close()


@asynccontextmanager
async def open_judge_transport(
    settings: ResolvedJudgeSettings,
) -> AsyncIterator[JudgeTransport]:
    """Create isolated Pydantic AI providers with all hidden retries disabled.

    Shared KG model settings remain authoritative. The small SDK client override
    follows the existing LP dispatch isolation pattern and changes retry behavior only.

    Parameters
    ----------
    settings
        Exact frozen dedicated evaluator settings.

    Yields
    ------
    JudgeTransport
        Fresh-agent transport using an evaluator-owned HTTP pool.

    Raises
    ------
    ValueError
        If settings or provider credentials are missing.
    """

    validate_judge_settings(settings)
    key_name = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[
        settings.provider
    ]
    api_key = (
        os.environ.get(key_name)
        or getattr(Settings, key_name, None)
        or getattr(Settings, key_name.lower(), None)
    )

    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError(f"Missing {key_name} for LP evaluation.")

    async def _capture_usage(response: httpx.Response) -> None:
        """Capture usage and validate output before the SDK can drop response items.

        Parameters
        ----------
        response
            Nonstreaming provider response; input messages and headers are not logged.
        """

        observed = _ATTEMPT_USAGE.get()

        if observed is None:
            return

        await response.aread()
        problem = None

        try:
            material = response.json()
        except ValueError:
            material = None
            problem = "Provider returned malformed JSON in a successful HTTP response."

        observed[0] = _provider_usage(
            material=material if isinstance(material, dict) else {},
            provider=settings.provider,
            request_id=response.headers.get("request-id")
            or response.headers.get("x-request-id"),
        )
        output = _ATTEMPT_OUTPUT.get()

        if response.is_success and output is not None:
            if isinstance(material, dict):
                output[0] = _provider_output(
                    material=material, provider=settings.provider
                )
            else:
                # Without an object envelope, output fields cannot be isolated safely.
                # Retain the failure without logging the body or inventing usage.
                output[0] = _ProviderOutput(
                    failure=JudgeCallError(
                        category="invalid_output",
                        message=problem
                        or "Provider returned non-object JSON in a successful HTTP response.",
                    ),
                    raw_response=None,
                )

    async with httpx.AsyncClient(
        event_hooks={"response": [_capture_usage]},
        follow_redirects=False,
        limits=httpx.Limits(max_connections=JUDGE_CONCURRENCY),
        timeout=JUDGE_ATTEMPT_TIMEOUT_SECONDS,
        transport=httpx.AsyncHTTPTransport(retries=0),
    ) as http:
        model: AnthropicModel | OpenAIResponsesModel
        name = settings.model.partition(":")[2]

        if settings.provider == "anthropic":
            model = AnthropicModel(
                name,
                profile=judge_output_profile(
                    ModelConfig.model_validate_json(settings.model_config_json)
                ),
                provider=AnthropicProvider(
                    api_key=api_key,
                    base_url="https://api.anthropic.com",
                    http_client=http,
                ),
            )
        else:
            model = OpenAIResponsesModel(
                name,
                provider=OpenAIProvider(
                    api_key=api_key,
                    base_url="https://api.openai.com/v1",
                    http_client=http,
                ),
            )

        model.client = model.client.with_options(
            max_retries=0, timeout=JUDGE_ATTEMPT_TIMEOUT_SECONDS
        )

        try:
            yield _ProviderJudge(model=model, settings=settings)
        finally:
            await model.client.close()


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
        output_contract_json=judge_output_contract(model_config),
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


def validate_judge_settings(settings: ResolvedJudgeSettings) -> None:
    """Check complete frozen model/settings consistency without client construction.

    Parameters
    ----------
    settings
        Captured dedicated model, shared registry settings and finite execution policy.

    Raises
    ------
    ValueError
        If any captured setting is malformed, inconsistent or unsupported.
    """

    config = ModelConfig.model_validate_json(settings.model_config_json)

    if settings.provider not in {"anthropic", "openai"}:
        raise ValueError("Unsupported LP judge provider.")

    provider, separator, name = settings.model.partition(":")

    if (
        not separator
        or not name
        or ":" in name
        or provider != settings.provider
        or any(character.isspace() for character in settings.model)
    ):
        raise ValueError("Invalid LP judge model identifier.")

    if config.model != settings.model or config.provider != settings.provider:
        raise ValueError("Frozen judge model configuration is inconsistent.")

    effective = canonical_lp_json(dict(config.kgs_settings("learning_progressions")))

    if effective != settings.model_settings_json:
        raise ValueError("Frozen judge settings differ from the shared registry.")

    if settings.output_contract_json != judge_output_contract(config):
        raise ValueError("Frozen judge structured output contract is incompatible.")

    expected = {
        "attempt_timeout_seconds": JUDGE_ATTEMPT_TIMEOUT_SECONDS,
        "concurrency": JUDGE_CONCURRENCY,
        "max_retries": JUDGE_MAX_RETRIES,
        "retry_waits_seconds": list(JUDGE_RETRY_WAITS_SECONDS),
        "sdk_max_retries": 0,
    }

    if json.loads(settings.execution_json) != expected:
        raise ValueError("Frozen judge execution settings are unsupported.")
