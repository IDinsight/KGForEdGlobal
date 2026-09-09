"""Red-team ordered adjudication, durable recovery, and offline failure accounting."""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib
import json
import socket
import subprocess
import sys

from copy import deepcopy
from pathlib import Path
from textwrap import dedent
from typing import Any
from unittest.mock import Mock
from uuid import UUID

# Third Party Library
import pytest

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import agents, llm, lp_checkpoints, lp_generation, lp_requests
from kgfeg.kgs.llm import KGUsageTracker
from kgfeg.kgs.lp_generation import LPGenerationFailed
from kgfeg.kgs.lp_requests import LPGenerationRequest
from kgfeg.kgs.schemas import LPGenerationResponse, LPGenerationValidationVerdict
from kgfeg.kgs.utils import KGDirs
from kgfeg.model_registry import ModelConfig
from tests.kgfeg.kgs import test_lp_checker as _checker
from tests.kgfeg.kgs import test_lp_generation as _fixtures

_DRAFT = "lp_generation_draft_responses.jsonl"
_VERDICT = "lp_generation_validation_verdicts.jsonl"
_RESPONSE = "lp_generation_responses.jsonl"
_FAILURE = "lp_generation_failures.json"
_RECEIPT = "lp_generation_checkpoint_manifest.json"
_JOURNAL = "lp_generation_checkpoint_transaction.json"
_INPUTS = (
    "lp_candidate_pairs.jsonl",
    "lp_candidate_summary.json",
    "lp_generation_requests.jsonl",
    "lp_generation_requests_manifest.json",
)
_STORED = (_DRAFT, _VERDICT, _RESPONSE, _FAILURE, _RECEIPT)
_PROFILES = (
    "ghana_english",
    "ghana_math",
    "madhi_math",
    "nigeria_math",
    "pratham_science",
    "rwanda_math",
)


class _Harness:
    """Keep isolated inputs and deterministic proposals outside persistence logic."""

    def __init__(self, *, batch: int = 1, count: int = 3, root: Path) -> None:
        """Construct an independent small same-rank candidate cohort.

        Parameters
        ----------
        batch
            Maximum pairs per bounded request.
        count
            Number of synthetic standards.
        root
            Isolated output directory.
        """
        self.bundle = _fixtures._bundle(count)
        self.calls: list[tuple[str, int]] = []
        self.config = _fixtures._config(batch=batch)
        self.config.learning_progressions.retry.producer_max_retries = 0
        self.config.learning_progressions.retry.checker_max_retries = 0
        self.corrects = False
        self.decision = "no_relation"
        self.root = root
        self.script: dict[tuple[str, int], list[Any]] = {}
        self.tracker = KGUsageTracker()

    def _call(self, **kwargs: Any) -> Any:
        """Inspect actual pre-call files before supplying one untrusted proposal.

        Parameters
        ----------
        kwargs
            Production call boundary arguments.

        Returns
        -------
        Any
            Synthetic stage result or scripted exception.
        """
        _assert_population(self.root)
        request = kwargs["request"]
        stage = "draft" if kwargs["draft"] is None else "verdict"
        key = (stage, request.request_index)
        self.calls.append(key)
        if self.script.get(key):
            action = self.script[key].pop(0)
            if isinstance(action, BaseException):
                raise action
            if action is not None:
                return action
        response = _checker._draft(request)
        for judgment in response.judgments:
            judgment.decision = self.decision
            judgment.direction = (
                "first_to_second" if self.decision == "buildsTowards" else None
            )
        if stage == "draft":
            return response
        assert kwargs["draft"].request_id == request.request_id
        correction = response.model_copy(deep=True) if self.corrects else None
        if correction is not None:
            for judgment in correction.judgments:
                judgment.decision = "needs_review"
                judgment.direction = None
        return _checker._verdict(correction=correction, request=request)

    def _run(self, *, overwrite: bool = False) -> tuple[LPGenerationResponse, ...]:
        """Invoke the public orchestration boundary with isolated material.

        Parameters
        ----------
        overwrite
            Explicitly archive and regenerate prior execution evidence.

        Returns
        -------
        tuple[LPGenerationResponse, ...]
            Complete validated processing outcomes.
        """
        return lp_generation.generate_learning_progressions(
            as_lc_bundle=self.bundle,
            doc_key="synthetic-selection-document",
            kg_config=self.config,
            kg_dirs=KGDirs(root=self.root),
            overwrite=overwrite,
            usage_tracker=self.tracker,
        )


class _Interruption(BaseException):
    """Emulate process interruption outside ordinary model-error retry handling."""


def _assert_population(root: Path) -> None:
    """Independently reconcile all population bytes, identities, coverage, and counts.

    Parameters
    ----------
    root
        Directory observed at the model-call boundary.
    """
    candidates = _rows(root / _INPUTS[0])
    requests = _rows(root / _INPUTS[2])
    manifest = json.loads((root / _INPUTS[3]).read_bytes())
    ids = [row["pair_id"] for row in candidates]
    assert len(ids) == len(set(ids)) == manifest["total_candidate_pairs"]
    assert ids == manifest["pair_ids"]
    assert ids == [pair["pair_id"] for row in requests for pair in row["pairs"]]
    assert len(requests) == manifest["total_requests"]
    assert [row["request_index"] for row in requests] == list(range(len(requests)))
    assert [row["request_id"] for row in requests] == manifest["request_ids"]
    assert manifest["artifact_byte_hashes"] == {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in _INPUTS[:3]
    }
    # Population content hashes use canonical arrays without a terminal newline.
    for field, value in (
        ("candidate_pairs_content_hash", candidates),
        (
            "candidate_summary_content_hash",
            json.loads((root / _INPUTS[1]).read_bytes()),
        ),
        ("requests_content_hash", requests),
    ):
        assert (
            manifest[field] == hashlib.sha256(_bytes(value).rstrip(b"\n")).hexdigest()
        )
    for row in requests:
        content = {
            k: v
            for k, v in row.items()
            if k not in {"request_id", "request_content_hash"}
        }
        assert (
            row["request_content_hash"]
            == hashlib.sha256(_bytes(content).rstrip(b"\n")).hexdigest()
        )


def _assert_stage_usage(
    *,
    calls: list[str],
    retries: int,
    stage: str,
    succeeds: bool,
    tracker: KGUsageTracker,
) -> None:
    """Reconcile retry counts and usage against observed local model calls.

    Parameters
    ----------
    calls
        Stage names recorded at each local model invocation.
    retries
        Additional attempts permitted for the failing stage.
    stage
        Stage whose model first produces malformed output.
    succeeds
        Whether the final permitted model attempt succeeds.
    tracker
        Actual usage accumulated by orchestration.
    """
    assert calls.count(stage) == retries + 1
    other_calls = int(succeeds or stage == "verdict")
    assert calls.count("verdict" if stage == "draft" else "draft") == other_calls
    for name, bucket in (
        ("draft", tracker.lp_generation),
        ("verdict", tracker.lp_generation_validation),
    ):
        count = calls.count(name)
        assert bucket.requests == count
        assert bucket.input_tokens == 7 * count
        assert bucket.output_tokens == 3 * count
    assert tracker.to_dict()["totals"]["requests"] == len(calls)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid provider and other external connections in all execution tests.

    Parameters
    ----------
    monkeypatch
        Restoring network guard and fixed non-secret model identity.
    """
    guard = Mock(side_effect=AssertionError("Orchestration tests must remain offline."))
    monkeypatch.setattr(name="connect", target=socket.socket, value=guard)
    monkeypatch.setattr(name="connect_ex", target=socket.socket, value=guard)
    monkeypatch.setattr(name="create_connection", target=socket, value=guard)
    monkeypatch.setattr(name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.2")


def _bytes(value: Any) -> bytes:
    """Serialize independently specified canonical artifact bytes.

    Parameters
    ----------
    value
        JSON-compatible material.

    Returns
    -------
    bytes
        Sorted finite compact UTF-8 JSON followed by one newline.
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
    ).encode()


def _install(*, harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only the external call boundary while retaining real orchestration.

    Parameters
    ----------
    harness
        Synthetic script and observed calls.
    monkeypatch
        Restoring test seam.
    """
    monkeypatch.setattr(
        name="generate_learning_progressions_for_request",
        target=lp_generation,
        value=harness._call,
    )


def _reseal(*, name: str, root: Path) -> None:
    """Update outer byte receipts so attacks reach inner structural validation.

    Parameters
    ----------
    name
        Intentionally altered artifact.
    root
        Isolated artifact directory.
    """
    receipt = json.loads((root / _RECEIPT).read_bytes())
    receipt["artifact_byte_hashes"][name] = hashlib.sha256(
        (root / name).read_bytes()
    ).hexdigest()
    if name in (_DRAFT, _VERDICT, _RESPONSE):
        stage = {_DRAFT: "draft", _VERDICT: "verdict", _RESPONSE: "response"}[name]
        receipt["stage_counts"][stage] = len(_rows(root / name))
    (root / _RECEIPT).write_bytes(_bytes(receipt))


def _rows(path: Path) -> list[dict[str, Any]]:
    """Parse each actual JSONL row without a production loader.

    Parameters
    ----------
    path
        Complete stage or population artifact.

    Returns
    -------
    list[dict[str, Any]]
        Ordered parsed row contents.
    """
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _snapshot(root: Path) -> dict[str, bytes]:
    """Capture evidence exactly, excluding the writer's empty lock file.

    Parameters
    ----------
    root
        Isolated generation directory.

    Returns
    -------
    dict[str, bytes]
        Material evidence used to prove rejection is non-mutating.
    """
    return {
        p.name: p.read_bytes()
        for p in root.iterdir()
        if p.is_file() and p.name != ".lp_generation.lock"
    }


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
@pytest.mark.parametrize(argnames="retries", argvalues=[0, 2])
@pytest.mark.parametrize(argnames="succeeds", argvalues=[False, True])
def test_actual_agents_keep_independent_evidence_exact_retries_and_usage(
    monkeypatch: pytest.MonkeyPatch,
    retries: int,
    stage: str,
    succeeds: bool,
    tmp_path: Path,
) -> None:
    """Execute real agents and validators with tool-based local models and exact usage.

    Parameters
    ----------
    monkeypatch
        Substitute only model transport while inspecting factory arguments.
    retries
        Additional stage attempts allowed by orchestration.
    stage
        Stage whose model first produces malformed output.
    succeeds
        Whether the final permitted model attempt succeeds.
    tmp_path
        Isolated material and checkpoint directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    harness.config.learning_progressions.retry.producer_max_retries = retries
    harness.config.learning_progressions.retry.checker_max_retries = retries
    request = lp_requests.build_lp_generation_requests(
        as_lc_bundle=harness.bundle,
        doc_key="synthetic-selection-document",
        kg_config=harness.config,
    ).requests[0]
    producer_factory = agents.create_lp_generation_agent
    checker_factory = agents.create_lp_generation_validation_agent
    calls: list[str] = []
    factories: list[tuple[str, Any]] = []

    def _factory(**kwargs: Any) -> Any:
        """Preserve the production agent factory and replace its model transport.

        Parameters
        ----------
        kwargs
            Real instructions, validator, request, and retry arguments.

        Returns
        -------
        Any
            Real independent agent using a local deterministic function model.
        """
        current = "verdict" if "draft_response" in kwargs else "draft"
        assert kwargs["max_retries"] == 0
        original_model = kwargs["model_config"]
        assert original_model.model == "openai:gpt-5.2"
        factories.append((current, kwargs))
        payload = (
            _checker._draft(request)
            if current == "draft"
            else _checker._verdict(request=request)
        ).model_dump(mode="json")

        def _respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
            """Follow the model callback's required positional argument protocol.

            Parameters
            ----------
            messages
                Fresh independent agent messages.
            info
                Structured output schema and allowed tools.

            Returns
            -------
            ModelResponse
                Synthetic structured result and deterministic billed token usage.
            """
            _assert_population(tmp_path)
            calls.append(current)
            assert not info.function_tools
            assert len(messages) == 1
            content = "\n".join(
                part.content
                for message in messages
                for part in message.parts
                if part.part_kind == "user-prompt"
            )
            if current == "verdict":
                sent = json.loads(content.split("## Draft and request JSON\n")[1])
                assert sent == {
                    "draft_response": _checker._draft(request).model_dump(mode="json"),
                    "lp_generation_request": request.model_dump(mode="json"),
                }
            else:
                assert str(request.request_id) in content
                assert request.request_content_hash in content
            malformed = current == stage and (
                not succeeds or calls.count(current) <= retries
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        args={} if malformed else payload,
                        tool_name=info.output_tools[0].name,
                    )
                ],
                usage=RequestUsage(input_tokens=7, output_tokens=3),
            )

        model = Mock(spec=ModelConfig)
        model.model = FunctionModel(function=_respond)
        model.kgs_settings.return_value = original_model.kgs_settings(
            "learning_progressions"
        )
        model.wrap_output_type.side_effect = original_model.wrap_output_type
        new_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in {"instructions", "max_retries", "model_config"}
        }
        return (producer_factory if current == "draft" else checker_factory)(
            instructions=kwargs["instructions"],
            max_retries=kwargs["max_retries"],
            model_config=model,
            **new_kwargs,
        )

    monkeypatch.setattr(name="create_lp_generation_agent", target=llm, value=_factory)
    monkeypatch.setattr(
        name="create_lp_generation_validation_agent", target=llm, value=_factory
    )
    if succeeds:
        assert len(harness._run()) == 1
    else:
        with pytest.raises(expected_exception=LPGenerationFailed):
            harness._run()
    assert [s for s, _ in factories] == calls
    _assert_stage_usage(
        calls=calls,
        retries=retries,
        stage=stage,
        succeeds=succeeds,
        tracker=harness.tracker,
    )
    failures = json.loads((tmp_path / _FAILURE).read_bytes())
    assert len(failures) == retries + int(not succeeds)
    if succeeds:
        calls.clear()
        before_usage = deepcopy(harness.tracker.to_dict())
        harness._run()
        assert not calls
        assert harness.tracker.to_dict() == before_usage


@pytest.mark.parametrize(argnames="after", argvalues=[False, True])
@pytest.mark.parametrize(argnames="name", argvalues=[_JOURNAL, *_STORED])
def test_atomic_rename_interruptions_preserve_durable_draft_without_repeated_call(
    after: bool, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Interrupt actual renames inside atomic writes, including before directory sync.

    Parameters
    ----------
    after
        Interrupt after rename instead of before it.
    monkeypatch
        Restoring low-level filesystem fault injection.
    name
        Artifact whose draft transaction rename is interrupted.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    original = lp_checkpoints.os.replace
    transaction = 0

    def _replace(*, dst: Path, src: str) -> None:
        """Interrupt one rename while retaining the real atomic write implementation.

        Parameters
        ----------
        dst
            Final artifact path.
        src
            Fully written and flushed temporary file.
        """
        nonlocal transaction
        if dst.name == _JOURNAL:
            transaction += 1
        hit = transaction == 3 and dst.name == name
        if hit and not after:
            raise _Interruption()
        original(dst=dst, src=src)
        if hit and after:
            raise _Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="replace", target=lp_checkpoints.os, value=_replace)
        with pytest.raises(expected_exception=_Interruption):
            harness._run()
    assert harness.calls == [("draft", 0)]
    harness.calls.clear()
    harness._run()
    assert harness.calls == (
        [("draft", 0), ("verdict", 0)]
        if name == _JOURNAL and not after
        else [("verdict", 0)]
    )
    assert not (tmp_path / _JOURNAL).exists()


@pytest.mark.parametrize(argnames="corrects", argvalues=[False, True])
@pytest.mark.parametrize(
    argnames="decision",
    argvalues=["buildsTowards", "relatesTo", "no_relation", "needs_review"],
)
def test_complete_adjudication_preserves_decisions_corrections_and_zero_call_reuse(
    corrects: bool, decision: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Require complete ordered batches and full checker replacement without repeat calls.

    Parameters
    ----------
    corrects
        Whether every checker supplies a complete replacement.
    decision
        Synthetic producer judgment category.
    monkeypatch
        Restoring offline seam.
    tmp_path
        Isolated generation directory.
    """
    harness = _Harness(batch=2, count=4, root=tmp_path)
    harness.corrects, harness.decision = corrects, decision
    _install(harness=harness, monkeypatch=monkeypatch)
    outcomes = harness._run()
    assert len(outcomes) == 3
    assert harness.calls == [(s, i) for i in range(3) for s in ("draft", "verdict")]
    expected = "needs_review" if corrects else decision
    assert [j.decision for r in outcomes for j in r.judgments] == [expected] * 6
    for name, schema in (
        (_DRAFT, LPGenerationResponse),
        (_VERDICT, LPGenerationValidationVerdict),
        (_RESPONSE, LPGenerationResponse),
    ):
        rows = _rows(tmp_path / name)
        assert [r["request_index"] for r in rows] == [0, 1, 2]
        for row in rows:
            assert (
                schema.model_validate(row["payload"]).model_dump(mode="json")
                == row["payload"]
            )
            assert (
                row["payload_content_hash"]
                == hashlib.sha256(_bytes(row["payload"])).hexdigest()
            )
    assert json.loads((tmp_path / _FAILURE).read_bytes()) == []
    receipt = json.loads((tmp_path / _RECEIPT).read_bytes())
    assert receipt["status"] == "completed"
    assert receipt["failed_pair_ids"] == []
    assert receipt["stage_counts"] == {"draft": 3, "verdict": 3, "response": 3}
    saved = {name: (tmp_path / name).read_bytes() for name in (*_INPUTS, *_STORED[:-1])}
    harness.calls.clear()
    assert harness._run() == outcomes
    assert not harness.calls
    assert saved == {name: (tmp_path / name).read_bytes() for name in saved}


@pytest.mark.parametrize(argnames="name", argvalues=[*_INPUTS, *_STORED])
@pytest.mark.parametrize(
    argnames="attack",
    argvalues=["missing", "truncated", "invalid", "blank_line", "duplicate_key"],
)
def test_corrupted_artifacts_fail_closed_before_calls_and_preserve_evidence(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Reject incomplete or noncanonical evidence without repairing arbitrary bytes.

    Parameters
    ----------
    attack
        Missing file or malformed serialization.
    monkeypatch
        Restoring offline seam.
    name
        Required material artifact to damage.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    path = tmp_path / name
    original = path.read_bytes()
    if attack == "missing":
        path.unlink()
    elif attack == "truncated":
        path.write_bytes(original[:-1])
    elif attack == "invalid":
        path.write_bytes(b"{invalid\n")
    elif attack == "blank_line":
        path.write_bytes(original + b"\n")
    else:
        path.write_bytes(b'{"duplicate":1,"duplicate":2}\n')
    before = _snapshot(tmp_path)
    harness.calls.clear()
    with pytest.raises(expected_exception=(ValueError, OSError)):
        harness._run()
    assert not harness.calls
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="failed", argvalues=[False, True])
def test_explicit_regeneration_archives_exact_old_material_and_failure_evidence(
    failed: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retain exact old evidence and a content-addressed disposition before regeneration.

    Parameters
    ----------
    failed
        Whether prior execution exhausted checker attempts.
    monkeypatch
        Restoring offline stage seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    if failed:
        harness.script[("verdict", 0)] = [TimeoutError("synthetic failure")]
        with pytest.raises(expected_exception=LPGenerationFailed):
            harness._run()
    else:
        harness._run()
    before = _snapshot(tmp_path)
    hashes = {
        name: hashlib.sha256(payload).hexdigest() for name, payload in before.items()
    }
    archive_id = hashlib.sha256(_bytes(hashes)).hexdigest()
    harness.config.learning_progressions.producer_instructions += (
        " Regenerated synthetic instruction."
    )
    harness.calls.clear()
    harness._run(overwrite=True)
    archive = tmp_path / "lp_generation_history" / archive_id
    assert {name: (archive / name).read_bytes() for name in before} == before
    assert json.loads((archive / "disposition.json").read_bytes()) == {
        "artifact_byte_hashes": hashes,
        "disposition": "superseded_by_explicit_regeneration",
    }
    assert harness.calls == [("draft", 0), ("verdict", 0)]
    assert json.loads((tmp_path / _FAILURE).read_bytes()) == []
    assert json.loads((tmp_path / _RECEIPT).read_bytes())["run_number"] == 1


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
@pytest.mark.parametrize(argnames="retries", argvalues=[0, 2])
@pytest.mark.parametrize(argnames="succeeds", argvalues=[False, True])
def test_failure_budgets_halt_and_preserve_history_with_resumed_dispositions(
    monkeypatch: pytest.MonkeyPatch,
    retries: int,
    stage: str,
    succeeds: bool,
    tmp_path: Path,
) -> None:
    """Enforce exact retries, stop after one failed batch, and retain later resolution.

    Parameters
    ----------
    monkeypatch
        Restoring offline seam.
    retries
        Additional configured attempts.
    stage
        Failing producer or checker.
    succeeds
        Whether the last permitted attempt succeeds within this invocation.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(batch=2, count=4, root=tmp_path)
    retry = harness.config.learning_progressions.retry
    retry.producer_max_retries = retries if stage == "draft" else 0
    retry.checker_max_retries = retries if stage == "verdict" else 0
    failures = retries if succeeds else retries + 1
    harness.script[(stage, 1)] = [
        TimeoutError("synthetic private diagnostic")
    ] * failures
    _install(harness=harness, monkeypatch=monkeypatch)
    if succeeds:
        result = harness._run()
    else:
        with pytest.raises(
            expected_exception=LPGenerationFailed, match="processing halted"
        ):
            harness._run()
        assert ("draft", 2) not in harness.calls
        assert len(_rows(tmp_path / _RESPONSE)) == 1
        assert len(_rows(tmp_path / _DRAFT)) == (1 if stage == "draft" else 2)
        receipt = json.loads((tmp_path / _RECEIPT).read_bytes())
        assert receipt["status"] == "failed"
        request = _rows(tmp_path / _INPUTS[2])[1]
        assert receipt["failed_pair_ids"] == sorted(
            p["pair_id"] for p in request["pairs"]
        )
        assert harness.calls.count((stage, 1)) == retries + 1
        harness.calls.clear()
        result = harness._run()
        assert harness.calls[0] == (stage, 1)
        assert ("draft", 0) not in harness.calls
        if stage == "verdict":
            assert ("draft", 1) not in harness.calls
    history = json.loads((tmp_path / _FAILURE).read_bytes())
    assert len(result) == 3
    assert len(history) == failures
    assert [f["attempt"] for f in history] == list(range(1, failures + 1))
    assert [f["exhausted"] for f in history] == (
        [False] * retries + [True] if not succeeds else [False] * retries
    )
    assert all(f["resolved_run_number"] == (1 if succeeds else 2) for f in history)
    assert all(
        f["resolved_response_content_hash"]
        == _rows(tmp_path / _RESPONSE)[1]["payload_content_hash"]
        for f in history
    )
    assert b"synthetic private diagnostic" not in (tmp_path / _FAILURE).read_bytes()
    assert json.loads((tmp_path / _RECEIPT).read_bytes())["status"] == "completed"


@pytest.mark.parametrize(
    argnames="field,value",
    argvalues=[
        ("attempt", 2),
        ("exhausted", False),
        ("pair_ids", ["foreign"]),
        ("request_id", str(UUID(int=900))),
        ("request_index", 2),
        ("request_content_hash", "0" * 64),
        ("run_number", 9),
        ("stage", "verdict"),
        ("resolved_run_number", 1),
        ("resolved_response_content_hash", "0" * 64),
    ],
)
def test_failure_history_rejects_resealed_false_attempts_identity_and_dispositions(
    field: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: Any
) -> None:
    """Validate failure evidence beyond its outer artifact checksum.

    Parameters
    ----------
    field
        Failure field to corrupt.
    monkeypatch
        Restoring offline seam.
    tmp_path
        Isolated artifact directory.
    value
        Structurally false replacement.
    """
    harness = _Harness(root=tmp_path)
    harness.script[("draft", 0)] = [TimeoutError("synthetic")]
    _install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(expected_exception=LPGenerationFailed):
        harness._run()
    rows = json.loads((tmp_path / _FAILURE).read_bytes())
    rows[0][field] = value
    (tmp_path / _FAILURE).write_bytes(_bytes(rows))
    _reseal(name=_FAILURE, root=tmp_path)
    before = _snapshot(tmp_path)
    harness.calls.clear()
    with pytest.raises(expected_exception=(ValueError, OSError)):
        harness._run()
    assert not harness.calls
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="after", argvalues=[False, True])
def test_failure_journal_removal_interruption_keeps_exhaustion_and_later_disposition(
    after: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retain a failed checker attempt across interruption while removing its journal.

    Parameters
    ----------
    after
        Interrupt after unlink instead of before it.
    monkeypatch
        Restoring filesystem fault injection.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    harness.script[("verdict", 0)] = [TimeoutError("synthetic")]
    _install(harness=harness, monkeypatch=monkeypatch)
    original = Path.unlink
    observed = 0

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        """Follow the filesystem protocol and interrupt the failure journal removal.

        Parameters
        ----------
        self
            Artifact selected for removal.
        missing_ok
            Whether absent temporary files are acceptable.
        """
        nonlocal observed
        if self.name == _JOURNAL:
            observed += 1
        hit = self.name == _JOURNAL and observed == 4
        if hit and not after:
            raise _Interruption()
        original(missing_ok=missing_ok, self=self)
        if hit and after:
            raise _Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="unlink", target=Path, value=_unlink)
        with pytest.raises(expected_exception=_Interruption):
            harness._run()
    harness.calls.clear()
    harness._run()
    assert harness.calls == [("verdict", 0)]
    history = json.loads((tmp_path / _FAILURE).read_bytes())
    assert len(history) == 1
    assert history[0]["exhausted"] is True
    assert history[0]["run_number"] == 1
    assert history[0]["resolved_run_number"] == 2


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
@pytest.mark.parametrize(
    argnames="attack",
    argvalues=["missing", "duplicate", "endpoint", "identity", "malformed"],
)
def test_invalid_model_proposals_never_enter_success_prefixes(
    attack: str, monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Apply real integrity validators to untrusted producer and corrected outputs.

    Parameters
    ----------
    attack
        Intrinsic or request-relative violation.
    monkeypatch
        Restoring offline seam.
    stage
        Producer or independent correction boundary.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(batch=2, count=4, root=tmp_path)
    request = lp_requests.build_lp_generation_requests(
        as_lc_bundle=harness.bundle,
        doc_key="synthetic-selection-document",
        kg_config=harness.config,
    ).requests[0]
    response = _checker._draft(request)
    if attack == "missing":
        response.judgments.pop()
    elif attack == "duplicate":
        response.judgments[1] = response.judgments[0].model_copy(deep=True)
    elif attack == "endpoint":
        response.judgments[0].first_sfi_uuid = UUID(int=99999)
    elif attack == "identity":
        response.request_content_hash = "0" * 64
    else:
        response.judgments[0].confidence = 2.0
    if stage == "draft":
        proposal = response
    else:
        proposal = _checker._verdict(request=request)
        proposal.passed = False
        proposal.issues = [_checker._issue()]
        proposal.corrected_response = response
    harness.script[(stage, 0)] = [proposal]
    _install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(expected_exception=LPGenerationFailed):
        harness._run()
    assert _rows(tmp_path / _RESPONSE) == []
    assert _rows(tmp_path / _VERDICT) == []
    assert len(_rows(tmp_path / _DRAFT)) == (stage == "verdict")
    assert len(json.loads((tmp_path / _FAILURE).read_bytes())) == 1
    assert all(index == 0 for _, index in harness.calls)


@pytest.mark.parametrize(argnames="after", argvalues=[False, True])
@pytest.mark.parametrize(argnames="phase", argvalues=[1, 2, 3, 4, 5])
def test_journal_removal_interruption_preserves_all_completed_calls(
    after: bool, monkeypatch: pytest.MonkeyPatch, phase: int, tmp_path: Path
) -> None:
    """Reuse committed progress whether interruption precedes or follows journal removal.

    Parameters
    ----------
    after
        Interrupt after removal rather than before.
    monkeypatch
        Restoring unlink fault injector.
    phase
        Initialization, run, draft, verdict, or response transaction ordinal.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    original = Path.unlink
    observed = 0

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        """Interrupt a selected journal removal while preserving ordinary cleanup.

        Parameters
        ----------
        self
            File selected for removal.
        missing_ok
            Whether absent temporary files are acceptable.
        """
        nonlocal observed
        if self.name == _JOURNAL:
            observed += 1
            if observed == phase and not after:
                raise _Interruption()
        original(missing_ok=missing_ok, self=self)
        if self.name == _JOURNAL and observed == phase and after:
            raise _Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="unlink", target=Path, value=_unlink)
        with pytest.raises(expected_exception=_Interruption):
            harness._run()
    harness.calls.clear()
    harness._run()
    assert harness.calls == (
        [("draft", 0), ("verdict", 0)]
        if phase < 3
        else [("verdict", 0)] if phase == 3 else []
    )


@pytest.mark.parametrize(argnames="after", argvalues=[False, True])
@pytest.mark.parametrize(
    argnames="phase",
    argvalues=["initialize", "begin", "draft", "verdict", "response", "failure"],
)
@pytest.mark.parametrize(argnames="name", argvalues=[_JOURNAL, *_STORED])
def test_journaled_transactions_resume_at_every_artifact_write_boundary(
    after: bool, monkeypatch: pytest.MonkeyPatch, name: str, phase: str, tmp_path: Path
) -> None:
    """Recover exact old/new transaction states without repeating durable valid calls.

    Parameters
    ----------
    after
        Interrupt after atomic replacement instead of before it.
    monkeypatch
        Restoring atomic-write fault injector.
    name
        Journal, stage artifact, failure artifact, or receipt boundary.
    phase
        Execution transition whose transaction is interrupted.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    if phase == "failure":
        harness.script[("verdict", 0)] = [TimeoutError("synthetic")]
    _install(harness=harness, monkeypatch=monkeypatch)
    original = lp_checkpoints._atomic_write
    transaction = 0
    target = {
        "initialize": 1,
        "begin": 2,
        "draft": 3,
        "verdict": 4,
        "response": 5,
        "failure": 4,
    }[phase]
    fired = False

    def _write(*, path: Path, payload: bytes) -> None:
        """Interrupt exactly one observed durable publication boundary.

        Parameters
        ----------
        path
            Actual destination file.
        payload
            Proposed complete bytes.
        """
        nonlocal fired, transaction
        if path.name == _JOURNAL:
            transaction += 1
        hit = not fired and transaction == target and path.name == name
        if hit and not after:
            fired = True
            raise _Interruption()
        original(path=path, payload=payload)
        if hit and after:
            fired = True
            raise _Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="_atomic_write", target=lp_checkpoints, value=_write)
        with pytest.raises(expected_exception=_Interruption):
            harness._run()
    assert fired
    before_calls = list(harness.calls)
    harness.calls.clear()
    outcomes = harness._run()
    assert len(outcomes) == 1
    assert not (tmp_path / _JOURNAL).exists()
    assert json.loads((tmp_path / _RECEIPT).read_bytes())["status"] == "completed"
    durable = name != _JOURNAL or after
    expected = {
        "initialize": [("draft", 0), ("verdict", 0)],
        "begin": [("draft", 0), ("verdict", 0)],
        "draft": [("verdict", 0)] if durable else [("draft", 0), ("verdict", 0)],
        "verdict": [] if durable else [("verdict", 0)],
        "response": [],
        "failure": [("verdict", 0)],
    }[phase]
    assert harness.calls == expected
    if phase in {"draft", "verdict", "response", "failure"}:
        assert ("draft", 0) in before_calls
    history = json.loads((tmp_path / _FAILURE).read_bytes())
    assert len(history) == int(phase == "failure" and durable)
    if history:
        assert history[0]["resolved_run_number"] == 2


@pytest.mark.parametrize(argnames="failed", argvalues=[False, True])
@pytest.mark.parametrize(
    argnames="name", argvalues=[_INPUTS[0], _INPUTS[2], _DRAFT, _FAILURE, _RECEIPT]
)
@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_material_corruption_during_call_is_not_retried_or_recorded_as_model_failure(
    failed: bool, monkeypatch: pytest.MonkeyPatch, name: str, stage: str, tmp_path: Path
) -> None:
    """Stop immediately on changed material, preserving it even when the call failed.

    Parameters
    ----------
    failed
        Whether the interrupted model boundary raises an ordinary exception.
    monkeypatch
        Restoring offline call seam.
    name
        Material file changed while the call is in flight.
    stage
        Producer or checker boundary to damage.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(root=tmp_path)
    harness.config.learning_progressions.retry.producer_max_retries = 2
    harness.config.learning_progressions.retry.checker_max_retries = 2
    damaged: dict[str, bytes] = {}

    def _call(**kwargs: Any) -> Any:
        """Damage one artifact after inspecting valid pre-call material.

        Parameters
        ----------
        kwargs
            Actual call arguments from orchestration.

        Returns
        -------
        Any
            Complete synthetic proposal if no model exception is requested.
        """
        result = harness._call(**kwargs)
        current = "draft" if kwargs["draft"] is None else "verdict"
        if current == stage:
            (tmp_path / name).write_bytes(b"unexplained concurrent mutation\n")
            damaged.update(_snapshot(tmp_path))
            if failed:
                raise TimeoutError("synthetic model failure after corruption")
        return result

    monkeypatch.setattr(
        name="generate_learning_progressions_for_request",
        target=lp_generation,
        value=_call,
    )
    with pytest.raises(expected_exception=ValueError):
        harness._run()
    assert harness.calls == (
        [("draft", 0)] if stage == "draft" else [("draft", 0), ("verdict", 0)]
    )
    assert _snapshot(tmp_path) == damaged


@pytest.mark.parametrize(
    argnames="first_module",
    argvalues=[
        "agents",
        "llm",
        "lp_checkpoints",
        "lp_generation",
        "lp_requests",
        "prompts",
        "validators",
    ],
)
def test_modules_import_cold_from_each_entry_point_with_shared_request_identity(
    first_module: str,
) -> None:
    """Keep fresh imports cycle-free with one request schema across every consumer.

    Parameters
    ----------
    first_module
        Module imported before any other LP request or execution consumer.
    """
    names = (
        "agents",
        "llm",
        "lp_checkpoints",
        "lp_generation",
        "lp_requests",
        "prompts",
        "validators",
    )
    script = dedent(
        '''
        import importlib
        import sys

        def _reject_network(event, args):
            """Follow the positional protocol of a Python audit hook.

            Parameters
            ----------
            event
                Event emitted by the interpreter.
            args
                Event-specific arguments, never logged.
            """
            if event.startswith(("socket.connect", "socket.getaddrinfo")):
                raise AssertionError("Importing LP modules must remain offline.")

        sys.addaudithook(_reject_network)
        sys.path.insert(0, sys.argv[1])
        for name in sys.argv[2:]:
            importlib.import_module("kgfeg.kgs." + name)

        shared = sys.modules["kgfeg.kgs.lp_requests"]
        request = shared.LPGenerationRequest
        assert request.__module__ == shared.__name__
        for name in ("agents", "llm", "lp_checkpoints", "prompts", "validators"):
            assert sys.modules["kgfeg.kgs." + name].LPGenerationRequest is request
        for name in ("lp_checkpoints", "lp_generation"):
            assert sys.modules["kgfeg.kgs." + name].LPRequestPopulation is shared.LPRequestPopulation
        # Resolving the schema also checks forward references after standalone import.
        assert request.model_json_schema()["properties"]["pairs"]["minItems"] == 1
        '''
    )
    completed = subprocess.run(
        args=[
            sys.executable,
            "-B",
            "-I",
            "-c",
            script,
            str(Path(lp_generation.__file__).resolve().parents[2]),
            first_module,
            *(name for name in names if name != first_module),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "file_corruption",
        "missing_file",
        "journal_invalid",
        "journal_partial",
        "journal_payload",
        "journal_material",
    ],
)
def test_pending_transaction_never_authorizes_arbitrary_corruption_repair(
    attack: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preserve unexplained disk or journal corruption before recovery and any call.

    Parameters
    ----------
    attack
        Unexpected disk bytes or invalid intended transaction state.
    monkeypatch
        Restoring transaction fault injector.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    original = lp_checkpoints._atomic_write

    def _write(*, path: Path, payload: bytes) -> None:
        """Leave a durable draft transaction and an old receipt.

        Parameters
        ----------
        path
            Artifact selected for replacement.
        payload
            Complete proposed bytes.
        """
        original(path=path, payload=payload)
        if path.name == _DRAFT and payload:
            raise _Interruption()

    with monkeypatch.context() as patch:
        patch.setattr(name="_atomic_write", target=lp_checkpoints, value=_write)
        with pytest.raises(expected_exception=_Interruption):
            harness._run()
    if attack == "file_corruption":
        (tmp_path / _DRAFT).write_bytes(b"arbitrary corrupt bytes")
    elif attack == "missing_file":
        (tmp_path / _FAILURE).unlink()
    elif attack == "journal_invalid":
        (tmp_path / _JOURNAL).write_bytes(b"{incomplete")
    else:
        journal = json.loads((tmp_path / _JOURNAL).read_bytes())
        if attack == "journal_partial":
            del journal["next_payloads"][_FAILURE]
        elif attack == "journal_payload":
            row = json.loads(journal["next_payloads"][_DRAFT])
            row["payload"]["judgments"] = []
            row["payload_content_hash"] = hashlib.sha256(
                _bytes(row["payload"])
            ).hexdigest()
            journal["next_payloads"][_DRAFT] = _bytes(row).decode()
            receipt = json.loads(journal["next_payloads"][_RECEIPT])
            receipt["artifact_byte_hashes"][_DRAFT] = hashlib.sha256(
                _bytes(row)
            ).hexdigest()
            journal["next_payloads"][_RECEIPT] = _bytes(receipt).decode()
        else:
            receipt = json.loads(journal["next_payloads"][_RECEIPT])
            receipt["material"]["producer_instructions"] += " changed"
            journal["next_payloads"][_RECEIPT] = _bytes(receipt).decode()
        (tmp_path / _JOURNAL).write_bytes(_bytes(journal))
    before = _snapshot(tmp_path)
    harness.calls.clear()
    with pytest.raises(expected_exception=(ValueError, OSError)):
        harness._run()
    assert not harness.calls
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="after", argvalues=[False, True])
@pytest.mark.parametrize(argnames="name", argvalues=_INPUTS)
def test_population_write_interruptions_make_zero_calls_and_reject_partial_reuse(
    after: bool, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Only an entirely materialized population may initialize or resume execution.

    Parameters
    ----------
    after
        Whether interruption follows writing the selected complete file.
    monkeypatch
        Restoring population-write fault injector.
    name
        Candidate, summary, request, or population receipt boundary.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(count=2, root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    original = Path.write_bytes

    def _write(self: Path, data: bytes) -> int:
        """Follow the filesystem method protocol and interrupt a population write.

        Parameters
        ----------
        self
            Destination artifact.
        data
            Complete population bytes.

        Returns
        -------
        int
            Number of bytes written on unaffected calls.
        """
        hit = self.parent == tmp_path and self.name == name
        if hit and not after:
            raise _Interruption()
        result = original(data=data, self=self)
        if hit and after:
            raise _Interruption()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(name="write_bytes", target=Path, value=_write)
        with pytest.raises(expected_exception=_Interruption):
            harness._run()
    assert not harness.calls
    before = _snapshot(tmp_path)
    if not before or (name == _INPUTS[-1] and after):
        assert len(harness._run()) == 1
    else:
        with pytest.raises(expected_exception=(ValueError, OSError)):
            harness._run()
        assert not harness.calls
        assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
def test_required_warning_loss_halts_before_success_checkpoint(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Reject omitted upstream warnings in producer or checker replacement judgments.

    Parameters
    ----------
    monkeypatch
        Restoring offline stage seam.
    stage
        Producer or checker-correction boundary.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture("ghana_math")
    harness.config = _fixtures._config(batch=3, profile="ghana_math")
    harness.config.learning_progressions.retry.producer_max_retries = 0
    harness.config.learning_progressions.retry.checker_max_retries = 0
    population = lp_requests.build_lp_generation_requests(
        as_lc_bundle=harness.bundle,
        doc_key="synthetic-selection-document",
        kg_config=harness.config,
    )
    request = next(r for r in population.requests if any(p.warnings for p in r.pairs))
    response = _checker._draft(request)
    pair_index = next(i for i, pair in enumerate(request.pairs) if pair.warnings)
    response.judgments[pair_index].warnings = []
    proposal = (
        response
        if stage == "draft"
        else _checker._verdict(correction=response, request=request)
    )
    harness.script[(stage, request.request_index)] = [proposal]
    _install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(expected_exception=LPGenerationFailed):
        harness._run()
    assert len(_rows(tmp_path / _RESPONSE)) == request.request_index
    assert harness.calls[-1] == (stage, request.request_index)


@pytest.mark.parametrize(argnames="name", argvalues=[_DRAFT, _VERDICT, _RESPONSE])
@pytest.mark.parametrize(
    argnames="attack",
    argvalues=[
        "gap",
        "duplicate",
        "reverse",
        "index",
        "stage",
        "payload_hash",
        "prerequisite",
        "prompt",
        "execution",
    ],
)
def test_resealed_success_prefix_attacks_reach_structural_validation(
    attack: str, monkeypatch: pytest.MonkeyPatch, name: str, tmp_path: Path
) -> None:
    """Reject dishonest prefixes even when outer hashes and counts are recomputed.

    Parameters
    ----------
    attack
        Ordering, coverage, or material-binding violation.
    monkeypatch
        Restoring offline seam.
    name
        Successful stage artifact under attack.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    rows = _rows(tmp_path / name)
    if attack == "gap":
        rows.pop(1)
    elif attack == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif attack == "reverse":
        rows.reverse()
    elif attack == "index":
        rows[0]["request_index"] = 1
    elif attack == "stage":
        rows[0]["stage"] = "response" if name != _RESPONSE else "draft"
    else:
        field = {
            "payload_hash": "payload_content_hash",
            "prerequisite": "prerequisite_content_hash",
            "prompt": "prompt_content_hash",
            "execution": "execution_content_hash",
        }[attack]
        rows[0][field] = "0" * 64
    (tmp_path / name).write_bytes(b"".join(_bytes(row) for row in rows))
    _reseal(name=name, root=tmp_path)
    before = _snapshot(tmp_path)
    harness.calls.clear()
    with pytest.raises(expected_exception=(ValueError, OSError)):
        harness._run()
    assert not harness.calls
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="empty", argvalues=[False, True])
@pytest.mark.parametrize(argnames="profile", argvalues=_PROFILES)
def test_six_curriculum_execution_retains_dag_warnings_bounds_identity_and_compatibility(
    empty: bool, monkeypatch: pytest.MonkeyPatch, profile: str, tmp_path: Path
) -> None:
    """Keep structural diversity through real checkpointing without asserting semantics.

    Parameters
    ----------
    empty
        Whether a framework-only synthetic bundle makes the population empty.
    monkeypatch
        Restoring offline stage boundary.
    profile
        Approved reduced curriculum plus synthetic same-grain peers.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(root=tmp_path)
    harness.bundle = _fixtures._expanded_fixture(profile)
    harness.config = _fixtures._config(
        batch=3, limits={"max_ancestor_path_depth": 1}, profile=profile
    )
    if empty:
        harness.bundle = _fixtures._factories._bundle(items=())
    before_bundle = harness.bundle.model_dump_json()
    before_config = harness.config.model_dump_json()
    sentinels = {
        name: (name + " original bytes").encode()
        for name in (
            "as_kg_bundle.json",
            "as_nodes.jsonl",
            "as_relationships.jsonl",
            "as_lc_kg_bundle.json",
            "as_lc_nodes.jsonl",
            "as_lc_relationships.jsonl",
        )
    }
    for name, payload in sentinels.items():
        (tmp_path / name).write_bytes(payload)
    _install(harness=harness, monkeypatch=monkeypatch)
    outcomes = harness._run()
    _assert_population(tmp_path)
    requests = [
        LPGenerationRequest.model_validate(row) for row in _rows(tmp_path / _INPUTS[2])
    ]
    assert bool(requests) != empty
    assert len(harness.calls) == 2 * len(requests)
    assert len(outcomes) == len(requests)
    seen_dag = seen_unresolved = False
    for request, response in zip(requests, outcomes, strict=True):
        assert len(request.pairs) <= 3
        assert response.request_id == request.request_id
        assert {j.pair_id for j in response.judgments} == {
            p.pair_id for p in request.pairs
        }
        assert all(j.decision == "no_relation" for j in response.judgments)
        for pair, judgment in zip(request.pairs, response.judgments, strict=True):
            assert pair.first_sfi_uuid == judgment.first_sfi_uuid
            assert pair.second_sfi_uuid == judgment.second_sfi_uuid
            assert set(pair.warnings) <= set(judgment.warnings)
        for sfi in request.sfis:
            direct = {
                UUID(edge.source_entity_value)
                for edge in harness.bundle.relationships_has_child
                if edge.source_entity == "StandardsFrameworkItem"
                and UUID(edge.target_entity_value) == sfi.context.sfi_uuid
            }
            assert set(sfi.parent_sfi_uuids) == direct
            seen_dag |= len(direct) > 1
            seen_unresolved |= sfi.context.unresolved_ancestry
            assert all(len(path.ancestor_sfi_uuids) <= 1 for path in sfi.ancestor_paths)
            if sfi.context.unresolved_ancestry:
                assert (
                    harness.bundle.framework.case_identifier_uuid
                    not in sfi.parent_sfi_uuids
                )
                assert any(
                    "unresolved" in warning.lower() for warning in request.warnings
                )
    if not empty and profile == "pratham_science":
        assert seen_dag
    if not empty and profile == "ghana_math":
        assert seen_unresolved
    assert harness.bundle.model_dump_json() == before_bundle
    assert harness.config.model_dump_json() == before_config
    assert all(
        (tmp_path / name).read_bytes() == payload for name, payload in sentinels.items()
    )
    saved = {name: (tmp_path / name).read_bytes() for name in (*_INPUTS, *_STORED[:-1])}
    harness.calls.clear()
    harness.bundle.items.reverse()
    harness.bundle.relationships_has_child.reverse()
    harness.bundle.learning_components.reverse()
    harness.bundle.relationships_supports.reverse()
    assert harness._run() == outcomes
    assert not harness.calls
    assert saved == {name: (tmp_path / name).read_bytes() for name in saved}
    assert json.loads((tmp_path / _RECEIPT).read_bytes())["status"] == "completed"


@pytest.mark.parametrize(argnames="stage", argvalues=["draft", "verdict"])
@pytest.mark.parametrize(argnames="index", argvalues=[0, 1, 2])
def test_stage_interruptions_resume_earliest_unfinished_call(
    index: int, monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: Path
) -> None:
    """Resume empty, partial, and final request prefixes at the missing call stage.

    Parameters
    ----------
    index
        Request interrupted before a valid call result exists.
    monkeypatch
        Restoring offline seam.
    stage
        Interrupted producer or checker.
    tmp_path
        Isolated generation directory.
    """
    harness = _Harness(root=tmp_path)
    harness.script[(stage, index)] = [_Interruption()]
    _install(harness=harness, monkeypatch=monkeypatch)
    with pytest.raises(expected_exception=_Interruption):
        harness._run()
    harness.calls.clear()
    harness._run()
    expected = [(s, i) for i in range(index, 3) for s in ("draft", "verdict")]
    if stage == "verdict":
        expected.pop(0)
    assert harness.calls == expected
    assert json.loads((tmp_path / _FAILURE).read_bytes()) == []


@pytest.mark.parametrize(
    argnames="change",
    argvalues=[
        "upstream",
        "batch",
        "producer",
        "checker",
        "retry",
        "model",
        "settings",
        "warning_policy",
    ],
)
def test_stale_material_is_rejected_before_any_saved_progress_changes(
    change: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bind reuse to actual upstream, curriculum, prompt, and shared model inputs.

    Parameters
    ----------
    change
        Material input altered after a complete execution.
    monkeypatch
        Restoring runtime settings and offline seam.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    if change == "upstream":
        harness.bundle.framework.name += " changed"
    elif change == "batch":
        harness.config.learning_progressions.request_batch_size = 2
    elif change in {"producer", "checker"}:
        field = change + "_instructions"
        setattr(
            harness.config.learning_progressions,
            field,
            getattr(harness.config.learning_progressions, field) + " changed",
        )
    elif change == "retry":
        harness.config.learning_progressions.retry.producer_max_retries += 1
    elif change == "model":
        monkeypatch.setattr(
            name="LLM_KG_MODEL", target=Settings, value="openai:gpt-5.1"
        )
    elif change == "settings":
        monkeypatch.setattr(name="LLM_MAX_OUTPUT_TOKENS", target=Settings, value=2048)
    else:
        harness.config.learning_progressions.unresolved_participation = (
            "exclude_unresolved"
        )
    before = _snapshot(tmp_path)
    harness.calls.clear()
    with pytest.raises(expected_exception=(ValueError, OSError)):
        harness._run()
    assert not harness.calls
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(argnames="overwrite", argvalues=[False, True])
def test_writer_lock_blocks_competing_generation_and_regeneration(
    monkeypatch: pytest.MonkeyPatch, overwrite: bool, tmp_path: Path
) -> None:
    """Reject a second writer before it changes or archives any material evidence.

    Parameters
    ----------
    monkeypatch
        Restoring offline seam.
    overwrite
        Whether the competing writer requests explicit regeneration.
    tmp_path
        Isolated artifact directory.
    """
    harness = _Harness(root=tmp_path)
    _install(harness=harness, monkeypatch=monkeypatch)
    harness._run()
    before = _snapshot(tmp_path)
    harness.calls.clear()
    with (tmp_path / ".lp_generation.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(expected_exception=OSError):
            harness._run(overwrite=overwrite)
    assert not harness.calls
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "lp_generation_history").exists()
